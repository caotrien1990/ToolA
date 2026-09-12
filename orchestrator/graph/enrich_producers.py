"""GAP-01 producers: cold-compute side of the P7 Heavy enrich cache.

hooks.py is cache-READ only by contract ("never cold-computes, reindexes, or
spawns a subprocess"). This module is the sanctioned producer side: it computes
a real payload and stores it via hooks.put_cache() so same-process consumers
(enrich_test_result / git commit advisory / council dispatch) hit the cache
instead of reporting skipped:cache_miss forever.

Every entry point is:
  * flag-gated by the same live CBM_ENRICH_* env flags the consumers read;
  * fail-open -- returns a {"warmed": bool, "reason": str|None} dict, never
    raises, and a producer failure leaves the consumer on its honest
    skipped/cache_miss path;
  * deadline-bounded -- compute runs in a supervised daemon thread with bounded
    admission (CBM_ENRICH_PRODUCER_BUDGET_S, default 45s). Timed-out workers stay
    accounted until exit and cannot publish a cache entry after the caller returns.

Honesty rules inherited from hooks.py: test selection is NEVER scraped from
stdout -- H2 fires only on caller-declared executed_test_files. Payloads that
come back status="unavailable" are NOT cached (never fabricate a hit).
"""
from __future__ import annotations

import hashlib
import os
import threading
import time
from typing import Any, Callable, Iterable, MutableMapping

from .hooks import (
    KIND_COMMIT,
    KIND_COUNCIL,
    KIND_TEST,
    lookup_enrich,
    put_cache,
    scoped_fingerprint,
)

BUDGET_ENV = "CBM_ENRICH_PRODUCER_BUDGET_S"
# Measured cold build_test_gap on the 2025-file _refscale_p9 fixture (graph.db
# present) = ~16.6s in a bare process; under daemon GIL contention it exceeds a
# 20s budget, so the first cold warm returned deadline -> the consumer reported a
# permanent cache_miss (the GAP-01 symptom). 45s gives ~2.7x margin over the
# measured cold cost while still bounding a runaway (hard-join daemon thread).
# Tune without a code change via CBM_ENRICH_PRODUCER_BUDGET_S.
DEFAULT_BUDGET_S = 45.0
PRODUCER_VERSION = "enrich-producer-v1"
_GIT_SCOPE_TIMEOUT_S = 5.0
_GIT_SCOPE_MAX_FILES = 500
_MAX_ACTIVE = 2
_ACTIVE_LOCK = threading.Lock()
_ACTIVE_THREADS: set[threading.Thread] = set()


def _budget_s(deadline_s: float | None = None) -> float:
    if deadline_s is not None:
        try:
            v = float(deadline_s)
            if v > 0:
                return v
        except Exception:
            pass
    try:
        v = float(os.environ.get(BUDGET_ENV, "").strip())
        return v if v > 0 else DEFAULT_BUDGET_S
    except Exception:
        return DEFAULT_BUDGET_S


def _flag(env_name: str) -> bool:
    return os.environ.get(env_name, "0").strip().lower() in ("1", "true", "yes", "on")


def active_producer_count() -> int:
    with _ACTIVE_LOCK:
        return len(_ACTIVE_THREADS)


def _run_bounded(fn: Callable[[], Any], deadline_s: float) -> tuple[bool, Any]:
    """Run fn on a supervised daemon thread. (done, value)."""
    box: dict[str, Any] = {}

    def _work() -> None:
        try:
            box["value"] = fn()
            box["ok"] = True
        except Exception as exc:  # noqa: BLE001 -- fail-open by contract
            box["error"] = f"{type(exc).__name__}: {exc}"
            box["ok"] = False
        finally:
            with _ACTIVE_LOCK:
                _ACTIVE_THREADS.discard(threading.current_thread())

    t = threading.Thread(target=_work, daemon=True)
    with _ACTIVE_LOCK:
        if len(_ACTIVE_THREADS) >= _MAX_ACTIVE:
            return False, None
        _ACTIVE_THREADS.add(t)
    t.start()
    t.join(deadline_s)
    if t.is_alive():
        return False, None
    return bool(box.get("ok")), box.get("value")


async def run_producer_async(
    fn: Callable[..., dict[str, Any]], *args: Any, **kwargs: Any,
) -> dict[str, Any]:
    """Execute a producer off the caller's event loop."""
    import asyncio

    return await asyncio.to_thread(fn, *args, **kwargs)


