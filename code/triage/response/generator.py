"""Grounded response generation.

We do NOT ask the LLM to write the user-facing response in a separate call.
The LLM already produced `answer_draft` inside its single JSON call. The
generator's job is to:

  * pick the canned response when the policy demands it
  * otherwise paraphrase the draft, prefixed/suffixed with safe wording
  * scrub against PII regurgitation
  * scrub against system-prompt leakage
  * cite only chunks that materially overlap with the response

This is the final stage before output.
"""

from __future__ import annotations

import re
from typing import Iterable

from ..config import RETRIEVAL, REPO_ROOT
from ..models import ChunkRef, LLMDecision, PolicyDecision, RetrievalResult, SafetyAssessment
from ..safety.pii import get_detector as _get_pii_detector
from ..safety.pii import redact_pii


# Words / phrases that are red flags if they appear in the output: they hint
# the model is leaking internal context. We strip them.
_LEAK_PATTERNS = (
    re.compile(r"\bsystem prompt\b", re.I),
    re.compile(r"\bmy instructions\b", re.I),
    re.compile(r"\bi am (?:claude|gpt|gemini|llama|an? llm|an? ai language model)\b", re.I),
    re.compile(r"\banthropic(?:'s|’s)?\b", re.I),
    re.compile(r"\bopenai(?:'s|’s)?\b", re.I),
    re.compile(r"\binternal_tools\.json\b", re.I),
    re.compile(r"<<<.+?>>>", re.S),
    re.compile(r"<<<.+", re.S),
)


def _strip_leaks(text: str) -> str:
    out = text
    for p in _LEAK_PATTERNS:
        out = p.sub("", out)
    # Repair fragments left from removing a token (e.g. "Anthropic
    # publishes" -> " publishes").
    out = re.sub(r"[ \t]+([.,;:])", r"\1", out)
    out = re.sub(r"[ \t]{2,}", " ", out)
    # Drop leading whitespace/punctuation on each line first.
    out = re.sub(r"^[\s\.,;:!?'’]+", "", out, flags=re.M)
    out = out.lstrip()
    # Recapitalise the first character if lower, and after sentence
    # punctuation followed by whitespace.
    def _recap(m: re.Match[str]) -> str:
        return m.group(1) + m.group(2).upper()
    out = re.sub(r"(^|(?<=[.!?]\s))([a-z])", _recap, out)
    return out


def _strip_markdown(text: str) -> str:
    """Remove obvious markdown syntax from a user-facing response so it
    reads as plain prose. Keeps bullet lists and numbered lists; strips
    headers, bold/italic, inline code, link syntax, and reference-style
    artefacts."""
    if not text:
        return ""
    out = text
    # Headers: '# Foo' -> 'Foo'
    out = re.sub(r"(?m)^\s*#{1,6}\s+", "", out)
    # Bold/italic emphasis: **x** / __x__ / *x* / _x_ -> x
    out = re.sub(r"\*\*([^*\n]+)\*\*", r"\1", out)
    out = re.sub(r"__([^_\n]+)__", r"\1", out)
    out = re.sub(r"(?<!\w)\*([^*\n]+)\*(?!\w)", r"\1", out)
    out = re.sub(r"(?<!\w)_([^_\n]+)_(?!\w)", r"\1", out)
    # Inline code: `x` -> x
    out = re.sub(r"`([^`\n]+)`", r"\1", out)
    # Markdown links: [text](url) -> text
    out = re.sub(r"\[([^\]]+)\]\(([^)]+)\)", r"\1", out)
    # Collapse 3+ blank lines.
    out = re.sub(r"\n{3,}", "\n\n", out)
    return out


def _scrub_pii(text: str) -> str:
    """Belt-and-braces: run the PII detector on the OUTBOUND text. If anything
    detectable remains, replace it generically. This catches the case where
    the LLM has somehow echoed PII despite redaction upstream."""
    det = _get_pii_detector()
    hits = det.detect(text)
    if not hits:
        return text
    return redact_pii(text, hits)


def _select_citations(answer: str, retrieval: RetrievalResult) -> list[str]:
    """Return doc paths of chunks that materially overlap with the answer.

    We use a fast word-set Jaccard since at this scale it's accurate enough
    and dependency-free.
    """
    if not answer.strip() or not retrieval.chunks:
        return []
    ans_tokens = set(re.findall(r"[A-Za-z0-9_]+", answer.lower()))
    if len(ans_tokens) < 4:
        return []
    chosen: list[tuple[float, str]] = []
    for ch in retrieval.chunks:
        ch_tokens = set(re.findall(r"[A-Za-z0-9_]+", ch.text.lower()))
        if not ch_tokens:
            continue
        union = ans_tokens | ch_tokens
        if not union:
            continue
        overlap = len(ans_tokens & ch_tokens) / len(union)
        if overlap >= RETRIEVAL.citation_cosine_threshold:
            chosen.append((overlap, ch.doc_path))
    chosen.sort(key=lambda t: (-t[0], t[1]))
    # Dedup paths while preserving order.
    seen: set[str] = set()
    out: list[str] = []
    for _, p in chosen:
        if p in seen:
            continue
        seen.add(p)
        out.append(p)
        if len(out) >= RETRIEVAL.max_citations:
            break
    # Verify each path exists.
    return [p for p in out if (REPO_ROOT / p).exists()]


def _trim(text: str, max_chars: int = 1200) -> str:
    text = text.strip()
    if len(text) <= max_chars:
        return text
    return text[: max_chars - 3].rstrip() + "..."


