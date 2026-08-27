"""Independent authenticated loopback restart controller."""

from __future__ import annotations

import threading
import argparse
import json
import os
import subprocess
import sys
import time
import urllib.request
import psutil
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from dataclasses import dataclass
from typing import Callable, Mapping, Protocol

from .restart_guard import GuardDecision, evaluate_restart_guard
from .restart_protocol import (
    RestartProtocolError,
    RestartRequest,
    SlidingWindowRateLimiter,
    canonical_secret_path,
    load_control_secret,
    migrate_legacy_secret,
    validate_restart_request,
)
from .console_warp import child_creationflags, child_startupinfo, ensure_hidden_console


class Scheduler(Protocol):
    def end(self) -> bool: ...
    def run(self) -> bool: ...


@dataclass(frozen=True)
class ProcessIdentity:
    pid: int
    generation: str
    created_at: float = 0.0
    executable: str = ""


class RestartController:
    def __init__(
        self,
        *,
        scheduler: Scheduler,
        guard: Callable[[RestartRequest], GuardDecision],
        current_identity: Callable[[], ProcessIdentity],
        prove_stopped: Callable[[ProcessIdentity], bool],
        prove_started: Callable[[ProcessIdentity], ProcessIdentity | None],
        append_audit: Callable[[Mapping[str, object]], None],
    ) -> None:
        self._scheduler = scheduler
        self._guard = guard
        self._current_identity = current_identity
        self._prove_stopped = prove_stopped
        self._prove_started = prove_started
        self._append_audit = append_audit
        self._results: dict[tuple[str, str], dict[str, object]] = {}
        self._request_generations: dict[str, str] = {}
        self._lock = threading.Lock()
        self._active_request_id: str | None = None

    def status(self, request_id: str) -> dict[str, object] | None:
        with self._lock:
            generation = self._request_generations.get(request_id)
            if generation is None:
                return None
            result = self._results.get((request_id, generation))
            if result is None:
                return {"ok": True, "status": "running", "request_id": request_id,
                        "expected_generation": generation, "terminal": False}
            return {**result, "status": "terminal", "terminal": True}

    def accept(self, request: RestartRequest) -> dict[str, object]:
        key = (request.request_id, request.expected_generation)
        with self._lock:
            prior_generation = self._request_generations.get(request.request_id)
            if prior_generation is not None and prior_generation != request.expected_generation:
                return {"ok": False, "error_name": "REQUEST_ID_CONFLICT", "terminal": True}
            if prior_generation is not None:
                result = self._results.get(key)
                return ({**result, "status": "terminal", "terminal": True, "replayed": True}
                        if result else {"ok": True, "status": "running", "terminal": False,
                                        "request_id": request.request_id,
                                        "expected_generation": request.expected_generation,
                                         "replayed": True})
            if self._active_request_id is not None:
                return {
                    "ok": False,
                    "error_name": "RESTART_ALREADY_PENDING",
                    "active_request_id": self._active_request_id,
                    "terminal": True,
                }
            self._request_generations[request.request_id] = request.expected_generation
            self._active_request_id = request.request_id
        try:
            threading.Thread(target=self._execute_accepted, args=(request,),
                             name=f"restart-{request.request_id[:16]}", daemon=False).start()
        except Exception as exc:
            result: dict[str, object] = {
                "ok": False,
                "error_name": "CONTROLLER_INTERNAL_ERROR",
                "detail": type(exc).__name__,
                "request_id": request.request_id,
                "expected_generation": request.expected_generation,
            }
            try:
                self._append_audit(dict(result))
            except Exception:
                pass
            with self._lock:
                self._results[key] = result
                if self._active_request_id == request.request_id:
                    self._active_request_id = None
            return {**result, "status": "terminal", "terminal": True}
        return {"ok": True, "status": "accepted", "terminal": False,
                "request_id": request.request_id,
                "expected_generation": request.expected_generation}

    def _execute_accepted(self, request: RestartRequest) -> None:
        result: dict[str, object] | None = None
        try:
            result = self._execute_once(request)
        except Exception as exc:
            result = {
                "ok": False,
                "error_name": "CONTROLLER_INTERNAL_ERROR",
                "detail": type(exc).__name__,
                "request_id": request.request_id,
                "expected_generation": request.expected_generation,
            }
        finally:
            if result is None:
                result = {
                    "ok": False, "error_name": "CONTROLLER_INTERNAL_ERROR",
                    "detail": "MissingTerminalResult", "request_id": request.request_id,
                    "expected_generation": request.expected_generation,
                }
            with self._lock:
                self._results[(request.request_id, request.expected_generation)] = result
                if self._active_request_id == request.request_id:
                    self._active_request_id = None

    def execute(self, request: RestartRequest) -> dict[str, object]:
        key = (request.request_id, request.expected_generation)
        with self._lock:
            prior_generation = self._request_generations.get(request.request_id)
            if prior_generation is not None and prior_generation != request.expected_generation:
                return {"ok": False, "error_name": "REQUEST_ID_CONFLICT"}
            if key in self._results:
                return {**self._results[key], "replayed": True}
            self._request_generations[request.request_id] = request.expected_generation
            result = self._execute_once(request)
            self._results[key] = result
            return dict(result)

    def _execute_once(self, request: RestartRequest) -> dict[str, object]:
        old = self._current_identity()
        if old.generation != request.expected_generation:
            return self._finish(request, old, False, "GENERATION_MISMATCH")
        decision = self._guard(request)
        if not decision.allowed:
            return self._finish(
                request, old, False, "RESTART_GUARD_BLOCKED",
                reasons=list(decision.reasons),
                blocking_guards=list(decision.blocking_guards),
            )
        try:
            if not self._scheduler.end():
                # schtasks /end signalled the task but the old server survived (its
                # `start /wait` pythonw child outlives the task's terminated main process),
                # so it still owns :8083 and the next /run cannot bind -> the recurring
                # "restart kẹt" / orphan. The controller runs High-IL: force-kill the
                # proven-old PID (cross-elevation OK) and re-verify before giving up.
                self._scheduler.force_kill(old.pid)
                if not self._prove_stopped(old):
                    return self._finish(request, old, False, "SCHEDULER_END_FAILED")
            if not self._prove_stopped(old):
                return self._finish(request, old, False, "OLD_PROCESS_NOT_STOPPED")
            if not self._scheduler.run():
                return self._finish(request, old, False, "SCHEDULER_RUN_FAILED")
            new = self._prove_started(old)
            if new is None or new.pid == old.pid or new.generation == old.generation:
                return self._finish(request, old, False, "NEW_PROCESS_NOT_PROVEN")
            return self._finish(
                request,
                old,
                True,
                "RESTART_VERIFIED",
                new_pid=new.pid,
                new_generation=new.generation,
            )
        except Exception as exc:
            return self._finish(request, old, False, "RESTART_TRANSACTION_FAILED", detail=type(exc).__name__)

    def _finish(
        self,
        request: RestartRequest,
        old: ProcessIdentity,
        ok: bool,
        error_name: str,
        **extra: object,
    ) -> dict[str, object]:
        result: dict[str, object] = {
            "ok": ok,
            "error_name": error_name,
            "request_id": request.request_id,
            "old_pid": old.pid,
            "old_generation": old.generation,
            **extra,
        }
        # Audit receives only transaction metadata; credentials never enter this API.
        self._append_audit(dict(result))
        return result


