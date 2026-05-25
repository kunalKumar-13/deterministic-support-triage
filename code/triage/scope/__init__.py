"""Scope / out-of-scope detector.

Determines whether a ticket is actually a support request for one of the
three product ecosystems (DevPlatform / Claude / Visa). Out-of-scope
tickets split into:

  * harmless out-of-scope (chitchat, trivia, advice)  -> polite reply
  * suspicious out-of-scope (write me a scraper, ...) -> escalate

The detector is rule-based, deterministic, and additive — it produces a
`ScopeSignal` consumed by the policy validator and confidence
calibrator.
"""
from .scope import ScopeSignal, classify_scope, get_classifier

__all__ = ["ScopeSignal", "classify_scope", "get_classifier"]
