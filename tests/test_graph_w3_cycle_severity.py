"""W3: a cycle is a crash risk only if it can still close at import time.

`_cycle_details` classified severity from `top_level_edges == 0` -- "does this
loop contain any import-time edge". The question that decides urgency is
different: "does a loop survive when only the import-time edges remain". The two
answers differ for every cycle that mixes deferred and top-level imports, which
is the common shape.

Measured on [A] itself: the 18-file SCC (`code_tools`, `orchestrator_server`,
`tools_v15`, the `tools/read` and `tools/write` packages, ...) has 17 top-level
pairs and 24 deferred-only pairs and was reported `import_time_risk`. Delete
every deferred pair and the remaining top-level subgraph is ACYCLIC -- no import
order can close that loop. The report sent a reader after 18 files and 17 import
lines for a crash that cannot happen.

These tests check the classification against the INTERPRETER rather than against
the implementation, because agreeing with the implementation is exactly how the
W5 test certified the wrong rule for two waves. Each fixture is imported for
real, and the severity assertion is tied to whether that import raised.
"""
import os

os.environ["GRAPH_ENABLED"] = "1"

import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from orchestrator.graph import cycle_point
from orchestrator.graph.indexer import index_project


def _index(tmp_path, monkeypatch, name, files):
    project = tmp_path / name
    project.mkdir(parents=True, exist_ok=True)
    for rel, text in files.items():
        p = project / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(textwrap.dedent(text).lstrip(), encoding="utf-8")
    monkeypatch.setenv("GRAPH_DB_DIR", str(tmp_path / "graph" / name))
    result = index_project(str(project).replace("\\", "/"), scope="full")
    assert result["status"] == "completed", result
    return str(project).replace("\\", "/")


def _imports_cleanly(project, module):
    """Import `module` in a FRESH interpreter and report whether it raised.

    The ground truth for every severity assertion below. A subprocess because
    the current process has already imported the graph package and because a
    half-initialised module left in `sys.modules` would poison later tests.
    """
    proc = subprocess.run(
        [sys.executable, "-c",
         "import sys; sys.path.insert(0, %r)\nimport %s" % (project, module)],
        capture_output=True, text=True, timeout=60,
    )
    return proc.returncode == 0, (proc.stderr or "").strip()


# --- the shape [A] itself has: mixed cycle, acyclic at import time -----------

_MIXED = {
    "a.py": """
        import b

        def eager():
            return b.thing()
    """,
    "b.py": """
        def thing():
            return 1

        def lazy():
            import a
            return a.eager()
    """,
}


def test_a_cycle_broken_by_a_deferred_import_is_debt_not_risk(tmp_path, monkeypatch):
    """One top-level edge, one deferred: the loop cannot close during import.

    The interpreter is asked first, so this test states a fact about Python and
    not a preference about our own output.
    """
    project = _index(tmp_path, monkeypatch, "mixed", _MIXED)

    ok, err = _imports_cleanly(project, "a")
    assert ok, "fixture must import cleanly for the rest to mean anything: %s" % err

    out = cycle_point.compute_cycle_point(project)
    assert out["cycle_count"] == 1, out["cycles"]
    detail = out["cycle_details"][0]

    # The old rule keyed off this being non-zero and called the loop a hazard.
    assert detail["top_level_edges"] == 1
    assert detail["severity"] == "deferred_debt"
    assert detail["deferred_only"] is True
    assert detail["import_time_subcycles"] == []
    assert out["import_time_cycles"] == 0
    assert out["deferred_only_cycles"] == 1


# --- a cycle that really does crash -----------------------------------------

_CRASHING = {
    "a.py": """
        from b import thing

        def eager():
            return thing()
    """,
    "b.py": """
        from a import eager

        def thing():
            return eager
    """,
}


def test_a_cycle_that_actually_raises_is_reported_as_a_risk(tmp_path, monkeypatch):
    """Both directions at import time, and `from x import name`, so Python
    raises. Without this the fix could be 'always say deferred_debt'."""
    project = _index(tmp_path, monkeypatch, "crashing", _CRASHING)

    ok, err = _imports_cleanly(project, "a")
    assert not ok, "fixture was supposed to raise on import"
    assert "circular import" in err or "partially initialized" in err, err

    out = cycle_point.compute_cycle_point(project)
    detail = out["cycle_details"][0]
    assert detail["severity"] == "import_time_risk"
    assert detail["deferred_only"] is False
    assert out["import_time_cycles"] == 1
    assert out["deferred_only_cycles"] == 0

    # The work-list must name the surviving loop, not the whole component.
    assert len(detail["import_time_subcycles"]) == 1
    assert {Path(f).name for f in detail["import_time_subcycles"][0]} == {"a.py", "b.py"}
    assert detail["import_time_cycle_size"] == 2

    # And every reported crash site must carry an import line to go edit.
    assert len(detail["breakable_at_top_level"]) == 2
    assert all(e["lines"] for e in detail["breakable_at_top_level"])


