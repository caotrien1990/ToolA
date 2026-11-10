"""No-window spawn census (flicker-fix campaign, design draft v4 section 7).

WHAT THIS TEST IS
    A structural recurrence guard: every console spawn the ToolA daemon can
    make from scanned source must carry a hidden-window mechanism -- either
    a recognised ``creationflags`` form or the equivalent verified mechanism
    (a ``startupinfo`` traced to ``child_startupinfo()``).  A future
    unflagged spawn of any statically resolvable shape turns the suite red
    at commit time.

WALK BASIS (pinned by the campaign plan)
    The census enumerates **git-tracked files only** (the file set from
    ``git ls-files`` in this repo).  Git-ignored trees (``venv312/``,
    ``.orchestrator_cache/``, ``.tmp/``, scratch expectations) are never
    walked, so the partition assert below runs over the tracked set and
    cannot trip on ignored scratch.  Within the tracked ``.py`` set the
    scanned subset is: every ``.py`` under ``orchestrator/**``, plus
    ``bin/*.py``, plus repo-root ``*.py`` excluding root-level ``test_*.py``
    (the test-file exclusion takes precedence at the root).  Excluded by
    named class only: ``tests/``, ``test_*.py`` anywhere outside
    ``orchestrator/``, ``scripts/``, ``harness/``, ``docs/``, ``examples/``,
    ``hooks/``, ``.council/``, scratch/worktree trees
    (``test-usagelog-patch-20261105/``, any ``wt_*`` directory),
    ``__pycache__``.  The test asserts the tracked ``.py`` set partitions
    EXACTLY into scanned + excluded-by-named-class; a new top-level
    directory fails the partition assert and forces a human scope decision
    (the anti-rot mechanism for scope itself -- no silent skips).

ENUMERATION (per scanned file, by import resolution, not by name list)
    Per-file subprocess bindings are resolved from imports at ANY nesting
    level (function-local imports included): ``import subprocess`` under
    any alias, module re-export access actually used in this repo
    (``ct.subprocess`` where ``ct`` is the file's code_tools import), and
    ``from subprocess import run/Popen/call/check_call/check_output``.
    A spawn site is:
      (a) a direct call to a resolved run-family member;
      (b) a carrier site -- any call receiving a bare reference (not a
          call) to a resolved run-family member as an argument, where the
          spawn's kwargs ride on the carrier call (the carrier set is
          definitional; live forms: ``asyncio.to_thread(...)``,
          ``loop.run_in_executor(...)``, ``run_in_executor_lane(...)``,
          ``functools.partial(...)``);
      (c) a bound-callable site -- a name or attribute itself bound to a
          resolved run-family member (by assignment, by default parameter
          value, or by attribute store, e.g. ``self.runner = runner``
          where the parameter's default is ``subprocess.run``); calls
          through it are spawn sites.  Span: a binding stored on, or
          consumed through, an attribute spans the enclosing CLASS; a
          binding held in a plain local name spans its enclosing FUNCTION
          only.  A construction in scanned scope that passes an explicit
          argument rebinding the parameter is followed the same way (the
          argument's value governs for that instance).  The kwargs tracing
          for an invocation is performed in the method or function
          containing the invocation.

RECOGNITION (a site is FLAGGED iff a hidden-window mechanism traces to it)
    Primary mechanism ``creationflags``: recognised forms are expressions
    referencing ``CREATE_NO_WINDOW``, ``NOWINDOW_FLAGS``, or
    ``child_creationflags(`` -- where referencing ``CREATE_NO_WINDOW``
    explicitly includes the string-literal getattr form
    ``getattr(<binding>, "CREATE_NO_WINDOW", 0)``.
    Equivalent mechanism: a ``startupinfo`` value in the same invocation
    kwargs that traces to a ``child_startupinfo()`` call also makes the
    site FLAGGED.  That is the ONLY recognised startupinfo form: an
    arbitrary or hand-built STARTUPINFO does NOT count.
    The trace resolves through:
      (i)   the literal kwarg expression at the call / carrier call /
            bound-callable invocation;
      (ii)  variable tracing -- a variable passed as ``creationflags=<var>``
            whose assignments within the enclosing function (plain
            assignment, ``|=`` chains, or conditional/ternary expressions)
            include a recognised form;
      (iii) dict splat -- a ``**<dict>`` argument where the enclosing
            function assigns ``<dict>["creationflags"]`` a recognised form
            (any dict variable name; the assignment may be conditional),
            OR where the splatted value is a dict expression itself (a
            dict literal, or a conditional expression selecting between
            dict literals) whose ``"creationflags"`` key maps to a
            recognised form, OR where the splatted value is a variable
            whose traced construction in the enclosing function is a dict
            literal carrying a ``"creationflags"`` entry.  In every dict
            form, the ``"creationflags"`` entry's value may itself be a
            variable, provided that variable traces to a recognised form
            under rule (ii) in the same enclosing function.  Rules (ii)
            and (iii) apply identically to the ``"startupinfo"`` entry for
            the equivalent mechanism.

TRACE SEMANTICS
    Tracing is existence-based over control paths, with removal dominance.
    A recognised form reaches an invocation iff at least one control path
    from an assignment of that form to the invocation contains no removal
    of that form.  A removal is: ``pop("<key>")`` or ``del <dict>["<key>"]``
    on the traced dict, for that key; or reassignment of the traced
    variable to an expression carrying no recognised form.  Augmented
    accumulation (``|=``) is NOT a removal, and an alternative branch
    assigning a different form does not remove a form assigned on another
    branch.  Conditional assignments count on the paths where they execute.

FLAG ANTAGONISM (2026-11-09 pick-folder flash)
    A recognised creationflags form is NEGATED when a DETACHED_PROCESS
    form traces to the same invocation's creationflags value (the same
    variable trace, or the same ``creationflags`` entry of a splatted
    dict).  DETACHED_PROCESS wins console allocation at the spawned
    hop, so the intermediary is born with no console at all and the
    console application it launches allocates a fresh VISIBLE console:
    the hidden-window form is present in the source yet does not hold
    at runtime.  A negated site classifies UNFLAGGED.  DETACHED_PROCESS
    on its own -- no recognised form on the same value -- is untouched
    by this rule and stays governed by the allowlist (the deliberate
    restart-helper exception).  The runtime half of this guard is
    test_background_spawn_flags_carry_no_detached at the bottom of
    this file.

EXPLICITNESS LINES (campaign plan rule 9)
    (i)  Rule (iii) applies at bound-callable invocations exactly as at
         direct calls.
    (ii) Worked example of the bare-variable entry rule:
         ``flags = child_creationflags()`` ... ``{"creationflags": flags}``
         traces FLAGGED; ``flags = 0`` ... ``{"creationflags": flags}``
         traces UNFLAGGED.

EXTENSION POINT / ACCEPTED RESIDUAL (draft section 7, OL-8)
    Shapes that defeat static, per-file import resolution evade this
    census until it is extended: subprocess loaded via importlib/eval,
    string-built attribute access (``getattr(subprocess, "ru"+"n")``),
    spawn helpers in files outside the scanned scope, run-family callables
    whose binding is invisible from the file that invokes them (assembled
    across module boundaries so neither the binding nor the invocation
    file alone shows the link), and flag dictionaries assembled in one
    method and splatted in another method of the same file through an
    attribute store (intra-file cross-method dict flow -- rules (ii)/(iii)
    trace within the enclosing function only, so such a site fails this
    census in the safe direction, as unflagged, until the trace is
    extended; no live shape exercises it at the pin).

KNOWN FAIL-SAFE RESIDUAL FORMS (named explicitly, per plan rule 9)
    Removal forms outside the closed removal list above are NOT treated
    as removals: ``dict.clear()``, overwriting ``dict.update(...)``, and
    alias-mediated pop (``alias = d; alias.pop("creationflags")``).
    Non-traced dict forms are NOT traced: ``dict.update(...)`` carrying
    the entry, a ``{**base}`` display whose entry lives only in ``base``,
    and entry assignment inside a same-file helper function.

ALLOWLIST
    Exactly 3 entries, keyed by (file, enclosing qualname, reason) and
    matched on the (file, qualname) pair plus the run-family member the
    entry's reason names (the line number is recorded for humans, not
    used for matching).  The member is part of the key because one
    qualname can host two enumerated sites: ``restart_orchestrator_via_task``
    hosts both the DETACHED Popen exception (:7514) and an ordinary
    unflagged ``schtasks /query`` run (:7463, a fix site) -- the entry
    covers the Popen it names, not every site in the function.
    Rot control: an allowlist entry matching no live enumerated site
    FAILS this test.

ENUMERATION DUMP
    Whenever the env var ``CENSUS_DUMP`` is set, the full per-site dump
    (file, line, enclosing qualname, enumeration pass, classification
    FLAGGED / UNFLAGGED / ALLOWLISTED; one row per enumerated invocation
    site, duplicates retained) is written to a dump file -- the value of
    ``CENSUS_DUMP`` when it names a path, otherwise ``census_dump.txt``
    next to the test output (the process working directory) -- and the
    dump path is printed on stdout.
"""

