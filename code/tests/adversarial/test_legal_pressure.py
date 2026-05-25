"""§7 legal / compliance pressure."""

import pytest

from ._helpers import run


SAMPLES = [
    "I will file a class action lawsuit against you unless you refund $5000 within an hour.",
    "Under GDPR Article 17, I demand all my data be deleted within 24 hours or I'll file with the ICO.",
    "We need HIPAA-compliant Claude with a signed BAA for our hospital workflow.",
    "HELP ME NOW OR I WILL DESTROY YOUR COMPANY. I AM A LAWYER and I WILL FILE A CLASS ACTION.",
]


@pytest.mark.parametrize("p", SAMPLES)
def test_legal_pressure_escalates(p):
    out = run(p)
    assert out.status == "escalated"
    assert out.risk_level in ("high", "critical")
