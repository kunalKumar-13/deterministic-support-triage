"""Pydantic domain models.

These models are the contract between pipeline stages. Each stage takes the
prior stage's output and returns a new immutable object. The output CSV row
is constructed from `FinalOutput`.
"""

from __future__ import annotations

from typing import Any, Literal, Optional

from pydantic import BaseModel, Field, field_validator


# ---------------------------------------------------------------------------
# Input
# ---------------------------------------------------------------------------

class ConversationTurn(BaseModel):
    role: Literal["user", "assistant", "system"]
    content: str = ""

    @field_validator("content", mode="before")
    @classmethod
    def _coerce_to_str(cls, v: Any) -> str:
        if v is None:
            return ""
        return str(v)


class Ticket(BaseModel):
    ticket_id: str
    issue_raw: str                 # raw JSON string, untrusted
    subject: str = ""              # untrusted
    company: Optional[str] = None  # untrusted hint
    turns: list[ConversationTurn] = Field(default_factory=list)
    last_user_text: str = ""       # extracted from turns
    normalised_text: str = ""      # NFKC, zero-width stripped


# ---------------------------------------------------------------------------
# Safety
# ---------------------------------------------------------------------------

class PIIHit(BaseModel):
    kind: Literal[
        "email", "phone", "ssn", "card", "iban", "address",
        "token", "account_id", "ip", "url_with_credentials",
    ]
    span: tuple[int, int]
    placeholder: str  # what we substitute in redacted text


class InjectionHit(BaseModel):
    pattern: str
    span: tuple[int, int]
    severity: float


class SafetyAssessment(BaseModel):
    injection_score: float = 0.0
    injection_hits: list[InjectionHit] = Field(default_factory=list)
    pii_detected: bool = False
    pii_hits: list[PIIHit] = Field(default_factory=list)
    language: str = "en"
    risk_level: Literal["low", "medium", "high", "critical"] = "low"
    risk_reasons: list[str] = Field(default_factory=list)
    redacted_text: str = ""
    is_in_scope: bool = True


# ---------------------------------------------------------------------------
# Routing
# ---------------------------------------------------------------------------

class RoutingDecision(BaseModel):
    inferred_domain: Optional[str] = None      # devplatform | claude | visa | None
    company_field_trusted: bool = False
    multi_product_detected: bool = False
    secondary_domain: Optional[str] = None
    subject_body_conflict: bool = False


# ---------------------------------------------------------------------------
# Retrieval
# ---------------------------------------------------------------------------

class ChunkRef(BaseModel):
    doc_path: str            # relative to repo root
    chunk_id: int
    title: str = ""
    domain: str = ""
    text: str = ""
    char_start: int = 0
    char_end: int = 0
    has_injection_marker: bool = False
    is_specific_doc: bool = False
    recency_score: float = 0.5


class RetrievalResult(BaseModel):
    chunks: list[ChunkRef] = Field(default_factory=list)
    scores: list[float] = Field(default_factory=list)
    top1_score: float = 0.0
    agreement: float = 0.0          # jaccard top1/top2
    weak_match: bool = False
    no_grounding: bool = False


# ---------------------------------------------------------------------------
# LLM decision
# ---------------------------------------------------------------------------

class ProposedAction(BaseModel):
    action: str
    parameters: dict[str, Any] = Field(default_factory=dict)


class LLMDecision(BaseModel):
    request_type: Literal["product_issue", "feature_request", "bug", "invalid"] = "invalid"
    product_area: str = "general"
    answer_draft: str = ""
    proposed_actions: list[ProposedAction] = Field(default_factory=list)
    llm_confidence: float = 0.0
    reasoning_note: str = ""
    used_fallback: bool = False


# ---------------------------------------------------------------------------
# Policy
# ---------------------------------------------------------------------------

class PolicyDecision(BaseModel):
    status: Literal["replied", "escalated"] = "escalated"
    reason: str = ""
    escalation_reasons: list[str] = Field(default_factory=list)
    dropped_actions: list[str] = Field(default_factory=list)
    validated_actions: list[ProposedAction] = Field(default_factory=list)
    confidence_cap: float = 1.0
    canned_response: Optional[str] = None  # forced response template if set
    state: str = "NEW"                     # state-machine label at exit


# ---------------------------------------------------------------------------
# Final output (row of output.csv)
# ---------------------------------------------------------------------------

class FinalOutput(BaseModel):
    ticket_id: str
    status: Literal["replied", "escalated"]
    product_area: str
    response: str
    justification: str
    request_type: Literal["product_issue", "feature_request", "bug", "invalid"]
    confidence_score: float = Field(ge=0.0, le=1.0)
    source_documents: str          # pipe-separated; "" if none
    risk_level: Literal["low", "medium", "high", "critical"]
    pii_detected: bool
    language: str
    actions_taken: str             # JSON string of list[ProposedAction]


# ---------------------------------------------------------------------------
# Per-ticket pipeline trace (for observability / debug)
# ---------------------------------------------------------------------------

class PipelineTrace(BaseModel):
    ticket_id: str
    stage_times_ms: dict[str, float] = Field(default_factory=dict)
    notes: list[str] = Field(default_factory=list)

    def add(self, msg: str) -> None:
        self.notes.append(msg)
