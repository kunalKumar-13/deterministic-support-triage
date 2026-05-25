"""Deterministic language identification.

We avoid heavy third-party deps; this is a small heuristic that handles the
ISO-639-1 codes we care about. The fallback is "en".

The detector is used:
  * for the `language` output column
  * to apply the right multilingual injection patterns
  * to choose response wording (we always respond in English unless the
    body is clearly non-English; even then we keep the canned escalation
    template English to avoid grounding errors)
"""

from __future__ import annotations

import re

# Block ranges sourced from Unicode standard. Crude but stable.
_SCRIPT_RANGES = (
    ("zh", (0x4E00, 0x9FFF)),   # CJK Unified
    ("ja", (0x3040, 0x30FF)),   # Hiragana / Katakana
    ("ko", (0xAC00, 0xD7AF)),   # Hangul
    ("ar", (0x0600, 0x06FF)),   # Arabic
    ("he", (0x0590, 0x05FF)),   # Hebrew
    ("hi", (0x0900, 0x097F)),   # Devanagari
    ("ru", (0x0400, 0x04FF)),   # Cyrillic
    ("el", (0x0370, 0x03FF)),   # Greek
    ("th", (0x0E00, 0x0E7F)),   # Thai
)

# Stop-word hints for languages with shared alphabet.
# We use words >= 3 chars and language-distinctive accents to avoid collisions
# with English. The detector prefers English unless another language wins
# convincingly.
_EUROPEAN_HINTS = {
    "es": re.compile(
        r"\b(?:los|las|una|que|para|gracias|por favor|hola|esto|esta|tambi[eé]n|porque|"
        r"reembolso|cuenta|tarjeta|usuario|contrase[nñ]a|p[aá]gina|ahora|m[aá]s)\b",
        re.I,
    ),
    "fr": re.compile(
        r"\b(?:les|une|pour|merci|bonjour|aussi|parce|comment|compte|carte|"
        r"utilisateur|mot de passe|page|maintenant|s'il vous pla[iî]t|tr[eè]s)\b",
        re.I,
    ),
    "de": re.compile(
        r"\b(?:der|die|das|und|nicht|f[uü]r|danke|bitte|hallo|warum|konto|karte|"
        r"benutzer|passwort|seite|jetzt|auch|noch|sehr)\b",
        re.I,
    ),
    "pt": re.compile(
        r"\b(?:uma|para|obrigad[oa]|por favor|ol[aá]|tamb[eé]m|porque|conta|cart[aã]o|"
        r"utilizador|usu[aá]rio|senha|p[aá]gina|agora|n[aã]o)\b",
        re.I,
    ),
    "it": re.compile(
        r"\b(?:una|che|per|grazie|per favore|ciao|anche|perch[eé]|conto|carta|"
        r"utente|password|pagina|adesso|molto|sono|sono|non)\b",
        re.I,
    ),
}

# Accent characters that are clear non-English signals.
_ACCENT_HINTS = {
    "es": re.compile(r"[ñáéíóúü¿¡]"),
    "fr": re.compile(r"[àâçéèêëîïôûùüÿœæ]"),
    "de": re.compile(r"[äöüß]"),
    "pt": re.compile(r"[ãõáéíóúâêô]"),
    "it": re.compile(r"[àèéìòù]"),
}


def detect_language(text: str) -> str:
    if not text:
        return "en"
    # Script first (strongest signal).
    counts: dict[str, int] = {}
    for ch in text:
        cp = ord(ch)
        for code, (lo, hi) in _SCRIPT_RANGES:
            if lo <= cp <= hi:
                counts[code] = counts.get(code, 0) + 1
                break
    if counts:
        best, n = max(counts.items(), key=lambda kv: (kv[1], kv[0]))
        if n >= 5 or n / max(1, len(text)) > 0.10:
            return best

    # European: combine stopword hits with accent hits. Require both signals
    # to fire or a high stopword count, otherwise prefer English.
    word_hits = {code: len(rx.findall(text)) for code, rx in _EUROPEAN_HINTS.items()}
    accent_hits = {code: len(rx.findall(text)) for code, rx in _ACCENT_HINTS.items()}

    scored: list[tuple[int, int, str]] = []
    for code, w in word_hits.items():
        a = accent_hits.get(code, 0)
        # Boost languages with accents (very distinctive); require >= 2 hints
        # OR >= 1 hint + >= 1 accent OR >= 3 hints.
        combined = w + 2 * a
        scored.append((combined, w, code))

    scored.sort(reverse=True)
    top_combined, top_words, top_code = scored[0]
    if top_combined >= 3 or (top_words >= 2 and accent_hits.get(top_code, 0) >= 1):
        return top_code

    return "en"
