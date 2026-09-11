"""2026-08-07 Upgrade — Phase 4: huge/professional file & data tools (read-only, T1).

All tools here are PURE READS: no writes, no side effects, bounded output, stdlib only.
Registered into code_tools.TOOL_MAP via a small block near the V15 registration.
Roadmap ref: ORCHESTRATOR_UPGRADE_ROADMAP.md Phase 4.
"""
from __future__ import annotations

import csv
import json
import sqlite3
from pathlib import Path

# B009 (2026-08-20): SQLite lock contention is a WAIT, not a failure. Passing
# this to connect(timeout=) is the whole mechanism -- sqlite3 installs it as the
# busy handler, so PRAGMA busy_timeout reports it back (measured; an extra PRAGMA
# here was redundant and a mutant proved it by surviving). Named so a test can
# shrink it to milliseconds instead of blocking for the real wait. 20s:
# graph/store.py uses 15s, council/cron_jobs.py 10s, and this is the tool CAs
# poll by hand against databases another process is writing.
_SQLITE_BUSY_TIMEOUT_MS = 20000


def _err(msg: str, **extra) -> str:
    d = {"ok": False, "result": "error", "error": msg}
    d.update(extra)
    return json.dumps(d, ensure_ascii=False)


def _resolve(path: str, project_path: str = "") -> Path:
    p = Path(path)
    if not p.is_absolute() and project_path:
        p = Path(project_path) / path
    return p


async def query_sqlite(db_path: str, sql: str = "", table: str = "",
                       list_tables: bool = False, limit: int = 50,
                       project_path: str = "", **_kw) -> str:
    """Inspect a SQLite DB schema and run bounded READ-ONLY queries (mode=ro). Never writes."""
    try:
        p = _resolve(db_path, project_path)
        if not p.exists():
            return _err(f"DB not found: {p}", error_name="PATH_NOT_FOUND")
        limit = max(1, min(int(limit or 50), 1000))
        conn = sqlite3.connect(f"file:{p.as_posix()}?mode=ro", uri=True,
                               timeout=_SQLITE_BUSY_TIMEOUT_MS / 1000.0)
        try:
            conn.row_factory = sqlite3.Row
            if not sql or list_tables:
                rows = conn.execute(
                    "SELECT name, type FROM sqlite_master WHERE type IN ('table','view') "
                    "AND name NOT LIKE 'sqlite_%' ORDER BY name").fetchall()
                tables = []
                for r in rows:
                    if table and r["name"] != table:
                        continue
                    cols = conn.execute(f'PRAGMA table_info("{r["name"]}")').fetchall()
                    tables.append({"name": r["name"], "type": r["type"],
                                   "columns": [{"name": c["name"], "type": c["type"]} for c in cols]})
                return json.dumps({"ok": True, "result": "schema", "db": str(p),
                                   "table_count": len(tables), "tables": tables},
                                  ensure_ascii=False)
            s = sql.strip().rstrip(";").strip()
            low = s.lower()
            if not low.startswith(("select", "with", "pragma", "explain")):
                return _err("Only SELECT / WITH / PRAGMA / EXPLAIN allowed (read-only).",
                            error_name="WRITE_QUERY_REJECTED")
            forbidden = ("insert ", "update ", "delete ", "drop ", "alter ",
                         "create ", "attach ", "replace ", "reindex ", "vacuum")
            if any(k in low for k in forbidden):
                return _err("Query contains a write/DDL keyword; rejected.",
                            error_name="WRITE_QUERY_REJECTED")
            if " limit " not in low and low.startswith(("select", "with")):
                s = f"{s} LIMIT {limit}"
            cur = conn.execute(s)
            cols = [d[0] for d in (cur.description or [])]
            rows = [dict(zip(cols, r)) for r in cur.fetchmany(limit)]
            return json.dumps({"ok": True, "result": "rows", "db": str(p), "columns": cols,
                               "row_count": len(rows), "rows": rows},
                              ensure_ascii=False, default=str)
        finally:
            conn.close()
    except sqlite3.OperationalError as e:
        # A writer holding the DB is transient state, not a bad request. Reported
        # as retryable so a CA waits instead of concluding the tool is broken;
        # every other OperationalError (bad SQL, no such table) stays a hard error.
        text = str(e).lower()
        if "locked" in text or "database is busy" in text:
            return _err(
                f"OperationalError: {e}",
                error_name="SQLITE_DATABASE_LOCKED",
                retryable=True,
                retry_after_seconds=1,
                busy_timeout_ms=int(_SQLITE_BUSY_TIMEOUT_MS),
                ca_next_action="another process holds the DB lock; wait, then resend the same call",
            )
        return _err(f"OperationalError: {e}", error_name="QUERY_SQLITE_ERROR",
                    retryable=False)
    except Exception as e:
        return _err(f"{type(e).__name__}: {e}", error_name="QUERY_SQLITE_ERROR")


