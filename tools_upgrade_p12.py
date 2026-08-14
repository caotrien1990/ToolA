"""2026-08-07 Upgrade — Phase 12: structured file-mutating tools (T2).

Tools: insert_text, replace_symbol, move_file, copy_range, cut_range.

Every tool here is a TARGETED, LINE-ORIENTED mutation on an existing file:
- Writes a .bak backup next to the file BEFORE mutating (mirrors write_fix).
- Auto-syncs the project file cache after a successful write (mirrors write_fix).
- Returns structured JSON with ok/error + the changed range.
- Rejects out-of-project absolute paths unless explicitly allowed.
No architecture/contract change: same async -> json.dumps handler shape as the
rest of [A]; registration lives in code_tools.py next to the UP4 block.
"""
from __future__ import annotations

import base64
import json
import shutil
from pathlib import Path


def _err(msg: str, **extra) -> str:
    d = {"ok": False, "result": "error", "error": msg}
    d.update(extra)
    return json.dumps(d, ensure_ascii=False)


def _resolve(project_path: str, path: str) -> Path:
    p = Path(path)
    if not p.is_absolute() and project_path:
        p = Path(project_path) / path
    return p


def _decode(content: str, content_encoding: str = "none") -> tuple[str, dict | None]:
    enc = (content_encoding or "none").strip().lower()
    if enc in ("base64", "b64"):
        try:
            raw = base64.b64decode(content)
            return raw.decode("utf-8"), None
        except Exception as exc:
            return "", {"ok": False, "error": f"base64 decode failed: {exc}",
                        "error_name": "BASE64_DECODE_FAILED"}
    return content, None


def _def_name(tok: str) -> str:
    """Normalize a def/class header token like `foo():` or `Bar:` to its bare name."""
    return tok.split("(", 1)[0].rstrip(":").strip()


def _read_lines(fp: Path) -> list[str]:
    return fp.read_text(encoding="utf-8", errors="replace").splitlines()


def _detect_eol(fp: Path) -> str:
    """Dominant line-ending of `fp` ('\\r\\n' or '\\n'); '\\n' when undeterminable."""
    try:
        raw = fp.read_bytes()
    except Exception:
        return "\n"
    crlf = raw.count(b"\r\n")
    lf_only = raw.count(b"\n") - crlf
    return "\r\n" if crlf > lf_only else "\n"


def _write_lines(fp: Path, lines: list[str], eol: str = "\n",
                 had_final_eol: bool = True) -> None:
    """Write lines with the dominant eol style and optional final EOL.

    NOTE: Mixed CRLF/LF input is normalized to its dominant style; this helper
    does not promise per-line byte preservation."""
    text = eol.join(lines) + (eol if lines and had_final_eol else "")
    fp.write_bytes(text.encode("utf-8"))


def _backup(fp: Path) -> str:
    bak = fp.with_suffix(fp.suffix + ".bak")
    bak.write_bytes(fp.read_bytes())  # byte-exact backup (preserves EOL/encoding)
    return str(bak)


def _sync_cache(fp: Path, project_path: str) -> dict:
    # Cache sync requires an explicit project_path; auto-detecting a root here can
    # resolve to a huge parent (e.g. C:\Users\ADMIN) and scan it. write_fix follows
    # the same rule: callers pass project_path, never rely on auto-discovery.
    if not project_path:
        return {"cache_synced": False, "cache_sync_skipped": "project_path required"}
    try:
        import code_tools as ct
        ok = ct._update_cache_after_write(str(fp), project_path)
        return {"cache_synced": bool(ok)}
    except Exception as exc:
        return {"cache_synced": False, "cache_sync_error": f"{type(exc).__name__}: {exc}"}


def _write_result(fp: Path, project_path: str, *, backup: str, start_line: int,
                  end_line: int, changed_lines: int, extra: dict | None = None) -> str:
    out = {
        "ok": True,
        "file": str(fp),
        "backup": backup,
        "changed_range": f"{start_line}-{end_line}",
        "changed_lines": changed_lines,
    }
    out.update(_sync_cache(fp, project_path))
    if extra:
        out.update(extra)
    return json.dumps(out, ensure_ascii=False)


