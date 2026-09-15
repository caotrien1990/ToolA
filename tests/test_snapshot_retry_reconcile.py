"""INDEX ahead of CURRENT after a failed publish must recover on the next write."""
import os
from unittest import mock

import pytest

import surgical_mapper as sm
from orchestrator import search_snapshots as ss


def seed(root):
    (root / "keep.py").write_bytes(b"keep = 1\n")
    (root / "target.py").write_bytes(b"old = 1\n")
    (root / "gone.py").write_bytes(b"gone = 1\n")
    sm.generate_project_map(str(root))


def test_retry_captures_index_changes_from_failed_publish(tmp_path):
    seed(tmp_path)
    before = ss.current_snapshot_id(tmp_path)
    with ss.pin_current_snapshot(tmp_path) as old:
        (tmp_path / "target.py").write_bytes(b"new = 2\n")
        (tmp_path / "gone.py").rename(tmp_path / "added.py")
        with mock.patch.object(sm, "_publish_search_snapshot",
                               side_effect=ss.SnapshotDeadlineExceeded("injected")):
            with pytest.raises(ss.SnapshotDeadlineExceeded):
                sm.update_file_cache(str(tmp_path / "keep.py"), str(tmp_path))
        assert ss.current_snapshot_id(tmp_path) == before
        # Disk and INDEX now agree; CURRENT still has old/deleted content.
        with mock.patch.object(sm, "_reindex_from_disk", side_effect=AssertionError("no full rebuild")):
            result = sm.update_file_cache(str(tmp_path / "keep.py"), str(tmp_path))
        assert result["snapshot_id"] != before
        assert old.read_bytes("target.py") == b"old = 1\n"
        with ss.pin_current_snapshot(tmp_path) as current:
            assert current.read_bytes("target.py") == b"new = 2\n"
            assert current.read_bytes("added.py") == b"gone = 1\n"
            assert "gone.py" not in current.read_index()["files"]
            assert current.manifest["index_authoritative"] is True


def test_same_content_mtime_change_reconciles(tmp_path):
    seed(tmp_path)
    target = tmp_path / "target.py"
    stat = target.stat()
    os.utime(target, ns=(stat.st_atime_ns, stat.st_mtime_ns + 2_000_000_000))
    sm.update_file_cache(str(tmp_path / "keep.py"), str(tmp_path))
    with ss.pin_current_snapshot(tmp_path) as current:
        rec = next(r for r in current.manifest["files"] if r["path"] == "target.py")
        assert rec["mtime"] == target.stat().st_mtime
        assert current.read_index()["files"]["target.py"]["mtime"] == rec["mtime"]


def test_repeated_failed_publish_keeps_recoverable_current(tmp_path):
    seed(tmp_path)
    before = ss.current_snapshot_id(tmp_path)
    (tmp_path / "added.py").write_bytes(b"added = 1\n")
    for _ in range(2):
        with mock.patch.object(sm, "_publish_search_snapshot",
                               side_effect=ss.SnapshotDeadlineExceeded("injected")):
            with pytest.raises(ss.SnapshotDeadlineExceeded):
                sm.update_file_cache(str(tmp_path / "keep.py"), str(tmp_path))
        assert ss.current_snapshot_id(tmp_path) == before
    sm.update_file_cache(str(tmp_path / "keep.py"), str(tmp_path))
    with ss.pin_current_snapshot(tmp_path) as current:
        assert current.read_bytes("added.py") == b"added = 1\n"
