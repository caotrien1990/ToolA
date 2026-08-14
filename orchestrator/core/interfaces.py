"""Typed contracts shared across orchestration runtime modules."""
from __future__ import annotations

import ast
import inspect
from pathlib import Path
from typing import Protocol, runtime_checkable


@runtime_checkable
class DesktopDialogRuntime(Protocol):
    async def fill_folder_dialog(self, title_pattern: str = ..., folder_path: str = ..., timeout_ms: int = ..., wait_for_dialog_ms: int = ..., auto_try_common_titles: bool = ..., electron_fallback_hint: dict | None = ..., owner_pid: int = ..., parent_hwnd: int = ..., class_name_pattern: str = ..., exclude_titles: list | None = ..., launched_after_ts: str = ..., dry_run: bool = ..., try_modern_picker: bool = ...) -> str: ...
    async def folder_dialog_snapshot(self, owner_pid: int = ..., parent_hwnd: int = ..., title_pattern: str = ..., launched_after_ts: str = ..., include_child_controls: bool = ...) -> str: ...
    async def cancel_native_dialog(self, title_pattern: str = ..., button_pattern: str = ..., owner_pid: int = ..., parent_hwnd: int = ..., exclude_titles: list | None = ...) -> str: ...
    async def click_and_handle_folder_dialog(self, page_id: str, click_selector: str, folder_path: str, owner_pid: int = ..., owner_title: str = ..., wait_for_dialog_ms: int = ..., timeout_ms: int = ..., try_modern_picker: bool = ..., verify_target_exists: bool = ...) -> str: ...


def protocol_method_signatures(protocol: type) -> dict[str, inspect.Signature]:
    """Return public Protocol method signatures for pinning in tests."""
    out: dict[str, inspect.Signature] = {}
    for name, value in protocol.__dict__.items():
        if name.startswith("_") or not callable(value):
            continue
        out[name] = inspect.signature(value)
    return out


def _module_name(root: Path, path: Path) -> str:
    rel = path.relative_to(root).with_suffix("")
    if rel.parts[0] == "orchestrator":
        return ".".join(rel.parts)
    return rel.name


def _resolve_import(module: str, node: ast.AST, known: set[str]) -> set[str]:
    targets: set[str] = set()
    if isinstance(node, ast.Import):
        for alias in node.names:
            parts = alias.name.split(".")
            candidates = [alias.name] + [".".join(parts[:i]) for i in range(len(parts), 0, -1)]
            targets.update(c for c in candidates if c in known and c != module)
    elif isinstance(node, ast.ImportFrom):
        base = node.module or ""
        if node.level:
            parent = module.split(".")[:-node.level]
            base = ".".join(parent + ([base] if base else []))
        candidates = [base] + [base + "." + alias.name for alias in node.names if base]
        targets.update(c for c in candidates if c in known and c != module)
    return targets


def build_static_import_graph(root: str | Path) -> dict[str, set[str]]:
    """Build import-time graph for orchestrator package plus code_tools/server.

    The scan is AST-based and intentionally limited to module-level imports. Lazy
    imports inside functions are runtime compatibility hooks, not import-time cycles.
    """
    project_root = Path(root).resolve()
    paths = [project_root / "code_tools.py", project_root / "orchestrator_server.py"]
    paths.extend(sorted((project_root / "orchestrator").rglob("*.py")))
    modules = {
        _module_name(project_root, path): path
        for path in paths
        if path.exists() and "__pycache__" not in path.parts
    }
    graph: dict[str, set[str]] = {name: set() for name in modules}
    known = set(modules)
    for name, path in modules.items():
        tree = ast.parse(path.read_text(encoding="utf-8-sig"), filename=str(path))
        for node in tree.body:
            if isinstance(node, (ast.Import, ast.ImportFrom)):
                graph[name].update(_resolve_import(name, node, known))
    return graph


def bidirectional_import_pairs(graph: dict[str, set[str]]) -> list[tuple[str, str]]:
    pairs: list[tuple[str, str]] = []
    for left, targets in graph.items():
        for right in targets:
            if left < right and left in graph.get(right, set()):
                pairs.append((left, right))
    return pairs