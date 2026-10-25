# -*- coding: utf-8 -*-
"""CF-09 invariant-guard tests: append-only log write path (Phase 1, Sprint 1).

Placement note (recorded, NOT pre-decided): these tests live in this new
dedicated file. The existing suite files covering logs.py
(tests/test_usage_log_tmp_orphan_b030.py, tests/test_post_call_accounting.py)
were found via search_code callers, but they pin B030 orphan behaviour and
post-call accounting respectively; the CF-09 guards are a distinct suite.
CF-09 Gate 1 names/judges the final placement (mini-CF-09 §12).

All tests use temp directories only, never the live USAGE_LOG. The flag
is monkeypatched per test and restored afterwards; the module default
(PHASE1_CF9_FLAG = False) is itself asserted.
"""

from __future__ import annotations

import json
import os
import shutil
import sys
import tempfile
import threading
import unittest
from datetime import datetime
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from orchestrator.session import logs as L  # noqa: E402


def _records(path: Path):
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()
            if line.strip()]


def _archives(log_path: Path):
    archive_dir = L._cf09_archive_dir(log_path)
    if not archive_dir.is_dir():
        return []
    return sorted(p for p in archive_dir.iterdir()
                  if p.is_file() and p.suffix == ".jsonl")


class _Cf09Base(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp(prefix="cf09_"))
        self.addCleanup(shutil.rmtree, self.root, ignore_errors=True)
        self.log = self.root / "USAGE_LOG.jsonl"
        self._saved_flag = L.PHASE1_CF9_FLAG
        self._saved_threshold = L.CF09_ROTATION_MAX_BYTES
        self._saved_budget = L.CF09_COMBINED_DISK_BUDGET_BYTES
        self._saved_now = L._cf09_now
        self._saved_external = os.environ.pop(L.CF09_EXTERNAL_ARCHIVE_ENV, None)
        self._saved_usage_file = os.environ.pop("ORCHESTRATOR_USAGE_LOG_FILE", None)
        self.addCleanup(self._restore)

    def _restore(self):
        L.PHASE1_CF9_FLAG = self._saved_flag
        L.CF09_ROTATION_MAX_BYTES = self._saved_threshold
        L.CF09_COMBINED_DISK_BUDGET_BYTES = self._saved_budget
        L._cf09_now = self._saved_now
        L._CF09_ROTATION_PAUSED.clear()
        L._CF09_DELETE_ATTEMPTS.clear()
        if self._saved_external is None:
            os.environ.pop(L.CF09_EXTERNAL_ARCHIVE_ENV, None)
        else:
            os.environ[L.CF09_EXTERNAL_ARCHIVE_ENV] = self._saved_external
        if self._saved_usage_file is None:
            os.environ.pop("ORCHESTRATOR_USAGE_LOG_FILE", None)
        else:
            os.environ["ORCHESTRATOR_USAGE_LOG_FILE"] = self._saved_usage_file

    def _enable(self):
        L.PHASE1_CF9_FLAG = True

    def _write(self, n, pad=0):
        entry = {"n": n}
        if pad:
            entry["pad"] = "x" * pad
        self.assertTrue(L._log_to_file(self.log, entry, max_entries=1000))

    def _write_until_archive(self, max_writes=500):
        """Write numbered records until a rotation archives one file."""
        for i in range(max_writes):
            self._write(i)
            if _archives(self.log):
                return i + 1
        self.fail("no rotation archive appeared within %d writes" % max_writes)


class TestCf09FlagDefaultOff(_Cf09Base):
    def test_cf09_flag_default_off(self):
        # Restored by every other test's tearDown; the module default is OFF.
        self.assertIs(L.PHASE1_CF9_FLAG, False)


