# -*- coding: utf-8 -*-
"""Phase-0 Step 0.6: lock-holder instrumentation (ORCH_LOCK_HOLDER_TRACE).

Two layers (PLAN_TOOLA_1210_FINAL.md, Step 0.6):

Layer 1 -- in process:
  install() patches sqlite3.connect process-wide. The factory
  (traced_connect) wraps ONLY connections whose database resolves to
  sessions.db or durable_audit.db; every other connect passes through to
  the stock sqlite3.connect untouched.
  The wrapper records, per open transaction, the stack that opened it, the
  thread, and the tool running at open time (best effort: the dispatcher
  sets a ContextVar; asyncio.to_thread propagates it, custom
  executor-lane worker threads do not inherit it).
  start_watchdog() runs a daemon thread ticking every 1 s. A wrapped
  connection with in_transaction True for more than 2 s gets ONE WARNING
  log carrying the transaction-open stack; more than 30 s gets ONE ERROR
  log, which is the cue to run the layer-2 script.

Layer 2 -- out of process:
  sandbox-1210/lock_holder_layer2.ps1, run via ToolA run_ps1_script when a
  lock_hold exceeds 30 s. Lists processes holding sessions.db (handle.exe
  when present, psutil fallback), checks Defender exclusions, and checks
  which sync/backup tools touch .orchestrator_cache.

Contract (Step 0.6 item 4 + G4):
  - Everything is behind the env flag ORCH_LOCK_HOLDER_TRACE, default OFF,
    read once at import time (same pattern as ORCH_PHASE_TIMING).
  - Flag OFF: install()/start_watchdog() are no-ops and traced_connect is
    never installed, so sqlite3.connect stays the stock one. Zero overhead,
    zero behaviour change.
  - Flag ON: per-connect cost is one cheap basename prefilter plus a path
    compare; per-execute cost is one in_transaction read. isolation_level
    and commit/rollback semantics are NEVER changed -- the wrapper only
    observes.
"""

from __future__ import annotations

import contextvars
import logging
import os
import sqlite3
import threading
import time
import traceback
import weakref

logger = logging.getLogger(__name__)

_FLAG_NAME = "ORCH_LOCK_HOLDER_TRACE"
_OFF_VALUES = {"", "0", "off", "false", "no", "none"}

_ENABLED = os.environ.get(_FLAG_NAME, "").strip().lower() not in _OFF_VALUES

# Watchdog tick and alert thresholds (seconds).
_TICK_S = 1.0
_ALERT_AFTER_S = 2.0    # in_transaction longer than this -> WARNING with open-stack
_LAYER2_AFTER_S = 30.0  # ... longer than this -> ERROR (cue to run the layer-2 script)

_TARGET_BASENAMES = ("sessions.db", "durable_audit.db")

# The stock connector, captured at import time (before install() patches it).
_real_connect = sqlite3.connect

_current_tool: contextvars.ContextVar = contextvars.ContextVar(
    "orch_lock_holder_tool", default=None)


def enabled() -> bool:
    """True only when ORCH_LOCK_HOLDER_TRACE was ON at import time."""
    return _ENABLED


def set_current_tool(name):
    """Remember the tool running on this task. No-op when the flag is OFF."""
    if not _ENABLED:
        return None
    return _current_tool.set(name or None)


def reset_current_tool(token) -> None:
    if token is not None:
        _current_tool.reset(token)


def current_tool():
    return _current_tool.get()


def _normpath(p: str) -> str:
    return os.path.normcase(os.path.abspath(p))


def _target_paths() -> set:
    """Configured sessions.db / durable_audit.db locations (cheap; no I/O)."""
    out = set()
    for key in ("ORCHESTRATOR_SESSIONS_DB", "ORCHESTRATOR_DURABLE_AUDIT_DB"):
        v = (os.environ.get(key) or "").strip()
        if v:
            out.add(_normpath(v))
    return out


def _should_wrap(database) -> bool:
    if not _ENABLED or database is None:
        return False
    s = str(database)
    if s == ":memory:" or s.startswith("file:"):
        return False
    if os.path.basename(s).lower() not in _TARGET_BASENAMES:
        return False  # cheap prefilter: most connects never get past here
    norm = _normpath(s)
    if norm in _target_paths():
        return True
    # Fallback for the default layout (<repo>/.orchestrator_cache/*.db)
    # when the env overrides are not set.
    parent = os.path.basename(os.path.dirname(norm)).lower()
    return parent == ".orchestrator_cache"


def _capture_stack(limit: int = 30):
    frames = [f for f in traceback.extract_stack() if f.filename != __file__]
    return traceback.format_list(frames[-limit:])


class _Record:
    __slots__ = ("ref", "db_path", "thread_name", "open_tool", "opened_at",
                 "open_stack", "alerted_2s", "alerted_30s")

    def __init__(self, ref, db_path):
        self.ref = ref
        self.db_path = db_path
        self.thread_name = None
        self.open_tool = None
        self.opened_at = None
        self.open_stack = None
        self.alerted_2s = False
        self.alerted_30s = False


