"""Declare the R101 ``fields=`` projection parameter on every tool schema.

WHY this module exists (A34 / CASE-1809-E, measured 2026-09-18 against the live daemon):
``fields=`` works on ALL 258 registered tools -- the dispatcher pops it out of the body at
``orchestrator_server`` BEFORE the UNSUPPORTED_FIELD check, exactly like ``page_id`` and
``pipeline`` -- yet only 2 tools declared a ``fields`` property, and those 2 mean something
else entirely (``checkpoint_loop_dom_state`` / ``click_and_checkpoint_loop`` take a DOM-state
property list: ``disabled|visible|bg|color|className|value_length``). So the one parameter a
CA is told to put on every call was discoverable on 0 of 258 tools.

That is B019 in its purest form -- "registry properties are the public contract; signatures
are internal". A field absent from the registry is a field no CA can find from
``GET /api/tools/<tool>/schema``; the only reason ``fields=`` gets used today is that the rule
file mentions it, which does not survive a CA that reads the schema instead.

DERIVED, NEVER HAND-TABLED: writing the property into 253 schemas by hand would rot the next
time a tool is added. One property definition is stamped at the two places a schema can enter
the live registry -- the same two places ``x_phase`` is stamped, for the same reason:

    code_tools bulk loop            the literal dicts + the four TOOL_SCHEMAS concatenations
    orchestrator.core.registry      register_tool(), every late registration (git, cron,
                                    memory_*, cbm_*, ocr_*, rule_drift_check, write_status)

Neither alone is enough: ``orchestrator_server`` never concatenates, it calls register_tool,
and ``code_tools`` itself calls register_tool AFTER its own bulk loop has already run.

Two measured constraints decide the property's shape, and both are load-bearing:

  NO ``default`` KEY. ``_generated_payload_example`` puts every property that carries a
  ``default`` into the example (``name == "project_path" or "default" in spec``), so a default
  here would silently add ``"fields": ""`` to ~256 payload examples, every corrected_payload
  and every error hint. Absence is also the honest default: omitting ``fields`` returns the
  full response.

  SKIP AN EMPTY ``properties`` DICT. The same generator answers a propertyless schema with
  ``{"schema_ref": "GET /api/tools/<tool>/schema"}`` (B6) precisely so an empty ``{}`` cannot
  be misread as "takes no arguments". Creating a properties dict just to hold ``fields`` would
  delete that hint, so those schemas are left alone and counted instead of claimed.

NEVER OVERWRITE: a tool that defines its own ``fields`` keeps it untouched -- the same rule the
passive chain and ``ca_next_tool`` follow, but here it is load-bearing rather than polite,
because replacing the two DOM-state descriptions with this one would be a lie about what those
tools accept. Those two collisions are a REAL open defect, not a cosmetic one: the dispatcher
pops ``fields`` unconditionally, so their own parameter never reaches the handler. Fixing that
needs a rename in the tool, not a schema stamp, so it stays visible here rather than laundered.

Placement follows the sibling leaf ``orchestrator/core/params.py``: stdlib-only (in fact
import-free), under ``orchestrator.core``, so ``code_tools`` and ``registry`` may import it at
module level without pulling anything in -- ``orchestrator/__init__.py`` is a docstring and
``orchestrator/core/__init__.py`` is empty.
"""

from __future__ import annotations

__all__ = [
    "FIELDS_PARAM_NAME",
    "R101_MARKER",
    "r101_fields_property",
    "stamp_fields_parameter",
    "stamp_all_fields_parameters",
    "fields_declarations",
    "UNKNOWN_KEY",
    "UNKNOWN_HINT_KEY",
    "LATE_STAMPED_KEY",
    "prune_unknown_fields",
]

FIELDS_PARAM_NAME = "fields"

# Same ``x_`` convention register_tool already uses for x_phase / x_phase_label: stamped
# metadata, not part of the JSON Schema vocabulary. It is what tells a declaration this module
# wrote apart from one the tool wrote itself -- the only way to audit the never-overwrite rule
# without a hand-maintained list of exceptions.
R101_MARKER = "x_r101_projection"

# Kept short on purpose: this string is repeated once per tool, and GET /api/tools dumps every
# schema at once. It is the one place that says how to CALL fields= (dotted keys included);
# the fields_unknown_hint the dispatcher returns says only what a miss means, and the full rule
# (fields_force_kept, the hash-domain markers that survive a projection) lives in
# examples/CORE_RULE.txt.
_DESCRIPTION = (
    "R101 projection, on every tool: comma list (or JSON array) of response keys to keep; a "
    "dotted key (result.files) keeps one nested branch. Omit for the full response. Keys "
    "this tool does not return come back as fields_unknown and drop nothing."
)


