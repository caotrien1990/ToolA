from __future__ import annotations

import contextlib
import math
import os
import pickle
import re
import subprocess
import sys
import tempfile
import time
from typing import Any, Iterable

from orchestrator.text_lines import split_lines

MAX_PATTERN_CHARS = 4096
MAX_INPUT_BYTES = 32 * 1024 * 1024
MAX_LINE_BYTES = 1 * 1024 * 1024
MAX_RECORDS = 20000
DEFAULT_DEADLINE_SECONDS = 8.0
MIN_DEADLINE_SECONDS = 0.05
MAX_DEADLINE_SECONDS = 15.0
_REAP_GRACE_SECONDS = 0.25
# ISS-12: how often the child flushes what it has found so far. Frequent enough
# that a killed scan leaves usable evidence, rare enough that the flush (one
# pickle of the results accumulated so far, between files only) stays off the
# hot path. D: is an HDD, so this is deliberately not per-match.
_CHECKPOINT_INTERVAL_SECONDS = 0.35


class RegexSearchError(RuntimeError):
    error_name = "REGEX_SEARCH_FAILED"


class RegexSearchInvalid(RegexSearchError):
    error_name = "REGEX_INVALID"


class RegexSearchTimeout(RegexSearchError):
    error_name = "REGEX_SEARCH_TIMEOUT"

    def __init__(self, *args: Any) -> None:
        super().__init__(*args)
        # ISS-12: a deadline used to destroy every match the child had already
        # found -- the results list lived in the killed subprocess and was
        # pickled only after the WHOLE scan finished. _run_isolated now fills
        # these from the child's last checkpoint AFTER reaping it, so a search
        # that ran out of budget on a large repo still reports what it saw.
        self.partial_results: list[dict] = []
        self.records_scanned: int = 0
        self.records_total: int = 0
        self.partial_checkpoint_age_ms: int | None = None


class RegexSearchBudgetExceeded(RegexSearchError):
    def __init__(self, message: str, error_name: str) -> None:
        super().__init__(message)
        self.error_name = error_name


_WORKER_INPUT_PATH: str | None = None
_WORKER_OUTPUT_PATH: str | None = None


def normalize_deadline_seconds(value: Any) -> float:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        parsed = DEFAULT_DEADLINE_SECONDS
    if not math.isfinite(parsed):
        parsed = DEFAULT_DEADLINE_SECONDS
    return max(MIN_DEADLINE_SECONDS, min(parsed, MAX_DEADLINE_SECONDS))


def validate_pattern_budget(pattern: str) -> None:
    if len(pattern) > MAX_PATTERN_CHARS:
        raise RegexSearchBudgetExceeded(
            f"regex pattern exceeds {MAX_PATTERN_CHARS} characters",
            "REGEX_PATTERN_BUDGET_EXCEEDED",
        )


def validate_next_record_budget(record_count: int, total_bytes: int, payload_size: int) -> int:
    if record_count >= MAX_RECORDS:
        raise RegexSearchBudgetExceeded(
            f"regex input exceeds {MAX_RECORDS} records",
            "REGEX_INPUT_BUDGET_EXCEEDED",
        )
    safe_size = max(0, int(payload_size))
    next_total = total_bytes + safe_size
    if next_total > MAX_INPUT_BYTES:
        raise RegexSearchBudgetExceeded(
            f"regex input exceeds {MAX_INPUT_BYTES} bytes",
            "REGEX_INPUT_BUDGET_EXCEEDED",
        )
    return next_total


def validate_line_budget(payload: bytes) -> None:
    # The unit is the line the worker searches. split_lines() breaks on CRLF, CR
    # and LF, and bytes.splitlines() breaks on exactly those three, line ends
    # excluded. F5 2026-09-25: a split on LF alone read a CR-separated file as one
    # line and refused it, though the worker searches it as short lines.
    if any(len(line) > MAX_LINE_BYTES for line in payload.splitlines()):
        raise RegexSearchBudgetExceeded(
            f"regex line exceeds {MAX_LINE_BYTES} bytes",
            "REGEX_LINE_BUDGET_EXCEEDED",
        )


