"""Cross-surface parity over a D6 corpus subset (Sprint 5 net, plan D5).

Every consumer of the /livez payload must agree with the single source of
truth in orchestrator.lifecycle.livez_contract:

- contract: classify_livez state + is_alive;
- rcs surface: rcs_skip_verdict (adapter D1b) must equal the verdict the
  D1b table prescribes for the classified state (table re-expressed here
  from the plan, not imported from the adapter);
- helper surface: as_bat_helpers gate (is_alive via _livez_assessment /
  _livez_ok) with the fetch seam stubbed;
- watchdog surface: probe()'s livez_ok flag with _read_json stubbed.

The full 722-case corpus parity against the immutable golden is the S1/S5
harness gate; this test pins a representative D6 subset in-repo so a future
drift turns a test red instead of restarting a live daemon.
"""
import math
from unittest import mock

import pytest

import watchdog_a
from orchestrator.lifecycle import as_bat_helpers
from orchestrator.lifecycle import livez_contract as lc

BASE = {"ok": True, "service": "project-orchestrator", "generation": "g1"}


def _p(**kw):
    payload = dict(BASE)
    payload.update(kw)
    return payload


def _health():
    return {
        "ok": True,
        "service": "project-orchestrator",
        "generation": "g1",
        "executor_accepting": True,
        "executor_active_total": 0,
        "executor_queued_total": 0,
        "executor_lanes": [
            {"name": "t", "active": 0, "queued": 0, "accepting": True}
        ],
        "accounting_processed_total": 0,
        "accounting_failed_total": 0,
        "accounting_rejected_total": 0,
    }


# (payload, expected state) — one row per D6 family.
CASES = [
    (_p(livez_protocol=2, startup_phase="ready", boot_age_s=10), lc.ALIVE_READY),
    (_p(livez_protocol=2, startup_phase="ready_degraded", boot_age_s=10), lc.ALIVE_READY),
    (_p(livez_protocol=1), lc.ALIVE_LEGACY),
    (_p(), lc.ALIVE_LEGACY),  # protocol missing -> legacy 1 (PRESERVED)
    (_p(livez_protocol=2, startup_phase="booting", boot_age_s=100), lc.ALIVE_BOOTING),
    (_p(livez_protocol=2, startup_phase="boot_write_sweep", boot_age_s=700), lc.ALIVE_BOOTING),
    (_p(livez_protocol=2, boot_age_s=100), lc.MISMATCH),  # phase missing
    (_p(livez_protocol=2, startup_phase="", boot_age_s=100), lc.ALIVE_BOOTING),
    (_p(livez_protocol=2, startup_phase=5, boot_age_s=100), lc.MISMATCH),
    (_p(livez_protocol=2, startup_phase="stopping", boot_age_s=100), lc.ALIVE_STOPPING),
    (_p(livez_protocol=2, startup_phase="stopping", boot_age_s="not-a-number"), lc.ALIVE_STOPPING),
    (_p(livez_protocol=2.5, startup_phase="ready"), lc.INVALID),
    (_p(livez_protocol=2.0, startup_phase="ready"), lc.INVALID),
    (_p(livez_protocol=True), lc.INVALID),
    (_p(livez_protocol="2", startup_phase="ready"), lc.INVALID),
    (_p(livez_protocol=None), lc.INVALID),
    (_p(livez_protocol=[]), lc.INVALID),
    (_p(livez_protocol=0), lc.INVALID),
    (_p(livez_protocol=-1), lc.INVALID),
    (_p(livez_protocol=math.inf), lc.INVALID),
    (_p(livez_protocol=math.nan), lc.INVALID),
    (_p(ok=False, livez_protocol=1), lc.INVALID),
    (_p(ok=1, livez_protocol=2, startup_phase="ready"), lc.INVALID),
    (_p(service="other", livez_protocol=2, startup_phase="ready"), lc.INVALID),
    (_p(service="other", livez_protocol=1), lc.INVALID),  # service checked first
    (_p(livez_protocol=2, startup_phase="booting", boot_age_s=100), lc.ALIVE_BOOTING),
    (_p(livez_protocol=2, startup_phase="booting", boot_age_s=True), lc.ALIVE_BOOTING),
    (_p(livez_protocol=2, startup_phase="booting", boot_age_s="100"), lc.ALIVE_BOOTING),
    (_p(livez_protocol=2, startup_phase="booting", boot_age_s=math.inf), lc.ALIVE_BOOTING),
    ([1, 2, 3], lc.INVALID),  # non-dict payload
]


def _expected_adapter(assessment, grace_s=None):
    """D1b table re-expressed from the plan (independent of the adapter)."""
    grace = lc.BOOT_GRACE_S if grace_s is None else grace_s
    state = assessment.state
    if state == lc.UNREACHABLE:
        return False, "livez_unreachable"
    if state == lc.INVALID:
        if assessment.detail == "non_dict":
            return False, "invalid_payload"
        return False, "contract_invalid"
    if state == lc.ALIVE_LEGACY:
        return False, "protocol_1_legacy"
    if state == lc.ALIVE_READY:
        return False, "already_ready"
    if state == lc.ALIVE_STOPPING:
        if assessment.boot_age_s is None:
            return False, "invalid_boot_age"
        return False, "boot_grace_expired"
    if assessment.boot_age_s is None:
        return False, "invalid_boot_age"
    if assessment.boot_age_s >= grace:
        return False, "boot_grace_expired"
    return True, "booting_phase={}_age={:.1f}s".format(
        assessment.phase, assessment.boot_age_s
    )


@pytest.mark.parametrize("payload,expected_state", CASES)
def test_all_surfaces_agree(payload, expected_state):
    assessment = lc.classify_livez(lc.fetch_result_ok(payload))
    assert assessment.state == expected_state

    # rcs adapter surface vs the D1b table.
    assert lc.rcs_skip_verdict(assessment) == _expected_adapter(assessment)

    # helper surface (fetch seam stubbed to the same payload).
    with mock.patch.object(
        as_bat_helpers, "_fetch_livez", return_value=lc.fetch_result_ok(payload)
    ):
        helper_assessment = as_bat_helpers._livez_assessment("http://stub/livez")
        assert helper_assessment.state == assessment.state
        assert helper_assessment.is_alive == assessment.is_alive
        assert (as_bat_helpers._livez_ok("http://stub/livez") is not None) == (
            assessment.is_alive
        )

    # watchdog surface: probe()'s livez_ok must equal contract is_alive.
    with mock.patch.object(
        watchdog_a, "_read_json", side_effect=[payload, _health()]
    ):
        _classification, _generation, _detail, _health_out, livez_ok = (
            watchdog_a.probe()
        )
    assert livez_ok == assessment.is_alive


def test_unreachable_agrees_everywhere():
    assessment = lc.classify_livez(lc.fetch_result_error("down"))
    assert assessment.state == lc.UNREACHABLE
    assert lc.rcs_skip_verdict(assessment) == (False, "livez_unreachable")
    assert not assessment.is_alive


def test_grace_per_call_preserved():
    payload = _p(livez_protocol=2, startup_phase="booting", boot_age_s=100)
    assessment = lc.classify_livez(lc.fetch_result_ok(payload))
    assert lc.rcs_skip_verdict(assessment, grace_s=60) == (
        False,
        "boot_grace_expired",
    )
    assert lc.rcs_skip_verdict(assessment, grace_s=600)[0] is True
