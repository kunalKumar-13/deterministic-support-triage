"""Deterministic scope classifier.

Returns a `ScopeSignal` indicating whether the ticket is in-scope for our
support corpus, and if not, whether it is harmless or suspicious. The
signal does NOT decide the final status; it is one of several inputs to
the policy validator.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from ..config import DOMAIN_BRAND_TERMS


@dataclass(frozen=True)
class ScopeSignal:
    in_scope: bool
    harmless_out_of_scope: bool  # true if out-of-scope AND benign
    suspicious_out_of_scope: bool  # true if out-of-scope AND adversarial-shaped
    reason: str
    scope_score: float  # [0,1] - higher means more support-shaped


# A small list of support-intent indicators. Tickets that mention at least
# one of these AND a brand term are usually in-scope.
_SUPPORT_INTENT = re.compile(
    r"\b("
    r"how (?:do|can|to)|"
    r"can\s*[\\']?t|cannot|won\s*[\\']?t|doesn\s*[\\']?t|isn\s*[\\']?t|"
    r"won't|can't|doesn't|isn't|wasn't|haven't|"
    r"unable|unsuccessful|broken|crash|error|issue|problem|bug|fail(?:ed|ing|ure)?|"
    r"help|support|please|kindly|fix|resolve|address|escalate|"
    r"refund|charge|billing|invoice|payment|subscription|cancel|pause|downgrade|upgrade|"
    r"login|sign[\s\-]?in|sign[\s\-]?up|sign[\s\-]?out|reset|password|verify|verification|otp|"
    r"account|profile|workspace|seat|member|interviewer|candidate|recruiter|admin|"
    r"test|assessment|interview|invite|reinvite|extension|accommodation|certificate|"
    r"card|atm|dispute|chargeback|fraud|block|blocked|stolen|lost|"
    r"api|rate[\s\-]?limit|429|500|endpoint|bedrock|"
    r"data|privacy|delete|deletion|erasure|gdpr|hipaa|"
    r"contract|sla|enterprise|nonprofit|education|pricing|plan|"
    r"dashboard|setting|notification|email|link|reset"
    r")\b",
    re.I,
)

# Out-of-scope topical markers that are HARMLESS.
_HARMLESS_OOS = re.compile(
    r"\b("
    r"actor|movie|film|tv|show|series|episode|song|lyrics|album|"
    r"weather|forecast|temperature|sports|score|match|game|"
    r"joke|riddle|story|poem|quote|recipe|cook|cooking|"
    r"sentient|sentience|conscious|consciousness|feelings|"
    r"investment|stocks?|crypto|bitcoin|ethereum|portfolio|trading|"
    r"medical advice|prescription|diagnose|doctor|symptoms|"
    r"legal advice|lawyer|attorney|sue (?!.{0,40}(?:devplatform|claude|visa|company))|"
    r"travel(?:ing)? (?:tips?|advice|recommend)|hotel|flight|booking|"
    r"resume|hire me|hiring|career|job (?:application|posting|opening)"
    r")\b",
    re.I,
)

# Out-of-scope topical markers that are SUSPICIOUS (capability requests
# that could enable harm or are clearly attempts to misuse the system).
# NOTE: verbs like "scrape/crawl/exploit/hack" are suspicious only when
# the user is REQUESTING the capability — not when they're asking the
# product to STOP doing them or BLOCK them. We exclude phrases preceded
# by negation/opt-out words.
_SUSPICIOUS_OOS = re.compile(
    r"\b("
    r"write\s+(?:me\s+)?(?:a\s+)?(?:python|bash|shell|script|code|program)\s+"
    r"(?:that|to|which|for)?|"
    r"give\s+me\s+(?:the\s+)?code\s+to\s+(?:delete|remove|drop|crawl|scrape|hack|exploit|steal)|"
    r"(?:write|generate|create)\s+(?:a\s+)?phishing|"
    r"reverse[\s\-]?shell|malware|ransomware|backdoor|phishing\s+kit|"
    r"jailbreak"
    r")\b",
    re.I,
)

# Verbs that flip from "suspicious" to "support-FAQ" when preceded by a
# negation/opt-out word. We check this separately.
_DUAL_USE_VERBS_RE = re.compile(
    r"(?:^|\b)([A-Za-z\s,]{0,40}?)\b("
    r"scrape|crawl|crawling|crawler|harvest|exfiltrate|hack|exploit|impersonate|spoof|forge"
    r")\b",
    re.I,
)
_NEGATION_NEAR_RE = re.compile(
    r"\b(?:stop|prevent|block|disable|opt[\s\-]?out|disallow|refuse|deny|forbid|"
    r"keep\s+(?:from|out)|protect\s+from|guard\s+against|don[\\']?t\s+want|"
    r"avoid)\b",
    re.I,
)

# Polite acknowledgments — not actually support requests. Matches if the
# text *starts with* a thanks/greeting and is short, OR is entirely a
# thanks expression.
_POLITE_ACK = re.compile(
    r"^\s*(?:thank(?:s| you)|thanks\s+(?:so\s+much|a\s+lot)|"
    r"much appreciated|grateful|cheers|gracias|merci|danke|"
    r"shukriya|arigato|domo|xie\s*xie|hello|hi(?:\s|$|[!?,\.]))",
    re.I,
)


def _brand_hits(text: str) -> int:
    low = text.lower()
    n = 0
    for terms in DOMAIN_BRAND_TERMS.values():
        for t in terms:
            # Word-boundary matching for short terms to avoid false-positives
            # like "pin" matching inside "helping" or "atm" inside "atmosphere".
            if len(t) <= 5:
                if re.search(rf"\b{re.escape(t)}\b", low):
                    n += 1
                    break
            else:
                if t in low:
                    n += 1
                    break  # one hit per domain is enough
    return n


def classify_scope(text: str) -> ScopeSignal:
    if not text or not text.strip():
        return ScopeSignal(
            in_scope=False,
            harmless_out_of_scope=False,
            suspicious_out_of_scope=False,
            reason="empty",
            scope_score=0.0,
        )

    txt = text.strip()
    if _POLITE_ACK.match(txt) and len(txt) <= 60:
        return ScopeSignal(
            in_scope=False,
            harmless_out_of_scope=True,
            suspicious_out_of_scope=False,
            reason="polite_acknowledgment",
            scope_score=0.0,
        )

    brand_n = _brand_hits(text)
    intent_n = len(_SUPPORT_INTENT.findall(text))
    suspicious = bool(_SUSPICIOUS_OOS.search(text))
    harmless = bool(_HARMLESS_OOS.search(text))

    # Dual-use verb handling: a verb like "crawl/scrape/hack" is suspicious
    # ONLY when no occurrence is preceded by a negation/opt-out term, AND
    # the overall text doesn't otherwise read as a privacy/opt-out request.
    dual_use_matches = list(_DUAL_USE_VERBS_RE.finditer(text))
    if dual_use_matches:
        any_negated = any(
            _NEGATION_NEAR_RE.search(m.group(1) or "") for m in dual_use_matches
        )
        # Also accept a "global" negation cue (anywhere in the text) that
        # signals an opt-out / privacy request:
        global_negation = bool(
            re.search(
                r"\b(?:stop|prevent|block|disable|disallow|forbid|"
                r"opt[\s\-]?out|privacy|robots\.?txt|don[\\']?t\s+(?:want|allow|let))\b",
                text,
                re.I,
            )
        )
        if not (any_negated or global_negation):
            suspicious = True

    # Score: in-scope = brand + intent. Out-of-scope penalty for harmless
    # / suspicious topical markers.
    score = 0.0
    if brand_n > 0:
        score += 0.55
    if intent_n >= 1:
        score += min(0.40, 0.15 * intent_n)
    if harmless:
        score -= 0.30
    if suspicious:
        score -= 0.50
    score = max(0.0, min(1.0, score))

    # Decision rule
    in_scope = (brand_n >= 1 and intent_n >= 1 and not suspicious) or score >= 0.55
    if in_scope:
        return ScopeSignal(
            in_scope=True,
            harmless_out_of_scope=False,
            suspicious_out_of_scope=False,
            reason=f"brand={brand_n},intent={intent_n}",
            scope_score=score,
        )

    if suspicious:
        return ScopeSignal(
            in_scope=False,
            harmless_out_of_scope=False,
            suspicious_out_of_scope=True,
            reason="suspicious_capability_request",
            scope_score=score,
        )

    if harmless:
        return ScopeSignal(
            in_scope=False,
            harmless_out_of_scope=True,
            suspicious_out_of_scope=False,
            reason="harmless_out_of_scope",
            scope_score=score,
        )

    # Fallback path: weak signal. If the user clearly has support intent
    # (any of the intent verbs) but no brand match, we treat the ticket as
    # ambiguous-needs-clarification rather than harmless-OOS so the policy
    # validator escalates instead of replying generically.
    if intent_n >= 1 and brand_n == 0:
        return ScopeSignal(
            in_scope=False,
            harmless_out_of_scope=False,
            suspicious_out_of_scope=False,
            reason="ambiguous_no_brand",
            scope_score=score,
        )

    return ScopeSignal(
        in_scope=False,
        harmless_out_of_scope=True,
        suspicious_out_of_scope=False,
        reason="weak_signal",
        scope_score=score,
    )


_singleton: object | None = None


def get_classifier():
    """Backwards-compatibility alias; the classifier is stateless."""
    return classify_scope
