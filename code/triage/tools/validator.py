"""Schema + prerequisite + idempotency validator for tool calls.

We avoid hard-deps on `jsonschema` — we ship a minimal validator that covers
the constructs we use in `internal_tools.json` (type, required, enum,
min/max, minLength/maxLength, additionalProperties, items, uniqueItems,
exclusiveMinimum). It is deterministic and dependency-light.
"""

from __future__ import annotations

import hashlib
from typing import Any, Iterable

from ..models import ProposedAction
from .registry import ToolRegistry, ToolSpec, get_registry


_RISK_ORDER = {"low": 0, "medium": 1, "high": 2, "critical": 3}


# ---------------------------------------------------------------------------
# Tiny JSON-schema validator (subset)
# ---------------------------------------------------------------------------

def _type_ok(value: Any, declared: str) -> bool:
    if declared == "string":
        return isinstance(value, str)
    if declared == "integer":
        return isinstance(value, int) and not isinstance(value, bool)
    if declared == "number":
        return isinstance(value, (int, float)) and not isinstance(value, bool)
    if declared == "boolean":
        return isinstance(value, bool)
    if declared == "array":
        return isinstance(value, list)
    if declared == "object":
        return isinstance(value, dict)
    if declared == "null":
        return value is None
    return True


def _validate_subset(instance: Any, schema: dict[str, Any], path: str = "") -> list[str]:
    errs: list[str] = []
    if not schema:
        return errs
    t = schema.get("type")
    if t and not _type_ok(instance, t):
        errs.append(f"{path or '<root>'}: expected type {t}, got {type(instance).__name__}")
        return errs
    if t == "object":
        if not isinstance(instance, dict):
            return errs
        required = schema.get("required") or []
        for r in required:
            if r not in instance:
                errs.append(f"{path}.{r}: required")
        props = schema.get("properties") or {}
        additional = schema.get("additionalProperties", True)
        for k, v in instance.items():
            if k in props:
                errs.extend(_validate_subset(v, props[k], f"{path}.{k}"))
            else:
                if additional is False:
                    errs.append(f"{path}.{k}: additional property not allowed")
    elif t == "array":
        if not isinstance(instance, list):
            return errs
        items_schema = schema.get("items")
        if items_schema:
            for i, v in enumerate(instance):
                errs.extend(_validate_subset(v, items_schema, f"{path}[{i}]"))
        if schema.get("uniqueItems"):
            seen: list[Any] = []
            for v in instance:
                if v in seen:
                    errs.append(f"{path}: items must be unique")
                    break
                seen.append(v)
        max_items = schema.get("maxItems")
        if max_items is not None and len(instance) > max_items:
            errs.append(f"{path}: maxItems {max_items}")
    elif t == "string":
        if not isinstance(instance, str):
            return errs
        if "enum" in schema and instance not in schema["enum"]:
            errs.append(f"{path}: not in enum")
        if "minLength" in schema and len(instance) < schema["minLength"]:
            errs.append(f"{path}: minLength {schema['minLength']}")
        if "maxLength" in schema and len(instance) > schema["maxLength"]:
            errs.append(f"{path}: maxLength {schema['maxLength']}")
    elif t in ("integer", "number"):
        if not isinstance(instance, (int, float)) or isinstance(instance, bool):
            return errs
        if "minimum" in schema and instance < schema["minimum"]:
            errs.append(f"{path}: minimum {schema['minimum']}")
        if "maximum" in schema and instance > schema["maximum"]:
            errs.append(f"{path}: maximum {schema['maximum']}")
        if "exclusiveMinimum" in schema and instance <= schema["exclusiveMinimum"]:
            errs.append(f"{path}: must be > {schema['exclusiveMinimum']}")
    return errs


# ---------------------------------------------------------------------------
# Action validator
# ---------------------------------------------------------------------------

def validate_action(
    action: ProposedAction,
    *,
    risk_level: str,
    identity_verified: bool,
    registry: ToolRegistry | None = None,
) -> tuple[bool, list[str]]:
    reg = registry or get_registry()
    spec = reg.get(action.action)
    if spec is None:
        return False, [f"unknown_tool:{action.action}"]

    errs: list[str] = []

    # Schema validation
    errs.extend(_validate_subset(action.parameters, spec.parameters_schema, path="params"))

    # Risk gate: propose
    if _RISK_ORDER[risk_level] < _RISK_ORDER[spec.min_risk_to_propose]:
        errs.append(
            f"risk_below_propose_threshold:{risk_level}<{spec.min_risk_to_propose}"
        )

    # Risk gate: execute
    if _RISK_ORDER[risk_level] > _RISK_ORDER[spec.max_risk_to_execute]:
        # Destructive actions are dropped under critical risk unless this is
        # itself a safety action (escalate_to_human, create_internal_note).
        if spec.destructive:
            errs.append(
                f"destructive_above_execute_threshold:{risk_level}>{spec.max_risk_to_execute}"
            )

    # Identity prerequisite
    if spec.requires_identity_verification and not identity_verified:
        errs.append("identity_verification_required")

    return (len(errs) == 0), errs


def _idem_key(action: ProposedAction, spec: ToolSpec) -> str:
    fields = spec.idempotency_key_fields or ()
    parts = [action.action]
    for f in fields:
        v = action.parameters.get(f)
        parts.append(f"{f}={v}")
    return hashlib.sha256("|".join(parts).encode()).hexdigest()[:16]


def validate_actions(
    actions: Iterable[ProposedAction],
    *,
    risk_level: str,
) -> tuple[list[ProposedAction], list[str]]:
    """Validate a list of actions with proper prerequisite + idempotency chain.

    Returns (kept, dropped_with_reasons).
    """
    reg = get_registry()
    kept: list[ProposedAction] = []
    dropped: list[str] = []
    seen_idem: set[str] = set()
    # Identity verification is satisfied iff a `verify_identity` precedes the
    # destructive action OR an identity_verified marker exists in the convo
    # (we assume false here; the policy validator may set this externally).
    verified = False

    for act in actions:
        spec = reg.get(act.action)
        if spec is None:
            dropped.append(f"{act.action}:unknown_tool")
            continue
        ok, reasons = validate_action(
            act, risk_level=risk_level, identity_verified=verified
        )
        if not ok:
            dropped.append(f"{act.action}:" + ",".join(reasons))
            continue
        # Idempotency
        key = _idem_key(act, spec)
        if key in seen_idem:
            dropped.append(f"{act.action}:duplicate_idempotency")
            continue
        seen_idem.add(key)

        # If this action is verify_identity, future destructive actions are now ok.
        if act.action == "verify_identity":
            verified = True

        kept.append(act)

    return kept, dropped
