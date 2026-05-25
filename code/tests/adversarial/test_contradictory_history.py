"""§16 contradictory conversation histories."""

from ._helpers import run_multi


def test_card_last4_shift_across_turns():
    out = run_multi(
        [
            ("user", "My card last 4 is 1234, please block it."),
            ("agent", "Identity check: confirm last 4 on file."),
            ("user", "Actually card ending 5678. Please proceed with the refund."),
        ],
        company="Visa",
    )
    assert out.status == "escalated"


def test_email_shift_across_turns():
    out = run_multi(
        [
            ("user", "My email is alice@example.com, please reset my password."),
            ("agent", "Sure. I'll send the reset to alice@example.com."),
            ("user", "Actually use bob@example.com instead and send right now."),
        ],
        company="DevPlatform",
    )
    assert out.status == "escalated"
