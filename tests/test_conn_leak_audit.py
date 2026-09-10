"""Static AST audit — every production ``sqlite3.connect`` must be leak-managed.

WHY: a new ``sqlite3.connect`` added to production code without a ``with``
context manager or a ``finally``-close is a connection-pool leak risk (AC20,
Phase 3B.7). This test walks the production modules, finds every
``sqlite3.connect`` call site (including ``import sqlite3 as <alias>`` forms
and the UTF-8 BOM in ``code_tools.py``), and asserts each site is:

  (a) inside a ``with`` statement (context manager), OR
  (b) in a function whose body closes the conn in a ``finally`` block, OR
  (c) in the WHITELIST below — matched by (file, function-name) so it is
      tolerant to line-number drift from parallel edits.

The whitelist also pins the expected connect-count per whitelisted function,
so adding a *new* connect inside an already-whitelisted function is still
caught (function-name matching alone would mask that).

Whitelist categories:
  FACTORY       — function returns the conn; callers close it in ``finally``.
  CLOSE_IN_TRY  — ``conn.close()`` runs in the try body on the success path
                  (not ``finally``). On exception the local conn is refcounted
                  to 0 by CPython and released by ``sqlite3.Connection.__del__``
                  (no persistent leak; P1.18 baseline leak-free). Grandfathered
                  — new production code MUST use ``with`` / ``finally``.
  POOL          — long-lived connection cached on a ``threading.local`` and
                  reused across calls (a connection pool). It is intentionally
                  not closed per-call; it is closed on DB-path rebind and by
                  ``__del__`` at thread/interpreter shutdown — bounded by the
                  worker-thread count, so no unbounded leak.

Production set = ``code_tools.py`` + ``orchestrator_server.py`` +
``orchestrator/**.py``, excluding tests/ research/ BACKUP/ staging/ scripts/
examples/ .council/ __pycache__/ venv312/.

A transient mid-write by a parallel worker can make ``ast.parse`` raise
``SyntaxError``; each file is retried once after ``_RETRY_SECONDS`` (default
60) before the test fails — per the 3B.7 parallel-edit contract.

Run:  ./venv312/Scripts/python.exe -m pytest tests/test_conn_leak_audit.py -v
"""

from __future__ import annotations

import ast
import os
import time
from pathlib import Path

_A_ROOT = Path(__file__).resolve().parent.parent
_RETRY_SECONDS = int(os.environ.get("CONN_AUDIT_RETRY_SECONDS", "5"))

_EXCLUDE_DIRS = {
    "tests", "research", "BACKUP", "staging", "scripts", "examples",
    ".council", "__pycache__", ".git", "venv312", "node_modules", ".agents",
    "skills",
}

# (relative_file, function_name) -> (expected_connect_count, category, reason)
# Matched by (file, function-name) — NOT line number — so parallel line drift
# is tolerated.  The expected count catches a new connect added inside an
# already-whitelisted function.
WHITELIST: dict[tuple[str, str], tuple[int, str, str]] = {
    # ── FACTORY: returns conn; callers close in finally ───────────────────
    ("orchestrator/graph/store.py", "get_db"): (
        1, "FACTORY",
        "connection factory; returns conn; init_db/integrity_check/reset_db "
        "close it in finally",
    ),
    ("orchestrator/graph/lsp_resolver.py", "_get_ro_conn"): (
        1, "FACTORY",
        "read-only factory; returns conn; resolve_benchmark closes in finally",
    ),
    ("orchestrator/graph/lsp_resolver.py", "_get_rw_conn"): (
        1, "FACTORY",
        "read-write factory; returns conn; resolve_apply closes in finally/except",
    ),
    # ── CLOSE_IN_TRY: close() on success path; CPython refcount + __del__ ──
    # ── releases conn on exception (no persistent leak). Grandfathered.   ──
    ("code_tools.py", "get_usage_stats"): (
        1, "CLOSE_IN_TRY", "close in try body; CPython refcount-safe on exception"),
    ("orchestrator/session/logs.py", "log_native"): (
        1, "CLOSE_IN_TRY", "close in try body; CPython refcount-safe on exception"),
    # ── FACTORY: returns conn; every caller closes it in a finally block ──
    ("orchestrator/server/audit_writer.py", "_connect"): (
        1, "FACTORY",
        ("connection factory; returns conn; all 11 call sites "
         "(_initialize_schema_once/_stage_transaction/etc.) close it in finally"),
    ),
    ("orchestrator/session/store.py", "_open_connection"): (
        1, "FACTORY",
        ("SessionWriter connection factory; returns conn; _drain_loop closes it "
         "in the batch except-path and the outer finally on writer shutdown"),
    ),
    # ── POOL: thread-local cached read connection; bounded by the to_thread ──
    # ── worker pool, reused across calls, closed on DB-path rebind and by   ──
    # ── __del__ at thread/interpreter shutdown (no unbounded leak).         ──
    ("orchestrator/session/store.py", "_session_read_connection"): (
        2, "POOL",
        ("thread-local query_only read connection; cached on threading.local, "
         "reused per worker thread, closed on path change — a connection pool, "
         "not a per-call leak. STT-27 B032: +1 for :memory: fallback when "
         "_TEARDOWN_COMPLETE is set (prevents daemon threads from pinning "
         "sessions.db after close_writer())."),
    ),
}


