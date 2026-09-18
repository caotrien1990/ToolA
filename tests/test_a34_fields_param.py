"""CASE-1809-E: the R101 ``fields=`` projection must be DISCOVERABLE, not just accepted.

Measured before this change: ``fields=`` works on every registered tool -- the dispatcher pops it
out of the body before the UNSUPPORTED_FIELD check -- yet exactly 2 schemas declared a ``fields``
property, and both of those mean a DOM-state list, not a projection. So the one parameter the rule
file tells every CA to put on every call was discoverable from ``GET /api/tools/<tool>/schema`` on
zero tools. That is B019: registry properties are the public contract; a field absent from them is
a field no CA can find.

This file guards the fix at the level that matters -- the LIVE registry, after every hook has run
and after every late ``register_tool`` -- rather than the two source sites. Every count is derived
from ``code_tools.TOOL_SCHEMAS``; the only hand-written constant is the measured collision set, and
a test exists whose whole job is to fail if a third collision ever appears.
"""

from __future__ import annotations

import pytest

import code_tools as ct
from orchestrator.core.fields_param import (
    FIELDS_PARAM_NAME,
    R101_MARKER,
    fields_declarations,
    r101_fields_property,
    stamp_all_fields_parameters,
    stamp_fields_parameter,
)
from orchestrator.core.registry import register_tool

# Measured 2026-09-18 against the live registry: the only two tools that declared their own
# ``fields`` before this change. Both take a DOM-state property list
# (disabled|visible|bg|color|className|value_length), NOT an R101 projection. They are named here
# so that a THIRD collision fails this file instead of being silently overwritten -- the same
# reason the A31 self-chain allowlists name their subjects.
_MEASURED_FOREIGN = {"checkpoint_loop_dom_state", "click_and_checkpoint_loop"}


def _census() -> dict:
    return fields_declarations(ct.TOOL_SCHEMAS)


def _props(fn: dict) -> dict:
    return ((fn.get("parameters") or {}).get("properties") or {})


def _schemas_by_name() -> dict:
    out = {}
    for schema in ct.TOOL_SCHEMAS:
        fn = (schema or {}).get("function") or {}
        if fn.get("name"):
            out[fn["name"]] = fn
    return out


# ---------------------------------------------------------------- the live registry claim

def test_every_tool_that_takes_parameters_declares_fields():
    """Claim 1, the whole point: no tool with a parameter block is missing the projection.

    The expected exception set is DERIVED, not listed: a schema whose ``properties`` dict is
    missing or empty is deliberately left alone (creating one would delete the B6
    ``schema_ref`` hint that _generated_payload_example returns for propertyless tools).
    """
    census = _census()
    schemas = _schemas_by_name()
    missing = sorted(
        name for name, state in census.items()
        if state == "absent" and _props(schemas[name])
    )
    assert missing == [], (
        "%d tool(s) accept parameters but do not declare the R101 fields= projection: %s"
        % (len(missing), missing)
    )


def test_the_only_undeclared_schemas_are_the_propertyless_ones():
    """The skip set is honest: everything skipped really has no properties to declare into."""
    census = _census()
    schemas = _schemas_by_name()
    absent = {name for name, state in census.items() if state == "absent"}
    for name in sorted(absent):
        assert not _props(schemas[name]), (
            "%s has properties yet was skipped -- the skip rule no longer matches reality" % name
        )


def test_the_scan_is_not_vacuous():
    """Positive control. A census that silently returned {} would pass every test above."""
    census = _census()
    counts = {
        state: sum(1 for v in census.values() if v == state)
        for state in ("r101", "foreign", "absent")
    }
    print("R101 fields= census: %s of %d tools" % (counts, len(census)))
    assert len(census) > 100, "only %d tools in the registry: the import did not finish" % len(
        census)
    assert counts["r101"] > 100, "only %d R101 declarations: a hook did not run" % counts["r101"]


def test_the_bulk_hook_in_code_tools_actually_ran():
    """Positive control for hook #1 specifically: the literal dicts + the 4 concatenations.

    ``_R101_FIELDS_STAMPED`` is 0 both when the import failed (fail-open) and when the loop was
    deleted, so a real number here is the only proof the bulk site is still wired.
    """
    stamped = getattr(ct, "_R101_FIELDS_STAMPED", None)
    assert isinstance(stamped, int), "code_tools._R101_FIELDS_STAMPED missing: hook #1 is gone"
    assert stamped > 100, "hook #1 stamped only %r schemas" % stamped


# ---------------------------------------------------------------- never overwrite

def test_foreign_declarations_are_exactly_the_measured_collisions():
    """A third tool declaring its own ``fields`` must fail here, not be silently absorbed."""
    foreign = {name for name, state in _census().items() if state == "foreign"}
    assert foreign == _MEASURED_FOREIGN, (
        "the set of tools declaring their own fields= changed: %s (measured: %s). Re-measure "
        "before touching the stamp -- an unexpected member may be a real projection collision."
        % (sorted(foreign), sorted(_MEASURED_FOREIGN))
    )


@pytest.mark.parametrize("name", sorted(_MEASURED_FOREIGN))
def test_a_collision_keeps_its_own_description_untouched(name):
    """The DOM-state meaning survives: no marker, and not our text.

    NOTE this pins the never-overwrite rule, not those two tools' wording -- their description is
    only required to differ from the projection one.
    """
    schemas = _schemas_by_name()
    if name not in schemas:
        pytest.skip("%s is not registered in this build" % name)
    spec = _props(schemas[name]).get(FIELDS_PARAM_NAME)
    assert isinstance(spec, dict), "%s no longer declares fields at all" % name
    assert R101_MARKER not in spec, "%s was overwritten by the R101 stamp" % name
    assert spec.get("description") != r101_fields_property()["description"]


