"""A34 W2 - pin the PASSIVE auto-chain table and the one place it is applied.

The gap it closes, measured 2026-09-18 (ast walk over the production tree with
orchestrator_server imported, so the late-registered git/cron tools are in TOOL_MAP):
258 tools, 117 emitting a hint from their own handler source, 141 emitting none -- and by
real ledger traffic (.orchestrator_cache/sessions.db table `events`, 13406 rows) the biggest
of those gaps is read_file_range at 3478 calls.

The fix is ONE table (orchestrator/core/passive_chain.py) applied once at the dispatch funnel,
reusing the already-registered `ca_hint` and the free-form `ca_next_action` so that
`ca_next_tool` derives from it with no second mechanism. That design is only safe while three
things stay true, and each one is a way this feature can be broken without any test failing:

  1. a row is written against a payload key that really exists. Every trigger below is
     checked against _PROBE_SHAPES -- the recorded output of a LIVE call to each tool
     (run_project_test_script label a34_probe_shapes, 2026-09-18). A row keyed on a field
     nobody measured would silently never fire, and "never fires" looks exactly like
     "the condition did not hold".
  2. a row's ca_next_action names something callable. Re-derived against the live TOOL_MAP
     every run, never listed here, so removing a tool fails this file instead of shipping a
     hint that costs a CA a round trip.
  3. the application point covers every tool and runs BEFORE the ca_next_tool resolver. An ast
     check pins both, because the two pre-existing git hints beside it ARE written inside
     `if tool_name == "..."` branches and that is the shape to not copy.

The deliberate NON-coverage is pinned too (test_git_log_is_an_honest_gap_not_an_oversight):
git_log is the highest-traffic tool with no row, because its payload is `{commits, ref}` and
there is no git_* tool that reads a commit. If one is ever added, that test fails and the gap
has to be re-decided rather than quietly persisting.
"""

import ast
from pathlib import Path

import pytest

import code_tools as ct
import orchestrator_server as srv  # noqa: F401 -- triggers the late git/cron registrations
from orchestrator.core.next_tool import annotate_next_tool, resolve_next_tool
from orchestrator.core.passive_chain import (
    PASSIVE_CHAIN_TOOLS,
    PASSIVE_CHAINS,
    apply_passive_chain,
)

REPO = Path(__file__).resolve().parents[1]

# The measurement the table is built on: top-level keys returned by a LIVE call to each tool
# probed on 2026-09-18. This is evidence, not decoration -- a trigger outside it is a row
# written against a field that does not exist.
_PROBE_SHAPES = {
    "read_file_range": {
        "ok", "result", "file", "project_path", "file_group", "lang", "total_lines", "shown",
        "range_id", "bounded", "max_lines", "max_lines_cap", "max_lines_default",
        "scope_requested", "scope_resolved", "selection", "content_hash", "range_ref",
        "cache_hit", "cache_waited", "estimated_tokens", "token_budget", "token_estimator",
        "auto_full_file", "small_file_threshold", "truncated", "next_ranges",
        "read_token_seeded", "derived_state_touched",
    },
    "git_log": {"ok", "commits", "ref"},
    "cache_delta": {"ok", "added", "modified", "deleted", "total_changed", "cache_updated_at"},
    "query_index": {
        "ok", "matched", "returned", "truncated", "total_files", "files", "snapshot_id",
        "query_scope", "content_files_scanned",
    },
    "cbm_graph_status": {"ok", "result"},
    # A38, probed live on 2026-09-19 over HTTP /api/call/<tool>. `generation` and the operation_*
    # keys are the HTTP envelope, recorded as measured; the tool's own answer is `result`, which
    # is why all four triggers below live in _PROBE_NESTED.
    "cbm_quality_gate": {"ok", "result", "generation"},
    "cbm_dsm": {"ok", "result", "generation"},
    "cbm_evolution": {"ok", "result", "generation"},
    # #36 (2026-09-25): operation_replayed left the envelope (it restated attempts > 1),
    # so it is dropped from both recorded shapes rather than kept as a key nothing emits.
    "cbm_what_if": {
        "ok", "result", "generation", "operation_id", "write_status_url", "operation_attempts",
    },
    # A38-B, probed live 2026-09-20 over HTTP /api/call/<tool>. Same envelope story as A38:
    # `generation` and the operation_* keys are the transport, the tool's own answer is `result`,
    # so all three triggers below live in _PROBE_NESTED.
    "cbm_nodes_query": {"ok", "result", "generation"},
    "cbm_edges_query": {"ok", "result", "generation"},
    "cbm_test_gap": {
        "ok", "result", "generation", "operation_id", "write_status_url", "operation_attempts",
    },
}

