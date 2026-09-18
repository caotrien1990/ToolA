# pyright: reportOptionalMemberAccess=false
"""A-T4 named-drift regression tests (Add-only, do NOT edit source).

Two specific instances of schema<->implementation drift were flagged but never
locked by a named test:

  1) git_stage must agree about the X-Orchestrator-Token gate:
       - The published schema (code_tools.TOOL_SCHEMAS) advertises
         requires_token=True.
       - The mutation-token enforcement set (code_tools.TOKEN_REQUIRED_TOOLS)
         contains "git_stage".
     A future drift in either direction breaks CA's mutation-safety
     contract silently, so a dedicated test pins both sides.

  2) query_dom_candidates must not advertise schema fields that the
     implementation cannot accept.  The published schema
     (code_tools.TOOL_SCHEMAS) lists the accepted parameters; the tool
     function is tools_v15.query_dom_candidates.  A drift in either
     direction (impl drops a field the schema still claims, or schema
     publishes a new field the impl doesn't accept) silently breaks CA
     payloads.  Because the impl has no **kwargs, accepted fields MUST
     be a subset of the signature parameters.

These tests are additive.  They must PASS now and FAIL on any future
regression of those two specific instances.

CASE-1809-E qualifies the subset rule in (2), and only there: a parameter the
DISPATCHER pops out of the request body before dispatch is accepted by the
transport, so it can never appear in a handler signature.  R101 ``fields=`` is
the first such parameter to be published in the schema, so the subset check
subtracts exactly the properties carrying the R101 marker -- by marker, not by
name -- and a positive-control test keeps that subtraction from growing into a
blanket pass.

Run:
  ./venv312/Scripts/python.exe -m pytest tests/test_at4_named_drift.py -q
"""

import inspect

import code_tools
import orchestrator_server as srv  # noqa: F401 — triggers late registrations
import tools_v15
from orchestrator.core.fields_param import R101_MARKER

# Module-level reference so inspect.signature does not see a bound
# method (which happens when the attribute lives on a class).
_QUERY_DOM_CANDIDATES_FN = tools_v15.query_dom_candidates


# ── helpers ──────────────────────────────────────────────────────────────────

def _schema_function(name: str) -> dict | None:
    """Return the inner 'function' dict for *name* from code_tools.TOOL_SCHEMAS."""
    for item in code_tools.TOOL_SCHEMAS:
        if not isinstance(item, dict):
            continue
        fn = item.get("function") or {}
        if fn.get("name") == name:
            return fn
    return None


def _dispatcher_popped_props(fn: dict | None) -> set[str]:
    """Schema properties the DISPATCHER consumes, so no handler can declare them.

    Measured 2026-09-18: orchestrator_server pops ``fields`` out of the request
    body before the UNSUPPORTED_FIELD check, which is exactly why the R101
    projection is accepted on every tool while appearing in no tool's
    signature.  Membership is decided by the marker the stamper writes
    (orchestrator.core.fields_param), NOT by the name -- two DOM-state tools,
    checkpoint_loop_dom_state and click_and_checkpoint_loop, declare an
    unmarked ``fields`` that means a property list and IS a real parameter.
    """
    props = ((fn or {}).get("parameters") or {}).get("properties") or {}
    return {
        prop_name
        for prop_name, spec in props.items()
        if isinstance(spec, dict) and spec.get(R101_MARKER) is True
    }


# ── 1) git_stage token gate agreement ───────────────────────────────────────

