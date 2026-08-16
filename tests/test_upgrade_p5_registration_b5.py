"""B5 regression: tools_upgrade_p5 (start_app / stop_app / http_check /
browser_smoke_test) must be REGISTERED and correctly GATED.

Provenance: these four tools were live once (OPE_REALTEST_A_TOOLS_20260807) but
their registration block was lost in a squash commit; only a fossil `stop_app`
entry survived in orchestrator_server._NO_SESSION_REQUIRED. B5 restores the
registration and — critically — the security gate: start_app wraps
run_project_command (arbitrary shell) and stop_app wraps kill_process, both
in-process calls that bypass the HTTP token gate, so the wrappers MUST inherit
the token gate of what they wrap.

Run: ./venv312/Scripts/python.exe -m pytest tests/test_upgrade_p5_registration_b5.py -v
"""

import importlib
import json
import sys
import unittest
from unittest import mock

import code_tools

_P5_TOOLS = ("start_app", "stop_app", "http_check", "browser_smoke_test")
# Wrappers that reach a TOKEN_REQUIRED tool (run_project_command / kill_process)
# in-process; they MUST carry the same gate. http_check (stdlib probe) and
# browser_smoke_test (follows open_browser_page precedent: PROTECTED, NOT token)
# are intentionally NOT in this set.
_P5_TOKEN_GATED = ("start_app", "stop_app")


def _schema_names() -> set[str]:
    out = set()
    for item in code_tools.TOOL_SCHEMAS:
        fn = (item.get("function") or {}) if isinstance(item, dict) else {}
        if fn.get("name"):
            out.add(fn["name"])
    return out


def _schema_for(name: str) -> dict:
    for item in code_tools.TOOL_SCHEMAS:
        fn = (item.get("function") or {}) if isinstance(item, dict) else {}
        if fn.get("name") == name:
            return fn
    return {}


class TestP5Registration(unittest.TestCase):

    def test_all_four_in_tool_map(self):
        for name in _P5_TOOLS:
            self.assertIn(name, code_tools.TOOL_MAP,
                          f"{name} missing from TOOL_MAP (registration lost)")

    def test_each_has_phase_entry(self):
        for name in _P5_TOOLS:
            self.assertIn(name, code_tools.TOOL_PHASES,
                          f"{name} missing from TOOL_PHASES")
            # module declares T2 for all four (see tools_upgrade_p5 docstrings)
            self.assertEqual(code_tools.TOOL_PHASES[name], "T2",
                             f"{name} phase should be T2 as the module declares")

    def test_each_has_schema(self):
        names = _schema_names()
        for name in _P5_TOOLS:
            self.assertIn(name, names, f"{name} missing from TOOL_SCHEMAS")


class TestP5SecurityGate(unittest.TestCase):
    """The whole point of B5: the shell/process wrappers must be token-gated."""

    def test_shell_wrappers_are_token_required(self):
        for name in _P5_TOKEN_GATED:
            self.assertIn(
                name, code_tools.TOKEN_REQUIRED_TOOLS,
                f"SECURITY: {name} wraps a TOKEN_REQUIRED tool in-process but is "
                f"NOT in TOKEN_REQUIRED_TOOLS -> unauthenticated shell/process "
                f"exec over /api/call")

    def test_shell_wrappers_are_protected(self):
        for name in _P5_TOKEN_GATED:
            self.assertIn(
                name, code_tools.PROTECTED_TOOL_NAMES,
                f"SECURITY: {name} not in PROTECTED_TOOL_NAMES (origin/session gate)")

    def test_shell_wrappers_have_process_scope(self):
        for name in _P5_TOKEN_GATED:
            self.assertEqual(
                code_tools.tool_mutation_scope(name), "process",
                f"{name} should have mutation_scope 'process'")

    def test_browser_smoke_test_follows_open_browser_page_precedent(self):
        # open_browser_page (spawns Chromium) is PROTECTED, scope 'process',
        # but NOT token-required. browser_smoke_test wraps it in-process, so it
        # follows the same precedent (safer than leaving it unprotected).
        self.assertIn("browser_smoke_test", code_tools.PROTECTED_TOOL_NAMES)
        self.assertEqual(code_tools.tool_mutation_scope("browser_smoke_test"),
                         "process")

    def test_http_check_is_a_plain_read_probe(self):
        # stdlib bounded HTTP probe: no shell, no process spawn -> not gated.
        self.assertNotIn("http_check", code_tools.TOKEN_REQUIRED_TOOLS)


class TestP5SchemaQuality(unittest.TestCase):

    def test_every_property_has_description_and_type(self):
        for name in _P5_TOOLS:
            props = (_schema_for(name).get("parameters") or {}).get("properties") or {}
            self.assertTrue(props, f"{name} has no schema properties")
            for pname, pdef in props.items():
                self.assertIsInstance(pdef, dict, f"{name}.{pname} not a dict")
                self.assertIn("description", pdef,
                              f"{name}.{pname} missing description (ratchet risk)")
                self.assertIn("type", pdef, f"{name}.{pname} missing type")


