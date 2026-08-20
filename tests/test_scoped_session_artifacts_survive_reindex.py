# -*- coding: utf-8 -*-
"""A full cache refresh must not delete the session artifacts of live callers.

Found while chasing a single "flaky" failure in the full suite
(``test_prepare_ca_session_concurrent_artifacts_are_agent_scoped`` failing with
FileNotFoundError on CA_SESSION_BOOTSTRAP.md). It was not flaky and not a test
problem -- it was a real data-loss bug, and the test was the only thing noticing.

The mechanism was a name-scoping drift between two modules that never referenced
each other:

* ``surgical_mapper.PRESERVED_CACHE_FILES`` was written when every session
  artifact sat FLAT in ``.orchestrator_cache``, so ``_cache_path_is_preserved``
  compared the WHOLE relative path against that set.
* ``code_tools._scoped_session_artifact_path`` later moved those artifacts to
  ``sessions/<label>-<digest>/`` so two callers could not collide.

After that move the exact-match check no longer recognised them, and
``_reindex_from_disk`` -- which deliberately wipes non-preserved cache entries to
reconcile external deletes, on every ``generate_project_map`` and every periodic
full refresh -- deleted the bootstrap of every live CA session. In production that
is a session losing its own bootstrap mid-flight, not a test artifact.

``WORKFLOW_STATE.json`` survived, which is why this went unnoticed: not because it
was preserved, but because the prune loop skips a name whose stem has no extension
left after stripping ``.json``. An accident. So this file asserts on BOTH names --
the one that broke and the one that got lucky -- because only one of them was ever
actually protected.

The tests drive ``_reindex_from_disk`` (the shared implementation both public
entry points delegate to) and assert on the filesystem, so they keep working if
the prune loop is rewritten again.
"""
from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import code_tools as ct  # noqa: E402
import surgical_mapper as sm  # noqa: E402


@pytest.fixture()
def project(tmp_path, monkeypatch):
    """A project root with one source file, so a reindex has real work to do."""
    (tmp_path / "app.py").write_text("x = 1\n", encoding="utf-8")
    monkeypatch.setattr(ct, "_SESSIONS_DB", tmp_path / "sessions_scoped_test.db")
    return tmp_path


def _prepare(root: Path, agent_id: str, host_session_id: str) -> dict:
    return json.loads(asyncio.run(ct.prepare_ca_session(
        str(root), mode="T2", idea="Build CRM %s" % agent_id,
        agent_id=agent_id, host_session_id=host_session_id,
    )))


def test_a_scoped_bootstrap_survives_a_full_reindex(project) -> None:
    """The exact production data-loss case, asserted on disk.

    Before the fix this failed on the first assertion after the reindex: the file
    was written, then deleted by the prune loop, and the caller held a path to
    something that no longer existed.
    """
    result = _prepare(project, "agent-a", "host-a")
    bootstrap = Path(result["bootstrap_path"])
    state = Path(result["workflow_state_path"])
    assert bootstrap.is_file(), result
    body_before = bootstrap.read_text(encoding="utf-8")

    sm._reindex_from_disk(project)

    assert bootstrap.is_file(), "a full reindex deleted a live session's bootstrap"
    assert state.is_file(), "a full reindex deleted a live session's workflow state"
    assert bootstrap.read_text(encoding="utf-8") == body_before, \
        "the bootstrap survived but its content was rewritten"


def test_the_scoped_path_is_the_one_actually_used(project) -> None:
    """Pins the premise, so this file cannot pass for the wrong reason.

    If scoping were ever reverted to flat names, the test above would pass
    trivially (a flat name was always preserved) while proving nothing. Assert
    that the artifact really does live under ``sessions/<label>-<digest>/``.
    """
    result = _prepare(project, "agent-a", "host-a")
    rel = Path(result["bootstrap_path"]).resolve().relative_to(
        (project / ".orchestrator_cache").resolve()).as_posix()
    assert rel.startswith("sessions/"), "artifact is no longer scoped: %r" % rel
    assert rel.count("/") == 2, "unexpected scoped layout: %r" % rel
    assert sm._cache_path_is_preserved(rel), \
        "the scoped path the code actually produces is not preserved: %r" % rel


