# pyright: reportArgumentType=false, reportAssignmentType=false, reportOptionalMemberAccess=false, reportOptionalSubscript=false
# ─── Tool 6: write_fix ────────────────────────────────────────────────────────

from collections import OrderedDict
from contextlib import contextmanager
from pathlib import Path
import asyncio
import logging
import os
import threading
import time
_logger = logging.getLogger(__name__)

_VALIDATE_EXTS = {".html", ".js", ".ts", ".mjs", ".cjs", ".py", ".jsx", ".tsx", ".json", ".css", ".vue"}


# ── Hybrid per-path gate primitive (Shard A) ──
# Coordinates asyncio tasks AND cross-thread callers.
# Each path key owns a _GateEntry with both lock types + refcount.
# Held entries are pinned and never evicted (F4 fix).
# Acquire: thread first (cross-thread), then async (same-loop).
# Release: reverse in one finally.

class _GateEntry:
    """One per-path entry holding both async and thread locks with refcounting."""
    __slots__ = ('key', 'async_lock', 'thread_lock', 'refcount', '_guard')

    def __init__(self, key: str):
        self.key = key
        self.async_lock = asyncio.Lock()
        # RLock, not Lock: the auto-revert path calls _restore_backup_and_reseed(), which
        # takes the SYNCHRONOUS _path_lock() for the same file while this gate already
        # holds it on the same thread. With a plain Lock that second acquire blocked the
        # event loop forever and was not cancellable -- the hang behind
        # test_codoc_a2_validation_rollback_serialized_by_gate. Cross-task exclusion does
        # not weaken: same-loop writers are still serialised by async_lock below, and a
        # genuinely different thread still blocks on the RLock.
        self.thread_lock = threading.RLock()
        self.refcount = 0
        self._guard = threading.Lock()

    def _inc(self):
        with self._guard:
            self.refcount += 1

    def _dec(self):
        with self._guard:
            self.refcount = max(0, self.refcount - 1)

    @property
    def held(self) -> bool:
        with self._guard:
            return self.refcount > 0


_GATE_REGISTRY: "OrderedDict[str, _GateEntry]" = OrderedDict()
_GATE_GUARD = threading.Lock()
_GATE_CAP = 1024


def _normalize_lock_path(path: Path) -> str:
    try:
        return str(Path(path).resolve()).lower()
    except Exception:
        return str(Path(path).absolute()).lower()


_WRITE_GENERATIONS: dict[str, int] = {}
_WRITE_GENERATION_GUARD = threading.Lock()


def _next_write_generation(path: Path) -> int:
    key = _normalize_lock_path(path)
    with _WRITE_GENERATION_GUARD:
        generation = _WRITE_GENERATIONS.get(key, 0) + 1
        _WRITE_GENERATIONS[key] = generation
        return generation


def _current_write_generation(path: Path) -> int:
    with _WRITE_GENERATION_GUARD:
        return _WRITE_GENERATIONS.get(_normalize_lock_path(path), 0)


def _disk_signature(path: Path, file_hash: str = "") -> tuple[str, int, int, int] | None:
    try:
        stat = path.stat()
        return (file_hash or __import__("hashlib").sha256(path.read_bytes()).hexdigest(),
                stat.st_mtime_ns, stat.st_size, getattr(stat, "st_ino", 0))
    except FileNotFoundError:
        return None


def _gates_for_paths(paths: list[Path]) -> list[_GateEntry]:
    """Get or create gate entries for normalized paths.

    Held entries (refcount > 0) are pinned and never evicted (F4 fix).
    Only entries with refcount == 0 are candidates for eviction when the
    registry reaches capacity.
    """
    keys = sorted({_normalize_lock_path(p) for p in paths})
    entries: list[_GateEntry] = []
    with _GATE_GUARD:
        for key in keys:
            entry = _GATE_REGISTRY.get(key)
            if entry is None:
                # Evict only unheld entries when at capacity
                if len(_GATE_REGISTRY) >= _GATE_CAP:
                    target = _GATE_CAP // 2
                    for old_key in list(_GATE_REGISTRY.keys()):
                        old = _GATE_REGISTRY.get(old_key)
                        if old is not None and not old.held:
                            _GATE_REGISTRY.pop(old_key, None)
                        if len(_GATE_REGISTRY) < target:
                            break
                entry = _GateEntry(key)
                _GATE_REGISTRY[key] = entry
            else:
                _GATE_REGISTRY.move_to_end(key)
            entries.append(entry)
    return entries


class _GateHandle:
    """Acquired gate entries with deterministic release.

    Acquires thread_lock (cross-thread exclusion) then async_lock (same-loop).
    Refcount protects against eviction. Single finally block releases in
    reverse acquisition order.
    """
    __slots__ = ('_entries', '_async_done')

    def __init__(self, entries: list[_GateEntry]):
        self._entries = entries
        self._async_done = False

    async def __aenter__(self):
        # Stage 1: cross-thread exclusion, WITHOUT blocking the event loop.
        #
        # This used to call entry.thread_lock.acquire() straight through. That runs on
        # the loop thread, so two write_fix() coroutines gathered on the SAME loop
        # deadlocked permanently: task A took the lock and yielded mid-write, task B
        # entered here and blocked the whole loop waiting for A, and A could never be
        # resumed to release it. (tests/test_code_tools_cache.py
        # test_ac_g4_parallel_write_fix_same_file_serializes_full_content hung forever
        # and took the pytest run down with it.)
        #
        # Wait by polling with backoff rather than parking the wait in a worker thread:
        # asyncio.to_thread() fixes the loop-blocking half but is NOT cancellable, so a
        # writer cancelled while queued (wait_for timeout, task cancellation) left a
        # thread stuck in .acquire() forever and asyncio.run() then hung on
        # shutdown_default_executor(). Polling yields at an await point, so cancellation
        # lands in the handler below and the gate unwinds cleanly.
        #
        # Fast path is a plain non-blocking acquire — writes are almost never contended.
        acquired: list[_GateEntry] = []
        try:
            for entry in self._entries:
                delay = 0.001
                while not entry.thread_lock.acquire(blocking=False):
                    await asyncio.sleep(delay)
                    delay = min(delay * 2, 0.05)
                acquired.append(entry)
                entry._inc()
        except BaseException:
            # Never leak a partially acquired gate (cancellation lands here too).
            for entry in reversed(acquired):
                try:
                    entry.thread_lock.release()
                except RuntimeError:
                    pass
                entry._dec()
            raise
        # Stage 2: acquire same-loop exclusion
        for entry in self._entries:
            await entry.async_lock.acquire()
        self._async_done = True
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        # Single finally: release in reverse order
        for entry in reversed(self._entries):
            if self._async_done:
                try:
                    entry.async_lock.release()
                except RuntimeError:
                    pass
        for entry in reversed(self._entries):
            try:
                entry.thread_lock.release()
            except RuntimeError:
                pass
            entry._dec()
        return False  # do not suppress


def _gate_for_paths(paths: list[Path]) -> _GateHandle:
    """async context manager: gate write paths for deterministic exclusion.

    Returns a handle for use with 'async with'. Coordinates asyncio tasks
    (asyncio.Lock) and cross-thread callers (threading.Lock). Entries are
    refcount-pinned against eviction. Releases in one finally.
    """
    return _GateHandle(_gates_for_paths(paths))


@contextmanager
def _path_lock(paths: list[Path]):
    """Synchronous gate for thread-only callers (boot sweep, restore).

    Acquires only thread_lock with refcount pinning. Compatible with
    _gate_for_paths: an async holder's thread_lock blocks sync callers
    and vice versa.
    """
    entries = _gates_for_paths(paths)
    for entry in entries:
        entry.thread_lock.acquire()
        entry._inc()
    try:
        yield
    finally:
        for entry in reversed(entries):
            try:
                entry.thread_lock.release()
            except RuntimeError:
                pass
            entry._dec()


