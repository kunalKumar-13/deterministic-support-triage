"""LLM decision engine.

This subsystem owns the single, strictly-scoped LLM call per ticket. The LLM
sees a hardened system prompt and structured ticket/retrieval data; it
returns a JSON object validated against `LLMDecision`. Anything outside
the schema is rejected and the pipeline falls back to a deterministic
heuristic classifier.

If no API key is configured, the entire subsystem cleanly falls back to the
heuristic path and forces escalation when it's not confident.
"""
from .engine import DecisionEngine, get_engine

__all__ = ["DecisionEngine", "get_engine"]
