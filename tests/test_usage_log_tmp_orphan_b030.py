# -*- coding: utf-8 -*-
"""B030: an abrupt kill orphans `.USAGE_LOG.jsonl.<rand>.tmp` forever.

Measured 2026-08-23, on the real cache dir of [A] itself:

    .orchestrator_cache/.USAGE_LOG.jsonl.*.tmp     132 files, 10,360,372 bytes

Deleting them cleared the symptom; the count regrew 2 -> 4 -> 6 -> 8 on three
consecutive runs of `tests/test_task_epoch_cross_process.py` +
`tests/test_task_epoch_guard.py`, so it is a live leak, not history.

The mechanism is NOT a failing `os.replace`. Three hypotheses were measured and
all three were WRONG:

    A) 6 threads x 60 writes             leaked=0  (the RLock serialises them)
    B) destination held open for read     leaked=0  (PermissionError, except: unlinks)
    C) 6 PROCESSES x ~410 writes          leaked=0  (os.replace is atomic on Windows)

An instrumented `_atomic_write_bytes` -- counting every mkstemp and every
successful replace -- produced the answer:

    LOGS_MADE 13   LOGS_REPLACED 11   probe_exceptions 0

Two tmp files created, no exception raised anywhere. The writer was killed
between `mkstemp` and `os.replace` by the `p.terminate()` in the epoch tests'
`stop_http()` / `MCP.close()`, which on Windows is `TerminateProcess`: no
`except`, no `finally`, no atexit. That is why every "handle the exception
better" fix is dead code for this defect -- the code that would clean up never
runs. The cleanup has to be done by the NEXT writer, not the dying one.

Direction matters: a tmp file may also be a LIVE writer's in-flight file, in
another process, microseconds from `os.replace`. Deleting that one turns a
harmless orphan into a lost log record. So the sweep is bounded by age, and the
tests below pin BOTH directions -- stale is removed, fresh is left alone.
"""

from __future__ import annotations

import os
import shutil
import sys
import tempfile
import time
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from orchestrator.session import logs as L  # noqa: E402


def _orphan(directory: Path, name: str, age_seconds: float) -> Path:
    """Exactly what a killed writer leaves behind: a real mkstemp file, no replace."""
    fd, tmp_name = tempfile.mkstemp(prefix="." + name + ".", suffix=".tmp",
                                    dir=str(directory))
    os.close(fd)
    tmp = Path(tmp_name)
    tmp.write_bytes(b'{"orphan": true}\n')
    old = time.time() - age_seconds
    os.utime(tmp, (old, old))
    return tmp


class TheSweepHasATtlConstantTest(unittest.TestCase):
    def test_the_ttl_is_a_named_constant(self):
        self.assertTrue(hasattr(L, "_TMP_ORPHAN_TTL_SECONDS"),
                        "the age boundary must be nameable, not a magic number")
        self.assertGreaterEqual(L._TMP_ORPHAN_TTL_SECONDS, 60,
                                "too short and a slow live writer gets its file "
                                "deleted from under it")

    def test_the_sweeper_is_callable(self):
        self.assertTrue(callable(getattr(L, "_sweep_orphan_tmp", None)))


class AStaleOrphanIsRemovedByTheNextWriteTest(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp(prefix="b030_stale_"))
        self.addCleanup(shutil.rmtree, self.root, ignore_errors=True)
        self.log = self.root / "USAGE_LOG.jsonl"

    def test_the_next_write_removes_it(self):
        orphan = _orphan(self.root, "USAGE_LOG.jsonl",
                         L._TMP_ORPHAN_TTL_SECONDS + 60)
        self.assertTrue(orphan.exists())
        self.assertTrue(L._log_to_file(self.log, {"n": 1}, max_entries=10))
        self.assertFalse(orphan.exists(), "a stale orphan must not survive a write")

    def test_the_write_itself_still_lands(self):
        _orphan(self.root, "USAGE_LOG.jsonl", L._TMP_ORPHAN_TTL_SECONDS + 60)
        self.assertTrue(L._log_to_file(self.log, {"n": 7}, max_entries=10))
        self.assertIn('"n": 7', self.log.read_text(encoding="utf-8"))

    def test_many_orphans_all_go(self):
        """132 accumulated in production; one write must not clear only one."""
        orphans = [_orphan(self.root, "USAGE_LOG.jsonl",
                           L._TMP_ORPHAN_TTL_SECONDS + 60) for _ in range(12)]
        L._log_to_file(self.log, {"n": 1}, max_entries=10)
        alive = [p.name for p in orphans if p.exists()]
        self.assertEqual(alive, [], alive)

    def test_no_tmp_is_left_after_a_normal_write(self):
        L._log_to_file(self.log, {"n": 1}, max_entries=10)
        leftover = sorted(p.name for p in self.root.glob(".USAGE_LOG.jsonl.*.tmp"))
        self.assertEqual(leftover, [])