_REG = {}
_REG_LOCK = threading.Lock()


def _unregister(key) -> None:
    try:
        with _REG_LOCK:
            _REG.pop(key, None)
    except Exception:
        pass


class _TracedConnection:
    """Transparent proxy around a real sqlite3.Connection.

    Behaviour contract: every attribute read not explicitly overridden
    delegates to the wrapped connection, and every attribute WRITE is
    forwarded to it (so ``conn.row_factory = ...`` / ``conn.isolation_level
    = ...`` keep working). isolation_level, timeout and commit/rollback
    semantics are untouched -- the wrapper only OBSERVES in_transaction
    transitions.
    """

    __slots__ = ("_conn", "_db_path", "__weakref__")

    def __init__(self, conn, db_path: str):
        # Bypass __setattr__ (which forwards to the real connection).
        object.__setattr__(self, "_conn", conn)
        object.__setattr__(self, "_db_path", db_path)
        key = id(self)
        with _REG_LOCK:
            _REG[key] = _Record(
                weakref.ref(self, lambda _r, k=key: _unregister(k)), db_path)

    # -- transparent delegation -------------------------------------------
    def __getattr__(self, name):
        if name in ("_conn", "_db_path"):
            raise AttributeError(name)
        return getattr(self._conn, name)

    def __setattr__(self, name, value):
        if name in ("_conn", "_db_path"):
            object.__setattr__(self, name, value)
        else:
            setattr(self._conn, name, value)

    @property
    def in_transaction(self) -> bool:
        return self._conn.in_transaction

    def _real_in_transaction(self) -> bool:
        try:
            return bool(self._conn.in_transaction)
        except Exception:
            return False

    def _reconcile(self) -> None:
        """Sync the open-transaction record with reality. Called after each
        statement from the owning thread (the only place a stack capture is
        meaningful)."""
        with _REG_LOCK:
            rec = _REG.get(id(self))
            need_open = rec is not None and rec.opened_at is None
        in_txn = self._real_in_transaction()
        stack = _capture_stack() if (in_txn and need_open) else None
        with _REG_LOCK:
            rec = _REG.get(id(self))
            if rec is None:
                return
            if in_txn:
                if rec.opened_at is None:
                    rec.opened_at = time.monotonic()
                    rec.thread_name = threading.current_thread().name
                    rec.open_tool = _current_tool.get()
                    rec.open_stack = stack
                    rec.alerted_2s = False
                    rec.alerted_30s = False
            else:
                rec.opened_at = None
                rec.open_stack = None
                rec.thread_name = None
                rec.open_tool = None
                rec.alerted_2s = False
                rec.alerted_30s = False

    # -- tracked entry points ----------------------------------------------
    def execute(self, *args, **kwargs):
        cur = self._conn.execute(*args, **kwargs)
        self._reconcile()
        return _TracedCursor(cur, self)

    def executemany(self, *args, **kwargs):
        cur = self._conn.executemany(*args, **kwargs)
        self._reconcile()
        return _TracedCursor(cur, self)

    def executescript(self, *args, **kwargs):
        cur = self._conn.executescript(*args, **kwargs)
        self._reconcile()
        return _TracedCursor(cur, self)

    def cursor(self, *args, **kwargs):
        if args or kwargs.get("factory") is not None:
            # Custom cursor factory: return it unwrapped (rare path).
            return self._conn.cursor(*args, **kwargs)
        return _TracedCursor(self._conn.cursor(), self)

    def commit(self, *args, **kwargs):
        try:
            return self._conn.commit(*args, **kwargs)
        finally:
            self._reconcile()

    def rollback(self, *args, **kwargs):
        try:
            return self._conn.rollback(*args, **kwargs)
        finally:
            self._reconcile()

    def close(self, *args, **kwargs):
        try:
            return self._conn.close(*args, **kwargs)
        finally:
            _unregister(id(self))

    # -- context-manager protocol (dunder lookup bypasses __getattr__) ------
    def __enter__(self):
        self._conn.__enter__()
        return self

    def __exit__(self, exc_type, exc, tb):
        try:
            return self._conn.__exit__(exc_type, exc, tb)
        finally:
            self._reconcile()


class _TracedCursor:
    """Proxy around a real sqlite3.Cursor; statements route through the
    owning connection's reconciler so transaction opens are observed."""

    __slots__ = ("_cur", "_owner", "__weakref__")

    def __init__(self, cur, owner):
        object.__setattr__(self, "_cur", cur)
        object.__setattr__(self, "_owner", owner)

    def __getattr__(self, name):
        if name in ("_cur", "_owner"):
            raise AttributeError(name)
        return getattr(self._cur, name)

    def __setattr__(self, name, value):
        if name in ("_cur", "_owner"):
            object.__setattr__(self, name, value)
        else:
            setattr(self._cur, name, value)

    @property
    def connection(self):
        # Keep the illusion: cursor.connection is the (wrapped) connection.
        return self._owner

    def __iter__(self):
        return iter(self._cur)

    def execute(self, *args, **kwargs):
        r = self._cur.execute(*args, **kwargs)
        self._owner._reconcile()
        return r

    def executemany(self, *args, **kwargs):
        r = self._cur.executemany(*args, **kwargs)
        self._owner._reconcile()
        return r


