"""Centralised configuration: paths, seeds, thresholds.

Every tunable lives here so threshold sweeps don't touch pipeline plumbing.
All values are conservative — when in doubt, escalate.
"""

from __future__ import annotations

import os
import random
from dataclasses import dataclass
from pathlib import Path

import numpy as np

# ---------------------------------------------------------------------------
# Determinism
# ---------------------------------------------------------------------------

SEED = 13
random.seed(SEED)
np.random.seed(SEED)
os.environ.setdefault("PYTHONHASHSEED", str(SEED))


# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------

# code/triage/config.py  ->  repo root is two parents up from this file
REPO_ROOT = Path(__file__).resolve().parents[2]
CODE_DIR = REPO_ROOT / "code"
DATA_DIR = REPO_ROOT / "data"
TICKETS_DIR = REPO_ROOT / "support_tickets"
CACHE_DIR = CODE_DIR / ".cache"
CORPUS_DIRS = (
    DATA_DIR / "devplatform",
    DATA_DIR / "claude",
    DATA_DIR / "visa",
)
TOOLS_SPEC = DATA_DIR / "api_specs" / "internal_tools.json"

INPUT_TICKETS_CSV = TICKETS_DIR / "support_tickets.csv"
SAMPLE_TICKETS_CSV = TICKETS_DIR / "sample_support_tickets.csv"
OUTPUT_CSV = TICKETS_DIR / "output.csv"

CACHE_DIR.mkdir(parents=True, exist_ok=True)


# ---------------------------------------------------------------------------
# Output CSV schema (frozen — order is contractual)
# ---------------------------------------------------------------------------

OUTPUT_COLUMNS = (
    "ticket_id",
    "status",
    "product_area",
    "response",
    "justification",
    "request_type",
    "confidence_score",
    "source_documents",
    "risk_level",
    "pii_detected",
    "language",
    "actions_taken",
)


# ---------------------------------------------------------------------------
# Retrieval
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class RetrievalConfig:
    chunk_size: int = 800
    chunk_overlap: int = 120
    bm25_k1: float = 1.5
    bm25_b: float = 0.75
    tfidf_max_df: float = 0.95
    tfidf_min_df: int = 1
    tfidf_ngram_max: int = 2
    candidate_pool: int = 80           # per ticket, before rerank
    top_k: int = 6                     # after rerank
    weak_match_threshold: float = 0.30 # below this -> "no grounding"
    hedged_match_threshold: float = 0.50
    citation_cosine_threshold: float = 0.25
    max_citations: int = 4
    weight_tfidf: float = 0.55
    weight_bm25: float = 0.35
    weight_title: float = 0.10


RETRIEVAL = RetrievalConfig()


# ---------------------------------------------------------------------------
# Safety thresholds
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class SafetyConfig:
    injection_critical: float = 0.85
    injection_high: float = 0.70
    injection_medium: float = 0.40
    subject_body_conflict_cosine: float = 0.30
    max_input_chars_per_turn: int = 8_000
    max_input_chars_per_ticket: int = 32_000


SAFETY = SafetyConfig()


# ---------------------------------------------------------------------------
# Confidence calibration weights (must sum to 1.0)
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class ConfidenceWeights:
    retrieval: float = 0.30
    agreement: float = 0.15
    risk: float = 0.15
    injection: float = 0.15
    llm: float = 0.15
    scope: float = 0.10


CONFIDENCE = ConfidenceWeights()
assert abs(
    CONFIDENCE.retrieval
    + CONFIDENCE.agreement
    + CONFIDENCE.risk
    + CONFIDENCE.injection
    + CONFIDENCE.llm
    + CONFIDENCE.scope
    - 1.0
) < 1e-6


# ---------------------------------------------------------------------------
# Confidence caps
# ---------------------------------------------------------------------------

ESCALATED_MAX_CONFIDENCE = 0.60
WEAK_RETRIEVAL_CAP = 0.45
HEDGED_RETRIEVAL_CAP = 0.65
NO_GROUNDING_CONFIDENCE = 0.20


# ---------------------------------------------------------------------------
# LLM config
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class LLMConfig:
    provider: str = os.environ.get("TRIAGE_LLM_PROVIDER", "auto")  # auto|anthropic|openai|off
    anthropic_model: str = os.environ.get(
        "TRIAGE_ANTHROPIC_MODEL", "claude-haiku-4-5-20251001"
    )
    openai_model: str = os.environ.get("TRIAGE_OPENAI_MODEL", "gpt-4o-mini")
    temperature: float = 0.0
    max_tokens: int = 800
    timeout_s: float = 25.0
    seed: int = SEED


LLM = LLMConfig()


# ---------------------------------------------------------------------------
# Domains
# ---------------------------------------------------------------------------

DOMAINS = ("devplatform", "claude", "visa")
DOMAIN_BRAND_TERMS = {
    "devplatform": (
        "devplatform", "dev platform", "workspace", "assessment", "test invite",
        "candidate", "interviewer", "rubric", "code playback", "proctoring",
    ),
    "claude": (
        "claude", "anthropic", "claude.ai", "console", "api key", "claude pro",
        "claude max", "claude code", "context window",
    ),
    "visa": (
        "visa", "card", "cardholder", "chargeback", "merchant", "atm",
        "pin", "issuer", "bank", "dispute", "credit card", "debit card",
    ),
}

# Allowed enum values from the problem statement
ALLOWED_STATUS = ("replied", "escalated")
ALLOWED_REQUEST_TYPE = ("product_issue", "feature_request", "bug", "invalid")
ALLOWED_RISK = ("low", "medium", "high", "critical")
