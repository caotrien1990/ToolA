import ast
import fnmatch
import hashlib
import json
import logging
import os
import re
import time
from datetime import datetime
from pathlib import Path

from orchestrator import search_snapshots

try:
    from tree_sitter_languages import get_parser
    TREE_SITTER_AVAILABLE = True
except ImportError:
    TREE_SITTER_AVAILABLE = False

CACHE_DIR_NAME = ".orchestrator_cache"
INDEX_FILENAME = "INDEX.json"
PROJECT_CONTEXT_FILENAME = "PROJECT_CONTEXT.md"
MAP_SCHEMA_VERSION = 2
PRESERVED_CACHE_FILES = {
    "WORKFLOW_STATE.json",
    "BACKUP_MANIFEST.json",
    "CA_SESSION_BOOTSTRAP.md",
    "ui_log.jsonl",
    "mutation_token.txt",
    "orchestrator.lock",
}
# TT-A-04 (2026-08-10): ALL SQLite artifacts under .orchestrator_cache must be
# preserved deterministically. The previous explicit list covered sessions.db
# and graph.db but not their -wal/-shm/-journal sidecars, and not
# durable_audit.db / missions.db at all. refresh_project_map then tried to
# unlink a WAL-mode SQLite file that the orchestrator process holds open,
# which fails on Windows with WinError 32 (sharing violation) and bubbles up
# as AUTO_REFRESH_FAILED from query_index(auto_refresh=true). Pattern-based
# matching is the correct boundary: any file whose name ends in .db,
# .db-wal, .db-shm, or .db-journal is a runtime SQLite artifact, not a cache
# artifact safe to delete.
SQLITE_CACHE_SUFFIXES = (".db", ".db-wal", ".db-shm", ".db-journal")
PRESERVED_CACHE_DIRS = {"search_snapshots"}
SKIP_DIRS = {
    "node_modules", "venv", "venv312", ".venv", ".git", ".svn",
    ".orchestrator_cache", "__pycache__", ".pytest_cache", ".mypy_cache",
    ".tox", "dist", "build", ".idea", ".vscode",
    # Anh 2026-08-11: backup_project writes a "_orchestrator_backups" tree
    # (root.parent, but live repos also keep in-tree copies). It is not a
    # dotfile, so should_skip_dir did not skip it and search/index scanned the
    # whole backup set -> REGEX_SEARCH_TIMEOUT on narrow patterns. Skip it.
    "_orchestrator_backups",
    # 2026-08-13: live-data repos (e.g. ToolQ) keep large NON-code trees that bloat the
    # index and break regex/symbol search + cause INDEX/manifest scan-vs-walk drift: an
    # embedded venv, backup snapshots, staging clones, codex mission dirs, pytest temp.
    ".venv_bge_m3", "backups", "staging", ".codex", ".pytest_tmp",
}
# SOT council lives in .council — searchable despite the generic dot-dir skip.
DOT_DIRS_SEARCHABLE = {".council"}


def should_skip_dir(name: str) -> bool:
    lowered = name.lower()
    if lowered in DOT_DIRS_SEARCHABLE:
        return False
    return lowered in SKIP_DIRS or name.startswith(".")


SKIP_FILE_GLOBS = {
    "*.bak", "*.log", "*.tmp", "*.temp", "*.rar", "*.zip", "*.7z", "*.lnk",
    "*.pyc", "*.pyo", ".DS_Store", "Thumbs.db",
    # 2026-08-13: transient runtime state — never index. On an active repo (e.g. ToolQ) a
    # runner recreates locks/*.running/*.lease between the cache scan and the snapshot walk,
    # so an indexed one races out of the walk and generate_project_map died with
    # "INDEX/manifest file set mismatch". Skipping them in BOTH scan + walk keeps the sets
    # consistent and the full build resilient on live repos.
    "*.running", "*runlock*", "*.lease", "*.pid",
    "analyze_functions_classes*.py", "clean_venv.py", "find_junk.py",
    "inspect_index_json.py", "remove_venv_keys.py", "report_index.py",
    "scan_full_project.py", "claudecode.html", "find_8082.py",
    "inspect_orchestrator.py", "restart_orchestrator.py", "temp_*.py",
    "analysis_output.txt", "summary*.txt", "scan_result*.txt",
    "structure_output.txt", "PROJECT_MAP.md", "PROJECT_CONTEXT.md", "unicode_test.txt", "test_*_output.txt",
    "test_structure.txt", "test_mcp.bat",
    "*proxy*.py", "*proxy*.ps1", "*proxy*.bat", "ollama_executor.py",
    ".gemini_proxy_state.json", ".gpt_config.json", ".deepseek_config.json",
}
SECRET_FILE_GLOBS = {
    "*apikey*", "*api_key*", "*secret*", "*access_token*", "*refresh_token*",
    "*auth_token*", "token.*", "*.pem", "*.key",
    ".env", ".env.*",
}
SUPPORTED_EXTENSIONS = {
    '.py', '.js', '.jsx', '.ts', '.tsx', '.css', '.sql', '.sh', '.json', '.cfg', '.ini', '.txt',
    '.yaml', '.yml', '.md', '.toml', '.xml', '.lock',
    '.bat', '.cmd', '.ps1', '.html', '.reg'
}
ENTRYPOINT_FILES = {"main.py", "app.py", "index.js", "server.js"}
IMPORTANT_FILENAMES = {
    "readme.md", "agent_workflow.md", "requirements.txt", "package.json",
    "pyproject.toml", "orchestrator_server.py", "code_tools.py",
    "surgical_mapper.py", "orchestrator_ui.py",
}