_LANG_BY_EXT = {
    "py": "python", "js": "javascript", "ts": "typescript", "tsx": "typescript",
    "jsx": "javascript", "vue": "vue", "json": "json", "html": "html", "css": "css",
    "md": "markdown", "sql": "sql", "sh": "shell", "bat": "bat", "ps1": "powershell",
    "go": "go", "rs": "rust", "java": "java", "cs": "csharp", "cpp": "cpp", "c": "c",
    "php": "php", "rb": "ruby", "csv": "csv", "tsv": "tsv", "jsonl": "jsonl",
    "yaml": "yaml", "yml": "yaml", "xml": "xml", "toml": "toml",
}


async def file_profile(file_path: str, project_path: str = "", **_kw) -> str:
    """Type/size/encoding/line_count/binary-or-text/language WITHOUT dumping content."""
    try:
        p = _resolve(file_path, project_path)
        if not p.exists():
            return _err(f"Not found: {p}", error_name="PATH_NOT_FOUND")
        if p.is_dir():
            return _err(f"Is a directory: {p}", error_name="IS_DIRECTORY")
        st = p.stat()
        size = st.st_size
        with open(p, "rb") as f:
            head = f.read(8192)
        is_binary = b"\x00" in head
        enc = "binary"
        line_count = None
        estimated = False
        if not is_binary:
            enc = "utf-8"
            try:
                head.decode("utf-8")
            except Exception:
                enc = "latin-1"
            if size <= 20 * 1024 * 1024:
                with open(p, "rb") as f:
                    line_count = sum(1 for _ in f)
            elif head:
                nl = head.count(b"\n") or 1
                line_count = int(size / (len(head) / nl))
                estimated = True
        ext = p.suffix.lower().lstrip(".")
        return json.dumps({
            "ok": True, "result": "profile", "path": str(p),
            "size_bytes": size, "size_mb": round(size / 1048576, 3),
            "is_binary": is_binary, "encoding": enc,
            "line_count": line_count, "line_count_estimated": estimated,
            "ext": ext, "language": _LANG_BY_EXT.get(ext, "unknown"),
            "mtime": st.st_mtime,
        }, ensure_ascii=False)
    except Exception as e:
        return _err(f"{type(e).__name__}: {e}", error_name="FILE_PROFILE_ERROR")


async def sample_data_file(file_path: str, n: int = 5, mode: str = "head",
                           project_path: str = "", **_kw) -> str:
    """First/last/random N rows for CSV/TSV/JSONL/log/text files without full dump."""
    try:
        p = _resolve(file_path, project_path)
        if not p.exists():
            return _err(f"Not found: {p}", error_name="PATH_NOT_FOUND")
        n = max(1, min(int(n or 5), 200))
        ext = p.suffix.lower().lstrip(".")
        with open(p, "r", encoding="utf-8", errors="replace") as f:
            if mode == "head":
                lines = []
                for i, ln in enumerate(f):
                    if i >= n:
                        break
                    lines.append(ln.rstrip("\n"))
            elif mode == "tail":
                from collections import deque
                lines = [x.rstrip("\n") for x in deque(f, maxlen=n)]
            else:  # random
                import random
                alll = [x.rstrip("\n") for x in f]
                lines = random.sample(alll, min(n, len(alll))) if alll else []
        parsed = None
        if ext in ("csv", "tsv"):
            parsed = list(csv.reader(lines, delimiter="\t" if ext == "tsv" else ","))
        elif ext == "jsonl":
            parsed = []
            for ln in lines:
                try:
                    parsed.append(json.loads(ln))
                except Exception:
                    parsed.append({"_raw": ln})
        return json.dumps({
            "ok": True, "result": "sample", "path": str(p), "mode": mode,
            "returned": len(lines), "ext": ext,
            "lines": None if parsed is not None else lines, "parsed": parsed,
        }, ensure_ascii=False, default=str)
    except Exception as e:
        return _err(f"{type(e).__name__}: {e}", error_name="SAMPLE_DATA_ERROR")