class TestCf09AppendOnlyNoRewriteLoss(_Cf09Base):
    def test_cf09_append_only_no_rewrite_loss(self):
        self._enable()
        for i in range(5):
            self._write(i)
        prefix = self.log.read_bytes()
        for i in range(5, 1505):
            self._write(i)
        rows = _records(self.log)
        self.assertEqual(len(rows), 1505)
        self.assertEqual([r["n"] for r in rows], list(range(1505)))
        # Append-only observable property: pre-existing bytes are a prefix,
        # never rewritten away.
        self.assertTrue(self.log.read_bytes().startswith(prefix))


class TestCf09RetentionExceedsCampaignWindow(_Cf09Base):
    def test_cf09_retention_exceeds_campaign_window(self):
        # Campaign window is UNDEFINED at fix time (Phase 0 §11 fill-ins
        # open), so the append-only branch asserts the property that
        # holds without a window: retention unbounded within the disk
        # bound. A burst far past the old 1,000-line cap loses nothing.
        self._enable()
        for i in range(1500):
            self._write(i)
        rows = _records(self.log)
        self.assertEqual(len(rows), 1500)
        self.assertEqual([r["n"] for r in rows], list(range(1500)))


class TestCf09RotationArchivesBeforeDelete(_Cf09Base):
    def test_cf09_rotation_archives_before_delete(self):
        self._enable()
        L.CF09_ROTATION_MAX_BYTES = 400
        L._cf09_now = lambda: datetime(2026, 10, 25, 8, 30, 0)
        written = self._write_until_archive()
        archives = _archives(self.log)
        self.assertEqual(len(archives), 1)
        # Windows-safe dated name: no ':' or '/'.
        self.assertEqual(archives[0].name, "USAGE_LOG-20261025-083000.jsonl")
        archived = _records(archives[0])
        live = _records(self.log)
        # Archive is a strict prefix, live log the suffix, no duplication.
        self.assertEqual([r["n"] for r in archived] + [r["n"] for r in live],
                         list(range(written)))
        self.assertTrue(len(archived) > 0)
        self.assertEqual(len(set(r["n"] for r in archived)) , len(archived))


class TestCf09CombinedDiskBound(_Cf09Base):
    def test_cf09_combined_disk_bound_external_archival(self):
        self._enable()
        L.CF09_ROTATION_MAX_BYTES = 300
        L.CF09_COMBINED_DISK_BUDGET_BYTES = 550
        external = self.root / "external"
        os.environ[L.CF09_EXTERNAL_ARCHIVE_ENV] = str(external)
        written = 0
        for i in range(300):
            self._write(i)
            written += 1
            if len(_archives(self.log)) >= 2:
                break
        self.assertGreaterEqual(len(_archives(self.log)), 1)
        # After enforcement, local log + archives are within budget...
        self.assertLessEqual(L._cf09_combined_bytes(self.log), 550)
        # ...and the oldest archive really did move to the external location.
        self.assertTrue(external.is_dir())
        self.assertTrue(any(p.is_file() and p.suffix == ".jsonl"
                            for p in external.iterdir()))
        # ...and nothing was silently deleted: live + local + external
        # records together are exactly the written sequence.
        seen = [r["n"] for r in _records(self.log)]
        for archive in _archives(self.log):
            seen += [r["n"] for r in _records(archive)]
        if external.is_dir():
            for archive in sorted(p for p in external.iterdir()
                                  if p.is_file() and p.suffix == ".jsonl"):
                seen += [r["n"] for r in _records(archive)]
        self.assertEqual(sorted(seen), list(range(written)))
        self.assertEqual(len(seen), len(set(seen)))

    def test_cf09_combined_disk_bound_escalates_without_external(self):
        self._enable()
        L.CF09_ROTATION_MAX_BYTES = 300
        L.CF09_COMBINED_DISK_BUDGET_BYTES = 550
        written = 0
        for i in range(300):
            self._write(i)
            written += 1
            if len(_archives(self.log)) >= 2:
                break
        archive_dir = L._cf09_archive_dir(self.log)
        markers = sorted(archive_dir.glob("CF09_ESCALATION-bound-exceeded-*.json"))
        self.assertTrue(markers, "bound breach must file an escalation marker")
        # Escalation, never silent deletion: all records still present.
        seen = [r["n"] for r in _records(self.log)]
        for archive in _archives(self.log):
            seen += [r["n"] for r in _records(archive)]
        self.assertEqual(sorted(seen), list(range(written)))

    def test_cf09_combined_bound_counts_stranded_archives(self):
        self._enable()
        L.CF09_COMBINED_DISK_BUDGET_BYTES = 100
        archive_dir = L._cf09_archive_dir(self.log)
        archive_dir.mkdir(parents=True, exist_ok=True)
        stranded = archive_dir / "OLD-ARCHIVE.jsonl"
        stranded.write_bytes(b'{"n": 0}\n' * 50)
        # The non-standard-named archive counts toward the bound...
        self.assertGreater(L._cf09_combined_bytes(self.log), 100)
        status = L._cf09_enforce_combined_bound(self.log)
        self.assertEqual(status, "paused-bound")
        # ...and enforcement escalates instead of deleting it.
        self.assertTrue(stranded.exists())
        self.assertTrue(list(archive_dir.glob("CF09_ESCALATION-bound-exceeded-*.json")))


