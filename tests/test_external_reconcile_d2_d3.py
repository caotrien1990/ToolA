"""Cost and partial-snapshot contracts for external reconciliation."""
import asyncio
import json
from pathlib import Path
from unittest import mock

import pytest

import code_tools as ct
import surgical_mapper as sm
from orchestrator import search_snapshots


def read_index(root):
    return json.loads((root / ".orchestrator_cache" / "INDEX.json").read_text(encoding="utf-8"))


def test_warm_reconcile_does_not_rehash_unchanged_sources(tmp_path):
    for i in range(12):
        (tmp_path / f"file{i}.py").write_text(f"VALUE = {i}\n", encoding="utf-8")
    sm.generate_project_map(str(tmp_path))
    sm.clear_file_hash_sig_memo()
    index = read_index(tmp_path)
    with mock.patch.object(sm, "get_file_hash", wraps=sm.get_file_hash) as raw_hash:
        sm._reconcile_external_files(tmp_path, index, {})
        assert raw_hash.call_count == 12, "a cold process must measure each source once"
        raw_hash.reset_mock()
        sm._reconcile_external_files(tmp_path, index, {})
        assert raw_hash.call_count == 0, "an unchanged warm source must not be re-read"


@pytest.mark.parametrize("full_refresh", [False, True])
@pytest.mark.parametrize("poison_is_target", [False, True])
def test_capture_failure_is_omitted_persisted_and_recovered(
        tmp_path, full_refresh, poison_is_target):
    healthy = tmp_path / "healthy.py"
    poison = tmp_path / "poison.js"
    healthy.write_text("VALUE = 1\n", encoding="utf-8")
    poison.write_text("const oldSymbol = 1;\n", encoding="utf-8")
    # Deterministic parser-independent baseline, then fail this one capture.
    with mock.patch.object(sm, "TREE_SITTER_AVAILABLE", False):
        sm.generate_project_map(str(tmp_path))
    poison.write_text("", encoding="utf-8")
    real_analyze = sm.analyze_file

    def analyze(path, root):
        if Path(path) == poison:
            return {"file": "poison.js", "hash": sm.get_file_hash(str(poison)),
                    "error": True, "error_message": "injected parser failure"}
        return real_analyze(path, root)

    target = poison if poison_is_target else healthy
    with mock.patch.object(sm, "analyze_file", side_effect=analyze), \
            mock.patch.object(sm, "_snapshot_needs_full_refresh", return_value=full_refresh):
        for _ in range(2):
            metadata = sm.update_file_cache(str(target), str(tmp_path))
            assert metadata["snapshot_omitted_files"] == {"poison.js": "injected parser failure"}
            assert metadata["snapshot_id"]
            index = read_index(tmp_path)
            assert "poison.js" not in index["files"]
            assert index["snapshot_omitted_files"] == metadata["snapshot_omitted_files"]
            with search_snapshots.pin_current_snapshot(tmp_path) as pin:
                assert {r["path"] for r in pin.iter_records()} == {"healthy.py"}
                assert pin.read_bytes("healthy.py") == healthy.read_bytes()
    assert poison.exists() and poison.stat().st_size == 0, "quarantine never deletes source"
    # The same bytes can recover when a parser dependency is repaired.
    with mock.patch.object(sm, "TREE_SITTER_AVAILABLE", False), \
            mock.patch.object(sm, "_snapshot_needs_full_refresh", return_value=False):
        recovered = sm.update_file_cache(str(healthy), str(tmp_path))
    assert not recovered.get("snapshot_omitted_files")
    assert not read_index(tmp_path).get("snapshot_omitted_files")
    with search_snapshots.pin_current_snapshot(tmp_path) as pin:
        assert pin.read_bytes("poison.js") == b""


@pytest.mark.parametrize("tool", ["new", "fix"])
def test_write_api_discloses_partial_snapshot_without_failing_healthy_write(tmp_path, tool):
    poison = tmp_path / "poison.js"
    target = tmp_path / "healthy.py"
    poison.write_text("", encoding="utf-8")
    target.write_text("VALUE = 1\n", encoding="utf-8")
    with mock.patch.object(sm, "TREE_SITTER_AVAILABLE", False):
        sm.generate_project_map(str(tmp_path))
    poison.write_text("bad parser input\n", encoding="utf-8")
    real_analyze = sm.analyze_file

    def analyze(path, root):
        if Path(path) == poison:
            return {"file": "poison.js", "error": True,
                    "error_message": "injected parser failure"}
        return real_analyze(path, root)

    with mock.patch.object(sm, "analyze_file", side_effect=analyze), \
            mock.patch.object(sm, "_snapshot_needs_full_refresh", return_value=False):
        if tool == "new":
            result = json.loads(asyncio.run(ct.write_new_file(
                str(tmp_path / "new.py"), project_path=str(tmp_path),
                new_content="VALUE = 2\n")))
        else:
            read = json.loads(asyncio.run(ct.read_file_range(
                str(target), project_path=str(tmp_path), start_line=1, end_line=1)))
            assert read["ok"], read
            result = json.loads(asyncio.run(ct.write_fix(
                str(target), 1, 1, "VALUE = 2\n", project_path=str(tmp_path))))
    assert result["ok"], result
    assert result["cache_synced"], result
    assert result["cache_sync_status"] == "partial"
    assert result["snapshot_omitted_files"] == {"poison.js": "injected parser failure"}
    assert "cache_sync_warning" in result


def test_first_capture_failure_publishes_empty_but_explicit_partial_snapshot(tmp_path):
    target = tmp_path / "poison.js"
    target.write_text("", encoding="utf-8")
    with mock.patch.object(sm, "analyze_file", return_value={
            "file": "poison.js", "error": True, "error_message": "parser unavailable"}):
        metadata = sm.update_file_cache(str(target), str(tmp_path))
    assert metadata["snapshot_id"]
    assert metadata["snapshot_omitted_files"] == {"poison.js": "parser unavailable"}
    with search_snapshots.pin_current_snapshot(tmp_path) as pin:
        assert list(pin.iter_records()) == []
    assert target.exists()


def test_unexpected_cache_failure_reports_root_cause_and_stays_false(tmp_path, caplog):
    target = tmp_path / "healthy.py"
    target.write_text("VALUE = 1\n", encoding="utf-8")
    diagnostics = {}
    with mock.patch.object(sm, "update_file_cache", side_effect=OSError("cache disk full")):
        assert not ct._update_cache_after_write(str(target), str(tmp_path), diagnostics)
    assert diagnostics["cache_sync_error"]["type"] == "OSError"
    assert diagnostics["cache_sync_error"]["message"] == "cache disk full"
    assert "cache disk full" in caplog.text
