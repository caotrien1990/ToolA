"""Graph status tool: expose freshness, counts, and WAL state (Phase 1 Step 8).

Reads the graph DB and the files table to report staleness (files changed since
last index). Fail-open: if the DB is missing or corrupt, returns a disabled payload
rather than raising — callers must never crash on a status probe.
"""
import os
import sqlite3
import time
from typing import overload

from .config import graph_enabled, CBM_USE_TREE_SITTER, EXTRACTOR_VERSION
from . import store as _store

# STT-26/ISS-05: floor for the staleness scan's share of a bounded status call.
# The scan budget used to be whatever was left of the response budget AFTER the
# integrity probe, so on an HDD the probe alone could leave ~0ms and every
# status answer degraded to freshness=unknown at files_checked=25/424. The scan
# now always gets at least this much wall clock, and the payload reports the
# split so a slow probe is visible instead of being blamed on the scan.
CBM_STATUS_SCAN_MIN_BUDGET_S = float(
    os.environ.get("CBM_STATUS_SCAN_MIN_BUDGET_S", "1.0"))

# D1: the integrity probe needs a budget of its OWN. The scan floor above only
# guarantees the scan gets wall clock; it does nothing to stop an unbounded
# `PRAGMA quick_check` from spending 53.7s first and blowing the whole call past
# any client timeout. Measured healthy cost is ~109ms, so 1.0s is ~9x headroom
# and an abort means "slow disk", never "corrupt".
CBM_STATUS_INTEGRITY_BUDGET_S = float(
    os.environ.get("CBM_STATUS_INTEGRITY_BUDGET_S", "1.0"))

# Gap C: how OFTEN the integrity budget above is checked, in sqlite vm opcodes -- so
# the two constants only mean anything together. A PRAGMA takes no deadline and sqlite
# is interruptible only between opcodes, so the budget can be overshot by one
# inter-call gap and no value here changes that.
# MEASURED 2026-09-21, ToolQ graph db on a spinning D:, deadline 1.0s:
#   interval 2000 (the value shipped until now): handler fired 9 times in 1172ms,
#     max gap 828ms -- and the live tool answered integrity_ms=3828 for a declared
#     1000ms, i.e. 3.8x, with the result then thrown away as "unknown".
#   interval 100: max gap 313ms (0.38x of 2000).
# Cost of the finer interval where it is actually paid -- a HEALTHY probe that never
# aborts (ToolA graph db, same warm state): 172ms over 24838 callbacks at 100 vs 187ms
# over 1236 at 2000. Inside the noise band, so resolution here is free.
# Why not lower: at 25 the max gap measured 16ms, which is just the Windows clock
# granularity, and no interval can interrupt a single slow page read. That floor is
# why the payload publishes integrity_budget_enforcement as best_effort instead of
# letting integrity_budget_s read as a guarantee.
CBM_STATUS_INTEGRITY_OPCODE_INTERVAL = max(1, int(
    os.environ.get("CBM_STATUS_INTEGRITY_OPCODE_INTERVAL", "100")))

# Gap C: the wall-clock the whole status answer aims at. This was a magic local
# `response_budget_s = 2.0` inside graph_status() that NAMED the response but sized
# only the stale-scan slice (scan_budget = budget - elapsed_so_far). MEASURED
# 2026-09-21 on ToolQ: pre_scan_ms=6328, so that subtraction went negative, max()
# quietly returned CBM_STATUS_SCAN_MIN_BUDGET_S, and the answer took elapsed_ms=6843
# against a declared 2.0s. Module-level and env-overridable for the same reason the
# other two are: an operator on a slow disk must be able to raise it without editing
# code, and stale_scan.budget_source / budget_overrun_ms now say when it was blown.
CBM_STATUS_RESPONSE_BUDGET_S = float(
    os.environ.get("CBM_STATUS_RESPONSE_BUDGET_S", "2.0"))

# EXTRACTOR_VERSION moved to config (W5): the indexer and store need it to
# decide staleness, and they cannot import status.py (it imports store).
# Re-exported here so `from .status import EXTRACTOR_VERSION` keeps working.


@overload
def _stale_count(conn: sqlite3.Connection, project_root: str, *,
                 budget_s: None = None) -> int: ...
@overload
def _stale_count(conn: sqlite3.Connection, project_root: str, *,
                 budget_s: float) -> dict: ...
