"""A34 W2 - the PASSIVE half of the auto-chain: a hint for a tool that computes none.

Measured 2026-09-18 with an ast walk over the production tree, orchestrator_server imported
so the LATE-registered git/cron tools are in TOOL_MAP:

  258 tools registered
  117 emit a hint or a ca_next_action from their own handler source
  141 emit neither

141 hand-written hints is exactly the rot A31 forbids -- prose beside code, one edit per tool,
and no way to ask what is covered. So the passive side is ONE table applied once at the
dispatch funnel, and it reuses vocabulary a CA has already been told about (`ca_hint`, plus
the free-form `ca_next_action` from which `ca_next_tool` then derives for free in
orchestrator.core.next_tool) rather than inventing a field nothing documents.

Ranked by REAL traffic -- .orchestrator_cache/sessions.db table `events`, 13406 rows over 166
distinct tool_name -- the hintless tools a CA actually meets are:

  read_file_range 3478 | cbm_quality_gate 267 | cbm_what_if 263 | cbm_evolution 216
  cbm_dsm 200 | run_ps1_script 179 | cache_delta 137 | cbm_graph_status 112
  git_log 60 | query_index 37

A row exists only where the next step can be decided from a payload key that was MEASURED --
the shapes below come from a live call to each tool (script label a34_probe_shapes,
2026-09-18), not from reading a handler and hoping. A tool whose useful next step is not in
its payload gets NO row, and git_log is the worked example: its payload is `{commits, ref}`,
the eight git_* tools contain no `git_show`, and "read the commit you just listed" is not
something any of them does. A missing row is a measured gap; a guessed row is a lie a CA
pays a round trip for.

Three traps this file is written around:

  1. A pre-declared empty key is NOT an absence. `tool_names` answers with `"ca_hint": ""`
     and `"ca_next_action": ""` already in the payload, so `key in result` would suppress the
     passive hint on every tool of that shape. Presence is TRUTHINESS here.
  2. R101 `fields=` projection runs INSIDE the tool (the _R101_FIELDS contextvar is read
     there), so the payload reaching the funnel is ALREADY projected. A key added afterwards
     bypasses the caller's token budget, so a projection that did not ask for a key means:
     do not write it.
  3. A predicate reads a value the probe never showed at a value level (only the key names
     one dict deep). Every predicate here is written so that an unexpected shape makes it
     stay SILENT: the failure mode is a missing hint, never a wrong one.

A38 added the cbm read success paths, and measuring them moved three facts into this file:

  * The whole cbm family answers under `result`, so every cbm trigger is a NESTED key and the
    guard test records it in _PROBE_NESTED. A top-level trigger there would never fire.
  * An oversize payload already carries its own `ca_next_action` (the R101 size wrapper stamps
    truncated/result_ref/read_hint plus advice). The truthiness guard below therefore stands the
    passive row down on exactly those payloads, by design -- the rows fire on the small success
    payloads that had no advice at all, which is where the traffic ranking said the CAs were.
  * MEASURED NON-COVERAGE, recorded so nobody adds the row later and calls it an oversight:
    `cbm_quality_gate` -> `counts.unavailable > 0` has THREE different causes (missing_layers =
    the caller's rules_toml, missing_file_line_counts = the caller's metrics dict,
    missing_quality_factors = another tool's output). No single next_action is true for all
    three, so there is no row -- same standing as the git_log gap.
"""

from __future__ import annotations

from typing import Callable, NamedTuple


class PassiveChain(NamedTuple):
    """One row: when `tool`'s ok payload satisfies `when`, attach `hint` + `next_action`.

    `trigger` names the payload key the predicate reads. It is not decoration: the guard test
    asserts every trigger is a key the probe really measured, which is what stops a row from
    being written against a field that does not exist.
    """

    tool: str
    trigger: str
    when: Callable
    hint: str
    next_action: str


def _graph_db_missing(payload) -> bool:
    """True only when cbm_graph_status positively reports no graph database.

    The probe showed `result` is a dict of {enabled, db_present, integrity, nodes, edges,
    files} but did not show the VALUES, so this reads the one key whose name fixes its type.
    An absent key, a non-dict `result`, or a truthy non-bool all leave it False -- trap 3.
    """
    inner = payload.get("result")
    if not isinstance(inner, dict) or "db_present" not in inner:
        return False
    return not inner.get("db_present")


