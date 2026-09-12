"""P7 cached opt-in Heavy enrich — cache-READ only.

Flags default OFF and stay OFF after install. This module never cold-computes,
reindexes, or spawns a subprocess. Flag-OFF returns skipped:disabled without
touching the cache.

Lookup key = (kind, fingerprint). H3 later uses the precommit staged-content
hash as the fingerprint. TTL = 60s. Cache miss / stale / mismatch => skipped
plus a reason, never a fabricated payload.
"""
from __future__ import annotations

import hashlib
import os
import time
from typing import Any, Mapping, MutableMapping

from .config import (
    COUNCIL_CONTEXT_MAX_FILES,
    COUNCIL_CONTEXT_MAX_SYMBOLS,
    enrich_commit_enabled,
    enrich_council_enabled,
    enrich_test_enabled,
)

KIND_TEST = "test"
KIND_COMMIT = "commit"
KIND_COUNCIL = "council"

CACHE_TTL_S = 60.0
PAYLOAD_VERSION = 1
ALGORITHM_NAME = "hooks-cache-v1"

_STORE: dict[Any, dict[str, Any]] = {}


def scoped_fingerprint(project_root: str, fingerprint: str) -> str:
    """Namespace Heavy cache keys by physical project root."""
    root = os.path.normcase(os.path.realpath(str(project_root or "")))
    raw = f"{root}\0{str(fingerprint or '').strip()}"
    return hashlib.sha256(raw.encode("utf-8", errors="replace")).hexdigest()


def _skipped(reason: str, **extra: Any) -> dict[str, Any]:
    out: dict[str, Any] = {"status": "skipped", "reason": reason, "payload": None}
    out.update(extra)
    return out


def _flag_for(kind: str) -> bool:
    if kind == KIND_TEST:
        return enrich_test_enabled()
    if kind == KIND_COMMIT:
        return enrich_commit_enabled()
    if kind == KIND_COUNCIL:
        return enrich_council_enabled()
    return False


def lookup_enrich(
    kind: str,
    fingerprint: str,
    *,
    now: float | None = None,
    cache: MutableMapping[Any, Any] | None = None,
) -> dict[str, Any]:
    """Read-only cache lookup. Never computes, never reindexes.

    Returns status=ok + payload on a matching fresh hit; otherwise
    status=skipped with reason disabled|cache_miss|cache_stale|
    fingerprint_mismatch|lookup_error.
    """
    try:
        if not _flag_for(kind):
            return _skipped("disabled")
        fp = str(fingerprint or "").strip()
        if not fp:
            return _skipped("cache_miss")
        store = _STORE if cache is None else cache
        key = (kind, fp)
        if key in store:
            entry = store[key]
        elif fp in store:
            entry = store[fp]
        else:
            return _skipped("cache_miss")
        if not isinstance(entry, Mapping):
            return _skipped("cache_miss")
        entry_fp = str(entry.get("fingerprint") or "")
        if entry_fp and entry_fp != fp:
            return _skipped("fingerprint_mismatch")
        stored_at = entry.get("stored_at")
        if stored_at is None:
            return _skipped("cache_stale")
        t = time.monotonic() if now is None else float(now)
        age = t - float(stored_at)
        if age < 0 or age > CACHE_TTL_S:
            return _skipped("cache_stale")
        payload = entry.get("payload")
        if payload is None:
            return _skipped("cache_miss")
        return {
            "status": "ok",
            "reason": None,
            "payload": payload,
            "kind": kind,
            "fingerprint": fp,
            "age_s": age,
        }
    except Exception:
        return _skipped("lookup_error")


def put_cache(
    kind: str,
    fingerprint: str,
    payload: Any,
    *,
    stored_at: float | None = None,
    cache: MutableMapping[Any, Any] | None = None,
    extra: Mapping[str, Any] | None = None,
) -> None:
    """Store a ready payload. Not a compute path — caller already has the bytes."""
    store = _STORE if cache is None else cache
    entry: dict[str, Any] = {
        "fingerprint": str(fingerprint),
        "stored_at": time.monotonic() if stored_at is None else float(stored_at),
        "payload": payload,
        "version": PAYLOAD_VERSION,
    }
    if extra:
        entry.update(dict(extra))
    store[(kind, str(fingerprint))] = entry


