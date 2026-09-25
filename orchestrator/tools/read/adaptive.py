"""Pure in-memory content cache and structural read planning."""
from __future__ import annotations

import ast
import hashlib
import math
import re
import threading
from collections import OrderedDict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from orchestrator.fileops import common as fo
from orchestrator.text_lines import split_lines

CACHE_MAX_ENTRIES = 64
CACHE_MAX_BYTES = 32 * 1024 * 1024
VALID_SCOPES = {"range", "function", "class", "section", "file_summary"}


@dataclass
class Document:
    path: Path
    root_key: str
    text: str
    lines: list[str]
    content_hash: str
    stat_sig: tuple[int, int]
    encoding: str
    size_bytes: int
    stable: bool = True
    analysis: dict[str, Any] | None = field(default=None)


_CACHE_BY_PATH: OrderedDict[str, Document] = OrderedDict()
_CACHE_BY_HASH: dict[tuple[str, str], Document] = {}
_CACHE_BYTES = 0
_CACHE_HITS = 0
_CACHE_MISSES = 0
_PARSE_HITS = 0
_INFLIGHT: set[str] = set()
_CACHE_COND = threading.Condition(threading.RLock())


def _stat_sig(value: Any) -> tuple[int, int]:
    return (int(getattr(value, "st_mtime_ns", value.st_mtime * 1e9)),
            int(value.st_size))


def _root_key(project_path: str | None, fp: Path) -> str:
    root = Path(project_path).resolve() if project_path else fp.parent.resolve()
    return str(root).replace("\\", "/").lower()


def _evict_locked() -> None:
    global _CACHE_BYTES
    while _CACHE_BY_PATH and (
            len(_CACHE_BY_PATH) > CACHE_MAX_ENTRIES or _CACHE_BYTES > CACHE_MAX_BYTES):
        _key, old = _CACHE_BY_PATH.popitem(last=False)
        _CACHE_BYTES = max(0, _CACHE_BYTES - old.size_bytes)
        hkey = (old.root_key, old.content_hash)
        if _CACHE_BY_HASH.get(hkey) is old:
            _CACHE_BY_HASH.pop(hkey, None)


def _cache_put_locked(key: str, doc: Document) -> None:
    global _CACHE_BYTES
    old = _CACHE_BY_PATH.pop(key, None)
    if old is not None:
        _CACHE_BYTES = max(0, _CACHE_BYTES - old.size_bytes)
        if _CACHE_BY_HASH.get((old.root_key, old.content_hash)) is old:
            _CACHE_BY_HASH.pop((old.root_key, old.content_hash), None)
    _CACHE_BY_PATH[key] = doc
    _CACHE_BY_HASH[(doc.root_key, doc.content_hash)] = doc
    _CACHE_BYTES += doc.size_bytes
    _evict_locked()


def get_document(fp: Path, project_path: str | None = None, *,
                 content_hash: str = "", force_refresh: bool = False) -> tuple[Document, dict]:
    """Return stable text via path-stat/content-hash LRU and per-path singleflight."""
    global _CACHE_HITS, _CACHE_MISSES
    key = str(fp.resolve()).replace("\\", "/").lower()
    root = _root_key(project_path, fp)
    before = fp.stat()
    signature = _stat_sig(before)
    waited = False
    with _CACHE_COND:
        if not force_refresh:
            cached = _CACHE_BY_PATH.get(key)
            if cached and cached.stat_sig == signature and (
                    not content_hash or cached.content_hash == content_hash):
                _CACHE_BY_PATH.move_to_end(key)
                _CACHE_HITS += 1
                return cached, {"cache_hit": True, "cache_waited": False,
                                "content_hash_mismatch": False}
            while key in _INFLIGHT:
                waited = True
                _CACHE_COND.wait(0.25)
                cached = _CACHE_BY_PATH.get(key)
                if cached and cached.stat_sig == signature and (
                        not content_hash or cached.content_hash == content_hash):
                    _CACHE_BY_PATH.move_to_end(key)
                    _CACHE_HITS += 1
                    return cached, {"cache_hit": True, "cache_waited": True,
                                    "content_hash_mismatch": False}
        _INFLIGHT.add(key)
        _CACHE_MISSES += 1
    try:
        encoding = fo.detect_text_encoding(fp)
        text = fp.read_text(encoding=encoding, errors="replace")
        after = fp.stat()
        stable = _stat_sig(after) == signature
        digest = hashlib.sha256(text.encode("utf-8", errors="replace")).hexdigest()
        doc = Document(fp, root, text, split_lines(text), digest, _stat_sig(after),
                       encoding, len(text.encode("utf-8", errors="replace")), stable)
        with _CACHE_COND:
            if stable:
                old_hash = _CACHE_BY_HASH.get((root, digest))
                if old_hash is not None and old_hash.text == text:
                    doc.analysis = old_hash.analysis
                _cache_put_locked(key, doc)
        return doc, {"cache_hit": False, "cache_waited": waited,
                     "content_hash_mismatch": bool(content_hash and content_hash != digest)}
    finally:
        with _CACHE_COND:
            _INFLIGHT.discard(key)
            _CACHE_COND.notify_all()


