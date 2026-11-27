"""P-46: standalone, read-only observer; writes only its own state/evidence.

No daemon API dependency: the daemon may be hung at the incident being captured.
No evidence deletion; retention is at least 72h, with disk exhaustion failing loud.
"""
from __future__ import annotations

import argparse
from collections import deque
from contextlib import contextmanager
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import threading
import time
import uuid

PATTERN = re.compile(r"store contention|busy budget", re.I)
DATE = re.compile(r"^(\d{4}-\d\d-\d\d)[ T](\d\d:\d\d:\d\d)")
CREATE_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)


def stamp():
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S.%fZ")


def atomic_json(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".pending")
    with tmp.open("w", encoding="utf-8") as stream:
        json.dump(data, stream, indent=2, sort_keys=True)
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(tmp, path)


@contextmanager
def exclusive(path):
    """OS lock, automatically released after termination; stale file is harmless."""
    path.parent.mkdir(parents=True, exist_ok=True)
    stream = path.open("a+b")
    try:
        if stream.tell() == 0:
            stream.write(b"0")
            stream.flush()
        stream.seek(0)
        if os.name == "nt":
            import msvcrt
            msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl
            fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
        yield
    finally:
        stream.close()


def load_config(path):
    path = Path(path).resolve()
    cfg = json.loads(path.read_text(encoding="utf-8-sig"))
    for key in ("log_path", "state_path", "archive_dir", "own_log", "db_path", "cache_dir"):
        cfg[key] = (path.parent / cfg[key]).resolve()
    if not 0 < cfg["poll_seconds"] <= 5:
        raise ValueError("poll_seconds must be in (0,5]")
    if cfg["window_seconds"] != 30 or cfg["quiet_seconds"] < 60:
        raise ValueError("30s trigger window and >=60s quiet are required")
    if cfg["heartbeat_seconds"] != 60 or cfg["retention_hours"] < 72:
        raise ValueError("60s heartbeat and >=72h retention are required")
    if type(cfg["threshold"]) is not int or cfg["threshold"] < 1:
        raise ValueError("positive calibrated integer threshold is required")
    if cfg["min_free_bytes"] < 0 or not 0 < cfg["capture_timeout_seconds"] <= 45:
        raise ValueError("invalid storage/capture budget")
    # Observed inputs cannot also be an output; never overwrite the daemon log/DB.
    outputs = (cfg["state_path"], cfg["own_log"])
    for dest in (*outputs, cfg["archive_dir"]):
        if dest in (cfg["log_path"], cfg["db_path"], cfg["cache_dir"]):
            raise ValueError("observer output collides with observed input")
    if outputs[0] == outputs[1] or cfg["archive_dir"] in outputs:
        raise ValueError("observer outputs collide")
    cfg["_config_path"] = path
    return cfg


