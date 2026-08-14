from __future__ import annotations

import asyncio
import json
import re
import sqlite3
import threading
import time
import uuid
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Awaitable, Callable

from orchestrator.council.identity import canonical_id


class CronJobError(ValueError):
    error_name = "INVALID_CRON_JOB"


class JobNotFound(CronJobError):
    error_name = "CRON_JOB_NOT_FOUND"


class RunConflict(CronJobError):
    error_name = "CRON_JOB_OVERLAP"


_INTERVAL_RE = re.compile(r"^([1-9]\d*)\s*([smhd]?)$", re.I)
_FIELD_RE = re.compile(r"^(?:\*|\d+(?:-\d+)?)(?:/\d+)?$")
_ACTIVE_RUN_STATES = ("queued", "running")


def _now() -> float:
    return time.time()


def _iso(value: float | None) -> str | None:
    if value is None:
        return None
    return datetime.fromtimestamp(float(value), timezone.utc).isoformat().replace("+00:00", "Z")


def _as_bool(value: Any, name: str) -> bool:
    if isinstance(value, bool):
        return value
    if value in (0, 1):
        return bool(value)
    raise CronJobError(f"{name} must be boolean")


def parse_interval(value: Any) -> int:
    match = _INTERVAL_RE.fullmatch(str(value).strip())
    if not match:
        raise CronJobError("interval must be seconds or use s/m/h/d, for example 15m")
    amount = int(match.group(1))
    multiplier = {"": 1, "s": 1, "m": 60, "h": 3600, "d": 86400}[match.group(2).lower()]
    seconds = amount * multiplier
    if not 60 <= seconds <= 604800:
        raise CronJobError("interval must be between 60 seconds and 7 days")
    return seconds


def _parse_cron_field(expr: str, low: int, high: int, *, dow: bool = False) -> tuple[set[int], bool]:
    expr = expr.strip()
    if not expr:
        raise CronJobError("cron field cannot be empty")
    values: set[int] = set()
    wildcard = expr == "*"
    for raw in expr.split(","):
        token = raw.strip()
        if token == "*":
            start, end, step = low, high, 1
        else:
            if not _FIELD_RE.fullmatch(token):
                raise CronJobError(f"unsupported cron token: {token}")
            base, slash, step_text = token.partition("/")
            step = int(step_text) if slash else 1
            if step < 1:
                raise CronJobError("cron step must be positive")
            if base == "*":
                start, end = low, high
            elif "-" in base:
                start_text, end_text = base.split("-", 1)
                start, end = int(start_text), int(end_text)
            else:
                start = end = int(base)
        if start < low or end > high or start > end:
            raise CronJobError(f"cron value outside {low}-{high}: {token}")
        values.update(range(start, end + 1, step))
    if dow and 7 in values:
        values.remove(7)
        values.add(0)
    return values, wildcard


def parse_cron(expression: str) -> tuple[tuple[set[int], bool], ...]:
    parts = str(expression).split()
    if len(parts) != 5:
        raise CronJobError("cron schedule must contain exactly 5 fields")
    return (
        _parse_cron_field(parts[0], 0, 59),
        _parse_cron_field(parts[1], 0, 23),
        _parse_cron_field(parts[2], 1, 31),
        _parse_cron_field(parts[3], 1, 12),
        _parse_cron_field(parts[4], 0, 7, dow=True),
    )


def next_cron_at(expression: str, after: float) -> float:
    minute, hour, day, month, weekday = parse_cron(expression)
    cursor = datetime.fromtimestamp(after, timezone.utc).replace(second=0, microsecond=0) + timedelta(minutes=1)
    for _ in range(527040):
        cron_dow = (cursor.weekday() + 1) % 7
        day_match = cursor.day in day[0]
        dow_match = cron_dow in weekday[0]
        if day[1] and weekday[1]:
            calendar_match = True
        elif day[1]:
            calendar_match = dow_match
        elif weekday[1]:
            calendar_match = day_match
        else:
            calendar_match = day_match or dow_match
        if (cursor.minute in minute[0] and cursor.hour in hour[0]
                and cursor.month in month[0] and calendar_match):
            return cursor.timestamp()
        cursor += timedelta(minutes=1)
    raise CronJobError("cron schedule has no occurrence within 366 days")


def next_schedule_at(schedule_type: str, schedule_value: Any, after: float) -> tuple[str, float]:
    kind = str(schedule_type).strip().lower()
    if kind == "interval":
        seconds = parse_interval(schedule_value)
        return str(seconds), after + seconds
    if kind == "cron":
        value = " ".join(str(schedule_value).split())
        parse_cron(value)
        return value, next_cron_at(value, after)
    raise CronJobError("schedule_type must be cron or interval")