def cache_info() -> dict:
    with _CACHE_COND:
        return {"entries": len(_CACHE_BY_PATH), "bytes": _CACHE_BYTES,
                "hits": _CACHE_HITS, "misses": _CACHE_MISSES,
                "parse_hits": _PARSE_HITS, "max_entries": CACHE_MAX_ENTRIES,
                "max_bytes": CACHE_MAX_BYTES}


def estimate_tokens(value: str) -> int:
    return int(math.ceil(len(value.encode("utf-8", errors="replace")) / 4.0))


def numbered_text(lines: list[str], start_line: int) -> str:
    return "\n".join(f"{start_line + i}: {line}" for i, line in enumerate(lines))


def fit_numbered_lines(lines: list[str], start_line: int, *,
                       max_lines: int, token_budget: int = 0) -> tuple[list[str], int]:
    selected: list[str] = []
    used = 0
    for offset, line in enumerate(lines[:max(1, int(max_lines))]):
        cost = estimate_tokens(f"{start_line + offset}: {line}\n")
        if token_budget > 0 and selected and used + cost > token_budget:
            break
        if token_budget > 0 and not selected and cost > token_budget:
            selected.append(line[:max(1, token_budget * 4)])
            used = estimate_tokens(f"{start_line}: {selected[0]}")
            break
        selected.append(line)
        used += cost
    return selected, used


def _brace_end(lines: list[str], start: int) -> int:
    depth, opened = 0, False
    for idx in range(max(0, start - 1), len(lines)):
        line = re.sub(r"(['\"]).*?\1", "", lines[idx])
        for char in line:
            if char == "{":
                depth += 1
                opened = True
            elif char == "}" and opened:
                depth -= 1
                if depth <= 0:
                    return idx + 1
    return start


def _python_analysis(doc: Document) -> dict:
    symbols: list[dict] = []
    imports: list[str] = []

    class Visitor(ast.NodeVisitor):
        def __init__(self) -> None:
            self.stack: list[tuple[str, str]] = []

        def add(self, node: Any, kind: str) -> None:
            decos = getattr(node, "decorator_list", []) or []
            start = min([getattr(node, "lineno", 1)] +
                        [getattr(d, "lineno", getattr(node, "lineno", 1)) for d in decos])
            end = int(getattr(node, "end_lineno", getattr(node, "lineno", start)))
            name = str(getattr(node, "name", ""))
            qualified = ".".join([item[0] for item in self.stack] + [name])
            parent = self.stack[-1][1] if self.stack else ""
            resolved = "method" if kind == "function" and parent == "class" else kind
            symbols.append({"name": name, "qualified_name": qualified, "kind": resolved,
                            "start_line": int(start), "end_line": max(int(start), end),
                            "source": "python_ast"})
            self.stack.append((name, kind))
            self.generic_visit(node)
            self.stack.pop()

        def visit_ClassDef(self, node: ast.ClassDef) -> None:
            self.add(node, "class")

        def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
            self.add(node, "function")

        def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
            self.add(node, "function")

        def visit_Import(self, node: ast.Import) -> None:
            imports.extend(alias.name for alias in node.names)

        def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
            module = node.module or ""
            imports.extend(f"{module}.{alias.name}".strip(".") for alias in node.names)

    try:
        Visitor().visit(ast.parse(doc.text))
        return {"symbols": symbols, "imports": sorted(set(imports)),
                "parse_error": None, "parser": "python_ast"}
    except SyntaxError as exc:
        return {"symbols": [], "imports": [], "parse_error": str(exc),
                "parser": "python_ast_failed"}


