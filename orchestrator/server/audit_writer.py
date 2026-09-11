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
        busy_timeout_ms: int = 200,
        db_retry_attempts: int = 4,
        retry_backoff_s: float = 0.025,
        capacity_wait_timeout_s: float = 0.25,
        poll_interval_s: float = 0.05,
        lease_duration_s: float = 1.0,
        sink_retry_base_s: float = 0.05,
        sink_retry_max_s: float = 5.0,
        auto_start: bool = True,
    ) -> None:
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
        """Atomically lease a batch so peer writers cannot invoke the same sink."""

        conn = self._connect()
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
            raise
        finally:
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
        interval = max(0.01, min(self.lease_duration_s / 3.0, 0.25))
        while not done.wait(interval):
            try:
                if not self._renew_lease(audit_id):
                    self._bump("lease_lost_total")
                    return
            except Exception as exc:
                # Keep retrying while the sink is live. Expiry still provides
                # crash recovery, and the sink must remain idempotent by audit_id.
                self._set_last_error(exc)

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

    def _worker_main(self) -> None:
        try:
            while not self._stop.is_set():
                try:
                    batch = self._retry_db(
                        "claim pending audit batch", self._claim_pending_batch,
                    )
                except AuditWriterError as exc:
                    self._set_last_error(exc)
                    self._wake.wait(self.poll_interval_s)
                    self._wake.clear()
                    continue
                except BaseException as exc:
                    self._set_last_error(exc)
                    self._stop.set()
                    break

                if not batch:
                    self._wake.wait(self.poll_interval_s)
                    self._wake.clear()
                    continue

                had_failure = False
                for record in batch:
                    if self._stop.is_set():
                        break
                    if not self._apply_one(record):
                        had_failure = True
                if had_failure and not self._stop.is_set():
                    self._wake.wait(self.poll_interval_s)
                    self._wake.clear()
        finally:
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

        counts = self._retry_db("read audit metrics", state_counts)
        with self._metrics_lock:
            result: dict[str, Any] = dict(self._counters)
            result["last_error"] = self._last_error
            result["last_error_kind"] = self._last_error_kind
            result["last_error_context"] = self._last_error_context
            result["last_error_at"] = self._last_error_at
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
