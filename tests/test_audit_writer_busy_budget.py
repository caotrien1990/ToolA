"""Busy budget, metrics read order, and the audit spool (F1/F3/F17).

Three defects measured on 2026-09-27, all in the durable audit writer:

* F17 -- the SQLite busy budget was hardcoded at 200 ms x 4 attempts. The outbox
  lives on a spinning disk where a 2000-row retention DELETE holds the write lock
  longer than that, so a retention pass could make a concurrent stage() fail.
* F3  -- metrics() read _last_error AFTER a _retry_db call, and _retry_db clears a
  "storage" error on success. The one field that reports a dropped row was erased
  by the very read that came to fetch it.
* F1  -- stage() failing past the whole budget logged and returned, so the payload
  was gone. It now parks on a spool and replays at start.
"""

from __future__ import annotations

import os
import sqlite3
import tempfile
import threading
import unittest
from contextlib import closing
from pathlib import Path
from unittest import mock

from orchestrator.server.audit_writer import (
    SESSION_EVENT_KIND,
    AuditStorageBusy,
    DurableAuditWriter,
)
from orchestrator.storage.spool import read_spool, spool_depth

_ENV_KEYS = (
    "ORCHESTRATOR_DURABLE_AUDIT_BUSY_TIMEOUT_MS",
    "ORCHESTRATOR_DURABLE_AUDIT_RETRY_ATTEMPTS",
    "ORCHESTRATOR_DURABLE_AUDIT_RETRY_BACKOFF_S",
    "ORCHESTRATOR_DURABLE_AUDIT_CAPACITY_WAIT_S",
)


class _FakeDone:
    """Stands in for the heartbeat's threading.Event, recording every wait.

    The interval is the thing under test and it is an argument to wait(), so
    capturing the arguments measures the backoff directly -- no sleeping, and no
    wall-clock flake.
    """

    def __init__(self, stop_after: int) -> None:
        self.waits: list[float] = []
        self._stop_after = stop_after

    def wait(self, timeout: float | None = None) -> bool:
        self.waits.append(float(timeout if timeout is not None else 0.0))
        return len(self.waits) > self._stop_after


class _EnvIsolatedTestCase(unittest.TestCase):
    """Clear the four knobs so a developer's shell cannot change an assertion."""

    def setUp(self) -> None:
        self._temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self._temp_dir.name)
        self._env = mock.patch.dict(os.environ, {}, clear=False)
        self._env.start()
        for key in _ENV_KEYS:
            os.environ.pop(key, None)

    def tearDown(self) -> None:
        self._env.stop()
        self._temp_dir.cleanup()

    def _writer(self, name: str = "audit.db", **kwargs) -> DurableAuditWriter:
        writer = DurableAuditWriter(
            self.root / name,
            kwargs.pop("sink", lambda record: None),
            auto_start=False,
            **kwargs,
        )
        self.addCleanup(writer.shutdown, 1.0)
        return writer


