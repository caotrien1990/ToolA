"""JSONL append/tail helpers, written for the broker race incidents.

The lock here guards ONE byte of the target file, and it has to survive two
kinds of contention that Windows reports through the same errno:

* Two handles inside THIS process. ``msvcrt.locking`` ownership is per-process,
  not per-handle, so the second handle is refused while the first holds the byte
  -- and it does acquire once that one releases, which an earlier note here
  denied.
* A handle in ANOTHER process. Same refusal, same cure: wait for the release.

``LK_LOCK`` collapses both into ``OSError(EDEADLK)`` -- immediately for the
first, after its own fixed ~9s of internal retries for the second -- and the old
code let that escape ``append_jsonl``, so a contended append was simply LOST.
Measured before this fix: 4 processes x 4 threads x 60 appends kept 240 of 480
lines with ZERO corrupt lines -- silent loss rather than detectable damage, which
is why nothing upstream ever reported it.

Scope, so this file is not read as more load-bearing than it is: NOTHING in the
package imports it today (checked by walking every import in the tree; the only
importers are its two test modules). The live ``.jsonl`` logs are written
elsewhere -- ``session/logs.py:_log_to_file`` rewrites the whole file under a
mutex, while ``_council_usage_append`` and ``council/route_metrics.py`` append
with no lock at all and are safe only because the council drain that calls them
is serialised per agent. Those two are the ones to fix, or route through here,
before a second concurrent writer ever reaches them: the same probe on a plain
``open(path, "a")`` kept 280 of 480 lines and produced a line with its leading
brace overwritten, with no exception and exit code 0.

The two cases are therefore separated instead of conflated. ``LK_NBLCK`` under an
explicit deadline replaces ``LK_LOCK``, so the wait is this module's decision
rather than one buried in the CRT, and giving up raises ``TimeoutError``, which
says what actually happened; ``EDEADLK`` claimed a deadlock that was never there.
A per-path in-process ``RLock`` sits in front of it to queue threads of this
process and to make a nested append reentrant instead of self-blocking.

The lock also moved OUT of the data file entirely, which was two bugs at once. A
Windows byte-range lock denies READS through every other handle, so while a
broker append held byte 0 a concurrent ``read_tail`` in another process got
``PermissionError`` on the council log it is supposed to be tailing -- measured
directly. And byte 0 is inside the data: appending to an EMPTY file writes at
offset 0, so the writer collided with its own lock and only ever worked because
the file was usually non-empty. Moving the byte within the data file just
relocates the trap, as the ``LOCK_SIDECAR_SUFFIX`` note below records; the fix is
a separate lock file, which is what ``orchestrator/mission/instance_lock.py``
already does for precisely this caveat.
"""

from __future__ import annotations

import contextlib
import json
import os
import threading
import time
from pathlib import Path
from typing import Any, Iterator

# Cross-process wait budget, kept above the old CRT behaviour (~9s) so this fix
# cannot turn a previously-succeeding contended append into a timeout.
LOCK_TIMEOUT_SECONDS = 15.0

# The lock lives in a sidecar file, never in the data file, and byte 0 of the
# sidecar is fine because nothing else ever reads it.
#
# Locking the data file cannot work here, at any offset. A Windows byte-range
# lock denies reads through every OTHER handle, so whichever byte is chosen must
# be one no reader touches -- but `read_tail` reads the whole tail, so the only
# safe byte is one past EOF, and "past EOF" is a moving target. An earlier
# revision picked a fixed offset near 2 GiB and justified it with the assumption
# that these logs rotate long before that. Tested rather than assumed: grow the
# file past that offset and a foreign read fails with EACCES again, i.e. the
# original bug returns for exactly the large busy log that needs the lock most.
#
# `mission/instance_lock.py` and `orchestrator_server.py` already take this exit
# for the same reason, so this is the house pattern, not a new one.
LOCK_SIDECAR_SUFFIX = ".lock"

_LOCK_REGISTRY_GUARD = threading.Lock()
_PATH_LOCKS: dict[str, threading.RLock] = {}
_REENTRY = threading.local()


def _lock_key(target: Path) -> str:
    """Normalise so two spellings of one file share one lock, as the OS does."""
    try:
        resolved = str(target.resolve())
    except OSError:  # pragma: no cover - permission/path edge
        resolved = os.path.abspath(str(target))
    return os.path.normcase(resolved)


def _path_lock(key: str) -> threading.RLock:
    with _LOCK_REGISTRY_GUARD:
        lock = _PATH_LOCKS.get(key)
        if lock is None:
            lock = threading.RLock()
            _PATH_LOCKS[key] = lock
        return lock


def _sidecar_path(target: Path) -> Path:
    return target.with_name(target.name + LOCK_SIDECAR_SUFFIX)


