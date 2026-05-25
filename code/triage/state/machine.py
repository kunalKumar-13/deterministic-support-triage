"""Explicit, deterministic state machine for a single ticket pass.

A ticket starts in NEW and walks through a constrained set of transitions.
Invalid transitions raise; the pipeline catches them and falls back to
ESCALATED as a defensive default.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum


class State(str, Enum):
    NEW = "NEW"
    NEEDS_VERIFICATION = "NEEDS_VERIFICATION"
    VERIFIED = "VERIFIED"
    ACTIONABLE = "ACTIONABLE"
    ESCALATED = "ESCALATED"
    RESOLVED = "RESOLVED"


_ALLOWED: dict[State, set[State]] = {
    State.NEW: {
        State.NEEDS_VERIFICATION,
        State.ACTIONABLE,
        State.ESCALATED,
        State.RESOLVED,
    },
    State.NEEDS_VERIFICATION: {
        State.VERIFIED,
        State.ESCALATED,
        State.RESOLVED,
    },
    State.VERIFIED: {
        State.ACTIONABLE,
        State.ESCALATED,
        State.RESOLVED,
    },
    State.ACTIONABLE: {
        State.RESOLVED,
        State.ESCALATED,
    },
    State.ESCALATED: set(),
    State.RESOLVED: set(),
}


class InvalidTransition(Exception):
    pass


@dataclass
class StateMachine:
    state: State = State.NEW
    history: list[tuple[State, str]] = field(default_factory=list)

    def transition(self, to: State, reason: str = "") -> None:
        if to not in _ALLOWED[self.state]:
            raise InvalidTransition(
                f"invalid transition: {self.state.value} -> {to.value} ({reason})"
            )
        self.history.append((to, reason))
        self.state = to

    def escalate(self, reason: str) -> None:
        if self.state in {State.ESCALATED, State.RESOLVED}:
            return
        self.transition(State.ESCALATED, reason=reason)

    def resolve(self, reason: str) -> None:
        if self.state in {State.ESCALATED, State.RESOLVED}:
            return
        self.transition(State.RESOLVED, reason=reason)

    def is_terminal(self) -> bool:
        return self.state in {State.ESCALATED, State.RESOLVED}