def _timeout(deadline_seconds: float) -> RegexSearchTimeout:
    return RegexSearchTimeout(
        f"regex search exceeded hard deadline of {deadline_seconds:.3f}s",
    )


def _remaining(deadline_at: float, deadline_seconds: float) -> float:
    remaining = deadline_at - time.monotonic()
    if remaining <= 0:
        raise _timeout(deadline_seconds)
    return remaining


def _materialize_records(
    records: Iterable[tuple[str, str, bytes]],
    *,
    deadline_at: float,
    deadline_seconds: float,
    validate_lines: bool = True,
) -> list[tuple[str, str, bytes]]:
    """Materialize all records with budget checks.

    ISS-20: the previous docstring claimed "R38 OPTIMIZATION: per-record line
    validation removed" while the loop below still called validate_line_budget
    on every payload -- it described a change that was never made, so the
    O(total_bytes) splitlines() it claims to have removed was still charged to
    the deadline, a SECOND time, after the parent had already paid for it. The
    check itself must stay reachable: it is what makes an oversize line fail
    before a child is ever spawned. `validate_lines=False` is the caller
    asserting it ran the same check while reading the payloads.
    """
    materialized: list[tuple[str, str, bytes]] = []
    total_bytes = 0
    for relative_path, label, payload in records:
        _remaining(deadline_at, deadline_seconds)
        if not isinstance(payload, (bytes, bytearray, memoryview)):
            raise RegexSearchError("regex record payload must be bytes")
        payload_bytes = bytes(payload)
        total_bytes = validate_next_record_budget(
            len(materialized), total_bytes, len(payload_bytes),
        )
        if validate_lines:
            validate_line_budget(payload_bytes)
        materialized.append((relative_path, label, payload_bytes))
    _remaining(deadline_at, deadline_seconds)
    return materialized


def _dump_atomic(path: str | None, payload: tuple) -> None:
    """ISS-12: write a pickle a concurrent reader can never see half-written.

    The parent reads this file after killing the child on deadline, which can
    land in the middle of a flush. os.replace is atomic on Windows and POSIX,
    so the reader always gets a whole pickle -- the previous checkpoint or the
    new one. Without it a truncated file would raise "output corrupt" and lose
    the earlier checkpoint too. Failures are swallowed on purpose: a missing
    checkpoint degrades to the old empty-handed timeout, it must never become a
    second error on top of the real one.
    """
    if not path:
        return
    # Deterministic sibling name: exactly one process ever writes a given
    # output_path, so there is no collision, and the parent can unlink this
    # leftover if the child is killed mid-flush.
    tmp_path = f"{path}.part"
    try:
        with open(tmp_path, "wb") as fh:
            pickle.dump(payload, fh)
            fh.flush()
        os.replace(tmp_path, path)
    except Exception:
        with contextlib.suppress(OSError):
            os.unlink(tmp_path)


def _bootstrap(input_path: str, output_path: str) -> None:
    global _WORKER_INPUT_PATH, _WORKER_OUTPUT_PATH
    _WORKER_INPUT_PATH = input_path
    _WORKER_OUTPUT_PATH = output_path
    _worker()


