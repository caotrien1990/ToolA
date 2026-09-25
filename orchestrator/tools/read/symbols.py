from __future__ import annotations

import ast
import asyncio
import json
import re
from pathlib import Path

from orchestrator.text_lines import split_lines


async def get_file_symbols(file_path: str, project_path: str = "") -> str:
    if project_path and not Path(file_path).is_absolute():
        file_path = str(Path(project_path) / file_path)
    fp = Path(file_path)
    if not fp.exists():
        return json.dumps({"error": f"File not found: {file_path}"})

    js_patterns = [
        (re.compile(r"^\s*function\s+(\w+)\s*\("), "function"),
        (re.compile(r"^\s*async\s+function\s+(\w+)\s*\("), "function"),
        (re.compile(r"^\s*class\s+(\w+)\s*[\({]"), "class"),
        (re.compile(r"^\s*(export\s+)?(const|let|var)\s+(\w+)\s*="), "variable"),
        (re.compile(r"^\s*(\w+)\s*:\s*function\s*\("), "method"),
        (re.compile(r"^\s*(?:export\s+)?(?:const|let|var)\s+(\w+)\s*=\s*(?:async\s+)?\([^)]*\)\s*=>"), "arrow_fn"),
    ]

    def _scan() -> dict:
        ext = fp.suffix.lower()
        try:
            content = fp.read_text(encoding="utf-8", errors="ignore")
        except Exception as exc:
            return {"error": str(exc)}
        if content.startswith("\ufeff"):
            content = content.lstrip("\ufeff")
        if ext == ".py":
            symbols: list[dict] = []
            try:
                tree = ast.parse(content)
            except SyntaxError as exc:
                return {"error": f"syntax error: {exc}", "symbols": [], "count": 0}
            for node in ast.walk(tree):
                if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    symbols.append({"name": node.name, "type": "function", "line": node.lineno})
                elif isinstance(node, ast.ClassDef):
                    symbols.append({"name": node.name, "type": "class", "line": node.lineno})
            return {"symbols": symbols, "count": len(symbols), "lang": "python", "kind": "source_symbols"}
        if ext in (".js", ".mjs", ".cjs", ".ts", ".tsx", ".jsx"):
            symbols: list[dict] = []
            for i, line in enumerate(split_lines(content), 1):
                for pat, sym_type in js_patterns:
                    match = pat.match(line)
                    if match:
                        symbols.append({"name": match.group(match.lastindex), "type": sym_type, "line": i})
                        break
            return {"symbols": symbols, "count": len(symbols), "lang": "js", "kind": "source_symbols"}
        return {"symbols": [], "count": 0, "lang": "unsupported", "kind": "unsupported_symbols", "ext": ext}

    return json.dumps(await asyncio.to_thread(_scan), ensure_ascii=False)
