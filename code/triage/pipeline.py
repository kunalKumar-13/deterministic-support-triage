"""Deterministic pipeline orchestrator.

This is the only public entry point used by `main.py`. It loads the corpus
once, then processes tickets one at a time via `process_ticket`.

A failure on a single ticket never raises out of this module; the row is
emitted as an escalation with a diagnostic justification.
"""

from __future__ import annotations

import json
import time
import traceback
from typing import Optional

from .config import (
    ALLOWED_REQUEST_TYPE,
    ALLOWED_RISK,
    ALLOWED_STATUS,
    DOMAINS,
    SAFETY,
)
from .confidence import score_confidence
from .decision import get_engine
from .logging_setup import log
from .models import (
    ConversationTurn,
    FinalOutput,
    LLMDecision,
    PipelineTrace,
    PolicyDecision,
    ProposedAction,
    RetrievalResult,
    RoutingDecision,
    SafetyAssessment,
    Ticket,
)
from .conversation import analyze as analyze_consistency
from .policy import get_validator
from .response import generate_response
from .retrieval import get_retriever
from .retrieval.consensus import analyze as analyze_consensus
from .retrieval.diagnostics import record as _record_retrieval
from .safety import assess
from .scope import classify_scope


# ---------------------------------------------------------------------------
# Input parsing
# ---------------------------------------------------------------------------

def _parse_issue(issue_field: str) -> list[ConversationTurn]:
    """`issue` is a JSON-encoded array (per the spec). Be liberal in what we
    accept — strings that aren't JSON are treated as a single user turn."""
    if not issue_field:
        return []
    raw = issue_field.strip()
    # Try JSON
    try:
        parsed = json.loads(raw)
    except Exception:
        return [ConversationTurn(role="user", content=raw)]

    if isinstance(parsed, list):
        out = []
        for item in parsed:
            if isinstance(item, dict):
                role = item.get("role", "user")
                if role not in {"user", "assistant", "system"}:
                    role = "user"
                content = item.get("content", "")
                if not isinstance(content, str):
                    content = json.dumps(content, ensure_ascii=False)
                out.append(ConversationTurn(role=role, content=content))  # type: ignore[arg-type]
            elif isinstance(item, str):
                out.append(ConversationTurn(role="user", content=item))
        if out:
            return out
        return [ConversationTurn(role="user", content="")]
    if isinstance(parsed, str):
        return [ConversationTurn(role="user", content=parsed)]
    # Dict at top level — treat as a single user turn with stringified content.
    return [ConversationTurn(role="user", content=json.dumps(parsed, ensure_ascii=False))]


def _last_user_text(turns: list[ConversationTurn]) -> str:
    for t in reversed(turns):
        if t.role == "user" and t.content.strip():
            return t.content
    # Fallback: any non-empty turn.
    for t in reversed(turns):
        if t.content.strip():
            return t.content
    return ""


def _truncate(s: str, n: int) -> str:
    if len(s) <= n:
        return s
    return s[:n]


def build_ticket(
    *, ticket_id: str, issue: str, subject: str, company: Optional[str]
) -> Ticket:
    turns = _parse_issue(issue)
    # Truncate per-turn and per-ticket
    total = 0
    norm_turns: list[ConversationTurn] = []
    for t in turns:
        c = _truncate(t.content, SAFETY.max_input_chars_per_turn)
        if total + len(c) > SAFETY.max_input_chars_per_ticket:
            c = c[: max(0, SAFETY.max_input_chars_per_ticket - total)]
        total += len(c)
        norm_turns.append(ConversationTurn(role=t.role, content=c))
        if total >= SAFETY.max_input_chars_per_ticket:
            break
    last = _last_user_text(norm_turns)
    return Ticket(
        ticket_id=ticket_id,
        issue_raw=issue,
        subject=_truncate(subject or "", SAFETY.max_input_chars_per_turn),
        company=(company or None) if (company and company.strip().lower() != "none") else None,
        turns=norm_turns,
        last_user_text=last,
        normalised_text=last,
    )


# ---------------------------------------------------------------------------
# Routing
# ---------------------------------------------------------------------------