def _worker() -> None:
    try:
        with open(_WORKER_INPUT_PATH, "rb") as fh:
            operation, pattern, flags, records, max_results, context_lines = pickle.load(fh)
        try:
            compiled = re.compile(pattern, flags)
        except re.error as exc:
            payload_out = ("invalid", str(exc))
        else:
            if operation == "preflight":
                payload_out = ("ok", True)
            elif operation == "search":
                results: list[dict] = []
                total_records = len(records)
                scanned = 0
                flushed_count = 0
                last_flush = time.monotonic()
                for _relative_path, label, payload in records:
                    lines = split_lines(payload.decode("utf-8", errors="ignore"))
                    for index, line in enumerate(lines, 1):
                        if compiled.search(line):
                            before = ""
                            after = ""
                            if context_lines:
                                before = "\n".join(
                                    item.strip()
                                    for item in lines[max(0, index - 1 - context_lines):index - 1]
                                )
                                after = "\n".join(
                                    item.strip()
                                    for item in lines[index:min(len(lines), index + context_lines)]
                                )
                            results.append({
                                "file": label,
                                "line": index,
                                "match": line.strip(),
                                "context_before": before,
                                "context_after": after,
                            })
                            if len(results) >= max_results:
                                break
                    scanned += 1
                    if len(results) >= max_results:
                        break
                    # ISS-12: checkpoint BETWEEN files only, so a salvaged
                    # partial set is always a whole-file prefix -- never half a
                    # file, which would make the counts lie about coverage.
                    now = time.monotonic()
                    # The FIRST match is flushed immediately, not on the
                    # interval: catastrophic backtracking happens inside a
                    # single compiled.search() call, which no Python-level hook
                    # can interrupt, so one pathological file can consume the
                    # whole remaining budget. Without the first-match flush the
                    # matches found before it would still be lost -- the exact
                    # failure this fix exists to remove.
                    due = flushed_count == 0 or now - last_flush >= _CHECKPOINT_INTERVAL_SECONDS
                    if results and due and flushed_count != len(results):
                        last_flush = now
                        flushed_count = len(results)
                        _dump_atomic(
                            _WORKER_OUTPUT_PATH,
                            ("partial", results, scanned, total_records, time.time()),
                        )
                payload_out = ("ok", results)
            else:
                payload_out = ("error", f"unsupported regex worker operation: {operation}")
    except BaseException as exc:
        payload_out = ("error", f"{type(exc).__name__}: {exc}")
    # ISS-12: atomic, because the parent may be reading a checkpoint right now.
    _dump_atomic(_WORKER_OUTPUT_PATH, payload_out)


# #33 2026-09-25: the regex child used to be a multiprocessing "spawn" Process,
# and spawn rebuilds the PARENT's __main__ inside every child before it runs
# the target. Measured: the daemon (started as a script) re-imported
# orchestrator_server.py in each child -- 2617 ms for a one-file search, and
# that import rewrote the live daemon's .orchestrator_cache/mutation_token.txt;
# a test runner calling pytest.main() at top level re-ran its whole test file
# inside each child until the 8 s and 15 s deadlines fired as
# REGEX_SEARCH_TIMEOUT. `python -m pytest` is exempt from that re-run, which is
# why the suite stayed green. The child now starts from an explicit entry that
# imports this module and nothing else, so whatever the parent's __main__ is
# can no longer change what a search does or costs (~55 ms per child).
#
# -I ignores every PYTHON* variable -- a leaked PYTHONHOME kills an interpreter
# in init, before it can report anything -- and keeps the parent's cwd and
# script directory off sys.path; the repo root is passed in explicitly instead.
# The child sees no venv packages, which _worker does not need: keep this
# module (and orchestrator.text_lines) stdlib-only.
_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_CHILD_ENTRY = (
    "import sys; sys.path.insert(0, sys.argv[1]); "
    "from orchestrator.search_regex_worker import _bootstrap; "
    "_bootstrap(sys.argv[2], sys.argv[3])"
)


def _child_executable() -> str:
    """The base interpreter rather than a venv's Scripts/python.exe.

    On Windows that file is a redirector that starts the base interpreter as
    ITS child, so each search would cost two processes and ~15 ms more (measured
    ~70 vs ~55 ms), and the pid we report would be the redirector's. That is
    cost, not correctness: terminating the redirector takes its child down with
    it (measured -- no descendant survived), so returning sys.executable passes
    every test and is a known equivalent mutant. multiprocessing's spawn used
    _base_executable as well; under -I the venv adds nothing to a stdlib-only
    child.
    """
    base = getattr(sys, "_base_executable", "")
    return base if base and os.path.isfile(base) else sys.executable


