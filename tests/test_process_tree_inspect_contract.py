import asyncio
import json
import os
import subprocess
import sys
from unittest import mock

import pytest

import orchestrator_server as srv
import tools_v15


def test_schema_allows_root_pid_without_name_pattern():
    schema = srv._tool_schema_payload("process_tree_inspect")[0]["tool"]["function"]
    assert schema["parameters"]["required"] == []
    assert "cmdline" in schema["description"]


def test_root_pid_and_empty_domain_contract():
    root = json.loads(asyncio.run(tools_v15.process_tree_inspect(root_pid=os.getpid())))
    assert root["ok"] and root["tree"]["pid"] == os.getpid()
    empty = json.loads(asyncio.run(tools_v15.process_tree_inspect()))
    assert not empty["ok"] and empty["error_name"] == "MISSING_REQUIRED_FIELD"
    assert "root_pid" in empty["accepted_fields"]
    assert len(empty["payload_examples"]) == 2


def test_dispatcher_root_pid_reaches_handler():
    response = asyncio.run(srv.dispatch_tool_call(
        "process_tree_inspect", {"root_pid": os.getpid(), "max_depth": 0},
        transport="native"))
    payload = json.loads(response.body)
    assert payload["ok"] and payload["tree"]["pid"] == os.getpid()
    assert payload.get("error_name") != "MISSING_REQUIRED_FIELD"


def test_cmdline_only_marker_and_case_insensitive_match(tmp_path):
    marker = "PTI_CmdLine_Only_Marker_1509"
    proc = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)", marker])
    try:
        result = json.loads(asyncio.run(tools_v15.process_tree_inspect(
            name_pattern=marker.swapcase(), max_depth=0)))
        assert result["ok"] and any(x.get("pid") == proc.pid for x in result["trees"])
    finally:
        proc.terminate()
        proc.wait(timeout=10)


def test_process_iteration_races_fail_open(monkeypatch):
    psutil = pytest.importorskip("psutil")

    class Bad:
        info = {"pid": 1, "name": "", "cmdline": []}

        def __getattribute__(self, name):
            if name == "info":
                raise psutil.NoSuchProcess(1)
            return object.__getattribute__(self, name)

    monkeypatch.setattr(psutil, "process_iter", lambda attrs: [Bad()])
    result = json.loads(asyncio.run(tools_v15.process_tree_inspect(name_pattern="missing")))
    assert result["ok"] and result["count"] == 0


def test_name_match_cap_is_ten(monkeypatch):
    psutil = pytest.importorskip("psutil")
    rows = [mock.Mock(info={"pid": i + 1, "name": "match.exe", "cmdline": []})
            for i in range(15)]
    monkeypatch.setattr(psutil, "process_iter", lambda attrs: rows)
    with mock.patch.object(psutil, "Process") as process:
        process.side_effect = psutil.NoSuchProcess(0)
        result = json.loads(asyncio.run(tools_v15.process_tree_inspect(name_pattern="match")))
    assert result["count"] == 10