class TaskSchedulerAdapter:
    """Own both scheduler transitions; never starts a process directly."""

    def __init__(self, task_name: str = "ToolA_Orchestrator",
                 runner: Callable[..., object] = subprocess.run,
                 settled_stop: Callable[[], bool] | None = None) -> None:
        self.task_name = task_name
        self.runner = runner
        self.settled_stop = settled_stop

    def _call(self, action: str) -> bool:
        kwargs = {"capture_output": True, "text": True, "timeout": 20, "check": False}
        if os.name == "nt":
            kwargs["creationflags"] = child_creationflags()
            kwargs["startupinfo"] = child_startupinfo()
        try:
            result = self.runner(
                ["schtasks", action, "/tn", self.task_name],
                **kwargs,
            )
        except TypeError:
            kwargs.pop("creationflags", None)
            result = self.runner(
                ["schtasks", action, "/tn", self.task_name],
                **kwargs,
            )
        return getattr(result, "returncode", 1) == 0

    def end(self) -> bool:
        ended = self._call("/end")
        if self.settled_stop is not None:
            return bool(self.settled_stop())
        return ended

    def run(self) -> bool:
        return self._call("/run")

    def force_kill(self, pid: int) -> bool:
        """Last-resort High-IL kill of a survivor that `schtasks /end` did not stop: the
        server's `start /wait` pythonw child outlives the task's terminated main process
        (Windows does not cascade-kill children), so /end returns but the old instance keeps
        owning :8083. The controller runs High-IL, so taskkill /F /T succeeds cross-elevation."""
        try:
            pid = int(pid)
        except (TypeError, ValueError):
            return False
        if pid <= 0:
            return False
        kwargs = {"capture_output": True, "text": True, "timeout": 15, "check": False}
        if os.name == "nt":
            kwargs["creationflags"] = child_creationflags()
            kwargs["startupinfo"] = child_startupinfo()
        try:
            result = self.runner(
                ["taskkill", "/F", "/T", "/PID", str(pid)],
                **kwargs,
            )
        except TypeError:
            kwargs.pop("creationflags", None)
            result = self.runner(
                ["taskkill", "/F", "/T", "/PID", str(pid)],
                **kwargs,
            )
        return getattr(result, "returncode", 1) == 0


