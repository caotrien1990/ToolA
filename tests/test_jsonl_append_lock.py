"""The JSONL append lock, tested at the contention level that actually broke it.

`test_append_jsonl_parallel_process_thread_keeps_all_lines` used one process and
one thread, and it PASSED against the broken lock -- two writers rarely collide
hard enough. The bug needed four processes plus four threads to surface, and then
it lost exactly half the lines with no corruption at all, so nothing in the file
said anything had gone wrong.

Four distinct defects are pinned here, all in the same handful of lines:

* `LK_LOCK` gives up and raises instead of waiting, and the raise escaped
  `append_jsonl`, so a contended append was simply dropped;
* same-process contention can never be waited out, because the OS grants byte
  ranges per process and therefore counts the range as already ours;
* the lock byte lived in the DATA file at offset 0, which both blocks readers in
  other processes and sits exactly where an append to an empty file writes. Moving
  it to a high offset was tried and is not a fix: grown past that offset the data
  file swallows the lock byte again and a foreign read is refused with EACCES, so
  the tests below assert a separate lock file rather than any chosen number;
* `os.fsync` sat INSIDE the critical section, which every test here passed with,
  because none of them measured how long the lock is held -- 125ms per append
  against 0.2ms without it, so the losers under contention timed out and the
  cause looked like the lock algorithm for far longer than it should have.

Every count here is chosen because the fixed code passes it and the old code did
not; lowering them re-opens the hole the old test left.
"""
from __future__ import annotations

import json
import multiprocessing as mp
import os
import subprocess
import sys
import threading
import time
from pathlib import Path

import pytest

from orchestrator.storage import jsonl_store
from orchestrator.storage.jsonl_store import append_jsonl

# Four writers of each kind: the count at which the pre-fix lock dropped half its
# appends. Two of each was not enough to fail.
WRITERS = 4
PER_WRITER = 40


def _spam(path: str, prefix: str, count: int, errors: list) -> None:
    for index in range(count):
        try:
            append_jsonl(path, {"prefix": prefix, "index": index})
        except BaseException as exc:  # noqa: BLE001 - the failure IS the finding
            errors.append("%s[%d]: %r" % (prefix, index, exc))
            return


def test_heavy_process_and_thread_contention_loses_no_append(tmp_path: Path) -> None:
    """Not "does it corrupt" but "does every append arrive" -- the loss was clean."""
    target = tmp_path / "events.jsonl"
    manager = mp.Manager()
    errors = manager.list()

    processes = [
        mp.Process(target=_spam, args=(str(target), "p%d" % i, PER_WRITER, errors))
        for i in range(WRITERS)
    ]
    threads = [
        threading.Thread(target=_spam, args=(str(target), "t%d" % i, PER_WRITER, errors))
        for i in range(WRITERS)
    ]
    for worker in processes + threads:
        worker.start()
    for worker in threads:
        worker.join(timeout=180)
    for worker in processes:
        worker.join(timeout=180)

    assert not [t for t in threads if t.is_alive()], "a thread never finished"
    assert [p.exitcode for p in processes] == [0] * WRITERS
    assert list(errors) == [], "an append raised instead of waiting its turn"

    text = target.read_text(encoding="utf-8")
    lines = [line for line in text.splitlines() if line.strip()]
    assert len(lines) == 2 * WRITERS * PER_WRITER, "appends were silently dropped"
    events = [json.loads(line) for line in lines]
    assert {(e["prefix"], e["index"]) for e in events} == {
        ("%s%d" % (kind, i), index)
        for kind in ("p", "t")
        for i in range(WRITERS)
        for index in range(PER_WRITER)
    }


def test_second_handle_in_this_process_waits_instead_of_failing(tmp_path: Path) -> None:
    """The exact shape that raised EDEADLK: two handles, one process, one file.

    `msvcrt.locking` grants byte ranges per process, so the second handle used to
    be refused instantly. Waiting cannot fix that -- only in-process
    serialisation can.
    """
    target = tmp_path / "one.jsonl"
    target.touch()
    key = jsonl_store._lock_key(target)
    entered = threading.Event()
    released = threading.Event()
    second_ok: list = []

    def hold() -> None:
        with target.open("a+", encoding="utf-8", newline="\n"):
            with jsonl_store._exclusive_file_lock(target, key):
                entered.set()
                released.wait(timeout=30)

    holder = threading.Thread(target=hold)
    holder.start()
    assert entered.wait(timeout=10), "the first lock never engaged"

    def contend() -> None:
        try:
            with target.open("a+", encoding="utf-8", newline="\n"):
                with jsonl_store._exclusive_file_lock(target, key, 20.0):
                    second_ok.append(True)
        except BaseException as exc:  # noqa: BLE001
            second_ok.append(exc)

    other = threading.Thread(target=contend)
    other.start()
    time.sleep(0.3)
    assert second_ok == [], "the second handle must block, not sail through"

    released.set()
    holder.join(timeout=30)
    other.join(timeout=30)
    assert second_ok == [True], "the second handle failed: %r" % (second_ok,)


