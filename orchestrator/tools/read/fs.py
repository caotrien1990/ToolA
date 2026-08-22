from __future__ import annotations

import asyncio
import hashlib
import json
import logging
from pathlib import Path

from orchestrator.fileops import common as fo

MAX_READ_LINES = fo.MAX_READ_LINES

# CAN-053: negative-path cache + miss-streak breaker state (per process).
_NEG_PATH_CACHE: dict = {}
_MISS_STREAK: dict = {}
_NEG_TTL_S = 600.0


async def list_structure(path: str = "", depth: int = 2, save_output: bool = False,
                         project_path: str = "", compact: bool = False,
                         verbosity: str = "", strict: bool = False) -> str:
    # CAN-051: save_output default flipped True->False — a T1 read tool must not
    # write structure_output.txt unless explicitly asked (mutation_scope=cache).
    if path and project_path and not Path(path).is_absolute():
        path = str(Path(project_path) / path)
    else:
        path = path or project_path or "."
    base = Path(path)
    if not base.exists():
        if not strict:
            return json.dumps({
                "ok": True,
                "exists": False,
                "path": str(base),
                "error_name": "PATH_NOT_FOUND",
                "ca_next_action": "treat_as_empty_probe_or_retry_with_strict_true",
            }, ensure_ascii=False)
        return json.dumps({"ok": False, "error": f"Path not found: {path}", "error_name": "PATH_NOT_FOUND", "path": str(base)})

    effective_compact = compact or (verbosity == "terse")
    effective_depth = min(depth, 2) if effective_compact else depth
    truncated_folders: list[str] = []

    # B027e 2026-08-23. The last skip gate the overlay could not reach. This tool
    # ALREADY filtered through should_skip_dir, so it was never the "raw tree" view
    # its missing root suggested -- it was a filtered view whose filter a project
    # could not correct, which put `!name` out of reach at the one surface a human
    # uses to check what their .orchestrator_ignore actually did.
    #
    # ONE direction only, on purpose: `!name` REVEALS, and an overlay must never be
    # able to HIDE a directory from a structure listing. A listing that silently
    # omits a real directory is how a caller concludes a file does not exist, which
    # is CASE-2008's shape. `_unskipped` therefore asks should_skip_dir twice and
    # only accepts the answer that shows more, never less. Pinned by
    # test_an_extra_skip_is_deliberately_not_honoured.
    overlay_root = project_path or (str(base) if base.is_dir() else "")

    def _hidden(name: str) -> bool:
        if not fo.should_skip_dir(name):
            return False
        if overlay_root and not fo.should_skip_dir(name, overlay_root):
            return False  # an explicit !name un-skip: reveal it
        return True

    def _build(current: Path, current_depth: int, prefix: str) -> list[str]:
        if current_depth > effective_depth:
            return []
        lines: list[str] = []
        try:
            entries = sorted(current.iterdir(), key=lambda e: (e.is_file(), e.name.lower()))
        except PermissionError:
            return []
        entries = [entry for entry in entries if not _hidden(entry.name)]
        if effective_compact and len(entries) > 30:
            files_only = [e for e in entries if e.is_file()]
            dirs_only = [e for e in entries if e.is_dir()]
            shown_files = files_only[:10]
            remaining_files = len(files_only) - len(shown_files)
            display_entries = dirs_only + shown_files
            try:
                rel = str(current.relative_to(base))
            except ValueError:
                rel = current.name
            truncated_folders.append(f"{rel} ({remaining_files} more files hidden)")
            for entry in display_entries:
                connector = "+-- " if entry == display_entries[-1] else "|-- "
                lines.append(f"{prefix}{connector}{entry.name}{'/' if entry.is_dir() else ''}")
                if entry.is_dir():
                    extension = "    " if entry == display_entries[-1] else "|   "
                    lines.extend(_build(entry, current_depth + 1, prefix + extension))
            if remaining_files > 0:
                lines.append(f"{prefix}+-- ... ({remaining_files} more files)")
            return lines
        for entry in entries:
            connector = "+-- " if entry == entries[-1] else "|-- "
            lines.append(f"{prefix}{connector}{entry.name}{'/' if entry.is_dir() else ''}")
            if entry.is_dir():
                extension = "    " if entry == entries[-1] else "|   "
                lines.extend(_build(entry, current_depth + 1, prefix + extension))
        return lines

    def _run() -> str:
        lines = [f"{base.name}/"]
        lines.extend(_build(base, 1, ""))
        tree_output = "\n".join(lines)
        if save_output:
            try:
                markers = {".git", "package.json", "pyproject.toml", ".orchestrator_cache", "setup.py", "Cargo.toml"}
                if any((base / marker).exists() for marker in markers):
                    cache_out = fo.project_cache_dir(str(base))
                    cache_out.mkdir(parents=True, exist_ok=True)
                    fo.atomic_write_text(cache_out / "structure_output.txt", tree_output, encoding="utf-8")
            except Exception:
                pass
        return tree_output

    result = await asyncio.to_thread(_run)
    response = {
        "ok": True,
        "result": result,
        "path": str(base),
        "depth": effective_depth,
        "save_output": save_output,
        "compact_mode": effective_compact,
    }
    if truncated_folders:
        response["truncated_folders"] = truncated_folders[:20]
        response["truncation_hint"] = "compact=true -> some folders summarized. Pass compact=false for full tree."
    return json.dumps(response, ensure_ascii=False)