def traced_connect(database=None, *args, **kwargs):
    """Step 0.6 factory: drop-in replacement for sqlite3.connect.

    Wraps ONLY connections to sessions.db / durable_audit.db. Everything
    else (other files, :memory:, file: URIs) goes to the stock connector
    with no wrapper at all.
    """
    if database is None:
        database = kwargs.get("database")
    if _should_wrap(database):
        real = _real_connect(database, *args, **kwargs)
        return _TracedConnection(real, _normpath(str(database)))
    return _real_connect(database, *args, **kwargs)


_INSTALL_LOCK = threading.Lock()
_installed = False


def install() -> bool:
    """Patch sqlite3.connect process-wide. No-op when the flag is OFF.
    Idempotent."""
    global _installed
    if not _ENABLED:
        return False
    with _INSTALL_LOCK:
        if _installed:
            return True
        sqlite3.connect = traced_connect
        _installed = True
    logger.info(
        "LOCK_HOLDER_TRACE enabled: sqlite3.connect wrapped for "
        "sessions.db/durable_audit.db (watchdog tick %ss, alert >%ss, "
        "layer-2 cue >%ss)",
        _TICK_S, _ALERT_AFTER_S, _LAYER2_AFTER_S,
    )
    return True


_WATCHDOG_LOCK = threading.Lock()
_watchdog_started = False


def start_watchdog() -> bool:
    """Start the 1 s watchdog thread. No-op when the flag is OFF. Idempotent."""
    global _watchdog_started
    if not _ENABLED:
        return False
    with _WATCHDOG_LOCK:
        if _watchdog_started:
            return True
        t = threading.Thread(
            target=_watchdog_loop, name="lock-holder-watchdog", daemon=True)
        t.start()
        _watchdog_started = True
    logger.info("LOCK_HOLDER_TRACE watchdog started (tick %ss)", _TICK_S)
    return True


def _watchdog_loop() -> None:
    while True:
        time.sleep(_TICK_S)
        try:
            _watchdog_tick()
        except Exception:
            logger.exception("LOCK_HOLDER_TRACE watchdog tick failed")


def _watchdog_tick() -> None:
    now = time.monotonic()
    with _REG_LOCK:
        items = list(_REG.items())
    pending_logs = []
    for key, rec in items:
        wrapper = rec.ref()
        if wrapper is None:
            _unregister(key)
            continue
        try:
            in_txn = wrapper._real_in_transaction()
        except Exception:
            continue
        with _REG_LOCK:
            if _REG.get(key) is not rec:
                continue
            if in_txn:
                if rec.opened_at is None:
                    # Transaction began outside the wrapper's tracked calls
                    # (defensive; should be rare). No open-stack available.
                    rec.opened_at = now
                age = now - rec.opened_at
                if age > _ALERT_AFTER_S and not rec.alerted_2s:
                    rec.alerted_2s = True
                    pending_logs.append((_log_holder, rec, age))
                elif age > _LAYER2_AFTER_S and not rec.alerted_30s:
                    rec.alerted_30s = True
                    pending_logs.append((_log_layer2_cue, rec, age))
            else:
                rec.opened_at = None
                rec.open_stack = None
                rec.thread_name = None
                rec.open_tool = None
                rec.alerted_2s = False
                rec.alerted_30s = False
    for fn, rec, age in pending_logs:
        try:
            fn(rec, age)
        except Exception:
            logger.exception("LOCK_HOLDER_TRACE alert logging failed")


def _log_holder(rec, age: float) -> None:
    stack_txt = "".join(rec.open_stack) if rec.open_stack else "<stack unavailable>"
    logger.warning(
        "LOCK_HOLDER_TRACE txn_open>%.0fs db=%s thread=%s tool=%s age=%.1fs\n"
        "open_stack (most recent call last):\n%s",
        _ALERT_AFTER_S, rec.db_path, rec.thread_name, rec.open_tool, age,
        stack_txt,
    )


def _log_layer2_cue(rec, age: float) -> None:
    logger.error(
        "LOCK_HOLDER_TRACE lock_hold>%.0fs db=%s thread=%s tool=%s age=%.1fs -- "
        "run the layer-2 script (lock_holder_layer2.ps1 via run_ps1_script) "
        "to list external holders",
        _LAYER2_AFTER_S, rec.db_path, rec.thread_name, rec.open_tool, age,
    )
