# Deterministic Support Triage Agent

A terminal-based, adversarially-robust support triage agent for tickets
across DevPlatform, Claude, and Visa.

> This system prioritises deterministic orchestration, safe escalation,
> adversarial robustness, and calibrated confidence over unconstrained
> autonomy.

## 1. Design philosophy

The agent is a **deterministic pipeline** where code controls the flow
and the LLM is a constrained analyst. The LLM is invoked **exactly once
per ticket** inside an unforgeable sentinel block; it returns a typed
JSON object that the policy validator adjudicates. The LLM cannot decide
escalation, cannot execute tools directly, and never sees raw PII or
internal control text.

Five design decisions, in priority order:

| # | Decision | Why |
|---|---|---|
| A | **Deterministic pipeline over autonomous agents** | Reproducible runs, bounded attack surface, auditable failures. |
| B | **Escalation bias under uncertainty** | Asymmetric cost of wrong destructive actions; 60% of the score comes from a hidden adversarial set. |
| C | **Explicit trust boundaries** | Untrusted ticket text never leaves a `<<<TICKET>>>` block; corpus chunks are grounding, never instructions. |
| D | **Retrieval consensus validation** | When retrieved chunks contradict each other (numeric or imperative disagreement), confidence drops and the policy validator escalates. |
| E | **Brier-aware confidence calibration** | Eight signals + multiplicative penalties; escalations capped at 0.60; never report ≥ 0.95. |

The detailed rationale lives in
[code/ARCHITECTURE.md §0](code/ARCHITECTURE.md).

## 2. Why deterministic

- **Reproducibility**: running the agent twice on the same input
  produces a byte-identical structural CSV. Verified by SHA-256
  comparison across two consecutive runs.
- **Bounded failure modes**: the LLM produces one JSON object inside a
  schema-validated frame. The policy validator drops anything outside
  the frame. Worst-case behaviour is "escalate to a human" — never a
  destructive action.
- **Safer tool execution**: every tool call is JSON-schema validated,
  risk-gated, prerequisite-chained, and idempotency-checked. Identity
  verification is required before any destructive action.
- **Easier auditing**: each stage emits structured logs, and the
  `justification` column in the output CSV carries a machine-friendly
  `key=value | reasons=tag1,tag2` trace. Every decision is reverse-
  traceable to a rule, signal, or pattern.

## 3. Threat model (summary)

The full model is in [docs/threat_model.md](docs/threat_model.md). The
agent defends against:

- **Prompt injection** — direct, multilingual (EN/ES/FR/DE/HI/ZH/AR),
  obfuscated (zero-width, leet, Cyrillic homoglyphs, base64).
- **Indirect / retrieval-borne injection** — imperative-shaped chunks
  flagged and trust-downgraded; corpus statements never drive control
  flow.
- **Authority impersonation** — "I'm from the Visa security team",
  fake `AUTH_CODE: ...` audit pretexts, fake monitoring alerts, third-
  party authorization claims ("on behalf of the cardholder").
- **PII leakage** — Luhn-validated cards, SSN, IBAN, tokens, emails,
  phones, IPs, addresses. Two-pass scrub (inbound + outbound).
- **Social engineering across turns** — identity-claim shifts (card
  last-4 / email / phone changes between turns), pressure tactics,
  fake "previous agent promised" claims.
- **Out-of-scope manipulation** — capability requests (write me a
  scraper, give me code to delete files) → escalate; harmless OOS
  (jokes, trivia) → polite reply with `request_type=invalid`.
- **Tool manipulation** — pasted tool-JSON, refund coercion via PII,
  multi-destructive-intent in one payload.

## 4. Performance metrics

Final state on the visible 90-ticket test set, LLM off (heuristic path):

