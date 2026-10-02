"""Shared filesystem helpers for orchestrator tool modules.

This module does not import code_tools. Tool implementations can depend on it
without creating a code_tools -> orchestrator.tools -> code_tools cycle.
"""
from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import os
import threading
import time
from pathlib import Path
from typing import Any

import surgical_mapper

SKIP_DIRS = surgical_mapper.SKIP_DIRS
MAX_READ_LINES = 200
MAX_READ_LINES_HARD_CAP = 800
MAX_SEARCH_RESULTS = 50
MAX_SEARCH_CONTEXT_LINES = 1


def is_unc_path_like(value: Any) -> bool:
    try:
        s = str(value).strip()
    except Exception:
        return False
    return s.startswith("\\\\") or s.startswith("//")


def should_skip_dir(name: str, project_root=None) -> bool:
    # B027: project_root is optional and forwarded. This wrapper is what the walk
    # sites actually call, so the per-project overlay has to reach through it --
    # patching only the leaf module would have left every real caller on the
    # global-only path.
    return surgical_mapper.should_skip_dir(name, project_root)


def should_scan_for_text(file_path: Path, project_root=None) -> bool:
    # B027f 2026-08-23. `project_root` is OPTIONAL and forwarded, for the same
    # reason `should_skip_dir` above takes it: this is the FILE half of one
    # question, and it was the last gate that still answered from the global
    # rules alone. Measured before the fix, on a project whose overlay says
    # `!build`: should_skip_dir opened the directory (False), _skip_reason
    # opened the file (None), and this wrapper closed it again (False), so
    # `!build` reached INDEX.json and list_structure but never search_code.
    #
    # Binding it also removes an ORDER DEPENDENCE that predates B027: R82 bound
    # the root at the refresh publish so deep data-JSON is judged by depth, while
    # the search-first fallback publish was unbound -- depth unknown, file kept.
    # Same project, same query, two answers depending on who published first.
    try:
        return surgical_mapper._should_scan_file(file_path, project_root)
    except Exception:
        return file_path.suffix.lower() in surgical_mapper.SUPPORTED_EXTENSIONS


def text_file_extensions() -> set[str]:
    text_exts = set(getattr(surgical_mapper, "SUPPORTED_EXTENSIONS", set()))
    text_exts.update({".txt", ".md", ".json", ".html", ".css", ".csv", ".xml", ".yaml", ".yml", ".log"})
    return text_exts


def detect_text_encoding(file_path: Path, sample_size: int = 4096) -> str:
    try:
        with file_path.open("rb") as stream:
            sample = stream.read(sample_size)
    except Exception:
        return "utf-8"
    if sample.startswith((b"\xff\xfe", b"\xfe\xff")):
        return "utf-16"
    if sample.startswith(b"\xef\xbb\xbf"):
        return "utf-8-sig"
    return "utf-8"


def is_binary_file(file_path: Path, sample_size: int = 4096, allow_utf16_text: bool = False) -> bool:
    try:
        with file_path.open("rb") as stream:
            sample = stream.read(sample_size)
    except Exception:
        return False
    text_exts = text_file_extensions()
    if sample.startswith((b"\xff\xfe", b"\xfe\xff")):
        return not (allow_utf16_text and file_path.suffix.lower() in text_exts)
    if b"\x00" in sample:
        return True
    if file_path.suffix.lower() in text_exts:
        return False
    try:
        sample.decode("utf-8")
        return False
    except UnicodeDecodeError:
        return True


def resolve_project_file(project_path: str | None, file_path: str) -> Path:
    if project_path and is_unc_path_like(project_path):
        raise ValueError("UNC project_path is unsupported for project guard")
    if is_unc_path_like(file_path):
        if project_path:
            raise ValueError("UNC file_path with project_path cannot be guarded relative to root")
        return Path(file_path)
    fp = Path(file_path)
    if fp.is_absolute():
        resolved = fp.resolve()
        if project_path:
            root = Path(project_path).resolve()
            try:
                resolved.relative_to(root)
            except ValueError:
                raise ValueError(f"File path '{file_path}' is outside project root '{project_path}'.") from None
        return resolved
    if not project_path:
        return fp.resolve()
    root = Path(project_path).resolve()
    resolved = (root / fp).resolve()
    try:
        resolved.relative_to(root)
    except ValueError:
        raise ValueError(f"File path escapes project root: {file_path}") from None
    return resolved


def project_cache_dir(project_path: str) -> Path:
    if is_unc_path_like(project_path):
        raise ValueError("UNC project_path does not support project cache auto-infer")
    return Path(project_path).resolve() / ".orchestrator_cache"


