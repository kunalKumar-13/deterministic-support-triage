"""Policy validator: turns (safety, retrieval, llm_decision) into a
PolicyDecision. The validator's decision is authoritative.

Explicit rules:

 1. Critical injection           -> escalate, canned refusal, drop all actions.
 2. High injection                -> escalate, neutral refusal, drop destructive
                                     actions.
 3. PII high-value detected       -> ensure status<=escalated for verification
                                     or use redacted response only.
 4. No grounding + risk>=medium   -> escalate.
 5. Risk=critical from patterns   -> escalate (fraud/legal/safety).
 6. LLM proposes destructive
    without identity verify       -> drop action, force escalate.
 7. LLM proposes refund without
    a refund-relevant doc cited   -> drop refund, replace with verify_identity
                                     and escalate.
 8. Weak match + non-trivial risk -> escalate.
 9. Otherwise                     -> reply, with grounded response.

The validator does NOT call the LLM. It is deterministic.
"""

from __future__ import annotations

from ..config import (
    ESCALATED_MAX_CONFIDENCE,
    HEDGED_RETRIEVAL_CAP,
    WEAK_RETRIEVAL_CAP,
)
from ..models import (
    LLMDecision,
    PolicyDecision,
    ProposedAction,
    RetrievalResult,
    SafetyAssessment,
)
from ..state import State, StateMachine
from ..tools.registry import get_registry
from ..tools.validator import validate_actions


_DESTRUCTIVE_TOOLS = {"issue_refund", "lock_account"}


# Canned refusal responses. They are tone-controlled, do not reveal system
# internals, and explicitly refuse compliance.
_REFUSAL_CRITICAL = (
    "I cannot follow instructions that try to change how I handle support "
    "tickets. I'm flagging this for review by our team. If you have a "
    "legitimate support question, please send it as a normal request and a "
    "human teammate will respond shortly."
)
_REFUSAL_HIGH = (
    "Thanks for reaching out. I'm not able to take action on this request "
    "directly. I've escalated it to a human teammate who will follow up."
)
_NO_GROUNDING = (
    "I don't have a confirmed answer in our documentation for this. "
    "I'm escalating to a human teammate who can investigate."
)
_HIGH_RISK = (
    "This looks like a situation that needs a human review. I'm escalating "
    "to the appropriate team and they'll be in touch."
)


