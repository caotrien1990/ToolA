"""R5 verification: RAM-recorded SQL trace + SLOW_WRITE_FN warning.

Verifies the camera blind-spot fix from PLAN TOOLA_1510_RECOVERY R5,
as redesigned by ClauLTN B1 (CHANGES-REQUESTED):
1. 1000 fast statements produce ZERO log lines (RAM record only).
2. ONE slow statement (>2s) produces EXACTLY ONE warning with thread +
   the first 120 chars of SQL (via the 1s watchdog).
3. Params are NEVER logged/recorded in a leakable way.
4. A batch write_fn running > 1s emits a SLOW_WRITE_FN WARNING with the
   operation name.
5. Flag OFF -> zero SLOW_STMT/SLOW_WRITE_FN lines, install() is a no-op.
"""

import logging
import sqlite3
import threading
import time

from orchestrator import lock_holder_trace
from orchestrator.session import store as session_store

SLOW_SQL = (
    "WITH RECURSIVE cnt(x) AS ("
    " SELECT 1 UNION ALL SELECT x + 1 FROM cnt WHERE x < 20000000"
    ") SELECT count(*) FROM cnt"
)


class _Capture(logging.Handler):
    def __init__(self):
        super().__init__()
        self.records = []

    def emit(self, record):
        self.records.append(record)

    def texts(self, levelno=None):
        out = []
        for r in self.records:
            if levelno is not None and r.levelno != levelno:
                continue
            out.append(r.getMessage())
        return out


import pytest


@pytest.fixture()
def capture():
    h = _Capture()
    root = logging.getLogger()
    old_level = root.level
    root.setLevel(logging.DEBUG)
    root.addHandler(h)
    try:
        yield h
    finally:
        root.removeHandler(h)
        root.setLevel(old_level)


@pytest.fixture()
def trace_enabled():
    prev = lock_holder_trace._ENABLED
    lock_holder_trace._ENABLED = True
    try:
        yield
    finally:
        lock_holder_trace._ENABLED = prev


@pytest.fixture()
def installed(trace_enabled):
    # install() is idempotent via module-global _installed, but teardown
    # restores the original sqlite3.connect -- without resetting the flag,
    # the 2nd+ test using this fixture would silently get the UNPATCHED
    # connect. Reset both ways for true test isolation.
    lock_holder_trace._installed = False
    orig_connect = sqlite3.connect
    assert lock_holder_trace.install() is True
    try:
        yield
    finally:
        sqlite3.connect = orig_connect
        lock_holder_trace._installed = False


@pytest.fixture(autouse=True)
def _clean_trace_state():
    """Per-test isolation for lock_holder_trace module globals.

    - Reset _watchdog_started so a test needing the watchdog starts from a
      known state. A lingering daemon thread from an earlier test is harmless:
      the stmt_alerted flag is set atomically under _REG_LOCK, so it can
      never double-log for the same statement.
    - Clear _REG so no stale connection records leak between tests.
    """
    lock_holder_trace._watchdog_started = False
    with lock_holder_trace._REG_LOCK:
        lock_holder_trace._REG.clear()
    try:
        yield
    finally:
        with lock_holder_trace._REG_LOCK:
            lock_holder_trace._REG.clear()
@pytest.fixture()
def traced_db(tmp_path):
    d = tmp_path / ".orchestrator_cache"
    d.mkdir(exist_ok=True)
    return str(d / "sessions.db")



def test_1000_fast_sql_zero_logs(capture, installed, traced_db):
    # B1 (ClauLTN): 1000 fast statements must produce ZERO log lines.
    # Records live in RAM only; the 1s watchdog only fires for >2s items.
    conn = sqlite3.connect(traced_db)
    try:
        conn.execute("CREATE TABLE b1 (v INTEGER)")
        for i in range(1000):
            conn.execute("INSERT INTO b1 VALUES (?)", (i,))
        conn.commit()
        n = conn.execute("SELECT count(*) FROM b1").fetchone()[0]
    finally:
        conn.close()
    assert n == 1000
    # Give the 1s watchdog a chance to tick mid-run and after.
    time.sleep(2.5)
    for text in capture.texts():
        assert "slow_stmt" not in text, "slow_stmt log for fast SQL: %s" % text[:200]
        assert "SQL_TRACE" not in text, "SQL_TRACE log for fast SQL: %s" % text[:200]


