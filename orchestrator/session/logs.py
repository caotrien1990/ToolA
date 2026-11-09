"""JSONL log lifecycle helpers extracted from code_tools."""

from __future__ import annotations

import json
import os
import threading
from datetime import datetime, timedelta
from pathlib import Path

from ..text_lines import split_lines
from .store import _SESSIONS_DB, _log_session_event, _project_cache_dir

_ORCHESTRATOR_ROOT = Path(__file__).parents[2]
_LOG_FILE_LOCK = threading.RLock()


def _usage_log_path() -> Path:
    """Resolve USAGE_LOG.jsonl at CALL time: ORCHESTRATOR_USAGE_LOG_FILE when set
    (pytest sets it so an in-process probe never lands in the live ledger --
    measured 2026-09-30: 176/724 rows were test probes carrying 46/48 `fails`),
    else the default `.orchestrator_cache/USAGE_LOG.jsonl` under the orchestrator
    root. Resolved per call, not at import, so a test that sets the env after
    import still redirects; the existing `logs._project_cache_dir` monkeypatch in
    tests/test_post_call_accounting.py keeps working because the env is only a
    default -- that test clears it explicitly."""
    override = os.environ.get("ORCHESTRATOR_USAGE_LOG_FILE")
    if override:
        return Path(override)
    return _project_cache_dir(str(_ORCHESTRATOR_ROOT)) / "USAGE_LOG.jsonl"

# B030 2026-08-23. How old a `.<name>.<rand>.tmp` must be before the NEXT writer
# reclaims it. Measured cause: 132 orphans / 10,360,372 bytes accumulated in
# [A]'s own .orchestrator_cache, and the count regrew 2 -> 4 -> 6 -> 8 over three
# runs of the epoch tests. An instrumented `_atomic_write_bytes` counted
# LOGS_MADE 13 / LOGS_REPLACED 11 with ZERO exceptions raised: the writer was
# killed between `mkstemp` and `os.replace` by `p.terminate()` (TerminateProcess
# on Windows), so no `except`, no `finally` and no atexit ever ran. The dying
# process cannot clean up after itself -- only a later writer can.
#
# 15 minutes, not seconds: a fresh tmp may be ANOTHER process's in-flight write
# microseconds from its own `os.replace`, and deleting that one would turn
# cosmetic garbage into a lost log record. The tests pin both directions.
_TMP_ORPHAN_TTL_SECONDS = 15 * 60

# B031 2026-10-11. Time-based retention for USAGE_LOG.jsonl: the hot write
# path (`_log_to_file`) only enforces a small count cap (1000), so under heavy
# load the log's time window collapses (measured: 1000 entries ~= 9 minutes at
# ~1.9 calls/s). `_sweep_old_entries` (called by a periodic scheduler, NOT the
# write path) drops entries older than this many days.
USAGE_LOG_RETENTION_DAYS = 3

# CF-09 (Phase 1, Sprint 1) — append-only log write path, flag-gated.
#
# Flag: PHASE1_CF9_FLAG, REQUIRED, default OFF (draft v13 §7 convention
# PHASE1_<CF>_FLAG as a code constant). Mechanism verified in this file,
# never assumed: this module already carries module-level constants read
# at call time (e.g. _TMP_ORPHAN_TTL_SECONDS, USAGE_LOG_RETENTION_DAYS)
# and the call-time env-override pattern in _usage_log_path, so a code
# constant is a mechanism this file already supports. No config-file
# move was needed, so no move is filed (E1-T5 cure: filed here + the
# Gate 1 mechanism-verification duty stands). Flip requires a restart
# (U-P2 CLOSED, Phase 0 §8.1: NO FLAG FLIP — RESTART REQUIRED).
# Kill switch = G5 rollback (revert + restart); latency = approval +
# drain window, NOT instant. Soak = one full Gate 3 window with
# operational-watch criteria passing and no new error names; soak
# deadline + removal owner are recorded in the ledger row at flag
# creation (coordinator records; not restated here as a decision).
PHASE1_CF9_FLAG = False

