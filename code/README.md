# Support triage agent — run instructions

Deterministic, adversarially-robust support triage agent. For the full
design rationale see [ARCHITECTURE.md](ARCHITECTURE.md).

## Requirements

- Python 3.9+ (tested on 3.14).
- ~200 MB disk for the dependency wheels.
- No GPU.
- (Optional) `ANTHROPIC_API_KEY` or `OPENAI_API_KEY` for the LLM-enabled
  path. The pipeline runs without them via a deterministic heuristic.

## Setup (3 commands)

From the repository root:

```bash
python -m pip install -r code/requirements.txt
cp .env.example .env             # then edit to add an API key (optional)
python code/main.py --self-check # smoke test, ~1s
```

If you do not set an API key, the pipeline uses its heuristic fallback
path. Set `TRIAGE_LLM_PROVIDER=off` to force the heuristic path even when
keys are present (useful for fully reproducible runs without provider
non-determinism).

## Run on the visible test set

```bash
python code/main.py
```

This reads `support_tickets/support_tickets.csv` and writes
`support_tickets/output.csv`. On the included 20-row sample the pipeline
completes in ~0.1 s on standard hardware. For ~150 tickets it completes
well under the 3-minute evaluation budget.

## Run on the sample set (with expected outputs)

```bash
python code/main.py --sample
```

## Validate the output CSV structurally

```bash
python code/validate_output.py
```

This checks column presence, enum values, `actions_taken` is valid JSON,
`confidence_score` is in [0, 1], `source_documents` paths exist on disk,
and `language` looks like an ISO-639-1 code. It does not evaluate
response quality.

## Run the tests

```bash
python -m pytest code/tests -q
```

Tests cover the safety detectors (injection, PII, language, risk), the
tool registry / validator, retrieval determinism, and an end-to-end
adversarial suite. The tests do not require an LLM key.

## Configuration

All tunables live in `code/triage/config.py`. Common environment overrides:

| Env var | Default | Meaning |
|---|---|---|
| `ANTHROPIC_API_KEY` | — | Enables Anthropic LLM path. |
| `OPENAI_API_KEY` | — | Enables OpenAI LLM path. |
| `TRIAGE_LLM_PROVIDER` | `auto` | `auto`, `anthropic`, `openai`, or `off`. |
| `TRIAGE_ANTHROPIC_MODEL` | `claude-haiku-4-5-20251001` | Override the model. |
| `TRIAGE_OPENAI_MODEL` | `gpt-4o-mini` | Override the model. |
| `TRIAGE_LOG_LEVEL` | `INFO` | structlog level. |

## What the pipeline produces

`support_tickets/output.csv` columns (in this exact order):

```
ticket_id, status, product_area, response, justification, request_type,
confidence_score, source_documents, risk_level, pii_detected, language,
actions_taken
```

`actions_taken` is always a valid JSON array (possibly empty).
`source_documents` is `|`-separated, possibly empty. `confidence_score`
is in [0.05, 0.95].

## Determinism

The structural columns (everything except `response`) are byte-stable
across repeat runs on the same input + corpus + LLM version. Verified by
`code/tests/test_adversarial.py::test_deterministic_outputs_across_runs`.

The `response` text may differ across LLM provider versions (a provider's
underlying weights can change). Set `TRIAGE_LLM_PROVIDER=off` for full
byte-stable output text.

## File-level safety notes

- No file under `data/` ever drives control flow; corpus is data, not
  instructions. The pipeline strips injection markers from retrieved
  chunks before they reach the LLM.
- The LLM never sees raw PII. An additional outbound PII pass scrubs the
  generated response.
- `actions_taken` is built from typed Pydantic models — it cannot be
  invalid JSON.
- Citations are only emitted for chunks the response materially overlaps
  with, and each cited path is `os.path.exists`-checked before write.

## Where to start reading

If you have 5 minutes:

1. [ARCHITECTURE.md](ARCHITECTURE.md) (sections 2 and 5).
2. `code/triage/pipeline.py` — the orchestrator.
3. `code/triage/policy/validator.py` — the FINAL controller. This is
   where escalation decisions are made.
4. `code/triage/safety/injection.py` — the regex pack that drives the
   adversarial gate.

If you have 20 minutes, add:

5. `code/triage/retrieval/engine.py` — BM25 + TF-IDF + rerank.
6. `code/triage/tools/validator.py` — tool schema + prereq + idempotency.
7. `docs/threat_model.md` — what we are defending against and why.

## License / disclaimer

This is an evaluation submission for the MLE Hiring Challenge.