def test_two_concurrent_callers_both_keep_their_artifacts(project) -> None:
    """Scoping exists so callers do not collide; preservation must be per-caller.

    A predicate that happened to match only the first directory, or only the
    newest one, would satisfy the single-session test above and still lose data
    for every other agent on the box.
    """
    a = _prepare(project, "agent/a ..", "host-a")
    b = _prepare(project, "agent\\a ..", "host-b")
    assert a["bootstrap_path"] != b["bootstrap_path"]

    sm._reindex_from_disk(project)

    for result in (a, b):
        assert Path(result["bootstrap_path"]).is_file(), \
            "lost the bootstrap of %r" % result["bootstrap_path"]
        assert Path(result["workflow_state_path"]).is_file()


def test_generate_project_map_also_preserves_them(project) -> None:
    """The public entry point, not just the internal helper.

    ``generate_project_map`` is what callers and the periodic refresh actually
    invoke; it delegates to ``_reindex_from_disk`` today, but a test that only
    covers the helper would go quiet if that delegation changed.

    ``force=True`` is required and is the whole point of the mutation check that
    caught this: ``_project_map_build`` returns a "skipped" payload when the cache
    is still fresh, and ``prepare_ca_session`` has just made it fresh -- so the
    version of this test without ``force`` never reindexed at all and passed even
    with the fix reverted. A prune that never ran proves nothing about pruning.
    """
    result = _prepare(project, "agent-a", "host-a")
    bootstrap = Path(result["bootstrap_path"])
    assert bootstrap.is_file()

    payload = json.loads(asyncio.run(ct.generate_project_map(str(project), force=True)))
    assert payload.get("skipped") is False, (
        "the build was skipped, so nothing was pruned: %r" % payload)

    assert bootstrap.is_file(), "generate_project_map deleted a live bootstrap"


def test_one_agent_working_does_not_destroy_another_agents_session(project) -> None:
    """The production shape of the loss: a bystander loses its bootstrap.

    This is the case that hurts and the case that hides. When agent A calls
    anything that triggers a full refresh, the prune runs over the WHOLE cache --
    so agent B's bootstrap is deleted while B is still using it. B never observes
    the deletion at the moment it happens, and A sees nothing wrong at all.

    Deliberately NOT written as "prepare, reindex, prepare again": that version
    passes even with the fix reverted, because the second prepare simply REWRITES
    the file it lost. Verified against the reverted predicate -- the bootstrap was
    gone after the reindex and back after the re-prepare, so the assertions never
    saw the gap. Here nothing re-prepares for B, which is exactly the position a
    live session is in.
    """
    victim = _prepare(project, "agent-victim", "host-v")
    victim_bootstrap = Path(victim["bootstrap_path"])
    victim_state = Path(victim["workflow_state_path"])
    victim_body = victim_bootstrap.read_text(encoding="utf-8")

    # A second agent shows up and does ordinary work that reconciles the cache.
    _prepare(project, "agent-worker", "host-w")
    sm._reindex_from_disk(project)

    assert victim_bootstrap.is_file(), \
        "another agent's activity deleted a live session's bootstrap"
    assert victim_bootstrap.read_text(encoding="utf-8") == victim_body
    state = json.loads(victim_state.read_text(encoding="utf-8"))
    assert state["agent_id"] == "agent-victim"
    assert state["host_session_id"] == "host-v"


def test_the_a02_a30_pact_is_not_widened_by_this_fix() -> None:
    """Preserving by basename must not accidentally preserve the write ledger.

    ``write_backups.jsonl`` and ``.write_backups.seeded`` are deliberately NOT
    preserved and must share one fate -- keeping the marker while the ledger is
    wiped orphans a pending .bak and loses data (see the comment block in
    orchestrator/tools/write/core.py, pinned by test_a02_a30_interaction.py).
    Broadening the predicate is exactly the kind of change that could have caught
    them by accident, so assert it did not, at both depths.
    """
    for name in ("write_backups.jsonl", ".write_backups.seeded"):
        assert not sm._cache_path_is_preserved(name), name
        assert not sm._cache_path_is_preserved("sessions/agent-a-deadbeef/%s" % name), name
    # And ordinary cache entries must still be prunable, or reconciling external
    # deletes stops working.
    assert not sm._cache_path_is_preserved("app.py.json")
    assert not sm._cache_path_is_preserved("sub/mod.py.json")