class TestGitStageTokenGateAgreement:
    """Lock the schema-vs-set agreement for git_stage's requires_token.

    The published schema (TOOL_SCHEMAS) advertises requires_token=True.
    The token-required set (code_tools.TOKEN_REQUIRED_TOOLS) must list
    "git_stage".  Both sides are imported from the real code; if either
    drifts, this test fails.
    """

    SCHEMA_TOOL = "git_stage"
    ENFORCEMENT_SET = "code_tools.TOKEN_REQUIRED_TOOLS"

    def test_schema_advertises_requires_token_true(self):
        fn = _schema_function(self.SCHEMA_TOOL)
        assert fn is not None, (
            f"tool {self.SCHEMA_TOOL!r} missing from code_tools.TOOL_SCHEMAS"
        )
        # The published schema must declare requires_token=True.  A missing
        # flag (None) is treated as a drift because every destructive tool
        # in code_tools.TOKEN_REQUIRED_TOOLS is expected to advertise it.
        assert fn.get("requires_token") is True, (
            f"schema drift: {self.SCHEMA_TOOL}.requires_token "
            f"expected True, got {fn.get('requires_token')!r}"
        )

    def test_token_required_set_contains_git_stage(self):
        assert "git_stage" in code_tools.TOKEN_REQUIRED_TOOLS, (
            f"enforcement drift: code_tools.TOKEN_REQUIRED_TOOLS no longer "
            f"contains 'git_stage' (current contents: "
            f"{sorted(code_tools.TOKEN_REQUIRED_TOOLS)})"
        )

    def test_orchestrator_local_token_set_contains_git_stage(self):
        # orchestrator_server._TOKEN_REQUIRED_TOOLS is a snapshot kept in
        # lockstep with code_tools.TOKEN_REQUIRED_TOOLS; either side
        # drifting means CA can no longer rely on the X-Orchestrator-Token
        # gate being applied at request time.
        assert "git_stage" in srv._TOKEN_REQUIRED_TOOLS, (
            f"orchestrator_server._TOKEN_REQUIRED_TOOLS no longer contains "
            f"'git_stage' (current contents: "
            f"{sorted(srv._TOKEN_REQUIRED_TOOLS)})"
        )

    def test_schema_and_set_agree(self):
        """Final guard: requires_token (schema) and membership
        (TOKEN_REQUIRED_TOOLS) must agree."""
        fn = _schema_function(self.SCHEMA_TOOL)
        schema_flag = bool(fn.get("requires_token"))
        in_set = "git_stage" in code_tools.TOKEN_REQUIRED_TOOLS
        assert schema_flag == in_set, (
            f"drift between schema and enforcement set: "
            f"schema.requires_token={schema_flag} vs "
            f"in TOKEN_REQUIRED_TOOLS={in_set}"
        )


# ── 2) query_dom_candidates schema<->impl field agreement ───────────────────

