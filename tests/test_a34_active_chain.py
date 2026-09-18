"""Guard for the ACTIVE half of the auto-chain (orchestrator/core/active_chain.py).

The passive half's guard earned its keep by catching a raising predicate in code that
looked finished, so this file is written the same way: every claim the leaf's docstring
makes is asserted here against a DERIVED source, never against a hand-copied list.

The four claims that matter, and what proves each:

  1. "every step is a READ"  -- the whole legality argument. examples/CORE_RULE.txt:82
     allows chain HINTS but forbids automatic EXECUTION, so an active chain is only
     defensible while it cannot mutate. Proven against orchestrator_server._MUTATING_TOOLS
     (derived at :359 from ct.PROTECTED_TOOL_NAMES | _TOKEN_REQUIRED_TOOLS), not against
     a list in this file -- so a target that is read-only today and gains a write
     tomorrow fails this test rather than shipping.
  2. "nothing here invents a target" -- every source and target must exist in the live
     TOOL_MAP, and every built payload's keys must exist in the TARGET'S OWN schema.
     That last one is deliberate: a builder that passes `symbol=` to a tool whose
     parameter is called something else would fail only in production, on a real call.
  3. "three outcomes, kept distinct" -- a builder that cannot run must be
     distinguishable from one that ran and found nothing. This is the passive half's
     exact bug, so the junk matrix from that file comes along.
  4. "the cap is never silent" -- over MAX_STEPS_PER_CALL, the dropped steps must be
     NAMED. A truncation nobody reports reads as complete coverage.

The fixtures are real payload shapes observed in this campaign, not invented ones. One
measured asymmetry drives its own test: a read_file_range envelope carries project_path,
but a search_code envelope does NOT -- so search rows can only resolve the project from
the source call's arguments.
"""

from __future__ import annotations

import ast
import asyncio
import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import code_tools as ct
import orchestrator_server as srv  # importing it is what lands the late git/cron registrations
from orchestrator.core.active_chain import (
    ACTIVE_CHAIN_STEPS,
    ACTIVE_CHAIN_TARGETS,
    ACTIVE_CHAINS,
    MAX_STEPS_PER_CALL,
    PIPELINE_RESPONSE_KEY,
    build_step_payload,
    execute_active_pipeline,
    pipeline_key_requested,
    resolve_active_pipeline,
)

_ROWS = list(ACTIVE_CHAINS)
_IDS = ["%s:%s" % (row.source, row.step) for row in _ROWS]

# Real envelope shapes, trimmed to the keys a builder reads. read_file_range answers with
# project_path; search_code does not -- that difference is asserted below, not smoothed over.
_SOURCE_RESULT = {
    "read_file_range": {
        "ok": True,
        "project_path": "D:\\proj",
        "file": "D:\\proj\\orchestrator\\graph\\tools.py",
        "selection": {"ok": True, "scope": "function", "name": "_ensure_graph_fresh",
                      "start_line": 95, "end_line": 127, "source": "python_ast"},
        "next_ranges": [
            {"file_path": "D:\\proj\\orchestrator\\graph\\tools.py", "scope": "function",
             "start_line": 180, "end_line": 190, "name": "_graph_write_lock",
             "kind": "function", "relevance_score": 3.02, "reason": "structural_neighbor"},
        ],
    },
    "search_code": {
        "ok": True,
        "results": [
            {"file": "orchestrator_server.py", "line": 359,
             "match": "_MUTATING_TOOLS = set(ct.PROTECTED_TOOL_NAMES) | _TOKEN_REQUIRED_TOOLS"},
        ],
        "count": 1,
        "mode": "regex",
    },
}

_SOURCE_ARGS = {
    "read_file_range": {"project_path": "D:\\proj",
                        "file_path": "orchestrator/graph/tools.py"},
    "search_code": {"project_path": "D:\\proj", "query": "_MUTATING_TOOLS"},
}

# Per row: a source payload that is well formed but carries NO input for that step.
# Every one must answer "no_input" -- a normal outcome, not an error.
_STARVED = {
    ("read_file_range", "next_range"): {"ok": True, "project_path": "D:\\proj",
                                        "next_ranges": []},
    ("read_file_range", "callers"): {"ok": True, "project_path": "D:\\proj",
                                     "selection": {"ok": True, "scope": "range",
                                                   "name": None, "source": "line_range"}},
    ("read_file_range", "file_symbols"): {"ok": True, "project_path": "D:\\proj"},
    ("search_code", "top_hit"): {"ok": True, "project_path": "D:\\proj", "results": []},
    ("search_code", "hit_symbols"): {"ok": True, "project_path": "D:\\proj", "results": []},
}


