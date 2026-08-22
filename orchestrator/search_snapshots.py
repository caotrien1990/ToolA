"""Immutable content-addressed snapshots for deterministic project search.

A writer captures stable source bytes into a CAS, completes a generation directory,
and only then atomically replaces CURRENT. Readers pin one generation with a
cross-process lease and never reopen live project files.
"""

from __future__ import annotations

import contextlib
import ctypes
import hashlib
import json
import os
import re
import shutil
import threading
import time
import unicodedata
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Iterator

from orchestrator.skip_rules import should_skip_dir


CACHE_DIR_NAME = ".orchestrator_cache"
STORE_DIR_NAME = "search_snapshots"
SNAPSHOT_SCHEMA_VERSION = 1
CURRENT_FILENAME = "CURRENT"
MANIFEST_FILENAME = "MANIFEST.json"
INDEX_FILENAME = "INDEX.json"
READY_FILENAME = "READY"
RECOVERY_ORDER = "authenticated_publication_sequence"
_SNAPSHOT_ID_RE = re.compile(r"^v1-[0-9a-f]{64}$")
_WRITER_WAIT_SECONDS = 30.0
_CAPTURE_RETRIES = 3


class SnapshotError(RuntimeError):
    """Base class for snapshot consistency failures."""


class SnapshotUnavailable(SnapshotError):
    """No complete current generation is available."""


class SnapshotIntegrityError(SnapshotError):
    """A pointer, manifest, index, or blob failed integrity validation."""


class SnapshotUnrecoverableError(SnapshotIntegrityError):
    """A corrupt CURRENT pointer has no valid READY generation to recover."""


class SnapshotCaptureChanged(SnapshotError):
    """A source file changed while its bytes were being captured."""


class SnapshotPathCollision(SnapshotError):
    """Two source paths collapse to the same normalized Windows lookup key."""


class SnapshotWriterBusy(SnapshotError):
    """Another process owns the per-project snapshot writer lease."""


class SnapshotDeadlineExceeded(SnapshotUnavailable):
    """A deadline-aware reader could not acquire snapshot state in time."""


@dataclass(frozen=True)
class CapturedFile:
    record: dict[str, Any]
    payload: bytes


_LOCKS_GUARD = threading.Lock()
_PROJECT_LOCKS: dict[str, threading.RLock] = {}
_STATE_THREAD_LOCKS: dict[str, threading.RLock] = {}
_ACTIVE_LEASES_LOCK = threading.Lock()
_ACTIVE_LEASES: set[str] = set()
_ACTIVE_LEASE_REFS: dict[str, int] = {}
_PROCESS_LEASES: dict[tuple[str, str], str] = {}


def _default_skip_dir(name: str) -> bool:
    """Default walk filter when callers omit skip_dir.

    W1 2026-08-19: this used to lazy-import surgical_mapper, with an except
    branch whose fallback silently disagreed with the real rule (it knew only
    about ".council"). The rules now live in the leaf module
    `orchestrator.skip_rules`, imported at the top of this file, so there is
    one behaviour and no import cycle.
    """
    # B027: no project_root here on purpose. This default is called from the
    # snapshot walk, which passes only a bare name; callers that want the
    # per-project overlay pass their own skip_dir (search.py passes
    # fo.should_skip_dir, which is root-aware). Wiring a root in here would mean
    # threading it through publish_snapshot's whole filter contract for a default
    # that every real caller already overrides.
    return should_skip_dir(name)


def _project_lock(project_root: Path) -> threading.RLock:
    key = os.path.normcase(str(project_root.resolve()))
    with _LOCKS_GUARD:
        lock = _PROJECT_LOCKS.get(key)
        if lock is None:
            lock = threading.RLock()
            _PROJECT_LOCKS[key] = lock
        return lock


def _check_deadline(deadline_at: float | None, operation: str = "snapshot operation") -> None:
    if deadline_at is not None and time.monotonic() >= float(deadline_at):
        raise SnapshotDeadlineExceeded(f"{operation} deadline exceeded")


@contextlib.contextmanager
def _project_lock_until(project_root: Path, deadline_at: float) -> Iterator[None]:
    lock = _project_lock(project_root)
    remaining = float(deadline_at) - time.monotonic()
    if remaining <= 0 or not lock.acquire(timeout=remaining):
        raise SnapshotDeadlineExceeded("snapshot project lock deadline exceeded")
    try:
        yield
    finally:
        lock.release()


def _state_thread_lock(store: Path) -> threading.RLock:
    key = os.path.normcase(str(store.resolve()))
    with _LOCKS_GUARD:
        lock = _STATE_THREAD_LOCKS.get(key)
        if lock is None:
            lock = threading.RLock()
            _STATE_THREAD_LOCKS[key] = lock
        return lock


def snapshot_store_path(project_root: str | Path) -> Path:
    return Path(project_root).resolve() / CACHE_DIR_NAME / STORE_DIR_NAME


def _ensure_layout(store: Path) -> None:
    for relative in ("blobs/sha256", "generations", "leases", "staging", "trash"):
        (store / relative).mkdir(parents=True, exist_ok=True)


