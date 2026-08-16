"""A31: the READ path for .council/worker_models.json must validate each override
entry against the curated ID_MODELS catalog and fall back to the config default when
an entry is garbage — mirroring the WRITE path (_council_validate_model_setting).

Regression incident: worker_models.json was hand-edited to point 6 agents at a
non-existent provider `agent247` with fabricated model ids. The write tool would have
rejected those, but the read path (_council_worker_model) applied them verbatim,
silently destroying every affected agent's working config default
(resolution_status "unavailable" -> undispatchable).

These tests patch orchestrator_server._HERE to a temp dir that carries a COPY of the
real .council/ID_MODELS catalog, so validation is genuine end-to-end (not mocked).
"""
import json
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import orchestrator_server as s


class TestWorkerModelsReadValidationA31(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="a31_wm_"))
        council = self.tmp / ".council"
        council.mkdir(parents=True)
        # Real curated catalog so _council_valid_models does genuine validation.
        shutil.copytree(s._HERE / ".council" / "ID_MODELS", council / "ID_MODELS")
        self.wm = council / "worker_models.json"
        self.patch_here = mock.patch.object(s, "_HERE", self.tmp)
        self.patch_here.start()

    def tearDown(self):
        self.patch_here.stop()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _write(self, obj):
        self.wm.write_text(json.dumps(obj), encoding="utf-8")

    # ── Branch A: invalid override -> fall back to config default ──────────────
    def test_garbage_override_falls_back_to_default(self):
        self._write(
            {"clau": {"provider": "agent247", "model": "agent/claude-opus-4-8"}})
        prov, model = s._council_worker_model("clau", "claude", "claude-opus-4-8")
        self.assertEqual((prov, model), ("claude", "claude-opus-4-8"))

    def test_garbage_model_valid_provider_falls_back(self):
        # provider is fine, model is fabricated -> still invalid -> fall back.
        self._write({"clau": {"provider": "claude", "model": "agent/claude-opus-4-8"}})
        prov, model = s._council_worker_model("clau", "claude", "claude-opus-4-8")
        self.assertEqual((prov, model), ("claude", "claude-opus-4-8"))

    # ── Branch B: a rejection is logged, naming agent + bad value ──────────────
    def test_garbage_override_emits_warning(self):
        self._write(
            {"clau": {"provider": "agent247", "model": "agent/claude-opus-4-8"}})
        with self.assertLogs(s.logger, level="WARNING") as cm:
            s._council_worker_model("clau", "claude", "claude-opus-4-8")
        joined = "\n".join(cm.output)
        self.assertIn("clau", joined)
        self.assertIn("agent/claude-opus-4-8", joined)

    # ── Branch C: a valid override is still honored ────────────────────────────
    def test_valid_override_is_honored(self):
        self._write({"clau": {"provider": "claude", "model": "claude-sonet-5"}})
        prov, model = s._council_worker_model("clau", "claude", "claude-opus-4-8")
        self.assertEqual(model, "claude-sonet-5")

    def test_provider_mismatch_valid_model_still_honored(self):
        # PROVIDER_MODEL_MISMATCH is a warning (ok=True), NOT a rejection: a valid
        # curated model must not be discarded just because the provider label differs.
        self._write({"clau": {"provider": "totally-wrong", "model": "claude-sonet-5"}})
        _prov, model = s._council_worker_model("clau", "claude", "claude-opus-4-8")
        self.assertEqual(model, "claude-sonet-5")

    # ── Regression guards for pre-existing behavior ────────────────────────────
    def test_override_equal_to_default_is_honored(self):
        self._write({"clau": {"provider": "claude", "model": "claude-opus-4-8"}})
        prov, model = s._council_worker_model("clau", "claude", "claude-opus-4-8")
        self.assertEqual((prov, model), ("claude", "claude-opus-4-8"))

    def test_non_dict_root_falls_back(self):
        self.wm.write_text("[]", encoding="utf-8")
        prov, model = s._council_worker_model("clau", "claude", "claude-opus-4-8")
        self.assertEqual((prov, model), ("claude", "claude-opus-4-8"))

    def test_missing_file_uses_default(self):
        # no worker_models.json in the temp .council
        prov, model = s._council_worker_model("kilo", "ltnai", "ltnai/glm-5.2")
        self.assertEqual((prov, model), ("ltnai", "ltnai/glm-5.2"))

    def test_explicit_model_override_arg_wins_over_garbage_file(self):
        self._write({"clau": {"provider": "agent247", "model": "agent/x"}})
        _prov, model = s._council_worker_model(
            "clau", "claude", "claude-opus-4-8", model_override="claude-sonet-5")
        self.assertEqual(model, "claude-sonet-5")

    def test_partial_provider_only_override_on_valid_default(self):
        # entry sets provider only; model comes from the (valid) config default.
        # provider mismatch is a warning, so the provider override still applies.
        self._write({"ope": {"provider": "opencode-zen"}})
        prov, model = s._council_worker_model(
            "ope", "opencode-zen", "opencode/deepseek-v4-flash-free")
        self.assertEqual(model, "opencode/deepseek-v4-flash-free")


if __name__ == "__main__":
    unittest.main()
