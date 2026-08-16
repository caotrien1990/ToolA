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
import orchestrator_server


# ---------------------------------------------------------------------------
# B3 — dashboard order must cover exactly the active council roster
# ---------------------------------------------------------------------------

def test_dashboard_order_covers_exactly_the_active_roster():
    # The core guard: the two sets must be equal. Missing agent (pic dropped
    # from dashboard) OR stale extra agent both fail here.
    assert set(orchestrator_server._COUNCIL_DASHBOARD_AGENT_ORDER) == set(
        orchestrator_server._COUNCIL_AGENTS
    )


def test_dashboard_order_has_no_duplicates():
    order = orchestrator_server._COUNCIL_DASHBOARD_AGENT_ORDER
    assert len(order) == len(set(order))


def test_dashboard_order_matches_roster_order_exactly():
    # Set equality alone does not pin ORDER, and order is the whole point of a
    # constant named ..._ORDER: a sabotage that swapped clau<->ope left both
    # guards above green. The source of truth is roster_order in
    # .council/council_config.json, which _council_active_from_config() reads
    # into _COUNCIL_AGENTS -- so comparing against that tuple keeps the two in
    # step without hard-coding the roster a second time here.
    assert list(orchestrator_server._COUNCIL_DASHBOARD_AGENT_ORDER) == list(
        orchestrator_server._COUNCIL_AGENTS
    )


def test_pic_renders_a_dashboard_card():
    # _council_dashboard_agents() is what the dashboard HTML iterates. pic is
    # enabled in council_config.json, so it must appear.
    assert "pic" in orchestrator_server._council_dashboard_agents()


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