async def _pre_write_syntax_check(fp: Path, content: str) -> dict:
    import ast
    import json
    import re
    import code_tools as ct

    suffix = fp.suffix.lower()

    def _syntax_error(detail: str, ca_next_action: str = "") -> dict:
        return {
        "ok":False ,
        "error_name":"SYNTAX_ERROR_PRE_WRITE",
        "detail":detail ,
        "ca_next_action":ca_next_action or "fix syntax then retry",
        }

    def _compact(text: str) -> str:
        lines = [line.strip() for line in (text or "").splitlines() if line.strip()]
        compact = " | ".join(lines)
        return compact[:1200]

    def _run_external(cmd: list[str], suffix_for_temp: str, source: str) -> dict:
        tmp_path = ""
        out_path = ""
        try:
            with ct.tempfile.NamedTemporaryFile(
            suffix=suffix_for_temp, delete=False, mode="w", encoding="utf-8"
            ) as tmp:
                tmp.write(source)
                tmp_path = tmp.name
            actual_cmd = [part.format(input=tmp_path, output="{output}") for part in cmd]
            if any("{output}" in part for part in actual_cmd):
                fd, out_path = ct.tempfile.mkstemp(suffix=".js")
                ct.os.close(fd)
                actual_cmd = [part.format(output=out_path) for part in actual_cmd]
            resolved = ct.shutil.which(actual_cmd[0])
            if not resolved and actual_cmd[0] == "esbuild":
                appdata = ct.os.environ.get("APPDATA", "")
                candidate = ct.Path(appdata) / "npm" / "esbuild.cmd" if appdata else None
                if candidate and candidate.exists():
                    resolved = str(candidate)
            if resolved:
                actual_cmd[0] = resolved
            proc = ct.subprocess.run(
            actual_cmd,
            stdout=ct.subprocess.PIPE,
            stderr=ct.subprocess.PIPE,
            text=True,
            timeout=5,
            )
            if proc.returncode != 0:
                return _syntax_error(
                _compact(proc.stderr or proc.stdout),
                "fix syntax reported by parser then retry",
                )
            return {"ok":True}
        except ct.subprocess.TimeoutExpired:
            return {
            "ok":True ,
            "warn":"pre_write_syntax_check_timeout_5s",
            "checker":" ".join(cmd),
            }
        except FileNotFoundError as exc:
            return {
            "ok":True ,
            "warn":f"pre_write_syntax_check_checker_unavailable: {exc}",
            "checker":" ".join(cmd),
            }
        finally:
            for path in (tmp_path, out_path):
                if path:
                    try:
                        ct.os.unlink(path)
                    except Exception:
                        pass

    if suffix == ".py":
        try:
            ast.parse(content)
        except SyntaxError as exc:
            lineno = exc.lineno or 0
            detail = f"line {lineno}: {exc.msg}"
            return _syntax_error(detail, f"fix syntax at line {lineno} then retry")
        return {"ok":True}

    if suffix in {".js", ".mjs", ".cjs"}:
        return await ct.asyncio.to_thread(
        _run_external,
        ["node", "--check", "{input}"],
        suffix,
        content,
        )

    if suffix in {".ts", ".jsx", ".tsx"}:
        return await ct.asyncio.to_thread(
        _run_external,
        ["esbuild", "{input}", "--bundle=false", "--outfile={output}"],
        suffix,
        content,
        )

    if suffix == ".vue":
        match = re.search(r"<script\b[^>]*>(.*?)</script>", content, re.IGNORECASE | re.DOTALL)
        if not match:
            return {"ok":True}
        return await ct.asyncio.to_thread(
        _run_external,
        ["esbuild", "{input}", "--bundle=false", "--outfile={output}"],
        ".ts",
        match.group(1),
        )

    if suffix == ".json":
        try:
            json.loads(content)
        except json.JSONDecodeError as exc:
            return {
            "ok":False ,
            "error_name":"JSON_INVALID_PRE_WRITE",
            "detail":str(exc),
            "ca_next_action":"fix JSON syntax then retry",
            }
        return {"ok":True}

    # A-AUTO-UPGRADE (2026-07-12): html/css used to fall through with no
    # pre-write gate, so a broken write went write→validate_file→fail→revert
    # instead of being caught BEFORE touching disk. Mirrors validate_file's
    # html tag-balance and css brace-balance checks.
    if suffix in (".html", ".htm"):
        # OpeC7 bughunt F6: strip HTML comments first so a <script> mentioned
        # inside <!-- ... --> doesn't count as a real tag (false positive on
        # valid HTML). Attribute-value edge cases stay covered by validate_file
        # post-write + the skip_pre_write_check escape hatch.
        _h = re.sub(r"<!--.*?-->", "", content, flags=re.DOTALL)
        for tag in ("script", "style"):
            opens = len(re.findall(rf"<{tag}[\s>]", _h, re.IGNORECASE))
            closes = len(re.findall(rf"</{tag}\s*>", _h, re.IGNORECASE))
            if opens != closes:
                return {
                    "ok": False,
                    "error_name": "HTML_TAG_MISMATCH_PRE_WRITE",
                    "detail": f"{opens} <{tag}> vs {closes} </{tag}>",
                    "ca_next_action": f"balance <{tag}> tags in new_content then retry",
                }
        return {"ok": True}

    if suffix == ".css":
        # OpeC7 bughunt F5: strip comments + string literals before counting so
        # a brace inside content:"{" / "}" doesn't trigger a false mismatch on
        # valid CSS (and can't hide a real missing brace).
        _c = re.sub(r"/\*.*?\*/", "", content, flags=re.DOTALL)
        _c = re.sub(r'"(?:[^"\\]|\\.)*"', '""', _c)
        _c = re.sub(r"'(?:[^'\\]|\\.)*'", "''", _c)
        if _c.count("{") != _c.count("}"):
            return {
                "ok": False,
                "error_name": "CSS_BRACE_MISMATCH_PRE_WRITE",
                "detail": f"{_c.count('{')} open vs {_c.count('}')} close braces (strings/comments ignored)",
                "ca_next_action": "balance braces in new_content then retry",
            }
        return {"ok": True}

    return {"ok":True}


def _audit_skip_pre_write_check(file_path: str, caller: str) -> None:
    try:
        import logging
        logging.getLogger("[A]").warning(
        "skip_pre_write_check=true file_path=%s caller=%s",
        file_path,
        caller,
        )
    except Exception:
        pass


async def _post_write_symbol_check(fp: Path, backup_path: Path, project_path: str) -> dict:
    import ast
    import importlib.util
    import code_tools as ct

    def _top_level_symbols(source: str) -> set[str]:
        tree = ast.parse(source)
        return {
        node.name for node in tree.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))
        }

    def _module_candidates_exist(module: str, search_dirs) -> bool:
        for d in search_dirs:
            try:
                if (d / f"{module}.py").exists() or (d / module / "__init__.py").exists():
                    return True
            except Exception:
                continue
        return False

    def _check_imports(new_tree: ast.AST, old_tree=None) -> dict:
        """CAN-053 (codo P1): project-aware import validation.

        Only NEW top-level modules (diff vs the old source) are checked — a
        pre-existing import must never block an unrelated patch. Resolution
        order: stdlib -> file dir / project root / src / bin -> PathFinder over
        those dirs -> [A] environment (legacy, last). Never executes project code.
        """
        def _mods(tree) -> set:
            out = set()
            if tree is None:
                return out
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    for alias in node.names:
                        m = (alias.name or "").split(".")[0]
                        if m:
                            out.add(m)
                elif isinstance(node, ast.ImportFrom) and node.level == 0:
                    m = (node.module or "").split(".")[0]
                    if m:
                        out.add(m)
            return out

        new_modules = sorted(_mods(new_tree) - _mods(old_tree))
        if not new_modules:
            return {"ok": True}
        import sys as _sys
        from importlib.machinery import PathFinder as _PF
        try:
            root = ct.Path(project_path).resolve() if project_path else fp.parent
        except Exception:
            root = fp.parent
        search_dirs = [fp.parent, root, root / "src", root / "bin"]
        searched = [str(d) for d in search_dirs]
        stdlib = set(getattr(_sys, "stdlib_module_names", ()) or ())
        for module in new_modules:
            if module in stdlib:
                continue
            if _module_candidates_exist(module, search_dirs):
                continue
            try:
                if _PF.find_spec(module, [str(d) for d in search_dirs if d.exists()]) is not None:
                    continue
            except Exception:
                pass
            try:
                if importlib.util.find_spec(module) is not None:
                    continue
            except Exception:
                pass
            return {"ok": False, "module": module, "searched_paths": searched,
                    "new_modules_checked": new_modules}
        return {"ok": True}

    def _run() -> dict:
        new_text = fp.read_text(encoding="utf-8", errors="replace")
        old_text = backup_path.read_text(encoding="utf-8", errors="replace")
        try:
            new_tree = ast.parse(new_text)
            old_tree = ast.parse(old_text)
        except SyntaxError:
            return {"ok":True}
        import_check = _check_imports(new_tree, old_tree)
        if not import_check.get("ok"):
            return {
            "ok":False,
            "error_name":"IMPORT_ERROR_PRE_WRITE",
            "error":f"Import not found: {import_check.get('module')} (new import; resolved against project paths, not only [A] env)",
            "searched_paths": import_check.get("searched_paths"),
            "new_modules_checked": import_check.get("new_modules_checked"),
            }
        removed = sorted(_top_level_symbols(old_text) - _top_level_symbols(new_text))
        if removed:
            return {
            "ok":False,
            "error_name":"SYMBOL_DRIFT_AUTO_REVERT",
            "error":f"Top-level symbol(s) removed: {removed}",
            "symbols_removed":removed,
            }
        return {"ok":True}

    if fp.suffix.lower() != ".py":
        return {"ok":True}
    try:
        return await ct.asyncio.wait_for(ct.asyncio.to_thread(_run), timeout=5)
    except ct.asyncio.TimeoutError:
        return {"ok":True, "warn":"symbol_check_timeout_5s"}


async def _post_write_batch_verify_or_revert(project_path: str, changed_files: list[str],
backup_paths: dict[str, str]) -> dict:
    import code_tools as ct
    if not changed_files or not project_path:
        return {"ok":True, "skipped":True, "reason":"missing_project_path_or_changed_files"}
    verify = ct.json.loads(await ct.post_write_batch_verify(
    project_path=project_path,
    changed_files=changed_files,
    run_validate_symbols=True,
    run_cache_delta=True,
    ))
    if verify.get("ok"):
        return {"ok":True, "validation":verify}
    root = ct.Path(project_path).resolve()
    for rel in changed_files:
        bak = backup_paths.get(rel)
        fp = ct._resolve_project_file(str(root), rel)
        if bak and ct.Path(bak).exists():
            _restore_backup_and_reseed(fp, ct.Path(bak), project_path)
    return {
    "ok":False,
    "error_name":"BATCH_VERIFY_FAILED_AUTO_REVERT",
    "error":"Batch verification failed; reverted changed files from backups.",
    "auto_reverted":True,
    "validation":verify,
    }


def _restore_backup_and_reseed(fp: Path, backup_path: Path, project_path: str = "", already_gated: bool = False) -> bool:
    import code_tools as ct
    def _do() -> bool:
        original = backup_path.read_text(encoding="utf-8", errors="replace")
        ct._atomic_write_text(fp, original, encoding="utf-8")
        return ct._update_cache_after_write(str(fp), project_path or None)
    if already_gated:
        return _do()
    with _path_lock([fp]):
        return _do()


def _index_missing_error(fp: Path, project_path: str) -> dict | None:
    """R83: the "INDEX not found" precondition, callable BEFORE the write branch.

    The gate used to live only inside _write(), which runs after the dry_run branch
    has already returned. So dry_run=true answered ok:true with a diff and a
    dry_run_id, and the identical payload with dry_run=false came back ok:false
    "INDEX not found" seconds later — the CA was handed a promise the real write
    could not keep. Same precondition, checked on both paths; dry-run reports it as
    a blocker instead of pretending the apply will succeed."""
    import code_tools as ct
    from orchestrator.fileops.xroot import is_outside_project_root

    # Mirror _write_fix_guard_result's own preconditions exactly, or the dry-run
    # refuses writes the real path would have allowed: a target outside the project
    # root is governed by the xroot policy, not by this project's INDEX, and the
    # guard bails on it before ever looking at INDEX.json. write_transaction
    # pre-checks each part with an internal dry-run, so an over-eager gate here
    # would break cross-root transactions that work fine.
    if project_path and is_outside_project_root(fp, project_path):
        return None
    _root = project_path or ct.find_project_root(str(fp))
    if not _root:
        return None
    if (ct._project_cache_dir(_root) / "INDEX.json").exists():
        return None
    return ct.json.loads(ct._json_error(
        "INDEX not found. Call generate_project_map before writing.",
        action_required="generate_project_map",
        project_path=str(ct.Path(_root).resolve()),
    ))


