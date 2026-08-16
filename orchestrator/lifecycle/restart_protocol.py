"""Authentication and request validation for the loopback restart controller."""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import secrets
import re
import stat
import subprocess
import time
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Mapping


MAX_BODY_BYTES = 4096
MAX_CLOCK_SKEW_SECONDS = 30.0
DEFAULT_CONTROL_URL = "http://127.0.0.1:8085/restart"

# --- C3 DURABLE: canonical restart control secret path ---
# The secret MUST NOT live under .orchestrator_cache/ because the write-pipeline
# delete_file tool permits deletion of anything under that directory with no backup.
# The stable home is .orchestrator_lifecycle/ which is outside any ephemeral cache.
RESTART_LIFECYCLE_DIR_NAME = ".orchestrator_lifecycle"
RESTART_CONTROL_SECRET_FILENAME = "restart_control.secret"
LEGACY_SECRET_RELPATH = ".orchestrator_cache/restart_control.secret"


def canonical_secret_path(root) -> Path:
    """Return the stable on-disk path for the restart control secret."""
    return Path(root) / RESTART_LIFECYCLE_DIR_NAME / RESTART_CONTROL_SECRET_FILENAME


def is_protected_control_path(path: str | Path) -> bool:
    """Return True when *path* is a restart-control lifecycle artifact.

    Covers the canonical .orchestrator_lifecycle/ directory AND the legacy
    .orchestrator_cache/restart_control.secret location (still valid during the
    migration window before the one-time move runs). Used by the write-pipeline
    delete_file denylist to refuse deletion of restart-control artifacts.
    """
    try:
        normalized = str(Path(path)).replace("\\", "/").lower()
        marker = f"/{RESTART_LIFECYCLE_DIR_NAME.lower()}/"
        if marker in normalized or normalized.endswith(f"/{RESTART_LIFECYCLE_DIR_NAME.lower()}"):
            return True
        # Legacy location: protect the secret itself while it still lives in the cache dir.
        return normalized.endswith("/" + LEGACY_SECRET_RELPATH.lower())
    except Exception:
        return False


def migrate_legacy_secret(root) -> Path | None:
    """Move legacy .orchestrator_cache/restart_control.secret to the canonical path.

    Returns the canonical path if migration succeeded or was unnecessary,
    None if the legacy file existed but migration failed.
    """
    canonical = canonical_secret_path(root)
    legacy = Path(root) / LEGACY_SECRET_RELPATH
    if canonical.exists():
        # Canonical already in place; remove legacy if present to avoid confusion.
        if legacy.exists():
            try:
                legacy.unlink()
            except OSError:
                pass
        return canonical
    if not legacy.exists():
        return canonical  # load_control_secret will create fresh
    try:
        canonical.parent.mkdir(parents=True, exist_ok=True)
        data = legacy.read_bytes()
        fd = os.open(str(canonical), os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "wb") as f:
            f.write(data)
            f.flush()
            os.fsync(f.fileno())
        legacy.unlink()
        return canonical
    except OSError:
        return None