# ---------------------------------------------------------------- the payload_example trap

def test_no_declaration_carries_a_default():
    """The measured trap that decided this property's shape.

    ``orchestrator_server._generated_payload_example`` puts every property that carries a
    ``default`` into the example (``name == "project_path" or "default" in spec``). A default here
    would silently add ``"fields"`` to ~256 payload examples, every corrected_payload and every
    error hint -- the "large MCP" outcome this whole change exists to avoid.
    """
    schemas = _schemas_by_name()
    offenders = sorted(
        name for name, fn in schemas.items()
        if (_props(fn).get(FIELDS_PARAM_NAME) or {}).get(R101_MARKER) is True
        and "default" in _props(fn)[FIELDS_PARAM_NAME]
    )
    assert offenders == [], "a default on fields= leaks into every payload_example: %s" % offenders
    assert "default" not in r101_fields_property()


def test_the_declaration_stays_small():
    """GET /api/tools dumps every schema, so this string is paid ~250 times over."""
    prop = r101_fields_property()
    assert len(prop["description"]) < 260, len(prop["description"])
    assert set(prop) == {"type", "description", R101_MARKER}


# ---------------------------------------------------------------- hook #2, behaviourally

def _register(schema: dict, name: str = "_probe_tool"):
    """Register into throwaway structures: proves register_tool's behaviour, touches nothing live."""
    tool_map, tool_schemas, tool_phases = {}, [], {}
    register_tool(
        name=name,
        handler=lambda **_kw: None,
        schema=schema,
        phase="T1",
        tool_map=tool_map,
        tool_schemas=tool_schemas,
        tool_phases=tool_phases,
    )
    return tool_schemas[0]["function"]


def test_a_late_registration_gets_the_declaration():
    """Hook #2. code_tools registers 6 more tools BELOW its own bulk loop, and
    orchestrator_server registers git/cron/memory/write_status through here -- none of them pass
    through hook #1, which is why x_phase already had to be stamped in both places.
    """
    fn = _register({
        "description": "probe",
        "parameters": {"type": "object", "properties": {"project_path": {"type": "string"}},
                       "required": []},
    })
    spec = _props(fn).get(FIELDS_PARAM_NAME)
    assert isinstance(spec, dict) and spec.get(R101_MARKER) is True


def test_a_late_registration_keeps_its_own_fields():
    own = {"type": "string", "description": "DOM state properties to read"}
    fn = _register({
        "description": "probe",
        "parameters": {"type": "object", "properties": {"fields": dict(own)}, "required": []},
    })
    assert _props(fn)[FIELDS_PARAM_NAME] == own


def test_a_propertyless_late_registration_gains_no_properties_dict():
    """B6: _generated_payload_example answers a propertyless schema with a schema_ref hint. An
    injected ``fields`` would replace that hint with a bare {} -- worse than saying nothing."""
    fn = _register({
        "description": "probe",
        "parameters": {"type": "object", "properties": {}, "required": []},
    })
    assert _props(fn) == {}


# ---------------------------------------------------------------- the leaf itself

def test_stamp_is_idempotent():
    fn = {"parameters": {"type": "object", "properties": {"a": {"type": "string"}}}}
    assert stamp_fields_parameter(fn) is True
    first = dict(_props(fn)[FIELDS_PARAM_NAME])
    assert stamp_fields_parameter(fn) is False
    assert _props(fn)[FIELDS_PARAM_NAME] == first


def test_each_stamp_gets_its_own_dict():
    """A shared property dict would let one tool's edit rewrite every other tool's schema."""
    a = {"parameters": {"type": "object", "properties": {"x": {"type": "string"}}}}
    b = {"parameters": {"type": "object", "properties": {"x": {"type": "string"}}}}
    stamp_fields_parameter(a)
    stamp_fields_parameter(b)
    assert _props(a)[FIELDS_PARAM_NAME] is not _props(b)[FIELDS_PARAM_NAME]


@pytest.mark.parametrize("junk", [
    None,
    "a string",
    {},
    {"parameters": None},
    {"parameters": "nope"},
    {"parameters": {}},
    {"parameters": {"properties": None}},
    {"parameters": {"properties": "nope"}},
    {"parameters": {"properties": {}}},
])
def test_stamp_never_raises_and_declares_nothing_on_a_shape_it_was_not_measured_against(junk):
    """This runs at import time: a malformed row must not stop the daemon from booting."""
    assert stamp_fields_parameter(junk) is False


@pytest.mark.parametrize("junk", [None, [], (), [None], ["x"], [{}], [{"function": None}]])
def test_stamp_all_never_raises_on_a_malformed_registry(junk):
    assert stamp_all_fields_parameters(junk) == 0


def test_census_labels_come_from_the_marker_not_from_a_name_list():
    """The audit path itself: 'foreign' must be decided by the absent marker, so that a hand list
    of exceptions can never drift away from what the schemas really say."""
    schemas = [
        {"function": {"name": "mine", "parameters": {"type": "object",
                                                     "properties": {"a": {"type": "string"}}}}},
        {"function": {"name": "theirs", "parameters": {
            "type": "object", "properties": {"fields": {"type": "string"}}}}},
        {"function": {"name": "bare", "parameters": {"type": "object", "properties": {}}}},
    ]
    stamp_all_fields_parameters(schemas)
    assert fields_declarations(schemas) == {
        "mine": "r101", "theirs": "foreign", "bare": "absent"}