def _checkpoint(end_mono: float) -> Callable[[str], bool]:
    """Cooperative stop for build_* calls: True once the deadline passed."""
    return lambda _tag: time.monotonic() > end_mono


def _looks_like_test(path: str) -> bool:
    p = str(path or "").replace("\\", "/").lower()
    base = p.rsplit("/", 1)[-1]
    if base.startswith("test_") or base.startswith("test-"):
        return True
    if "_test." in base or ".test." in base or ".spec." in base:
        return True
    return "/tests/" in f"/{p}"


def _git_changed_files(root: str) -> list[str]:
    """Best-effort working-tree change scope for the H2 advisory. Bounded.

    Empty on any failure (not a git repo, timeout, odd porcelain) -- callers
    fall back to the declared-tests-only scope and label it in the payload.
    """
    try:
        import subprocess

        proc = subprocess.run(
            ["git", "status", "--porcelain=v1", "-z"],
            cwd=root, capture_output=True, timeout=_GIT_SCOPE_TIMEOUT_S,
        )
        if proc.returncode != 0:
            return []
        raw = proc.stdout.decode("utf-8", "replace")
        files: list[str] = []
        for entry in raw.split("\0"):
            # v1 -z entries: "XY <path>"; rename old-paths arrive as bare
            # chunks without the "XY " prefix -- the entry[2] == " " check
            # drops them (best-effort scope, not a protocol parser).
            if len(entry) < 4 or entry[2] != " ":
                continue
            xy, p = entry[:2], entry[3:].strip()
            if not p or xy == "??":
                continue
            files.append(p.replace("\\", "/"))
        return sorted(set(files))[:_GIT_SCOPE_MAX_FILES]
    except Exception:
        return []


def produce_test_advisory(
    project_root: str,
    script_hash: str,
    test_files: Iterable[str],
    *,
    included_files: Iterable[str] | None = None,
    deadline_s: float | None = None,
    cache: MutableMapping[Any, Any] | None = None,
) -> dict[str, Any]:
    """H2: warm KIND_TEST keyed by script_hash with a real test-gap payload.

    Scope: caller-declared test files, plus git working-tree changes as the
    production scope when the caller did not declare one (labelled in
    payload["included_scope"]). Never inspects stdout.
    """
    if not _flag("CBM_ENRICH_TEST"):
        return {"warmed": False, "reason": "disabled"}
    raw_fp = str(script_hash or "").strip()
    if not raw_fp:
        return {"warmed": False, "reason": "no_fingerprint"}
    files = sorted({str(f).strip().replace("\\", "/")
                    for f in (test_files or []) if str(f).strip()})
    if not files:
        return {"warmed": False, "reason": "no_test_files"}
    root = str(project_root or "").strip()
    if not root or not os.path.isdir(root):
        return {"warmed": False, "reason": "no_root"}
    fp = scoped_fingerprint(root, raw_fp)
    hit = lookup_enrich(KIND_TEST, fp, cache=cache)
    if hit.get("status") == "ok":
        return {"warmed": True, "reason": "already_cached"}

    declared = [str(f).strip().replace("\\", "/")
                for f in (included_files or []) if str(f).strip()]
    scope = "declared"
    if not declared:
        declared = _git_changed_files(root)
        scope = "git_changed" if declared else "declared_tests_only"
    budget = _budget_s(deadline_s)
    end = time.monotonic() + budget

    def _compute() -> Any:
        from . import testgap as _testgap

        return _testgap.build_test_gap(
            root,
            included_files=declared or None,
            test_files=files,
            checkpoint=_checkpoint(end),
        )

    done, payload = _run_bounded(_compute, budget)
    if not done:
        return {"warmed": False, "reason": "deadline"}
    if not isinstance(payload, dict) or payload.get("status") == "unavailable":
        return {"warmed": False, "reason": "unavailable_payload"}
    payload = dict(payload)
    payload["producer"] = PRODUCER_VERSION
    payload["included_scope"] = scope
    put_cache(KIND_TEST, fp, payload, cache=cache)
    return {"warmed": True, "reason": None}