class CouncilCronService:
    def __init__(self, db_path: Path, workers: Callable[[], tuple[str, ...] | list[str]], poll_seconds: float = 2.0):
        self.db_path = Path(db_path)
        self.workers = workers
        self.poll_seconds = max(0.2, float(poll_seconds))
        self._lock = threading.RLock()
        self._scheduler_task: asyncio.Task | None = None
        self._run_tasks: set[asyncio.Task] = set()
        self._dispatch: Callable[[dict], Awaitable[dict]] | None = None
        self._narrate: Callable[[dict, str], Awaitable[dict]] | None = None
        self._started_at = 0.0
        self.initialize()

    @contextmanager
    def _connect(self):
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(str(self.db_path), timeout=10)
        try:
            connection.row_factory = sqlite3.Row
            connection.execute("PRAGMA journal_mode=WAL")
            connection.execute("PRAGMA foreign_keys=ON")
            connection.execute("PRAGMA busy_timeout=10000")
            yield connection
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    def initialize(self) -> None:
        with self._lock, self._connect() as db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS council_cron_jobs (
                    id TEXT PRIMARY KEY,
                    name TEXT NOT NULL,
                    worker TEXT NOT NULL,
                    worker_model TEXT NOT NULL DEFAULT '',
                    worker_provider TEXT NOT NULL DEFAULT '',
                    workers_json TEXT NOT NULL DEFAULT '[]',
                    prompt TEXT NOT NULL,
                    schedule_type TEXT NOT NULL,
                    schedule_value TEXT NOT NULL,
                    enabled INTEGER NOT NULL DEFAULT 1,
                    narrator_enabled INTEGER NOT NULL DEFAULT 0,
                    narrator_worker TEXT NOT NULL DEFAULT '',
                    narrator_model TEXT NOT NULL DEFAULT '',
                    narrator_provider TEXT NOT NULL DEFAULT '',
                    narrator_prompt TEXT NOT NULL DEFAULT '',
                    timeout_sec INTEGER NOT NULL DEFAULT 3600,
                    created_at REAL NOT NULL,
                    updated_at REAL NOT NULL,
                    next_run_at REAL,
                    last_run_at REAL,
                    deleted_at REAL
                );
                CREATE TABLE IF NOT EXISTS council_cron_runs (
                    id TEXT PRIMARY KEY,
                    job_id TEXT NOT NULL,
                    trigger_kind TEXT NOT NULL,
                    status TEXT NOT NULL,
                    scheduled_for REAL,
                    started_at REAL,
                    finished_at REAL,
                    worker_reply TEXT NOT NULL DEFAULT '',
                    narrator_reply TEXT NOT NULL DEFAULT '',
                    error TEXT NOT NULL DEFAULT '',
                    created_at REAL NOT NULL,
                    FOREIGN KEY(job_id) REFERENCES council_cron_jobs(id)
                );
                CREATE INDEX IF NOT EXISTS idx_council_cron_due
                    ON council_cron_jobs(enabled, next_run_at);
                CREATE INDEX IF NOT EXISTS idx_council_cron_runs_job
                    ON council_cron_runs(job_id, created_at DESC);
                CREATE UNIQUE INDEX IF NOT EXISTS idx_council_cron_one_active
                    ON council_cron_runs(job_id) WHERE status IN ('queued', 'running');
            """)
            columns = {row[1] for row in db.execute("PRAGMA table_info(council_cron_jobs)")}
            for name, ddl in (("workers_json", "TEXT NOT NULL DEFAULT '[]'"), ("worker_model", "TEXT NOT NULL DEFAULT ''"), ("worker_provider", "TEXT NOT NULL DEFAULT ''"), ("narrator_model", "TEXT NOT NULL DEFAULT ''"), ("narrator_provider", "TEXT NOT NULL DEFAULT ''")):
                if name not in columns:
                    db.execute(f"ALTER TABLE council_cron_jobs ADD COLUMN {name} {ddl}")

    def _validate(self, payload: dict, existing: dict | None = None) -> dict:
        allowed = {"name", "worker", "worker_model", "worker_provider", "workers", "prompt", "schedule", "schedule_type", "schedule_value", "enabled",
                   "narrator_enabled", "narrator_worker", "narrator_model", "narrator_provider", "narrator_prompt", "timeout_sec"}
        unknown = sorted(set(payload) - allowed)
        if unknown:
            raise CronJobError("unsupported fields: " + ", ".join(unknown))
        merged = dict(existing or {})
        merged.update(payload)
        schedule = payload.get("schedule")
        if schedule is not None:
            if not isinstance(schedule, dict) or set(schedule) - {"type", "value"}:
                raise CronJobError("schedule must be an object with type and value")
            merged["schedule_type"] = schedule.get("type")
            merged["schedule_value"] = schedule.get("value")
        name = str(merged.get("name") or "").strip()
        prompt = str(merged.get("prompt") or "").strip()
        raw_workers = merged.get("workers")
        if raw_workers is None:
            try: raw_workers = json.loads(str(merged.get("workers_json") or "[]"))
            except Exception: raw_workers = []
        if not isinstance(raw_workers, list): raise CronJobError("workers must be an array")
        workers = list(dict.fromkeys(canonical_id(x) for x in raw_workers if canonical_id(x)))
        worker = canonical_id(merged.get("worker") or (workers[0] if workers else ""))
        if not workers and worker: workers = [worker]
        if not name or len(name) > 100:
            raise CronJobError("name is required and limited to 100 characters")
        if not prompt or len(prompt) > 100000:
            raise CronJobError("prompt is required and limited to 100000 characters")
        valid_workers = set(self.workers())
        invalid_workers = [item for item in workers if item not in valid_workers]
        if not workers or invalid_workers:
            raise CronJobError(f"workers must contain only: {', '.join(sorted(valid_workers))}")
        schedule_type = str(merged.get("schedule_type") or "").lower()
        schedule_value, next_at = next_schedule_at(schedule_type, merged.get("schedule_value"), _now())
        enabled = _as_bool(merged.get("enabled", True), "enabled")
        narrator_enabled = _as_bool(merged.get("narrator_enabled", False), "narrator_enabled")
        narrator_worker = canonical_id(merged.get("narrator_worker") or worker)
        if narrator_enabled and narrator_worker not in valid_workers:
            raise CronJobError(f"narrator_worker must be one of: {', '.join(sorted(valid_workers))}")
        timeout_sec = int(merged.get("timeout_sec") or 3600)
        if not 30 <= timeout_sec <= 7200:
            raise CronJobError("timeout_sec must be between 30 and 7200")
        return {
            "name": name, "worker": worker, "worker_model": str(merged.get("worker_model") or "").strip(), "worker_provider": str(merged.get("worker_provider") or "").strip(), "workers_json": json.dumps(workers), "prompt": prompt,
            "schedule_type": schedule_type, "schedule_value": schedule_value,
            "enabled": enabled, "narrator_enabled": narrator_enabled,
            "narrator_worker": narrator_worker,
            "narrator_model": str(merged.get("narrator_model") or "").strip(),
            "narrator_provider": str(merged.get("narrator_provider") or "").strip(),
            "narrator_prompt": str(merged.get("narrator_prompt") or "").strip()[:4000],
            "timeout_sec": timeout_sec, "next_run_at": next_at if enabled else None,
        }

    @staticmethod
    def _job(row: sqlite3.Row | dict) -> dict:
        item = dict(row)
        for key in ("enabled", "narrator_enabled"):
            item[key] = bool(item[key])
        try: item["workers"] = json.loads(item.get("workers_json") or "[]")
        except Exception: item["workers"] = [item.get("worker")] if item.get("worker") else []
        for key in ("created_at", "updated_at", "next_run_at", "last_run_at", "deleted_at"):
            item[key] = _iso(item.get(key))
        return item

    @staticmethod
    def _run(row: sqlite3.Row | dict) -> dict:
        item = dict(row)
        for key in ("created_at", "scheduled_for", "started_at", "finished_at"):
            item[key] = _iso(item.get(key))
        return item

    def list_jobs(self) -> list[dict]:
        with self._lock, self._connect() as db:
            rows = db.execute("SELECT * FROM council_cron_jobs WHERE deleted_at IS NULL ORDER BY created_at DESC").fetchall()
        return [self._job(row) for row in rows]

    def get_job(self, job_id: str, *, raw: bool = False) -> dict:
        with self._lock, self._connect() as db:
            row = db.execute("SELECT * FROM council_cron_jobs WHERE id=? AND deleted_at IS NULL", (job_id,)).fetchone()
        if not row:
            raise JobNotFound("cron job not found")
        return dict(row) if raw else self._job(row)

    def create_job(self, payload: dict) -> dict:
        values = self._validate(payload)
        now = _now()
        job_id = "cron-" + uuid.uuid4().hex[:12]
        with self._lock, self._connect() as db:
            db.execute("""INSERT INTO council_cron_jobs
                (id,name,worker,worker_model,worker_provider,workers_json,prompt,schedule_type,schedule_value,enabled,narrator_enabled,narrator_worker,
                 narrator_model,narrator_provider,narrator_prompt,timeout_sec,created_at,updated_at,next_run_at)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (job_id, values["name"], values["worker"], values["worker_model"], values["worker_provider"], values["workers_json"], values["prompt"], values["schedule_type"],
                 values["schedule_value"], int(values["enabled"]), int(values["narrator_enabled"]),
                 values["narrator_worker"], values["narrator_model"], values["narrator_provider"], values["narrator_prompt"], values["timeout_sec"], now, now,
                 values["next_run_at"]))
        return self.get_job(job_id)

    def update_job(self, job_id: str, payload: dict) -> dict:
        current = self.get_job(job_id, raw=True)
        values = self._validate(payload, current)
        schedule_changed = bool({"schedule", "schedule_type", "schedule_value", "enabled"} & set(payload))
        if current.get("enabled") and not schedule_changed:
            values["next_run_at"] = current.get("next_run_at")
        with self._lock, self._connect() as db:
            db.execute("""UPDATE council_cron_jobs SET name=?,worker=?,worker_model=?,worker_provider=?,workers_json=?,prompt=?,schedule_type=?,schedule_value=?,
                enabled=?,narrator_enabled=?,narrator_worker=?,narrator_model=?,narrator_provider=?,narrator_prompt=?,timeout_sec=?,updated_at=?,next_run_at=?
                WHERE id=? AND deleted_at IS NULL""",
                (values["name"], values["worker"], values["worker_model"], values["worker_provider"], values["workers_json"], values["prompt"], values["schedule_type"],
                 values["schedule_value"], int(values["enabled"]), int(values["narrator_enabled"]),
                 values["narrator_worker"], values["narrator_model"], values["narrator_provider"], values["narrator_prompt"], values["timeout_sec"], _now(),
                 values["next_run_at"], job_id))
        return self.get_job(job_id)

    def delete_job(self, job_id: str) -> None:
        self.get_job(job_id, raw=True)
        with self._lock, self._connect() as db:
            db.execute("UPDATE council_cron_jobs SET enabled=0,next_run_at=NULL,deleted_at=?,updated_at=? WHERE id=?",
                       (_now(), _now(), job_id))

    def list_runs(self, job_id: str | None = None, limit: int = 50) -> list[dict]:
        limit = max(1, min(int(limit), 200))
        with self._lock, self._connect() as db:
            if job_id:
                rows = db.execute("SELECT * FROM council_cron_runs WHERE job_id=? ORDER BY created_at DESC LIMIT ?",
                                  (job_id, limit)).fetchall()
            else:
                rows = db.execute("SELECT * FROM council_cron_runs ORDER BY created_at DESC LIMIT ?", (limit,)).fetchall()
        return [self._run(row) for row in rows]

    def _claim(self, job: dict, trigger_kind: str, scheduled_for: float | None = None) -> dict:
        now = _now()
        run_id = "run-" + uuid.uuid4().hex[:14]
        with self._lock, self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            active = db.execute("SELECT id FROM council_cron_runs WHERE job_id=? AND status IN ('queued','running')",
                                (job["id"],)).fetchone()
            if active:
                raise RunConflict(f"job already has active run {active['id']}")
            db.execute("""INSERT INTO council_cron_runs
                (id,job_id,trigger_kind,status,scheduled_for,created_at) VALUES (?,?,?,?,?,?)""",
                (run_id, job["id"], trigger_kind, "queued", scheduled_for or now, now))
        return self.list_runs(job["id"], 1)[0]

    def _launch(self, job: dict, run_id: str) -> None:
        task = asyncio.create_task(self._execute(job, run_id), name=f"council-cron-{run_id}")
        self._run_tasks.add(task)
        task.add_done_callback(self._run_tasks.discard)

    async def trigger_job(self, job_id: str) -> dict:
        if self._dispatch is None:
            raise CronJobError("cron scheduler is not started")
        job = self.get_job(job_id, raw=True)
        run = self._claim(job, "manual")
        self._launch(job, run["id"])
        return run

    def _update_run(self, run_id: str, **fields: Any) -> None:
        if not fields:
            return
        allowed = {"status", "started_at", "finished_at", "worker_reply", "narrator_reply", "error"}
        if set(fields) - allowed:
            raise ValueError("invalid run fields")
        sql = ",".join(f"{key}=?" for key in fields)
        with self._lock, self._connect() as db:
            db.execute(f"UPDATE council_cron_runs SET {sql} WHERE id=?", (*fields.values(), run_id))

    async def _execute(self, job: dict, run_id: str) -> None:
        self._update_run(run_id, status="running", started_at=_now())
        worker_reply = ""
        narrator_reply = ""
        try:
            result = await self._dispatch(job)  # type: ignore[misc]
            worker_reply = str(result.get("reply_text") or result.get("reply") or "")
            if not result.get("ok"):
                raise RuntimeError(str(result.get("error") or result.get("error_name") or "worker dispatch failed"))
            if job.get("narrator_enabled") and self._narrate:
                narration = await self._narrate(job, worker_reply)
                narrator_reply = str(narration.get("reply_text") or narration.get("reply") or "")
                if not narration.get("ok"):
                    narrator_reply = ""
                    raise RuntimeError("narrator: " + str(narration.get("error") or "dispatch failed"))
            self._update_run(run_id, status="succeeded", finished_at=_now(), worker_reply=worker_reply,
                             narrator_reply=narrator_reply, error="")
        except asyncio.CancelledError:
            self._update_run(run_id, status="interrupted", finished_at=_now(), worker_reply=worker_reply,
                             narrator_reply=narrator_reply, error="server_stopped")
            raise
        except Exception as exc:
            self._update_run(run_id, status="failed", finished_at=_now(), worker_reply=worker_reply,
                             narrator_reply=narrator_reply, error=str(exc)[:2000])
        finally:
            with self._lock, self._connect() as db:
                db.execute("UPDATE council_cron_jobs SET last_run_at=?,updated_at=? WHERE id=?", (_now(), _now(), job["id"]))

    def _claim_due(self) -> list[tuple[dict, str]]:
        now = _now()
        claimed: list[tuple[dict, str]] = []
        with self._lock, self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            jobs = db.execute("""SELECT * FROM council_cron_jobs
                WHERE deleted_at IS NULL AND enabled=1 AND next_run_at IS NOT NULL AND next_run_at<=?
                ORDER BY next_run_at LIMIT 25""", (now,)).fetchall()
            for row in jobs:
                job = dict(row)
                scheduled_for = float(job["next_run_at"])
                normalized, next_at = next_schedule_at(job["schedule_type"], job["schedule_value"], now)
                db.execute("UPDATE council_cron_jobs SET schedule_value=?,next_run_at=?,updated_at=? WHERE id=?",
                           (normalized, next_at, now, job["id"]))
                active = db.execute("SELECT id FROM council_cron_runs WHERE job_id=? AND status IN ('queued','running')",
                                    (job["id"],)).fetchone()
                trigger_kind = "recovery" if scheduled_for < self._started_at else "schedule"
                if active:
                    db.execute("""INSERT INTO council_cron_runs
                        (id,job_id,trigger_kind,status,scheduled_for,finished_at,error,created_at)
                        VALUES (?,?,?,?,?,?,?,?)""",
                        ("run-" + uuid.uuid4().hex[:14], job["id"], trigger_kind, "skipped", scheduled_for,
                         now, "overlap_prevented", now))
                    continue
                run_id = "run-" + uuid.uuid4().hex[:14]
                db.execute("""INSERT INTO council_cron_runs
                    (id,job_id,trigger_kind,status,scheduled_for,created_at) VALUES (?,?,?,?,?,?)""",
                    (run_id, job["id"], trigger_kind, "queued", scheduled_for, now))
                claimed.append((job, run_id))
        return claimed

    async def _loop(self) -> None:
        while True:
            for job, run_id in self._claim_due():
                self._launch(job, run_id)
            await asyncio.sleep(self.poll_seconds)

    async def start(self, dispatch: Callable[[dict], Awaitable[dict]],
                    narrate: Callable[[dict, str], Awaitable[dict]] | None = None) -> None:
        if self._scheduler_task and not self._scheduler_task.done():
            return
        self.initialize()
        self._dispatch = dispatch
        self._narrate = narrate
        self._started_at = _now()
        with self._lock, self._connect() as db:
            db.execute("""UPDATE council_cron_runs SET status='interrupted',finished_at=?,error='server_restarted'
                WHERE status IN ('queued','running')""", (self._started_at,))
        self._scheduler_task = asyncio.create_task(self._loop(), name="council-cron-scheduler")

    async def stop(self) -> None:
        tasks = [task for task in [self._scheduler_task, *self._run_tasks] if task and not task.done()]
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        self._scheduler_task = None
        self._run_tasks.clear()