# Rotation trigger: SIZE (bytes), not line count alone. A rotation file
# at this size holds roughly an order of magnitude more records than the
# old 1,000-line cap at typical row sizes; the exact record count is
# load-dependent and is NOT claimed as a retention guarantee.
CF09_ROTATION_MAX_BYTES = 10 * 1024 * 1024

# Total evidence-channel disk budget: live log + ALL archives in the
# archive directory COMBINED. Archive growth beyond this bound triggers
# archival to a named external location (when
# ORCHESTRATOR_CF9_EXTERNAL_ARCHIVE_DIR is set) or operator escalation
# (marker file, automatic rotation pauses) — never silent deletion of
# archives. Archives moved to the external location leave this local
# bound; that move is itself hash-verified (see
# _cf09_enforce_combined_bound).
CF09_COMBINED_DISK_BUDGET_BYTES = 100 * 1024 * 1024

CF09_EXTERNAL_ARCHIVE_ENV = "ORCHESTRATOR_CF9_EXTERNAL_ARCHIVE_DIR"

# In-process rotation state, keyed by str(log_path) / str(archive_path).
# Automatic rotation pauses for a log while its delete-fail or
# bound-breach pause is set; an explicit _cf09_rotate() call still
# attempts and clears the pause on success. State is intentionally
# in-process: a restart re-derives it from disk (hash-equal archive
# detection), so a paused rotation can never strand a log forever.
_CF09_ROTATION_PAUSED = set()
_CF09_DELETE_ATTEMPTS = {}


def _phase1_cf9_enabled() -> bool:
    """Read PHASE1_CF9_FLAG at call time (global lookup, so tests and a
    restart-time edit both take effect without re-import tricks)."""
    return bool(PHASE1_CF9_FLAG)


def _cf09_now():
    """Now, as a seam: tests pin it to fix dated archive names."""
    return datetime.now()


def _cf09_archive_dir(log_path: Path) -> Path:
    """NAMED archive location: a sibling `<stem>_archive` directory next
    to the log file (e.g. `.orchestrator_cache/USAGE_LOG_archive/`)."""
    return Path(log_path).parent / (Path(log_path).stem + "_archive")


def _cf09_dated_archive_name(log_path: Path, when=None) -> str:
    # Windows-safe: strftime %Y%m%d-%H%M%S contains no ':' or '/'.
    stamp = (_cf09_now() if when is None else when).strftime("%Y%m%d-%H%M%S")
    return f"{Path(log_path).stem}-{stamp}.jsonl"


def _cf09_sha256_file(path: Path) -> str:
    import hashlib

    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def _cf09_sha256_bytes(data: bytes) -> str:
    import hashlib

    return hashlib.sha256(data).hexdigest()


def _cf09_escalate(log_path: Path, kind: str, detail: dict) -> None:
    """Best-effort operator escalation: a marker file in the archive
    directory. Never raises, never fails the append that triggered it.
    Rotation owner (named): the Stage 3 coordinator, who owns the
    CF-09 ledger row and consumes escalation markers at the Gate 3
    operational watch."""
    try:
        archive_dir = _cf09_archive_dir(log_path)
        archive_dir.mkdir(parents=True, exist_ok=True)
        stamp = _cf09_now().strftime("%Y%m%d-%H%M%S-%f")
        marker = archive_dir / f"CF09_ESCALATION-{kind}-{stamp}.json"
        payload = {"kind": kind, "log": str(log_path),
                   "ts": _cf09_now().isoformat(timespec="seconds")}
        payload.update(detail)
        marker.write_text(json.dumps(payload, ensure_ascii=False) + "\n",
                          encoding="utf-8")
    except Exception:
        pass