async def insert_text(file_path: str, content: str, at_line: int = 0,
                      after_line: int = 0, content_encoding: str = "none",
                      project_path: str = "", dry_run: bool = False, **_kw) -> str:
    """T2 MUTATING: insert text at a line. `at_line` inserts BEFORE that 1-based line
    (append when 0/omitted); `after_line` inserts AFTER that 1-based line (mutually
    exclusive with at_line). Writes .bak backup + syncs cache. Mutating tool."""
    try:
        if not content:
            return _err("content is required", error_name="MISSING_REQUIRED_FIELD")
        text, decode_err = _decode(content, content_encoding)
        if decode_err:
            return json.dumps(decode_err, ensure_ascii=False)
        fp = _resolve(project_path, file_path)
        if not fp.exists():
            return _err(f"File not found: {fp}", error_name="PATH_NOT_FOUND")
        if fp.is_dir():
            return _err(f"Is a directory: {fp}", error_name="IS_DIRECTORY")
        raw = fp.read_bytes()
        lines = _read_lines(fp)
        eol = _detect_eol(fp)
        had_final_eol = raw.endswith(b"\n")
        total = len(lines)
        at_line = max(1, min(int(at_line or 0), total + 1))
        after_line = max(0, min(int(after_line or 0), total))
        if at_line and after_line:
            return _err("at_line and after_line are mutually exclusive; pass only one.",
                        error_name="AMBIGUOUS_INSERT_POINT")
        insert_idx = total
        if after_line:
            insert_idx = after_line  # after_line is 1-based -> index == after_line
        elif at_line:
            insert_idx = at_line - 1  # at_line is 1-based, insert BEFORE it
        new_lines = text.splitlines()
        built = lines[:insert_idx] + new_lines + lines[insert_idx:]
        if dry_run:
            return json.dumps({
                "ok": True, "dry_run": True, "file": str(fp),
                "insert_index": insert_idx, "new_lines_count": len(new_lines),
                "preview": text[:4000], "preview_truncated": len(text) > 4000,
            }, ensure_ascii=False)
        bak = _backup(fp)
        _write_lines(fp, built, eol, had_final_eol)
        return _write_result(fp, project_path, backup=bak,
                             start_line=insert_idx + 1, end_line=insert_idx + len(new_lines),
                             changed_lines=len(new_lines),
                             extra={"inserted_at_line": insert_idx + 1})
    except Exception as exc:
        return _err(f"{type(exc).__name__}: {exc}", error_name="INSERT_TEXT_ERROR")


async def replace_symbol(file_path: str, symbol: str, new_content: str,
                         content_encoding: str = "none",
                         project_path: str = "", dry_run: bool = False, **_kw) -> str:
    """T2 MUTATING: replace a function/class/var body by symbol name using 0-indent
    def/class/`name =` scan. Indented (nested) symbols are NOT matched. Rejects when
    the symbol is missing or the new content is empty. Writes .bak + syncs cache."""
    try:
        if not symbol:
            return _err("symbol is required", error_name="MISSING_REQUIRED_FIELD")
        text, decode_err = _decode(new_content, content_encoding)
        if decode_err:
            return json.dumps(decode_err, ensure_ascii=False)
        fp = _resolve(project_path, file_path)
        if not fp.exists():
            return _err(f"File not found: {fp}", error_name="PATH_NOT_FOUND")
        raw = fp.read_bytes()
        lines = _read_lines(fp)
        eol = _detect_eol(fp)
        had_final_eol = raw.endswith(b"\n")
        total = len(lines)
        start = None
        for i, ln in enumerate(lines):
            stripped = ln.lstrip()
            if not stripped or stripped.startswith(("#", "//", "/*", "*", '"""', "'''")):
                continue
            if ln[:1].isspace():
                continue
            if (stripped.startswith(("def ", "class ", "async def ")) and
                    any(_def_name(tok) == symbol for tok in stripped.split()[1:3])):
                start = i
                break
            if "=" in ln and ln.index("=") > 0:
                lhs = ln[: ln.index("=")].strip()
                if lhs == symbol:
                    start = i
                    break
        if start is None:
            return _err(f"Symbol not found: {symbol} (top-level only).",
                        error_name="SYMBOL_NOT_FOUND")
        end = start + 1
        while end < total and (lines[end][:1].isspace() or not lines[end].strip()):
            end += 1
        new_lines = text.splitlines()
        built = lines[:start] + new_lines + lines[end:]
        if dry_run:
            return json.dumps({
                "ok": True, "dry_run": True, "file": str(fp), "symbol": symbol,
                "replaced_range": f"{start + 1}-{end}", "removed_lines": end - start,
                "preview": text[:4000], "preview_truncated": len(text) > 4000,
            }, ensure_ascii=False)
        bak = _backup(fp)
        _write_lines(fp, built, eol, had_final_eol)
        return _write_result(fp, project_path, backup=bak,
                             start_line=start + 1, end_line=end,
                             changed_lines=end - start,
                             extra={"symbol": symbol, "replaced_range": f"{start + 1}-{end}"})
    except Exception as exc:
        return _err(f"{type(exc).__name__}: {exc}", error_name="REPLACE_SYMBOL_ERROR")