def _target_params(target: str) -> dict:
    """The parameters block the TARGET tool really declares, from the live schema.

    TOOL_SCHEMAS is a LIST of OpenAI-style envelopes -- {"type": "function", "function":
    {"name", "description", "parameters"}} -- NOT a dict keyed by tool name. The codebase
    reaches it the same way (code_tools._audit_tool_matrix_sync iterates and reads
    schema["function"]["name"]). It is also rebound several times as later schema batches are
    appended, so it must be read through the MODULE attribute here and never captured by
    `from code_tools import TOOL_SCHEMAS`, which would freeze an early copy.
    """
    for schema in getattr(ct, "TOOL_SCHEMAS", None) or ():
        if not isinstance(schema, dict):
            continue
        fn = schema.get("function") or {}
        if fn.get("name") == target:
            return fn.get("parameters") or {}
    return {}


def _target_properties(target: str) -> set:
    """The parameter NAMES the target accepts."""
    return set(_target_params(target).get("properties") or {})


def test_the_schema_accessor_can_actually_find_a_schema():
    """Positive control. Every conformance assertion below is of the form "the payload uses
    only / supplies all of what the schema declares". An accessor that quietly answered {}
    for every target would make all of them pass on nothing -- which is exactly how the
    first version of this file went green in review and red on the first real run. So prove
    the lookup resolves, and prove a name that does not exist answers empty.
    """
    params = _target_params("read_file_range")
    assert params.get("properties"), "the accessor found no parameters for read_file_range"
    assert "project_path" in _target_properties("read_file_range")
    assert _target_params("no_such_tool_exists_anywhere") == {}


# ─── 1. table integrity ───────────────────────────────────────────────────────────

def test_the_table_is_not_empty():
    """Positive control: every parametrized test below is vacuous if this table is empty."""
    assert _ROWS, "ACTIVE_CHAINS is empty -- every parametrized guard here would pass on nothing"


def test_source_and_step_pairs_are_distinct():
    keys = [(row.source, row.step) for row in _ROWS]
    assert len(keys) == len(set(keys)), "a duplicate (source, step) makes one row unreachable"


def test_step_map_is_derived_from_the_table_not_written_beside_it():
    derived: dict = {}
    for row in _ROWS:
        derived[row.source] = derived.get(row.source, ()) + (row.step,)
    assert ACTIVE_CHAIN_STEPS == derived


def test_target_set_is_derived_from_the_table():
    assert ACTIVE_CHAIN_TARGETS == frozenset(row.target for row in _ROWS)


@pytest.mark.parametrize("row", _ROWS, ids=_IDS)
def test_source_and_target_are_live_tools(row):
    assert row.source in ct.TOOL_MAP, "%s is not a live tool" % row.source
    assert row.target in ct.TOOL_MAP, "%s is not a live tool" % row.target


@pytest.mark.parametrize("row", _ROWS, ids=_IDS)
def test_why_is_one_readable_line(row):
    assert row.why and "\n" not in row.why
    assert len(row.why) <= 120, "a hint a CA has to scroll is not a hint"


# ─── 2. the legality guard: a step may never mutate ───────────────────────────────

@pytest.mark.parametrize("row", _ROWS, ids=_IDS)
def test_no_step_can_mutate(row):
    """The entire rule argument for this feature. See the module docstring, claim 1."""
    assert not srv._is_mutating_tool(row.target), (
        "%s:%s targets %s, which is mutating: an active chain may only READ, or it becomes "
        "the automatic execution CORE_RULE.txt:82 forbids" % (row.source, row.step, row.target))
    assert row.target not in srv._MUTATING_TOOLS


@pytest.mark.parametrize("row", _ROWS, ids=_IDS)
def test_no_step_declares_a_mutation_scope(row):
    scope = ct.tool_mutation_scope(row.target)
    assert scope in (None, "", "none"), (
        "%s declares mutation_scope=%r" % (row.target, scope))