def r101_fields_property() -> dict:
    """A fresh property definition. New dict per call: schemas are mutated in place."""
    return {
        "type": "string",
        "description": _DESCRIPTION,
        R101_MARKER: True,
    }


def stamp_fields_parameter(fn: dict) -> bool:
    """Declare ``fields`` on ONE inner ``function`` schema, in place.

    Returns True only when this call added the property, so a caller can count what it did
    rather than assume. Idempotent, and a no-op on every shape that must not be touched:
    a non-dict, a schema with no ``parameters``/``properties`` block, an EMPTY properties dict
    (see the module docstring: that would delete the B6 schema_ref hint), and any tool that
    already declares ``fields`` itself.
    """
    if not isinstance(fn, dict):
        return False
    params = fn.get("parameters")
    if not isinstance(params, dict):
        return False
    props = params.get("properties")
    if not isinstance(props, dict) or not props:
        return False
    if FIELDS_PARAM_NAME in props:
        return False
    props[FIELDS_PARAM_NAME] = r101_fields_property()
    return True


def stamp_all_fields_parameters(tool_schemas) -> int:
    """Stamp every OpenAI-style envelope in ``tool_schemas``; return how many were added.

    Fail-open by shape: a malformed entry is skipped, never raised on. This runs at import
    time, and a schema list that is one bad row away from being useful must still boot.
    """
    added = 0
    for schema in tool_schemas or ():
        if not isinstance(schema, dict):
            continue
        if stamp_fields_parameter(schema.get("function")):
            added += 1
    return added


def fields_declarations(tool_schemas) -> dict:
    """Census of who declares ``fields`` and who wrote it: the auditable form of this module.

    ``{tool_name: "r101" | "foreign" | "absent"}``. ``foreign`` is a tool that declared its own
    ``fields`` before this module ran (the two DOM-state collisions); ``absent`` is a schema
    that was deliberately skipped. A guard can then assert the never-overwrite rule and the
    skip set from the live registry instead of from a list that rots.
    """
    out = {}
    for schema in tool_schemas or ():
        if not isinstance(schema, dict):
            continue
        fn = schema.get("function")
        if not isinstance(fn, dict):
            continue
        name = fn.get("name")
        if not name:
            continue
        spec = ((fn.get("parameters") or {}).get("properties") or {}).get(FIELDS_PARAM_NAME)
        if not isinstance(spec, dict):
            out[name] = "absent"
        elif spec.get(R101_MARKER) is True:
            out[name] = "r101"
        else:
            out[name] = "foreign"
    return out


UNKNOWN_KEY = "fields_unknown"
UNKNOWN_HINT_KEY = "fields_unknown_hint"
LATE_STAMPED_KEY = "fields_late_stamped"


def prune_unknown_fields(payload) -> list:
    """Drop from ``fields_unknown`` every key the caller DOES receive; return those names.

    WHY (A37 #4, measured 2026-09-19 on the live daemon): a key stamped AFTER the
    R101 projection is invisible to the filter that decided what the caller asked
    for that does not exist. ``generation`` was one, so ``fields=ok,generation`` came
    back carrying ``fields_unknown:["generation"]`` right next to the very key it
    said was not in the response -- a false "you typoed it" on a correct request,
    the same class of harm as a silent drop. Since 2026-09-24 the dispatcher's
    per-call envelope goes THROUGH the projection (orchestrator_server
    ``_r101_dispatch_result``), so what is still stamped late is only what a caller
    cannot know to ask for -- ``pushed_schema``, ``schema_rearmed``,
    ``cached_result_available`` -- plus, on the MCP side, ``generation_source`` /
    ``mcp_generation*``.

    DERIVED, NEVER HAND-TABLED: a frozenset of injected key names would rot the next
    time a late stamp is added, and the question the caller actually asks is "is
    this key in the response I am holding" -- so ask the response, not a table.

    Dotted specs are left alone: ``a.b`` names a key inside a nested dict, and the
    late stamp only ever lands at the top level.

    The prune is DISCLOSED as ``fields_late_stamped`` rather than done silently: a
    caller who really did mistype is still told, and one who did not learns that the
    key arrives after the projection. Idempotent, and a no-op on any other shape.
    """
    if not isinstance(payload, dict):
        return []
    names = payload.get(UNKNOWN_KEY)
    if not isinstance(names, list) or not names:
        return []
    late = sorted({n for n in names
                   if isinstance(n, str) and "." not in n and n in payload})
    if not late:
        return []
    remaining = [n for n in names if n not in late]
    if remaining:
        payload[UNKNOWN_KEY] = remaining
    else:
        payload.pop(UNKNOWN_KEY, None)
        payload.pop(UNKNOWN_HINT_KEY, None)
    payload[LATE_STAMPED_KEY] = late
    return late