class TestCf09RotationRecoveryR2S09(_Cf09Base):
    def _seed_log(self, count=10):
        self._enable()
        L.CF09_ROTATION_MAX_BYTES = 10 ** 9
        for i in range(count):
            self._write(i, pad=40)
        L.CF09_ROTATION_MAX_BYTES = 100

    def test_cf09_rotation_delete_fail_keeps_both_and_retries(self):
        self._seed_log()
        L._cf09_now = lambda: datetime(2026, 10, 25, 8, 30, 0)
        real_unlink = os.unlink
        calls = [0]

        def flaky_unlink(path, *args, **kwargs):
            calls[0] += 1
            if calls[0] <= 2:
                raise OSError("injected delete failure")
            return real_unlink(path, *args, **kwargs)

        with mock.patch("os.unlink", side_effect=flaky_unlink):
            status1 = L._cf09_rotate(self.log)
            self.assertEqual(status1, "delete-failed-retry-pending")
            # First failure: BOTH copies kept, no escalation yet.
            self.assertTrue(self.log.exists())
            archives = _archives(self.log)
            self.assertEqual(len(archives), 1)
            self.assertEqual(L._cf09_sha256_file(archives[0]),
                             L._cf09_sha256_file(self.log))
            status2 = L._cf09_rotate(self.log)
            self.assertEqual(status2, "paused-delete-failed")
            # Second failure: escalated, automatic rotation paused.
            archive_dir = L._cf09_archive_dir(self.log)
            self.assertTrue(list(archive_dir.glob("CF09_ESCALATION-delete-failed-*.json")))
            self.assertEqual(L._cf09_maybe_rotate(self.log), "paused")
            self.assertTrue(self.log.exists())
            self.assertEqual(len(_archives(self.log)), 1)
        # Explicit retry after the failure clears: delete verified, no duplicate.
        status3 = L._cf09_rotate(self.log)
        self.assertEqual(status3, "rotated")
        self.assertFalse(self.log.exists())
        self.assertEqual(len(_archives(self.log)), 1)

    def test_cf09_rotation_partial_archive_discarded_and_retried(self):
        self._seed_log()
        source = self.log.read_bytes()
        real_hash = L._cf09_sha256_file
        calls = [0]

        def flaky_hash(path):
            calls[0] += 1
            if calls[0] == 1:
                return "0" * 64
            return real_hash(path)

        with mock.patch.object(L, "_cf09_sha256_file", side_effect=flaky_hash):
            status = L._cf09_rotate(self.log)
        self.assertEqual(status, "partial-discarded")
        # Partial archive discarded; live log untouched for retry.
        self.assertEqual(_archives(self.log), [])
        self.assertEqual(self.log.read_bytes(), source)
        self.assertEqual(L._cf09_rotate(self.log), "rotated")
        archives = _archives(self.log)
        self.assertEqual(len(archives), 1)
        self.assertEqual(archives[0].read_bytes(), source)

    def test_cf09_rotation_name_collision_suffixes_not_overwrites(self):
        self._seed_log()
        L._cf09_now = lambda: datetime(2026, 10, 25, 8, 30, 0)
        archive_dir = L._cf09_archive_dir(self.log)
        archive_dir.mkdir(parents=True, exist_ok=True)
        occupant = archive_dir / L._cf09_dated_archive_name(self.log)
        occupant.write_bytes(b'{"other": true}\n')
        self.assertEqual(L._cf09_rotate(self.log), "rotated")
        # The different-content archive under the same name is untouched;
        # the rotation landed under a suffixed name instead.
        self.assertEqual(occupant.read_bytes(), b'{"other": true}\n')
        self.assertEqual(len(_archives(self.log)), 2)