def test_the_mutating_guard_can_actually_fail():
    """Positive control for the two tests above.

    If _MUTATING_TOOLS were empty or _is_mutating_tool always answered False, both guards
    would pass on a table full of writers. Pin a tool that must be in it.
    """
    assert srv._is_mutating_tool("write_fix") is True
    assert "write_fix" in srv._MUTATING_TOOLS


# ─── 3. a builder may not invent a parameter ──────────────────────────────────────

@pytest.mark.parametrize("row", _ROWS, ids=_IDS)
def test_built_payload_uses_only_parameters_the_target_declares(row):
    """Claim 2. A guessed parameter name would otherwise fail only on a live call."""
    payload, reason = build_step_payload(
        row, _SOURCE_RESULT[row.source], _SOURCE_ARGS[row.source])
    assert reason == "", "%s:%s found no input in a measured payload: %s" % (
        row.source, row.step, reason)
    accepted = _target_properties(row.target)
    assert accepted, "no schema properties found for %s" % row.target
    unknown = set(payload) - accepted
    assert not unknown, "%s:%s passes %s, which %s does not accept (accepts %s)" % (
        row.source, row.step, sorted(unknown), row.target, sorted(accepted))


@pytest.mark.parametrize("row", _ROWS, ids=_IDS)
def test_built_payload_supplies_every_required_parameter(row):
    payload, reason = build_step_payload(
        row, _SOURCE_RESULT[row.source], _SOURCE_ARGS[row.source])
    assert reason == ""
    required = set(_target_params(row.target).get("required") or ())
    missing = required - set(payload)
    assert not missing, "%s:%s omits required %s for %s" % (
        row.source, row.step, sorted(missing), row.target)


def test_a_search_row_resolves_the_project_from_the_call_arguments():
    """Measured asymmetry: a search_code envelope carries no project_path.

    read_file_range answers with the path it resolved; search_code does not. If a builder
    read the project only from the result, every search row would silently answer no_input
    -- the feature would look implemented and do nothing.

    HOW the project reaches the payload is derived from the target rather than assumed:
    read_file_range declares project_path, while get_file_symbols declares only file_path
    (measured against the live schema), so there the project can arrive only joined into an
    absolute path. Either way it came from the ARGUMENTS, because the result has none.
    """
    assert "project_path" not in _SOURCE_RESULT["search_code"], (
        "fixture drift: this test exists because the real envelope lacks project_path")
    hit_file = _SOURCE_RESULT["search_code"]["results"][0]["file"]
    assert not os.path.isabs(hit_file), (
        "fixture drift: search_code names a hit RELATIVE to the project, which is the whole "
        "reason the join exists")
    checked = 0
    for row in _ROWS:
        if row.source != "search_code":
            continue
        payload, reason = build_step_payload(
            row, _SOURCE_RESULT["search_code"], _SOURCE_ARGS["search_code"])
        assert reason == "", "%s:%s found no input: %s" % (row.source, row.step, reason)
        if "project_path" in _target_properties(row.target):
            assert payload.get("project_path") == "D:\\proj", (
                "%s dropped the project: %r" % (row.step, payload))
        else:
            assert payload.get("file_path") == os.path.join("D:\\proj", hit_file), (
                "%s must carry the project inside an absolute file_path: %r"
                % (row.step, payload))
        checked += 1
    assert checked, "no search_code row was exercised: the loop proved nothing"


# ─── 4. three distinct outcomes ───────────────────────────────────────────────────

@pytest.mark.parametrize("row", _ROWS, ids=_IDS)
def test_a_starved_payload_answers_no_input_not_an_error(row):
    starved = _STARVED[(row.source, row.step)]
    payload, reason = build_step_payload(row, starved, {})
    assert payload is None
    assert reason == "no_input", "%s:%s answered %r" % (row.source, row.step, reason)


