"""Orphan-proof result cache for idempotent read-only tool calls (A41 tier 1b/3a).

THE DEFECT THIS EXISTS FOR (measured 2026-09-21 13:30): a tool completes, the
server picks HTTP 200, writes ok=true to USAGE_LOG -- and ships 0 bytes to a
socket the CA already closed. Access log signature `"POST /api/call/<tool>" 200 0`.
The CA paid the wall clock, [A] paid the CPU, and the answer was thrown away.
Five such calls of 22-133s each were measured inside one window with not one
ERROR or WARNING line to reconcile them.

The unstated inequality behind it is `server budget < client timeout < CA
patience`, and nothing in [A] declared or pinned it. Tier 1a pins the budget.
This module makes the SECOND attempt nearly free instead of building a ledger:
read-only tools are idempotent, so the same payload may be answered from the
result we already computed.

WHITELIST, NEVER BLACKLIST (Anh's hard rule, applied to a new mechanism).
`code_tools.tool_mutation_scope` defaults to 'none' for any tool ABSENT from its
map, so "cache everything whose scope is none" would silently cache a mutating
tool the map has not caught up with yet. Only the names in CACHEABLE_TOOLS are
ever stored, and each one is there because it returns a REPORT: re-answering it
from a bounded-age cache cannot corrupt anything. `read_file_range` is
deliberately NOT here -- it seeds the write_fix read_token, and a cached seed
would hand out stale write authority.

IN-MEMORY BY DESIGN. An orphan matters for the minutes around one CA turn, so a
process dict needs no eviction cron, no second file to drift, and a restart
legitimately forgets. The duration observatory is the one thing persisted,
because a floor that resets to "unknown" on every restart cannot gate anything.
"""
import atexit
import hashlib
import json
import os
import tempfile
import threading
import time
from pathlib import Path

# Report-producing reads. Every entry answers a question; none hands out authority.
CACHEABLE_TOOLS = frozenset({
    "cbm_get_architecture", "cbm_dsm", "cbm_trace_path",
    "cbm_nodes_query", "cbm_edges_query", "cbm_what_if", "cbm_dead_code",
    "cbm_test_gap",
    "ocr_map", "ocr_scan", "ocr_review",
    "search_code", "find_symbol",
})
# cbm_graph_status is DELIBERATELY absent (P11, measured 2026-10-02): it is a
# freshness PROBE -- its whole purpose is to measure the project's state NOW.
# Caching it answered stale_files:17 for 54s after cbm_incremental_index had
# already taken the project to 0, i.e. the probe reported the past as the
# present. A probe that can be served from cache contradicts itself, so it is
# never stored. (It also stays in BUDGETED_TOOLS below -- that is a server wall
# clock, unrelated to cacheability.)

# Short enough that a cached report cannot be mistaken for a fresh one, long
# enough to cover "CA gave up, thought about it, and called again".
CALL_CACHE_TTL_S = float(os.environ.get("ORCHESTRATOR_CALL_CACHE_TTL_S", "300"))
CALL_CACHE_MAX_ENTRIES = int(os.environ.get("ORCHESTRATOR_CALL_CACHE_MAX", "64"))
# A cached report is only worth keeping if computing it hurt. Below this the
# retry is cheaper than the bookkeeping.
CALL_CACHE_MIN_MS = int(os.environ.get("ORCHESTRATOR_CALL_CACHE_MIN_MS", "1500"))

# Transport metadata and field projections are NOT part of the question asked, so
# two calls that differ only here must share one cache entry.
_SIGNATURE_IGNORED_KEYS = frozenset({
    "fields", "agent_id", "session_id", "task_epoch", "prompt_hash", "generation",
    "operation_id", "read_token", "expected_generation",
})

_LOCK = threading.Lock()
# key -> {tool, project_path, agent_id, result, stored_at, duration_ms,
#         undelivered, advised, generation, witness}
_ENTRIES: dict[str, dict] = {}

