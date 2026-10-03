"""Durable, synchronous SQLite outbox for audit events.

The writer deliberately has no asyncio dependency. Producers synchronously stage a
row and receive a durable acknowledgement only after SQLite COMMIT. A dedicated
batch thread invokes an application sink and marks each row applied in a second
COMMIT. The sink receives audit_id and must use it as its idempotency key:
a process crash after the sink returns but before the applied-state COMMIT can
legitimately replay the callback (at-least-once delivery, exactly one outbox row).

Typical integration routes record.kind == "session_event" to the session event
store and record.kind == "write_ledger" to the write ledger.
"""

from __future__ import annotations

import json
import os
import sqlite3
import threading
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Protocol

from orchestrator.storage.spool import (
    append_spool,
    drain_spool,
    spool_depth as _spool_depth,
)


SESSION_EVENT_KIND = "session_event"
WRITE_LEDGER_KIND = "write_ledger"


def _durable_audit_synchronous() -> str:
    """SQLite synchronous mode for the durable outbox (Anh 2026-08-10).

    Default NORMAL: in WAL this is crash-safe for the database and survives an [A]
    application restart; only an OS/power crash can drop the last uncheckpointed
    commits. Because durable audit is at-least-once (replayed on restart) AND
    mirrored to the fast session-store, NORMAL removes the per-commit fsync that
    serialized concurrent staging (every tool response awaits one durable COMMIT)
    without a meaningful durability loss. Set ORCH_DURABLE_AUDIT_SYNCHRONOUS=FULL
    to restore per-commit fsync.
    """
    mode = str(os.environ.get("ORCH_DURABLE_AUDIT_SYNCHRONOUS", "NORMAL")).strip().upper()
    return mode if mode in {"OFF", "NORMAL", "FULL", "EXTRA"} else "NORMAL"


# Busy budget. Hardcoded in the constructor until 2026-09-27, when storage_busy_failures
# showed the outbox dropping rows: 200 ms x 4 attempts gave a ~0.95 s worst case, and
# this database lives on a spinning disk where _claim_pending_batch (BEGIN IMMEDIATE plus
# up to batch_size UPDATEs) and prune_applied_older_than (2000-row DELETE batches) both
# hold the write lock longer than that. Read live from the environment, never snapshotted
# at import -- same contract as _durable_audit_retention_days below.
_DURABLE_AUDIT_BUSY_TIMEOUT_MS_ENV = "ORCHESTRATOR_DURABLE_AUDIT_BUSY_TIMEOUT_MS"
_DURABLE_AUDIT_RETRY_ATTEMPTS_ENV = "ORCHESTRATOR_DURABLE_AUDIT_RETRY_ATTEMPTS"
_DURABLE_AUDIT_RETRY_BACKOFF_S_ENV = "ORCHESTRATOR_DURABLE_AUDIT_RETRY_BACKOFF_S"
_DURABLE_AUDIT_CAPACITY_WAIT_S_ENV = "ORCHESTRATOR_DURABLE_AUDIT_CAPACITY_WAIT_S"


def _env_number(name: str, default: float, minimum: float) -> float:
    """Read one live numeric knob. Blank or unparseable keeps the default.

    Clamped at minimum rather than rejected, because these are all timeouts: a
    misconfigured 0 would turn "wait for the lock" back into "drop the row", which
    is the exact failure these knobs exist to stop.
    """
    raw = str(os.environ.get(name, "")).strip()
    if not raw:
        return default
    try:
        value = float(raw)
    except (TypeError, ValueError):
        return default
    return value if value >= minimum else minimum


def _durable_audit_busy_timeout_ms() -> int:
    """SQLite busy_timeout per connection: 1000 ms, up from the original 200 ms.

    One second is the order of magnitude a 2000-row retention DELETE plus its WAL
    commit holds the write lock on this HDD. 200 ms gave up inside that window, which
    is how a retention pass could make a concurrent stage() fail.
    """
    return int(_env_number(_DURABLE_AUDIT_BUSY_TIMEOUT_MS_ENV, 1000.0, 1.0))


def _durable_audit_retry_attempts() -> int:
    """Attempts around that timeout. Deliberately still 4, not more.

    Worst case is attempts x busy_timeout plus backoff = 4 x 1.0 s + 0.3 s = 4.3 s,
    and stage() blocks an HTTP tool response for that whole time. Widening the timeout
    already bought 4.5x over the old 0.95 s; more attempts would buy latency, not
    reliability.
    """
    return int(_env_number(_DURABLE_AUDIT_RETRY_ATTEMPTS_ENV, 4.0, 1.0))


def _durable_audit_retry_backoff_s() -> float:
    """Linear backoff step between attempts, 0.05 s x attempt (was 0.025 s).

    With busy_timeout at a full second the pause is no longer what decides whether we
    outlast the other writer, so it only has to keep two retries off the same instant.
    """
    return _env_number(_DURABLE_AUDIT_RETRY_BACKOFF_S_ENV, 0.05, 0.0)


def _durable_audit_capacity_wait_s() -> float:
    """How long stage() waits for a full queue before rejecting: 1.0 s, was 0.25 s.

    Capacity-full is a drain-rate problem, not a lock problem -- the batch thread
    clears batch_size rows per pass -- so waiting is almost always cheaper for the
    caller than the rejection it would otherwise have to handle.
    """
    return _env_number(_DURABLE_AUDIT_CAPACITY_WAIT_S_ENV, 1.0, 0.0)


_DURABLE_AUDIT_RETENTION_DAYS_ENV = "ORCHESTRATOR_DURABLE_AUDIT_RETENTION_DAYS"
_DURABLE_AUDIT_RETENTION_DAYS_DEFAULT = 14
_DURABLE_AUDIT_RETENTION_BATCH = 2000


def _durable_audit_retention_days() -> int:
    """Return the LIVE retention window in days for APPLIED outbox rows.

    Re-read from the environment on every call, never snapshotted at import -- same
    contract as ``_session_retention_days`` in orchestrator/session/store.py (A22).
    An unset, blank, non-integer or NON-POSITIVE value falls back to the 14-day
    default: a window of 0 would mean "prune everything up to now", so a
    misconfigured value must never be allowed to WIDEN a destructive delete.
    """
    raw = os.environ.get(_DURABLE_AUDIT_RETENTION_DAYS_ENV, "")
    try:
        val = int(str(raw).strip())
    except (TypeError, ValueError):
        return _DURABLE_AUDIT_RETENTION_DAYS_DEFAULT
    if val <= 0:
        return _DURABLE_AUDIT_RETENTION_DAYS_DEFAULT
    return val

class AuditWriterError(RuntimeError):
    """Base error for durable audit writer failures."""


class AuditSchemaMismatch(AuditWriterError):
    """An existing outbox object does not match the required additive schema."""


class AuditStorageBusy(AuditWriterError):
    """SQLite stayed locked beyond the configured bounded retry budget."""


class AuditCapacityError(AuditWriterError):
    """The bounded pending queue did not gain capacity before the deadline."""


class AuditClosedError(AuditWriterError):
    """Admission is closed, so no new audit row can be accepted."""


class AuditIdConflict(AuditWriterError):
    """An audit_id was reused with different immutable content."""


class AuditApplyTimeout(AuditWriterError):
    """The event remained pending beyond submit_and_wait's deadline."""


@dataclass(frozen=True)
class AuditRecord:
    audit_id: str
    kind: str
    payload: Any
    created_at: float
    attempt_count: int