async def move_file(file_path: str, destination: str, project_path: str = "",
                    overwrite: bool = False, **_kw) -> str:
    """T2 MUTATING: move (rename) a file. Creates destination parent dirs.
    Refuses to overwrite an existing destination unless overwrite=true."""
    try:
        if not destination:
            return _err("destination is required", error_name="MISSING_REQUIRED_FIELD")
        src = _resolve(project_path, file_path)
        if not src.exists():
            return _err(f"Source not found: {src}", error_name="PATH_NOT_FOUND")
        if src.is_dir():
            return _err(f"Source is a directory (move_file is file-only): {src}",
                        error_name="IS_DIRECTORY")
        dst = _resolve(project_path, destination)
        if dst.exists() and not overwrite:
            return _err(f"Destination exists: {dst}. Pass overwrite=true to replace.",
                        error_name="DESTINATION_EXISTS")
        if dst.resolve() == src.resolve():
            return _err("Source and destination are the same path.", error_name="SAME_PATH")
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(src), str(dst))
        return json.dumps({
            "ok": True, "moved": True, "from": str(src), "to": str(dst),
            "source_removed": not src.exists(), "target_exists": dst.exists(),
            **_sync_cache(dst, project_path),
        }, ensure_ascii=False)
    except Exception as exc:
        return _err(f"{type(exc).__name__}: {exc}", error_name="MOVE_FILE_ERROR")


async def copy_range(file_path: str, start_line: int, end_line: int,
                     destination: str, at_line: int = 0, append: bool = True,
                     project_path: str = "", **_kw) -> str:
    """T2 MUTATING: copy a line range from file_path into `destination`.
    Default appends to the destination; pass at_line to insert at a 1-based line.
    Never modifies the source. Destination parent dirs are created."""
    try:
        fp = _resolve(project_path, file_path)
        if not fp.exists():
            return _err(f"Source not found: {fp}", error_name="PATH_NOT_FOUND")
        start_line = max(1, int(start_line or 1))
        end_line = max(start_line, int(end_line or start_line))
        src_lines = _read_lines(fp)
        src_eol = _detect_eol(fp)
        total = len(src_lines)
        if start_line > total:
            return _err(f"start_line {start_line} exceeds file line count {total}.",
                        error_name="RANGE_OUT_OF_BOUNDS")
        end_line = min(end_line, total)
        chunk = src_lines[start_line - 1:end_line]
        dst = _resolve(project_path, destination)
        dst.parent.mkdir(parents=True, exist_ok=True)
        dst_exists = dst.exists()
        dst_raw = dst.read_bytes() if dst_exists else b""
        dst_lines = _read_lines(dst) if dst_exists else []
        dst_eol = _detect_eol(dst) if dst_exists else src_eol
        dst_had_final_eol = dst_raw.endswith(b"\n") if dst_exists else True
        if append and not at_line:
            built = dst_lines + chunk
        else:
            idx = max(0, min(int(at_line or 0) - 1, len(dst_lines)))
            built = dst_lines[:idx] + chunk + dst_lines[idx:]
        _write_lines(dst, built, dst_eol, dst_had_final_eol)
        return json.dumps({
            "ok": True, "copied_lines": len(chunk), "from": str(fp),
            "source_range": f"{start_line}-{end_line}", "to": str(dst),
            "inserted_at_line": (len(dst_lines) + 1 if append and not at_line else (at_line or 1)),
            **_sync_cache(dst, project_path),
        }, ensure_ascii=False)
    except Exception as exc:
        return _err(f"{type(exc).__name__}: {exc}", error_name="COPY_RANGE_ERROR")


async def cut_range(file_path: str, start_line: int, end_line: int,
                    project_path: str = "", dry_run: bool = False, **_kw) -> str:
    """T2 MUTATING: delete a line range from a file. Writes .bak backup + syncs cache.
    Removed lines are returned so the caller can restore with insert_text if needed."""
    try:
        fp = _resolve(project_path, file_path)
        if not fp.exists():
            return _err(f"File not found: {fp}", error_name="PATH_NOT_FOUND")
        raw = fp.read_bytes()
        lines = _read_lines(fp)
        eol = _detect_eol(fp)
        had_final_eol = raw.endswith(b"\n")
        total = len(lines)
        start_line = max(1, int(start_line or 1))
        end_line = max(start_line, int(end_line or start_line))
        if start_line > total:
            return _err(f"start_line {start_line} exceeds file line count {total}.",
                        error_name="RANGE_OUT_OF_BOUNDS")
        end_line = min(end_line, total)
        removed = lines[start_line - 1:end_line]
        built = lines[:start_line - 1] + lines[end_line:]
        if dry_run:
            return json.dumps({
                "ok": True, "dry_run": True, "file": str(fp),
                "remove_range": f"{start_line}-{end_line}", "removed_lines_count": len(removed),
                "removed_preview": "\n".join(removed)[:4000],
            }, ensure_ascii=False)
        bak = _backup(fp)
        _write_lines(fp, built, eol, had_final_eol)
        return _write_result(fp, project_path, backup=bak,
                             start_line=start_line, end_line=end_line,
                             changed_lines=len(removed),
                             extra={"removed_range": f"{start_line}-{end_line}",
                                    "removed_lines_count": len(removed)})
    except Exception as exc:
        return _err(f"{type(exc).__name__}: {exc}", error_name="CUT_RANGE_ERROR")


