"""Campaign 1608 B3 + B6 regression guards.

B3: the council dashboard render order (_COUNCIL_DASHBOARD_AGENT_ORDER) is a
    hand-maintained duplicate of the active roster (_COUNCIL_AGENTS). It once
    omitted "pic", so that agent silently got no dashboard card. The set-equality
    guard below is the cheap check that would have caught the miss; adding an
    agent to the roster without adding it to the dashboard order (or vice versa)
    now fails loudly.

B6: _generated_payload_example returned {} for a tool whose schema has no
    properties. An empty dict reads like "this tool takes no arguments", which
    may be false (schema simply empty/unbuilt). It must instead point the caller
    at the live schema endpoint via a NON-payload-shaped key.
"""
import json
from pathlib import Path

import orchestrator_server


# ---------------------------------------------------------------------------
# B3 — dashboard order must cover exactly the active council roster
# ---------------------------------------------------------------------------

def _enabled_roster_from_config():
    """Enabled agents in roster_order, read straight from the config FILE.

    _COUNCIL_DASHBOARD_AGENT_ORDER is now DERIVED from _COUNCIL_AGENTS, which made the
    old tuple-vs-tuple asserts tautological. Re-deriving the roster from the JSON here
    keeps a real cross-source guard: it still fails loudly if
    _council_active_from_config() stops honouring roster_order or `enabled`.
    """
    path = (Path(orchestrator_server.__file__).resolve().parent
            / ".council" / "council_config.json")
    assert path.exists(), f"council config SOT not found at {path}"
    cfg = json.loads(path.read_text(encoding="utf-8"))
    agents = cfg["agents"]
    order = list(cfg.get("roster_order") or []) + list(agents)
    seen, out = set(), []
    for aid in order:
        meta = agents.get(aid)
        if meta and aid not in seen and meta.get("enabled", True):
            out.append(aid)
            seen.add(aid)
    return out


def test_dashboard_order_covers_exactly_the_active_roster():
    # The core guard: the two sets must be equal. A missing agent (pic dropped from the
    # dashboard) OR a stale extra agent both fail here.
    assert set(orchestrator_server._COUNCIL_DASHBOARD_AGENT_ORDER) == set(
        _enabled_roster_from_config()
    )


def test_dashboard_order_has_no_duplicates():
    order = orchestrator_server._COUNCIL_DASHBOARD_AGENT_ORDER
    assert len(order) == len(set(order))


def test_dashboard_order_matches_roster_order_exactly():
    # Set equality alone does not pin ORDER, and order is the whole point of a constant
    # named ..._ORDER: a sabotage that swapped clau<->ope leaves both guards above green.
    assert list(orchestrator_server._COUNCIL_DASHBOARD_AGENT_ORDER) == (
        _enabled_roster_from_config()
    )


def test_every_enabled_agent_renders_a_dashboard_card():
    # _council_dashboard_agents() is what the dashboard HTML iterates. B3 was `pic`
    # silently missing a card. The original guard asserted one hard-coded id ("pic"),
    # which guarded that one agent only and broke the day it was retired, so assert
    # the whole enabled set instead.
    assert list(orchestrator_server._council_dashboard_agents()) == (
        _enabled_roster_from_config()
    )


def test_a_disabled_agent_gets_no_dashboard_card():
    # The other half of B3: enabled:false must REMOVE the card, not just reorder it.
    # kilo and pic were retired this way on 2026-09-20, rows kept so they can be
    # switched back on.
    path = (Path(orchestrator_server.__file__).resolve().parent
            / ".council" / "council_config.json")
    cfg = json.loads(path.read_text(encoding="utf-8"))
    disabled = {a for a, m in cfg["agents"].items() if not m.get("enabled", True)}
    cards = set(orchestrator_server._council_dashboard_agents())
    assert not (disabled & cards), f"disabled agents still rendering: {sorted(disabled & cards)}"


# ---------------------------------------------------------------------------
# B6 — empty-props schema yields a schema_ref pointer, not a bare {}
# ---------------------------------------------------------------------------

def test_empty_props_real_tool_points_to_live_schema():
    # protected_tools_status is a REAL registered tool whose schema has no
    # properties, so it actually reaches the empty-props branch.
    example = orchestrator_server._payload_example_for_tool(
        "protected_tools_status",
        orchestrator_server._schema_for_tool("protected_tools_status"),
    )
    assert example == {
        "schema_ref": "GET /api/tools/protected_tools_status/schema"
    }


def test_empty_props_substitutes_the_real_tool_name():
    # The <tool> placeholder must be substituted, not emitted literally.
    example = orchestrator_server._payload_example_for_tool(
        "leader_bootstrap",
        orchestrator_server._schema_for_tool("leader_bootstrap"),
    )
    assert example["schema_ref"] == "GET /api/tools/leader_bootstrap/schema"
    assert "<tool>" not in example["schema_ref"]


def test_schema_ref_is_not_mistakable_for_a_payload():
    # The value is embedded in corrected_payload / payload_example that a CA may
    # retry with. It must NOT look like a literal request body: the only key is
    # schema_ref and its value is an HTTP verb+path, which cannot be a field set.
    example = orchestrator_server._payload_example_for_tool(
        "cancel_pending_restart",
        orchestrator_server._schema_for_tool("cancel_pending_restart"),
    )
    assert list(example.keys()) == ["schema_ref"]
    assert example["schema_ref"].startswith("GET /api/tools/")


def test_generated_payload_example_empty_direct_call():
    # Direct call on the lower-level function for an unknown tool (no schema)
    # also returns the pointer rather than {}.
    example = orchestrator_server._generated_payload_example(
        "b6_unknown_tool_xyz", {"parameters": {"properties": {}}}
    )
    assert example == {"schema_ref": "GET /api/tools/b6_unknown_tool_xyz/schema"}


def test_nonempty_props_unaffected():
    # Guard against over-reach: a tool WITH properties must still get a real
    # generated example, never a schema_ref.
    example = orchestrator_server._payload_example_for_tool(
        "search_code", orchestrator_server._schema_for_tool("search_code")
    )
    assert "schema_ref" not in example
    assert example  # non-empty