class AuditSink(Protocol):
    def __call__(self, record: AuditRecord) -> None:
        """Apply one record or raise; implementations should key on audit_id."""


@dataclass(frozen=True)
class AuditAck:
    audit_id: str
    state: str
    duplicate: bool
    durable: bool = True

    @property
    def applied(self) -> bool:
        return self.state == "applied"


@dataclass(frozen=True)
class ShutdownResult:
    drained: bool
    pending: int
    worker_stopped: bool
    timed_out: bool
    admission_closed: bool = True
    storage_error: str | None = None

    @property
    def ok(self) -> bool:
        return (
            self.drained
            and self.worker_stopped
            and self.admission_closed
            and self.storage_error is None
        )


class _CapacityFull:
    def __init__(self, pending: int) -> None:
        self.pending = pending


class DurableAuditWriter:
    """SQLite WAL outbox with bounded synchronous admission and a batch worker."""

    TABLE = "durable_audit_outbox"
    INDEX = "ix_durable_audit_outbox_state_created"
    LEASE_INDEX = "ix_durable_audit_outbox_claim"
    _CREATE_TABLE = """
        CREATE TABLE IF NOT EXISTS durable_audit_outbox (
            audit_id       TEXT PRIMARY KEY NOT NULL,
            kind           TEXT NOT NULL,
            payload_json   TEXT NOT NULL,
            state          TEXT NOT NULL
                           CHECK (state IN ('pending', 'applied')),
            created_at     REAL NOT NULL,
            applied_at     REAL,
            attempt_count  INTEGER NOT NULL DEFAULT 0,
            last_error     TEXT,
            lease_owner    TEXT,
            lease_until    REAL,
            next_attempt_at REAL NOT NULL DEFAULT 0
        )
    """
    _CREATE_INDEX = """
        CREATE INDEX IF NOT EXISTS ix_durable_audit_outbox_state_created
        ON durable_audit_outbox(state, created_at, audit_id)
    """
    _CREATE_LEASE_INDEX = """
        CREATE INDEX IF NOT EXISTS ix_durable_audit_outbox_claim
        ON durable_audit_outbox(
            state, next_attempt_at, lease_until, created_at, audit_id
        )
    """
    _EXPECTED_COLUMNS = {
        "audit_id": ("TEXT", 1, 1),
        "kind": ("TEXT", 1, 0),
        "payload_json": ("TEXT", 1, 0),
        "state": ("TEXT", 1, 0),
        "created_at": ("REAL", 1, 0),
        "applied_at": ("REAL", 0, 0),
        "attempt_count": ("INTEGER", 1, 0),
        "last_error": ("TEXT", 0, 0),
        "lease_owner": ("TEXT", 0, 0),
        "lease_until": ("REAL", 0, 0),
        "next_attempt_at": ("REAL", 1, 0),
    }

    def __init__(
        self,
        db_path: str | Path,
        sink: AuditSink | Callable[[AuditRecord], None],
        *,
        capacity: int = 4096,
        batch_size: int = 64,
        busy_timeout_ms: int | None = None,
        db_retry_attempts: int | None = None,
        retry_backoff_s: float | None = None,
        capacity_wait_timeout_s: float | None = None,
        poll_interval_s: float = 0.05,
        lease_duration_s: float = 1.0,
        sink_retry_base_s: float = 0.05,
        sink_retry_max_s: float = 5.0,
        auto_start: bool = True,
    ) -> None:
        # None means "read the live environment default". Resolved here, above the
        # validation below, so a misconfigured env value is rejected by exactly the
        # same checks as an explicit keyword argument.
        if busy_timeout_ms is None:
            busy_timeout_ms = _durable_audit_busy_timeout_ms()
        if db_retry_attempts is None:
            db_retry_attempts = _durable_audit_retry_attempts()
        if retry_backoff_s is None:
            retry_backoff_s = _durable_audit_retry_backoff_s()
        if capacity_wait_timeout_s is None:
            capacity_wait_timeout_s = _durable_audit_capacity_wait_s()
        if not callable(sink):
            raise TypeError("sink must be callable")
        if capacity < 1:
            raise ValueError("capacity must be >= 1")
        if batch_size < 1:
            raise ValueError("batch_size must be >= 1")
        if busy_timeout_ms < 1:
            raise ValueError("busy_timeout_ms must be >= 1")
        if db_retry_attempts < 1:
            raise ValueError("db_retry_attempts must be >= 1")
        if retry_backoff_s < 0 or capacity_wait_timeout_s < 0 or poll_interval_s <= 0:
            raise ValueError("timeouts/backoff must be non-negative and poll_interval_s > 0")
        if lease_duration_s <= 0:
            raise ValueError("lease_duration_s must be > 0")
        if sink_retry_base_s < 0 or sink_retry_max_s < sink_retry_base_s:
            raise ValueError("sink retry bounds must satisfy 0 <= base <= max")

        self.db_path = Path(db_path)
        self.sink = sink
        self.capacity = int(capacity)
        self.batch_size = int(batch_size)
        self.busy_timeout_ms = int(busy_timeout_ms)
        self.db_retry_attempts = int(db_retry_attempts)
        self.retry_backoff_s = float(retry_backoff_s)
        self.capacity_wait_timeout_s = float(capacity_wait_timeout_s)
        self.poll_interval_s = float(poll_interval_s)
        self.lease_duration_s = float(lease_duration_s)
        self.sink_retry_base_s = float(sink_retry_base_s)
        self.sink_retry_max_s = float(sink_retry_max_s)
        self._owner_token = uuid.uuid4().hex

        self._condition = threading.Condition(threading.RLock())
        self._metrics_lock = threading.Lock()
        self._wake = threading.Event()
        self._stop = threading.Event()
        self._abandon_apply_commit = threading.Event()
        self._thread: threading.Thread | None = None
        # Worker-thread connection cache (P16): the batch worker used to open a
        # fresh SQLite connection per claim -- ~16-20 connect()+PRAGMA pairs
        # per second on an idle outbox, measurable GIL/disk churn that starved
        # search/cbm. The worker reuses one connection and reconnects only
        # after an error; other threads keep per-call connections.
        self._worker_conn: sqlite3.Connection | None = None
        self._worker_conn_ident: int | None = None
        self._admission_open = True
        self._counters: dict[str, int] = {
            "staged_total": 0,
            "duplicate_total": 0,
            "id_conflicts_total": 0,
            "applied_total": 0,
            "apply_failures": 0,
            "busy_retries": 0,
            "storage_busy_failures": 0,
            "capacity_rejections": 0,
            "apply_wait_timeouts": 0,
            "lease_claimed_total": 0,
            "lease_recovered_total": 0,
            "lease_renewed_total": 0,
            "lease_released_total": 0,
            "lease_lost_total": 0,
            "sink_retry_deferred_total": 0,
            # D4 retention. Declared here because _bump() indexes _counters
            # directly and would raise KeyError on an undeclared key.
            "retention_runs_total": 0,
            "retention_deleted_total": 0,
            # Spool, same reason.
            "spool_parked_total": 0,
            "spool_park_failures": 0,
            "spool_replayed_total": 0,
            "spool_replay_conflicts": 0,
            "spool_replay_unusable": 0,
        }
        self._last_error: str | None = None
        self._last_error_kind: str | None = None
        self._last_error_context: str | None = None
        self._last_error_at: float | None = None

        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._retry_db("initialize schema", self._initialize_schema_once)
        if auto_start:
            self.start()

    @staticmethod
    def _is_busy_error(exc: sqlite3.OperationalError) -> bool:
        text = str(exc).lower()
        return "locked" in text or "busy" in text

    def _bump(self, key: str, amount: int = 1) -> None:
        with self._metrics_lock:
            self._counters[key] += amount

    @staticmethod
    def _classify_error(exc: BaseException | str) -> str:
        if isinstance(exc, AuditSchemaMismatch):
            return "schema"
        if isinstance(exc, (AuditStorageBusy, sqlite3.Error)):
            return "storage"
        if isinstance(exc, AuditIdConflict):
            return "id_conflict"
        if isinstance(exc, AuditCapacityError):
            return "capacity"
        if isinstance(exc, AuditApplyTimeout):
            return "apply_timeout"
        return "runtime"

    def _set_last_error(
        self,
        exc: BaseException | str,
        *,
        kind: str | None = None,
        context: str | None = None,
    ) -> None:
        with self._metrics_lock:
            self._last_error = str(exc)[:2000]
            self._last_error_kind = kind or self._classify_error(exc)
            self._last_error_context = context
            self._last_error_at = time.time()

    def _clear_last_error(
        self,
        *,
        kinds: set[str] | None = None,
        context: str | None = None,
    ) -> bool:
        """Clear only a proven-recovered current error; counters keep history."""

        with self._metrics_lock:
            if self._last_error is None:
                return False
            if kinds is not None and self._last_error_kind not in kinds:
                return False
            if context is not None and self._last_error_context != context:
                return False
            self._last_error = None
            self._last_error_kind = None
            self._last_error_context = None
            self._last_error_at = None
            return True

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(
            str(self.db_path),
            timeout=self.busy_timeout_ms / 1000.0,
            isolation_level=None,
        )
        conn.row_factory = sqlite3.Row
        conn.execute(f"PRAGMA busy_timeout={self.busy_timeout_ms}")
        conn.execute(f"PRAGMA synchronous={_durable_audit_synchronous()}")
        return conn

    def _worker_db(self) -> sqlite3.Connection:
        """Connection owned by the batch worker thread; opened once, reused.

        Reopened when missing, when the owning thread differs (a restarted
        worker), or after a claim failure dropped the previous handle. Dropped
        before every long idle wait, so a parked writer never pins the db file
        (B032/P16). Producers, heartbeats and ad-hoc reads keep their
        short-lived per-call connections: those run on other threads and are
        outside the idle hot loop.

        Built directly rather than via _connect(): the worker variant needs
        check_same_thread=False so shutdown() -- a different thread -- can
        close the handle of a worker that is already confirmed dead, and
        _connect's signature is a published contract that tests subclass and
        override. check_same_thread=False does NOT mean the connection is used
        from many threads: only the worker thread ever executes statements on
        it.
        """
        ident = threading.get_ident()
        conn = self._worker_conn
        if conn is not None and self._worker_conn_ident == ident:
            return conn
        if conn is not None:
            try:
                conn.close()
            except sqlite3.Error:
                pass
        conn = sqlite3.connect(
            str(self.db_path),
            timeout=self.busy_timeout_ms / 1000.0,
            isolation_level=None,
            check_same_thread=False,
        )
        conn.row_factory = sqlite3.Row
        conn.execute(f"PRAGMA busy_timeout={self.busy_timeout_ms}")
        conn.execute(f"PRAGMA synchronous={_durable_audit_synchronous()}")
        self._worker_conn = conn
        self._worker_conn_ident = ident
        return conn

    def _drop_worker_db(self) -> None:
        conn = self._worker_conn
        self._worker_conn = None
        self._worker_conn_ident = None
        if conn is not None:
            try:
                conn.close()
            except sqlite3.Error:
                pass

    def _retry_db(self, operation: str, fn: Callable[[], Any]) -> Any:
        last: sqlite3.OperationalError | None = None
        for attempt in range(1, self.db_retry_attempts + 1):
            try:
                result = fn()
                # A successful SQLite operation proves an earlier transient
                # storage-busy condition has recovered. Historical counters stay.
                self._clear_last_error(kinds={"storage"})
                return result
            except sqlite3.OperationalError as exc:
                if not self._is_busy_error(exc):
                    raise
                last = exc
                if attempt == self.db_retry_attempts:
                    break
                self._bump("busy_retries")
                time.sleep(self.retry_backoff_s * attempt)
        self._bump("storage_busy_failures")
        error = AuditStorageBusy(
            f"{operation} exceeded SQLite busy budget "
            f"({self.db_retry_attempts} attempts x {self.busy_timeout_ms}ms): {last}",
        )
        self._set_last_error(error, kind="storage", context=operation)
        raise error from last

    def _initialize_schema_once(self) -> None:
        conn = self._connect()
        try:
            mode = str(conn.execute("PRAGMA journal_mode=WAL").fetchone()[0]).lower()
            if mode != "wal":
                raise AuditSchemaMismatch(
                    f"durable audit database requires WAL journal mode, got {mode!r}",
                )
            # Serialize schema discovery plus additive DDL at the database.
            # A process-local lock would not protect multiple orchestrators.
            conn.execute("BEGIN IMMEDIATE")
            existed = conn.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
                (self.TABLE,),
            ).fetchone() is not None
            conn.execute(self._CREATE_TABLE)
            if existed:
                self._validate_existing_base_schema(conn)
            self._migrate_lease_columns(conn)
            self._validate_table(conn)
            conn.execute(self._CREATE_INDEX)
            conn.execute(self._CREATE_LEASE_INDEX)
            self._validate_indexes(conn)
            conn.execute("COMMIT")
        except BaseException:
            if conn.in_transaction:
                try:
                    conn.execute("ROLLBACK")
                except sqlite3.Error:
                    pass
            raise
        finally:
            conn.close()

    @staticmethod
    def _actual_columns(conn: sqlite3.Connection) -> dict[str, tuple[str, int, int]]:
        return {
            str(row["name"]): (
                str(row["type"]).upper(),
                int(row["notnull"]),
                int(row["pk"]),
            )
            for row in conn.execute(
                "PRAGMA table_info(durable_audit_outbox)",
            ).fetchall()
        }

    def _state_constraint_missing(self, conn: sqlite3.Connection) -> bool:
        sql_row = conn.execute(
            "SELECT sql FROM sqlite_master WHERE type='table' AND name=?",
            (self.TABLE,),
        ).fetchone()
        normalized_sql = "".join(str(sql_row["sql"] if sql_row else "").upper().split())
        return (
            "CHECK(STATEIN('PENDING','APPLIED'))" not in normalized_sql
            and 'CHECK(STATEIN("PENDING","APPLIED"))' not in normalized_sql
        )

    def _validate_existing_base_schema(self, conn: sqlite3.Connection) -> None:
        actual = self._actual_columns(conn)
        base_names = set(self._EXPECTED_COLUMNS) - {
            "lease_owner", "lease_until", "next_attempt_at",
        }
        mismatches = []
        for name in base_names:
            expected = self._EXPECTED_COLUMNS[name]
            if name not in actual:
                mismatches.append(f"missing column {name}")
            elif actual[name] != expected:
                mismatches.append(
                    f"column {name} expected {expected}, found {actual[name]}",
                )
        if self._state_constraint_missing(conn):
            mismatches.append("state CHECK must allow only pending/applied")
        if mismatches:
            raise AuditSchemaMismatch(
                f"{self.TABLE} schema mismatch: " + "; ".join(sorted(mismatches)),
            )

    def _migrate_lease_columns(self, conn: sqlite3.Connection) -> None:
        columns = self._actual_columns(conn)
        migrations = {
            "lease_owner": "ALTER TABLE durable_audit_outbox ADD COLUMN lease_owner TEXT",
            "lease_until": "ALTER TABLE durable_audit_outbox ADD COLUMN lease_until REAL",
            "next_attempt_at": (
                "ALTER TABLE durable_audit_outbox "
                "ADD COLUMN next_attempt_at REAL NOT NULL DEFAULT 0"
            ),
        }
        for name, statement in migrations.items():
            if name not in columns:
                conn.execute(statement)

    def _validate_table(self, conn: sqlite3.Connection) -> None:
        actual = self._actual_columns(conn)
        mismatches = []
        for name, expected in self._EXPECTED_COLUMNS.items():
            if name not in actual:
                mismatches.append(f"missing column {name}")
            elif actual[name] != expected:
                mismatches.append(
                    f"column {name} expected {expected}, found {actual[name]}",
                )
        if self._state_constraint_missing(conn):
            mismatches.append("state CHECK must allow only pending/applied")
        if mismatches:
            raise AuditSchemaMismatch(
                f"{self.TABLE} schema mismatch: " + "; ".join(mismatches),
            )

    def _validate_indexes(self, conn: sqlite3.Connection) -> None:
        index_rows = conn.execute(f"PRAGMA index_list({self.TABLE})").fetchall()
        names = {str(row["name"]) for row in index_rows}
        expected_indexes = {
            self.INDEX: ["state", "created_at", "audit_id"],
            self.LEASE_INDEX: [
                "state", "next_attempt_at", "lease_until", "created_at", "audit_id",
            ],
        }
        for index_name, expected in expected_indexes.items():
            if index_name not in names:
                raise AuditSchemaMismatch(f"missing index {index_name}")
            columns = [
                str(row["name"])
                for row in conn.execute(f"PRAGMA index_info({index_name})").fetchall()
            ]
            if columns != expected:
                raise AuditSchemaMismatch(
                    f"{index_name} columns expected {expected}, found {columns}",
                )
    @staticmethod
    def _canonical_payload(payload: Any) -> str:
        try:
            return json.dumps(
                payload,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
                allow_nan=False,
            )
        except (TypeError, ValueError) as exc:
            raise ValueError(f"payload must be finite JSON data: {exc}") from exc

    @staticmethod
    def _validate_identity(audit_id: str, kind: str) -> tuple[str, str]:
        audit_id = str(audit_id).strip()
        kind = str(kind).strip()
        if not audit_id:
            raise ValueError("audit_id must be non-empty")
        if not kind:
            raise ValueError("kind must be non-empty")
        if len(audit_id) > 512:
            raise ValueError("audit_id exceeds 512 characters")
        if len(kind) > 128:
            raise ValueError("kind exceeds 128 characters")
        return audit_id, kind

    def _stage_transaction(
        self, audit_id: str, kind: str, payload_json: str,
    ) -> AuditAck | _CapacityFull:
        conn = self._connect()
        try:
            conn.execute("BEGIN IMMEDIATE")
            existing = conn.execute(
                f"SELECT kind, payload_json, state FROM {self.TABLE} WHERE audit_id=?",
                (audit_id,),
            ).fetchone()
            if existing is not None:
                if (
                    str(existing["kind"]) != kind
                    or str(existing["payload_json"]) != payload_json
                ):
                    conn.execute("ROLLBACK")
                    raise AuditIdConflict(
                        f"audit_id {audit_id!r} already exists with different content",
                    )
                state = str(existing["state"])
                conn.execute("COMMIT")
                return AuditAck(audit_id=audit_id, state=state, duplicate=True)

            pending = int(
                conn.execute(
                    f"SELECT COUNT(*) FROM {self.TABLE} WHERE state='pending'",
                ).fetchone()[0],
            )
            if pending >= self.capacity:
                conn.execute("ROLLBACK")
                return _CapacityFull(pending)

            conn.execute(
                f"""INSERT INTO {self.TABLE}
                    (audit_id, kind, payload_json, state, created_at,
                     applied_at, attempt_count, last_error)
                    VALUES (?, ?, ?, 'pending', ?, NULL, 0, NULL)""",
                (audit_id, kind, payload_json, time.time()),
            )
            conn.execute("COMMIT")
            return AuditAck(audit_id=audit_id, state="pending", duplicate=False)
        except BaseException:
            if conn.in_transaction:
                try:
                    conn.execute("ROLLBACK")
                except sqlite3.Error:
                    pass
            raise
        finally:
            conn.close()

    def stage(
        self,
        audit_id: str,
        kind: str,
        payload: Any,
        *,
        timeout: float | None = None,
    ) -> AuditAck:
        """Durably stage an event; return only after the INSERT transaction commits."""

        audit_id, kind = self._validate_identity(audit_id, kind)
        payload_json = self._canonical_payload(payload)
        wait_budget = self.capacity_wait_timeout_s if timeout is None else float(timeout)
        if wait_budget < 0:
            raise ValueError("timeout must be >= 0")
        deadline = time.monotonic() + wait_budget

        with self._condition:
            while True:
                if not self._admission_open:
                    raise AuditClosedError("durable audit admission is closed")
                try:
                    result = self._retry_db(
                        "stage audit row",
                        lambda: self._stage_transaction(audit_id, kind, payload_json),
                    )
                except AuditIdConflict as exc:
                    self._bump("id_conflicts_total")
                    self._set_last_error(
                        exc, kind="id_conflict", context=audit_id,
                    )
                    raise
                if not isinstance(result, _CapacityFull):
                    if result.duplicate:
                        self._bump("duplicate_total")
                    else:
                        self._bump("staged_total")
                    self._clear_last_error(kinds={"id_conflict", "capacity"})
                    break
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    self._bump("capacity_rejections")
                    error = AuditCapacityError(
                        f"durable audit capacity {self.capacity} is full "
                        f"({result.pending} pending); no event was dropped",
                    )
                    self._set_last_error(error)
                    raise error
                self._condition.wait(
                    timeout=min(remaining, self.poll_interval_s),
                )

        if result.state == "pending":
            self._wake.set()
        return result

    def submit(
        self,
        audit_id: str,
        kind: str,
        payload: Any,
        *,
        timeout: float | None = None,
    ) -> AuditAck:
        return self.stage(audit_id, kind, payload, timeout=timeout)

    @property
    def spool_path(self) -> Path:
        """Where a row parks when SQLite stayed busy past the whole retry budget.

        Beside the outbox database, so whoever can find one can find the other.
        """
        return self.db_path.with_name(self.db_path.stem + "_spool.jsonl")

    def spool_failed_row(
        self,
        audit_id: str,
        kind: str,
        payload: Any,
        reason: str,
    ) -> bool:
        """Park a row stage() could not commit. Never raises: every caller is an
        except block, and a spool that throws would turn a recoverable drop into a
        crashed request.

        Until this existed the caller logged the failure and returned, so the payload
        was simply gone -- 21 rows were lost that way. A False return means even the
        spool write failed, which is the caller's signal to fall back to logging.
        """
        # "Never raises" has to be enforced HERE, not inherited from append_spool.
        # append_spool promises a False return, but this method is the contract the
        # except blocks depend on, and a defect below it must not escape upward.
        try:
            parked = append_spool(
                self.spool_path,
                {
                    "audit_id": str(audit_id),
                    "kind": str(kind),
                    "payload": payload,
                    "reason": str(reason)[:2000],
                    "parked_at": time.time(),
                },
            )
        except Exception:
            parked = False
        try:
            self._bump("spool_parked_total" if parked else "spool_park_failures")
        except Exception:
            pass
        return parked

    def spool_depth(self) -> int:
        """Rows still waiting on replay, for /health. 0 means nothing is parked."""
        return _spool_depth(self.spool_path)

    def replay_spool(self) -> dict[str, int]:
        """Re-stage every parked row. Safe to call on every start: at-least-once."""
        return drain_spool(self.spool_path, self._replay_parked_row)

    def _replay_parked_row(self, record: dict[str, Any]) -> bool:
        """Return True when the row reached the outbox, so drain_spool can drop it."""
        audit_id = str(record.get("audit_id") or "")
        kind = str(record.get("kind") or "")
        if not audit_id or not kind:
            # Unusable record: no retry can ever make it stageable, and keeping it
            # would block the spool from ever emptying.
            self._bump("spool_replay_unusable")
            return True
        try:
            self.stage(audit_id, kind, record.get("payload"))
        except AuditIdConflict:
            # The row is already in the outbox. Delivery is at-least-once by design,
            # so "already present" is the success case, not a failure to retry.
            self._bump("spool_replay_conflicts")
            return True
        except Exception:
            # Storage still unhealthy, or admission closed. Keep it for the next start.
            return False
        self._bump("spool_replayed_total")
        return True

    def _read_row_state(self, audit_id: str) -> tuple[str, str | None] | None:
        conn = self._connect()
        try:
            row = conn.execute(
                f"SELECT state, last_error FROM {self.TABLE} WHERE audit_id=?",
                (audit_id,),
            ).fetchone()
            if row is None:
                return None
            return str(row["state"]), (
                None if row["last_error"] is None else str(row["last_error"])
            )
        finally:
            conn.close()

    def submit_and_wait(
        self,
        audit_id: str,
        kind: str,
        payload: Any,
        *,
        timeout: float = 5.0,
    ) -> AuditAck:
        """Return an applied ACK only after the pending->applied COMMIT is visible."""

        if timeout < 0:
            raise ValueError("timeout must be >= 0")
        deadline = time.monotonic() + timeout
        ack = self.stage(audit_id, kind, payload, timeout=timeout)
        if ack.applied:
            return ack

        with self._condition:
            while True:
                current = self._retry_db(
                    "read applied acknowledgement",
                    lambda: self._read_row_state(audit_id),
                )
                if current is None:
                    raise AuditWriterError(
                        f"staged audit row {audit_id!r} disappeared",
                    )
                state, last_error = current
                if state == "applied":
                    return AuditAck(
                        audit_id=audit_id,
                        state="applied",
                        duplicate=ack.duplicate,
                    )
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    self._bump("apply_wait_timeouts")
                    suffix = f"; last sink error: {last_error}" if last_error else ""
                    error = AuditApplyTimeout(
                        f"audit_id {audit_id!r} remains pending after {timeout:.3f}s"
                        f"{suffix}",
                    )
                    self._set_last_error(
                        error, kind="apply_timeout", context=audit_id,
                    )
                    raise error
                self._condition.wait(
                    timeout=min(remaining, self.poll_interval_s),
                )

    def _claim_pending_batch(self) -> list[AuditRecord]:
        """Atomically lease a batch so peer writers cannot invoke the same sink.

        On the batch-worker thread the connection is the cached worker
        connection (opened once, not per claim); anywhere else a short-lived
        connection is used as before. A failed claim drops the cached worker
        connection so the retry reconnects instead of reusing a bad handle.
        """

        on_worker = threading.current_thread() is self._thread
        conn = self._worker_db() if on_worker else self._connect()
        claimed_rows: list[sqlite3.Row] = []
        recovered = 0
        try:
            # Probe without a write transaction. Idle peer workers must not
            # continuously starve the active owner's lease heartbeat.
            probe_now = time.time()
            probe = conn.execute(
                f"""SELECT 1 FROM {self.TABLE}
                    WHERE state='pending'
                      AND next_attempt_at<=?
                      AND (lease_owner IS NULL OR lease_until IS NULL OR lease_until<=?)
                    LIMIT 1""",
                (probe_now, probe_now),
            ).fetchone()
            if probe is None:
                return []

            conn.execute("BEGIN IMMEDIATE")
            now = time.time()
            lease_until = now + self.lease_duration_s
            rows = conn.execute(
                f"""SELECT audit_id, kind, payload_json, created_at,
                           attempt_count, lease_owner, lease_until
                    FROM {self.TABLE}
                    WHERE state='pending'
                      AND next_attempt_at<=?
                      AND (lease_owner IS NULL OR lease_until IS NULL OR lease_until<=?)
                    ORDER BY created_at, audit_id
                    LIMIT ?""",
                (now, now, self.batch_size),
            ).fetchall()
            for row in rows:
                cursor = conn.execute(
                    f"""UPDATE {self.TABLE}
                        SET lease_owner=?, lease_until=?
                        WHERE audit_id=? AND state='pending'
                          AND next_attempt_at<=?
                          AND (lease_owner IS NULL OR lease_until IS NULL
                               OR lease_until<=?)""",
                    (
                        self._owner_token,
                        lease_until,
                        str(row["audit_id"]),
                        now,
                        now,
                    ),
                )
                if cursor.rowcount == 1:
                    claimed_rows.append(row)
                    if row["lease_owner"] is not None:
                        recovered += 1
            conn.execute("COMMIT")
        except BaseException:
            if conn.in_transaction:
                try:
                    conn.execute("ROLLBACK")
                except sqlite3.Error:
                    pass
            if on_worker:
                self._drop_worker_db()
            raise
        finally:
            if not on_worker:
                conn.close()

        if claimed_rows:
            self._bump("lease_claimed_total", len(claimed_rows))
        if recovered:
            self._bump("lease_recovered_total", recovered)

        records: list[AuditRecord] = []
        for row in claimed_rows:
            try:
                payload = json.loads(str(row["payload_json"]))
            except json.JSONDecodeError as exc:
                raise AuditSchemaMismatch(
                    f"invalid payload_json for audit_id {row['audit_id']!r}: {exc}",
                ) from exc
            records.append(
                AuditRecord(
                    audit_id=str(row["audit_id"]),
                    kind=str(row["kind"]),
                    payload=payload,
                    created_at=float(row["created_at"]),
                    attempt_count=int(row["attempt_count"]),
                ),
            )
        return records

    # Upper bound for one idle sleep. Long enough that an empty outbox costs
    # ~0.5 claims/s instead of ~16-20; short enough that a crashed peer's lease
    # (minimum lease_duration_s) is recovered promptly even if the computed
    # wake-up were somehow missed.
    _IDLE_WAIT_CAP_S = 2.0

    def _next_idle_wait_s(self) -> float:
        """Seconds until the next event an idle worker must wake for.

        The claim probe just found no claimable row. The remaining reasons to
        wake are: (a) a producer staging a row -- covered by self._wake, set in
        stage(); (b) a retry-due row reaching next_attempt_at; (c) a peer
        writer's lease expiring. (b) and (c) are readable from the table, so
        sleep until the earliest of them, capped at _IDLE_WAIT_CAP_S when none
        exists. Any failure falls back to poll_interval_s -- the old fixed
        cadence -- so a scheduler defect costs latency, never a lost row.
        Runs on the worker thread and reuses the cached worker connection.
        """
        try:
            conn = self._worker_db()
            now = time.time()
            row = conn.execute(
                f"""SELECT
                        MIN(CASE WHEN next_attempt_at>? THEN next_attempt_at END)
                            AS next_retry,
                        MIN(CASE WHEN lease_until>? THEN lease_until END)
                            AS next_lease
                    FROM {self.TABLE}
                    WHERE state='pending'""",
                (now, now),
            ).fetchone()
        except Exception as exc:
            self._set_last_error(exc)
            self._drop_worker_db()
            return self.poll_interval_s
        due = [
            float(value)
            for value in (
                None if row is None else row["next_retry"],
                None if row is None else row["next_lease"],
            )
            if value is not None
        ]
        if not due:
            return self._IDLE_WAIT_CAP_S
        return max(
            self.poll_interval_s,
            min(self._IDLE_WAIT_CAP_S, min(due) - now),
        )

    def _renew_lease(self, audit_id: str) -> bool:
        def update() -> bool:
            conn = self._connect()
            try:
                cursor = conn.execute(
                    f"""UPDATE {self.TABLE}
                        SET lease_until=?
                        WHERE audit_id=? AND state='pending' AND lease_owner=?""",
                    (time.time() + self.lease_duration_s, audit_id, self._owner_token),
                )
                return cursor.rowcount == 1
            finally:
                conn.close()

        changed = bool(self._retry_db("renew audit lease", update))
        if changed:
            self._bump("lease_renewed_total")
        return changed

    def _release_lease(self, audit_id: str) -> bool:
        def update() -> bool:
            conn = self._connect()
            try:
                cursor = conn.execute(
                    f"""UPDATE {self.TABLE}
                        SET lease_owner=NULL, lease_until=NULL
                        WHERE audit_id=? AND state='pending' AND lease_owner=?""",
                    (audit_id, self._owner_token),
                )
                return cursor.rowcount == 1
            finally:
                conn.close()

        return bool(self._retry_db("release audit lease", update))

    def _release_owned_leases(self) -> int:
        def update() -> int:
            conn = self._connect()
            try:
                cursor = conn.execute(
                    f"""UPDATE {self.TABLE}
                        SET lease_owner=NULL, lease_until=NULL
                        WHERE state='pending' AND lease_owner=?""",
                    (self._owner_token,),
                )
                return int(cursor.rowcount)
            finally:
                conn.close()

        released = int(self._retry_db("release owned audit leases", update))
        if released:
            self._bump("lease_released_total", released)
        return released

    def _lease_heartbeat(self, audit_id: str, done: threading.Event) -> None:
        # _renew_lease goes through _retry_db, so a tick that lands while
        # _claim_pending_batch holds BEGIN IMMEDIATE (up to batch_size UPDATEs) or
        # prune_applied_older_than is mid-DELETE burns the busy budget and then comes
        # straight back at the same interval. Back off after a failure instead. The
        # ceiling stays under lease_duration_s so a backed-off tick cannot let the
        # lease expire, which would hand this row to another claimant.
        base = max(0.01, min(self.lease_duration_s / 3.0, 0.25))
        ceiling = max(base, self.lease_duration_s * 0.6)
        interval = base
        while not done.wait(interval):
            try:
                if not self._renew_lease(audit_id):
                    self._bump("lease_lost_total")
                    return
            except Exception as exc:
                # Keep retrying while the sink is live. Expiry still provides
                # crash recovery, and the sink must remain idempotent by audit_id.
                self._set_last_error(exc)
                interval = min(ceiling, interval * 2)
                continue
            interval = base

    def _record_sink_failure(self, record: AuditRecord, exc: BaseException) -> bool:
        message = f"{type(exc).__name__}: {exc}"[:2000]
        exponent = min(max(record.attempt_count, 0), 16)
        retry_delay = min(
            self.sink_retry_max_s,
            self.sink_retry_base_s * (2 ** exponent),
        )

        def update() -> bool:
            conn = self._connect()
            try:
                conn.execute("BEGIN IMMEDIATE")
                cursor = conn.execute(
                    f"""UPDATE {self.TABLE}
                        SET attempt_count=attempt_count+1, last_error=?,
                            next_attempt_at=?, lease_owner=NULL, lease_until=NULL
                        WHERE audit_id=? AND state='pending' AND lease_owner=?""",
                    (
                        message,
                        time.time() + retry_delay,
                        record.audit_id,
                        self._owner_token,
                    ),
                )
                changed = cursor.rowcount == 1
                conn.execute("COMMIT")
                return changed
            except BaseException:
                if conn.in_transaction:
                    try:
                        conn.execute("ROLLBACK")
                    except sqlite3.Error:
                        pass
                raise
            finally:
                conn.close()

        changed = bool(self._retry_db("record sink failure", update))
        if changed and retry_delay > 0:
            self._bump("sink_retry_deferred_total")
        return changed

    def _mark_applied(self, audit_id: str) -> bool:
        def update() -> bool:
            conn = self._connect()
            try:
                conn.execute("BEGIN IMMEDIATE")
                cursor = conn.execute(
                    f"""UPDATE {self.TABLE}
                        SET state='applied', applied_at=?,
                            attempt_count=attempt_count+1, last_error=NULL,
                            next_attempt_at=0, lease_owner=NULL, lease_until=NULL
                        WHERE audit_id=? AND state='pending' AND lease_owner=?""",
                    (time.time(), audit_id, self._owner_token),
                )
                changed = cursor.rowcount == 1
                conn.execute("COMMIT")
                return changed
            except BaseException:
                if conn.in_transaction:
                    try:
                        conn.execute("ROLLBACK")
                    except sqlite3.Error:
                        pass
                raise
            finally:
                conn.close()

        return bool(self._retry_db("commit applied acknowledgement", update))

    def _apply_one(self, record: AuditRecord) -> bool:
        try:
            if not self._renew_lease(record.audit_id):
                self._bump("lease_lost_total")
                return False
        except Exception as exc:
            self._set_last_error(exc)
            return False

        heartbeat_done = threading.Event()
        heartbeat = threading.Thread(
            target=self._lease_heartbeat,
            args=(record.audit_id, heartbeat_done),
            name=f"durable-audit-lease-{record.audit_id[:24]}",
            daemon=True,
        )
        heartbeat.start()
        sink_error: Exception | None = None
        try:
            self.sink(record)
        except Exception as exc:
            sink_error = exc
        finally:
            heartbeat_done.set()
            heartbeat.join(timeout=max(0.05, self.lease_duration_s))

        if sink_error is not None:
            self._bump("apply_failures")
            self._set_last_error(
                sink_error, kind="sink", context=record.audit_id,
            )
            try:
                if not self._record_sink_failure(record, sink_error):
                    self._bump("lease_lost_total")
            except Exception as store_exc:
                self._set_last_error(store_exc)
            with self._condition:
                self._condition.notify_all()
            return False

        if self._abandon_apply_commit.is_set():
            try:
                self._release_lease(record.audit_id)
            except Exception as exc:
                self._set_last_error(exc)
            with self._condition:
                self._condition.notify_all()
            return False

        try:
            changed = self._mark_applied(record.audit_id)
        except Exception as exc:
            self._set_last_error(exc)
            with self._condition:
                self._condition.notify_all()
            return False
        if changed:
            self._bump("applied_total")
            # A later successful apply proves the writer is healthy.
            # apply_timeout is session-sink lag, not a sticky writer death.
            self._clear_last_error(kinds={"apply_timeout"})
            self._clear_last_error(kinds={"sink"})
        else:
            self._bump("lease_lost_total")
        with self._condition:
            self._condition.notify_all()
        return changed

    # --- retention (D4) ---------------------------------------------------- #
    def prune_applied_older_than(
        self,
        days: int | None = None,
        *,
        dry_run: bool = False,
        limit: int = _DURABLE_AUDIT_RETENTION_BATCH,
    ) -> dict:
        """Delete APPLIED outbox rows older than the retention window.

        Why this exists (measured 2026-09-22): durable_audit.db had reached 38.4MB /
        ~47800 applied rows and this module contained no DELETE, no prune and no
        VACUUM anywhere -- the outbox is append-only. An applied row has already been
        mirrored into the session store and nothing ever reads it back, so it is pure
        history. sessions.db got exactly this treatment in A22.

        WHITELIST, never blacklist. The predicate is positively
        ``state='applied' AND created_at <= cutoff``. A row in ANY other state --
        pending, or a state a future version adds -- is unreachable from here, so an
        in-flight or failed audit can never be removed by retention. That is also why
        this is not written as "delete everything except pending".

        Cutoff TYPE (the A22 trap): ``created_at`` is REAL epoch seconds, so the
        cutoff is an epoch float. SQLite orders storage classes REAL < TEXT, so
        comparing this column against an ISO *string* would match EVERY row and empty
        the table while looking like a correct query.

        The delete is BATCHED, each batch its own implicit commit, driven by the
        existing ``ix_durable_audit_outbox_state_created(state, created_at,
        audit_id)`` index -- so the worker thread never holds one long exclusive lock
        on a HDD while producers are staging. ``limit`` bounds ONE batch, not the run.

        VACUUM is deliberately NOT run here: it cannot run inside a transaction and
        would hold an exclusive lock over the whole file. Reclaiming file space stays
        a separate, explicit operation.

        ``dry_run=True`` counts the eligible rows and deletes nothing, which is the
        shape a destructive-gate review needs.
        """
        window = _durable_audit_retention_days() if days is None else max(0, int(days))
        if int(limit) < 1:
            raise ValueError("limit must be >= 1")
        cutoff = time.time() - (window * 86400.0)

        def count() -> int:
            conn = self._connect()
            try:
                return int(
                    conn.execute(
                        f"SELECT COUNT(*) FROM {self.TABLE} "
                        "WHERE state='applied' AND created_at <= ?",
                        (cutoff,),
                    ).fetchone()[0],
                )
            finally:
                conn.close()

        eligible = int(self._retry_db("count prunable audits", count))
        out: dict[str, Any] = {
            "ok": True,
            "retention_days": window,
            "cutoff_epoch": cutoff,
            "eligible": eligible,
            "deleted": 0,
            "batches": 0,
            "dry_run": bool(dry_run),
        }
        if dry_run or eligible <= 0:
            return out

        def delete_batch() -> int:
            conn = self._connect()
            try:
                cur = conn.execute(
                    f"DELETE FROM {self.TABLE} WHERE audit_id IN ("
                    f"SELECT audit_id FROM {self.TABLE} "
                    "WHERE state='applied' AND created_at <= ? "
                    "ORDER BY created_at, audit_id LIMIT ?)",
                    (cutoff, int(limit)),
                )
                return int(cur.rowcount or 0)
            finally:
                conn.close()

        while not self._stop.is_set():
            removed = int(self._retry_db("prune applied audits", delete_batch))
            if removed <= 0:
                break
            out["deleted"] += removed
            out["batches"] += 1
            self._bump("retention_deleted_total", removed)
        self._bump("retention_runs_total")
        return out

    def _prune_applied_on_start(self) -> None:
        """Run retention once per writer start, ON THE WORKER THREAD.

        Not in ``start()``: that method holds ``self._condition``, and a multi-batch
        delete under that lock would block every producer staging an audit row. Here
        it costs the first poll interval and nothing else.

        Failures are recorded and swallowed. Retention is housekeeping; it must never
        be the reason audits stop being applied.
        """
        try:
            self.prune_applied_older_than()
        except BaseException as exc:  # housekeeping must never kill the worker
            self._set_last_error(exc, kind="retention",
                                 context="prune_applied_on_start")

    def _wait_for_work(self, timeout_s: float) -> bool:
        """Sleep until stage()/shutdown wakes the worker or the timeout runs out.

        Returns True when the event fired (a producer staged a row or shutdown
        began), False on timeout -- the caller uses that to tell "woken for
        work" apart from "slept the whole window" for the idle-connection drop.
        """
        woke = self._wake.wait(timeout_s)
        self._wake.clear()
        return woke

    def _worker_main(self) -> None:
        try:
            self._prune_applied_on_start()
            idle_cycles = 0
            while not self._stop.is_set():
                try:
                    batch = self._retry_db(
                        "claim pending audit batch", self._claim_pending_batch,
                    )
                except AuditWriterError as exc:
                    self._set_last_error(exc)
                    self._wait_for_work(self.poll_interval_s)
                    continue
                except BaseException as exc:
                    self._set_last_error(exc)
                    self._stop.set()
                    break

                if not batch:
                    idle_wait = self._next_idle_wait_s()
                    if idle_wait >= self._IDLE_WAIT_CAP_S and idle_cycles > 0:
                        # Durably idle: the previous wait already ran its full
                        # cap with no producer wake, and there is still nothing
                        # pending. Release the db handle for the whole wait
                        # (B032/P16): on Windows an open SQLite handle makes
                        # durable_audit.db's directory un-removable for its
                        # owner's teardown, and a writer parked at its idle cap
                        # is exactly the state the module-global server writer
                        # sits in when a test session ends. The connection
                        # reopens lazily on the next claim. idle_cycles > 0
                        # keeps the handle through a burst: a submit arriving
                        # right after an empty claim must not pay a reconnect.
                        self._drop_worker_db()
                    if self._wait_for_work(idle_wait):
                        idle_cycles = 0
                    else:
                        idle_cycles += 1
                    continue

                idle_cycles = 0
                had_failure = False
                for record in batch:
                    if self._stop.is_set():
                        break
                    if not self._apply_one(record):
                        had_failure = True
                if had_failure and not self._stop.is_set():
                    # Failed rows got a future next_attempt_at; sleep until the
                    # earliest one is due instead of re-claiming at 20 Hz.
                    retry_wait = self._next_idle_wait_s()
                    if retry_wait >= self._IDLE_WAIT_CAP_S and idle_cycles > 0:
                        self._drop_worker_db()
                    if self._wait_for_work(retry_wait):
                        idle_cycles = 0
                    else:
                        idle_cycles += 1
        finally:
            self._drop_worker_db()
            try:
                self._release_owned_leases()
            except Exception as exc:
                self._set_last_error(exc)
            with self._condition:
                self._condition.notify_all()

    def start(self) -> None:
        with self._condition:
            if self._thread is not None and self._thread.is_alive():
                return
            if self._stop.is_set():
                raise AuditClosedError("a stopped durable audit writer cannot restart")
            self._thread = threading.Thread(
                target=self._worker_main,
                name=f"durable-audit-{self.db_path.name}",
                daemon=True,
            )
            self._thread.start()
            self._wake.set()
        # Outside the condition on purpose: the replay calls stage(), which takes the
        # same lock. Rows parked by spool_failed_row are recoverable only here, so a
        # writer that started without draining would leave them on disk forever.
        self.replay_spool()

    def close_admission(self) -> None:
        with self._condition:
            self._admission_open = False
            self._condition.notify_all()

    def pending_count(self) -> int:
        def count() -> int:
            conn = self._connect()
            try:
                return int(
                    conn.execute(
                        f"SELECT COUNT(*) FROM {self.TABLE} WHERE state='pending'",
                    ).fetchone()[0],
                )
            finally:
                conn.close()

        return int(self._retry_db("count pending audits", count))

    def wait_until_idle(self, timeout: float = 5.0) -> bool:
        if timeout < 0:
            raise ValueError("timeout must be >= 0")
        deadline = time.monotonic() + timeout
        with self._condition:
            while True:
                if self.pending_count() == 0:
                    return True
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    return False
                self._condition.wait(
                    timeout=min(remaining, self.poll_interval_s),
                )

    def metrics(self) -> dict[str, Any]:
        def state_counts() -> dict[str, Any]:
            conn = self._connect()
            try:
                counts: dict[str, Any] = {"pending": 0, "applied": 0}
                for row in conn.execute(
                    f"SELECT state, COUNT(*) AS n FROM {self.TABLE} GROUP BY state",
                ):
                    state = str(row["state"])
                    if state not in counts:
                        raise AuditSchemaMismatch(
                            f"unexpected durable audit state {state!r}",
                        )
                    counts[state] = int(row["n"])
                now = time.time()
                lease_row = conn.execute(
                    f"""SELECT
                            SUM(CASE WHEN state='pending' AND lease_owner IS NOT NULL
                                      AND lease_until>? THEN 1 ELSE 0 END) AS active,
                            SUM(CASE WHEN state='pending' AND lease_owner IS NOT NULL
                                      AND lease_until<=? THEN 1 ELSE 0 END) AS expired,
                            MIN(CASE WHEN state='pending' AND next_attempt_at>?
                                     THEN next_attempt_at END) AS next_retry
                        FROM {self.TABLE}""",
                    (now, now, now),
                ).fetchone()
                counts["active_leases"] = int(lease_row["active"] or 0)
                counts["expired_leases"] = int(lease_row["expired"] or 0)
                counts["next_retry_at"] = (
                    None if lease_row["next_retry"] is None
                    else float(lease_row["next_retry"])
                )
                return counts
            finally:
                conn.close()

        # Snapshot the error state BEFORE the metrics read, not after. state_counts goes
        # through _retry_db, and _retry_db clears a "storage" last_error on every success
        # -- so reading _last_error_kind below that call erased exactly the storage drop
        # the caller came to ask about, and durable_audit_ok could never go false for it.
        # The clearing is right (a success does prove recovery); the read order was not.
        with self._metrics_lock:
            error_snapshot = (
                self._last_error,
                self._last_error_kind,
                self._last_error_context,
                self._last_error_at,
            )
        counts = self._retry_db("read audit metrics", state_counts)
        parked = _spool_depth(self.spool_path)
        with self._metrics_lock:
            result: dict[str, Any] = dict(self._counters)
            (
                result["last_error"],
                result["last_error_kind"],
                result["last_error_context"],
                result["last_error_at"],
            ) = error_snapshot
        with self._condition:
            result.update(
                {
                    "pending": counts["pending"],
                    "applied": counts["applied"],
                    "active_leases": counts["active_leases"],
                    "expired_leases": counts["expired_leases"],
                    "next_retry_at": counts["next_retry_at"],
                    "capacity": self.capacity,
                    "admission_open": self._admission_open,
                    "worker_alive": bool(
                        self._thread is not None and self._thread.is_alive(),
                    ),
                    "lease_duration_s": self.lease_duration_s,
                    "owner_token_suffix": self._owner_token[-8:],
                    # Parked rows and the live busy budget: without these a reader of
                    # /health cannot tell a healthy writer from one that is dropping.
                    "spool_depth": parked,
                    "busy_timeout_ms": self.busy_timeout_ms,
                    "db_retry_attempts": self.db_retry_attempts,
                },
            )
        return result

    def shutdown(self, timeout: float = 5.0, *, drain: bool = True) -> ShutdownResult:
        """Close admission and stop without skipping cleanup on storage failures."""

        if timeout < 0:
            raise ValueError("timeout must be >= 0")
        self.close_admission()
        deadline = time.monotonic() + timeout
        pending: int | None = None
        errors: list[str] = []

        try:
            pending = self.pending_count()
            if drain:
                with self._condition:
                    while pending:
                        remaining = deadline - time.monotonic()
                        if remaining <= 0:
                            break
                        self._condition.wait(
                            timeout=min(remaining, self.poll_interval_s),
                        )
                        pending = self.pending_count()
        except Exception as exc:
            errors.append(f"{type(exc).__name__}: {exc}")
            self._set_last_error(exc)
        finally:
            if pending is None or pending != 0:
                self._abandon_apply_commit.set()
            self._stop.set()
            self._wake.set()
            with self._condition:
                self._condition.notify_all()

            thread = self._thread
            if thread is not None:
                try:
                    thread.join(timeout=max(0.0, deadline - time.monotonic()))
                except Exception as exc:
                    errors.append(f"worker join failed: {type(exc).__name__}: {exc}")
                    self._set_last_error(exc)
            # B032/P16: the worker's finally already drops its cached connection
            # on a normal exit, but a worker that never started (auto_start=False)
            # or died before its finally ran would leave the handle open, and on
            # Windows an open durable_audit.db makes its directory un-rmtree-able.
            # A confirmed-dead worker cannot be using the connection, so closing
            # it here is safe (the worker connection is check_same_thread=False
            # exactly so a non-worker thread may close it; only the worker ever
            # executes statements on it). A still-alive worker keeps its handle:
            # the writer's working life has not ended.
            if thread is None or not thread.is_alive():
                self._drop_worker_db()

        stopped = self._thread is None or not self._thread.is_alive()
        try:
            final_pending = self.pending_count()
        except Exception as exc:
            errors.append(f"final pending count failed: {type(exc).__name__}: {exc}")
            self._set_last_error(exc)
            final_pending = -1 if pending is None else pending

        drained = final_pending == 0
        timed_out = bool(drain and (not drained or not stopped))
        return ShutdownResult(
            drained=drained,
            pending=final_pending,
            worker_stopped=stopped,
            timed_out=timed_out,
            admission_closed=not self._admission_open,
            storage_error="; ".join(errors) or None,
        )

    def wait_stopped(self, timeout: float = 5.0) -> bool:
        thread = self._thread
        if thread is None:
            return True
        thread.join(timeout=max(0.0, timeout))
        return not thread.is_alive()


__all__ = [
    "SESSION_EVENT_KIND",
    "WRITE_LEDGER_KIND",
    "AuditAck",
    "AuditApplyTimeout",
    "AuditCapacityError",
    "AuditClosedError",
    "AuditIdConflict",
    "AuditRecord",
    "AuditSchemaMismatch",
    "AuditSink",
    "AuditStorageBusy",
    "AuditWriterError",
    "DurableAuditWriter",
    "ShutdownResult",
]