# The one nested level the probe expanded. cbm_graph_status carries its whole answer under
# `result`, so its trigger is legitimately not a top-level key.
_PROBE_NESTED = {
    # A39: re-probed live 2026-09-20 (/api/call/cbm_graph_status, this repo). The six keys the
    # A34 probe recorded are all still present; the thirteen added here were in the same
    # answer. `stale_files` is listed because the M-2 row keys on it, and the ORDER matters --
    # measure first, then add the row, never the reverse: this guard exists because a row
    # keyed on a field nobody measured never fires, and that silence is indistinguishable from
    # a false condition. Values at that call: db_present=true, stale_files=9, freshness=
    # "stale", stale_scan.complete=true -- the exact state M-2 exists to catch (graph PRESENT
    # but behind) and precisely the state the db_present row above cannot see.
    "cbm_graph_status": {
        "enabled", "db_present", "integrity", "nodes", "edges", "files", "last_run",
        "stale_files", "extractor_version", "tree_sitter_enabled", "wal", "freshness",
        "elapsed_ms", "error", "integrity_probe", "integrity_ms", "stale_scan", "scan_ms",
        "extract_js_enabled",
    },
    # A38: the whole cbm family answers under `result`, so these four are nested for the same
    # reason. Probed live on 2026-09-19 (script label a38_probe_cbm..8) against this repo.
    "cbm_quality_gate": {
        "version", "status", "reason", "pass", "aggregate", "checks", "counts", "cycles",
        "max_depth", "metric_notes", "truncated", "reasons", "provenance", "config", "mode",
        "wrote", "cache_hit", "elapsed_ms",
    },
    "cbm_dsm": {
        "version", "status", "reason", "pass", "files", "size", "total_files", "edge_count",
        "density", "propagation_cost", "propagation_mode", "above_diagonal", "below_diagonal",
        "same_level", "clusters", "page", "levels", "truncated", "sampled", "metric_notes",
        "provenance", "cache_hit", "elapsed_ms", "wrote",
    },
    "cbm_evolution": {
        "version", "status", "reason", "pass", "lookback_days", "commits_analyzed",
        "commits_dropped_merges", "files_dropped_pair_cap", "pairs_considered",
        "rename_history_partial", "history_truncated", "shallow", "churn", "coupling",
        "authors", "hotspots", "bus_factor_score", "single_author_ratio", "churn_score",
        "evolution_score", "metric_notes", "truncated", "provenance", "wrote",
    },
    "cbm_what_if": {
        "version", "status", "reason", "pass", "applied", "ops_applied", "steps", "before",
        "after", "diff", "clone", "metric_notes", "truncated", "elapsed_ms", "provenance",
        "original_unchanged", "caps", "cache_hit", "wrote", "auto_index_result",
    },
    # A38-B, probed live 2026-09-20: limit=3 on the two index queries, a bare call on test_gap.
    "cbm_nodes_query": {
        "count", "enabled", "error", "limit", "next_offset", "offset", "results", "total",
    },
    "cbm_edges_query": {
        "count", "enabled", "error", "limit", "next_offset", "offset", "results", "total",
    },
    "cbm_test_gap": {
        "auto_index_result", "cache_hit", "counts", "coverage_ratio", "covered_files",
        "elapsed_ms", "gaps", "isolated_uncovered", "metric_notes", "pass", "pass_semantics",
        "per_test", "provenance", "reason", "reasons", "score", "source_files", "status",
        "test_files", "truncated", "uncovered_files", "vacuous", "version",
    },
}

