"""Single tool-registration pipeline + drift validation (ADR R3 §D1.3/§D2 — S4).

Historically tools were registered by mutating ``code_tools.TOOL_MAP`` /
``TOOL_SCHEMAS`` / ``TOOL_PHASES`` (and the server's ``_ARG_ALIASES``) at
scattered sites — the second source of truth caused the ``council_message``
x_phase drift that broke contract tests. From S4 on, EVERY late registration
(anything outside the literal dicts in code_tools) must go through
:func:`register_tool`, and :func:`validate_registry` is the import-time/test
gate that proves map↔schema↔phase stay in lockstep.
"""

from __future__ import annotations

from typing import Callable

from orchestrator.core.fields_param import stamp_fields_parameter


class RegistryDriftError(RuntimeError):
    """A tool's map/schema/phase/alias entries are inconsistent."""


def register_tool(
    *,
    name: str,
    handler: Callable,
    schema: dict,
    phase: str,
    tool_map: dict,
    tool_schemas: list,
    tool_phases: dict,
    aliases: dict | None = None,
    arg_aliases: dict | None = None,
    phase_labels: dict | None = None,
    requires_token: bool = False,
    requires_session: bool | None = None,
    mutation_scope: str | None = None,
    token_required_set: set | None = None,
    mutating_set: set | None = None,
    mutation_scope_map: dict | None = None,
) -> None:
    """Idempotently register one tool into the live registry structures.

    ``schema`` is the inner ``function`` dict (name/description/parameters);
    the OpenAI-style envelope and ``x_phase``/``x_phase_label`` stamps are
    applied here so no caller can register a phase-less schema again.

    CAN-015: ``requires_token``/``requires_session``/``mutation_scope`` are accepted
    and stamped onto the schema so a late-registered tool can declare enforcement
    metadata. CAN-014 (D7 hot-reload): when the live enforcement structures are
    passed (``token_required_set``/``mutating_set``/``mutation_scope_map``) they are
    updated in lockstep so a tool registered AFTER import is gated immediately —
    no restart required.
    """
    if not name or not callable(handler):
        raise RegistryDriftError(f"invalid registration for {name!r}")
    if schema.get("name") not in (None, name):
        raise RegistryDriftError(f"schema name {schema.get('name')!r} != tool name {name!r}")
    if not phase:
        raise RegistryDriftError(f"tool {name!r} registered without a phase")

    existing = tool_map.get(name)
    if existing is not None and existing is not handler:
        raise RegistryDriftError(f"tool {name!r} already registered with a different handler")

    fn = dict(schema)
    fn["name"] = name
    fn["x_phase"] = phase
    if phase_labels:
        fn.setdefault("x_phase_label", phase_labels.get(phase, ""))
    # CASE-1809-E: declare the R101 ``fields=`` projection. The dispatcher accepts it on every
    # tool (it is popped out of the body before the UNSUPPORTED_FIELD check), yet it was declared
    # by only 2 schemas -- and both of those mean a DOM-state list, not a projection. Stamped
    # HERE as well as in the code_tools bulk loop for the same reason x_phase is stamped twice:
    # a late registration never passes through that loop. Never overwrites a tool's own
    # ``fields`` property.
    stamp_fields_parameter(fn)
    # CAN-015: stamp enforcement metadata into the schema (surfaces in /api/tools).
    if requires_token:
        fn["requires_token"] = True
    if requires_session is not None:
        fn["requires_session"] = bool(requires_session)
    if mutation_scope:
        fn["mutation_scope"] = mutation_scope

    tool_map[name] = handler
    tool_phases[name] = phase
    # CAN-014 (D7 hot-reload): keep the live enforcement sets in lockstep so a tool
    # registered after import is gated without a restart.
    if mutation_scope and mutation_scope_map is not None:
        mutation_scope_map[name] = mutation_scope
    if requires_token and token_required_set is not None:
        token_required_set.add(name)
    if mutating_set is not None and (requires_token or mutation_scope or requires_session):
        mutating_set.add(name)
    replaced = False
    for item in tool_schemas:
        if isinstance(item, dict) and (item.get("function") or {}).get("name") == name:
            item["function"] = fn
            replaced = True
            break
    if not replaced:
        tool_schemas.append({"type": "function", "function": fn})
    if aliases and arg_aliases is not None:
        arg_aliases.setdefault(name, {}).update(aliases)


def validate_registry(tool_map: dict, tool_schemas: list, tool_phases: dict) -> list[str]:
    """Return human-readable drift findings (empty list == healthy registry)."""
    issues: list[str] = []
    schema_by_name: dict[str, dict] = {}
    for item in tool_schemas:
        fn = (item.get("function") or {}) if isinstance(item, dict) else {}
        n = fn.get("name")
        if not n:
            issues.append("schema entry without function.name")
            continue
        if n in schema_by_name:
            issues.append(f"duplicate schema for {n}")
        schema_by_name[n] = fn

    for n in tool_map:
        if n not in tool_phases:
            issues.append(f"{n}: in TOOL_MAP but missing TOOL_PHASES")
        fn = schema_by_name.get(n)
        if fn is None:
            issues.append(f"{n}: in TOOL_MAP but missing TOOL_SCHEMAS")
            continue
        if fn.get("x_phase") != tool_phases.get(n):
            issues.append(f"{n}: x_phase {fn.get('x_phase')!r} != TOOL_PHASES {tool_phases.get(n)!r}")
    return issues
