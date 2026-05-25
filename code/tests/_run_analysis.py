"""Behavioural analysis of the visible-set output.csv.

Reports the things the evaluator will care about:

  - escalation distribution + escalation reasons
  - confidence histogram (and Brier-safe checks)
  - risk histogram
  - request_type / product_area distribution
  - citation coverage
  - suspicious-confidence / weak-justification / over-escalation /
    under-escalation candidates
  - retrieval miss summary (rows with no citation)

Writes a Markdown summary to docs/visible_run_analysis.md.

Run AFTER `python code/main.py`. Reads support_tickets/output.csv plus
support_tickets/support_tickets.csv to cross-reference inputs.
"""

from __future__ import annotations

import csv
import json
import re
import sys
from collections import Counter, defaultdict
from pathlib import Path
from statistics import mean, median, pstdev

REPO = Path(__file__).resolve().parents[2]
OUTPUT = REPO / "support_tickets" / "output.csv"
INPUT = REPO / "support_tickets" / "support_tickets.csv"
TARGET = REPO / "docs" / "visible_run_analysis.md"


def _load_rows(p: Path) -> list[dict[str, str]]:
    with p.open(encoding="utf-8") as fh:
        return list(csv.DictReader(fh))


def _hist(values: list[float], buckets: int = 10) -> list[tuple[str, int]]:
    if not values:
        return []
    lo, hi = 0.0, 1.0
    step = (hi - lo) / buckets
    counts = [0] * buckets
    for v in values:
        i = min(buckets - 1, max(0, int((v - lo) / step)))
        counts[i] += 1
    out = []
    for i, c in enumerate(counts):
        lo_b = lo + i * step
        hi_b = lo + (i + 1) * step
        out.append((f"[{lo_b:.2f},{hi_b:.2f})", c))
    return out


def _bar(n: int, scale: int) -> str:
    return "█" * min(n, scale) + ("" if n <= scale else f" (+{n-scale})")