# ── P11 freshness: an entry is valid only while the STATE it was computed from ──
# is unchanged. Change reaches the cache two ways, so the check has two halves.
#
# HALF 1 -- generation (change THROUGH [A]). The dispatcher calls
# `note_successful_mutation` after every successful mutating call; each one bumps
# a per-project counter. `store` records the counter alongside the answer;
# `lookup` refuses the entry the moment the counter has moved. A mutation in
# project B bumps only B's counter, so project A's paid-for answers survive.
#
# HALF 2 -- disk witness (change OUTSIDE [A]). A native edit, a git checkout, or
# another process (the ToolQ cron reindexing) never touches the counter, so the
# entry ALSO records a cheap O(1) stat of the artifact its answer was computed
# from: the published search-snapshot id / INDEX for search_code+find_symbol, the
# graph.db file for cbm_*. ocr_* and an unresolvable root record NO witness and
# fall back to the pre-P11 TTL-only behaviour -- a whitelist must never evict an
# answer it cannot check.
#
# Cost contract: each witness is a stat / one tiny pointer read (<5ms), far under
# the CALL_CACHE_MIN_MS an entry had to cost to be stored at all.
_STATE_LOCK = threading.Lock()
# resolved project root (casefolded) -> monotonically increasing mutation count
_PROJECT_GENERATION: dict[str, int] = {}
# tools that do not read project disk state get no witness (TTL-only, pre-P11)
_WITNESS_NONE_TOOLS = frozenset({"ocr_map", "ocr_scan", "ocr_review"})
# git worktree writers: their targets may sit outside the resolved root (a
# git -C call names no file_path), so they bump the broad root too.
_GIT_WRITER_TOOLS = frozenset({
    "git_stage", "git_unstage", "git_commit", "git_checkout", "git_revert",
    "git_reset", "git_stash", "git_stash_pop", "git_branch_create",
    "git_branch_delete", "git_merge", "git_pull",
})


def _normalize_root(project_path) -> str:
    """One canonical key for a project root: resolved + casefolded.

    The dispatcher passes the SAME body['project_path'] to store and to
    note_successful_mutation, and the snapshot/graph paths are derived from it --
    so case/separator/spelling variants (D:/x vs D:\\x) must land on ONE bucket
    or a write under one spelling would not retire an entry stored under the
    other. One helper, used by every read AND write of the generation map.
    """
    text = str(project_path or "").strip().strip('"')
    if not text:
        return ""
    try:
        return os.path.normcase(str(Path(text).resolve()))
    except Exception:
        # UNC / undriveable path: resolve() may block or raise; fall back to a
        # normalised-but-unresolved key rather than fail the call that cached.
        return os.path.normcase(text.replace("\\", "/"))


def note_successful_mutation(tool_name: str, project_path: str = "") -> None:
    """Bump the per-project generation after a SUCCESSFUL mutating call.

    Called by the dispatcher from its `finally`, only when the call did not fail
    and carried a resolvable project_path. A no-project mutation (a UI click, a
    session rename) has no disk-state cache to invalidate and bumps nothing. A
    git writer bumps BOTH the resolved root and the raw body path, because its
    target tree may sit outside the resolved root.
    """
    roots = set()
    resolved = _normalize_root(project_path)
    if resolved:
        roots.add(resolved)
    if str(tool_name) in _GIT_WRITER_TOOLS:
        raw = os.path.normcase(str(project_path or "").strip())
        if raw:
            roots.add(raw)
    if not roots:
        return
    with _STATE_LOCK:
        for root in roots:
            _PROJECT_GENERATION[root] = _PROJECT_GENERATION.get(root, 0) + 1


def _current_generation(project_path: str) -> int:
    root = _normalize_root(project_path)
    if not root:
        return 0
    with _STATE_LOCK:
        return _PROJECT_GENERATION.get(root, 0)


def _disk_witness(tool: str, project_path: str):
    """A cheap fingerprint of the artifact `tool`'s answer was computed from.

    Returns a JSON-able value compared by equality at lookup, or None when this
    tool has no disk witness (then the generation + TTL alone govern). Never
    raises: an unreadable witness is simply "unknown", and unknown must not
    evict an answer the generation still vouches for.

    APPEARANCE IS NOT DRIFT. A cached 0-hit answer ("no db", "no index") is
    CORRECT at the moment it is computed; the artifact coming into existence a
    moment later does not make that answer a lie about the past, and evicting on
    appearance would churn the cache on every first index. So an artifact that
    was ABSENT at store contributes a stable sentinel that matches only the same
    absence at lookup -- only a change to an artifact that EXISTED at store
    retires the entry.
    """
    tool = str(tool)
    if tool in _WITNESS_NONE_TOOLS:
        return None
    root = str(project_path or "").strip()
    if not root:
        return None
    try:
        if tool in ("search_code", "find_symbol"):
            # The published snapshot id IS the tree search_code serves. It moves
            # on every publish (write pipeline, cron), so it catches an external
            # re-snapshot without any [A] call. INDEX.json backs it up only once
            # it EXISTS -- an INDEX appearing under an unchanged snapshot is not
            # drift in the served tree.
            from orchestrator import search_snapshots as _ss
            snap = _ss.current_snapshot_id(root) or ""
            index = Path(root) / ".orchestrator_cache" / "INDEX.json"
            try:
                ist = index.stat()
                index_sig = (ist.st_mtime_ns, ist.st_size)
            except OSError:
                index_sig = "absent"
            return {"snap": snap, "index": index_sig}
        if tool.startswith("cbm_"):
            # Every cbm_* answer is a view over graph.db; a reindex (in- or
            # out-of-process) rewrites that file, so (mtime_ns, size) is the
            # witness. A mid-write WAL would make this over-invalidate -- safe
            # direction: it recomputes, never serves stale. A db that was absent
            # at store yields "absent", so the FIRST index does not churn a
            # cached "not indexed" answer; only a rewrite of an existing db does.
            from orchestrator.graph.config import graph_db_path as _gdp
            db = Path(_gdp(root))
            try:
                st = db.stat()
            except OSError:
                return {"db": "absent"}
            return {"db": (st.st_mtime_ns, st.st_size)}
    except Exception:
        return None
    return None