@pytest.mark.parametrize("junk", [
    {},
    {"next_ranges": "a string"},
    {"next_ranges": [None]},
    {"next_ranges": [{"file_path": "x.py", "start_line": "three", "end_line": 9}]},
    {"next_ranges": [{"file_path": "x.py", "start_line": True, "end_line": True}]},
    {"selection": "a string"},
    {"selection": None},
    {"results": "a string"},
    {"results": [None]},
    {"results": [{"file": "x.py", "line": "three"}]},
    {"results": [{"file": "x.py", "line": True}]},
    {"project_path": None, "file": None},
])
@pytest.mark.parametrize("row", _ROWS, ids=_IDS)
def test_no_builder_raises_on_a_shape_it_was_not_measured_against(row, junk):
    """Claim 3, and the passive half's actual defect: int("three") raises, True is an int.

    A builder that raises must be REPORTED, never silently downgraded to "no input" --
    so a build_error reason is acceptable here while an exception is not.
    """
    try:
        payload, reason = build_step_payload(row, junk, {})
    except Exception as exc:  # the assertion IS that this cannot happen
        pytest.fail("%s:%s raised on %r: %r" % (row.source, row.step, junk, exc))
    if payload is None:
        assert reason in ("no_input",) or reason.startswith("build_error:")
    else:
        assert reason == ""


@pytest.mark.parametrize("row", _ROWS, ids=_IDS)
def test_a_non_dict_result_is_no_input(row):
    for bad in (None, "a string", 7, ["a", "list"]):
        payload, reason = build_step_payload(row, bad, {})
        assert payload is None and reason == "no_input"