def _cache_path_is_preserved(relative: str) -> bool:
    normalized = str(relative).replace("\\", "/").strip("/")
    if normalized in PRESERVED_CACHE_FILES:
        return True
    # TT-A-04: any SQLite runtime artifact (any depth) is preserved. Check the
    # basename so a .db file inside a subdirectory is still caught.
    basename = normalized.rsplit("/", 1)[-1].lower()
    if basename.endswith(SQLITE_CACHE_SUFFIXES):
        return True
    return any(
        normalized == directory or normalized.startswith(directory + "/")
        for directory in PRESERVED_CACHE_DIRS
    )


def _iter_cache_json_files(cache_root: Path):
    for walk_root, dirs, files in os.walk(cache_root):
        dirs[:] = sorted(
            directory
            for directory in dirs
            if directory not in PRESERVED_CACHE_DIRS
        )
        for filename in sorted(files):
            if filename.lower().endswith(".json"):
                yield Path(walk_root) / filename


def _write_index_atomic(project_root: Path, index: dict) -> None:
    index_path = project_root / CACHE_DIR_NAME / INDEX_FILENAME
    payload = (json.dumps(index, ensure_ascii=False, indent=2) + "\n").encode("utf-8")
    search_snapshots._atomic_write(index_path, payload)


def _publish_search_snapshot(project_root: Path, index: dict, changed_paths: dict | None = None) -> str:
    def _publish(cp):
        return search_snapshots.publish_snapshot(
            project_root,
            include_file=_should_scan_file,
            skip_dir=should_skip_dir,
            index=index,
            changed_paths=cp,
        )
    try:
        snapshot_id = _publish(changed_paths)
    except search_snapshots.SnapshotDeadlineExceeded:
        raise
    except Exception:
        # The walk-avoidance fast path is only valid when the prior snapshot is compatible
        # with `index` (same file set/filter). If it is not, the validators raised before
        # commit — fall back to an authoritative full walk (self-healing, never mis-publishes).
        if changed_paths is None:
            raise
        snapshot_id = _publish(None)
    # GC every _SNAPSHOT_GC_EVERY publishes rather than every write: it parses the retained
    # generation manifests + scans the blob store (O(generations x repo)), which is wasteful
    # to repeat on each single-file write. keep_last bounds accumulation between sweeps.
    if _snapshot_should_gc(Path(project_root).resolve()):
        search_snapshots.collect_garbage(project_root, keep_last=10)
    return snapshot_id


def get_file_hash(file_path: str) -> str:
    fp = Path(file_path)
    h = hashlib.md5()
    with fp.open("rb") as f:
        for chunk in iter(lambda: f.read(8192), b""):
            h.update(chunk)
    return h.hexdigest()


def _is_comment_line(line: str, extension: str) -> bool:
    stripped = line.strip()
    if not stripped:
        return False
    if extension == ".py":
        return stripped.startswith("#")
    if extension in {".js", ".jsx", ".ts"}:
        return stripped.startswith("//") or stripped.startswith("/*") or stripped.startswith("* ")
    return False


def count_loc(lines: list[str], extension: str) -> int:
    loc = 0
    in_block = False
    for line in lines:
        stripped = line.strip()
        if not stripped:
            continue
        if extension in {".js", ".jsx", ".ts"}:
            if stripped.startswith("/*"):
                in_block = True
            if in_block:
                if stripped.endswith("*/"):
                    in_block = False
                continue
        if _is_comment_line(line, extension):
            continue
        loc += 1
    return loc


def _rel_path(file_path: str | Path, project_root: str | Path) -> str:
    return str(Path(file_path).resolve().relative_to(Path(project_root).resolve())).replace("\\", "/")


def _matches_any(name: str, patterns: set[str]) -> bool:
    low = name.lower()
    return any(fnmatch.fnmatch(low, pattern.lower()) for pattern in patterns)


def _is_secret_file(file_path: Path) -> bool:
    return _matches_any(file_path.name, SECRET_FILE_GLOBS)


def _is_generated_or_junk(file_path: Path) -> bool:
    return file_path.name.startswith(".") or _matches_any(file_path.name, SKIP_FILE_GLOBS)


def _skip_reason(file_path: Path) -> str | None:
    if _is_secret_file(file_path):
        return "secret_like_filename"
    if _is_generated_or_junk(file_path):
        return "generated_archive_backup_or_internal_ai"
    if file_path.suffix.lower() not in SUPPORTED_EXTENSIONS:
        return "unsupported_extension"
    parts = {part.lower() for part in file_path.parts}
    for skip_dir in SKIP_DIRS:
        if skip_dir.lower() in parts:
            return f"ignored_directory:{skip_dir}"
    return None


def _infer_purpose(rel_path: str, group: str, symbols: list[dict]) -> str:
    name = Path(rel_path).name.lower()
    if name == "orchestrator_server.py":
        return "HTTP/MCP server that exposes project tools."
    if name == "code_tools.py":
        return "Tool implementations shared by REST and MCP entrypoints."
    if name == "surgical_mapper.py":
        return "Project-map and per-file cache generator."
    if name == "orchestrator_ui.py":
        return "Desktop UI wrapper for running and monitoring the orchestrator."
    if name == "agent_workflow.md":
        return "Workflow contract for CA/worker collaboration."
    if name == "readme.md":
        return "Human setup and usage documentation."
    if group == "entrypoint":
        return "Application entrypoint."
    if group == "config":
        return "Configuration or dependency metadata."
    if group == "doc":
        return "Project documentation."
    if symbols:
        names = ", ".join(s.get("name", "") for s in symbols[:3] if s.get("name"))
        return f"Source module defining {names}." if names else "Source module."
    return group or "unknown"