# A minimal payload per (tool, trigger) that SHOULD fire, and one that should not. Written
# from the probe shapes above, so a row whose predicate reads something else fails here.
_FIRES = {
    ("read_file_range", "truncated"): {"ok": True, "truncated": True},
    ("read_file_range", "read_token_seeded"): {"ok": True, "read_token_seeded": True},
    ("cache_delta", "total_changed"): {"ok": True, "total_changed": 3},
    ("query_index", "files"): {"ok": True, "files": [{"path": "a.py"}]},
    ("cbm_graph_status", "db_present"): {"ok": True, "result": {"db_present": False}},
    # A38, probed live: a gate run that counted cycles, a DSM with one A<->B component, a DSM
    # page that stops short of `size` on both axes, one co-change pair, and the measured
    # ok:true / applied:false shape (an op named an id the graph view does not contain).
    ("cbm_quality_gate", "cycles"): {"ok": True, "result": {"cycles": 1}},
    ("cbm_dsm", "clusters"): {
        "ok": True,
        "result": {"clusters": [{"files": ["a.py", "b.py"], "internal_edges": 2, "level": 0}]},
    },
    ("cbm_dsm", "page"): {
        "ok": True,
        "result": {"size": 130,
                   "page": {"row_start": 0, "col_start": 0, "rows": 100, "cols": 100}},
    },
    ("cbm_evolution", "coupling"): {
        "ok": True,
        "result": {"coupling": [{"file_a": "a.py", "file_b": "b.py", "jaccard": 0.7}]},
    },
    ("cbm_what_if", "applied"): {
        "ok": True,
        "result": {"applied": False, "status": "unavailable", "reason": "invalid_id"},
    },
    # A38-B, probed live 2026-09-20: a nodes/edges query with limit=3 that matched, and the BARE
    # cbm_test_gap call -- which answers status "unavailable" precisely because no test file was
    # ever compared, while `pass`/`score` still look like a verdict.
    ("cbm_nodes_query", "count"): {
        "ok": True,
        "result": {"count": 3, "total": 3, "results": [{"id": "a.py"}]},
    },
    ("cbm_edges_query", "count"): {
        "ok": True,
        "result": {"count": 3, "total": 3, "results": [{"src": "a.py", "dst": "b.py"}]},
    },
    ("cbm_test_gap", "status"): {
        "ok": True,
        "result": {"status": "unavailable", "gaps": [], "pass": False, "score": None},
    },
    # A39 (M-2), probed live 2026-09-20 on this repo: db_present true, stale_files 9, freshness
    # "stale", stale_scan.complete true. The graph EXISTS and every answer computed from it is
    # behind the working tree -- exactly the state the db_present row cannot see, which is why
    # M-2 is a SECOND row and not a widened predicate on the first.
    ("cbm_graph_status", "stale_files"): {
        "ok": True,
        "result": {"db_present": True, "stale_files": 9, "freshness": "stale",
                   "stale_scan": {"stale_files": 9, "files_checked": 469, "complete": True}},
    },
}
_STAYS_SILENT = {
    ("read_file_range", "truncated"): {"ok": True, "truncated": False},
    ("read_file_range", "read_token_seeded"): {"ok": True, "read_token_seeded": False},
    ("cache_delta", "total_changed"): {"ok": True, "total_changed": 0},
    ("query_index", "files"): {"ok": True, "files": []},
    ("cbm_graph_status", "db_present"): {"ok": True, "result": {"db_present": True}},
    # A38. The gate with zero cycles, a DSM whose page covers the whole matrix (size == rows ==
    # cols is exactly dsm.py's "complete axis"), no co-change pair, and a what_if that really
    # applied its ops.
    ("cbm_quality_gate", "cycles"): {"ok": True, "result": {"cycles": 0}},
    ("cbm_dsm", "clusters"): {"ok": True, "result": {"clusters": []}},
    ("cbm_dsm", "page"): {
        "ok": True,
        "result": {"size": 100,
                   "page": {"row_start": 0, "col_start": 0, "rows": 100, "cols": 100}},
    },
    ("cbm_evolution", "coupling"): {"ok": True, "result": {"coupling": []}},
    ("cbm_what_if", "applied"): {"ok": True, "result": {"applied": True, "ops_applied": 1}},
    # A38-B. A query that matched nothing is a dead end, not a chain. And a test_gap run that
    # really compared test files (status "ok", measured with included_files + test_files) has a
    # coverage answer of its own, so it needs no runner hint.
    ("cbm_nodes_query", "count"): {"ok": True, "result": {"count": 0, "results": []}},
    ("cbm_edges_query", "count"): {"ok": True, "result": {"count": 0, "results": []}},
    ("cbm_test_gap", "status"): {
        "ok": True,
        "result": {"status": "ok", "gaps": [], "coverage_ratio": 1.0,
                   "counts": {"source_files": 1, "test_files": 1, "covered_files": 1}},
    },
    # A39 (M-2), from the SAME live probe: a graph that is present AND current. stale_files=0 is
    # the only silent state this row can have, and it is measured rather than assumed because the
    # db_present row above wins first whenever the graph is missing -- so this row only ever
    # speaks about a graph that exists. freshness "current" is carried for the same reason the
    # positive carries "stale": it is what the daemon really answered, so the two states stay
    # tellable apart without re-probing.
    ("cbm_graph_status", "stale_files"): {
        "ok": True,
        "result": {"db_present": True, "stale_files": 0, "freshness": "current",
                   "stale_scan": {"stale_files": 0, "files_checked": 469, "complete": True}},
    },
}