| Metric | Value |
|---|---:|
| Tests passing | **132 / 132** |
| Wall-clock on 90 tickets | **0.55 s** |
| Wall-clock on 150-ticket stress | **0.82 s** (182 tickets/s, p95 12.7 ms) |
| Hallucinated citations | **0** |
| PII echoes in response | **0** |
| Determinism (SHA-256 of `output.csv`, 2 runs) | **byte-identical** |
| Confidence range | 0.15 – 0.78 (no flat scores) |
| Escalation rate (visible set) | 31% |
| Languages detected | en, zh, fr, de, es, it |
| Cold-start corpus index build | 45 ms |

Acceptance checks (all green, see
[docs/visible_run_analysis.md](docs/visible_run_analysis.md)):

- ✅ 0 PII echoes in response
- ✅ confidence range within [0.05, 0.95]
- ✅ confidence spread ≥ 0.30
- ✅ at least one `invalid` request_type observed
- ✅ citation rate on replied ≥ 50%
- ✅ at least one escalation observed

## 5. Architecture at a glance

```
   ┌──────────────────────────────────────────────────────┐
   │                  UNTRUSTED  INPUT                    │
   │   (issue JSON, subject, company)                     │
   └──────────────────────────────┬───────────────────────┘
                                  │
                       ┌──────────▼──────────┐
                       │   Preprocessing     │   NFKC, zero-width strip,
                       │                     │   per-turn parse, truncation
                       └──────────┬──────────┘
                                  │
                       ┌──────────▼──────────┐
                       │   Safety Engine     │   injection ◇ PII ◇ language
                       │                     │   risk (tagged reasons)
                       └──────────┬──────────┘
                                  │
                       ┌──────────▼──────────┐
                       │   Scope detector    │   in / harmless OOS /
                       │                     │   suspicious OOS / ambiguous
                       └──────────┬──────────┘
                                  │
                       ┌──────────▼──────────┐
                       │   Multi-turn        │   identity shift ◇
                       │   consistency       │   cross-ticket ref ◇ soft exfil
                       └──────────┬──────────┘
                                  │
                       ┌──────────▼──────────┐
                       │   Routing           │   brand gazetteer +
                       │                     │   company hint reconciliation
                       └──────────┬──────────┘
                                  │
                       ┌──────────▼──────────┐
                       │   Retrieval         │   BM25 + TF-IDF +
                       │                     │   RRF + trust rerank
                       └──────────┬──────────┘
                                  │
                       ┌──────────▼──────────┐
                       │   Consensus check   │   numeric / imperative
                       │                     │   disagreement detection
                       └──────────┬──────────┘
                                  │
                       ┌──────────▼──────────┐
                       │   LLM decision      │   ONE strict-JSON call
                       │   (heuristic        │   inside <<<TICKET>>> /
                       │    fallback)        │   <<<DOC>>> sentinels
                       └──────────┬──────────┘
                                  │
                       ┌──────────▼──────────┐
                       │  Policy validator   │   ← FINAL CONTROLLER.
                       │                     │   Rules 0–6 + 4b–4g.
                       │                     │   Owns status, risk_level,
                       │                     │   action set, confidence cap.
                       └──────────┬──────────┘
                                  │
                       ┌──────────▼──────────┐
                       │  Response generator │   grounded paraphrase +
                       │                     │   PII scrub + markdown strip
                       │                     │   + uncertainty rationale
                       └──────────┬──────────┘
                                  │
                       ┌──────────▼──────────┐
                       │  Confidence         │   8 signals + multiplicative
                       │  calibration        │   penalties + policy cap
                       └──────────┬──────────┘
                                  │
                       ┌──────────▼──────────┐
                       │  Output formatter   │   schema-correct CSV row
                       └──────────┬──────────┘
                                  │
                                  ▼
                          support_tickets/output.csv
```

Trust zones:

