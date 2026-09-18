"""A34 W4 -- the ACTIVE half of the auto-chain: one requested READ in the same round trip.

The passive half (orchestrator/core/passive_chain.py) closes a hint gap: a tool that
emitted no advice now says what to do next. This module closes the round trip that
advice costs. Both halves are P2c and they are deliberately NOT one mechanism, because
they answer to different rules.

What the rule permits -- read, not assumed
------------------------------------------
examples/CORE_RULE.txt:82 says auto-chains are "CA-level recommendations, NOT automatic
execution", and the same file forbids auto-executing ca_next_action / ca_next_tool /
ca_chain_next "without user go-ahead; present as suggestion". An active chain that fired
by itself -- reading a hint and acting on it -- would be the exact thing that names. So
nothing here ever fires by itself:

  * the CALLER asks, explicitly, per call:  pipeline=["next_range"]
  * every step is a READ. No step may be a mutating tool, and that is not a docstring
    promise: tests/test_a34_active_chain.py resolves each target against
    orchestrator_server._MUTATING_TOOLS (itself derived at :359 from
    ct.PROTECTED_TOOL_NAMES | _TOKEN_REQUIRED_TOOLS) and fails if one ever turns
    mutating -- including a tool that is read-only today and is given a write later.

A read the CA asked for is not an auto-execution, and a read needs no MUTATE gate, so
this is legal under the T1 read-only default and grants no new authority.

Where the bound comes from
--------------------------
[A] already had two active chains before this module, and they set the doctrine:

  orchestrator/graph/tools.py:95  _ensure_graph_fresh -- 5 CBM call sites auto-index a
      stale graph before analysing it. Its own comment at :106 is the rule this module
      obeys: "do NOT start an unbounded index from inside a bounded call." When the
      precondition cannot be met cheaply it REFUSES (_freshness_refusal) instead of
      proceeding on state nobody verified.
  write_fix -- validation + ruff_preview + cache_synced carried in the write response.

Both are a tool completing its OWN contract, which is why neither collides with :82 --
and neither is a caller-chosen fan-out, so neither had to bound one. This one does:
MAX_STEPS_PER_CALL caps the work, and every step reports one of THREE outcomes, because
a step that could not run must never be confusable with a step that ran and found
nothing. That is the same trap the passive half was caught by: a predicate that raised
looked exactly like a condition that did not hold.

The builders take the source tool's OWN output as their input -- read_file_range already
computes next_ranges, search_code already returns hit files and line numbers -- so a step
is never a second guess at what the caller wanted. Nothing here invents a target.
"""

from __future__ import annotations

import os
import time
from typing import Callable, NamedTuple

# The bounded-call rule from _ensure_graph_fresh:106, expressed as a number. A caller
# asking for more gets the first MAX_STEPS_PER_CALL and is TOLD what was dropped:
# silent truncation would read as "everything you asked for ran".
MAX_STEPS_PER_CALL = 3

# The window read around a search hit. Small on purpose: the point is to save a round
# trip on the match, not to ship the file.
_CONTEXT_LINES = 20


class ActiveStep(NamedTuple):
    """One caller-requestable follow-up read.

    source -- the tool whose payload the step chains FROM
    step   -- the name a caller passes in pipeline=[...]
    target -- the [A] tool the step calls; MUST be non-mutating (guard-tested)
    build  -- (source_result, source_args) -> payload dict, or None for "no input here"
    why    -- the round trip this saves, in one line
    """

    source: str
    step: str
    target: str
    build: Callable[[dict, dict], dict | None]
    why: str


def _line(value) -> int:
    """A 1-based line number, or 0 when `value` is not one.

    Written from the defect the passive guard caught rather than from anticipation:
    int("three") raises, and True is an int. A line number arriving as a flag is a
    wrong shape, not line 1.
    """
    if isinstance(value, bool) or not isinstance(value, (int, float, str)):
        return 0
    try:
        number = int(value)
    except (TypeError, ValueError):
        return 0
    return number if number > 0 else 0


