"""§10 cross-product confusion."""

import json

from ._helpers import run


def test_spoofed_company_field_does_not_bypass_routing():
    # body says Visa, company says Claude
    out = run(
        "My Visa card was charged twice for $100. I want a dispute opened.",
        company="Claude",
    )
    # The justification should reflect the visa domain inference.
    assert "visa" in out.justification.lower() or "dispute" in out.justification.lower()


def test_subject_body_contradiction_escalates():
    out = run(
        "Subject says billing but actually I just wanted to say the platform is amazing.",
        subject="URGENT: Billing Discrepancy — Account Compromised",
        company="DevPlatform",
    )
    assert out.status == "escalated"


def test_compound_refund_request_does_not_refund_either():
    out = run(
        "My Claude Pro subscription auto-renewed and I want a refund AND I want my Visa "
        "chargeback to go through.",
        company=None,
    )
    actions = json.loads(out.actions_taken)
    assert all(a["action"] != "issue_refund" for a in actions)
