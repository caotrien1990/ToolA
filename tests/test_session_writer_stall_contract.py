"""F-store / F7-partial / F25: what a stalled SessionWriter does and says, pinned.

WHY this file exists
--------------------
The 2026-09-27 triage of 48h of logs counted 24 ``SessionWriter STALLED`` lines
and one wedge ``FAILED`` line, and proposed spooling the fire-and-forget writes a
stall drops. Measuring first refuted that: the drop branch logged 0 times, no
production caller submits fire-and-forget at all, and its payload is a
``write_fn`` closure that no spool could replay. So no spool was built; this file
pins the facts that decision rests on instead:

  * no production call site submits fire-and-forget. If one is ever added, the
    drop branch starts losing real work, and this is the test that says so;
  * the two log lines an operator reads, per call site: level, exact format
    string, rendered numbers;
  * one STALLED line per EPISODE, not per watchdog poll -- the only reason a log
    count can be read as an episode count;
  * the live thresholds, 5 s to STALLED and x6 = 30 s to a wedge FAILED: the
    ``5.00s`` and ``(> 30.00s)`` the log shows. They decide when a new durable
    call is refused, and no test held either default.

Reading the watchdog to write those pins found F25: it judges a batch from an
unlocked read of ``_active_batch_since``, so the batch can commit -- and
``_clear_stalled`` run -- before the verdict latches. Nothing clears a verdict
latched that late: a stalled or wedged writer admits no durable submit, and with
no fire-and-forget caller no batch ever runs again, so every durable session
write fails until the process restarts. The two ``_Preempted*`` tests replay that
interleaving deterministically.
"""
import ast
import json
import logging
import os
import re
import subprocess
import sys
import threading
import time
from pathlib import Path

from orchestrator.session import store

ROOT = Path(store.__file__).resolve().parents[2]
STORE_LOGGER = store.logger.name
STALLED_FORMAT = "SessionWriter STALLED: %s"
FAILED_FORMAT = "SessionWriter FAILED: %s"