def test_preservation_matches_the_basename_at_any_depth() -> None:
    """The predicate's contract, stated directly.

    Guards against a fix that only special-cases the literal string "sessions/":
    the names in PRESERVED_CACHE_FILES are runtime artifacts wherever they live,
    which is the same reasoning TT-A-04 already applied to SQLite sidecars.
    """
    assert sm._cache_path_is_preserved("CA_SESSION_BOOTSTRAP.md")
    assert sm._cache_path_is_preserved("sessions/a-1/CA_SESSION_BOOTSTRAP.md")
    assert sm._cache_path_is_preserved("some/other/depth/WORKFLOW_STATE.json")
    assert sm._cache_path_is_preserved("sessions/a-1/graph.db-wal")


def test_an_in_flight_atomic_temp_survives_a_reindex(project) -> None:
    """Third layer of the same bug, and the one the full suite caught.

    Preserving the FINAL name is not enough. ``_atomic_write_bytes`` writes
    ``.<name>.<random>.tmp`` beside the target and only then ``os.replace``s it,
    so a prune landing inside that window deletes a file the writer still holds a
    path to. That is not hypothetical: it is how
    ``test_prepare_ca_session_concurrent_artifacts_are_agent_scoped`` failed in the
    full suite with FileNotFoundError on the temp -- on the PermissionError
    fallback path, where the temp is read back rather than renamed.

    Asserted with a temp AND its final file present, because the bug is precisely
    that the second one was saved while the first was not.
    """
    session_dir = project / ".orchestrator_cache" / "sessions" / "agent-a-deadbeef"
    session_dir.mkdir(parents=True)
    in_flight = session_dir / ".CA_SESSION_BOOTSTRAP.md._t2t247a.tmp"
    in_flight.write_text("IN FLIGHT", encoding="utf-8")
    final = session_dir / "CA_SESSION_BOOTSTRAP.md"
    final.write_text("final", encoding="utf-8")

    sm._reindex_from_disk(project)

    assert in_flight.is_file(), "a reindex deleted an in-flight atomic temp"
    assert final.is_file()


def test_the_temp_rule_is_shaped_not_named(project) -> None:
    """It must cover every ``_atomic_write_*`` caller, not just session artifacts.

    The wrapped name is arbitrary -- ``_atomic_write_bytes`` is used for sidecars,
    snapshots and state files alike -- so matching on the temp SHAPE is what makes
    the guarantee general. A fix that special-cased CA_SESSION_BOOTSTRAP.md would
    pass the test above and leave every other in-flight write exposed.
    """
    assert sm._cache_path_is_preserved(".INDEX.json.9f1c.tmp")
    assert sm._cache_path_is_preserved("sessions/a-1/.anything.at.all.abc123.tmp")
    assert sm._cache_path_is_preserved("deep/nested/.WORKFLOW_STATE.json.ff.tmp")


def test_the_temp_rule_does_not_stop_the_prune_working(project) -> None:
    """Reconciling external deletes is the prune's job and must still happen.

    The rule is deliberately narrow: leading dot AND ``.tmp`` suffix, which is the
    shape ``tempfile.mkstemp(prefix=".<name>.", suffix=".tmp")`` produces. A plain
    ``scratch.tmp`` is not an in-flight write and stays prunable, and an orphaned
    sidecar must still be removed or ``cache_delta`` keeps reporting zero drift
    while stale entries shadow deleted modules.
    """
    assert not sm._cache_path_is_preserved("scratch.tmp")
    assert not sm._cache_path_is_preserved("sessions/a-1/notes.tmp")
    assert not sm._cache_path_is_preserved(".hidden.json")

    orphan = project / ".orchestrator_cache" / "gone.py.json"
    orphan.parent.mkdir(parents=True, exist_ok=True)
    orphan.write_text("{}", encoding="utf-8")
    sm._reindex_from_disk(project)
    assert not orphan.is_file(), "the prune stopped removing orphaned sidecars"
