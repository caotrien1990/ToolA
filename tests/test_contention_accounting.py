"""M3 (checkpoint-contention Stage 2): durable attributed contention record.

Counting sits at post-call accounting (code_tools._log_usage): every
dispatched call whose final response carries one of the four contention
error names must be counted exactly once, aggregated in memory, and leave
bounded durable lines (first event immediately; exact-count summary at the
60 s window expiry) on the orchestrator.contention logger -- the record
USAGE_LOG's 1,000-row whole-file-rewrite channel cannot provide.
"""
from __future__ import annotations

import logging

import pytest

import code_tools as ct


# The four names, spelled out from the minting sites (the funnel
# _session_contention_fields, the close_session bypass mint, and the store
# co-mint) -- NOT copied from the aggregator constant, so the check below
# is an independent derivation.
EXPECTED_NAMES = {
    "SESSION_STORE_CONTENTION",
    "SESSION_STORE_COMMIT_PENDING",
    "TASK_CHECKPOINT_CONTENTION",
    "TASK_CHECKPOINT_COMMIT_PENDING",
}


@pytest.fixture(autouse=True)
def _clean_aggregator():
    ct._reset_contention_aggregator()
    yield
    ct._reset_contention_aggregator()


def _records(caplog):
    return [r for r in caplog.records if "SESSION_CONTENTION_RECORD" in r.getMessage()]


def test_contention_error_names_match_the_minting_sites():
    assert set(ct._CONTENTION_ERROR_NAMES) == EXPECTED_NAMES


def test_accounting_counts_storm_with_one_immediate_line(caplog, monkeypatch):
    calls = []

    def recorder(*args, **kwargs):
        calls.append((args, kwargs))
        return "producer-result"

    monkeypatch.setattr(ct._session_logs_module, "_log_usage", recorder)
    with caplog.at_level(logging.WARNING, logger="orchestrator.contention"):
        for _ in range(300):
            result = ct._log_usage(
                "read_file_range", {"project_path": "p"}, True, 5, "",
                error_name="SESSION_STORE_CONTENTION", agent_id="muse1",
            )
            assert result == "producer-result"
    assert len(calls) == 300  # the producer still ran exactly once per call
    lines = _records(caplog)
    assert len(lines) == 1, caplog.text  # first-event line only; no flood
    assert "kind=first" in lines[0].getMessage()
    snapshot = dict(ct._contention_buckets)
    assert len(snapshot) == 1
    (key, bucket), = snapshot.items()
    assert key == ("SESSION_STORE_CONTENTION", "read_file_range", "muse1")
    assert bucket["count"] == 300


def test_window_summary_flushes_exact_count_on_later_call(caplog):
    with caplog.at_level(logging.WARNING, logger="orchestrator.contention"):
        ct._note_contention_usage("tool_a", {}, "SESSION_STORE_CONTENTION", "muse1", now=1000.0)
        ct._note_contention_usage("tool_a", {}, "SESSION_STORE_CONTENTION", "muse1", now=1001.0)
        ct._note_contention_usage("tool_a", {}, "SESSION_STORE_CONTENTION", "muse1", now=1002.0)
        # A NON-contention call after the window expires triggers the sweep.
        ct._note_contention_usage("tool_b", {}, "SOME_OTHER_ERROR", "muse2", now=1061.0)
    lines = _records(caplog)
    assert len(lines) == 2, caplog.text
    summaries = [r.getMessage() for r in lines if "kind=summary" in r.getMessage()]
    assert len(summaries) == 1
    assert "count=3" in summaries[0]
    assert "window_start=" in summaries[0] and "window_end=" in summaries[0]
    assert ct._contention_buckets == {}


def test_each_contention_name_counts_and_others_do_not(caplog):
    with caplog.at_level(logging.WARNING, logger="orchestrator.contention"):
        for i, name in enumerate(sorted(EXPECTED_NAMES)):
            ct._note_contention_usage("t", {}, name, "a", now=2000.0 + i)
        ct._note_contention_usage("t", {}, "NOT_A_CONTENTION_NAME", "a", now=2010.0)
    assert set(ct._contention_buckets) == {(n, "t", "a") for n in EXPECTED_NAMES}
    assert len(_records(caplog)) == 4  # one first-event line per name


def test_hook_failure_never_breaks_accounting(monkeypatch):
    calls = []

    def recorder(*args, **kwargs):
        calls.append((args, kwargs))
        return "producer-result"

    monkeypatch.setattr(ct._session_logs_module, "_log_usage", recorder)

    def boom(*args, **kwargs):
        raise RuntimeError("aggregator exploded")

    monkeypatch.setattr(ct, "_note_contention_usage", boom)
    result = ct._log_usage(
        "tool", {}, True, 1, "",
        error_name="SESSION_STORE_CONTENTION", agent_id="x",
    )
    assert result == "producer-result"
    assert len(calls) == 1


def test_attribution_fallback_chain(caplog):
    with caplog.at_level(logging.WARNING, logger="orchestrator.contention"):
        ct._note_contention_usage("tool", {"session_id": "sess-9"}, "SESSION_STORE_CONTENTION", "", now=3000.0)
        ct._note_contention_usage("tool", {"agent_id": "param-agent"}, "SESSION_STORE_CONTENTION", "", now=3000.0)
        ct._note_contention_usage("tool", {}, "SESSION_STORE_CONTENTION", "", now=3000.0)
    agents = {key[2] for key in ct._contention_buckets}
    assert agents == {"sess-9", "param-agent", "system"}


def test_emitted_lines_never_carry_params(caplog):
    params = {"project_path": "D:/secret-path", "session_id": "sess-x", "content": "PAYLOAD-TEXT"}
    with caplog.at_level(logging.WARNING, logger="orchestrator.contention"):
        ct._note_contention_usage("tool", params, "TASK_CHECKPOINT_COMMIT_PENDING", "", now=4000.0)
    lines = _records(caplog)
    assert len(lines) == 1
    msg = lines[0].getMessage()
    assert "D:/secret-path" not in msg and "PAYLOAD-TEXT" not in msg
    assert "agent_id=sess-x" in msg  # attribution is the only params-derived field


def test_key_cap_folds_into_overflow_bucket(caplog):
    with caplog.at_level(logging.WARNING, logger="orchestrator.contention"):
        for i in range(300):
            ct._note_contention_usage(f"tool-{i}", {}, "SESSION_STORE_CONTENTION", "a", now=5000.0)
    buckets = ct._contention_buckets
    assert len(buckets) <= 257  # 256 named keys + the overflow bucket
    assert ("SESSION_STORE_CONTENTION", "__overflow__", "__overflow__") in buckets
    assert sum(b["count"] for b in buckets.values()) == 300