_ROWS = list(PASSIVE_CHAINS)
_IDS = ["%s:%s" % (r.tool, r.trigger) for r in _ROWS]


def _expected_tool(row):
    """What the funnel's resolver will make of this row -- derived, never hand-listed."""
    return resolve_next_tool(row.next_action, ct.TOOL_MAP, row.tool)


# ------------------------------------------------------------------- 1. table integrity

def test_the_table_is_not_empty_and_every_row_is_distinct():
    # Positive control for every parametrized test below: an empty table would make them all
    # pass by collecting nothing.
    assert len(_ROWS) >= 4
    keys = [(r.tool, r.trigger) for r in _ROWS]
    assert len(keys) == len(set(keys)), "duplicate (tool, trigger): %r" % keys


def test_the_covered_tool_list_is_derived_and_not_a_second_hand_written_list():
    assert PASSIVE_CHAIN_TOOLS == tuple(sorted({r.tool for r in _ROWS}))


@pytest.mark.parametrize("row", _ROWS, ids=_IDS)
def test_every_row_names_a_tool_that_really_exists(row):
    assert row.tool in ct.TOOL_MAP, \
        "%s is not a registered tool, so this row can never fire" % row.tool


@pytest.mark.parametrize("row", _ROWS, ids=_IDS)
def test_every_row_trigger_is_a_key_the_probe_actually_measured(row):
    shape = _PROBE_SHAPES.get(row.tool)
    assert shape, "no recorded probe for %s -- measure it before adding a row" % row.tool
    allowed = shape | _PROBE_NESTED.get(row.tool, set())
    assert row.trigger in allowed, (
        "%s has no %r in its measured payload; a row keyed on a field that does not exist "
        "never fires and the silence is indistinguishable from a false condition"
        % (row.tool, row.trigger))


@pytest.mark.parametrize("row", _ROWS, ids=_IDS)
def test_every_row_next_action_resolves_to_a_live_tool(row):
    """The whole point of reusing ca_next_action: ca_next_tool derives from it for free. A row
    whose action resolves to nothing gives the CA prose it cannot act on."""
    assert _expected_tool(row), (
        "%r resolves to no live tool, so this row emits advice with no callable half"
        % row.next_action)


@pytest.mark.parametrize("row", _ROWS, ids=_IDS)
def test_every_row_carries_a_one_line_hint(row):
    assert row.hint.strip() == row.hint and row.hint
    assert "\n" not in row.hint
    assert row.next_action.strip() == row.next_action and row.next_action