_COST_LOCK = threading.Lock()
_COST: dict[str, dict] = {}
_COST_LOADED = False
_COST_MIN_SAMPLES = int(os.environ.get("ORCHESTRATOR_COST_MIN_SAMPLES", "3"))
_COST_MAX_SAMPLES = 25
# The observatory is telemetry, so it is persisted on a CLOCK, never once per sample.
# `_cost_save_locked` carries the measured reason this is not 0.
_COST_SAVE_MIN_INTERVAL_S = float(os.environ.get("ORCHESTRATOR_COST_SAVE_INTERVAL_S", "30"))
_COST_LAST_SAVE = 0.0
_COST_DIRTY = False


def _norm(value):
    """Order-independent, type-stable rendering of one param value."""
    if isinstance(value, dict):
        return {str(k): _norm(v) for k, v in sorted(value.items(), key=lambda kv: str(kv[0]))}
    if isinstance(value, (list, tuple)):
        return [_norm(v) for v in value]
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    return str(value)


def call_signature(tool: str, body: dict) -> str:
    """Stable key for (tool, question asked). Values are hashed, never stored."""
    payload = {}
    for key, value in (body or {}).items():
        skey = str(key)
        if skey in _SIGNATURE_IGNORED_KEYS:
            continue
        payload[skey] = _norm(value)
    blob = json.dumps({"tool": str(tool), "params": payload},
                      ensure_ascii=False, sort_keys=True)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()[:16]


def is_cacheable(tool: str) -> bool:
    return str(tool) in CACHEABLE_TOOLS


def _prune_locked(now: float) -> None:
    dead = [k for k, e in _ENTRIES.items() if now - e["stored_at"] > CALL_CACHE_TTL_S]
    for k in dead:
        _ENTRIES.pop(k, None)
    if len(_ENTRIES) > CALL_CACHE_MAX_ENTRIES:
        for k, _e in sorted(_ENTRIES.items(), key=lambda kv: kv[1]["stored_at"])[
                :len(_ENTRIES) - CALL_CACHE_MAX_ENTRIES]:
            _ENTRIES.pop(k, None)


def store(tool: str, body: dict, result, duration_ms: int,
          project_path: str = "", agent_id: str = "") -> str:
    """Remember one completed read-only answer. Returns the key, or '' if skipped.

    `result` is the TOOL's own answer -- a dict, or the JSON text the tool returned,
    which is parsed only once the cheap gates pass. Never the dispatcher's decorated
    view: a hit re-enters the dispatcher, which stamps the per-call metadata again
    for the new caller, and a stored stamp would outlive the call it described.
    """
    if not is_cacheable(tool) or result is None:
        return ""
    if int(duration_ms or 0) < CALL_CACHE_MIN_MS:
        return ""
    if isinstance(result, (str, bytes, bytearray)):
        try:
            result = json.loads(result)
        except (TypeError, ValueError):
            return ""  # not a JSON answer: there is nothing a hit could replay
    if not isinstance(result, dict):
        return ""
    if result.get("ok") is False:
        return ""  # never serve a failure twice; the cause may already be gone
    key = call_signature(tool, body)
    now = time.time()
    # P11: pin the answer to the state it was computed from -- the per-project
    # mutation counter (in-[A] change) and the disk witness (out-of-[A] change).
    generation = _current_generation(project_path)
    witness = _disk_witness(tool, project_path)
    with _LOCK:
        _ENTRIES[key] = {
            "tool": str(tool),
            "project_path": str(project_path or ""),
            "agent_id": str(agent_id or ""),
            "result": result,
            "stored_at": now,
            "duration_ms": int(duration_ms or 0),
            "undelivered": False,
            "advised": False,
            "generation": generation,
            "witness": witness,
        }
        _prune_locked(now)
    return key


