"""D4: retention for the durable audit outbox.

Why this exists (measured 2026-09-22): `.orchestrator_cache/durable_audit.db` had
reached 38.4MB with ~47800 rows in state 'applied', and `audit_writer.py` contained no
DELETE, no prune and no VACUUM at all -- the outbox was append-only for its whole life.
An applied row has already been handed to the sink and mirrored into the session store,
so nothing reads it back; it is pure history. sessions.db got this treatment in A22.

These tests pin the three things that can go wrong with a retention delete, in the
order they would hurt:

  1. WHITELIST: only `state='applied'` rows may be reachable. A `pending` row is an
     audit that has NOT been applied yet; deleting one loses the event silently. The
     predicate is positive, never "everything except pending".
  2. The A22 REAL-vs-TEXT cutoff trap: `created_at` is REAL epoch seconds. SQLite
     orders storage classes REAL < TEXT, so comparing that column against an ISO
     *string* matches EVERY row -- a retention query written that way empties the
     table while reading as correct. A recent applied row must survive.
  3. The A22 "built but never called" trap: `prune_sessions_older_than` shipped with
     ZERO production callers and therefore pruned nothing for a month. One test here
     drives the writer end to end and asserts the prune ran because the writer STARTED,
     with nobody calling it by hand.

Every zero is positive-controlled: the same fixture is asserted to delete rows once the
window really covers them, so a green test can never be an empty table.
"""

from __future__ import annotations

import os
import sqlite3
import tempfile
import time
import unittest
from contextlib import closing
from pathlib import Path

from orchestrator.server import audit_writer as aw
from orchestrator.server.audit_writer import SESSION_EVENT_KIND, DurableAuditWriter

DAY = 86400.0


def _seed(db_path: Path, rows: list[tuple[str, str, float]]) -> None:
    """Insert (audit_id, state, created_at) rows straight into the outbox."""
    with closing(sqlite3.connect(db_path)) as conn:
        conn.executemany(
            "INSERT INTO durable_audit_outbox "
            "(audit_id, kind, payload_json, state, created_at, applied_at, "
            " attempt_count, next_attempt_at) "
            "VALUES (?, ?, '{}', ?, ?, NULL, 0, 0)",
            [(aid, SESSION_EVENT_KIND, state, created) for aid, state, created in rows],
        )
        conn.commit()


def _ids(db_path: Path) -> set[str]:
    with closing(sqlite3.connect(db_path)) as conn:
        return {r[0] for r in conn.execute("SELECT audit_id FROM durable_audit_outbox")}


class RetentionWindowTest(unittest.TestCase):
    """The live env read. Same contract as _session_retention_days (A22)."""

    def setUp(self) -> None:
        self._saved = os.environ.get(aw._DURABLE_AUDIT_RETENTION_DAYS_ENV)

    def tearDown(self) -> None:
        if self._saved is None:
            os.environ.pop(aw._DURABLE_AUDIT_RETENTION_DAYS_ENV, None)
        else:
            os.environ[aw._DURABLE_AUDIT_RETENTION_DAYS_ENV] = self._saved

    def _set(self, value: str | None) -> None:
        if value is None:
            os.environ.pop(aw._DURABLE_AUDIT_RETENTION_DAYS_ENV, None)
        else:
            os.environ[aw._DURABLE_AUDIT_RETENTION_DAYS_ENV] = value

    def test_unset_is_the_fourteen_day_default(self) -> None:
        self._set(None)
        self.assertEqual(aw._durable_audit_retention_days(), 14)
        self.assertEqual(aw._DURABLE_AUDIT_RETENTION_DAYS_DEFAULT, 14)

    def test_a_valid_value_is_read_LIVE_not_snapshotted_at_import(self) -> None:
        self._set("3")
        self.assertEqual(aw._durable_audit_retention_days(), 3)
        self._set("30")
        self.assertEqual(aw._durable_audit_retention_days(), 30)

    def test_a_misconfigured_value_never_widens_the_delete(self) -> None:
        """0 would mean 'prune everything up to now' -- a foot-gun, so it must not win."""
        for bad in ("", "   ", "abc", "0", "-5", "3.7"):
            with self.subTest(value=bad):
                self._set(bad)
                self.assertEqual(aw._durable_audit_retention_days(), 14)


