"""Tool registry loaded from `data/api_specs/internal_tools.json`.

The registry is a singleton built at startup. It is immutable after build.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

from ..config import TOOLS_SPEC


@dataclass(frozen=True)
class ToolSpec:
    name: str
    description: str
    destructive: bool
    requires_identity_verification: bool
    min_risk_to_propose: str   # low|medium|high|critical
    max_risk_to_execute: str
    idempotency_key_fields: tuple[str, ...]
    parameters_schema: dict[str, Any]


class ToolRegistry:
    def __init__(self, tools: dict[str, ToolSpec]):
        self._tools = tools

    def get(self, name: str) -> ToolSpec | None:
        return self._tools.get(name)

    def __contains__(self, name: str) -> bool:
        return name in self._tools

    def names(self) -> list[str]:
        return sorted(self._tools.keys())

    def all(self) -> list[ToolSpec]:
        return [self._tools[n] for n in self.names()]


def _load_spec_file() -> dict[str, Any]:
    if not TOOLS_SPEC.exists():
        # Empty registry — pipeline still works; only `escalate_to_human` is
        # synthesised as a safe default.
        return {"tools": []}
    return json.loads(TOOLS_SPEC.read_text(encoding="utf-8"))


def _build_registry() -> ToolRegistry:
    raw = _load_spec_file()
    tools: dict[str, ToolSpec] = {}
    for t in raw.get("tools", []):
        name = t["name"]
        tools[name] = ToolSpec(
            name=name,
            description=t.get("description", ""),
            destructive=bool(t.get("destructive", False)),
            requires_identity_verification=bool(t.get("requires_identity_verification", False)),
            min_risk_to_propose=t.get("min_risk_to_propose", "low"),
            max_risk_to_execute=t.get("max_risk_to_execute", "critical"),
            idempotency_key_fields=tuple(t.get("idempotency_key_fields", [])),
            parameters_schema=t.get("parameters_schema", {"type": "object"}),
        )
    # Ensure we always have escalate_to_human, since the safety layer relies
    # on it as a universal fallback.
    if "escalate_to_human" not in tools:
        tools["escalate_to_human"] = ToolSpec(
            name="escalate_to_human",
            description="Escalate to a human agent (fallback)",
            destructive=False,
            requires_identity_verification=False,
            min_risk_to_propose="low",
            max_risk_to_execute="critical",
            idempotency_key_fields=("ticket_id",),
            parameters_schema={
                "type": "object",
                "required": ["queue", "priority", "reason"],
                "properties": {
                    "queue": {"type": "string"},
                    "priority": {"type": "string"},
                    "reason": {"type": "string"},
                },
            },
        )
    return ToolRegistry(tools)


_singleton: ToolRegistry | None = None


def get_registry() -> ToolRegistry:
    global _singleton
    if _singleton is None:
        _singleton = _build_registry()
    return _singleton