class AFreshTmpIsALiveWriterAndMustSurviveTest(unittest.TestCase):
    """The direction that makes this safe. A tmp seconds old belongs to a process
    that is about to `os.replace` it; deleting it loses that process's records and
    turns cosmetic garbage into data loss."""

    def setUp(self):
        self.root = Path(tempfile.mkdtemp(prefix="b030_fresh_"))
        self.addCleanup(shutil.rmtree, self.root, ignore_errors=True)
        self.log = self.root / "USAGE_LOG.jsonl"

    def test_a_brand_new_tmp_is_left_alone(self):
        fresh = _orphan(self.root, "USAGE_LOG.jsonl", 0.0)
        L._log_to_file(self.log, {"n": 1}, max_entries=10)
        self.assertTrue(fresh.exists(),
                        "a tmp younger than the TTL may be another process's "
                        "in-flight write")

    def test_a_tmp_just_under_the_ttl_is_left_alone(self):
        fresh = _orphan(self.root, "USAGE_LOG.jsonl",
                        max(1.0, L._TMP_ORPHAN_TTL_SECONDS - 30))
        L._log_to_file(self.log, {"n": 1}, max_entries=10)
        self.assertTrue(fresh.exists())

    def test_a_concurrent_writers_file_is_not_stolen(self):
        """Hold an open handle, as a mid-write process does, and make it look old.
        On Windows the unlink then fails -- the sweep must swallow that, not raise."""
        held = _orphan(self.root, "USAGE_LOG.jsonl",
                       L._TMP_ORPHAN_TTL_SECONDS + 60)
        fh = open(held, "ab")
        try:
            self.assertTrue(L._log_to_file(self.log, {"n": 1}, max_entries=10),
                            "an un-deletable orphan must not fail the write")
        finally:
            fh.close()


    def test_one_locked_orphan_does_not_abort_the_others(self):
        """Mutation-verify found this hole. Reverting `except OSError: continue`
        to `raise` left all 13 tests green, because the sweep's outer
        `except Exception` swallows the raise and the write still succeeds -- so
        the held-handle test above could not tell the two apart.

        The contract that actually differs is the LOOP: one un-deletable orphan
        (measured on Windows: PermissionError 13, another process holds the
        mkstemp fd) must not stop the remaining orphans from being reclaimed.
        Otherwise a single locked file re-opens the 132-file leak.

        Forcing the failure on the FIRST unlink makes this independent of the
        order `Path.glob` happens to return."""
        stale = [_orphan(self.root, "USAGE_LOG.jsonl",
                         L._TMP_ORPHAN_TTL_SECONDS + 60) for _ in range(3)]
        real_unlink = os.unlink
        calls = {"n": 0}

        def flaky_unlink(target):
            calls["n"] += 1
            if calls["n"] == 1:
                raise PermissionError(13, "simulated: another process holds it")
            return real_unlink(target)

        os.unlink = flaky_unlink
        try:
            removed = L._sweep_orphan_tmp(self.log)
        finally:
            os.unlink = real_unlink

        self.assertEqual(calls["n"], 3,
                         "the sweep stopped early: it tried %d of 3" % calls["n"])
        self.assertEqual(removed, 2)
        alive = [p.name for p in stale if p.exists()]
        self.assertEqual(len(alive), 1, alive)


class TheSweepIsScopedToThisLogTest(unittest.TestCase):
    """`_atomic_write_bytes` also writes user files. A sweep that matched by
    suffix alone would delete a caller's own `.something.xxxx.tmp`."""

    def setUp(self):
        self.root = Path(tempfile.mkdtemp(prefix="b030_scope_"))
        self.addCleanup(shutil.rmtree, self.root, ignore_errors=True)
        self.log = self.root / "USAGE_LOG.jsonl"

    def test_another_logs_orphan_is_not_touched(self):
        other = _orphan(self.root, "APP_SESSION_LOG.jsonl",
                        L._TMP_ORPHAN_TTL_SECONDS + 60)
        L._log_to_file(self.log, {"n": 1}, max_entries=10)
        self.assertTrue(other.exists(),
                        "only tmp files of the file being written may be swept")

    def test_an_unrelated_stale_file_is_not_touched(self):
        keep = self.root / "notes.txt"
        keep.write_text("mine\n", encoding="utf-8")
        old = time.time() - (L._TMP_ORPHAN_TTL_SECONDS + 600)
        os.utime(keep, (old, old))
        L._log_to_file(self.log, {"n": 1}, max_entries=10)
        self.assertTrue(keep.exists())

    def test_the_real_log_file_is_never_a_sweep_target(self):
        self.log.write_text('{"seed": 1}\n', encoding="utf-8")
        old = time.time() - (L._TMP_ORPHAN_TTL_SECONDS + 600)
        os.utime(self.log, (old, old))
        L._log_to_file(self.log, {"n": 1}, max_entries=10)
        self.assertTrue(self.log.exists())
        self.assertIn('"n": 1', self.log.read_text(encoding="utf-8"))


class TheKillPathIsWhatThisPinsTest(unittest.TestCase):
    """Reproduce the measured mechanism without killing anything: enter
    `_atomic_write_bytes`, create the tmp, and never reach `os.replace`."""

    def setUp(self):
        self.root = Path(tempfile.mkdtemp(prefix="b030_kill_"))
        self.addCleanup(shutil.rmtree, self.root, ignore_errors=True)
        self.log = self.root / "USAGE_LOG.jsonl"
        self.log.write_text('{"seed": 1}\n', encoding="utf-8")

    def test_a_tmp_abandoned_mid_write_is_reclaimed_later(self):
        real_replace = os.replace

        class Killed(BaseException):
            """BaseException: not caught by `except Exception`, like TerminateProcess."""

        def die(_src, _dst):
            raise Killed()

        os.replace = die
        try:
            with self.assertRaises(Killed):
                L._atomic_write_text(self.log, "x\n")
        finally:
            os.replace = real_replace

        orphans = list(self.root.glob(".USAGE_LOG.jsonl.*.tmp"))
        self.assertEqual(len(orphans), 1,
                         "the repro must actually leave an orphan: %r" % (orphans,))
        old = time.time() - (L._TMP_ORPHAN_TTL_SECONDS + 60)
        os.utime(orphans[0], (old, old))

        self.assertTrue(L._log_to_file(self.log, {"n": 2}, max_entries=10))
        self.assertFalse(orphans[0].exists(),
                         "the NEXT writer is the only code that can clean this up")


if __name__ == "__main__":
    unittest.main()