def _structured_escalation_reasons(
    *,
    safety: SafetyAssessment,
    retrieval: RetrievalResult,
    scope,
    consistency,
    consensus,
    insufficient: bool,
    policy: PolicyDecision,
) -> list[str]:
    """Collect a deduplicated, ordered list of machine-readable tags
    explaining why the policy made its decision.

    The list is meaningful even on a 'replied' decision (it shows what
    signals were considered but did not trigger escalation).
    """
    tags: list[str] = []
    if insufficient:
        tags.append("insufficient_signal")
    if safety.injection_score >= 0.85:
        tags.append("prompt_injection_critical")
    elif safety.injection_score >= 0.70:
        tags.append("prompt_injection_high")
    elif safety.injection_score >= 0.40:
        tags.append("prompt_injection_medium")
    if safety.pii_detected:
        pii_kinds = sorted({h.kind for h in safety.pii_hits})
        for k in pii_kinds[:4]:
            tags.append(f"pii_{k}")
    for rr in safety.risk_reasons:
        # surface only the meaningful tags (pattern_*:tag, pii_*, injection_*)
        if rr.startswith("pattern_") or rr.startswith("pii_") or rr.startswith("injection_"):
            tags.append(f"risk_{rr.replace(':', '_')}")
    if retrieval.no_grounding:
        tags.append("no_grounding")
    elif retrieval.weak_match:
        tags.append("weak_retrieval")
    if consensus is not None:
        for t in getattr(consensus, "tags", ()) or ():
            tags.append(f"retrieval_{t}")
        if getattr(consensus, "single_source_only", False):
            tags.append("retrieval_single_source")
    if consistency is not None:
        for t in getattr(consistency, "tags", ()) or ():
            tags.append(f"conversation_{t}")
    if scope is not None:
        if getattr(scope, "suspicious_out_of_scope", False):
            tags.append("scope_suspicious")
        elif getattr(scope, "harmless_out_of_scope", False):
            tags.append("scope_harmless")
        elif not getattr(scope, "in_scope", True):
            tags.append("scope_ambiguous")
    if policy.dropped_actions:
        for d in policy.dropped_actions[:4]:
            tags.append(f"dropped_{d.split(':',1)[0]}")
    # Dedup while preserving order.
    seen: set[str] = set()
    out: list[str] = []
    for t in tags:
        if t in seen:
            continue
        seen.add(t)
        out.append(t)
    return out[:16]  # cap


def _route(ticket: Ticket) -> RoutingDecision:
    retriever = get_retriever()
    body = ticket.last_user_text + " " + " ".join(t.content for t in ticket.turns if t.role == "user")
    domain, trusted = retriever.infer_domain(body, ticket.company)
    multi_brand_count = sum(
        1
        for d in DOMAINS
        if any(term in body.lower() for term in (d, "claude" if d == "claude" else d))
    )
    return RoutingDecision(
        inferred_domain=domain,
        company_field_trusted=bool(trusted),
        multi_product_detected=multi_brand_count > 1,
    )


# ---------------------------------------------------------------------------
# Single-ticket processing
# ---------------------------------------------------------------------------

