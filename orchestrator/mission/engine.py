"""P3b engine A_CSLT v3.1 (G1 attempt-token, G2 heartbeat, G3 verifying rule,
SYS-02 kill-or-block, SYS-03 explicit artifact anchor).

Pure logic for mission task dispatch wrappers, attempt-token tracking,
artifact completion checks, dependency-gated task selection, and reaper
classification. No I/O except artifact file existence/last-line read.
No sqlite, no HTTP.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Optional

_ATTEMPT_TOKEN_RE = re.compile(r"mission_attempt=(\d+):(\d+)")


def make_attempt_token(task_id: int, attempt: int) -> str:
    """Build canonical attempt token string."""
    return f"mission_attempt={task_id}:{attempt}"


def parse_attempt_token(text: str) -> Optional[tuple[int, int]]:
    """Find mission_attempt=<task_id>:<attempt> anywhere in text.

    Returns (task_id, attempt) on first match, else None.
    """
    if not isinstance(text, str):
        return None
    m = _ATTEMPT_TOKEN_RE.search(text)
    if m is None:
        return None
    return int(m.group(1)), int(m.group(2))


def is_stale_attempt(token_attempt: int, current_attempt: int) -> bool:
    """True when token_attempt is older than current_attempt."""
    return token_attempt < current_attempt


def build_wrapper(
    *,
    task_id: int,
    attempt: int,
    title: str,
    prompt_artifact_path: str,
    output_artifact_path: str,
    agent_label: str,
    end_marker: Optional[str] = None,
    tool_call_budget: int = 20,
) -> str:
    """Build one-shot worker wrapper prompt text.

    Line 2 (prompt path) uses forward slashes; line 3 (output path) uses
    backslashes so [A] dedup regex can parse the .md artifact path.
    """
    _title = title  # reserved for future header enrichment
    if end_marker is None:
        end_marker = f"END_TASK_{task_id}_A{attempt}"
    prompt_fwd = str(prompt_artifact_path).replace(chr(92), "/")
    output_bwd = str(output_artifact_path).replace("/", chr(92))
    partial_bwd = output_bwd + ".partial"
    budget = max(1, int(tool_call_budget or 20))
    token = make_attempt_token(task_id, attempt)
    lines = [
        f"[MISSION TASK {task_id} ATTEMPT {attempt}] Bạn là {agent_label}, worker one-shot, không có context cũ.",
        f"Đọc prompt đầy đủ tại: {prompt_fwd}",
        f"Ghi kết quả vào: {output_bwd}",
        "Artifact có thể đã partial từ attempt trước — CONTINUE, đừng làm lại từ đầu.",
        (f"Budget: ~{budget} tool calls; at ~70% you MUST write current progress to "
         f"{partial_bwd} before continuing."),
        token,
        "If this task must write outside cwd, bootstrap [A] and use [A] write tools.",
        f"Kết thúc file + reply bằng dòng: {end_marker}",
    ]
    return "\n".join(lines) + "\n"


def artifact_complete(artifact_path: str | Path, end_marker: str) -> bool:
    """True when file exists, non-empty, and last stripped line == end_marker."""
    p = Path(artifact_path)
    if not p.exists() or not p.is_file():
        return False
    data = p.read_bytes()
    if not data:
        return False
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError:
        text = data.decode("utf-8", errors="replace")
    lines = text.splitlines()
    if not lines:
        return False
    return lines[-1].strip() == end_marker


def next_ready_task(tasks: list[dict]) -> Optional[dict]:
    """Select the pending task whose every dependency id is in the done set.

    `tasks` rows carry keys: id, state, depends_on_json (JSON list of ids), seq.
    Done set = ids of tasks with state == 'done'. Ties broken by (seq, id).
    Returns the task dict or None.
    """
    done_ids = {t["id"] for t in tasks if t.get("state") == "done"}
    candidates = []
    for t in tasks:
        if t.get("state") != "pending":
            continue
        deps_raw = t.get("depends_on_json")
        if deps_raw is None or deps_raw == "":
            deps: list[int] = []
        elif isinstance(deps_raw, str):
            try:
                deps = json.loads(deps_raw)
            except (json.JSONDecodeError, TypeError):
                deps = []
        else:
            deps = list(deps_raw)
        if all(d in done_ids for d in deps):
            candidates.append(t)
    if not candidates:
        return None
    candidates.sort(key=lambda t: (t.get("seq", 0), t["id"]))
    return candidates[0]


def classify_for_reaper(
    *,
    state: str,
    side_effect_class: str,
    dispatch_started: bool,
    grace_expired: bool,
    lease_expired: bool,
    artifact_stale: bool,
    heartbeat_recent: bool,
    child_pid_alive: Optional[bool],
) -> str:
    """Classify a stuck task for reaper action.

    Returns one of: 'none', 'requeue', 'kill_then_requeue',
    'blocked_gate', 'requeue_verify'.

    G2: dispatching not started + grace expired.
    G3/SYS-02: started + lease expired + stale artifact + no heartbeat.
    G3 verifying: verifying + lease expired (no worker re-dispatch).
    """
    is_pure = side_effect_class == "pure_artifact"

    # G2 — dispatching stuck before start
    if state == "dispatching" and not dispatch_started and grace_expired:
        if child_pid_alive:
            return "kill_then_requeue" if is_pure else "blocked_gate"
        return "requeue"

    # G3/SYS-02 — started, dead, stale, silent
    if (
        state == "started"
        and lease_expired
        and artifact_stale
        and not heartbeat_recent
    ):
        if child_pid_alive:
            return "kill_then_requeue" if is_pure else "blocked_gate"
        return "requeue"

    # G3 verifying — lease expired, requeue verify only (no worker dispatch)
    if state == "verifying" and lease_expired:
        return "requeue_verify"

    return "none"