from __future__ import annotations

import ast
import json
import os
import subprocess
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent

RUN_FAMILY = frozenset({"run", "Popen", "call", "check_call", "check_output"})
FLAG_NAMES = frozenset({"CREATE_NO_WINDOW", "NOWINDOW_FLAGS"})

# (file, enclosing qualname) -> {"reason": ..., "member": ...}; the key is
# (file, qualname, reason) and a site matches an entry iff file, qualname
# and the run-family member named in the reason all agree (see header).
ALLOWLIST = {
    ("tools_v15.py", "restart_orchestrator_via_bat"): {
        "reason": "warp Popen (:7906, deliberate STARTUPINFO design)",
        "member": "Popen",
    },
}

_EXCLUDED_TOP_DIRS = frozenset(
    {"tests", "scripts", "harness", "docs", "examples", "hooks", ".council",
     # experiments/: offline upgrade-pilot scripts (CLOSING_PACKAGE_20261109
     # F-3) -- batch_pilot.py is an in-process read dispatcher and
     # measure_batch.py replays captured payloads in-process; neither
     # spawns a process (both files read in full at classification time).
     "experiments"}
)


# --------------------------------------------------------------------------
# Scan scope (partition over the git-tracked .py set)
# --------------------------------------------------------------------------

def tracked_py_files() -> list[str]:
    out = subprocess.run(
        ["git", "ls-files"],
        cwd=str(REPO_ROOT), capture_output=True, text=True, check=True,
    ).stdout
    return sorted(p for p in out.splitlines() if p.endswith(".py"))


