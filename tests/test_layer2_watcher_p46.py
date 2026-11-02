"""P-46 observable contracts: durable dedupe, rotation, bounded read-only capture."""
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

ROOT = Path(__file__).resolve().parents[1]


def module(name):
    spec = importlib.util.spec_from_file_location(name, ROOT / "bin" / (name + ".py"))
    obj = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(obj)
    return obj


watch = module("layer2_watch")
calibration = module("calibrate_layer2")


@pytest.fixture
def configured(tmp_path):
    config = dict(log_path="input.log", state_path="observer/state.json",
                  archive_dir="archive", own_log="observer/watch.jsonl",
                  db_path="sessions.db", cache_dir="cache",
                  poll_seconds=.05, window_seconds=30, quiet_seconds=60,
                  heartbeat_seconds=60, retention_hours=72, threshold=3,
                  min_free_bytes=1024, capture_timeout_seconds=30, start_at_end=False)
    path = tmp_path / "config.json"
    path.write_text(json.dumps(config), encoding="utf-8")
    cfg = watch.load_config(path)
    cfg["log_path"].write_text("", encoding="utf-8")
    return cfg


def append(cfg, text):
    with cfg["log_path"].open("a", encoding="utf-8") as stream:
        stream.write(text)


def test_episode_persists_across_restart_and_quiet_rearms(configured):
    cfg, now, calls = configured, [1000.0], []
    def clock():
        return now[0]
    watcher = watch.Watcher(cfg, capture=lambda dest: calls.append(dest), clock=clock)
    append(cfg, "store contention\nbusy budget\nstore contention\nlock_hold>30s\n")
    watcher.poll()
    assert len(calls) == 1
    watcher = watch.Watcher(cfg, capture=lambda dest: calls.append(dest), clock=clock)
    watcher.poll()
    append(cfg, "lock_hold>30s\n")
    watcher.poll()
    assert len(calls) == 1
    now[0] += 61
    watcher.poll()
    append(cfg, "lock_hold>30s\n")
    watcher.poll()
    assert len(calls) == 2
    events = [json.loads(line)["event"] for line in cfg["own_log"].read_text().splitlines()]
    assert events.count("heartbeat") == 2


def test_partial_line_is_not_lost_at_restart(configured):
    cfg, calls = configured, []
    append(cfg, "lock_hold>")
    watch.Watcher(cfg, capture=calls.append).poll()
    assert not calls
    append(cfg, "30s\n")
    watch.Watcher(cfg, capture=calls.append).poll()
    assert len(calls) == 1


def test_rotation_drains_unread_cue_and_preserves_evidence(configured):
    cfg, calls = configured, []
    watcher = watch.Watcher(cfg, capture=calls.append)
    append(cfg, "before rotate\n")
    watcher.poll()
    append(cfg, "lock_hold>30s\n")
    rotated = cfg["log_path"].with_suffix(".log.1")
    cfg["log_path"].rename(rotated)
    cfg["log_path"].write_text("new file\n", encoding="utf-8")
    watcher.poll()
    assert len(calls) == 1
    archived = list(cfg["archive_dir"].glob("*rotation.log"))
    assert len(archived) == 1
    assert archived[0].read_bytes() == rotated.read_bytes()
    watch.Watcher(cfg, capture=calls.append).poll()
    assert len(calls) == 1


def test_truncate_and_mtime_reset_archive_observed_prefix(configured):
    cfg = configured
    watcher = watch.Watcher(cfg, capture=lambda _: None)
    original = "observed long line before truncate\n"
    append(cfg, original)
    watcher.poll()
    cfg["log_path"].write_text("reset\n", encoding="utf-8")
    os.utime(cfg["log_path"], (10, 10))
    watcher.poll()
    archived = list(cfg["archive_dir"].glob("*truncated-prefix.log"))
    assert len(archived) == 1 and archived[0].read_text() == original
    assert watcher.state["offset"] == len(cfg["log_path"].read_bytes())


def test_capture_failure_is_recorded_and_not_replayed(configured):
    cfg, calls = configured, []
    def fail(dest):
        calls.append(dest)
        raise TimeoutError("probe timeout")
    append(cfg, "lock_hold>30s\n")
    watch.Watcher(cfg, capture=fail).poll()
    watch.Watcher(cfg, capture=fail).poll()
    assert len(calls) == 1
    assert json.loads(calls[0].read_text())["status"] == "failed"


def test_pending_intent_after_crash_never_replays(configured):
    cfg = configured
    watcher = watch.Watcher(cfg)
    watcher.state["capture_intent"] = "interrupted.json"
    watcher.state["active"] = True
    watcher.save()
    resumed = watch.Watcher(cfg, capture=lambda _: pytest.fail("replayed"))
    assert resumed.state["capture_intent"] is None
    assert "interrupted_capture" in cfg["own_log"].read_text()


def test_low_space_does_not_advance_watermark(configured, monkeypatch):
    cfg, calls = configured, []
    watcher = watch.Watcher(cfg, capture=calls.append)
    append(cfg, "lock_hold>30s\n")
    class Full:
        free = 0
    monkeypatch.setattr(watch.shutil, "disk_usage", lambda _: Full())
    with pytest.raises(OSError, match="free space"):
        watcher.poll()
    assert watcher.state["offset"] == 0 and not calls