def lookup(tool: str, body: dict) -> dict | None:
    """Return a live entry for this exact question, or None.

    P11: "live" now means more than "young". The entry must also still describe
    the state it was computed from: the project generation must not have moved
    (an [A] mutation) and the disk witness must still match (an outside change).
    Either failing retires the entry, so a stale answer is never served.
    """
    if not is_cacheable(tool):
        return None
    key = call_signature(tool, body)
    now = time.time()
    project_path = str((body or {}).get("project_path", "") or "")
    with _LOCK:
        entry = _ENTRIES.get(key)
        if entry is None:
            return None
        age = now - entry["stored_at"]
        if age > CALL_CACHE_TTL_S:
            _ENTRIES.pop(key, None)
            return None
        # P11 freshness gate 1: an [A] mutation bumped this project's generation.
        if entry.get("generation", 0) != _current_generation(project_path):
            _ENTRIES.pop(key, None)
            return None
        # P11 freshness gate 2: the disk artifact the answer was computed from
        # moved (native edit, git checkout, another process). A None witness on
        # either side means "cannot check" -- never evict on an unknown.
        stored_witness = entry.get("witness")
        current_witness = _disk_witness(tool, project_path)
        if (stored_witness is not None and current_witness is not None
                and current_witness != stored_witness):
            _ENTRIES.pop(key, None)
            return None
        # Serving it discharges the advisory: the CA is holding the answer now.
        entry["advised"] = True
        entry["undelivered"] = False
        return {
            "key": key,
            "result": entry["result"],
            "age_s": round(age, 3),
            "duration_ms": entry["duration_ms"],
            "tool": entry["tool"],
        }


# ── Single-flight (A41 tier 1c, measured 2026-09-22) ──────────────────────────────
#
# MEASURED DEFECT: a CA abandoned cbm_dead_code at 3s; a second caller sent the
# IDENTICAL payload while that computation was STILL RUNNING and paid a full
# 7227ms recompute. `lookup` could not help -- nothing is stored until a call
# finishes -- so two identical expensive questions burned the CPU twice and no
# counter anywhere showed it. Neither call was wrong; the waste was invisible.
#
# Whitelist INHERITED, not widened: only CACHEABLE_TOOLS may join, so a joiner can
# only ever receive a report. Four of those names are T2 `mutating` tools whose
# mutation_scope is `derived_index`; joining them is still correct because the
# mutation is the index refresh the leader already performed -- the same reasoning
# `store` relies on, written down here so the next reader need not re-derive it
# (the dae3f265 lesson: never claim "read-only graph reads only" about this set).
#
# Polled, not evented, ON PURPOSE. The dispatcher is async: a threading.Event
# .wait() there would block the event loop for the whole wait and freeze every
# other CA, and moving it to an executor would burn a pool thread per joiner. The
# sync idiom in code_tools.py:3084 (_MATRIX_AUDIT_OPEN_SESSION_INFLIGHT) is right
# for threads and wrong here, so this keeps only the claim dict and lets the
# caller await between checks.
_INFLIGHT_LOCK = threading.Lock()
_INFLIGHT: dict[str, float] = {}  # key -> time.time() the leader claimed it
_JOINS_WAITED = 0


def begin(tool: str, body: dict) -> tuple[bool, str]:
    """Claim this question, or report that an identical one is already in flight.

    Returns (is_leader, key). is_leader=True: compute it, then call `finish(key)`
    AFTER storing the result. is_leader=False: an identical payload is mid-flight;
    poll `is_inflight(key)`, then `lookup` it. A non-cacheable tool never joins.
    """
    global _JOINS_WAITED
    if not is_cacheable(tool):
        return True, ""
    key = call_signature(tool, body)
    now = time.time()
    with _INFLIGHT_LOCK:
        started = _INFLIGHT.get(key)
        if started is not None and now - started <= CALL_SINGLEFLIGHT_WAIT_S:
            _JOINS_WAITED += 1
            return False, key
        # Absent, or a claim older than any leader is allowed to live: a leader that
        # died without releasing must not wedge this key until the process restarts.
        _INFLIGHT[key] = now
        return True, key


def is_inflight(key: str) -> bool:
    if not key:
        return False
    with _INFLIGHT_LOCK:
        return key in _INFLIGHT


def finish(key: str) -> None:
    """Release joiners. MUST run AFTER `store`, or a joiner wakes to a cache miss
    and recomputes the very work it waited for."""
    if not key:
        return
    with _INFLIGHT_LOCK:
        _INFLIGHT.pop(key, None)


def mark_undelivered(key: str) -> bool:
    """Flag a stored answer whose client socket was already gone (tier 3a)."""
    if not key:
        return False
    with _LOCK:
        entry = _ENTRIES.get(key)
        if entry is None:
            return False
        entry["undelivered"] = True
        entry["advised"] = False
        return True


