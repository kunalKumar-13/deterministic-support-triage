"""Safety engine: prompt injection detection, PII detection, risk classification.

This is the first defensive layer. It runs BEFORE retrieval and BEFORE any
LLM call. The safety layer never calls the LLM, so it cannot be subverted
by prompt injection.
"""
from .engine import assess  # re-export
from .injection import InjectionDetector
from .pii import PIIDetector, redact_pii
from .risk import classify_risk

__all__ = ["assess", "InjectionDetector", "PIIDetector", "redact_pii", "classify_risk"]
