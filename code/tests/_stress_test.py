"""Performance stress test: simulate the hidden-set load.

Runs the pipeline against a synthetic 150-ticket workload constructed by:
  1. Loading the visible support_tickets.csv (90 rows).
  2. Sampling 60 additional perturbed copies (different ticket_ids, same
     bodies with small modifications to defeat caches).
  3. Measuring wall-clock total and per-stage breakdown.

Prints a structured report and exits 0 if total < 60s and 1 otherwise.
"""

from __future__ import annotations

import csv
import json
import os
import sys
import time
from pathlib import Path
from statistics import mean, median

# Make `triage` importable.
_CODE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_CODE))

from triage.config import INPUT_TICKETS_CSV
from triage.pipeline import process_ticket_safe
from triage.retrieval import get_retriever


def _read_rows() -> list[dict[str, str]]:
    with INPUT_TICKETS_CSV.open(encoding="utf-8") as fh:
        return list(csv.DictReader(fh))


def _perturb(text: str, idx: int) -> str:
    """Tiny perturbation so caches do not amortise across copies."""
    return f"[ref={idx}] {text}"


def _expand_to(n: int, base: list[dict[str, str]]) -> list[dict[str, str]]:
    out = list(base)
    i = 0
    while len(out) < n:
        src = base[i % len(base)]
        new = dict(src)
        new["ticket_id"] = f"S{len(out):04d}"
        # Perturb the user content inside the JSON-encoded issue.
        try:
            parsed = json.loads(src.get("issue", "[]"))
            if isinstance(parsed, list):
                for t in parsed:
                    if isinstance(t, dict) and t.get("role") == "user":
                        t["content"] = _perturb(t.get("content", ""), len(out))
                        break
                new["issue"] = json.dumps(parsed, ensure_ascii=False)
        except Exception:
            pass
        out.append(new)
        i += 1
    return out


def main() -> int:
    os.environ.setdefault("TRIAGE_LLM_PROVIDER", "off")

    t0 = time.perf_counter()
    retriever = get_retriever()
    cold_index_s = time.perf_counter() - t0

    rows = _read_rows()
    full = _expand_to(150, rows)

    per_ticket: list[float] = []
    t_total = time.perf_counter()
    n_replied = n_escalated = n_pii = 0
    for r in full:
        t = time.perf_counter()
        out = process_ticket_safe(
            ticket_id=r.get("ticket_id", ""),
            issue=r.get("issue", ""),
            subject=r.get("subject", ""),
            company=r.get("company") or None,
        )
        per_ticket.append((time.perf_counter() - t) * 1000)
        if out.status == "replied":
            n_replied += 1
        else:
            n_escalated += 1
        if out.pii_detected:
            n_pii += 1
    elapsed = time.perf_counter() - t_total

    print(f"== Performance Stress Test ==")
    print(f"  corpus chunks indexed   : {retriever.n_chunks}")
    print(f"  cold-start index build  : {cold_index_s*1000:.1f} ms")
    print(f"  tickets processed       : {len(full)}")
    print(f"  total wall clock        : {elapsed:.3f} s")
    print(f"  tickets/sec             : {len(full)/max(elapsed,1e-9):.1f}")
    print(f"  per-ticket mean         : {mean(per_ticket):.2f} ms")
    print(f"  per-ticket median       : {median(per_ticket):.2f} ms")
    print(f"  per-ticket p95          : {sorted(per_ticket)[int(0.95*len(per_ticket))-1]:.2f} ms")
    print(f"  per-ticket max          : {max(per_ticket):.2f} ms")
    print(f"  replied / escalated     : {n_replied} / {n_escalated}")
    print(f"  pii_detected            : {n_pii}")

    budget_s = 60.0
    if elapsed > budget_s:
        print(f"FAIL: exceeded {budget_s}s budget")
        return 1
    print(f"PASS: well under {budget_s}s budget")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