@contextlib.contextmanager
def _exclusive_file_lock(target: Path, key: str,
                         timeout: float = LOCK_TIMEOUT_SECONDS) -> Iterator[None]:
    """Hold the sidecar lock for ``target``, serialising this process then the OS.

    The in-process lock is a queue, not a correctness requirement, and an earlier
    version of this docstring got that wrong. Probed directly: a second handle in
    this process is refused with EACCES while the byte is held, and acquires
    normally once it is released -- so the retry loop below would win on its own.
    Dropping the lock and re-running the suite confirms it: everything still
    passes. What it buys is that same-process waiters queue on a futex instead of
    burning the backoff loop, which matters because threads inside one broker
    contend far more often than separate processes do.

    Reentry is the part that genuinely needs bookkeeping: a nested append on one
    thread already owns the OS range, and re-asking for it is refused exactly like
    a foreign handle would be, so the depth counter must skip the OS call rather
    than deadlock against itself.
    """
    depth = getattr(_REENTRY, "depth", None)
    if depth is None:
        depth = _REENTRY.depth = {}

    with _path_lock(key):
        # RLock lets one thread re-enter; the OS range is already ours then, and
        # re-asking for it is refused exactly like any foreign handle.
        nested = depth.get(key, 0)
        depth[key] = nested + 1
        try:
            if nested or os.name != "nt":
                yield
                return

            import msvcrt

            sidecar = _sidecar_path(target)
            sidecar.parent.mkdir(parents=True, exist_ok=True)
            deadline = time.monotonic() + max(0.0, float(timeout))
            delay = 0.005
            # "a+b" creates the sidecar if absent and never truncates an existing
            # one. Its contents are irrelevant; only the byte range matters, and
            # locking beyond EOF is legal on Windows.
            with open(sidecar, "a+b") as guard:
                while True:
                    guard.seek(0)
                    try:
                        msvcrt.locking(guard.fileno(), msvcrt.LK_NBLCK, 1)
                        break
                    except OSError:
                        remaining = deadline - time.monotonic()
                        if remaining <= 0:
                            raise TimeoutError(
                                "could not lock %s within %.1fs; another process "
                                "holds the JSONL lock byte" % (key, timeout),
                            ) from None
                        time.sleep(min(delay, remaining))
                        delay = min(delay * 2, 0.05)
                try:
                    yield
                finally:
                    guard.seek(0)
                    msvcrt.locking(guard.fileno(), msvcrt.LK_UNLCK, 1)
        finally:
            if depth.get(key, 0) <= 1:
                depth.pop(key, None)
            else:
                depth[key] -= 1


def append_jsonl(path: str | os.PathLike[str], obj: Any,
                 *, lock_timeout: float = LOCK_TIMEOUT_SECONDS) -> None:
    """Append one strict JSON line under lock because broker appends have raced.

    ``fsync`` runs AFTER the lock is dropped, and that placement is the single
    biggest reason contended appends now survive. Measured on this disk: an append
    with ``fsync`` inside the critical section costs 125ms against 0.2ms without
    it, so eight writers needed ~60s of strictly serial work and any deadline
    shorter than that had to fail somebody -- which is the shape of the original
    loss, mistaken at first for a lock-algorithm problem.

    Correctness is unchanged by moving it. The lock exists so two writers cannot
    interleave inside one line; once ``flush`` has handed the bytes to the OS the
    line is whole and ordered for every reader, and ``fsync`` only decides whether
    it survives a power cut. Waiting for the platter while holding the lock makes
    every other writer wait for durability that is not theirs.
    """
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    line = json.dumps(obj, ensure_ascii=False, allow_nan=False) + "\n"
    key = _lock_key(target)
    with target.open("a+", encoding="utf-8", newline="\n") as handle:
        with _exclusive_file_lock(target, key, lock_timeout):
            handle.seek(0, os.SEEK_END)
            handle.write(line)
            handle.flush()
        # Same open handle, so this is the file description that just wrote; only
        # the lock has been released.
        os.fsync(handle.fileno())


def read_tail(path: str | os.PathLike[str], limit: int = 100) -> list[Any]:
    """Return the last JSONL objects in file order without reading huge logs fully."""
    if limit <= 0:
        return []

    target = Path(path)
    try:
        with target.open("rb") as handle:
            handle.seek(0, os.SEEK_END)
            position = handle.tell()
            chunks: list[bytes] = []
            newline_count = 0
            chunk_size = 8192

            while position > 0 and newline_count <= limit:
                read_size = min(chunk_size, position)
                position -= read_size
                handle.seek(position)
                chunk = handle.read(read_size)
                chunks.append(chunk)
                newline_count += chunk.count(b"\n")

        data = b"".join(reversed(chunks))
    except FileNotFoundError:
        return []

    lines = [line for line in data.splitlines() if line.strip()]
    return [json.loads(line.decode("utf-8")) for line in lines[-limit:]]