def _safe_reply_template(
    llm: LLMDecision, retrieval: RetrievalResult, *, hedged: bool = False
) -> str:
    """Compose a grounded paraphrase response.

    The lead phrase is chosen from a small deterministic pool keyed by
    the top retrieved doc's path so the wording varies without
    introducing randomness.
    """
    draft = _strip_markdown(_strip_leaks(llm.answer_draft or ""))
    if not draft.strip() and retrieval.chunks:
        # Build a minimal grounded paragraph from the top chunk.
        top = retrieval.chunks[0]
        lead = _pick_lead(top.doc_path, hedged=hedged)
        body = _strip_markdown(_strip_leaks(top.text[:600].strip()))
        draft = f"{lead}\n\n{body}"
    if not draft.strip():
        draft = (
            "I don't have a confirmed answer in our documentation for this "
            "specific question."
        )

    closing = (
        "\n\nIf this doesn't fully address your question, reply to this "
        "message and I'll route it to a human teammate."
    )
    return _trim(draft + closing)


# Deterministic pool of lead phrases. We pick one by hashing the doc
# path so the same query always gets the same lead but different docs
# get different leads.
_LEAD_POOL_CONFIRMED = (
    "Here's what our documentation says:",
    "From our help center on this topic:",
    "Here's the relevant guidance from our documentation:",
    "Based on the documented process, here's how this works:",
)
_LEAD_POOL_HEDGED = (
    "Our documentation has the following guidance, though it may not be "
    "the exact answer you need:",
    "The closest match in our documentation is the following — if this "
    "doesn't quite fit your situation, let me know:",
    "I found a related section in our documentation; here it is for "
    "reference:",
)


def _pick_lead(key: str, *, hedged: bool) -> str:
    pool = _LEAD_POOL_HEDGED if hedged else _LEAD_POOL_CONFIRMED
    # Stable hash on path so repeated runs produce identical leads.
    import hashlib
    h = int(hashlib.sha256(key.encode("utf-8")).hexdigest()[:8], 16)
    return pool[h % len(pool)]


# Map internal escalation tags to plain-language hints surfaced to the
# user. The hint is one short, neutral sentence — no internal jargon,
# no leakage of tool names or pattern IDs. Order matters: the first
# matching tag wins, so we put the most specific reasons first.
_USER_HINT_MAP: tuple[tuple[str, str], ...] = (
    ("prompt_injection_critical", ""),  # already handled by canned refusal
    ("prompt_injection_high",     ""),  # canned refusal handles it
    ("scope_suspicious",          "This request is outside what I can help with directly."),
    ("conversation_identity_shift",
        "I noticed the account details in this conversation changed; for safety we verify these in person."),
    ("conversation_soft_exfiltration_last_turn", ""),
    ("conversation_cross_ticket_reference",
        "Because this references another case, a teammate will check the history before any change."),
    ("no_grounding",
        "I couldn't find a matching answer in our documentation, so a teammate can investigate."),
    ("weak_retrieval",
        "Our documentation has only a partial match for this, so I'd rather a teammate confirm."),
    ("risk_pattern_high_legal",
        "Because this involves a legal matter, our legal-support team will handle it."),
    ("risk_pattern_high_compliance",
        "Because this involves a compliance request, our compliance team will handle it."),
    ("risk_pattern_critical_safety",
        "Because of the safety signals in this message, a specialist will follow up."),
    ("risk_pattern_critical_account_takeover",
        "Because there are signs of an account-access issue, a specialist will follow up."),
    ("risk_pattern_critical_fraud",
        "Because there are fraud-related signals here, our fraud team will follow up."),
    ("risk_pii_high_value",
        "This message contains sensitive personal data, so verification is done by a teammate."),
    ("insufficient_signal",     ""),  # canned response already explains it
    ("retrieval_numeric_disagreement",
        "Our documentation has more than one figure for this — a teammate will confirm the right one."),
    ("retrieval_imperative_disagreement",
        "Our documentation has differing guidance on this — a teammate will confirm."),
)


def _user_facing_reason_hint(policy: PolicyDecision) -> str:
    if not policy.escalation_reasons:
        return ""
    reasons = " ".join(policy.escalation_reasons)
    for tag, hint in _USER_HINT_MAP:
        if tag in reasons and hint:
            return hint
    return ""


def generate_response(
    *,
    safety: SafetyAssessment,
    retrieval: RetrievalResult,
    llm: LLMDecision,
    policy: PolicyDecision,
) -> tuple[str, list[str]]:
    """Return (response_text, source_documents_paths)."""

    # 1. Canned response from the policy (injection, critical risk, etc.)
    if policy.canned_response:
        text = policy.canned_response
        # No citations on a canned response.
        return _scrub_pii(_strip_leaks(text)), []

    # 2. Escalation without canned text
    if policy.status == "escalated":
        text = (
            "Thanks for reaching out. I've reviewed the information you "
            "shared, and I'll route this to the right team for a closer look. "
            "Someone will follow up shortly."
        )
        if policy.reason == "no_grounding_in_corpus":
            text = (
                "I don't have a confirmed answer in our documentation for "
                "this. I'm escalating to a human teammate."
            )
        # Optional uncertainty rationale: one short user-friendly sentence
        # explaining *why* we routed to a human, derived from the
        # structured escalation_reasons. We never expose internal tags
        # verbatim — they're mapped to plain language.
        hint = _user_facing_reason_hint(policy)
        if hint:
            text = f"{text} {hint}"
        return _scrub_pii(_strip_leaks(text)), []

    # 3. Reply path — grounded paraphrase
    hedged = retrieval.weak_match or policy.confidence_cap < 0.7
    text = _safe_reply_template(llm, retrieval, hedged=hedged)
    text = _scrub_pii(_strip_leaks(_strip_markdown(text)))
    citations = _select_citations(text, retrieval)
    return text, citations
