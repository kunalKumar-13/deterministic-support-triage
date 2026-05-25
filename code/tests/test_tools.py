"""Tool registry + validator tests."""

import pytest

from triage.models import ProposedAction
from triage.tools.registry import get_registry
from triage.tools.validator import validate_action, validate_actions


def test_registry_loads_known_tools():
    reg = get_registry()
    names = reg.names()
    assert "escalate_to_human" in names
    assert "verify_identity" in names
    assert "issue_refund" in names


def test_refund_requires_identity_verification():
    a = ProposedAction(
        action="issue_refund",
        parameters={
            "transaction_reference": "TXN-001",
            "amount": 50,
            "currency": "USD",
            "reason_code": "duplicate_charge",
        },
    )
    ok, reasons = validate_action(a, risk_level="medium", identity_verified=False)
    assert not ok
    assert any("identity_verification_required" in r for r in reasons)
    ok2, _ = validate_action(a, risk_level="medium", identity_verified=True)
    assert ok2


def test_refund_blocked_under_critical_risk():
    a = ProposedAction(
        action="issue_refund",
        parameters={
            "transaction_reference": "TXN-001",
            "amount": 50,
            "currency": "USD",
            "reason_code": "duplicate_charge",
        },
    )
    ok, reasons = validate_action(a, risk_level="critical", identity_verified=True)
    assert not ok
    assert any("destructive_above_execute_threshold" in r for r in reasons)


def test_unknown_tool_is_rejected():
    a = ProposedAction(action="delete_universe", parameters={})
    ok, reasons = validate_action(a, risk_level="low", identity_verified=False)
    assert not ok


def test_idempotency_drops_duplicates():
    a = ProposedAction(
        action="verify_identity",
        parameters={"account_reference": "abc123", "method": "email_otp"},
    )
    kept, dropped = validate_actions([a, a], risk_level="medium")
    assert len(kept) == 1
    assert any("duplicate_idempotency" in d for d in dropped)


def test_verify_then_refund_chain_works():
    chain = [
        ProposedAction(
            action="verify_identity",
            parameters={"account_reference": "abc123", "method": "email_otp"},
        ),
        ProposedAction(
            action="issue_refund",
            parameters={
                "transaction_reference": "TXN-001",
                "amount": 50,
                "currency": "USD",
                "reason_code": "duplicate_charge",
            },
        ),
    ]
    kept, dropped = validate_actions(chain, risk_level="medium")
    assert len(kept) == 2
    assert not dropped