class TestP5ScopeGateIsSatisfiable(unittest.TestCase):
    """Found by LIVE real-test after the restart that loaded B5, not by the
    offline suite: a tool with mutation_scope 'process' makes the dispatcher
    resolve a project root, so a call without project_path answers 400
    PROJECT_PATH_REQUIRED. browser_smoke_test did not DECLARE project_path, so
    supplying it hit S4 and answered 400 UNSUPPORTED_FIELD. Two 400s, no way
    through -- the dead-end-recovery-hint shape this campaign keeps meeting
    (B1, P11b). Any process-scope tool must let the caller satisfy its own gate.

    stop_app is exempt and must stay exempt: it is in
    orchestrator_server._NO_SESSION_REQUIRED (inherited from kill_process, which
    it wraps), so the root-resolution gate never runs for it -- verified live,
    a no-project_path call reaches the handler and answers PROCESS_KILL_FAILED.
    Adding project_path there would be cargo-culting a field the tool never uses.
    """

    @staticmethod
    def _needs_a_project_root(name: str) -> bool:
        import orchestrator_server
        return (code_tools.tool_mutation_scope(name) == "process"
                and name not in orchestrator_server._NO_SESSION_REQUIRED)

    def test_process_scope_tools_declare_project_path(self):
        for name in _P5_TOOLS:
            if not self._needs_a_project_root(name):
                continue
            props = (_schema_for(name).get("parameters") or {}).get("properties") or {}
            self.assertIn(
                "project_path", props,
                f"{name} has mutation_scope 'process' (dispatcher demands a project "
                f"root) but does not declare project_path, so the caller cannot "
                f"comply: PROJECT_PATH_REQUIRED then UNSUPPORTED_FIELD")

    def test_project_path_is_optional_not_required(self):
        # The gate resolves a root from session/cwd when it can; forcing the field
        # would break every call that already works.
        for name in _P5_TOOLS:
            required = (_schema_for(name).get("parameters") or {}).get("required") or []
            self.assertNotIn("project_path", required, f"{name}")


