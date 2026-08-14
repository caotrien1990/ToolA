"""S4 registry/schema source-of-truth regression coverage."""
import inspect
import json
import unittest
from unittest import mock

import code_tools
import orchestrator_server as srv
from orchestrator.mcp_packs.generator import BUILTIN_PACKS


def _params(name):
    return (srv._schema_for_tool(name).get("parameters") or {})


def _parity_rows():
    rows = []
    dispatcher = {
        "delete_file": {"backup_override"},
        "write_fix": {"backup_override"},
        "write_fix_html": {"backup_override"},
        "write_new_file": {"backup_override"},
        "apply_after_dry_run": {"backup_override"},
    }
    private = {("write_fix", "skip_pre_write_check"), ("write_new_file", "skip_pre_write_check")}
    for name, fn in code_tools.TOOL_MAP.items():
        props = set((_params(name).get("properties") or {}))
        sig = inspect.signature(fn)
        variadic = any(p.kind == inspect.Parameter.VAR_KEYWORD for p in sig.parameters.values())
        handler = {p.name for p in sig.parameters.values() if p.kind not in (inspect.Parameter.VAR_POSITIONAL, inspect.Parameter.VAR_KEYWORD)}
        for field in sorted(props - handler):
            category = "dispatcher-only" if field in dispatcher.get(name, set()) else ("kwargs-supported" if variadic else "legit-public-gap")
            rows.append((name, field, category))
        for field in sorted(handler - props):
            category = "private-handler-param" if (name, field) in private else ("kwargs-supported" if variadic else "default-type-requiredness-drift")
            rows.append((name, field, category))
    return rows


class TestRegistryParity(unittest.TestCase):
    def test_all_differences_are_classified_and_listed_gaps_resolve(self):
        rows = _parity_rows()
        allowed = {"legit-public-gap", "private-handler-param", "dispatcher-only", "kwargs-supported", "default-type-requiredness-drift"}
        self.assertTrue(rows)
        self.assertEqual({row[2] for row in rows} - allowed, set())
        self.assertIn(("write_fix", "skip_pre_write_check", "private-handler-param"), rows)
        for name in ("delete_file", "write_fix", "write_fix_html", "write_new_file", "apply_after_dry_run"):
            self.assertIn((name, "backup_override", "dispatcher-only"), rows)

    def test_backup_timeout_matches_handler_default(self):
        self.assertEqual(_params("backup_project")["properties"]["timeout"]["default"], 600)
        self.assertEqual(inspect.signature(code_tools.backup_project).parameters["timeout"].default, 600)

    def test_force_drain_schema_documents_conditional_reason(self):
        params = _params("restart_orchestrator_via_bat")
        self.assertIn("force_drain", params["properties"])
        self.assertIn("force_drain_reason", params["properties"])
        self.assertEqual(params["allOf"][0]["then"]["properties"]["force_drain_reason"]["minLength"], 1)

    def test_orchestrator_mcp_pack_tools_resolve(self):
        missing = []
        for pack_name, pack in BUILTIN_PACKS.items():
            if pack.get("kind") != "orchestrator_call":
                continue
            for tool in pack["tools"]:
                if tool["name"] not in code_tools.TOOL_MAP:
                    missing.append((pack_name, tool["name"]))
        self.assertEqual(missing, [])


class TestRegistryFirstDispatch(unittest.IsolatedAsyncioTestCase):
    async def test_begin_task_rejects_project_path_and_remediation_omits_it(self):
        response = await srv.dispatch_tool_call("begin_task", {"session_id": "s", "prompt": "p", "project_path": "x"}, transport="mcp")
        payload = json.loads(response.text)
        self.assertEqual(payload["error_name"], "UNSUPPORTED_FIELD")
        self.assertNotIn("project_path", payload["corrected_payload"])
        self.assertNotIn("project_path", payload["ca_guidance"]["payload_example"])

    async def test_private_skip_pre_write_check_is_rejected_publicly(self):
        response = await srv.dispatch_tool_call("write_fix", {"file_path": "x.py", "start_line": 1, "end_line": 1, "new_content": "x=1", "skip_pre_write_check": True}, transport="mcp")
        payload = json.loads(response.text)
        self.assertEqual(payload["error_name"], "UNSUPPORTED_FIELD")
        self.assertIn("skip_pre_write_check", payload["unsupported_fields"])

    async def test_force_drain_fields_reach_handler(self):
        with mock.patch.object(srv, "_restart_busy_state", return_value={"busy": False}), mock.patch("tools_v15.restart_orchestrator_via_bat", new=mock.AsyncMock(return_value="ok")) as restart:
            result = await srv.restart_orchestrator_via_bat(2, True, "stale accounting verified")
        self.assertEqual(result, "ok")
        self.assertEqual(restart.await_args.kwargs["force_drain_reason"], "stale accounting verified")


class TestRegistryExamples(unittest.TestCase):
    def test_every_published_example_uses_declared_fields_and_required_fields(self):
        failures = []
        for name in code_tools.TOOL_MAP:
            params = _params(name)
            props = params.get("properties") or {}
            example = srv._payload_example_for_tool(name, srv._schema_for_tool(name))
            extra = set(example) - set(props)
            missing = set(params.get("required") or []) - set(example)
            if extra or missing:
                failures.append((name, sorted(extra), sorted(missing)))
            for field, value in example.items():
                spec = props[field]
                if spec.get("enum") and value not in spec["enum"]:
                    failures.append((name, field, "enum"))
                if spec.get("type") == "array" and len(value) < spec.get("minItems", 0):
                    failures.append((name, field, "minItems"))
        self.assertEqual(failures, [])
