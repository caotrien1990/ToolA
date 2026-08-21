"""JS/TS symbol/edge extractor (Phase 6 Step 23 prototype, opt-in).

tree-sitter based, using the official grammar wheels tree-sitter-javascript /
tree-sitter-typescript (both MIT). Mirrors the python extractor node/edge
shape and the CBM confidence scale (A8). Opt-in via CBM_EXTRACT_JS=1; any
grammar/import/parse failure degrades to an empty result with errors[] so
indexing never crashes on JS/TS files (per-language rollback = disable flag).
"""
import hashlib
import os
import posixpath

from ..config import (
    CBM_EXACT, CBM_UNIQUE, CBM_SAME_MODULE, CBM_SUFFIX_LOW, CBM_SUFFIX_HIGH,
    CBM_LABEL_TO_NUMERIC,
)

_NUMERIC_TO_LABEL = {v: k for k, v in CBM_LABEL_TO_NUMERIC.items()}

_JS_EXTS = (".js", ".jsx", ".mjs", ".cjs")
_TS_EXTS = (".ts",)
_TSX_EXTS = (".tsx",)
SUPPORTED_EXTS = _JS_EXTS + _TS_EXTS + _TSX_EXTS

_parsers: dict = {}  # grammar key -> Parser (lazy, cached per process)


def _label_for(confidence: float) -> str:
    return _NUMERIC_TO_LABEL.get(round(confidence, 2), "unknown")


def _node_id(file_path: str, kind: str, name: str, line: int) -> str:
    return f"{file_path}:{kind}:{name}:{line}"


def _qualified_name(module: str, *parts: str) -> str:
    return ".".join([module] + [p for p in parts if p])


def _grammar_key(ext: str) -> str:
    if ext in _TSX_EXTS:
        return "tsx"
    if ext in _TS_EXTS:
        return "typescript"
    return "javascript"


def _language_name(ext: str) -> str:
    return "typescript" if ext in (_TS_EXTS + _TSX_EXTS) else "javascript"


def _get_parser(ext: str):
    key = _grammar_key(ext)
    if key in _parsers:
        return _parsers[key]
    from tree_sitter import Language, Parser
    if key == "javascript":
        import tree_sitter_javascript as _g
        lang = Language(_g.language())
    elif key == "typescript":
        import tree_sitter_typescript as _g
        lang = Language(_g.language_typescript())
    else:
        import tree_sitter_typescript as _g
        lang = Language(_g.language_tsx())
    parser = Parser(lang)
    _parsers[key] = parser
    return parser


def _module_name(file_path: str, project_root: str | None = None) -> str:
    fp = os.path.normpath(file_path).replace(os.sep, "/")
    if project_root:
        root = os.path.normpath(project_root).replace(os.sep, "/")
        rel = fp[len(root):].lstrip("/") if fp.startswith(root) else os.path.basename(fp)
    else:
        rel = os.path.basename(fp)
    for ext in SUPPORTED_EXTS:
        if rel.endswith(ext):
            rel = rel[: -len(ext)]
            break
    if rel.endswith("/index"):
        rel = rel[: -len("/index")]
    return rel.replace("/", ".")