class TestCf09RotationTriggerAndConcurrency(_Cf09Base):
    def test_cf09_rotation_trigger_on_size_not_line_count(self):
        self._enable()
        L.CF09_ROTATION_MAX_BYTES = 300
        for i in range(3):
            self._write(i, pad=200)
        # 3 lines only — far below any line cap — yet size rotation fired.
        self.assertTrue(_archives(self.log))

    def test_cf09_rotation_under_concurrent_writes(self):
        self._enable()
        L.CF09_ROTATION_MAX_BYTES = 2000
        errors = []

        def worker(base):
            try:
                for i in range(50):
                    self._write(base + i)
            except Exception as exc:  # noqa: BLE001 - collected, asserted below
                errors.append(exc)

        threads = [threading.Thread(target=worker, args=(t * 50,))
                   for t in range(4)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        self.assertEqual(errors, [])
        seen = [r["n"] for r in _records(self.log)]
        for archive in _archives(self.log):
            seen += [r["n"] for r in _records(archive)]
        self.assertEqual(sorted(seen), list(range(200)))
        self.assertEqual(len(seen), len(set(seen)))


class TestCf09OffStateAndTrimGate(_Cf09Base):
    def test_cf09_off_state_reproduces_old_capped_behaviour(self):
        self.assertIs(L.PHASE1_CF9_FLAG, False)
        for i in range(1500):
            self._write(i)
        rows = _records(self.log)
        self.assertEqual(len(rows), 1000)
        self.assertEqual([r["n"] for r in rows], list(range(500, 1500)))

    def test_cf09_auto_trim_suspended_when_on(self):
        entries = []
        for session in range(3):
            entries.append({"tool": "t", "n": session})
            entries.append({"type": "session_end", "n": session})
        for entry in entries:
            self.assertTrue(L._log_to_file(self.log, entry, max_entries=1000))
        before = self.log.read_bytes()
        self._enable()
        L._auto_trim_log(self.log, keep_sessions=1)
        self.assertEqual(self.log.read_bytes(), before)
        # OFF-state trim still works (sanity: the gate, not the trim, changed).
        L.PHASE1_CF9_FLAG = False
        L._auto_trim_log(self.log, keep_sessions=1)
        after = _records(self.log)
        self.assertTrue(all(r.get("n") == 2 for r in after))
        self.assertLess(len(after), len(entries))

    def test_cf09_log_usage_uses_append_path_when_on(self):
        self._enable()
        usage_file = self.root / "USAGE_LOG.jsonl"
        os.environ["ORCHESTRATOR_USAGE_LOG_FILE"] = str(usage_file)
        for i in range(3):
            self.assertTrue(L._log_usage("probe_tool", {"project_path": "x"},
                                         True, 5))
        rows = _records(usage_file)
        self.assertEqual(len(rows), 3)
        self.assertTrue(all(r["tool"] == "probe_tool" for r in rows))


if __name__ == "__main__":
    unittest.main()
