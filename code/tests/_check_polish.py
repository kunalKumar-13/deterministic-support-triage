"""Quick check of the polished response wording on key tickets."""

import csv
from pathlib import Path

p = Path(__file__).resolve().parents[2] / "support_tickets" / "output.csv"
keys = {
    "T0001", "T0026", "T0034", "T0044", "T0066", "T0086",
    "T0089", "T0090", "T0050", "T0070",
}
with p.open(encoding="utf-8") as f:
    for r in csv.DictReader(f):
        if r["ticket_id"] in keys:
            short = r["response"].replace("\n", " ")[:280]
            print(
                f"{r['ticket_id']} st={r['status']:9s} risk={r['risk_level']:8s} "
                f"src={'Y' if r['source_documents'] else '-'} -> {short}"
            )
            print()
