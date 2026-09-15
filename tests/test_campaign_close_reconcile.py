"""Campaign closure: disk reconciliation and immutable recovery invariants."""
import os
from unittest import mock

import pytest
import surgical_mapper as sm
from orchestrator import search_snapshots as ss


def test_equal_stat_edit_add_remove_and_old_reader(tmp_path):
    root = tmp_path / "project"
    root.mkdir()
    keep = root / "keep.py"
    target = root / "target.py"
    gone = root / "gone.py"
    keep.write_text("keep = 1\n")
    target.write_text("value = 'OLD'\n")
    gone.write_text("gone = 1\n")
    sm.generate_project_map(str(root))
    with ss.pin_current_snapshot(root) as old:
        stat = target.stat()
        target.write_text("value = 'NEW'\n")
        os.utime(target, ns=(stat.st_atime_ns, stat.st_mtime_ns))
        gone.rename(root / "added.py")
        sm.update_file_cache(str(keep), str(root))
        assert b"OLD" in old.read_bytes("target.py")
        with ss.pin_current_snapshot(root) as current:
            assert b"NEW" in current.read_bytes("target.py")
            paths = {r["path"] for r in current.manifest["files"]}
            assert "gone.py" not in paths
            assert "added.py" in paths
            assert current.manifest["index_authoritative"] is True


def test_failed_reconcile_preserves_last_snapshot_and_retry_recovers(tmp_path):
    root = tmp_path / "project"
    root.mkdir()
    keep = root / "keep.py"
    target = root / "target.py"
    keep.write_text("keep = 1\n")
    target.write_text("old = 1\n")
    sm.generate_project_map(str(root))
    before = ss.current_snapshot_id(root)
    target.write_text("new = 2\n")
    real = sm.write_file_cache

    def fail(path, project):
        if path == str(target):
            raise OSError("injected read failure")
        return real(path, project)

    with mock.patch.object(sm, "write_file_cache", side_effect=fail):
        with pytest.raises(OSError, match="injected"):
            sm.update_file_cache(str(keep), str(root))
    assert ss.current_snapshot_id(root) == before
    sm.update_file_cache(str(keep), str(root))
    with ss.pin_current_snapshot(root) as current:
        assert b"new = 2" in current.read_bytes("target.py")