# --- the distinction the old rule could not draw ----------------------------

_PARTIAL = {
    "a.py": """
        import b

        def f():
            return b.g()

        def reach_d():
            import d
            return d.only_deferred()
    """,
    "b.py": """
        import c

        def g():
            return c.h()
    """,
    "c.py": """
        import a

        def h():
            return a.f
    """,
    # Joined to the component only because `a.reach_d` imports it inside a
    # function body: mutually reachable, but never during an import of `a`.
    "d.py": """
        import a

        def only_deferred():
            return a.f
    """,
}


def test_only_the_files_in_the_surviving_loop_are_the_work_list(tmp_path, monkeypatch):
    """A 3-file import-time loop with a 4th file joined by a deferred import.

    `files` is the whole SCC (correct: they are mutually reachable), but the
    thing a reader must edit to stop an ImportError is the surviving sub-cycle.
    Reporting the component as the work-list is what made the [A] measurement
    unusable -- 18 files named, none of them the problem.
    """
    project = _index(tmp_path, monkeypatch, "partial", _PARTIAL)
    out = cycle_point.compute_cycle_point(project)

    assert out["cycle_count"] == 1
    detail = out["cycle_details"][0]
    assert detail["severity"] == "import_time_risk"

    sub = detail["import_time_subcycles"]
    assert len(sub) == 1
    names = {Path(f).name for f in sub[0]}
    assert names == {"a.py", "b.py", "c.py"}, names
    assert "d.py" in {Path(f).name for f in detail["files"]}
    assert "d.py" not in names, "a file reachable only through a deferred import \
cannot be part of an import-time loop"


def test_severity_never_disagrees_with_the_subcycle_list(tmp_path, monkeypatch):
    """The invariant behind the fix, stated once so the two cannot drift.

    `severity`, `deferred_only`, `import_time_cycles` and
    `import_time_subcycles` are four views of one fact. W6 and the `_stale_count`
    follow-up were both the same defect -- one question answered twice -- so the
    agreement is pinned rather than assumed.
    """
    for name, files in (("inv_mixed", _MIXED), ("inv_crash", _CRASHING),
                        ("inv_partial", _PARTIAL)):
        project = _index(tmp_path, monkeypatch, name, files)
        out = cycle_point.compute_cycle_point(project)
        risky = 0
        for d in out["cycle_details"]:
            has_sub = bool(d["import_time_subcycles"])
            assert d["deferred_only"] is (not has_sub), (name, d["files"])
            assert d["severity"] == (
                "import_time_risk" if has_sub else "deferred_debt"), (name, d["files"])
            assert d["import_time_cycle_size"] == max(
                (len(c) for c in d["import_time_subcycles"]), default=0)
            # Sub-cycle members are always drawn from the component.
            assert all(set(c) <= set(d["files"]) for c in d["import_time_subcycles"])
            risky += 1 if has_sub else 0
        assert out["import_time_cycles"] == risky, name
        assert out["deferred_only_cycles"] == len(out["cycle_details"]) - risky


def test_hazards_sort_above_debt(tmp_path, monkeypatch):
    """Ordering is the report's only prioritisation, so a deferred-debt cycle
    must never outrank a crashing one -- however much larger it is."""
    project = _index(tmp_path, monkeypatch, "ordering", {
        # A crashing 2-file loop.
        "x.py": """
            from y import gy

            def gx():
                return gy()
        """,
        "y.py": """
            from x import gx

            def gy():
                return gx
        """,
        # A larger, purely deferred loop: 3 files, no import-time edge.
        "p.py": """
            def fp():
                import q
                return q.fq()
        """,
        "q.py": """
            def fq():
                import r
                return r.fr()
        """,
        "r.py": """
            def fr():
                import p
                return p.fp
        """,
    })
    out = cycle_point.compute_cycle_point(project)
    assert out["cycle_count"] == 2, out["cycles"]
    first, second = out["cycle_details"][0], out["cycle_details"][1]
    assert first["severity"] == "import_time_risk"
    assert first["size"] == 2
    assert second["severity"] == "deferred_debt"
    assert second["size"] == 3, "the bigger cycle is the harmless one -- that is the test"
    assert out["import_time_cycles"] == 1
    assert out["deferred_only_cycles"] == 1


def test_a_self_consistent_acyclic_project_reports_nothing(tmp_path, monkeypatch):
    """Baseline: without it, a classifier that always returns deferred_debt with
    an empty sub-cycle list would satisfy most of this file."""
    project = _index(tmp_path, monkeypatch, "clean", {
        "base.py": "def b():\n    return 1\n",
        "mid.py": "import base\n\ndef m():\n    return base.b()\n",
        "top.py": "import mid\n\ndef t():\n    return mid.m()\n",
    })
    out = cycle_point.compute_cycle_point(project)
    assert out["cycle_count"] == 0
    assert out["cycle_details"] == []
    assert out["import_time_cycles"] == 0
    assert out["deferred_only_cycles"] == 0
