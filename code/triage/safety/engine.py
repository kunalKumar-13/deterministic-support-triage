"""Top-level safety assessment.

`assess(text, in_scope)` runs the injection detector, PII detector, language
identifier, and risk classifier in deterministic order. Returns a
`SafetyAssessment` model.

This is the function the pipeline calls. It is the ONLY entry point into the
safety subsystem.
"""

from __future__ import annotations

import unicodedata

from ..models import SafetyAssessment
from .injection import get_detector as _get_injection_detector
from .language import detect_language
from .pii import get_detector as _get_pii_detector
from .pii import redact_pii
from .risk import classify_risk

_ZW_DROP = dict.fromkeys(
    (0x200B, 0x200C, 0x200D, 0x2060, 0xFEFF, 0x202A, 0x202B, 0x202C, 0x202D, 0x202E),
    None,
)


def normalise(text: str) -> str:
    if not text:
        return ""
    text = unicodedata.normalize("NFKC", text)
    text = text.translate(_ZW_DROP)
    text = "".join(ch for ch in text if ch == "\n" or unicodedata.category(ch)[0] != "C")
    return text


def assess(text: str, *, is_in_scope: bool = True) -> SafetyAssessment:
    normalised = normalise(text)

    # Injection
    inj_det = _get_injection_detector()
    inj_score, inj_hits = inj_det.detect(normalised)

    # PII
    pii_det = _get_pii_detector()
    pii_hits = pii_det.detect(normalised)
    pii_detected = bool(pii_hits)
    redacted = redact_pii(normalised, pii_hits)

    # Language (operate on normalised; the user's text, not the redacted one)
    lang = detect_language(normalised)

    # Risk: use redacted text (no PII strings) + signals
    risk, reasons = classify_risk(
        text=redacted,
        injection_score=inj_score,
        pii_kinds=[h.kind for h in pii_hits],
        is_in_scope=is_in_scope,
    )

    return SafetyAssessment(
        injection_score=inj_score,
        injection_hits=inj_hits,
        pii_detected=pii_detected,
        pii_hits=pii_hits,
        language=lang,
        risk_level=risk,
        risk_reasons=reasons,
        redacted_text=redacted,
        is_in_scope=is_in_scope,
    )