class _Helper:
    """The slice of multiprocessing.Process that _run_isolated and
    _stop_and_reap drive, over a plain subprocess started from _CHILD_ENTRY."""

    def __init__(self, name: str, input_path: str, output_path: str) -> None:
        self.name = name
        self._paths = (input_path, output_path)
        self._popen: subprocess.Popen | None = None

    @property
    def pid(self) -> int | None:
        return None if self._popen is None else self._popen.pid

    @property
    def exitcode(self) -> int | None:
        return None if self._popen is None else self._popen.poll()

    def start(self) -> None:
        # stdout stays closed: in the MCP stdio host stdout IS the protocol
        # channel. stderr is inherited so a child that crashes leaves a trace.
        self._popen = subprocess.Popen(
            [_child_executable(), "-I", "-c", _CHILD_ENTRY, _REPO_ROOT, *self._paths],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )

    def is_alive(self) -> bool:
        return self._popen is not None and self._popen.poll() is None

    def join(self, timeout: float | None = None) -> None:
        if self._popen is not None:
            with contextlib.suppress(subprocess.TimeoutExpired):
                self._popen.wait(timeout)

    def terminate(self) -> None:
        if self._popen is not None:
            with contextlib.suppress(OSError):
                self._popen.terminate()

    def kill(self) -> None:
        if self._popen is not None:
            with contextlib.suppress(OSError):
                self._popen.kill()

    def close(self) -> None:
        if self.is_alive():
            raise ValueError(f"regex helper is still running: pid={self.pid}")
        self._popen = None


def _stop_and_reap(process: _Helper) -> None:
    if process.is_alive():
        process.terminate()
        process.join(_REAP_GRACE_SECONDS)
    if process.is_alive():
        process.kill()
        process.join(_REAP_GRACE_SECONDS)
    if process.is_alive():
        raise RegexSearchError(f"regex helper could not be reaped: pid={process.pid}")
    process.join(_REAP_GRACE_SECONDS)
    if process.is_alive():
        raise RegexSearchError(f"regex helper remained alive after bounded join: pid={process.pid}")
    try:
        process.close()
    except (ValueError, OSError):
        pass


def _attach_partial(error: RegexSearchTimeout, output_path: str) -> None:
    """ISS-12: salvage the child's last checkpoint into the timeout error.

    MUST be called only after _stop_and_reap: while the child lives the file
    can still change, and a half-read checkpoint is worse than none. A file
    holding a FINAL ("ok", results) tuple is also accepted -- that is a child
    which finished in the same instant we declared the deadline, and throwing
    its complete result set away would be the exact bug this fixes.
    """
    if not output_path:
        return
    try:
        with open(output_path, "rb") as fh:
            record = pickle.load(fh)
    except Exception:
        return
    if not isinstance(record, tuple) or not record:
        return
    if record[0] == "ok" and len(record) == 2 and isinstance(record[1], list):
        error.partial_results = record[1]
        error.partial_checkpoint_age_ms = 0
        return
    if record[0] != "partial" or len(record) != 5 or not isinstance(record[1], list):
        return
    results, scanned, total, stamped_at = record[1], record[2], record[3], record[4]
    error.partial_results = results
    with contextlib.suppress(TypeError, ValueError):
        error.records_scanned = int(scanned or 0)
    with contextlib.suppress(TypeError, ValueError):
        error.records_total = int(total or 0)
    with contextlib.suppress(TypeError, ValueError):
        error.partial_checkpoint_age_ms = max(
            0, int((time.time() - float(stamped_at)) * 1000),
        )


def _decode_worker_result(status: str, payload: Any) -> Any:
    if status == "invalid":
        raise RegexSearchInvalid(str(payload))
    if status != "ok":
        raise RegexSearchError(str(payload))
    return payload