def _regex_analysis(doc: Document) -> dict:
    symbols: list[dict] = []
    imports: list[str] = []
    suffix = doc.path.suffix.lower()
    for line_no, line in enumerate(doc.lines, 1):
        match = re.match(r"^\s*(?:export\s+)?(?:default\s+)?(?:async\s+)?"
                         r"(function|class)\s+([A-Za-z_$][\w$]*)", line)
        if match:
            kind, name = match.groups()
            symbols.append({"name": name, "qualified_name": name, "kind": kind,
                            "start_line": line_no, "end_line": _brace_end(doc.lines, line_no),
                            "source": "structural_regex"})
        arrow = re.match(r"^\s*(?:export\s+)?(?:const|let|var)\s+([A-Za-z_$][\w$]*)"
                         r"\s*=\s*(?:async\s*)?(?:\([^)]*\)|[A-Za-z_$][\w$]*)\s*=>", line)
        if arrow:
            name = arrow.group(1)
            symbols.append({"name": name, "qualified_name": name, "kind": "function",
                            "start_line": line_no, "end_line": _brace_end(doc.lines, line_no),
                            "source": "structural_regex"})
        if suffix in {".js", ".jsx", ".ts", ".tsx", ".mjs", ".cjs"}:
            imp = re.match(r"^\s*import\s+.*?\s+from\s+['\"]([^'\"]+)['\"]", line)
            if imp:
                imports.append(imp.group(1))
    return {"symbols": symbols, "imports": sorted(set(imports)),
            "parse_error": None, "parser": "structural_regex"}


def _sections(doc: Document) -> list[dict]:
    headings: list[tuple[int, int, str, str]] = []
    suffix = doc.path.suffix.lower()
    for line_no, line in enumerate(doc.lines, 1):
        if suffix in {".md", ".markdown"}:
            match = re.match(r"^\s*(#{1,6})\s+(.+?)\s*#*\s*$", line)
            if match:
                headings.append((line_no, len(match.group(1)),
                                 match.group(2).strip(), "markdown_heading"))
                continue
        match = re.match(r"^\s*(?:#|//|/\*)\s*(?:region|section)\s*[:\-]?\s*"
                         r"(.+?)(?:\*/)?\s*$", line, re.IGNORECASE)
        if match:
            headings.append((line_no, 1, match.group(1).strip(), "section_marker"))
        elif suffix in {".ini", ".toml", ".cfg"}:
            match = re.match(r"^\s*\[([^\]]+)\]\s*$", line)
            if match:
                headings.append((line_no, 1, match.group(1).strip(), "section_header"))
    sections: list[dict] = []
    for index, (start, level, name, source) in enumerate(headings):
        end = len(doc.lines)
        for next_start, next_level, _name, _source in headings[index + 1:]:
            if next_level <= level:
                end = next_start - 1
                break
        sections.append({"name": name, "qualified_name": name, "kind": "section",
                         "start_line": start, "end_line": max(start, end),
                         "source": source})
    return sections


def analyze_document(doc: Document) -> dict:
    global _PARSE_HITS
    with _CACHE_COND:
        if doc.analysis is not None:
            _PARSE_HITS += 1
            return doc.analysis
    result = _python_analysis(doc) if doc.path.suffix.lower() == ".py" else _regex_analysis(doc)
    result["sections"] = _sections(doc)
    result["line_count"] = len(doc.lines)
    with _CACHE_COND:
        doc.analysis = result
    return result


