# Deterministic Support Triage Agent

Submission for the MLE Hiring Challenge.

A terminal-based, adversarially-robust support triage agent for tickets
across DevPlatform, Claude, and Visa. The system is built as a
deterministic pipeline where code controls the flow and the LLM is a
constrained analyst — not an autonomous agent.

## Quickstart

```bash
python -m pip install -r code/requirements.txt
python code/main.py --self-check    # 1-second smoke test
python code/main.py                 # write output.csv for support_tickets.csv
python code/validate_output.py      # structural validation of output.csv
python -m pytest code/tests -q      # 29 tests, no LLM required
```

Details in [code/README.md](code/README.md) and design in
[code/ARCHITECTURE.md](code/ARCHITECTURE.md).

## Layout

```
.
├── code/                    # agent implementation
├── data/                    # corpus + internal_tools.json
├── support_tickets/         # inputs + output.csv
├── docs/                    # Phase 0 architecture notes / threat model
└── README.md
```

## Highlights

- **Safety-first** — six independent safety layers (normalisation,
  injection detector, PII detector, risk classifier, policy validator,
  outbound PII scrubber). A single critical injection cannot bypass them.
- **Deterministic** — fixed seeds, `temperature=0`, sorted globs, stable
  ties. Structural CSV columns are byte-stable on repeat runs.
- **Grounded** — every cited document path is verified on disk. Citations
  are only included if the response materially overlaps with the chunk.
- **Auditable** — every stage emits structured logs; the `justification`
  column carries `key=value` traces.
- **Bounded LLM surface** — one strict-JSON LLM call per ticket; the LLM
  cannot decide escalation, risk level, citations, or final response.

## Self-assessment

See the "Self-Assessment" section at the end of
[code/ARCHITECTURE.md](code/ARCHITECTURE.md).