# ── AST helpers ────────────────────────────────────────────────────────────

def _excluded(p: Path) -> bool:
    return bool(set(p.parts) & _EXCLUDE_DIRS)


def _prod_files() -> list[Path]:
    """code_tools.py + orchestrator_server.py + orchestrator/**.py, minus the
    non-production dirs."""
    out: list[Path] = []
    for name in ("code_tools.py", "orchestrator_server.py"):
        f = _A_ROOT / name
        if f.exists():
            out.append(f)
    orch = _A_ROOT / "orchestrator"
    if orch.exists():
        for f in orch.rglob("*.py"):
            if not _excluded(f):
                out.append(f)
    return sorted(set(out))


_tree_cache: dict[Path, ast.AST] = {}
_err_cache: dict[Path, Exception] = {}


def _parse(path: Path) -> ast.AST:
    """ast.parse with utf-8-sig (code_tools.py carries a BOM). Retry once after
    ``_RETRY_SECONDS`` on SyntaxError to tolerate a parallel mid-write; the
    failure is cached so later tests do not re-sleep."""
    if path in _tree_cache:
        return _tree_cache[path]
    if path in _err_cache:
        raise _err_cache[path]

    def _attempt() -> ast.AST:
        return ast.parse(path.read_text(encoding="utf-8-sig"))

    try:
        tree = _attempt()
    except SyntaxError:
        time.sleep(_RETRY_SECONDS)
        try:
            tree = _attempt()
        except SyntaxError as e:
            err = AssertionError(
                f"AST parse failed twice for {path.name} — transient parallel "
                f"mid-write? {type(e).__name__}: {e}"
            )
            _err_cache[path] = err
            raise err from e
    _tree_cache[path] = tree
    return tree


def _sqlite3_names(tree: ast.AST) -> set[str]:
    """Names that refer to the sqlite3 module: ``sqlite3`` plus any
    ``import sqlite3 as <alias>`` asname (e.g. orchestrator_server's ``_sq``)."""
    names = {"sqlite3"}
    for n in ast.walk(tree):
        if isinstance(n, ast.Import):
            for a in n.names:
                if a.name == "sqlite3":
                    names.add(a.asname or "sqlite3")
    return names


def _parents(tree: ast.AST) -> dict[ast.AST, ast.AST]:
    parents: dict[ast.AST, ast.AST] = {}
    for parent in ast.walk(tree):
        for child in ast.iter_child_nodes(parent):
            parents[child] = parent
    return parents


def _enclosing_func(node, parents):
    cur = node
    while cur is not None:
        if isinstance(cur, (ast.FunctionDef, ast.AsyncFunctionDef)):
            return cur
        cur = parents.get(cur)
    return None


def _inside_with(node, parents) -> bool:
    cur = parents.get(node)
    while cur is not None:
        if isinstance(cur, (ast.With, ast.AsyncWith)):
            return True
        cur = parents.get(cur)
    return False


def _assign_vars(call, parents) -> list[str]:
    p = parents.get(call)
    if isinstance(p, ast.Assign):
        out: list[str] = []
        for t in p.targets:
            if isinstance(t, ast.Name):
                out.append(t.id)
            elif isinstance(t, (ast.Tuple, ast.List)):
                for el in t.elts:
                    if isinstance(el, ast.Name):
                        out.append(el.id)
        return out
    return []


def _finally_closes(func_node, var_names) -> bool:
    """True if func_node has a try/finally whose finally body calls
    ``<one of var_names>.close()``."""
    if not func_node or not var_names:
        return False
    for sub in ast.walk(func_node):
        if isinstance(sub, ast.Try) and sub.finalbody:
            for stmt in sub.finalbody:
                for n in ast.walk(stmt):
                    if (isinstance(n, ast.Call)
                            and isinstance(n.func, ast.Attribute)
                            and n.func.attr == "close"
                            and isinstance(n.func.value, ast.Name)
                            and n.func.value.id in var_names):
                        return True
    return False