def test_1_slow_sql_one_log(capture, installed, traced_db):
    # B1 (ClauLTN): ONE slow statement (>2s) must produce EXACTLY ONE warning
    # with thread + SQL, via the 1s watchdog (no per-statement INFO anymore).
    lock_holder_trace.start_watchdog()
    t0 = time.monotonic()
    done = threading.Event()

    def run():
        threading.current_thread().name = "r5-slow-thread"
        conn = sqlite3.connect(traced_db)
        try:
            # Deterministic 5s statement via a sleep UDF (watchdog: 1s tick,
            # 2s threshold -> firing ticks at ~3s and ~4s, robust against
            # single-tick jitter; stmt_alerted guarantees exactly one log).
            conn.create_function("b1_sleep_s", 1, lambda s: (time.sleep(s), s)[1])
            conn.execute("SELECT b1_sleep_s(5)").fetchone()
        finally:
            conn.close()
            done.set()

    th = threading.Thread(target=run, name="r5-slow-thread")
    th.start()
    slow_texts = []
    deadline = t0 + 30.0
    while time.monotonic() < deadline:
        slow_texts = [t for t in capture.texts(levelno=logging.WARNING)
                      if "slow_stmt" in t and "r5-slow-thread" in t]
        if slow_texts:
            break
        time.sleep(0.2)
    th.join(timeout=60)
    assert done.is_set(), "slow statement thread did not finish"
    assert slow_texts, "no slow_stmt warning for a 5s statement"
    # Exactly one warning per slow statement (stmt_alerted flag); a later
    # watchdog tick must NOT log it again.
    time.sleep(2.0)
    slow_after = [t for t in capture.texts(levelno=logging.WARNING)
                  if "slow_stmt" in t and "r5-slow-thread" in t]
    assert len(slow_after) == len(slow_texts), (
        "slow statement logged more than once: %d -> %d"
        % (len(slow_texts), len(slow_after))
    )
    text = slow_texts[0]
    assert "thread=r5-slow-thread" in text
    assert "op=execute" in text
    assert "b1_sleep_s(5)" in text, "SQL missing from slow_stmt log"


def test_sql_params_never_logged(capture, installed, traced_db):
    secret = "r5_secret_param_9f8e7d6c"
    conn = sqlite3.connect(traced_db)
    try:
        conn.execute("CREATE TABLE t (v TEXT)")
        conn.execute("INSERT INTO t VALUES (?)", (secret,))
        conn.commit()
    finally:
        conn.close()
    for text in capture.texts():
        assert secret not in text, "param leaked into log: %s" % text[:200]


def test_slow_write_fn_warns_with_operation(capture, trace_enabled, tmp_path):
    db = tmp_path / "sessions_r5.db"
    writer = session_store.SessionWriter(str(db), auto_start=True)
    try:
        def slow_fn(conn):
            time.sleep(2.0)

        writer.submit("r5_probe_op", slow_fn, durable=False)
        deadline = time.monotonic() + 15.0
        found = None
        while time.monotonic() < deadline:
            for text in capture.texts(levelno=logging.WARNING):
                if text.startswith("SLOW_WRITE_FN") and "r5_probe_op" in text:
                    found = text
                    break
            if found:
                break
            time.sleep(0.1)
        assert found is not None, "no SLOW_WRITE_FN warning for a 2s write_fn"
        assert "op=r5_probe_op" in found
    finally:
        writer.close(timeout=10.0)


def test_flag_off_zero_logs(capture, tmp_path):
    lock_holder_trace._ENABLED = False
    assert lock_holder_trace.install() is False
    conn = sqlite3.connect(":memory:")
    try:
        conn.execute("SELECT 1").fetchone()
    finally:
        conn.close()
    db = tmp_path / "sessions_r5_off.db"
    writer = session_store.SessionWriter(str(db), auto_start=True)
    try:
        writer.submit("r5_fast_op", lambda conn: None, durable=False)
        time.sleep(1.0)
    finally:
        writer.close(timeout=10.0)
    for text in capture.texts():
        assert not text.startswith("SQL_TRACE"), "SQL_TRACE with flag OFF: %s" % text[:160]
        assert not text.startswith("SLOW_WRITE_FN"), "SLOW_WRITE_FN with flag OFF: %s" % text[:160]