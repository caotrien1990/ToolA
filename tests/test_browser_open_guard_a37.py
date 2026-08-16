"""A37 regression: the /council browser tab must open ONLY on a genuine user
launch, never on an automatic/subprocess/CI launch.

Background (observed on a real machine): orchestrator_server.py used to call
webbrowser.open("http://localhost:<PORT>/council") UNCONDITIONALLY at startup.
tests/test_task_epoch_cross_process.py spawns a REAL server via
subprocess.Popen([PYTHON, orchestrator_server.py]) on a random free port; that
child reached the open() call and popped a real browser tab onto the user's
desktop. After the test killed the server the tab became a dead page and looked
like agent drift.

These tests pin the PROPERTY, not the mechanism:
  1. genuine user launch  -> webbrowser.open IS called
  2. automatic/subprocess -> webbrowser.open is NOT called
  3. a NEW spawn site whose author forgot about the tab -> NOT called
     (the default must be safe: absence of an explicit opt-in means no tab)

The producer/consumer coupling test guarantees the real user launch path and
the real server guard agree on the exact same signal, so the property cannot be
half-broken by renaming the flag on only one side. webbrowser.open is always
mocked; no test may actually open a browser.
"""
import ast
import inspect
import os
import textwrap
import unittest
from unittest import mock

import orchestrator_server as srv
from orchestrator.lifecycle import high_il_launch


class CouncilTabOptInGuardTests(unittest.TestCase):
    def _env(self, extra):
        """A clean environment (no inherited opt-in) plus `extra`."""
        base = {k: v for k, v in os.environ.items()
                if k != srv.COUNCIL_TAB_ENV}
        base.update(extra)
        return base

    def test_genuine_user_launch_opens_tab(self):
        # PROPERTY 1: when the launcher opted in, the tab opens.
        with mock.patch.dict(srv.os.environ,
                             self._env({srv.COUNCIL_TAB_ENV: "1"}),
                             clear=True), \
                mock.patch.object(srv.webbrowser, "open") as m_open:
            opened = srv._maybe_open_council_tab()
        self.assertTrue(opened)
        m_open.assert_called_once()
        # the tab targets the /council route on the running port
        (url,), _ = m_open.call_args
        self.assertIn("/council", url)
        self.assertIn(str(srv.PORT), url)

    def test_automatic_spawn_does_not_open_tab(self):
        # PROPERTY 2: a subprocess/CI/test spawn (flag absent) opens nothing.
        with mock.patch.dict(srv.os.environ, self._env({}), clear=True), \
                mock.patch.object(srv.webbrowser, "open") as m_open:
            opened = srv._maybe_open_council_tab()
        self.assertFalse(opened)
        m_open.assert_not_called()

    def test_new_callsite_that_forgot_is_safe_by_default(self):
        # PROPERTY 3 (the important one): a brand-new spawn site that just runs
        # the server with a plain copied environment and never thinks about the
        # browser must inherit the SAFE default. This is what proves the design
        # is opt-in, not opt-out. If this fails, the guard mechanism is wrong —
        # change the mechanism, do not weaken this test.
        for wrong in ("0", "", "true", "yes", "TRUE", "council"):
            with self.subTest(value=wrong):
                with mock.patch.dict(srv.os.environ,
                                     self._env({srv.COUNCIL_TAB_ENV: wrong}),
                                     clear=True), \
                        mock.patch.object(srv.webbrowser, "open") as m_open:
                    opened = srv._maybe_open_council_tab()
                self.assertFalse(opened)
                m_open.assert_not_called()

    def test_startup_path_actually_invokes_the_guard(self):
        # The guard is only useful if the real startup coroutine calls it.
        # Direct-call tests above would still pass if someone no-op'd the call
        # site in _run_server (e.g. commented it out or turned it into a bare
        # expression), so pin the wiring. Parse the AST so a call buried in a
        # comment/string does NOT count as calling it — only a real Call node.
        tree = ast.parse(textwrap.dedent(inspect.getsource(srv._run_server)))
        called = {
            node.func.id
            for node in ast.walk(tree)
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
        }
        self.assertIn("_maybe_open_council_tab", called)
        # and there must be no un-guarded direct browser open in the startup path
        attr_calls = {
            f"{node.func.value.id}.{node.func.attr}"
            for node in ast.walk(tree)
            if isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and isinstance(node.func.value, ast.Name)
        }
        self.assertNotIn("webbrowser.open", attr_calls)

    def test_user_launch_path_and_server_guard_agree(self):
        # Producer/consumer coupling: high_il_launch.launch_server is the REAL
        # user launch path (start_orchestrator.bat -> Task Scheduler -> here).
        # Capture the environment it hands the child, then feed that exact env
        # into the REAL server guard and assert the tab opens. This couples the
        # two sides: renaming the opt-in flag on only one side breaks this test.
        captured = {}

        def fake_run(**kwargs):
            captured.update(kwargs.get("env") or {})
            return mock.Mock(returncode=0)

        with mock.patch.object(high_il_launch.subprocess, "run",
                               side_effect=fake_run):
            rc = high_il_launch.launch_server(high_il_launch.root_dir(),
                                              high_il_launch.py_exes(
                                                  high_il_launch.root_dir())[1])
        self.assertEqual(rc, 0)
        self.assertIn(srv.COUNCIL_TAB_ENV, captured)

        # Now the consumer, driven by exactly what the producer set.
        with mock.patch.dict(srv.os.environ, captured, clear=True), \
                mock.patch.object(srv.webbrowser, "open") as m_open:
            opened = srv._maybe_open_council_tab()
        self.assertTrue(opened)
        m_open.assert_called_once()


if __name__ == "__main__":
    unittest.main()
