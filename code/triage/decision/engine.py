"""Strict-JSON decision engine.

`DecisionEngine.decide(ticket, safety, retrieval)` returns an LLMDecision.
It will attempt one LLM call; on failure or schema mismatch, it falls back
to a deterministic heuristic classifier.

The heuristic classifier is conservative: it never proposes destructive
actions and never claims a confident answer. Its job is to keep the
pipeline running and route to escalation when in doubt.
"""

from __future__ import annotations

import json
import re
from typing import Any, Optional

from ..config import DOMAINS
from ..models import (
    LLMDecision,
    ProposedAction,
    RetrievalResult,
    SafetyAssessment,
    Ticket,
)
from .llm import LLMClient, LLMResult
from .prompts import SYSTEM_PROMPT, build_user_prompt


# ---------------------------------------------------------------------------
# JSON extraction
# ---------------------------------------------------------------------------

_JSON_OBJ_RE = re.compile(r"\{[\s\S]*\}\s*$")


def _extract_json(text: str) -> Optional[dict[str, Any]]:
    if not text:
        return None
    t = text.strip()
    # Strip code fences if present.
    if t.startswith("```"):
        t = t.strip("`")
        if t.lower().startswith("json"):
            t = t[4:]
        t = t.strip()
    # First, try the whole string.
    try:
        return json.loads(t)
    except Exception:
        pass
    # Fallback: take from the first '{' to the matching last '}'.
    start = t.find("{")
    end = t.rfind("}")
    if start == -1 or end == -1 or end < start:
        return None
    try:
        return json.loads(t[start : end + 1])
    except Exception:
        return None


# ---------------------------------------------------------------------------
# Schema validation -> LLMDecision
# ---------------------------------------------------------------------------

_ALLOWED_REQ = {"product_issue", "feature_request", "bug", "invalid"}


def _coerce_decision(obj: dict[str, Any]) -> Optional[LLMDecision]:
    if not isinstance(obj, dict):
        return None
    req = obj.get("request_type")
    if req not in _ALLOWED_REQ:
        req = "invalid"
    pa = obj.get("product_area", "general")
    if not isinstance(pa, str) or not pa.strip():
        pa = "general"
    pa = pa.strip().lower()[:60]
    ans = obj.get("answer_draft", "")
    if not isinstance(ans, str):
        ans = ""
    ans = ans[:1200]
    conf = obj.get("llm_confidence", 0.5)
    try:
        conf = float(conf)
    except Exception:
        conf = 0.5
    conf = max(0.0, min(1.0, conf))
    note = obj.get("reasoning_note", "")
    if not isinstance(note, str):
        note = ""
    note = note[:300]
    raw_actions = obj.get("proposed_actions", [])
    actions: list[ProposedAction] = []
    if isinstance(raw_actions, list):
        for a in raw_actions[:6]:
            if not isinstance(a, dict):
                continue
            name = a.get("action")
            if not isinstance(name, str):
                continue
            params = a.get("parameters", {})
            if not isinstance(params, dict):
                params = {}
            actions.append(ProposedAction(action=name, parameters=params))
    return LLMDecision(
        request_type=req,
        product_area=pa,
        answer_draft=ans,
        proposed_actions=actions,
        llm_confidence=conf,
        reasoning_note=note,
        used_fallback=False,
    )


# ---------------------------------------------------------------------------
# Heuristic fallback
# ---------------------------------------------------------------------------

def _heuristic_decision(
    ticket: Ticket, safety: SafetyAssessment, retrieval: RetrievalResult
) -> LLMDecision:
    text = (safety.redacted_text or ticket.last_user_text or "").lower()

    # Request type
    req = "invalid"
    if any(w in text for w in ("bug", "crash", "broken", "doesn't work", "won't load", "error 5", "stack trace")):
        req = "bug"
    elif any(w in text for w in ("feature request", "feature_request", "would be great if", "please add", "wish you", "could you add")):
        req = "feature_request"
    elif any(
        w in text
        for w in (
            "refund", "billing", "charge", "subscription", "login", "password",
            "access", "permission", "card", "dispute", "chargeback", "account",
            "api", "rate limit", "context window",
        )
    ):
        req = "product_issue"
    elif text.strip():
        req = "product_issue"

    # Product area = top retrieved doc domain + simple keyword.
    if retrieval.chunks:
        domain = retrieval.chunks[0].domain
        pa = f"{domain}_general"
        for kw, label in (
            ("refund", "billing"),
            ("billing", "billing"),
            ("charge", "billing"),
            ("password", "account_access"),
            ("login", "account_access"),
            ("permission", "account_access"),
            ("fraud", "fraud_dispute"),
            ("dispute", "fraud_dispute"),
            ("chargeback", "fraud_dispute"),
            ("bug", "bug_report"),
            ("api", "api_usage"),
            ("test", "assessment"),
            ("candidate", "assessment"),
        ):
            if kw in text:
                pa = f"{domain}_{label}"
                break
    else:
        pa = "general"

    # Draft — only when we have grounding and no injection. We do NOT
    # include a lead phrase here; the response generator selects a varied
    # lead from a deterministic pool based on the top doc.
    if retrieval.no_grounding or safety.injection_score >= 0.4:
        draft = ""
    else:
        draft = ""  # let the response generator format around the chunk

    # Conservative confidence
    conf = 0.5 if retrieval.chunks and not retrieval.weak_match else 0.25

    return LLMDecision(
        request_type=req,  # type: ignore[arg-type]
        product_area=pa,
        answer_draft=draft,
        proposed_actions=[],
        llm_confidence=conf,
        reasoning_note="heuristic_fallback",
        used_fallback=True,
    )


# ---------------------------------------------------------------------------
# Engine
# ---------------------------------------------------------------------------

class DecisionEngine:
    def __init__(self, client: Optional[LLMClient] = None) -> None:
        self.client = client or LLMClient()

    def decide(
        self,
        ticket: Ticket,
        safety: SafetyAssessment,
        retrieval: RetrievalResult,
    ) -> LLMDecision:
        # Hard gate: if injection is critical, skip the LLM entirely. We will
        # NOT pass adversarial text to the model. The pipeline will force
        # escalation regardless.
        if safety.injection_score >= 0.85:
            return LLMDecision(
                request_type="invalid",
                product_area="safety_review",
                answer_draft="",
                proposed_actions=[],
                llm_confidence=0.0,
                reasoning_note="critical_injection_bypassed_llm",
                used_fallback=True,
            )

        # No-grounding tickets get the heuristic path — there's nothing
        # useful for the LLM to ground in.
        if retrieval.no_grounding:
            return _heuristic_decision(ticket, safety, retrieval)

        prompt = build_user_prompt(ticket, safety, retrieval)
        res: Optional[LLMResult] = self.client.call(SYSTEM_PROMPT, prompt)
        if res is None:
            return _heuristic_decision(ticket, safety, retrieval)

        obj = _extract_json(res.text)
        if obj is None:
            return _heuristic_decision(ticket, safety, retrieval)

        decision = _coerce_decision(obj)
        if decision is None:
            return _heuristic_decision(ticket, safety, retrieval)
        return decision


_singleton: DecisionEngine | None = None


def get_engine() -> DecisionEngine:
    global _singleton
    if _singleton is None:
        _singleton = DecisionEngine()
    return _singleton