def _stale_count(conn: sqlite3.Connection, project_root: str, *, budget_s: float | None = None) -> int | dict:
    """Count indexed files needing re-extraction, by the SAME rule the indexer uses.

    Return shape depends on `budget_s`: omit it for the unbounded legacy `int`,
    pass a budget for the bounded `stale_scan` dict. The two overloads above
    exist so callers of the bounded form get a `dict` statically -- the union
    return type used to force every caller into `locals()`/cast workarounds.

    Staleness has one definition and it lives in `store.stale_files`: content
    changed, never indexed, OR indexed by a different EXTRACTOR_VERSION. This
    function used to apply only the first clause, and the two answers diverged
    the moment a version was bumped -- measured on [A] right after v7 -> v8:
    this reported 5 stale while every one of the 700 rows was still v7, so the
    report said "fresh" about a database that no longer matched the extractor.

    Worse than a wrong number: `query._auto_reindex_if_stale` gates on this
    count, so a bump could never trigger the reindex it exists to force. The DB
    would have stayed frozen at the old node/edge shape indefinitely while every
    status probe called it fresh -- exactly the failure `store.stale_files`
    documents from W5, reintroduced through the other half of the pair.

    STT-26 (large-repo latency): this gate runs inside EVERY analysis-view
    build, and hashing all 549 ToolQ rows on an HDD cost ~75s per call — every
    cbm_test_gap/dsm/quality_gate died on the freshness gate before the view
    even started. Order per row now: version drift -> stale with ZERO disk I/O;
    then one os.stat; hash (still authoritative) only when size or mtime moved
    or when the row lacks usable size/modified_at metadata. Residual risk: a
    content edit that preserves BOTH size and mtime reads fresh here — the
    hash-based indexer.detect_changes still catches it on any explicit
    cbm_incremental_index, so drift can never outlive the next index run.
    """
    rows = conn.execute(
        "SELECT path, content_hash, size, modified_at, extractor_version "
        "FROM files;").fetchall()
    import hashlib
    stale = 0
    started = time.monotonic()
    for position, r in enumerate(rows, 1):
        if budget_s is not None and time.monotonic() - started >= budget_s:
            return {"stale_files": stale, "files_checked": position - 1,
                    "files_total": len(rows), "complete": False,
                    "stop_reason": "response_budget"}
        # Cheapest clause first: version drift needs no filesystem touch.
        if r["extractor_version"] != EXTRACTOR_VERSION:
            stale += 1
            continue
        path = r["path"]
        abs_p = os.path.join(project_root, path)
        try:
            st = os.stat(abs_p)
        except OSError:
            stale += 1
            continue
        size = r["size"]
        mtime_ms = r["modified_at"]
        # Hash remains authoritative. Size+mtime equality is not proof: restore,
        # checkout and copy tools can preserve both while replacing the bytes.
        try:
            digest = hashlib.sha256()
            with open(abs_p, "rb") as f:
                for chunk in iter(lambda: f.read(1024 * 1024), b""):
                    digest.update(chunk)
                    if budget_s is not None and time.monotonic() - started >= budget_s:
                        return {"stale_files": stale, "files_checked": position - 1,
                                "files_total": len(rows), "complete": False,
                                "stop_reason": "response_budget"}
            chash = digest.hexdigest()
        except OSError:
            stale += 1
            continue
        if chash != r["content_hash"]:
            stale += 1
    if budget_s is None:
        return stale
    return {"stale_files": stale, "files_checked": len(rows),
            "files_total": len(rows), "complete": True, "stop_reason": None}


