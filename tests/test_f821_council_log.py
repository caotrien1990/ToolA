"""Regression tests for F821-3/4/5/6: four council recovery branches in
orchestrator_server.py called ``_log(msg, "warning")`` but no ``_log``
helper exists at module scope (the module logger is ``logger``). Any real
invocation of these branches raised ``NameError`` and masked the actual
error path:

- F821-3 (line ~6491): worker_models.json decodes to a non-dict root
- F821-4 (line ~6500): a per-agent entry is present but not a dict
- F821-5 (line ~6502): worker_models.json read/parse raised
- F821-6 (line ~8966): valid-models handler hit the same read/parse failure

The invariant these tests protect: each recovery branch must log a warning
via the module ``logger`` and fail open (return defaults / HTTP 200 with
``ok:True,current:{}``), never raise NameError.
"""

from __future__ import annotations

import asyncio
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import orchestrator_server as server


class _FakeRequest:
    """Minimal stub for handle_council_valid_models."""

    def __init__(self, agent: str):
        self.headers = {"Origin": "http://localhost"}
        self.query = {"agent": agent}
        self.remote = "127.0.0.1"


class TestCouncilWorkerModelRecoveryBranches(unittest.TestCase):
    """F821-3, F821-4, F821-5: _council_worker_model fall-throughs."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.council_dir = Path(self._tmp.name) / ".council"
        self.council_dir.mkdir(parents=True)
        self.wm_path = self.council_dir / "worker_models.json"
        # Patch _HERE so the function under test reads from our temp dir.
        self._here_patcher = mock.patch.object(server, "_HERE", Path(self._tmp.name))
        self._here_patcher.start()
        self.addCleanup(self._here_patcher.stop)

    def test_f821_3_non_dict_root_returns_defaults_and_logs_warning(self):
        # WHY: a worker_models.json whose root decodes to a list/scalar must
        # be IGNORED (defaults returned) and logged. Pre-fix this branch
        # raised NameError on the _log call, breaking every dispatch that
        # read a malformed config.
        self.wm_path.write_text(json.dumps(["not", "a", "dict"]), encoding="utf-8")
        with mock.patch.object(server.logger, "warning") as warn:
            prov, model = server._council_worker_model(
                "clau", default_provider="p_default", default_model="m_default",
            )
        self.assertEqual(prov, "p_default")
        self.assertEqual(model, "m_default")
        warn.assert_called()
        # Pin the message so the log stays actionable.
        joined = " ".join(str(c.args[0]) for c in warn.call_args_list)
        self.assertIn("root is not an object", joined)

    def test_f821_4_non_dict_entry_returns_defaults_and_logs_warning(self):
        # WHY: an entry like {"clau": "not-a-dict"} must be ignored with a
        # warning, not crash with NameError. The default provider/model
        # pair must be returned so dispatch can proceed.
        self.wm_path.write_text(
            json.dumps({"clau": "i-am-a-string-not-a-dict"}), encoding="utf-8",
        )
        with mock.patch.object(server.logger, "warning") as warn:
            prov, model = server._council_worker_model(
                "clau", default_provider="p_default", default_model="m_default",
            )
        self.assertEqual(prov, "p_default")
        self.assertEqual(model, "m_default")
        warn.assert_called()
        joined = " ".join(str(c.args[0]) for c in warn.call_args_list)
        self.assertIn("entry is not an object", joined)
        self.assertIn("clau", joined)

    def test_f821_5_read_failure_returns_defaults_and_logs_warning(self):
        # WHY: any exception while reading worker_models.json (corrupt JSON,
        # permission error, etc.) must be swallowed with a warning. Pre-fix
        # the _log NameError escaped the except-block, masking the real
        # read failure and propagating to the caller.
        self.wm_path.write_text("{ this is not valid json ", encoding="utf-8")
        with mock.patch.object(server.logger, "warning") as warn:
            prov, model = server._council_worker_model(
                "clau", default_provider="p_default", default_model="m_default",
            )
        self.assertEqual(prov, "p_default")
        self.assertEqual(model, "m_default")
        warn.assert_called()
        joined = " ".join(str(c.args[0]) for c in warn.call_args_list)
        self.assertIn("read failed", joined)
        self.assertIn("clau", joined)


class TestCouncilValidModelsHandler(unittest.TestCase):
    """F821-6: /api/council/valid-models read/parse failure recovery."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.council_dir = Path(self._tmp.name) / ".council"
        self.council_dir.mkdir(parents=True)
        self.wm_path = self.council_dir / "worker_models.json"
        self._here_patcher = mock.patch.object(server, "_HERE", Path(self._tmp.name))
        self._here_patcher.start()
        self.addCleanup(self._here_patcher.stop)
        # Bypass the origin/local gates: the regression targets the JSON
        # read/parse branch, not access control.
        self._is_local_patcher = mock.patch.object(server, "_is_local", lambda r: True)
        self._origin_patcher = mock.patch.object(server, "_origin_ok", lambda r: True)
        self._is_local_patcher.start()
        self._origin_patcher.start()
        self.addCleanup(self._is_local_patcher.stop)
        self.addCleanup(self._origin_patcher.stop)
        # Ensure the agent is in the allow-list for the duration of the test.
        self._agents_patcher = mock.patch.object(
            server, "_COUNCIL_AGENTS", list(server._COUNCIL_AGENTS) + ["testagent"],
        )
        self._agents_patcher.start()
        self.addCleanup(self._agents_patcher.stop)

    def test_f821_6_read_failure_returns_200_ok_true_current_empty_and_warns(self):
        # WHY: a corrupt worker_models.json must NOT break the valid-models
        # endpoint — clients still need the curated ``valid``/``models``
        # lists. Pre-fix the _log NameError escaped the handler and the
        # request failed. Post-fix the handler logs a warning and returns
        # HTTP 200 with ok:True and current:{}.
        self.wm_path.write_text("{ not json ", encoding="utf-8")
        req = _FakeRequest(agent="testagent")
        with mock.patch.object(server.logger, "warning") as warn:
            resp = asyncio.run(server.handle_council_valid_models(req))
        self.assertEqual(resp.status, 200)
        payload = json.loads(resp.text)
        self.assertTrue(payload.get("ok"), payload)
        self.assertEqual(payload.get("current"), {}, payload)
        self.assertEqual(payload.get("agent"), "testagent", payload)
        # Curated lists still present (may be empty for a test agent).
        self.assertIn("valid", payload)
        self.assertIn("models", payload)
        warn.assert_called()
        joined = " ".join(str(c.args[0]) for c in warn.call_args_list)
        self.assertIn("valid-models", joined)
        self.assertIn("testagent", joined)


if __name__ == "__main__":
    unittest.main()