def _collect() -> list[dict]:
    """One dict per sqlite3.connect call site in production code."""
    rows: list[dict] = []
    for f in _prod_files():
        tree = _parse(f)
        names = _sqlite3_names(tree)
        parents = _parents(tree)
        rel = str(f.relative_to(_A_ROOT)).replace("\\", "/")
        for node in ast.walk(tree):
            if not (isinstance(node, ast.Call)
                    and isinstance(node.func, ast.Attribute)
                    and node.func.attr == "connect"
                    and isinstance(node.func.value, ast.Name)
                    and node.func.value.id in names):
                continue
            fn = _enclosing_func(node, parents)
            rows.append({
                "file": rel,
                "lineno": node.lineno,
                "func": fn.name if fn else "<module>",
                "vars": _assign_vars(node, parents),
                "with": _inside_with(node, parents),
                "func_node": fn,
            })
    return rows


# ── tests ──────────────────────────────────────────────────────────────────

def test_no_unmanaged_sqlite3_connect_in_production():
    """Every production sqlite3.connect is with / finally-close / whitelisted."""
    rows = _collect()
    assert rows, "no sqlite3.connect sites found — scanner is broken"
    unmanaged: list[str] = []
    for r in rows:
        key = (r["file"], r["func"])
        if r["with"]:
            continue
        if _finally_closes(r["func_node"], r["vars"]):
            continue
        if key in WHITELIST:
            continue
        unmanaged.append(f"{r['file']}::{r['func']} (L{r['lineno']})")
    assert not unmanaged, (
        "Unmanaged sqlite3.connect in production (add with/finally or "
        "WHITELIST with a reason):\n  - " + "\n  - ".join(unmanaged)
    )


def test_whitelist_connect_counts_match():
    """Each whitelisted function has exactly its expected connect count
    (catches a new connect added inside an already-whitelisted function)."""
    rows = _collect()
    by_key: dict[tuple[str, str], int] = {}
    for r in rows:
        by_key[(r["file"], r["func"])] = by_key.get((r["file"], r["func"]), 0) + 1
    mismatches: list[str] = []
    for key, (expected, cat, _reason) in WHITELIST.items():
        actual = by_key.get(key, 0)
        if actual != expected:
            mismatches.append(
                f"{key[0]}::{key[1]} expected={expected} actual={actual} [{cat}]"
            )
    assert not mismatches, (
        "Whitelist connect-count drift (update WHITELIST count or remove the "
        "extra unmanaged connect):\n  - " + "\n  - ".join(mismatches)
    )


def test_whitelist_entries_still_present():
    """No stale whitelist entries (a whitelisted function lost its connect)."""
    rows = _collect()
    seen = {(r["file"], r["func"]) for r in rows}
    stale = [f"{k[0]}::{k[1]}" for k in WHITELIST if k not in seen]
    assert not stale, (
        "Stale WHITELIST entries (no connect site found — remove them after a "
        "refactor):\n  - " + "\n  - ".join(stale)
    )


def test_no_bare_sqlite3_connect_import():
    """Guard against evasion via ``from sqlite3 import connect`` + bare
    ``connect(...)`` (the Attribute-call scanner would not detect it)."""
    offenders: list[str] = []
    for f in _prod_files():
        tree = _parse(f)
        rel = str(f.relative_to(_A_ROOT)).replace("\\", "/")
        for n in ast.walk(tree):
            if (isinstance(n, ast.ImportFrom) and n.module == "sqlite3"
                    and "connect" in [a.name for a in n.names]):
                offenders.append(rel)
    assert not offenders, (
        "Production uses `from sqlite3 import connect` (bare-connect evasion) "
        "in: " + ", ".join(offenders)
    )


# ── manual inspection entry ────────────────────────────────────────────────
if __name__ == "__main__":  # pragma: no cover
    for r in _collect():
        key = (r["file"], r["func"])
        wl = WHITELIST.get(key)
        tag = ("WITH" if r["with"]
               else ("FINALLY" if _finally_closes(r["func_node"], r["vars"])
                     else (f"WL:{wl[1]}" if wl else "UNMANAGED")))
        print(f"{r['file']}\tL{r['lineno']}\t{r['func']}\t{tag}")