def _cf09_append_record(log_path: Path, line: str) -> bool:
    """Append ONE record; never read the file back, never rewrite it.
    fsync per record, preserving the durability step the old atomic
    rewrite got from _atomic_write_bytes (flush + os.fsync)."""
    try:
        with open(log_path, "a", encoding="utf-8") as f:
            f.write(line + "\n")
            f.flush()
            os.fsync(f.fileno())
        return True
    except Exception:
        return False


def _cf09_combined_bytes(log_path: Path) -> int:
    """Live log + *.jsonl archives COMBINED. Marker files
    (CF09_ESCALATION-*.json) and tmp files are operational metadata,
    not evidence, and are excluded — stated, not hidden. Archives with
    non-standard names still count: the bound scans the whole archive
    directory, so an interval change cannot strand archives outside it
    (a cleanup pass is never required to keep the bound true)."""
    total = 0
    try:
        if Path(log_path).exists():
            total += Path(log_path).stat().st_size
    except OSError:
        pass
    try:
        archive_dir = _cf09_archive_dir(log_path)
        if archive_dir.is_dir():
            for candidate in archive_dir.iterdir():
                if candidate.is_file() and candidate.suffix == ".jsonl":
                    try:
                        total += candidate.stat().st_size
                    except OSError:
                        continue
    except OSError:
        pass
    return total


def _cf09_delete_verified(path: Path) -> bool:
    """Unlink + post-delete existence check. Returns False (never
    raises) when the unlink fails or the file is still there."""
    try:
        os.unlink(str(path))
    except OSError:
        return False
    return not Path(path).exists()


def _cf09_find_hash_equal_archive(archive_dir: Path, source_hash: str):
    """Any *.jsonl archive in the directory whose bytes hash to
    source_hash, or None. A re-run after a delete failure lands in a
    new second (new dated name) and must still recognise the archive
    the previous attempt already verified — never duplicate it."""
    try:
        if not archive_dir.is_dir():
            return None
        for candidate in sorted(archive_dir.iterdir(), key=lambda c: c.name):
            if candidate.is_file() and candidate.suffix == ".jsonl":
                try:
                    if _cf09_sha256_file(candidate) == source_hash:
                        return candidate
                except OSError:
                    continue
    except OSError:
        pass
    return None


def _cf09_enforce_combined_bound(log_path: Path) -> str:
    """After a rotation, bring log + archives back within the combined
    budget, oldest archive first. With a named external location set,
    oldest archives MOVE there (os.replace rename + hash verify, with
    a suffix if the target name already exists); without one, file an
    escalation marker and pause automatic rotation. Archives are NEVER
    silently deleted. Never raises."""
    try:
        while _cf09_combined_bytes(log_path) > CF09_COMBINED_DISK_BUDGET_BYTES:
            archive_dir = _cf09_archive_dir(log_path)
            archives = []
            if archive_dir.is_dir():
                archives = sorted(
                    (c for c in archive_dir.iterdir()
                     if c.is_file() and c.suffix == ".jsonl"),
                    key=lambda c: c.name,
                )
            if not archives:
                break
            external = os.environ.get(CF09_EXTERNAL_ARCHIVE_ENV, "")
            if not external:
                _CF09_ROTATION_PAUSED.add(str(log_path))
                _cf09_escalate(log_path, "bound-exceeded",
                               {"combined_bytes": _cf09_combined_bytes(log_path),
                                "budget_bytes": CF09_COMBINED_DISK_BUDGET_BYTES})
                return "paused-bound"
            oldest = archives[0]
            target_dir = Path(external)
            try:
                target_dir.mkdir(parents=True, exist_ok=True)
                before_hash = _cf09_sha256_file(oldest)
                target = target_dir / oldest.name
                suffix = 2
                while target.exists():
                    target = target_dir / f"{oldest.stem}-{suffix}{oldest.suffix}"
                    suffix += 1
                os.replace(str(oldest), str(target))
                if not target.is_file() or oldest.exists():
                    return "external-move-failed"
                if _cf09_sha256_file(target) != before_hash:
                    return "external-move-failed"
            except OSError:
                _CF09_ROTATION_PAUSED.add(str(log_path))
                _cf09_escalate(log_path, "external-move-failed",
                               {"archive": str(oldest)})
                return "paused-bound"
        return "within-bound"
    except Exception:
        return "bound-check-failed"