def classify_rel(rel: str) -> str | None:
    """Return 'scanned', an 'excluded:<class>' label, or None (unclassified)."""
    parts = rel.split("/")
    base = parts[-1]
    if parts[0] == "orchestrator":
        return "scanned"
    if len(parts) == 2 and parts[0] == "bin":
        return "scanned"
    if len(parts) == 1 and not base.startswith("test_"):
        return "scanned"
    if parts[0] in _EXCLUDED_TOP_DIRS:
        return "excluded:dir:" + parts[0]
    if "__pycache__" in parts:
        return "excluded:pycache"
    if parts[0].startswith("wt_") or parts[0] == "test-usagelog-patch-20261105":
        return "excluded:scratch"
    if base.startswith("test_"):
        return "excluded:test-file"
    return None


# --------------------------------------------------------------------------
# Recognised-form predicates
# --------------------------------------------------------------------------

def _walk_pruned(node: ast.AST):
    """Walk a subtree without descending into nested scopes."""
    stack = [node]
    while stack:
        cur = stack.pop()
        yield cur
        for child in ast.iter_child_nodes(cur):
            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef,
                                  ast.ClassDef, ast.Lambda)):
                continue
            stack.append(child)


def expr_has_flag_form(expr: ast.AST) -> bool:
    for node in _walk_pruned(expr):
        if isinstance(node, ast.Name) and node.id in FLAG_NAMES:
            return True
        if isinstance(node, ast.Attribute) and node.attr in FLAG_NAMES:
            return True
        if isinstance(node, ast.Constant) and node.value == "CREATE_NO_WINDOW":
            return True
        if isinstance(node, ast.Call):
            func = node.func
            if isinstance(func, ast.Name) and func.id == "child_creationflags":
                return True
            if isinstance(func, ast.Attribute) and func.attr == "child_creationflags":
                return True
    return False


def expr_has_detached_form(expr: ast.AST) -> bool:
    for node in _walk_pruned(expr):
        if isinstance(node, ast.Name) and node.id == "DETACHED_PROCESS":
            return True
        if isinstance(node, ast.Attribute) \
                and node.attr == "DETACHED_PROCESS":
            return True
        if isinstance(node, ast.Constant) \
                and node.value == "DETACHED_PROCESS":
            return True
    return False


def expr_has_startupinfo_form(expr: ast.AST) -> bool:
    for node in _walk_pruned(expr):
        if isinstance(node, ast.Call):
            func = node.func
            if isinstance(func, ast.Name) and func.id == "child_startupinfo":
                return True
            if isinstance(func, ast.Attribute) and func.attr == "child_startupinfo":
                return True
    return False


def expr_recognised(expr: ast.AST, mech: str) -> bool:
    if mech == "cf":
        return expr_has_flag_form(expr)
    if mech == "det":
        return expr_has_detached_form(expr)
    return expr_has_startupinfo_form(expr)


# --------------------------------------------------------------------------
# Per-function control-flow trace (existence over paths, removal dominance)
# --------------------------------------------------------------------------

_EXIT = -1