def _fetch_identity(url: str, timeout: float = 2.0) -> ProcessIdentity:
    with urllib.request.urlopen(url, timeout=timeout) as response:
        payload = json.loads(response.read().decode("utf-8"))
    if not payload.get("ok") or not payload.get("pid") or not payload.get("generation"):
        raise RuntimeError("invalid liveness identity")
    pid = int(payload["pid"])
    created_at, executable = _process_details(pid)
    return ProcessIdentity(pid, str(payload["generation"]), created_at, executable)


def _process_details(pid: int) -> tuple[float, str]:
    try:
        process = psutil.Process(int(pid))
        return float(process.create_time()), str(process.exe() or "").lower()
    except (psutil.Error, OSError, ValueError):
        pass
    script = (
        f"$p=Get-CimInstance Win32_Process -Filter \"ProcessId={int(pid)}\";"
        "if(-not $p){exit 3};"
        "$created=[Management.ManagementDateTimeConverter]::ToDateTime($p.CreationDate).ToUniversalTime();"
        "$unix=([DateTimeOffset]$created).ToUnixTimeMilliseconds()/1000.0;"
        "$o=[ordered]@{created=$unix;exe=$p.ExecutablePath};"
        "$o|ConvertTo-Json -Compress"
    )
    run_kwargs = {"capture_output": True, "text": True, "timeout": 5, "check": False}
    if os.name == "nt":
        run_kwargs["creationflags"] = child_creationflags()
        run_kwargs["startupinfo"] = child_startupinfo()
    result = subprocess.run(["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", script],
                            **run_kwargs)
    if result.returncode != 0 or not result.stdout.strip():
        raise RuntimeError("process identity unavailable")
    try:
        payload = json.loads(result.stdout)
    except (TypeError, ValueError, json.JSONDecodeError) as exc:
        raise RuntimeError("process identity output invalid") from exc
    if not isinstance(payload, dict) or payload.get("created") is None:
        raise RuntimeError("process identity output incomplete")
    return float(payload["created"]), str(payload.get("exe") or "").lower()


def _wait_stopped(old: ProcessIdentity, livez_url: str, timeout: float = 30.0) -> bool:
    deadline = time.monotonic() + timeout
    absent_samples = 0
    while time.monotonic() < deadline:
        try:
            created_at, executable = _process_details(old.pid)
            old_alive = created_at == old.created_at and executable == old.executable
        except Exception:
            old_alive = False
        try:
            current = _fetch_identity(livez_url, 1.0)
            absent_samples = 0
        except Exception:
            absent_samples += 1
            if absent_samples >= 2 and not old_alive:
                return True
            time.sleep(0.25)
            continue
        if current.pid != old.pid:
            return False
        time.sleep(0.25)
    return False


def _wait_started(old: ProcessIdentity, livez_url: str, health_url: str, timeout: float = 90.0) -> ProcessIdentity | None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            current = _fetch_identity(livez_url, 1.5)
            with urllib.request.urlopen(health_url, timeout=1.5) as response:
                health = json.loads(response.read().decode("utf-8"))
            if health.get("ok") and current.pid != old.pid and current.generation != old.generation:
                return current
        except Exception:
            pass
        time.sleep(0.5)
    return None


def _read_json(path: Path) -> object:
    return json.loads(path.read_text(encoding="utf-8"))


def _pid_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    try:
        result = subprocess.run(
            ["tasklist", "/FI", f"PID eq {pid}", "/FO", "CSV", "/NH"],
            capture_output=True, text=True, timeout=5, check=False,
            **({"creationflags": child_creationflags(), "startupinfo": child_startupinfo()} if os.name == "nt" else {}),
        )
        return result.returncode == 0 and str(pid) in result.stdout
    except OSError:
        return False


def _snapshot_captured_at(snap: object) -> float | None:
    if not isinstance(snap, Mapping):
        return None
    value = snap.get("captured_at")
    return float(value) if isinstance(value, (int, float)) else None