class BusySnapshotPublisher:
    """Publish server busy state without depending on the asyncio event loop."""

    def __init__(self, path: str | Path, snapshot: Callable[[], Mapping[str, object]], interval: float = 0.5) -> None:
        self.path = Path(path)
        self.snapshot = snapshot
        self.interval = interval
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._publish_lock = threading.Lock()
        # A45: publish outcome, read by /livez. `last_publish_ok` is the third
        # state the A39 liveness channel was missing — see `start()`.
        self._counters_lock = threading.Lock()
        self.publish_ok = 0
        self.publish_denied = 0
        self.last_publish_ok = 0.0
        self.last_publish_error = ""

    def publish_once(self) -> None:
        with self._publish_lock:
            payload = dict(self.snapshot())
            payload["captured_at"] = time.time()
            # A39/PH-5: liveness of THIS publisher is NOT stamped here. A dead
            # publisher thread cannot stamp its own death, and the process pid stays
            # alive after the thread dies — a pid stamp on a frozen snapshot fails
            # OPEN. The restart guard instead samples the publisher THREAD's live
            # is_alive() from the lock-free /livez channel; see
            # restart_control_service._snapshot_publisher_alive.
            self.path.parent.mkdir(parents=True, exist_ok=True)
            temporary = self.path.with_name(
                f"{self.path.name}.tmp.{os.getpid()}.{threading.get_ident()}.{secrets.token_hex(4)}"
            )
            try:
                with temporary.open("w", encoding="utf-8") as stream:
                    json.dump(payload, stream, separators=(",", ":"), sort_keys=True)
                    stream.flush()
                # A41/PH-2: NO fsync here. This snapshot is ephemeral — a lost write
                # is simply re-published on the next 0.5s cycle — so the reader needs
                # atomicity (a torn/partial read must never happen), NOT crash-
                # durability. os.replace() already gives atomic publish; fsync only
                # buys crash-survival that has no meaning for data regenerated twice a
                # second. On the slow repo drive (D) an fsync costs 300ms idle and
                # >1.3s under concurrent disk load, so it — not lock contention —
                # dominated the publisher's cycle time and let captured_at age past the
                # restart guard's staleness deadline. Dropping it keeps every cycle
                # sub-2ms. Matches existing ephemeral/atomic-write precedent in the
                # repo (fileops.common.atomic_write_bytes, storage.fs.atomic_write_text)
                # while durable writers (session logs, the restart secret) keep fsync.
                #
                # A45: os.replace is retried. On Windows a concurrent reader holding
                # the target open makes os.replace raise PermissionError (WinError 5)
                # — measured at 93.7% of attempts under tight-loop readers on this
                # repo's D drive. A single denial freezes `captured_at` while the
                # publisher THREAD stays alive, which is precisely the state A39's
                # liveness suppression reads as "transient burst, safe to ignore the
                # staleness block". Measured with the real thread running: age
                # reached 7.9s against a 3.0s deadline with .alive still True. The
                # retry ladder is the one storage/fs.py:atomic_write_text already
                # uses for this exact error ("broker writes have raced 37 times"):
                # 4 attempts, 25ms doubling, ~175ms worst case — well inside the
                # 500ms cycle. Deliberately NO fallback to a direct in-place write
                # here (fs.py has one): that would sacrifice atomicity, and a torn
                # snapshot read is worse for the guard than a skipped cycle.
                last_error: OSError | None = None
                for attempt in range(4):
                    try:
                        os.replace(temporary, self.path)
                        last_error = None
                        break
                    except OSError as exc:
                        last_error = exc
                        if attempt < 3:
                            time.sleep(0.025 * (2**attempt))
                with self._counters_lock:
                    if last_error is None:
                        self.publish_ok += 1
                        self.last_publish_ok = payload["captured_at"]
                        self.last_publish_error = ""
                    else:
                        self.publish_denied += 1
                        self.last_publish_error = f"{type(last_error).__name__}: {last_error}"
                if last_error is not None:
                    raise last_error
            finally:
                try:
                    temporary.unlink(missing_ok=True)
                except OSError:
                    pass

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()

        def run() -> None:
            while not self._stop.is_set():
                try:
                    self.publish_once()
                except Exception:
                    # Still swallowed ON PURPOSE: the publisher thread must survive a
                    # failed cycle (a dead thread freezes captured_at forever, which
                    # is strictly worse). A45 removes the SILENCE, not the survival —
                    # publish_once() records the denial in the counters above, and
                    # `publish_health` exposes it so a frozen-but-alive publisher can
                    # no longer masquerade as a healthy one on the /livez channel.
                    pass
                self._stop.wait(self.interval)

        self._thread = threading.Thread(target=run, name="restart-busy-snapshot", daemon=True)
        self._thread.start()

    def stop(self, timeout: float = 2.0) -> None:
        self._stop.set()
        if self._thread:
            self._thread.join(timeout)

    @property
    def alive(self) -> bool:
        return bool(self._thread and self._thread.is_alive())

    def publish_health(self) -> dict[str, object]:
        """Publisher liveness AND publish success, for the lock-free /livez channel.

        A45: `alive` alone answers the wrong question. A39/PH-5 uses thread liveness
        to decide whether a stale-by-age snapshot is a harmless transient burst
        (blocked-but-alive publisher, suppress the block) or a real hazard (dead
        publisher, keep the block). That split assumes an alive thread is still
        PUBLISHING — but a thread whose os.replace is being denied is alive and
        publishing nothing, so `captured_at` freezes exactly as if it had died while
        the guard reads "alive" and suppresses the block. Measured on the real
        publisher thread: age 7.9s against a 3.0s deadline, .alive True throughout.

        `last_publish_ok` closes that gap: it is stamped only by a replace that
        actually landed, so a reader can tell "thread alive AND its last write
        landed N seconds ago" from "thread alive but nothing has landed since".
        Takes only the counters lock (never the publish lock), so it stays
        responsive while a publish is in flight — same constraint as `alive`.
        """
        with self._counters_lock:
            return {
                "alive": self.alive,
                "publish_ok": self.publish_ok,
                "publish_denied": self.publish_denied,
                "last_publish_ok": self.last_publish_ok,
                "last_publish_error": self.last_publish_error,
            }