def _write_fix_guard_result(fp: Path, project_path: str, file_path: str, expected_hash: str) -> tuple[bool, dict, bool]:
    import code_tools as ct
    from orchestrator.fileops.xroot import is_outside_project_root

    if project_path and is_outside_project_root(fp, project_path):
        return False, {}, False

    _root = project_path or ct.find_project_root(str(fp))
    if not _root:
        return False, {}, False
    _idx_path = ct._project_cache_dir(_root) / "INDEX.json"
    if not _idx_path.exists():
        return True, ct.json.loads(ct._json_error(
            "INDEX not found. Call generate_project_map before writing.",
            action_required="generate_project_map",
            project_path=str(ct.Path(_root).resolve()),
        )), True
    try:
        _idx = ct.json.loads(_idx_path.read_text(encoding="utf-8"))
        _rel = str(fp.relative_to(ct.Path(_root).resolve())).replace("\\", "/")
        _entry = (_idx.get("files", {}).get(_rel)
                  or _idx.get("files", {}).get(_rel.replace("/", "\\"))) or {}
        # CAN-052: read tokens live in the session-store token table now
        # (read_file_range no longer rewrites INDEX.json). INDEX entry values
        # stay as legacy fallback for tokens seeded before the migration.
        _tok = {}
        try:
            _tok = ct._read_token_get(str(ct.Path(_root).resolve()), _rel) or {}
        except Exception:
            _tok = {}
        if not _entry and not _tok:
            return True, {}, False

        _stat = fp.stat()
        _current_hash = ""
        _current_read_hash = ""
        if expected_hash:
            _current_hash = ct._sha256_file(fp)

        if expected_hash and _current_hash == expected_hash:
            try:
                ct._read_token_upsert(str(ct.Path(_root).resolve()), _rel,
                                      _current_hash, _stat.st_mtime, _stat.st_size)
            except Exception:
                pass
            return True, {}, False

        _read_hash = _tok.get("read_hash") or _entry.get("read_hash")
        _read_mtime = float(_tok.get("read_mtime") or _entry.get("read_mtime") or 0)
        _read_size = _tok.get("read_size_bytes")
        if _read_size is None:
            _read_size = _entry.get("read_size_bytes")
        if _read_hash or _read_mtime:
            if _read_hash:
                _current_text = fp.read_text(encoding="utf-8", errors="replace")
                if _current_text.startswith("\ufeff"):
                    _current_text = _current_text[1:]
                _current_read_hash = ct.hashlib.sha256(
                    _current_text.encode("utf-8", errors="replace")
                ).hexdigest()
            _hash_changed = bool(_read_hash and _current_read_hash != _read_hash)
            _mtime_changed = bool(_read_mtime and abs(_stat.st_mtime - _read_mtime) > 0.5)
            _size_changed = _read_size is not None and int(_read_size) != _stat.st_size
            if _hash_changed or _mtime_changed or _size_changed:
                return True, _file_changed_error(ct, fp), True
            return True, {}, False

        _indexed_mtime = float(_entry.get("mtime") or 0)
        if _indexed_mtime > 0 and abs(_stat.st_mtime - _indexed_mtime) > 0.5:
            if expected_hash:
                _current_hash = _current_hash or ct._sha256_file(fp)
                if _current_hash == expected_hash:
                    _entry["mtime"] = _stat.st_mtime
                    ct._atomic_write_json(_idx_path, _idx)
                    return True, {}, False
            return True, _file_changed_error(ct, fp), True
    except ct.json.JSONDecodeError as _idx_exc:
        _log_wf = __import__("logging")
        _log_wf.warning(f"write_fix: INDEX.json is corrupt: {_idx_exc}")
        return True, ct.json.loads(ct._json_error(
            "INDEX.json is unreadable. Re-seed the file token; do not refresh a large root.",
            error_name="STALE_INDEX",
            action_required="read_file_range",
            hint=(
                "Call read_file_range(project_path, file_path, force_reseed=true), "
                "then retry write_fix once. If the INDEX is still corrupt, use cache_delta. "
                "Never refresh_project_map on a large root such as [A:P]."
            ),
            ca_next_action="read_file_range",
            fallback_sequence=["read_file_range", "cache_delta"],
        )), True
    except Exception as _idx_exc:
        _log_wf2 = __import__("logging")
        _log_wf2.warning(f"write_fix: guardrail skipped due to unexpected error: {_idx_exc}")
    return True, {}, False


def _file_changed_error(ct, fp: Path) -> dict:
    return ct.json.loads(ct._json_error(
        "File modified outside Orchestrator since last read token. Re-read file before writing.",
        error_name="FILE_CHANGED_OUTSIDE_ORCHESTRATOR",
        action_required="read_file_range",
        file_path=str(fp),
        hint=(
            "Call read_file_range(project_path, file_path, start_line, end_line, force_reseed=true) "
            "to reseed the stable read token, then retry write_fix once. "
            "Never refresh_project_map on a large root such as [A:P]."
        ),
        ca_next_action="read_file_range",
        fallback_sequence=["read_file_range", "cache_delta"],
        expected_when="File changed after the last stable read token, or INDEX has no current read token.",
        one_step_recovery="read_file_range(project_path, file_path, start_line, end_line, force_reseed=true) -> retry write_fix once.",
        bypass_hint=(
            "R43 P0 #3-A: if YOU wrote this file in a prior "
            "write_fix this round, pass expected_hash from "
            "that response's new_hash field to skip this guard."
        ),
    ))


def _boot_sweep_abs_path(value: str | os.PathLike) -> Path:
    """Normalize lexically without filesystem-resolving every indexed path."""
    return Path(os.path.abspath(os.fspath(value)))


def _boot_sweep_max_session_roots() -> int:
    try:
        return max(1, int(os.environ.get("ORCHESTRATOR_BOOT_SWEEP_MAX_SESSION_ROOTS", "16")))
    except (TypeError, ValueError):
        return 16


_BOOT_SWEEP_SKIP_DIR_NAMES = frozenset({
    "venv312", ".venv", "venv", "node_modules", "__pycache__",
})


def _boot_sweep_root_usable(path: Path) -> bool:
    """Skip missing trees, unreadable roots, and heavy virtualenv/node caches."""
    try:
        if not path.exists() or not path.is_dir():
            return False
    except Exception:
        return False
    skip = {name.lower() for name in _BOOT_SWEEP_SKIP_DIR_NAMES}
    if any(part.lower() in skip for part in path.parts):
        return False
    # A22 layer 2: exists()/is_dir() is not enough. A root can pass both yet be
    # unreadable (permission denied, dead network share, damaged ACL) and then
    # explode later when the sweep stats INDEX.json under it. os.access(R_OK) is
    # unreliable on Windows (returns True for read-denied dirs), so probe an
    # actual directory read instead: if we cannot list it, reject it here.
    try:
        with os.scandir(path) as it:
            next(it, None)
    except Exception:
        return False
    return True


def _boot_sweep_project_roots(extra_roots: list[str] | None = None) -> list[Path]:
    import code_tools as ct
    roots: dict[str, Path] = {}
    for root in extra_roots or []:
        try:
            p = _boot_sweep_abs_path(root)
            roots[os.path.normcase(str(p))] = p
        except Exception:
            pass
    try:
        with ct._with_sessions_db() as conn:
            rows = conn.execute(
                """SELECT project_path, MAX(rowid) AS rid
                   FROM sessions
                   WHERE project_path IS NOT NULL AND project_path != ''
                   GROUP BY project_path
                   ORDER BY rid DESC"""
            ).fetchall()
        session_added = 0
        cap = _boot_sweep_max_session_roots()
        for row in rows:
            if session_added >= cap:
                break
            try:
                p = _boot_sweep_abs_path(row[0])
                key = os.path.normcase(str(p))
                if key in roots:
                    continue
                if not _boot_sweep_root_usable(p):
                    continue
                roots[key] = p
                session_added += 1
            except Exception:
                pass
    except Exception:
        pass
    try:
        here_root = Path(__file__).resolve().parents[3]
        roots[os.path.normcase(str(here_root))] = here_root
    except Exception:
        pass
    return list(roots.values())


def _boot_sweep_index_files(root: Path) -> list[Path]:
    import code_tools as ct
    idx_path = ct._project_cache_dir(str(root)) / "INDEX.json"
    if not idx_path.exists():
        return []
    try:
        idx = ct.json.loads(idx_path.read_text(encoding="utf-8"))
    except Exception:
        return []
    files = []
    for rel in (idx.get("files") or {}).keys():
        try:
            fp = _boot_sweep_abs_path(root / rel)
            if fp.suffix.lower() in _VALIDATE_EXTS:
                files.append(fp)
        except Exception:
            pass
    return files


def _boot_sweep_xroot_files(max_rows: int = 200) -> list[Path]:
    try:
        ledger = Path(__file__).resolve().parents[3] / ".orchestrator_cache" / "xroot_writes.jsonl"
        if not ledger.exists():
            return []
        rows = ledger.read_text(encoding="utf-8", errors="replace").splitlines()[-max_rows:]
    except Exception:
        return []
    files = []
    for row in rows:
        try:
            import json
            data = json.loads(row)
            fp = _boot_sweep_abs_path(str(data.get("path") or ""))
            if fp.suffix.lower() in _VALIDATE_EXTS:
                files.append(fp)
        except Exception:
            pass
    return files


# campaign-1608 (A02): the boot sweep's write-ahead ledger. Both names live in
# .orchestrator_cache, which surgical_mapper._reindex_from_disk WIPES on every
# periodic full refresh (A30: once per _SNAPSHOT_FULL_REFRESH_EVERY writes) and
# on generate_project_map. Neither name is in PRESERVED_CACHE_FILES, and that is
# DELIBERATE and LOAD-BEARING -- they must share one fate:
#   both wiped  -> ledger empty AND marker gone -> next boot re-seeds from the
#                  index and re-discovers every legacy .bak. Correct, just pays
#                  the O(index) seed again (rare: only after a full refresh).
#   ledger wiped, marker KEPT -> seed never re-runs, so a pending .bak written
#                  before the refresh is ORPHANED and never restored. DATA LOSS.
# So: never add the marker to PRESERVED_CACHE_FILES without adding the ledger,
# and never add the ledger without the marker. test_a02_a30_interaction.py pins
# both directions -- see that file before touching PRESERVED_CACHE_FILES.
_WRITE_BACKUP_LEDGER_NAME = "write_backups.jsonl"
_WRITE_BACKUP_SEED_MARKER = ".write_backups.seeded"