class PruneBase(unittest.TestCase):
    def setUp(self) -> None:
        self._temp = tempfile.TemporaryDirectory()
        self.root = Path(self._temp.name)
        self.db = self.root / "retention.db"
        self.writer = DurableAuditWriter(self.db, lambda record: None, auto_start=False)
        self.addCleanup(self._temp.cleanup)

    def tearDown(self) -> None:
        try:
            self.writer.shutdown(timeout=2.0)
        except Exception:
            pass


class PruneWhitelistTest(PruneBase):
    def test_only_applied_rows_are_reachable(self) -> None:
        now = time.time()
        _seed(self.db, [
            ("old-applied-1", "applied", now - 40 * DAY),
            ("old-applied-2", "applied", now - 20 * DAY),
            ("old-PENDING", "pending", now - 40 * DAY),
            ("new-applied", "applied", now - 1 * DAY),
        ])
        out = self.writer.prune_applied_older_than(days=14)
        self.assertTrue(out["ok"])
        self.assertEqual(out["retention_days"], 14)
        self.assertEqual(out["eligible"], 2)
        self.assertEqual(out["deleted"], 2)
        self.assertEqual(_ids(self.db), {"old-PENDING", "new-applied"})

    def test_an_ancient_pending_row_survives_a_zero_length_window(self) -> None:
        """The strongest form of the whitelist: even days=0 cannot reach 'pending'."""
        now = time.time()
        _seed(self.db, [
            ("old-PENDING", "pending", now - 400 * DAY),
            ("applied-now", "applied", now - 1.0),
        ])
        out = self.writer.prune_applied_older_than(days=0)
        self.assertEqual(out["deleted"], 1)  # positive control: days=0 DOES delete
        self.assertEqual(_ids(self.db), {"old-PENDING"})

    def test_the_eligible_count_itself_is_state_scoped(self) -> None:
        """A blacklist ('everything not pending') would count these rows; a whitelist
        cannot.

        Measured where it CAN be measured: the CHECK constraint on the table admits only
        'pending' and 'applied', so a third state cannot be inserted today -- an attempt
        raises IntegrityError, which is why this is asserted through the eligible COUNT
        rather than by fabricating a 'quarantined' row. The count is produced by the same
        positive predicate the DELETE uses, so if retention ever grew to reach a state it
        was not taught about, `eligible` would move here first.
        """
        now = time.time()
        _seed(self.db, [
            ("old-applied", "applied", now - 90 * DAY),
            ("older-PENDING-1", "pending", now - 200 * DAY),
            ("older-PENDING-2", "pending", now - 400 * DAY),
        ])
        preview = self.writer.prune_applied_older_than(days=14, dry_run=True)
        self.assertEqual(preview["eligible"], 1, "the two pending rows are older and"
                                                 " must not be counted")
        with closing(sqlite3.connect(self.db)) as conn:
            with self.assertRaises(sqlite3.IntegrityError):
                conn.execute("UPDATE durable_audit_outbox SET state='quarantined'")
        out = self.writer.prune_applied_older_than(days=14)
        self.assertEqual(out["deleted"], 1)
        self.assertEqual(_ids(self.db), {"older-PENDING-1", "older-PENDING-2"})


class CutoffTypeTest(PruneBase):
    def test_a_recent_applied_row_survives(self) -> None:
        """The A22 REAL-vs-TEXT trap, stated as behaviour.

        If the cutoff were ever built as an ISO string, SQLite's class ordering
        (REAL < TEXT) would make every one of these rows match and the table would be
        emptied. The positive control is the second call: the same rows DO go once the
        window genuinely covers them, so this is not an inert query.
        """
        now = time.time()
        _seed(self.db, [
            ("a", "applied", now - 1.0),
            ("b", "applied", now - 60.0),
            ("c", "applied", now - 3 * DAY),
        ])
        kept = self.writer.prune_applied_older_than(days=14)
        self.assertEqual(kept["eligible"], 0)
        self.assertEqual(kept["deleted"], 0)
        self.assertEqual(len(_ids(self.db)), 3)

        gone = self.writer.prune_applied_older_than(days=0)
        self.assertEqual(gone["deleted"], 3)
        self.assertEqual(_ids(self.db), set())

    def test_the_cutoff_is_reported_as_an_epoch_number(self) -> None:
        out = self.writer.prune_applied_older_than(days=14, dry_run=True)
        self.assertIsInstance(out["cutoff_epoch"], float)
        self.assertLess(out["cutoff_epoch"], time.time())