# --- A08: boot reaper for orphaned busy-snapshot temp files ---
# publish_once() writes "<name>.tmp.<pid>.<tid>.<hex>" then os.replace()s it onto
# the real snapshot. A hard kill (the restart pipeline SIGKILLs the old PID)
# between create and replace orphans the temp. There is no in-process cleanup for
# a dead writer, so temps accumulate across restarts. This reaper runs once at
# boot and removes ONLY temps whose writer PID is dead.
_SNAPSHOT_TMP_RE = re.compile(r"\.tmp\.(?P<pid>\d+)\.\d+\.[0-9a-fA-F]+$")

# PID-reuse defense: Windows recycles PIDs aggressively, so "PID is dead" alone is
# not enough — a temp could belong to a *live* writer whose PID we misread, or a
# just-created temp mid-replace. We therefore also require the file to be older
# than this floor before deleting. A real orphan is only ever growing older; a
# live writer replaces its temp within one publish interval (0.5s), so a minute
# is comfortably conservative.
_SNAPSHOT_TMP_MIN_AGE_S = 60.0


def _snapshot_pid_alive(pid: int) -> bool:
    """Return True if *pid* is a live process. Uncertain => True (never reap a
    possibly-live writer's temp). Mirrors orchestrator_server._pid_alive so the
    reaper does not depend on the (forbidden) search_snapshots helper."""
    try:
        pid = int(pid)
    except (TypeError, ValueError):
        return True
    if pid <= 0:
        return True
    if os.name == "nt":
        try:
            import ctypes

            # PROCESS_QUERY_LIMITED_INFORMATION
            handle = ctypes.windll.kernel32.OpenProcess(0x1000, False, pid)
            if handle:
                ctypes.windll.kernel32.CloseHandle(handle)
                return True
            return False
        except Exception:
            return True  # uncertain -> assume alive, never falsely clear a live run
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        return True  # exists but not ours
    except OSError:
        return True


def reap_orphan_busy_snapshots(
    snapshot_path: str | Path,
    *,
    now: float | None = None,
    min_age_s: float = _SNAPSHOT_TMP_MIN_AGE_S,
    pid_alive: Callable[[int], bool] = _snapshot_pid_alive,
) -> dict:
    """Remove orphaned "<name>.tmp.<pid>.*" busy-snapshot temp files at boot.

    A temp is reaped only when its embedded writer PID is dead AND the file is at
    least *min_age_s* old (PID-reuse defense). Never touches the current process's
    temps. Fail-loud (7-RULE #7): returns a structured summary of what was reaped
    vs. kept and why, so the outcome is observable via log + /health rather than a
    silent sweep.
    """
    snapshot_path = Path(snapshot_path)
    directory = snapshot_path.parent
    prefix = snapshot_path.name + ".tmp."
    now = time.time() if now is None else now
    current_pid = os.getpid()
    reaped: list[str] = []
    kept_alive: list[str] = []
    kept_young: list[str] = []
    errors: list[str] = []

    try:
        entries = list(directory.iterdir())
    except OSError as exc:
        return {
            "scanned": 0, "reaped": [], "reaped_count": 0,
            "kept_alive": [], "kept_young": [], "errors": [f"scandir: {exc}"],
        }

    scanned = 0
    for entry in entries:
        name = entry.name
        if not name.startswith(prefix):
            continue
        match = _SNAPSHOT_TMP_RE.search(name)
        if not match:
            continue
        scanned += 1
        pid = int(match.group("pid"))
        if pid == current_pid:
            kept_alive.append(name)  # our own in-flight temp
            continue
        if pid_alive(pid):
            kept_alive.append(name)
            continue
        try:
            age_s = max(0.0, now - entry.stat().st_mtime)
        except OSError as exc:
            errors.append(f"{name}: stat {exc}")
            continue
        if age_s < min_age_s:
            kept_young.append(name)  # dead PID but too fresh -> PID-reuse guard
            continue
        try:
            entry.unlink()
            reaped.append(name)
        except FileNotFoundError:
            reaped.append(name)  # someone else removed it; still gone
        except OSError as exc:
            errors.append(f"{name}: unlink {exc}")

    return {
        "scanned": scanned,
        "reaped": reaped,
        "reaped_count": len(reaped),
        "kept_alive": kept_alive,
        "kept_young": kept_young,
        "errors": errors,
    }