def clear_cache(cache: MutableMapping[Any, Any] | None = None) -> None:
    store = _STORE if cache is None else cache
    store.clear()


def _explicit_test_files(result: Mapping[str, Any]) -> tuple[list[str] | None, str]:
    """Return explicit executed-test-file metadata only.

    Unknown / missing / non-list => (None, test_selection_unknown).
    Never scrape stdout. Accepts additive optional field
    ``executed_test_files`` (list[str]).
    """
    if "executed_test_files" not in result:
        return None, "test_selection_unknown"
    raw = result.get("executed_test_files")
    if raw is None:
        return None, "test_selection_unknown"
    if not isinstance(raw, (list, tuple)):
        return None, "test_selection_unknown"
    files: list[str] = []
    for item in raw:
        if not isinstance(item, str) or not item.strip():
            return None, "test_selection_unknown"
        files.append(item.strip())
    return files, "explicit"


def enrich_test_result(
    result: Mapping[str, Any],
    *,
    cache: MutableMapping[Any, Any] | None = None,
    now: float | None = None,
) -> dict[str, Any]:
    """H2: attach test_gap advisory without mutating original runner fields.

    Flag-OFF / miss / unknown selection => skipped reason. Original keys
    stay byte/value-equivalent; only ``test_gap`` is added when enabled.
    """
    out = dict(result)
    if not enrich_test_enabled():
        return out
    files, sel = _explicit_test_files(result)
    if sel == "test_selection_unknown" or files is None:
        out["test_gap"] = _skipped("test_selection_unknown")
        return out
    fp = str(result.get("enrich_fingerprint") or result.get("script_hash") or "").strip()
    if not fp:
        out["test_gap"] = _skipped("cache_miss")
        return out
    hit = lookup_enrich(KIND_TEST, fp, now=now, cache=cache)
    if hit.get("status") != "ok":
        out["test_gap"] = _skipped(str(hit.get("reason") or "cache_miss"))
        return out
    payload = hit.get("payload")
    if not isinstance(payload, Mapping):
        out["test_gap"] = _skipped("cache_miss")
        return out
    advisory = dict(payload)
    advisory["executed_test_files"] = list(files)
    advisory["selection"] = "explicit"
    out["test_gap"] = {
        "status": "ok",
        "reason": None,
        "payload": advisory,
    }
    return out


def cap_council_pack(files: list[Any] | None, symbols: list[Any] | None) -> dict[str, Any]:
    """H5: keep existing 10-file / 50-symbol pack caps. Pure slice, no I/O."""
    src_files = list(files or [])
    src_syms = list(symbols or [])
    max_f = int(COUNCIL_CONTEXT_MAX_FILES)
    max_s = int(COUNCIL_CONTEXT_MAX_SYMBOLS)
    truncated = False
    reasons: list[str] = []
    if len(src_files) > max_f:
        src_files = src_files[:max_f]
        truncated = True
        reasons.append("max_files")
    if len(src_syms) > max_s:
        src_syms = src_syms[:max_s]
        truncated = True
        reasons.append("max_symbols")
    return {
        "files": src_files,
        "symbols": src_syms,
        "truncated": truncated,
        "truncation_reason": "+".join(reasons) if reasons else None,
        "budget": {"max_files": max_f, "max_symbols": max_s},
    }


__all__ = [
    "ALGORITHM_NAME",
    "CACHE_TTL_S",
    "KIND_COMMIT",
    "KIND_COUNCIL",
    "KIND_TEST",
    "PAYLOAD_VERSION",
    "cap_council_pack",
    "clear_cache",
    "enrich_test_result",
    "lookup_enrich",
    "put_cache",
    "scoped_fingerprint",
]
