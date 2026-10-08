"""Quarantine must survive rebuild/fallback and clear after source removal."""
from pathlib import Path
from unittest import mock

import surgical_mapper as sm
from orchestrator import search_snapshots as ss


def test_summary_and_pinned_index_disclose_omissions_then_clear_deleted_source(tmp_path):
    healthy = tmp_path / "healthy.py"
    poison = tmp_path / "poison.js"
    healthy.write_text("VALUE = 1\n", encoding="utf-8")
    poison.write_text("", encoding="utf-8")
    with mock.patch.object(sm, "TREE_SITTER_AVAILABLE", True), \
            mock.patch.object(sm, "_extract_symbols_with_parser", side_effect=TypeError("parser broken")):
        summary = sm.generate_project_map(str(tmp_path))
    assert summary["snapshot_omitted_files"] == {"poison.js": "parser broken"}
    with ss.pin_current_snapshot(tmp_path) as pin:
        assert pin.read_index()["snapshot_omitted_files"] == summary["snapshot_omitted_files"]
    poison.unlink()
    result = sm.update_file_cache(str(healthy), str(tmp_path))
    assert not result.get("snapshot_omitted_files")
    with ss.pin_current_snapshot(tmp_path) as pin:
        assert not pin.read_index().get("snapshot_omitted_files")
        assert {r["path"] for r in pin.iter_records()} == {"healthy.py"}


def test_full_walk_fallback_does_not_reintroduce_quarantined_source(tmp_path):
    healthy = tmp_path / "healthy.py"
    (tmp_path / "poison.js").write_text("", encoding="utf-8")
    healthy.write_text("VALUE = 1\n", encoding="utf-8")
    with mock.patch.object(sm, "TREE_SITTER_AVAILABLE", True), \
            mock.patch.object(sm, "_extract_symbols_with_parser", side_effect=TypeError("parser broken")):
        sm.generate_project_map(str(tmp_path))
    with ss.pin_current_snapshot(tmp_path) as pin:
        index = pin.read_index()
    real_publish = ss.publish_snapshot
    calls = []

    def publish(root, **kwargs):
        calls.append(kwargs["changed_paths"])
        if len(calls) == 1:
            raise ss.SnapshotIntegrityError("force full-walk fallback")
        return real_publish(root, **kwargs)

    with mock.patch.object(ss, "publish_snapshot", side_effect=publish):
        assert sm._publish_search_snapshot(tmp_path, index, {"healthy.py": "modified"})
    assert calls == [{"healthy.py": "modified"}, None]
    with ss.pin_current_snapshot(tmp_path) as pin:
        assert {r["path"] for r in pin.iter_records()} == {"healthy.py"}
        assert pin.read_index()["snapshot_omitted_files"] == {"poison.js": "parser broken"}


def test_source_hash_io_error_is_quarantined_without_hiding_cache_storage_errors(tmp_path):
    healthy = tmp_path / "healthy.py"
    poison = tmp_path / "unreadable.py"
    healthy.write_text("VALUE = 1\n", encoding="utf-8")
    poison.write_text("VALUE = 2\n", encoding="utf-8")
    sm.generate_project_map(str(tmp_path))
    real_hash = sm.get_file_hash

    def file_hash(path):
        if Path(path) == poison:
            raise PermissionError("source cannot be read")
        return real_hash(path)

    sm.clear_file_hash_sig_memo()
    with mock.patch.object(sm, "get_file_hash", side_effect=file_hash):
        result = sm.update_file_cache(str(healthy), str(tmp_path))
    assert result["snapshot_omitted_files"] == {"unreadable.py": "source cannot be read"}
    with ss.pin_current_snapshot(tmp_path) as pin:
        assert {r["path"] for r in pin.iter_records()} == {"healthy.py"}
    assert poison.exists()
