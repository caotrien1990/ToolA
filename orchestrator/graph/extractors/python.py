"""Python symbol/edge extractor (Phase 1 Step 6).

P0 primary: stdlib `ast`. Optional: tree-sitter when CBM_USE_TREE_SITTER=1 (A5 aspirational).
Emits nodes (function | class | method | variable | module | import) and edges
(calls | imports | defines | inherits) with CBM confidence on the numeric
scale (A8): EXACT=1.0, UNIQUE=0.9, SAME_MODULE=0.7, SUFFIX=0.3-0.7. The stored value is
numeric; cbm_label is a display-only category (static | inferred | dynamic | unknown).
`references` edges (non-call name/attribute loads) are deferred - they would explode the
edge count for P0; calls already cover the primary cross-symbol usage signal.
"""
import ast
import hashlib
import os
from typing import Optional

from ..config import (
    CBM_EXACT, CBM_UNIQUE, CBM_SAME_MODULE, CBM_SUFFIX_LOW, CBM_SUFFIX_HIGH,
    CBM_LABEL_TO_NUMERIC, CBM_USE_TREE_SITTER,
)

# Reverse lookup: numeric -> display label (category only, not stored as truth).
_NUMERIC_TO_LABEL = {v: k for k, v in CBM_LABEL_TO_NUMERIC.items()}


def _label_for(confidence: float) -> str:
    """Map a numeric CBM value back to its display category label."""
    return _NUMERIC_TO_LABEL.get(round(confidence, 2), "unknown")


def _node_id(file_path: str, kind: str, name: str, line: int) -> str:
    return f"{file_path}:{kind}:{name}:{line}"


def _qualified_name(module: str, *parts: str) -> str:
    bits = [module] + [p for p in parts if p]
    return ".".join(bits)


def _visibility(name: str) -> str:
    if name.startswith("__") and name.endswith("__"):
        return "public"  # dunder is conventionally public interface
    if name.startswith("_"):
        return "private"
    return "public"


def _signature(fn: ast.FunctionDef | ast.AsyncFunctionDef) -> str:
    args = fn.args
    parts = []
    # positional
    pos = [a.arg for a in args.posonlyargs] + [a.arg for a in args.args]
    if args.vararg:
        pos.append("*" + args.vararg.arg)
    elif args.kwonlyargs:
        pos.append("*")
    parts.append(", ".join(pos))
    # kwonly
    if args.kwonlyargs:
        kw = ", ".join(a.arg for a in args.kwonlyargs)
        parts.append(kw)
    if args.kwarg:
        parts.append("**" + args.kwarg.arg)
    sig = ", ".join(p for p in parts if p)
    name = fn.name
    return f"{name}({sig})"


def _decorators(node: ast.AST) -> list[str]:
    out = []
    for d in getattr(node, "decorator_list", []):
        try:
            out.append(ast.unparse(d))
        except Exception:
            out.append("<decorator>")
    return out