class BusyBudgetDefaultsTests(_EnvIsolatedTestCase):
    def test_defaults_come_from_the_env_helpers_not_a_hardcoded_200ms(self) -> None:
        writer = self._writer()
        # These four numbers ARE the fix. 200/4/0.025/0.25 was the budget that
        # dropped rows; assert the new values so a silent revert fails here.
        self.assertEqual(writer.busy_timeout_ms, 1000)
        self.assertEqual(writer.db_retry_attempts, 4)
        self.assertAlmostEqual(writer.retry_backoff_s, 0.05)
        self.assertAlmostEqual(writer.capacity_wait_timeout_s, 1.0)

    def test_worst_case_stage_latency_stays_within_an_http_response(self) -> None:
        writer = self._writer()
        attempts = writer.db_retry_attempts
        worst = attempts * (writer.busy_timeout_ms / 1000.0)
        worst += writer.retry_backoff_s * (attempts * (attempts - 1) / 2)
        # stage() blocks a tool response for this long. Widening the timeout was
        # deliberate; widening it without bound would trade one defect for a hang.
        self.assertLessEqual(worst, 6.0)
        self.assertGreaterEqual(worst, 2.0)

    def test_env_overrides_are_read_live_not_snapshotted_at_import(self) -> None:
        # Same contract as _durable_audit_retention_days: an operator must be able
        # to widen the budget without editing code, and a module-level snapshot
        # would ignore them until restart.
        os.environ["ORCHESTRATOR_DURABLE_AUDIT_BUSY_TIMEOUT_MS"] = "2500"
        os.environ["ORCHESTRATOR_DURABLE_AUDIT_RETRY_ATTEMPTS"] = "7"
        os.environ["ORCHESTRATOR_DURABLE_AUDIT_RETRY_BACKOFF_S"] = "0.2"
        os.environ["ORCHESTRATOR_DURABLE_AUDIT_CAPACITY_WAIT_S"] = "3"

        writer = self._writer("env.db")

        self.assertEqual(writer.busy_timeout_ms, 2500)
        self.assertEqual(writer.db_retry_attempts, 7)
        self.assertAlmostEqual(writer.retry_backoff_s, 0.2)
        self.assertAlmostEqual(writer.capacity_wait_timeout_s, 3.0)

    def test_unparseable_env_keeps_the_default(self) -> None:
        os.environ["ORCHESTRATOR_DURABLE_AUDIT_BUSY_TIMEOUT_MS"] = "soon"
        self.assertEqual(self._writer("junk.db").busy_timeout_ms, 1000)

    def test_blank_env_keeps_the_default(self) -> None:
        os.environ["ORCHESTRATOR_DURABLE_AUDIT_BUSY_TIMEOUT_MS"] = "   "
        self.assertEqual(self._writer("blank.db").busy_timeout_ms, 1000)

    def test_zero_busy_timeout_is_clamped_not_honoured(self) -> None:
        # A 0 would restore the exact failure these knobs exist to stop: "wait for
        # the lock" becomes "drop the row". Clamped, so a typo cannot regress it.
        os.environ["ORCHESTRATOR_DURABLE_AUDIT_BUSY_TIMEOUT_MS"] = "0"
        self.assertGreaterEqual(self._writer("zero.db").busy_timeout_ms, 1)

    def test_explicit_kwargs_still_win_over_the_environment(self) -> None:
        os.environ["ORCHESTRATOR_DURABLE_AUDIT_BUSY_TIMEOUT_MS"] = "9000"
        writer = self._writer("explicit.db", busy_timeout_ms=150)
        # Existing tests pin tight timeouts on purpose; env defaults must not
        # override a caller that stated a number.
        self.assertEqual(writer.busy_timeout_ms, 150)

    def test_explicit_out_of_range_kwarg_is_still_validated(self) -> None:
        # The None-resolution was inserted ABOVE the validation block precisely so
        # an explicit bad value keeps hitting the same check.
        with self.assertRaises(ValueError):
            DurableAuditWriter(
                self.root / "bad.db",
                lambda record: None,
                busy_timeout_ms=0,
                auto_start=False,
            )

    def test_out_of_range_env_cannot_raise_out_of_the_constructor(self) -> None:
        # A misconfigured shell variable must not make the daemon unbootable:
        # clamping, not raising, is what keeps a typo from taking [A] down.
        os.environ["ORCHESTRATOR_DURABLE_AUDIT_RETRY_ATTEMPTS"] = "-5"
        self.assertGreaterEqual(self._writer("neg.db").db_retry_attempts, 1)

    def test_live_budget_is_published_in_metrics(self) -> None:
        metrics = self._writer("pub.db").metrics()
        # Without these, a reader of /health cannot tell which budget is in force,
        # so an env override is invisible at exactly the moment it matters.
        self.assertEqual(metrics["busy_timeout_ms"], 1000)
        self.assertEqual(metrics["db_retry_attempts"], 4)


