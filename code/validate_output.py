"""Structural validator for output.csv.

This script checks column presence, enum values, JSON shape, and citation
existence. It does NOT evaluate response quality.

Exit code 0 == all checks passed. Non-zero on any structural failure.

Usage:
    python code/validate_output.py
    python code/validate_output.py --path support_tickets/output.csv
"""

from __future__ import annotations

import argparse
import csv
import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent

OUTPUT_COLUMNS = [
    "ticket_id",
    "status",
    "product_area",
    "response",
    "justification",
    "request_type",
    "confidence_score",
    "source_documents",
    "risk_level",
    "pii_detected",
    "language",
    "actions_taken",
]

ALLOWED_STATUS = {"replied", "escalated"}
ALLOWED_REQUEST_TYPE = {"product_issue", "feature_request", "bug", "invalid"}
ALLOWED_RISK = {"low", "medium", "high", "critical"}
ALLOWED_BOOL = {"true", "false"}


def _err(errors: list[str], msg: str) -> None:
    errors.append(msg)


def validate(path: Path) -> list[str]:
    errors: list[str] = []
    if not path.exists():
        return [f"missing file: {path}"]

    with path.open("r", encoding="utf-8", newline="") as fh:
        reader = csv.DictReader(fh)
        fieldnames = reader.fieldnames or []
        missing = [c for c in OUTPUT_COLUMNS if c not in fieldnames]
        if missing:
            _err(errors, f"missing columns: {missing}")

        for i, row in enumerate(reader, start=1):
            if row.get("status") not in ALLOWED_STATUS:
                _err(errors, f"row {i}: bad status '{row.get('status')}'")
            if row.get("request_type") not in ALLOWED_REQUEST_TYPE:
                _err(errors, f"row {i}: bad request_type '{row.get('request_type')}'")
            if row.get("risk_level") not in ALLOWED_RISK:
                _err(errors, f"row {i}: bad risk_level '{row.get('risk_level')}'")
            if (row.get("pii_detected") or "").lower() not in ALLOWED_BOOL:
                _err(errors, f"row {i}: bad pii_detected '{row.get('pii_detected')}'")
            # confidence in [0,1]
            try:
                c = float(row.get("confidence_score", ""))
                if not 0.0 <= c <= 1.0:
                    _err(errors, f"row {i}: confidence out of range: {c}")
            except Exception:
                _err(errors, f"row {i}: non-numeric confidence")
            # actions_taken JSON
            at = row.get("actions_taken", "")
            try:
                parsed = json.loads(at) if at.strip() else []
                if not isinstance(parsed, list):
                    _err(errors, f"row {i}: actions_taken not a JSON array")
                else:
                    for j, a in enumerate(parsed):
                        if not isinstance(a, dict):
                            _err(errors, f"row {i}.action[{j}]: not an object")
                        elif "action" not in a:
                            _err(errors, f"row {i}.action[{j}]: missing 'action'")
            except Exception as e:
                _err(errors, f"row {i}: invalid JSON in actions_taken ({e})")
            # source_documents existence
            sd = row.get("source_documents", "")
            if sd:
                for p in sd.split("|"):
                    if not p.strip():
                        continue
                    full = (REPO_ROOT / p).resolve()
                    if not full.exists():
                        _err(errors, f"row {i}: source_documents path missing: {p}")
            # language ISO-639-1 (2 letters)
            lang = row.get("language", "")
            if not (isinstance(lang, str) and 2 <= len(lang) <= 5):
                _err(errors, f"row {i}: suspicious language code '{lang}'")
    return errors


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--path", type=Path, default=REPO_ROOT / "support_tickets" / "output.csv")
    args = ap.parse_args(argv)
    errors = validate(Path(args.path))
    if errors:
        print(f"FAIL ({len(errors)} issues):", file=sys.stderr)
        for e in errors[:200]:
            print("  - " + e, file=sys.stderr)
        return 1
    print("OK", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
