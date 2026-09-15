import asyncio
import json
import os
import sys
from pathlib import Path
from unittest import mock

import pytest

import code_tools


@pytest.mark.skipif(sys.platform != "win32", reason="Windows command path contract")
def test_keep_alive_unquoted_absolute_path_is_rejected_before_spawn(tmp_path):
    command = r"C:\Program Files\missing-tool-for-test.bat --serve"
    with mock.patch.object(code_tools.subprocess, "Popen") as popen:
        result = json.loads(asyncio.run(code_tools.run_project_command(
            str(tmp_path), command, shell_mode="cmd", keep_alive=True, background=True)))
    popen.assert_not_called()
    assert result["error_name"] == "PATH_QUOTING_ERROR"
    assert result["spawn_attempted"] is False
    assert result["keep_alive"] is True


@pytest.mark.skipif(sys.platform != "win32", reason="Windows command path contract")
def test_quoted_path_is_not_rejected_by_preflight(tmp_path):
    command = r'"C:\Program Files\missing-tool-for-test.bat" --serve'
    with mock.patch.object(code_tools.subprocess, "Popen", side_effect=FileNotFoundError("fixture")) as popen:
        result = json.loads(asyncio.run(code_tools.run_project_command(
            str(tmp_path), command, shell_mode="cmd", keep_alive=True, background=True)))
    popen.assert_called_once()
    assert result.get("error_name") != "PATH_QUOTING_ERROR"


@pytest.mark.skipif(sys.platform != "win32", reason="Windows command path contract")
def test_spaced_arguments_do_not_trigger_path_preflight(tmp_path):
    command = 'cmd.exe /c echo "argument with spaces"'
    with mock.patch.object(code_tools.subprocess, "Popen", side_effect=FileNotFoundError("fixture")) as popen:
        result = json.loads(asyncio.run(code_tools.run_project_command(
            str(tmp_path), command, shell_mode="cmd", keep_alive=True, background=True)))
    popen.assert_called_once()
    assert result.get("error_name") != "PATH_QUOTING_ERROR"


@pytest.mark.skipif(sys.platform != "win32", reason="Windows command path contract")
def test_foreground_contract_stays_structured(tmp_path):
    result = json.loads(asyncio.run(code_tools.run_project_command(
        str(tmp_path), r"C:\Program Files\missing-tool-for-test.bat",
        timeout=5, shell_mode="cmd")))
    assert result["error_name"] == "PATH_QUOTING_ERROR"