class Watcher:
    def __init__(self, cfg, *, capture=None, clock=time.time, background=None):
        self.cfg, self.clock = cfg, clock
        self.capture = capture or self._capture
        self.background = (capture is None) if background is None else background
        self.capture_thread = None
        self.state_path = cfg["state_path"]
        self.mirror = self.state_path.with_suffix(".segment")
        self.cfg["archive_dir"].mkdir(parents=True, exist_ok=True)
        self.state_path.parent.mkdir(parents=True, exist_ok=True)
        self.cfg["own_log"].parent.mkdir(parents=True, exist_ok=True)
        if self.state_path.exists():
            self.state = json.loads(self.state_path.read_text(encoding="utf-8"))
            if self.state["source"] != str(cfg["log_path"]):
                raise ValueError("watermark belongs to a different log")
        else:
            self.state = dict(version=1, source=str(cfg["log_path"]), identity=None,
                              offset=0, mirror_start=0, prefix="", prefix_len=0, mtime_ns=0,
                              active=False, last_match=None, hits=[], heartbeat=None,
                              capture_intent=None)
        self.hits = deque(self.state["hits"])
        # An intent persisted before a crash is never replayed blindly.
        if self.state.get("capture_intent"):
            self.log("interrupted_capture", intent=self.state["capture_intent"])
            self.state["capture_intent"] = None
            self.save()

    def log(self, event, **data):
        with self.cfg["own_log"].open("a", encoding="utf-8") as stream:
            stream.write(json.dumps(dict(time=self.clock(), event=event, **data)) + "\n")

    def space(self, size=0):
        if shutil.disk_usage(self.cfg["archive_dir"]).free < self.cfg["min_free_bytes"] + size:
            raise OSError("insufficient free space for layer2 evidence")

    def save(self):
        self.state["hits"] = list(self.hits)
        atomic_json(self.state_path, self.state)

    def _archive(self, source, kind):
        if not source.exists():
            self.log("archive_unavailable", source=str(source), kind=kind)
            return
        self.space(source.stat().st_size)
        dest = self.cfg["archive_dir"] / (stamp() + "-" + uuid.uuid4().hex[:10] + "-" + kind + ".log")
        shutil.copyfile(source, dest)
        self.log("archived", path=str(dest), source=str(source), kind=kind)

    def _capture(self, dest):
        # Direct child keeps the timeout attached to the actual scanner, not
        # a PowerShell wrapper whose child could survive wrapper termination.
        proc = subprocess.run(
            [sys.executable, str(Path(__file__).resolve()), "--capture",
             "--config", str(self.cfg["_config_path"])],
            capture_output=True, text=True, encoding="utf-8", errors="replace",
            stdin=subprocess.DEVNULL,  # TESTGAPS-1124: WinError 6 family
            timeout=self.cfg["capture_timeout_seconds"], creationflags=CREATE_NO_WINDOW,
        )
        data = {"exit_code": proc.returncode, "stderr": proc.stderr[-2000:]}
        try:
            data["capture"] = json.loads(proc.stdout)
        except ValueError:
            data["invalid_stdout"] = proc.stdout[-2000:]
        atomic_json(dest, data)
        if proc.returncode:
            raise RuntimeError("capture process failed: " + str(proc.returncode))

    def _line(self, raw, now):
        text = raw.decode("utf-8", errors="replace")
        hit, cue = bool(PATTERN.search(text)), "lock_hold>" in text
        if not (hit or cue):
            return
        # Event timestamps make a backlog behave like the original log, not a
        # storm compressed into one polling instant. Untimestamped cues use now.
        match = DATE.match(text)
        when = datetime.fromisoformat(" ".join(match.groups())).timestamp() if match else now
        previous = self.state["last_match"]
        when = max(when, previous) if previous is not None else when
        if previous is not None and when - previous >= self.cfg["quiet_seconds"]:
            self.state["active"] = False
            self.hits.clear()
        self.state["last_match"] = when
        while self.hits and when - self.hits[0] > self.cfg["window_seconds"]:
            self.hits.popleft()
        if hit:
            self.hits.append(when)
        trigger = cue or len(self.hits) >= self.cfg["threshold"]
        if trigger and not self.state["active"]:
            if self.capture_thread is not None and self.capture_thread.is_alive():
                self.log("capture_deferred", reason="previous capture still running")
                return
            self.space()
            dest = self.cfg["archive_dir"] / (stamp() + "-" + uuid.uuid4().hex + "-capture.json")
            self.state["active"] = True
            self.state["capture_intent"] = str(dest)
            # Persist BOTH watermark and episode before invoking any child.
            # If killed during capture, report an interrupted attempt, don't replay.
            self.save()
            self.log("capture_start", path=str(dest), reason="lock_hold" if cue else "storm")
            if self.background:
                self.capture_thread = threading.Thread(
                    target=self._capture_task, args=(dest,), name="layer2-capture", daemon=True)
                self.capture_thread.start()
            else:
                self._capture_task(dest)
                self.state["capture_intent"] = None
                self.save()

    def _capture_task(self, dest):
        try:
            self.capture(dest)
        except Exception as exc:
            atomic_json(dest, {"capture_error": repr(exc), "status": "failed"})
            self.log("capture_failed", error=repr(exc), path=str(dest))
        else:
            self.log("capture_complete", path=str(dest))

    def finish_capture(self, *, wait=False):
        if self.capture_thread is not None:
            if wait:
                self.capture_thread.join(self.cfg["capture_timeout_seconds"] + 2)
                if self.capture_thread.is_alive():
                    raise TimeoutError("capture did not terminate within its budget")
            if not self.capture_thread.is_alive():
                self.capture_thread = None
                self.state["capture_intent"] = None
                self.save()

    def _read(self, path, now):
        with path.open("rb") as stream:
            stream.seek(self.state["offset"])
            chunk = stream.read(1024 * 1024)
        end = chunk.rfind(b"\n") + 1
        if not end:
            if len(chunk) == 1024 * 1024:
                raise ValueError("log line exceeds 1MiB; watermark not advanced")
            return
        chunk = chunk[:end]
        self.space(len(chunk))
        with self.mirror.open("r+b" if self.mirror.exists() else "w+b") as shadow:
            shadow.seek(self.state["offset"] - self.state["mirror_start"])
            shadow.write(chunk)
            shadow.truncate()
            shadow.flush()
            os.fsync(shadow.fileno())
        for line in chunk.splitlines(keepends=True):
            self.state["offset"] += len(line)
            self._line(line, now)

    def poll(self):
        self.finish_capture()
        now = self.clock()
        path = self.cfg["log_path"]
        # Missing while logger rotates is transient; state remains unchanged.
        try:
            stat = path.stat()
        except FileNotFoundError:
            self.log("source_missing", path=str(path))
        else:
            identity = [stat.st_dev, stat.st_ino]
            with path.open("rb") as stream:
                prior_prefix = stream.read(self.state["prefix_len"])
            reset = self.state["identity"] is not None and (
                identity != self.state["identity"] or stat.st_size < self.state["offset"]
                or stat.st_mtime_ns < self.state["mtime_ns"]
                or hashlib.sha256(prior_prefix).hexdigest() != self.state["prefix"])
            if reset:
                rotated = None
                for candidate in path.parent.glob(path.name + ".*"):
                    try:
                        old = candidate.stat()
                        if [old.st_dev, old.st_ino] == self.state["identity"]:
                            rotated = candidate
                            break
                    except OSError:
                        continue
                if rotated is not None:
                    # Drain the tail before archiving, including cues written
                    # since the previous poll. Bound each read, not total file size.
                    while rotated.stat().st_size - self.state["offset"] > 0:
                        before = self.state["offset"]
                        self._read(rotated, now)
                        if self.state["offset"] == before:
                            break
                    self._archive(rotated, "rotation")
                else:
                    self._archive(self.mirror, "truncated-prefix")
                self.log("rotation", previous_identity=self.state["identity"], identity=identity)
                self.state["offset"] = 0
                self.state["mirror_start"] = 0
                self.mirror.write_bytes(b"")
                self.hits.clear()
                # Preserve active episode; rotation alone isn't 60 seconds quiet.
            elif self.state["identity"] is None and self.cfg.get("start_at_end", True):
                self.state["offset"] = stat.st_size
                self.state["mirror_start"] = stat.st_size
            self.state["identity"] = identity
            self.state["mtime_ns"] = stat.st_mtime_ns
            self.state["prefix_len"] = min(stat.st_size, 256, self.state["offset"])
            with path.open("rb") as stream:
                self.state["prefix"] = hashlib.sha256(stream.read(self.state["prefix_len"])).hexdigest()
            self._read(path, now)
        previous = self.state["last_match"]
        caught_up = not path.exists() or path.stat().st_size <= self.state["offset"]
        if caught_up and previous is not None and now - previous >= self.cfg["quiet_seconds"]:
            self.state["active"] = False
            self.hits.clear()
        heartbeat = self.state["heartbeat"]
        if heartbeat is None or now - heartbeat >= self.cfg["heartbeat_seconds"]:
            self.log("heartbeat", offset=self.state["offset"], active=self.state["active"])
            self.state["heartbeat"] = now
        self.save()


