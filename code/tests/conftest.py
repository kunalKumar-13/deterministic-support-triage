"""Make `triage` importable from anywhere under `code/`."""
import sys
from pathlib import Path

_CODE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_CODE))