def _run_isolated(payload: tuple, timeout_seconds: float) -> Any:
    timeout_seconds = max(0.001, float(timeout_seconds))
    deadline_at = time.monotonic() + timeout_seconds
    input_fd = -1
    output_fd = -1
    input_path = ""
    output_path = ""
    process: _Helper | None = None
    started = False
    primary_error: BaseException | None = None
    try:
        input_fd, input_path = tempfile.mkstemp(prefix="srwi_", suffix=".bin")
        output_fd, output_path = tempfile.mkstemp(prefix="srwo_", suffix=".bin")
        with os.fdopen(input_fd, "wb") as fh:
            pickle.dump(payload, fh)
        input_fd = -1
        os.close(output_fd)
        output_fd = -1

        process = _Helper(
            f"search-regex-{time.monotonic_ns()}", input_path, output_path,
        )
        try:
            process.start()
            started = True
            process.join(_remaining(deadline_at, timeout_seconds))
            if process.is_alive():
                raise _timeout(timeout_seconds)
            if process.exitcode != 0:
                raise RegexSearchError(
                    f"regex helper exited with exit_code={process.exitcode}",
                )
            try:
                with open(output_path, "rb") as fh:
                    status, result = pickle.load(fh)
            except FileNotFoundError as exc:
                raise RegexSearchError(
                    f"regex helper produced no output (exit_code={process.exitcode})",
                ) from exc
            except Exception as exc:
                raise RegexSearchError(
                    f"regex helper output corrupt (exit_code={process.exitcode}): {exc}",
                ) from exc
            return _decode_worker_result(status, result)
        except BaseException as exc:
            primary_error = exc
            raise
        finally:
            if process is not None:
                try:
                    if started or getattr(process, "pid", None) is not None:
                        _stop_and_reap(process)
                    else:
                        process.close()
                except (ValueError, OSError, RegexSearchError) as reap_error:
                    if primary_error is None:
                        raise
                    primary_error.add_note(f"regex helper cleanup also failed: {reap_error}")
                # ISS-12: the child is dead now, so its last checkpoint can no
                # longer change -- salvage it BEFORE the outer finally unlinks
                # output_path, otherwise the deadline destroys the evidence.
                if isinstance(primary_error, RegexSearchTimeout):
                    _attach_partial(primary_error, output_path)
    finally:
        for fd in (input_fd, output_fd):
            if fd >= 0:
                with contextlib.suppress(OSError):
                    os.close(fd)
        for path in (input_path, output_path):
            if path:
                with contextlib.suppress(OSError):
                    os.unlink(path)
        # ISS-12: a child killed mid-checkpoint leaves this sibling behind.
        if output_path:
            with contextlib.suppress(OSError):
                os.unlink(f"{output_path}.part")


def preflight_pattern(
    pattern: str,
    flags: int,
    *,
    deadline_seconds: float = DEFAULT_DEADLINE_SECONDS,
) -> None:
    validate_pattern_budget(pattern)
    deadline = normalize_deadline_seconds(deadline_seconds)
    _run_isolated(("preflight", pattern, flags, [], 1, 0), deadline)


def search_records(
    pattern: str,
    flags: int,
    records: Iterable[tuple[str, str, bytes]],
    *,
    max_results: int,
    context_lines: int,
    deadline_seconds: float = DEFAULT_DEADLINE_SECONDS,
    records_pre_validated: bool = False,
) -> list[dict]:
    """Match `pattern` over `records` in an isolated child under a hard deadline.

    ISS-20: `records_pre_validated=True` is the caller's assertion that it
    already ran `validate_line_budget` on every payload as it read it -- which
    is exactly what orchestrator/tools/search.py does, per file, before it
    appends. Re-splitting every payload here is O(total_bytes) of duplicate
    work charged to this deadline, and on an HDD-backed repo it was a
    measurable slice of the budget the matcher itself needed. The default
    stays False: an un-audited caller must still be line-budget checked HERE,
    before a child is ever spawned.
    """
    validate_pattern_budget(pattern)
    deadline = normalize_deadline_seconds(deadline_seconds)
    deadline_at = time.monotonic() + deadline
    materialized = _materialize_records(
        records,
        deadline_at=deadline_at,
        deadline_seconds=deadline,
        validate_lines=not records_pre_validated,
    )
    remaining = _remaining(deadline_at, deadline)
    return _run_isolated(
        ("search", pattern, flags, materialized, max_results, context_lines),
        remaining,
    )