def _text(value) -> str:
    """A non-empty string, stripped, or ""."""
    return value.strip() if isinstance(value, str) and value.strip() else ""


def _project(source_result: dict, source_args: dict) -> str:
    """project_path as the SOURCE call resolved it, else what that call was handed.

    Result first: the daemon answers with the path it actually resolved, which is the
    one a follow-up must reuse. Native path spellings differ (a caller may send forward
    slashes and get back a Windows path) and a step that re-resolved the raw argument
    could read a different tree than the call it claims to continue.
    """
    for holder in (source_result, source_args):
        if isinstance(holder, dict):
            found = _text(holder.get("project_path"))
            if found:
                return found
    return ""


def _abs_path(project: str, file_path: str) -> str:
    """An absolute file path, or "" when one cannot be formed.

    get_file_symbols declares exactly ONE parameter -- file_path -- and no project_path
    (measured against the live schema; passing one would be refused as an unsupported
    field). A relative path therefore has nothing to resolve against and must be joined
    here, against the project the SOURCE call resolved.
    """
    if not file_path:
        return ""
    if os.path.isabs(file_path):
        return file_path
    return os.path.join(project, file_path) if project else ""

def _first_hit(source_result: dict):
    """The first search_code match, or None."""
    hits = source_result.get("results")
    if not isinstance(hits, list) or not hits:
        return None
    return hits[0] if isinstance(hits[0], dict) else None


# ─── builders ────────────────────────────────────────────────────────────────────
# Every builder returns None -- never raises, never guesses -- when the source payload
# does not carry what the step needs. "No input" is a normal answer: a line-range read
# has no symbol to find callers for, and that is not a failure of anything.


def _b_next_range(result: dict, args: dict):
    """read_file_range -> read_file_range on the neighbour the same call already ranked."""
    ranges = result.get("next_ranges")
    if not isinstance(ranges, list) or not ranges or not isinstance(ranges[0], dict):
        return None
    top = ranges[0]
    path = _text(top.get("file_path"))
    start = _line(top.get("start_line"))
    end = _line(top.get("end_line"))
    project = _project(result, args)
    if not (path and start and end and project):
        return None
    return {"project_path": project, "file_path": path,
            "start_line": start, "end_line": max(start, end)}


def _b_callers(result: dict, args: dict):
    """read_file_range -> find_callers for the symbol just read.

    Only a symbol-scoped read carries selection.name; a line range answers None.
    """
    selection = result.get("selection")
    name = _text(selection.get("name")) if isinstance(selection, dict) else ""
    project = _project(result, args)
    if not (name and project):
        return None
    return {"project_path": project, "symbol": name}


def _b_file_symbols(result: dict, args: dict):
    """read_file_range -> get_file_symbols for the file just read."""
    path = _abs_path(_project(result, args),
                     _text(result.get("file")) or _text(args.get("file_path")))
    if not path:
        return None
    return {"file_path": path}


def _b_top_hit(result: dict, args: dict):
    """search_code -> read_file_range around the first match."""
    top = _first_hit(result)
    if top is None:
        return None
    path = _text(top.get("file"))
    line = _line(top.get("line"))
    project = _project(result, args)
    if not (path and line and project):
        return None
    return {"project_path": project, "file_path": path,
            "start_line": max(1, line - _CONTEXT_LINES),
            "end_line": line + _CONTEXT_LINES}


def _b_hit_symbols(result: dict, args: dict):
    """search_code -> get_file_symbols for the file holding the first match.

    search_code names a hit file RELATIVE to the project ("orchestrator_server.py") and
    get_file_symbols takes no project_path to resolve it against, so joining here is not
    cosmetic: without it the step ships a path that resolves only by luck of cwd.
    """
    top = _first_hit(result)
    if top is None:
        return None
    path = _abs_path(_project(result, args), _text(top.get("file")))
    if not path:
        return None
    return {"file_path": path}


# ─── the table ───────────────────────────────────────────────────────────────────
# ONE place. A new step is a row here and nothing else: the schema enum, the validator
# and the docs all derive from it, exactly as PASSIVE_CHAINS drives the passive half.