def _brief(items: list[dict]) -> list[dict]:
    return [{"name": x.get("name"), "qualified_name": x.get("qualified_name"),
             "kind": x.get("kind"), "start_line": x.get("start_line"),
             "end_line": x.get("end_line")} for x in items[:20]]


def resolve_selection(doc: Document, *, scope: str, start_line: int,
                      end_line: int | None, symbol: str = "", section: str = "",
                      related_query: str = "") -> dict:
    scope = (scope or "range").strip().lower()
    if scope not in VALID_SCOPES:
        return {"ok": False, "error_name": "INVALID_SCOPE",
                "error": f"Unsupported scope: {scope}",
                "accepted_values": sorted(VALID_SCOPES),
                "ca_next_action": "choose_scope_from_accepted_values"}
    total = len(doc.lines)
    if scope == "range":
        start = int(start_line) if start_line is not None else 1
        requested_end = int(end_line) if end_line is not None else None
        if start < 1 or (requested_end is not None and requested_end < start):
            return {
                "ok": False, "error_name": "INVALID_RANGE",
                "error": "Range requires 1 <= start_line <= end_line when end_line is supplied.",
                "requested_start_line": start_line, "requested_end_line": end_line,
                "read_token_seeded": False, "derived_state_touched": False,
                "action_required": "correct_range", "ca_next_action": "correct_range",
                "hint": "Choose the intended range with start_line >= 1 and end_line >= start_line, "
                        "or omit end_line. Bounds are inclusive; start_line=end_line reads one line. "
                        "For up to N lines, use start_line + max_lines=N and omit end_line "
                        "(subject to EOF and backend/token caps). "
                        "Do not blindly swap bounds. force_reseed does not repair an invalid range.",
            }
        if total == 0:
            if start > 1:
                return {
                    "ok": False,
                    "error_name": "RANGE_PAST_EOF",
                    "error": f"start_line {start} exceeds file line count 0.",
                    "total_lines": 0,
                    "ca_next_action": "re_read_file_range_within_total_lines",
                    "hint": (
                        "Use start_line from a prior read_file_range on THIS file; "
                        "native editor line numbers are not accepted."
                    ),
                }
            return {"ok": True, "scope": "range", "name": None,
                    "start_line": 1, "end_line": 0, "source": "line_range"}
        if start > total:
            return {
                "ok": False,
                "error_name": "RANGE_PAST_EOF",
                "error": f"start_line {start} exceeds file line count {total}.",
                "total_lines": total,
                "ca_next_action": "re_read_file_range_within_total_lines",
                "hint": (
                    "Use start_line from a prior read_file_range on THIS file; "
                    "native editor line numbers are not accepted."
                ),
            }
        end = min(total, requested_end) if requested_end is not None else total
        return {"ok": True, "scope": "range", "name": None,
                "start_line": start, "end_line": end,
                "source": "line_range"}
    analysis = analyze_document(doc)
    if scope == "file_summary":
        return {"ok": True, "scope": scope, "name": doc.path.name,
                "start_line": 1, "end_line": total, "source": analysis["parser"]}
    if scope in {"function", "class"}:
        allowed = {"function", "method"} if scope == "function" else {"class"}
        pool = [x for x in analysis["symbols"] if x["kind"] in allowed]
        target = (symbol or related_query or "").strip()
    else:
        pool = list(analysis["sections"])
        target = (section or related_query or symbol or "").strip()
    matches: list[dict] = []
    if target:
        lower = target.lower()
        matches = [x for x in pool if str(x.get("qualified_name", "")).lower() == lower]
        if not matches:
            matches = [x for x in pool if str(x.get("name", "")).lower() == lower]
    else:
        anchor = max(1, int(start_line or 1))
        matches = sorted([x for x in pool if x["start_line"] <= anchor <= x["end_line"]],
                         key=lambda x: x["end_line"] - x["start_line"])[:1]
    if len(matches) > 1:
        return {"ok": False, "error_name": "AMBIGUOUS_SELECTOR",
                "error": f"Multiple {scope} matches for {target!r}",
                "candidates": _brief(matches),
                "ca_next_action": "retry_with_qualified_symbol_or_anchor_line"}
    if not matches:
        token = target.lower()
        ranked = sorted(pool, key=lambda x: (
            0 if token and token in str(x.get("name", "")).lower() else 1,
            x["start_line"]))
        return {"ok": False,
                "error_name": "SECTION_NOT_FOUND" if scope == "section" else "SYMBOL_NOT_FOUND",
                "error": f"No {scope} matched {target!r}",
                "candidates": _brief(ranked), "parse_error": analysis.get("parse_error"),
                "ca_next_action": "choose_candidate_or_retry_with_anchor_line"}
    selected = dict(matches[0])
    selected.update({"ok": True, "scope": scope})
    return selected


