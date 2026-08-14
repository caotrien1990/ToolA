"""Write-draft loop: unlimited write size with part-level audit (2026-07-17).

Anh's contract change: write tools accept content of ANY size; when validation
fails the backend does NOT force the CA to resend everything. Instead:

1. write_fix / write_new_file validate the full proposed content BEFORE (and,
   as a safety net, after) touching the target. On failure they return a
   ``draft_id`` plus per-part findings (line-accurate where the checker gives
   lines, with numbered excerpts).
2. The CA fixes ONLY the failed range via ``write_draft_patch(draft_id,
   start_line, end_line, new_content)``. The backend re-audits the whole
   draft and reports the remaining failed parts.
3. The moment the draft is clean it ships automatically through the ORIGINAL
   write pipeline (backup, post-write validation, symbol check, cache sync,
   xroot/session guards) — so no guard is bypassed, only the retry loop is
   cheap and precise now.

Drafts live in memory only: the target file is never dirtied while iterating.
"""
from __future__ import annotations

import json
import re
import threading
import time
import uuid
from pathlib import Path
from typing import Any

DRAFT_TTL_SEC = 1800          # 30 min of fixing time per draft
MAX_DRAFTS = 40               # LRU cap across all projects
PART_SIZE = 100               # reporting granularity (lines per part)
_EXCERPT_BEFORE = 3
_EXCERPT_AFTER = 2

_DRAFTS: dict[str, dict[str, Any]] = {}
_LOCK = threading.RLock()

_LINE_PATTERNS = (
    re.compile(r"line\s+(\d{1,6})(?:[:,]\s*(?:col(?:umn)?\s*)?(\d{1,4}))?", re.IGNORECASE),
    re.compile(r"[/\\][^\s:]+:(\d{1,6})(?::(\d{1,4}))?"),
    re.compile(r"\((\d{1,6})[,:](\d{1,4})\)"),
    re.compile(r"(?:^|\s)(\d{1,6}):(\d{1,4})(?:\s|$)"),
)


def _now() -> float:
    return time.time()


def _sweep_locked() -> None:
    dead = [k for k, v in _DRAFTS.items() if v["expires_at_epoch"] < _now()]
    for k in dead:
        _DRAFTS.pop(k, None)
    if len(_DRAFTS) > MAX_DRAFTS:
        for k, _v in sorted(_DRAFTS.items(), key=lambda kv: kv[1]["updated_at_epoch"])[
                :len(_DRAFTS) - MAX_DRAFTS]:
            _DRAFTS.pop(k, None)


def _norm_lines(text: str) -> list[str]:
    lines = text.splitlines(keepends=True)
    if lines and not lines[-1].endswith("\n"):
        lines[-1] += "\n"
    return lines or ["\n"]


def _parse_lines_from_text(detail: str) -> list[dict[str, Any]]:
    """Best-effort extraction of (line, col) pairs from checker output."""
    found: list[dict[str, Any]] = []
    seen: set[int] = set()
    for pat in _LINE_PATTERNS:
        for m in pat.finditer(detail or ""):
            try:
                line = int(m.group(1))
            except (TypeError, ValueError):
                continue
            if line <= 0 or line in seen:
                continue
            seen.add(line)
            col = None
            if m.lastindex and m.lastindex >= 2 and m.group(2):
                try:
                    col = int(m.group(2))
                except ValueError:
                    col = None
            found.append({"line": line, "col": col})
        if found:
            break
    return found


