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

    def publish_once(self) -> None:
        with self._publish_lock:
            payload = dict(self.snapshot())
            payload["captured_at"] = time.time()
            self.path.parent.mkdir(parents=True, exist_ok=True)
            temporary = self.path.with_name(
                f"{self.path.name}.tmp.{os.getpid()}.{threading.get_ident()}.{secrets.token_hex(4)}"
            )
            try:
                with temporary.open("w", encoding="utf-8") as stream:
                    json.dump(payload, stream, separators=(",", ":"), sort_keys=True)
                    stream.flush()
                    os.fsync(stream.fileno())
                os.replace(temporary, self.path)
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