def _write_backup_ledger_path() -> Path:
    return Path(__file__).resolve().parents[3] / ".orchestrator_cache" / _WRITE_BACKUP_LEDGER_NAME


def _write_backup_seed_marker_path() -> Path:
    return Path(__file__).resolve().parents[3] / ".orchestrator_cache" / _WRITE_BACKUP_SEED_MARKER


def _record_write_backup(target: Path, bak: Path) -> None:
    """WAL: record a .bak creation at write time.

    This is what makes boot recovery O(number of backups) instead of
    O(every indexed file). It is written as an *intent* (before the .bak is
    physically created) so a crash mid-write still leaves a recoverable pointer
    -- strictly more complete than the old "walk the index and probe siblings"
    scan, which could only find backups that happened to sit next to an already
    indexed file.

    Best-effort by design: a ledger append must never make the real file write
    fail. A failure is logged LOUDLY (not swallowed) so the gap is visible; the
    backup is still recoverable via the explicit-roots index path.
    """
    try:
        import json
        led = _write_backup_ledger_path()
        led.parent.mkdir(parents=True, exist_ok=True)
        row = json.dumps(
            {
                "ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                "target": str(target),
                "bak": str(bak),
            },
            ensure_ascii=False,
            sort_keys=True,
        )
        with led.open("a", encoding="utf-8") as fh:
            fh.write(row + "\n")
    except Exception:
        _logger.warning(
            "write_backup ledger append FAILED for %s (recovery falls back to index path)",
            target,
            exc_info=True,
        )


def _boot_sweep_ledger_candidates() -> tuple[dict[str, Path], list[dict]]:
    """Discover boot-recovery candidates from the write-backup WAL.

    Returns (candidates_by_normcase, ledger_errors). A corrupt/unreadable
    ledger is reported (fail loud) -- never silently treated as "nothing to do",
    because that would turn "could not read the recovery journal" into a false
    "clean" and lose data.
    """
    candidates: dict[str, Path] = {}
    errors: list[dict] = []
    led = _write_backup_ledger_path()
    try:
        if not led.exists():
            return candidates, errors
        lines = led.read_text(encoding="utf-8", errors="replace").splitlines()
    except Exception as exc:
        errors.append({
            "ledger": str(led),
            "error": f"{type(exc).__name__}: {exc}",
            "stage": "read",
        })
        _logger.warning("boot_write_sweep ledger unreadable %s: %s", led, exc)
        return candidates, errors
    import json
    corrupt = 0
    for line in lines:
        line = line.strip()
        if not line:
            continue
        try:
            data = json.loads(line)
            target = str(data.get("target") or "")
            if not target:
                continue
            fp = _boot_sweep_abs_path(target)
            candidates[os.path.normcase(str(fp))] = fp
        except Exception:
            corrupt += 1
    if corrupt:
        errors.append({
            "ledger": str(led),
            "error": f"{corrupt} corrupt ledger line(s)",
            "stage": "parse",
        })
        _logger.warning("boot_write_sweep ledger had %d corrupt line(s) in %s", corrupt, led)
    return candidates, errors


def _boot_sweep_seed_from_index(
    stop_event: threading.Event | None,
    deadline_monotonic: float | None,
) -> dict:
    """One-time migration: fold legacy (pre-WAL) backups into the candidate set.

    Runs the legacy O(indexed) discovery exactly ONCE (marker-gated by the
    caller) so any backup created before the WAL existed is still recovered.
    Bounded by the same stop_event/deadline as the rest of the sweep and
    reports `aborted` honestly so the caller never marks the seed complete on a
    partial pass.
    """
    candidates: dict[str, Path] = {}
    root_errors: list[dict] = []
    roots = _boot_sweep_project_roots(None)
    aborted = False
    for root in roots:
        if _boot_sweep_abort_reason(stop_event, deadline_monotonic):
            aborted = True
            break
        try:
            index_files = _boot_sweep_index_files(root)
        except Exception as exc:
            root_errors.append({
                "root": str(root),
                "error": f"{type(exc).__name__}: {exc}",
                "stage": "index_files",
            })
            _logger.warning("boot_write_sweep seed skipped unreadable root %s: %s", root, exc)
            continue
        for fp in index_files:
            if _boot_sweep_abort_reason(stop_event, deadline_monotonic):
                aborted = True
                break
            candidates[os.path.normcase(str(fp))] = fp
        if aborted:
            break
    return {
        "candidates": candidates,
        "root_errors": root_errors,
        "roots": roots,
        "aborted": aborted,
    }


def _boot_sweep_abort_reason(
    stop_event: threading.Event | None,
    deadline_monotonic: float | None,
) -> str:
    """Return a cooperative abort reason without performing filesystem work."""
    if stop_event is not None and stop_event.is_set():
        return "stop_requested"
    if deadline_monotonic is not None and time.monotonic() >= deadline_monotonic:
        if stop_event is not None:
            stop_event.set()
        return "deadline_exceeded"
    return ""


def _boot_sweep_scan(
    project_roots: list[str] | None = None,
    *,
    stop_event: threading.Event | None = None,
    deadline_monotonic: float | None = None,
) -> dict:
    """Discover candidates off-loop, while honoring the startup deadline."""
    started = time.perf_counter()
    abort_reason = _boot_sweep_abort_reason(stop_event, deadline_monotonic)
    if abort_reason:
        return {
            "root_count": 0,
            "candidate_count": 0,
            "pending_count": 0,
            "pending": [],
            "roots_ms": 0.0,
            "index_ms": 0.0,
            "stat_ms": 0.0,
            "scan_ms": 0.0,
            "aborted": True,
            "abort_reason": abort_reason,
            "scan_complete": False,
        }
    candidates: dict[str, Path] = {}
    root_errors: list[dict] = []
    roots: list[Path] = []
    # A02: the production boot (project_roots is None) discovers candidates from
    # the write-backup WAL -> O(backups written), not O(every indexed file). An
    # explicit project_roots (tests + any deliberate targeted sweep) keeps the
    # legacy index walk so its semantics are byte-for-byte preserved.
    use_ledger = project_roots is None
    seeded_now = False
    if use_ledger:
        ledger_candidates, ledger_errors = _boot_sweep_ledger_candidates()
        candidates.update(ledger_candidates)
        root_errors.extend(ledger_errors)
        # One-time migration: on the first boot after this fix ships, fold any
        # legacy pre-WAL backup into the candidate set so nothing written before
        # the ledger existed is orphaned. Marker-gated -> paid at most once.
        seed_marker = _write_backup_seed_marker_path()
        seed_marker_present = False
        try:
            seed_marker_present = seed_marker.exists()
        except Exception:
            seed_marker_present = False
        if not seed_marker_present and not abort_reason:
            seed = _boot_sweep_seed_from_index(stop_event, deadline_monotonic)
            candidates.update(seed["candidates"])
            root_errors.extend(seed["root_errors"])
            roots = seed["roots"]
            if seed["aborted"]:
                abort_reason = (
                    _boot_sweep_abort_reason(stop_event, deadline_monotonic)
                    or "stop_requested"
                )
            else:
                seeded_now = True
    else:
        roots = _boot_sweep_project_roots(project_roots)
        for root in roots:
            abort_reason = _boot_sweep_abort_reason(stop_event, deadline_monotonic)
            if abort_reason:
                break
            try:
                index_files = _boot_sweep_index_files(root)
            except Exception as exc:
                # A22: one unreadable/permission-denied root (e.g. a stale junk temp
                # dir with broken ACLs) must NOT collapse the whole sweep. Record it
                # loudly and keep sweeping the remaining roots.
                root_errors.append({
                    "root": str(root),
                    "error": f"{type(exc).__name__}: {exc}",
                    "stage": "index_files",
                })
                _logger.warning("boot_write_sweep skipped unreadable root %s: %s", root, exc)
                continue
            for fp in index_files:
                abort_reason = _boot_sweep_abort_reason(stop_event, deadline_monotonic)
                if abort_reason:
                    break
                candidates[os.path.normcase(str(fp))] = fp
            if abort_reason:
                break
    roots_done = time.perf_counter()
    if not abort_reason:
        for fp in _boot_sweep_xroot_files():
            abort_reason = _boot_sweep_abort_reason(stop_event, deadline_monotonic)
            if abort_reason:
                break
            candidates[os.path.normcase(str(fp))] = fp
    indexes_done = time.perf_counter()

    pending: list[tuple[Path, Path]] = []
    existing_baks: list[tuple[Path, Path]] = []
    if not abort_reason:
        for fp in candidates.values():
            abort_reason = _boot_sweep_abort_reason(stop_event, deadline_monotonic)
            if abort_reason:
                break
            bak = fp.with_suffix(fp.suffix + ".bak")
            try:
                # Nearly every indexed file has no backup. Probe that cheap reject first;
                # only touch the target/stat metadata for the rare backup candidate.
                if not bak.exists() or not fp.exists():
                    continue
                existing_baks.append((fp, bak))
                if bak.stat().st_mtime <= fp.stat().st_mtime:
                    continue
                pending.append((fp, bak))
            except Exception:
                pass
    finished = time.perf_counter()

    # After a COMPLETE seed pass: persist the legacy backups we actually found
    # into the WAL so future boots never re-walk the index, then drop the marker.
    # Only when the seed ran to completion -- a partial/aborted seed must retry
    # next boot (fail loud, never mark done on incomplete work).
    if use_ledger and seeded_now and not abort_reason:
        try:
            # Record every target that still HAS a sibling .bak (not just the
            # ones pending right now): the legacy scan re-evaluated mtime every
            # boot, so a .bak that is currently stale could still become the
            # newest copy after a later edit. The WAL must carry the same set.
            for fp, bak in existing_baks:
                _record_write_backup(fp, bak)
        finally:
            try:
                marker = _write_backup_seed_marker_path()
                marker.parent.mkdir(parents=True, exist_ok=True)
                marker.write_text(
                    time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                    encoding="utf-8",
                )
            except Exception:
                _logger.warning("boot_write_sweep could not write seed marker", exc_info=True)

    return {
        "root_count": len(roots),
        "candidate_count": len(candidates),
        "pending_count": len(pending),
        "pending": pending,
        "roots_ms": round((roots_done - started) * 1000.0, 1),
        "index_ms": round((indexes_done - roots_done) * 1000.0, 1),
        "stat_ms": round((finished - indexes_done) * 1000.0, 1),
        "scan_ms": round((finished - started) * 1000.0, 1),
        "aborted": bool(abort_reason),
        "abort_reason": abort_reason,
        "scan_complete": not bool(abort_reason),
        "root_errors": root_errors,
        "root_error_count": len(root_errors),
        "discovery": "ledger" if use_ledger else "index",
        "seeded": seeded_now,
    }


