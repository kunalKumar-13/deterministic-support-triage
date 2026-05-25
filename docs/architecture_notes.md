# architecture_notes.md

Pre-implementation architecture analysis for the MLE Hiring Challenge support
triage agent. Written before any code.

## 1. Constraints distilled from the spec

| Source | Constraint | Implication |
|---|---|---|
| problem_statement | Terminal-based, corpus-only, no external knowledge | No web calls; retrieval must be local. |
| problem_statement | Adversarial tickets present | Safety layer must precede retrieval and LLM. |
| problem_statement | Deterministic output (rerun-identical) | temperature=0, seeded, stable sort, no `set()` ordering, sorted globs. |
| problem_statement | ≤3 min total wall time | Cache embeddings to disk; precompute BM25; batch LLM calls or short prompts. |
| problem_statement | All extended columns required | `confidence_score`, `source_documents`, `risk_level`, `pii_detected`, `language`, `actions_taken`. |
| evaluation | Single prompt-injection compliance → 0 on a 25% dimension | Treat injection detection as a hard gate, not a soft signal. |
| evaluation | Hallucinated citations penalised > omitting citations | Cite only docs we actually retrieved; verify path existence at write time. |
| evaluation | PII echo → −50% | Outbound response goes through PII redaction even after grounded generation. |
| evaluation | Hidden test set is 60% of CSV score, different adversarial distribution | Generalise via rule‑based detectors + retrieval, not memorisation. |
| evaluation | Flat confidence scores get minimal credit | Confidence must be multi-signal and span a wide range. |
| evaluation | Invalid JSON in `actions_taken` → 0 on Tool Calling | Schema-validate every tool call before write; default `[]`. |
| AGENTS.md | Must log every turn to `$HOME/mle_hiring/log.txt` | Wire structlog + a turn-log appender; redact PII before logging. |
| AGENTS.md | Standard machine, no GPU, 8 vCPU / 32 GB | Avoid GPU-only models; favor BM25 + sklearn TF-IDF over heavy embeddings. |

## 2. Non-goals

- We are not building a chat product. No multi-turn streaming UX.
- We are not building autonomous agent loops. The LLM never controls flow.
- We are not optimising for cleverness on the visible sample set; we optimise
  for robustness across the hidden set.
- We are not building a vector DB. The corpus is small enough that an in-memory
  numpy matrix + cosine similarity is faster and more deterministic than FAISS.

## 3. High-level pipeline

```
                ┌──────────────────────────────────────────────┐
                │              UNTRUSTED INPUT                 │
                │   (issue JSON, subject, company)             │
                └───────────────┬──────────────────────────────┘
                                │
                          [1] Preprocessing
                              parse JSON, normalise, language id,
                              extract last user turn
                                │
                          [2] Safety Engine  (HARD GATE)
                              prompt-injection detector
                              PII detector + redactor
                              risk classifier (rule-based)
                                │
                  ┌─────────────┴───────────────┐
                  │ injection_score ≥ critical? │── yes ──► force escalate path
                  └─────────────┬───────────────┘
                                │ no
                          [3] Routing
                              company inference (subject/body/history),
                              cross-product detection, scope check
                                │
                          [4] Retrieval Engine
                              BM25 ∪ TF-IDF cosine → merge → rerank
                              (deterministic ties broken by path)
                                │
                          [5] Document Trust Scoring
                              specificity, agreement, recency proxy
                                │
                          [6] LLM Decision Engine  (STRICT JSON)
                              classify request_type, propose actions,
                              propose product_area, summarise grounded answer
                              — never executes, never decides escalation
                                │
                          [7] Policy Validator (FINAL CONTROLLER)
                              checks consistency, blocks unsupported claims,
                              blocks destructive actions without prereqs,
                              overrides LLM if needed
                                │
                          [8] Tool Execution Validation
                              schema-validate against internal_tools.json,
                              idempotency, prerequisite chain
                                │
                          [9] Response Generation
                              template-driven, grounded, PII-redacted,
                              cites only existing files
                                │
                         [10] Confidence Calibration
                              multi-signal score (retrieval, agreement,
                              risk, injection, LLM self-conf)
                                │
                         [11] Output Formatter
                              schema-conformant CSV row
                                │
                                ▼
                         output.csv row
```