class LeaseHeartbeatBackoffTests(_EnvIsolatedTestCase):
    def test_failing_renew_backs_off_instead_of_re_firing_at_the_same_rate(self) -> None:
        writer = self._writer("hb.db", lease_duration_s=1.0)
        done = _FakeDone(stop_after=4)
        with mock.patch.object(
            writer,
            "_renew_lease",
            side_effect=AuditStorageBusy("database is locked"),
        ):
            writer._lease_heartbeat("a1", done)

        base = 0.25
        # Each failed tick had already burned the busy budget against whoever holds
        # the write lock. Re-firing at 0.25 s added load to the contention it lost
        # to; doubling gives the other writer room to commit.
        self.assertAlmostEqual(done.waits[0], base)
        self.assertAlmostEqual(done.waits[1], base * 2)
        for earlier, later in zip(done.waits, done.waits[1:]):
            self.assertGreaterEqual(later, earlier)

    def test_backoff_ceiling_stays_below_the_lease_duration(self) -> None:
        # A backed-off tick that outlasts the lease would let it expire and hand
        # the row to another claimant -- a worse bug than the contention.
        writer = self._writer("hb2.db", lease_duration_s=1.0)
        done = _FakeDone(stop_after=12)
        with mock.patch.object(
            writer,
            "_renew_lease",
            side_effect=AuditStorageBusy("database is locked"),
        ):
            writer._lease_heartbeat("a1", done)

        self.assertLess(max(done.waits), writer.lease_duration_s)

    def test_success_resets_the_interval(self) -> None:
        # Two failures then successes: the interval must come back to base, or one
        # transient blip would permanently slow every later renewal.
        writer = self._writer("hb3.db", lease_duration_s=1.0)
        done = _FakeDone(stop_after=5)
        outcomes = [AuditStorageBusy("locked"), AuditStorageBusy("locked"), True, True, True]

        def renew(audit_id: str) -> bool:
            outcome = outcomes.pop(0)
            if isinstance(outcome, BaseException):
                raise outcome
            return outcome

        with mock.patch.object(writer, "_renew_lease", side_effect=renew):
            writer._lease_heartbeat("a1", done)

        self.assertAlmostEqual(done.waits[0], 0.25)
        self.assertGreater(done.waits[2], done.waits[0])
        self.assertAlmostEqual(done.waits[-1], 0.25)

    def test_lost_lease_still_stops_the_heartbeat(self) -> None:
        # Backoff must not change the exit path: a renew that returns False means
        # someone else owns the row now, and this thread has to stop immediately.
        writer = self._writer("hb4.db", lease_duration_s=1.0)
        done = _FakeDone(stop_after=10)
        with mock.patch.object(writer, "_renew_lease", return_value=False):
            writer._lease_heartbeat("a1", done)

        self.assertEqual(len(done.waits), 1)
        self.assertEqual(writer.metrics()["lease_lost_total"], 1)


class MetricsErrorReadOrderTests(_EnvIsolatedTestCase):
    def test_metrics_reports_the_storage_error_its_own_read_would_clear(self) -> None:
        writer = self._writer("m1.db")
        writer._set_last_error(AuditStorageBusy("database is locked"), kind="storage")

        metrics = writer.metrics()

        # THE defect: state_counts goes through _retry_db, _retry_db clears a
        # "storage" last_error on success, and the old code read the field below
        # that call -- so durable_audit_ok could never go false for a dropped row.
        self.assertEqual(metrics["last_error_kind"], "storage")
        self.assertIn("locked", metrics["last_error"])
        self.assertIsNotNone(metrics["last_error_at"])

    def test_the_recovery_clear_still_happens_after_the_snapshot(self) -> None:
        # The clearing itself was never wrong -- a successful read does prove the
        # storage recovered. Only the read order was. So the error surfaces exactly
        # once, then goes away.
        writer = self._writer("m2.db")
        writer._set_last_error(AuditStorageBusy("database is locked"), kind="storage")

        self.assertEqual(writer.metrics()["last_error_kind"], "storage")
        self.assertIsNone(writer.metrics()["last_error_kind"])

    def test_non_storage_error_is_not_cleared_by_a_metrics_read(self) -> None:
        writer = self._writer("m3.db")
        writer._set_last_error(RuntimeError("sink exploded"), kind="sink")

        self.assertEqual(writer.metrics()["last_error_kind"], "sink")
        self.assertEqual(writer.metrics()["last_error_kind"], "sink")