class RestartProtocolError(ValueError):
    def __init__(self, error_name: str, status: int = 400) -> None:
        super().__init__(error_name)
        self.error_name = error_name
        self.status = status


@dataclass(frozen=True)
class SecretStatus:
    secret: bytes | None
    path: Path
    acl_restricted: bool
    usable: bool
    error: str | None = None


@dataclass(frozen=True)
class RestartRequest:
    request_id: str
    expected_generation: str
    timestamp: float


class SlidingWindowRateLimiter:
    def __init__(self, limit: int = 4, window_seconds: float = 60.0) -> None:
        self.limit = limit
        self.window_seconds = window_seconds
        self._hits: dict[str, list[float]] = {}
        self._lock = threading.Lock()

    def allow(self, key: str, now: float) -> bool:
        with self._lock:
            cutoff = now - self.window_seconds
            hits = [item for item in self._hits.get(key, ()) if item > cutoff]
            if len(hits) >= self.limit:
                self._hits[key] = hits
                return False
            hits.append(now)
            self._hits[key] = hits
            return True


def _restrict_secret(path: Path) -> tuple[bool, str | None]:
    try:
        if os.name == "nt":
            script = (
                "$p=$env:RESTART_SECRET_PATH;"
                "$acl=Get-Acl -LiteralPath $p;"
                "$acl.SetAccessRuleProtection($true,$false);"
                "foreach($r in @($acl.Access)){$acl.RemoveAccessRuleSpecific($r)};"
                "$ids=@([System.Security.Principal.WindowsIdentity]::GetCurrent().User,"
                "[System.Security.Principal.SecurityIdentifier]'S-1-5-18',"
                "[System.Security.Principal.SecurityIdentifier]'S-1-5-32-544');"
                "foreach($id in $ids){$acl.AddAccessRule([System.Security.AccessControl.FileSystemAccessRule]::new($id,'FullControl','Allow'))};"
                "Set-Acl -LiteralPath $p -AclObject $acl;"
                "$v=Get-Acl -LiteralPath $p;"
                "$explicit=@($v.Access|Where-Object{-not $_.IsInherited});"
                "$allowed=@($ids|ForEach-Object{$_.Value});"
                "$bad=@($explicit|Where-Object{$_.IdentityReference.Translate([System.Security.Principal.SecurityIdentifier]).Value -notin $allowed -or $_.AccessControlType -ne 'Allow' -or ($_.FileSystemRights -band [System.Security.AccessControl.FileSystemRights]::FullControl) -ne [System.Security.AccessControl.FileSystemRights]::FullControl});"
                "if(-not $v.AreAccessRulesProtected -or $bad.Count -ne 0 -or $explicit.Count -ne $allowed.Count){exit 7};"
                "'ACL_OK'"
            )
            environment = dict(os.environ)
            environment["RESTART_SECRET_PATH"] = str(path)
            result = subprocess.run(
                ["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", script],
                capture_output=True,
                text=True,
                timeout=10,
                check=False,
                env=environment,
            )
            if result.returncode != 0 or result.stdout.strip() != "ACL_OK":
                return False, "secret DACL set or verification failed"
        else:
            path.chmod(stat.S_IRUSR | stat.S_IWUSR)
            if stat.S_IMODE(path.stat().st_mode) != 0o600:
                return False, "secret mode is not 0600"
        return True, None
    except (OSError, subprocess.SubprocessError) as exc:
        return False, f"{type(exc).__name__}: {exc}"


