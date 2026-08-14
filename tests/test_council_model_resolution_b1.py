import json
import unittest
from unittest import mock

import orchestrator_server


class TestCouncilModelResolutionB1(unittest.TestCase):
    def setUp(self):
        self.valid = mock.patch.object(orchestrator_server, "_council_valid_models", return_value=["wanted", "fallback"])
        self.requested = mock.patch.object(orchestrator_server, "_council_requested_model", return_value=("provider-a", "wanted"))
        self.valid.start()
        self.requested.start()

    def tearDown(self):
        self.requested.stop()
        self.valid.stop()

    def test_get_resolution_is_truthful_before_harness_attestation(self):
        result = orchestrator_server._council_resolve_model("ope")
        self.assertEqual(result.requested_model, "wanted")
        self.assertEqual(result.resolved_model, "wanted")
        self.assertIsNone(result.observed_model)
        self.assertEqual(result.resolution_status, "unverified")
        self.assertEqual(result.as_dict()["model"], "wanted")

    def test_unsupported_model_fails_before_execution(self):
        self.requested.stop()
        with mock.patch.object(orchestrator_server, "_council_requested_model", return_value=("provider-a", "missing")):
            result = orchestrator_server._council_resolve_model("ope")
        self.assertEqual(result.resolution_status, "unavailable")
        self.assertEqual(result.resolved_model, "")

    def test_explicit_low_risk_fallback_is_allowlisted_and_preserves_identities(self):
        self.requested.stop()
        with mock.patch.object(orchestrator_server, "_council_requested_model", return_value=("provider-a", "missing")):
            denied = orchestrator_server._council_resolve_model(
                "ope", fallback_policy="explicit", allowed_fallbacks=["other"], risk_level="low")
            allowed = orchestrator_server._council_resolve_model(
                "ope", fallback_policy="explicit", allowed_fallbacks=["fallback"], risk_level="low")
            strict = orchestrator_server._council_resolve_model(
                "ope", fallback_policy="explicit", allowed_fallbacks=["fallback"], risk_level="high")
        self.assertEqual(denied.resolution_status, "unavailable")
        self.assertEqual(strict.resolution_status, "unavailable")
        self.assertEqual(allowed.resolution_status, "explicit_fallback")
        self.assertEqual(allowed.requested_model, "missing")
        self.assertEqual(allowed.resolved_model, "fallback")

    def test_matching_attestation_becomes_exact(self):
        initial = orchestrator_server._council_resolve_model("ope")
        raw = json.dumps({"type": "result", "request_id": "req-b1", "session_id": "session-b1", "provider": "provider-a", "model": "wanted"})
        final, error = orchestrator_server._council_finalize_model_resolution(
            initial, raw, risk_level="high", request_id="req-b1", session_id="session-b1")
        self.assertEqual(error, "")
        self.assertEqual(final.resolution_status, "exact")
        self.assertEqual(final.observed_model, "wanted")

    def test_correct_request_wrong_session_rejected(self):
        initial = orchestrator_server._council_resolve_model("ope")
        raw = json.dumps({"type": "result", "request_id": "req-b1", "session_id": "wrong-session", "provider": "provider-a", "model": "wanted"})
        final, error = orchestrator_server._council_finalize_model_resolution(
            initial, raw, risk_level="high", request_id="req-b1", session_id="session-b1")
        self.assertEqual(error, "COUNCIL_MODEL_UNVERIFIED")
        self.assertEqual((final.observed_provider, final.observed_model), (None, None))

    def test_wrong_request_correct_session_rejected(self):
        initial = orchestrator_server._council_resolve_model("ope")
        raw = json.dumps({"type": "result", "request_id": "wrong-request", "session_id": "session-b1", "provider": "provider-a", "model": "wanted"})
        final, error = orchestrator_server._council_finalize_model_resolution(
            initial, raw, risk_level="high", request_id="req-b1", session_id="session-b1")
        self.assertEqual(error, "COUNCIL_MODEL_UNVERIFIED")
        self.assertEqual((final.observed_provider, final.observed_model), (None, None))
    def test_harness_substitution_is_rejected(self):
        initial = orchestrator_server._council_resolve_model("ope")
        raw = json.dumps({"type": "result", "request_id": "req-b1", "provider": "provider-b", "model": "substitute"})
        final, error = orchestrator_server._council_finalize_model_resolution(
            initial, raw, risk_level="high", request_id="req-b1")
        self.assertEqual(error, "COUNCIL_MODEL_SUBSTITUTED")
        self.assertEqual(final.observed_model, "substitute")
        self.assertEqual(final.resolution_status, "unavailable")

    def test_high_risk_unverified_is_rejected(self):
        initial = orchestrator_server._council_resolve_model("ope")
        final, error = orchestrator_server._council_finalize_model_resolution(initial, "not-json", risk_level="high")
        self.assertEqual(error, "COUNCIL_MODEL_UNVERIFIED")
        self.assertEqual(final.resolution_status, "unverified")

    def test_runtime_state_exposes_resolution_contract(self):
        resolution = orchestrator_server._council_resolve_model("ope").as_dict()
        with (
            mock.patch.object(orchestrator_server, "_council_runtime_write", return_value="ok"),
            mock.patch.object(orchestrator_server, "_council_dashboard_payload_invalidate", return_value=None),
            mock.patch.dict(orchestrator_server._COUNCIL_AGENT_RUNTIME, {}, clear=True),
        ):
            value = orchestrator_server._council_runtime_start(
                "ope", "req-b1", "broker", "ope", "provider-a", "wanted", model_resolution=resolution)
        self.assertEqual(value["model_resolution"]["requested_model"], "wanted")
        self.assertEqual(value["model_resolution"]["resolution_status"], "unverified")

    def test_dashboard_and_canonical_validation_share_warning_owner(self):
        with mock.patch.object(orchestrator_server, "_council_provider_for_model", return_value="provider-a"):
            result = orchestrator_server._council_validate_model_setting("ope", "wrong-provider", "wanted")
            invalid = orchestrator_server._council_validate_model_setting("ope", "provider-a", "missing")
        self.assertTrue(result["ok"])
        self.assertEqual(result["warnings"][0]["warning_name"], "PROVIDER_MODEL_MISMATCH")
        self.assertFalse(invalid["ok"])
        self.assertEqual(invalid["error_name"], "INVALID_MODEL")

    def test_provider_substitution_same_model_is_rejected(self):
        initial = orchestrator_server._council_resolve_model("ope")
        raw = json.dumps({"type": "result", "request_id": "req-b1", "provider": "unexpected", "model": "wanted"})
        final, error = orchestrator_server._council_finalize_model_resolution(initial, raw, request_id="req-b1")
        self.assertEqual(error, "COUNCIL_MODEL_SUBSTITUTED")
        self.assertEqual((final.observed_provider, final.observed_model), ("unexpected", "wanted"))
        self.assertEqual(final.resolution_status, "unavailable")

    def test_envelope_only_attestation_ignores_nested_and_unrelated_fields(self):
        initial = orchestrator_server._council_resolve_model("ope")
        raw = chr(10).join([
            json.dumps({"type": "tool", "request_id": "req-b1", "payload": {"provider": "provider-a", "model": "wanted"}}),
            json.dumps({"type": "result", "request_id": "other", "provider": "provider-a", "model": "wanted"}),
            json.dumps({"type": "result", "request_id": "req-b1", "payload": {"provider": "provider-a", "model": "wanted"}}),
        ])
        final, error = orchestrator_server._council_finalize_model_resolution(initial, raw, risk_level="high", request_id="req-b1")
        self.assertEqual(error, "COUNCIL_MODEL_UNVERIFIED")
        self.assertEqual((final.observed_provider, final.observed_model), (None, None))

    def test_conflicting_bound_identity_records_are_rejected(self):
        initial = orchestrator_server._council_resolve_model("ope")
        raw = chr(10).join([
            json.dumps({"type": "result", "request_id": "req-b1", "provider": "provider-a", "model": "wanted"}),
            json.dumps({"type": "result", "request_id": "req-b1", "provider": "provider-b", "model": "wanted"}),
        ])
        final, error = orchestrator_server._council_finalize_model_resolution(initial, raw, request_id="req-b1")
        self.assertEqual((error, final.resolution_status), ("COUNCIL_MODEL_ATTESTATION_CONFLICT", "unavailable"))

    def test_runtime_finish_persists_terminal_observed_resolution(self):
        cases = (("exact", "provider-a", "wanted", True, ""), ("unavailable", "unexpected", "wanted", False, "COUNCIL_MODEL_SUBSTITUTED"), ("unverified", None, None, False, "COUNCIL_MODEL_UNVERIFIED"))
        for status, provider, model, ok, error in cases:
            initial = orchestrator_server._council_resolve_model("ope").as_dict()
            final = dict(initial, resolution_status=status, observed_provider=provider, observed_model=model)
            persisted = []
            with mock.patch.object(orchestrator_server, "_council_runtime_write", return_value="ok"), mock.patch.object(orchestrator_server, "_council_runtime_persist", side_effect=lambda a, v, r: persisted.append(dict(v)) or True), mock.patch.object(orchestrator_server, "_council_cancel_worker", return_value=None), mock.patch.object(orchestrator_server, "_council_dashboard_payload_invalidate", return_value=None), mock.patch.dict(orchestrator_server._COUNCIL_AGENT_RUNTIME, {}, clear=True):
                orchestrator_server._council_runtime_start("ope", "req-runtime", "broker", "ope", "provider-a", "wanted", model_resolution=initial)
                terminal = orchestrator_server._council_runtime_finish("ope", "req-runtime", ok, error, model_resolution=final)
            self.assertEqual(terminal["model_resolution"]["resolution_status"], status)
            self.assertEqual(terminal["model_resolution"]["observed_provider"], provider)
            self.assertEqual(persisted[-1]["model_resolution"], terminal["model_resolution"])