async def _structured_validate(suffix: str, content: str) -> list[dict[str, Any]]:
    """Full-content audit -> list of {line, col, message, checker} (empty = clean).

    Layer 1 mirrors the pre-write syntax gates with structured locations.
    Layer 2 runs the same deep validate_file check the post-write path uses,
    so a clean draft cannot ship and then bounce off auto-revert.
    """
    import code_tools as ct
    from orchestrator.tools.write import core as _core

    errors: list[dict[str, Any]] = []
    suffix = (suffix or "").lower()

    if suffix == ".py":
        try:
            compile(content, "<draft>", "exec")
        except SyntaxError as exc:
            errors.append({"line": int(exc.lineno or 0) or None,
                           "col": int(exc.offset or 0) or None,
                           "message": str(exc.msg or "syntax error"),
                           "checker": "python-ast"})
    elif suffix == ".json":
        try:
            json.loads(content)
        except json.JSONDecodeError as exc:
            errors.append({"line": exc.lineno, "col": exc.colno,
                           "message": exc.msg, "checker": "json"})
    else:
        pre = await _core._pre_write_syntax_check(Path("draft" + (suffix or ".txt")), content)
        if not pre.get("ok"):
            detail = str(pre.get("detail") or pre.get("error") or "syntax error")
            located = _parse_lines_from_text(detail)
            if located:
                for loc in located[:20]:
                    errors.append({"line": loc["line"], "col": loc.get("col"),
                                   "message": detail[:400],
                                   "checker": pre.get("error_name", "pre_write_check")})
            else:
                errors.append({"line": None, "col": None, "message": detail[:400],
                               "checker": pre.get("error_name", "pre_write_check")})

    if errors:
        return errors

    # Layer 2: deep validation (same engine as the post-write gate).
    if suffix in getattr(_core, "_VALIDATE_EXTS", set()):
        tmp_path = ""
        try:
            with ct.tempfile.NamedTemporaryFile(
                    suffix=suffix or ".txt", delete=False, mode="w",
                    encoding="utf-8") as tmp:
                tmp.write(content)
                tmp_path = tmp.name
            val = ct.json.loads(await ct.validate_file(tmp_path))
            if not val.get("ok"):
                detail = str(val.get("error") or "validation failed")
                located = _parse_lines_from_text(detail)
                if located:
                    for loc in located[:20]:
                        errors.append({"line": loc["line"], "col": loc.get("col"),
                                       "message": detail[:400],
                                       "checker": "validate_file"})
                else:
                    errors.append({"line": None, "col": None,
                                   "message": detail[:400], "checker": "validate_file"})
        except Exception as exc:  # validator infra failure must not block writes
            errors_note = f"validate_file unavailable: {type(exc).__name__}"
            return [] if not errors else errors + [
                {"line": None, "col": None, "message": errors_note, "checker": "validate_file"}]
        finally:
            if tmp_path:
                try:
                    ct.os.unlink(tmp_path)
                except Exception:
                    pass
    return errors


def _excerpt(lines: list[str], line_no: int) -> str:
    lo = max(1, line_no - _EXCERPT_BEFORE)
    hi = min(len(lines), line_no + _EXCERPT_AFTER)
    out = []
    for n in range(lo, hi + 1):
        marker = ">>" if n == line_no else "  "
        out.append(f"{marker}{n}: {lines[n - 1].rstrip()}"[:220])
    return "\n".join(out)


def _view(rec: dict[str, Any]) -> dict[str, Any]:
    lines = rec["lines"]
    total = len(lines)
    errors = rec["last_errors"]
    by_part: dict[int, list[dict[str, Any]]] = {}
    file_level: list[dict[str, Any]] = []
    for err in errors:
        line = err.get("line")
        if not line or line < 1 or line > total:
            file_level.append(err)
            continue
        part_no = (line - 1) // PART_SIZE + 1
        by_part.setdefault(part_no, []).append(err)
    failed_parts = []
    for part_no in sorted(by_part):
        start = (part_no - 1) * PART_SIZE + 1
        end = min(part_no * PART_SIZE, total)
        errs = by_part[part_no]
        failed_parts.append({
            "part": part_no,
            "line_start": start,
            "line_end": end,
            "errors": errs,
            "excerpt": _excerpt(lines, errs[0]["line"]),
        })
    return {
        "draft": True,
        "draft_id": rec["draft_id"],
        "draft_tool": rec["tool"],
        "target_file": rec["target_file"],
        "total_lines": total,
        "part_size": PART_SIZE,
        "parts_total": (total + PART_SIZE - 1) // PART_SIZE,
        "clean": not errors,
        "failed_parts": failed_parts,
        "file_level_errors": file_level,
        "draft_expires_in_sec": max(0, int(rec["expires_at_epoch"] - _now())),
        "ca_next_action": (
            "clean" if not errors else
            "Sửa ĐÚNG range lỗi: write_draft_patch(draft_id, start_line, end_line, "
            "new_content, content_encoding='base64' nếu multiline). KHÔNG cần gửi lại "
            "toàn file — sạch hết lỗi là backend tự ship qua đúng pipeline write."),
    }