def process_ticket(
    *, ticket_id: str, issue: str, subject: str, company: Optional[str]
) -> FinalOutput:
    trace = PipelineTrace(ticket_id=ticket_id)

    # 1. Build ticket
    t0 = time.perf_counter()
    ticket = build_ticket(ticket_id=ticket_id, issue=issue, subject=subject, company=company)
    trace.stage_times_ms["build_ticket"] = (time.perf_counter() - t0) * 1000

    # 2. Safety (covers last user turn + subject + full conversation body so
    # injection patterns embedded in earlier turns still fire).
    t0 = time.perf_counter()
    full_conversation = "\n".join(
        t.content for t in ticket.turns if t.content
    )
    composite_text = (
        (full_conversation or ticket.last_user_text or "")
        + "\nSUBJECT: " + (ticket.subject or "")
    )
    safety_scope_in = True  # provisional; updated after scope check
    safety = assess(composite_text, is_in_scope=safety_scope_in)
    trace.stage_times_ms["safety"] = (time.perf_counter() - t0) * 1000

    # 2b. Scope classification (Phase E)
    t0 = time.perf_counter()
    scope = classify_scope(
        (ticket.last_user_text or "") + "\n" + (ticket.subject or "")
    )
    trace.stage_times_ms["scope"] = (time.perf_counter() - t0) * 1000

    # 2c. Multi-turn consistency (Phase F)
    t0 = time.perf_counter()
    consistency = analyze_consistency(ticket.turns)
    trace.stage_times_ms["consistency"] = (time.perf_counter() - t0) * 1000

    # 3. Routing
    t0 = time.perf_counter()
    routing = _route(ticket)
    trace.stage_times_ms["routing"] = (time.perf_counter() - t0) * 1000

    # 4. Retrieval (always on the redacted text)
    t0 = time.perf_counter()
    retriever = get_retriever()
    retrieval = retriever.query(
        safety.redacted_text or ticket.last_user_text,
        domain_hint=routing.inferred_domain,
    )
    trace.stage_times_ms["retrieval"] = (time.perf_counter() - t0) * 1000

    # 4b. Retrieval consensus (Phase C)
    t0 = time.perf_counter()
    consensus = analyze_consensus(retrieval.chunks)
    trace.stage_times_ms["consensus"] = (time.perf_counter() - t0) * 1000

    # 4c. Retrieval diagnostics (observability only)
    try:
        _record_retrieval(
            ticket_id=ticket_id,
            query=safety.redacted_text or ticket.last_user_text,
            retrieval=retrieval,
            consensus=consensus,
        )
    except Exception:
        pass  # diagnostics never crash the pipeline

    # 5. LLM decision (or heuristic fallback)
    t0 = time.perf_counter()
    try:
        llm = get_engine().decide(ticket, safety, retrieval)
    except Exception as e:
        trace.add(f"llm_exception:{type(e).__name__}")
        llm = LLMDecision(
            request_type="invalid",
            product_area="general",
            answer_draft="",
            proposed_actions=[],
            llm_confidence=0.2,
            reasoning_note="exception_fallback",
            used_fallback=True,
        )
    trace.stage_times_ms["decision"] = (time.perf_counter() - t0) * 1000

    # 6. Policy validator
    t0 = time.perf_counter()
    # Insufficient-signal detection: after stripping URLs and emoji,
    # how much meaningful "letter" content remains?
    import re as _re
    raw = ticket.last_user_text or ""
    stripped = _re.sub(r"https?://\S+", "", raw)
    # Letters in scripts that carry support intent.
    letters = _re.sub(
        r"[^A-Za-zÀ-ɏͰ-ϿЀ-ӿ֐-׿؀-ۿऀ-ॿ一-鿿]",
        "",
        stripped,
    )
    insufficient = len(letters) < 6
    # Also: very few real words AND no inferred domain.
    if not insufficient and not routing.inferred_domain:
        word_count = len(_re.findall(r"[A-Za-z]{3,}", raw))
        if word_count < 4 and len(raw.strip()) < 40:
            insufficient = True

    # If the ticket is truly empty (no user text at all), suppress the
    # scope signal so the validator's insufficient_signal rule wins instead
    # of the harmless-OOS rule.
    effective_scope = None if not raw.strip() else scope

    policy = get_validator().decide(
        safety=safety,
        retrieval=retrieval,
        llm=llm,
        scope=effective_scope,
        consistency=consistency,
        consensus=consensus,
        insufficient_signal=insufficient,
    )
    # Derive structured escalation_reasons (used for explainability/auditing).
    policy.escalation_reasons = _structured_escalation_reasons(
        safety=safety, retrieval=retrieval, scope=effective_scope,
        consistency=consistency, consensus=consensus,
        insufficient=insufficient, policy=policy,
    )
    trace.stage_times_ms["policy"] = (time.perf_counter() - t0) * 1000

    # 7. Response + citations
    t0 = time.perf_counter()
    response, citations = generate_response(
        safety=safety, retrieval=retrieval, llm=llm, policy=policy
    )
    trace.stage_times_ms["response"] = (time.perf_counter() - t0) * 1000

    # 8. Confidence
    t0 = time.perf_counter()
    confidence = score_confidence(
        safety=safety,
        retrieval=retrieval,
        llm=llm,
        policy=policy,
        scope=scope,
        consistency=consistency,
        consensus=consensus,
    )
    trace.stage_times_ms["confidence"] = (time.perf_counter() - t0) * 1000

    # 9. Compose final row
    actions = policy.validated_actions
    if policy.status == "escalated" and not any(a.action == "escalate_to_human" for a in actions):
        actions = list(actions) + [
            ProposedAction(
                action="escalate_to_human",
                parameters={
                    "queue": "tier1_general",
                    "priority": "normal",
                    "reason": policy.reason or "escalation",
                },
            )
        ]
    actions_json = json.dumps(
        [{"action": a.action, "parameters": a.parameters} for a in actions],
        ensure_ascii=False,
        sort_keys=True,
    )

    # Justification: concise + machine-friendly
    parts: list[str] = []
    parts.append(f"state={policy.state}")
    parts.append(f"reason={policy.reason}")
    parts.append(f"risk={safety.risk_level}")
    if safety.injection_score >= 0.4:
        parts.append(f"injection_score={safety.injection_score:.2f}")
    parts.append(f"retrieval_top1={retrieval.top1_score:.3f}")
    if retrieval.weak_match:
        parts.append("weak_match")
    if routing.inferred_domain:
        parts.append(f"domain={routing.inferred_domain}")
    if llm.used_fallback:
        parts.append("decision=heuristic_fallback")
    if policy.dropped_actions:
        parts.append("dropped=" + ",".join(policy.dropped_actions)[:160])
    if policy.escalation_reasons:
        parts.append("reasons=" + ",".join(policy.escalation_reasons[:8]))
    justification = " | ".join(parts)[:800]

    request_type = llm.request_type
    if request_type not in ALLOWED_REQUEST_TYPE:
        request_type = "invalid"
    # Scope override: polite acknowledgements + harmless OOS are always
    # `invalid`. Suspicious OOS already routes to escalation, so we mark
    # them invalid for consistency.
    if scope is not None and not scope.in_scope:
        request_type = "invalid"

    product_area = llm.product_area or "general"
    if policy.status == "escalated" and not product_area:
        product_area = "general"

    risk_level = safety.risk_level
    if risk_level not in ALLOWED_RISK:
        risk_level = "low"

    status = policy.status
    if status not in ALLOWED_STATUS:
        status = "escalated"

    return FinalOutput(
        ticket_id=ticket_id,
        status=status,  # type: ignore[arg-type]
        product_area=product_area,
        response=response,
        justification=justification,
        request_type=request_type,  # type: ignore[arg-type]
        confidence_score=confidence,
        source_documents="|".join(citations),
        risk_level=risk_level,  # type: ignore[arg-type]
        pii_detected=safety.pii_detected,
        language=safety.language,
        actions_taken=actions_json,
    )