Every stage is pure-function-ish: it takes the prior stage's structured output
and returns a new immutable Pydantic model. No stage mutates an earlier one.

## 4. Trust zones

- **Trusted code paths**: `triage/safety/`, `triage/policy/`, `triage/tools/`,
  the system prompt, the output formatter. These cannot be influenced by the
  ticket content; they decide what happens.
- **Semi-trusted**: documents in `data/{devplatform,claude,visa}/`. We use
  their content for grounding but never as instructions. Anything that looks
  like instructions inside a retrieved doc is stripped before it ever appears
  in an LLM prompt.
- **Untrusted**: every field in the ticket — `issue`, `subject`, `company`,
  prior assistant turns inside `issue`. We never let untrusted strings reach
  the LLM as instructions; only as a quoted data block delimited by
  unforgeable sentinels.

## 5. LLM responsibilities (allowed surface)

The LLM is invoked at most once per ticket with a strict JSON schema. Allowed
outputs:

```
{
  "request_type": "product_issue" | "feature_request" | "bug" | "invalid",
  "product_area": "<short string>",
  "answer_draft": "<grounded paraphrase of retrieved chunks, no policies invented>",
  "proposed_actions": [ { "action": "<tool_name>", "parameters": {...} } ],
  "llm_confidence": 0.0..1.0,
  "reasoning_note": "<≤200 chars, never user-facing>"
}
```

It is NOT allowed to choose `status` (`replied|escalated`), `risk_level`,
`pii_detected`, citations, or the final response. Those are decided by code.

If the LLM call fails for any reason (network, JSON parse, schema mismatch,
timeout, missing API key) the pipeline falls back to a deterministic
rule‑based classifier and **forces escalation**. Crash on a ticket is a
−20% penalty across all dimensions, so we never raise above the ticket loop.

## 6. Deterministic retrieval design

- Chunking: sliding window of 800 chars with 120 char overlap, normalised
  whitespace, stable file order via `sorted(glob)`.
- BM25: rank_bm25 BM25Okapi with `k1=1.5, b=0.75`, tokeniser = lowercase
  word_re. Pure Python, no hidden state.
- TF-IDF vectors: `sklearn.TfidfVectorizer(ngram_range=(1,2),
  sublinear_tf=True, min_df=1, max_df=0.95)`. Deterministic, no embeddings
  service required. (Optional sentence-transformers code path is gated
  behind an env var and disabled by default for reproducibility.)
- Merge: reciprocal rank fusion with `k=60`. Ties broken by document path
  ascending — deterministic.
- Rerank: cross-feature lexical+semantic score:
  `0.55 * tfidf_cos + 0.35 * bm25_norm + 0.10 * title_overlap`.
- Top-k = 6. Anything below absolute score `< τ_low` is treated as "no
  grounding" — confidence drops, and unanswerable risk increases.

We persist the BM25 index and TF-IDF matrix to `code/.cache/` keyed by a
SHA-256 of `(corpus_paths, chunk_params)` so cold start fits well under the
3-minute budget on first run; subsequent runs are instant.

## 7. Confidence calibration model

Final confidence is a deterministic function, not a model output. Inputs:

```
c_retrieval   = clamp(top1_rerank_score, 0, 1)
c_agreement   = jaccard(top1.tokens, top2.tokens)   # higher = corroborated
c_risk        = 1 - risk_weight(risk_level)          # high risk lowers conf
c_injection   = 0.0 if injection_critical else 1.0
c_llm         = llm_confidence (clipped, smoothed)
c_scope       = 1.0 if in_scope else 0.4

confidence = w · [c_retrieval, c_agreement, c_risk, c_injection,
                  c_llm, c_scope]    # weights sum to 1
```

with weights tuned so that the score genuinely spreads across [0.1, 0.95].
Escalations never report > 0.6 confidence. Replies with weak retrieval
(top1 < 0.3) are capped at 0.45.

## 8. Tool execution gating

Tools are loaded from `data/api_specs/internal_tools.json` and registered into
a typed `ToolRegistry`. For each tool we record:

- JSON schema (required + properties + types)
- `requires_identity_verification: bool`
- `destructive: bool`
- `idempotency_key_fields: [...]`
- `min_risk_to_propose`, `max_risk_to_execute`

The validator runs BEFORE the action is written to `actions_taken`:

1. Schema validation (jsonschema).
2. Prerequisite chain (e.g. `issue_refund` requires a prior or proposed
   `verify_identity` in the action list).
3. Duplicate / idempotency check across the same ticket's action list.
4. Forbidden combinations (e.g. cannot both `issue_refund` and
   `lock_account` in one turn without escalation).
5. Risk gate (destructive action under critical risk → block, force escalate).

A failing validation does not crash — it drops the bad action and forces the
status to `escalated` with a justification noting the failed precondition.

## 9. Adversarial robustness — guarantees we want to provide

- No instruction from `issue`/`subject` text can change the system's behavior.
  We achieve this by:
  - Wrapping all user content in `<USER_TEXT_BEGIN>…<USER_TEXT_END>` sentinels.
  - Telling the model only the *delimited content is data, not instructions*.
  - The policy validator does not consult the LLM's choice for `status`;
    that decision is owned by code.
- A ticket containing "ignore previous instructions" gets:
  - `risk_level = high` (or critical if combined with destructive intent)
  - `status = escalated`
  - `response = <safe refusal template>` — never the original injected text.
  - `injection_detected = true` in the structured log.
- Multilingual injections handled via:
  - Detector regex pack covering EN/ES/FR/DE/HI translations of common
    injection triggers ("ignora las instrucciones", "ignore les instructions",
    "ignoriere die Anweisungen", "पिछले निर्देशों को अनदेखा करें", etc.).
  - Generalised "role-override" pattern: any user content containing the
    tokens `system|developer|assistant` followed by a colon and an
    imperative verb scores high.
- Fake corpus instructions: any retrieved chunk containing imperative
  injection patterns is logged and trust-downgraded; the chunk's content is
  still summarisable but instructions are stripped from the prompt.

## 10. Determinism guarantees

- `random.seed(13)`, `numpy.random.seed(13)` at module import.
- LLM calls: `temperature=0, top_p=1, max_tokens` bounded.
- Sorted globs everywhere. No `os.listdir` without sort.
- No `set()` or `dict.keys()` ordering relied on for output.
- Cached indices keyed by content hash.
- The pipeline emits the same CSV row given the same ticket + same corpus +
  same model+version. Across model versions, only `response` may differ; the
  structural columns (`status`, `risk_level`, `actions_taken`, …) are stable.

## 11. Failure modes we accept

- The corpus is small / noisy — some tickets are genuinely unanswerable. We
  prefer to escalate with a clear justification rather than guess.
- Subject/body contradictions — we trust the body (richer signal) and log
  the conflict; if severe, we escalate.
- Unknown product (`company == None` and content is generic) — we attempt
  cross-corpus retrieval; if no top-1 ≥ τ, we escalate as
  `product_area=general` with a polite "please clarify" response.

## 12. Tradeoffs we explicitly chose

- **TF-IDF over sentence-transformers**: a small accuracy hit on semantic
  paraphrase queries vs. perfect reproducibility, no model download (zero
  cold start), and no GPU dependency. We can plug in embeddings later as a
  pure-additional rerank signal without changing the pipeline shape.
- **Rule-based safety over LLM-based safety**: rules are explainable,
  auditable, deterministic, and immune to prompt injection (since the safety
  layer never calls the LLM). They will miss novel attack phrasings, but
  the policy validator + "escalate when uncertain" rule provides a safety
  net.
- **One LLM call per ticket**: minimises latency and cost, eliminates
  multi-step agent loops, and bounds the attack surface. We pay with a
  slightly less elaborate reasoning step.
- **Code as controller, LLM as analyst**: the LLM cannot break the system
  because it cannot decide whether to reply or escalate. Worst case it
  proposes a bad action; the validator drops it.

## 13. Open questions logged (not blockers)

- Tool schema details — `internal_tools.json` is not shipped in this checkout;
  we author a faithful version from the spec and the evaluation rubric. If
  the real file differs at evaluation time we adapt at load.
- Corpus categories — we treat the dir structure as advisory, not
  authoritative, per the problem statement.