async def read_chunks(file_path: str, by: str = "line", start: int = 0,
                      count: int = 200, project_path: str = "", **_kw) -> str:
    """DEPRECATED (Task 2 2026-09-10): use read_file_range instead. Legacy raw read.

    Bounded chunk read by line or byte; never returns the whole file by default."""
    try:
        p = _resolve(file_path, project_path)
        if not p.exists():
            return _err(f"Not found: {p}", error_name="PATH_NOT_FOUND")
        start = max(0, int(start or 0))
        count = max(1, min(int(count or 200), 2000))
        if by == "byte":
            size = p.stat().st_size
            with open(p, "rb") as f:
                f.seek(min(start, size))
                data = f.read(count)
            return json.dumps({
                "ok": True, "result": "chunk", "path": str(p), "by": "byte",
                "start": start, "returned_bytes": len(data), "total_bytes": size,
                "has_more": start + len(data) < size,
                "content": data.decode("utf-8", "replace"),
            }, ensure_ascii=False)
        lines = []
        has_more = False
        with open(p, "r", encoding="utf-8", errors="replace") as f:
            for i, ln in enumerate(f):
                if i < start:
                    continue
                if i >= start + count:
                    has_more = True
                    break
                lines.append(ln.rstrip("\n"))
        return json.dumps({
            "ok": True, "result": "chunk", "path": str(p), "by": "line",
            "start_line": start, "returned_lines": len(lines),
            "has_more": has_more, "lines": lines,
        }, ensure_ascii=False)
    except Exception as e:
        return _err(f"{type(e).__name__}: {e}", error_name="READ_CHUNKS_ERROR")


UPGRADE_P4_SCHEMA_SPECS = [
    ("query_sqlite",
     "T1 READ-ONLY: inspect a SQLite DB (tables + columns) and run bounded SELECT/WITH/PRAGMA/EXPLAIN queries against the file opened mode=ro. Rejects every write/DDL keyword. Empty sql -> schema listing. Never writes. Waits out a busy writer for 20s; if the lock outlasts that, answers SQLITE_DATABASE_LOCKED with retryable:true -- resend the same call rather than treating it as a failure.",
     {"db_path": {"type": "string", "description": "Path to .db (absolute or relative to project_path)."},
      "sql": {"type": "string", "default": "", "description": "Read-only query; empty -> return schema."},
      "table": {"type": "string", "default": "", "description": "Optional: focus schema on one table."},
      "list_tables": {"type": "boolean", "default": False, "description": "Return schema listing (ignore sql)."},
      "limit": {"type": "integer", "default": 50, "description": "Max rows (auto-appended if absent). Cap 1000."},
      "project_path": {"type": "string", "default": "", "description": "Optional root for relative db_path."}},
     ["db_path"]),
    ("file_profile",
     "T1 READ-ONLY: type/size/encoding/line_count/binary-or-text/language of a file WITHOUT dumping its content.",
     {"file_path": {"type": "string", "description": "File path (absolute or relative to project_path)."},
      "project_path": {"type": "string", "default": "", "description": "Optional root for relative file_path."}},
     ["file_path"]),
    ("sample_data_file",
     "T1 READ-ONLY: first/last/random N rows for CSV/TSV/JSONL/log/text files without full dump.",
     {"file_path": {"type": "string", "description": "File path (absolute or relative to project_path)."},
      "n": {"type": "integer", "default": 5, "description": "Rows/lines to sample. Cap 200."},
      "mode": {"type": "string", "enum": ["head", "tail", "random"], "default": "head"},
      "project_path": {"type": "string", "default": ""}},
     ["file_path"]),
    ("read_chunks",
     "T1 READ-ONLY: bounded chunk read by line or byte; never returns the whole file by default.",
     {"file_path": {"type": "string", "description": "File path (absolute or relative to project_path)."},
      "by": {"type": "string", "enum": ["line", "byte"], "default": "line"},
      "start": {"type": "integer", "default": 0, "description": "Start line index (by=line) or byte offset (by=byte)."},
      "count": {"type": "integer", "default": 200, "description": "Lines (cap 2000) or bytes to read."},
      "project_path": {"type": "string", "default": ""}},
     ["file_path"]),
]
