"""Future-bump test (Sprint 5 net, plan D5).

A synthetic protocol 3 (and beyond) payload must be treated as a live,
current-generation daemon on every surface: aliveness is not an equality
check on the protocol number, and boot grace keeps working by phase/age.
This is the regression net for the 2026-10-23 drift class (server bumped
livez_protocol 1 -> 2 while consumers demanded == 1).
"""
from unittest import mock

import watchdog_a
from orchestrator.lifecycle import as_bat_helpers
from orchestrator.lifecycle import livez_contract as lc

BASE = {"ok": True, "service": "project-orchestrator", "generation": "g-next"}


def _p(**kw):
    payload = dict(BASE)
    payload.update(kw)
    return payload


def _health():
    return {
        "ok": True,
        "service": "project-orchestrator",
        "generation": "g-next",
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


def _assert_alive_everywhere(payload):
    assessment = lc.classify_livez(lc.fetch_result_ok(payload))
    assert assessment.is_alive
    with mock.patch.object(
        as_bat_helpers, "_fetch_livez", return_value=lc.fetch_result_ok(payload)
    ):
        assert as_bat_helpers._livez_assessment("http://stub/livez").is_alive
        assert as_bat_helpers._livez_ok("http://stub/livez") is not None
    with mock.patch.object(
        watchdog_a, "_read_json", side_effect=[payload, _health()]
    ):
        livez_ok = watchdog_a.probe()[4]
    assert livez_ok
    return assessment


def test_protocol3_ready_is_alive():
    assessment = _assert_alive_everywhere(
        _p(livez_protocol=3, startup_phase="ready", boot_age_s=5000)
    )
    assert assessment.state == lc.ALIVE_READY
    assert lc.rcs_skip_verdict(assessment) == (False, "already_ready")


def test_protocol3_booting_gets_grace():
    assessment = _assert_alive_everywhere(
        _p(livez_protocol=3, startup_phase="booting", boot_age_s=100)
    )
    assert assessment.state == lc.ALIVE_BOOTING
    assert lc.rcs_skip_verdict(assessment) == (
        True,
        "booting_phase=booting_age=100.0s",
    )


def test_protocol3_booting_past_grace_is_hung_not_dead():
    assessment = _assert_alive_everywhere(
        _p(livez_protocol=3, startup_phase="boot_write_sweep", boot_age_s=700)
    )
    assert assessment.state == lc.ALIVE_BOOTING
    assert lc.rcs_skip_verdict(assessment) == (False, "boot_grace_expired")


def test_protocol3_missing_phase_is_mismatch_alive():
    assessment = _assert_alive_everywhere(_p(livez_protocol=3, boot_age_s=100))
    assert assessment.state == lc.MISMATCH
    assert lc.rcs_skip_verdict(assessment)[0] is True


def test_protocol3_stopping_gets_no_grace():
    assessment = _assert_alive_everywhere(
        _p(livez_protocol=3, startup_phase="stopping", boot_age_s=100)
    )
    assert assessment.state == lc.ALIVE_STOPPING
    assert lc.rcs_skip_verdict(assessment) == (False, "boot_grace_expired")


def test_far_future_protocol_still_alive():
    assessment = _assert_alive_everywhere(
        _p(livez_protocol=99, startup_phase="ready_degraded", boot_age_s=10)
    )
    assert assessment.state == lc.ALIVE_READY