def _wait_until(predicate, timeout: float = 5.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(0.01)
    raise AssertionError("condition did not become true before timeout")


def _writer(tmp_path: Path, cls=store.SessionWriter, **kwargs):
    return cls(tmp_path / "stall.db", batch_window_ms=1, watchdog_interval=0.01, **kwargs)


def _hold_batch(writer) -> threading.Event:
    """Park the writer inside one batch until the returned event is set."""
    entered, release = threading.Event(), threading.Event()

    def _blocker(_conn):
        entered.set()
        release.wait(timeout=10)

    writer.submit("hold", _blocker, durable=False)
    assert entered.wait(timeout=5)
    return release


def _records(caplog, prefix: str):
    return [r for r in caplog.records
            if r.name == STORE_LOGGER and r.getMessage().startswith(prefix)]


# --- no production caller submits fire-and-forget ---------------------------

# _log_session_event takes ``durable`` as its 9th positional parameter; the two
# other sinks make it keyword-only, so only this one can be flipped positionally.
_POSITIONAL_DURABLE = {"_log_session_event": 8}
_KWARG_SINKS = {"_log_session_event", "_run_session_write"}


def _fire_and_forget_sites(source: str, filename: str = "<src>") -> list[str]:
    """Every place that can make a session write fire-and-forget.

    Allowed: ``durable=True``, or passing the caller's own ``durable`` parameter
    through -- safe only because every ``durable`` parameter must itself default
    to True (or have no default), which the same walk enforces.
    """
    found = []
    for node in ast.walk(ast.parse(source, filename)):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            a = node.args
            positional = a.posonlyargs + a.args
            pairs = list(zip(positional[len(positional) - len(a.defaults):], a.defaults))
            pairs += [(arg, d) for arg, d in zip(a.kwonlyargs, a.kw_defaults) if d is not None]
            for arg, default in pairs:
                if arg.arg == "durable" and not (
                        isinstance(default, ast.Constant) and default.value is True):
                    found.append(f"{filename}:{node.lineno} def {node.name}(durable=...)")
        if not isinstance(node, ast.Call):
            continue
        name = getattr(node.func, "attr", None) or getattr(node.func, "id", None) or ""
        for kw in node.keywords:
            if kw.arg is None and name in _KWARG_SINKS:
                found.append(f"{filename}:{node.lineno} {name}(**...)")
            if kw.arg != "durable":
                continue
            value = kw.value
            if (isinstance(value, ast.Constant) and value.value is True) or (
                    isinstance(value, ast.Name) and value.id == "durable"):
                continue
            found.append(f"{filename}:{node.lineno} durable={ast.unparse(value)}")
        limit = _POSITIONAL_DURABLE.get(name)
        if limit is not None and len(node.args) > limit:
            found.append(f"{filename}:{node.lineno} {name}(positional durable)")
    return found


def _production_files() -> list[Path]:
    files = [p for p in ROOT.glob("*.py")
             if not p.name.startswith(("_", "test_")) and p.name != "conftest.py"]
    for sub in ("orchestrator", "scripts"):
        files.extend((ROOT / sub).rglob("*.py"))
    return sorted(files)


def test_the_scanner_sees_every_fire_and_forget_shape():
    """Positive control: a scan that finds nothing proves nothing until it is
    shown to find each shape it exists for."""
    bad = (
        "_run_session_write('op', fn, durable=False)\n"
        "writer.submit('op', fn, durable=flag)\n"
        "_log_session_event(1, 2, 3, 4, 5, 6, 7, 8, False)\n"
        "_run_session_write('op', fn, **opts)\n"
        "def f(durable=False):\n    pass\n"
        "def g(*, durable=None):\n    pass\n"
    )
    assert len(_fire_and_forget_sites(bad)) == 6
    good = (
        "_run_session_write('op', fn)\n"
        "_run_session_write('op', fn, durable=True)\n"
        "def g(x, *, durable: bool):\n    return _run_session_write('op', x, durable=durable)\n"
        "def h(durable=True):\n    pass\n"
        "_log_session_event(1, 2, 3, 4, 5, 6, 7, audit_id='a')\n"
        "executor.submit(fn, **kwargs)\n"
    )
    assert _fire_and_forget_sites(good) == []


def test_no_production_caller_submits_fire_and_forget():
    files = _production_files()
    names = {p.name for p in files}
    assert {"store.py", "orchestrator_server.py", "code_tools.py"} <= names
    found = []
    for path in files:
        text = path.read_text(encoding="utf-8", errors="replace")
        if not any(k in text for k in ("durable", "_run_session_write", "_log_session_event")):
            continue
        found += _fire_and_forget_sites(text, str(path.relative_to(ROOT)))
    assert found == [], (
        "a fire-and-forget session write is DROPPED while the writer is stalled, "
        "wedged or its queue is full, and its write_fn closure cannot be spooled; "
        f"make it durable or give it a replayable payload first: {found}")


# --- the thresholds and the two log lines -----------------------------------

def test_the_live_thresholds_are_five_seconds_and_a_thirty_second_wedge(tmp_path):
    """Read in a clean interpreter: both knobs are frozen at import from env."""
    env = {k: v for k, v in os.environ.items()
           if not k.startswith("ORCH_SESSION_WRITE_")
           and k not in ("PYTHONHOME", "PYTHONPATH", "PYTHONSTARTUP", "PYTHONUSERBASE")}
    env["ORCHESTRATOR_SESSIONS_DB"] = str(tmp_path / "defaults.db")
    probe = (
        "import json\n"
        "from orchestrator.session import store as s\n"
        "w = s.SessionWriter(s._SESSIONS_DB, auto_start=False)\n"
        "print(json.dumps([s._SESSION_WRITE_STALL_SECONDS,"
        " s._SESSION_WRITE_WEDGE_MULTIPLIER, w.stall_seconds, w.wedge_seconds]))\n"
    )
    proc = subprocess.run([sys.executable, "-c", probe], cwd=ROOT, env=env,
                          capture_output=True, text=True, timeout=120)
    assert proc.returncode == 0, proc.stderr[-2000:]
    assert json.loads(proc.stdout.strip().splitlines()[-1]) == [5.0, 6.0, 5.0, 30.0]


def test_a_stall_logs_one_warning_per_episode_in_the_pinned_format(tmp_path, caplog):
    caplog.set_level(logging.WARNING, logger=STORE_LOGGER)
    writer = _writer(tmp_path, stall_seconds=0.1, wedge_multiplier=100)
    release = threading.Event()
    try:
        for _episode in range(2):
            release = _hold_batch(writer)
            _wait_until(lambda: writer.metrics()["stalled"])
            time.sleep(0.2)  # ~20 more watchdog polls inside the same episode
            release.set()
            _wait_until(lambda: not writer.metrics()["stalled"])
    finally:
        release.set()
        writer.close()
    stalled = _records(caplog, "SessionWriter STALLED")
    assert len(stalled) == 2, "one line per episode, not one per watchdog poll"
    for record in stalled:
        assert record.levelno == logging.WARNING
        assert record.msg == STALLED_FORMAT
        assert record.getMessage() == (
            "SessionWriter STALLED: writer batch made no observable progress "
            "for more than 0.10s")


def test_a_wedge_logs_one_error_in_the_pinned_format(tmp_path, caplog):
    caplog.set_level(logging.WARNING, logger=STORE_LOGGER)
    writer = _writer(tmp_path, stall_seconds=0.1, wedge_multiplier=2)
    release = threading.Event()
    try:
        release = _hold_batch(writer)
        _wait_until(lambda: writer.failed)
        time.sleep(0.1)
        release.set()
        _wait_until(lambda: not writer.failed)  # a later commit retracts a wedge
    finally:
        release.set()
        writer.close()
    failed = _records(caplog, "SessionWriter FAILED")
    assert len(failed) == 1
    assert failed[0].levelno == logging.ERROR
    assert failed[0].msg == FAILED_FORMAT
    assert re.fullmatch(
        r"SessionWriter FAILED: writer batch made no observable progress for "
        r"\d+\.\d{2}s \(> 0\.20s\); treating the writer as wedged",
        failed[0].getMessage())


# --- F25: a verdict about a batch that already committed ---------------------

class _Preempted(store.SessionWriter):
    """Signals the moment a verdict becomes late: the drain loop's finally calls
    _clear_stalled once the batch has committed and _active_batch_since is zeroed.

    ``_idle`` is not that signal: the loop's empty-queue branch tests ``empty()``
    and then sets ``_idle``, so it can re-set the event submit() just cleared
    while the held batch is still queued.
    """

    def __init__(self, *args, **kwargs):
        self.judged, self.resume, self.latched, self.cleared = (
            threading.Event(), threading.Event(), threading.Event(), threading.Event())
        super().__init__(*args, **kwargs)

    def _clear_stalled(self):
        super()._clear_stalled()
        self.cleared.set()


class _PreemptedStall(_Preempted):
    """Hold the watchdog between judging a batch and latching STALLED -- the
    window a GIL switch opens in production -- until the test resumes it."""

    def _mark_stalled(self, reason, **kwargs):
        self.judged.set()
        self.resume.wait(timeout=10)
        super()._mark_stalled(reason, **kwargs)
        self.latched.set()


class _PreemptedWedge(_Preempted):
    """Same window for the wedge verdict; the stall verdict passes straight through."""

    def _mark_failed(self, reason, **kwargs):
        if kwargs.get("wedge"):
            self.judged.set()
            self.resume.wait(timeout=10)
        super()._mark_failed(reason, **kwargs)
        if kwargs.get("wedge"):
            self.latched.set()


def _replay_late_verdict(writer):
    release = _hold_batch(writer)
    try:
        assert writer.judged.wait(timeout=5), "the watchdog never judged the batch"
        release.set()
        assert writer.cleared.wait(timeout=5), "the held batch never finished"
    finally:
        release.set()
        writer.resume.set()
    assert writer.latched.wait(timeout=5)


def test_a_stall_verdict_that_lands_after_the_commit_does_not_latch(tmp_path):
    writer = _writer(tmp_path, cls=_PreemptedStall, stall_seconds=0.1, wedge_multiplier=100)
    try:
        _replay_late_verdict(writer)
        assert not writer.metrics()["stalled"]
        assert writer.submit("after", lambda _conn: 7, durable=True) == 7
    finally:
        writer.resume.set()
        writer.close()


def test_a_wedge_verdict_that_lands_after_the_commit_does_not_latch(tmp_path):
    writer = _writer(tmp_path, cls=_PreemptedWedge, stall_seconds=0.1, wedge_multiplier=2)
    try:
        _replay_late_verdict(writer)
        assert not writer.failed
        assert writer.submit("after", lambda _conn: 7, durable=True) == 7
    finally:
        writer.resume.set()
        writer.close()
