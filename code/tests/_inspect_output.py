"""Tiny inspector for output.csv. Not a real test."""

import csv
import json
import sys
from collections import Counter
from pathlib import Path

p = Path(__file__).resolve().parents[2] / "support_tickets" / "output.csv"
with p.open(encoding="utf-8") as f:
    rows = list(csv.DictReader(f))

if "--counts" in sys.argv:
    print(f"n_rows={len(rows)}")
    print("status:", Counter(r["status"] for r in rows))
    print("risk:", Counter(r["risk_level"] for r in rows))
    print("request_type:", Counter(r["request_type"] for r in rows))
    print("language:", Counter(r["language"] for r in rows))
    print("pii:", Counter(r["pii_detected"] for r in rows))
    print(
        "no_citation:",
        sum(1 for r in rows if not r["source_documents"]),
        "of",
        len(rows),
    )
    sys.exit(0)

print(f"{len(rows)} rows in {p}")
for r in rows:
    short = r["response"][:90].replace("\n", " ")
    print(
        f"{r['ticket_id']} st={r['status']:9s} risk={r['risk_level']:8s} "
        f"pii={r['pii_detected']:5s} lang={r['language']:2s} conf={r['confidence_score']} "
        f"req={r['request_type']:14s} src={'Y' if r['source_documents'] else '-'} "
        f"-> {short}"
    )