def test_at_least_one_row_is_self_referential_so_the_imperative_rule_is_exercised():
    """read_file_range's truncation row points back at read_file_range. The resolver drops a
    self-referential answer UNLESS the literal says call/use/run, so this row is the live proof
    that the table respects that rule instead of silently losing its ca_next_tool."""
    selfies = [r for r in _ROWS if _expected_tool(r) == r.tool]
    assert selfies, "no self-referential row left; the imperative rule is now untested here"
    for row in selfies:
        assert resolve_next_tool(row.next_action, ct.TOOL_MAP, row.tool) == row.tool


def test_git_log_is_an_honest_gap_not_an_oversight():
    """git_log is the top-traffic tool with no row: its payload is {commits, ref} and no git_*
    tool reads a commit. If one is added, this fails and the gap must be re-decided."""
    assert "git_log" not in PASSIVE_CHAIN_TOOLS
    readers = [t for t in ("git_show", "git_cat_file", "git_read_commit") if t in ct.TOOL_MAP]
    assert not readers, "%r exists now -- git_log can be given a row" % readers


def test_the_gate_unavailable_count_is_an_honest_gap():
    """cbm_quality_gate answers `counts.unavailable`, and A38 deliberately gave it NO row.

    Measured causes are three, each fixed somewhere else: missing_layers (the caller's
    rules_toml), missing_file_line_counts (the caller's metrics dict), missing_quality_factors
    (another tool's output). No single next_action is true for all three, so a row here would be
    a guess a CA pays a round trip for. If the reasons ever collapse to one, this fails and the
    gap must be re-decided rather than silently filled.
    """
    gate_rows = [r for r in _ROWS if r.tool == "cbm_quality_gate"]
    assert gate_rows, "cbm_quality_gate lost its row; this gap note now guards nothing"
    assert not [r for r in gate_rows if "unavailable" in r.trigger]


def test_the_test_gap_row_is_keyed_on_status_and_not_on_gaps():
    """A38-B gave cbm_test_gap a row, but keyed on `status` -- never on `gaps`.

    Measured 2026-09-20: `gaps` came back [] in every state reachable without inventing a payload
    (bare, included_files only, and both of those with a tested and with an untested file). A
    `gaps > 0` predicate would therefore be trap 3 -- a row written against a shape no probe ever
    showed. If a live call is ever seen returning a non-empty `gaps`, this is where the decision
    gets re-made instead of being quietly widened.
    """
    rows = [r for r in _ROWS if r.tool == "cbm_test_gap"]
    assert rows, "cbm_test_gap lost its row; this decision note now guards nothing"
    assert not [r for r in rows if "gaps" in r.trigger]
    assert [r for r in rows if r.trigger == "status"]


# ---------------------------------------------------------------------- 2. predicates

@pytest.mark.parametrize("row", _ROWS, ids=_IDS)
def test_every_predicate_fires_on_its_measured_positive(row):
    payload = _FIRES[(row.tool, row.trigger)]
    assert row.when(payload) is True or bool(row.when(payload)), payload


@pytest.mark.parametrize("row", _ROWS, ids=_IDS)
def test_every_predicate_is_silent_on_its_measured_negative(row):
    payload = _STAYS_SILENT[(row.tool, row.trigger)]
    assert not row.when(payload), payload


@pytest.mark.parametrize("junk", [
    {},                          # the key absent entirely
    {"result": "a string"},      # `result` not a dict
    {"result": None},
    {"truncated": None, "read_token_seeded": None, "total_changed": None, "files": None},
    {"total_changed": "three"},  # a value of the wrong type -- this one really did raise
    {"total_changed": True},     # a count arriving as a flag: int(True) is 1, so it would fire
])
@pytest.mark.parametrize("row", _ROWS, ids=_IDS)
def test_no_predicate_raises_or_fires_on_a_shape_it_was_not_measured_against(row, junk):
    """Trap 3 in the leaf: the probe showed key names, not always value types. Every predicate
    must degrade to a MISSING hint, never a wrong one and never an exception."""
    try:
        fired = row.when(junk)
    except Exception as exc:  # the assertion IS that this cannot happen
        pytest.fail("%s:%s raised on %r: %r" % (row.tool, row.trigger, junk, exc))
    assert not fired, "%s:%s fired on an unmeasured shape %r" % (row.tool, row.trigger, junk)