def test_a_build_error_is_reported_and_not_laundered_into_no_input():
    """Positive control for the three-outcome claim.

    Without this, a build_step_payload that swallowed everything into "no_input" would
    pass every test above -- which is precisely how the passive predicate bug hid.
    """
    exploding = _ROWS[0]._replace(build=lambda result, args: 1 // 0)
    payload, reason = build_step_payload(exploding, {"ok": True}, {})
    assert payload is None
    assert reason == "build_error:ZeroDivisionError", reason


# ─── 5. resolve semantics ─────────────────────────────────────────────────────────

def test_an_unknown_step_is_rejected_not_raised():
    steps, rejected, dropped = resolve_active_pipeline("read_file_range", ["no_such_step"])
    assert steps == () and dropped == ()
    assert rejected == ("no_such_step",)


def test_a_good_step_survives_beside_a_typo():
    steps, rejected, _ = resolve_active_pipeline(
        "read_file_range", ["next_range", "next_rnage"])
    assert [row.step for row in steps] == ["next_range"]
    assert rejected == ("next_rnage",)


def test_a_step_is_scoped_to_its_own_source_tool():
    """`top_hit` is a search_code step; asking read_file_range for it is a rejection."""
    steps, rejected, _ = resolve_active_pipeline("read_file_range", ["top_hit"])
    assert steps == () and rejected == ("top_hit",)


def test_a_tool_with_no_rows_offers_nothing():
    steps, rejected, _ = resolve_active_pipeline("git_status", ["next_range"])
    assert steps == () and rejected == ("next_range",)


def test_duplicates_collapse_and_order_is_the_callers():
    steps, rejected, dropped = resolve_active_pipeline(
        "read_file_range", ["file_symbols", "next_range", "file_symbols"])
    assert [row.step for row in steps] == ["file_symbols", "next_range"]
    assert rejected == () and dropped == ()


def test_a_bare_string_is_accepted_as_a_one_step_pipeline():
    steps, rejected, _ = resolve_active_pipeline("read_file_range", "next_range")
    assert [row.step for row in steps] == ["next_range"] and rejected == ()


@pytest.mark.parametrize("empty", [None, [], (), "", "   ", 0, False, {}])
def test_no_pipeline_requested_is_not_an_error(empty):
    assert resolve_active_pipeline("read_file_range", empty) == ((), (), ())


def test_the_cap_holds_and_names_what_it_dropped(monkeypatch):
    """Claim 4: bounded like _ensure_graph_fresh:106, and never silently.

    The cap is lowered for this one test rather than skipping when nothing would drop.
    Every source happens to offer exactly MAX_STEPS_PER_CALL steps today, so the honest
    skip this used to take left the bound with no positive control anywhere -- a cap that
    silently stopped dropping would have gone unnoticed. _AC is the module (defined below
    at import time, so it is bound long before any test body runs) and the slice is read
    from module globals at call time, which is what makes the patch effective.
    """
    source = "read_file_range"
    offered = list(ACTIVE_CHAIN_STEPS[source])
    assert len(offered) >= 2, "%s offers %r: too few to drop anything" % (source, offered)
    monkeypatch.setattr(_AC, "MAX_STEPS_PER_CALL", 1)
    steps, rejected, dropped = resolve_active_pipeline(source, offered)
    assert len(steps) == 1
    assert rejected == ()
    assert list(dropped) == offered[1:]
    assert set(dropped).isdisjoint({row.step for row in steps})


def test_the_cap_is_a_real_bound():
    assert MAX_STEPS_PER_CALL >= 1
    for source, offered in ACTIVE_CHAIN_STEPS.items():
        steps, _, _ = resolve_active_pipeline(source, list(offered))
        assert len(steps) <= MAX_STEPS_PER_CALL, source


# ─── 6. the executor: the half that actually runs the requested steps ─────────────

# Reached through sys.modules rather than a second import line: monkeypatching _BY_KEY is
# the only way to exercise the executor's build_error path, because resolve_active_pipeline
# resolves rows from that table and never from a row handed in by a caller.
_AC = sys.modules["orchestrator.core.active_chain"]


def _ok_invoke(record=None):
    """A fake target: async, answers a dict, and records what it was actually called with."""
    async def invoke(target, payload):
        if record is not None:
            record.append((target, payload))
        return {"ok": True, "echo_target": target, "echo": payload}
    return invoke


def _run(source_tool, requested, result=None, args=None, invoke=None):
    """Drive the coroutine with asyncio.run instead of an async test.

    Deliberate: pytest-asyncio is not assumed to be installed here, and a missing plugin
    turns async tests into skips or errors -- the one outcome a guard must never produce
    quietly. asyncio.run needs nothing but the stdlib.
    """
    return asyncio.run(execute_active_pipeline(
        source_tool,
        requested,
        _SOURCE_RESULT[source_tool] if result is None else result,
        _SOURCE_ARGS[source_tool] if args is None else args,
        invoke or _ok_invoke(),
    ))


def test_the_executor_is_awaitable_because_the_funnel_awaits_it():
    """The dispatch funnel is async (orchestrator_server.py:14834 awaits its tools). A
    plain def here would return a coroutine-less value and the wiring would ship a bug no
    other test in this file could see."""
    assert asyncio.iscoroutinefunction(execute_active_pipeline)


@pytest.mark.parametrize("empty", [None, [], (), "", "   ", 0, False, {}])
def test_nothing_requested_answers_none_so_the_response_gains_no_key(empty):
    """None, not an empty report. Nearly every read will not pass pipeline=, and those
    reads must not pay a single byte for a feature they did not ask for."""
    record = []
    assert _run("read_file_range", empty, invoke=_ok_invoke(record)) is None
    assert record == [], "nothing was requested, so nothing may be invoked"


def test_a_requested_step_runs_and_the_report_says_what_was_sent():
    record = []
    report = _run("read_file_range", ["next_range"], invoke=_ok_invoke(record))
    assert report["attempted"] == ["next_range"]
    assert report["ran"] == ["next_range"], report
    assert report["skipped"] == [] and report["failed"] == []
    assert len(record) == 1 and record[0][0] == "read_file_range", record
    step = report["steps"][0]
    assert step["state"] == "ran"
    assert step["result"]["echo_target"] == "read_file_range"
    # The reported args ARE the args sent -- otherwise the report is a story about a call
    # that did not happen, which is worse than no report.
    assert step["args"] == record[0][1]
    assert step["why"] == _AC._BY_KEY[("read_file_range", "next_range")].why
    assert isinstance(step["duration_ms"], int) and step["duration_ms"] >= 0
    assert report["max_steps_per_call"] == MAX_STEPS_PER_CALL


def test_a_rejected_name_is_echoed_and_costs_only_itself():
    record = []
    report = _run("read_file_range", ["next_range", "nope"], invoke=_ok_invoke(record))
    assert report["ran"] == ["next_range"]
    assert report["rejected"] == ["nope"]
    assert report["offered"] == list(ACTIVE_CHAIN_STEPS["read_file_range"])
    assert len(record) == 1, "the typo must not have cost the good step"


def test_a_pipeline_of_only_typos_answers_a_report_not_none():
    """The one case where an all-empty run must NOT collapse to None: the caller asked for
    something real-looking and got nothing, and silence there reads as success."""
    record = []
    report = _run("read_file_range", ["nope"], invoke=_ok_invoke(record))
    assert report is not None
    assert report["rejected"] == ["nope"]
    assert report["attempted"] == [] and report["ran"] == []
    assert record == []


def test_no_input_is_skipped_without_ever_touching_the_target():
    """A source payload with nothing to chain from costs a report line, not a call."""
    record = []
    report = _run("read_file_range", ["next_range"], result={"ok": True},
                  invoke=_ok_invoke(record))
    assert report["skipped"] == ["next_range"], report
    assert report["steps"][0]["reason"] == "no_input"
    assert "result" not in report["steps"][0], "a step that did not run has no result"
    assert record == []


def test_a_builder_that_raises_is_skipped_under_its_own_reason(monkeypatch):
    """build_error must survive the executor as build_error. Laundering it into no_input
    would make a builder bug indistinguishable from an empty source payload -- the exact
    confusion the passive half was caught by."""
    key = ("read_file_range", "next_range")
    monkeypatch.setitem(_AC._BY_KEY, key,
                        _AC._BY_KEY[key]._replace(build=lambda result, args: 1 // 0))
    record = []
    report = _run("read_file_range", ["next_range"], invoke=_ok_invoke(record))
    assert report["skipped"] == ["next_range"]
    assert report["steps"][0]["reason"] == "build_error:ZeroDivisionError"
    assert record == [], "a builder that raised must not reach the target"


def test_one_failing_step_does_not_cancel_the_next():
    async def invoke(target, payload):
        if target == "read_file_range":
            raise RuntimeError("boom")
        return {"ok": True}

    report = _run("read_file_range", ["next_range", "file_symbols"], invoke=invoke)
    assert report["failed"] == ["next_range"], report
    assert report["ran"] == ["file_symbols"], report
    assert report["steps"][0]["reason"] == "invoke_error:RuntimeError"
    assert "duration_ms" in report["steps"][0], "a failure still took time; report it"


def test_a_non_dict_answer_is_a_failure_not_a_result():
    async def invoke(target, payload):
        return "a string"

    report = _run("read_file_range", ["next_range"], invoke=invoke)
    assert report["failed"] == ["next_range"]
    assert report["steps"][0]["reason"] == "bad_result:str"
    assert "result" not in report["steps"][0]


def test_the_cap_bounds_the_executor_and_not_only_the_resolver(monkeypatch):
    """Positive control for the bound itself.

    Every source currently offers exactly MAX_STEPS_PER_CALL steps, so a test that asked
    for all of them would SKIP -- and the cap would then have no exercise anywhere in the
    suite, in either half. Lowering the cap for one test is what makes the drop observable
    today, and it keeps working when a fourth step is added to a source later.
    """
    source = "read_file_range"
    offered = list(ACTIVE_CHAIN_STEPS[source])
    assert len(offered) >= 2, "%s offers %r: too few to drop anything" % (source, offered)
    monkeypatch.setattr(_AC, "MAX_STEPS_PER_CALL", 1)
    record = []
    report = _run(source, offered, invoke=_ok_invoke(record))
    assert len(record) == 1, record
    assert report["attempted"] == offered[:1]
    assert list(report["dropped"]) == offered[1:]
    assert report["max_steps_per_call"] == 1, "the report must state the bound in force"
    assert set(report["dropped"]).isdisjoint(report["ran"])


def test_the_report_accounts_for_every_attempted_step():
    """Invariant: attempted == ran + skipped + failed, disjointly. Without it a step could
    vanish from the report entirely and every other assertion here would still pass."""
    checked = 0
    for source, offered in ACTIVE_CHAIN_STEPS.items():
        report = _run(source, list(offered))
        assert report is not None, source
        buckets = [report["ran"], report["skipped"], report["failed"]]
        flat = [name for bucket in buckets for name in bucket]
        assert sorted(flat) == sorted(report["attempted"]), (source, report)
        assert len(flat) == len(set(flat)), "a step was counted twice: %r" % (report,)
        assert len(report["steps"]) == len(report["attempted"])
        checked += 1
    assert checked, "no source tool was exercised: the loop proved nothing"


# ------------------------------------------------------ 5. the wiring (W2 P2c, 2026-09-18)
#
# Everything above proves the LEAF. None of it proves the leaf is REACHABLE, and that gap is
# B019 paid a third time: nine tests passed against a Python signature while the live tool
# stayed broken, because the dispatcher validates against the REGISTRY -- "registry
# properties are the public contract; signatures are internal". So this section drives
# _schema_for_tool, the same function the dispatcher itself reads, and asks the server's own
# source for the ordering that no import can show.

_SERVER_SRC = Path(srv.__file__).read_text(encoding="utf-8", errors="ignore")
_SERVER_TREE = ast.parse(_SERVER_SRC)
_PARENTS = {child: parent for parent in ast.walk(_SERVER_TREE)
            for child in ast.iter_child_nodes(parent)}


def _registry_props(tool: str) -> dict:
    """The properties dict the dispatcher's own allowed_params is built from."""
    return ((srv._schema_for_tool(tool) or {}).get("parameters") or {}).get("properties") or {}


def _calls_named(tree, name: str) -> list:
    """Every Call node whose callee is exactly `name`, plain or attribute-qualified."""
    out = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        got = func.id if isinstance(func, ast.Name) else getattr(func, "attr", None)
        if got == name:
            out.append(node)
    return out


def _enclosing_function(node):
    while node in _PARENTS:
        node = _PARENTS[node]
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            return node
    return None


def _enclosing_ifs(node, stop):
    """The ast.If tests wrapping `node` up to (not including) `stop`."""
    out = []
    while node is not stop and node in _PARENTS:
        node = _PARENTS[node]
        if isinstance(node, ast.If):
            out.append(node)
    return out


def _the_pipeline_pop():
    hits = [c for c in _calls_named(_SERVER_TREE, "pop")
            if c.args and isinstance(c.args[0], ast.Constant)
            and c.args[0].value == "pipeline"]
    assert len(hits) == 1, "expected exactly one `pipeline` pop, found %d" % len(hits)
    return hits[0]


def _the_executor_call():
    hits = _calls_named(_SERVER_TREE, "execute_active_pipeline")
    assert len(hits) == 1, "expected exactly one executor call site, found %d" % len(hits)
    return hits[0]


def test_every_source_tool_declares_pipeline_in_the_registry_the_dispatcher_reads():
    """A parameter absent from the registry is one no CA can discover and none may send: the
    dispatcher would answer UNSUPPORTED_FIELD before the handler ever ran. Asserted through
    _schema_for_tool rather than the raw table, because that indirection is where B019 hid."""
    for source, offered in ACTIVE_CHAIN_STEPS.items():
        props = _registry_props(source)
        assert props, "%s has no registry properties at all" % source
        assert "pipeline" in props, \
            "%s offers %r but the registry never declares pipeline" % (source, list(offered))
        spec = props["pipeline"]
        assert spec.get("type") == "array", spec
        assert list((spec.get("items") or {}).get("enum") or []) == sorted(offered), \
            "%s: the declared enum drifted from the leaf's own table" % source
        assert PIPELINE_RESPONSE_KEY in spec.get("description", ""), \
            "%s: the description never says which response key the answers land under" % source


def test_only_the_source_tools_declare_pipeline():
    """Positive control for the test above, which would pass vacuously if _schema_for_tool
    answered {} for everything, and would also pass if the field had been pasted onto all 258
    tools. Derived from the live registry, so a new source tool cannot forget to be listed."""
    declaring = {name for name in ct.TOOL_MAP if "pipeline" in _registry_props(name)}
    assert declaring == set(ACTIVE_CHAIN_STEPS), \
        "tools declaring pipeline %r != sources offering steps %r" % (
            sorted(declaring), sorted(ACTIVE_CHAIN_STEPS))
    assert len(ct.TOOL_MAP) - len(declaring) > 100, \
        "almost every tool declares pipeline: this scan is not discriminating anything"


def test_pipeline_is_popped_before_the_dispatcher_validates_unknown_parameters():
    """`pipeline` is dispatcher-consumed and never a handler kwarg -- exactly why `fields` and
    `page_id` are popped in the same place. Left in body it reaches the tool as an unexpected
    keyword argument; popped after the check it is refused as UNSUPPORTED_FIELD instead."""
    pop = _the_pipeline_pop()
    fn = _enclosing_function(pop)
    assert fn is not None, "the pop is at module level: it cannot see a request body"
    allowed = [n.lineno for n in ast.walk(fn) if isinstance(n, ast.Assign)
               and any(isinstance(t, ast.Name) and t.id == "allowed_params" for t in n.targets)]
    assert allowed, "allowed_params is no longer built in this function; re-locate the pop"
    assert pop.lineno < min(allowed), \
        "pipeline is popped at line %d, after allowed_params is built at %d" % (
            pop.lineno, min(allowed))


def test_the_pop_is_unconditional_so_a_tool_with_no_steps_answers_rather_than_refuses():
    """Popped for EVERY tool, not just the two that offer steps: a tool offering none then
    answers a report naming what it does offer (an empty list), which tells the caller more
    than UNSUPPORTED_FIELD would -- the same self-describing choice `fields` makes with
    fields_unknown."""
    pop = _the_pipeline_pop()
    for branch in _enclosing_ifs(pop, _enclosing_function(pop)):
        names = {n.id for n in ast.walk(branch.test) if isinstance(n, ast.Name)}
        assert "tool_name" not in names, \
            "the pipeline pop is gated on tool_name at line %d" % branch.lineno


def test_the_executor_is_reached_from_the_same_function_that_popped_the_request():
    """If the pop and the run sat in different functions the name would not even be in scope,
    and the failure would be a runtime NameError on the first real pipeline= call."""
    assert _enclosing_function(_the_executor_call()) is _enclosing_function(_the_pipeline_pop())


def test_the_executor_is_not_nested_inside_a_tool_name_branch():
    """The two pre-existing git hints live inside `if tool_name == \"git_status\"` blocks. This
    one must not: _result is the parsed payload for every tool, which is the only reason a
    single insertion covers the whole surface."""
    call = _the_executor_call()
    for branch in _enclosing_ifs(call, _enclosing_function(call)):
        names = {n.id for n in ast.walk(branch.test) if isinstance(n, ast.Name)}
        assert "tool_name" not in names, \
            "the executor is nested inside a tool_name test at line %d" % branch.lineno


def test_the_pipeline_runs_after_the_passive_annotations_and_before_the_ledger():
    """Ordering is the design, not a preference. Every step builds its payload from THIS
    call's answer, so the answer must be finished first; and the ledger must record what the
    CA was actually told, which is the same reason ca_next_tool is attached before it."""
    call = _the_executor_call()
    fn = _enclosing_function(call)

    def _first(name):
        hits = [c.lineno for c in _calls_named(fn, name)]
        assert hits, "%s is no longer called in the dispatch funnel" % name
        return min(hits)

    assert _first("annotate_next_tool") < call.lineno, \
        "the pipeline runs before ca_next_tool is resolved, so a step reads a partial payload"
    assert call.lineno < _first("_write_ledger_finish_async"), \
        "the pipeline report is attached after the ledger write, so the ledger loses it"


def test_the_r101_projection_is_checked_before_any_step_runs():
    """A report the caller projected away must cost ZERO reads, not three reads whose answers
    are then thrown out -- that is the entire point of a token projection."""
    gate = _calls_named(_SERVER_TREE, "pipeline_key_requested")
    assert len(gate) == 1, "expected exactly one R101 gate call, found %d" % len(gate)
    run = _the_executor_call()
    assert gate[0].lineno < run.lineno, \
        "the projection is consulted at line %d, after the steps already ran at %d" % (
            gate[0].lineno, run.lineno)
    assert _enclosing_function(gate[0]) is _enclosing_function(run)


@pytest.mark.parametrize("fields,expected", [
    (None, True),                        # no projection at all -> emit
    (set(), True),                       # empty: no fields= was sent
    ({"pipeline"}, True),                # explicitly requested
    ({"ok", "pipeline", "error"}, True),
    ({"pipeline.ran"}, True),            # a dotted R101 spec rooted at this key
    ({"pipeline.steps.duration_ms"}, True),
    ({"ok", "result"}, False),           # projected away -> must not sneak back in
    ({"ok", "pipelines"}, False),        # a near-miss name is not this key
    ({"ran"}, False),                    # a leaf name without its root is not this key
    (5, True),                           # not iterable: no usable projection, so emit
])
def test_pipeline_key_requested_matches_the_rule_annotate_next_tool_already_follows(
        fields, expected):
    """One R101 decision, stated once in the leaf and shaped exactly like
    test_annotate_respects_an_explicit_fields_projection in the ca_next_tool guard. It lives
    in the leaf so the rule is pinned by BEHAVIOUR rather than by grepping an expression."""
    assert pipeline_key_requested(fields) is expected
