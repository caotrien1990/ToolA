"""B027: a tool that OWNS a `fields` parameter must be exempt from the R101 pop.

The dispatcher pops `fields` out of the request body before the UNSUPPORTED_FIELD
check.  That is deliberate and load-bearing -- it is why the R101 projection works
on every tool while appearing in no handler signature.  The cost, measured
2026-09-18: two DOM-state tools (checkpoint_loop_dom_state and
click_and_checkpoint_loop) declare their own `fields`, a list of DOM-state names to
capture, and the pop meant the caller's value never reached the handler.  Anh's
call: exempt them from the pop.

The exemption is EARNED BY THE MARKER the R101 stamper writes, never by a name, so
these tests derive the expected set from the live schemas.  A hand-written pair of
names would keep passing after a rename while the bug came back.  Two controls: the
exemption must not be empty (or it is dead code and B027 is silently un-fixed), and
a stamped tool must never fall into it (or the projection stops working there).
"""
from __future__ import annotations

import code_tools as ct
import orchestrator_server as srv
from orchestrator.core.fields_param import FIELDS_PARAM_NAME, R101_MARKER


def _functions() -> list:
    return [s.get("function") or {} for s in ct.TOOL_SCHEMAS if isinstance(s, dict)]


def _props(fn: dict) -> dict:
    return (fn.get("parameters") or {}).get("properties") or {}


def _spec(fn: dict):
    return _props(fn).get(FIELDS_PARAM_NAME)


def _owners() -> set:
    """Tools whose `fields` property carries NO R101 marker -- measured, not listed."""
    return {
        fn.get("name")
        for fn in _functions()
        if isinstance(_spec(fn), dict) and _spec(fn).get(R101_MARKER) is not True
    }


def _stamped() -> set:
    return {
        fn.get("name")
        for fn in _functions()
        if isinstance(_spec(fn), dict) and _spec(fn).get(R101_MARKER) is True
    }


def test_the_exempt_set_is_exactly_the_tools_that_own_the_property():
    assert srv._r101_pop_exempt_tools() == frozenset(_owners())


def test_the_exemption_is_not_vacuous():
    """Control 1: an empty exemption means the pop is still unconditional."""
    exempt = srv._r101_pop_exempt_tools()
    assert exempt, (
        "no tool declares an unmarked `fields`, so the pop exemption is dead code "
        "and B027 is only nominally fixed")
    for name in sorted(exempt):
        spec = _spec(next(fn for fn in _functions() if fn.get("name") == name))
        assert isinstance(spec, dict), "%s is exempt without declaring fields" % name
        assert spec.get(R101_MARKER) is not True, (
            "%s is exempt while carrying the R101 marker: the projection would "
            "stop working on it" % name)


def test_a_stamped_tool_is_never_exempt():
    """Control 2: the exemption must not grow into the projection's own population."""
    stamped = _stamped()
    assert len(stamped) > 100, (
        "only %d tools carry the R101 stamp -- the measured population collapsed, "
        "so this control proves nothing" % len(stamped))
    assert not (stamped & srv._r101_pop_exempt_tools())


def test_the_cache_hands_back_an_immutable_set():
    """It is consulted on every dispatch; a caller must not be able to edit it."""
    first = srv._r101_pop_exempt_tools()
    assert isinstance(first, frozenset)
    assert srv._r101_pop_exempt_tools() is first, "the cache is being rebuilt"