```
   ┌─ TRUSTED ─────────────────────────────────────────────────┐
   │  system prompt, validator code, tool schema, templates    │
   └──────────────────────────▲────────────────────────────────┘
                              │  invariant: lower zones never
                              │  influence upper-zone behavior
   ┌─ SEMI-TRUSTED ───────────┴────────────────────────────────┐
   │  corpus docs (grounding only — imperatives are stripped)  │
   └──────────────────────────▲────────────────────────────────┘
                              │
   ┌─ UNTRUSTED ──────────────┴────────────────────────────────┐
   │  ticket text (any role), subject, company, URLs           │
   └───────────────────────────────────────────────────────────┘
```

## 6. Quickstart

```bash
python -m pip install -r code/requirements.txt
python code/main.py --self-check    # 1-second smoke test
python code/main.py                 # writes support_tickets/output.csv
python code/validate_output.py      # structural validation
python -m pytest code/tests -q      # 132 tests, no LLM required
```

Optional: set `ANTHROPIC_API_KEY` or `OPENAI_API_KEY` to enable the LLM
path. Set `TRIAGE_LLM_PROVIDER=off` to force the deterministic
heuristic path for fully reproducible runs.

## 7. Repository layout

```
.
├── README.md                   # this file
├── git_history.txt             # 26 conventional-commit messages
├── .env.example                # API-key template; .env is gitignored
├── .gitignore
│
├── code/                       # agent implementation
│   ├── ARCHITECTURE.md         # design rationale (sections A–E)
│   ├── README.md               # exact run / configure instructions
│   ├── main.py                 # CLI entry point
│   ├── validate_output.py      # structural output validator
│   ├── requirements.txt        # pinned dependencies
│   ├── triage/                 # the pipeline (one package per stage)
│   │   ├── safety/             # injection / PII / risk / language
│   │   ├── scope/              # in / harmless-OOS / suspicious-OOS
│   │   ├── conversation/       # multi-turn consistency
│   │   ├── retrieval/          # BM25+TFIDF, consensus, diagnostics
│   │   ├── decision/           # strict-JSON LLM + heuristic fallback
│   │   ├── policy/             # final controller (validator.py)
│   │   ├── tools/              # registry + JSON-schema validation
│   │   ├── response/           # grounded reply + scrub + cite
│   │   ├── confidence/         # Brier-aware calibrator
│   │   ├── state/              # explicit state machine
│   │   ├── pipeline.py         # orchestrator
│   │   ├── models.py           # Pydantic domain models
│   │   ├── config.py           # paths, seeds, thresholds
│   │   └── logging_setup.py
│   └── tests/                  # 132 tests (unit + adversarial)
│       ├── test_safety.py
│       ├── test_adversarial.py
│       ├── test_retrieval.py
│       ├── test_tools.py
│       └── adversarial/        # 19-category red-team suite
│
├── data/                       # corpus + tool schema
│   ├── api_specs/internal_tools.json
│   ├── devplatform/
│   ├── claude/
│   └── visa/
│
├── support_tickets/
│   ├── support_tickets.csv     # 90 real tickets (input)
│   ├── sample_support_tickets.csv
│   └── output.csv              # agent output
│
└── docs/                       # all docs in one place
    ├── threat_model.md
    ├── architecture_notes.md           # Phase 0 pre-implementation
    ├── corpus_analysis.md              # Phase 0
    ├── hidden_test_predictions.md      # what the hidden set likely contains
    ├── retrieval_gaps.md
    ├── retrieval_failure_modes.md
    ├── retrieval_run_diagnostics.md    # auto-generated per run
    ├── policy_gaps.md
    ├── adversarial_matrix.md           # 19-category coverage
    ├── visible_run_analysis.md         # behavioural audit
    ├── performance.md
    ├── interview_prep.md
    ├── interview_war_game.md           # 30 hostile Q+A
    └── FREEZE.md                       # what is locked, what is not
```

## 8. Self-assessment

See the "Self-Assessment" section at the bottom of
[code/ARCHITECTURE.md](code/ARCHITECTURE.md), plus the honest gap list
in [docs/interview_war_game.md](docs/interview_war_game.md).