def _cf09_rotate(log_path: Path) -> str:
    """Rotate ONE log file. The caller holds _LOG_FILE_LOCK, so
    rotation under concurrent writes is serialised by the same lock
    the append path uses.

    Binding recovery rule (R2-S-09):
    - archive is name-keyed and hash-verified: a re-run finding a
      hash-equal archive (same dated name, or any archive name after
      a delete-failure retry) SKIPS re-copy (never duplicates) and
      proceeds to the delete step;
    - delete is delete-verified (post-delete existence check);
    - delete failure after a successful archive KEEPS BOTH copies,
      retries delete on the next rotation attempt, and on the second
      consecutive failure escalates to the operator and pauses
      AUTOMATIC rotation (a direct _cf09_rotate() call still
      retries and clears the pause on success);
    - a partial archive (hash mismatch after copy) is DISCARDED and
      retried from source on the next attempt; the live log is never
      deleted on a failed archive;
    - the live log is NEVER truncated in place (no copy-truncate):
      content leaves only by whole-file delete after a verified
      archive. Archive files themselves are created by
      _atomic_write_bytes (tmp + os.replace rename into place), and
      a name collision with DIFFERENT content (two rotations in one
      second) suffixes the name, never overwriting another archive.
    Returns a status string; never raises.
    """
    log_path = Path(log_path)
    key = str(log_path)
    try:
        if not log_path.exists():
            return "no-log"
        source = log_path.read_bytes()
    except OSError:
        return "read-failed"
    source_hash = _cf09_sha256_bytes(source)
    try:
        archive_dir = _cf09_archive_dir(log_path)
        archive_dir.mkdir(parents=True, exist_ok=True)
        archive_path = _cf09_find_hash_equal_archive(archive_dir, source_hash)
        if archive_path is None:
            stamp = _cf09_now().strftime("%Y%m%d-%H%M%S")
            candidate = archive_dir / f"{log_path.stem}-{stamp}.jsonl"
            suffix = 2
            while candidate.is_file():
                candidate = archive_dir / f"{log_path.stem}-{stamp}-{suffix}.jsonl"
                suffix += 1
            archive_path = candidate
            try:
                _atomic_write_bytes(archive_path, source)
            except Exception:
                return "archive-failed"
            try:
                if _cf09_sha256_file(archive_path) != source_hash:
                    raise ValueError("archive hash mismatch")
            except Exception:
                try:
                    archive_path.unlink()
                except OSError:
                    pass
                return "partial-discarded"
        # Archive verified (fresh or pre-existing). Delete, verified.
        if _cf09_delete_verified(log_path):
            _CF09_DELETE_ATTEMPTS.pop(str(archive_path), None)
            _CF09_ROTATION_PAUSED.discard(key)
            _cf09_enforce_combined_bound(log_path)
            return "rotated"
        attempts = _CF09_DELETE_ATTEMPTS.get(str(archive_path), 0) + 1
        _CF09_DELETE_ATTEMPTS[str(archive_path)] = attempts
        if attempts >= 2:
            _CF09_ROTATION_PAUSED.add(key)
            _cf09_escalate(log_path, "delete-failed",
                           {"archive": str(archive_path), "attempts": attempts})
            return "paused-delete-failed"
        return "delete-failed-retry-pending"
    except Exception:
        return "rotate-failed"