def _store(tool: str, fp: Path, project_path: str, file_path_param: str,
           proposed_text: str, orig: dict[str, Any],
           errors: list[dict[str, Any]]) -> dict[str, Any]:
    import code_tools as ct
    rec = {
        "draft_id": uuid.uuid4().hex[:16],
        "tool": tool,
        "target_file": str(fp),
        "file_path_param": file_path_param,
        "project_path": project_path or "",
        "target_existed": fp.exists(),
        "target_hash": ct._sha256_file(fp) if fp.exists() else "",
        "suffix": fp.suffix.lower(),
        "lines": _norm_lines(proposed_text),
        "orig": dict(orig or {}),
        "last_errors": errors,
        "created_at_epoch": _now(),
        "updated_at_epoch": _now(),
        "expires_at_epoch": _now() + DRAFT_TTL_SEC,
        "patch_count": 0,
    }
    with _LOCK:
        _sweep_locked()
        _DRAFTS[rec["draft_id"]] = rec
    return rec


async def draft_fields_for_failure(tool: str, fp: Path, project_path: str,
                                   file_path_param: str, proposed_text: str,
                                   error_text: str,
                                   orig: dict[str, Any] | None = None) -> dict[str, Any]:
    """Attach a draft to an existing failure response (post-write revert path)."""
    errors = await _structured_validate(fp.suffix.lower(), proposed_text)
    if not errors:
        errors = [{"line": None, "col": None,
                   "message": (error_text or "validation failed")[:400],
                   "checker": "post_write"}]
    rec = _store(tool, fp, project_path, file_path_param, proposed_text,
                 orig or {}, errors)
    return _view(rec)


async def draft_failure_response(tool: str, fp: Path, project_path: str,
                                 file_path_param: str, proposed_text: str,
                                 base_error: dict[str, Any],
                                 orig: dict[str, Any] | None = None) -> str:
    """Pre-write gate failure -> full JSON response carrying the draft."""
    import code_tools as ct
    fields = await draft_fields_for_failure(
        tool, fp, project_path, file_path_param, proposed_text,
        str(base_error.get("detail") or base_error.get("error") or ""), orig)
    out = {"ok": False}
    out.update({k: v for k, v in (base_error or {}).items() if k != "ok"})
    out.update(fields)
    return ct.json.dumps(out, ensure_ascii=False)


def _get(draft_id: str) -> dict[str, Any] | None:
    with _LOCK:
        _sweep_locked()
        return _DRAFTS.get(str(draft_id or ""))


async def _ship(rec: dict[str, Any]) -> dict[str, Any]:
    import code_tools as ct
    from orchestrator.tools.write import core as _core

    proposed = "".join(rec["lines"])
    fp = Path(rec["target_file"])
    orig = rec.get("orig") or {}

    if rec["tool"] == "write_new_file" or not fp.exists() \
            or not rec.get("target_existed"):
        res_raw = await _core.write_new_file(
            path=rec["file_path_param"], new_content=proposed,
            project_path=rec["project_path"], content_encoding="plain",
            overwrite=bool(orig.get("overwrite")) or fp.exists(),
            skip_pre_write_check=False)
    else:
        cur_hash = ct._sha256_file(fp)
        if rec["target_hash"] and cur_hash != rec["target_hash"]:
            return {
                "ok": False,
                "error_name": "STALE_TARGET",
                "error": "Target file changed since the draft was created; "
                         "re-read the file and start a fresh write.",
                "draft_id": rec["draft_id"],
                "draft_kept": True,
                "ca_next_action": "read_file_range then re-run the original write "
                                  "(draft content is still available via write_draft_status)",
            }
        total = len(fp.read_text(encoding="utf-8", errors="replace").splitlines())
        if total <= 0:
            res_raw = await _core.write_new_file(
                path=rec["file_path_param"], new_content=proposed,
                project_path=rec["project_path"], content_encoding="plain",
                overwrite=True, skip_pre_write_check=False)
        else:
            res_raw = await _core.write_fix(
                file_path=rec["file_path_param"], start_line=1, end_line=total,
                new_content=proposed, project_path=rec["project_path"],
                content_encoding="plain", expected_hash=cur_hash,
                skip_symbol_check=bool(orig.get("skip_symbol_check")))

    try:
        res = ct.json.loads(res_raw)
    except Exception:
        res = {"ok": False, "error": str(res_raw)[:400]}
    if res.get("ok"):
        res["draft_id"] = rec["draft_id"]
        res["shipped"] = True
        res["draft_patches_applied"] = rec["patch_count"]
        with _LOCK:
            _DRAFTS.pop(rec["draft_id"], None)
    else:
        # A failed ship may have opened an inner draft (post-write revert hook)
        # for the SAME content — drop it; the original draft stays authoritative.
        inner_id = res.get("draft_id")
        if inner_id and inner_id != rec["draft_id"]:
            with _LOCK:
                _DRAFTS.pop(inner_id, None)
        res["draft_id"] = rec["draft_id"]
        res["draft_kept"] = True
        with _LOCK:
            rec["last_errors"] = [{
                "line": None, "col": None,
                "message": str(res.get("error") or "ship failed")[:400],
                "checker": str(res.get("error_name") or "ship"),
            }]
        res.setdefault("ca_next_action",
                       "inspect error (vd symbol drift); sửa qua write_draft_patch "
                       "hoặc write_draft_abort nếu chủ đích bỏ")
    return res