# ---------------------------------------------------------------------------
# Safe wrapper: never raise
# ---------------------------------------------------------------------------

def process_ticket_safe(
    *, ticket_id: str, issue: str, subject: str, company: Optional[str]
) -> FinalOutput:
    try:
        return process_ticket(
            ticket_id=ticket_id, issue=issue, subject=subject, company=company
        )
    except Exception as e:
        # Defensive: emit an escalation row rather than crash the whole batch.
        try:
            tb = traceback.format_exc()
            log.error("ticket_pipeline_crash", ticket_id=ticket_id, error=str(e)[:200], tb=tb[:1000]) if hasattr(log, "error") else None
        except Exception:
            pass
        return FinalOutput(
            ticket_id=ticket_id,
            status="escalated",
            product_area="general",
            response=(
                "I ran into an internal issue handling this request and have "
                "escalated to a human teammate."
            ),
            justification=f"state=ESCALATED | reason=pipeline_exception | err={type(e).__name__}",
            request_type="invalid",
            confidence_score=0.1,
            source_documents="",
            risk_level="medium",
            pii_detected=False,
            language="en",
            actions_taken=json.dumps(
                [{
                    "action": "escalate_to_human",
                    "parameters": {
                        "queue": "tier1_general",
                        "priority": "normal",
                        "reason": "Internal processing exception",
                    },
                }],
                ensure_ascii=False,
                sort_keys=True,
            ),
        )