def _cf09_maybe_rotate(log_path: Path) -> str:
    """Automatic rotation from the append path. Size-triggered (or
    size+count via the byte threshold on typical rows); never line
    count alone. Paused logs skip automatic rotation until an
    explicit _cf09_rotate() retry succeeds. Never raises, never
    fails the append that triggered it."""
    try:
        if str(log_path) in _CF09_ROTATION_PAUSED:
            return "paused"
        try:
            size = Path(log_path).stat().st_size
        except OSError:
            return "no-log"
        if size < CF09_ROTATION_MAX_BYTES:
            return "not-triggered"
        return _cf09_rotate(log_path)
    except Exception:
        return "rotate-failed"


def _sweep_orphan_tmp(path: Path, ttl_seconds: float = _TMP_ORPHAN_TTL_SECONDS) -> int:
    """Remove stale `.<path.name>.<rand>.tmp` siblings; return how many went.

    Scoped to `path`'s own tmp pattern on purpose: `_atomic_write_bytes` also
    writes caller files, and a sweep matching `*.tmp` alone would delete
    someone else's unrelated temporary. Best-effort by contract -- an orphan
    another process still holds open cannot be unlinked on Windows, and that
    must not fail the write this sweep is a side effect of.
    """
    removed = 0
    try:
        import os
        import time
        parent = path.parent
        cutoff = time.time() - float(ttl_seconds)
        for candidate in parent.glob("." + path.name + ".*.tmp"):
            try:
                if candidate.stat().st_mtime > cutoff:
                    continue
                os.unlink(str(candidate))
                removed += 1
            except OSError:
                continue
    except Exception:
        return removed
    return removed


def _atomic_write_bytes(path: Path, data: bytes) -> None:
    import os
    import tempfile
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=str(path.parent))
    tmp = Path(tmp_name)
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(data)
            f.flush()
            os.fsync(f.fileno())
        os.replace(str(tmp), str(path))
    except Exception:
        try:
            tmp.unlink()
        except Exception:
            pass
        raise


def _atomic_write_text(path: Path, text: str, encoding: str = "utf-8") -> None:
    _atomic_write_bytes(Path(path), text.encode(encoding))


def _log_to_file(log_path: Path, entry: dict, max_entries: int = 500) -> bool:
    """Append one JSONL record atomically; return False instead of hiding failure.

    CF-09: with PHASE1_CF9_FLAG ON, the write path is append-only — each
    record is appended, never read back and rewritten, and max_entries
    no longer caps retention (retention is unbounded within the
    combined log + archive disk bound; rotation archives before
    deleting). With the flag OFF, the old capped behaviour is
    reproduced exactly (read + trim + atomic rewrite).
    """
    try:
        with _LOG_FILE_LOCK:
            log_path.parent.mkdir(parents=True, exist_ok=True)
            # B030: reclaim what a killed writer left behind. This is the only
            # place that CAN -- the process that made the orphan was terminated.
            _sweep_orphan_tmp(log_path)
            # One record per "\n", the only separator json.dumps below writes:
            # ensure_ascii=False keeps a raw U+2028/U+0085 INSIDE a record, and
            # str.splitlines() re-emitted it as two unparseable halves.
            line = json.dumps(entry, ensure_ascii=False)
            if _phase1_cf9_enabled():
                if not _cf09_append_record(log_path, line):
                    return False
                _cf09_maybe_rotate(log_path)
                return True
            lines = split_lines(log_path.read_text(encoding="utf-8")) if log_path.exists() else []
            lines.append(line)
            if len(lines) > max_entries:
                lines = lines[-max_entries:]
            _atomic_write_text(log_path, "\n".join(lines) + "\n", encoding="utf-8")
        return True
    except Exception:
        return False


# Sentinel distinguishing "caller did not pass this field" (old callers:
# keep the old row shape) from "caller passed None" (patched dispatcher
# path: stamp an explicit null so absence is visible, never fabricated).
_LOG_USAGE_MISSING = object()