def _refresh_busy_snapshot(
    snapshot_path: Path, first: Mapping[str, object] | None,
    *, settle: float = 0.6, sleep: Callable[[float], None] | None = None,
    read_json: Callable[[Path], object] | None = None,
) -> Mapping[str, object] | None:
    """Return the FRESHEST readable busy-snapshot for the guard to JUDGE ON.

    A39 / PH-1: when *first* is stale-by-age, the publisher may have written a
    newer, more accurate reading since. Re-read once and return whichever reading
    has the greater captured_at — the guard MUST judge the CONTENT of the freshest
    reading (busy, executor_in_flight, generation), not merely learn that a fresher
    one exists and then trust the old content. Returning a bool (the previous bug)
    suppressed "this content is too old" and then judged the too-old content anyway,
    letting a restart through while read #2 showed workers in flight.

    Never returns something older than *first*: if the re-read is unreadable or not
    newer, *first* is kept. Side-effect-free apart from the single settle sleep.
    """
    _sleep = sleep if sleep is not None else time.sleep
    _read = read_json if read_json is not None else _read_json
    _sleep(max(0.0, settle))
    try:
        second = _read(snapshot_path)
    except Exception:
        return first
    before = _snapshot_captured_at(first)
    after = _snapshot_captured_at(second)
    if after is None:
        return first
    if before is None or after > before:
        return second if isinstance(second, Mapping) else first
    return first


def _probe_livez(url: str, timeout: float = 1.0,
                 opener: Callable[..., object] = urllib.request.urlopen) -> Mapping[str, object] | None:
    """Fetch the lock-free /livez payload, or None if unreachable/unparseable."""
    try:
        with opener(url, timeout=timeout) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except Exception:
        return None
    return payload if isinstance(payload, Mapping) else None


def _snapshot_publisher_alive(
    snapshot: Mapping[str, object] | None,
    expected_generation: str,
    livez: Mapping[str, object] | None,
) -> bool:
    """A39: is the busy-snapshot PUBLISHER THREAD live and current?

    Two-condition decision (mirrors reap_orphan_busy_snapshots' PID-reuse defense):
    suppress the stale-by-age block ONLY when BOTH hold, else fail closed.

      1. LIVE thread-liveness: /livez.restart_busy_snapshot_alive is True. This is
         sourced from the running process (Thread.is_alive(), lock-free) — NOT
         stamped into the snapshot, because a dead publisher thread cannot stamp its
         own death (PH-5: process pid stays alive forever after the thread dies, so
         a pid check fails OPEN on a frozen snapshot). /livez is used, not /health,
         because /health takes _LANES_LOCK — the exact lock a busy handler holds
         during the block PH-2 targets — and would hang in that very window.
      2. GENERATION match: the LIVE process generation (from the same /livez we just
         trusted for liveness) equals the generation we are authorising a restart
         for. Without this, a NEW generation's healthy publisher could vouch for an
         OLD generation's frozen snapshot. Also closes PH-6: a signal sourced from
         the real process on :8084 cannot be spoofed by a stranger holding a
         recycled pid — there is no pid in this decision at all.

    Why this survives an ongoing lock-block (does not regress PH-2): the publisher
    thread stuck on _publish_lock is still is_alive()==True, and /livez never touches
    that lock, so restart_busy_snapshot_alive stays True throughout the block.
    Why it blocks a dead thread (fixes PH-5): a dead thread flips is_alive() to
    False permanently, so restart_busy_snapshot_alive is False and the stale block
    stands.
    """
    if not isinstance(snapshot, Mapping):
        return False
    if not isinstance(livez, Mapping):
        return False
    if livez.get("restart_busy_snapshot_alive") is not True:
        return False
    live_generation = livez.get("generation")
    if not isinstance(live_generation, str) or not live_generation:
        return False
    if live_generation != expected_generation:
        # Trust the live signal only for the generation it actually describes.
        return False
    # 3. PUBLISHING, not merely alive (A45). Condition 1 asks whether the thread
    #    exists; it cannot distinguish a thread blocked on the publish lock (which
    #    WILL catch up — the PH-2 case this suppression exists for) from a thread
    #    whose os.replace keeps being denied, which is alive, looping, and landing
    #    nothing. Both freeze captured_at; only the first is safe to suppress.
    #    Measured on the real publisher thread with 5 concurrent readers on the D
    #    drive: age reached 7.9s against this function's 3.0s deadline with
    #    restart_busy_snapshot_alive True for the whole run — the guard would have
    #    authorised a restart on a 7.9s-old reading of "not busy".
    #    A missing/blank publish block means an older generation that does not
    #    report it: fall through to the pre-A45 answer rather than newly blocking a
    #    restart on a payload shape, since that would be a fail-CLOSED regression on
    #    a signal this function never required before.
    publish = livez.get("restart_busy_snapshot_publish")
    if isinstance(publish, Mapping) and publish:
        if publish.get("last_publish_error"):
            # The last cycle did not land. Whatever the thread's state, the file on
            # disk is not being refreshed, so the stale reading is real.
            return False
    return True


def _resolve_livez_url() -> str:
    # A39: the liveness port is configurable via ORCHESTRATOR_LIVEZ_PORT (default
    # 8084). Resolve it the same way watchdog_a.py / orchestrator_server.py do so a
    # moved port does not leave the restart controller probing a dead port.
    port = int(os.environ.get("ORCHESTRATOR_LIVEZ_PORT", "8084"))
    return f"http://127.0.0.1:{port}/livez"