def _positive_count(value) -> bool:
    """True only when `value` is a real count above zero.

    Measured by the guard test, not anticipated: `int(value or 0) > 0` raised ValueError on a
    non-numeric string, and apply_passive_chain's own try/except would have swallowed it -- so
    a wrong-shape read would have been indistinguishable from a condition that did not hold.
    That is precisely the failure trap 3 exists to prevent, so the check belongs here rather
    than in the caller's net. A bool is rejected on purpose: True is an int in python, but a
    count arriving as a flag is a wrong shape, not the number 1.
    """
    if isinstance(value, bool) or not isinstance(value, (int, float, str)):
        return False
    try:
        return int(value) > 0
    except (TypeError, ValueError):
        return False


def _inner(payload) -> dict:
    """The cbm family answers under `result`; a non-dict there yields no keys, never a raise.

    Every cbm trigger below is therefore a NESTED key, recorded in the guard test's
    _PROBE_NESTED rather than _PROBE_SHAPES -- the same shape cbm_graph_status already had.
    """
    inner = payload.get("result")
    return inner if isinstance(inner, dict) else {}


def _gate_has_cycles(payload) -> bool:
    """True when cbm_quality_gate measured cycles in the module graph.

    Measured: a valid rules_toml returns `cycles` and `max_depth` beside the checks, while an
    invalid_config payload (rules.py _cfg_error) has NEITHER -- so an absent key is a config
    error, not zero cycles, and _positive_count keeps both of those silent.
    """
    return _positive_count(_inner(payload).get("cycles"))


def _dsm_has_mutual_clusters(payload) -> bool:
    """True when cbm_dsm found an A<->B component: [{files, internal_edges, level}, ...]."""
    clusters = _inner(payload).get("clusters")
    return isinstance(clusters, list) and bool(clusters)


def _dsm_page_is_a_window(payload) -> bool:
    """True when the page stops short of the matrix end on either axis.

    dsm.py builds `page.rows = min(page_rows, size - row_start)`, so `start + span == size` is
    exactly \"this axis is complete\" and needs no threshold guess. A non-int on either side is
    a shape the probe never showed, so that axis is skipped rather than assumed.
    """
    inner = _inner(payload)
    size = inner.get("size")
    page = inner.get("page")
    if isinstance(size, bool) or not isinstance(size, int) or size <= 0:
        return False
    if not isinstance(page, dict):
        return False
    for start_key, span_key in (("row_start", "rows"), ("col_start", "cols")):
        start = page.get(start_key)
        span = page.get(span_key)
        if isinstance(start, bool) or isinstance(span, bool):
            continue
        if not isinstance(start, int) or not isinstance(span, int):
            continue
        if start + span < size:
            return True
    return False


def _evolution_has_coupling(payload) -> bool:
    """True when cbm_evolution returned co-change pairs: [{file_a, file_b, jaccard, ...}, ...]."""
    coupling = _inner(payload).get("coupling")
    return isinstance(coupling, list) and bool(coupling)


def _what_if_applied_nothing(payload) -> bool:
    """True only on the measured ok:true / applied:false shape (status unavailable, invalid_id).

    Strict `is False` on purpose: an EMPTY ops list answers applied:true, and a missing key is
    an unmeasured shape -- neither may fire.
    """
    return _inner(payload).get("applied") is False

