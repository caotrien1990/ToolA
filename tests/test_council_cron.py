# pyright: reportMissingImports=false
"""Regression checks for the council cron scheduler.

Rewritten 2026-07-24: the previous version imported `orchestrator.council.cron` and a
flat save_job/claim_due/finish_run API. Neither has existed since the scheduler became
`orchestrator.council.cron_jobs.CouncilCronService`, so the module failed at COLLECTION
and took the whole pytest run down with it — the tests below had not executed in a long
time. They now target the API that actually ships.
"""
import tempfile
from pathlib import Path

import pytest

from orchestrator.council.cron_jobs import (
    CouncilCronService,
    CronJobError,
    JobNotFound,
    next_cron_at,
    next_schedule_at,
    parse_cron,
    parse_interval,
)

WORKERS = ("clauc", "codoc")


def _service(tmp: str) -> CouncilCronService:
    return CouncilCronService(Path(tmp) / "cron.db", workers=lambda: WORKERS)


def _payload(**over) -> dict:
    base = {
        "name": "nightly-digest",
        "prompt": "summarise the day",
        "workers": ["clauc"],
        "schedule": {"type": "interval", "value": "15m"},
    }
    base.update(over)
    return base


def test_parse_interval_units_and_bounds():
    assert parse_interval("15m") == 900
    assert parse_interval("60") == 60
    assert parse_interval("2h") == 7200
    # the floor is 60s: a 10s job would hammer the broker
    with pytest.raises(CronJobError):
        parse_interval("10s")
    with pytest.raises(CronJobError):
        parse_interval("never")


def test_cron_expression_advances_from_the_given_instant():
    parse_cron("*/5 * * * *")
    assert next_cron_at("*/5 * * * *", 100) > 100
    with pytest.raises(CronJobError):
        parse_cron("*/5 * * *")


def test_next_schedule_at_normalises_both_kinds():
    value, when = next_schedule_at("interval", "15m", 100)
    assert (value, when) == ("900", 1000)
    value, when = next_schedule_at("cron", "*/5   *  * * *", 100)
    assert value == "*/5 * * * *"  # whitespace collapsed
    assert when > 100
    with pytest.raises(CronJobError):
        next_schedule_at("weekly", "1", 100)


def test_job_crud_round_trip():
    with tempfile.TemporaryDirectory() as tmp:
        svc = _service(tmp)
        job = svc.create_job(_payload())
        assert job["workers"] == ["clauc"]
        assert job["worker"] == "clauc"
        assert job["schedule_type"] == "interval"
        assert job["schedule_value"] == "900"
        assert job["enabled"] is True
        assert job["next_run_at"]

        assert [item["id"] for item in svc.list_jobs()] == [job["id"]]
        assert svc.get_job(job["id"])["name"] == "nightly-digest"

        updated = svc.update_job(job["id"], _payload(name="renamed", workers=["clauc", "codoc"]))
        assert updated["name"] == "renamed"
        assert updated["workers"] == ["clauc", "codoc"]

        # delete is a soft delete: the row stops being listed and stops resolving
        svc.delete_job(job["id"])
        assert svc.list_jobs() == []
        with pytest.raises(JobNotFound):
            svc.get_job(job["id"])


def test_disabled_job_gets_no_next_run():
    with tempfile.TemporaryDirectory() as tmp:
        job = _service(tmp).create_job(_payload(enabled=False))
        assert job["enabled"] is False
        assert job["next_run_at"] is None


def test_validation_rejects_bad_payloads():
    with tempfile.TemporaryDirectory() as tmp:
        svc = _service(tmp)
        with pytest.raises(CronJobError):
            svc.create_job(_payload(surprise="x"))
        with pytest.raises(CronJobError):
            svc.create_job(_payload(workers=["nobody"]))
        with pytest.raises(CronJobError):
            svc.create_job(_payload(name=""))
        with pytest.raises(CronJobError):
            svc.create_job(_payload(timeout_sec=5))


def test_runs_table_starts_empty_and_is_scoped_per_job():
    with tempfile.TemporaryDirectory() as tmp:
        svc = _service(tmp)
        job = svc.create_job(_payload())
        assert svc.list_runs() == []
        assert svc.list_runs(job["id"]) == []