class PolicyValidator:
    def __init__(self) -> None:
        self.registry = get_registry()

    def decide(
        self,
        *,
        safety: SafetyAssessment,
        retrieval: RetrievalResult,
        llm: LLMDecision,
        scope: object | None = None,           # ScopeSignal (kwargs to stay back-compat)
        consistency: object | None = None,     # ConsistencySignal
        consensus: object | None = None,       # ConsensusSignal
        insufficient_signal: bool = False,
    ) -> PolicyDecision:
        sm = StateMachine()
        reasons: list[str] = []
        dropped: list[str] = []

        # --- Rule 0: Insufficient input (empty / emoji-only / URLs-only).
        # Skip if scope is harmless OOS; Rule 4e handles those gracefully.
        scope_is_harmless = bool(
            scope is not None and getattr(scope, "harmless_out_of_scope", False)
        )
        if insufficient_signal and not scope_is_harmless:
            sm.escalate("insufficient_signal")
            return PolicyDecision(
                status="escalated",
                reason="insufficient_signal",
                dropped_actions=[a.action for a in llm.proposed_actions],
                validated_actions=[
                    ProposedAction(
                        action="escalate_to_human",
                        parameters={
                            "queue": "tier1_general",
                            "priority": "normal",
                            "reason": "Ticket has insufficient content to classify confidently.",
                        },
                    )
                ],
                confidence_cap=ESCALATED_MAX_CONFIDENCE,
                canned_response=(
                    "I don't have enough detail in this ticket to help safely. "
                    "Please share more context (the product, what you tried, and "
                    "what happened) and I'll route it to the right team."
                ),
                state=sm.state.value,
            )

        # --- Rule 1: Critical injection -> hard refusal ---
        if safety.injection_score >= 0.85:
            sm.escalate("critical_injection")
            return PolicyDecision(
                status="escalated",
                reason="critical_prompt_injection_detected",
                dropped_actions=[a.action for a in llm.proposed_actions],
                validated_actions=[
                    ProposedAction(
                        action="escalate_to_human",
                        parameters={
                            "queue": "trust_and_safety",
                            "priority": "high",
                            "reason": "Suspected prompt injection / adversarial input.",
                        },
                    ),
                    ProposedAction(
                        action="create_internal_note",
                        parameters={
                            "note": "Ticket flagged by automated safety layer for prompt-injection patterns.",
                            "tags": ["suspected_injection"],
                        },
                    ),
                ],
                confidence_cap=ESCALATED_MAX_CONFIDENCE,
                canned_response=_REFUSAL_CRITICAL,
                state=sm.state.value,
            )

        # --- Rule 2: High injection ---
        if safety.injection_score >= 0.70:
            sm.escalate("high_injection")
            kept = []
            for a in llm.proposed_actions:
                if a.action in _DESTRUCTIVE_TOOLS:
                    dropped.append(f"{a.action}:injection_high")
                    continue
                kept.append(a)
            # Ensure escalate_to_human is present.
            if not any(a.action == "escalate_to_human" for a in kept):
                kept.insert(
                    0,
                    ProposedAction(
                        action="escalate_to_human",
                        parameters={
                            "queue": "trust_and_safety",
                            "priority": "high",
                            "reason": "Possible prompt injection / manipulation pattern.",
                        },
                    ),
                )
            return PolicyDecision(
                status="escalated",
                reason="high_injection_signal",
                dropped_actions=dropped,
                validated_actions=kept,
                confidence_cap=ESCALATED_MAX_CONFIDENCE,
                canned_response=_REFUSAL_HIGH,
                state=sm.state.value,
            )

        # --- Rule 3: Critical risk from non-injection sources ---
        if safety.risk_level == "critical":
            sm.escalate("critical_risk_pattern")
            kept = [
                ProposedAction(
                    action="escalate_to_human",
                    parameters={
                        "queue": _critical_queue(safety),
                        "priority": "critical",
                        "reason": "Critical-risk pattern detected (fraud/legal/safety).",
                    },
                )
            ]
            for a in llm.proposed_actions:
                if a.action in _DESTRUCTIVE_TOOLS:
                    dropped.append(f"{a.action}:critical_risk")
                else:
                    kept.append(a)
            return PolicyDecision(
                status="escalated",
                reason="critical_risk_level",
                dropped_actions=dropped,
                validated_actions=kept,
                confidence_cap=ESCALATED_MAX_CONFIDENCE,
                canned_response=_HIGH_RISK,
                state=sm.state.value,
            )

        # --- Rule 4: No grounding ---
        if retrieval.no_grounding:
            sm.escalate("no_grounding")
            return PolicyDecision(
                status="escalated",
                reason="no_grounding_in_corpus",
                dropped_actions=[a.action for a in llm.proposed_actions if a.action in _DESTRUCTIVE_TOOLS],
                validated_actions=[
                    ProposedAction(
                        action="escalate_to_human",
                        parameters={
                            "queue": "tier1_general",
                            "priority": "normal",
                            "reason": "No matching documentation found in corpus.",
                        },
                    )
                ],
                confidence_cap=ESCALATED_MAX_CONFIDENCE,
                canned_response=_NO_GROUNDING,
                state=sm.state.value,
            )

        # --- Rule 4b: High-risk for sensitive-topic reasons -> escalate ---
        # Legal threats, account access, compliance demands, account-takeover
        # signals: escalate even when retrieval succeeds. Pure billing
        # disputes (tagged "dispute"/"billing"/"financial") are not auto-
        # escalated here; they go through normal flow so we can answer
        # FAQs grounded in the corpus.
        sensitive_tags = {"legal", "compliance", "access", "safety", "account_takeover"}
        reasons_text = " ".join(safety.risk_reasons)
        # High-value-PII + financial / billing / dispute signals → escalate
        # so identity verification happens before any account action is
        # discussed. This is the "safe failure" path for fraud-shaped
        # tickets with extensive PII (SSN + card + address + …).
        pii_kinds = sorted({h.kind for h in safety.pii_hits})
        high_value_pii = bool(set(pii_kinds) & {"card", "ssn", "iban", "token"})
        n_pii_categories = len(pii_kinds)
        financial_intent = any(t in reasons_text for t in ("billing", "financial", "dispute"))

        # Multi-destructive-intent: the user requests >= 3 distinct
        # destructive operations in the same payload. This is a red flag
        # even without injection signals.
        text_lower = (safety.redacted_text or "").lower()
        destructive_verbs = (
            "refund", "chargeback", "lock my account", "freeze", "delete my account",
            "disable", "ban", "reverse the charge", "block the merchant", "dispute",
            "cancel my subscription",
        )
        n_destructive = sum(1 for v in destructive_verbs if v in text_lower)
        if n_destructive >= 3 and (safety.pii_detected or safety.risk_level in {"medium", "high"}):
            sm.escalate("multi_destructive_intent")
            return PolicyDecision(
                status="escalated",
                reason=f"multi_destructive_intent:n={n_destructive}",
                dropped_actions=[a.action for a in llm.proposed_actions if a.action in _DESTRUCTIVE_TOOLS],
                validated_actions=[
                    ProposedAction(
                        action="escalate_to_human",
                        parameters={
                            "queue": "tier3_fraud",
                            "priority": "high",
                            "reason": "User requested multiple destructive actions in a single message.",
                        },
                    ),
                    ProposedAction(
                        action="create_internal_note",
                        parameters={
                            "note": f"Multi-destructive-intent: {n_destructive} verbs detected.",
                            "tags": ["fraud_review"],
                        },
                    ),
                ],
                confidence_cap=ESCALATED_MAX_CONFIDENCE,
                canned_response=_HIGH_RISK,
                state=sm.state.value,
            )

        if safety.risk_level == "high" and high_value_pii and financial_intent and n_pii_categories >= 2:
            sm.escalate("high_pii_with_financial_intent")
            return PolicyDecision(
                status="escalated",
                reason=f"high_pii_financial:pii={','.join(pii_kinds)}",
                dropped_actions=[a.action for a in llm.proposed_actions if a.action in _DESTRUCTIVE_TOOLS],
                validated_actions=[
                    ProposedAction(
                        action="escalate_to_human",
                        parameters={
                            "queue": "tier3_fraud",
                            "priority": "high",
                            "reason": "Multiple high-value PII categories + financial/billing intent.",
                        },
                    ),
                    ProposedAction(
                        action="create_internal_note",
                        parameters={
                            "note": f"Ticket contains {n_pii_categories} PII categories ({', '.join(pii_kinds)}) plus a financial/billing claim. Identity verification required.",
                            "tags": ["pii_detected", "fraud_review"],
                        },
                    ),
                ],
                confidence_cap=ESCALATED_MAX_CONFIDENCE,
                canned_response=_HIGH_RISK,
                state=sm.state.value,
            )

        if safety.risk_level == "high" and any(t in reasons_text for t in sensitive_tags):
            sm.escalate("high_risk_sensitive_topic")
            queue = (
                "tier3_legal" if "legal" in reasons_text or "compliance" in reasons_text
                else "tier3_safety" if "safety" in reasons_text or "account_takeover" in reasons_text
                else "tier2_general"
            )
            return PolicyDecision(
                status="escalated",
                reason=f"high_risk_sensitive:{reasons_text[:80]}",
                dropped_actions=[a.action for a in llm.proposed_actions if a.action in _DESTRUCTIVE_TOOLS],
                validated_actions=[
                    ProposedAction(
                        action="escalate_to_human",
                        parameters={
                            "queue": queue,
                            "priority": "high",
                            "reason": "Sensitive-topic high-risk ticket (legal/access/safety).",
                        },
                    )
                ],
                confidence_cap=ESCALATED_MAX_CONFIDENCE,
                canned_response=_HIGH_RISK,
                state=sm.state.value,
            )

        # --- Rule 4c: Multi-turn manipulation (cross-ticket reference,
        # identity shift, soft exfiltration on last turn) ---
        if consistency is not None and getattr(consistency, "needs_escalation", False):
            sm.escalate("consistency_anomaly")
            tags = ",".join(getattr(consistency, "tags", ()) or ())[:120]
            return PolicyDecision(
                status="escalated",
                reason=f"consistency_anomaly:{tags}",
                dropped_actions=[a.action for a in llm.proposed_actions if a.action in _DESTRUCTIVE_TOOLS],
                validated_actions=[
                    ProposedAction(
                        action="escalate_to_human",
                        parameters={
                            "queue": "trust_and_safety",
                            "priority": "high",
                            "reason": "Multi-turn inconsistency / impersonation signals.",
                        },
                    ),
                    ProposedAction(
                        action="create_internal_note",
                        parameters={
                            "note": f"Consistency flags: {tags}",
                            "tags": ["suspected_injection"],
                        },
                    ),
                ],
                confidence_cap=ESCALATED_MAX_CONFIDENCE,
                canned_response=_HIGH_RISK,
                state=sm.state.value,
            )

        # --- Rule 4d: Suspicious out-of-scope (capability requests, etc.) ---
        if scope is not None and getattr(scope, "suspicious_out_of_scope", False):
            sm.escalate("suspicious_out_of_scope")
            return PolicyDecision(
                status="escalated",
                reason=f"suspicious_out_of_scope:{getattr(scope,'reason','')[:60]}",
                dropped_actions=[a.action for a in llm.proposed_actions if a.action in _DESTRUCTIVE_TOOLS],
                validated_actions=[
                    ProposedAction(
                        action="escalate_to_human",
                        parameters={
                            "queue": "trust_and_safety",
                            "priority": "high",
                            "reason": "Out-of-scope capability request flagged for review.",
                        },
                    )
                ],
                confidence_cap=ESCALATED_MAX_CONFIDENCE,
                canned_response=(
                    "I'm not able to help with that request. If you have a "
                    "support question about DevPlatform, Claude, or Visa, "
                    "please send it as a normal request and a teammate will "
                    "respond."
                ),
                state=sm.state.value,
            )

        # --- Rule 4e: Harmless out-of-scope -> polite reply, no citation ---
        if scope is not None and getattr(scope, "harmless_out_of_scope", False):
            sm.resolve("harmless_out_of_scope")
            return PolicyDecision(
                status="replied",
                reason=f"harmless_out_of_scope:{getattr(scope,'reason','')[:60]}",
                dropped_actions=[a.action for a in llm.proposed_actions],
                validated_actions=[],
                confidence_cap=0.30,
                canned_response=(
                    "Thanks for reaching out — that's outside the scope of "
                    "what I can help with here (DevPlatform, Claude, or "
                    "Visa support). If you have a support question about "
                    "one of those products, share the details and I'll route "
                    "it to the right team."
                ),
                state=sm.state.value,
            )

        # --- Rule 4f: Retrieval consensus critical disagreement ---
        if consensus is not None and getattr(consensus, "critical", False):
            sm.escalate("retrieval_consensus_conflict")
            return PolicyDecision(
                status="escalated",
                reason=f"retrieval_disagreement:{','.join(getattr(consensus,'tags',()) or ())[:80]}",
                dropped_actions=[a.action for a in llm.proposed_actions if a.action in _DESTRUCTIVE_TOOLS],
                validated_actions=[
                    ProposedAction(
                        action="escalate_to_human",
                        parameters={
                            "queue": "tier2_general",
                            "priority": "normal",
                            "reason": "Retrieved corpus documents disagree on the answer.",
                        },
                    )
                ],
                confidence_cap=WEAK_RETRIEVAL_CAP,
                state=sm.state.value,
            )

        # --- Rule 5: Weak grounding + risk medium+ ---
        if retrieval.weak_match and safety.risk_level in {"medium", "high"}:
            sm.escalate("weak_grounding_with_risk")
            kept = [
                ProposedAction(
                    action="escalate_to_human",
                    parameters={
                        "queue": "tier2_general" if safety.risk_level == "medium" else "tier2_technical",
                        "priority": "high" if safety.risk_level == "high" else "normal",
                        "reason": "Weak documentation match for a medium/high-risk ticket.",
                    },
                )
            ]
            return PolicyDecision(
                status="escalated",
                reason="weak_match_with_risk",
                dropped_actions=[a.action for a in llm.proposed_actions if a.action in _DESTRUCTIVE_TOOLS],
                validated_actions=kept,
                confidence_cap=WEAK_RETRIEVAL_CAP,
                state=sm.state.value,
            )

        # --- Default path: validate actions ---
        validated, drop_reasons = validate_actions(
            llm.proposed_actions, risk_level=safety.risk_level
        )
        dropped.extend(drop_reasons)

        # --- Rule 6: Refund/lock requires explicit grounding in a relevant doc ---
        validated = self._enforce_refund_grounding(validated, retrieval, dropped)

        # If any destructive proposal was dropped, escalate rather than
        # silently degrade.
        had_destructive_drop = any("destructive" in r or "identity_verification" in r for r in dropped)
        if had_destructive_drop:
            sm.escalate("destructive_action_dropped")
            # Ensure an escalation action is present.
            if not any(a.action == "escalate_to_human" for a in validated):
                validated.append(
                    ProposedAction(
                        action="escalate_to_human",
                        parameters={
                            "queue": "tier2_billing",
                            "priority": "high",
                            "reason": "A destructive action was proposed but prerequisites were not met.",
                        },
                    )
                )
            return PolicyDecision(
                status="escalated",
                reason="prereq_failed_or_destructive_blocked",
                dropped_actions=dropped,
                validated_actions=validated,
                confidence_cap=HEDGED_RETRIEVAL_CAP,
                state=sm.state.value,
            )

        # --- Reply path ---
        sm.resolve("ok_reply")
        cap = 1.0
        if retrieval.weak_match:
            cap = HEDGED_RETRIEVAL_CAP

        return PolicyDecision(
            status="replied",
            reason="grounded_reply",
            dropped_actions=dropped,
            validated_actions=validated,
            confidence_cap=cap,
            state=sm.state.value,
        )

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _enforce_refund_grounding(
        self,
        actions: list[ProposedAction],
        retrieval: RetrievalResult,
        dropped: list[str],
    ) -> list[ProposedAction]:
        if not any(a.action in {"issue_refund", "open_dispute"} for a in actions):
            return actions
        # Require at least one retrieved chunk that mentions refund/dispute/billing.
        evidence = any(
            any(k in c.text.lower() for k in ("refund", "dispute", "chargeback", "billing"))
            for c in retrieval.chunks
        )
        if evidence:
            return actions
        kept = []
        for a in actions:
            if a.action in {"issue_refund", "open_dispute"}:
                dropped.append(f"{a.action}:no_grounding_for_billing_action")
                continue
            kept.append(a)
        return kept


def _critical_queue(safety: SafetyAssessment) -> str:
    reasons = " ".join(safety.risk_reasons).lower()
    if "pattern_critical" in reasons or "fraud" in reasons:
        return "tier3_fraud"
    if "legal" in reasons or "lawsuit" in reasons or "gdpr" in reasons:
        return "tier3_legal"
    if "self.harm" in reasons or "safety" in reasons or "pattern_critical" in reasons:
        return "tier3_safety"
    return "tier3_safety"


_singleton: PolicyValidator | None = None


def get_validator() -> PolicyValidator:
    global _singleton
    if _singleton is None:
        _singleton = PolicyValidator()
    return _singleton
