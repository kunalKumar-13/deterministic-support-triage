"""PII detection + redaction.

Detects common PII categories and produces:

  * a list of `PIIHit` records
  * a redacted version of the input with stable placeholders

The redacted text is what we pass to retrieval / LLM. The original text is
held in memory only for the lifetime of the pipeline call and never logged.
"""

from __future__ import annotations

import re

from ..models import PIIHit


# ---------------------------------------------------------------------------
# Patterns
# ---------------------------------------------------------------------------

# Each detector returns spans; we then validate (e.g. Luhn for cards).

_EMAIL_RE = re.compile(
    r"\b[a-zA-Z0-9_.+\-]+@[a-zA-Z0-9\-]+\.[a-zA-Z0-9\-.]+\b"
)

# Match common phone formats (incl. +country, spaces, dashes, parens).
_PHONE_RE = re.compile(
    r"(?<!\d)(?:\+?\d{1,3}[\s\-]?)?(?:\(?\d{2,4}\)?[\s\-]?)?\d{3,4}[\s\-]?\d{3,4}(?!\d)"
)

# US SSN.
_SSN_RE = re.compile(r"(?<!\d)\d{3}-\d{2}-\d{4}(?!\d)")

# 13-19 digit card-like (validated with Luhn after).
_CARD_RE = re.compile(r"(?<!\d)(?:\d[\s\-]?){13,19}(?!\d)")

# Common API token / secret formats.
_TOKEN_RE = re.compile(
    r"\b(?:sk-(?:ant-)?[A-Za-z0-9_\-]{16,}|"
    r"ghp_[A-Za-z0-9]{20,}|"
    r"AKIA[0-9A-Z]{16}|"
    r"AIza[0-9A-Za-z_\-]{20,}|"
    r"xox[baprs]-[A-Za-z0-9\-]{10,})\b"
)

# IBAN (rough): 2 letters + 2 digits + 11-30 alphanum.
_IBAN_RE = re.compile(r"\b[A-Z]{2}\d{2}[A-Z0-9]{11,30}\b")

# IPv4.
_IP_RE = re.compile(r"(?<!\d)(?:\d{1,3}\.){3}\d{1,3}(?!\d)")

# URL with embedded creds.
_URL_CREDS_RE = re.compile(r"https?://[^\s/:@]+:[^\s/:@]+@\S+")

# Account-id-ish (alphanumeric 6-32 with at least one digit). Heuristic.
_ACCOUNT_ID_RE = re.compile(r"\b(?=[A-Z0-9_\-]*\d)[A-Z0-9_\-]{6,32}\b")

# Loose street address (number + words + suffix). Heuristic.
_ADDRESS_RE = re.compile(
    r"\b\d{1,5}\s+[A-Z][a-z]+(?:\s+[A-Z][a-z]+){0,4}\s+"
    r"(?:St|St\.|Street|Ave|Ave\.|Avenue|Rd|Rd\.|Road|Blvd|Boulevard|Ln|Lane|Dr|Drive|Way|Court|Ct|Plaza)\b"
)


def _luhn_ok(digits: str) -> bool:
    d = [int(c) for c in digits if c.isdigit()]
    if len(d) < 13 or len(d) > 19:
        return False
    total = 0
    parity = len(d) % 2
    for i, n in enumerate(d):
        if i % 2 == parity:
            n *= 2
            if n > 9:
                n -= 9
        total += n
    return total % 10 == 0


# ---------------------------------------------------------------------------
# Public
# ---------------------------------------------------------------------------

class PIIDetector:
    def detect(self, text: str) -> list[PIIHit]:
        if not text:
            return []
        hits: list[PIIHit] = []

        for m in _EMAIL_RE.finditer(text):
            hits.append(PIIHit(kind="email", span=m.span(), placeholder="[email_redacted]"))

        for m in _CARD_RE.finditer(text):
            digits = re.sub(r"\D", "", m.group(0))
            if _luhn_ok(digits):
                last4 = digits[-4:]
                hits.append(
                    PIIHit(
                        kind="card",
                        span=m.span(),
                        placeholder=f"[card_ending_{last4}]",
                    )
                )

        for m in _SSN_RE.finditer(text):
            hits.append(PIIHit(kind="ssn", span=m.span(), placeholder="[ssn_redacted]"))

        for m in _TOKEN_RE.finditer(text):
            hits.append(PIIHit(kind="token", span=m.span(), placeholder="[token_redacted]"))

        for m in _IBAN_RE.finditer(text):
            hits.append(PIIHit(kind="iban", span=m.span(), placeholder="[iban_redacted]"))

        for m in _URL_CREDS_RE.finditer(text):
            hits.append(PIIHit(kind="url_with_credentials", span=m.span(),
                               placeholder="[url_redacted]"))

        for m in _IP_RE.finditer(text):
            # Filter obvious non-IPs.
            parts = m.group(0).split(".")
            if all(0 <= int(p) <= 255 for p in parts):
                hits.append(PIIHit(kind="ip", span=m.span(), placeholder="[ip_redacted]"))

        for m in _ADDRESS_RE.finditer(text):
            hits.append(PIIHit(kind="address", span=m.span(), placeholder="[address_redacted]"))

        # Phone last so it doesn't gobble overlapping card spans.
        used = sorted((h.span for h in hits))

        def _overlap(span: tuple[int, int]) -> bool:
            a, b = span
            for x, y in used:
                if not (b <= x or y <= a):
                    return True
            return False

        for m in _PHONE_RE.finditer(text):
            digits = re.sub(r"\D", "", m.group(0))
            if 7 <= len(digits) <= 15 and not _overlap(m.span()):
                hits.append(PIIHit(kind="phone", span=m.span(), placeholder="[phone_redacted]"))

        # Account IDs last; tightest filter.
        used = sorted((h.span for h in hits))
        for m in _ACCOUNT_ID_RE.finditer(text):
            tok = m.group(0)
            if _overlap(m.span()):
                continue
            if tok.isupper() and len(tok) >= 8 and not tok.startswith("HTTP"):
                hits.append(
                    PIIHit(kind="account_id", span=m.span(),
                           placeholder="[account_id_redacted]")
                )

        # Deterministic sort by start span then kind.
        hits.sort(key=lambda h: (h.span[0], h.span[1], h.kind))
        # Remove duplicates by exact span+kind.
        out: list[PIIHit] = []
        seen: set[tuple[str, tuple[int, int]]] = set()
        for h in hits:
            key = (h.kind, h.span)
            if key in seen:
                continue
            seen.add(key)
            out.append(h)
        return out


def redact_pii(text: str, hits: list[PIIHit]) -> str:
    """Apply hits to produce a redacted copy. Non-overlapping spans only."""
    if not hits:
        return text
    spans = sorted(hits, key=lambda h: h.span[0])
    out: list[str] = []
    cursor = 0
    used_end = 0
    for h in spans:
        a, b = h.span
        if a < used_end:  # skip overlap with prior
            continue
        out.append(text[cursor:a])
        out.append(h.placeholder)
        cursor = b
        used_end = b
    out.append(text[cursor:])
    return "".join(out)


_singleton: PIIDetector | None = None


def get_detector() -> PIIDetector:
    global _singleton
    if _singleton is None:
        _singleton = PIIDetector()
    return _singleton