def produce_commit_advisory(
    project_root: str,
    staged_hash: str,
    staged_files: Iterable[str],
    *,
    deadline_s: float | None = None,
    cache: MutableMapping[Any, Any] | None = None,
) -> dict[str, Any]:
    """H3: warm KIND_COMMIT keyed by the pre-commit staged-content hash.

    Payload = test-gap advisory scoped to the staged files (production vs
    test split derived from PATH NAMES in the git index -- static metadata,
    not stdout). Must run close to commit time: the cache TTL is 60s.
    """
    if not _flag("CBM_ENRICH_COMMIT"):
        return {"warmed": False, "reason": "disabled"}
    raw_fp = str(staged_hash or "").strip()
    if not raw_fp:
        return {"warmed": False, "reason": "no_fingerprint"}
    files = sorted({str(f).strip().replace("\\", "/")
                    for f in (staged_files or []) if str(f).strip()})
    if not files:
        return {"warmed": False, "reason": "no_staged_files"}
    root = str(project_root or "").strip()
    if not root or not os.path.isdir(root):
        return {"warmed": False, "reason": "no_root"}
    fp = scoped_fingerprint(root, raw_fp)
    hit = lookup_enrich(KIND_COMMIT, fp, cache=cache)
    if hit.get("status") == "ok":
        return {"warmed": True, "reason": "already_cached"}

    tests = [f for f in files if _looks_like_test(f)]
    prod = [f for f in files if f not in set(tests)]
    budget = _budget_s(deadline_s)
    end = time.monotonic() + budget

    def _compute() -> Any:
        from . import testgap as _testgap

        return _testgap.build_test_gap(
            root,
            included_files=prod or None,
            test_files=tests,
            checkpoint=_checkpoint(end),
        )

    done, payload = _run_bounded(_compute, budget)
    if not done:
        return {"warmed": False, "reason": "deadline"}
    if not isinstance(payload, dict) or payload.get("status") == "unavailable":
        return {"warmed": False, "reason": "unavailable_payload"}
    payload = dict(payload)
    payload["producer"] = PRODUCER_VERSION
    payload["staged_files"] = files
    put_cache(KIND_COMMIT, fp, payload, cache=cache)
    return {"warmed": True, "reason": None}


def produce_council_pack(
    project_path: str,
    *,
    generation: int = 0,
    deadline_s: float | None = None,
    cache: MutableMapping[Any, Any] | None = None,
) -> dict[str, Any]:
    """H5: warm KIND_COUNCIL and return the capped council-context pack.

    Fingerprint = (project_path, context-registry generation) so a bumped
    generation invalidates the pack. Payload = council_graph_context
    (changes mode) down-capped by hooks.cap_council_pack (10 files /
    50 symbols). Returned payload is for RESPONSE ATTACHMENT only -- it is
    never auto-injected into a worker prompt (Step-21 contract).
    """
    if not _flag("CBM_ENRICH_COUNCIL"):
        return {"warmed": False, "reason": "disabled", "payload": None}
    root = str(project_path or "").strip()
    if not root:
        return {"warmed": False, "reason": "no_project", "payload": None}
    norm = root.replace("\\", "/").rstrip("/").lower()
    fp = hashlib.sha256(
        f"council|{norm}|gen:{int(generation or 0)}".encode("utf-8"),
    ).hexdigest()[:32]
    hit = lookup_enrich(KIND_COUNCIL, fp, cache=cache)
    if hit.get("status") == "ok":
        return {"warmed": True, "reason": "already_cached",
                "fingerprint": fp, "payload": hit.get("payload")}
    budget = _budget_s(deadline_s)

    def _compute() -> Any:
        from . import query as _query
        from .hooks import cap_council_pack

        ctx = _query.council_graph_context(root, name="", depth=2, file_path="")
        ctx = ctx if isinstance(ctx, dict) else {}
        pack = cap_council_pack(ctx.get("files"), ctx.get("symbols"))
        pack["graph_status"] = ctx.get("graph_status")
        pack["graph_degraded_reason"] = ctx.get("graph_degraded_reason")
        pack["source"] = ctx.get("source")
        return pack

    done, payload = _run_bounded(_compute, budget)
    if not done:
        return {"warmed": False, "reason": "deadline", "payload": None}
    if not isinstance(payload, dict):
        return {"warmed": False, "reason": "bad_payload", "payload": None}
    payload = dict(payload)
    payload["producer"] = PRODUCER_VERSION
    put_cache(KIND_COUNCIL, fp, payload, cache=cache)
    return {"warmed": True, "reason": None, "fingerprint": fp, "payload": payload}


__all__ = [
    "BUDGET_ENV",
    "DEFAULT_BUDGET_S",
    "PRODUCER_VERSION",
    "active_producer_count",
    "run_producer_async",
    "produce_commit_advisory",
    "produce_council_pack",
    "produce_test_advisory",
]
