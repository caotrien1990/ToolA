"""Path-scoped file_pattern must use the documented basename-or-relative-path contract."""
from __future__ import annotations

import asyncio
import json
from pathlib import Path

import surgical_mapper as sm
from orchestrator.tools.search import search_code


def _search(root: Path, query: str, file_pattern: str, mode: str = "text") -> dict:
    return json.loads(asyncio.run(search_code(
        query=query,
        project_path=str(root),
        file_pattern=file_pattern,
        mode=mode,
    )))


def _repo(root: Path) -> None:
    target = root / "bin" / "clm_lib" / "routes_read.py"
    target.parent.mkdir(parents=True)
    target.write_text("budget_seconds = 30\n", encoding="utf-8")
    (root / "bin" / "worker.py").write_text("busy_timeout=30000\n", encoding="utf-8")
    (root / "other.py").write_text("busy_timeout=30000\n", encoding="utf-8")
    archived = root / "archive_copy" / "bin" / "clm_lib" / "routes_read.py"
    archived.parent.mkdir(parents=True)
    archived.write_text("budget_seconds = 30\n", encoding="utf-8")
    sm.generate_project_map(str(root))


def test_exact_relative_path_matches(tmp_path):
    _repo(tmp_path)

    result = _search(tmp_path, "budget_seconds", "bin/clm_lib/routes_read.py")

    assert result["count"] == 1, result
    assert result["results"][0]["file"] == "bin/clm_lib/routes_read.py"


def test_relative_path_does_not_suffix_match_archived_copy(tmp_path):
    _repo(tmp_path)

    result = _search(tmp_path, "budget_seconds", "bin/clm_lib/routes_read.py")

    assert [row["file"] for row in result["results"]] == [
        "bin/clm_lib/routes_read.py"
    ], result


def test_relative_path_glob_matches_only_that_subtree(tmp_path):
    _repo(tmp_path)

    result = _search(tmp_path, "busy_timeout=30000", "bin/*.py")

    assert result["count"] == 1, result
    assert result["results"][0]["file"] == "bin/worker.py"


def test_basename_pattern_remains_supported(tmp_path):
    _repo(tmp_path)

    result = _search(tmp_path, "budget_seconds", "routes_read.py")

    assert result["count"] == 2, result
    assert {row["file"] for row in result["results"]} == {
        "bin/clm_lib/routes_read.py",
        "archive_copy/bin/clm_lib/routes_read.py",
    }


def test_windows_separator_path_pattern_is_normalized(tmp_path):
    _repo(tmp_path)

    result = _search(tmp_path, "budget_seconds", r"bin\clm_lib\routes_read.py")

    assert result["count"] == 1, result


def test_regex_mode_uses_same_path_contract(tmp_path):
    _repo(tmp_path)

    result = _search(
        tmp_path,
        r"budget_seconds\s*=\s*30",
        "bin/clm_lib/routes_read.py",
        mode="regex",
    )

    assert result["count"] == 1, result
