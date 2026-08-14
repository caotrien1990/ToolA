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