def pending_advisory(agent_id: str = "", project_path: str = "",
                     consume: bool = True) -> dict | None:
    """Short note for the NEXT call: an answer you abandoned is already cached.

    A PROJECTION of the cache, never a second queue -- so it cannot outlive the
    entry it describes and cannot drift from it. Measured constraint: 0 of 1000
    USAGE_LOG rows carried an agent_id, so identity is frequently unknown and the
    match falls back to project scope. `matched_by` states which happened, because
    a project-scope match may describe a DIFFERENT CA's abandoned call -- still a
    real, idempotent, correctly-keyed entry, just possibly not yours.

    Consumed once (`advised`), so it costs tokens on ONE call after an orphan
    rather than on every call forever.
    """
    now = time.time()
    want_agent = str(agent_id or "")
    want_project = str(project_path or "")
    with _LOCK:
        best = None
        for key, entry in _ENTRIES.items():
            if not entry["undelivered"] or entry["advised"]:
                continue
            if now - entry["stored_at"] > CALL_CACHE_TTL_S:
                continue
            if want_agent and entry["agent_id"]:
                matched_by = "agent_id" if entry["agent_id"] == want_agent else ""
            elif want_project and entry["project_path"]:
                matched_by = ("project_path"
                              if os.path.normcase(entry["project_path"])
                              == os.path.normcase(want_project) else "")
            else:
                matched_by = "daemon_scope"
            if not matched_by:
                continue
            if best is None or entry["stored_at"] > best[1]["stored_at"]:
                best = (key, entry, matched_by)
        if best is None:
            return None
        key, entry, matched_by = best
        if consume:
            entry["advised"] = True
        return {
            "tool": entry["tool"],
            "cache_key": key,
            "age_s": round(now - entry["stored_at"], 3),
            "cost_ms": entry["duration_ms"],
            "ttl_s": CALL_CACHE_TTL_S,
            "matched_by": matched_by,
            "ca_next_action": "resend_the_same_payload_to_read_it_from_cache",
            "note": ("%s finished %.0fs ago but the answer reached a closed socket, "
                     "so it was never delivered. Resending the SAME payload returns "
                     "it from cache; widening the scope or raising a timeout would "
                     "recompute it instead."
                     % (entry["tool"], now - entry["stored_at"])),
        }


def cache_status() -> dict:
    now = time.time()
    # Snapshot the single-flight view BEFORE taking _LOCK. Nesting the two locks
    # would create a second lock order for no gain -- the A32 deadlock came from
    # exactly that kind of nesting, and a status call is the wrong place to risk it.
    with _INFLIGHT_LOCK:
        inflight = len(_INFLIGHT)
        joins = _JOINS_WAITED
    with _LOCK:
        return {
            "entries": len(_ENTRIES),
            "undelivered": sum(1 for e in _ENTRIES.values() if e["undelivered"]),
            "inflight": inflight,
            "joins_waited": joins,
            "joins_waited_semantics": (
                "callers that DECIDED to wait for an identical in-flight payload -- "
                "not joins that were then served: a leader failing, or finishing "
                "under min_ms, leaves its joiner to compute anyway"),
            "singleflight_wait_s": CALL_SINGLEFLIGHT_WAIT_S,
            "ttl_s": CALL_CACHE_TTL_S,
            "max_entries": CALL_CACHE_MAX_ENTRIES,
            "min_ms": CALL_CACHE_MIN_MS,
            "oldest_age_s": (round(now - min(e["stored_at"] for e in _ENTRIES.values()), 1)
                             if _ENTRIES else None),
            "tools": sorted(CACHEABLE_TOOLS),
        }


def reset_for_tests() -> None:
    """Clear BOTH stores, including the persisted one.

    `_COST_LOADED = True` with an empty `_COST` is not a hack, it is the only way
    to mean "loaded, and there is nothing": leaving the flag False makes the very
    next `measured_cost()` call re-read `CALL_COST.json` from disk and hand back
    the samples this function was asked to forget. Measured on the first run of the
    A41 probe: a test that reset the cache and asserted "no floor without samples"
    FAILED, because the three samples it had written seconds earlier came back off
    disk. That is the correct behaviour for the daemon (a floor must survive a
    restart) and the wrong behaviour for a reset, so the reset is what changes.
    """
    global _COST_LOADED, _COST_DIRTY, _COST_LAST_SAVE, _JOINS_WAITED
    with _LOCK:
        _ENTRIES.clear()
    # Single-flight state is part of "BOTH stores" too: a claim left behind by one
    # test makes the NEXT test a joiner that waits CALL_SINGLEFLIGHT_WAIT_S on a
    # leader that no longer exists, which reads as a hang, not as leaked state.
    with _INFLIGHT_LOCK:
        _INFLIGHT.clear()
        _JOINS_WAITED = 0
    # P11: the freshness halves are process-global too. A leaked generation or a
    # witness pinned to one test's temp dir would make a LATER test's identical
    # payload miss for the wrong reason -- the same "leaked state reads as a hang"
    # class the single-flight clear above already names.
    with _STATE_LOCK:
        _PROJECT_GENERATION.clear()
    with _COST_LOCK:
        _COST.clear()
        _COST_LOADED = True
        _COST_DIRTY = False
        _COST_LAST_SAVE = 0.0


