"""Policy validation layer.

This is the FINAL controller. The LLM proposes; the policy validator
disposes. Anything that violates explicit safety rules is blocked here.
"""
from .validator import PolicyValidator, get_validator

__all__ = ["PolicyValidator", "get_validator"]
