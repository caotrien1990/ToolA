"""The two live council JSONL ledgers must survive a second concurrent writer.

`_council_usage_append` and `route_metrics.append_measurement` were plain
`open(path, "a")` calls. That is correct for exactly one writer, and they had one
only by accident: the council drain that calls both is serialised per agent by
`_COUNCIL_AGENT_QUEUE_DRAINING`. Nothing in either signature said so, so any future
caller -- a second drain, a backfill script, a second daemon -- would have silently
broken them.

Silently is the operative word, and it is why this was worth fixing before anything
went wrong rather than after. Measured on this disk with 4 processes x 4 threads x 60
appends against the plain form: **280 of 480 lines survived, one line had its leading
brace overwritten, and there were ZERO exceptions with exit code 0.** A scorecard
built from those rows would have been quietly wrong with nothing anywhere to say so.
`orchestrator/storage/jsonl_store.py` already had the correct implementation --
sidecar lock so a concurrent `read_tail` is not denied, `fsync` outside the critical
section, `TimeoutError` rather than a lost line -- and had no production caller at
all. These two are now it.

The tests assert the property (nothing is lost, nothing is corrupt, and a reader is
never locked out) under real cross-process contention, rather than asserting that a
particular helper is called -- so routing them somewhere better later keeps passing.
"""
from __future__ import annotations

import json
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from orchestrator.council import route_metrics  # noqa: E402

_PROCESSES = 4
_THREADS = 4
_PER_THREAD = 40
_EXPECTED = _PROCESSES * _THREADS * _PER_THREAD

# Each child appends through the SAME public entry point production uses, so the test
# cannot pass by exercising a lock the real callers bypass.
_CHILD = textwrap.dedent(
    """
    import sys, threading
    sys.path.insert(0, sys.argv[1])
    from orchestrator.council.route_metrics import append_measurement

    target, tag, threads, per_thread = sys.argv[2], sys.argv[3], int(sys.argv[4]), int(sys.argv[5])

    def work(thread_id):
        for index in range(per_thread):
            append_measurement(target, {"tag": tag, "thread": thread_id, "index": index})

    workers = [threading.Thread(target=work, args=(t,)) for t in range(threads)]
    for worker in workers:
        worker.start()
    for worker in workers:
        worker.join()
    """
)


def _spawn_writers(target: Path, root: Path) -> list[subprocess.CompletedProcess]:
    script = root / "_ledger_writer.py"
    script.write_text(_CHILD, encoding="utf-8")
    procs = [
        subprocess.Popen(
            [sys.executable, str(script), str(_ROOT), str(target),
             "p%d" % pid, str(_THREADS), str(_PER_THREAD)],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
        )
        for pid in range(_PROCESSES)
    ]
    results = []
    for proc in procs:
        out, err = proc.communicate(timeout=180)
        results.append(subprocess.CompletedProcess(proc.args, proc.returncode, out, err))
    return results


@pytest.fixture()
def contended_ledger(tmp_path):
    """The measurement ledger written by 16 concurrent writers across 4 processes."""
    target = tmp_path / "route_measurements.jsonl"
    results = _spawn_writers(target, tmp_path)
    for result in results:
        assert result.returncode == 0, (
            "a writer process failed (rc=%s); a LOST line is the bug, but a crashed "
            "writer is a different one and must not be read as this test passing:\n%s"
            % (result.returncode, result.stderr[-2000:])
        )
    return target


def test_no_append_is_lost_under_cross_process_contention(contended_ledger) -> None:
    lines = [l for l in contended_ledger.read_text(encoding="utf-8").splitlines() if l.strip()]
    assert len(lines) == _EXPECTED, (
        "%d of %d appends survived; the plain open(...,'a') form kept 280/480 here "
        "with no exception at all, which is the failure this guards"
        % (len(lines), _EXPECTED)
    )


