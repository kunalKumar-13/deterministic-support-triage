"""Tool registry + execution validator.

The LLM proposes actions; the registry validates them; the policy validator
decides which proposals actually survive into the output. The agent NEVER
executes external side effects — `actions_taken` is purely a record of
*what the agent would call*, validated against `internal_tools.json`.
"""
from .registry import ToolRegistry, get_registry
from .validator import validate_action, validate_actions

__all__ = ["ToolRegistry", "get_registry", "validate_action", "validate_actions"]