# --------------------------------------------------------------------- 3. application

@pytest.mark.parametrize("row", _ROWS, ids=_IDS)
def test_apply_writes_both_keys_and_reports_which_trigger_fired(row):
    payload = dict(_FIRES[(row.tool, row.trigger)])
    assert apply_passive_chain(row.tool, payload) == row.trigger
    assert payload["ca_hint"] == row.hint
    assert payload["ca_next_action"] == row.next_action


@pytest.mark.parametrize("row", _ROWS, ids=_IDS)
def test_the_passive_action_then_derives_a_callable_ca_next_tool(row):
    """End to end in the funnel's order: table first, resolver second. This is the claim that
    the passive side needed no new vocabulary -- if it breaks, the table is emitting prose."""
    payload = dict(_FIRES[(row.tool, row.trigger)])
    apply_passive_chain(row.tool, payload)
    annotate_next_tool(payload, ct.TOOL_MAP, row.tool)
    assert payload["ca_next_tool"] == _expected_tool(row)
    assert payload["ca_next_tool"] in ct.TOOL_MAP


def test_precedence_inside_a_tool_is_the_table_order():
    # Both read_file_range rows hold on a truncated read. Truncation must win: a CA that only
    # saw part of a range should finish reading before it is told it may write.
    payload = {"ok": True, "truncated": True, "read_token_seeded": True}
    assert apply_passive_chain("read_file_range", payload) == "truncated"
    order = [r.trigger for r in _ROWS if r.tool == "read_file_range"]
    assert order.index("truncated") < order.index("read_token_seeded")


def test_a_predeclared_empty_hint_is_not_treated_as_an_existing_one():
    """Trap 1, and a measured one: tool_names answers with `"ca_hint": "", "ca_next_action": ""`
    already in the payload. An `in`-based guard would suppress the passive hint on every tool
    of that shape, and the suppression would be invisible."""
    payload = {"ok": True, "total_changed": 2, "ca_hint": "", "ca_next_action": ""}
    assert apply_passive_chain("cache_delta", payload) == "total_changed"
    assert payload["ca_hint"] and payload["ca_next_action"]


@pytest.mark.parametrize("existing", [
    {"ca_hint": "the tool said this itself"},
    {"ca_next_action": "refresh_project_map"},
    {"ca_hint": "a", "ca_next_action": "b"},
])
def test_a_hint_a_tool_wrote_itself_is_never_mixed_with_the_table(existing):
    payload = {"ok": True, "total_changed": 2, **existing}
    assert apply_passive_chain("cache_delta", payload) == ""
    for key, value in existing.items():
        assert payload[key] == value
    assert set(payload) == {"ok", "total_changed", *existing}


@pytest.mark.parametrize("payload", [
    {"ok": False, "total_changed": 2},
    {"total_changed": 2},          # no ok at all
    {"ok": "yes", "total_changed": 2},
])
def test_only_a_real_success_payload_gets_a_passive_hint(payload):
    """An error path already carries its own ca_next_action nearly everywhere (332 measured
    emission sites), so replacing a specific error message with a generic hint would be worse
    than silence."""
    before = dict(payload)
    assert apply_passive_chain("cache_delta", payload) == ""
    assert payload == before


@pytest.mark.parametrize("payload", [None, [], "text", 5, ()])
def test_apply_ignores_a_payload_that_is_not_a_dict(payload):
    assert apply_passive_chain("cache_delta", payload) == ""


def test_a_tool_with_no_row_is_left_untouched():
    payload = {"ok": True, "commits": [{"sha": "abc"}], "ref": "HEAD"}
    assert apply_passive_chain("git_log", payload) == ""
    assert set(payload) == {"ok", "commits", "ref"}


