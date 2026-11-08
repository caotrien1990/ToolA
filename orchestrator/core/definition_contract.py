"""Content versions for tool definitions, independent of daemon lifetime.

Versions cover the registry schema and purpose, not volatile response metadata.
The transport parameter is excluded from its own digest.
"""
from __future__ import annotations

import copy
import hashlib
import json


VERSION_PARAMETER = {
    "type": "string",
    "minLength": 1,
    "description": (
        "Optional definition_version copied from this tool's served schema. "
        "A mismatch returns STALE_TOOL_DEFINITION with the current schema before "
        "the handler runs. Omit for legacy behavior; this is not authorization."
    ),
}


def definition_version(schema: dict, purpose: str = "") -> str:
    """Hash definition content deterministically without mutating the registry."""
    canonical = copy.deepcopy(schema)
    fn = canonical.get("function", canonical)
    fn.pop("definition_version", None)
    parameters = fn.get("parameters", {})
    parameters.get("properties", {}).pop("definition_version", None)
    required = parameters.get("required")
    if isinstance(required, list):
        parameters["required"] = [key for key in required if key != "definition_version"]
    encoded = json.dumps(
        {"schema": canonical, "purpose": purpose},
        sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False,
    ).encode("utf-8")
    return "sha256:" + hashlib.sha256(encoded).hexdigest()


def decorate_schema(served: dict, source: dict, purpose: str = "") -> dict:
    """Add the opt-in precondition to a served copy; callers keep legacy shapes."""
    result = copy.deepcopy(served)
    fn = result.setdefault("function", {})
    fn["definition_version"] = definition_version(source, purpose)
    properties = fn.setdefault("parameters", {}).setdefault("properties", {})
    properties["definition_version"] = copy.deepcopy(VERSION_PARAMETER)
    return result