def _boot_sweep_restore(fp: Path, bak: Path) -> bool:
    import code_tools as ct
    with _path_lock([fp]):
        original = bak.read_text(encoding="utf-8", errors="replace")
        ct._atomic_write_text(fp, original, encoding="utf-8")
        return bool(ct._update_cache_after_write(str(fp)))


async def _boot_write_sweep(
    project_roots: list[str] | None = None,
    *,
    stop_event: threading.Event | None = None,
    deadline_monotonic: float | None = None,
) -> dict:
    import code_tools as ct
    total_started = time.perf_counter()
    if os.environ.get("ORCHESTRATOR_BOOT_SWEEP", "on").strip().lower() == "off":
        return {"ok": True, "skipped": True, "reason": "ORCHESTRATOR_BOOT_SWEEP=off"}

    scan = await asyncio.to_thread(
        _boot_sweep_scan,
        project_roots,
        stop_event=stop_event,
        deadline_monotonic=deadline_monotonic,
    )
    pending = scan.get("pending") or []
    restored = []
    checked = 0
    abort_reason = str(scan.get("abort_reason") or "")
    for fp, bak in pending:
        abort_reason = _boot_sweep_abort_reason(stop_event, deadline_monotonic)
        if abort_reason:
            break
        try:
            checked += 1
            val = ct.json.loads(await ct.validate_file(str(fp)))
            if val.get("ok"):
                continue
            abort_reason = _boot_sweep_abort_reason(stop_event, deadline_monotonic)
            if abort_reason:
                break
            synced = await asyncio.to_thread(_boot_sweep_restore, fp, bak)
            restored.append({"file": str(fp), "backup": str(bak), "cache_synced": synced})
            _logger.warning("boot_write_sweep restored %s from %s", fp, bak)
        except Exception as exc:
            _logger.warning("boot_write_sweep failed for %s: %s", fp, exc)

    result = {key: value for key, value in scan.items() if key != "pending"}
    scan_complete = bool(scan.get("scan_complete"))
    deadline_idle = (
        abort_reason == "deadline_exceeded"
        and scan_complete
        and not pending
        and not restored
    )
    result.update({
        "ok": (not bool(abort_reason)) or deadline_idle,
        "checked": checked,
        "restored": restored,
        "restored_count": len(restored),
        "aborted": bool(abort_reason) and not deadline_idle,
        "abort_reason": "" if deadline_idle else abort_reason,
        "scan_complete": scan_complete,
        "total_ms": round((time.perf_counter() - total_started) * 1000.0, 1),
    })
    return result


def _json_path_resolve_error(ct, exc: BaseException) -> str:
    """Map path-guard ValueError to a stable error_name (not INTERNAL_ERROR)."""
    msg = str(exc)
    lower = msg.lower()
    if "outside project" in lower or "escapes project root" in lower:
        return ct._json_error(
            msg,
            error_name="PATH_OUTSIDE_PROJECT",
            type="PERMISSION_POLICY",
            retryable=True,
            ca_next_action="use_project_relative_path_or_omit_project_path",
        )
    if "unc" in lower:
        return ct._json_error(
            msg,
            error_name="UNC_PATH_UNSUPPORTED",
            type="INVALID_REQUEST",
            retryable=True,
            ca_next_action="use_local_project_path_or_check_server_path",
        )
    return ct._json_error(msg, error_name="PATH_RESOLVE_FAILED", type="INVALID_REQUEST")


def _resolve_mutable_target(
    ct,
    path: str,
    project_path: str = "",
    tool_name: str = "",
    session_id: str = "",
    escape_project_root: bool = False,
):
    """Resolve a write target. Absolute paths outside project_path use xroot_check.

    Returns (fp, xroot_managed, policy, error_json_or_none).
    """
    from orchestrator.fileops.xroot import is_outside_project_root, xroot_check

    absolute_target = False
    try:
        raw_path = ct.Path(path)
        absolute_target = bool(raw_path.is_absolute() or ct._is_unc_path_like(path))
        if absolute_target and (
            escape_project_root
            or is_outside_project_root(raw_path, project_path or None)
        ):
            fp = ct.Path(path) if ct._is_unc_path_like(path) else ct.Path(path).resolve()
        else:
            fp = ct._resolve_project_file(project_path, path)
    except ValueError as exc:
        return None, False, None, _json_path_resolve_error(ct, exc)

    policy = xroot_check(fp, tool_name, session_id)
    managed = (
        (absolute_target and is_outside_project_root(fp, project_path or None))
        or policy.get("group") == "live_files"
    )
    if managed and not policy.get("allowed"):
        return None, managed, policy, ct._json_error(
            policy.get("reason", "xroot policy denied write"),
            error_name="XROOT_DENIED",
            group=policy.get("group", ""),
            reason=policy.get("reason", ""),
        )
    return fp, managed, policy, None


