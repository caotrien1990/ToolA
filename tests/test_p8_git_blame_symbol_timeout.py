"""P8 regression: git_blame_symbol must use the git tools standard 60s budget.

git_blame_symbol (tools_v15.py) hardcoded timeout=20 while every other git
path goes through orchestrator/git_tools.py::_run (default timeout=60).
Pattern A: budget inconsistency only -- the tool already reported timeouts
loudly via GIT_BLAME_FAIL. This test pins the budget at 60s by mocking
subprocess.run and asserting the timeout kwarg actually passed.
"""
import asyncio
import json
import subprocess
from unittest import mock

import tools_v15 as tv


def _fake_symbol_payload(name="my_func"):
    return json.dumps({
        "ok": True,
        "results": [{"file": "mod.py", "line": 10, "name": name}],
    })


def _run_blame(captured, stdout="abc1234\tJane\t2026-01-02\tinit\n"):
    def fake_run(cmd, **kwargs):
        captured["timeout"] = kwargs.get("timeout")
        captured["cmd"] = cmd
        m = mock.Mock()
        m.returncode = 0
        m.stdout = stdout
        m.stderr = ""
        return m

    # git_blame_symbol does `from code_tools import find_symbol` at call time,
    # so patching code_tools.find_symbol works; it calls the global
    # subprocess.run, so patching subprocess.run covers the threaded _git_log.
    with mock.patch("code_tools.find_symbol",
                    return_value=_fake_symbol_payload()), \
         mock.patch.object(subprocess, "run", side_effect=fake_run):
        return asyncio.run(tv.git_blame_symbol("my_func", project_path="D:/proj"))


def test_p8_git_blame_symbol_passes_60s_timeout():
    captured = {}
    _run_blame(captured)
    assert captured["timeout"] == 60, (
        "P8: git_blame_symbol must use the 60s git budget, got %r"
        % (captured.get("timeout"),)
    )
    assert captured["cmd"][0] == "git"


def test_p8_git_blame_symbol_still_parses_commits():
    captured = {}
    out = _run_blame(captured)
    data = json.loads(out)
    assert data["ok"] is True
    assert data["commits"] == [
        {"hash": "abc1234", "author": "Jane",
         "date": "2026-01-02", "subject": "init"}
    ]
    assert data["commit_count"] == 1