class _Extractor(ast.NodeVisitor):
    """Walks an AST tree, accumulating nodes + edges for one file."""

    def __init__(self, *, file_path: str, module: str, source_hash: str):
        self.file_path = file_path
        self.module = module
        self.source_hash = source_hash
        self.nodes: list[dict] = []
        self.edges: list[dict] = []
        # name -> node_id for definitions visible in this file (best-effort scope).
        self._local_defs: dict[str, str] = {}
        # class stack for method detection
        self._class_stack: list[str] = []
        self._class_stack_lineno: list[int] = []
        # enclosing function node id for call edges
        self._func_stack: list[str] = []
        # names listed in module __all__ (marks is_exported post-visit)
        self._exported: set[str] = set()

    def _emit_module_node(self) -> None:
        nid = _node_id(self.file_path, "module", self.module, 1)
        self.nodes.append({
            "id": nid, "kind": "module", "name": os.path.basename(self.file_path).rsplit(".", 1)[0],
            "qualified_name": self.module, "file_path": self.file_path, "language": "python",
            "start_line": 1, "end_line": 1, "visibility": "public",
        })

    # ── definitions ────────────────────────────────────────────────────────

    def _emit_function(self, node: ast.FunctionDef | ast.AsyncFunctionDef) -> str:
        kind = "method" if self._class_stack else "function"
        class_prefix = self._class_stack[-1] if self._class_stack else None
        qname = _qualified_name(self.module, class_prefix, node.name)
        nid = _node_id(self.file_path, kind, qname, node.lineno)
        self.nodes.append({
            "id": nid, "kind": kind, "name": node.name, "qualified_name": qname,
            "file_path": self.file_path, "language": "python",
            "start_line": node.lineno, "end_line": getattr(node, "end_lineno", node.lineno),
            "docstring": ast.get_docstring(node) or "",
            "signature": _signature(node), "visibility": _visibility(node.name),
            "is_async": isinstance(node, ast.AsyncFunctionDef),
            "is_static": any("staticmethod" in d for d in _decorators(node)),
            "is_abstract": any("abstractmethod" in d for d in _decorators(node)),
            "decorators": _decorators(node),
        })
        # register in local defs (last definition wins for name resolution)
        self._local_defs[node.name] = nid
        # defines edge: module/class -> function
        if self._class_stack:
            cls_id = _node_id(self.file_path, "class",
                              _qualified_name(self.module, self._class_stack[-1]),
                              self._class_stack_lineno[-1])
            self.edges.append({
                "source": cls_id, "target": nid, "kind": "defines",
                "confidence": CBM_EXACT, "cbm_label": _label_for(CBM_EXACT),
                "line": node.lineno,
            })
        return nid

    def _emit_class(self, node: ast.ClassDef) -> str:
        qname = _qualified_name(self.module, node.name)
        nid = _node_id(self.file_path, "class", qname, node.lineno)
        bases = []
        for b in node.bases:
            try:
                bases.append(ast.unparse(b))
            except Exception:
                bases.append("<base>")
        self.nodes.append({
            "id": nid, "kind": "class", "name": node.name, "qualified_name": qname,
            "file_path": self.file_path, "language": "python",
            "start_line": node.lineno, "end_line": getattr(node, "end_lineno", node.lineno),
            "docstring": ast.get_docstring(node) or "",
            "visibility": _visibility(node.name),
            "decorators": _decorators(node),
        })
        self._local_defs[node.name] = nid
        # inherits edges — resolve to a local def when possible so the target is
        # a real node id; unresolved bases get the ext: prefix (orphan-edge fix)
        for bname in bases:
            base_key = bname.split("[", 1)[0]          # strip generics: Base[T] -> Base
            simple = base_key.split(".")[-1]
            # only class/import defs are valid inherit targets — a same-named
            # local function must not capture the edge (kind-confusion fix)
            cand = (self._local_defs.get(base_key), self._local_defs.get(simple))
            target = next((c for c in cand
                           if c and (":class:" in c or ":import:" in c)), None)
            if target:
                self.edges.append({
                    "source": nid, "target": target, "kind": "inherits",
                    "confidence": CBM_SAME_MODULE, "cbm_label": _label_for(CBM_SAME_MODULE),
                    "line": node.lineno,
                })
            else:
                self.edges.append({
                    "source": nid, "target": f"ext:{base_key}", "kind": "inherits",
                    "confidence": CBM_SUFFIX_LOW, "cbm_label": _label_for(CBM_SUFFIX_LOW),
                    "line": node.lineno, "is_external": 1,
                })
        return nid

    # ── imports ────────────────────────────────────────────────────────────

    def _emit_imports(self, node: ast.Import | ast.ImportFrom) -> None:
        mod_id = _node_id(self.file_path, "module", self.module, 1)
        if isinstance(node, ast.Import):
            for alias in node.names:
                qname = alias.name
                nid = _node_id(self.file_path, "import", qname, node.lineno)
                self.nodes.append({
                    "id": nid, "kind": "import", "name": alias.asname or alias.name,
                    "qualified_name": qname, "file_path": self.file_path,
                    "language": "python", "start_line": node.lineno,
                    "end_line": node.lineno, "visibility": "public",
                })
                self.edges.append({
                    "source": mod_id, "target": nid, "kind": "imports",
                    "confidence": CBM_EXACT, "cbm_label": _label_for(CBM_EXACT),
                    "line": node.lineno,
                })
                self._local_defs[alias.asname or alias.name] = nid
        else:  # ImportFrom
            base_mod = node.module or ""
            for alias in node.names:
                qname = f"{base_mod}.{alias.name}" if base_mod else alias.name
                nid = _node_id(self.file_path, "import", qname, node.lineno)
                self.nodes.append({
                    "id": nid, "kind": "import", "name": alias.asname or alias.name,
                    "qualified_name": qname, "file_path": self.file_path,
                    "language": "python", "start_line": node.lineno,
                    "end_line": node.lineno, "visibility": "public",
                })
                self.edges.append({
                    "source": mod_id, "target": nid, "kind": "imports",
                    "confidence": CBM_EXACT, "cbm_label": _label_for(CBM_EXACT),
                    "line": node.lineno,
                })
                self._local_defs[alias.asname or alias.name] = nid

    # -- calls ----------------------------------------------------------------

    def _callable_target_for_name(self, name: str) -> str | None:
        target = self._local_defs.get(name)
        if target and (":function:" in target or ":method:" in target):
            return target
        return None

    def _callable_target_for_attribute(self, attr: str) -> str | None:
        matched = [nid for n, nid in self._local_defs.items()
                   if n == attr or n.endswith("." + attr)]
        return next((nid for nid in matched
                     if ":function:" in nid or ":method:" in nid), None)

    def _emit_callable_ref_edge(self, *, source_id: str, target_id: str,
                                line: int, via: str) -> None:
        self.edges.append({
            "source": source_id, "target": target_id, "kind": "calls",
            "confidence": CBM_SAME_MODULE, "cbm_label": _label_for(CBM_SAME_MODULE),
            "line": line,
            "metadata": {"callable_ref": via},
        })

    def _emit_callable_ref_from_expr(self, expr: ast.AST, *, source_id: str,
                                     line: int, via: str) -> None:
        if isinstance(expr, ast.Name):
            target = self._callable_target_for_name(expr.id)
        elif isinstance(expr, ast.Attribute):
            target = self._callable_target_for_attribute(expr.attr)
        else:
            target = None
        if target:
            self._emit_callable_ref_edge(
                source_id=source_id, target_id=target, line=line, via=via)

    def _emit_callable_string_ref(self, node: ast.Call, *, source_id: str) -> None:
        if not isinstance(node.func, ast.Name) or node.func.id not in {"getattr", "hasattr"}:
            return
        if len(node.args) < 2:
            return
        name_arg = node.args[1]
        if not (isinstance(name_arg, ast.Constant) and isinstance(name_arg.value, str)):
            return
        target = self._callable_target_for_name(name_arg.value)
        if target:
            self._emit_callable_ref_edge(
                source_id=source_id, target_id=target, line=node.lineno,
                via="callable_string_ref")

    def _emit_call(self, node: ast.Call) -> None:
        if not self._func_stack:
            return  # calls outside a function have no enclosing source node
        source_id = self._func_stack[-1]
        func = node.func
        if isinstance(func, ast.Name):
            callee = func.id
            if callee in self._local_defs:
                self.edges.append({
                    "source": source_id, "target": self._local_defs[callee],
                    "kind": "calls", "confidence": CBM_UNIQUE,
                    "cbm_label": _label_for(CBM_UNIQUE), "line": node.lineno,
                })
            else:
                # external / unresolved
                ext_id = f"ext:{callee}"
                self.edges.append({
                    "source": source_id, "target": ext_id, "kind": "calls",
                    "confidence": CBM_SUFFIX_LOW, "cbm_label": _label_for(CBM_SUFFIX_LOW),
                    "line": node.lineno, "is_external": 1,
                })
        elif isinstance(func, ast.Attribute):
            attr = func.attr
            # suffix match against local defs (e.g. store.get_db -> get_db)
            matched = [nid for n, nid in self._local_defs.items() if n == attr or n.endswith("." + attr)]
            if matched:
                self.edges.append({
                    "source": source_id, "target": matched[0], "kind": "calls",
                    "confidence": CBM_SUFFIX_HIGH, "cbm_label": _label_for(CBM_SUFFIX_HIGH),
                    "line": node.lineno,
                })
            else:
                ext_id = f"ext:{attr}"
                self.edges.append({
                    "source": source_id, "target": ext_id, "kind": "calls",
                    "confidence": CBM_SUFFIX_LOW, "cbm_label": _label_for(CBM_SUFFIX_LOW),
                    "line": node.lineno, "is_external": 1,
                })

        for arg in node.args:
            self._emit_callable_ref_from_expr(
                arg, source_id=source_id, line=getattr(arg, "lineno", node.lineno),
                via="callable_arg")
        for keyword in node.keywords:
            self._emit_callable_ref_from_expr(
                keyword.value, source_id=source_id,
                line=getattr(keyword.value, "lineno", node.lineno),
                via="callable_kwarg")
        self._emit_callable_string_ref(node, source_id=source_id)
    # ── traversal ──────────────────────────────────────────────────────────

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        nid = self._emit_function(node)
        self._func_stack.append(nid)
        self.generic_visit(node)
        self._func_stack.pop()

    visit_AsyncFunctionDef = visit_FunctionDef

    def visit_ClassDef(self, node: ast.ClassDef) -> None:
        nid = self._emit_class(node)
        self._class_stack.append(node.name)
        self._class_stack_lineno.append(node.lineno)
        self.generic_visit(node)
        self._class_stack.pop()
        self._class_stack_lineno.pop()

    def visit_Import(self, node: ast.Import) -> None:
        self._emit_imports(node)

    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
        self._emit_imports(node)

    def visit_Call(self, node: ast.Call) -> None:
        self._emit_call(node)
        self.generic_visit(node)

    def visit_Assign(self, node: ast.Assign) -> None:
        # module-level variables become variable nodes
        if not self._func_stack and not self._class_stack:
            for tgt in node.targets:
                if isinstance(tgt, ast.Name) and tgt.id == "__all__":
                    # record exported names so is_exported reaches the nodes
                    if isinstance(node.value, (ast.List, ast.Tuple, ast.Set)):
                        for el in node.value.elts:
                            if isinstance(el, ast.Constant) and isinstance(el.value, str):
                                self._exported.add(el.value)
                if isinstance(tgt, ast.Name):
                    qname = _qualified_name(self.module, tgt.id)
                    nid = _node_id(self.file_path, "variable", qname, node.lineno)
                    self.nodes.append({
                        "id": nid, "kind": "variable", "name": tgt.id,
                        "qualified_name": qname, "file_path": self.file_path,
                        "language": "python", "start_line": node.lineno,
                        "end_line": getattr(node, "end_lineno", node.lineno),
                        "visibility": _visibility(tgt.id),
                    })
                    value = node.value
                    if isinstance(value, ast.Call):
                        self._emit_callable_ref_from_expr(
                            value.func, source_id=nid, line=getattr(value.func, "lineno", node.lineno),
                            via="callable_assign_call")
                    else:
                        self._emit_callable_ref_from_expr(
                            value, source_id=nid, line=getattr(value, "lineno", node.lineno),
                            via="callable_assign")
                    self._local_defs[tgt.id] = nid
        self.generic_visit(node)


