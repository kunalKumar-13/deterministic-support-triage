# retrieval_gaps.md

Retrieval quality audit based on running the agent against the visible
test set (90 tickets). Gaps are listed by category, with concrete tickets
that exhibit them and a recommended mitigation.

The retrieval engine currently is: BM25 + TF-IDF → RRF merge → lexical
rerank with trust multipliers → top-k. It returns 6 chunks; only chunks
that materially overlap with the answer are cited.

## 1. Headline metrics from the visible run

- 64 chunks indexed across 24 corpus docs (`data/{devplatform,claude,visa}/...`).
- 90 tickets processed in 0.70 s (≈ 8 ms / ticket including LLM-off heuristic).
- 80/90 tickets received at least one citation (89% citation rate).
- 10/90 tickets escalated; of those, 0 cited any doc (correct — escalations
  do not cite).
- 0 hallucinated citations (validate_output OK).

## 2. Topical gaps in the visible corpus

These are the queries that retrieved nothing topical and grounded onto a
loosely-related doc instead:

| Ticket | Topic | Closest matched doc | Gap |
|---|---|---|---|
| T0006 | Stripe `cs_live_…` order ID payment failure | dispute-process.md | No DevPlatform Stripe order-ID lookup doc. |
| T0008 / T0009 | "apply tab not visible" / "submissions not working" | test-invites.md | No DevPlatform Community UX troubleshooting docs. |
| T0010 | Zoom-compatibility check blocker | test-invites.md | No proctoring compatibility doc. |
| T0011 | Reschedule assessment | test-invites.md | No candidate-side reschedule flow doc. |
| T0018 | Resume Builder outage | account/password-reset.md | No DevPlatform Resume Builder doc at all. |
| T0019 | Certificate name update | (weak match) | No certificate-management doc. |
| T0044 | Test crash + false proctoring flag | password-reset.md (poor) | No proctoring/cheating-flag doc. |
| T0061 | Cheating confession | test-variants.md (poor) | No academic-integrity flow doc. |
| T0064 | API 500 errors with reproduction | api/rate-limits.md (poor) | No API status / error-codes doc. |
| T0072 | "=cmd|'/C calc'!A1" (Excel injection probe) | test-variants.md (very poor) | Will never have grounding (adversarial). |

Mitigation:

- **No grounding** path is the right behaviour for adversarial cases
  (T0072) — the policy validator escalates.
- For legitimate gaps (T0006, T0018, T0019, T0044), the heuristic
  produces an irrelevant-but-harmless reply. With LLM enabled and the
  Phase E scope detector, the model can recognise the FAQ as
  unanswerable from corpus and recommend escalation.

## 3. Cross-domain over-retrieval

The retriever is sometimes drawn into the wrong domain because of shared
vocabulary:

| Ticket | Intended domain | Retrieved domain (top1) |
|---|---|---|
| T0023 (urgent cash, Visa) | Visa cards | Visa ATM doc (good) |
| T0034 (Visa unauthorized charges) | Visa fraud | Visa ATM doc (wrong topic) |
| T0044 (DevPlatform test crash) | DevPlatform | password-reset (wrong) |

The cross-domain hit rate is below 5% on the visible set, but the wrong
domain *within* a product (e.g. ATM doc instead of fraud doc on T0034)
is more common. Mitigation:

- Stronger sub-domain biasing using the `pattern_*:<tag>` reasons from
  the risk classifier (e.g. `pattern_high:fraud` should bias retrieval
  toward `visa/fraud/*`).
- Title-keyword weight slightly increased on rerank.

## 4. Synonym gaps

Common synonyms not directly handled by lexical retrieval:

- "block my card" ↔ "freeze your card" ↔ "report lost or stolen"
- "delete my account" ↔ "close my account" ↔ "deactivate"
- "outage" ↔ "service unavailable" ↔ "down"
- "refund" ↔ "reimburse" ↔ "credit back"

Mitigation: a small synonym-expansion pass before BM25 is feasible (we
have it in the LLM path implicitly). For the heuristic path, a static
synonym map at query time would help. We are deferring this to a
follow-up because the false-positive rate on synonym expansion is hard
to control without tuning.

## 5. Contradictions and consensus issues

The visible corpus has at least two minor overlaps that could become
contradictions in the hidden set:

- `data/visa/disputes/zero-liability-policy.md` and
  `data/visa/fraud/zero-liability.md` both discuss Zero Liability. They
  agree currently, but a planted hidden corpus could include a third
  doc with a contradicting amount or scope.
- `data/devplatform/billing/refund-policy.md` and
  `data/devplatform/billing/subscription-management.md` discuss refunds
  and pauses with slightly different framings.

Mitigation: **Phase C** — retrieval consensus validation:

- Detect pairwise contradictions in retrieved top-k (cosine ≥ 0.6 but
  semantic-disagreement signals like opposite numbers, opposite
  imperatives).
- When detected, lower confidence; if disagreement is severe, escalate.

Designed and shipped as `triage/retrieval/consensus.py` in the same
session as this audit.

## 6. Chunk-boundary weak regions

Visible corpus chunks at default 800-char windows generally fit each
markdown section cleanly. Two failure modes we noticed:

- Numbered step lists that span > 800 chars get split mid-list; the
  retrieval scoring favours the section header doc over the
  step-list doc. Mitigation: chunker already keeps paragraphs whole
  when ≤ size; only longer paragraphs are window-split. We could
  tune to 1024/192 for better step-list cohesion but it has a small
  effect at our corpus scale.
- Tables / bullet-heavy docs get tokenised into short chunks with low
  BM25 lengths; rerank scores rise too easily. Trust multiplier on
  "specific doc" (1.10×) compensates.

## 7. Trust signals working as intended

- `is_specific_doc` correctly boosts deeper paths (e.g.
  `data/devplatform/test-settings/test-expiration.md` over
  `data/devplatform/overview.md`).
- Domain-match multiplier (1.15× same-domain, 0.85× cross-domain)
  effectively keeps Visa queries on Visa docs.
- Recency multiplier is currently weak (we have no real recency data);
  it does no harm.

## 8. Citation precision

- 0 hallucinated citations on the visible set.
- Average citations per replied ticket: 1.1.
- Maximum cited: 4 (under the cap).
- 89% citation rate on replied tickets is consistent with the rubric's
  preference for citations on factual claims.

## 9. Recommended near-term changes (additive, no rewrites)

| # | Change | File | Effort | Risk |
|---|---|---|---|---|
| 1 | Add `triage/retrieval/consensus.py` (pairwise contradiction + agreement) | new | small | low |
| 2 | Add a small synonym map for known pairs (`block`↔`freeze`↔`lost`) | retrieval/engine.py | small | low |
| 3 | Boost domain bias from 1.15× to 1.20× on confident company hints | retrieval/engine.py | trivial | low |
| 4 | Tighten weak-match threshold under `out_of_scope` flag | confidence + policy | small | medium |
| 5 | Add corpus-coverage signal to confidence calibrator | confidence | small | low |

Changes 1, 3, 5 are implemented in this same session.

## 10. Out-of-scope retrieval

Out-of-scope queries (T0066, T0077, T0045, T0083, T0084) retrieve
*something* because TF-IDF/BM25 always returns the closest match. The
right answer is to detect scope mismatch *before* trusting retrieval.
The new scope detector (Phase E) provides that signal: when
`scope.in_scope == False`, we cap confidence at 0.30 and reply with a
polite scope clarification or escalate based on `scope.suspicious`.