# ─── Tools ───────────────────────────────────────────────────────────────────

async def write_draft_patch(draft_id: str, start_line: int, end_line: int,
                            new_content: str,
                            content_encoding: str = "none") -> str:
    """Patch ONLY a line range of a pending draft, re-audit, auto-ship when clean."""
    import code_tools as ct
    rec = _get(draft_id)
    if rec is None:
        return ct._json_error(
            f"draft_id not found or expired: {draft_id}",
            error_name="DRAFT_NOT_FOUND", retryable=False,
            ca_next_action="re-run the original write tool to open a new draft")

    new_content, encoding_check, decode_error = ct._decode_text_payload(
        new_content, content_encoding)
    if decode_error:
        return ct.json.dumps(decode_error, ensure_ascii=False)

    with _LOCK:
        total = len(rec["lines"])
        try:
            start = int(start_line)
            end = int(end_line)
        except (TypeError, ValueError):
            return ct._json_error("start_line/end_line must be integers",
                                  error_name="WRONG_ARGUMENTS")
        if start < 1 or start > total:
            return ct._json_error(
                f"start_line {start} out of range (draft has {total} lines)",
                error_name="RANGE_OUT_OF_BOUNDS",
                ca_next_action="use line numbers from failed_parts of this draft")
        if end < start:
            return ct._json_error(
                f"end_line {end} must be >= start_line {start}",
                error_name="RANGE_OUT_OF_BOUNDS")
        end = min(end, total)
        patch_lines = _norm_lines(new_content) if new_content else []
        rec["lines"][start - 1:end] = patch_lines
        rec["patch_count"] += 1
        rec["updated_at_epoch"] = _now()
        rec["expires_at_epoch"] = _now() + DRAFT_TTL_SEC
        proposed = "".join(rec["lines"])

    errors = await _structured_validate(rec["suffix"], proposed)
    with _LOCK:
        rec["last_errors"] = errors
    if errors:
        out = {"ok": False,
               "error_name": "DRAFT_STILL_FAILING",
               "patched_range": f"{start}-{end}",
               "encoding_check": encoding_check}
        out.update(_view(rec))
        return ct.json.dumps(out, ensure_ascii=False)

    result = await _ship(rec)
    result["patched_range"] = f"{start}-{end}"
    return ct.json.dumps(result, ensure_ascii=False)


async def write_draft_status(draft_id: str = "") -> str:
    """Inspect one draft (or list all pending drafts when draft_id is empty)."""
    import code_tools as ct
    if not draft_id:
        with _LOCK:
            _sweep_locked()
            items = [{
                "draft_id": r["draft_id"], "tool": r["tool"],
                "target_file": r["target_file"],
                "total_lines": len(r["lines"]),
                "failing": bool(r["last_errors"]),
                "patch_count": r["patch_count"],
                "expires_in_sec": max(0, int(r["expires_at_epoch"] - _now())),
            } for r in _DRAFTS.values()]
        return ct.json.dumps({"ok": True, "drafts": items, "count": len(items)},
                             ensure_ascii=False)
    rec = _get(draft_id)
    if rec is None:
        return ct._json_error(f"draft_id not found or expired: {draft_id}",
                              error_name="DRAFT_NOT_FOUND", retryable=False)
    out = {"ok": True}
    out.update(_view(rec))
    return ct.json.dumps(out, ensure_ascii=False)


async def write_draft_abort(draft_id: str) -> str:
    """Drop a pending draft without shipping anything."""
    import code_tools as ct
    with _LOCK:
        rec = _DRAFTS.pop(str(draft_id or ""), None)
    if rec is None:
        return ct._json_error(f"draft_id not found or expired: {draft_id}",
                              error_name="DRAFT_NOT_FOUND", retryable=False)
    return ct.json.dumps({"ok": True, "aborted": True, "draft_id": rec["draft_id"],
                          "target_file": rec["target_file"]}, ensure_ascii=False)