async def write_fix (file_path :str ,start_line :int ,end_line :int ,new_content :str ,
project_path :str ="",dry_run :bool =False ,
content_encoding :str ="none",
expected_hash :str ="",skip_pre_write_check :bool =False ,
skip_symbol_check :bool =False )->str :
    import code_tools as ct
    """V15 R43 P0 #3-A: response includes new_hash + new_mtime + indexed_mtime so CA can
    chain sequential write_fix without re-reading. Pass expected_hash from previous
    response to skip FILE_CHANGED guardrail (CA confirms its own prior write).
    """
    session_id = ""
    if project_path:
        try:
            session_id = ct._get_active_session_id(ct._session_project_key(project_path)) or ""
        except Exception:
            session_id = ""
    fp, _xroot_managed, _xroot_policy, _xroot_err = _resolve_mutable_target(
        ct, file_path, project_path, "write_fix", session_id,
    )
    if _xroot_err:
        return _xroot_err
    if not fp.exists():
        return ct.json.dumps({"error": f"File not found: {file_path}"})
    if ct._is_binary_file (fp ):
        return ct._json_error (
        f"Binary file unsupported by write_fix: {file_path}",
        error_name ="BINARY_FILE_UNSUPPORTED",
        type ="UNSUPPORTED_MEDIA_TYPE",
        severity ="CRITICAL",
        retryable =False ,
        suggested_fallback ="STOP_AND_REPORT",
        ca_next_action ="stop_and_report",
        ca_guidance ={
        "tool_category":"mutating",
        "retry_sequence":[],
        "notes":["Do not use text diff tools on binary files."],
        },
        )

    new_content ,encoding_check ,decode_error =ct._decode_text_payload (new_content ,content_encoding )
    if decode_error :
        return ct.json .dumps (decode_error ,ensure_ascii =False )

    _guardrail_active =False 

                # Tinh noi dung moi (dung cho ca dry_run va write that)
    def _build ():
        # Read BYTES (not read_text) so CRLF line endings survive: read_text's
        # universal-newline mode silently rewrites \r\n -> \n, which corrupts
        # .bat/.ps1 files on edit. Detect the file's dominant ending and keep it.
        original =fp .read_bytes ().decode ('utf-8',errors ='replace')
        _crlf =original .count ('\r\n')
        _lone_lf =original .count ('\n')-_crlf
        _eol ='\r\n' if _crlf and _crlf >=_lone_lf else '\n'
        lines =original .splitlines (keepends =True )
        total =len (lines )
        if start_line <1 or start_line >total :
            return {
            "error":f"start_line {start_line} out of range (file has {total} lines)",
            "error_name":"RANGE_PAST_EOF" if start_line >total else "RANGE_OUT_OF_BOUNDS",
            "total_lines":total ,
            "ca_next_action":"seed_read_file_range_then_retry_write_fix",
            "ca_guidance":{
            "notes":["use start_line from the same read_file_range response; native editor line numbers are not accepted."],
            },
            }
        if end_line <start_line :
            return {"error":f"end_line {end_line} must be >= start_line {start_line}"}
        end =min (end_line ,total )
        # Normalize the caller's new_content to the file's ending so the splice
        # never leaves a mixed CRLF/LF file.
        _nc =new_content .replace ('\r\n','\n')
        if _eol !='\n':
            _nc =_nc .replace ('\n',_eol )
        new_lines =_nc .splitlines (keepends =True )
        if new_lines and not new_lines [-1 ].endswith (('\n','\r')):
            new_lines [-1 ]+=_eol
        proposed =''.join (lines [:start_line -1 ]+new_lines +lines [end :])
        span =(end -start_line )+len (new_lines )
        return {
        "original":original ,
        "proposed":proposed ,
        "lines_removed":[l .rstrip ('\r\n')for l in lines [start_line -1 :end ]],
        "lines_added":[l .rstrip ('\r\n')for l in new_lines ],
        "end":end ,"span":span ,"new_lines":new_lines ,
        "eol":"CRLF" if _eol =='\r\n' else "LF",
        }

    built =await ct.asyncio .to_thread (_build )
    if "error"in built :
        err ={"ok":False ,"error":built ["error"]}
        for _k in ("error_name","total_lines","ca_next_action","ca_guidance"):
            if _k in built :
                err [_k ]=built [_k ]
        return ct.json .dumps (err ,ensure_ascii =False )

    end =built ["end"]
    span =built ["span"]
    # DRY RUN: validate proposed, tra preview + diff, KHONG ghi file
    if dry_run :
        # TOOLA-AHEALTHHUNG-R1: missing INDEX is no longer a hard blocker.
        # The real write auto-seeds a minimal INDEX (cache-only, no O(repo)
        # generate_project_map). Dry-run stays side-effect free and reports
        # the same apply-will-succeed contract via index_auto_seed_pending.
        _idx_err =_index_missing_error (fp ,project_path )
        _index_auto_seed_pending =bool (_idx_err )
        val_result =None 
        if fp .suffix .lower ()in _VALIDATE_EXTS :
            with ct.tempfile .NamedTemporaryFile (
            suffix =fp .suffix ,delete =False ,mode ='w',encoding ='utf-8'
            )as tmp :
                tmp .write (built ["proposed"])
                tmp_path =tmp .name 
            try :
                val_result =ct.json .loads (await ct.validate_file (tmp_path ))
            finally :
                try :
                    ct.os .unlink (tmp_path )
                except Exception :
                    pass 
        diff_preview =ct._unified_diff_preview (
        built ["original"],
        built ["proposed"],
        f"{fp.name} (before)",
        f"{fp.name} (after)",
        )
        dry_run_meta ={}
        _root_for_dry =project_path or ct.find_project_root (str (fp ))
        if _root_for_dry :
            dry_payload ={
            "tool":"write_fix",
            "project_path":str (ct.Path (_root_for_dry ).resolve ()),
            "file_path":file_path ,
            "payload":{
            "file_path":file_path ,
            "start_line":start_line ,
            "end_line":end_line ,
            "new_content":new_content ,
            "project_path":str (ct.Path (_root_for_dry ).resolve ()),
            "dry_run":False ,
            "content_encoding":"plain",
            },
            "payload_hash":ct._sha256_text (ct.json .dumps ({
            "tool":"write_fix",
            "file_path":file_path ,
            "start_line":start_line ,
            "end_line":end_line ,
            "new_content":new_content ,
            },ensure_ascii =False ,sort_keys =True )),
            "target_file":str (fp ),
            "target_file_hash":ct._sha256_file (fp ),
            "requires_confirm":False ,
            "warnings":(["index_auto_seed_pending"] if _index_auto_seed_pending else []),
            }
            dry_run_meta =ct._store_dry_run (str (ct.Path (_root_for_dry ).resolve ()),dry_payload )
        return ct.json .dumps ({
        "ok":True ,
        "dry_run":True ,
        "file":str (fp ),
        "preview":{
        "range":f"{start_line}-{end}",
        "lines_removed":built ["lines_removed"],
        "lines_added":built ["lines_added"],
        },
        "diff":diff_preview ["diff_unified"],
        "diff_preview":diff_preview ,
        "encoding_check":encoding_check ,
        "validation":val_result ,
        "range_id":f"{fp.name}:{start_line}p{span}",
        "index_auto_seed_pending":_index_auto_seed_pending ,
        **dry_run_meta ,
        "hint":("Goi lai write_fix voi dry_run=False (hoac bo dry_run) de apply"
        + ("; apply se tu seed INDEX.json toi thieu, khong can generate_project_map"
           if _index_auto_seed_pending else "")),
        })

    _idx_err =_index_missing_error (fp ,project_path )
    _index_auto_seeded =False 
    if _idx_err :
        import surgical_mapper as _sm_seed
        _seed_root =project_path or ct.find_project_root (str (fp ))
        if not _seed_root :
            return ct.json .dumps (_idx_err ,ensure_ascii =False )
        try :
            _seed =await ct.asyncio .to_thread (_sm_seed .ensure_minimal_index ,str (_seed_root ))
            _index_auto_seeded =bool (_seed .get ("ok"))
        except Exception :
            _index_auto_seeded =False 
        if not _index_auto_seeded and not (ct._project_cache_dir (_seed_root )/"INDEX.json").exists ():
            return ct.json .dumps (_idx_err ,ensure_ascii =False )

    if skip_pre_write_check :
        _audit_skip_pre_write_check (str (fp ),"write_fix")
    else :
        pre_check =await _pre_write_syntax_check (fp ,built ["proposed"])
        if not pre_check .get ("ok"):
            # 2026-07-17 write-draft loop: instead of a bare error that forces the
            # CA to resend the whole content, open a draft + per-part findings.
            from orchestrator .tools .write import draft as _draft
            return await _draft .draft_failure_response (
            "write_fix",fp ,project_path ,file_path ,built ["proposed"],pre_check ,
            orig ={"skip_symbol_check":skip_symbol_check })

        # WRITE THAT: backup -> ghi -> auto-validate -> auto-revert neu fail
    def _write ():
        guard_active, guard_error, _guard_blocked = _write_fix_guard_result(fp, project_path, file_path, expected_hash)
        if guard_active:
            nonlocal _guardrail_active
            _guardrail_active = True
        if guard_error:
            return guard_error
        backup_path =fp .with_suffix (fp .suffix +'.bak')
        # A02 WAL: journal the backup BEFORE it exists on disk, so boot recovery
        # can find it in O(backups) instead of walking every indexed file. Intent
        # ordering (record -> write) means even a crash between the two lines
        # leaves a recoverable pointer.
        _record_write_backup (fp ,backup_path )
        ct._atomic_write_text (backup_path ,built ["original"],encoding ='utf-8')
        write_mode =ct._atomic_write_text (fp ,built ["proposed"],encoding ='utf-8')
        return {
        "ok":True ,
        "file":str (fp ),
        "replaced_lines":f"{start_line}-{end}",
        "new_lines_count":len (built ["new_lines"]),
        "backup":str (backup_path ),
        "write_mode":write_mode ,
        "range_id":f"{fp.name}:{start_line}p{span}",
        "encoding_check":encoding_check ,
        "encoding_used":encoding_check .get ("encoding_used"),
        "bytes_written":len (built ["proposed"].encode ("utf-8")),
        "utf8_valid":encoding_check .get ("utf8_valid"),
        "mojibake_risk":encoding_check .get ("mojibake_risk"),
        "vietnamese_diacritic_check":encoding_check .get ("vietnamese_diacritic_check"),
        }

    from orchestrator.fileops.xroot import log_intent, log_result
    async with _gate_for_paths([fp]):
        if _xroot_managed:
            log_intent(fp, "write_fix", session_id)
        try:
            result = await ct.asyncio.to_thread(_write)
            if _xroot_managed:
                log_result(fp, "write_fix", session_id, ok=bool(result.get("ok")))
        except Exception:
            if _xroot_managed:
                log_result(fp, "write_fix", session_id, ok=False)
            raise
        if result.get("ok"):
            _next_write_generation(fp)

        # A-AUTO lock (2026-07-12): honor skip_pre_write_check in the POST-write
        # validate too, so the audited "write this deliberately-bad file" kill-switch
        # behaves identically in write_fix and write_new_file (was asymmetric).
        if result .get ("ok")and fp .suffix .lower ()in _VALIDATE_EXTS and not skip_pre_write_check :
            val =ct.json .loads (await ct.validate_file (str (fp )))
            if fp .suffix .lower ()==".html":
                for key in ("truncated","unclosed_tags","tag_errors"):
                    if key in val :
                        result [key]=val [key]
            if not val .get ("ok"):
                bak =fp .with_suffix (fp .suffix +'.bak')
                reverted_synced =False
                if bak .exists ():
                    reverted_synced = await ct.asyncio.to_thread(_restore_backup_and_reseed, fp, bak, project_path, True)
                result ["ok"]=False
                result ["syntax_ok"]=False
                # V15 R41: explicit error_name so CA không nhầm thành token/auth issue.
                # Pattern thực: patch valid về syntax tool nhưng content tạo syntax/lint error
                # trong file → backend auto-restore .bak. CA chỉ cần sửa new_content + retry.
                result ["error_name"]="VALIDATION_FAILED_AUTO_REVERT"
                result ["error"]=f"Auto-reverted: {val.get('error', 'validation failed')}"
                result ["auto_reverted"]=True 
                result ["validation_error_detail"]=val .get ("error","")
                result ["cache_synced"]=reverted_synced 
                if not reverted_synced:
                    result ["cache_sync_warning"]=(
                    "INDEX chua duoc reseed sau auto-revert. Goi cache_delta hoac "
                    "read_file_range(force_reseed=true) truoc khi ghi tiep. "
                    "KHONG goi refresh_project_map tren mot root lon nhu [A:P]."
                    )
                result ["ca_next_action"]="fix_only_failed_range_via_write_draft_patch"
                result ["hint"]=(
                "Backend ghi xong nhưng validation báo file bị lỗi → đã tự revert về .bak. "
                "KHÔNG phải lỗi token/auth. Draft đã mở sẵn: sửa ĐÚNG range trong failed_parts "
                "bằng write_draft_patch(draft_id, start_line, end_line, new_content) — sạch là tự ship."
                )
                try :
                    from orchestrator .tools .write import draft as _draft
                    result .update (await _draft .draft_fields_for_failure (
                    "write_fix",fp ,project_path ,file_path ,built ["proposed"],
                    val .get ("error","")or "",{"skip_symbol_check":skip_symbol_check }))
                except Exception as _draft_exc :
                    result ["draft_error"]=str (_draft_exc )[:200 ]
                return ct.json .dumps (result ,ensure_ascii =False )
            result ["validation"]=val
            result ["syntax_ok"]=bool (val .get ("ok"))
            if not skip_symbol_check:
                bak =fp .with_suffix (fp .suffix +'.bak')
                sym =await _post_write_symbol_check (fp ,bak ,project_path )
                if sym .get ("warn"):
                    result ["symbol_check_warning"]=sym .get ("warn")
                if not sym .get ("ok"):
                    reverted_synced =False
                    if bak .exists ():
                        reverted_synced = await ct.asyncio.to_thread(_restore_backup_and_reseed, fp, bak, project_path, True)
                    result ["ok"]=False
                    result ["error_name"]=sym .get ("error_name","SYMBOL_DRIFT_AUTO_REVERT")
                    result ["error"]=sym .get ("error","Symbol check failed")
                    result ["auto_reverted"]=True
                    result ["cache_synced"]=reverted_synced
                    if not reverted_synced:
                        result ["cache_sync_warning"]=(
                    "INDEX chua duoc reseed sau auto-revert. Goi cache_delta hoac "
                    "read_file_range(force_reseed=true) truoc khi ghi tiep. "
                    "KHONG goi refresh_project_map tren mot root lon nhu [A:P]."
                    )
                    if sym .get ("symbols_removed"):
                        result ["symbols_removed"]=sym .get ("symbols_removed")
                    result ["ca_next_action"]="fix_only_failed_range_via_write_draft_patch"
                    try :
                        from orchestrator .tools .write import draft as _draft
                        result .update (await _draft .draft_fields_for_failure (
                        "write_fix",fp ,project_path ,file_path ,built ["proposed"],
                        sym .get ("error","")or "symbol drift",{"skip_symbol_check":skip_symbol_check }))
                    except Exception as _draft_exc :
                        result ["draft_error"]=str (_draft_exc )[:200 ]
                    return ct.json .dumps (result ,ensure_ascii =False )
                result ["symbol_check"]=sym
                result ["symbols_changed"]=list (sym .get ("symbols_added")or sym .get ("symbols_changed")or [])
                result ["symbols_deleted"]=list (sym .get ("symbols_removed")or [])
            else:
                result ["symbol_check_skipped"]=True

            # Fix 3: escalate cache_sync fail
        if result .get ("ok"):
            if _index_auto_seeded :
                result ["index_auto_seeded"]=True 
            synced = await ct.asyncio.to_thread(ct._update_cache_after_write, str(fp), project_path)
            result ["cache_synced"]=synced 
            if not synced :
                # CA reported a double-bind here: this warning told CA to call
                # refresh_project_map, while CORE_RULE and every other stale-index
                # response in the tree forbid it on a large root ([A:P] is ~450MB) and
                # route to cache_delta instead. cache_delta is the per-file/delta probe
                # that actually matches what went stale after ONE write, so it is both
                # the cheap answer and the consistent one.
                result ["cache_sync_warning"]=(
                "INDEX chua duoc cap nhat cho file nay. Goi cache_delta(project_path) "
                "hoac read_file_range(..., force_reseed=true) truoc khi doc/ghi tiep. "
                "KHONG goi refresh_project_map tren mot root lon nhu [A:P]."
                )
                result ["action_required"]="cache_delta"
                result ["ca_next_action"]="cache_delta"
                result ["fallback_sequence"]=["cache_delta","read_file_range"]
                # R43 P0 #3-A: surface new_hash + new_mtime for sequential write chains.
                # CA can pass new_hash as expected_hash in next write_fix on the same file to
                # bypass FILE_CHANGED guardrail without re-reading.
            try :
                result ["new_hash"]=ct._sha256_file (fp )
                result ["new_mtime"]=fp .stat ().st_mtime 
                result ["sequential_chain_hint"]=(
                "If patching the same file again this round, pass expected_hash="
                "<this response's new_hash> to skip FILE_CHANGED re-check."
                )
            except Exception as _exc_hash :
                result ["new_hash"]=""
                result ["new_mtime"]=0 
                result ["new_hash_error"]=str (_exc_hash )

                # Fix 2: canh bao khi guardrail khong active
        if not _guardrail_active and result .get ("ok"):
            result ["guardrail_warning"]=(
            "project_root khong tim thay - conflict detection bi bo qua cho lan write nay."
            )

    return ct.json .dumps (result )



    # ─── Tool 7: write_new_file ───────────────────────────────────────────────────