def _module_name(file_path: str, project_root: str | None = None) -> str:
    """Derive a dotted module name from file path relative to project root."""
    fp = os.path.normpath(file_path).replace(os.sep, "/")
    if project_root:
        root = os.path.normpath(project_root).replace(os.sep, "/")
        if fp.startswith(root):
            rel = fp[len(root):].lstrip("/")
        else:
            rel = os.path.basename(fp)
    else:
        rel = os.path.basename(fp)
    if rel.endswith(".py"):
        rel = rel[:-3]
    if rel.endswith("/__init__"):
        rel = rel[:-len("/__init__")]
    return rel.replace("/", ".")


def extract(source: str, *, file_path: str, project_root: str | None = None,
            use_tree_sitter: Optional[bool] = None) -> dict:
    """Extract nodes + edges from Python source.

    Returns: {"nodes": [...], "edges": [...], "errors": [...], "module": str,
              "source_hash": str, "extractor": "ast" | "tree_sitter"}.
    ast is P0; tree-sitter used only if CBM_USE_TREE_SITTER=1 AND available.
    """
    source_hash = hashlib.sha256(source.encode("utf-8")).hexdigest()
    module = _module_name(file_path, project_root)
    errors: list[str] = []

    # tree-sitter optional path (A5). Falls back to ast on any failure.
    if use_tree_sitter is None:
        use_tree_sitter = CBM_USE_TREE_SITTER
    if use_tree_sitter:
        try:
            return _extract_tree_sitter(source, file_path=file_path, module=module,
                                        source_hash=source_hash)
        except Exception as e:
            errors.append(f"tree_sitter_fallback: {type(e).__name__}: {e}")

    # ast P0 path
    try:
        tree = ast.parse(source, filename=file_path)
    except SyntaxError as e:
        return {"nodes": [], "edges": [], "errors": [f"syntax_error: {e}"],
                "module": module, "source_hash": source_hash, "extractor": "ast"}

    ex = _Extractor(file_path=file_path, module=module, source_hash=source_hash)
    ex._emit_module_node()
    # pre-seed top-level defs so forward references (a caller defined above its
    # callee) resolve to real node ids instead of degrading to ext: targets
    for child in tree.body:
        if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
            ex._local_defs[child.name] = _node_id(
                file_path, "function", _qualified_name(module, child.name), child.lineno)
        elif isinstance(child, ast.ClassDef):
            ex._local_defs[child.name] = _node_id(
                file_path, "class", _qualified_name(module, child.name), child.lineno)
    ex.visit(tree)
    # apply module __all__ exports: mark function/method/class nodes whose name
    # appears in __all__ as is_exported=1 (BUG-2 root cause: _exported was
    # populated in visit_Assign but never reached node dicts because FunctionDef
    # is visited before the __all__ assignment).
    if ex._exported:
        for n in ex.nodes:
            if n.get("name") in ex._exported and n.get("kind") in ("function", "method", "class"):
                n["is_exported"] = 1
    # dedupe edges: identical (source, target, kind, line) rows collapse to one
    # (repeated same-name calls on one line produced dup rows — dup-edge fix)
    seen: set[tuple] = set()
    edges = []
    for e in ex.edges:
        key = (e["source"], e["target"], e["kind"], e.get("line"))
        if key in seen:
            continue
        seen.add(key)
        edges.append(e)
    return {
        "nodes": ex.nodes, "edges": edges, "errors": errors,
        "module": module, "source_hash": source_hash, "extractor": "ast",
    }


def _extract_tree_sitter(source: str, *, file_path: str, module: str,
                         source_hash: str) -> dict:
    """Optional tree-sitter path — NOT implemented (A5 deferred).

    Always raises ImportError, even when tree_sitter IS installed, so extract()
    falls back to the ast path; the import probe only enriches the error message.
    """
    try:
        import tree_sitter  # noqa: F401
        import tree_sitter_python  # noqa: F401
    except ImportError as e:
        raise ImportError(f"tree_sitter unavailable: {e}")
    # Full tree-sitter extraction is deferred (A5). Force ast fallback for now.
    raise ImportError("tree_sitter extraction not implemented (A5 deferred)")


__all__ = ["extract"]
