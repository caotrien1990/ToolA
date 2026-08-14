"""JSONL log lifecycle helpers extracted from code_tools."""

from __future__ import annotations

import json
import threading
from datetime import datetime, timedelta
from pathlib import Path

from .store import _SESSIONS_DB, _log_session_event, _project_cache_dir

_ORCHESTRATOR_ROOT = Path(__file__).parents[2]
_LOG_FILE_LOCK = threading.RLock()


def _atomic_write_bytes(path: Path, data: bytes) -> None:
    import os
    import tempfile
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=str(path.parent))
    tmp = Path(tmp_name)
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(data)
            f.flush()
            os.fsync(f.fileno())
        os.replace(str(tmp), str(path))
    except Exception:
        try:
            tmp.unlink()
        except Exception:
            pass
        raise


def _atomic_write_text(path: Path, text: str, encoding: str = "utf-8") -> None:
    _atomic_write_bytes(Path(path), text.encode(encoding))


def _log_to_file(log_path: Path, entry: dict, max_entries: int = 500) -> bool:
    """Append one JSONL record atomically; return False instead of hiding failure."""
    try:
        with _LOG_FILE_LOCK:
            log_path.parent.mkdir(parents=True, exist_ok=True)
            lines = log_path.read_text(encoding="utf-8").splitlines() if log_path.exists() else []
            lines.append(json.dumps(entry, ensure_ascii=False))
            if len(lines) > max_entries:
                lines = lines[-max_entries:]
            _atomic_write_text(log_path, "\n".join(lines) + "\n", encoding="utf-8")
        return True
    except Exception:
        return False


def _log_usage(tool: str, params: dict, ok: bool, ms: int, error: str = "") -> bool:
    """Ghi vào USAGE_LOG của Orchestrator (meta-log về chính Orchestrator)."""
    cache_dir = _project_cache_dir(str(_ORCHESTRATOR_ROOT))
    project_hint = params.get("project_path", params.get("file_path", ""))
    entry = {
        "ts": datetime.now().isoformat(timespec="seconds"),
        "tool": tool, "ok": ok, "ms": ms,
        "project": Path(project_hint).name if project_hint else "",
    }
    if error:
        entry["error"] = error[:120]
    return _log_to_file(cache_dir / "USAGE_LOG.jsonl", entry, max_entries=1000)


def _log_app_session(project_path: str, tool: str, extra: dict, ok: bool, ms: int = 0, error: str = ""):
    """Ghi vào APP_SESSION_LOG trong .orchestrator_cache/ của project đang làm việc."""
    try:
        cache_dir = _project_cache_dir(str(Path(project_path).resolve()))
        entry = {"ts": datetime.now().isoformat(timespec="seconds"), "tool": tool, "ok": ok, "ms": ms}
        if extra:
            entry.update(extra)
        if error:
            entry["error"] = error[:120]
        _log_to_file(cache_dir / "APP_SESSION_LOG.jsonl", entry, max_entries=500)
    except Exception:
        pass


def _auto_trim_log(log_path: Path, keep_sessions: int = 2):
    """Trim USAGE_LOG, keeping only the last completed sessions."""
    try:
        if not log_path.exists():
            return
        lines = log_path.read_text(encoding="utf-8").splitlines()
        entries = [json.loads(l) for l in lines if l.strip()]
    except Exception:
        return
    end_positions = [i for i, e in enumerate(entries) if e.get("type") == "session_end"]
    if len(end_positions) <= keep_sessions:
        return
    cutoff = end_positions[-(keep_sessions + 1)] + 1
    kept = entries[cutoff:]
    _atomic_write_text(
        log_path,
        "\n".join(json.dumps(e, ensure_ascii=False) for e in kept) + "\n",
        encoding="utf-8"
    )


def _check_incomplete_sessions() -> list[dict]:
    """Scan USAGE_LOG tìm sessions chưa có log_native (session_end)."""
    log_path = _project_cache_dir(str(_ORCHESTRATOR_ROOT)) / "USAGE_LOG.jsonl"
    if not log_path.exists():
        return []
    try:
        entries = [json.loads(l) for l in log_path.read_text(encoding="utf-8").splitlines() if l.strip()]
    except Exception:
        return []
    if not entries:
        return []
    sessions: list[list[dict]] = []
    current: list[dict] = [entries[0]]
    for entry in entries[1:]:
        try:
            gap = datetime.fromisoformat(entry["ts"]) - datetime.fromisoformat(current[-1]["ts"])
            if gap > timedelta(hours=2):
                sessions.append(current)
                current = [entry]
            else:
                current.append(entry)
        except Exception:
            current.append(entry)
    sessions.append(current)
    now = datetime.now()
    incomplete = []
    for session in sessions:
        has_calls = any(e.get("tool") and e.get("type") != "session_end" for e in session)
        has_end = any(e.get("type") == "session_end" for e in session)
        if not has_calls or has_end:
            continue
        ts_start = session[0].get("ts", "")
        try:
            age_h = (now - datetime.fromisoformat(ts_start)).total_seconds() / 3600
        except Exception:
            age_h = 999
        if age_h < 2:
            continue
        call_count = sum(1 for e in session if e.get("tool") and e.get("type") != "session_end")
        incomplete.append({"ts": ts_start, "calls": call_count})
    return incomplete


async def log_native(native: list = None) -> str:
    if native is None:
        native = []
    cleaned = [
        {"tool": str(item.get("tool", ""))[:60],
         "instead_of": str(item.get("instead_of", ""))[:60],
         "reason": str(item.get("reason", ""))[:200]}
        for item in native if isinstance(item, dict) and item.get("tool")
    ]
    entry = {
        "ts": datetime.now().isoformat(timespec="seconds"),
        "type": "session_end",
        "native": cleaned,
    }
    cache_dir = _project_cache_dir(str(_ORCHESTRATOR_ROOT))
    _log_to_file(cache_dir / "USAGE_LOG.jsonl", entry, max_entries=1000)
    _auto_trim_log(cache_dir / "USAGE_LOG.jsonl")
    try:
        import sqlite3
        conn = sqlite3.connect(str(_SESSIONS_DB))
        row = conn.execute(
            "SELECT session_id FROM sessions WHERE status='active' ORDER BY started_at DESC LIMIT 1"
        ).fetchone()
        conn.close()
        if row:
            _log_session_event(row[0], "log_native", {"native": cleaned}, {"ok": True, "native_count": len(cleaned)}, 0, "ok", "")
    except Exception:
        pass
    msg = "Session marked complete."
    if cleaned:
        msg += f" {len(cleaned)} native fallback(s) logged."
    return json.dumps({"ok": True, "message": msg, "native_count": len(cleaned)}, ensure_ascii=False)