def graph_status(project_path: str) -> dict:
    """Return a status payload for the project graph DB.

    Fields: enabled, db_present, integrity, nodes, edges, files, last_run,
    stale_files, extractor_version, tree_sitter_enabled, wal, freshness,
    elapsed_ms.
    Fail-open: any DB error returns enabled=False + error (no raise).
    """
    t0 = time.monotonic()
    payload = {
        "enabled": graph_enabled(),
        "db_present": False,
        "integrity": "unknown",
        "nodes": 0, "edges": 0, "files": 0,
        "last_run": None, "stale_files": None,
        "extractor_version": EXTRACTOR_VERSION,
        "tree_sitter_enabled": CBM_USE_TREE_SITTER,
        "wal": "unknown",
        "freshness": "disabled",
        "elapsed_ms": 0,
        "error": None,
    }
    if not graph_enabled():
        payload["elapsed_ms"] = round((time.monotonic() - t0) * 1000, 1)
        return payload

    db_path = _store.graph_db_path(project_path)
    payload["db_present"] = os.path.exists(db_path)
    if not payload["db_present"]:
        payload["freshness"] = "not_indexed"
        payload["elapsed_ms"] = round((time.monotonic() - t0) * 1000, 1)
        return payload

    try:
        conn = _store.get_db(project_path, readonly=True)
        try:
            # ISS-05: probe on the connection we already hold, with quick_check
            # instead of integrity_check. `_store.integrity_check` opens a SECOND
            # read-only connection to the same DB and runs the full check; on an
            # HDD that pair cost more than the entire response budget, so the
            # staleness scan that follows started with ~0ms left and every answer
            # degraded to freshness=unknown. quick_check rejects the same
            # structural corruption (verified on a page-scrambled copy);
            # integrity_check only adds index-content cross-checks that
            # init/migrate already cover.
            # D1: ISS-05 left quick_check itself UNBOUNDED. response_budget_s
            # never reached the probe, so a measured 53.7s probe on an HDD still
            # starved the scan down to its floor and the answer degraded to
            # freshness=unknown at files_checked=156/838 -- exactly the failure
            # the comment below claims is fixed. A PRAGMA takes no deadline, so
            # bound it the only way sqlite3 offers: a progress handler that
            # aborts the statement once the probe's own budget is spent.
            _t_ic = time.monotonic()
            _ic_deadline = _t_ic + CBM_STATUS_INTEGRITY_BUDGET_S
            _ic_aborted = False
            try:
                # The interval is the budget's ENFORCEMENT RESOLUTION, not a tuning
                # detail: the handler runs BETWEEN vm opcodes, so the deadline can only
                # be honoured to within one inter-call gap. See
                # CBM_STATUS_INTEGRITY_OPCODE_INTERVAL for the measurements.
                conn.set_progress_handler(
                    lambda: 1 if time.monotonic() > _ic_deadline else 0,
                    CBM_STATUS_INTEGRITY_OPCODE_INTERVAL)
                _ic_row = conn.execute("PRAGMA quick_check;").fetchone()
                ic = (_ic_row[0] if _ic_row else "unknown")
            except sqlite3.Error as _ic_exc:
                # Aborted by OUR handler is not corruption. Reporting "corrupt"
                # for a slow disk would be worse than the starvation it replaces,
                # so only a failure BEFORE the deadline keeps the corrupt path.
                if time.monotonic() >= _ic_deadline:
                    _ic_aborted = True
                    ic = "unknown"
                else:
                    ic = "error: %s" % type(_ic_exc).__name__
            finally:
                conn.set_progress_handler(None, 0)
            payload["integrity"] = ic
            payload["integrity_probe"] = (
                "quick_check_aborted_budget" if _ic_aborted else "quick_check")
            payload["integrity_ms"] = round((time.monotonic() - _t_ic) * 1000, 1)
            payload["integrity_budget_s"] = CBM_STATUS_INTEGRITY_BUDGET_S
            # integrity_budget_s alone reads as a ceiling this code can guarantee, and
            # it cannot: MEASURED integrity_ms=3828 for a declared 1000ms. Publishing
            # the mechanism next to the number is what keeps a CA from sizing a client
            # timeout off a bound nobody enforces.
            payload["integrity_budget_enforcement"] = (
                "best_effort_progress_handler_every_%d_vm_opcodes"
                % CBM_STATUS_INTEGRITY_OPCODE_INTERVAL)
            if _ic_aborted:
                # Honest gap, not a verdict: the scan below still gets its budget.
                payload["integrity_note"] = (
                    "quick_check exceeded CBM_STATUS_INTEGRITY_BUDGET_S=%ss and was"
                    " aborted after %sms; the budget is best-effort because sqlite is"
                    " interruptible only between vm opcodes, so an I/O-bound probe"
                    " overshoots by up to one page read; integrity is NOT a corruption"
                    " verdict here"
                    % (CBM_STATUS_INTEGRITY_BUDGET_S, payload["integrity_ms"]))
            elif ic != "ok":
                payload["freshness"] = "corrupt"
                payload["error"] = f"quick_check: {ic}"
                return payload
            counts = _store.status_counts(conn)
            payload["nodes"] = counts["nodes"]
            payload["edges"] = counts["edges"]
            payload["files"] = counts["files"]
            payload["last_run"] = counts["last_run"]
            wal = conn.execute("PRAGMA journal_mode;").fetchone()
            payload["wal"] = wal[0] if wal else "unknown"
            # CAN-053: stale-file count FIRST - freshness derives from actual
            # drift. Both full and incremental indexes record index_runs, so
            # last_run is the most recent graph mutation rather than last full run.
            try:
                # The scan gets whatever is left of the response budget, but
                # never less than CBM_STATUS_SCAN_MIN_BUDGET_S: a slow probe
                # must show up as integrity_ms, not silently starve the scan
                # into a permanent freshness=unknown.
                _pre_scan_ms = (time.monotonic() - t0) * 1000
                scan_budget = max(
                    CBM_STATUS_SCAN_MIN_BUDGET_S,
                    CBM_STATUS_RESPONSE_BUDGET_S - (time.monotonic() - t0))
                _t_scan = time.monotonic()
                stale_result = _stale_count(conn, project_path, budget_s=scan_budget)
                payload["stale_files"] = stale_result["stale_files"]
                stale_result["budget_s"] = round(scan_budget, 3)
                stale_result["scan_ms"] = round((time.monotonic() - _t_scan) * 1000, 1)
                stale_result["pre_scan_ms"] = round(_pre_scan_ms, 1)
                # MEASURED 2026-09-21 on the ToolQ graph db: pre_scan_ms=6328 against
                # a 2.0s response budget, so the subtraction went NEGATIVE, max() fell
                # back to the scan floor, and nothing in the payload said the budget
                # had stopped governing anything -- budget_s just read 1.0, which is
                # exactly what a healthy remainder would look like. A CA that sizes its
                # client timeout from the declared budget then abandons a call that
                # completed (elapsed_ms 6843), which is the wasted-call story this
                # campaign exists to close. The floor stays -- starving the scan is the
                # worse failure -- but which branch produced it is now on the record.
                if _pre_scan_ms > CBM_STATUS_RESPONSE_BUDGET_S * 1000:
                    stale_result["budget_source"] = "scan_min_floor"
                    stale_result["budget_overrun_ms"] = round(
                        _pre_scan_ms - CBM_STATUS_RESPONSE_BUDGET_S * 1000, 1)
                else:
                    stale_result["budget_source"] = "response_budget_remainder"
                payload["stale_scan"] = stale_result
                payload["scan_ms"] = stale_result["scan_ms"]
            except Exception as e:
                payload["stale_files"] = None
                payload["error"] = f"stale_count_failed: {type(e).__name__}"
            try:
                from .config import CBM_EXTRACT_JS as _ejs
                payload["extract_js_enabled"] = bool(_ejs)
                if not _ejs:
                    payload["coverage_note"] = "python-only (set CBM_EXTRACT_JS=1 for JS/TS; HTML/CSS not indexed)"
            except Exception:
                pass
            if counts["last_run"]:
                fin = counts["last_run"]["finished_at"]
                if fin:
                    stale_n = payload.get("stale_files")
                    scan_complete = bool((payload.get("stale_scan") or {}).get("complete"))
                    if stale_n == 0 and scan_complete:
                        payload["freshness"] = "fresh"
                    elif not scan_complete:
                        payload["freshness"] = "unknown"
                    elif stale_n is None:
                        # Count failed: age is not a freshness signal. Recent
                        # last_run must not become false-fresh (BUG-01 / AC-P1-S1).
                        payload["freshness"] = "unknown"
                    else:
                        # A non-zero count IS the staleness signal. The age branch
                        # used to answer "fresh" for a whole hour regardless, so
                        # `stale_files: 24` sat next to `freshness: "fresh"` -- one
                        # field contradicting the other in the same payload.
                        payload["freshness"] = "stale"
                else:
                    payload["freshness"] = "indexing"  # run in progress, not stale
            else:
                payload["freshness"] = "not_indexed"
            return payload
        finally:
            conn.close()
    except Exception as e:
        payload["freshness"] = "error"
        payload["error"] = f"{type(e).__name__}: {e}"
        return payload
    finally:
        payload["elapsed_ms"] = round((time.monotonic() - t0) * 1000, 1)


__all__ = ["graph_status", "EXTRACTOR_VERSION"]
