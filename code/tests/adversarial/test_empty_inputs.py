"""§18 empty / near-empty / non-text inputs."""

import json

from triage.pipeline import process_ticket_safe

from ._helpers import run


def test_empty_issue_array_escalates():
    out = process_ticket_safe(ticket_id="e1", issue="[]", subject="No subject", company=None)
    assert out.status == "escalated"
    assert "insufficient" in out.justification.lower()


def test_emoji_only_escalates_or_low_conf():
    out = run("🔥💳❌🏧🚫💰😡📞🆘🇮🇳", subject="Card emergency", company="Visa")
    # Either escalates (preferred) or replies with low confidence.
    assert out.status == "escalated" or out.confidence_score <= 0.45


def test_urls_only_escalates():
    out = run(
        "https://support.devplatform.com/x\nhttps://visa.co.in/y\nhttps://support.anthropic.com/z",
        company=None,
    )
    assert out.status == "escalated"


def test_two_word_help_escalates():
    out = run("it's not working, help", subject="help needed", company=None)
    # Single very short ticket with no company hint should escalate.
    assert out.status == "escalated"