def test_exclusive_instance_lock(configured):
    lock = configured["state_path"].with_suffix(".lock")
    with watch.exclusive(lock):
        with pytest.raises(OSError):
            with watch.exclusive(lock):
                pytest.fail("second watcher acquired lock")


def test_calibration_requires_separation_and_derives_threshold(tmp_path):
    path = tmp_path / "calibration.log"
    path.write_text(
        "2026-10-31 00:00:01 busy budget\n" +
        "".join("2026-10-15 00:00:01 store contention\n" for _ in range(20)),
        encoding="utf-8")
    report = calibration.calibrate([path], "2026-10-15", "2026-10-31")
    assert report["threshold"] == 6  # ceil((1 + 10.5) / 2)
    assert len(report["sources"][0]["sha256"]) == 64
    with pytest.raises(ValueError, match="do not separate"):
        calibration.calibrate([path], "2026-10-31", "2026-10-15")


@pytest.mark.skipif(os.name != "nt", reason="PowerShell entrypoint integration")
def test_real_powershell_entry_capture_once_then_resume(configured):
    cfg = configured
    append(cfg, "LOCK_HOLDER_TRACE lock_hold>30s synthetic copied log\n")
    args = ["powershell.exe", "-NoProfile", "-NonInteractive", "-File",
            str(ROOT / "bin/watch_layer2.ps1"), "-Config", str(cfg["_config_path"]),
            "-PythonExe", sys.executable, "-MaxPolls", "1"]
    for _ in range(2):
        proc = subprocess.run(args, capture_output=True, text=True, timeout=45,
                              creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        assert proc.returncode == 0, proc.stdout + proc.stderr
    captures = list(cfg["archive_dir"].glob("*capture.json"))
    assert len(captures) == 1
    data = json.loads(captures[0].read_text())
    assert data.get("exit_code") == 0, data
    assert {"holders", "defender", "sync_backup_processes"} <= data["capture"].keys()
    assert cfg["log_path"].read_text().count("lock_hold>") == 1

def test_capture_does_not_block_polling_or_heartbeat(configured):
    import threading
    import time
    cfg, now = configured, [1000.0]
    entered, release = threading.Event(), threading.Event()
    def slow_capture(dest):
        entered.set()
        assert release.wait(3)
        dest.write_text("{}", encoding="utf-8")
    watcher = watch.Watcher(cfg, capture=slow_capture, clock=lambda: now[0], background=True)
    append(cfg, "lock_hold>30s\n")
    try:
        started = time.monotonic()
        watcher.poll()
        assert entered.wait(1)
        append(cfg, "another observed line\n")
        now[0] += 60
        watcher.poll()
        assert time.monotonic() - started < 1
        assert watcher.state["offset"] == cfg["log_path"].stat().st_size
        events = cfg["own_log"].read_text()
        assert events.count('"event": "heartbeat"') == 2
    finally:
        release.set()
        watcher.finish_capture(wait=True)
    assert watcher.state["capture_intent"] is None


def test_start_at_end_archives_only_observed_tail(configured):
    cfg = configured
    cfg["start_at_end"] = True
    append(cfg, "preexisting ignored line\n")
    watcher = watch.Watcher(cfg, capture=lambda _: None)
    watcher.poll()
    append(cfg, "observed tail\n")
    watcher.poll()
    cfg["log_path"].write_text("", encoding="utf-8")
    watcher.poll()
    archived = next(cfg["archive_dir"].glob("*truncated-prefix.log"))
    assert archived.read_text() == "observed tail\n"


def test_bad_watermark_fails_loud_instead_of_replaying(configured):
    cfg = configured
    cfg["state_path"].parent.mkdir()
    cfg["state_path"].write_text("{invalid", encoding="utf-8")
    with pytest.raises(ValueError):
        watch.Watcher(cfg)


def test_same_size_rewrite_detected_by_prefix(configured):
    cfg = configured
    append(cfg, "original message\n")
    watcher = watch.Watcher(cfg, capture=lambda _: None)
    watcher.poll()
    watcher.poll()  # stable prefix checkpoint
    before = cfg["log_path"].stat().st_size
    cfg["log_path"].write_text("replaced message\n", encoding="utf-8")
    assert cfg["log_path"].stat().st_size == before
    watcher.poll()
    archived = next(cfg["archive_dir"].glob("*truncated-prefix.log"))
    assert archived.read_text() == "original message\n"

def test_capture_finds_real_db_and_cache_handles_without_recording_other_files(configured, monkeypatch):
    import psutil
    cfg = configured
    cfg["cache_dir"].mkdir()
    db, cache, other = cfg["db_path"], cfg["cache_dir"] / "held.bin", cfg["db_path"].with_name("unrelated.bin")
    process = psutil.Process()
    process.info = {"pid": process.pid, "name": process.name()}
    # Bound only the enumerated set; open_files is the real Windows OS probe.
    monkeypatch.setattr(psutil, "process_iter", lambda attrs: [process])
    with db.open("wb") as a, cache.open("wb") as b, other.open("wb") as c:
        for handle in (a, b, c):
            handle.write(b"fixture")
            handle.flush()
        result = watch.capture_system(cfg)
    paths = {Path(item["path"]).resolve() for item in result["holders"]}
    assert db in paths and cache in paths and other not in paths
    assert result["scan_truncated"] is False
    assert all("cmdline" not in item for item in result["holders"])
