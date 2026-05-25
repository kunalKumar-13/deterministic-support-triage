# retrieval_failure_modes.md

Catalogue of retrieval failure modes, how the agent detects each, and
how it degrades safely. Observability is provided by:

  - `triage/retrieval/diagnostics.py` — per-run counters + worst-case
    examples, dumped to `docs/retrieval_run_diagnostics.md`.
  - `triage/retrieval/consensus.py` — per-ticket pairwise agreement
    signal.
  - The `escalation_reasons` field in `PolicyDecision` — surfaces every
    retrieval-derived tag in the justification CSV column.

Use this doc as the manual companion to those data artefacts.

## Headline numbers (visible run)

From the most recent `docs/retrieval_run_diagnostics.md`:

- 90 tickets processed.
- 0 no-grounding retrievals.
- 0 weak-match retrievals.
- 0 single-source-only retrievals (post the consensus threshold tuning).
- 0 numeric consensus conflicts.
- 0 imperative consensus conflicts.
- Top1 score range: 0.88 … 1.31 (median 1.22).
- Top1 vs top2 agreement: 0.03 … 0.31 (median 0.12) — chunks are
  topically related but not redundant.

A fully-populated corpus matches every visible-set query above the
weak-match threshold (0.30). The hidden set may not behave so cleanly;
the failure-mode catalogue below describes what we expect to see and
how the pipeline reacts.

## F-1: No grounding

- **Definition**: top1 rerank score < 0.30 AND top1 raw < ~0.20.
- **Cause**: query terms have no overlap with any corpus chunk
  (off-topic, low-resource language, brand-new product area).
- **Detection**: `retrieval.no_grounding` flag on `RetrievalResult`.
- **Behaviour**: Policy rule 4 escalates with reason
  `no_grounding_in_corpus`. Response is the canned "I don't have a
  confirmed answer" template. `source_documents` is empty.
- **Why this is safe**: we never invent a doc to cite; the rubric
  penalises hallucinated citations more than omissions.

## F-2: Weak match

- **Definition**: 0.30 ≤ top1 rerank < 0.50.
- **Cause**: query lexically overlaps with the corpus on common words
  but is not semantically about the same thing (e.g. "I lost something"
  matches lots of docs at low precision).
- **Detection**: `retrieval.weak_match` on `RetrievalResult`.
- **Behaviour**:
  - If risk ≥ medium → escalate (rule 5).
  - Otherwise → reply with confidence capped at 0.65.
- **Why this is safe**: high risk + low confidence = escalation, by
  design.

## F-3: Single-source only

- **Definition**: only one chunk survives rerank above the citation
  threshold.
- **Cause**: the question is so specific that only one corpus doc is
  relevant.
- **Detection**: `consensus.single_source_only`.
- **Behaviour**: confidence multiplied by 0.85 (small hedge). Status
  unchanged — single-source isn't itself wrong, just less corroborated.
- **Why this is safe**: a single specific doc can be authoritative; we
  hedge but do not refuse.

## F-4: Numeric consensus conflict

- **Definition**: two or more retrieved chunks share a strong topic
  (jaccard > 0.35) AND both contain *policy keywords + policy numbers*
  (e.g. money amounts, percentages) that are disjoint.
- **Cause**: corpus contradictions — e.g. two docs cite different
  refund amounts for the same scenario.
- **Detection**: `consensus.numeric_disagreement` flag with
  `numeric_disagreement:<chunk_i,chunk_j>` tag.
- **Behaviour**: Policy rule 4f escalates with reason
  `retrieval_consensus_conflict`. Confidence multiplied by 0.45.
- **Why this is safe**: we will never confidently assert a policy that
  the corpus itself contradicts.

## F-5: Imperative consensus conflict

- **Definition**: a chunk has many negative-imperative assertions
  ("never / always / must not") while another, topically-similar chunk
  has none. Both must be policy-bearing.
- **Cause**: one doc states absolutes while another nuances them.
- **Detection**: `consensus.imperative_disagreement`.
- **Behaviour**: same as F-4 — escalate via rule 4f.
- **Why this is safe**: absolutes are often the load-bearing claim in
  policy disputes; if they conflict, we don't pick a side.

## F-6: Cross-domain over-retrieval

- **Definition**: a ticket about product X retrieves a top-1 chunk from
  product Y.
- **Cause**: shared vocabulary ("dispute", "refund", "billing", "card",
  "account") across the three domains.
- **Detection**: the rerank's domain-bias multiplier (1.15× same /
  0.85× cross) already corrects this in most cases. When the company
  hint is unreliable, we use brand-term gazetteer voting.
