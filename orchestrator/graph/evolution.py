"""Bounded argv-only git evolution metrics (P6-S1).

Adapted from the sentrux evo idea (MIT) onto campaign formulas.
Not a Rust port, not bitwise parity, no vendored crate, no git config
mutation, no shell, no network.

Jaccard = cochanges / (changes_a + changes_b - cochanges).
Pair cap is 50 FILES per commit (C(50,2)=1225), not 50 pairs.
Rename v1 canonical path = new path; lineage is not stitched
(rename_history_partial=true). Author id = SHA256(utf-8(trim(lower(email))))
full 64 hex; missing email is unknown, never a hashed name.
No git => unavailable:not_git_repository. Shallow => partial/history_truncated.
Missing CC => no fake hotspot (never default complexity=1).
"""
from __future__ import annotations

import hashlib
import os
import subprocess
import time
from typing import Any, Iterable

from .analysis_view import _identity_key, _norm_slash, _sorted_display


ALGORITHM_NAME = "evolution-v1"
PAYLOAD_VERSION = 1
DEFAULT_LOOKBACK_DAYS = 90
MAX_COMMITS = 1000
MAX_FILES_PER_COMMIT = 50
MAX_PAIRS_PER_COMMIT = 1225  # C(50, 2)
DEADLINE_S = 5.0
MIN_COUPLING_COUNT = 3
_UNKNOWN_AUTHOR = "unknown"
_NOTES: list[str] = [
    "campaign_formulas",
    "argv_only_git",
    "author_hashed_default",
    "heuristic_bus_factor",
    "rename_lineage_partial",
]


def jaccard(cochanges: int, changes_a: int, changes_b: int) -> float | None:
    """Campaign Jaccard. 3/(3+4-3) == 0.75. None when union is 0."""
    union = int(changes_a) + int(changes_b) - int(cochanges)
    if union <= 0:
        return None
    return int(cochanges) / union


def hash_author(email: str | None) -> str | None:
    """Full 64-hex SHA256 of trimmed lowercase email. Missing => None."""
    if email is None:
        return None
    text = str(email).strip().lower()
    if not text:
        return None
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _unavailable(
    reason: str,
    *,
    notes: list[str] | None = None,
    extra: dict[str, Any] | None = None,
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "version": PAYLOAD_VERSION,
        "status": "unavailable",
        "reason": reason,
        "pass": False,
        "lookback_days": DEFAULT_LOOKBACK_DAYS,
        "commits_analyzed": 0,
        "commits_dropped_merges": 0,
        "files_dropped_pair_cap": 0,
        "pairs_considered": 0,
        "rename_history_partial": False,
        "history_truncated": False,
        "shallow": False,
        "churn": [],
        "coupling": [],
        "authors": [],
        "hotspots": [],
        "bus_factor_score": None,
        "single_author_ratio": None,
        "churn_score": None,
        "evolution_score": None,
        "metric_notes": list(_NOTES) + list(notes or []),
        "truncated": False,
        "provenance": {"algorithm_version": ALGORITHM_NAME},
    }
    if extra:
        payload.update(extra)
    return payload


def _deadline_hit(started: float, now: float | None) -> bool:
    if now is None:
        return (time.monotonic() - started) > DEADLINE_S
    return (time.monotonic() - float(now)) > DEADLINE_S


def _git_env() -> dict[str, str]:
    env = os.environ.copy()
    env["GIT_TERMINAL_PROMPT"] = "0"
    env["GCM_INTERACTIVE"] = "never"
    env.pop("GIT_DIR", None)
    env.pop("GIT_WORK_TREE", None)
    return env


def _run_git(
    root: str,
    args: list[str],
    *,
    timeout: float,
    git_bin: str = "git",
) -> subprocess.CompletedProcess[bytes]:
    cmd = [git_bin, "-C", root, *args]
    return subprocess.run(
        cmd,
        capture_output=True,
        timeout=max(0.05, timeout),
        check=False,
        env=_git_env(),
        shell=False,
    )


def _is_rename_or_copy(status: str) -> bool:
    if not status:
        return False
    head, rest = status[0], status[1:]
    return (head in {"R", "C"} and rest.isdigit()) or status in {"R", "C"}