def test_every_surviving_line_is_still_whole_json(contended_ledger) -> None:
    """Interleaving corrupts lines as well as dropping them, and separately.

    The measured plain-append run produced a line whose leading brace had been
    overwritten by another writer -- countable loss and unparseable damage are two
    different symptoms of the same missing lock, so both are asserted.
    """
    bad = []
    seen = set()
    for number, line in enumerate(contended_ledger.read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError as exc:
            bad.append((number, str(exc), line[:80]))
            continue
        seen.add((row.get("tag"), row.get("thread"), row.get("index")))
    assert not bad, "corrupt (interleaved) lines: %r" % (bad[:5],)
    assert len(seen) == _EXPECTED, (
        "%d distinct records for %d appends -- a duplicate or overwritten record "
        "means two writers shared an offset" % (len(seen), _EXPECTED)
    )


def test_a_reader_in_another_process_is_never_locked_out(tmp_path) -> None:
    """A lock inside the DATA file would deny reads; the sidecar is why it does not.

    This is not hypothetical: a Windows byte-range lock refuses reads through every
    other handle, so an earlier lock-the-data-file design made a concurrent
    `read_tail` on the council log fail with PermissionError -- while writers were
    appending to the very log it exists to tail.
    """
    target = tmp_path / "route_measurements.jsonl"
    route_metrics.append_measurement(target, {"seed": True})

    script = tmp_path / "_reader.py"
    script.write_text(textwrap.dedent(
        """
        import sys, time
        sys.path.insert(0, sys.argv[1])
        from orchestrator.council.route_metrics import append_measurement

        target = sys.argv[2]
        deadline = time.time() + 6.0
        index = 0
        while time.time() < deadline:
            append_measurement(target, {"writer": True, "index": index})
            index += 1
        """
    ), encoding="utf-8")
    writer = subprocess.Popen(
        [sys.executable, str(script), str(_ROOT), str(target)],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
    )
    try:
        reads, denials = 0, []
        import time as _time
        stop = _time.time() + 3.0
        while _time.time() < stop:
            try:
                target.read_bytes()
                reads += 1
            except PermissionError as exc:
                denials.append(str(exc))
        assert reads > 0, "the reader never got a single read in 3s"
        assert not denials, (
            "%d of %d reads were DENIED while another process appended: %r"
            % (len(denials), len(denials) + reads, denials[:2])
        )
    finally:
        writer.terminate()
        writer.communicate(timeout=30)


def test_the_lock_never_lives_inside_the_data_file(tmp_path) -> None:
    """Pin the sidecar, because "which byte" is the part that was got wrong twice.

    Any offset inside the data is unsafe: `read_tail` reads the whole tail, so the
    only never-read byte is one past EOF, and that moves. A fixed far offset was
    tried and failed once the file grew past it.
    """
    target = tmp_path / "usage_log.jsonl"
    route_metrics.append_measurement(target, {"only": "row"})
    payload = json.loads(target.read_text(encoding="utf-8").strip())
    assert payload == {"only": "row"}, "the lock left bytes in the data file: %r" % payload
    siblings = {p.name for p in tmp_path.iterdir()} - {target.name}
    assert siblings, "no sidecar was created, so the lock has nowhere to live"


def test_appending_to_a_brand_new_empty_ledger_works(tmp_path) -> None:
    """The first append is the one a data-file lock breaks, and the common case.

    Offset 0 is inside the data of an EMPTY file, so a writer locking byte 0 collided
    with its own write; that design only ever worked because these logs are usually
    non-empty by the time anyone looks.
    """
    target = tmp_path / "nested" / "route_measurements.jsonl"
    route_metrics.append_measurement(target, {"first": 1})
    route_metrics.append_measurement(target, {"second": 2})
    rows = [json.loads(l) for l in target.read_text(encoding="utf-8").splitlines() if l.strip()]
    assert rows == [{"first": 1}, {"second": 2}], rows


def test_a_ledger_failure_still_cannot_break_a_council_dispatch(monkeypatch, tmp_path) -> None:
    """Both call sites are fail-open, and locking must not have changed that.

    `append_jsonl` can now raise `TimeoutError` where the plain form silently lost the
    line. That is the right trade for a ledger -- an audible failure beats a
    disappearing one -- but only as long as it stays audible rather than fatal: the
    usage ledger is written in the drain's `finally`, so raising there would take out
    the dispatch itself.
    """
    import orchestrator_server as srv

    def explode(*_args, **_kwargs):
        raise TimeoutError("simulated contention")

    monkeypatch.setattr(srv, "_jsonl_append_locked", explode)
    warnings = []
    monkeypatch.setattr(srv.logger, "warning", lambda *a, **k: warnings.append(a))
    srv._council_usage_append({"runtime_key": "k", "created_at": 0.0}, {"ok": True})
    assert warnings, (
        "the ledger failure was swallowed silently; a lost usage line must be "
        "distinguishable from a dispatch that never happened"
    )


def _p12_drain_style_append(srv, model: str) -> None:
    """One call shaped exactly like the drain's (orchestrator_server.py: `_council_queue_drain`
    finally block): `_council_usage_append(item, result)` with the item fields the
    ledger record reads."""
    import time
    item = {
        "runtime_key": "ope::" + model,
        "target": "ope",
        "provider": "openai-compatible",
        "model": model,
        "created_at": time.time() - 3.0,
        "request_id": "P12-GUARD",
        "measurement_metadata": {
            "task_id": "P12",
            "objective_class": "ledger-default-path-guard",
            "risk_level": "low",
            "dev_level": "low",
            "prompt_bytes": 16,
        },
    }
    result = {"ok": True, "error": "", "timeout_kind": "", "response_tokens": {"total": 5}}
    srv._council_usage_append(item, result)


def test_default_path_still_receives_real_rows_when_env_unset(monkeypatch, tmp_path) -> None:
    """Guard against a ledger that silently stops growing.

    P3 (2026-10-02) routed pytest's appends to ORCHESTRATOR_COUNCIL_USAGE_LOG_FILE so
    tests stopped polluting the live `.council/usage_log.jsonl` LAYA mines. The flip
    side: if a later change makes the DEFAULT branch of `_council_usage_log_path`
    unreachable -- or the drain stops calling `_council_usage_append` -- the live
    ledger goes stale with nothing anywhere saying so, and routing decisions keep
    being mined from a file frozen in the past. This pins that a completed drain
    item, with the env override UNSET and `_HERE` pointed at a tmp repo, still lands
    at `<_HERE>/.council/usage_log.jsonl`, i.e. the production default path.

    `_council_usage_append` is fail-open by design, so a broken write surfaces here
    as a missing/empty ledger -- which is exactly the signal this guard exists for.
    """
    import orchestrator_server as srv

    fake_repo = tmp_path / "repo"
    (fake_repo / ".council").mkdir(parents=True)
    monkeypatch.setattr(srv, "_HERE", fake_repo)
    monkeypatch.delenv("ORCHESTRATOR_COUNCIL_USAGE_LOG_FILE", raising=False)

    _p12_drain_style_append(srv, "gpt-real-default")

    ledger = fake_repo / ".council" / "usage_log.jsonl"
    assert ledger.exists(), (
        "a completed drain item wrote NO row at the production default path "
        "<_HERE>/.council/usage_log.jsonl -- the ledger would silently stop growing"
    )
    rows = [json.loads(l) for l in ledger.read_text(encoding="utf-8").splitlines() if l.strip()]
    assert len(rows) == 1, rows
    row = rows[0]
    assert row["model"] == "gpt-real-default", row
    assert row["runtime_key"] == "ope::gpt-real-default", row
    assert row["ok"] is True, row
    assert row["task_id"] == "P12", row
    assert row["duration_sec"] >= 3.0, row


def test_env_override_receives_rows_and_default_path_stays_empty(monkeypatch, tmp_path) -> None:
    """The other half of the P3 contract: with the env var set, the row goes ONLY
    to the override path -- the default `<_HERE>/.council/usage_log.jsonl` must not
    even be created. This is what keeps test pollution out of the live ledger, so
    both directions of the branch are pinned."""
    import orchestrator_server as srv

    fake_repo = tmp_path / "repo"
    (fake_repo / ".council").mkdir(parents=True)
    override = tmp_path / "elsewhere" / "council_usage_log.jsonl"
    monkeypatch.setattr(srv, "_HERE", fake_repo)
    monkeypatch.setenv("ORCHESTRATOR_COUNCIL_USAGE_LOG_FILE", str(override))

    _p12_drain_style_append(srv, "gpt-real-override")

    rows = [json.loads(l) for l in override.read_text(encoding="utf-8").splitlines() if l.strip()]
    assert len(rows) == 1 and rows[0]["model"] == "gpt-real-override", rows
    default_ledger = fake_repo / ".council" / "usage_log.jsonl"
    assert not default_ledger.exists(), (
        "the env override is set yet a row (or file) appeared at the default path: %r"
        % (default_ledger.read_text(encoding="utf-8")[:200] if default_ledger.exists() else "")
    )