def build_guard(root: Path, expected_generation: str, expected_request_id: str = "",
                max_age: float = 3.0, allow_force: bool = False,
                dispatch_stale_seconds: float = 600.0,
                livez_url: str | None = None) -> Callable[[], GuardDecision]:
    if livez_url is None:
        livez_url = _resolve_livez_url()
    snapshot_path = root / ".orchestrator_cache" / "restart_busy_snapshot.json"
    slots_path = root / ".council" / "runtime_state.json"
    broker_path = root / ".council" / "broker_filtered.jsonl"

    def guard() -> GuardDecision:
        try:
            snapshot = _read_json(snapshot_path)
        except Exception:
            snapshot = None
        # A39: when the reading is stale by age, re-read once and JUDGE ON THE
        # FRESHEST reading (PH-1) — a lagging publisher may already have written a
        # newer, accurate reading (idle OR busy). Liveness for suppressing the
        # stale-by-age block is the busy-snapshot publisher THREAD being live AND
        # current, sampled from the lock-free /livez channel (PH-5): a thread that
        # is blocked-but-alive keeps its snapshot suppressible, a DEAD thread (whose
        # captured_at is frozen forever while the process pid stays alive) does not.
        # Only re-read/probe when the first reading is actually stale so the common
        # fresh path stays fast.
        publisher_alive: bool | None = None
        if isinstance(snapshot, dict):
            captured_at = snapshot.get("captured_at")
            if isinstance(captured_at, (int, float)) and (
                time.time() - float(captured_at) > max_age
            ):
                snapshot = _refresh_busy_snapshot(snapshot_path, snapshot)
                livez = _probe_livez(livez_url)
                publisher_alive = _snapshot_publisher_alive(
                    snapshot, expected_generation, livez
                )
        try:
            if not slots_path.exists():
                raise FileNotFoundError(slots_path)
            raw_slots = _read_json(slots_path)
            if isinstance(raw_slots, list):
                slots = raw_slots
            elif isinstance(raw_slots, dict):
                slots = list(raw_slots.values())
            else:
                raise ValueError("council runtime state must be an object or array")
        except Exception:
            slots = [None]
        try:
            broker_lines = broker_path.read_text(encoding="utf-8").splitlines() if broker_path.exists() else []
        except Exception:
            broker_lines = ["<unreadable>"]
        return evaluate_restart_guard(
            busy_snapshot=snapshot if isinstance(snapshot, dict) else None,
            council_slots=slots,
            broker_lines=broker_lines,
            now=time.time(),
            snapshot_max_age_seconds=max_age,
            pid_alive=_pid_alive,
            expected_generation=expected_generation,
            expected_request_id=expected_request_id,
            allow_force=allow_force,
            dispatch_stale_seconds=dispatch_stale_seconds,
            snapshot_publisher_alive=publisher_alive,
        )

    return guard