class AuditSpoolTests(_EnvIsolatedTestCase):
    @staticmethod
    def _states(db_path: Path) -> dict[str, str]:
        with closing(sqlite3.connect(db_path)) as conn:
            rows = conn.execute(
                "SELECT audit_id, state FROM durable_audit_outbox",
            ).fetchall()
        return {row[0]: row[1] for row in rows}

    def test_spool_path_sits_beside_the_outbox_database(self) -> None:
        writer = self._writer("audit.db")
        # Discoverability is the point: whoever found the database during an
        # incident finds the parked rows without being told where to look.
        self.assertEqual(writer.spool_path.parent, writer.db_path.parent)
        self.assertEqual(writer.spool_path.name, "audit_spool.jsonl")

    def test_spool_failed_row_parks_the_payload_and_counts_it(self) -> None:
        writer = self._writer("s1.db")

        self.assertTrue(
            writer.spool_failed_row("a1", SESSION_EVENT_KIND, {"k": "v"}, "busy"),
        )

        records, corrupt = read_spool(writer.spool_path)
        self.assertEqual(corrupt, 0)
        self.assertEqual(len(records), 1)
        self.assertEqual(records[0]["audit_id"], "a1")
        self.assertEqual(records[0]["payload"], {"k": "v"})
        self.assertIn("busy", records[0]["reason"])
        self.assertEqual(writer.metrics()["spool_parked_total"], 1)

    def test_spool_failed_row_never_raises(self) -> None:
        # Every caller is inside an except block handling a storage failure. A
        # raising spool would turn a recoverable drop into a failed request.
        writer = self._writer("s2.db")
        with mock.patch(
            "orchestrator.server.audit_writer.append_spool",
            side_effect=OSError("disk full"),
        ):
            with self.assertRaises(OSError):
                # Guard the guard: prove the patch really does raise, so the
                # False below is the method swallowing it, not a no-op patch.
                from orchestrator.server.audit_writer import append_spool as patched

                patched(writer.spool_path, {})
            self.assertFalse(
                writer.spool_failed_row("a1", SESSION_EVENT_KIND, {}, "busy"),
            )

    def test_park_failure_is_counted_separately_from_a_park(self) -> None:
        writer = self._writer("s3.db")
        with mock.patch(
            "orchestrator.server.audit_writer.append_spool",
            return_value=False,
        ):
            writer.spool_failed_row("a1", SESSION_EVENT_KIND, {}, "busy")

        metrics = writer.metrics()
        # Distinct counters: "parked" is recoverable, "park failed" is a real loss
        # and is the only number that should page anyone.
        self.assertEqual(metrics["spool_park_failures"], 1)
        self.assertEqual(metrics["spool_parked_total"], 0)

    def test_depth_is_published_in_metrics(self) -> None:
        writer = self._writer("s4.db")
        self.assertEqual(writer.metrics()["spool_depth"], 0)

        writer.spool_failed_row("a1", SESSION_EVENT_KIND, {}, "busy")
        writer.spool_failed_row("a2", SESSION_EVENT_KIND, {}, "busy")

        self.assertEqual(writer.spool_depth(), 2)
        self.assertEqual(writer.metrics()["spool_depth"], 2)

    def test_replay_restages_parked_rows_and_empties_the_spool(self) -> None:
        writer = self._writer("s5.db")
        writer.spool_failed_row("a1", SESSION_EVENT_KIND, {"n": 1}, "busy")
        writer.spool_failed_row("a2", SESSION_EVENT_KIND, {"n": 2}, "busy")

        result = writer.replay_spool()

        self.assertEqual(result["replayed"], 2)
        self.assertEqual(result["kept"], 0)
        self.assertEqual(set(self._states(writer.db_path)), {"a1", "a2"})
        self.assertEqual(writer.spool_depth(), 0)

    def test_replay_of_an_already_staged_row_is_a_duplicate_not_a_retry(self) -> None:
        # Delivery is at-least-once by design. The row reached the outbox before the
        # process died, so re-staging the identical payload returns duplicate=True
        # and the spool must empty -- keeping it would make the spool never drain.
        writer = self._writer("s6.db")
        writer.stage("a1", SESSION_EVENT_KIND, {"n": 1})
        writer.spool_failed_row("a1", SESSION_EVENT_KIND, {"n": 1}, "busy")

        result = writer.replay_spool()

        self.assertEqual(result["replayed"], 1)
        self.assertEqual(writer.spool_depth(), 0)
        self.assertGreaterEqual(writer.metrics()["duplicate_total"], 1)

    def test_replay_treats_an_id_conflict_as_delivered(self) -> None:
        # Different content under the same audit_id is the one case stage() raises
        # on. Retrying can never succeed -- the stored row will keep differing -- so
        # a kept record would wedge the spool forever. The stored row wins.
        writer = self._writer("s6b.db")
        writer.stage("a1", SESSION_EVENT_KIND, {"n": 1})
        writer.spool_failed_row("a1", SESSION_EVENT_KIND, {"n": 999}, "busy")

        result = writer.replay_spool()

        self.assertEqual(result["replayed"], 1)
        self.assertEqual(writer.spool_depth(), 0)
        self.assertEqual(writer.metrics()["spool_replay_conflicts"], 1)

    def test_replay_keeps_rows_when_staging_still_fails(self) -> None:
        writer = self._writer("s7.db")
        writer.spool_failed_row("a1", SESSION_EVENT_KIND, {}, "busy")

        with mock.patch.object(
            writer,
            "stage",
            side_effect=AuditStorageBusy("still locked"),
        ):
            result = writer.replay_spool()

        self.assertEqual(result, {"replayed": 0, "kept": 1, "corrupt": 0})
        self.assertEqual(writer.spool_depth(), 1)

    def test_replay_drops_an_unusable_record_instead_of_wedging(self) -> None:
        # A record with no audit_id can never be staged, so keeping it would block
        # the spool from ever draining.
        writer = self._writer("s8.db")
        from orchestrator.storage.spool import append_spool

        append_spool(writer.spool_path, {"payload": {"n": 1}})

        result = writer.replay_spool()

        self.assertEqual(result["replayed"], 1)
        self.assertEqual(spool_depth(writer.spool_path), 0)
        self.assertEqual(writer.metrics()["spool_replay_unusable"], 1)

    def test_start_drains_the_spool_left_by_a_previous_process(self) -> None:
        # Rows parked before a crash are recoverable only here. A writer that
        # started without draining would leave them on disk forever.
        db_path = self.root / "s9.db"
        crashed = DurableAuditWriter(db_path, lambda record: None, auto_start=False)
        crashed.spool_failed_row("a1", SESSION_EVENT_KIND, {"n": 1}, "busy")
        crashed.shutdown(1.0)

        applied = threading.Event()
        restarted = DurableAuditWriter(
            db_path,
            lambda record: applied.set(),
            auto_start=False,
        )
        self.addCleanup(restarted.shutdown, 2.0)
        restarted.start()

        self.assertTrue(applied.wait(10.0))
        self.assertEqual(restarted.spool_depth(), 0)
        self.assertIn("a1", self._states(db_path))
        # Shutdown here, not only in addCleanup: the writer's worker thread now
        # holds one cached SQLite connection (P16), and addCleanup runs AFTER
        # tearDown removes the temp directory -- a still-running worker would
        # keep s9.db open and fail the teardown delete on Windows.
        restarted.shutdown(2.0)


if __name__ == "__main__":
    unittest.main()