def classify_group(file_path: str, project_root: str) -> str:
    rel = Path(file_path).resolve().relative_to(Path(project_root).resolve())
    rel_str = str(rel).replace("\\", "/")
    rel_low = rel_str.lower()
    suffix = Path(file_path).suffix.lower()
    name = Path(file_path).name.lower()

    if any(part.lower() in SKIP_DIRS for part in rel.parts):
        return "ignored"
    if _is_secret_file(Path(file_path)):
        return "secret"
    if _is_generated_or_junk(Path(file_path)):
        return "generated"
    if rel_low.startswith("tests/") or "/tests/" in rel_low:
        return "test"
    if rel_low.startswith("ui/") or "/ui/" in rel_low:
        return "ui"
    if name in ENTRYPOINT_FILES or name == "orchestrator_server.py":
        return "entrypoint"
    if name in {"requirements.txt", "package-lock.json"}:
        return "config"
    if name == "package.json":
        try:
            content = Path(file_path).read_text(encoding="utf-8", errors="ignore")
            data = json.loads(content)
            if isinstance(data, dict) and data.get("bin"):
                return "entrypoint"
        except Exception:
            pass
        return "config"
    if suffix in {".cfg", ".ini", ".toml", ".xml", ".json", ".lock"}:
        return "config"
    if suffix == ".md":
        return "doc"
    if suffix in {".py", ".js", ".jsx", ".ts"}:
        return "source"
    if suffix in {".bat", ".ps1", ".reg"}:
        return "script"
    if suffix == ".html":
        return "ui"
    return "unknown"


def _language_for_extension(extension: str) -> str | None:
    return {
        ".py": "python",
        ".js": "javascript",
        ".jsx": "javascript",
        ".ts": "typescript",
        ".tsx": "typescript",
        ".html": "html",
        ".css": "css",
        ".json": "json",
        ".yaml": "yaml",
        ".yml": "yaml",
        ".toml": "toml",
        ".md": "markdown",
        ".ps1": "powershell",
        ".bat": "batch",
        ".sh": "shell",
        ".sql": "sql",
        ".xml": "xml",
        ".txt": "text",
    }.get(extension)


def _human_language_hints(source: str) -> list[str]:
    hints: list[str] = []
    vietnamese_marks = "ăâđêôơưáàảãạấầẩẫậắằẳẵặéèẻẽẹếềểễệíìỉĩịóòỏõọốồổỗộớờởỡợúùủũụứừửữựýỳỷỹỵ"
    low = source.lower()
    if any(ch in low for ch in vietnamese_marks):
        hints.append("vi")
    if re.search(r"\b(the|and|with|for|from|return|project|workflow|cache)\b", low):
        hints.append("en")
    return hints[:3]


def _read_hint(group: str, loc: int, symbols: list[dict]) -> str:
    if loc > 500:
        return "large_file: use symbols, search, or bounded ranges before full read"
    if symbols:
        return "symbol_routed: use function/class refs from INDEX first"
    if group in {"doc", "config"} and loc <= 120:
        return "small_context: full read acceptable if needed"
    return "metadata_first: search or read narrow ranges"