# ── Duration observatory (tier 2a): a floor [A] MEASURED, not a number in prose ──

def _cost_path() -> Path:
    """Where the measured floors live. Env-overridable so a test can aim elsewhere.

    Without the override a probe writes real samples into [A]'s own cache file and
    they persist after the test process dies -- pollution that survives the thing
    that made it. The default is unchanged, so the daemon is unaffected.
    """
    override = os.environ.get("ORCHESTRATOR_CALL_COST_PATH", "").strip()
    if override:
        return Path(override)
    here = Path(__file__).resolve().parents[2]
    return here / ".orchestrator_cache" / "CALL_COST.json"


def _cost_key_is_junk(key: str) -> bool:
    """True for a bucket no future call can ever match, so keeping it is pure leak.

    MEASURED 2026-09-21 on the live CALL_COST.json: 3 of 4 buckets were unmatchable --
    `__fast_accounting_probe__|` (25 samples from an internal probe, not a tool any CA
    can call) and two `backup_project|` buckets whose project_path pointed inside a
    test's TemporaryDirectory (OS temp dir, .../tmp*/proj_a), a directory already
    deleted by the time the samples were flushed. `observe()` recorded whatever it was
    handed, and nothing ever bounded the NUMBER of buckets (_COST_MAX_SAMPLES bounds
    samples WITHIN one), so the file could only grow and every campaign ended with a
    hand purge. Per truy goc, khong va ngon: refuse at the producer AND self-heal on
    load, so the garbage cannot come back.

    What is NOT junk, deliberately: a key with an EMPTY project_path. The reason is
    narrower -- and far more useful to the next reader -- than "a real call may carry
    none", which is what this text claimed until 2026-09-22. MEASURED: `search_code`
    resolves its root as `path or project_path` (orchestrator/tools/search.py:1165,
    :903, :1011, :1068, :1305), so `path` is a schema-declared, fully working way to
    name the project -- while the dispatcher's store/observe sites read ONLY
    body["project_path"] (orchestrator_server.py:15877, :15886). The live
    `search_code|` bucket therefore collects calls that DID name a project, through
    the documented legacy alias.
    NOT patched, and that is a decision rather than an oversight: `budget_for` is flat
    and cost-independent, so no gate decision is ever wrong; producer and consumer
    (budget_exceeded_payload -> measured_cost) read the SAME expression, so the bucket
    is self-consistent; the only consequence is an advisory `measured_cost` figure
    averaged over a mixed bucket. Every available fix measures worse: bucketing on the
    raw `path` breaks on the tools where it is a FILE path (ruff_fix, ocr_scan), which
    fragments the bucket per file and makes THIS function prune real data, and
    resolving a real root costs a find_project_root filesystem walk on every observed
    call to improve one advisory number. Dropping the bucket would silently disable a
    working floor. So the rule names what it removes and removes nothing else.
    """
    tool, _, path = str(key).partition("|")
    if len(tool) > 4 and tool.startswith("__") and tool.endswith("__"):
        return True
    if not path:
        return False
    tmp = os.path.normcase(str(tempfile.gettempdir() or "")).rstrip("\\/")
    if not tmp:
        return False
    return path == tmp or path.startswith(tmp + os.sep) or path.startswith(tmp + "/")


def _cost_load_locked() -> None:
    global _COST_LOADED
    if _COST_LOADED:
        return
    _COST_LOADED = True
    try:
        raw = _cost_path().read_text(encoding="utf-8")
        data = json.loads(raw)
        if isinstance(data, dict):
            for k, v in data.items():
                if not isinstance(v, dict) or not isinstance(v.get("samples"), list):
                    continue
                if _cost_key_is_junk(str(k)):
                    continue  # self-heal: junk written before the guard existed
                _COST[str(k)] = {"samples": [float(s) for s in v["samples"]][-_COST_MAX_SAMPLES:]}
    except Exception:
        pass  # no history yet is a normal state, never an error


def _cost_write_locked() -> None:
    path = _cost_path()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        blob = json.dumps(_COST, ensure_ascii=False, sort_keys=True).encode("utf-8")
        fd, tmp = tempfile.mkstemp(prefix=".CALL_COST.", suffix=".tmp", dir=str(path.parent))
        try:
            with os.fdopen(fd, "wb") as f:
                f.write(blob)
                f.flush()
                os.fsync(f.fileno())
            os.replace(tmp, str(path))
        except Exception:
            try:
                os.unlink(tmp)
            except Exception:
                pass
            raise
    except Exception:
        pass  # telemetry must never fail a good call


