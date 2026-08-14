"""Regression tests for F821-1: ``_backup_lock_for`` was referenced by
``backup_project()`` and by concurrent-backup tests but was defined nowhere
in ``code_tools.py``, so every backup attempt raised ``NameError`` before
the per-project lock was acquired.

The invariant these tests protect: ``code_tools._backup_lock_for(key)``
returns a stable ``asyncio.Lock`` per canonical project key, and a real
(no-mocks) ``backup_project(..., force=True)`` call reaches the backup
pipeline (structured success/timeout/etc.) rather than crashing with
``NameError``.
"""

from __future__ import annotations

import asyncio
import json
import tempfile
import unittest
from pathlib import Path

import code_tools


class TestBackupLockRegistry(unittest.TestCase):
    """Direct unit coverage of the lock registry helper."""

    def test_backup_lock_for_returns_stable_asyncio_lock_per_key(self):
        # WHY: the whole point of the helper is that concurrent callers for
        # the SAME canonical project key contend on ONE lock. If a fresh
        # lock were returned each call, the BACKUP_IN_PROGRESS fast-reject
        # in backup_project() would never trigger and two full os.walk
        # backups would serialize on the global lane instead.
        key = "test_f821_stable_lock_key"
        try:
            lock_a = code_tools._backup_lock_for(key)
            lock_b = code_tools._backup_lock_for(key)
            self.assertIs(lock_a, lock_b)
            self.assertIsInstance(lock_a, asyncio.Lock)
        finally:
            code_tools._BACKUP_PROJECT_LOCKS.pop(key, None)

    def test_backup_lock_for_distinct_keys_get_distinct_locks(self):
        # WHY: serialization is scoped per project. Two different projects
        # must not share a lock, or an unrelated project's backup would
        # stall behind another's full walk.
        key_a = "test_f821_distinct_a"
        key_b = "test_f821_distinct_b"
        try:
            lock_a = code_tools._backup_lock_for(key_a)
            lock_b = code_tools._backup_lock_for(key_b)
            self.assertIsNot(lock_a, lock_b)
        finally:
            code_tools._BACKUP_PROJECT_LOCKS.pop(key_a, None)
            code_tools._BACKUP_PROJECT_LOCKS.pop(key_b, None)


class TestBackupProjectNoNameError(unittest.TestCase):
    """End-to-end (no mocks) proof that the F821-1 NameError is gone."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.project = Path(self._tmp.name) / "proj"
        self.project.mkdir(parents=True)
        (self.project / "hello.py").write_text("x = 1\n", encoding="utf-8")
        self.backup_dir = Path(self._tmp.name) / "backups"

    def tearDown(self):
        # Clean any lock entries we may have created so other tests see a
        # fresh registry.
        code_tools._BACKUP_PROJECT_LOCKS.pop(str(self.project.resolve()), None)

    def test_backup_project_force_true_does_not_raise_nameerror(self):
        # WHY: before the fix, this call path raised
        # ``NameError: name '_backup_lock_for' is not defined`` at
        # code_tools.py:2816 as soon as the per-project lock lookup ran.
        # The regression test pins the contract: a real call must return a
        # structured JSON result (ok / skipped / BACKUP_TIMEOUT / etc.) —
        # anything except an exception.
        raw = asyncio.run(
            code_tools.backup_project(
                str(self.project),
                backup_dir=str(self.backup_dir),
                force=True,
                timeout=120,
            )
        )
        result = json.loads(raw)
        self.assertIsInstance(result, dict)
        # Structured outcomes we accept: success, skip (already-backed-up),
        # or a domain error from deep in the pipeline. What we must NOT see
        # is the F821 NameError surfacing as an unhandled exception.
        self.assertIn("ok", result)
        if not result.get("ok"):
            self.assertNotIn("_backup_lock_for", json.dumps(result))
            self.assertNotIn("NameError", json.dumps(result))

    def test_concurrent_same_project_second_call_returns_backup_in_progress(self):
        # WHY: the per-project lock exists so a second same-project call
        # while one is running gets a structured BACKUP_IN_PROGRESS instead
        # of queueing another full walk. With _backup_lock_for undefined,
        # BOTH calls would NameError; with it defined, the second fast-
        # rejects. We drive two real backups on a project big enough that
        # the first is still inside its walk when the second arrives.
        big = self.project / "big.bin"
        big.write_bytes(b"z" * (8 * 1024 * 1024))

        async def scenario():
            first = asyncio.create_task(
                code_tools.backup_project(
                    str(self.project),
                    backup_dir=str(self.backup_dir),
                    force=True,
                    timeout=120,
                )
            )
            # Give the first call a scheduler chance to acquire the lock.
            await asyncio.sleep(0.05)
            second_raw = await code_tools.backup_project(
                str(self.project),
                backup_dir=str(self.backup_dir),
                force=True,
                timeout=120,
            )
            first_raw = await first
            return first_raw, second_raw

        first_raw, second_raw = asyncio.run(scenario())
        first = json.loads(first_raw)
        second = json.loads(second_raw)
        # Exactly one of these shapes is acceptable:
        #  * first acquired the lock and second fast-rejected, OR
        #  * first finished before second looked (small project, fast disk).
        # The hard requirement is: NEITHER call may raise NameError, and
        # if both reached the lock, exactly one wins.
        for res in (first, second):
            self.assertIsInstance(res, dict)
            self.assertIn("ok", res)
            self.assertNotIn("NameError", json.dumps(res))
        if not second.get("ok"):
            self.assertEqual(
                second.get("error_name"), "BACKUP_IN_PROGRESS",
                f"expected fast-reject, got {second}",
            )


if __name__ == "__main__":
    unittest.main()