def test_reentering_the_same_lock_on_one_thread_does_not_hang(tmp_path: Path) -> None:
    """Nesting must not self-block, or one nested append freezes a worker forever.

    Deliberately on an EMPTY file. While the lock sat on byte 0 this passed on any
    file that already held a line and failed on a fresh one, because offset 0 is
    at once the lock byte and where the first append lands.
    """
    target = tmp_path / "nested.jsonl"
    target.touch()
    assert target.stat().st_size == 0, "the empty case is the point of this test"
    key = jsonl_store._lock_key(target)

    with target.open("a+", encoding="utf-8", newline="\n"):
        with jsonl_store._exclusive_file_lock(target, key, 5.0):
            with target.open("a+", encoding="utf-8", newline="\n") as inner:
                with jsonl_store._exclusive_file_lock(target, key, 5.0):
                    inner.seek(0, os.SEEK_END)
                    inner.write('{"nested": true}\n')

    assert json.loads(target.read_text(encoding="utf-8").strip()) == {"nested": True}
    # The bookkeeping must unwind fully, or the next caller believes it already
    # holds the OS range and skips locking altogether.
    assert not getattr(jsonl_store._REENTRY, "depth", {}).get(key)


def test_fsync_runs_after_the_lock_is_released(tmp_path: Path, monkeypatch) -> None:
    """The durability sync must not be inside the critical section.

    This is the defect the whole contention suite missed. Every test above still
    passed with `fsync` under the lock, because none of them measured how LONG the
    lock is held -- and on this disk `fsync` costs 125ms against 0.2ms for a
    flush, so eight writers needed ~60s of strictly serial work and the losers
    timed out. It read as a lock-algorithm bug for a long time.

    Asserted structurally, not by timing: `_REENTRY.depth` carries the key while
    the lock is held, so its state at the moment `fsync` is called says exactly
    which side of the release the sync landed on. A timing assertion would be
    flaky on a fast disk; this cannot be.
    """
    target = tmp_path / "durable.jsonl"
    key = jsonl_store._lock_key(target)
    real_fsync = os.fsync
    held_during_fsync: list = []

    def watching_fsync(fd: int) -> None:
        held_during_fsync.append(getattr(jsonl_store._REENTRY, "depth", {}).get(key, 0))
        real_fsync(fd)

    monkeypatch.setattr(jsonl_store.os, "fsync", watching_fsync)
    append_jsonl(target, {"durable": True})

    # Called exactly once -- a "fix" that simply drops fsync loses durability.
    assert len(held_during_fsync) == 1, "fsync ran %d times" % len(held_during_fsync)
    assert held_during_fsync[0] == 0, "fsync ran while the lock was still held"
    assert jsonl_store.read_tail(target, limit=1) == [{"durable": True}]


def test_a_slow_fsync_does_not_delay_the_next_writer(tmp_path: Path,
                                                    monkeypatch) -> None:
    """The consequence of the above, measured the only way that is not flaky.

    Wall-clock budgets do not work here: `fsync` costs ~125ms on this disk whether
    or not it is under the lock, so a throughput assertion measures the DISK and
    swings with unrelated load -- an earlier draft of this test read 10s idle and
    59s under load, which is a coin toss, not a check.

    So the sync is made slow on purpose and the question becomes structural: while
    one writer is inside its `fsync`, can another writer take the lock? Under the
    fixed placement it can, and the second append finishes long before the first
    writer's sync does. With `fsync` back inside the lock the second writer is
    stuck behind the full sync, which is exactly the queue that starved the
    original loser threads.
    """
    target = tmp_path / "slowsync.jsonl"
    real_fsync = os.fsync
    syncing = threading.Event()
    let_sync_finish = threading.Event()

    def slow_fsync(fd: int) -> None:
        real_fsync(fd)
        syncing.set()
        let_sync_finish.wait(timeout=30)

    monkeypatch.setattr(jsonl_store.os, "fsync", slow_fsync)

    def first() -> None:
        append_jsonl(target, {"who": "first"})

    holder = threading.Thread(target=first)
    holder.start()
    assert syncing.wait(timeout=15), "the first writer never reached fsync"

    # First writer is parked inside fsync. Its bytes are already flushed, so the
    # lock has no reason to still be held -- prove that by using it.
    second_done = threading.Event()

    def second() -> None:
        monkeypatch.setattr(jsonl_store.os, "fsync", real_fsync)
        append_jsonl(target, {"who": "second"})
        second_done.set()

    other = threading.Thread(target=second)
    other.start()
    took_the_lock = second_done.wait(timeout=10)

    let_sync_finish.set()
    holder.join(timeout=30)
    other.join(timeout=30)

    assert took_the_lock, "the second writer waited for the first writer's fsync"
    written = [json.loads(l) for l in
               target.read_text(encoding="utf-8").splitlines() if l.strip()]
    assert {row["who"] for row in written} == {"first", "second"}



