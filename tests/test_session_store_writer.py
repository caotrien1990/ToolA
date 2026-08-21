from __future__ import annotations

import sqlite3
import threading
import time
from pathlib import Path

import pytest

from orchestrator.session import store


def _wait_until(predicate, timeout: float = 10.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(0.01)
    raise AssertionError("condition did not become true before timeout")


def _writer(tmp_path: Path, **kwargs) -> tuple[Path, store.SessionWriter]:
    db_path = tmp_path / "writer.db"
    conn = sqlite3.connect(db_path)
    try:
        conn.execute("CREATE TABLE records (id INTEGER PRIMARY KEY, value TEXT UNIQUE)")
        conn.commit()
    finally:
        conn.close()
    return db_path, store.SessionWriter(
        db_path,
        batch_window_ms=kwargs.pop("batch_window_ms", 25),
        busy_timeout_ms=kwargs.pop("busy_timeout_ms", 5000),
        checkpoint_every_batches=kwargs.pop("checkpoint_every_batches", 1000),
        **kwargs,
    )


def _concurrent_submit(writer: store.SessionWriter, jobs: dict[str, object]):
    barrier = threading.Barrier(len(jobs) + 1)
    results: dict[str, object] = {}
    errors: dict[str, BaseException] = {}

    def invoke(name: str, callback) -> None:
        try:
            barrier.wait(timeout=10)
            results[name] = writer.submit(
                name, callback, durable=True, timeout_ms=10000
            )
        except BaseException as exc:
            errors[name] = exc

    threads = [
        threading.Thread(target=invoke, args=(name, callback))
        for name, callback in jobs.items()
    ]
    for thread in threads:
        thread.start()
    barrier.wait(timeout=10)
    for thread in threads:
        thread.join(timeout=12)
    assert all(not thread.is_alive() for thread in threads)
    return results, errors


def test_group_commit_drains_many_items_in_one_transaction(tmp_path: Path) -> None:
    # WHY: the optimization only matters if an already-queued burst shares COMMIT.
    db_path, writer = _writer(tmp_path, batch_max=64, stall_seconds=10)
    entered = threading.Event()
    release = threading.Event()

    def hold_writer(_conn):
        entered.set()
        assert release.wait(timeout=3)

    try:
        writer.submit("group-prelude", hold_writer, durable=False)
        assert entered.wait(timeout=2)
        for value in range(16):
            writer.submit(
                f"group-{value}",
                lambda conn, value=value: conn.execute(
                    "INSERT INTO records(value) VALUES (?)", (str(value),)
                ).lastrowid,
                durable=False,
            )
        release.set()
        _wait_until(lambda: writer.committed_total == 17)
        with sqlite3.connect(db_path) as conn:
            assert conn.execute("SELECT COUNT(*) FROM records").fetchone() == (16,)
        assert writer._batch_count == 2
    finally:
        release.set()
        writer.close()


def test_per_item_savepoint_isolates_failure(tmp_path: Path) -> None:
    # WHY: one malformed audit item must not roll back healthy neighbors.
    db_path, writer = _writer(tmp_path, batch_window_ms=50)

    def bad(conn):
        conn.execute("INSERT INTO records(value) VALUES ('bad')")
        raise RuntimeError("forced item failure")

    def good(conn):
        return conn.execute(
            "INSERT INTO records(value) VALUES ('good')"
        ).lastrowid

    try:
        results, errors = _concurrent_submit(writer, {"bad": bad, "good": good})
        assert isinstance(errors["bad"], RuntimeError)
        assert results["good"] == 1
        with sqlite3.connect(db_path) as conn:
            assert conn.execute("SELECT value FROM records").fetchall() == [("good",)]
    finally:
        writer.close()


def test_fire_and_forget_returns_without_waiting_and_eventually_persists(
    tmp_path: Path,
) -> None:
    # WHY: request handlers must not wait for the session-event fsync path.
    db_path, writer = _writer(tmp_path, batch_window_ms=1)

    def slow_insert(conn):
        time.sleep(0.15)
        conn.execute("INSERT INTO records(value) VALUES ('async')")

    try:
        started = time.monotonic()
        assert writer.submit("async", slow_insert, durable=False) is None
        assert time.monotonic() - started < 0.05
        _wait_until(lambda: writer.committed_total == 1)
        with sqlite3.connect(db_path) as conn:
            assert conn.execute("SELECT value FROM records").fetchone() == ("async",)
    finally:
        writer.close()


def test_durable_submit_returns_value_and_raises_callback_error(tmp_path: Path) -> None:
    # WHY: the durable outbox can ACK only a real committed sink result.
    _db_path, writer = _writer(tmp_path, batch_window_ms=1)
    try:
        event_id = writer.submit(
            "durable-ok",
            lambda conn: conn.execute(
                "INSERT INTO records(value) VALUES ('durable')"
            ).lastrowid,
            durable=True,
        )
        assert event_id == 1
        with pytest.raises(ValueError, match="durable failure"):
            writer.submit(
                "durable-error",
                lambda _conn: (_ for _ in ()).throw(ValueError("durable failure")),
                durable=True,
            )
    finally:
        writer.close()


def test_checkpoint_epoch_is_monotonic_under_concurrency(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # WHY: grouped callbacks must observe earlier uncommitted items in batch order.
    db_path = tmp_path / "sessions.db"
    monkeypatch.setattr(store, "_SESSIONS_DB", db_path)
    store._init_session_db()
    assert store._task_checkpoint_save("epoch", {"epoch": 0})
    count = 24
    barrier = threading.Barrier(count + 1)
    epochs: list[int] = []
    errors: list[BaseException] = []

    def bump() -> None:
        try:
            barrier.wait(timeout=5)
            snapshot, _result, committed = store._task_checkpoint_update(
                "epoch",
                lambda previous: (
                    {"epoch": int(previous.get("epoch", 0)) + 1},
                    "updated",
                ),
            )
            assert committed
            epochs.append(snapshot["epoch"])
        except BaseException as exc:
            errors.append(exc)

    threads = [threading.Thread(target=bump) for _ in range(count)]
    for thread in threads:
        thread.start()
    barrier.wait(timeout=5)
    for thread in threads:
        thread.join(timeout=8)

    assert not errors
    assert all(not thread.is_alive() for thread in threads)
    assert sorted(epochs) == list(range(1, count + 1))
    assert store._task_checkpoint_load("epoch") == {"epoch": count}
    store._get_session_writer().close()


def test_queue_overflow_drops_only_fire_and_forget_and_fails_durable(
    tmp_path: Path,
) -> None:
    # WHY: overload must stay memory-bounded without silently losing durable work.
    _db_path, writer = _writer(
        tmp_path, capacity=1, batch_max=1, batch_window_ms=1, stall_seconds=10
    )
    entered = threading.Event()
    release = threading.Event()

    def blocker(_conn):
        entered.set()
        assert release.wait(timeout=3)

    try:
        writer.submit("blocker", blocker, durable=False)
        assert entered.wait(timeout=2)
        writer.submit("queued", lambda _conn: None, durable=False)
        writer.submit("dropped", lambda _conn: None, durable=False)
        with pytest.raises(store.SessionStoreContentionError, match="queue budget"):
            writer.submit(
                "durable-overflow",
                lambda _conn: True,
                durable=True,
                timeout_ms=30,
            )
        assert writer.dropped_count == 1
    finally:
        release.set()
        writer.close()


def test_watchdog_restarts_a_dead_writer(tmp_path: Path) -> None:
    # WHY: a dead daemon must not turn all future durable writes into silent loss.
    db_path = tmp_path / "restart.db"
    with sqlite3.connect(db_path) as conn:
        conn.execute("CREATE TABLE records (id INTEGER PRIMARY KEY, value TEXT)")

    class RestartOnceWriter(store.SessionWriter):
        def __init__(self, *args, **kwargs):
            self._exit_once = True
            super().__init__(*args, **kwargs)

        def _drain_loop(self):
            if self._exit_once:
                self._exit_once = False
                return
            return super()._drain_loop()

    writer = RestartOnceWriter(
        db_path, watchdog_interval=0.01, batch_window_ms=1, stall_seconds=5
    )
    try:
        _wait_until(lambda: writer.restart_count >= 1)
        assert writer.submit(
            "after-restart",
            lambda conn: conn.execute(
                "INSERT INTO records(value) VALUES ('alive')"
            ).lastrowid,
            durable=True,
        ) == 1
        assert writer.is_alive
    finally:
        writer.close()


def test_sqlite_io_error_is_per_item_and_writer_continues(tmp_path: Path) -> None:
    # WHY: disk/SQLite failure for one item must preserve the loop and healthy work.
    db_path, writer = _writer(tmp_path, batch_window_ms=50)

    def io_error(_conn):
        raise sqlite3.OperationalError("disk I/O error")

    def healthy(conn):
        return conn.execute(
            "INSERT INTO records(value) VALUES ('healthy')"
        ).lastrowid

    try:
        results, errors = _concurrent_submit(
            writer, {"io-error": io_error, "healthy": healthy}
        )
        assert isinstance(errors["io-error"], sqlite3.OperationalError)
        assert results["healthy"] == 1
        assert writer.is_alive
        writer.submit(
            "after-io-error",
            lambda conn: conn.execute(
                "INSERT INTO records(value) VALUES ('after')"
            ).lastrowid,
            durable=True,
        )
        with sqlite3.connect(db_path) as conn:
            assert conn.execute(
                "SELECT value FROM records ORDER BY id"
            ).fetchall() == [("healthy",), ("after",)]
    finally:
        writer.close()

def test_watchdog_stall_is_transient_and_writer_recovers(tmp_path: Path) -> None:
    # WHY: a slow SQLite callback is not a dead writer and must not poison all future writes.
    db_path, writer = _writer(
        tmp_path, batch_window_ms=1, watchdog_interval=0.01, stall_seconds=0.05
    )
    entered = threading.Event()
    release = threading.Event()

    def slow_insert(conn):
        entered.set()
        assert release.wait(timeout=2)
        return conn.execute(
            "INSERT INTO records(value) VALUES ('slow')"
        ).lastrowid

    first_result: list[int] = []
    first_error: list[BaseException] = []

    def submit_slow() -> None:
        try:
            first_result.append(
                writer.submit("slow", slow_insert, durable=True, timeout_ms=2000)
            )
        except BaseException as exc:
            first_error.append(exc)

    thread = threading.Thread(target=submit_slow)
    thread.start()
    try:
        assert entered.wait(timeout=1)
        _wait_until(lambda: writer.metrics()["stalled"], timeout=1)
        with pytest.raises(store.SessionStoreContentionError, match="temporarily stalled"):
            writer.submit("during-stall", lambda _conn: True, durable=True)
        release.set()
        thread.join(timeout=3)
        assert not thread.is_alive()
        assert not first_error
        assert first_result == [1]
        _wait_until(lambda: not writer.metrics()["stalled"], timeout=1)
        assert writer.submit(
            "after-stall",
            lambda conn: conn.execute(
                "INSERT INTO records(value) VALUES ('after')"
            ).lastrowid,
            durable=True,
        ) == 2
        assert not writer.failed
        with sqlite3.connect(db_path) as conn:
            assert conn.execute(
                "SELECT value FROM records ORDER BY id"
            ).fetchall() == [("slow",), ("after",)]
    finally:
        release.set()
        thread.join(timeout=3)
        writer.close()


def test_slow_but_progressing_writer_is_not_killed_by_a_short_budget(
    tmp_path: Path,
) -> None:
    # A33 core property: a writer that is merely SLOW (disk busy) but still making
    # progress must commit, even when the caller passed a small busy budget. The
    # old code turned timeout_ms into a wall-clock deadline on the caller's wait,
    # so a callback slower than the budget raised SessionStoreContentionError even
    # though the writer was healthy -- measured as p50 860ms commits under pure
    # disk-I/O saturation against a 93ms uncontended baseline, on a SEPARATE db
    # with no lock contention. This pins the fix without generating real disk load.
    _db_path, writer = _writer(tmp_path, batch_window_ms=1)
    budget_ms = 50
    slow_ms = 400
    try:
        actual = {}

        def slow_but_progressing(conn):
            # Far longer than the tiny busy budget below; the old deadline would
            # have fired at ~budget while this callback is still progressing.
            #
            # It times ITSELF rather than trusting the nominal sleep: on Windows
            # `time.sleep(0.4)` returns in as little as 0.391s (timer
            # granularity), so asserting the caller's wait against the NOMINAL
            # 0.4 made this test flake -- measured `0.391 >= 0.4` in a full run
            # that was otherwise green. The property being pinned is "the wait
            # was not cut off at timeout_ms", not "sleep is exact", and an
            # assertion against a number the OS never promised measures the
            # clock instead of the fix.
            t0 = time.monotonic()
            row = conn.execute(
                "INSERT INTO records(value) VALUES ('slow-progress')"
            ).lastrowid
            time.sleep(slow_ms / 1000.0)
            actual["callback_seconds"] = time.monotonic() - t0
            return row

        started = time.monotonic()
        event_id = writer.submit(
            "slow-progress", slow_but_progressing, durable=True,
            timeout_ms=budget_ms
        )
        elapsed = time.monotonic() - started
        # It waited for the real commit rather than aborting at the 50ms budget.
        assert event_id == 1
        assert "callback_seconds" in actual, "the callback never ran to completion"
        assert elapsed >= actual["callback_seconds"], (
            "the caller returned in %.3fs, before its own callback finished "
            "(%.3fs)" % (elapsed, actual["callback_seconds"])
        )
        # The real assertion: many times the budget, not merely past it. A
        # regression that restored the wall-clock deadline would land near
        # 0.05s, nowhere near this.
        assert elapsed >= (budget_ms * 4) / 1000.0, (
            "returned in %.3fs against a %dms budget -- that is the wall-clock "
            "deadline firing again" % (elapsed, budget_ms)
        )
        assert not writer.failed
    finally:
        writer.close()


def test_in_flight_durable_waiter_aborts_only_when_writer_truly_fails(
    tmp_path: Path,
) -> None:
    # A33: "slow" must not abort a waiter, but genuinely STUCK/dead must. An
    # in-flight durable waiter has no wall-clock deadline any more, so it aborts
    # only when the watchdog has declared the writer FAILED (it died and could not
    # be restarted) -- the one state in which its future will never be set.
    _db_path, writer = _writer(
        tmp_path, batch_window_ms=1, watchdog_interval=0.01, stall_seconds=10
    )
    entered = threading.Event()
    release = threading.Event()

    def wedge(_conn):
        entered.set()
        # Hold the drain loop so the submitted item's future is never resolved.
        assert release.wait(timeout=5)

    error: list[BaseException] = []
    result: list[object] = []

    def submit_wedged() -> None:
        try:
            result.append(writer.submit("wedged", wedge, durable=True))
        except BaseException as exc:  # noqa: BLE001 - capturing for assertion
            error.append(exc)

    thread = threading.Thread(target=submit_wedged)
    thread.start()
    try:
        assert entered.wait(timeout=2)
        # Simulate the terminal state the watchdog reaches for a dead,
        # non-restartable writer. The in-flight waiter must now give up.
        writer._mark_failed("simulated dead writer")
        thread.join(timeout=3)
        assert not thread.is_alive()
        assert not result
        assert len(error) == 1
        assert isinstance(error[0], store.SessionStoreContentionError)
        assert "SessionWriter failed" in str(error[0])
    finally:
        release.set()
        thread.join(timeout=3)
        writer.close()


def test_a_wedged_but_living_writer_still_releases_its_waiters(
    tmp_path: Path,
) -> None:
    """A33 boundary. Removing the caller-side deadline fixed "slow is not stuck"
    but left the opposite hole: a thread wedged INSIDE write_fn stays alive, so
    the watchdog only ever marked it STALLED -- and STALLED does not release an
    in-flight waiter. Measured before this guard: the waiter was still blocked
    after 20s with failed=False, i.e. forever.

    This pins the property, not the mechanism: a durable submit against a writer
    that makes no progress must TERMINATE. It deliberately does not assert how
    long that takes or which flag flips, only that the caller gets an answer.
    """
    _db_path, writer = _writer(
        tmp_path, watchdog_interval=0.02, stall_seconds=0.2, wedge_multiplier=2.0
    )
    entered = threading.Event()
    release = threading.Event()

    def wedge(_conn):
        entered.set()
        assert release.wait(timeout=30)

    outcome: list[object] = []

    def submit_wedged() -> None:
        try:
            outcome.append(("ok", writer.submit("wedged", wedge, durable=True)))
        except BaseException as exc:  # noqa: BLE001 - capturing for assertion
            outcome.append(("raised", exc))

    thread = threading.Thread(target=submit_wedged)
    thread.start()
    try:
        assert entered.wait(timeout=5)
        # No _mark_failed() call here -- unlike the test above, nothing simulates
        # the terminal state. The writer thread is genuinely alive and stuck, and
        # the system must reach a verdict on its own.
        thread.join(timeout=10)
        assert not thread.is_alive(), (
            "a durable waiter blocked forever on a wedged writer: this is the "
            "regression the caller-side deadline used to hide"
        )
        assert outcome and outcome[0][0] == "raised"
        assert isinstance(outcome[0][1], store.SessionStoreContentionError)
    finally:
        release.set()
        thread.join(timeout=5)
        writer.close()


def test_a_wedge_verdict_is_retracted_once_the_writer_commits_again(
    tmp_path: Path,
) -> None:
    """The cost of the guard above must not be a writer that dies permanently
    the first time the disk hiccups. A wedge verdict is an INFERENCE from
    silence; a completed commit is direct evidence it was wrong, so it has to be
    retractable -- while a real terminal failure stays terminal.
    """
    _db_path, writer = _writer(
        tmp_path, watchdog_interval=0.02, stall_seconds=0.2, wedge_multiplier=2.0
    )
    entered = threading.Event()
    release = threading.Event()

    def wedge(_conn):
        entered.set()
        assert release.wait(timeout=30)

    thread = threading.Thread(
        target=lambda: writer.submit("wedged", wedge, durable=False)
    )
    thread.start()
    try:
        assert entered.wait(timeout=5)
        deadline = time.monotonic() + 10
        while not writer.failed and time.monotonic() < deadline:
            time.sleep(0.02)
        assert writer.failed, "the watchdog never reached a wedge verdict"

        # While wedged, new durable work is refused as RETRYABLE contention --
        # not the terminal RuntimeError, which would tell callers to give up on a
        # writer that is about to come back.
        with pytest.raises(store.SessionStoreContentionError):
            writer.submit("rejected", lambda conn: None, durable=True)

        # The stuck batch finally drains: direct evidence the verdict was wrong.
        release.set()
        thread.join(timeout=5)
        deadline = time.monotonic() + 10
        while writer.failed and time.monotonic() < deadline:
            time.sleep(0.02)
        assert not writer.failed, "a commit must retract a wedge verdict"

        assert writer.submit(
            "recovered",
            lambda conn: conn.execute(
                "INSERT INTO records (value) VALUES ('after-wedge')"
            ),
            durable=True,
        ) is not None

        # A terminal failure is a different claim and survives further progress.
        writer._mark_failed("writer restart failed: simulated")
        writer._clear_stalled()
        assert writer.failed, "progress must not resurrect a terminal failure"
    finally:
        release.set()
        thread.join(timeout=5)
        writer.close()


def test_legacy_connection_is_read_only_and_cannot_block_writer(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # WHY: SW-01 requires every write lock to be owned by SessionWriter.
    db_path = tmp_path / "sessions.db"
    monkeypatch.setattr(store, "_SESSIONS_DB", db_path)
    store._init_session_db()
    try:
        with store._with_sessions_db() as legacy:
            assert legacy.execute("PRAGMA query_only").fetchone() == (1,)
            with pytest.raises(sqlite3.OperationalError, match="readonly"):
                legacy.execute(
                    "INSERT INTO task_checkpoints(session_key, payload, updated_at) "
                    "VALUES ('bypass', '{}', 'now')"
                )
            assert store._task_checkpoint_save("writer-owned", {"epoch": 1})
        assert store._task_checkpoint_load("writer-owned") == {"epoch": 1}
    finally:
        store._get_session_writer().close()