ACTIVE_CHAINS: tuple[ActiveStep, ...] = (
    ActiveStep("read_file_range", "next_range", "read_file_range", _b_next_range,
               "read the top structural neighbour this call already computed"),
    ActiveStep("read_file_range", "callers", "find_callers", _b_callers,
               "who calls the symbol just read (symbol-scoped reads only)"),
    ActiveStep("read_file_range", "file_symbols", "get_file_symbols", _b_file_symbols,
               "the symbol map of the file just read"),
    ActiveStep("search_code", "top_hit", "read_file_range", _b_top_hit,
               "read the window around the first match instead of asking for it"),
    ActiveStep("search_code", "hit_symbols", "get_file_symbols", _b_hit_symbols,
               "the symbol map of the file holding the first match"),
)

# Derived, never hand-listed: source tool -> the step names it accepts.
ACTIVE_CHAIN_STEPS: dict[str, tuple[str, ...]] = {}
for _row in ACTIVE_CHAINS:
    ACTIVE_CHAIN_STEPS[_row.source] = ACTIVE_CHAIN_STEPS.get(_row.source, ()) + (_row.step,)

# Every target the table can reach, for the mutating-tool guard to iterate.
ACTIVE_CHAIN_TARGETS: frozenset = frozenset(row.target for row in ACTIVE_CHAINS)

_BY_KEY: dict = {(row.source, row.step): row for row in ACTIVE_CHAINS}


def resolve_active_pipeline(source_tool: str, requested):
    """Validate a caller's pipeline= against the table.

    Returns (steps, rejected, dropped):
      steps    -- ActiveStep rows to run, in the caller's order, deduplicated
      rejected -- names this source tool does not offer, echoed back for the response
      dropped  -- steps beyond MAX_STEPS_PER_CALL, named so the cap is never silent

    A rejected name is reported, not raised: a caller asking for one good step and one
    typo should get the good step plus a note, not a failed read.
    """
    if isinstance(requested, str):
        # A blank string is ABSENCE, not a bad request: pipeline="" (or a stray "   ")
        # asked for nothing, so it must answer the way None does rather than come back as
        # a rejected step named "''". The guard caught exactly this.
        requested = [requested] if requested.strip() else []
    if not isinstance(requested, (list, tuple)) or not requested:
        return (), (), ()
    allowed = ACTIVE_CHAIN_STEPS.get(source_tool, ())
    steps: list = []
    rejected: list = []
    seen: set = set()
    for item in requested:
        name = _text(item)
        if not name or name not in allowed:
            rejected.append(name or repr(item))
            continue
        if name in seen:
            continue
        seen.add(name)
        steps.append(_BY_KEY[(source_tool, name)])
    dropped = tuple(row.step for row in steps[MAX_STEPS_PER_CALL:])
    return tuple(steps[:MAX_STEPS_PER_CALL]), tuple(rejected), dropped


def build_step_payload(step: ActiveStep, source_result, source_args):
    """Return (payload, reason). reason is "" only when payload is usable.

    THREE outcomes, kept distinct on purpose:
      (payload, "")                  -- run it
      (None, "no_input")             -- the source payload has nothing for this step
      (None, "build_error:<Type>")   -- the builder hit a shape it was not measured on

    Collapsing the last two into silence is the passive half's exact bug: there, a
    predicate that raised was swallowed by the caller's net and became indistinguishable
    from a condition that did not hold. A builder should not raise -- and if one does,
    the response says so instead of quietly shipping one less step than was asked for.
    """
    if not isinstance(source_result, dict):
        return None, "no_input"
    args = source_args if isinstance(source_args, dict) else {}
    try:
        payload = step.build(source_result, args)
    except Exception as exc:  # a builder bug must be visible, never silent
        return None, "build_error:%s" % type(exc).__name__
    if payload is None:
        return None, "no_input"
    return payload, ""


