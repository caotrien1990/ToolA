"""One definition of "this callable is dead", shared by every report.

Three reports answer the same question -- `cbm_dead_code`, the `orphan_symbols`
template, and `cbm_cycle_point`'s redundancy dimension -- and each used to carry
its own copy of the filter. `config.LIVE_REF_EXISTS_SQL` already fixed half of
that: the SQL predicate was copy-pasted in three places with the threshold
hardcoded in two, so a new edge kind reached only one report and the same symbol
read alive in one and dead in another.

The Python half drifted the same way and was missed. `cycle_point._dead_ratio`
said in its own docstring that it "mirrors the conservative filter in
query.dead_code_candidates" while applying neither `is_entry_point` nor
`is_framework_dispatch`. Measured on [A] at the time this module was written:
3167 dead reported against 664 actual, and 2200 of the 2503 phantom entries were
`test_*` functions -- pytest reaches those through discovery, so they have no
inbound edge and never will. The redundancy dimension was reading 41% of the
codebase as dead when the real figure was 8.6%, which moved `quality_signal`
from 0.4765 to 0.4367.

A measurement that is wrong in one report and right in another is worse than one
that is wrong everywhere: the disagreement is invisible until someone diffs two
reports by hand. So the filter chain lives here, once, and the reports differ
only in the two things they genuinely disagree about -- whether an `__all__`
export counts, and whether a decorated symbol counts.

This module is a leaf on purpose. It imports `config` and nothing else, so
`query` (which sits above `store`, `indexer` and `status`) and `cycle_point`
(which sits below them) can both reach it without an upward import. Putting
these helpers in `query` and importing them from `cycle_point` would have been
the smaller diff and would have created this project's first upward dependency
violation -- degrading the very depth metric the caller is trying to measure.
"""
from __future__ import annotations

from .config import LIVE_REF_EXISTS_SQL, LIVE_REF_BINDS

# Names that are an entry point by convention rather than by any edge in the
# graph: nothing in the source calls them, and something outside it does.
_ENTRY_NAMES = {"main", "run", "cli", "start", "serve", "app"}

# A defensive cap, not a page size. The callers that page their output do it
# after this filter, because a count taken before the filter is not the count of
# anything a reader would recognise.
_MAX_ROWS = 50000


def is_entry_point(name: str, decorators: str) -> bool:
    """Heuristic entry-point test: canonical names, handler prefixes, dunder
    mains, or routing/CLI decorators. Conservative by design (Step 17)."""
    if name in _ENTRY_NAMES or name.startswith(("handle_", "on_", "test_")):
        return True
    if name.startswith("__") and name.endswith("__"):
        return True
    dec = (decorators or "").lower()
    return any(k in dec for k in ("route", "command", "task", "hook", "register"))


def framework_dispatch_methods(conn) -> set:
    """(file_path, qualified_name) of every PUBLIC method the graph cannot prove
    is uncalled, because its class inherits from a base the graph cannot see.

    `_Extractor.visit_Call` has zero inbound call edges and never will: nothing
    in this project calls it by name. `ast.NodeVisitor.generic_visit` does, by
    building the name from the node type at runtime. Same shape for `do_GET` on
    a BaseHTTPRequestHandler subclass and for `as_payload` on a RuntimeError
    subclass. Measured on [A]: 16 of the 92 `orchestrator/` entries -- one in
    six -- were this, and every one of them is load-bearing code that would
    break the moment it was deleted. A dead-code list is a deletion work-list;
    an entry that is always wrong teaches the reader to distrust the whole list.

    The signal is an `inherits` edge whose target is `external` or `import` --
    a base outside the indexed source, so its dispatch table is unknowable. A
    base the graph CAN see inside is not covered here: if a project-local class
    dispatched to this method, that call site is indexed and produces an edge.

    Restricted to PUBLIC names on purpose. A base class cannot dispatch to a
    name it does not know, and `_helper` on a unittest.TestCase subclass is
    exactly as dead as `_helper` anywhere else -- excluding it would hide 21
    genuinely dead symbols to save these 16. Ambiguity resolves toward keeping
    an entry that a human can check, never toward inventing liveness.
    """
    return {(r["sf"], r["sq"]) for r in conn.execute(
        "SELECT DISTINCT s.file_path AS sf, s.qualified_name AS sq FROM edges e "
        "JOIN nodes s ON s.id = e.source JOIN nodes t ON t.id = e.target "
        "WHERE e.kind = 'inherits' AND t.kind IN ('external', 'import');")}


def is_framework_dispatch(row, opaque_classes: set) -> bool:
    """True when `row` is a public method on a class from framework_dispatch_methods."""
    if row["kind"] != "method" or row["name"].startswith("_"):
        return False
    cls = (row["qualified_name"] or "").rsplit(".", 1)[0]
    return (row["file_path"], cls) in opaque_classes


def dead_callables(conn, *, unexported_only: bool = False,
                   exclude_decorated: bool = False) -> list:
    """Rows for every callable with no confident inbound reference, filtered.

    The two flags are the only legitimate disagreement between the reports:

      `unexported_only` -- a symbol in `__all__` is reachable by import, so the
      deletion work-list skips it. `orphan_symbols` is a graph-shape view and
      wants it.

      `exclude_decorated` -- a decorator can hand the function to a framework
      that calls it later, invisibly. The deletion work-list skips those too.

    Everything else is applied unconditionally, because a symbol that is an
    entry point or a framework dispatch target is not dead under ANY reading of
    the word, and letting one report say otherwise is what this module exists to
    prevent. Ordered by name so two runs over the same DB return the same list.
    """
    where = ["n.kind IN ('function','method')"]
    if unexported_only:
        where.append("n.is_exported = 0")
    if exclude_decorated:
        where.append("(n.decorators IS NULL OR n.decorators IN ('','[]'))")
    where.append(f"NOT {LIVE_REF_EXISTS_SQL}")
    rows = conn.execute(
        "SELECT n.* FROM nodes n WHERE " + " AND ".join(where)
        + " ORDER BY n.qualified_name, n.start_line LIMIT ?;",
        (*LIVE_REF_BINDS, _MAX_ROWS)).fetchall()
    opaque = framework_dispatch_methods(conn)
    return [r for r in rows
            if not is_entry_point(r["name"], r["decorators"])
            and not is_framework_dispatch(r, opaque)]


__all__ = ["is_entry_point", "framework_dispatch_methods",
           "is_framework_dispatch", "dead_callables"]