class _JsWalker:
    """Walks a tree-sitter CST, accumulating nodes + edges for one file."""

    def __init__(self, *, src: bytes, file_path: str, module: str, language: str):
        self.src = src
        self.file_path = file_path
        self.module = module
        self.language = language
        self.nodes: list[dict] = []
        self.edges: list[dict] = []
        self._local_defs: dict[str, str] = {}
        self._class_stack: list[str] = []
        self._class_stack_line: list[int] = []
        self._func_stack: list[str] = []
        # names exported via export-clause / default-identifier (post-visit mark)
        self._exported: set = set()
        # require() calls already turned into import nodes by the declarator
        # branch, so the generic call_expression branch does not record them twice
        self._consumed_requires: set[int] = set()

    # -- helpers --------------------------------------------------------------

    def _text(self, node) -> str:
        return self.src[node.start_byte:node.end_byte].decode("utf-8", "replace")

    def _line(self, node) -> int:
        return node.start_point[0] + 1

    def _end_line(self, node) -> int:
        return node.end_point[0] + 1

    def _module_id(self) -> str:
        return _node_id(self.file_path, "module", self.module, 1)

    def _visibility(self, name: str) -> str:
        return "private" if name.startswith("_") or name.startswith("#") else "public"

    # -- emitters -------------------------------------------------------------

    def emit_module_node(self) -> None:
        base = os.path.basename(self.file_path)
        self.nodes.append({
            "id": self._module_id(), "kind": "module",
            "name": base.rsplit(".", 1)[0], "qualified_name": self.module,
            "file_path": self.file_path, "language": self.language,
            "start_line": 1, "end_line": 1, "visibility": "public",
        })

    def _emit_function(self, name: str, node, *, exported: bool, params: str = "") -> str:
        kind = "method" if self._class_stack else "function"
        prefix = self._class_stack[-1] if self._class_stack else None
        qname = _qualified_name(self.module, prefix, name)
        nid = _node_id(self.file_path, kind, qname, self._line(node))
        entry = {
            "id": nid, "kind": kind, "name": name, "qualified_name": qname,
            "file_path": self.file_path, "language": self.language,
            "start_line": self._line(node), "end_line": self._end_line(node),
            "signature": f"{name}({params})",
            "visibility": self._visibility(name),
        }
        if exported:
            entry["is_exported"] = 1
        self.nodes.append(entry)
        self._local_defs[name] = nid
        if self._class_stack:
            cls_id = _node_id(
                self.file_path, "class",
                _qualified_name(self.module, self._class_stack[-1]),
                self._class_stack_line[-1])
            self.edges.append({
                "source": cls_id, "target": nid, "kind": "defines",
                "confidence": CBM_EXACT, "cbm_label": _label_for(CBM_EXACT),
                "line": self._line(node),
            })
        return nid

    def _emit_class(self, name: str, node, *, exported: bool) -> str:
        qname = _qualified_name(self.module, name)
        nid = _node_id(self.file_path, "class", qname, self._line(node))
        entry = {
            "id": nid, "kind": "class", "name": name, "qualified_name": qname,
            "file_path": self.file_path, "language": self.language,
            "start_line": self._line(node), "end_line": self._end_line(node),
            "visibility": self._visibility(name),
        }
        if exported:
            entry["is_exported"] = 1
        self.nodes.append(entry)
        self._local_defs[name] = nid
        heritage = next((c for c in node.children if c.type == "class_heritage"), None)
        if heritage is not None:
            base = self._text(heritage)
            if base.startswith("extends"):
                base = base[len("extends"):]
            base_key = base.strip().split("<", 1)[0].strip()  # strip generics
            simple = base_key.split(".")[-1]
            cand = (self._local_defs.get(base_key), self._local_defs.get(simple))
            target = next((c for c in cand
                           if c and (":class:" in c or ":import:" in c)), None)
            if target:
                self.edges.append({
                    "source": nid, "target": target, "kind": "inherits",
                    "confidence": CBM_SAME_MODULE, "cbm_label": _label_for(CBM_SAME_MODULE),
                    "line": self._line(node),
                })
            else:
                self.edges.append({
                    "source": nid, "target": f"ext:{base_key}", "kind": "inherits",
                    "confidence": CBM_SUFFIX_LOW, "cbm_label": _label_for(CBM_SUFFIX_LOW),
                    "line": self._line(node), "is_external": 1,
                })
        return nid

    def _module_key(self, specifier: str) -> str:
        """Dotted module key for an import specifier, as the indexer keys modules.

        The raw specifier was used verbatim, so `./lib/util.js` became the
        qualified_name `./lib/util.js.helper`, whose dotted prefixes are
        `/lib/util.js.helper`, `/lib/util.js`, `/lib/util` -- and the indexer
        keys modules by `_dotted_from_path('lib/util.js')` == `lib.util`. The two
        could never meet, so every ESM project resolved to zero dependency edges
        no matter how many imports it had.

        This is the only place the relative forms CAN be resolved: it needs the
        importing file's directory, which the indexer no longer has when it walks
        import nodes. Bare package specifiers ('fs', 'react') are returned
        unchanged -- they are external and must stay unresolvable rather than
        collide with a same-named local file.
        """
        spec = (specifier or "").replace("\\", "/")
        if not spec.startswith("."):
            return spec.strip("/").replace("/", ".")
        base = os.path.dirname(self.file_path.replace("\\", "/"))
        joined = posixpath.normpath(posixpath.join(base, spec))
        joined = joined.replace("\\", "/").lstrip("./").strip("/")
        for ext in SUPPORTED_EXTS:
            if joined.endswith(ext):
                joined = joined[: -len(ext)]
                break
        if joined.endswith("/index"):
            joined = joined[: -len("/index")]
        return joined.replace("/", ".")

    def _emit_import(self, node) -> None:
        source_node = node.child_by_field_name("source")
        src_mod = ""
        if source_node is not None:
            src_mod = self._module_key(self._text(source_node).strip("'\"`"))
        names: list[str] = []
        clause = next((c for c in node.children if c.type == "import_clause"), None)
        if clause is not None:
            for c in clause.children:
                if c.type == "identifier":                      # default import
                    names.append(self._text(c))
                elif c.type == "named_imports":
                    for spec in c.children:
                        if spec.type == "import_specifier":
                            alias = spec.child_by_field_name("alias")
                            name_f = spec.child_by_field_name("name")
                            pick = alias if alias is not None else name_f
                            if pick is not None:
                                names.append(self._text(pick))
                elif c.type == "namespace_import":              # * as ns
                    ident = next((g for g in c.children if g.type == "identifier"), None)
                    if ident is not None:
                        names.append(self._text(ident))
        self._add_import_nodes(src_mod, names, node)

    def _require_specifier(self, node) -> str | None:
        """The specifier of a `require('...')` call, else None.

        `require()` is a call_expression, so it never reached _emit_import and a
        CommonJS project produced NO import nodes at all -- only an `ext:require`
        call edge. That is why dependency_edges was 0 on a 41-file repo.
        """
        if node is None or node.type != "call_expression":
            return None
        fn = node.child_by_field_name("function")
        if fn is None or fn.type != "identifier" or self._text(fn) != "require":
            return None
        args = node.child_by_field_name("arguments")
        if args is None:
            return None
        for a in args.children:
            if a.type in ("string", "template_string"):
                raw = self._text(a).strip("'\"`")
                return raw or None
        return None  # require(variable): specifier not statically known

    def _emit_require(self, call_node, *, bound: list[str]) -> bool:
        """Record a require() as an import node, like its ESM equivalent."""
        spec = self._require_specifier(call_node)
        if spec is None:
            return False
        self._consumed_requires.add(id(call_node))
        self._add_import_nodes(self._module_key(spec), list(bound), call_node)
        return True

    def _add_import_nodes(self, src_mod: str, names: list[str], node) -> None:
        if not names:
            names = [src_mod]  # side-effect import: import './x' / require('./x')
        for name in names:
            if not name:
                continue
            qname = f"{src_mod}.{name}" if name != src_mod else src_mod
            nid = _node_id(self.file_path, "import", qname, self._line(node))
            self.nodes.append({
                "id": nid, "kind": "import", "name": name, "qualified_name": qname,
                "file_path": self.file_path, "language": self.language,
                "start_line": self._line(node), "end_line": self._line(node),
                "visibility": "public",
            })
            self.edges.append({
                "source": self._module_id(), "target": nid, "kind": "imports",
                "confidence": CBM_EXACT, "cbm_label": _label_for(CBM_EXACT),
                "line": self._line(node),
            })
            self._local_defs[name] = nid
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

    def _emit_callable_ref_from_node(self, node, *, source_id: str, via: str) -> None:
        if node is None:
            return
        if node.type == "identifier":
            target = self._callable_target_for_name(self._text(node))
            if target:
                self._emit_callable_ref_edge(
                    source_id=source_id, target_id=target, line=self._line(node), via=via)
            return
        if node.type == "member_expression":
            prop = node.child_by_field_name("property")
            if prop is not None:
                target = self._callable_target_for_attribute(self._text(prop))
                if target:
                    self._emit_callable_ref_edge(
                        source_id=source_id, target_id=target, line=self._line(prop), via=via)
        for child in node.children:
            self._emit_callable_ref_from_node(child, source_id=source_id, via=via)

    def _emit_call(self, node) -> None:
        if not self._func_stack:
            return
        source_id = self._func_stack[-1]
        fn = node.child_by_field_name("function")
        if fn is None:
            return
        if fn.type == "identifier":
            callee = self._text(fn)
            if callee in self._local_defs:
                self.edges.append({
                    "source": source_id, "target": self._local_defs[callee],
                    "kind": "calls", "confidence": CBM_UNIQUE,
                    "cbm_label": _label_for(CBM_UNIQUE), "line": self._line(node),
                })
            else:
                self.edges.append({
                    "source": source_id, "target": f"ext:{callee}", "kind": "calls",
                    "confidence": CBM_SUFFIX_LOW, "cbm_label": _label_for(CBM_SUFFIX_LOW),
                    "line": self._line(node), "is_external": 1,
                })
        elif fn.type == "member_expression":
            prop = fn.child_by_field_name("property")
            if prop is None:
                return
            attr = self._text(prop)
            matched = [nid for n, nid in self._local_defs.items()
                       if n == attr or n.endswith("." + attr)]
            if matched:
                self.edges.append({
                    "source": source_id, "target": matched[0], "kind": "calls",
                    "confidence": CBM_SUFFIX_HIGH, "cbm_label": _label_for(CBM_SUFFIX_HIGH),
                    "line": self._line(node),
                })
            else:
                self.edges.append({
                    "source": source_id, "target": f"ext:{attr}", "kind": "calls",
                    "confidence": CBM_SUFFIX_LOW, "cbm_label": _label_for(CBM_SUFFIX_LOW),
                    "line": self._line(node), "is_external": 1,
                })

        args = node.child_by_field_name("arguments")
        if args is not None:
            for child in args.children:
                if child.type in ("(", ")", ","):
                    continue
                self._emit_callable_ref_from_node(
                    child, source_id=source_id, via="callable_arg")

    def _emit_variable(self, name: str, node, *, exported: bool) -> str:
        qname = _qualified_name(self.module, name)
        nid = _node_id(self.file_path, "variable", qname, self._line(node))
        entry = {
            "id": nid, "kind": "variable", "name": name, "qualified_name": qname,
            "file_path": self.file_path, "language": self.language,
            "start_line": self._line(node), "end_line": self._end_line(node),
            "visibility": self._visibility(name),
        }
        if exported:
            entry["is_exported"] = 1
        self.nodes.append(entry)
        self._local_defs[name] = nid
        return nid

    # -- traversal ------------------------------------------------------------

    def _params_text(self, node) -> str:
        p = node.child_by_field_name("parameters")
        if p is None:
            return ""
        return self._text(p).strip("()")

    def walk(self, node, *, exported: bool = False) -> None:
        t = node.type
        if t in ("function_declaration", "generator_function_declaration"):
            name_n = node.child_by_field_name("name")
            name = self._text(name_n) if name_n is not None else "default"
            nid = self._emit_function(name, node, exported=exported,
                                      params=self._params_text(node))
            self._func_stack.append(nid)
            body = node.child_by_field_name("body")
            if body is not None:
                self.walk(body)
            self._func_stack.pop()
            return
        if t == "class_declaration":
            name_n = node.child_by_field_name("name")
            name = self._text(name_n) if name_n is not None else "default"
            self._emit_class(name, node, exported=exported)
            self._class_stack.append(name)
            self._class_stack_line.append(self._line(node))
            body = node.child_by_field_name("body")
            if body is not None:
                self.walk(body)
            self._class_stack.pop()
            self._class_stack_line.pop()
            return
        if t == "method_definition":
            name_n = node.child_by_field_name("name")
            name = self._text(name_n) if name_n is not None else "anon"
            nid = self._emit_function(name, node, exported=False,
                                      params=self._params_text(node))
            self._func_stack.append(nid)
            body = node.child_by_field_name("body")
            if body is not None:
                self.walk(body)
            self._func_stack.pop()
            return
        if t == "import_statement":
            self._emit_import(node)
            return
        if t == "export_statement":
            decl = node.child_by_field_name("declaration")
            if decl is not None:
                self.walk(decl, exported=True)
                return
            value = node.child_by_field_name("value")
            if value is not None:
                if value.type == "identifier":
                    # export default myThing; -> mark the referenced def
                    self._exported.add(self._text(value))
                elif value.type in ("arrow_function", "function_expression",
                                    "function", "generator_function"):
                    # export default () => {} -> anonymous default function
                    nid = self._emit_function("default", node, exported=True,
                                              params=self._params_text(value))
                    self._func_stack.append(nid)
                    body = value.child_by_field_name("body")
                    if body is not None:
                        self.walk(body)
                    self._func_stack.pop()
                return
            clause = next((c for c in node.children
                           if c.type == "export_clause"), None)
            if clause is not None:
                # export { a, b as c } [from './x'] -> mark local + alias names
                # (STT-4 fix): also add the alias `c` so callers importing `c`
                # find the graph-side record; the local def `b` is still marked.
                for spec in clause.children:
                    if spec.type == "export_specifier":
                        name_f = spec.child_by_field_name("name")
                        if name_f is not None:
                            self._exported.add(self._text(name_f))
                        alias_f = spec.child_by_field_name("alias")
                        if alias_f is not None:
                            self._exported.add(self._text(alias_f))
            return
        # STT-29: all export_statement paths return above (lines 345/361/376) —
        # no fallthrough to the generic child-walk at 408, so no double-processing.
        # Known gap: `export * from './x'` matches none of the 3 branches and
        # returns at 376 with no node/edge — re-export source silently dropped
        # (backlog: add export_specifier-from handling if needed).
        if t in ("lexical_declaration", "variable_declaration"):
            for d in node.children:
                if d.type != "variable_declarator":
                    continue
                name_n = d.child_by_field_name("name")
                value = d.child_by_field_name("value")
                # `const {a, b} = require('./x')` -- the destructured names are
                # exactly the imported bindings, so this is the one destructuring
                # shape worth reading: without it the later call to `a()` stays an
                # `ext:` edge and the cross-file call is lost.
                if (name_n is not None and name_n.type == "object_pattern"
                        and self._require_specifier(value) is not None):
                    picks: list[str] = []
                    for el in name_n.children:
                        if el.type == "shorthand_property_identifier_pattern":
                            picks.append(self._text(el))
                        elif el.type == "pair_pattern":
                            alias = el.child_by_field_name("value")
                            if alias is not None and alias.type == "identifier":
                                picks.append(self._text(alias))
                    self._emit_require(value, bound=picks)
                    continue
                if name_n is None or name_n.type != "identifier":
                    continue  # other destructuring patterns: out of prototype scope
                name = self._text(name_n)
                if value is not None and self._require_specifier(value) is not None:
                    # `const util = require('./x')` -- default-style binding.
                    self._emit_require(value, bound=[name])
                    continue
                if value is not None and value.type in (
                        "arrow_function", "function_expression", "function",
                        "generator_function"):
                    # (STT-9 fix): emit the function-value node (not the declarator)
                    # so start_line/end_line span the function body, not `const x =`.
                    nid = self._emit_function(name, value, exported=exported,
                                              params=self._params_text(value))
                    self._func_stack.append(nid)
                    body = value.child_by_field_name("body")
                    if body is not None:
                        self.walk(body)
                    self._func_stack.pop()
                elif not self._func_stack and not self._class_stack:
                    nid = self._emit_variable(name, d, exported=exported)
                    if value is not None:
                        if value.type == "call_expression":
                            fn = value.child_by_field_name("function")
                            self._emit_callable_ref_from_node(
                                fn, source_id=nid, via="callable_assign_call")
                        else:
                            self._emit_callable_ref_from_node(
                                value, source_id=nid, via="callable_assign")
                        self.walk(value)
                else:
                    if value is not None:
                        self.walk(value)
            return
        if t == "call_expression":
            # A bare `require('./x');` (side-effect import) reaches here with no
            # declarator above it. Already-consumed requires are skipped so a
            # bound require is not recorded twice.
            if id(node) not in self._consumed_requires:
                if not self._emit_require(node, bound=[]):
                    self._emit_call(node)
            # fallthrough: keep walking arguments for nested calls
        for child in node.children:
            self.walk(child)