def file_summary(doc: Document, *, max_items: int = 40) -> dict:
    analysis = analyze_document(doc)
    return {"file": doc.path.name, "line_count": len(doc.lines),
            "size_bytes": doc.size_bytes, "content_hash": doc.content_hash,
            "parser": analysis["parser"], "parse_error": analysis.get("parse_error"),
            "imports": analysis["imports"][:max_items],
            "symbols": _brief(analysis["symbols"][:max_items]),
            "sections": _brief(analysis["sections"][:max_items]),
            "symbols_total": len(analysis["symbols"]),
            "sections_total": len(analysis["sections"])}


def rank_next_ranges(doc: Document, selection: dict, *,
                     related_query: str = "", limit: int = 12,
                     shown_end: int | None = None) -> list[dict]:
    analysis = analyze_document(doc)
    current_start = int(selection.get("start_line", 1))
    current_end = int(selection.get("end_line", current_start))
    query = " ".join(filter(None, [related_query, str(selection.get("name") or ""),
                                   str(selection.get("qualified_name") or "")])).lower()
    tokens = set(re.findall(r"[a-zA-Z_][\w]*", query))
    ranked: list[tuple[float, dict]] = []
    if shown_end is not None and shown_end < current_end:
        ranked.append((10000.0, {"file_path": str(doc.path), "scope": "range",
            "start_line": shown_end + 1, "end_line": current_end,
            "name": selection.get("name"), "kind": "continuation",
            "relevance_score": 1.0, "reason": "selected_scope_continuation",
            "source": selection.get("source")}))
    for item in list(analysis["symbols"]) + list(analysis["sections"]):
        start, end = int(item["start_line"]), int(item["end_line"])
        if start <= current_end and end >= current_start:
            continue
        haystack = f"{item.get('name', '')} {item.get('qualified_name', '')}".lower()
        overlap = sum(1 for token in tokens if token in haystack)
        distance = min(abs(start - current_end), abs(current_start - end))
        bonus = {"function": 3.0, "method": 3.0, "class": 2.5,
                 "section": 1.5}.get(str(item.get("kind")), 1.0)
        score = overlap * 10.0 + bonus + 1.0 / (1.0 + distance)
        child_scope = ("class" if item.get("kind") == "class" else
                       "section" if item.get("kind") == "section" else "function")
        ranked.append((score, {"file_path": str(doc.path), "scope": child_scope,
            "start_line": start, "end_line": end,
            "name": item.get("qualified_name") or item.get("name"),
            "kind": item.get("kind"), "relevance_score": round(score, 4),
            "reason": "query_overlap" if overlap else "structural_neighbor",
            "source": item.get("source")}))
    ranked.sort(key=lambda pair: (-pair[0], pair[1]["start_line"],
                                  str(pair[1].get("name") or "")))
    seen: set[tuple[int, int]] = set()
    output: list[dict] = []
    for _score, item in ranked:
        key = (item["start_line"], item["end_line"])
        if key in seen:
            continue
        seen.add(key)
        output.append(item)
        if len(output) >= max(0, int(limit)):
            break
    return output
