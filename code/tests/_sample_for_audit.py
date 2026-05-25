"""Stratified sample of output.csv for manual quality audit."""

import csv
import random
from pathlib import Path

ROW_PATH = Path(__file__).resolve().parents[2] / "support_tickets" / "output.csv"


def main() -> None:
    with ROW_PATH.open(encoding="utf-8") as fh:
        rows = list(csv.DictReader(fh))

    random.seed(7)
    escalated = [r for r in rows if r["status"] == "escalated"]
    borderline = [r for r in rows if r["status"] == "replied" and r["risk_level"] in ("high", "critical")]
    replied_low = [r for r in rows if r["status"] == "replied" and r["risk_level"] in ("low", "medium")]
    multilingual = [r for r in rows if r["language"] != "en"]
    financial = [r for r in rows if "billing" in r["justification"] or "financial" in r["justification"]]
    pii = [r for r in rows if r["pii_detected"] == "true"]

    sample_ids: set[str] = set()
    for bucket in (escalated, borderline, replied_low, multilingual, financial, pii):
        random.shuffle(bucket)
        for r in bucket[:5]:
            sample_ids.add(r["ticket_id"])

    sample = sorted([r for r in rows if r["ticket_id"] in sample_ids], key=lambda r: r["ticket_id"])
    print(f"sampled {len(sample)} of {len(rows)} tickets\n")
    for r in sample:
        resp = r["response"].replace("\n", " ")[:280]
        just = r["justification"][:200]
        print(
            f"--- {r['ticket_id']}  st={r['status']:9s} risk={r['risk_level']:8s} "
            f"pii={r['pii_detected']:5s} conf={r['confidence_score']} lang={r['language']:2s} "
            f"req={r['request_type']:14s} pa={r['product_area']}"
        )
        print(f"    src= {r['source_documents']}")
        print(f"    resp= {resp}")
        print(f"    just= {just}\n")


if __name__ == "__main__":
    main()