def _log_usage(tool: str, params: dict, ok: bool, ms: int, error: str = "", *,
               delivered: bool = True, client_gone: bool = False,
               error_name: str = "", agent_id: str = "",
               returns_shape_verdict: str = "", returns_shape_errors=None,
               accounting_id: str = "", accounting_sequence=None,
               session_id=_LOG_USAGE_MISSING, task_epoch=_LOG_USAGE_MISSING,
               prompt_hash=_LOG_USAGE_MISSING) -> bool:
    """Ghi vao USAGE_LOG cua Orchestrator (meta-log ve chinh Orchestrator).

    D6: the row used to be {ts, tool, ok, ms, project} where `project` was only a
    folder BASENAME. Two different repos sharing a basename collapsed into one
    row, and nothing recorded WHO called or WITH WHICH fields -- so a CA report of
    "search_code timed out" could not be matched against the call [A] itself
    recorded as ok=True. Param KEY NAMES only, never values: attribution without
    copying payloads (or secrets) into a log. This is a log-only change, so it
    costs nothing per call in the response.

    A41 tier 3a/3b -- the row used to be unintentionally DISHONEST in two ways:

    * `ok=true` was written for calls that delivered ZERO bytes. Measured
      2026-09-21: five calls of 22-133s answered `"POST /api/call/..." 200 0` in
      one window, every one of them logged ok=true, and no ERROR or WARNING line
      existed anywhere. `ok` answers "did the tool succeed", which was true; what
      no field answered was "did the caller ever receive it". Hence `delivered`,
      written on EVERY row rather than only on the bad ones: a flag that appears
      only when something went wrong is indistinguishable from an older row that
      predates the flag, and absence-means-true is the exact trap that made a
      wrong key read as an empty result earlier in this campaign.
    * only a truncated `error` STRING was kept, never `error_name`, so classifying
      a failure meant reading source to guess which branch produced the prose.

    `agent_id` is now an explicit argument instead of a lookup in `params`.
    Measured: 1000 USAGE_LOG rows, `with_agent_id=0` -- and the cause was a KEY
    NAME mismatch, not a missing value. `_compact_accounting_body` stamps
    `_task_agent_id` into the body it hands down, while the loop below has always
    looked for a bare `agent_id`, so a resolved identity sat one key away from the
    row it belonged to. The params loop is kept for the `session_id` case and for
    any caller that does carry a plain `agent_id`; the explicit argument wins.
    P16: the returns_shape verdict keys follow the same A41-style discipline --
    they arrive only from `_persist_post_call_accounting`, for window-reaching dispatches.
    USAGE_LOG patch (spec 2187/mapping 2188): `session_id`, `task_epoch`
    and `prompt_hash` arrive as explicit keyword arguments from
    `_persist_post_call_accounting` (mapped from the dispatcher's
    `host_session_id` value). An explicitly passed None stamps a JSON
    null (metadata absent); an omitted argument keeps the old behaviour
    (session_id from `params` only, no task_epoch/prompt_hash keys).
    """
    project_hint = params.get("project_path", params.get("file_path", ""))
    entry = {
        "ts": datetime.now().isoformat(timespec="seconds"),
        "tool": tool, "ok": ok, "ms": ms,
        "project": Path(project_hint).name if project_hint else "",
        "delivered": bool(delivered),
    }
    if project_hint:
        entry["project_path"] = str(project_hint)[:260]
    try:
        entry["params"] = sorted(str(k) for k in params)[:40]
    except Exception:
        pass
    if agent_id:
        entry["agent_id"] = str(agent_id)[:80]
    for _attr in ("agent_id", "session_id"):
        if entry.get(_attr):
            continue
        _val = params.get(_attr)
        if _val:
            entry[_attr] = str(_val)[:80]
    # USAGE_LOG patch (spec 2187/mapping 2188): explicit dispatcher fields
    # win over the params loop above. A passed None stamps an explicit
    # null (metadata absent, not fabricated); a missing argument (the
    # sentinel) leaves the old params-loop behaviour untouched.
    if session_id is not _LOG_USAGE_MISSING:
        entry["session_id"] = str(session_id)[:80] if session_id else None
    if task_epoch is not _LOG_USAGE_MISSING:
        if isinstance(task_epoch, (int, float)):
            entry["task_epoch"] = task_epoch
        else:
            entry["task_epoch"] = str(task_epoch)[:80] if task_epoch else None
    if prompt_hash is not _LOG_USAGE_MISSING:
        entry["prompt_hash"] = str(prompt_hash)[:80] if prompt_hash else None
    if client_gone:
        entry["client_gone"] = True
        entry["undelivered_reason"] = "client_transport_gone"
    if error_name:
        entry["error_name"] = str(error_name)[:80]
    if returns_shape_verdict:
        entry["returns_shape_verdict"] = str(returns_shape_verdict)[:40]
    if returns_shape_errors:
        entry["returns_shape_errors"] = [str(x)[:80] for x in list(returns_shape_errors)[:5]]
    if accounting_id:
        entry["accounting_id"] = str(accounting_id)[:80]
    if accounting_sequence is not None:
        try:
            entry["accounting_sequence"] = int(accounting_sequence)
        except Exception:
            pass
    if error:
        entry["error"] = error[:120]
    return _log_to_file(_usage_log_path(), entry, max_entries=1000)