class TestStopAppKillsTheWholeTree(unittest.IsolatedAsyncioTestCase):
    """Second defect found only by LIVE real-test, not by the offline suite.

    start_app launches via `cmd /c <command>`, so the spawned_pid it returns is
    the cmd.exe wrapper. stop_app called kill_process on exactly that PID and
    answered ok:true / killed:[cmd.exe] -- while the app it was asked to stop kept
    serving 200 on its port. Measured against a real HTTP server on 127.0.0.1:8791.
    A false ok is worse than an error here: the caller stops looking.
    """

    async def test_descendants_are_killed_before_the_parent(self):
        import tools_upgrade_p5

        killed_order: list[int] = []

        class _FakeProc:
            def __init__(self, pid):
                self.pid = pid

        class _FakePsutil:
            @staticmethod
            def Process(pid):
                class _P:
                    # grandchild(30) is returned after child(20) by psutil's
                    # recursive walk; stop_app must reverse it so leaves die first.
                    @staticmethod
                    def children(recursive=False):
                        return [_FakeProc(20), _FakeProc(30)]
                return _P()

        async def _fake_kill(pid=0, process_name="", force=True):
            killed_order.append(int(pid))
            return json.dumps({"ok": True, "pid": pid, "killed": [{"pid": pid, "name": "x"}],
                               "failed": [], "killed_count": 1, "failed_count": 0})

        with mock.patch.dict(sys.modules, {"psutil": _FakePsutil}), \
                mock.patch("orchestrator.session.process.kill_process", _fake_kill):
            payload = json.loads(await tools_upgrade_p5.stop_app(pid=10))

        self.assertEqual(killed_order, [30, 20, 10], msg=killed_order)
        self.assertTrue(payload["ok"], msg=payload)
        self.assertEqual(payload["killed_count"], 3, msg=payload)
        self.assertEqual(payload["children_killed"], 2, msg=payload)

    async def test_a_failed_child_kill_is_not_reported_as_success(self):
        # The exact shape of the live defect: partial success must not read as ok.
        import tools_upgrade_p5

        class _FakePsutil:
            @staticmethod
            def Process(pid):
                class _P:
                    @staticmethod
                    def children(recursive=False):
                        class _C:
                            pid = 20
                        return [_C()]
                return _P()

        async def _fake_kill(pid=0, process_name="", force=True):
            if pid == 20:
                return json.dumps({"ok": False, "pid": 20, "killed": [],
                                   "failed": [{"pid": 20, "error": "access denied"}],
                                   "killed_count": 0, "failed_count": 1})
            return json.dumps({"ok": True, "pid": pid, "killed": [{"pid": pid, "name": "x"}],
                               "failed": [], "killed_count": 1, "failed_count": 0})

        with mock.patch.dict(sys.modules, {"psutil": _FakePsutil}), \
                mock.patch("orchestrator.session.process.kill_process", _fake_kill):
            payload = json.loads(await tools_upgrade_p5.stop_app(pid=10))

        self.assertFalse(payload["ok"], msg=payload)
        self.assertEqual(payload["failed_count"], 1, msg=payload)

    async def test_a_pid_that_exited_on_its_own_is_success_not_failure(self):
        # Measured live: killing the python child made its cmd.exe parent exit by
        # itself, so the next kill in the sweep answered "PID not found" and the
        # call reported ok:false -- while the app was verifiably stopped (port
        # closed). Reporting that as failure trains callers to ignore ok, which is
        # exactly how the original silent-success bug survived unnoticed.
        import tools_upgrade_p5

        class _FakePsutil:
            @staticmethod
            def Process(pid):
                class _P:
                    @staticmethod
                    def children(recursive=False):
                        class _C:
                            pid = 20
                        return [_C()]
                return _P()

        async def _fake_kill(pid=0, process_name="", force=True):
            if pid == 10:  # the parent went away once its child died
                return json.dumps({"ok": False, "error": "kill_process failed: process "
                                                         "PID not found (pid=10)",
                                   "error_name": "PROCESS_KILL_FAILED", "pid": 10})
            return json.dumps({"ok": True, "pid": pid, "killed": [{"pid": pid, "name": "x"}],
                               "failed": [], "killed_count": 1, "failed_count": 0})

        with mock.patch.dict(sys.modules, {"psutil": _FakePsutil}), \
                mock.patch("orchestrator.session.process.kill_process", _fake_kill):
            payload = json.loads(await tools_upgrade_p5.stop_app(pid=10))

        self.assertTrue(payload["ok"], msg=payload)
        self.assertEqual(payload["failed_count"], 0, msg=payload)
        self.assertEqual(payload["already_exited_count"], 1, msg=payload)
        self.assertEqual(payload.get("error_name"), "", msg=payload)

    async def test_a_vanished_pid_reported_as_a_failed_entry_is_also_success(self):
        # kill_process has TWO shapes for "PID not found": a top-level error (the
        # live one, covered above) and an entry inside `failed` (when the process
        # dies between enumeration and proc.kill()). Both mean the same thing, so
        # both must be classified the same way -- a sabotage run showed the entry
        # shape had no coverage at all.
        import tools_upgrade_p5

        class _FakePsutil:
            @staticmethod
            def Process(pid):
                class _P:
                    @staticmethod
                    def children(recursive=False):
                        class _C:
                            pid = 20
                        return [_C()]
                return _P()

        async def _fake_kill(pid=0, process_name="", force=True):
            if pid == 20:
                return json.dumps({"ok": False, "pid": 20, "killed": [],
                                   "failed": [{"pid": 20, "name": "x",
                                               "error": "process PID not found (pid=20)"}],
                                   "killed_count": 0, "failed_count": 1})
            return json.dumps({"ok": True, "pid": pid, "killed": [{"pid": pid, "name": "x"}],
                               "failed": [], "killed_count": 1, "failed_count": 0})

        with mock.patch.dict(sys.modules, {"psutil": _FakePsutil}), \
                mock.patch("orchestrator.session.process.kill_process", _fake_kill):
            payload = json.loads(await tools_upgrade_p5.stop_app(pid=10))

        self.assertTrue(payload["ok"], msg=payload)
        self.assertEqual(payload["failed_count"], 0, msg=payload)
        self.assertEqual(payload["already_exited_count"], 1, msg=payload)

    async def test_include_children_false_keeps_the_old_single_pid_behaviour(self):
        import tools_upgrade_p5

        seen: list[int] = []

        async def _fake_kill(pid=0, process_name="", force=True):
            seen.append(int(pid))
            return json.dumps({"ok": True, "pid": pid, "killed": [{"pid": pid, "name": "x"}],
                               "failed": [], "killed_count": 1, "failed_count": 0})

        with mock.patch("orchestrator.session.process.kill_process", _fake_kill):
            payload = json.loads(
                await tools_upgrade_p5.stop_app(pid=10, include_children=False))

        self.assertEqual(seen, [10], msg=seen)
        self.assertNotIn("children_killed", payload, msg=payload)

    async def test_process_name_mode_still_works(self):
        # No pid -> no tree walk; kill_process handles name matching itself.
        import tools_upgrade_p5

        async def _fake_kill(pid=0, process_name="", force=True):
            return json.dumps({"ok": True, "pid": pid, "process_name": process_name,
                               "killed": [{"pid": 99, "name": process_name}], "failed": [],
                               "killed_count": 1, "failed_count": 0})

        with mock.patch("orchestrator.session.process.kill_process", _fake_kill):
            payload = json.loads(await tools_upgrade_p5.stop_app(process_name="app.exe"))

        self.assertTrue(payload["ok"], msg=payload)


class TestP5ModuleImportable(unittest.TestCase):

    def test_module_imports_in_isolation(self):
        mod = importlib.import_module("tools_upgrade_p5")
        self.assertTrue(hasattr(mod, "UPGRADE_P5_SCHEMA_SPECS"))
        self.assertEqual(len(mod.UPGRADE_P5_SCHEMA_SPECS), 4)
        for name in _P5_TOOLS:
            self.assertTrue(callable(getattr(mod, name, None)),
                            f"{name} not callable on module")


if __name__ == "__main__":
    unittest.main()
