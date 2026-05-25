"""Prompt-injection detection.

Rule-based, deterministic, multi-lingual. Returns a score in [0,1] plus
the matching spans. Score is calibrated so:

  * >= 0.85  -> critical (canned refusal, force escalate)
  * >= 0.70  -> high (escalate, neutral acknowledgement)
  * >= 0.40  -> medium (escalate one risk level, normal flow)
  *  < 0.40  -> none

The detector NEVER consults the LLM. That is by design — the safety gate
must not itself be vulnerable to prompts.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass

from ..models import InjectionHit


# ---------------------------------------------------------------------------
# Pattern bank
# ---------------------------------------------------------------------------

# Each pattern is (regex, severity 0..1).  Higher severity => stronger signal.
# Patterns are compiled lazily once.

_PATTERNS_RAW: tuple[tuple[str, float], ...] = (
    # English: classic instruction-override
    (r"ignore (?:all |the |any |your )?(?:previous|prior|above|earlier|preceding) (?:instructions?|prompts?|messages?|rules?|policies?)", 0.95),
    (r"disregard (?:all |the )?(?:previous|prior|above)(?:\s+(?:instructions?|prompts?|text|messages?|content))?", 0.90),
    (r"forget (?:everything|all|the previous)", 0.85),
    (r"(?:override|bypass|circumvent) (?:the )?(?:safety|policy|system|guidelines?)", 0.92),
    (r"(?:enter|activate|switch to|now in|now using) (?:developer|debug|admin|god|jailbreak|dan) mode", 0.95),
    (r"\b(?:you are|i am) now (?:in|using) (?:developer|debug|admin|god|jailbreak|dan) mode\b", 0.95),
    (r"you are (?:now|actually) (?:a |an )?(?:different|unrestricted|jailbroken|dan)", 0.92),
    (r"pretend (?:to be|you are) (?:a |an )?(?:different|new|unrestricted|jailbroken)", 0.80),
    (r"act as (?:if you (?:were|are)|a)\b[^.\n]{0,40}?(?:different|new|unrestricted|jailbroken|uncensored)", 0.78),

    # System / role hijack
    (r"\b(?:system|assistant|developer)\s*[:>\-]\s*(?:you (?:must|should|will)|please|now)", 0.85),
    (r"\[/?(?:system|inst|im_start|im_end|assistant)\]", 0.85),
    (r"<\|?(?:system|im_start|im_end|assistant|user)\|?>", 0.85),
    (r"\[\s*system[\s_\-]*(?:override|reset|admin|maintenance|alert)\s*\]", 0.92),
    (r"###\s*(?:system|assistant|instruction)s?", 0.70),
    (r"new instructions?\s*[:\-]", 0.70),
    (r"previous\s+conversation\s+context\s*[:\-]", 0.85),
    (r"\[agent:\s*i\s+(?:approved|granted|authorized|promised)\b", 0.88),

    # Exfiltration
    (r"(?:reveal|show|print|output|repeat|disclose|leak) (?:me )?(?:your |the )?(?:system|initial|hidden|secret) prompt", 0.95),
    (r"what (?:were|are) your (?:initial|original|system) (?:instructions?|prompts?)", 0.90),
    (r"(?:repeat|print) (?:back )?(?:the (?:above|previous) )?(?:text|message|prompt) verbatim", 0.85),
    (r"what (?:llm|model|company) (?:are |built |trained )you", 0.70),
    (r"list (?:every|all) (?:file|document|chunk|tool)s? (?:in|under) ", 0.78),

    # Action / policy manipulation
    (r"(?:refund|reimburse) (?:me )?(?:immediately|right now|without (?:verification|verifying))", 0.92),
    (r"(?:set|change|update) (?:the )?(?:refund|amount|status|classification) to", 0.80),
    (r"classify (?:this(?:\s+(?:ticket|case|issue|message))?|the ticket|it) as (?:replied|escalated|low|critical|high|medium|product_issue|invalid|bug|feature_request)", 0.92),
    (r"(?:set|change|update)\s+(?:the\s+)?(?:status|classification|risk|confidence)\s+(?:to|as)\s+\S+", 0.85),
    (r"(?:treat|consider) (?:this(?:\s+(?:ticket|case|issue))?|the ticket|it|the case) as (?:replied|escalated|low|critical|high|medium|safe)", 0.85),
    (r"mark (?:this(?:\s+(?:ticket|case|issue))?|the ticket|it) as (?:resolved|replied|escalated|closed|complete)", 0.85),
    (r"\b(?:set|change|update)\s+(?:the\s+)?(?:risk(?:\s+level)?|confidence|priority|status|classification|request[\s_]?type)\s+(?:to|as|=)\s+\S+", 0.88),
    (r"\bchange\s+(?:the\s+)?request[\s_]?type\s+to\s+(?:invalid|product_issue|bug|feature_request)", 0.90),

    # Multilingual: Spanish
    (r"ignor[ae] (?:todas? )?las (?:instrucciones|reglas) (?:anteriores|previas)", 0.95),
    (r"olvida (?:todas? )?(?:las )?(?:instrucciones|reglas) (?:anteriores|previas)", 0.90),
    (r"modo (?:desarrollador|jailbreak)", 0.85),

    # Multilingual: French
    (r"ignore[zr]? (?:toutes? )?les instructions (?:pr[eé]c[eé]dentes|ant[eé]rieures)", 0.95),
    (r"oublie[zr]? (?:toutes? )?(?:les )?instructions (?:pr[eé]c[eé]dentes)", 0.90),

    # Multilingual: German
    (r"ignoriere?n? (?:alle )?(?:vorherigen|fr[uü]heren|bisherigen) anweisungen", 0.95),
    (r"vergiss (?:alle )?(?:vorherigen|fr[uü]heren) anweisungen", 0.90),

    # Multilingual: Hindi (devanagari)
    (r"पिछले\s+निर्देश(?:ों)?\s+(?:को\s+)?(?:अनदेखा|भूल)", 0.90),

    # Multilingual: Chinese (forget instructions)
    (r"(?:忽略|忘记)(?:[之以]前|所有|上面|上述|先前)?[的之]?(?:所有)?(?:指令|指示|提示|规则)", 0.90),

    # Multilingual: Arabic (ignore previous)
    (r"تجاهل\s+(?:جميع\s+)?(?:التعليمات|الأوامر)\s+(?:السابقة|الماضية)", 0.90),

    # Indirect / social-engineered
    (r"this is (?:a |an )?(?:test|drill|simulation) (?:so|and you can) (?:safely|now)", 0.65),
    (r"(?:my|the) (?:lawyer|attorney|ceo|manager) (?:said|requires|demands) you", 0.55),
    (r"(?:as|since) you are (?:helpful|smart|capable), you (?:will|must|should)", 0.45),

    # Hidden-instruction markers (often inside fake quoted policies)
    (r"begin (?:system|admin) instructions?[:\-]", 0.85),
    (r"\bDAN\b.*(?:mode|prompt)", 0.85),

    # Generic imperative + system/policy mention
    (r"\b(?:you (?:must|will|shall)|please) (?:now )?(?:disregard|override|ignore) ", 0.75),

    # ---- French exfiltration / output requests ----
    (r"(?:affiche|montre|donne|envoie|liste|d[ée]voile)[zr]?\s+(?:moi\s+)?(?:toutes?\s+)?(?:les\s+)?(?:r[èe]gles|instructions?|documents?|prompts?|outils?|logiques?|donn[ée]es?)\s+(?:internes?|syst[èe]me|cach[ée]e?s?|secr[èe]tes?|exactes?|compl[èe]tes?)", 0.92),
    (r"affiche[zr]?\s+(?:toutes?\s+)?(?:les\s+)?r[èe]gles", 0.85),
    (r"donne[zr]?\s+(?:moi\s+)?(?:la\s+)?logique\s+exacte", 0.90),

    # ---- German exfiltration / output requests ----
    (r"(?:gib|geben\s+sie|zeig|stell)\s*(?:mir\s+)?(?:alle\s+|den\s+|das\s+|deine\s+)?(?:anweisungen|regeln|systemprompt|prompt|dokumente?|daten|werkzeuge?|tools?)\s*(?:aus)?", 0.90),
    (r"ignoriere[n]?\s+(?:sie\s+)?(?:ihre\s+|deine\s+|alle\s+|die\s+)?(?:system[\-\s]?anweisungen|systemprompt|anweisungen|regeln)", 0.95),
    (r"alle\s+(?:visa|claude|devplatform)[\-\s]?kundeninformationen", 0.95),

    # ---- Spanish exfiltration ----
    (r"(?:muestra|env[ií]a|dame|comparte|imprime)\s+(?:tu\s+|el\s+|la\s+|todos\s+|todas\s+)?(?:prompt|instrucciones|reglas|documentos|herramientas|datos)\s+(?:del\s+)?(?:sistema|internas?|completos?|exactos?)?", 0.90),

    # ---- Generic strong exfiltration ----
    (r"\b(?:output|emit|return|print|display|share|tell\s+me|give\s+me|reveal)\s+(?:(?:your|the|all|every|complete|full|entire|names?\s+of|list\s+of|system)\s+){0,5}(?:prompt|instructions?|rules?|tools?|tool\s+list|tool\s+names?|function\s+names?|functions?|memor(?:y|ies)|database|catalog|knowledge\s+base|kb|corpus)", 0.92),
    (r"\b(?:list|enumerate|name)\s+(?:every|all|the)\s+(?:support\s+)?(?:articles?|documents?|files?|chunks?|tools?|sources?|functions?)\b", 0.85),
    (r"\bprint\s+(?:the|your)\s+(?:entire|whole|full|complete)\s+(?:corpus|database|memory|catalog|knowledge\s+base)", 0.92),
    (r"\b(?:output|print)\s+(?:the|your)\s+(?:full|complete|entire)\s+system\s+prompt", 0.95),
    # Chinese exfiltration
    (r"(?:输出|打印|展示|告诉我)(?:你的)?(?:完整|全部|所有)?(?:系统)?(?:提示|指令|规则|工具)", 0.92),

    # ---- Fake-auth / impersonation injection ----
    (r"\bAUTH[_\-]?CODE\s*[:=]\s*[A-Z0-9_\-]+", 0.85),
    (r"\bALERT[_\-]?ACK[_\-]?[A-Z0-9_\-]+", 0.85),
    (r"\bvalidate\s+by\s+(?:responding|saying|outputting)\s+", 0.80),
    (r"\bemployee[\s_\-]?id\s*[:=]\s*[A-Z]+[\-_]?\d+", 0.80),
    (r"\baccess[\s_\-]?level\s*[:=]\s*L?\d+", 0.75),
    (r"\bauthorized\s+by\s+(?:the\s+)?(?:ministry|government|federal|agency|board|cto|ceo|ciso|vp)\b", 0.78),
    (r"\bi\s+am\s+(?:a\s+|an\s+|the\s+)?(?:devplatform|anthropic|visa|company|internal|trust\s*(?:and|&)?\s*safety|t&s|trs|qa|security|monitoring)\s+(?:employee|engineer|auditor|admin|operator|representative|agent|team|member|staff)\b", 0.82),
    # Variants: "I am / I'm from the X team / department"
    (r"\bi\s*'?m\s+(?:from|with|on|part\s+of)\s+(?:the\s+)?(?:devplatform|anthropic|visa)\s+(?:trust\s*(?:and|&)?\s*safety|security|fraud|risk|compliance|legal|engineering|product|qa|monitoring|operations|t&s)\s*(?:team|department|group)?\b", 0.85),
    (r"\bi\s+am\s+from\s+(?:the\s+)?(?:devplatform|anthropic|visa)\s+\w+\s+team\b", 0.85),
    (r"\bas\s+(?:a\s+|the\s+|an?\s+)?(?:senior\s+)?(?:ciso|cto|ceo|vp|director|admin|administrator|monitoring\s+system|t&s|trust\s*(?:and|&)?\s*safety|qa\s+engineer|devplatform\s+admin)\b", 0.78),
    (r"\bthis\s+is\s+(?:an\s+)?automated\s+message\s+from\s+(?:your\s+)?(?:monitoring|alerting|security)", 0.85),

    # ---- Third-party authorization impersonation ----
    # "I am the spouse / I am the cardholder's relative / on behalf of...".
    # We require the third-party role AND an authorization/cancellation
    # intent in the same payload before scoring high.
    (r"\bi\s+am\s+(?:that\s+person'?s\s+|the\s+|the\s+cardholder'?s\s+|my\s+)?(?:spouse|husband|wife|partner|parent|mother|father|guardian|relative|family\s+member|representative|attorney|caregiver|next\s+of\s+kin|power\s+of\s+attorney|trustee|executor)\b", 0.78),
    (r"\bi\s+authorize\s+(?:this|that|the\s+(?:refund|action|change|cancellation|reversal))\s+(?:on\s+behalf\s+of|for|to)\b", 0.90),
    (r"\bon\s+behalf\s+of\s+(?:the\s+|my\s+|that\s+)?(?:cardholder|account\s+holder|user|customer|client|patient|spouse|relative)\b", 0.80),
    # A cancel-the-dispute request is only adversarial when paired with
    # third-party-authorization context. Otherwise the customer is asking
    # a legitimate question ("should I withdraw the chargeback?"). So we
    # use two patterns: the strong one requires authorization phrasing
    # within ~160 chars; the weak one only escalates the "fraud report"
    # subtype, which is rarely something the user themselves would cancel.
    (r"\b(?:please\s+)?(?:cancel|reverse|undo|withdraw|drop|stop)\s+(?:that|the|my)\s+(?:previous\s+|prior\s+|earlier\s+)?fraud\s+report\b", 0.78),
    (r"\b(?:please\s+)?(?:cancel|reverse|undo|withdraw|drop|stop)\s+(?:that|the|my)\s+(?:fraud\s+report|dispute|chargeback|case|complaint)\b[\s\S]{0,160}\b(?:on\s+behalf|i\s+authorize|i\s+am\s+(?:the|that\s+person'?s)\s+(?:spouse|cardholder|representative|relative|partner|parent))\b", 0.90),

    # ---- Soft exfiltration ("which document did you use?") ----
    (r"\bwhich\s+(?:document|doc|file|article|source|chunk)\s+(?:did|do|are|were|was)\s+you\s+(?:use|pull|retrieve|reference|cite)", 0.78),
    (r"\b(?:just|only)\s+(?:the\s+)?(?:filename|file\s+name|file\s+path|document\s+name|title)\s+is\s+fine\b", 0.85),
    (r"\bcan\s+you\s+(?:share|tell\s+me|name|provide)\s+(?:me\s+)?(?:the\s+)?(?:document|article|source)\s+(?:name|filename|path|title)", 0.80),

    # ---- Excel / spreadsheet formula injection (DDE) ----
    (r"^\s*[=+\-@]\s*(?:cmd|HYPERLINK|WEBSERVICE|DDE|IMPORT|GOTO)\b", 0.85),

    # ---- Tool-spec mimicry inside user payload ----
    (r"\{\s*\"action\"\s*:\s*\"(?:issue_refund|lock_account|verify_identity|escalate_to_human|open_dispute)\"", 0.88),
    (r"\bcall\s+(?:internal_tool|tool|function)\s*\(\s*['\"]?(?:issue_refund|lock_account|verify_identity)", 0.88),
)


@dataclass
class _CompiledPattern:
    regex: re.Pattern[str]
    severity: float


def _compile() -> list[_CompiledPattern]:
    return [
        _CompiledPattern(re.compile(rx, re.IGNORECASE | re.UNICODE), sev)
        for rx, sev in _PATTERNS_RAW
    ]


_COMPILED: list[_CompiledPattern] = _compile()


# Base64 detector + safe decoder. We only decode contiguous base64-alphabet
# runs of >= 32 chars to avoid false positives on long alphanumeric IDs.
_B64_RUN_RE = re.compile(r"[A-Za-z0-9+/]{32,}={0,2}")


def _try_base64_decode(text: str) -> str:
    if not text or "=" not in text and len(text) < 32:
        # Cheap reject: most non-base64 text doesn't have any '=' padding
        # and isn't long enough; still scan for unpadded long runs.
        pass
    import base64
    out_parts: list[str] = []
    for m in _B64_RUN_RE.finditer(text):
        chunk = m.group(0)
        try:
            decoded = base64.b64decode(chunk + "==", validate=False)
            s = decoded.decode("utf-8", errors="ignore")
            # Skip if the decoded text looks like binary garbage.
            if s and sum(1 for c in s if c.isprintable() or c in "\n\r\t") / len(s) > 0.75:
                out_parts.append(s)
        except Exception:
            continue
    return "\n".join(out_parts)


# ---------------------------------------------------------------------------
# Normalisation
# ---------------------------------------------------------------------------

_ZERO_WIDTH = dict.fromkeys(
    (0x200B, 0x200C, 0x200D, 0x2060, 0xFEFF, 0x202A, 0x202B, 0x202C, 0x202D, 0x202E),
    None,
)

# Common leet-substitutions to fold for detection only.
_LEET_MAP = str.maketrans({
    "0": "o", "1": "i", "3": "e", "4": "a", "5": "s", "7": "t",
    "@": "a", "$": "s", "!": "i",
})

# Cyrillic + Greek homoglyph -> Latin look-alike. Detection only.
_CONFUSABLES_MAP = str.maketrans({
    "а": "a", "б": "b", "с": "c", "е": "e", "и": "i", "і": "i",
    "ј": "j", "ӏ": "l", "о": "o", "р": "p", "ѕ": "s", "т": "t",
    "и": "i", "у": "y", "х": "x", "А": "a", "В": "b", "С": "c",
    "Е": "e", "Н": "h", "І": "i", "О": "o", "Р": "p", "Т": "t",
    "Х": "x", "α": "a", "ο": "o", "ρ": "p", "ε": "e", "ν": "v",
})


def normalise_for_detection(s: str) -> str:
    """Aggressive normalisation: NFKC, strip zero-width, fold confusables, lower."""
    if not s:
        return ""
    s = unicodedata.normalize("NFKC", s)
    s = s.translate(_ZERO_WIDTH)
    # Strip control chars but keep newlines.
    s = "".join(ch for ch in s if ch == "\n" or unicodedata.category(ch)[0] != "C")
    # Light leet fold ONLY for detection (originals are preserved upstream).
    s_lower = s.lower()
    s_leet = s_lower.translate(_LEET_MAP)
    # Detection runs on both; we union the matches.
    s_conf = s_lower.translate(_CONFUSABLES_MAP)
    return s_lower + chr(10) + s_leet + chr(10) + s_conf


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

class InjectionDetector:
    """Stateless detector. Use as a singleton; the patterns are precompiled."""

    def __init__(self) -> None:
        self._patterns = _COMPILED

    def detect(self, text: str) -> tuple[float, list[InjectionHit]]:
        if not text:
            return 0.0, []
        normalised = normalise_for_detection(text)
        decoded = _try_base64_decode(text)
        if decoded:
            normalised = normalised + "\n" + normalise_for_detection(decoded)
        hits: list[InjectionHit] = []
        for cp in self._patterns:
            for m in cp.regex.finditer(normalised):
                hits.append(
                    InjectionHit(
                        pattern=cp.regex.pattern[:80],
                        span=(m.start(), m.end()),
                        severity=cp.severity,
                    )
                )

        if not hits:
            return 0.0, []

        # Score = max severity, boosted slightly when multiple high-severity hits
        # are present. Capped at 1.0.
        max_sev = max(h.severity for h in hits)
        n_high = sum(1 for h in hits if h.severity >= 0.7)
        boost = min(0.10, 0.03 * max(0, n_high - 1))
        score = min(1.0, max_sev + boost)

        # Deduplicate by (pattern, span) deterministically.
        seen: set[tuple[str, tuple[int, int]]] = set()
        dedup: list[InjectionHit] = []
        for h in sorted(hits, key=lambda x: (-x.severity, x.span[0], x.pattern)):
            key = (h.pattern, h.span)
            if key in seen:
                continue
            seen.add(key)
            dedup.append(h)
        return score, dedup


_singleton: InjectionDetector | None = None


def get_detector() -> InjectionDetector:
    global _singleton
    if _singleton is None:
        _singleton = InjectionDetector()
    return _singleton