def load_control_secret(path: str | Path) -> SecretStatus:
    """Load or atomically create the persistent secret, failing closed on weak ACLs."""
    target = Path(path)
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        if not target.exists():
            value = secrets.token_bytes(32)
            fd = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(fd, "wb") as stream:
                stream.write(value)
                stream.flush()
                os.fsync(stream.fileno())
        value = target.read_bytes()
        if len(value) < 32:
            return SecretStatus(None, target, False, False, "secret is shorter than 32 bytes")
        restricted, error = _restrict_secret(target)
        return SecretStatus(value if restricted else None, target, restricted, restricted, error)
    except OSError as exc:
        return SecretStatus(None, target, False, False, f"{type(exc).__name__}: {exc}")


def canonical_signature_input(method: str, path: str, body: bytes) -> bytes:
    return method.upper().encode("ascii") + b"\n" + path.encode("ascii") + b"\n" + body


def sign_request(secret: bytes, method: str, path: str, body: bytes) -> str:
    return hmac.new(secret, canonical_signature_input(method, path, body), hashlib.sha256).hexdigest()


def encode_request(request_id: str, expected_generation: str, timestamp: float) -> bytes:
    return json.dumps(
        {
            "expected_generation": expected_generation,
            "request_id": request_id,
            "timestamp": timestamp,
        },
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")


def validate_restart_request(
    *,
    method: str,
    path: str,
    peer_host: str,
    headers: Mapping[str, str],
    body: bytes,
    secret: bytes,
    current_generation: str,
    limiter: SlidingWindowRateLimiter,
    now: Callable[[], float] = time.time,
) -> RestartRequest:
    if method.upper() != "POST":
        raise RestartProtocolError("METHOD_NOT_ALLOWED", 405)
    if peer_host not in {"127.0.0.1", "::1", "localhost"}:
        raise RestartProtocolError("LOOPBACK_REQUIRED", 403)
    lowered = {str(key).lower(): str(value) for key, value in headers.items()}
    if "origin" in lowered:
        raise RestartProtocolError("ORIGIN_NOT_ALLOWED", 403)
    if len(body) > MAX_BODY_BYTES:
        raise RestartProtocolError("BODY_TOO_LARGE", 413)
    current_time = now()
    if not limiter.allow(peer_host, current_time):
        raise RestartProtocolError("RATE_LIMITED", 429)
    supplied = lowered.get("x-restart-signature", "")
    expected = sign_request(secret, method, path, body)
    if not supplied or not hmac.compare_digest(supplied, expected):
        raise RestartProtocolError("AUTH_FAILED", 401)
    try:
        payload = json.loads(body.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise RestartProtocolError("INVALID_JSON") from exc
    if not isinstance(payload, dict):
        raise RestartProtocolError("INVALID_REQUEST")
    if "force" in payload:
        raise RestartProtocolError("FORCE_NOT_ALLOWED", 400)
    if set(payload) != {"request_id", "expected_generation", "timestamp"}:
        raise RestartProtocolError("INVALID_FIELDS")
    request_id = payload.get("request_id")
    generation = payload.get("expected_generation")
    timestamp = payload.get("timestamp")
    if (not isinstance(request_id, str) or not (8 <= len(request_id) <= 128)
            or re.fullmatch(r"[A-Za-z0-9._-]+", request_id) is None):
        raise RestartProtocolError("INVALID_REQUEST_ID")
    if not isinstance(generation, str) or not generation:
        raise RestartProtocolError("INVALID_GENERATION")
    if not isinstance(timestamp, (int, float)) or abs(current_time - float(timestamp)) > MAX_CLOCK_SKEW_SECONDS:
        raise RestartProtocolError("STALE_REQUEST", 401)
    if not hmac.compare_digest(generation, current_generation):
        raise RestartProtocolError("GENERATION_MISMATCH", 409)
    return RestartRequest(request_id, generation, float(timestamp))