@pytest.mark.skipif(os.name != "nt", reason="byte-range locks are Windows-specific")
def test_giving_up_on_a_foreign_holder_says_timeout_not_deadlock(tmp_path: Path) -> None:
    """A busy file is a timeout. EDEADLK named a deadlock that was never there."""
    target = tmp_path / "held.jsonl"
    target.touch()
    holder_script = "\n".join([
        "import msvcrt, sys, time",
        "h = open(sys.argv[1], 'a+b')",
        "h.seek(0)",
        "msvcrt.locking(h.fileno(), msvcrt.LK_NBLCK, 1)",
        "print('locked', flush=True)",
        "time.sleep(20)",
    ])
    holder = subprocess.Popen(
        [sys.executable, "-c", holder_script,
         str(jsonl_store._sidecar_path(target))],
        stdout=subprocess.PIPE, text=True,
    )
    try:
        assert holder.stdout is not None
        assert holder.stdout.readline().strip() == "locked", "helper never locked"

        started = time.monotonic()
        with pytest.raises(TimeoutError) as caught:
            append_jsonl(target, {"blocked": True}, lock_timeout=1.0)
        waited = time.monotonic() - started

        assert "holds the JSONL lock byte" in str(caught.value)
        # It waited, and it stopped when told to -- not on the CRT's own ~9s.
        assert 0.5 <= waited < 8.0, "waited %.2fs" % waited
    finally:
        holder.kill()
        holder.wait(timeout=10)


@pytest.mark.skipif(os.name != "nt", reason="byte-range locks are Windows-specific")
def test_the_lock_is_actually_taken_and_released(tmp_path: Path) -> None:
    """Guard against a fix that "passes" by never locking across processes at all."""
    target = tmp_path / "probe.jsonl"
    target.touch()
    key = jsonl_store._lock_key(target)
    probe = "\n".join([
        "import msvcrt, sys",
        "h = open(sys.argv[1], 'a+b')",
        "h.seek(0)",
        "try:",
        "    msvcrt.locking(h.fileno(), msvcrt.LK_NBLCK, 1)",
        "    print('free')",
        "except OSError:",
        "    print('held')",
    ])

    def ask() -> str:
        done = subprocess.run(
            [sys.executable, "-c", probe,
             str(jsonl_store._sidecar_path(target))],
            capture_output=True, text=True, timeout=60,
        )
        return done.stdout.strip()

    with jsonl_store._exclusive_file_lock(target, key, 5.0):
        assert ask() == "held", "another process could still take the range"
    assert ask() == "free", "the lock byte was never released"


@pytest.mark.skipif(os.name != "nt", reason="byte-range locks are Windows-specific")
def test_a_held_append_lock_does_not_block_readers_in_other_processes(
        tmp_path: Path) -> None:
    """The council dashboard tails these files while the broker appends to them.

    A Windows byte-range lock denies reads through every OTHER handle, so a lock
    on byte 0 turned each append into a read outage on the log being tailed.
    Measured before the fix: a foreign `open(...).read()` raised PermissionError.
    Reading is the common case and must never see the writer's lock.
    """
    target = tmp_path / "tailed.jsonl"
    append_jsonl(target, {"line": 1})
    key = jsonl_store._lock_key(target)
    reader = "\n".join([
        "import sys",
        "try:",
        "    print(len(open(sys.argv[1], 'rb').read()))",
        "except OSError as exc:",
        "    print('DENIED %s' % exc)",
    ])

    with jsonl_store._exclusive_file_lock(target, key, 5.0):
        done = subprocess.run(
            [sys.executable, "-c", reader, str(target)],
            capture_output=True, text=True, timeout=60,
        )
        said = done.stdout.strip()
        assert not said.startswith("DENIED"), "a reader was locked out: %s" % said
        assert int(said) > 0

    # read_tail is that reader, and it must agree.
    assert jsonl_store.read_tail(target, limit=5) == [{"line": 1}]


@pytest.mark.skipif(os.name != "nt", reason="byte-range locks are Windows-specific")
def test_appending_to_an_empty_file_does_not_collide_with_its_own_lock(
        tmp_path: Path) -> None:
    """The first append lands at offset 0, so the lock cannot live there.

    The whole-stack version of the reentry test: nothing exotic, just the ordinary
    first write to a brand-new log through the public function, with a second
    handle open the way a nested append would have one.
    """
    target = tmp_path / "fresh.jsonl"
    append_jsonl(target, {"first": True})
    assert jsonl_store.read_tail(target, limit=1) == [{"first": True}]

    # And the lock lives outside the data file altogether, so no append can reach it
    # and no reader can be denied by it -- neither of which a byte inside the data
    # file can promise. Probed at 2 GiB + 4 KiB against the old fixed offset: the
    # foreign read came back DENIED [Errno 13], i.e. the original outage, on exactly
    # the large busy log that needs the lock most.
    sidecar = jsonl_store._sidecar_path(target)
    assert sidecar != target
    assert sidecar.name.endswith(jsonl_store.LOCK_SIDECAR_SUFFIX)