def _canonical_json_bytes(value: Any) -> bytes:
    return (json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n").encode("utf-8")


def _sha256_bytes(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _read_file_bytes(path: Path) -> bytes:
    return Path(path).read_bytes()


def _write_fsynced(path: Path, payload: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("xb") as handle:
        handle.write(payload)
        handle.flush()
        os.fsync(handle.fileno())


def _strict_replace(source: Path, destination: Path, *, replace_existing: bool) -> None:
    """Same-volume atomic replace; on Windows request write-through and retry sharing races."""
    source = Path(source)
    destination = Path(destination)
    if source.parent.resolve() != destination.parent.resolve() and source.drive != destination.drive:
        raise SnapshotIntegrityError("snapshot atomic replace must stay on one volume")

    attempts = 10 if os.name == "nt" else 1
    delay = 0.01
    for attempt in range(attempts):
        try:
            if os.name == "nt":
                kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
                move_file = kernel32.MoveFileExW
                move_file.argtypes = [ctypes.c_wchar_p, ctypes.c_wchar_p, ctypes.c_uint32]
                move_file.restype = ctypes.c_int
                flags = 0x8 | (0x1 if replace_existing else 0)  # WRITE_THROUGH | REPLACE_EXISTING
                if not move_file(str(source), str(destination), flags):
                    error_code = ctypes.get_last_error()
                    if error_code in {5, 32, 33}:
                        raise PermissionError(error_code, "MoveFileExW sharing violation", str(destination))
                    raise OSError(error_code, "MoveFileExW failed", str(destination))
            else:
                if not replace_existing and destination.exists():
                    raise FileExistsError(str(destination))
                os.replace(source, destination)
            return
        except PermissionError:
            if attempt + 1 >= attempts:
                raise
            time.sleep(delay)
            delay = min(delay * 2, 0.2)


def _atomic_write(path: Path, payload: bytes) -> None:
    temporary = path.with_name(f".{path.name}.{os.getpid()}.{uuid.uuid4().hex}.tmp")
    try:
        _write_fsynced(temporary, payload)
        _strict_replace(temporary, path, replace_existing=True)
    finally:
        with contextlib.suppress(FileNotFoundError):
            temporary.unlink()


def _blob_path(store: Path, digest: str) -> Path:
    if not re.fullmatch(r"[0-9a-f]{64}", digest or ""):
        raise SnapshotIntegrityError(f"invalid blob sha256: {digest!r}")
    return store / "blobs" / "sha256" / digest[:2] / digest


def _write_blob(
    store: Path,
    digest: str,
    payload: bytes,
    *,
    deadline_at: float | None = None,
) -> Path:
    _check_deadline(deadline_at, "snapshot blob write")
    destination = _blob_path(store, digest)
    if destination.exists():
        existing = destination.read_bytes()
        _check_deadline(deadline_at, "snapshot blob validation")
        if len(existing) == len(payload) and _sha256_bytes(existing) == digest:
            return destination
    temporary = destination.with_name(f".{digest}.{os.getpid()}.{uuid.uuid4().hex}.tmp")
    try:
        _write_fsynced(temporary, payload)
        _check_deadline(deadline_at, "snapshot blob write")
        _strict_replace(temporary, destination, replace_existing=True)
    finally:
        with contextlib.suppress(FileNotFoundError):
            temporary.unlink()
    return destination


def _normalize_relative_path(value: str) -> str:
    normalized = unicodedata.normalize("NFC", str(value).replace("\\", "/")).strip("/")
    parts = normalized.split("/") if normalized else []
    if not normalized or any(part in {"", ".", ".."} for part in parts):
        raise SnapshotIntegrityError(f"unsafe snapshot relative path: {value!r}")
    if Path(normalized).is_absolute() or re.match(r"^[A-Za-z]:", normalized):
        raise SnapshotIntegrityError(f"absolute path is forbidden in snapshot manifest: {value!r}")
    return normalized


def _path_lookup_key(value: str) -> str:
    return _normalize_relative_path(value).casefold()


def _validate_manifest_paths(records: list[dict[str, Any]]) -> None:
    seen: dict[str, str] = {}
    prior_sort_key: tuple[str, str] | None = None
    for record in records:
        relative = _normalize_relative_path(str(record.get("path") or ""))
        key = relative.casefold()
        previous = seen.get(key)
        if previous is not None:
            raise SnapshotPathCollision(
                f"duplicate/colliding snapshot path after NFC+casefold normalization: {previous!r} vs {relative!r}"
            )
        seen[key] = relative
        sort_key = (key, relative)
        if prior_sort_key is not None and sort_key < prior_sort_key:
            raise SnapshotIntegrityError("snapshot manifest file records are not deterministically sorted")
        prior_sort_key = sort_key


def _stat_signature(stat_result: os.stat_result) -> tuple[int, int, int, int]:
    return (
        int(getattr(stat_result, "st_dev", 0)),
        int(getattr(stat_result, "st_ino", 0)),
        int(stat_result.st_size),
        int(stat_result.st_mtime_ns),
    )


def capture_stable_file(
    project_root: str | Path,
    file_path: str | Path,
    retries: int = _CAPTURE_RETRIES,
    *,
    deadline_at: float | None = None,
) -> CapturedFile:
    _check_deadline(deadline_at, "snapshot capture")
    root = Path(project_root).resolve()
    candidate = Path(file_path)
    if not candidate.is_absolute():
        candidate = root / candidate
    candidate = candidate.absolute()
    if candidate.is_symlink():
        raise SnapshotIntegrityError(f"snapshot refuses symlink source: {candidate}")
    try:
        resolved = candidate.resolve(strict=True)
    except FileNotFoundError as exc:
        raise SnapshotCaptureChanged(f"source disappeared before capture: {candidate}") from exc
    if not resolved.is_relative_to(root):
        raise SnapshotIntegrityError(f"snapshot source escapes project root: {candidate}")
    relative = _normalize_relative_path(candidate.relative_to(root).as_posix())

    last_error = "source changed during capture"
    for _ in range(max(1, int(retries))):
        _check_deadline(deadline_at, "snapshot capture")
        try:
            before = candidate.stat()
            payload = _read_file_bytes(candidate)
            _check_deadline(deadline_at, "snapshot capture")
            middle = candidate.stat()
            confirmation = _read_file_bytes(candidate)
            _check_deadline(deadline_at, "snapshot capture")
            after = candidate.stat()
        except (FileNotFoundError, PermissionError, OSError) as exc:
            last_error = str(exc)
            continue
        signatures_match = (
            _stat_signature(before) == _stat_signature(middle) == _stat_signature(after)
        )
        if signatures_match and payload == confirmation and len(payload) == after.st_size:
            _check_deadline(deadline_at, "snapshot content hash")
            digest = _sha256_bytes(payload)
            return CapturedFile(
                record={
                    "path": relative,
                    "size": len(payload),
                    "mtime": float(after.st_mtime),
                    "mtime_ns": int(after.st_mtime_ns),
                    "sha256": digest,
                    "blob_sha256": digest,
                    "index_hash": hashlib.md5(payload).hexdigest(),
                },
                payload=payload,
            )
        last_error = f"metadata or bytes changed for {relative}"
    raise SnapshotCaptureChanged(f"could not capture a stable version of {relative}: {last_error}")


def _stable_index(index: dict[str, Any] | None) -> dict[str, Any]:
    if not isinstance(index, dict):
        return {}
    cloned = json.loads(json.dumps(index, ensure_ascii=False))
    cloned.pop("generated_at", None)
    cloned.pop("updated_at", None)
    return cloned


def _validate_index_against_records(
    index: dict[str, Any] | None,
    records: list[dict[str, Any]],
) -> tuple[dict[str, Any], bool]:
    if index is None:
        return {}, False
    stable = _stable_index(index)
    index_files = stable.get("files")
    if not isinstance(index_files, dict):
        raise SnapshotIntegrityError("authoritative snapshot INDEX must contain a files object")

    normalized_index: dict[str, tuple[str, dict[str, Any]]] = {}
    for raw_path, raw_info in index_files.items():
        relative = _normalize_relative_path(str(raw_path))
        key = relative.casefold()
        if key in normalized_index:
            raise SnapshotPathCollision(f"duplicate/colliding INDEX path: {raw_path!r}")
        if not isinstance(raw_info, dict):
            raise SnapshotIntegrityError(f"INDEX entry is not an object: {relative}")
        normalized_index[key] = (relative, raw_info)

    record_map = {record["path"].casefold(): record for record in records}
    if set(normalized_index) != set(record_map):
        missing = sorted(record_map.keys() - normalized_index.keys())[:10]
        extra = sorted(normalized_index.keys() - record_map.keys())[:10]
        raise SnapshotIntegrityError(
            f"INDEX/manifest file set mismatch: missing={missing}, extra={extra}"
        )
    for key, (relative, info) in normalized_index.items():
        record = record_map[key]
        indexed_hash = str(info.get("hash") or "")
        if indexed_hash not in {record["index_hash"], record["sha256"]}:
            raise SnapshotIntegrityError(f"INDEX/content hash mismatch: {relative}")
        try:
            indexed_size = int(info["size_bytes"])
            indexed_mtime = float(info["mtime"])
        except (KeyError, TypeError, ValueError) as exc:
            raise SnapshotIntegrityError(f"INDEX metadata missing/invalid: {relative}") from exc
        if indexed_size != int(record["size"]):
            raise SnapshotIntegrityError(f"INDEX/content size mismatch: {relative}")
        if abs(indexed_mtime - float(record["mtime"])) > 1e-6:
            raise SnapshotIntegrityError(f"INDEX/content mtime mismatch: {relative}")
    return stable, True


def _read_current_pointer(store: Path) -> dict[str, str] | None:
    current = store / CURRENT_FILENAME
    if not current.exists():
        return None
    try:
        payload = json.loads(current.read_text(encoding="utf-8"))
    except Exception as exc:
        raise SnapshotIntegrityError(f"CURRENT is not valid JSON: {exc}") from exc
    snapshot_id = str(payload.get("snapshot_id") or "")
    manifest_sha256 = str(payload.get("manifest_sha256") or "")
    if not _SNAPSHOT_ID_RE.fullmatch(snapshot_id):
        raise SnapshotIntegrityError(f"CURRENT has invalid snapshot_id: {snapshot_id!r}")
    if not re.fullmatch(r"[0-9a-f]{64}", manifest_sha256):
        raise SnapshotIntegrityError("CURRENT has invalid manifest_sha256")
    return {"snapshot_id": snapshot_id, "manifest_sha256": manifest_sha256}


def _replace_current_pointer(store: Path, snapshot_id: str, manifest_sha256: str) -> None:
    pointer = {
        "schema_version": SNAPSHOT_SCHEMA_VERSION,
        "snapshot_id": snapshot_id,
        "manifest_sha256": manifest_sha256,
    }
    current = store / CURRENT_FILENAME
    temporary = store / f".CURRENT.{os.getpid()}.{uuid.uuid4().hex}.tmp"
    try:
        _write_fsynced(temporary, _canonical_json_bytes(pointer))
        _strict_replace(temporary, current, replace_existing=True)
    finally:
        with contextlib.suppress(FileNotFoundError):
            temporary.unlink()


def _process_status(pid: int) -> tuple[bool, str]:
    """Return liveness plus a creation identity without ever signaling the process."""
    if pid <= 0:
        return False, ""
    if os.name == "nt":
        class _FILETIME(ctypes.Structure):
            _fields_ = [("low", ctypes.c_uint32), ("high", ctypes.c_uint32)]

        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        open_process = kernel32.OpenProcess
        open_process.argtypes = [ctypes.c_uint32, ctypes.c_int, ctypes.c_uint32]
        open_process.restype = ctypes.c_void_p
        close_handle = kernel32.CloseHandle
        close_handle.argtypes = [ctypes.c_void_p]
        close_handle.restype = ctypes.c_int
        get_exit_code = kernel32.GetExitCodeProcess
        get_exit_code.argtypes = [ctypes.c_void_p, ctypes.POINTER(ctypes.c_uint32)]
        get_exit_code.restype = ctypes.c_int
        get_process_times = kernel32.GetProcessTimes
        get_process_times.argtypes = [
            ctypes.c_void_p,
            ctypes.POINTER(_FILETIME),
            ctypes.POINTER(_FILETIME),
            ctypes.POINTER(_FILETIME),
            ctypes.POINTER(_FILETIME),
        ]
        get_process_times.restype = ctypes.c_int
        handle = open_process(0x1000, 0, int(pid))  # PROCESS_QUERY_LIMITED_INFORMATION
        if not handle:
            error_code = ctypes.get_last_error()
            return (True, "access-denied") if error_code == 5 else (False, "")
        try:
            exit_code = ctypes.c_uint32()
            if not get_exit_code(handle, ctypes.byref(exit_code)) or exit_code.value != 259:
                return False, ""
            creation = _FILETIME()
            exit_time = _FILETIME()
            kernel_time = _FILETIME()
            user_time = _FILETIME()
            if not get_process_times(
                handle,
                ctypes.byref(creation),
                ctypes.byref(exit_time),
                ctypes.byref(kernel_time),
                ctypes.byref(user_time),
            ):
                return True, "unknown"
            identity = f"{creation.high:08x}{creation.low:08x}"
            return True, identity
        finally:
            close_handle(handle)

    try:
        os.kill(pid, 0)
    except PermissionError:
        return True, "access-denied"
    except OSError:
        return False, ""
    try:
        identity = str((Path("/proc") / str(pid)).stat().st_ctime_ns)
    except OSError:
        identity = "unknown"
    return True, identity


def _process_identity(pid: int | None = None) -> str:
    alive, identity = _process_status(int(pid or os.getpid()))
    return identity if alive else ""


def _pid_is_alive(pid: int, expected_identity: str = "") -> bool:
    alive, actual_identity = _process_status(pid)
    if not alive:
        return False
    if expected_identity and actual_identity not in {"", "unknown", "access-denied"}:
        return actual_identity == expected_identity
    return True


@contextlib.contextmanager
def _exclusive_file_lease(
    store: Path,
    filename: str,
    timeout_seconds: float,
    *,
    deadline_at: float | None = None,
) -> Iterator[None]:
    lease_path = store / filename
    token = uuid.uuid4().hex
    deadline = (
        float(deadline_at)
        if deadline_at is not None
        else time.monotonic() + max(0.1, timeout_seconds)
    )
    payload = _canonical_json_bytes({
        "pid": os.getpid(),
        "process_identity": _process_identity(),
        "token": token,
        "created_ns": time.time_ns(),
    })
    while True:
        try:
            _write_fsynced(lease_path, payload)
            break
        except FileExistsError:
            stale = False
            try:
                owner = json.loads(lease_path.read_text(encoding="utf-8"))
                stale = not _pid_is_alive(
                    int(owner.get("pid") or 0),
                    str(owner.get("process_identity") or ""),
                )
            except Exception:
                try:
                    age_seconds = max(0.0, (time.time_ns() - lease_path.stat().st_mtime_ns) / 1_000_000_000)
                except OSError:
                    age_seconds = 0.0
                stale = age_seconds > max(60.0, timeout_seconds * 2)
            if stale:
                with contextlib.suppress(FileNotFoundError, PermissionError):
                    lease_path.unlink()
                continue
            if time.monotonic() >= deadline:
                raise SnapshotWriterBusy(f"snapshot lease is busy: {lease_path}")
            time.sleep(0.01)
    try:
        yield
    finally:
        try:
            owner = json.loads(lease_path.read_text(encoding="utf-8"))
            if owner.get("token") == token:
                lease_path.unlink()
        except (FileNotFoundError, PermissionError, json.JSONDecodeError):
            pass


@contextlib.contextmanager
def _writer_lease(store: Path, timeout_seconds: float = _WRITER_WAIT_SECONDS) -> Iterator[None]:
    with _exclusive_file_lease(store, "WRITE.lock", timeout_seconds):
        yield


@contextlib.contextmanager
def _writer_lease_until(store: Path, deadline_at: float) -> Iterator[None]:
    remaining = float(deadline_at) - time.monotonic()
    if remaining <= 0:
        raise SnapshotDeadlineExceeded("snapshot writer lease deadline exceeded")
    try:
        with _exclusive_file_lease(
            store,
            "WRITE.lock",
            remaining,
            deadline_at=deadline_at,
        ):
            yield
    except SnapshotWriterBusy as exc:
        raise SnapshotDeadlineExceeded("snapshot writer lease deadline exceeded") from exc


@contextlib.contextmanager
def _state_lease(store: Path, timeout_seconds: float = 5.0) -> Iterator[None]:
    with _state_thread_lock(store):
        with _exclusive_file_lease(store, "STATE.lock", timeout_seconds):
            yield


@contextlib.contextmanager
def _state_lease_until(store: Path, deadline_at: float) -> Iterator[None]:
    lock = _state_thread_lock(store)
    remaining = float(deadline_at) - time.monotonic()
    if remaining <= 0 or not lock.acquire(timeout=remaining):
        raise SnapshotDeadlineExceeded("snapshot state lock deadline exceeded")
    try:
        remaining = float(deadline_at) - time.monotonic()
        if remaining <= 0:
            raise SnapshotDeadlineExceeded("snapshot state lock deadline exceeded")
        try:
            with _exclusive_file_lease(
                store,
                "STATE.lock",
                remaining,
                deadline_at=deadline_at,
            ):
                yield
        except SnapshotWriterBusy as exc:
            raise SnapshotDeadlineExceeded("snapshot state lease deadline exceeded") from exc
    finally:
        lock.release()


def _reader_lease_registry_key(store: Path, snapshot_id: str) -> tuple[str, str]:
    return os.path.normcase(str(store.resolve())), snapshot_id


def _acquire_reader_lease(store: Path, snapshot_id: str) -> Path:
    lease_dir = store / "leases" / snapshot_id
    lease_dir.mkdir(parents=True, exist_ok=True)
    registry_key = _reader_lease_registry_key(store, snapshot_id)
    with _ACTIVE_LEASES_LOCK:
        existing_value = _PROCESS_LEASES.get(registry_key)
        if existing_value:
            existing = Path(existing_value)
            if existing.is_file():
                _ACTIVE_LEASE_REFS[existing_value] = _ACTIVE_LEASE_REFS.get(existing_value, 0) + 1
                _ACTIVE_LEASES.add(existing_value)
                return existing
            _PROCESS_LEASES.pop(registry_key, None)
            _ACTIVE_LEASE_REFS.pop(existing_value, None)
            _ACTIVE_LEASES.discard(existing_value)

        lease_id = uuid.uuid4().hex
        lease_path = lease_dir / f"{os.getpid()}-{lease_id}.lease"
        lease_value = str(lease_path)
        lease_payload = _canonical_json_bytes({
            "pid": os.getpid(),
            "process_identity": _process_identity(),
            "lease_id": lease_id,
            "snapshot_id": snapshot_id,
            "created_ns": time.time_ns(),
        })
        _write_fsynced(lease_path, lease_payload)
        _PROCESS_LEASES[registry_key] = lease_value
        _ACTIVE_LEASE_REFS[lease_value] = 1
        _ACTIVE_LEASES.add(lease_value)
        return lease_path


def _release_reader_lease(store: Path, snapshot_id: str, lease_path: Path) -> None:
    lease_value = str(lease_path)
    registry_key = _reader_lease_registry_key(store, snapshot_id)
    should_unlink = False
    with _ACTIVE_LEASES_LOCK:
        references = _ACTIVE_LEASE_REFS.get(lease_value, 0)
        if references > 1:
            _ACTIVE_LEASE_REFS[lease_value] = references - 1
            return
        _ACTIVE_LEASE_REFS.pop(lease_value, None)
        if _PROCESS_LEASES.get(registry_key) == lease_value:
            _PROCESS_LEASES.pop(registry_key, None)
        _ACTIVE_LEASES.discard(lease_value)
        should_unlink = True
    if should_unlink:
        with contextlib.suppress(FileNotFoundError, PermissionError):
            lease_path.unlink()
        # Keep the per-generation lease directory on the reader hot path.
        # Concurrent rmdir/mkdir can raise WinError 183 on Windows even when
        # mkdir uses exist_ok=True; GC removes empty lease directories while
        # holding the cross-process state lease.


def _staging_owner_is_alive(path: Path) -> bool:
    try:
        owner = json.loads((path / "STAMP").read_text(encoding="utf-8"))
        return _pid_is_alive(
            int(owner.get("pid") or 0),
            str(owner.get("process_identity") or ""),
        )
    except (FileNotFoundError, json.JSONDecodeError, TypeError, ValueError):
        match = re.match(r"^(\d+)-", path.name)
        return bool(match and _pid_is_alive(int(match.group(1))))


def _safe_remove_tree(store: Path, target: Path) -> None:
    resolved_store = store.resolve()
    resolved_target = target.resolve(strict=False)
    if resolved_target == resolved_store or not resolved_target.is_relative_to(resolved_store):
        raise SnapshotIntegrityError(f"refusing to remove path outside snapshot store: {target}")
    shutil.rmtree(target)


def _recover_current_pointer(
    store: Path,
    pointer_error: SnapshotError,
    *,
    deadline_at: float | None = None,
) -> dict[str, str]:
    sequenced: list[tuple[int, str, str]] = []
    legacy: list[tuple[str, str]] = []
    for generation in (store / "generations").iterdir():
        _check_deadline(deadline_at, "snapshot CURRENT recovery")
        if not generation.is_dir() or not _SNAPSHOT_ID_RE.fullmatch(generation.name):
            continue
        try:
            if (generation / READY_FILENAME).read_text(encoding="ascii").strip() != generation.name:
                continue
            manifest = _load_manifest(
                store,
                generation.name,
                deadline_at=deadline_at,
            )
            identity = dict(manifest)
            identity.pop("snapshot_id", None)
            _check_deadline(deadline_at, "snapshot generation identity validation")
            if f"v1-{_sha256_bytes(_canonical_json_bytes(identity))}" != generation.name:
                continue
            _check_deadline(deadline_at, "snapshot manifest hash validation")
            manifest_sha256 = _sha256_bytes(
                (generation / MANIFEST_FILENAME).read_bytes()
            )
            publication_sequence = manifest.get("publication_sequence")
            if publication_sequence is None:
                legacy.append((generation.name, manifest_sha256))
            elif isinstance(publication_sequence, int) and publication_sequence > 0:
                sequenced.append((publication_sequence, generation.name, manifest_sha256))
            else:
                continue
        except SnapshotDeadlineExceeded:
            raise
        except Exception:
            # One malformed generation must not prevent recovery from another.
            continue

    if sequenced:
        highest_sequence = max(candidate[0] for candidate in sequenced)
        newest = [candidate for candidate in sequenced if candidate[0] == highest_sequence]
        if len(newest) != 1:
            raise SnapshotUnrecoverableError(
                f"CURRENT is corrupt and publication sequence {highest_sequence} is ambiguous"
            ) from pointer_error
        _, snapshot_id, manifest_sha256 = newest[0]
    elif len(legacy) == 1:
        snapshot_id, manifest_sha256 = legacy[0]
    elif len(legacy) > 1:
        raise SnapshotUnrecoverableError(
            "CURRENT is corrupt and multiple legacy generations have no trustworthy publication order"
        ) from pointer_error
    else:
        raise SnapshotUnrecoverableError(
            f"CURRENT is corrupt and no valid READY generation can recover it: {pointer_error}"
        ) from pointer_error

    _replace_current_pointer(store, snapshot_id, manifest_sha256)
    return {"snapshot_id": snapshot_id, "manifest_sha256": manifest_sha256}


def _recover_locked(
    store: Path,
    *,
    deadline_at: float | None = None,
) -> dict[str, Any]:
    _check_deadline(deadline_at, "snapshot recovery")
    _ensure_layout(store)
    removed_staging = 0
    for child in list((store / "staging").iterdir()):
        _check_deadline(deadline_at, "snapshot recovery")
        if child.is_dir() and _staging_owner_is_alive(child):
            continue
        if child.is_dir():
            _safe_remove_tree(store, child)
        else:
            child.unlink()
        removed_staging += 1

    removed_trash = 0
    for child in list((store / "trash").iterdir()):
        _check_deadline(deadline_at, "snapshot recovery")
        if child.is_dir():
            _safe_remove_tree(store, child)
        else:
            child.unlink()
        removed_trash += 1

    for partial in store.glob(".CURRENT*"):
        if partial.name != CURRENT_FILENAME:
            with contextlib.suppress(FileNotFoundError):
                partial.unlink()
    recovered_current = False
    state_lease = (
        _state_lease_until(store, deadline_at)
        if deadline_at is not None else _state_lease(store)
    )
    with state_lease:
        _check_deadline(deadline_at, "snapshot recovery")
        try:
            pointer = _read_current_pointer(store)
            snapshot_id = pointer["snapshot_id"] if pointer else None
            if pointer:
                _load_manifest(
                    store,
                    snapshot_id,
                    expected_sha256=pointer["manifest_sha256"],
                    deadline_at=deadline_at,
                )
        except SnapshotError as exc:
            if isinstance(exc, SnapshotDeadlineExceeded):
                raise
            pointer = _recover_current_pointer(
                store,
                exc,
                deadline_at=deadline_at,
            )
            snapshot_id = pointer["snapshot_id"]
            recovered_current = True
    return {
        "ok": True,
        "snapshot_id": snapshot_id,
        "recovered_current": recovered_current,
        "removed_staging": removed_staging,
        "removed_trash": removed_trash,
    }


def recover_snapshot_store(project_root: str | Path) -> dict[str, Any]:
    root = Path(project_root).resolve()
    store = snapshot_store_path(root)
    with _project_lock(root):
        _ensure_layout(store)
        with _writer_lease(store):
            return _recover_locked(store)


def _load_manifest(
    store: Path,
    snapshot_id: str,
    expected_sha256: str = "",
    *,
    verify_blobs: bool = True,
    deadline_at: float | None = None,
) -> dict[str, Any]:
    _check_deadline(deadline_at, "snapshot manifest validation")
    if not _SNAPSHOT_ID_RE.fullmatch(snapshot_id or ""):
        raise SnapshotIntegrityError(f"invalid snapshot id: {snapshot_id!r}")
    generation = store / "generations" / snapshot_id
    ready_path = generation / READY_FILENAME
    if not ready_path.is_file():
        raise SnapshotUnavailable(f"snapshot generation is not READY: {snapshot_id}")
    try:
        ready_id = ready_path.read_text(encoding="ascii").strip()
    except (OSError, UnicodeError) as exc:
        raise SnapshotIntegrityError(f"snapshot READY is invalid: {snapshot_id}") from exc
    if ready_id != snapshot_id:
        raise SnapshotIntegrityError(f"snapshot READY id mismatch: {snapshot_id}")
    manifest_path = generation / MANIFEST_FILENAME
    try:
        manifest_bytes = manifest_path.read_bytes()
    except FileNotFoundError as exc:
        raise SnapshotUnavailable(f"snapshot manifest is missing: {snapshot_id}") from exc
    _check_deadline(deadline_at, "snapshot manifest hash")
    manifest_sha256 = _sha256_bytes(manifest_bytes)
    if expected_sha256 and manifest_sha256 != expected_sha256:
        raise SnapshotIntegrityError(f"snapshot manifest hash mismatch: {snapshot_id}")
    try:
        manifest = json.loads(manifest_bytes.decode("utf-8"))
    except Exception as exc:
        raise SnapshotIntegrityError(f"snapshot manifest is invalid JSON: {snapshot_id}: {exc}") from exc
    if not isinstance(manifest, dict):
        raise SnapshotIntegrityError(f"snapshot manifest is not an object: {snapshot_id}")
    if manifest.get("snapshot_id") != snapshot_id:
        raise SnapshotIntegrityError(f"snapshot manifest id mismatch: {snapshot_id}")
    if not isinstance(manifest.get("index_authoritative"), bool):
        raise SnapshotIntegrityError(
            f"snapshot manifest index_authoritative is invalid: {snapshot_id}"
        )
    records = manifest.get("files")
    if not isinstance(records, list):
        raise SnapshotIntegrityError(f"snapshot manifest files is not a list: {snapshot_id}")
    if not all(isinstance(record, dict) for record in records):
        raise SnapshotIntegrityError(f"snapshot manifest contains a non-object record: {snapshot_id}")
    _validate_manifest_paths(records)
    if verify_blobs:
        for record in records:
            _check_deadline(deadline_at, "snapshot blob validation")
            digest = str(record.get("blob_sha256") or "")
            blob = _blob_path(store, digest)
            try:
                blob_bytes = blob.read_bytes()
            except FileNotFoundError as exc:
                raise SnapshotIntegrityError(f"snapshot blob missing: {digest}") from exc
            if len(blob_bytes) != int(record.get("size") or 0):
                raise SnapshotIntegrityError(f"snapshot blob size mismatch: {digest}")
            _check_deadline(deadline_at, "snapshot blob hash")
            if _sha256_bytes(blob_bytes) != digest:
                raise SnapshotIntegrityError(f"snapshot blob hash mismatch: {digest}")
    index_path = generation / INDEX_FILENAME
    try:
        index_bytes = index_path.read_bytes()
    except FileNotFoundError as exc:
        raise SnapshotIntegrityError(f"snapshot INDEX missing: {snapshot_id}") from exc
    _check_deadline(deadline_at, "snapshot INDEX hash")
    if _sha256_bytes(index_bytes) != manifest.get("index_sha256"):
        raise SnapshotIntegrityError(f"snapshot INDEX hash mismatch: {snapshot_id}")
    return manifest


def current_snapshot_id(project_root: str | Path) -> str | None:
    store = snapshot_store_path(project_root)
    pointer = _read_current_pointer(store)
    return pointer["snapshot_id"] if pointer else None


def publish_snapshot(
    project_root: str | Path,
    *,
    include_file: Callable[[Path], bool] | None = None,
    skip_dir: Callable[[str], bool] | None = None,
    index: dict[str, Any] | None = None,
    deadline_at: float | None = None,
    changed_paths: dict[str, str] | None = None,
) -> str:
    """Publish a content-addressed snapshot generation.

    changed_paths (fast path): {relative_path: "modified"|"removed"} naming EXACTLY the
    files that changed since the current snapshot. When provided and a current snapshot
    exists, the new generation is built from the prior manifest's records (reused verbatim)
    plus a re-capture of only the changed paths — with NO full tree walk (O(changed) instead
    of O(repo); the write pipeline uses this to keep single-file writes sub-second on large
    repos). It is always safe to pass: the manifest/index validators run before commit, so
    an incompatible prior manifest simply raises and the caller can retry with a full walk.
    changed_paths=None keeps the authoritative full walk (with per-file mtime reuse).
    """
    _check_deadline(deadline_at, "snapshot publication")
    root = Path(project_root).resolve()
    if not root.is_dir():
        raise FileNotFoundError(f"project root does not exist: {root}")
    include_file = include_file or (lambda path: path.is_file())
    skip_dir = skip_dir or _default_skip_dir
    store = snapshot_store_path(root)

    project_lock = (
        _project_lock_until(root, deadline_at)
        if deadline_at is not None else _project_lock(root)
    )
    with project_lock:
        _check_deadline(deadline_at, "snapshot publication")
        _ensure_layout(store)
        writer_lease = (
            _writer_lease_until(store, deadline_at)
            if deadline_at is not None else _writer_lease(store)
        )
        with writer_lease:
            _check_deadline(deadline_at, "snapshot publication")
            _recover_locked(store, deadline_at=deadline_at)
            # Load the current snapshot's manifest ONCE (verify_blobs=False -> no O(repo) blob
            # re-hash) and reuse it for BOTH the next publication_sequence and the per-file
            # reuse below. The old code scanned and re-verified EVERY generation's manifest
            # here (O(generations x repo), growing until GC) purely to find the max sequence —
            # the current snapshot always carries the max, so a single read suffices.
            publication_sequence = 1
            _cur_id = current_snapshot_id(root)
            _cur_manifest: dict[str, Any] | None = None
            if _cur_id:
                try:
                    _cur_manifest = _load_manifest(
                        store, _cur_id, verify_blobs=False, deadline_at=deadline_at
                    )
                    _ps = _cur_manifest.get("publication_sequence")
                    if isinstance(_ps, int) and _ps > 0:
                        publication_sequence = _ps + 1
                except SnapshotDeadlineExceeded:
                    raise
                except SnapshotError:
                    _cur_manifest = None
            staging_id = uuid.uuid4().hex
            staging = store / "staging" / f"{os.getpid()}-{staging_id}"
            staging.mkdir(parents=True)
            promoted_generation: Path | None = None
            try:
                _check_deadline(deadline_at, "snapshot staging")
                _atomic_write(
                    staging / "STAMP",
                    _canonical_json_bytes({
                        "pid": os.getpid(),
                        "process_identity": _process_identity(),
                        "staging_id": staging_id,
                        "state": "BUILDING",
                        "created_ns": time.time_ns(),
                    }),
                )

                # Reuse the current snapshot's per-file records (loaded once above). For the
                # full-walk path a record is reused when a file's (mtime_ns, size) is
                # unchanged (skips the double-read + hash that dominated publish cost, ~10s /
                # 8k files); the fast path below reuses all of them minus the declared changes.
                # The store is content-addressed, so a reused record's blob already exists.
                _prior_by_path: dict[str, dict] = {}
                if _cur_manifest is not None:
                    for _rec in _cur_manifest.get("files", []):
                        _p = _rec.get("path")
                        if isinstance(_p, str):
                            _prior_by_path[_p] = _rec
                _fresh_blobs: set[str] = set()
                captured: list[CapturedFile] = []
                _fast = changed_paths is not None and bool(_prior_by_path)
                if _fast:
                    # Walk-avoidance fast path: reuse every prior manifest record and apply
                    # only the caller-declared changes (re-capture modified/added, drop
                    # removed). No os.walk -> O(changed) instead of O(repo). If the prior
                    # manifest is incompatible with `index`, the validators below raise and
                    # the caller falls back to a full walk, so this can never mis-publish.
                    _records_by_key: dict[str, dict] = {
                        _normalize_relative_path(str(_r.get("path") or "")): dict(_r)
                        for _r in _prior_by_path.values()
                    }
                    for _rel, _kind in changed_paths.items():
                        _check_deadline(deadline_at, "snapshot incremental capture")
                        try:
                            _key = _normalize_relative_path(_rel)
                        except Exception:
                            continue
                        if str(_kind).lower() == "removed":
                            _records_by_key.pop(_key, None)
                            continue
                        _cand = root / _key
                        try:
                            if _cand.is_symlink() or not _cand.resolve().is_relative_to(root):
                                _records_by_key.pop(_key, None)
                                continue
                            if not include_file(_cand):
                                _records_by_key.pop(_key, None)
                                continue
                        except (OSError, ValueError):
                            _records_by_key.pop(_key, None)
                            continue
                        _item = capture_stable_file(root, _cand, deadline_at=deadline_at)
                        _write_blob(store, _item.record["blob_sha256"], _item.payload, deadline_at=deadline_at)
                        _fresh_blobs.add(_item.record["blob_sha256"])
                        _records_by_key[_normalize_relative_path(_item.record["path"])] = _item.record
                    captured = [CapturedFile(record=_r, payload=None) for _r in _records_by_key.values()]
                for walk_root, dirs, files in ([] if _fast else os.walk(root, followlinks=False)):
                    _check_deadline(deadline_at, "snapshot tree walk")
                    walk_path = Path(walk_root)
                    safe_dirs: list[str] = []
                    for dirname in sorted(dirs, key=lambda item: (item.casefold(), item)):
                        _check_deadline(deadline_at, "snapshot tree walk")
                        if skip_dir(dirname):
                            continue
                        candidate = walk_path / dirname
                        try:
                            if candidate.is_symlink() or not candidate.resolve().is_relative_to(root):
                                continue
                        except OSError:
                            continue
                        safe_dirs.append(dirname)
                    dirs[:] = safe_dirs
                    for filename in sorted(files, key=lambda item: (item.casefold(), item)):
                        _check_deadline(deadline_at, "snapshot file capture")
                        candidate = walk_path / filename
                        try:
                            if candidate.is_symlink() or not candidate.resolve().is_relative_to(root):
                                continue
                            if not include_file(candidate):
                                continue
                        except (OSError, ValueError):
                            continue
                        reused = None
                        try:
                            _key = _normalize_relative_path(candidate.relative_to(root).as_posix())
                            _prior_rec = _prior_by_path.get(_key)
                            if _prior_rec is not None:
                                _st = candidate.stat()
                                if (int(_prior_rec.get("mtime_ns") or -1) == _st.st_mtime_ns
                                        and int(_prior_rec.get("size") or -1) == _st.st_size):
                                    _digest = str(_prior_rec.get("blob_sha256") or "")
                                    if _digest and _blob_path(store, _digest).is_file():
                                        # Unchanged file: an equal (mtime_ns, size) means the
                                        # bytes are unchanged, so a fresh capture would yield a
                                        # byte-identical record. Reuse it (blob already stored).
                                        reused = CapturedFile(record=dict(_prior_rec), payload=None)
                        except SnapshotDeadlineExceeded:
                            raise
                        except Exception:
                            reused = None
                        if reused is not None:
                            captured.append(reused)
                        else:
                            item = capture_stable_file(
                                root,
                                candidate,
                                deadline_at=deadline_at,
                            )
                            _write_blob(
                                store,
                                item.record["blob_sha256"],
                                item.payload,
                                deadline_at=deadline_at,
                            )
                            _fresh_blobs.add(item.record["blob_sha256"])
                            captured.append(item)

                _check_deadline(deadline_at, "snapshot manifest build")
                captured.sort(key=lambda item: (item.record["path"].casefold(), item.record["path"]))
                records = [item.record for item in captured]
                _validate_manifest_paths(records)
                stable_index, index_authoritative = _validate_index_against_records(index, records)
                index_bytes = _canonical_json_bytes(stable_index)
                _check_deadline(deadline_at, "snapshot index hash")
                index_sha256 = _sha256_bytes(index_bytes)
                identity = {
                    "schema_version": SNAPSHOT_SCHEMA_VERSION,
                    "publication_sequence": publication_sequence,
                    "project_root": str(root),
                    "files": records,
                    "index_sha256": index_sha256,
                    "index_authoritative": index_authoritative,
                }
                snapshot_id = f"v1-{_sha256_bytes(_canonical_json_bytes(identity))}"
                manifest = dict(identity)
                manifest["snapshot_id"] = snapshot_id
                manifest_bytes = _canonical_json_bytes(manifest)
                _check_deadline(deadline_at, "snapshot manifest hash")
                manifest_sha256 = _sha256_bytes(manifest_bytes)

                _check_deadline(deadline_at, "snapshot staging")
                _atomic_write(staging / MANIFEST_FILENAME, manifest_bytes)
                _check_deadline(deadline_at, "snapshot staging")
                _atomic_write(staging / INDEX_FILENAME, index_bytes)
                _check_deadline(deadline_at, "snapshot staging")
                _atomic_write(staging / READY_FILENAME, (snapshot_id + "\n").encode("ascii"))

                generation = store / "generations" / snapshot_id
                if generation.exists():
                    existing = (generation / MANIFEST_FILENAME).read_bytes()
                    if _sha256_bytes(existing) != manifest_sha256:
                        raise SnapshotIntegrityError(f"snapshot id collision for {snapshot_id}")
                    _safe_remove_tree(store, staging)
                else:
                    _check_deadline(deadline_at, "snapshot publication commit")
                    _strict_replace(staging, generation, replace_existing=False)
                    promoted_generation = generation
                # Commit-time integrity gate. _load_manifest's default full blob re-hash is
                # O(repo) read+hash — redundant here because blobs are content-addressed and
                # written atomically (_write_blob). Keep the cheap structural checks (READY
                # marker, manifest id, INDEX hash) via verify_blobs=False, then re-hash ONLY
                # the blobs freshly written by this publish. Reused blobs are trusted: their
                # existence was checked at reuse time in the capture loop (and they were
                # content-verified when first published), so no O(repo) sweep is needed.
                _load_manifest(
                    store,
                    snapshot_id,
                    expected_sha256=manifest_sha256,
                    verify_blobs=False,
                    deadline_at=deadline_at,
                )
                for _record in records:
                    _digest = str(_record.get("blob_sha256") or "")
                    if _digest not in _fresh_blobs:
                        continue
                    _check_deadline(deadline_at, "snapshot blob verify")
                    _blob = _blob_path(store, _digest)
                    try:
                        _blob_bytes = _blob.read_bytes()
                    except FileNotFoundError as exc:
                        raise SnapshotIntegrityError(f"snapshot blob missing: {_digest}") from exc
                    if len(_blob_bytes) != int(_record.get("size") or 0):
                        raise SnapshotIntegrityError(f"snapshot blob size mismatch: {_digest}")
                    if _sha256_bytes(_blob_bytes) != _digest:
                        raise SnapshotIntegrityError(f"snapshot blob hash mismatch: {_digest}")
                _check_deadline(deadline_at, "snapshot CURRENT commit")
                _replace_current_pointer(store, snapshot_id, manifest_sha256)
                promoted_generation = None
                return snapshot_id
            finally:
                if staging.exists():
                    _safe_remove_tree(store, staging)
                if promoted_generation is not None and promoted_generation.exists():
                    _safe_remove_tree(store, promoted_generation)


@dataclass
class SnapshotPin:
    project_root: Path
    store: Path
    snapshot_id: str
    manifest: dict[str, Any]
    lease_path: Path
    generation_path: Path
    _released: bool = False
    _records_by_key: dict[str, dict[str, Any]] = field(default_factory=dict)

    def __post_init__(self) -> None:
        self._records_by_key = {
            _path_lookup_key(record["path"]): record
            for record in self.manifest.get("files", [])
        }

    def __enter__(self) -> "SnapshotPin":
        return self

    def __exit__(self, _exc_type, _exc, _traceback) -> None:
        self.release()

    def release(self) -> None:
        if self._released:
            return
        self._released = True
        _release_reader_lease(self.store, self.snapshot_id, self.lease_path)

    def read_bytes(self, relative_path: str) -> bytes:
        if self._released:
            raise SnapshotUnavailable(f"snapshot pin already released: {self.snapshot_id}")
        record = self._records_by_key.get(_path_lookup_key(relative_path))
        if record is None:
            raise FileNotFoundError(relative_path)
        payload = _blob_path(self.store, record["blob_sha256"]).read_bytes()
        if len(payload) != int(record["size"]) or _sha256_bytes(payload) != record["sha256"]:
            raise SnapshotIntegrityError(
                f"snapshot blob verification failed: {self.snapshot_id}:{record['path']}"
            )
        return payload

    def read_index(self) -> dict[str, Any]:
        if self._released:
            raise SnapshotUnavailable(f"snapshot pin already released: {self.snapshot_id}")
        try:
            index_bytes = (self.generation_path / INDEX_FILENAME).read_bytes()
        except FileNotFoundError as exc:
            raise SnapshotIntegrityError(f"snapshot INDEX missing: {self.snapshot_id}") from exc
        if _sha256_bytes(index_bytes) != self.manifest.get("index_sha256"):
            raise SnapshotIntegrityError(f"snapshot INDEX hash mismatch: {self.snapshot_id}")
        try:
            payload = json.loads(index_bytes.decode("utf-8"))
        except Exception as exc:
            raise SnapshotIntegrityError(f"snapshot INDEX parse failed: {self.snapshot_id}: {exc}") from exc
        if not isinstance(payload, dict):
            raise SnapshotIntegrityError(f"snapshot INDEX is not an object: {self.snapshot_id}")
        return payload

    def iter_records(self, base: str | Path | None = None) -> Iterator[dict[str, Any]]:
        if base is None:
            yield from self.manifest.get("files", [])
            return
        base_path = Path(base)
        if not base_path.is_absolute():
            base_path = self.project_root / base_path
        base_path = base_path.resolve(strict=False)
        if not base_path.is_relative_to(self.project_root):
            raise SnapshotIntegrityError(f"search base escapes snapshot root: {base_path}")
        relative = base_path.relative_to(self.project_root).as_posix()
        if relative in {"", "."}:
            yield from self.manifest.get("files", [])
            return
        normalized = _normalize_relative_path(relative)
        exact_key = normalized.casefold()
        prefix = exact_key.rstrip("/") + "/"
        for record in self.manifest.get("files", []):
            key = record["path"].casefold()
            if key == exact_key or key.startswith(prefix):
                yield record


def pin_current_snapshot(
    project_root: str | Path,
    expected_id: str | None = None,
    *,
    deadline_at: float | None = None,
) -> SnapshotPin:
    root = Path(project_root).resolve()
    store = snapshot_store_path(root)
    _ensure_layout(store)
    last_error: Exception | None = None
    for _ in range(3):
        lease_path: Path | None = None
        try:
            state_lease = (
                _state_lease_until(store, deadline_at)
                if deadline_at is not None else _state_lease(store)
            )
            with state_lease:
                pointer = _read_current_pointer(store)
                if pointer is None:
                    raise SnapshotUnavailable(f"no CURRENT snapshot for {root}")
                snapshot_id = pointer["snapshot_id"]
                if expected_id is not None and snapshot_id != expected_id:
                    raise SnapshotUnavailable(
                        f"CURRENT changed: expected {expected_id}, found {snapshot_id}"
                    )
                lease_path = _acquire_reader_lease(store, snapshot_id)

            try:
                manifest = _load_manifest(
                    store,
                    snapshot_id,
                    expected_sha256=pointer["manifest_sha256"],
                    verify_blobs=False,
                    deadline_at=deadline_at,
                )
            except Exception:
                _release_reader_lease(store, snapshot_id, lease_path)
                raise
            return SnapshotPin(
                project_root=root,
                store=store,
                snapshot_id=snapshot_id,
                manifest=manifest,
                lease_path=lease_path,
                generation_path=store / "generations" / snapshot_id,
            )
        except SnapshotDeadlineExceeded:
            raise
        except (FileNotFoundError, SnapshotUnavailable) as exc:
            last_error = exc
            if expected_id is not None:
                break
            continue
    if last_error:
        raise SnapshotUnavailable(str(last_error)) from last_error
    raise SnapshotUnavailable(f"unable to pin CURRENT snapshot for {root}")


def _generation_has_live_lease(store: Path, snapshot_id: str) -> bool:
    lease_dir = store / "leases" / snapshot_id
    if not lease_dir.exists():
        return False
    live = False
    for lease in list(lease_dir.glob("*.lease")):
        with _ACTIVE_LEASES_LOCK:
            if str(lease) in _ACTIVE_LEASES:
                live = True
                continue
        try:
            payload = json.loads(lease.read_text(encoding="utf-8"))
            pid = int(payload.get("pid") or 0)
            process_identity = str(payload.get("process_identity") or "")
            lease_id = str(payload.get("lease_id") or "")
            payload_snapshot_id = str(payload.get("snapshot_id") or "")
            if (
                pid <= 0
                or not process_identity
                or not re.fullmatch(r"[0-9a-f]{32}", lease_id)
                or payload_snapshot_id != snapshot_id
            ):
                raise ValueError("invalid snapshot lease payload")
        except FileNotFoundError:
            continue
        except (json.JSONDecodeError, TypeError, ValueError):
            try:
                age_seconds = max(
                    0.0,
                    (time.time_ns() - lease.stat().st_mtime_ns) / 1_000_000_000,
                )
            except OSError:
                continue
            if age_seconds <= 60.0:
                live = True
                continue
            with contextlib.suppress(FileNotFoundError, PermissionError):
                lease.unlink()
            continue
        if _pid_is_alive(pid, process_identity):
            live = True
            continue
        with contextlib.suppress(FileNotFoundError, PermissionError):
            lease.unlink()
    with contextlib.suppress(OSError):
        lease_dir.rmdir()
    return live


def collect_garbage(project_root: str | Path, keep_last: int = 10) -> dict[str, Any]:
    root = Path(project_root).resolve()
    store = snapshot_store_path(root)
    with _project_lock(root):
        _ensure_layout(store)
        with _writer_lease(store):
            _recover_locked(store)
            retired: list[tuple[str, Path]] = []
            pinned: list[str] = []
            with _state_lease(store):
                pointer = _read_current_pointer(store)
                current = pointer["snapshot_id"] if pointer else None
                if pointer:
                    # Confirm CURRENT is loadable + its manifest hash matches; the O(repo)
                    # blob re-hash (verify_blobs default) is not needed just to gate GC.
                    _load_manifest(
                        store,
                        current,
                        expected_sha256=pointer["manifest_sha256"],
                        verify_blobs=False,
                    )
                generations = [
                    path for path in (store / "generations").iterdir()
                    if path.is_dir() and _SNAPSHOT_ID_RE.fullmatch(path.name)
                ]
                generations.sort(key=lambda path: path.stat().st_mtime_ns, reverse=True)
                retained = {path.name for path in generations[:max(0, int(keep_last))]}
                if current:
                    retained.add(current)
                for generation in generations:
                    if generation.name in retained:
                        continue
                    if _generation_has_live_lease(store, generation.name):
                        pinned.append(generation.name)
                        continue
                    trash_path = store / "trash" / f"{generation.name}-{uuid.uuid4().hex}"
                    _strict_replace(generation, trash_path, replace_existing=False)
                    retired.append((generation.name, trash_path))

            for _snapshot_id, trash_path in retired:
                if trash_path.exists():
                    _safe_remove_tree(store, trash_path)

            referenced_blobs: set[str] = set()
            for generation in (store / "generations").iterdir():
                if not generation.is_dir() or not _SNAPSHOT_ID_RE.fullmatch(generation.name):
                    continue
                # Only the blob_sha256 reference list is needed to decide what to keep, so
                # skip the O(repo) blob re-hash (verify_blobs default). Re-verifying every
                # blob of every generation on every publish was the dominant, generation-
                # count-scaling cost of the write path.
                manifest = _load_manifest(store, generation.name, verify_blobs=False)
                referenced_blobs.update(
                    str(record["blob_sha256"])
                    for record in manifest.get("files", [])
                )

            blobs_deleted = 0
            blob_root = store / "blobs" / "sha256"
            for prefix in list(blob_root.iterdir()):
                if not prefix.is_dir():
                    continue
                for blob in list(prefix.iterdir()):
                    if (
                        blob.is_file()
                        and re.fullmatch(r"[0-9a-f]{64}", blob.name)
                        and blob.name not in referenced_blobs
                    ):
                        with contextlib.suppress(FileNotFoundError):
                            blob.unlink()
                            blobs_deleted += 1
                with contextlib.suppress(OSError):
                    prefix.rmdir()

            return {
                "ok": True,
                "current": current,
                "deleted": [snapshot_id for snapshot_id, _path in retired],
                "retained_pinned": pinned,
                "blobs_deleted": blobs_deleted,
            }
