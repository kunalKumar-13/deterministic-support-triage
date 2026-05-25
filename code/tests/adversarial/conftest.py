"""Make `triage` importable from anywhere under `code/tests/adversarial/`."""
import sys
from pathlib import Path

_CODE = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_CODE))