def _is_single_status(status: str) -> bool:
    if not status:
        return False
    if status in {"A", "M", "D", "T", "U", "X", "B"}:
        return True
    return status[0] in {"A", "M", "D", "T", "U"} and status[1:].isdigit()


def parse_git_log_z(blob: bytes) -> tuple[list[dict[str, Any]], dict[str, int]]:
    """NUL-safe parser. Commit framing is %x1e-separated; name-status is -z.

    Rnnn consumes old+new. Tab/newline inside a path stay in the path.
    """
    stats = {"merges_dropped": 0, "records": 0, "renames": 0}
    if not blob:
        return [], stats
    text = blob.decode("utf-8", errors="surrogateescape")
    records: list[dict[str, Any]] = []
    for raw in text.split("\x1e"):
        if not raw:
            continue
        parts = raw.split("\0")
        # Drop a trailing empty field produced by the final NUL.
        while parts and parts[-1] == "":
            parts.pop()
        if len(parts) < 5:
            continue
        commit_hash, date, email, _, parents, *rest = parts
        parent_ids = [p for p in parents.split() if p]
        if len(parent_ids) > 1:
            stats["merges_dropped"] += 1
            continue
        files: list[str] = []
        seen: set[str] = set()
        renamed = False
        i = 0
        while i < len(rest):
            status = rest[i].lstrip("\r\n")
            i += 1
            if not status:
                continue
            if _is_rename_or_copy(status):
                if i + 1 >= len(rest):
                    break
                old, new = rest[i], rest[i + 1]
                i += 2
                if status[0] == "R":
                    renamed = True
                    stats["renames"] += 1
                canonical = _norm_slash(new) or _norm_slash(old)
                key = _identity_key(canonical)
                # Track old so the same rename is not also counted as the old name.
                old_key = _identity_key(old)
                if old_key and old_key != key:
                    seen.add(old_key)
                if canonical and key not in seen:
                    seen.add(key)
                    files.append(canonical)
                continue
            if _is_single_status(status):
                if i >= len(rest):
                    break
                path = _norm_slash(rest[i])
                i += 1
                key = _identity_key(path)
                if path and key not in seen:
                    seen.add(key)
                    files.append(path)
                continue
            # Unknown token: skip (do not treat as a path — keeps framing intact).
        records.append(
            {
                "hash": commit_hash,
                "date": date,
                "email": email,
                "author_id": hash_author(email),
                "files": files,
                "rename_in_commit": renamed,
            }
        )
        stats["records"] += 1
    return records, stats


def pair_files(files: Iterable[str]) -> tuple[list[str], int]:
    """Sort, unique, cap at 50. Returns (capped_sorted, dropped_count)."""
    unique = _sorted_display(files)
    dropped = max(0, len(unique) - MAX_FILES_PER_COMMIT)
    return unique[:MAX_FILES_PER_COMMIT], dropped