def load_index(project_path: str) -> dict:
    index_path = project_cache_dir(project_path) / "INDEX.json"
    if not index_path.exists():
        return {}
    return json.loads(index_path.read_text(encoding="utf-8"))


def atomic_write_bytes(path: Path, data: bytes) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.tmp")
    tmp.write_bytes(data)
    os.replace(str(tmp), str(path))


def atomic_write_text(path: Path, text: str, encoding: str = "utf-8") -> None:
    atomic_write_bytes(path, text.encode(encoding))


def sha256_file(path: Path) -> str | None:
    if not path.exists():
        return None
    return hashlib.sha256(path.read_bytes()).hexdigest()


def json_error(message: str, **extra) -> str:
    data = {"ok": False, "error": str(message or "unexpected internal error")}
    data.update(extra)
    data.setdefault("error_name", "INTERNAL_ERROR")
    data.setdefault("type", "INTERNAL_ERROR")
    data.setdefault("severity", "error")
    data.setdefault("retryable", False)
    data.setdefault("ca_next_action", "inspect_error_context_or_report_backend_issue")
    return json.dumps(data, ensure_ascii=False)


def decode_text_payload(raw_content: str, content_encoding: str | None) -> tuple[str | None, dict, dict | None]:
    encoding = (content_encoding or "plain").strip().lower()
    if encoding == "none":
        encoding = "plain"
    if encoding not in {"plain", "base64"}:
        return None, {}, {
            "ok": False,
            "error": f"Unsupported content_encoding: {content_encoding}",
            "error_name": "INVALID_CONTENT_ENCODING",
            "type": "SCHEMA_VIOLATION",
            "retryable": False,
            "ca_next_action": "use_plain_or_base64_content_encoding",
        }
    try:
        text = base64.b64decode(raw_content, validate=True).decode("utf-8") if encoding == "base64" else raw_content
    except Exception as exc:
        return None, {}, {
            "ok": False,
            "error": f"Invalid {encoding} payload: {exc}",
            "error_name": "INVALID_BASE64" if encoding == "base64" else "INVALID_TEXT_PAYLOAD",
            "type": "SCHEMA_VIOLATION",
            "retryable": True,
            "ca_next_action": "resend_content_as_valid_utf8_base64",
        }
    return text, {"encoding_used": "utf-8" if encoding == "plain" else "base64:utf-8", "utf8_valid": True}, None


def find_project_root(file_path: str) -> str | None:
    if not file_path or not str(file_path).strip() or is_unc_path_like(file_path):
        return None
    path = Path(file_path).resolve()
    # campaign-1608: never treat the user HOME dir, an ancestor of HOME, or a
    # filesystem/drive root as a project root. A stray marker there (e.g. a junk
    # .orchestrator_cache / package.json in C:\Users\<user>) would otherwise
    # hijack root inference for tempfile paths (they live under HOME via %TEMP%),
    # pointing writes at a mislocated unbounded snapshot store and hanging the
    # write pipeline. Return None so the caller's resolve chain falls through.
    # (kept byte-for-byte in sync with code_tools.find_project_root.)
    try:
        _home = Path.home().resolve()
    except Exception:
        _home = None
    for parent in [path] + list(path.parents):
        if not parent.exists():
            continue
        if parent == parent.parent:  # filesystem/drive root (e.g. C:\ or D:\)
            continue
        if _home is not None:
            if parent == _home:
                continue
            try:
                _home.relative_to(parent)  # parent is an ancestor of HOME
                continue
            except ValueError:
                pass
        if (
            (parent / ".orchestrator_cache").exists()
            or (parent / "package.json").exists()
            or (parent / "requirements.txt").exists()
            or (parent / ".git").exists()
        ):
            return str(parent)
    return None


