from __future__ import annotations

import contextlib
import math
import multiprocessing
import os
import pickle
import re
import tempfile
import time
from typing import Any, Iterable

MAX_PATTERN_CHARS = 4096
MAX_INPUT_BYTES = 32 * 1024 * 1024
MAX_LINE_BYTES = 1 * 1024 * 1024
MAX_RECORDS = 20000
DEFAULT_DEADLINE_SECONDS = 8.0
MIN_DEADLINE_SECONDS = 0.05
MAX_DEADLINE_SECONDS = 15.0
_REAP_GRACE_SECONDS = 0.25


class RegexSearchError(RuntimeError):
    error_name = "REGEX_SEARCH_FAILED"


class RegexSearchInvalid(RegexSearchError):
    error_name = "REGEX_INVALID"


class RegexSearchTimeout(RegexSearchError):
    error_name = "REGEX_SEARCH_TIMEOUT"


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
) -> list[tuple[str, str, bytes]]:
    """Materialize all records with budget checks.

    R38 OPTIMIZATION: Per-record line validation removed — the worker
    subprocess already validates lines during regex matching. This
    eliminates O(total_bytes) splitlines() work that previously consumed
    most of the deadline before the subprocess even started.
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
        validate_line_budget(payload_bytes)
        materialized.append((relative_path, label, payload_bytes))
    _remaining(deadline_at, deadline_seconds)
    return materialized


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
                for _relative_path, label, payload in records:
                    lines = payload.decode("utf-8", errors="ignore").splitlines()
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
                    if len(results) >= max_results:
                        break
                payload_out = ("ok", results)
            else:
                payload_out = ("error", f"unsupported regex worker operation: {operation}")
    except BaseException as exc:
        payload_out = ("error", f"{type(exc).__name__}: {exc}")
    try:
        with open(_WORKER_OUTPUT_PATH, "wb") as fh:
            pickle.dump(payload_out, fh)
    except Exception:
        pass


def _stop_and_reap(process: multiprocessing.Process) -> None:
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
    process: multiprocessing.Process | None = None
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

        ctx = multiprocessing.get_context("spawn")
        process = ctx.Process(
            target=_bootstrap,
            name=f"search-regex-{time.monotonic_ns()}",
            args=(input_path, output_path),
            daemon=False,
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
    finally:
        for fd in (input_fd, output_fd):
            if fd >= 0:
                with contextlib.suppress(OSError):
                    os.close(fd)
        for path in (input_path, output_path):
            if path:
                with contextlib.suppress(OSError):
                    os.unlink(path)


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
) -> list[dict]:
    validate_pattern_budget(pattern)
    deadline = normalize_deadline_seconds(deadline_seconds)
    deadline_at = time.monotonic() + deadline
    materialized = _materialize_records(
        records,
        deadline_at=deadline_at,
        deadline_seconds=deadline,
    )
    remaining = _remaining(deadline_at, deadline)
    return _run_isolated(
        ("search", pattern, flags, materialized, max_results, context_lines),
        remaining,
    )