class FuncTrace:
    """A statement-level CFG for one function (or the module body).

    Nodes are statements.  Edges model sequence, if/else, try/except/
    finally (with conservative exception edges from every try-body
    statement to every handler), loops (with a back edge), with, and
    match.  Nested function/class bodies are separate scopes and are not
    entered.
    """

    def __init__(self, body: list[ast.stmt]):
        self.edges: dict[int, set[int]] = {_EXIT: set()}
        self.stmt_of: dict[int, int] = {}
        self.nodes: list[ast.stmt] = []
        self._events_cache: dict = {}
        self.build_block(body, _EXIT)

    def register(self, stmt: ast.stmt) -> int:
        nid = len(self.nodes)
        self.nodes.append(stmt)
        self.stmt_of[id(stmt)] = nid
        return nid

    def build_block(self, stmts, succ):
        entry = succ
        for st in reversed(list(stmts)):
            entry = self.build_stmt(st, entry)
        return entry

    def _block_stmts(self, stmts):
        """All statement nodes inside a block, pruning nested scopes."""
        found = []
        stack = list(stmts)
        while stack:
            cur = stack.pop()
            if isinstance(cur, ast.stmt):
                found.append(cur)
            for child in ast.iter_child_nodes(cur):
                if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef,
                                      ast.ClassDef, ast.Lambda)):
                    continue
                stack.append(child)
        return found

    def build_stmt(self, st, succ):
        nid = self.register(st)
        if isinstance(st, ast.If):
            body_e = self.build_block(st.body, succ)
            else_e = self.build_block(st.orelse, succ) if st.orelse else succ
            self.edges[nid] = {body_e, else_e}
        elif isinstance(st, (ast.For, ast.AsyncFor)):
            body_e = self.build_block(st.body, nid)
            else_e = self.build_block(st.orelse, succ) if st.orelse else succ
            self.edges[nid] = {body_e, else_e, succ}
        elif isinstance(st, ast.While):
            body_e = self.build_block(st.body, nid)
            else_e = self.build_block(st.orelse, succ) if st.orelse else succ
            self.edges[nid] = {body_e, else_e, succ}
        elif isinstance(st, (ast.With, ast.AsyncWith)):
            self.edges[nid] = {self.build_block(st.body, succ)}
        elif isinstance(st, (ast.Try, ast.TryStar)):
            fin_e = self.build_block(st.finalbody, succ) if st.finalbody else succ
            else_e = self.build_block(st.orelse, fin_e) if st.orelse else fin_e
            handler_es = [self.build_block(h.body, fin_e) for h in st.handlers]
            body_e = self.build_block(st.body, else_e)
            self.edges[nid] = {body_e, *handler_es}
            if handler_es:
                for inner in self._block_stmts(st.body):
                    inid = self.stmt_of.get(id(inner))
                    if inid is not None and inid != nid:
                        self.edges.setdefault(inid, set()).update(handler_es)
        elif isinstance(st, ast.Match):
            case_es = [self.build_block(case.body, succ) for case in st.cases]
            self.edges[nid] = {*case_es, succ}
        elif isinstance(st, (ast.Return, ast.Raise)):
            self.edges[nid] = set()
        else:
            self.edges[nid] = {succ}
        return nid

    # -- statement containment -------------------------------------------

    def statement_of(self, node: ast.AST, parent: dict) -> int | None:
        cur: ast.AST | None = node
        while cur is not None:
            nid = self.stmt_of.get(id(cur))
            if nid is not None:
                return nid
            cur = parent.get(id(cur))
        return None

    # -- event extraction --------------------------------------------------

    def _own_parts(self, stmt: ast.stmt):
        """The expression/assignment parts a statement node itself owns.

        Body statements of compound statements are separate CFG nodes, so
        for compound statements only the header parts belong to this node.
        """
        if isinstance(stmt, (ast.If, ast.While)):
            return [stmt.test]
        if isinstance(stmt, (ast.For, ast.AsyncFor)):
            return [stmt.iter]
        if isinstance(stmt, (ast.With, ast.AsyncWith)):
            return [item.context_expr for item in stmt.items]
        if isinstance(stmt, ast.Match):
            return [stmt.subject]
        if isinstance(stmt, (ast.Try, ast.TryStar)):
            return []
        if isinstance(stmt, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            return []
        return [stmt]

    def events_for(self, target, entry_value_ok):
        """Return (sources, removals) node-id sets for one trace target.

        target is ('var', name, mech) or ('dict', dict_name, key, mech).
        entry_value_ok(expr, mech, at_nid) decides whether a dict entry's
        value expression counts as recognised at that statement (it
        resolves the value-through-variable form via the var trace).
        """
        if target in self._events_cache:
            return self._events_cache[target]
        sources: set[int] = set()
        removals: set[int] = set()
        kind = target[0]
        for nid, stmt in enumerate(self.nodes):
            parts = self._own_parts(stmt)
            if kind == "var":
                _, name, mech = target
                for part in parts:
                    self._var_events(part, name, mech, nid, sources, removals)
            else:
                _, dname, key, mech = target
                for part in parts:
                    self._dict_events(part, dname, key, mech, nid,
                                      sources, removals, entry_value_ok)
        self._events_cache[target] = (sources, removals)
        return sources, removals

    def _var_events(self, part, name, mech, nid, sources, removals):
        if isinstance(part, ast.Assign):
            hit = False
            for tgt in part.targets:
                if isinstance(tgt, ast.Name) and tgt.id == name:
                    hit = True
                elif isinstance(tgt, (ast.Tuple, ast.List)) and any(
                        isinstance(e, ast.Name) and e.id == name for e in tgt.elts):
                    hit = True
            if hit:
                if expr_recognised(part.value, mech):
                    sources.add(nid)
                else:
                    removals.add(nid)
            return
        if isinstance(part, ast.AnnAssign) and isinstance(part.target, ast.Name) \
                and part.target.id == name and part.value is not None:
            if expr_recognised(part.value, mech):
                sources.add(nid)
            else:
                removals.add(nid)
            return
        if isinstance(part, ast.AugAssign) and isinstance(part.target, ast.Name) \
                and part.target.id == name:
            if expr_recognised(part.value, mech):
                sources.add(nid)  # |= is never a removal
            return
        if isinstance(part, (ast.For, ast.AsyncFor)):
            if any(isinstance(e, ast.Name) and e.id == name
                   for e in ast.walk(part.target)):
                removals.add(nid)
            return
        if isinstance(part, ast.Delete):
            for tgt in part.targets:
                if isinstance(tgt, ast.Name) and tgt.id == name:
                    removals.add(nid)

    def _dict_events(self, part, dname, key, mech, nid,
                     sources, removals, entry_value_ok):
        if isinstance(part, ast.Assign):
            for tgt in part.targets:
                if isinstance(tgt, ast.Name) and tgt.id == dname:
                    if self._construction_has_entry(part.value, key, mech,
                                                    nid, entry_value_ok):
                        sources.add(nid)
                    else:
                        removals.add(nid)
                elif isinstance(tgt, ast.Subscript) \
                        and isinstance(tgt.value, ast.Name) \
                        and tgt.value.id == dname \
                        and isinstance(tgt.slice, ast.Constant) \
                        and tgt.slice.value == key:
                    if entry_value_ok(part.value, mech, nid):
                        sources.add(nid)
                    else:
                        removals.add(nid)
            return
        if isinstance(part, ast.AnnAssign) and isinstance(part.target, ast.Name) \
                and part.target.id == dname and part.value is not None:
            if self._construction_has_entry(part.value, key, mech,
                                            nid, entry_value_ok):
                sources.add(nid)
            else:
                removals.add(nid)
            return
        if isinstance(part, ast.Delete):
            for tgt in part.targets:
                if isinstance(tgt, ast.Subscript) \
                        and isinstance(tgt.value, ast.Name) \
                        and tgt.value.id == dname \
                        and isinstance(tgt.slice, ast.Constant) \
                        and tgt.slice.value == key:
                    removals.add(nid)
            return
        # pop("<key>") on the traced dict, anywhere in this statement's
        # own expressions (alias-mediated pop is a documented residual and
        # is intentionally not matched: the receiver must be the dict name).
        for node in _walk_pruned(part):
            if isinstance(node, ast.Call) \
                    and isinstance(node.func, ast.Attribute) \
                    and node.func.attr == "pop" \
                    and isinstance(node.func.value, ast.Name) \
                    and node.func.value.id == dname \
                    and node.args \
                    and isinstance(node.args[0], ast.Constant) \
                    and node.args[0].value == key:
                removals.add(nid)

    def _construction_has_entry(self, value, key, mech, nid, entry_value_ok):
        dicts = []
        if isinstance(value, ast.Dict):
            dicts = [value]
        elif isinstance(value, ast.IfExp):
            dicts = [n for n in _walk_pruned(value) if isinstance(n, ast.Dict)]
        for d in dicts:
            for k, v in zip(d.keys, d.values):
                if isinstance(k, ast.Constant) and k.value == key:
                    if entry_value_ok(v, mech, nid):
                        return True
        return False

    # -- reachability --------------------------------------------------------

    def reaches(self, sources, removals, target_nid) -> bool:
        if target_nid is None:
            return False
        seen: set[int] = set()
        stack = list(sources)
        while stack:
            cur = stack.pop()
            if cur == target_nid:
                return True
            if cur in seen:
                continue
            seen.add(cur)
            for nxt in self.edges.get(cur, set()):
                if nxt in removals and nxt != target_nid:
                    continue
                if nxt not in seen:
                    stack.append(nxt)
        return target_nid in seen


# --------------------------------------------------------------------------
# Per-file analysis
# --------------------------------------------------------------------------

def _param_pairs(funcdef):
    """(arg, default) pairs for a function def, defaults not None."""
    args = funcdef.args
    positional = list(args.posonlyargs) + list(args.args)
    defaults = [None] * (len(positional) - len(args.defaults)) \
        + list(args.defaults)
    pairs = [(a, d) for a, d in zip(positional, defaults) if d is not None]
    pairs += [(a, d) for a, d in zip(args.kwonlyargs, args.kw_defaults)
              if d is not None]
    return pairs


def own_qn_of(analysis, node) -> str:
    """Recompute a def node's own qualname from the parent chain."""
    parts = []
    cur: ast.AST | None = node
    while cur is not None and not isinstance(cur, ast.Module):
        if isinstance(cur, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            parts.append(cur.name)
        cur = analysis.parent.get(id(cur))
    return ".".join(reversed(parts))


class FileAnalysis:
    def __init__(self, rel: str):
        self.rel = rel
        path = REPO_ROOT / rel
        source = path.read_text(encoding="utf-8", errors="replace")
        self.tree = ast.parse(source, filename=rel)
        self.parent: dict[int, ast.AST] = {}
        for node in ast.walk(self.tree):
            for child in ast.iter_child_nodes(node):
                self.parent[id(child)] = node
        self.module_bindings: dict[str, str] = {}
        self.from_bindings: dict[str, str] = {}
        self._collect_imports()
        self._traces: dict[int, FuncTrace] = {}
        self.class_attr_bindings: dict[tuple[str, str], dict] = {}
        self.func_local_bindings: dict[tuple[str, str], str | None] = {}
        self._collect_bound_callables()
        self.sites: list[dict] = []
        self._enumerate()

    # -- imports -----------------------------------------------------------

    def _collect_imports(self):
        for node in ast.walk(self.tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    if alias.asname:
                        self.module_bindings[alias.asname] = alias.name
                    else:
                        top = alias.name.split(".")[0]
                        self.module_bindings[top] = top
            elif isinstance(node, ast.ImportFrom):
                module = node.module or ""
                for alias in node.names:
                    bound = alias.asname or alias.name
                    if alias.name == "subprocess" and (
                            module == "code_tools" or module.endswith(".code_tools")):
                        self.module_bindings[bound] = "code_tools.subprocess"
                    elif alias.name == "code_tools":
                        self.module_bindings[bound] = "code_tools"
                    elif module in ("subprocess", "code_tools.subprocess") \
                            and alias.name in RUN_FAMILY:
                        self.from_bindings[bound] = alias.name

    def resolve(self, expr: ast.AST) -> str | None:
        if isinstance(expr, ast.Name):
            if expr.id in self.module_bindings:
                return self.module_bindings[expr.id]
            if expr.id in self.from_bindings:
                return "subprocess." + self.from_bindings[expr.id]
            return None
        if isinstance(expr, ast.Attribute):
            base = self.resolve(expr.value)
            if base is not None:
                return base + "." + expr.attr
        return None

    def is_run_family_ref(self, expr: ast.AST) -> bool:
        return self.ref_member(expr) is not None

    def ref_member(self, expr: ast.AST) -> str | None:
        resolved = self.resolve(expr)
        if resolved is None:
            return None
        for prefix in ("subprocess.", "code_tools.subprocess."):
            if resolved.startswith(prefix) \
                    and resolved[len(prefix):] in RUN_FAMILY:
                return resolved[len(prefix):]
        return None

    # -- scoped walk ---------------------------------------------------------

    def _scoped_walk(self, node, cls_qn=None, func_node=None, func_qn=None):
        """Yield (node, cls_qn, func_node, func_qn) for every node."""
        yield node, cls_qn, func_node, func_qn
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            # Decorators / defaults / annotations live in the outer scope.
            outer_parts = list(node.decorator_list) + list(node.args.defaults) \
                + [d for d in node.args.kw_defaults if d is not None]
            for part in outer_parts:
                yield from self._scoped_walk(part, cls_qn, func_node, func_qn)
            qual = node.name if func_qn is None else func_qn + "." + node.name
            if func_qn is None and cls_qn is not None:
                qual = cls_qn + "." + node.name
            for stmt in node.body:
                yield from self._scoped_walk(stmt, cls_qn, node, qual)
            return
        if isinstance(node, ast.ClassDef):
            for part in list(node.decorator_list) + list(node.bases) \
                    + [k.value for k in node.keywords]:
                yield from self._scoped_walk(part, cls_qn, func_node, func_qn)
            qual = node.name if func_qn is None else func_qn + "." + node.name
            for stmt in node.body:
                yield from self._scoped_walk(stmt, qual, func_node, func_qn)
            return
        for child in ast.iter_child_nodes(node):
            yield from self._scoped_walk(child, cls_qn, func_node, func_qn)

    # -- bound callables (pass c) --------------------------------------------

    def _collect_bound_callables(self):
        param_defaults: list[tuple[str, str, str | None, ast.FunctionDef]] = []
        # (func_qn, param_name, enclosing class qualname, func node)
        for node, cls_qn, _fn, _fq in self._scoped_walk(self.tree):
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            own_qn = own_qn_of(self, node)
            for arg, default in _param_pairs(node):
                if self.is_run_family_ref(default):
                    self.func_local_bindings[(own_qn, arg.arg)] = \
                        self.ref_member(default)
                    param_defaults.append((own_qn, arg.arg, cls_qn, node))
            # Plain assignments and attribute stores in this function's own
            # statements (nested scopes pruned).
            for stmt in node.body:
                for sub in _walk_pruned(stmt):
                    if isinstance(sub, ast.Assign):
                        for tgt in sub.targets:
                            if isinstance(tgt, ast.Name) \
                                    and self.is_run_family_ref(sub.value):
                                self.func_local_bindings[(own_qn, tgt.id)] = \
                                    self.ref_member(sub.value)
                            elif isinstance(tgt, ast.Attribute) \
                                    and isinstance(tgt.value, ast.Name) \
                                    and tgt.value.id in ("self", "cls") \
                                    and cls_qn is not None:
                                if self.is_run_family_ref(sub.value):
                                    self.class_attr_bindings[(cls_qn, tgt.attr)] = \
                                        {"param": None, "active": True,
                                         "member": self.ref_member(sub.value)}
                                elif isinstance(sub.value, ast.Name):
                                    for (fq, pname, cq, dn) in param_defaults:
                                        if fq == own_qn and pname == sub.value.id \
                                                and cq == cls_qn:
                                            member = None
                                            for a2, d2 in _param_pairs(dn):
                                                if a2.arg == pname:
                                                    member = self.ref_member(d2)
                                            self.class_attr_bindings[
                                                (cls_qn, tgt.attr)] = \
                                                {"param": pname, "active": True,
                                                 "member": member}
        self._apply_construction_rebindings()

    def _apply_construction_rebindings(self):
        """Follow explicit construction-time rebindings in scanned scope.

        A class attribute binding stays active unless EVERY construction
        of the class in this file passes the bound parameter AND every
        such pass gives it a non-run-family value (the argument's value
        governs for that instance; a construction that omits the argument
        leaves the default in force).  At the pin the one live
        construction passes no runner, so the default governs.
        """
        by_class_param: dict[tuple[str, str], dict] = {}
        for (cls_qn, attr), info in self.class_attr_bindings.items():
            if info.get("param"):
                by_class_param[(cls_qn, info["param"])] = info
        if not by_class_param:
            return
        for node in ast.walk(self.tree):
            if not isinstance(node, ast.Call):
                continue
            fname = None
            if isinstance(node.func, ast.Name):
                fname = node.func.id
            elif isinstance(node.func, ast.Attribute):
                fname = node.func.attr
            if fname is None:
                continue
            for (cls_qn, param), info in by_class_param.items():
                if cls_qn.split(".")[-1] != fname:
                    continue
                passed = [kw.value for kw in node.keywords if kw.arg == param]
                if passed:
                    info.setdefault("constructions", []).append(
                        any(self.is_run_family_ref(v) for v in passed))
                else:
                    info.setdefault("constructions", []).append(None)
        for info in by_class_param.values():
            seen = info.get("constructions", [])
            if seen and all(v is False for v in seen):
                info["active"] = False

    # -- tracing -------------------------------------------------------------

    def trace_for(self, func_node) -> FuncTrace:
        key = id(func_node) if func_node is not None else -1
        if key not in self._traces:
            body = func_node.body if func_node is not None else self.tree.body
            self._traces[key] = FuncTrace(body)
        return self._traces[key]

    def _entry_value_ok(self, trace: FuncTrace):
        def ok(expr, mech, at_nid):
            if expr_recognised(expr, mech):
                return True
            for node in _walk_pruned(expr):
                if isinstance(node, ast.Name):
                    if self._var_reaches(trace, node.id, mech, at_nid):
                        return True
            return False
        return ok

    def _var_reaches(self, trace: FuncTrace, name, mech, target_nid) -> bool:
        sources, removals = trace.events_for(
            ("var", name, mech), self._entry_value_ok(trace))
        return trace.reaches(sources, removals, target_nid)

    def _dict_reaches(self, trace: FuncTrace, dname, key, mech, target_nid) -> bool:
        sources, removals = trace.events_for(
            ("dict", dname, key, mech), self._entry_value_ok(trace))
        return trace.reaches(sources, removals, target_nid)

    def _value_traced(self, trace: FuncTrace, expr, mech, inv_nid) -> bool:
        if expr_recognised(expr, mech):
            return True
        for node in _walk_pruned(expr):
            if isinstance(node, ast.Name):
                if self._var_reaches(trace, node.id, mech, inv_nid):
                    return True
        return False

    def _splat_traced_key(self, trace: FuncTrace, expr, mech, key,
                          inv_nid) -> bool:
        dicts: list[ast.Dict] = []
        if isinstance(expr, ast.Dict):
            dicts = [expr]
        elif isinstance(expr, (ast.IfExp, ast.BoolOp)):
            dicts = [n for n in _walk_pruned(expr) if isinstance(n, ast.Dict)]
        for d in dicts:
            for k, v in zip(d.keys, d.values):
                if isinstance(k, ast.Constant) and k.value == key:
                    if self._value_traced(trace, v, mech, inv_nid):
                        return True
        if isinstance(expr, ast.Name):
            if self._dict_reaches(trace, expr.id, key, mech, inv_nid):
                return True
        return False

    def _splat_traced(self, trace: FuncTrace, expr, inv_nid) -> bool:
        for mech, key in (("cf", "creationflags"), ("si", "startupinfo")):
            if self._splat_traced_key(trace, expr, mech, key, inv_nid):
                return True
        return False

    def _classify_call(self, call: ast.Call, func_node) -> str:
        trace = self.trace_for(func_node)
        inv_nid = trace.statement_of(call, self.parent)
        flagged = False
        negated = False
        for kw in call.keywords:
            if kw.arg == "creationflags":
                if self._value_traced(trace, kw.value, "cf", inv_nid):
                    flagged = True
                    # Flag antagonism: a DETACHED_PROCESS form tracing to
                    # the same creationflags value negates the
                    # hidden-window form (module docstring, FLAG
                    # ANTAGONISM).
                    if self._value_traced(trace, kw.value, "det", inv_nid):
                        negated = True
            elif kw.arg == "startupinfo":
                flagged = flagged or self._value_traced(
                    trace, kw.value, "si", inv_nid)
            elif kw.arg is None:  # **splat
                if self._splat_traced(trace, kw.value, inv_nid):
                    flagged = True
                    if self._splat_traced_key(
                            trace, kw.value, "det", "creationflags",
                            inv_nid):
                        negated = True
        if negated:
            return "UNFLAGGED"
        return "FLAGGED" if flagged else "UNFLAGGED"

    # -- enumeration -----------------------------------------------------------

    def _enumerate(self):
        for node, cls_qn, func_node, func_qn in self._scoped_walk(self.tree):
            if not isinstance(node, ast.Call):
                continue
            func = node.func
            pass_name = None
            member = None
            if self.is_run_family_ref(func):
                pass_name = "a"
                member = self.ref_member(func)
            elif isinstance(func, ast.Attribute) \
                    and isinstance(func.value, ast.Name) \
                    and func.value.id in ("self", "cls") \
                    and cls_qn is not None \
                    and self.class_attr_bindings.get(
                        (cls_qn, func.attr), {}).get("active"):
                pass_name = "c"
                member = self.class_attr_bindings[
                    (cls_qn, func.attr)].get("member")
            elif isinstance(func, ast.Name) \
                    and (func_qn, func.id) in self.func_local_bindings:
                pass_name = "c"
                member = self.func_local_bindings[(func_qn, func.id)]
            else:
                args = list(node.args) + [kw.value for kw in node.keywords]
                for arg in args:
                    probe = arg.value if isinstance(arg, ast.Starred) else arg
                    if isinstance(probe, (ast.Attribute, ast.Name)) \
                            and self.is_run_family_ref(probe):
                        pass_name = "b"
                        member = self.ref_member(probe)
                        break
            if pass_name is None:
                continue
            classification = self._classify_call(node, func_node)
            qualname = func_qn if func_qn is not None else "<module>"
            entry = ALLOWLIST.get((self.rel, qualname))
            if classification == "UNFLAGGED" and entry is not None \
                    and entry["member"] == member:
                classification = "ALLOWLISTED"
            self.sites.append({
                "file": self.rel,
                "line": node.lineno,
                "qualname": qualname,
                "pass": pass_name,
                "member": member,
                "classification": classification,
            })


# --------------------------------------------------------------------------
# Census assembly + dump
# --------------------------------------------------------------------------

_CENSUS: dict | None = None


def run_census() -> dict:
    global _CENSUS
    if _CENSUS is not None:
        return _CENSUS
    tracked = tracked_py_files()
    scanned: list[str] = []
    excluded: list[str] = []
    unclassified: list[str] = []
    for rel in tracked:
        cls = classify_rel(rel)
        if cls == "scanned":
            scanned.append(rel)
        elif cls is None:
            unclassified.append(rel)
        else:
            excluded.append(rel)
    sites: list[dict] = []
    for rel in scanned:
        sites.extend(FileAnalysis(rel).sites)
    sites.sort(key=lambda s: (s["file"], s["line"], s["qualname"], s["pass"]))
    result = {
        "tracked": tracked,
        "scanned": scanned,
        "excluded": excluded,
        "unclassified": unclassified,
        "sites": sites,
    }
    dump_env = os.environ.get("CENSUS_DUMP")
    if dump_env:
        if dump_env.lower() in ("1", "true", "yes"):
            dump_path = Path.cwd() / "census_dump.txt"
        else:
            dump_path = Path(dump_env)
        lines = [
            json.dumps(site, ensure_ascii=False, sort_keys=True)
            for site in sites
        ]
        dump_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
        print(f"CENSUS_DUMP_PATH={dump_path}")
        result["dump_path"] = str(dump_path)
    _CENSUS = result
    return result


# --------------------------------------------------------------------------
# Tests
# --------------------------------------------------------------------------

def test_scan_scope_partition():
    census = run_census()
    assert not census["unclassified"], (
        "tracked .py files matching no named scan-scope class "
        "(a new top-level directory forces a human scope decision): "
        + ", ".join(census["unclassified"]))
    assert len(census["scanned"]) + len(census["excluded"]) \
        == len(census["tracked"])


def test_allowlist_rot_control():
    census = run_census()
    live = {(s["file"], s["qualname"], s["member"]) for s in census["sites"]}
    rotten = [f"{f} :: {q}" for (f, q), e in ALLOWLIST.items()
              if (f, q, e["member"]) not in live]
    assert not rotten, (
        "allowlist entries matching no live enumerated site: "
        + "; ".join(rotten))
    assert len(ALLOWLIST) == 1


def test_no_unflagged_spawn_sites():
    census = run_census()
    unflagged = [s for s in census["sites"]
                 if s["classification"] == "UNFLAGGED"]
    listing = "\n".join(
        f"{s['file']}:{s['line']} qualname={s['qualname']} pass={s['pass']}"
        for s in unflagged)
    assert not unflagged, (
        f"{len(unflagged)} unflagged, non-allowlisted spawn site(s):\n"
        + listing)

def test_background_spawn_flags_carry_no_detached(tmp_path):
    """Runtime layer of the antagonism guard (2026-11-09 pick-folder flash).

    The static census traces flag forms through assignments; flag
    arithmetic done by other means would still slip past it.  This test
    drives the real runner instead: a background (keep_alive)
    non-interactive spawn must reach Popen with CREATE_NO_WINDOW and
    CREATE_NEW_PROCESS_GROUP set and DETACHED_PROCESS clear, and the
    payload must not report the spawn as detached.
    """
    import asyncio
    import subprocess as sp
    import sys
    from unittest import mock

    import code_tools

    class _FakeProc:
        pid = 987654
        returncode = None

        def poll(self):
            return None

    fake = _FakeProc()
    with mock.patch.object(code_tools.subprocess, "Popen",
                           return_value=fake) as popen:
        payload = json.loads(asyncio.run(code_tools.run_project_command(
            str(tmp_path), "echo pf-flash-guard", shell_mode="cmd",
            background=True)))
    assert payload["ok"], payload
    if sys.platform == "win32":
        flags = popen.call_args.kwargs["creationflags"]
        assert flags & sp.CREATE_NO_WINDOW
        assert flags & sp.CREATE_NEW_PROCESS_GROUP
        assert not flags & getattr(sp, "DETACHED_PROCESS", 0)
    assert payload["detached"] is False