def index_delta(project_path: str) -> dict | None:
    try:
        root = Path(project_path).resolve()
        index = load_index(str(root))
    except Exception:
        return None
    indexed = {
        str(k).replace("\\", "/"): v
        for k, v in (index.get("files", {}) if isinstance(index, dict) else {}).items()
        if isinstance(v, dict)
    }
    added: list[str] = []
    modified: list[str] = []
    deleted: list[str] = []
    for rel, entry in indexed.items():
        fp = root / rel
        if not fp.exists():
            deleted.append(rel)
            continue
        indexed_mtime = float(entry.get("mtime") or 0)
        stat = fp.stat()
        size_changed = int(entry.get("size_bytes") or -1) != stat.st_size
        mtime_changed = bool(indexed_mtime and abs(stat.st_mtime - indexed_mtime) > 0.5)
        # Anh 2026-08-10: fast path. Only fall back to the authoritative MD5 (which
        # reads the WHOLE file) when a cheap stat already signals a change. A write
        # that leaves BOTH size and mtime identical is not observable in practice,
        # so skipping the hash for unchanged-stat files keeps index_delta O(stat)
        # instead of O(read+hash every file) — the latter is what pinned the event
        # loop for minutes on this large repo (find_symbol/find_callers hang).
        # P10 2026-10-02: the hash itself now goes through the ONE shared matcher,
        # surgical_mapper.get_file_hash_stat_gated -- a file cache_delta already
        # hashed (memo on st_size/st_mtime_ns/st_ino) is never re-read here, and a
        # copy/replace (new inode on NTFS) is re-hashed even when the INDEX-recorded
        # size+mtime happen to match. The stat-vs-INDEX short-circuit above still
        # keeps a cold process O(stat) for untouched files.
        hash_changed = False
        hash_value = entry.get("hash")
        if (size_changed or mtime_changed) and hash_value:
            try:
                hash_changed = surgical_mapper.get_file_hash_stat_gated(str(fp)) != hash_value
            except Exception:
                hash_changed = False
        if size_changed or mtime_changed or hash_changed:
            modified.append(rel)
    for scan_root, dirs, files in os.walk(root):
        dirs[:] = [d for d in dirs if not should_skip_dir(d, root)]
        for name in files:
            fp = Path(scan_root) / name
            # B027f: bound, because the dirs[:] filter one line above already is.
            # Bound directory gate + unbound file gate = an un-skipped file gets
            # walked and then dropped, so it never shows up as `added` and the
            # index never learns it exists.
            if not should_scan_for_text(fp, root):
                continue
            rel = str(fp.relative_to(root)).replace("\\", "/")
            if rel not in indexed:
                added.append(rel)
    return {"ok": True, "added": added[:200], "modified": modified[:200], "deleted": deleted[:200]}


# Anh 2026-08-10: short-TTL dedupe for index_delta. Every find_symbol/find_callers/
# validate call probes freshness; under concurrency N callers each walked the whole
# repo (N redundant full-tree stat scans) — the machine-saturation half of the hang.
# The delta is identical for concurrent callers on the same repo, so one caller
# computes it and the rest (within the TTL) reuse the result. A per-project compute
# lock collapses a thundering herd of misses into a single scan.
_INDEX_DELTA_TTL_SECONDS = 1.0
_INDEX_DELTA_CACHE: dict[str, tuple[float, dict | None]] = {}
_INDEX_DELTA_LOCKS: dict[str, threading.Lock] = {}
_INDEX_DELTA_REGISTRY_LOCK = threading.Lock()


def _cached_index_delta(project_path: str) -> dict | None:
    now = time.monotonic()
    with _INDEX_DELTA_REGISTRY_LOCK:
        hit = _INDEX_DELTA_CACHE.get(project_path)
        if hit and hit[0] > now:
            return hit[1]
        lock = _INDEX_DELTA_LOCKS.setdefault(project_path, threading.Lock())
    with lock:  # only one thread per project recomputes; peers reuse the fresh entry
        with _INDEX_DELTA_REGISTRY_LOCK:
            hit = _INDEX_DELTA_CACHE.get(project_path)
            if hit and hit[0] > time.monotonic():
                return hit[1]
        result = index_delta(project_path)
        with _INDEX_DELTA_REGISTRY_LOCK:
            _INDEX_DELTA_CACHE[project_path] = (time.monotonic() + _INDEX_DELTA_TTL_SECONDS, result)
        return result


async def ensure_fresh_index(project_path: str) -> str | None:
    if not project_path:
        return None
    # Anh 2026-08-10: index_delta walks + stats the whole repo (and MD5s changed
    # files). Run it OFF the event loop so a stale-index probe never pins the loop
    # — previously find_symbol/find_callers/search blocked the loop for minutes
    # ("livez_ok_health_hung") because this ran synchronously inside the coroutine.
    # The short-TTL cache further collapses concurrent probes into one repo scan.
    delta = await asyncio.to_thread(_cached_index_delta, project_path)
    if not delta or not delta.get("ok"):
        return None
    if delta.get("added") or delta.get("modified") or delta.get("deleted"):
        return json_error(
            "Index stale; re-seed or cache_delta before symbol validation. Never refresh_project_map on a large root such as [A:P].",
            error_name="STALE_INDEX",
            category="stale_index",
            drift=delta,
            hint="Call cache_delta(project_path) or read_file_range(..., force_reseed=true), then retry. Never refresh_project_map on a large root such as [A:P].",
            ca_next_action="cache_delta",
        )
    return None
