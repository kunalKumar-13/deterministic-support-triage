"""Deterministic confidence scoring.

Inputs are signals already computed earlier in the pipeline. Output is a
float in [0.0, 0.99]. Escalated tickets are capped at 0.60. Weak-grounding
replies are capped at 0.65. We avoid 1.0 to prevent Brier-score blowups
when wrong.
"""

from __future__ import annotations

from ..config import (
    CONFIDENCE,
    ESCALATED_MAX_CONFIDENCE,
    HEDGED_RETRIEVAL_CAP,
    NO_GROUNDING_CONFIDENCE,
    WEAK_RETRIEVAL_CAP,
)
from ..models import LLMDecision, PolicyDecision, RetrievalResult, SafetyAssessment


_RISK_WEIGHT = {"low": 0.0, "medium": 0.25, "high": 0.55, "critical": 0.9}


def _clip(x: float, lo: float = 0.0, hi: float = 1.0) -> float:
    return max(lo, min(hi, x))


def score_confidence(
    *,
    safety: SafetyAssessment,
    retrieval: RetrievalResult,
    llm: LLMDecision,
    policy: PolicyDecision,
    scope: object | None = None,
    consistency: object | None = None,
    consensus: object | None = None,
) -> float:
    # No grounding -> we are very uncertain.
    if retrieval.no_grounding:
        base = NO_GROUNDING_CONFIDENCE
    else:
        c_retrieval = _clip(retrieval.top1_score)
        c_agreement = _clip(retrieval.agreement)
        c_risk = 1.0 - _RISK_WEIGHT.get(safety.risk_level, 0.5)
        c_injection = 0.0 if safety.injection_score >= 0.85 else (
            1.0 - _clip(safety.injection_score / 0.85)
        )
        c_llm = _clip(llm.llm_confidence) if not llm.used_fallback else 0.30

        # Scope is the new signal: explicit scope=in_scope -> 1.0, harmless
        # OOS -> 0.3 (we'll reply but with low confidence), suspicious OOS
        # -> 0.05 (we'll escalate; very low confidence is fine).
        if scope is not None:
            if getattr(scope, "suspicious_out_of_scope", False):
                c_scope = 0.05
            elif getattr(scope, "harmless_out_of_scope", False):
                c_scope = 0.30
            else:
                c_scope = _clip(getattr(scope, "scope_score", 0.7))
        else:
            c_scope = 1.0 if safety.is_in_scope else 0.4

        base = (
            CONFIDENCE.retrieval * c_retrieval
            + CONFIDENCE.agreement * c_agreement
            + CONFIDENCE.risk * c_risk
            + CONFIDENCE.injection * c_injection
            + CONFIDENCE.llm * c_llm
            + CONFIDENCE.scope * c_scope
        )

        # Additional multiplicative penalties for the new signals.
        # Consensus disagreement: any critical disagreement multiplies base by 0.45.
        if consensus is not None:
            if getattr(consensus, "critical", False):
                base *= 0.45
            elif getattr(consensus, "single_source_only", False):
                base *= 0.85  # single-source claims warrant some hedging
            else:
                # Soft attenuation toward the agreement score for a smoother
                # spread across replied tickets.
                agg = _clip(getattr(consensus, "score", 0.6))
                base *= 0.85 + 0.15 * agg

        # Consistency anomalies: identity shifts / cross-ticket refs heavily
        # penalise confidence (we'll typically escalate, but still cap).
        if consistency is not None and getattr(consistency, "needs_escalation", False):
            base *= 0.40
        elif consistency is not None and getattr(consistency, "pressure_tactic", False):
            base *= 0.85

    # Apply policy caps.
    cap = policy.confidence_cap
    if policy.status == "escalated":
        cap = min(cap, ESCALATED_MAX_CONFIDENCE)
    if retrieval.weak_match and not retrieval.no_grounding:
        cap = min(cap, HEDGED_RETRIEVAL_CAP)
    base = min(base, cap)

    # Never report > 0.95 to keep Brier-score safe.
    return round(_clip(base, 0.05, 0.95), 4)