#: The passive chain table. First matching row wins, so order inside a tool is precedence.
PASSIVE_CHAINS = (
    # read_file_range is 3478 of the 13406 measured calls -- by far the biggest gap. Two rows,
    # truncation first: a CA that only saw part of a range should finish reading before it
    # writes, and `truncated` is the only completeness key that is not always populated
    # (`next_ranges` came back as a 12-item list on an untruncated 3-line read, so a row keyed
    # on it would fire on nearly every call and permanently hide the write_fix row below).
    PassiveChain(
        "read_file_range", "truncated",
        lambda p: bool(p.get("truncated")),
        "range truncated: next_ranges lists what was not returned",
        # Imperative on purpose: the resolver drops a self-referential answer unless the
        # literal really says call/use/run, so a bare "read_file_range" here would resolve to
        # nothing and the CA would get no ca_next_tool at all.
        "call_read_file_range_on_the_next_range",
    ),
    PassiveChain(
        "read_file_range", "read_token_seeded",
        lambda p: bool(p.get("read_token_seeded")),
        "read_token seeded for this range: write_fix can edit it without a reseed",
        "write_fix",
    ),
    PassiveChain(
        "cache_delta", "total_changed",
        lambda p: _positive_count(p.get("total_changed")),
        "files moved since the cached snapshot: the derived index is behind",
        "refresh_project_map",
    ),
    PassiveChain(
        "query_index", "files",
        lambda p: bool(p.get("files")),
        "matched files listed, contents not read",
        "read_file_range",
    ),
    PassiveChain(
        "cbm_graph_status", "db_present",
        _graph_db_missing,
        "no CBM graph database: graph and coverage tools will answer on nothing",
        "cbm_incremental_index",
    ),
    # A38, probed live 2026-09-19 (script label a38_probe_cbm): the next four rows of the
    # traffic ranking above -- 267 / 263 / 216 / 200 calls -- and every one answered hintless.
    # Each trigger is a key a real call returned, and all of them sit under `result`, because
    # that is where the whole cbm family answers.
    PassiveChain(
        "cbm_quality_gate", "cycles",
        _gate_has_cycles,
        "the gate COUNTED cycles without naming them: max_cycles says how many, not which",
        "cbm_cycle_point",
    ),
    # clusters before page on purpose: _clusters() in orchestrator/graph/dsm.py runs over the
    # whole ordered set, so a mutual-dependency finding is already complete when the page is a
    # window. The read_file_range precedent (completeness first) does NOT transfer here --
    # there truncation hid the very thing the CA read; here only `cells` is paged.
    PassiveChain(
        "cbm_dsm", "clusters",
        _dsm_has_mutual_clusters,
        "mutual-dependency clusters (A<->B): each is a cycle no level ordering can break",
        "cbm_what_if",
    ),
    PassiveChain(
        "cbm_dsm", "page",
        _dsm_page_is_a_window,
        "cells are one <=100x100 window of a larger matrix, not the whole DSM",
        # Imperative, like the read_file_range row: without call/use/run the resolver drops a
        # self-referential answer and the CA gets no ca_next_tool at all.
        "call_cbm_dsm_on_the_next_page",
    ),
    PassiveChain(
        "cbm_evolution", "coupling",
        _evolution_has_coupling,
        "co-change pairs by jaccard: a pair with no static edge is coupling the graph cannot see",
        "cbm_edges_query",
    ),
    PassiveChain(
        "cbm_what_if", "applied",
        _what_if_applied_nothing,
        "ok but nothing was simulated: an op named an id the graph view does not contain",
        "cbm_nodes_query",
    ),
)

#: The tools the table covers, derived -- never a count and never a second hand-written list.
PASSIVE_CHAIN_TOOLS = tuple(sorted({row.tool for row in PASSIVE_CHAINS}))

_PASSIVE_KEYS = ("ca_hint", "ca_next_action")


def _requested(key: str, fields) -> bool:
    """R101: no projection means everything; a projection means only what it asked for."""
    if not fields:
        return True
    return any(str(f).split(".", 1)[0] == key for f in fields)


def apply_passive_chain(tool: str, result, fields=None) -> str:
    """Attach ca_hint / ca_next_action to `result` from the first matching row.

    Returns the trigger that fired, or "" when nothing was written. Success payloads only:
    an error path already carries its own ca_next_action almost everywhere (measured 332
    emission sites), and overwriting a deliberate error message with a generic one would be
    strictly worse than saying nothing.

    A tool that wrote EITHER key keeps both: mixing one author's hint with another author's
    next_action produces advice neither of them meant.
    """
    if not isinstance(result, dict) or result.get("ok") is not True:
        return ""
    if any(result.get(key) for key in _PASSIVE_KEYS):  # truthiness -- trap 1
        return ""
    for row in PASSIVE_CHAINS:
        if row.tool != tool:
            continue
        try:
            if not row.when(result):
                continue
        except Exception:
            continue  # advisory only: a predicate meeting an unexpected shape stays silent
        wrote = False
        if _requested("ca_hint", fields):
            result["ca_hint"] = row.hint
            wrote = True
        if _requested("ca_next_action", fields):
            result["ca_next_action"] = row.next_action
            wrote = True
        return row.trigger if wrote else ""
    return ""