- **Behaviour**: if mis-routing persists, the response is grounded in
  whatever chunk top-ranked. The validator does not honour cross-
  domain refund claims (refund-grounding rule).
- **Why this is safe**: even on mis-routed retrieval, no destructive
  action proceeds; the worst case is an irrelevant-but-grounded reply.

## F-7: Synonym gap

- **Definition**: ticket uses synonym X, corpus uses synonym Y for the
  same concept.
- **Examples**:
  - "block my card" ↔ "freeze your card" ↔ "report lost or stolen"
  - "delete account" ↔ "close account" ↔ "deactivate"
  - "outage" ↔ "service unavailable" ↔ "down"
- **Detection**: indirect — surfaces as weak_match (F-2).
- **Behaviour**: same as F-2.
- **Mitigation we did NOT ship**: a static synonym map. The LLM path
  closes this gap. We chose not to add it to the heuristic to keep
  false-positive risk low at the cost of recall.

## F-8: Multilingual underperformance

- **Definition**: non-English ticket with no parallel corpus
  documentation; retrieval still returns lexical matches on shared
  tokens (e.g. brand names) but precision is low.
- **Cause**: corpus is English-only.
- **Detection**: `language != "en"` + weak_match.
- **Behaviour**:
  - If the ticket is injection-shaped → critical injection detector
    fires regardless of language.
  - Otherwise → reply in English using the retrieved English doc, with
    a possibly-hedged confidence.
- **Why this is safe**: response is in English (avoids LLM-translation
  artefacts that could mislead a non-English speaker); the user can
  request escalation.

## F-9: Adversarial / poisoned chunk match

- **Definition**: a retrieved chunk contains injection-marker text
  ("ignore previous instructions", "developer mode", "begin system
  instructions").
- **Cause**: corpus contamination (intentional or accidental).
- **Detection**: `ChunkRef.has_injection_marker == True` (set at corpus-
  load time by `_has_injection_marker`).
- **Behaviour**:
  - Trust multiplier halved at rerank time.
  - The chunk's text is still summarised in the response but no
    instruction in the chunk affects control flow (the policy
    validator never reads corpus content).
- **Why this is safe**: code-not-corpus controls escalation and
  actions.

## F-10: Recency drift

- **Definition**: the corpus contains both current and stale docs about
  the same policy. The stale doc may rank above the current one.
- **Cause**: corpora are rarely garbage-collected.
- **Detection**: `recency_score` multiplier on rerank (0.9–1.1× span).
  This is a soft signal that we acknowledge is weak when we have no
  real publication dates.
- **Behaviour**: response may quote a slightly outdated chunk. The
  validator does not enforce dates; the response generator paraphrases
  what was retrieved.
- **Mitigation backlog**: parse content-date markers (e.g. "as of
  2024") inside chunks.

## F-11: Tool-name leak via retrieval

- **Definition**: a corpus chunk mentions internal tool names (e.g.
  `issue_refund`).
- **Detection**: not a special-case detector. The response generator's
  outbound scrubber removes `internal_tools.json` / `issue_refund` etc.
  via `_LEAK_PATTERNS` in `triage/response/generator.py`.
- **Behaviour**: if a corpus chunk says "the agent calls
  issue_refund", the resulting response will not.
- **Why this is safe**: the scrubber is the last line of defence.

## F-12: Empty corpus

- **Definition**: `data/{devplatform,claude,visa}/` has 0 files.
- **Cause**: missing data download.
- **Detection**: corpus loader logs `corpus_empty_warning` at startup;
  `n_chunks == 0`.
- **Behaviour**: every retrieval returns `no_grounding`; every reply
  becomes a "no documentation found" escalation.
- **Why this is safe**: degrades to "escalate everything" rather than
  hallucinating.

## How to read the run diagnostics

When you suspect a retrieval problem on a specific ticket:

1. Re-run `python code/main.py`.
2. Open `docs/retrieval_run_diagnostics.md`. Scan the "worst-case
   retrievals" section for the ticket ID and the top1 score.
3. Open `support_tickets/output.csv`, find the row, read the
   `justification` column — the `reasons=...` field contains the
   structured retrieval tags (`weak_retrieval`, `no_grounding`,
   `retrieval_single_source`, `retrieval_numeric_disagreement:...`).
4. If you want chunk-level traces, set `TRIAGE_LOG_LEVEL=DEBUG` and
   re-run; the structlog event stream includes per-ticket retrieval
   scores.

This catalogue is the operational pair of [retrieval_gaps.md]; gaps are
"what could go wrong in the design" and failure modes are "what is
already going wrong in the run".