class TestQueryDomCandidatesSchemaImplFieldAgreement:
    """Lock that every published accepted field for query_dom_candidates
    is a parameter of the tool function (tools_v15.query_dom_candidates).

    The function has no **kwargs, so the schema MUST stay a subset of
    the signature.  If a future edit adds a schema field without
    extending the signature, CA payloads will be silently dropped; this
    test fails first.
    """

    SCHEMA_TOOL = "query_dom_candidates"
    # Module-level reference.  Binding it as a CLASS attribute on a
    # class that happens to be named "query_dom_candidates..."-style
    # would turn self.IMPL_CALLABLE into a bound method; keeping the
    # lookup at module scope avoids that pitfall.
    IMPL_CALLABLE = tools_v15.query_dom_candidates  # noqa: F841 (kept for clarity)

    def _schema_props(self) -> set[str]:
        fn = _schema_function(self.SCHEMA_TOOL)
        assert fn is not None, (
            f"tool {self.SCHEMA_TOOL!r} missing from code_tools.TOOL_SCHEMAS"
        )
        return set((fn.get("parameters") or {}).get("properties") or {})

    def _sig_params(self) -> set[str]:
        # NOTE: do NOT route through self.IMPL_CALLABLE — when the
        # attribute is defined on a class, Python rebinds the lookup
        # into a bound method and `inspect.signature` then walks the
        # wrong object.  Resolve via the module to keep the plain
        # function.
        return set(inspect.signature(_QUERY_DOM_CANDIDATES_FN).parameters)

    def test_no_kwargs_in_signature(self):
        """If **kwargs ever appears, the subset rule no longer applies;
        a future maintainer must add a different check and update this
        test."""
        params = inspect.signature(_QUERY_DOM_CANDIDATES_FN).parameters
        assert not any(
            p.kind is inspect.Parameter.VAR_KEYWORD for p in params.values()
        ), (
            f"{_QUERY_DOM_CANDIDATES_FN.__name__} now takes **kwargs; this "
            f"subset check is no longer sufficient — replace with an "
            f"alias-resolves check."
        )

    def test_schema_props_are_subset_of_signature(self):
        schema_props = self._schema_props()
        sig_params = self._sig_params()
        # CASE-1809-E: subtract the parameters the DISPATCHER consumes.  R101
        # ``fields=`` is popped out of the request body in orchestrator_server
        # BEFORE the UNSUPPORTED_FIELD check, so every tool accepts it and NO
        # handler signature can ever contain it.  Publishing it is therefore
        # not drift -- withholding it was the bug (CA could not discover the
        # projection from the schema).  The exemption is earned by the stamped
        # marker, never by the name: two DOM-state tools own an UNMARKED
        # ``fields`` property that still has to match their signature.
        fn = _schema_function(self.SCHEMA_TOOL)
        dispatcher_popped = _dispatcher_popped_props(fn)
        extra_in_schema = schema_props - sig_params - dispatcher_popped
        assert not extra_in_schema, (
            f"schema<->impl drift: {self.SCHEMA_TOOL!r} schema publishes "
            f"fields the implementation cannot accept: "
            f"{sorted(extra_in_schema)}; signature params = "
            f"{sorted(sig_params)}; dispatcher-popped = "
            f"{sorted(dispatcher_popped)}"
        )

    def test_the_exemption_is_earned_by_the_marker_not_by_a_name(self):
        """Positive control for the subtraction in the test above.

        That subtraction can rot in two directions: it can stop matching
        anything (the R101 declaration would then fail the gate again), or it
        can grow until it swallows a genuinely undeclared property.  Both are
        checked here -- on the live schema, plus one fabricated stray.
        """
        fn = _schema_function(self.SCHEMA_TOOL)
        popped = _dispatcher_popped_props(fn)
        assert popped, (
            f"no dispatcher-popped property on {self.SCHEMA_TOOL!r}: either "
            f"the R101 ``fields`` declaration is gone or its marker was "
            f"renamed -- the subtraction above is now vacuous"
        )
        assert popped <= {"fields"}, (
            f"unexpected dispatcher-popped properties {sorted(popped)}: only "
            f"R101 ``fields`` is popped before dispatch, so anything else "
            f"listed here would be a real parameter hidden from this gate"
        )
        # A stray property carries no marker, so the gate still catches it.
        props = dict((fn.get("parameters") or {}).get("properties") or {})
        props["not_a_real_parameter"] = {"type": "string"}
        stray = {"parameters": {"properties": props}}
        assert _dispatcher_popped_props(stray) == popped
        leftover = set(props) - self._sig_params() - _dispatcher_popped_props(stray)
        assert leftover == {"not_a_real_parameter"}, (
            f"the exemption swallowed a stray property: leftover={sorted(leftover)}"
        )

    def test_required_fields_are_subset_of_signature(self):
        """Every field listed under 'required' must also be a real
        parameter; an unknown required field is an even more visible
        drift than a stray property."""
        fn = _schema_function(self.SCHEMA_TOOL)
        required = set((fn.get("parameters") or {}).get("required") or [])
        sig_params = self._sig_params()
        missing = required - sig_params
        assert not missing, (
            f"schema<->impl drift: {self.SCHEMA_TOOL!r} schema requires "
            f"fields the implementation does not accept: "
            f"{sorted(missing)}"
        )
