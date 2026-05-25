"""Risk classification.

Deterministic rule-based classifier returning one of:
  low, medium, high, critical.

Inputs:
  - text (already PII-redacted is fine; we operate on patterns)
  - injection_score (from InjectionDetector)
  - pii_kinds present
  - product domain

The classifier is intentionally simple and explainable. The policy
validator (later stage) is the final authority on escalation.
"""

from __future__ import annotations

import re
from typing import Iterable

from ..config import SAFETY


# Pattern -> tier
# Order matters: first match contributes a level. We then take the maximum.
# Each tuple is (pattern, tag). Tags surface in risk_reasons and let the
# policy validator distinguish "high risk because dispute" vs "high risk
# because legal threat". Order of tuples is not significant; we OR them.
_CRITICAL_PATTERNS = (
    (re.compile(r"\b(?:hacked|takeover|compromis(?:e|ed)|unauthori[zs]ed (?:access|charge|login))\b", re.I), "fraud"),
    (re.compile(r"\b(?:identity (?:theft|stolen)|stolen identity|account (?:taken|hijack)|someone (?:has |took |hijack))\b", re.I), "account_takeover"),
    (re.compile(r"\b(?:wire fraud|money laundering|chargeback fraud|scam(?:med)?)\b", re.I), "fraud"),
    (re.compile(r"\b(?:self.?harm|suicide|kill (?:my|me)self|i (?:will|want to) (?:hurt|kill))\b", re.I), "safety"),
    (re.compile(r"\b(?:bomb|threat to (?:kill|harm))\b", re.I), "safety"),
)

_HIGH_PATTERNS = (
    (re.compile(r"\b(?:lawsuit|sue|going to sue|legal action|attorney|lawyer|cease and desist|legal counsel|breach of contract|file a complaint|filing complaint|class action|grounds for (?:breach|legal))\b", re.I), "legal"),
    (re.compile(r"\b(?:gdpr|ccpa|hipaa|right to erasure|right to be forgotten|data protection)\b", re.I), "compliance"),
    (re.compile(r"\b(?:refund (?:of|over)|reimburse).{0,15}\$?\s*\d{3,}", re.I), "financial"),
    (re.compile(r"\b(?:dispute|chargeback)\b", re.I), "dispute"),
    (re.compile(r"\$\s*\d{3,}", re.I), "financial"),
    (re.compile(r"\b(?:locked out|cannot (?:log in|access)|password reset (?:not|never) (?:received|working))\b", re.I), "access"),
    (re.compile(r"\b(?:billing error|double charge|double charged|overcharged|fraudulent charges?|unauthori[zs]ed (?:charges?|transactions?|payments?))\b", re.I), "billing"),
    (re.compile(r"\b(?:never (?:heard of|recogni[sz]e)|don.?t recogni[sz]e|do not recogni[sz]e)\b.{0,60}(?:charge|merchant|transaction)", re.I), "billing"),
    (re.compile(r"\b(?:merchant|charge|transaction).{0,60}(?:never (?:heard of|recogni[sz]e)|don.?t recogni[sz]e|do not recogni[sz]e)", re.I), "billing"),
)

_MEDIUM_PATTERNS = (
    (re.compile(r"\b(?:refund|reimburse|cancel (?:my )?subscription|downgrade)\b", re.I), "billing"),
    (re.compile(r"\b(?:upgrade|change plan|payment (?:method|failed)|invoice)\b", re.I), "billing"),
    (re.compile(r"\b(?:bug|crash(?:ed|ing)|error|cannot|won.t|doesn.t work)\b", re.I), "bug"),
    (re.compile(r"\b(?:permission|access denied|forbidden|403|401)\b", re.I), "access"),
)


def _match_tags(text: str, patterns: Iterable[tuple[re.Pattern[str], str]]) -> list[str]:
    return [tag for pat, tag in patterns if pat.search(text)]


def classify_risk(
    *,
    text: str,
    injection_score: float,
    pii_kinds: Iterable[str],
    is_in_scope: bool = True,
) -> tuple[str, list[str]]:
    """Return (risk_level, reasons)."""
    reasons: list[str] = []
    level = "low"

    # Injection-driven risk.
    if injection_score >= SAFETY.injection_critical:
        level = "critical"
        reasons.append(f"injection_critical:{injection_score:.2f}")
    elif injection_score >= SAFETY.injection_high:
        level = "high"
        reasons.append(f"injection_high:{injection_score:.2f}")
    elif injection_score >= SAFETY.injection_medium:
        level = "medium"
        reasons.append(f"injection_medium:{injection_score:.2f}")

    # Pattern-driven risk. We surface tagged reasons so the policy layer can
    # distinguish, e.g., "high because legal threat" from "high because dispute".
    crit_tags = _match_tags(text, _CRITICAL_PATTERNS)
    if crit_tags:
        level = _max_level(level, "critical")
        for tag in crit_tags:
            reasons.append(f"pattern_critical:{tag}")
    else:
        high_tags = _match_tags(text, _HIGH_PATTERNS)
        if high_tags:
            level = _max_level(level, "high")
            for tag in high_tags:
                reasons.append(f"pattern_high:{tag}")
        else:
            med_tags = _match_tags(text, _MEDIUM_PATTERNS)
            if med_tags:
                level = _max_level(level, "medium")
                for tag in med_tags:
                    reasons.append(f"pattern_medium:{tag}")

    # PII-driven risk.
    pii_set = set(pii_kinds)
    if pii_set & {"card", "ssn", "token", "iban"}:
        level = _max_level(level, "high")
        reasons.append("pii_high_value")
    elif pii_set & {"email", "phone", "address", "account_id"}:
        level = _max_level(level, "medium")
        reasons.append("pii_medium_value")

    # Out-of-scope tickets get a small bump (we want to escalate uncertainty).
    if not is_in_scope and level == "low":
        level = "medium"
        reasons.append("out_of_scope")

    return level, reasons


_ORDER = ("low", "medium", "high", "critical")


def _max_level(a: str, b: str) -> str:
    return _ORDER[max(_ORDER.index(a), _ORDER.index(b))]
