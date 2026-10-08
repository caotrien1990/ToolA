"""Outside-project writes must not enroll an ancestor repository in cache sync."""
import asyncio
import json
import os
from pathlib import Path
from unittest import mock

import pytest

import code_tools as ct
import surgical_mapper as sm
from orchestrator import search_snapshots
from orchestrator.tools.write.core import write_new_file


@pytest.mark.parametrize("project_kind", ["omitted", "empty", "outside", "inside"])
def test_cache_root_is_explicit_containing_project_or_file_parent(tmp_path, project_kind):
    ancestor = tmp_path / "ancestor"
    target = ancestor / "nested" / "target.py"
    target.parent.mkdir(parents=True)
    (ancestor / ".git").mkdir()
    target.write_text("VALUE = 1\n", encoding="utf-8")
    other = tmp_path / "other"
    other.mkdir()
    project = {"omitted": None, "empty": "", "outside": str(other),
               "inside": str(ancestor)}[project_kind]
    expected = ancestor if project_kind == "inside" else target.parent
    with mock.patch.object(sm, "update_file_cache") as update:
        assert ct._update_cache_after_write(str(target), project)
    assert Path(update.call_args.args[1]) == expected.resolve()


@pytest.mark.parametrize("explicit_other", [False, True])
@pytest.mark.parametrize("full_refresh", [False, True])
def test_reconcile_and_snapshot_stay_inside_external_file_parent(
        tmp_path, explicit_other, full_refresh):
    ancestor = tmp_path / "ancestor"
    parent = ancestor / "target_dir"
    parent.mkdir(parents=True)
    (ancestor / ".git").mkdir()
    (ancestor / "unrelated.py").write_text("UNRELATED = 1\n", encoding="utf-8")
    target = parent / "target.py"
    sibling = parent / "sibling.py"
    target.write_text("VALUE = 1\n", encoding="utf-8")
    sibling.write_text("SIBLING = 1\n", encoding="utf-8")
    # Both stores exist: a parent marker alone must not make the ancestor safe.
    sm.generate_project_map(str(ancestor))
    sm.generate_project_map(str(parent))
    ancestor_index = ancestor / ".orchestrator_cache" / "INDEX.json"
    before = ancestor_index.read_bytes()
    target.write_text("VALUE = 2\n", encoding="utf-8")
    sibling.write_text("SIBLING = 2\n", encoding="utf-8")
    # An unrelated corrupt input must never enter this write's capture path.
    poison = ancestor / "poison.py"
    poison.write_text("def broken(\n", encoding="utf-8")
    other = tmp_path / "other"
    other.mkdir()
    walked, hashed = [], []
    real_walk, real_hash = os.walk, sm.get_file_hash

    def scoped_walk(top, *args, **kwargs):
        resolved = Path(top).resolve()
        walked.append(resolved)
        assert resolved.is_relative_to(parent), f"escaped walk: {resolved}"
        yield from real_walk(top, *args, **kwargs)

    def scoped_hash(path, *args, **kwargs):
        resolved = Path(path).resolve()
        hashed.append(resolved)
        assert resolved.is_relative_to(parent), f"escaped hash: {resolved}"
        return real_hash(path, *args, **kwargs)

    with mock.patch.object(sm, "_snapshot_needs_full_refresh", return_value=full_refresh), \
            mock.patch.object(sm.os, "walk", side_effect=scoped_walk), \
            mock.patch.object(sm, "get_file_hash", side_effect=scoped_hash):
        assert ct._update_cache_after_write(str(target), str(other) if explicit_other else "")
    assert walked, "the real reconcile/refresh must run"
    assert hashed
    assert ancestor_index.read_bytes() == before
    with search_snapshots.pin_current_snapshot(parent) as pin:
        assert pin.read_bytes("target.py") == target.read_bytes()
        assert pin.read_bytes("sibling.py") == sibling.read_bytes()
        assert "poison.py" not in {r["path"] for r in pin.iter_records()}


def test_in_project_equal_stat_drift_still_reconciles_across_directories(tmp_path):
    root = tmp_path / "project"
    (root / "a").mkdir(parents=True)
    (root / "b").mkdir()
    target = root / "a" / "target.py"
    sibling = root / "b" / "sibling.py"
    target.write_text("VALUE = 1\n", encoding="utf-8")
    sibling.write_text("SIBLING = 1\n", encoding="utf-8")
    sm.generate_project_map(str(root))
    old_stat = sibling.stat()
    sibling.write_text("SIBLING = 2\n", encoding="utf-8")
    os.utime(sibling, ns=(old_stat.st_atime_ns, old_stat.st_mtime_ns))
    target.write_text("VALUE = 2\n", encoding="utf-8")
    with mock.patch.object(sm, "_snapshot_needs_full_refresh", return_value=False):
        assert ct._update_cache_after_write(str(target), str(root))
    with search_snapshots.pin_current_snapshot(root) as pin:
        assert pin.read_bytes("b/sibling.py") == sibling.read_bytes()


@pytest.mark.parametrize("explicit_other", [False, True])
def test_write_new_file_outside_project_publishes_only_local_index(tmp_path, explicit_other):
    ancestor = tmp_path / "ancestor"
    parent = ancestor / "target_dir"
    parent.mkdir(parents=True)
    (ancestor / ".git").mkdir()
    (ancestor / "unrelated.py").write_text("UNRELATED = 1\n", encoding="utf-8")
    sm.generate_project_map(str(ancestor))
    ancestor_index = ancestor / ".orchestrator_cache" / "INDEX.json"
    before = ancestor_index.read_bytes()
    other = tmp_path / "other"
    other.mkdir()
    for i in range(2):
        target = parent / f"new{i}.py"
        result = json.loads(asyncio.run(write_new_file(
            str(target), new_content=f"VALUE = {i}\n",
            project_path=str(other) if explicit_other else "")))
        assert result["ok"], result
        assert result["cache_synced"], result
    assert ancestor_index.read_bytes() == before
    assert not (other / ".orchestrator_cache" / "INDEX.json").exists()
    target = parent / "new1.py"
    read = json.loads(asyncio.run(ct.read_file_range(
        file_path=str(target), project_path=str(parent), start_line=1, end_line=1)))
    assert read["ok"], read
    fixed = json.loads(asyncio.run(ct.write_fix(
        file_path=str(target), project_path=str(other) if explicit_other else "",
        start_line=1, end_line=1, new_content="VALUE = 3\n")))
    assert fixed["ok"], fixed
    assert fixed["cache_synced"], fixed
    assert ancestor_index.read_bytes() == before
    with search_snapshots.pin_current_snapshot(parent) as pin:
        assert pin.read_bytes("new1.py") == b"VALUE = 3\n"