class DryRunAndBatchTest(PruneBase):
    def test_dry_run_counts_and_deletes_nothing(self) -> None:
        now = time.time()
        _seed(self.db, [("old-%d" % i, "applied", now - 30 * DAY) for i in range(5)])
        preview = self.writer.prune_applied_older_than(days=14, dry_run=True)
        self.assertTrue(preview["dry_run"])
        self.assertEqual(preview["eligible"], 5)
        self.assertEqual(preview["deleted"], 0)
        self.assertEqual(preview["batches"], 0)
        self.assertEqual(len(_ids(self.db)), 5)
        # Positive control: the preview count is the number a real run removes.
        real = self.writer.prune_applied_older_than(days=14)
        self.assertEqual(real["deleted"], preview["eligible"])

    def test_the_delete_is_batched_so_no_single_long_lock(self) -> None:
        now = time.time()
        _seed(self.db, [("old-%d" % i, "applied", now - 30 * DAY) for i in range(5)])
        out = self.writer.prune_applied_older_than(days=14, limit=2)
        self.assertEqual(out["deleted"], 5)
        self.assertEqual(out["batches"], 3)  # 2 + 2 + 1
        self.assertEqual(_ids(self.db), set())

    def test_a_nonpositive_batch_limit_fails_loud(self) -> None:
        for bad in (0, -1):
            with self.subTest(limit=bad):
                with self.assertRaises(ValueError):
                    self.writer.prune_applied_older_than(days=14, limit=bad)

    def test_an_empty_table_is_a_quiet_no_op(self) -> None:
        out = self.writer.prune_applied_older_than(days=14)
        self.assertEqual((out["eligible"], out["deleted"], out["batches"]), (0, 0, 0))


class CallerWiringTest(unittest.TestCase):
    """The A22 'built but never called' trap: retention must fire on its own."""

    def setUp(self) -> None:
        self._temp = tempfile.TemporaryDirectory()
        self.addCleanup(self._temp.cleanup)
        self.root = Path(self._temp.name)
        self.db = self.root / "wired.db"
        self._saved = os.environ.get(aw._DURABLE_AUDIT_RETENTION_DAYS_ENV)

    def tearDown(self) -> None:
        if self._saved is None:
            os.environ.pop(aw._DURABLE_AUDIT_RETENTION_DAYS_ENV, None)
        else:
            os.environ[aw._DURABLE_AUDIT_RETENTION_DAYS_ENV] = self._saved

    def _wait_until(self, predicate, timeout: float = 5.0) -> bool:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if predicate():
                return True
            time.sleep(0.02)
        return False

    def test_starting_the_writer_prunes_old_applied_rows_with_nobody_calling_it(self):
        seeder = DurableAuditWriter(self.db, lambda record: None, auto_start=False)
        now = time.time()
        _seed(self.db, [
            ("stale-applied", "applied", now - 90 * DAY),
            ("fresh-applied", "applied", now - 1 * DAY),
            ("stale-PENDING", "pending", now - 90 * DAY),
        ])
        seeder.shutdown(timeout=2.0)

        os.environ[aw._DURABLE_AUDIT_RETENTION_DAYS_ENV] = "14"
        writer = DurableAuditWriter(self.db, lambda record: None)  # auto_start=True
        try:
            pruned = self._wait_until(lambda: "stale-applied" not in _ids(self.db))
            self.assertTrue(pruned, "start() never triggered retention -- 0 callers again")
            remaining = _ids(self.db)
            self.assertIn("fresh-applied", remaining)
            self.assertIn("stale-PENDING", remaining)
        finally:
            writer.shutdown(timeout=3.0)

    def test_a_failing_prune_never_stops_audits_from_being_applied(self) -> None:
        """Housekeeping is not allowed to take the worker down with it."""
        applied: list[str] = []
        original = DurableAuditWriter.prune_applied_older_than

        def boom(self, *a, **kw):
            raise RuntimeError("retention exploded")

        DurableAuditWriter.prune_applied_older_than = boom
        self.addCleanup(setattr, DurableAuditWriter,
                        "prune_applied_older_than", original)
        writer = DurableAuditWriter(self.db, lambda record: applied.append(record.audit_id))
        try:
            writer.submit_and_wait(
                audit_id="after-boom", kind=SESSION_EVENT_KIND,
                payload={"x": 1}, timeout=5.0,
            )
            self.assertEqual(applied, ["after-boom"])
            metrics = writer.metrics()
            self.assertEqual(metrics.get("last_error_kind"), "retention")
            self.assertIn("retention exploded", str(metrics.get("last_error") or ""))
        finally:
            writer.shutdown(timeout=3.0)


if __name__ == "__main__":
    unittest.main()