def _cost_save_locked(force: bool = False) -> None:
    """Persist the observatory, but NOT once per observation.

    MEASURED REGRESSION, 2026-09-21: the first version of this module fsync'd the
    file on every single successful dispatch. `tests/test_post_call_accounting.py`
    fires 50 dispatches and they went from ~47ms to ~2.3s -- one mkstemp + fsync +
    os.replace each, on the event loop thread, on a D: drive that is an HDD. The test
    reported it as "accounting blocked the event loop"; the actual defect was a
    synchronous disk write added to the hot path of EVERY tool call, forever, to
    record telemetry that nothing reads until the next call.

    So the write is debounced by wall clock, with ONE exception: the sample that
    first makes a bucket enforceable is written immediately. A floor that exists in
    RAM but dies with the process would make the pre-flight gate appear and vanish
    across restarts, and a gate that fires only sometimes is worse than no gate.
    """
    global _COST_DIRTY, _COST_LAST_SAVE
    _COST_DIRTY = True
    now = time.time()
    if not force and (now - _COST_LAST_SAVE) < _COST_SAVE_MIN_INTERVAL_S:
        return
    _cost_write_locked()
    _COST_DIRTY = False
    _COST_LAST_SAVE = now


def _cost_key(tool: str, project_path: str) -> str:
    return "%s|%s" % (str(tool), os.path.normcase(str(project_path or "")))


def observe(tool: str, project_path: str, duration_s: float) -> None:
    """Record one SUCCESSFUL wall clock so a later call can be floored by it."""
    try:
        seconds = float(duration_s)
    except (TypeError, ValueError):
        return
    if seconds <= 0:
        return
    key = _cost_key(tool, project_path)
    if _cost_key_is_junk(key):
        return  # never floors anything; recording it only grows the file
    with _COST_LOCK:
        _cost_load_locked()
        bucket = _COST.setdefault(key, {"samples": []})
        bucket["samples"].append(round(seconds, 3))
        bucket["samples"] = bucket["samples"][-_COST_MAX_SAMPLES:]
        # Force the write only on the sample that turns this bucket into a usable
        # floor; every other sample rides the clock.
        _cost_save_locked(force=len(bucket["samples"]) == _COST_MIN_SAMPLES)