def extract(source: str, *, file_path: str, project_root: str | None = None) -> dict:
    """Extract nodes + edges from JS/TS source (tree-sitter, fail-open).

    Returns the same shape as extractors.python.extract:
    {"nodes", "edges", "errors", "module", "source_hash", "extractor"}.
    Any grammar/import/parse failure returns an empty result with errors[]
    instead of raising - unsupported/broken files degrade gracefully.
    """
    source_hash = hashlib.sha256(source.encode("utf-8")).hexdigest()
    module = _module_name(file_path, project_root)
    ext = os.path.splitext(file_path)[1].lower()
    empty = {"nodes": [], "edges": [], "errors": [], "module": module,
             "source_hash": source_hash, "extractor": "tree_sitter_js"}
    if ext not in SUPPORTED_EXTS:
        empty["errors"].append(f"unsupported_extension: {ext}")
        return empty
    try:
        parser = _get_parser(ext)
    except Exception as e:
        empty["errors"].append(f"grammar_unavailable: {type(e).__name__}: {e}")
        return empty
    try:
        src = source.encode("utf-8")
        tree = parser.parse(src)
        walker = _JsWalker(src=src, file_path=file_path, module=module,
                           language=_language_name(ext))
        walker.emit_module_node()
        # pre-seed top-level defs so forward references resolve (mirror python)
        for child in tree.root_node.children:
            target = child
            if child.type == "export_statement":
                d = child.child_by_field_name("declaration")
                if d is None:
                    continue
                target = d
            if target.type in ("function_declaration",
                               "generator_function_declaration"):
                n = target.child_by_field_name("name")
                if n is not None:
                    name = src[n.start_byte:n.end_byte].decode("utf-8", "replace")
                    walker._local_defs[name] = _node_id(
                        file_path, "function", _qualified_name(module, name),
                        target.start_point[0] + 1)
            elif target.type == "class_declaration":
                n = target.child_by_field_name("name")
                if n is not None:
                    name = src[n.start_byte:n.end_byte].decode("utf-8", "replace")
                    walker._local_defs[name] = _node_id(
                        file_path, "class", _qualified_name(module, name),
                        target.start_point[0] + 1)
        walker.walk(tree.root_node)
        # export { a, b } / export default <ident> are seen after the defs they
        # reference — mark them post-visit (mirror of python __all__ handling)
        if walker._exported:
            for n in walker.nodes:
                if (n.get("name") in walker._exported and
                        n.get("kind") in ("function", "method", "class", "variable")):
                    n["is_exported"] = 1
        errors = []
        if tree.root_node.has_error:
            errors.append("parse_error: tree contains ERROR nodes (partial extract)")
        # dedupe edges (same rule as python extractor)
        seen: set = set()
        edges = []
        for e in walker.edges:
            key = (e["source"], e["target"], e["kind"], e.get("line"))
            if key in seen:
                continue
            seen.add(key)
            edges.append(e)
        return {"nodes": walker.nodes, "edges": edges, "errors": errors,
                "module": module, "source_hash": source_hash,
                "extractor": "tree_sitter_js"}
    except Exception as e:
        empty["errors"].append(f"extract_failed: {type(e).__name__}: {e}")
        return empty


__all__ = ["extract", "SUPPORTED_EXTS"]