async def write_new_file (path :str ,content :str ="",project_path :str ="",
content_encoding :str ="plain",dry_run :bool =False ,
overwrite :bool =False ,new_content :str |None =None ,
escape_project_root :bool =False ,skip_pre_write_check :bool =False )->str :
    import code_tools as ct
    from orchestrator.fileops.xroot import is_outside_project_root, log_intent, log_result, xroot_check
    if new_content is None :
        new_content =content 
    new_content ,encoding_check ,decode_error =ct._decode_text_payload (new_content ,content_encoding )
    if decode_error :
        return ct.json .dumps (decode_error ,ensure_ascii =False )
    # Windows batch/PowerShell REQUIRE CRLF; normalize so a LF payload does not
    # produce a broken .bat/.cmd/.ps1 (2026-07-17 CRLF-preserve upgrade).
    if str (path ).lower ().rsplit ('.',1 )[-1 ]in ('bat','cmd','ps1'):
        new_content =new_content .replace ('\r\n','\n').replace ('\n','\r\n')
        # XROOT: absolute paths outside project root go through the central policy.
        # escape_project_root is kept as a backward-compatible caller flag; policy
        # off preserves the older bypass behavior while still writing the ledger.
    try :
        _raw_path =ct.Path (path )
        _absolute_target =bool (_raw_path .is_absolute ()or ct._is_unc_path_like (path ))
        if _absolute_target and (escape_project_root or is_outside_project_root (_raw_path ,project_path or None )):
            fp =ct.Path (path )if ct._is_unc_path_like (path )else ct.Path (path ).resolve ()
        else :
            fp =ct._resolve_project_file (project_path ,path )
    except ValueError as exc :
        return _json_path_resolve_error(ct, exc)
    session_id =""
    if project_path :
        try :
            session_id =ct._get_active_session_id (ct._session_project_key (project_path ))or ""
        except Exception :
            session_id =""
    _xroot_policy =xroot_check (fp ,"write_new_file",session_id )
    _xroot_managed =(_absolute_target and is_outside_project_root (fp ,project_path or None ))or _xroot_policy .get ("group")=="live_files"
    if _xroot_managed and not _xroot_policy .get ("allowed"):
        return ct._json_error (
        _xroot_policy .get ("reason","xroot policy denied write"),
        error_name ="XROOT_DENIED",
        group =_xroot_policy .get ("group",""),
        reason =_xroot_policy .get ("reason",""),
        )
    target_exists =fp .exists ()
    parent_exists =fp .parent .exists ()
    if target_exists and ct._is_binary_file (fp ):
        return ct._json_error (
        f"Binary file unsupported by write_new_file: {path}",
        error_name ="BINARY_FILE_UNSUPPORTED",
        type ="UNSUPPORTED_MEDIA_TYPE",
        severity ="CRITICAL",
        retryable =False ,
        ca_next_action ="stop_and_report",
        )
    if target_exists and not overwrite :
        return ct.json .dumps ({
        "ok":False ,
        "error":f"File already exists: {path}. Use write_fix to modify existing files.",
        "error_name":"FILE_ALREADY_EXISTS",
        "overwrite_warning":True ,
        "target_exists":True ,
        "overwrite_risk":True ,
        "ca_next_action":"use_write_fix_or_choose_new_path",
        },ensure_ascii =False )

    before =fp .read_text (encoding ="utf-8",errors ="replace")if target_exists else ""
    diff_preview =ct._unified_diff_preview (
    before ,
    new_content ,
    f"{fp.name} (before)"if target_exists else "/dev/null",
    f"{fp.name} (after)",
    )
    encoding_check ["bytes_written"]=len (new_content .encode ("utf-8"))

    if dry_run :
        dry_run_meta ={}
        _root_for_dry =project_path or ct.find_project_root (str (fp .parent ))
        if _root_for_dry :
            payload ={
            "tool":"write_new_file",
            "project_path":str (ct.Path (_root_for_dry ).resolve ()),
            "file_path":path ,
            "payload":{
            "path":path ,
            "new_content":new_content ,
            "project_path":str (ct.Path (_root_for_dry ).resolve ()),
            "content_encoding":"plain",
            "dry_run":False ,
            "overwrite":overwrite ,
            },
            "payload_hash":ct._sha256_text (ct.json .dumps ({
            "tool":"write_new_file",
            "path":path ,
            "new_content":new_content ,
            "overwrite":overwrite ,
            },ensure_ascii =False ,sort_keys =True )),
            "target_file":str (fp ),
            "target_file_hash":ct._sha256_file (fp ),
            "requires_confirm":target_exists or encoding_check .get ("mojibake_risk",False ),
            "warnings":["overwrite_risk"]if target_exists else [],
            }
            dry_run_meta =ct._store_dry_run (str (ct.Path (_root_for_dry ).resolve ()),payload )
        return ct.json .dumps ({
        "ok":True ,
        "dry_run":True ,
        "would_create":not target_exists ,
        "target_exists":target_exists ,
        "target_new_ok":(not target_exists )and parent_exists ,
        "parent_exists":parent_exists ,
        "parent_path":str (fp .parent ),
        "overwrite_risk":target_exists ,
        "file":str (fp ),
        "preview":new_content [:4000 ],
        "preview_truncated":len (new_content )>4000 ,
        "encoding_check":encoding_check ,
        "diff_preview":diff_preview ,
        **dry_run_meta ,
        "ca_next_action":"review_diff_then_apply_after_dry_run",
        },ensure_ascii =False )

        # ── Guardrail: cảnh báo nếu INDEX chưa tồn tại (soft — không block) ─────
    _root =project_path or ct.find_project_root (str (fp .parent ))
    _cache_warning =None 
    if _root :
        _idx_path =ct._project_cache_dir (_root )/"INDEX.json"
        if not _idx_path .exists ():
            _cache_warning ="INDEX chưa tồn tại — gọi generate_project_map sau khi tạo xong các file để index project."

    if skip_pre_write_check :
        _audit_skip_pre_write_check (str (fp ),"write_new_file")
    else :
        pre_check =await _pre_write_syntax_check (fp ,new_content )
        if not pre_check .get ("ok"):
            # 2026-07-17 write-draft loop: open a draft so the CA fixes only the
            # failed range instead of resending the whole file.
            from orchestrator .tools .write import draft as _draft
            return await _draft .draft_failure_response (
            "write_new_file",fp ,project_path ,path ,new_content ,pre_check ,
            orig ={"overwrite":overwrite })

    write_generation =0
    written_signature =None
    written_hash =""
    original_bytes =None

    def _write ():
        nonlocal original_bytes ,written_hash
        if _xroot_managed :
            log_intent (fp ,"write_new_file",session_id )
        try :
            fp .parent .mkdir (parents =True ,exist_ok =True )
            if target_exists :
                original_bytes =fp .read_bytes ()
            write_mode =ct._atomic_write_text (fp ,new_content ,encoding ='utf-8')
            written_hash =ct._sha256_file (fp )
            if _xroot_managed :
                log_result (fp ,"write_new_file",session_id ,ok =True )
        except Exception :
            if _xroot_managed :
                log_result (fp ,"write_new_file",session_id ,ok =False )
            raise
        return {
        "ok":True ,
        "file":str (fp ),
        "write_mode":write_mode ,
        "lines_written":len (new_content .splitlines ()),
        "encoding_check":encoding_check ,
        "encoding_used":encoding_check .get ("encoding_used"),
        "bytes_written":len (new_content .encode ("utf-8")),
        "utf8_valid":encoding_check .get ("utf8_valid"),
        "mojibake_risk":encoding_check .get ("mojibake_risk"),
        "vietnamese_diacritic_check":encoding_check .get ("vietnamese_diacritic_check"),
        "diff_preview":diff_preview ,
        }

    # Was `with _path_lock([fp])`: a synchronous threading lock held across an await.
    # Use the async gate so same-loop writers actually serialise (and so this path
    # cannot block the loop the way the old sync acquire did).
    async with _gate_for_paths ([fp]):
        result =await ct.asyncio .to_thread (_write )
        if result .get ("ok"):
            write_generation =_next_write_generation (fp )
            written_signature =_disk_signature (fp ,written_hash )
    synced =False
    if isinstance (result ,dict )and result .get ("ok"):
        # A-AUTO-UPGRADE (2026-07-12): write_new_file used to skip ALL
        # post-write verification while write_fix auto-validates + reverts.
        # Same contract now: validate in _VALIDATE_EXTS; a new file that fails
        # validation is deleted (a brand-new file's revert IS deletion).
        # skip_pre_write_check is an audited kill-switch meaning "write this
        # deliberately-bad file, I take responsibility" — honor it here too so
        # the flag keeps its documented escape-hatch semantics.
        if fp .suffix .lower ()in _VALIDATE_EXTS and not skip_pre_write_check :
            val =ct.json .loads (await ct.validate_file (str (fp )))
            if fp .suffix .lower ()==".html":
                for key in ("truncated","unclosed_tags","tag_errors"):
                    if key in val :
                        result [key]=val [key]
            if not val .get ("ok"):
                superseded =False
                async with _gate_for_paths ([fp]):
                    current_hash =ct._sha256_file (fp )if fp .exists ()else ""
                    owns_write =(
                    _current_write_generation (fp )==write_generation
                    and _disk_signature (fp ,current_hash )==written_signature
                    )
                    if not owns_write :
                        superseded =True
                        synced = await ct.asyncio.to_thread(ct._update_cache_after_write, str(fp), project_path)
                    elif target_exists and original_bytes is not None :
                        await ct.asyncio .to_thread (ct._atomic_write_bytes ,fp ,original_bytes )
                        synced = await ct.asyncio.to_thread(ct._update_cache_after_write, str(fp), project_path)
                    else :
                        try :
                            fp .unlink ()
                        except FileNotFoundError :
                            pass
                        synced =False
                result ["ok"]=False
                result ["cache_synced"]=synced
                result ["validation_error_detail"]=val .get ("error","")
                if superseded :
                    result ["error_name"]="VALIDATION_FAILED_SUPERSEDED"
                    result ["error"]="Validation failed, but a newer write superseded this content; newer content was preserved."
                    result ["auto_reverted"]=False
                    result ["superseded"]=True
                    result ["ca_next_action"]="inspect_newer_content"
                else :
                    result ["error_name"]="VALIDATION_FAILED_AUTO_REVERT"
                    result ["error"]=f"File failed validation and was restored: {val.get('error', 'validation failed')}"
                    result ["auto_reverted"]=True
                    result ["ca_next_action"]="fix_only_failed_range_via_write_draft_patch"
                try :
                    from orchestrator .tools .write import draft as _draft
                    result .update (await _draft .draft_fields_for_failure (
                    "write_new_file",fp ,project_path ,path ,new_content ,
                    val .get ("error","")or "",{"overwrite":overwrite }))
                except Exception as _draft_exc :
                    result ["draft_error"]=str (_draft_exc )[:200 ]
                return ct.json .dumps (result ,ensure_ascii =False )
            result ["validation"]=val
        # TOOLA-AHEALTHHUNG-R1: write_fix already offloads this; write_new_file
        # used to run update_file_cache (and a possible 180s snapshot full-walk)
        # synchronously on the asyncio loop, so /health could not answer and
        # auto-heal killed the process after the file had already been committed.
        synced =await ct.asyncio .to_thread (ct._update_cache_after_write ,str (fp ),project_path )
        result ["cache_synced"]=synced
        if not synced :
            # Same double-bind as the write_fix path above: route to cache_delta, not
            # a full refresh of a large root.
            result ["cache_sync_warning"]=(
            "INDEX chua duoc cap nhat cho file nay. Goi cache_delta(project_path) "
            "hoac read_file_range(..., force_reseed=true) truoc khi doc/ghi tiep. "
            "KHONG goi refresh_project_map tren mot root lon nhu [A:P]."
            )
            result ["action_required"]="cache_delta"
            result ["ca_next_action"]="cache_delta"
            result ["fallback_sequence"]=["cache_delta","read_file_range"]
        if _cache_warning :
            result ["warning"]=_cache_warning
    return ct.json .dumps (result )


    # ─── Tool: apply_after_dry_run ────────────────────────────────────────────────

