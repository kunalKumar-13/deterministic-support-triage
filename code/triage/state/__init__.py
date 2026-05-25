"""Ticket state machine.

The states (NEW, NEEDS_VERIFICATION, VERIFIED, ACTIONABLE, ESCALATED,
RESOLVED) are an explicit progression that the pipeline tracks even though
each ticket is processed in one pass. The state at exit is included in the
internal trace and is the source of truth for `status` in the output row:

  ESCALATED -> status="escalated"
  RESOLVED  -> status="replied"
  others    -> status="escalated" (defensive default)
"""
from .machine import State, StateMachine

__all__ = ["State", "StateMachine"]