@pytest.mark.parametrize("fields,written", [
    (None, {"ca_hint", "ca_next_action"}),               # no projection -> both
    (set(), {"ca_hint", "ca_next_action"}),               # empty -> no fields= was sent
    ({"ca_hint"}, {"ca_hint"}),                          # only what was asked for
    ({"ca_next_action"}, {"ca_next_action"}),
    ({"ok", "ca_hint", "ca_next_action"}, {"ca_hint", "ca_next_action"}),
    ({"ca_hint.anything"}, {"ca_hint"}),                 # dotted R101 spec on the same key
    ({"ok", "total_changed"}, set()),                    # projected away -> must not sneak in
])
def test_a_fields_projection_decides_which_keys_may_be_added(fields, written):
    """R101 runs inside the tool, so the payload here is already projected: a key added now
    bypasses the caller's token budget entirely."""
    payload = {"ok": True, "total_changed": 2}
    got = apply_passive_chain("cache_delta", payload, fields)
    assert set(payload) - {"ok", "total_changed"} == written
    # A matching row that wrote nothing must report nothing, or a caller reading the return
    # value believes the payload carries a hint it does not.
    assert bool(got) is bool(written)


# --------------------------------------------------------------- 4. the funnel wiring

def _funnel(name):
    src = (REPO / "orchestrator_server.py").read_text(encoding="utf-8", errors="ignore")
    tree = ast.parse(src)
    parents = {}
    for node in ast.walk(tree):
        for child in ast.iter_child_nodes(node):
            parents[child] = node
    calls = [n for n in ast.walk(tree)
             if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)
             and n.func.id == name]
    assert len(calls) == 1, "expected exactly one %s call, found %d" % (name, len(calls))
    return calls[0], parents


def test_the_table_is_applied_for_every_tool_not_from_inside_a_tool_name_branch():
    """The git hints immediately above it live inside `if tool_name == "git_status"` blocks.
    This one must not: `_result` is the parsed payload for every tool, and that is the only
    reason one application point can cover a 141-tool gap."""
    call, parents = _funnel("apply_passive_chain")
    node = call
    while node in parents:
        node = parents[node]
        if isinstance(node, ast.If):
            names = {n.id for n in ast.walk(node.test) if isinstance(n, ast.Name)}
            assert "tool_name" not in names, (
                "the passive chain is nested inside a tool_name test at line %d, so it no "
                "longer covers every tool" % node.lineno)
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            break
    assert isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))


def test_the_table_runs_before_the_resolver_and_before_the_ledger_write():
    """Order is the whole design: the table sets ca_next_action, the resolver turns it into
    ca_next_tool, the ledger records what the CA was told. Reorder any pair and either the
    passive hint has no callable half or the ledger disagrees with the response."""
    passive, parents = _funnel("apply_passive_chain")
    resolver, _ = _funnel("annotate_next_tool")
    assert passive.lineno < resolver.lineno, \
        "the resolver runs first, so a passive ca_next_action never becomes a ca_next_tool"
    fn = passive
    while fn in parents and not isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)):
        fn = parents[fn]
    ledger = [n.lineno for n in ast.walk(fn)
              if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)
              and n.func.id == "_write_ledger_finish_async"]
    assert ledger, "the funnel no longer writes the ledger; re-locate the application point"
    assert passive.lineno < min(ledger)


def test_the_funnel_passes_the_tool_name_and_the_active_projection():
    call, _parents = _funnel("apply_passive_chain")
    args = [ast.dump(a) for a in call.args]
    assert any("tool_name" in a for a in args), "without tool_name no row can ever match"
    assert any("_R101_FIELDS" in a for a in args), \
        "without the projection the table can smuggle keys past a fields= budget"


def test_ca_hint_is_registered_and_its_trigger_now_names_the_table():
    """The table deliberately introduces NO new field. That is only honest while the existing
    ca_hint row admits the funnel table as one of its triggers."""
    rows = [r for r in ct.AUTO_CHAIN_RULES if r[0] == "ca_hint"]
    assert len(rows) == 1
    assert "PASSIVE_CHAINS" in rows[0][1], \
        "the registry row does not mention the passive table, so nothing documents it"
    assert "ca_next_action" not in ct.AUTO_CHAIN_FIELDS, \
        "ca_next_action must stay free-form prose; the callable half is ca_next_tool"
""