def _log_app_session(project_path: str, tool: str, extra: dict, ok: bool, ms: int = 0, error: str = ""):
    """Ghi vào APP_SESSION_LOG trong .orchestrator_cache/ của project đang làm việc."""
    try:
        cache_dir = _project_cache_dir(str(Path(project_path).resolve()))
        entry = {"ts": datetime.now().isoformat(timespec="seconds"), "tool": tool, "ok": ok, "ms": ms}
        if extra:
            entry.update(extra)
        if error:
            entry["error"] = error[:120]
        _log_to_file(cache_dir / "APP_SESSION_LOG.jsonl", entry, max_entries=500)
    except Exception:
        pass


def _auto_trim_log(log_path: Path, keep_sessions: int = 2):
    """Trim USAGE_LOG, keeping only the last completed sessions."""
    if _phase1_cf9_enabled():
        # CF-09 append-only: the session-trim rewrite is suspended while
        # the flag is ON. Rotation + the combined disk bound govern disk
        # use instead; trimming here would reintroduce rewrite loss on
        # the session_end path (log_native calls this after every
        # session end). OFF-state behaviour is unchanged.
        return
    try:
        if not log_path.exists():
            return
        lines = split_lines(log_path.read_text(encoding="utf-8"))
        entries = [json.loads(l) for l in lines if l.strip()]
    except Exception:
        return
    end_positions = [i for i, e in enumerate(entries) if e.get("type") == "session_end"]
    if len(end_positions) <= keep_sessions:
        return
    cutoff = end_positions[-(keep_sessions + 1)] + 1
    kept = entries[cutoff:]
    _atomic_write_text(
        log_path,
        "\n".join(json.dumps(e, ensure_ascii=False) for e in kept) + "\n",
        encoding="utf-8",
    )


def _sweep_old_entries(log_path: Path, retention_days: float = USAGE_LOG_RETENTION_DAYS) -> int:
    """Drop USAGE_LOG entries older than `retention_days`; return how many went.

    Time-based retention for the usage ledger. The hot write path
    (`_log_to_file`) only enforces a small count cap, so without a periodic
    sweep the log's time window collapses to minutes under heavy load
    (measured 2026-10-11: 1000 entries ~= 9 minutes at ~1.9 calls/s).

    Fail-safe: lines that don't parse as JSON, or entries without a parseable
    `ts`, are KEPT -- never delete data we don't understand. A timezone-aware
    `ts` that can't be compared against the naive cutoff is also kept.

    Uses the same `_LOG_FILE_LOCK` + atomic replace as the writers. NOT called
    from `_log_to_file`; intended for a periodic scheduler (e.g. hourly), so
    the per-write cost stays O(current file), not O(3 days of history).
    """
    try:
        cutoff = datetime.now() - timedelta(days=float(retention_days))
    except Exception:
        return 0
    try:
        with _LOG_FILE_LOCK:
            if not log_path.exists():
                return 0
            raw_lines = split_lines(log_path.read_text(encoding="utf-8"))
            kept: list[str] = []
            dropped = 0
            for line in raw_lines:
                if not line.strip():
                    continue
                try:
                    entry = json.loads(line)
                    ts = datetime.fromisoformat(entry.get("ts", ""))
                    if ts >= cutoff:
                        kept.append(line)
                    else:
                        dropped += 1
                except Exception:
                    # Unparseable line, missing/bad ts, or tz-aware vs naive
                    # mismatch: keep the line, don't delete what we can't read.
                    kept.append(line)
            if dropped == 0:
                return 0
            _atomic_write_text(log_path, "\n".join(kept) + "\n", encoding="utf-8")
            return dropped
    except Exception:
        return 0