def measured_cost(tool: str, project_path: str) -> dict | None:
    """What this tool has actually cost on THIS project, or None if never measured."""
    key = _cost_key(tool, project_path)
    with _COST_LOCK:
        _cost_load_locked()
        bucket = _COST.get(key)
        samples = sorted(bucket["samples"]) if bucket else []
    if len(samples) < _COST_MIN_SAMPLES:
        return None
    idx = min(len(samples) - 1, int(round(0.95 * (len(samples) - 1))))
    return {
        "samples": len(samples),
        "min_s": samples[0],
        "median_s": samples[len(samples) // 2],
        "p95_s": samples[idx],
        "max_s": samples[-1],
    }


def flush_cost_observatory() -> bool:
    """Write any debounced samples now. Registered at exit; also callable by a test.

    Without this a restart would recompute the floor from whatever happened to reach
    disk before the debounce window closed -- and FEWER samples means `p95 == max_s`
    (the arithmetic in `measured_cost` above), i.e. a STRICTER floor than the one the
    daemon was enforcing a second earlier. The pre-flight gate would then refuse a
    timeout it had just accepted, which is exactly the flapping the forced write on
    the third sample exists to prevent.

    Returns True only when something was actually written, so a caller can tell
    "flushed" from "nothing was pending".
    """
    global _COST_DIRTY
    with _COST_LOCK:
        if not _COST_DIRTY:
            return False
        _cost_write_locked()
        _COST_DIRTY = False
    return True


atexit.register(flush_cost_observatory)

# ── Server budget (tier 1a): a ceiling BELOW the client timeout, or it means nothing ──
#
# The orphan exists because of an inequality nothing in [A] ever declared:
#   server budget < client timeout < CA patience
# Violating the first `<` is what produces a completed-but-undelivered answer.
# Measured 2026-09-21: search_code warm on ToolQ took 25.33s against a ~30s client
# timeout, and the `SEARCH_TEXT_DEADLINE_SECONDS = 60.0` bound shipped in 23116d3a is
# LARGER than that timeout -- so it can only fire after the client has already gone.
# A bound above the client timeout is not a bound; it is a log line. 25s leaves ~5s of
# headroom for serialisation and the response trip on a 30s client.
CALL_BUDGET_S = float(os.environ.get("ORCHESTRATOR_CALL_BUDGET_S", "25"))

# Tier 1c ceiling for a JOINER (see `begin`). DERIVED from the budget, never set
# independently: tier 1a guarantees a budgeted leader answers within CALL_BUDGET_S,
# so a joiner that waited longer could only be waiting on a leader already gone.
# An unbudgeted cacheable leader (`ocr_*`, `search_code`) is capped by the same
# number for the same reason -- a joiner must never outlive the answer it wants.
CALL_SINGLEFLIGHT_WAIT_S = float(
    os.environ.get("ORCHESTRATOR_SINGLEFLIGHT_WAIT_S", str(CALL_BUDGET_S)))

# The cbm graph surface. NOT "read-only", despite what this comment used to claim:
# `cbm_dead_code` and `cbm_test_gap` are both declared T2 / mutating:true with
# mutation_scope="derived_index", and cbm_dead_code calls _ensure_graph_fresh
# (orchestrator/graph/tools.py), so a REINDEX can run inside a ceiling sized for a
# read. That is deliberate -- the budget guards the CALL, not the purity of the tool
# -- but it means 25s must cover reindex + both scanners, which is exactly how the
# DEFAULT cbm_dead_code(scope="full") came to exceed it: measured 2026-09-22,
# ok:false / SERVER_BUDGET_EXCEEDED at elapsed_s 25.017, of which 66.1s was vulture
# walking venv312/ (fixed in 092c5be5: 5.5s now, so the ceiling holds again with
# headroom). Leave the number alone without a measurement -- raising a budget to fit
# a scan that should not have been running is the vá-ngọn B029c already tried.
# `ocr_*` is deliberately absent: it MEASURES 23-42s on real work, so a 25s ceiling
# would break a tool that is behaving correctly -- for those the answer is the
# measured-floor gate on the caller's own `timeout`, not a server cap.
BUDGETED_TOOLS = frozenset({
    "cbm_graph_status", "cbm_get_architecture", "cbm_dsm", "cbm_trace_path",
    "cbm_nodes_query", "cbm_edges_query", "cbm_what_if", "cbm_dead_code",
    "cbm_test_gap",
})


def budget_for(tool: str) -> float | None:
    """Wall-clock ceiling for this tool, or None when it is not budgeted here."""
    return CALL_BUDGET_S if str(tool) in BUDGETED_TOOLS else None


def budget_exceeded_payload(tool: str, budget_s: float, elapsed_s: float,
                            project_path: str = "") -> dict:
    """Tier 1c + 2b: an honest partial, and a name that says WHO stopped it.

    2b: three situations used to arrive at a CA as one word, "timeout".
    SERVER_BUDGET_EXCEEDED means [A] chose to stop -- it is not the caller's
    parameter being wrong (CALLER_TIMEOUT_TOO_LOW) and not the client abandoning a
    finished answer (an orphan, which by definition returns no payload at all).

    2c: the remedy is never "widen the scope" or "raise the timeout blindly". Both
    recompute the same work that just failed to fit. REPLACE_* placeholders cannot be
    resent verbatim, so a CA following its standing "resend corrected_payload" rule
    cannot accidentally re-ask the expensive question.
    """
    cost = measured_cost(tool, project_path)
    payload = {
        "ok": False,
        "error": ("%s stopped at its own %.1fs server budget after %.1fs"
                  % (tool, budget_s, elapsed_s)),
        "error_name": "SERVER_BUDGET_EXCEEDED",
        "budget_s": budget_s,
        "elapsed_s": round(elapsed_s, 3),
        "stopped_by": "server",
        "freshness": "unknown",
        "partial": True,
        "integrity_note": (
            "No result was computed: the budget is deliberately set BELOW the default "
            "client timeout so an answer can still be delivered. A perfect answer at "
            "29s that reaches a closed socket is worth less than this one."),
        "ca_next_action": "narrow_the_question_do_not_widen_or_raise_timeout",
        "retry_sequence": [
            {"tool": tool, "REPLACE_scope": "the narrowest module/path that can still "
                                            "answer the question"},
        ],
        "hint": ("Ask the index a narrower question (one module, one path, one symbol) "
                 "rather than the same one with more time. Raising a timeout cannot "
                 "help: this ceiling is the server's, not yours."),
    }
    if cost:
        payload["measured_cost"] = cost
    return payload


__all__ = [
    "CACHEABLE_TOOLS", "CALL_CACHE_TTL_S", "call_signature", "is_cacheable",
    "store", "lookup", "mark_undelivered", "pending_advisory", "cache_status",
    "reset_for_tests", "observe", "measured_cost",
    "CALL_BUDGET_S", "BUDGETED_TOOLS", "budget_for", "budget_exceeded_payload",
    "CALL_SINGLEFLIGHT_WAIT_S", "begin", "is_inflight", "finish",
    "note_successful_mutation",
]
