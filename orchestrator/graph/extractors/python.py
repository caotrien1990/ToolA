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
import keyword
import os
from typing import Optional

from ..config import (
    CBM_EXACT, CBM_UNIQUE, CBM_SAME_MODULE, CBM_SUFFIX_LOW, CBM_SUFFIX_HIGH,
    CBM_LABEL_TO_NUMERIC, CBM_USE_TREE_SITTER, CBM_NAME_LITERAL_MIN_LEN,
)

# W5: words that are identifier-shaped but can never name a callable, so a
# string literal spelling one of them is prose, not a dispatch key.
_NAME_LITERAL_STOPWORDS = frozenset(keyword.kwlist) | frozenset(
    getattr(keyword, "softkwlist", ()))

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
        # W4: the names each enclosing function scope BINDS locally (params,
        # assignments, imports, comprehension vars). A bare `foo` inside a
        # function is only a reference to the module-level `def foo` when the
        # function does not rebind `foo` itself. Measured on [A]: of 318 bare
        # references to a name this file defines, 190 -- the majority -- are a
        # local variable that merely shares the name, so binding without this
        # check would create more wrong edges than right ones.
        self._scope_binds: list[set[str]] = []
        # W4: the nested def/class each enclosing function scope declares, and
        # the names it binds for any OTHER reason. `_scope_binds` alone cannot
        # tell `def guard(): ...` apart from `guard = 3`, so it blocked every
        # reference to a closure -- including the `return guard` that is the
        # whole point of `build_guard`. Measured on [A]: 903 such references,
        # and `restart_control_service.guard` read as dead while the function
        # that returns it is the restart gate the daemon calls on every cycle.
        #
        # A separate stack rather than a wider `_local_defs`: 147 names in 61
        # files are defined more than once in the same file (`_run` x4 in
        # code_tools.py), so a flat name map would bind a closure reference to
        # whichever same-named def was registered last. Scope is exactly the
        # information that disambiguates them.
        self._scope_defs: list[dict[str, str]] = []
        self._scope_nondef_binds: list[set[str]] = []
        # names listed in module __all__ (marks is_exported post-visit)
        self._exported: set[str] = set()
        # W6: id() of the string Constant nodes that ARE those __all__ entries.
        # `__all__ = ["public"]` is a declaration that the name may be imported;
        # it is not a call site, and it is not a dispatch registry. Harvesting it
        # as a `name_literal` made the indexer resolve it back to the `def` three
        # lines below and emit a `dynamic_ref` -- so every `__all__` export read
        # ALIVE on the strength of its own export line, with no caller anywhere.
        # Measured on [A]: 8 callables were alive only through a same-file
        # literal, 1 of them purely through its own __all__ entry, and the flaw
        # also made `unexported_only` unobservable in Python (its 220 real
        # exclusions were all TypeScript, where `export` leaves no literal).
        # Populated from the same walk that fills `_exported`, so the two cannot
        # disagree about which elements those are.
        self._export_literal_ids: set[int] = set()
        # W5: string literals in this file that spell an identifier. A registry
        # that dispatches by name ("search_code" -> the function) leaves no call
        # edge, so the callable reads as dead. The extractor sees one file at a
        # time and cannot know which names are defined elsewhere, so it records
        # the literal as a `name_literal` NODE and leaves resolution to the
        # indexer, which owns the cross-file view — the same split already used
        # for `import` nodes -> `imports_module` edges.
        # Maps name -> first line seen (one node per name per file).
        self._string_names: dict[str, int] = {}
        # ast.Name nodes already handled as part of a Call (its `func`, or an
        # argument `_emit_call` passed to _emit_callable_ref_from_expr), so
        # visit_Name does not emit a second edge for the same reference.
        self._consumed_as_call_operand: set[int] = set()

    def _nested_def_ids(self, fn: ast.AST) -> dict:
        """{name: node id} for every def/class declared DIRECTLY in this scope.

        Directly means same scope, not same subtree: a def inside `if` or `try`
        still belongs here, a def inside a nested function does not -- the outer
        scope cannot refer to it, so recording it would invent an edge.

        The ids are computed the way `_emit_function`/`_emit_class` will compute
        them, class stack included: a closure inside a method is emitted as a
        `method` node qualified by the enclosing class.
        """
        out: dict[str, str] = {}
        prefix = self._class_stack[-1] if self._class_stack else None
        kind = "method" if self._class_stack else "function"

        def walk(body) -> None:
            for st in body:
                if isinstance(st, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    out[st.name] = _node_id(
                        self.file_path, kind,
                        _qualified_name(self.module, prefix, st.name), st.lineno)
                elif isinstance(st, ast.ClassDef):
                    out[st.name] = _node_id(
                        self.file_path, "class",
                        _qualified_name(self.module, st.name), st.lineno)
                else:
                    # Descend through the statement, NOT into a def: the two
                    # branches above register a def and stop there, so a def
                    # written under `if`/`try`/`with` is reached and one nested
                    # inside another function is not.
                    for sub in ast.iter_child_nodes(st):
                        walk([sub])
        walk(fn.body)
        return out

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
        # W5/G3: an import inside a function body is DEFERRED — it runs at call
        # time, not at module load. That distinction is the whole difference
        # between a cycle that crashes on import and one that is only technical
        # debt, so it is recorded at extraction time rather than re-derived by
        # every consumer from line ranges.
        deferred = bool(self._func_stack)
        edge_meta = {"deferred": deferred,
                     "scope": "deferred" if deferred else "top_level"}
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
                    "line": node.lineno, "metadata": dict(edge_meta),
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
                    "line": node.lineno, "metadata": dict(edge_meta),
                })
                self._local_defs[alias.asname or alias.name] = nid

    # -- calls ----------------------------------------------------------------

    def _scope_callable_target(self, name: str) -> str | None:
        """Node id of the enclosing-scope def `name` refers to, or None.

        Walks scopes innermost-outward and STOPS at the first one that binds the
        name for a non-def reason: in `def f(): guard = 3; return guard` the
        assignment is what `guard` means, and an edge to some `def guard`
        elsewhere would be a wrong edge -- worse than a missing one, because it
        makes dead code look alive forever.
        """
        for defs, nondef in zip(reversed(self._scope_defs),
                                reversed(self._scope_nondef_binds), strict=True):
            if name in nondef:
                return None
            nid = defs.get(name)
            if nid:
                return nid
        return None

    def _callable_target_for_name(self, name: str) -> str | None:
        # A closure wins over a module-level def of the same name: it is what
        # the code actually refers to at that point.
        scoped = self._scope_callable_target(name)
        if scoped and (":function:" in scoped or ":method:" in scoped):
            return scoped
        target = self._local_defs.get(name)
        if target and (":function:" in target or ":method:" in target):
            return target
        return None

    def _receiver_import_node(self, recv: ast.AST) -> str | None:
        """The `import` node id a receiver Name is bound to, or None.

        `_session_logs_module._log_app_session(...)` and `plain.attr(...)` are
        indistinguishable to a suffix match on the bare attribute -- and the
        suffix match wins, binding the call to whatever local definition happens
        to share the name. That is how `code_tools._log_app_session`, a wrapper
        whose entire body is the forwarded call, ended up calling ITSELF while
        `logs._log_app_session` read as dead with zero callers.

        The receiver is the one piece of evidence that separates the two, and
        this file can see it: an `import` line bound the name. What that import
        actually resolves to is a cross-file question, so the id is recorded on
        the edge and `indexer._resolve_imported_calls` follows it.
        """
        if not isinstance(recv, ast.Name):
            return None
        nid = self._local_defs.get(recv.id)
        return nid if nid and ":import:" in nid else None

    def _receiver_is_shadowed(self, recv: ast.AST) -> bool:
        """True when an enclosing function rebinds the receiver name locally.

        `os = _FakeOs()` inside a test body makes the module-level `import os`
        irrelevant, so the recorded receiver would name the wrong module. The
        suffix match stays suppressed either way -- knowing less about the
        receiver is never a reason to guess harder -- but no receiver is
        recorded on the edge, and the resolver leaves it external.
        """
        return isinstance(recv, ast.Name) and any(recv.id in b for b in self._scope_binds)

    def _callable_target_for_attribute(self, attr: str, recv: ast.AST | None = None) -> str | None:
        """Local definition a `<recv>.<attr>` VALUE reference points at.

        Returns None when the receiver is an imported name: the same
        receiver-blindness described in _receiver_import_node applies here, and
        it produced six self-loops on [A] -- `_query.cbm_trace_path` bound to
        `tools.cbm_trace_path`, the wrapper, instead of the function in the
        module `_query` names. The caller emits a cross-file placeholder for
        those instead, so the resolver can bind them through the right module.
        """
        if recv is not None and self._receiver_import_node(recv) is not None:
            return None
        matched = [nid for n, nid in self._local_defs.items()
                   if n == attr or n.endswith("." + attr)]
        return next((nid for nid in matched
                     if ":function:" in nid or ":method:" in nid), None)

    def _emit_ext_call_edge(self, *, source_id: str, attr: str, recv: ast.AST,
                            line: int, extra: dict | None = None) -> None:
        """Unresolved `<recv>.<attr>(...)` placeholder, carrying the receiver.

        `recv_import` is the id of the `import` node the receiver name is bound
        to. It is the whole reason this edge can be resolved correctly later:
        the resolver looks at THAT module rather than at every module the file
        imports, which is what bound `os.walk` to `tools_v15.walk` and
        `subprocess.run` to `ExecutorLane.run` -- measured on [A], 62 such
        edges, every one of them wrong.
        """
        meta = dict(extra or {})
        recv_import = self._receiver_import_node(recv)
        if recv_import is not None and not self._receiver_is_shadowed(recv):
            meta["recv_import"] = recv_import
        edge = {
            "source": source_id, "target": f"ext:{attr}", "kind": "calls",
            "confidence": CBM_SUFFIX_LOW, "cbm_label": _label_for(CBM_SUFFIX_LOW),
            "line": line, "is_external": 1,
        }
        if meta:
            edge["metadata"] = meta
        self.edges.append(edge)

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
            target = self._callable_target_for_attribute(expr.attr, expr.value)
            if target is None and self._receiver_import_node(expr.value) is not None:
                # `asyncio.to_thread(_query.nodes_query, ...)` -- a real
                # reference to a callable in another file. Emitting the
                # placeholder is what lets the cross-file resolver bind it;
                # without it the reference is simply lost and the callee reads
                # as dead.
                self._emit_ext_call_edge(
                    source_id=source_id, attr=expr.attr, recv=expr.value,
                    line=line, extra={"callable_ref": via})
                return
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

    def _call_source_id(self) -> str:
        """The node that OWNS a call, whatever depth it was written at.

        A call written outside any function used to be dropped outright -- the
        source stack was empty, so no `calls` edge was emitted and the callee
        read as dead. That is not an edge case: `raise SystemExit(main())`, a
        registry built by a module-level `_build()`, a decorator applied in a
        class body all execute at import, which is the STRONGEST evidence of
        life there is. Measured on [A]: 171 module-level calls in orchestrator/
        alone were invisible, including all 5 `main()` entry points, every one
        of which the dead-code detector then reported as dead.

        The module and class nodes already exist and are the honest owners, so
        a call binds to the innermost enclosing definition:
        function -> class -> module.
        """
        if self._func_stack:
            return self._func_stack[-1]
        if self._class_stack:
            return _node_id(self.file_path, "class",
                            _qualified_name(self.module, self._class_stack[-1]),
                            self._class_stack_lineno[-1])
        return _node_id(self.file_path, "module", self.module, 1)

    def _emit_call(self, node: ast.Call) -> None:
        source_id = self._call_source_id()
        func = node.func
        if isinstance(func, ast.Name):
            callee = func.id
            # A nested def is the innermost meaning of the name, and `_local_defs`
            # is flat: `_run()` inside one helper of code_tools.py would otherwise
            # bind to whichever of that file's four `_run` definitions was
            # registered last.
            local = self._scope_callable_target(callee) or self._local_defs.get(callee)
            if local:
                self.edges.append({
                    "source": source_id, "target": local,
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
            # suffix match against local defs (e.g. store.get_db -> get_db),
            # but ONLY when the receiver is not a name this file imported --
            # see _receiver_import_node for what that match did to re-export
            # wrappers. An imported receiver names a module this file cannot
            # read, so the honest answer is a placeholder plus the receiver.
            if self._receiver_import_node(func.value) is not None:
                matched = []
            else:
                matched = [nid for n, nid in self._local_defs.items() if n == attr or n.endswith("." + attr)]
            if matched:
                self.edges.append({
                    "source": source_id, "target": matched[0], "kind": "calls",
                    "confidence": CBM_SUFFIX_HIGH, "cbm_label": _label_for(CBM_SUFFIX_HIGH),
                    "line": node.lineno,
                })
            else:
                self._emit_ext_call_edge(source_id=source_id, attr=attr,
                                         recv=func.value, line=node.lineno)

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
        bound, nondef = _scope_bound_names(node)
        self._scope_binds.append(bound)
        self._scope_nondef_binds.append(nondef)
        # Pre-seeded, not filled as the visitor reaches each def: `return guard`
        # is written BELOW `def guard`, but `helper()` called at the top of a
        # function whose `def helper` sits at the bottom is just as common, and
        # source order is not what decides whether a callable is alive.
        self._scope_defs.append(self._nested_def_ids(node))
        self.generic_visit(node)
        self._scope_defs.pop()
        self._scope_nondef_binds.pop()
        self._scope_binds.pop()
        self._func_stack.pop()

    visit_AsyncFunctionDef = visit_FunctionDef

    def visit_ClassDef(self, node: ast.ClassDef) -> None:
        self._emit_class(node)
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
        self._consumed_as_call_operand.add(id(node.func))
        for a in node.args:
            self._consumed_as_call_operand.add(id(a))
        for k in node.keywords:
            self._consumed_as_call_operand.add(id(k.value))
        self.generic_visit(node)

    def visit_Name(self, node: ast.Name) -> None:
        """A callable used as a VALUE is a reference, not a call.

        `skip_dir = skip_dir or _default_skip_dir`, `HANDLERS = {"a": handle_a}`,
        `run_in_executor(_query.nodes_query, ...)`, `def f(fn=_default)` -- every
        one of these keeps the callable alive, and none of them is an ast.Call
        with that callable as `func`, so `_emit_call` never sees it and the
        callee ends with zero inbound edges.

        _emit_callable_ref_from_expr already existed for exactly this, but was
        wired only to call arguments/keywords and to module-level assignment
        RHS. That covered the argument shape and missed the rest. Measured on
        [A]: `search_snapshots._default_skip_dir` and
        `reaper._default_kill_process_tree` sat on the dead list with their one
        real reference being `x = x or _default_...` -- the fallback-default
        idiom, which is a reference the graph could not see.

        Binding on ast.Name only, never ast.Attribute: `.status`, `.alive`,
        `.run` are ordinary attribute reads on unrelated objects, and the
        attribute resolver matches on the bare suffix, so widening to them
        would invent 20 edges of which most would be wrong. A wrong call edge
        is worse than a missing one -- it makes dead code look alive forever.
        """
        if (isinstance(node.ctx, ast.Load)
                and id(node) not in self._consumed_as_call_operand
                and (self._scope_callable_target(node.id) is not None
                     or not any(node.id in b for b in self._scope_binds))):
            self._emit_callable_ref_from_expr(
                node, source_id=self._call_source_id(),
                line=node.lineno, via="callable_value")
        self.generic_visit(node)

    def visit_Constant(self, node: ast.Constant) -> None:
        # W5: harvest identifier-shaped string literals. A dispatch registry
        # ("search_code" -> the function) is invisible to the call graph, so
        # every tool it routes to reads as dead code. Recorded as a node here
        # and resolved cross-file by the indexer -- this file cannot know which
        # names are defined elsewhere.
        # W6: an `__all__` entry is an export declaration, not a reference. See
        # `_export_literal_ids`. Skipped BEFORE setdefault on purpose: when the
        # same name also appears in a real registry further down the file, the
        # node now lands on that line instead of on the export list.
        if id(node) in self._export_literal_ids:
            return
        value = node.value
        if (isinstance(value, str)
                and value.isidentifier()
                and value not in _NAME_LITERAL_STOPWORDS
                and ("_" in value or len(value) >= CBM_NAME_LITERAL_MIN_LEN)):
            self._string_names.setdefault(value, node.lineno)

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
                                self._export_literal_ids.add(id(el))
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
                        # visit_Name would otherwise emit a second edge for the
                        # same reference, owned by the module instead of the
                        # variable this assignment creates.
                        self._consumed_as_call_operand.add(id(value))
                    self._local_defs[tgt.id] = nid
        self.generic_visit(node)


def _scope_bound_names(fn: ast.AST) -> tuple:
    """(all names this function scope binds, names it binds for a NON-def reason).

    Used to decide whether a bare `foo` inside a function refers to the
    module-level `def foo` or to a local that merely shares the name. Nested
    functions are included on purpose -- their bindings are not visible to the
    outer scope, but a name bound in EITHER is not a safe reference to the
    module-level definition, and over-refusing costs a missing edge while
    over-binding costs a wrong one.

    The second set is what makes a closure referencable. `def guard(): ...` and
    `guard = 3` both bind `guard`, and treating them alike blocked every
    reference to a nested def, so `return guard` emitted nothing at all and the
    closure read as dead with zero callers. Only the non-def bindings are
    evidence that the name means something other than the def; a name that is
    BOTH (a def later reassigned) stays in this set and stays blocked.
    """
    bound: set = set()
    defs: set = set()
    other: set = set()
    for x in ast.walk(fn):
        if isinstance(x, ast.Name) and isinstance(x.ctx, (ast.Store, ast.Del)):
            other.add(x.id)
        elif isinstance(x, ast.arg):
            other.add(x.arg)
        elif isinstance(x, ast.alias):
            other.add((x.asname or x.name).split(".")[0])
        elif isinstance(x, ast.ExceptHandler) and x.name:
            other.add(x.name)
        elif isinstance(x, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)) and x is not fn:
            defs.add(x.name)
    bound = defs | other
    return bound, other


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
    # pre-seed defs so forward references (a caller defined above its callee)
    # resolve to real node ids instead of degrading to ext: targets.
    #
    # METHODS are seeded too, not just top-level defs. `_Extractor` registers a
    # method in `_local_defs` when it VISITS it, so a `self._helper()` written
    # above `def _helper` found nothing and emitted `ext:_helper` -- the callee
    # then read as dead purely because of the order the two were written in.
    # Measured on [A]: 302 `self.<method>()` calls to a method of the same
    # class, 20 of them forward references, which is why five private methods
    # of `DurableAuditWriter` sat on the dead list while `_initialize_schema_once`
    # calls four of them on consecutive lines.
    #
    # Seeding a method into the same flat name map the visitor already writes to
    # adds no new ambiguity: it makes resolution independent of source order
    # rather than widening what a name can bind to.
    for child in tree.body:
        if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
            ex._local_defs[child.name] = _node_id(
                file_path, "function", _qualified_name(module, child.name), child.lineno)
        elif isinstance(child, ast.ClassDef):
            ex._local_defs[child.name] = _node_id(
                file_path, "class", _qualified_name(module, child.name), child.lineno)
            for m in child.body:
                if isinstance(m, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    ex._local_defs.setdefault(m.name, _node_id(
                        file_path, "method",
                        _qualified_name(module, f"{child.name}.{m.name}"), m.lineno))
    ex.visit(tree)
    # apply module __all__ exports: mark function/method/class nodes whose name
    # appears in __all__ as is_exported=1 (BUG-2 root cause: _exported was
    # populated in visit_Assign but never reached node dicts because FunctionDef
    # is visited before the __all__ assignment).
    if ex._exported:
        for n in ex.nodes:
            if n.get("name") in ex._exported and n.get("kind") in ("function", "method", "class"):
                n["is_exported"] = 1
    # W5: one `name_literal` node per identifier-shaped string literal seen in
    # this file. It is deliberately a NODE and not an edge: the target of a
    # name-based dispatch usually lives in another file, and an extractor only
    # ever sees one file. The indexer joins these against every function/method
    # definition of the same name and materialises `dynamic_ref` edges — the
    # exact split already used by `import` nodes -> `imports_module` edges.
    for name, lineno in ex._string_names.items():
        ex.nodes.append({
            "id": _node_id(file_path, "name_literal", name, lineno),
            "kind": "name_literal", "name": name,
            "qualified_name": name, "file_path": file_path,
            "language": "python", "start_line": lineno, "end_line": lineno,
            "visibility": _visibility(name),
        })
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


def _extract_tree_sitter(source: str, *, file_path: str,  # noqa: ARG001
                         module: str, source_hash: str) -> dict:  # noqa: ARG001
    """Optional tree-sitter path — NOT implemented (A5 deferred).

    Always raises ImportError, even when tree_sitter IS installed, so extract()
    falls back to the ast path; the import probe only enriches the error message.
    """
    try:
        import tree_sitter  # noqa: F401
        import tree_sitter_python  # noqa: F401
    except ImportError as e:
        raise ImportError(f"tree_sitter unavailable: {e}") from e
    # Full tree-sitter extraction is deferred (A5). Force ast fallback for now.
    raise ImportError("tree_sitter extraction not implemented (A5 deferred)")


__all__ = ["extract"]
