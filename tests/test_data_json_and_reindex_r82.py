"""R82/R84: data-JSON exclusion + non-destructive reindex (ToolQ INDEX starvation)."""
import json
import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import surgical_mapper as sm


def _mk(root: Path, rel: str, body: str = "{}") -> Path:
    p = root / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(body, encoding="utf-8")
    return p


class TestDataJsonExclusion:
    def test_deep_unknown_json_is_data(self, tmp_path):
        f = _mk(tmp_path, "qsleep/runs/2026/08/job_1.json")
        assert sm._should_scan_file(f, tmp_path) is False
        assert sm._skip_reason(f, tmp_path) == "data_json_not_source"

    def test_jsonl_is_always_data(self, tmp_path):
        f = _mk(tmp_path, "queue_item.jsonl", '{"a":1}\n')
        assert sm._should_scan_file(f, tmp_path) is False

    def test_known_config_survives_at_any_depth(self, tmp_path):
        for rel in ("package.json", "a/b/c/d/e/package.json", "x/y/z/w/tsconfig.json"):
            f = _mk(tmp_path, rel)
            assert sm._should_scan_file(f, tmp_path) is True, rel

    def test_config_shaped_name_survives(self, tmp_path):
        for rel in ("a/b/c/d/app_config.json", "a/b/c/d/e/user_settings.json",
                    "deep/deep/deep/deep/my_schema.json"):
            f = _mk(tmp_path, rel)
            assert sm._should_scan_file(f, tmp_path) is True, rel

    def test_shallow_unknown_json_survives(self, tmp_path):
        f = _mk(tmp_path, "data.json")
        assert sm._should_scan_file(f, tmp_path) is True

    def test_unknown_root_keeps_the_file(self, tmp_path):
        # depth unknown -> conservative: index it rather than silently drop it
        f = _mk(tmp_path, "a/b/c/d/e/whatever.json")
        assert sm._should_scan_file(f, None) is True

    def test_python_is_never_data(self, tmp_path):
        f = _mk(tmp_path, "a/b/c/d/e/f/deep.py", "x = 1\n")
        assert sm._should_scan_file(f, tmp_path) is True

    def test_runtime_dirs_are_skipped(self):
        for name in ("runs", "queue", "archive", "_archive"):
            assert sm.should_skip_dir(name) is True


class TestReindexIsNonDestructive:
    def test_partial_failure_leaves_the_old_cache(self, tmp_path, monkeypatch):
        _mk(tmp_path, "a.py", "A = 1\n")
        _mk(tmp_path, "b.py", "B = 2\n")
        sm.generate_project_map(str(tmp_path))
        cache = tmp_path / sm.CACHE_DIR_NAME
        before = {p.name for p in cache.rglob("*.json")}
        assert {"a.py.json", "b.py.json"} <= before

        real = sm.write_file_cache
        calls = {"n": 0}

        def boom(file_path, project_root):
            calls["n"] += 1
            if calls["n"] > 1:
                raise RuntimeError("simulated deadline")
            return real(file_path, project_root)

        monkeypatch.setattr(sm, "write_file_cache", boom)
        with pytest.raises(RuntimeError):
            sm._reindex_from_disk(tmp_path)

        after = {p.name for p in cache.rglob("*.json")}
        # the whole point of R84: a crashed rebuild must not have emptied the cache
        assert {"a.py.json", "b.py.json"} <= after
        assert (cache / "INDEX.json").exists()

    def test_deleted_source_is_still_reconciled(self, tmp_path):
        _mk(tmp_path, "keep.py", "K = 1\n")
        gone = _mk(tmp_path, "gone.py", "G = 1\n")
        sm.generate_project_map(str(tmp_path))
        cache = tmp_path / sm.CACHE_DIR_NAME
        assert (cache / "gone.py.json").exists()

        gone.unlink()
        sm._reindex_from_disk(tmp_path)
        assert not (cache / "gone.py.json").exists()
        assert (cache / "keep.py.json").exists()
        idx = json.loads((cache / "INDEX.json").read_text(encoding="utf-8"))
        assert "gone.py" not in idx["files"]
        assert "keep.py" in idx["files"]

    def test_data_json_does_not_reach_the_index(self, tmp_path):
        _mk(tmp_path, "src.py", "S = 1\n")
        _mk(tmp_path, "deep/nest/more/rec_001.json", '{"r":1}')
        _mk(tmp_path, "package.json", '{"name":"x"}')
        sm.generate_project_map(str(tmp_path))
        idx = json.loads((tmp_path / sm.CACHE_DIR_NAME / "INDEX.json").read_text(encoding="utf-8"))
        assert "src.py" in idx["files"]
        assert "package.json" in idx["files"]
        assert "deep/nest/more/rec_001.json" not in idx["files"]

    def test_runtime_tree_costs_nothing(self, tmp_path):
        _mk(tmp_path, "app.py", "A = 1\n")
        for i in range(60):
            _mk(tmp_path, f"qsleep/runs/r{i}.json", '{"i":%d}' % i)
        sm.generate_project_map(str(tmp_path))
        idx = json.loads((tmp_path / sm.CACHE_DIR_NAME / "INDEX.json").read_text(encoding="utf-8"))
        assert len(idx["files"]) == 1
        assert "app.py" in idx["files"]