UPGRADE_P12_SCHEMA_SPECS = [
    ("insert_text",
     "T2 MUTATING: insert text into an existing file at a 1-based line. at_line inserts BEFORE that line; after_line inserts AFTER that line (mutually exclusive; omit both to append). Writes a .bak backup and syncs the project file cache. Supports content_encoding=base64. Mutating tool — requires session.",
     {"file_path": {"type": "string", "description": "Target file (absolute or relative to project_path)."},
      "content": {"type": "string", "description": "Text to insert (can be multi-line)."},
      "at_line": {"type": "integer", "default": 0, "description": "1-based line to insert BEFORE; 0/omitted = use after_line or append."},
      "after_line": {"type": "integer", "default": 0, "description": "1-based line to insert AFTER; mutually exclusive with at_line."},
      "content_encoding": {"type": "string", "default": "none", "enum": ["none", "base64"], "description": "Base64-decode content first (Unicode/Vietnamese safe)."},
      "project_path": {"type": "string", "default": "", "description": "Optional root for relative paths."},
      "dry_run": {"type": "boolean", "default": False, "description": "Preview insert index without writing."}},
     ["file_path", "content"]),
    ("replace_symbol",
     "T2 MUTATING: replace a top-level function/class/var definition by symbol name in an existing file. Matches `def NAME`, `async def NAME`, `class NAME`, or `NAME =`. Nested/indented symbols are not matched. Writes .bak + syncs cache. Supports content_encoding=base64. Mutating tool — requires session.",
     {"file_path": {"type": "string", "description": "Target file."},
      "symbol": {"type": "string", "description": "Top-level symbol name to replace."},
      "new_content": {"type": "string", "description": "Replacement body/text for the symbol."},
      "content_encoding": {"type": "string", "default": "none", "enum": ["none", "base64"], "description": "Base64-decode new_content first."},
      "project_path": {"type": "string", "default": "", "description": "Optional root."},
      "dry_run": {"type": "boolean", "default": False, "description": "Preview replaced range without writing."}},
     ["file_path", "symbol", "new_content"]),
    ("move_file",
     "T2 MUTATING: move (rename) a single file. Creates destination parent dirs. Refuses to overwrite an existing destination unless overwrite=true. Mutating tool — requires session.",
     {"file_path": {"type": "string", "description": "Source file path."},
      "destination": {"type": "string", "description": "Destination path."},
      "project_path": {"type": "string", "default": "", "description": "Optional root for relative paths."},
      "overwrite": {"type": "boolean", "default": False, "description": "Replace existing destination."}},
     ["file_path", "destination"]),
    ("copy_range",
     "T2 MUTATING: copy a 1-based line range from file_path into `destination`. Defaults to appending; pass at_line to insert at a 1-based line. The source is never modified. Destination parent dirs are created. Mutating tool — requires session.",
     {"file_path": {"type": "string", "description": "Source file."},
      "start_line": {"type": "integer", "description": "1-based start line."},
      "end_line": {"type": "integer", "description": "1-based end line (inclusive)."},
      "destination": {"type": "string", "description": "Destination file (created/append/insert)."},
      "at_line": {"type": "integer", "default": 0, "description": "1-based insert line in destination (0/omitted + append=true = append)."},
      "append": {"type": "boolean", "default": True, "description": "Append to destination when at_line is 0."},
      "project_path": {"type": "string", "default": "", "description": "Optional root."}},
     ["file_path", "start_line", "end_line", "destination"]),
    ("cut_range",
     "T2 MUTATING: delete a 1-based line range from a file. Writes .bak backup + syncs cache. Returns the removed lines so the caller can restore with insert_text if needed. Mutating tool — requires session.",
     {"file_path": {"type": "string", "description": "Target file."},
      "start_line": {"type": "integer", "description": "1-based start line."},
      "end_line": {"type": "integer", "description": "1-based end line (inclusive)."},
      "project_path": {"type": "string", "default": "", "description": "Optional root."},
      "dry_run": {"type": "boolean", "default": False, "description": "Preview removed lines without writing."}},
     ["file_path", "start_line", "end_line"]),
]