def capture_system(cfg):
    """Read-only external probe; no handles closed, exclusions changed or kills."""
    result = {"time_utc": stamp(), "holders": [], "sync_backup_processes": [],
              "handle_scan": "unknown", "denied_processes": 0, "defender": "unknown"}
    db = os.path.normcase(str(cfg["db_path"]))
    cache = os.path.normcase(str(cfg["cache_dir"]))
    sync = re.compile(r"onedrive|dropbox|googledrive|drivefs|backup|syncthing|acronis", re.I)
    try:
        import psutil
        deadline = time.monotonic() + 12
        processes = list(psutil.process_iter(["pid", "name"]))
        result["sync_backup_processes"] = [dict(p.info) for p in processes
                                           if sync.search(p.info.get("name") or "")]
        # Prioritize likely DB users; explicitly report incomplete coverage.
        processes.sort(key=lambda p: not re.search(
            r"python|sqlite|orchestrator", p.info.get("name") or "", re.I))
        result["processes_total"] = len(processes)
        result["processes_scanned"] = 0
        result["scan_truncated"] = False
        for process in processes:
            if time.monotonic() >= deadline:
                result["scan_truncated"] = True
                break
            result["processes_scanned"] += 1
            info = dict(process.info)
            # psutil 7.2.2 on Windows can UNDER-REPORT a live handle table with
            # no exception: each duplicated handle is named on its own thread
            # with THREAD_TIMEOUT=100ms; on timeout psutil TerminateThread's it
            # and skips the handle silently (return 0 = success). A loaded CI
            # runner (Defender minifilter on freshly written files, handle-table
            # pressure after 2000+ tests) can therefore yield an empty/partial
            # list, or a transient AccessDenied/OSError. Retry the probe a few
            # times with a short pause, bounded by the scan deadline so retries
            # cannot starve later processes. A persistently denied process is
            # counted once, after its retries; NoSuchProcess is not retried.
            opened_files = None
            for attempt in range(3):
                try:
                    opened_files = process.open_files()
                except psutil.NoSuchProcess:
                    result["denied_processes"] += 1
                    break
                except (psutil.AccessDenied, OSError):
                    if attempt == 2 or time.monotonic() >= deadline:
                        result["denied_processes"] += 1
                        break
                    time.sleep(0.1 * (attempt + 1))
                    continue
                if opened_files or attempt == 2 or time.monotonic() >= deadline:
                    break
                time.sleep(0.1 * (attempt + 1))
            for opened in opened_files or ():
                path = os.path.normcase(os.path.abspath(opened.path))
                is_db = path in (db, db + "-wal", db + "-shm", db + "-journal")
                in_cache = path == cache or path.startswith(cache + os.sep)
                if is_db or in_cache:
                    result["holders"].append(dict(info, path=opened.path, database=is_db))
        result["handle_scan"] = "psutil.open_files (best effort; not exhaustive kernel handles)"
    except ImportError:
        result["handle_scan"] = "unknown: psutil unavailable"
    if os.name == "nt":
        try:
            probe = subprocess.run(
            ["powershell.exe", "-NoProfile", "-NonInteractive", "-Command",
             "$ErrorActionPreference='Stop'; (Get-MpPreference).ExclusionPath | ConvertTo-Json -Compress"],
            capture_output=True, text=True, encoding="utf-8", errors="replace",
            stdin=subprocess.DEVNULL,  # TESTGAPS-1124: WinError 6 family
                timeout=10, creationflags=CREATE_NO_WINDOW)
        except subprocess.TimeoutExpired:
            result["defender"] = {"error": "read-only Get-MpPreference timed out"}
            return result
        result["defender"] = {"exit_code": probe.returncode, "exclusion_paths": None}
        if probe.returncode == 0:
            result["defender"]["exclusion_paths"] = json.loads(probe.stdout or "null")
        else:
            result["defender"]["error"] = probe.stderr[-1500:]
    return result


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", required=True)
    parser.add_argument("--max-polls", type=int, default=0, help="0 = standing process")
    parser.add_argument("--capture", action="store_true")
    args = parser.parse_args(argv)
    cfg = load_config(args.config)
    if args.capture:
        print(json.dumps(capture_system(cfg), ensure_ascii=True))
        return 0
    with exclusive(cfg["state_path"].with_suffix(".lock")):
        watcher = Watcher(cfg)
        watcher.log("started", pid=os.getpid())
        count = 0
        try:
            while True:
                watcher.poll()
                count += 1
                if args.max_polls and count >= args.max_polls:
                    watcher.finish_capture(wait=True)
                    return 0
                time.sleep(cfg["poll_seconds"])
        except Exception as exc:
            watcher.log("fatal", error=repr(exc))
            raise


if __name__ == "__main__":
    raise SystemExit(main())