def compute_from_records(
    records: list[dict[str, Any]],
    *,
    known_files: Iterable[str] | None = None,
    complexity_map: dict[str, int] | None = None,
    lookback_days: int = DEFAULT_LOOKBACK_DAYS,
    merges_dropped: int = 0,
    shallow: bool = False,
    history_truncated: bool = False,
    deadline: bool = False,
) -> dict[str, Any]:
    known_keys: set[str] | None = None
    known_display: dict[str, str] = {}
    if known_files is not None:
        known_keys = set()
        for raw in known_files:
            display = _norm_slash(raw)
            if not display:
                continue
            key = _identity_key(display)
            known_keys.add(key)
            known_display.setdefault(key, display)

    def _allowed(path: str) -> bool:
        return known_keys is None or _identity_key(path) in known_keys

    file_commits: dict[str, int] = {}
    file_display: dict[str, str] = dict(known_display)
    file_authors: dict[str, dict[str, int]] = {}
    pair_count: dict[tuple[str, str], int] = {}
    files_dropped = 0
    pairs_considered = 0
    rename_partial = False
    notes = list(_NOTES)

    for rec in records:
        if rec.get("rename_in_commit"):
            rename_partial = True
        present = [p for p in rec.get("files") or [] if _allowed(p)]
        # Presence counts every unique file; pair cap is separate.
        for path in _sorted_display(present):
            key = _identity_key(path)
            file_display.setdefault(key, path)
            file_commits[key] = file_commits.get(key, 0) + 1
            author_id = rec.get("author_id") or _UNKNOWN_AUTHOR
            bucket = file_authors.setdefault(key, {})
            bucket[author_id] = bucket.get(author_id, 0) + 1
        capped, dropped = pair_files(present)
        files_dropped += dropped
        n = len(capped)
        pairs_considered += n * (n - 1) // 2
        keys = [_identity_key(p) for p in capped]
        for i, a in enumerate(keys):
            for b in keys[i + 1 :]:
                pair = (a, b) if a < b else (b, a)
                pair_count[pair] = pair_count.get(pair, 0) + 1

    churn_rows = []
    for key, count in file_commits.items():
        churn_rows.append(
            {
                "file": file_display.get(key, key),
                "commit_count": count,
                "total_churn": count,
            }
        )
    churn_rows.sort(key=lambda r: (-int(r["commit_count"]), r["file"].casefold()))

    coupling = []
    for (a, b), co in pair_count.items():
        if co < MIN_COUPLING_COUNT:
            continue
        ca = file_commits.get(a, 0)
        cb = file_commits.get(b, 0)
        strength = jaccard(co, ca, cb)
        if strength is None:
            continue
        fa, fb = file_display.get(a, a), file_display.get(b, b)
        if fa.casefold() > fb.casefold():
            fa, fb = fb, fa
        coupling.append(
            {
                "file_a": fa,
                "file_b": fb,
                "co_change_count": co,
                "changes_a": ca if fa.casefold() <= fb.casefold() else cb,
                "changes_b": cb if fa.casefold() <= fb.casefold() else ca,
                "jaccard": strength,
            }
        )
    # Recompute changes_* against the displayed order.
    fixed = []
    for row in coupling:
        a_key, b_key = _identity_key(row["file_a"]), _identity_key(row["file_b"])
        fixed.append(
            {
                **row,
                "changes_a": file_commits.get(a_key, 0),
                "changes_b": file_commits.get(b_key, 0),
                "jaccard": jaccard(
                    row["co_change_count"],
                    file_commits.get(a_key, 0),
                    file_commits.get(b_key, 0),
                ),
            }
        )
    coupling = fixed
    coupling.sort(
        key=lambda r: (
            -(r["jaccard"] or 0.0),
            -int(r["co_change_count"]),
            r["file_a"].casefold(),
            r["file_b"].casefold(),
        )
    )

    author_rows = []
    single = 0
    for key, bucket in file_authors.items():
        total = sum(bucket.values())
        ranked = sorted(bucket.items(), key=lambda kv: (-kv[1], kv[0]))
        primary_id, primary_n = ranked[0]
        if len(ranked) == 1:
            single += 1
        author_rows.append(
            {
                "file": file_display.get(key, key),
                "author_count": len(ranked),
                "primary_author_id": primary_id,
                "primary_ratio": (primary_n / total) if total else 1.0,
                "author_ids": [aid for aid, _ in ranked],
            }
        )
    author_rows.sort(key=lambda r: r["file"].casefold())
    total_files = len(author_rows)
    single_ratio = (single / total_files) if total_files else 0.0
    bus = (1.0 - single_ratio) if total_files else 1.0

    # Churn concentration heuristic (campaign: labelled heuristic).
    if not churn_rows:
        churn_score = 1.0
    else:
        values = sorted((int(r["total_churn"]) for r in churn_rows), reverse=True)
        total = sum(values)
        if total == 0:
            churn_score = 1.0
        else:
            top_n = max(1, len(values) // 10)
            churn_score = max(0.0, min(1.0, 1.0 - (sum(values[:top_n]) / total)))

    hotspots: list[dict[str, Any]] = []
    missing_cc = False
    cc_map: dict[str, int] = {}
    if complexity_map:
        for raw, val in complexity_map.items():
            try:
                cc = int(val)
            except (TypeError, ValueError):
                continue
            cc_map[_identity_key(raw)] = cc
    for row in churn_rows:
        key = _identity_key(row["file"])
        if key not in cc_map:
            missing_cc = True
            continue
        cc = cc_map[key]
        if cc <= 0:
            missing_cc = True
            continue
        hotspots.append(
            {
                "file": row["file"],
                "churn_count": row["commit_count"],
                "max_complexity": cc,
                "risk_score": int(row["commit_count"]) * cc,
            }
        )
    if complexity_map is None or missing_cc:
        notes.append("missing_cc_no_fake_hotspot")
    hotspots.sort(key=lambda r: (-int(r["risk_score"]), r["file"].casefold()))
    hotspots = hotspots[:30]

    if rename_partial:
        notes.append("rename_history_partial")
    if shallow:
        notes.append("shallow_history")
    if history_truncated:
        notes.append("history_truncated")
    if deadline:
        notes.append("deadline")

    status = "ok"
    reason = None
    if deadline and not records:
        return _unavailable(
            "deadline",
            notes=notes,
            extra={
                "lookback_days": lookback_days,
                "shallow": shallow,
                "history_truncated": True,
            },
        )
    if deadline or shallow or history_truncated:
        status = "partial"
        reason = "deadline" if deadline else "history_truncated"

    return {
        "version": PAYLOAD_VERSION,
        "status": status,
        "reason": reason,
        "pass": status in {"ok", "partial"},
        "lookback_days": lookback_days,
        "commits_analyzed": len(records),
        "commits_dropped_merges": merges_dropped,
        "files_dropped_pair_cap": files_dropped,
        "pairs_considered": pairs_considered,
        "rename_history_partial": rename_partial,
        "history_truncated": bool(history_truncated or shallow or deadline),
        "shallow": shallow,
        "churn": churn_rows,
        "coupling": coupling,
        "authors": author_rows,
        "hotspots": hotspots,
        "bus_factor_score": bus,
        "single_author_ratio": single_ratio,
        "churn_score": churn_score,
        "evolution_score": min(bus, churn_score),
        "metric_notes": notes,
        "truncated": bool(history_truncated or shallow or deadline or files_dropped),
        "provenance": {"algorithm_version": ALGORITHM_NAME},
    }


def _probe_repo(root: str, git_bin: str, timeout: float) -> tuple[str | None, bool]:
    """Return (error_reason, shallow). error_reason None => usable work tree."""
    try:
        inside = _run_git(
            root, ["rev-parse", "--is-inside-work-tree"], timeout=timeout, git_bin=git_bin
        )
    except FileNotFoundError:
        return "not_git_repository", False
    except subprocess.TimeoutExpired:
        return "deadline", False
    if inside.returncode != 0 or inside.stdout.strip().lower() != b"true":
        return "not_git_repository", False
    try:
        shallow = _run_git(
            root,
            ["rev-parse", "--is-shallow-repository"],
            timeout=timeout,
            git_bin=git_bin,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return None, _has_shallow_file(root, git_bin, timeout)
    flagged = shallow.returncode == 0 and shallow.stdout.strip().lower() == b"true"
    if flagged:
        return None, True
    return None, _has_shallow_file(root, git_bin, timeout)


def _has_shallow_file(root: str, git_bin: str, timeout: float) -> bool:
    """Windows git sometimes omits --is-shallow-repository; .git/shallow is authoritative."""
    try:
        git_dir = _run_git(root, ["rev-parse", "--git-dir"], timeout=timeout, git_bin=git_bin)
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return False
    if git_dir.returncode != 0:
        return False
    raw = git_dir.stdout.decode("utf-8", errors="replace").strip()
    if not raw:
        return False
    path = raw if os.path.isabs(raw) else os.path.join(root, raw)
    return os.path.isfile(os.path.join(path, "shallow"))


def _since_iso(lookback_days: int, now_unix: float | None) -> str:
    import datetime as _dt

    if now_unix is None:
        end = _dt.datetime.now(tz=_dt.timezone.utc)
    else:
        end = _dt.datetime.fromtimestamp(float(now_unix), tz=_dt.timezone.utc)
    start = end - _dt.timedelta(days=int(lookback_days))
    return start.strftime("%Y-%m-%dT%H:%M:%SZ")


def walk_git_history(
    root: str,
    *,
    lookback_days: int = DEFAULT_LOOKBACK_DAYS,
    max_commits: int = MAX_COMMITS,
    now: float | None = None,
    now_unix: float | None = None,
    git_bin: str = "git",
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """argv-only `git log`. No shell, no network, no git config write."""
    meta = {
        "error": None,
        "shallow": False,
        "history_truncated": False,
        "merges_dropped": 0,
        "deadline": False,
        "commits_capped": False,
    }
    started = time.monotonic()
    if _deadline_hit(started, now):
        meta["error"] = "deadline"
        meta["deadline"] = True
        return [], meta
    remain = DEADLINE_S if now is not None else max(0.05, DEADLINE_S - (time.monotonic() - started))
    err, shallow = _probe_repo(root, git_bin, remain)
    meta["shallow"] = shallow
    if err:
        meta["error"] = err
        meta["deadline"] = err == "deadline"
        return [], meta
    if _deadline_hit(started, now):
        meta["error"] = "deadline"
        meta["deadline"] = True
        return [], meta
    since = _since_iso(lookback_days, now_unix)
    pretty = "%x1e%H%x00%aI%x00%ae%x00%an%x00%P%x00"
    args = [
        "log",
        "--first-parent",
        "--no-merges",
        f"--since={since}",
        f"--max-count={int(max_commits)}",
        "-M",
        "--name-status",
        "-z",
        f"--pretty=format:{pretty}",
    ]
    remain = max(0.05, DEADLINE_S - (time.monotonic() - started)) if now is None else DEADLINE_S
    try:
        proc = _run_git(root, args, timeout=remain, git_bin=git_bin)
    except FileNotFoundError:
        meta["error"] = "not_git_repository"
        return [], meta
    except subprocess.TimeoutExpired:
        meta["error"] = "deadline"
        meta["deadline"] = True
        return [], meta
    if proc.returncode != 0:
        # Empty repo still has a work tree; treat as zero commits, not fake hotspots.
        stderr = (proc.stderr or b"").decode("utf-8", errors="replace").lower()
        if "does not have any commits" in stderr or "bad default revision" in stderr:
            return [], meta
        meta["error"] = "not_git_repository"
        return [], meta
    records, stats = parse_git_log_z(proc.stdout or b"")
    meta["merges_dropped"] = int(stats.get("merges_dropped") or 0)
    if len(records) >= int(max_commits):
        meta["history_truncated"] = True
        meta["commits_capped"] = True
    if shallow:
        meta["history_truncated"] = True
    if _deadline_hit(started, now):
        meta["deadline"] = True
        meta["history_truncated"] = True
    return records, meta


def compute_evolution(
    root: str,
    *,
    known_files: Iterable[str] | None = None,
    complexity_map: dict[str, int] | None = None,
    lookback_days: int = DEFAULT_LOOKBACK_DAYS,
    max_commits: int = MAX_COMMITS,
    now: float | None = None,
    now_unix: float | None = None,
    git_bin: str = "git",
    records: list[dict[str, Any]] | None = None,
    shallow: bool = False,
    history_truncated: bool = False,
    merges_dropped: int = 0,
) -> dict[str, Any]:
    """Public kernel. Inject `records=` to skip git (unit tests)."""
    lookback_days = int(lookback_days or DEFAULT_LOOKBACK_DAYS)
    if records is None:
        if not root or not os.path.isdir(root):
            return _unavailable("not_git_repository", extra={"lookback_days": lookback_days})
        walked, meta = walk_git_history(
            root,
            lookback_days=lookback_days,
            max_commits=max_commits,
            now=now,
            now_unix=now_unix,
            git_bin=git_bin,
        )
        if meta.get("error") == "not_git_repository":
            return _unavailable(
                "not_git_repository",
                extra={"lookback_days": lookback_days, "shallow": False},
            )
        if meta.get("error") == "deadline" and not walked:
            return _unavailable(
                "deadline",
                extra={
                    "lookback_days": lookback_days,
                    "history_truncated": True,
                    "shallow": bool(meta.get("shallow")),
                },
            )
        records = walked
        shallow = bool(meta.get("shallow"))
        history_truncated = bool(meta.get("history_truncated"))
        merges_dropped = int(meta.get("merges_dropped") or 0)
        deadline = bool(meta.get("deadline"))
    else:
        deadline = bool(now is not None and _deadline_hit(time.monotonic(), now))
        if deadline and not records:
            return _unavailable("deadline", extra={"lookback_days": lookback_days})
    return compute_from_records(
        list(records),
        known_files=known_files,
        complexity_map=complexity_map,
        lookback_days=lookback_days,
        merges_dropped=merges_dropped,
        shallow=shallow,
        history_truncated=history_truncated,
        deadline=deadline,
    )