async def apply_after_dry_run (project_path :str ,dry_run_id :str ,
confirm_hash :str ="",
confirm_warnings :bool =False ,
skip_verify :bool =False )->str :
    import code_tools as ct
    root =ct.Path (project_path ).resolve ()
    dry_path =ct._dry_run_store_path (str (root ),dry_run_id )
    if not dry_path .exists ():
        return ct._json_error (
        f"dry_run_id not found: {dry_run_id}",
        error_name ="DRY_RUN_NOT_FOUND",
        retryable =False ,
        ca_next_action ="repeat_dry_run",
        )
    try :
        dry =ct.json .loads (dry_path .read_text (encoding ="utf-8"))
    except Exception as exc :
        return ct._json_error (
        f"Unable to read dry_run_id: {exc}",
        error_name ="DRY_RUN_INVALID",
        retryable =False ,
        ca_next_action ="repeat_dry_run",
        )
    if ct.time .time ()>float (dry .get ("expires_at_epoch")or 0 ):
        return ct._json_error (
        "dry_run_id expired",
        error_name ="DRY_RUN_EXPIRED",
        retryable =True ,
        ca_next_action ="repeat_dry_run",
        )
    if confirm_hash and confirm_hash !=dry .get ("payload_hash"):
        return ct._json_error (
        "confirm_hash does not match preview payload",
        error_name ="DRY_RUN_HASH_MISMATCH",
        retryable =True ,
        ca_next_action ="use_payload_hash_from_dry_run",
        )
    if dry .get ("requires_confirm")and not confirm_warnings :
        return ct._json_error (
        "dry_run has warnings that require confirm_warnings=true",
        error_name ="DRY_RUN_CONFIRM_REQUIRED",
        retryable =True ,
        warnings =dry .get ("warnings",[]),
        ca_next_action ="review_warnings_then_confirm_or_abort",
        )
    target =ct.Path (dry .get ("target_file","")).resolve ()
    try :
        target .relative_to (root )
    except ValueError :
        return ct._json_error (
        "dry_run target is outside project_path",
        error_name ="DRY_RUN_PROJECT_MISMATCH",
        retryable =False ,
        ca_next_action ="repeat_dry_run_with_correct_project_path",
        )
    current_hash =ct._sha256_file (target )
    if current_hash !=dry .get ("target_file_hash"):
        return ct._json_error (
        "Target file changed after dry-run",
        error_name ="STALE_TARGET",
        retryable =True ,
        target_file_hash =dry .get ("target_file_hash"),
        current_file_hash =current_hash ,
        ca_next_action ="repeat_dry_run",
        )
    payload =dry .get ("payload")or {}
    if dry .get ("tool")=="write_fix":
        result =ct.json .loads (await write_fix (**payload ))
    elif dry .get ("tool")=="write_new_file":
        result =ct.json .loads (await write_new_file (**payload ))
    else :
        return ct._json_error (
        f"Unsupported dry-run tool: {dry.get('tool')}",
        error_name ="DRY_RUN_UNSUPPORTED_TOOL",
        retryable =False ,
        ca_next_action ="repeat_dry_run",
        )
    result ["applied_from_dry_run"]=dry_run_id 
    result ["payload_hash"]=dry .get ("payload_hash")
    if result .get ("ok")and not skip_verify:
        try:
            target_rel =str (target .relative_to (root )).replace ("\\","/")
            backup_paths ={}
            if result .get ("backup"):
                backup_paths [target_rel]=result .get ("backup")
            batch =await _post_write_batch_verify_or_revert (str (root ),[target_rel],backup_paths )
            result ["batch_verify"]=batch .get ("validation",batch )
            if not batch .get ("ok"):
                result .update (batch )
        except Exception as exc:
            result ["batch_verify_warning"]=str (exc )
    elif result .get ("ok"):
        result ["batch_verify_skipped"]=True
    try :
        dry_path .unlink ()
    except Exception :
        pass 
    return ct.json .dumps (result ,ensure_ascii =False )


    # ─── Tool 8: delete_file ───────────────────────────────────────────────────────

async def delete_file (file_path :str ,project_path :str ="",ephemeral :bool =False )->str :
    import code_tools as ct
# R83: ephemeral=true is a CLEANUP mode for temp/derived files (C:/temp, %TEMP%,
# *.bak_*, .orchestrator_cache). When set AND the path is under such a root, the
# server skips the backup + session gates (see _is_ephemeral_temp_path), so deleting
# a one-off request/job/args file is a single call with no project_path / backup_override.
# F1: Reject relative paths without project_path to prevent out-of-project deletion
    if not ct.Path (file_path ).is_absolute ()and not project_path :
        return ct.json .dumps ({
        "ok":False ,
        "error":"project_path is required when file_path is relative",
        "hint":"Provide project_path to ensure deletion stays within project scope",
        })
    try :
        fp =ct._resolve_project_file (project_path ,file_path )
    except ValueError as exc :
        return _json_path_resolve_error(ct, exc)
    # C3 DURABLE: deny deletion of restart-control lifecycle artifacts. The secret at
    # .orchestrator_lifecycle/restart_control.secret is the authentication material for
    # the loopback restart controller; deleting it (as happened under .orchestrator_cache/)
    # permanently breaks restart_orchestrator_via_bat with RESTART_CONTROL_SECRET_UNAVAILABLE.
    # The check runs AFTER path resolution so it applies to absolute, relative, and
    # ephemeral deletions alike — the denylist cannot be bypassed via any caller mode.
    try :
        from orchestrator.lifecycle.restart_protocol import is_protected_control_path
        if is_protected_control_path (fp ):
            return ct.json .dumps ({
                "ok":False ,
                "error":"deletion of restart-control lifecycle files is denied",
                "error_name":"CONTROL_FILE_DELETE_DENIED",
                "protected_path":str (fp ),
                "ca_next_action":"do not delete restart control artifacts; if rotation is required, stop the restart controller and remove the file manually outside the write pipeline",
            })
    except ImportError :
        pass
    if not fp .exists ():
        return ct.json .dumps ({"error":f"File not found: {file_path}"})
    if not fp .is_file ():
        return ct.json .dumps ({"error":f"Not a file: {file_path}"})

    def _delete ():
        try :
            fp .unlink ()
            return {"ok":True ,"deleted":str (fp )}
        except Exception as exc :
            return {"error":str (exc )}

    result =await ct.asyncio .to_thread (_delete )
    if isinstance (result ,dict )and result .get ("ok"):
        root =ct.find_project_root (file_path )
        if root :
            try :
                ct.surgical_mapper .update_file_cache (file_path ,root )
            except Exception :
                pass 
    return ct.json .dumps (result )