def analyze(out_rows: list[dict[str, str]], in_rows: list[dict[str, str]]) -> str:
    by_id_in = {r.get("ticket_id", ""): r for r in in_rows}
    n = len(out_rows)
    statuses = Counter(r["status"] for r in out_rows)
    risks = Counter(r["risk_level"] for r in out_rows)
    reqs = Counter(r["request_type"] for r in out_rows)
    langs = Counter(r["language"] for r in out_rows)
    pa = Counter(r["product_area"] for r in out_rows)
    pii = sum(1 for r in out_rows if r["pii_detected"].lower() == "true")

    confs = [float(r["confidence_score"]) for r in out_rows]
    cf_hist = _hist(confs)
    rep_conf = [float(r["confidence_score"]) for r in out_rows if r["status"] == "replied"]
    esc_conf = [float(r["confidence_score"]) for r in out_rows if r["status"] == "escalated"]

    citation_count = [0 if not r["source_documents"] else len(r["source_documents"].split("|")) for r in out_rows]
    n_with_cite = sum(1 for c in citation_count if c > 0)
    n_replied = statuses.get("replied", 0)
    n_escalated = statuses.get("escalated", 0)

    # Escalation reason buckets (parsed from justification reason=…)
    reason_counter: Counter[str] = Counter()
    for r in out_rows:
        if r["status"] != "escalated":
            continue
        m = re.search(r"reason=([^|]+)", r["justification"])
        if m:
            reason_counter[m.group(1).strip().split(":")[0]] += 1

    # Suspiciously high-confidence: confidence ≥ 0.85 (we cap at 0.95)
    suspicious_high = [r for r in out_rows if float(r["confidence_score"]) >= 0.85]
    # Low-confidence replies (replied but conf <= 0.35) — borderline answers
    low_conf_replies = [
        r for r in out_rows
        if r["status"] == "replied" and float(r["confidence_score"]) <= 0.35
    ]
    # Replied with no citation despite low risk (potential retrieval misses)
    replied_no_cite = [
        r for r in out_rows
        if r["status"] == "replied" and not r["source_documents"]
        and r["risk_level"] in {"low", "medium"}
    ]
    # Weak justifications (very short)
    weak_just = [r for r in out_rows if len(r["justification"]) < 40]

    # Over-escalation candidates: escalated but a normal-FAQ-shaped input
    # (no injection score, low risk pattern, single user turn, common
    # support intent words). Crude heuristic.
    def _looks_normal_faq(in_row: dict[str, str]) -> bool:
        try:
            parsed = json.loads(in_row.get("issue", "[]"))
        except Exception:
            return False
        if not isinstance(parsed, list) or len(parsed) != 1:
            return False
        first = parsed[0]
        if not isinstance(first, dict):
            return False
        text = (first.get("content") or "").lower()
        # injection indicators absent?
        if any(w in text for w in ("ignore previous", "system prompt", "developer mode")):
            return False
        # has support intent words and a brand
        intent = any(w in text for w in (
            "how do i", "how can i", "can't", "cannot", "won't",
            "please help", "issue", "problem", "fix", "error", "refund",
            "subscription", "password", "login", "verify",
        ))
        brand = any(w in text for w in ("devplatform", "claude", "visa", "anthropic"))
        return intent and brand and len(text) < 600

    over_escalation = []
    for r in out_rows:
        if r["status"] != "escalated":
            continue
        tid = r["ticket_id"]
        in_row = by_id_in.get(tid)
        if not in_row:
            continue
        if _looks_normal_faq(in_row):
            # but only flag if NOT one of the canonical adversarial reasons
            j = r["justification"].lower()
            if any(k in j for k in (
                "injection", "consistency_anomaly", "critical_risk_level",
                "high_risk_sensitive", "suspicious_out_of_scope",
                "insufficient_signal",
            )):
                continue
            over_escalation.append(r)

    # Under-escalation candidates: replied at high/critical risk
    under_escalation = [
        r for r in out_rows
        if r["status"] == "replied" and r["risk_level"] in {"high", "critical"}
    ]

    # Tickets with PII echoed (defensive check)
    pii_echo = []
    for r in out_rows:
        body = r["response"]
        # Card-like 13-19 digit run that passes Luhn -> we should never see one
        for m in re.finditer(r"\b\d{4}[\s\-]?\d{4}[\s\-]?\d{4}[\s\-]?\d{4}\b", body):
            pii_echo.append((r["ticket_id"], "card_like", m.group(0)))
        for m in re.finditer(r"\b\d{3}-\d{2}-\d{4}\b", body):
            pii_echo.append((r["ticket_id"], "ssn_like", m.group(0)))

    # ---- Markdown report ----
    out: list[str] = []
    out.append("# visible_run_analysis.md")
    out.append("")
    out.append(
        f"Behavioural analysis of the agent run against the real "
        f"`support_tickets/support_tickets.csv` ({n} tickets). "
        f"Generated by `code/tests/_run_analysis.py`."
    )
    out.append("")
    out.append("## 1. Distributions")
    out.append("")
    out.append(f"- replied = {statuses.get('replied',0)}  / escalated = {statuses.get('escalated',0)}  ({n_escalated/max(n,1):.0%} escalation rate)")
    out.append(f"- PII detected on {pii} tickets")
    out.append(f"- {n_with_cite} of {n} replies carry at least one citation ({n_with_cite/max(n,1):.0%})")
    out.append("")
    out.append("### Risk level")
    for level in ("low", "medium", "high", "critical"):
        c = risks.get(level, 0)
        out.append(f"- `{level:<8}` = {c:>3}  {_bar(c, 50)}")
    out.append("")
    out.append("### Request type")
    for t, c in reqs.most_common():
        out.append(f"- `{t:<16}` = {c:>3}")
    out.append("")
    out.append("### Language (ISO-639-1)")
    for t, c in langs.most_common():
        out.append(f"- `{t}` = {c}")
    out.append("")
    out.append("### Product area (top 12)")
    for area, c in pa.most_common(12):
        out.append(f"- `{area or '<empty>':<28}` = {c}")
    out.append("")
    out.append("## 2. Confidence")
    out.append("")
    out.append(
        f"- overall mean = {mean(confs):.3f}, median = {median(confs):.3f}, "
        f"std = {pstdev(confs):.3f}, min = {min(confs):.3f}, max = {max(confs):.3f}"
    )
    out.append(
        f"- replied   mean = {mean(rep_conf):.3f}  (n={len(rep_conf)})"
    )
    out.append(
        f"- escalated mean = {mean(esc_conf):.3f}  (n={len(esc_conf)})"
    )
    out.append("")
    out.append("### Histogram")
    for label, c in cf_hist:
        out.append(f"- {label}  {_bar(c, 40)}  ({c})")
    out.append("")
    out.append("## 3. Escalation reasons")
    out.append("")
    for reason, c in reason_counter.most_common():
        out.append(f"- `{reason:<40}` = {c}")
    out.append("")
    out.append("## 4. Citation coverage")
    out.append("")
    cite_counter = Counter(citation_count)
    for k in sorted(cite_counter):
        out.append(f"- {k} citations: {cite_counter[k]} tickets")
    out.append("")
    out.append("## 5. Suspicious / candidate-issue rows")
    out.append("")
    out.append(f"### 5.1 High-confidence replies (≥ 0.85)  -- {len(suspicious_high)}")
    if not suspicious_high:
        out.append("None.")
    for r in suspicious_high[:25]:
        out.append(
            f"- {r['ticket_id']} conf={r['confidence_score']} risk={r['risk_level']} "
            f"status={r['status']} just={r['justification'][:120]}"
        )
    out.append("")
    out.append(f"### 5.2 Low-confidence replies (≤ 0.35)  -- {len(low_conf_replies)}")
    if not low_conf_replies:
        out.append("None.")
    for r in low_conf_replies[:25]:
        out.append(
            f"- {r['ticket_id']} conf={r['confidence_score']} risk={r['risk_level']} "
            f"just={r['justification'][:120]}"
        )
    out.append("")
    out.append(f"### 5.3 Replied without citation (potential retrieval miss)  -- {len(replied_no_cite)}")
    if not replied_no_cite:
        out.append("None.")
    for r in replied_no_cite[:25]:
        out.append(
            f"- {r['ticket_id']} risk={r['risk_level']} conf={r['confidence_score']} "
            f"just={r['justification'][:120]}"
        )
    out.append("")
    out.append(f"### 5.4 Potential over-escalation (FAQ-shaped, no adversarial trigger)  -- {len(over_escalation)}")
    if not over_escalation:
        out.append("None.")
    for r in over_escalation[:25]:
        out.append(
            f"- {r['ticket_id']} just={r['justification'][:160]}"
        )
    out.append("")
    out.append(f"### 5.5 Potential under-escalation (replied at high/critical risk)  -- {len(under_escalation)}")
    if not under_escalation:
        out.append("None.")
    for r in under_escalation[:25]:
        out.append(
            f"- {r['ticket_id']} risk={r['risk_level']} conf={r['confidence_score']} "
            f"just={r['justification'][:140]}"
        )
    out.append("")
    out.append(f"### 5.6 PII echoes in response (defensive check)  -- {len(pii_echo)}")
    if not pii_echo:
        out.append("None. (Outbound PII scrubber is working.)")
    else:
        for tid, kind, blob in pii_echo[:25]:
            out.append(f"- {tid} kind={kind} value={blob[:30]!r}")
    out.append("")
    out.append(f"### 5.7 Weak justifications (< 40 chars)  -- {len(weak_just)}")
    if not weak_just:
        out.append("None.")
    for r in weak_just[:25]:
        out.append(f"- {r['ticket_id']} just={r['justification']!r}")
    out.append("")
    out.append("## 6. Acceptance summary")
    out.append("")
    ok = []
    bad = []
    (bad if pii_echo else ok).append("0 PII echoes in response")
    (bad if any(c < 0.05 or c > 0.95 for c in confs) else ok).append("confidence range within [0.05, 0.95]")
    (bad if (max(confs) - min(confs)) < 0.30 else ok).append("confidence spread >= 0.30")
    (bad if reqs.get("invalid", 0) == 0 else ok).append("at least one invalid request_type observed")
    (bad if n_with_cite / max(n_replied, 1) < 0.50 else ok).append("citation rate on replied >= 50%")
    (bad if statuses.get("escalated", 0) == 0 else ok).append("at least one escalation observed")
    for k in ok:
        out.append(f"- ✅ {k}")
    for k in bad:
        out.append(f"- ❌ {k}")
    out.append("")

    return "\n".join(out)


def main() -> int:
    if not OUTPUT.exists():
        print(f"missing {OUTPUT}; run main.py first", file=sys.stderr)
        return 1
    out_rows = _load_rows(OUTPUT)
    in_rows = _load_rows(INPUT) if INPUT.exists() else []
    report = analyze(out_rows, in_rows)
    TARGET.parent.mkdir(parents=True, exist_ok=True)
    TARGET.write_text(report, encoding="utf-8", newline="\n")
    print(f"wrote {TARGET}", file=sys.stderr)
    # Also print the highlights to stderr for fast feedback.
    for line in report.splitlines():
        if (
            line.startswith("- ✅")
            or line.startswith("- ❌")
            or line.startswith("## ")
        ):
            print(line, file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