def _seed_read_token_detailed(fp: Path, project_path: str | None, text: str,
                              stat_after, force_reseed: bool,
                              precomputed_hash: str = "") -> tuple[bool, dict]:
    """Seed the write-freshness token, and say WHY when it does not.

    P11 (campaign-1608): the token is keyed by (project_root, rel_path). When the
    caller omits project_path (e.g. read_file_range invoked through the `path`
    alias with an absolute file_path), infer the root from the absolute file_path
    via code_tools.find_project_root so a force_reseed is not a silent no-op. The
    caller (read_file_range) turns a failed force_reseed into a loud RESEED_FAILED
    rather than returning ok:true without a token.

    P11b: a reseed can fail three distinct ways, and P11's first cut printed the
    no-root message for all of them. Measured with a VALID project_path and a
    raising _read_token_upsert: "no project_path was supplied", plus a hint
    telling the caller to pass the project_path it had already passed. That is
    P11's own dead loop one layer up -- the difference is that here the caller
    BELIEVES the diagnosis. Return the reason so the caller can say the true one:

      no_project_root -- no project_path and none inferable (the P11 case)
      outside_root    -- the file does not live under the root that was resolved
      upsert_failed   -- root fine, the token store itself refused
    """
    try:
        import code_tools as ct

        root_str = project_path or ct.find_project_root(str(fp))
        if not root_str:
            return False, {"reason": "no_project_root", "error": ""}
        root = Path(root_str).resolve()
        try:
            rel = str(fp.relative_to(root)).replace("\\", "/")
        except ValueError as exc:
            return False, {"reason": "outside_root", "error": f"{type(exc).__name__}: {exc}",
                           "root": str(root)}
        digest = precomputed_hash or hashlib.sha256(
            text.encode("utf-8", errors="replace")).hexdigest()
        seeded = bool(ct._read_token_upsert(
            str(root), rel, digest, stat_after.st_mtime, stat_after.st_size))
        if seeded:
            return True, {}
        # Not an exception -- the store simply declined. Still not a success.
        return False, {"reason": "upsert_failed", "error": "_read_token_upsert returned falsy",
                       "root": str(root)}
    except Exception as exc:
        logging.getLogger(__name__).warning(
            "read_file_range reseed failed for %s: %s", fp, exc)
        return False, {"reason": "upsert_failed", "error": f"{type(exc).__name__}: {exc}"}


def _missing_file_response(fp: Path, file_path: str, project_path: str | None) -> str:
    """CAN-053 (codo P1): FILE_NOT_FOUND with negative-path cache, fuzzy
    suggestions from INDEX, and a 3-miss circuit breaker — stop blind probing."""
    import time as _t
    root_key = str(project_path or "").replace("\\", "/").rstrip("/").lower()
    rel_key = str(file_path).replace("\\", "/").lower()
    now = _t.time()
    if len(_NEG_PATH_CACHE) > 4096:
        _NEG_PATH_CACHE.clear()
    ent = _NEG_PATH_CACHE.get((root_key, rel_key))
    repeat = bool(ent and (now - ent["ts"]) < _NEG_TTL_S)
    if ent:
        ent["hits"] += 1
        ent["ts"] = now
    else:
        ent = {"ts": now, "hits": 1}
        _NEG_PATH_CACHE[(root_key, rel_key)] = ent
    streak = [t for t in _MISS_STREAK.get(root_key, []) if now - t < 30.0] + [now]
    _MISS_STREAK[root_key] = streak[-10:]
    suggestions = []
    try:
        if project_path:
            import difflib
            import os as _os
            import code_tools as ct
            idx = ct._load_index(str(Path(project_path).resolve()))
            if idx:
                names = {}
                for k in (idx.get("files") or {}):
                    names.setdefault(_os.path.basename(str(k)).lower(), str(k))
                base = _os.path.basename(rel_key)
                for m in difflib.get_close_matches(base, list(names), n=5, cutoff=0.6):
                    suggestions.append(names[m])
    except Exception:
        pass
    payload = {
        "ok": False,
        "error": f"File not found: {file_path}",
        "error_name": "FILE_NOT_FOUND",
        "exists": False,
        "repeat_probe": repeat,
        "probe_count": ent["hits"],
        "fuzzy_suggestions": suggestions,
        "ca_next_action": ("stop_probing_run_inventory_first" if len(streak) >= 3
                           else "check_fuzzy_suggestions_or_list_structure"),
    }
    if len(streak) >= 3:
        payload["circuit_breaker"] = {
            "miss_streak_30s": len(streak),
            "hint": "3+ blind path probes — call project_context_pack / list_structure(compact=true) / query_index(file_pattern=...) instead of guessing paths.",
        }
    if repeat:
        payload["hint"] = "This exact path already missed recently (negative cache). Use fuzzy_suggestions or inventory instead of re-probing."
    return json.dumps(payload, ensure_ascii=False)


async def read_file_range(
        file_path: str, start_line: int = 1, end_line: int | None = None,
        project_path: str | None = None, max_lines: int = MAX_READ_LINES,
        force_reseed: bool = False, scope: str = "range", symbol: str = "",
        section: str = "", related_query: str = "", query: str = "",
        token_budget: int = 0, small_file_threshold: int = 240,
        include_next_ranges: bool = True, content_hash: str = "",
        force_refresh: bool = False) -> str:
    """Backward-compatible entrypoint for adaptive structural reads."""
    from .context import read_file_range as adaptive_read_file_range

    return await adaptive_read_file_range(
        file_path=file_path, start_line=start_line, end_line=end_line,
        project_path=project_path, max_lines=max_lines,
        force_reseed=force_reseed, scope=scope, symbol=symbol,
        section=section, related_query=related_query, query=query,
        token_budget=token_budget, small_file_threshold=small_file_threshold,
        include_next_ranges=include_next_ranges, content_hash=content_hash,
        force_refresh=force_refresh)
