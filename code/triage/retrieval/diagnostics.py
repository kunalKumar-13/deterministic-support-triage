"""Per-run retrieval diagnostics.

Tracks across the run:

  - count of no-grounding retrievals
  - count of weak retrievals (top1 < threshold)
  - count of single-source retrievals
  - count of consensus conflicts (numeric / imperative disagreement)
  - histogram of top1 scores
  - histogram of agreement scores
  - top low-retrieval ticket ids (worst-case examples)

This is observability, not control flow. The pipeline calls
`record(...)` after each retrieval; `dump_report(...)` writes a markdown
summary at end of run.
"""

from __future__ import annotations

import threading
from dataclasses import dataclass, field
from pathlib import Path
from statistics import mean, median, pstdev

from ..config import REPO_ROOT
from ..models import RetrievalResult


@dataclass
class _RunStats:
    n: int = 0
    no_grounding: int = 0
    weak_match: int = 0
    single_source: int = 0
    consensus_numeric_conflict: int = 0
    consensus_imperative_conflict: int = 0
    top1_scores: list[float] = field(default_factory=list)
    agreement_scores: list[float] = field(default_factory=list)
    worst_examples: list[tuple[str, float, str]] = field(default_factory=list)  # (ticket_id, top1, query_excerpt)


_lock = threading.Lock()
_stats: _RunStats | None = None


def reset() -> None:
    global _stats
    with _lock:
        _stats = _RunStats()


def record(
    *,
    ticket_id: str,
    query: str,
    retrieval: RetrievalResult,
    consensus,
) -> None:
    global _stats
    with _lock:
        if _stats is None:
            _stats = _RunStats()
        _stats.n += 1
        if retrieval.no_grounding:
            _stats.no_grounding += 1
        if retrieval.weak_match:
            _stats.weak_match += 1
        _stats.top1_scores.append(retrieval.top1_score)
        _stats.agreement_scores.append(retrieval.agreement)
        if consensus is not None:
            if getattr(consensus, "single_source_only", False):
                _stats.single_source += 1
            tags = getattr(consensus, "tags", ()) or ()
            if any("numeric" in t for t in tags):
                _stats.consensus_numeric_conflict += 1
            if any("imperative" in t for t in tags):
                _stats.consensus_imperative_conflict += 1
        # Track the 10 worst (lowest top1) examples for later inspection.
        _stats.worst_examples.append((ticket_id, retrieval.top1_score, query[:80]))
        _stats.worst_examples.sort(key=lambda t: t[1])
        del _stats.worst_examples[10:]


def dump_report(out_path: Path | None = None) -> Path:
    global _stats
    if _stats is None or _stats.n == 0:
        return Path()
    s = _stats
    target = out_path or (REPO_ROOT / "docs" / "retrieval_run_diagnostics.md")
    target.parent.mkdir(parents=True, exist_ok=True)
    lines: list[str] = []
    lines.append("# retrieval_run_diagnostics.md")
    lines.append("")
    lines.append(f"Per-run retrieval diagnostics across {s.n} tickets.")
    lines.append("")
    lines.append("## Aggregate counters")
    lines.append("")
    lines.append(f"- no_grounding (top1 ~ 0) ......... {s.no_grounding}  ({s.no_grounding/s.n:.0%})")
    lines.append(f"- weak_match (top1 < threshold) ... {s.weak_match}  ({s.weak_match/s.n:.0%})")
    lines.append(f"- single_source_only .............. {s.single_source}  ({s.single_source/s.n:.0%})")
    lines.append(f"- consensus.numeric_conflict ...... {s.consensus_numeric_conflict}")
    lines.append(f"- consensus.imperative_conflict ... {s.consensus_imperative_conflict}")
    lines.append("")
    lines.append("## Top1 score distribution")
    lines.append("")
    lines.append(
        f"- min={min(s.top1_scores):.3f}  median={median(s.top1_scores):.3f}  "
        f"mean={mean(s.top1_scores):.3f}  max={max(s.top1_scores):.3f}  "
        f"std={pstdev(s.top1_scores):.3f}"
    )
    lines.append("")
    lines.append("## Agreement (top1 vs top2 jaccard) distribution")
    lines.append("")
    lines.append(
        f"- min={min(s.agreement_scores):.3f}  median={median(s.agreement_scores):.3f}  "
        f"mean={mean(s.agreement_scores):.3f}  max={max(s.agreement_scores):.3f}"
    )
    lines.append("")
    lines.append("## 10 worst-case retrievals (lowest top1 score)")
    lines.append("")
    for tid, score, q in s.worst_examples:
        lines.append(f"- `{tid}`  top1={score:.3f}  query={q!r}")
    target.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return target
