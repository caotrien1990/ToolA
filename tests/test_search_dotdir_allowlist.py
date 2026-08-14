"""SRCH: .council is searchable; other dot-dirs stay skipped."""
from __future__ import annotations

import asyncio
import json
import tempfile
import unittest
from pathlib import Path

import code_tools
import surgical_mapper
from orchestrator.fileops.common import should_skip_dir
from orchestrator.search_snapshots import pin_current_snapshot, publish_snapshot
from orchestrator.tools.search import search_code


class TestDotDirSearchAllowlist(unittest.TestCase):
    def test_should_skip_dir_allows_council_only(self):
        self.assertFalse(should_skip_dir(".council"))
        self.assertFalse(should_skip_dir(".Council"))
        self.assertFalse(surgical_mapper.should_skip_dir(".council"))
        self.assertTrue(should_skip_dir(".git"))
        self.assertTrue(should_skip_dir(".orchestrator_cache"))
        self.assertTrue(should_skip_dir(".venv"))
        self.assertTrue(should_skip_dir("venv312"))
        self.assertTrue(should_skip_dir("_orchestrator_backups"))
        self.assertTrue(should_skip_dir(".BUILDING PLACE"))

    def test_search_code_finds_council_config_skips_git(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            council = root / ".council"
            council.mkdir()
            (council / "council_config.json").write_text(
                '{\n  "model": "xAI-grok-4.6-build",\n  "argv": "grok.exe"\n}\n',
                encoding="utf-8",
            )
            git = root / ".git"
            git.mkdir()
            (git / "council_config.json").write_text(
                '{\n  "model": "xAI-grok-4.6-build"\n}\n',
                encoding="utf-8",
            )
            cache = root / ".orchestrator_cache"
            cache.mkdir()
            (cache / "council_config.json").write_text(
                '{\n  "model": "xAI-grok-4.6-build"\n}\n',
                encoding="utf-8",
            )
            (root / "readme.md").write_text("no marker here\n", encoding="utf-8")

            raw = asyncio.run(search_code(
                query="xAI-grok-4.6-build",
                project_path=str(root),
                file_pattern="council_config.json",
                mode="text",
            ))
            data = json.loads(raw)
            self.assertNotIn(data.get("error_name"), {"REGEX_SEARCH_TIMEOUT"}, data)
            self.assertGreaterEqual(int(data.get("count") or 0), 1, data)
            files = [str(r.get("file", "")).replace("\\", "/") for r in data.get("results", [])]
            self.assertTrue(
                any(f == ".council/council_config.json" or f.endswith("/.council/council_config.json") for f in files),
                files,
            )
            self.assertFalse(any(".git/" in f or f.startswith(".git/") for f in files), files)
            self.assertFalse(any(".orchestrator_cache" in f for f in files), files)

    def test_project_map_indexes_council_not_git(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "sample.py").write_text("def foo():\n    return 1\n", encoding="utf-8")
            council = root / ".council"
            council.mkdir()
            (council / "council_config.json").write_text('{"k": 1}\n', encoding="utf-8")
            git = root / ".git"
            git.mkdir()
            (git / "config.json").write_text("{}\n", encoding="utf-8")

            result = json.loads(asyncio.run(code_tools.generate_project_map(str(root))))
            self.assertIn("files_indexed", result, result)
            index = json.loads((root / ".orchestrator_cache" / "INDEX.json").read_text(encoding="utf-8"))
            keys = {str(k).replace("\\", "/") for k in index.get("files", {})}
            self.assertIn(".council/council_config.json", keys, keys)
            self.assertFalse(any(k.startswith(".git/") or "/.git/" in k for k in keys), keys)

    def test_cache_delta_reports_new_council_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "sample.py").write_text("def foo():\n    return 1\n", encoding="utf-8")
            asyncio.run(code_tools.generate_project_map(str(root)))
            council = root / ".council"
            council.mkdir()
            (council / "council_config.json").write_text('{"k": 2}\n', encoding="utf-8")
            delta = json.loads(asyncio.run(code_tools.cache_delta(str(root))))
            added = [str(p).replace("\\", "/") for p in delta.get("added", [])]
            self.assertIn(".council/council_config.json", added, delta)

    def test_search_snapshots_default_skip_allows_council(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            council = root / ".council"
            council.mkdir()
            (council / "council_config.json").write_text(
                '{"model": "xAI-grok-4.6-build"}\n',
                encoding="utf-8",
            )
            (root / "ok.txt").write_text("hello\n", encoding="utf-8")
            sid = publish_snapshot(root)
            pin = pin_current_snapshot(root, expected_id=sid)
            try:
                paths = [str(r["path"]).replace("\\", "/") for r in pin.iter_records(root)]
            finally:
                pin.release()
            self.assertTrue(
                any(p == ".council/council_config.json" or p.endswith("/.council/council_config.json") for p in paths),
                paths,
            )
