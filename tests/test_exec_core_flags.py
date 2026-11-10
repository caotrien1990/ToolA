"""C2 exec-core flags composition (design FIX_DESIGN draft v6, section 3C2).

Drives the REAL run_project_command composition in code_tools.py with
subprocess.Popen mocked out (the FakeProcess harness pattern from
tests/test_command_transport_c2.py) and console_warp.has_console patched
both ways, capturing the creationflags that reach the Popen call.

Matrix:
- keep_alive+interactive is EXEMPT from helper routing: CREATE_NEW_CONSOLE
  present and CREATE_NO_WINDOW absent in BOTH console states.
- non-interactive routes through child_creationflags(
  extra=CREATE_NEW_PROCESS_GROUP): console present -> NEW_GROUP only;
  console absent -> NEW_GROUP | CREATE_NO_WINDOW.
"""
from __future__ import annotations

import asyncio
import io
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import code_tools
from orchestrator.lifecycle import console_warp


class FakeProcess:
    """Popen as the runner reads it: text pipes it drains itself, plus wait()."""

    def __init__(self, stdout="", stderr=""):
        self.pid, self.returncode = 43211, 0
        self.stdout, self.stderr = (
            io.TextIOWrapper(io.BytesIO(v.encode("utf-8")), encoding="utf-8")
            for v in (stdout, stderr)
        )

    def wait(self, timeout=None):
        return self.returncode

    def poll(self):
        return self.returncode

    def kill(self):
        self.returncode = -9


@unittest.skipUnless(sys.platform == "win32", "Windows creationflags composition")
class TestExecCoreFlags(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory(prefix="exec_core_flags_")
        self.project = Path(self.tempdir.name)

    def tearDown(self):
        self.tempdir.cleanup()

    def _capture_flags(self, *, has_console, keep_alive, interactive):
        fake = FakeProcess()
        with mock.patch.object(
            console_warp, "has_console", return_value=has_console
        ), mock.patch.object(
            code_tools.subprocess, "Popen", return_value=fake
        ) as popen:
            result = json.loads(
                asyncio.run(
                    code_tools.run_project_command(
                        str(self.project),
                        [sys.executable, "-c", "pass"],
                        shell_mode="exec",
                        keep_alive=keep_alive,
                        interactive=interactive,
                    )
                )
            )
        self.assertTrue(result.get("ok"), result)
        return popen.call_args.kwargs["creationflags"]

    def test_interactive_keep_alive_console_present(self):
        flags = self._capture_flags(
            has_console=True, keep_alive=True, interactive=True
        )
        self.assertTrue(flags & console_warp.CREATE_NEW_CONSOLE)
        self.assertFalse(flags & console_warp.CREATE_NO_WINDOW)

    def test_interactive_keep_alive_console_absent(self):
        flags = self._capture_flags(
            has_console=False, keep_alive=True, interactive=True
        )
        self.assertTrue(flags & console_warp.CREATE_NEW_CONSOLE)
        self.assertFalse(flags & console_warp.CREATE_NO_WINDOW)

    def test_non_interactive_console_present(self):
        flags = self._capture_flags(
            has_console=True, keep_alive=False, interactive=False
        )
        with mock.patch.object(console_warp, "has_console", return_value=True):
            expected = console_warp.child_creationflags(
                extra=console_warp.CREATE_NEW_PROCESS_GROUP
            )
        self.assertEqual(flags, expected)
        self.assertEqual(flags, console_warp.CREATE_NEW_PROCESS_GROUP)
        self.assertFalse(flags & console_warp.CREATE_NO_WINDOW)

    def test_non_interactive_console_absent(self):
        flags = self._capture_flags(
            has_console=False, keep_alive=False, interactive=False
        )
        with mock.patch.object(console_warp, "has_console", return_value=False):
            expected = console_warp.child_creationflags(
                extra=console_warp.CREATE_NEW_PROCESS_GROUP
            )
        self.assertEqual(flags, expected)
        self.assertEqual(
            flags,
            console_warp.CREATE_NEW_PROCESS_GROUP | console_warp.CREATE_NO_WINDOW,
        )


if __name__ == "__main__":
    unittest.main()
