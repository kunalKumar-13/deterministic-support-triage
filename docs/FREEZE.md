# FREEZE.md

The architecture is **frozen** at commit `bb00cf8` (HEAD).

After this point, only the following classes of change are allowed:

- bug fixes (with a regression test)
- wording polish in response templates / docstrings
- documentation improvements
- new tests (never relaxing existing tests)

The following are explicitly OUT OF SCOPE post-freeze:

- adding LangGraph / agent frameworks
- adding recursive planners / multi-agent loops
- adding memory systems / scratchpads
- rewriting retrieval (BM25 + TF-IDF + RRF + rerank is final)
- rewriting orchestration (single-LLM-call + policy validator is final)
- changing the policy rule ordering
- changing the public `OUTPUT_COLUMNS` schema

## What is frozen

| Layer | Frozen file(s) |
|---|---|
| Pipeline order | `code/triage/pipeline.py` |
| Policy rule order | `code/triage/policy/validator.py` |
| Retrieval algorithm | `code/triage/retrieval/{engine,index,chunking}.py` |
| Confidence formula | `code/triage/confidence/calibration.py` |
| Tool schema | `data/api_specs/internal_tools.json` |
| Output CSV schema | `code/triage/config.py::OUTPUT_COLUMNS` |
| Trust-boundary sentinels | `code/triage/decision/prompts.py` |

## Final state at freeze

- 132 / 132 tests pass (113 unit + adversarial + 19 final red-team).
- 90 visible tickets processed in 0.53 s.
- Output `support_tickets/output.csv` is byte-identical across two
  consecutive runs (SHA-256 verified).
- All six acceptance checks in `docs/visible_run_analysis.md` are green.
- 25 git commits with conventional-commit prefixes;
  `git_history.txt` written.
