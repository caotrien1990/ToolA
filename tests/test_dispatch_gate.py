import json
import tempfile
import unittest
from pathlib import Path

import orchestrator_server


class TestCouncilDispatchGate(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.old_here = orchestrator_server._HERE
        self.old_run = orchestrator_server._council_run_agent
        self.old_defaults = {k: dict(v) for k, v in orchestrator_server._COUNCIL_DEFAULT_MODELS.items()}
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        (self.root / ".council").mkdir()
        orchestrator_server._HERE = self.root
        orchestrator_server._COUNCIL_DEFAULT_MODELS["ope"] = {"provider": "", "model": ""}

    async def asyncTearDown(self):
        orchestrator_server._HERE = self.old_here
        orchestrator_server._council_run_agent = self.old_run
        orchestrator_server._COUNCIL_DEFAULT_MODELS.clear()
        orchestrator_server._COUNCIL_DEFAULT_MODELS.update(self.old_defaults)
        self.tmp.cleanup()

    async def test_worker_without_configured_model_returns_structured_error(self):
        called = False

        def _stub_run(*args, **kwargs):
            nonlocal called
            called = True
            return {"ok": True, "reply": "should not run"}

        orchestrator_server._council_run_agent = _stub_run

        result = await orchestrator_server._council_dispatch_message(
            "user", "ope", "dispatch gate probe", queue=False
        )

        self.assertFalse(result["ok"])
        self.assertEqual(result["error_name"], "WORKER_MODEL_NOT_SET")
        self.assertEqual(result["agent"], "ope")
        self.assertEqual(result["ca_next_action"], "council_worker_model set+get before dispatch")
        self.assertFalse(called)

    async def test_worker_with_model_set_passes_gate(self):
        (self.root / ".council" / "worker_models.json").write_text(
            json.dumps({"ope": {"provider": "opencode-zen", "model": "opencode/test-model"}}),
            encoding="utf-8",
        )
        calls = []

        def _stub_run(agent, prompt, timeout_sec=180, mode="broker", project_path="", request_id="", runtime_key="",
                      provider_override="", model_override="", model_resolution=None,
                      risk_level="unknown", dev_level="unknown"):
            calls.append((agent, provider_override, model_override))
            return {"ok": True, "returncode": 0, "reply": "gate passed"}

        orchestrator_server._council_run_agent = _stub_run

        result = await orchestrator_server._council_dispatch_message(
            "user", "ope", "dispatch gate pass", queue=False
        )

        self.assertTrue(result["ok"])
        self.assertEqual(result["reply"], "gate passed")
        self.assertEqual(calls[0][0], "ope")

    async def test_unset_worker_model_does_not_fall_back_to_command_default(self):
        def _fallback_would_have_run(*args, **kwargs):
            raise AssertionError("dispatch reached run path and could use a silent fallback model")

        orchestrator_server._council_run_agent = _fallback_would_have_run

        result = await orchestrator_server._council_dispatch_message(
            "user", "ope", "no silent fallback", queue=False
        )

        self.assertFalse(result["ok"])
        self.assertEqual(result["error_name"], "WORKER_MODEL_NOT_SET")