def _check_incomplete_sessions() -> list[dict]:
    """Scan USAGE_LOG tìm sessions chưa có log_native (session_end)."""
    log_path = _usage_log_path()
    if not log_path.exists():
        return []
    try:
        entries = [json.loads(l) for l in split_lines(log_path.read_text(encoding="utf-8")) if l.strip()]
    except Exception:
        return []
    if not entries:
        return []
    sessions: list[list[dict]] = []
    current: list[dict] = [entries[0]]
    for entry in entries[1:]:
        try:
            gap = datetime.fromisoformat(entry["ts"]) - datetime.fromisoformat(current[-1]["ts"])
            if gap > timedelta(hours=2):
                sessions.append(current)
                current = [entry]
            else:
                current.append(entry)
        except Exception:
            current.append(entry)
    sessions.append(current)
    now = datetime.now()
    incomplete = []
    for session in sessions:
        has_calls = any(e.get("tool") and e.get("type") != "session_end" for e in session)
        has_end = any(e.get("type") == "session_end" for e in session)
        if not has_calls or has_end:
            continue
        ts_start = session[0].get("ts", "")
        try:
            age_h = (now - datetime.fromisoformat(ts_start)).total_seconds() / 3600
        except Exception:
            age_h = 999
        if age_h < 2:
            continue
        call_count = sum(1 for e in session if e.get("tool") and e.get("type") != "session_end")
        incomplete.append({"ts": ts_start, "calls": call_count})
    return incomplete


async def log_native(native: list = None) -> str:
    import asyncio
    return await asyncio.to_thread(_log_native_sync, native)


def _log_native_sync(native: list = None) -> str:
    from .store import _get_unique_caller_session
    try:
        session_id = _get_unique_caller_session()
    except ValueError as exc:
        return json.dumps({"ok": False, "error_name": "NATIVE_LOG_SESSION_UNRESOLVED",
                           "error": str(exc)}, ensure_ascii=False)
    if native is None:
        native = []
    cleaned = [
        {"tool": str(item.get("tool", ""))[:60],
         "instead_of": str(item.get("instead_of", ""))[:60],
         "reason": str(item.get("reason", ""))[:200]}
        for item in native if isinstance(item, dict) and item.get("tool")
    ]
    entry = {
        "ts": datetime.now().isoformat(timespec="seconds"),
        "type": "session_end",
        "native": cleaned,
    }
    usage_path = _usage_log_path()
    _log_to_file(usage_path, entry, max_entries=1000)
    _auto_trim_log(usage_path)
    event_id = _log_session_event(session_id, "log_native", {"native": cleaned},
                                  {"ok": True, "native_count": len(cleaned)}, 0, "ok", "")
    if event_id is None:
        raise RuntimeError("log_native event persistence failed")
    msg = "Session completion logged."
    if cleaned:
        msg += f" {len(cleaned)} native fallback(s) logged."
    return json.dumps({"ok": True, "message": msg, "native_count": len(cleaned)}, ensure_ascii=False)