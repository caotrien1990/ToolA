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