async def execute_active_pipeline(
    source_tool: str,
    requested,
    source_result,
    source_args,
    invoke: Callable,
):
    """Run the steps the CALLER asked for and return one report -- or None if none were.

    `invoke` is INJECTED: an async callable (target_name, payload) -> parsed result. The
    server passes a closure over its own resolver (ct.TOOL_MAP, then json.loads -- the
    idiom measured at orchestrator_server.py:13779/14834/14838); the guard passes a fake.
    So this leaf imports no server module and is testable without a running daemon, for
    the same reason the builders take the source payload instead of reaching for globals.

    Returns None -- not an empty report -- when nothing was requested, so a caller that
    never opted in gets no new key in its response at all. That is a token decision: the
    overwhelming majority of reads will not pass pipeline=, and they must not pay for it.

    Every step reports its OWN outcome, never merged into a count:
      ran      -- executed; its answer is under `result`
      skipped  -- build_step_payload found no input, or a builder raised
      failed   -- invoke raised, or answered something that is not a dict
    A step that could not run and a step that ran and found nothing are different facts.
    And one step's failure never cancels the next: the point of a bounded fan-out is that
    the caller still receives whatever did work.
    """
    allowed, rejected, dropped = resolve_active_pipeline(source_tool, requested)
    if not allowed and not rejected and not dropped:
        return None
    steps: list = []
    for step in allowed:
        payload, reason = build_step_payload(step, source_result, source_args)
        if reason:
            steps.append({
                "step": step.step, "target": step.target, "state": "skipped",
                "reason": reason, "why": step.why,
            })
            continue
        started = time.monotonic()
        try:
            result = await invoke(step.target, payload)
        except Exception as exc:  # one bad step must not cost the caller the others
            steps.append({
                "step": step.step, "target": step.target, "state": "failed",
                "reason": "invoke_error:%s" % type(exc).__name__, "why": step.why,
                "args": payload,
                "duration_ms": int((time.monotonic() - started) * 1000),
            })
            continue
        elapsed = int((time.monotonic() - started) * 1000)
        if not isinstance(result, dict):
            # A target that answered a non-dict broke its contract; that is not a result.
            # Say so instead of passing the raw object through for the CA to guess at.
            steps.append({
                "step": step.step, "target": step.target, "state": "failed",
                "reason": "bad_result:%s" % type(result).__name__, "why": step.why,
                "args": payload, "duration_ms": elapsed,
            })
            continue
        steps.append({
            "step": step.step, "target": step.target, "state": "ran",
            "why": step.why, "args": payload, "duration_ms": elapsed,
            "result": result,
        })
    report = {
        "attempted": [row.step for row in allowed],
        "ran": [row["step"] for row in steps if row["state"] == "ran"],
        "skipped": [row["step"] for row in steps if row["state"] == "skipped"],
        "failed": [row["step"] for row in steps if row["state"] == "failed"],
        "steps": steps,
        "max_steps_per_call": MAX_STEPS_PER_CALL,
    }
    if rejected:
        # Echoed, never raised: one typo next to one good step should cost the typo only.
        report["rejected"] = list(rejected)
        report["offered"] = list(ACTIVE_CHAIN_STEPS.get(source_tool, ()))
    if dropped:
        # Never silent: a dropped step must not read as "everything you asked for ran".
        report["dropped"] = list(dropped)
    return report


PIPELINE_RESPONSE_KEY = "pipeline"


def pipeline_key_requested(fields) -> bool:
    """Whether an R101 `fields=` projection still wants the `pipeline` key.

    Lives here, not at the funnel, so the rule is testable without an HTTP request -- the same
    reason annotate_next_tool owns its own copy of this decision. And it is asked BEFORE any
    step runs, never after: a report the caller projected away must cost zero reads, not three
    reads whose answers are then thrown out. That is the whole point of a token projection.

    No projection at all (None, or an empty set: no fields= was sent) means emit. An explicit
    projection means emit only if it names this key -- `pipeline`, or any dotted spec rooted at
    it, since R101 projects inside a key as well as selecting it.
    """
    if not fields:
        return True
    try:
        specs = list(fields)
    except TypeError:  # not iterable: treat as no usable projection rather than guessing
        return True
    return any(str(spec).split(".")[0] == PIPELINE_RESPONSE_KEY for spec in specs)
