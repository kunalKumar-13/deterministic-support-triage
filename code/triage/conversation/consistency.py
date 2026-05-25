"""Conversation consistency analyzer.

Public API: `analyze(turns: list[ConversationTurn]) -> ConsistencySignal`.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from ..models import ConversationTurn


_TICKET_REF_RE = re.compile(
    r"(?:"
    r"row\s+\d+\s+above|"
    r"the (?:above|previous|earlier) (?:ticket|row|case)|"
    r"ticket\s*(?:id|#|number)\s*[:#-]?\s*[A-Z0-9_\-]+|"
    r"case\s+(?:id|#|number)\s*[:#-]?\s*[A-Z0-9_\-]+|"
    r"reference\s+#?[A-Z]+[-]\d+|"
    r"previous (?:agent|representative|support|rep)\b|"
    r"agent\s+(?:id|#)\s*[:#]?\s*[A-Z]+[-]\d+"
    r")",
    re.I,
)

_CARD_LAST4_RE = re.compile(
    r"\b(?:card\s+(?:last\s*4\s+)?(?:is\s+|in\s+|:\s*)?|"
    r"last\s*4\s+(?:is\s+|in\s+|:\s*)?|"
    r"ending\s+(?:in\s+|with\s+|:\s*)?|"
    r"ends?\s+(?:in\s+|with\s+)?|"
    r"\*{2,}|x{2,})"
    r"\s*(\d{4})\b",
    re.I,
)
_EMAIL_RE = re.compile(r"\b[a-zA-Z0-9_.+\-]+@[a-zA-Z0-9\-]+\.[a-zA-Z0-9\-.]+\b")
_PHONE_4PLUS_RE = re.compile(r"\b\d{7,15}\b")
_USER_ID_RE = re.compile(r"\b(?:USR|user|emp|acct|account)[\s\-_]?[Ii][Dd]?\s*[:=]?\s*[A-Z0-9_\-]+", re.I)

_PRESSURE_RE = re.compile(
    r"\b(?:within (?:\d+\s*)?(?:hour|minute|day)s?|"
    r"respond (?:in\s+)?\d+|escalate to (?:supervisor|manager|legal|press|media)|"
    r"contact(?:ed|ing)?\s+(?:cnn|bbc|press|media|reporter)|"
    r"class action|lawsuit|sue you|filing complaint|"
    r"or i will|or else|immediately|right now|asap|urgent)\b",
    re.I,
)

_SOFT_EXFIL_RE = re.compile(
    r"\b(?:"
    r"which\s+(?:document|file|doc|article|source)|"
    r"just\s+(?:the\s+)?(?:filename|path|name|title)|"
    r"can\s+you\s+(?:share|tell\s+me|name)\s+the\s+(?:document|article|source)|"
    r"what(?:'s| is)\s+the\s+(?:doc|file|article)\s+name|"
    r"name\s+of\s+the\s+document"
    r")\b",
    re.I,
)


@dataclass(frozen=True)
class ConsistencySignal:
    cross_ticket_reference: bool = False
    identity_shift: bool = False
    pressure_tactic: bool = False
    soft_exfiltration_last_turn: bool = False
    tags: tuple[str, ...] = field(default_factory=tuple)
    score: float = 0.0  # 0..1, higher means more inconsistent

    @property
    def needs_escalation(self) -> bool:
        # Cross-ticket reference alone is NOT enough to escalate — users
        # legitimately quote their own case IDs. We escalate when there
        # is also a pressure tactic or identity shift, OR when the last
        # user turn contains a soft exfiltration. Identity shift alone
        # is always an escalation.
        return (
            self.identity_shift
            or self.soft_exfiltration_last_turn
            or (self.cross_ticket_reference and self.pressure_tactic)
        )


def _identities_in_turn(text: str) -> dict[str, set[str]]:
    out: dict[str, set[str]] = {
        "card_last4": set(),
        "email": set(),
        "phone": set(),
        "user_id": set(),
    }
    for m in _CARD_LAST4_RE.finditer(text):
        out["card_last4"].add(m.group(1))
    for m in _EMAIL_RE.finditer(text):
        out["email"].add(m.group(0).lower())
    for m in _PHONE_4PLUS_RE.finditer(text):
        digits = m.group(0)
        if 7 <= len(digits) <= 15:
            out["phone"].add(digits)
    for m in _USER_ID_RE.finditer(text):
        out["user_id"].add(m.group(0).lower())
    return out


def analyze(turns: list[ConversationTurn]) -> ConsistencySignal:
    if not turns:
        return ConsistencySignal()

    tags: list[str] = []
    cross_ticket = False
    pressure = False
    soft_exfil_last = False

    # Cross-ticket references / fake previous agent
    user_texts = [t.content for t in turns if t.role == "user"]
    full_text = "\n".join(user_texts)
    if _TICKET_REF_RE.search(full_text):
        cross_ticket = True
        tags.append("cross_ticket_reference")

    # Pressure tactics anywhere
    if _PRESSURE_RE.search(full_text):
        pressure = True
        tags.append("pressure_tactic")

    # Soft exfiltration in the *last* user turn (after a benign opening)
    last_user = ""
    for t in reversed(turns):
        if t.role == "user" and t.content.strip():
            last_user = t.content
            break
    if last_user and _SOFT_EXFIL_RE.search(last_user):
        soft_exfil_last = True
        tags.append("soft_exfiltration_last_turn")

    # Identity shift across user turns. We collect identities per user turn.
    identity_shift = False
    if len(user_texts) >= 2:
        seen: dict[str, set[str]] = {
            "card_last4": set(),
            "email": set(),
            "phone": set(),
            "user_id": set(),
        }
        for txt in user_texts:
            per_turn = _identities_in_turn(txt)
            for k, vals in per_turn.items():
                if not vals:
                    continue
                if seen[k] and not (vals & seen[k]):
                    identity_shift = True
                    tags.append(f"identity_shift:{k}")
                seen[k] |= vals

    # Score
    components = [
        1.0 if cross_ticket else 0.0,
        1.0 if identity_shift else 0.0,
        0.6 if pressure else 0.0,
        0.8 if soft_exfil_last else 0.0,
    ]
    score = min(1.0, sum(components) / 2.0)

    return ConsistencySignal(
        cross_ticket_reference=cross_ticket,
        identity_shift=identity_shift,
        pressure_tactic=pressure,
        soft_exfiltration_last_turn=soft_exfil_last,
        tags=tuple(tags),
        score=score,
    )