def _file_weight(group: str, loc: int, complexity: int, rel: str) -> int:
    weight = 1
    if group == "entrypoint":
        weight += 8
    elif group in {"source", "ui"}:
        weight += 4
    elif group == "test":
        weight += 2
    if Path(rel).name.lower() in IMPORTANT_FILENAMES:
        weight += 5
    weight += min(5, max(0, loc // 150))
    weight += min(5, max(0, complexity // 20))
    return weight


def _extract_python_symbols(source: str) -> tuple[list[dict], list[str], int]:
    symbols: list[dict] = []
    imports: list[str] = []
    complexity = 0
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return _extract_symbols_with_regex(source, ".py")

    # Only scan top-level definitions + one-level class methods.
    # ast.walk recurses into nested helpers, causing false "symbols removed" in validate_symbols.
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            symbols.append({
                "type": "function",
                "name": node.name,
                "line_start": node.lineno,
                "line_end": getattr(node, "end_lineno", node.lineno),
            })
        elif isinstance(node, ast.ClassDef):
            symbols.append({
                "type": "class",
                "name": node.name,
                "line_start": node.lineno,
                "line_end": getattr(node, "end_lineno", node.lineno),
            })
            for sub in node.body:
                if isinstance(sub, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    symbols.append({
                        "type": "method",
                        "name": f"{node.name}.{sub.name}",
                        "line_start": sub.lineno,
                        "line_end": getattr(sub, "end_lineno", sub.lineno),
                    })
        elif isinstance(node, ast.Assign):
            if getattr(node, "col_offset", 0) == 0:
                for target in node.targets:
                    if isinstance(target, ast.Name) and target.id.upper() == target.id:
                        symbols.append({
                            "type": "global_constant",
                            "name": target.id,
                            "line_start": node.lineno,
                            "line_end": getattr(node, "end_lineno", node.lineno),
                        })
        elif isinstance(node, ast.Import):
            imports.extend(alias.name.split(".", 1)[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imports.append(node.module.split(".", 1)[0])

    # Complexity: count branching nodes at any depth
    for node in ast.walk(tree):
        if isinstance(node, (ast.If, ast.For, ast.AsyncFor, ast.While, ast.Try, ast.BoolOp, ast.ExceptHandler)):
            complexity += 1

    symbols.sort(key=lambda item: (item["line_start"], item["name"]))
    return symbols, imports, complexity


def _extract_symbols_with_parser(source: str, extension: str) -> tuple[list[dict], list[str], int]:
    symbols: list[dict] = []
    imports: list[str] = []
    complexity = 0

    if not TREE_SITTER_AVAILABLE:
        return symbols, imports, complexity

    language_name = _language_for_extension(extension)
    if not language_name:
        return symbols, imports, complexity

    try:
        parser = get_parser(language_name)
        tree = parser.parse(source.encode("utf-8"))
        root = tree.root_node
        for node in root.walk():
            typ = node.type
            if typ in {"function_definition", "function_declaration", "method_definition", "class_definition", "class"}:
                name = None
                for child in node.children:
                    if child.type == "identifier":
                        name = source[child.start_byte:child.end_byte].strip()
                        break
                if name:
                    symbols.append({
                        "type": "function" if "function" in typ else "class",
                        "name": name,
                        "line_start": node.start_point[0] + 1,
                        "line_end": node.end_point[0] + 1,
                    })
            if typ == "import_statement" or typ == "import_clause" or typ == "import" or typ == "import_declaration":
                imports.extend([source[child.start_byte:child.end_byte].strip() for child in node.children if child.type == "string" or child.type == "identifier"])
        complexity = source.count("if ") + source.count("else") + source.count("for ") + source.count("while ")
    except (ValueError, RuntimeError) as exc:
        logging.getLogger(__name__).debug("tree-sitter parse failed: %s", exc)
    return symbols, imports, complexity


def _extract_symbols_with_regex(source: str, extension: str) -> tuple[list[dict], list[str], int]:
    symbols: list[dict] = []
    imports: list[str] = []
    complexity = 0
    lines = source.splitlines()

    def _estimate_block_end(start_index: int) -> int:
        start_line = lines[start_index - 1]
        base_indent = len(start_line) - len(start_line.lstrip())
        if extension == ".py":
            for j in range(start_index, len(lines)):
                line = lines[j]
                stripped_line = line.strip()
                if not stripped_line or stripped_line.startswith("#"):
                    continue
                indent = len(line) - len(line.lstrip())
                if indent <= base_indent:
                    return j
            return len(lines)

        balance = 0
        seen_open = False
        for j in range(start_index - 1, len(lines)):
            balance += lines[j].count("{") - lines[j].count("}")
            seen_open = seen_open or "{" in lines[j]
            if seen_open and balance <= 0:
                return j + 1
        return start_index

    for i, line in enumerate(lines, 1):
        stripped = line.strip()
        if not stripped:
            continue
        if extension == ".py":
            if stripped.startswith("def "):
                name = stripped.split("def ", 1)[1].split("(", 1)[0].strip()
                symbols.append({"type": "function", "name": name, "line_start": i, "line_end": _estimate_block_end(i)})
            elif stripped.startswith("class "):
                name = stripped.split("class ", 1)[1].split("(", 1)[0].split(":", 1)[0].strip()
                symbols.append({"type": "class", "name": name, "line_start": i, "line_end": _estimate_block_end(i)})
            elif re.match(r"^[A-Z0-9_]+\s*=", stripped):
                name = stripped.split("=", 1)[0].strip()
                symbols.append({"type": "global_constant", "name": name, "line_start": i, "line_end": i})
            elif stripped.startswith("import "):
                imports.extend([part.strip().split(".")[0] for part in stripped[len("import "):].split(",")])
            elif stripped.startswith("from "):
                imports.append(stripped.split()[1].split(".")[0])
        if extension in {".js", ".jsx", ".ts"}:
            if stripped.startswith("function "):
                name = stripped.split("function ", 1)[1].split("(", 1)[0].strip()
                symbols.append({"type": "function", "name": name, "line_start": i, "line_end": _estimate_block_end(i)})
            elif re.match(r"^(const|let|var)\s+[A-Z0-9_]+\s*=", stripped):
                name = stripped.split()[1].split("=")[0].strip()
                symbols.append({"type": "global_constant", "name": name, "line_start": i, "line_end": i})
            elif stripped.startswith("class "):
                name = stripped.split("class ", 1)[1].split("{", 1)[0].strip()
                symbols.append({"type": "class", "name": name, "line_start": i, "line_end": _estimate_block_end(i)})
            elif stripped.startswith("import "):
                imports.append(stripped.split()[1].strip().split(".")[0])
            elif stripped.startswith("const ") or stripped.startswith("let ") or stripped.startswith("var "):
                if "=" in stripped:
                    lhs = stripped.split("=", 1)[0].strip().split()
                    if len(lhs) >= 2:
                        imports.append(lhs[1].split(".")[0])
        complexity += stripped.count("if ") + stripped.count("else") + stripped.count("for ") + stripped.count("while ")
    return symbols, imports, complexity


def analyze_file(file_path: str, project_root: str) -> dict:
    file_path = str(Path(file_path))
    extension = Path(file_path).suffix.lower()
    rel = _rel_path(file_path, project_root)
    result = {
        "file": rel,
        "hash": "",
        "loc": 0,
        "size_bytes": 0,
        "line_count": 0,
        "encoding": "utf-8",
        "language": _language_for_extension(extension) or "unknown",
        "extension": extension,
        "group": classify_group(file_path, project_root),
        "purpose": "",
        "role": "",
        "read_hint": "",
        "human_languages": [],
        "symbols": [],
        "imports": [],
        "complexity_score": 0,
        "weight": 1,
        "error": False,
        "error_message": None,
    }

    try:
        fp = Path(file_path)
        if not fp.exists() or not fp.is_file():
            return result
        _st = fp.stat()
        result["size_bytes"] = _st.st_size
        result["mtime"] = _st.st_mtime
        result["hash"] = get_file_hash(file_path)
        try:
            raw = fp.read_text(encoding="utf-8", errors="ignore")
        except Exception as exc:
            result["error"] = True
            result["error_message"] = str(exc)
            return result
        if raw.startswith("\ufeff"):
            raw = raw.lstrip("\ufeff")

        if extension in SUPPORTED_EXTENSIONS:
            lines = raw.splitlines()
            result["line_count"] = len(lines)
            result["loc"] = count_loc(lines, extension)
            if extension == ".py":
                symbols, imports, complexity = _extract_python_symbols(raw)
            else:
                symbols, imports, complexity = _extract_symbols_with_regex(raw, extension)
            if TREE_SITTER_AVAILABLE and extension in {".js", ".jsx", ".ts"}:
                parser_symbols, parser_imports, parser_complexity = _extract_symbols_with_parser(raw, extension)
                if parser_symbols:
                    symbols = parser_symbols
                if parser_imports:
                    imports = parser_imports
                if parser_complexity:
                    complexity = parser_complexity
            result["symbols"] = symbols
            result["imports"] = list(dict.fromkeys(imports))
            result["complexity_score"] = complexity
        else:
            lines = raw.splitlines()
            result["line_count"] = len(lines)
            result["loc"] = len([line for line in lines if line.strip()])
        result["purpose"] = _infer_purpose(rel, result["group"], result["symbols"])
        result["role"] = result["purpose"]
        result["read_hint"] = _read_hint(result["group"], result["loc"], result["symbols"])
        result["human_languages"] = _human_language_hints(raw[:20000])
        result["weight"] = _file_weight(result["group"], result["loc"], result["complexity_score"], rel)
    except Exception as exc:
        result["error"] = True
        result["error_message"] = str(exc)
    return result


def _cache_path(project_root: str, file_path: str) -> Path:
    project_root = Path(project_root).resolve()
    rel = Path(file_path).resolve().relative_to(project_root)
    cache_file = Path(project_root) / CACHE_DIR_NAME / f"{rel.as_posix()}.json"
    return cache_file


def write_file_cache(file_path: str, project_root: str) -> dict:
    if not _should_scan_file(Path(file_path)):
        return {
            "file": _rel_path(file_path, project_root),
            "skipped": True,
            "reason": "ignored_by_project_map_filter",
        }
    cache_file = _cache_path(project_root, file_path)
    cache_file.parent.mkdir(parents=True, exist_ok=True)
    # F8: Wrap analyze_file - errors must not crash the whole cache pipeline
    try:
        metadata = analyze_file(file_path, project_root)
    except Exception as _exc:
        import logging as _log_wfc
        _log_wfc.warning(f"write_file_cache: analyze_file failed for {file_path}: {_exc}")
        metadata = {
            "file": _rel_path(file_path, project_root),
            "error": True,
            "error_message": str(_exc),
        }
    cache_file.write_text(json.dumps(metadata, ensure_ascii=False, indent=2), encoding="utf-8")
    return metadata


def remove_file_cache(file_path: str, project_root: str) -> bool:
    try:
        cache_file = _cache_path(project_root, file_path)
    except Exception:
        return False
    if cache_file.exists():
        cache_file.unlink()
        for parent in cache_file.parents:
            if parent == Path(project_root).resolve() / CACHE_DIR_NAME:
                break
            try:
                parent.rmdir()
            except OSError:
                break
        return True
    return False


def _new_index_skeleton(project_root: Path) -> dict:
    return {
        "schema_version": MAP_SCHEMA_VERSION,
        "project_root": str(project_root),
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "files": {},
        "functions": {},
        "classes": {},
        "entrypoints": [],
        "groups": {},
        "hotspots": [],
    }


def _index_file_entry(data: dict) -> dict:
    """Projection of one file's cache JSON into an INDEX 'files' entry. Shared by the full
    build_index and the incremental single-file update so both emit byte-identical entries
    (CAN-039 D5 anti-drift: one source of truth, no delta divergence)."""
    return {
        "hash": data.get("hash"),
        "mtime": data.get("mtime"),
        "loc": data.get("loc"),
        "line_count": data.get("line_count"),
        "size_bytes": data.get("size_bytes"),
        "encoding": data.get("encoding"),
        "language": data.get("language"),
        "extension": data.get("extension"),
        "group": data.get("group"),
        "purpose": data.get("purpose"),
        "role": data.get("role"),
        "read_hint": data.get("read_hint"),
        "human_languages": data.get("human_languages", []),
        "weight": data.get("weight"),
        "complexity_score": data.get("complexity_score"),
        "symbols_count": len(data.get("symbols", [])),
        "imports": data.get("imports", [])[:20],
    }


def _index_register_symbols(index: dict, rel: str, data: dict) -> None:
    """Append this file's function/class refs + entrypoint flag into the index (shared)."""
    for symbol in data.get("symbols", []):
        name = symbol.get("name")
        typ = symbol.get("type")
        if not name or not typ:
            continue
        ref = {
            "file": rel,
            "line_start": symbol.get("line_start"),
            "line_end": symbol.get("line_end"),
        }
        if typ == "function":
            index["functions"].setdefault(name, []).append(ref)
        elif typ == "class":
            index["classes"].setdefault(name, []).append(ref)
    if data.get("group") == "entrypoint":
        index["entrypoints"].append(rel)


def _index_finalize_aggregates(index: dict) -> None:
    """(Re)compute groups + hotspots from index['files'] in memory (no disk I/O). Shared by
    the full build and the incremental patch so aggregates never drift between the two."""
    groups: dict[str, dict] = {}
    for file_info in index["files"].values():
        group = file_info.get("group") or "unknown"
        groups.setdefault(group, {"files": 0, "loc": 0})
        groups[group]["files"] += 1
        groups[group]["loc"] += file_info.get("loc") or 0
    index["groups"] = groups
    index["hotspots"] = [
        {"file": rel, "loc": info.get("loc"), "complexity_score": info.get("complexity_score"), "group": info.get("group")}
        for rel, info in sorted(
            index["files"].items(),
            key=lambda item: ((item[1].get("complexity_score") or 0), (item[1].get("loc") or 0)),
            reverse=True,
        )[:20]
    ]


def build_index(project_root: str, timeout_s: float | None = None) -> dict:
    # CAN-040: bound the cache-assembly walk so a pathological / very large cache tree
    # cannot hang the daemon (build_index runs on every write via update_file_cache).
    # On exceed we raise BEFORE the INDEX.json write below, so the previous valid index
    # is preserved; callers' try/except surface a controlled error, not a hang. Generous
    # default (cache reads are JSON, normally <1s); env-tunable.
    if timeout_s is None:
        try:
            timeout_s = float(os.environ.get("ORCHESTRATOR_BUILD_INDEX_TIMEOUT_SEC", "30") or 30)
        except Exception:
            timeout_s = 30.0
    _bi_deadline = time.monotonic() + max(1.0, timeout_s)
    project_root = Path(project_root).resolve()
    cache_root = project_root / CACHE_DIR_NAME
    index = _new_index_skeleton(project_root)

    if not cache_root.exists():
        return index

    # F9: Collect corrupt cache files instead of silently skipping
    _corrupt_cache_files: list[str] = []
    for json_file in _iter_cache_json_files(cache_root):
        if time.monotonic() > _bi_deadline:
            # CAN-040: controlled timeout — raise before any INDEX.json write so the
            # previous index is preserved (callers catch and keep the old cache).
            raise TimeoutError(
                f"build_index exceeded {timeout_s}s for {project_root} "
                f"(cache too large/pathological); previous INDEX.json preserved"
            )
        try:
            data = json.loads(json_file.read_text(encoding="utf-8"))
        except Exception as _exc:
            import logging as _log_bi
            _log_bi.warning(f"build_index: corrupt cache file {json_file}: {_exc}")
            _corrupt_cache_files.append(str(json_file))
            continue
        rel = data.get("file")
        if not rel:
            continue
        index["files"][rel] = _index_file_entry(data)
        _index_register_symbols(index, rel, data)

    _index_finalize_aggregates(index)
    index["corrupt_cache_files"] = _corrupt_cache_files

    _write_index_atomic(project_root, index)
    return index


def update_index_incremental(project_root, cache_data: dict, *, removed: bool = False) -> dict:
    """Patch INDEX.json for ONE changed file WITHOUT re-reading the whole cache tree.

    The old design called build_index() on every write, which re-reads every cached JSON
    (minutes on a 29k-file repo -> writers fell back to native edits). Here we load the
    existing INDEX, drop the changed file's entry + its function/class refs + entrypoint
    flag, re-add them from `cache_data` (the freshly written cache JSON, unless removed),
    then recompute the aggregates in memory. Cost is O(changed) disk + O(symbols) memory,
    not O(repo) disk.

    Correctness: the per-file projection and the aggregate recomputation reuse the SAME
    helpers as build_index (_index_file_entry / _index_register_symbols /
    _index_finalize_aggregates), so an incrementally-patched INDEX is byte-identical to a
    full rebuild for the same cache state. If the INDEX is missing/corrupt we fall back to
    a full build_index so first-write and recovery paths stay correct.
    """
    project_root = Path(project_root).resolve()
    rel = cache_data.get("file") if isinstance(cache_data, dict) else None
    if not rel:
        return build_index(str(project_root))
    idx_path = project_root / CACHE_DIR_NAME / INDEX_FILENAME
    try:
        index = json.loads(idx_path.read_text(encoding="utf-8"))
        if not isinstance(index.get("files"), dict):
            raise ValueError("INDEX missing 'files' map")
    except Exception:
        # No usable prior index (first write, corruption) -> authoritative full rebuild.
        return build_index(str(project_root))

    index.setdefault("functions", {})
    index.setdefault("classes", {})
    index.setdefault("entrypoints", [])

    # Remove any prior entry/refs for this file (tolerate slash-variant keys defensively).
    rel_variants = {rel, rel.replace("\\", "/"), rel.replace("/", "\\")}
    for key in list(index["files"].keys()):
        if key in rel_variants:
            index["files"].pop(key, None)

    def _drop_refs(name_map: dict) -> None:
        for name in list(name_map.keys()):
            kept = [r for r in name_map[name] if r.get("file") not in rel_variants]
            if kept:
                name_map[name] = kept
            else:
                name_map.pop(name, None)

    _drop_refs(index["functions"])
    _drop_refs(index["classes"])
    index["entrypoints"] = [e for e in index["entrypoints"] if e not in rel_variants]

    # Re-add (unless the file was removed/skipped). build_index adds any cache JSON that
    # carries a "file" field regardless of error, so we mirror that: add on !removed.
    if not removed:
        index["files"][rel] = _index_file_entry(cache_data)
        _index_register_symbols(index, rel, cache_data)

    index["generated_at"] = datetime.now().isoformat(timespec="seconds")
    _index_finalize_aggregates(index)
    index.setdefault("corrupt_cache_files", [])
    _write_index_atomic(project_root, index)
    return index


def _should_scan_file(file_path: Path) -> bool:
    return _skip_reason(file_path) is None


def _write_project_map_markdown(project_root: Path, index: dict) -> None:
    lines = [
        "# Project Map",
        "",
        f"- Schema: {index.get('schema_version')}",
        f"- Generated: {index.get('generated_at')}",
        f"- Root: `{index.get('project_root')}`",
        f"- Files indexed: {len(index.get('files', {}))}",
        "",
        "## Groups",
        "",
    ]
    for group, info in sorted(index.get("groups", {}).items()):
        lines.append(f"- `{group}`: {info.get('files', 0)} files, {info.get('loc', 0)} LOC")

    lines.extend(["", "## Entrypoints", ""])
    for rel in index.get("entrypoints", []):
        purpose = index.get("files", {}).get(rel, {}).get("purpose", "")
        lines.append(f"- `{rel}`: {purpose}")

    lines.extend(["", "## Hotspots", ""])
    for item in index.get("hotspots", [])[:10]:
        lines.append(
            f"- `{item.get('file')}`: complexity {item.get('complexity_score')}, "
            f"{item.get('loc')} LOC, group `{item.get('group')}`"
        )

    lines.extend(["", "## Agent Usage", ""])
    lines.append("- Start with `.orchestrator_cache/INDEX.json` for routing.")
    lines.append("- Prefer `PROJECT_CONTEXT.md` for a compact route guide before opening source.")
    lines.append("- Use function/class refs to call `read_file_range` only on the needed line span.")
    lines.append("- Use per-file cache JSON for imports, purpose, and symbol details before opening source.")
    (project_root / "PROJECT_MAP.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


def _write_project_context_markdown(project_root: Path, index: dict) -> None:
    files = index.get("files", {})
    lines = [
        "# Project Context",
        "",
        "This file is generated for CA routing. Read it before opening source files.",
        "",
        f"- Root: `{index.get('project_root')}`",
        f"- Generated: {index.get('generated_at')}",
        f"- Files indexed: {len(files)}",
        "",
        "## First Actions",
        "",
        "1. `cache_status` -> `get_project_summary` -> (if has_session_log) `get_usage_stats`",
        "2. Navigate: `find_symbol(name)` -> lay range_id -> `read_file_range` (<=200 lines)",
        "3. KHONG doc `.orchestrator_cache/INDEX.json` raw -- dung tool query",
        "4. Sau write: `validate_file` -> `cache_delta`",
        "",
        "## Search Routing",
        "",
        "| Goal | Tool | Use search_code when |",
        "|------|------|----------------------|",
        "| Find function/class def | `find_symbol` | find_symbol misses |",
        "| Filter files by group/LOC | `query_index` | -- |",
        "| Find callers/imports | `search_code` | always valid |",
        "| Regex pattern search | `search_code` | always valid |",
        "",
        "Guardrail: NOT first tool in session -- NOT replacing find_symbol for defs -- escape `.()+-` in regex",
        "",
        "## Groups",
        "",
    ]
    for group, info in sorted(index.get("groups", {}).items()):
        lines.append(f"- `{group}`: {info.get('files', 0)} files, {info.get('loc', 0)} LOC")

    lines.extend(["", "## Highest Value Files", ""])
    ranked = sorted(
        files.items(),
        key=lambda item: ((item[1].get("weight") or 0), (item[1].get("complexity_score") or 0), (item[1].get("loc") or 0)),
        reverse=True,
    )
    for rel, info in ranked[:20]:
        lines.append(
            f"- `{rel}` | group `{info.get('group')}` | lang `{info.get('language')}` | "
            f"LOC {info.get('loc')} | weight {info.get('weight')}: {info.get('read_hint')}"
        )

    lines.extend(["", "## Entrypoints", ""])
    for rel in index.get("entrypoints", []):
        info = files.get(rel, {})
        lines.append(f"- `{rel}`: {info.get('purpose', '')}")

    lines.extend(["", "## Hotspots", ""])
    for item in index.get("hotspots", [])[:10]:
        lines.append(
            f"- `{item.get('file')}`: complexity {item.get('complexity_score')}, "
            f"{item.get('loc')} LOC, group `{item.get('group')}`"
        )

    lines.extend(["", "## Deterministic Rules", ""])
    lines.append("- No internal AI/model/proxy tools are required.")
    lines.append("- No crawler tools are part of this workflow.")
    lines.append("- Preserve human-language text unless the user requests rewriting or translation.")
    lines.append("- Treat files over 500 LOC as large: read symbols or bounded ranges first.")
    lines.append("- Never read `.orchestrator_cache/INDEX.json` raw -- use `get_project_summary` or `query_index`.")
    lines.append("- Use `find_symbol` before `read_file_range` for large files (>500 LOC).")
    (project_root / PROJECT_CONTEXT_FILENAME).write_text("\n".join(lines) + "\n", encoding="utf-8")


def summarize_index(index: dict) -> dict:
    project_root = Path(index.get("project_root", ""))
    return {
        "schema_version": index.get("schema_version"),
        "project_root": index.get("project_root"),
        "generated_at": index.get("generated_at"),
        "index_path": str(project_root / CACHE_DIR_NAME / INDEX_FILENAME) if project_root else "",
        "project_map_path": str(project_root / "PROJECT_MAP.md") if project_root else "",
        "project_context_path": str(project_root / PROJECT_CONTEXT_FILENAME) if project_root else "",
        "files_indexed": len(index.get("files", {})),
        "functions_indexed": len(index.get("functions", {})),
        "classes_indexed": len(index.get("classes", {})),
        "groups": index.get("groups", {}),
        "entrypoints": index.get("entrypoints", []),
        "hotspots": index.get("hotspots", [])[:10],
    }


import threading as _threading

_PMAP_LOCKS: dict = {}
_PMAP_GUARD = _threading.Lock()


def _pmap_lock(project_root) -> "_threading.RLock":
    """R49 (2026-08-12): per-project reentrant lock serializing full cache rebuilds.
    Concurrent generate/refresh of the SAME project race on unlink/rmdir of the shared
    .orchestrator_cache tree -> FILE_ACCESS_DENIED (surfaced under stress: N agents
    calling refresh_project_map at once). Reentrant so refresh->generate on one thread
    does not self-deadlock; keyed per project so different projects stay parallel."""
    key = str(project_root)
    with _PMAP_GUARD:
        lk = _PMAP_LOCKS.get(key)
        if lk is None:
            lk = _threading.RLock()
            _PMAP_LOCKS[key] = lk
        return lk


def generate_project_map(project_path: str) -> dict:
    project_root = Path(project_path).resolve()
    if not project_root.exists() or not project_root.is_dir():
        raise FileNotFoundError(f"Project path not found: {project_path}")

    with _pmap_lock(project_root):
        cache_root = project_root / CACHE_DIR_NAME
        if cache_root.exists():
            for path in sorted(cache_root.rglob("*"), reverse=True):
                rel = path.relative_to(cache_root).as_posix()
                if _cache_path_is_preserved(rel):
                    continue
                if path.is_file():
                    path.unlink()
                elif path.is_dir():
                    try:
                        path.rmdir()
                    except OSError:
                        pass

        for root, dirs, files in os.walk(project_root):
            dirs[:] = [d for d in dirs if not should_skip_dir(d)]

            for fn in files:
                fp = Path(root) / fn
                if _should_scan_file(fp):
                    write_file_cache(str(fp), str(project_root))

        index = build_index(str(project_root))
        _write_project_map_markdown(project_root, index)
        _write_project_context_markdown(project_root, index)
        snapshot_id = _publish_search_snapshot(project_root, index)
        summary = summarize_index(index)
        summary["snapshot_id"] = snapshot_id
        return summary


def refresh_project_map(project_path: str) -> dict:
    # R49: refresh IS a full rebuild. generate_project_map already deletes the cache
    # before rebuilding, so the old separate delete loop here was redundant AND a second
    # concurrent collision point. Delegate under the same per-project lock.
    return generate_project_map(project_path)


# Per-write snapshot uses the O(changed) walk-avoidance fast path (see publish_snapshot).
# Every _SNAPSHOT_FULL_REFRESH_EVERY writes we force one authoritative full walk instead, so
# any file changed OUTSIDE the write pipeline (external editor, git checkout) is reconciled
# with a bounded staleness window rather than never. Counter is advisory; a benign race only
# shifts which write does the full walk.
_SNAPSHOT_WRITE_COUNTER: dict[str, int] = {}
_SNAPSHOT_FULL_REFRESH_EVERY = 50
_SNAPSHOT_GC_COUNTER: dict[str, int] = {}
_SNAPSHOT_GC_EVERY = 10


def _snapshot_needs_full_refresh(project_root: Path) -> bool:
    key = os.path.normcase(str(project_root))
    n = _SNAPSHOT_WRITE_COUNTER.get(key, 0) + 1
    _SNAPSHOT_WRITE_COUNTER[key] = n
    return (n % _SNAPSHOT_FULL_REFRESH_EVERY) == 0


def _snapshot_should_gc(project_root: Path) -> bool:
    key = os.path.normcase(str(project_root))
    n = _SNAPSHOT_GC_COUNTER.get(key, 0) + 1
    _SNAPSHOT_GC_COUNTER[key] = n
    return (n % _SNAPSHOT_GC_EVERY) == 1  # run on the 1st publish of each project, then every 10th


def update_file_cache(file_path: str, project_root: str) -> dict:
    """Write/remove ONE file's cache entry, then rebuild the project INDEX.

    CAN-039 (decision D5 = document, no delta rework): the per-file cache write is
    already incremental (write_file_cache / remove_file_cache touch only this file),
    but the INDEX is then rebuilt in full via build_index() on every write. That full
    rebuild is intentionally kept because it is the SIMPLE + always-CORRECT source of
    truth — partial/delta INDEX maintenance risks drifting the functions/classes/
    groups/hotspots aggregates. The rebuild reads cached JSON (not source), so it is
    cheap on current project sizes; build_index is timeout-guarded (CAN-040).

    CAN-039 invalidation policy (decision D8 = directory-entry): cache validity is
    scoped at the directory level — SKIP_DIRS subtrees are never cached, and a moved/
    deleted directory is reconciled by remove_file_cache + the next full build_index
    (which only retains entries whose cache JSON still exists). A single file change
    invalidates exactly that file's entry; the rebuilt INDEX drops entries for files
    whose cache was removed.
    """
    if not Path(file_path).exists() or not _should_scan_file(Path(file_path)):
        removed = remove_file_cache(file_path, project_root)
        metadata = {
            "file": _rel_path(file_path, project_root),
            "skipped": True,
            "cache_removed": removed,
        }
    else:
        metadata = write_file_cache(file_path, project_root)
    resolved_root = Path(project_root).resolve()
    # Incremental single-file INDEX patch (was: full build_index on every write, which
    # re-read the entire cache tree — O(repo) disk, minutes on 29k-file repos). Falls back
    # to a full rebuild inside update_index_incremental when the INDEX is missing/corrupt.
    removed = bool(metadata.get("skipped") or metadata.get("cache_removed"))
    index = update_index_incremental(resolved_root, metadata, removed=removed)
    _write_project_map_markdown(resolved_root, index)
    _write_project_context_markdown(resolved_root, index)
    # Snapshot: name the single changed file so publish_snapshot takes the walk-avoidance
    # fast path (O(changed), not O(repo)); periodically force a full walk to catch external
    # edits. changed_paths falls back to a full walk automatically if the prior is stale.
    rel_for_snapshot = metadata.get("file")
    changed_paths = None
    if rel_for_snapshot and not _snapshot_needs_full_refresh(resolved_root):
        changed_paths = {rel_for_snapshot: "removed" if removed else "modified"}
    metadata["snapshot_id"] = _publish_search_snapshot(resolved_root, index, changed_paths)
    return metadata


def get_cache_status(project_path: str) -> dict:
    project_root = Path(project_path).resolve()
    if not project_root.exists() or not project_root.is_dir():
        return {
            "cache_exists": False,
            "index_exists": False,
            "status": "invalid_project_path",
            "message": f"Project path not found: {project_path}",
        }

    cache_root = project_root / CACHE_DIR_NAME
    index_file = cache_root / INDEX_FILENAME
    if not cache_root.exists() or not index_file.exists():
        return {
            "cache_exists": cache_root.exists(),
            "index_exists": index_file.exists(),
            "status": "not_initialized",
            "message": "Cache is not initialized. Run generate_project_map to create the cache.",
        }

    try:
        modified = index_file.stat().st_mtime
        file_count = sum(1 for _path in _iter_cache_json_files(cache_root))
        return {
            "cache_exists": True,
            "index_exists": True,
            "status": "ok",
            "last_updated": modified,
            "last_updated_iso": datetime.fromtimestamp(modified).isoformat(timespec='seconds'),
            "cache_files": file_count,
            "message": "Cache is initialized.",
        }
    except Exception as exc:
        return {
            "cache_exists": True,
            "index_exists": True,
            "status": "error",
            "message": str(exc),
        }