def _append_audit(path: Path, event: Mapping[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as stream:
        stream.write(json.dumps(dict(event), separators=(",", ":"), sort_keys=True) + "\n")
        stream.flush()
        os.fsync(stream.fileno())


class ControllerAlreadyRunning(RuntimeError):
    """A healthy restart controller already owns the requested endpoint."""


class _SingleInstanceHTTPServer(ThreadingHTTPServer):
    allow_reuse_address = False

    def server_bind(self) -> None:
        import socket

        if hasattr(socket, "SO_EXCLUSIVEADDRUSE"):
            self.socket.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
        super().server_bind()


def _probe_existing_controller(host: str, port: int, timeout: float = 0.5) -> bool:
    """Return True only when the endpoint identifies a healthy restart controller."""
    if not port:
        return False
    probe_host = "127.0.0.1" if host in {"", "0.0.0.0", "::"} else host
    try:
        with urllib.request.urlopen(f"http://{probe_host}:{port}/health", timeout=timeout) as response:
            payload = json.loads(response.read().decode("utf-8"))
        return bool(
            payload.get("ok")
            and payload.get("service") == "restart-control"
            and int(payload.get("port", -1)) == port
        )
    except Exception:
        return False


def create_control_server(
    *,
    controller: RestartController,
    secret: bytes | None = None,
    secret_path: Path | None = None,
    current_generation: Callable[[], str],
    reload_guards: Callable[[], GuardDecision] | None = None,
    host: str = "127.0.0.1",
    port: int = 8085,
    limiter: SlidingWindowRateLimiter | None = None,
) -> ThreadingHTTPServer:
    if _probe_existing_controller(host, port):
        raise ControllerAlreadyRunning(f"restart controller already running on {host}:{port}")
    rate_limiter = limiter or SlidingWindowRateLimiter()

    def current_secret() -> bytes:
        if secret_path is not None:
            status = load_control_secret(secret_path)
            if not status.usable or status.secret is None:
                raise RestartProtocolError("AUTH_UNAVAILABLE", 503)
            return status.secret
        if secret is None:
            raise RestartProtocolError("AUTH_UNAVAILABLE", 503)
        return secret

    class Handler(BaseHTTPRequestHandler):
        def _json(self, status: int, payload: Mapping[str, object]) -> None:
            body = json.dumps(dict(payload), separators=(",", ":")).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)

        def _authenticated(self, method: str, path: str, body: bytes) -> bool:
            supplied = self.headers.get("X-Restart-Signature", "")
            newline = bytes((10,))
            expected = __import__('hmac').new(
                current_secret(), method.encode("ascii") + newline + path.encode("ascii") + newline + body,
                __import__('hashlib').sha256,
            ).hexdigest()
            return bool(supplied and __import__('hmac').compare_digest(supplied, expected))

        def do_GET(self) -> None:
            parsed_path = self.path.split("?", 1)[0]
            if parsed_path.startswith("/status/"):
                request_id = parsed_path[len("/status/"):]
                try:
                    authenticated = self._authenticated("GET", parsed_path, b"")
                except RestartProtocolError as exc:
                    self._json(exc.status, {"ok": False, "error_name": exc.error_name})
                    return
                if not authenticated:
                    self._json(401, {"ok": False, "error_name": "AUTH_FAILED"})
                    return
                result = controller.status(request_id)
                self._json(200 if result else 404, result or {"ok": False, "error_name": "REQUEST_NOT_FOUND"})
                return
            if parsed_path != "/health":
                self._json(404, {"ok": False, "error_name": "NOT_FOUND"})
                return
            self._json(200, {"ok": True, "service": "restart-control", "port": self.server.server_port})

        def do_POST(self) -> None:
            path = self.path.split("?", 1)[0]
            if path not in {"/restart", "/reload_guards"}:
                self._json(404, {"ok": False, "error_name": "NOT_FOUND"})
                return
            try:
                length = int(self.headers.get("Content-Length", "0"))
                body = self.rfile.read(min(length, 4097))
                active_secret = current_secret()
                if path == "/reload_guards":
                    if not self._authenticated("POST", path, body):
                        self._json(401, {"ok": False, "error_name": "AUTH_FAILED"})
                        return
                    if reload_guards is None:
                        self._json(503, {"ok": False, "error_name": "GUARD_RELOAD_UNAVAILABLE"})
                        return
                    decision = reload_guards()
                    self._json(200, {"ok": True, "reloaded": True, "allowed": decision.allowed,
                                     "reasons": list(decision.reasons),
                                     "blocking_guards": list(decision.blocking_guards)})
                    return
                request = validate_restart_request(
                    method="POST", path=path, peer_host=self.client_address[0],
                    headers=self.headers, body=body, secret=active_secret,
                    current_generation=current_generation(), limiter=rate_limiter,
                )
                accepted = controller.accept(request)
                self._json(202 if accepted.get("ok") else 409, accepted)
            except RestartProtocolError as exc:
                self._json(exc.status, {"ok": False, "error_name": exc.error_name})
            except Exception as exc:
                self._json(503, {"ok": False, "error_name": "CONTROL_FAILURE", "detail": type(exc).__name__})

        def log_message(self, format, *args):
            return

    return _SingleInstanceHTTPServer((host, port), Handler)


def _health_is_responsive(url: str, timeout: float,
                          opener: Callable[..., object] = urllib.request.urlopen) -> bool:
    """R45: True if /health returns ANY HTTP response (event loop alive), False ONLY
    on a genuine no-response (timeout / connection refused / reset). A busy or
    degraded loop still answers (slow 200, or 503 / ok=false) and must NOT be healed
    -- only a true event-loop deadlock cannot answer HTTP at all. This separates a
    'restart-worthy deadlock' from 'alive but slow/degraded'."""
    import urllib.error as _ue
    try:
        with opener(url, timeout=timeout) as resp:
            resp.read()
        return True
    except _ue.HTTPError:
        return True
    except Exception:
        return False


def _should_defer_auto_heal(
    snapshot: Mapping[str, object] | None,
    *,
    now: float | None = None,
    max_age_s: float = 30.0,
    defer_streak: int = 0,
    max_defer_streak: int = 20,
) -> tuple[bool, str]:
    """Return (defer, reason) for auto-heal when /health is silent.

    Killing mid-write loses the USAGE_LOG line and can lose INDEX; a FRESH
    busy snapshot means in-flight work is still being published by the
    independent snapshot thread, so the loop is busy rather than dead.

    Does NOT reopen the fake-livez hole: a genuine deadlock that freezes the
    publisher makes captured_at go stale, and we then heal as before. A
    deadlock that releases the GIL but never yields the loop can keep the
    snapshot fresh+busy; max_defer_streak caps that at ~10 min (20*30s).
    """
    if defer_streak >= max_defer_streak:
        return False, "defer_cap"
    if not isinstance(snapshot, Mapping):
        return False, "no_snapshot"
    captured_at = snapshot.get("captured_at")
    if not isinstance(captured_at, (int, float)):
        return False, "invalid_captured_at"
    age = (now if now is not None else time.time()) - float(captured_at)
    if age > max_age_s:
        return False, "snapshot_stale"
    inflight = (
        int(snapshot.get("requests_in_flight") or 0) > 0
        or int(snapshot.get("executor_in_flight") or 0) > 0
        or int(snapshot.get("accounting_pending") or 0) > 0
        or bool(snapshot.get("busy"))
    )
    if not inflight:
        return False, "not_busy"
    return True, "inflight"


def _auto_heal_monitor(
    livez_url: str,
    health_url: str,
    scheduler: "TaskSchedulerAdapter",
    settled_stop: Callable[[], bool],
    append_audit: Callable[[Mapping[str, object]], None],
    *,
    poll_interval: float = 30.0,
    hung_threshold: int = 5,
    health_timeout: float = 10.0,
    warmup_grace_s: float = 60.0,
    # Anh 2026-08-11: 900s -> 300s (env-tunable ORCH_WATCHDOG_COOLDOWN_S). The old
    # 15-min cooldown left a re-hang unhealed for 15 min after an auto-heal; 5 min
    # recovers faster while still preventing restart-thrashing.
    # TOOLA-AHEALTHHUNG-R1 (2026-08-27): the "on-loop hang is fixed so re-hangs
    # are rare" claim was false — write_new_file still ran update_file_cache on
    # the loop (snapshot full-walk up to 180s) and this monitor killed mid-write.
    cooldown_s: float = float(os.environ.get("ORCH_WATCHDOG_COOLDOWN_S", "300")),
    busy_snapshot_path: str | os.PathLike[str] | None = None,
) -> None:
    """(d) Anh 2026-08-09: autonomous auto-heal cho event-loop TREO.

    /livez (:8084) doc lap event-loop -> instance treo van bao 'song' (livez GIA) nen khong ai
    restart. Monitor phat hien /livez OK NHUNG /health (:8083, qua event-loop) treo/timeout LIEN
    TIEP >= hung_threshold lan -> ket luan event-loop hung -> tu restart [A] qua schtasks /end +
    /run (high_il.cmd force-kill + relaunch), verify gen DOI. KHONG dung start_orchestrator.bat
    (tranh tu-quiesce chinh ToolA_RestartControl dang chay monitor nay). Anti-churn: can nhieu lan
    lien tiep (~poll_interval*hung_threshold giay xac nhan treo) + cooldown giua 2 heal; chi heal
    khi livez UP + health DOWN (dung 'event-loop hung'); ca hai down = startup/crash -> de task
    LogonTrigger / RestartOnFailure lo, KHONG phai viec cua monitor.
    """
    import json as _json
    import urllib.request as _ur
    import urllib.error as _ue

    def _probe(url: str, timeout: float) -> bool:
        try:
            with _ur.urlopen(url, timeout=timeout) as resp:
                payload = _json.loads(resp.read().decode("utf-8"))
            return bool(payload.get("ok"))
        except Exception:
            return False

    def _health_responsive(url: str, timeout: float) -> bool:
        return _health_is_responsive(url, timeout)

    hung_streak = 0
    defer_streak = 0
    last_heal = 0.0
    last_gen = None
    warmup_until = 0.0
    snap_path = Path(busy_snapshot_path) if busy_snapshot_path else None
    while True:
        time.sleep(poll_interval)
        livez_ok = _probe(livez_url, 2.0)
        health_alive = _health_responsive(health_url, health_timeout)
        # R45: detect a fresh (re)start and grant a warmup grace. Early init can
        # briefly block the loop; counting that as 'hung' caused heal churn (a
        # just-healed process being healed again -- observed 2026-08-11).
        try:
            gen = getattr(_fetch_identity(livez_url), "generation", None)
        except Exception:
            gen = None
        if gen is not None and gen != last_gen:
            last_gen = gen
            warmup_until = time.monotonic() + warmup_grace_s
            hung_streak = 0
            defer_streak = 0
        if time.monotonic() < warmup_until:
            continue
        if livez_ok and not health_alive:
            snap = None
            if snap_path is not None:
                try:
                    loaded = _read_json(snap_path)
                    snap = loaded if isinstance(loaded, Mapping) else None
                except Exception:
                    snap = None
            defer, defer_why = _should_defer_auto_heal(snap, defer_streak=defer_streak)
            if defer:
                defer_streak += 1
                append_audit({
                    "event": "auto_heal_deferred_inflight",
                    "reason": defer_why,
                    "defer_streak": defer_streak,
                    "hung_streak": hung_streak,
                })
                continue
            hung_streak += 1
            defer_streak = 0
        else:
            hung_streak = 0
            defer_streak = 0
        if hung_streak < hung_threshold:
            continue
        if (time.monotonic() - last_heal) < cooldown_s:
            continue
        try:
            old = _fetch_identity(livez_url)
        except Exception:
            old = None
        append_audit({
            "event": "auto_heal_triggered", "reason": "livez_ok_health_hung",
            "hung_streak": hung_streak,
            "old_generation": getattr(old, "generation", None),
            "old_pid": getattr(old, "pid", None),
        })
        healed_ok = False
        new_gen = None
        try:
            scheduler._call("/end")
            try:
                settled_stop()
            except Exception:
                pass
            scheduler._call("/run")
            if old is not None:
                new = _wait_started(old, livez_url, health_url, timeout=90.0)
                if new is not None:
                    healed_ok = True
                    new_gen = new.generation
        except Exception as exc:
            append_audit({"event": "auto_heal_error", "detail": type(exc).__name__})
        append_audit({"event": "auto_heal_result", "ok": healed_ok, "new_generation": new_gen})
        last_heal = time.monotonic()
        hung_streak = 0
        warmup_until = time.monotonic() + warmup_grace_s


def main() -> int:
    parser = argparse.ArgumentParser(description="Independent Project Orchestrator restart controller")
    parser.add_argument("--port", type=int, default=8085)
    parser.add_argument("--task-name", default="ToolA_Orchestrator")
    args = parser.parse_args()
    ensure_hidden_console()
    root = Path(__file__).resolve().parents[2]
    cache = root / ".orchestrator_cache"
    # C3 DURABLE: migrate legacy secret if still at old cache path, then use canonical.
    migrated = migrate_legacy_secret(root)
    if migrated is None:
        raise SystemExit("restart controller secret migration failed")
    secret_path = canonical_secret_path(root)
    secret_status = load_control_secret(secret_path)
    if not secret_status.usable or secret_status.secret is None:
        raise SystemExit("restart controller secret ACL is not usable")
    livez_url = _resolve_livez_url()
    health_url = "http://127.0.0.1:8083/health"
    console_python = Path(sys.executable)
    if console_python.stem.lower() == "pythonw":
        sibling = console_python.with_name("python.exe")
        if sibling.is_file():
            console_python = sibling
    def settled_stop() -> bool:
        result = subprocess.run(
            [str(console_python), str(root / "restart_orchestrator.py"), "--clear-timeout", "30"],
            cwd=str(root), capture_output=True, text=True, timeout=45, check=False,
            **({"creationflags": child_creationflags(), "startupinfo": child_startupinfo()} if os.name == "nt" else {}),
        )
        return result.returncode == 0

    scheduler = TaskSchedulerAdapter(args.task_name, settled_stop=settled_stop)
    controller = RestartController(
        scheduler=scheduler,
        guard=lambda request: build_guard(
            root, request.expected_generation, request.request_id, livez_url=livez_url
        )(),
        current_identity=lambda: _fetch_identity(livez_url),
        prove_stopped=lambda old: _wait_stopped(old, livez_url),
        prove_started=lambda old: _wait_started(old, livez_url, health_url),
        append_audit=lambda event: _append_audit(cache / "restart_control_audit.jsonl", event),
    )
    # C3 DURABLE: serve from canonical path (already set above).
    try:
        server = create_control_server(
            controller=controller,
            secret_path=secret_path,
            current_generation=lambda: _fetch_identity(livez_url).generation,
            reload_guards=lambda: build_guard(
                root, _fetch_identity(livez_url).generation, livez_url=livez_url
            )(),
            port=args.port,
        )
    except ControllerAlreadyRunning:
        print(f"restart controller already running on 127.0.0.1:{args.port}; exiting")
        return 0
    # (d) Anh 2026-08-09: autonomous auto-heal cho event-loop TREO (livez gia song van bao alive).
    threading.Thread(
        target=_auto_heal_monitor,
        args=(livez_url, health_url, scheduler, settled_stop,
              lambda event: _append_audit(cache / "restart_control_audit.jsonl", event)),
        kwargs={"busy_snapshot_path": cache / "restart_busy_snapshot.json"},
        name="auto-heal-monitor",
        daemon=True,
    ).start()
    server.serve_forever()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
