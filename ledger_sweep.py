#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
ToolA Ledger Sweep -- nightly run.
Design SOT: docs/LEDGER_SWEEP_DESIGN_20261110.md (museB campaign brainstorm-20261110-1322).
Keeps durable_audit.db (durable_audit_outbox) + sessions.db (events) under a combined
cap of 1,000,000,000 B (file + WAL bytes), deleting history oldest-first, never touching
live state, with a receipt on every exit path. Stdlib only. Zero ToolA calls.
"""
import json
import os
import shutil
import socket
import sqlite3
import sys
import time
import uuid
from datetime import datetime, timedelta

SCRIPT_VERSION = "1.1.0"
SCHEMA_VERSION = 1

REPO = r"D:\0. TRIEN\1. DATA\DATA\5. VIBE APP\1. APP\8. ToolA\project-orchestrator"
CACHE = os.path.join(REPO, ".orchestrator_cache")
DB_PATHS = {
    "durable_audit": os.path.join(CACHE, "durable_audit.db"),
    "sessions": os.path.join(CACHE, "sessions.db"),
}
SPOOL = os.path.join(CACHE, "durable_audit_spool.jsonl")
RECEIPTS = r"D:\0. TRIEN\1. DATA\DATA\5. VIBE APP\1. APP\41. CLAUDELTNAI\ledger_sweep\receipts"
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
MARKER_NAME = "conversion-done.json"
OUTBOX_TABLE = "durable_audit_outbox"
DRY_RUN = False


def apply_config(args):
    """Apply CLI configuration (Item 3, LEDGER_FOLLOWUPS_CLAIM_OPE_20261121).
    Defaults preserve the historical hardcoded behavior exactly. --db-path is an
    alias for --durable-db-path. Env fallbacks are read here in addition to CLI."""
    global REPO, CACHE, DB_PATHS, SPOOL, RECEIPTS, OUTBOX_TABLE, DRY_RUN
    global OUTBOX_FLOOR_HOURS, EVENTS_CEILING_DAYS, EVENTS_FLOOR_DAYS
    REPO = args.repo or os.environ.get("LEDGER_SWEEP_REPO") or REPO
    CACHE = os.path.join(REPO, ".orchestrator_cache")
    durable = (args.durable_db_path or os.environ.get("LEDGER_SWEEP_DURABLE_DB")
               or os.path.join(CACHE, "durable_audit.db"))
    sessions = (args.sessions_db_path or os.environ.get("LEDGER_SWEEP_SESSIONS_DB")
                or os.path.join(CACHE, "sessions.db"))
    DB_PATHS = {"durable_audit": durable, "sessions": sessions}
    SPOOL = os.path.join(CACHE, "durable_audit_spool.jsonl")
    RECEIPTS = args.receipts_dir or os.environ.get("LEDGER_SWEEP_RECEIPTS") or RECEIPTS
    OUTBOX_TABLE = args.outbox_table or os.environ.get("LEDGER_SWEEP_OUTBOX_TABLE") or OUTBOX_TABLE
    if args.outbox_floor_hours is not None:
        OUTBOX_FLOOR_HOURS = args.outbox_floor_hours
    elif os.environ.get("LEDGER_SWEEP_OUTBOX_FLOOR_HOURS"):
        OUTBOX_FLOOR_HOURS = float(os.environ["LEDGER_SWEEP_OUTBOX_FLOOR_HOURS"])
    if args.events_ceiling_days is not None:
        EVENTS_CEILING_DAYS = args.events_ceiling_days
    elif os.environ.get("LEDGER_SWEEP_EVENTS_CEILING_DAYS"):
        EVENTS_CEILING_DAYS = float(os.environ["LEDGER_SWEEP_EVENTS_CEILING_DAYS"])
    if args.events_floor_days is not None:
        EVENTS_FLOOR_DAYS = args.events_floor_days
    elif os.environ.get("LEDGER_SWEEP_EVENTS_FLOOR_DAYS"):
        EVENTS_FLOOR_DAYS = float(os.environ["LEDGER_SWEEP_EVENTS_FLOOR_DAYS"])
    DRY_RUN = bool(args.dry_run)


def parse_args(argv=None):
    import argparse
    ap = argparse.ArgumentParser(
        prog="ledger_sweep.py",
        description="ToolA nightly ledger sweep. Defaults preserve the historical behavior exactly.")
    ap.add_argument("--dry-run", action="store_true",
                    help="report what WOULD be deleted; no DELETE, VACUUM, checkpoint or marker writes")
    ap.add_argument("--repo", default=None, help="repo root (default: the historical hardcoded path)")
    ap.add_argument("--durable-db-path", "--db-path", dest="durable_db_path", default=None,
                    help="durable-audit DB path (--db-path is an alias)")
    ap.add_argument("--sessions-db-path", default=None, help="sessions DB path")
    ap.add_argument("--receipts-dir", default=None, help="receipts directory")
    ap.add_argument("--outbox-table", default=None, help="outbox table name (default durable_audit_outbox)")
    ap.add_argument("--outbox-floor-hours", type=float, default=None, help="outbox retention floor in hours (default 48)")
    ap.add_argument("--events-ceiling-days", type=float, default=None, help="events ceiling in days, Sunday runs (default 10)")
    ap.add_argument("--events-floor-days", type=float, default=None, help="events trim floor in days (default 7)")
    return ap.parse_args(argv)

CAP = 1_000_000_000
I_ALLOW = 180_000_000
TRIM_TRIGGER = 950_000_000
TRIM_TARGET = 850_000_000
OUTBOX_FLOOR_HOURS = 48
EVENTS_CEILING_DAYS = 10
EVENTS_FLOOR_DAYS = 7
CHUNK = 2000
BUSY_TIMEOUT_MS = 30000
RETRY_SLEEPS = (2.0, 5.0)          # after 1st and 2nd consecutive failure; 3rd aborts the phase
MAX_CONSEC_FAILURES = 3
CONTENTION_BUDGET_S = 300.0        # cumulative busy-wait per phase
DB_WALL_BUDGET_S = 1200.0          # per-database wall budget
RUN_CAP_S = 2700.0                 # whole-run cap (45 min)
CHUNK_PAUSE_S = 0.25
WAL_TRUNCATE_THRESHOLD = 16 * 1024 * 1024
WAL_ALARM_BYTES = 100 * 1024 * 1024
SPOOL_ALARM_BYTES = 10 * 1024 * 1024
DISK_ALARM_FREE = 1_500_000_000
IVAC_PAGES_PER_CALL = 4096
IVAC_FREELIST_TARGET = 2048
IVAC_TIME_BUDGET_S = 60.0
ESTIMATOR_TOLERANCE = 1_000_000

LIVE_TABLES = ("sessions", "read_tokens", "write_operations", "task_checkpoints", "pending_audits")


# ----------------------------------------------------------------------------- utils

def now_local():
    return datetime.now()


def iso(dt):
    return dt.isoformat(timespec="seconds")


def fsize(path):
    try:
        return os.path.getsize(path)
    except OSError:
        return 0


def file_sizes(db_path):
    return {
        "file": fsize(db_path),
        "wal": fsize(db_path + "-wal"),
        "shm": fsize(db_path + "-shm"),
    }


def connect(path, readonly=False):
    if readonly:
        con = sqlite3.connect("file:" + path.replace("\\", "/") + "?mode=ro", uri=True, timeout=30)
    else:
        con = sqlite3.connect(path, timeout=30)
    con.execute("PRAGMA busy_timeout = %d" % BUSY_TIMEOUT_MS)
    return con


def parse_events_ts(value):
    """Return (naive_local_datetime_or_None, offset_bearing_bool). None => unparseable."""
    if not isinstance(value, str):
        return None, False
    try:
        dt = datetime.fromisoformat(value)
    except Exception:
        return None, False
    if dt.tzinfo is not None:
        dt = dt.astimezone().replace(tzinfo=None)
        return dt, True
    return dt, False


class PhaseAbort(Exception):
    def __init__(self, reason, contention_s=0.0):
        super().__init__(reason)
        self.reason = reason
        self.contention_s = contention_s


class Contention:
    """Tracks R-YIELD budgets for one database phase."""
    def __init__(self, label):
        self.label = label
        self.consec_failures = 0
        self.wait_s = 0.0
        self.started = time.monotonic()

    def run(self, fn):
        """Run fn() with the design's retry discipline; returns fn's result."""
        while True:
            t0 = time.monotonic()
            try:
                out = fn()
                self.consec_failures = 0
                return out
            except sqlite3.OperationalError as exc:
                msg = str(exc).lower()
                if "locked" not in msg and "busy" not in msg:
                    raise
                self.wait_s += time.monotonic() - t0
                self.consec_failures += 1
                if self.consec_failures >= MAX_CONSEC_FAILURES:
                    raise PhaseAbort(
                        "%s: %d consecutive locked/busy failures" % (self.label, self.consec_failures),
                        contention_s=self.wait_s)
                sleep = RETRY_SLEEPS[min(self.consec_failures - 1, len(RETRY_SLEEPS) - 1)]
                self.wait_s += sleep
                if self.wait_s > CONTENTION_BUDGET_S:
                    raise PhaseAbort("%s: cumulative contention wait %.1fs > budget" % (self.label, self.wait_s),
                                     contention_s=self.wait_s)
                if time.monotonic() - self.started > DB_WALL_BUDGET_S:
                    raise PhaseAbort("%s: per-database wall budget exceeded" % self.label,
                                     contention_s=self.wait_s)
                time.sleep(sleep)


# ----------------------------------------------------------------------------- receipts / history

def receipts_dirs():
    return [RECEIPTS, SCRIPT_DIR]


def list_receipt_files():
    out = []
    for d in receipts_dirs():
        try:
            names = os.listdir(d)
        except OSError:
            continue
        for n in names:
            if n.startswith("receipt-") and n.endswith(".json"):
                out.append(os.path.join(d, n))
    return sorted(out)


def load_receipt(path):
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return None


def prior_receipts(before_name):
    """All parseable receipts strictly older than the file named before_name (by name order)."""
    res = []
    for p in list_receipt_files():
        if os.path.basename(p) >= before_name:
            continue
        r = load_receipt(p)
        if r is not None:
            res.append((p, r))
    return res


def marker_paths():
    return [os.path.join(RECEIPTS, MARKER_NAME), os.path.join(SCRIPT_DIR, MARKER_NAME)]


def read_marker():
    """Returns (marker_dict_or_None, state) where state in
    'ok' | 'missing' | 'unparseable'."""
    seen_unparseable = False
    for p in marker_paths():
        if not os.path.exists(p):
            continue
        try:
            with open(p, "r", encoding="utf-8") as f:
                return json.load(f), "ok"
        except Exception:
            seen_unparseable = True
    return None, ("unparseable" if seen_unparseable else "missing")


def write_marker(per_db_modes, converted_at, source):
    payload = {
        "converted_at": converted_at,
        "per_db": per_db_modes,
        "source": source,
        "written_at": iso(now_local()),
        "written_by": "ledger_sweep.py %s" % SCRIPT_VERSION,
    }
    text = json.dumps(payload, indent=1, ensure_ascii=False)
    for p in marker_paths():
        try:
            os.makedirs(os.path.dirname(p), exist_ok=True)
            with open(p, "w", encoding="utf-8") as f:
                f.write(text)
            with open(p, "r", encoding="utf-8") as f:
                json.load(f)
            return p
        except Exception:
            continue
    return None


def write_receipt_files(receipt, md_text):
    """Write receipt JSON + md twin to the receipts folder; fallback beside the script.
    Returns (json_path, used_fallback)."""
    ts = receipt["started"].replace("-", "").replace(":", "").replace("T", "-")
    base = "receipt-%s" % ts
    for folder, fallback in ((RECEIPTS, False), (SCRIPT_DIR, True)):
        try:
            os.makedirs(folder, exist_ok=True)
            jp = os.path.join(folder, base + ".json")
            mp = os.path.join(folder, base + ".md")
            with open(jp, "w", encoding="utf-8") as f:
                json.dump(receipt, f, indent=1, ensure_ascii=False)
            with open(mp, "w", encoding="utf-8") as f:
                f.write(md_text)
            return jp, fallback
        except Exception:
            continue
    return None, True


def write_alarm(name, receipt, detail_lines):
    ts = receipt["started"].replace("-", "").replace(":", "").replace("T", "-")
    base = "ALARM-%s-%s" % (name, ts)
    body = ["ALARM %s" % name,
            "run_id: %s" % receipt["run_id"],
            "started: %s" % receipt["started"],
            "script_version: %s" % SCRIPT_VERSION,
            "status: %s" % receipt.get("status"),
            ""]
    body += detail_lines
    text = "\n".join(body) + "\n"
    for folder in (RECEIPTS, SCRIPT_DIR):
        try:
            os.makedirs(folder, exist_ok=True)
            for ext in (".txt", ".md"):
                with open(os.path.join(folder, base + ext), "w", encoding="utf-8") as f:
                    f.write(text)
            return os.path.join(folder, base + ".txt")
        except Exception:
            continue
    return None


# ----------------------------------------------------------------------------- census (shared with the window driver)

def census_outbox(con):
    total = con.execute("SELECT COUNT(*) FROM %s" % OUTBOX_TABLE).fetchone()[0]
    nonconf = con.execute(
        "SELECT COUNT(*) FROM %s "
        "WHERE created_at IS NOT NULL AND typeof(created_at) <> 'real'" % OUTBOX_TABLE).fetchone()[0]
    nulls = con.execute(
        "SELECT COUNT(*) FROM %s WHERE created_at IS NULL" % OUTBOX_TABLE).fetchone()[0]
    return {"rows": total, "nonconforming_created_at": nonconf, "null_created_at": nulls}


def census_events(con):
    """Full scan (design S5): storage classes, NULLs, unparseable, offset-bearing."""
    classes = {}
    nulls = 0
    unpar = 0
    offset = 0
    total = 0
    for value, tclass in con.execute("SELECT created_at, typeof(created_at) FROM events"):
        total += 1
        classes[tclass] = classes.get(tclass, 0) + 1
        if value is None:
            nulls += 1
            continue
        if tclass == "text":
            dt, off = parse_events_ts(value)
            if dt is None:
                unpar += 1
            elif off:
                offset += 1
    nonconf = sum(v for k, v in classes.items() if k not in ("text", "null"))
    return {
        "rows": total,
        "classes": classes,
        "nonconforming_created_at": nonconf,
        "null_created_at": nulls,
        "unparseable_created_at": unpar,
        "offset_bearing_created_at": offset,
    }

def outbox_delete_sql():
    """The production outbox DELETE predicate (chunked, oldest-first). Single SOT:
    the nightly loop and the predicate tests both use this exact statement."""
    return (
        "DELETE FROM %s WHERE audit_id IN ("
        "SELECT audit_id FROM %s "
        "WHERE state = 'applied' AND created_at <= ? AND typeof(created_at) = 'real' "
        "ORDER BY created_at LIMIT %d)" % (OUTBOX_TABLE, OUTBOX_TABLE, CHUNK))


def outbox_delete_preview(con, cutoff):
    """Dry-run projection of the outbox DELETE predicate: count + up to 5 oldest samples."""
    count = con.execute(
        "SELECT COUNT(*) FROM %s "
        "WHERE state = 'applied' AND created_at <= ? AND typeof(created_at) = 'real'" % OUTBOX_TABLE,
        (cutoff,)).fetchone()[0]
    sample = [
        {"audit_id": aid, "created_at": ca}
        for aid, ca in con.execute(
            "SELECT audit_id, created_at FROM %s "
            "WHERE state = 'applied' AND created_at <= ? AND typeof(created_at) = 'real' "
            "ORDER BY created_at LIMIT 5" % OUTBOX_TABLE, (cutoff,)).fetchall()
    ]
    return {"count": count, "sample": sample}


def events_count_older(con, cutoff_dt):
    """Count events rows the prefix-based ceiling/trim logic would delete (text,
    parseable, strictly older than cutoff_dt)."""
    n = 0
    for value, tclass in con.execute("SELECT created_at, typeof(created_at) FROM events"):
        if tclass == "text":
            dt, _off = parse_events_ts(value)
            if dt is not None and dt < cutoff_dt:
                n += 1
    return n


def events_preview_sample(con, cutoff_dt, limit=5):
    out = []
    for rid, value, tclass in con.execute("SELECT id, created_at, typeof(created_at) FROM events ORDER BY id"):
        if tclass == "text":
            dt, _off = parse_events_ts(value)
            if dt is not None and dt < cutoff_dt:
                out.append({"id": rid, "created_at": value})
                if len(out) >= limit:
                    break
    return out


def print_dry_run_summary(receipt):
    print("[dry-run] ledger_sweep %s -- no deletion, no vacuum, no checkpoint, no marker write" % SCRIPT_VERSION)
    print("[dry-run] effective config: %s" % json.dumps(receipt.get("effective_config", {}), ensure_ascii=False))
    for key, db in receipt.get("databases", {}).items():
        wd = db.get("would_delete")
        if wd is None:
            continue
        if key == "durable_audit":
            cut = db.get("effective_cutoff", {})
            print("[dry-run] %s.%s: would delete %d rows (cutoff %s = %s)"
                  % (key, OUTBOX_TABLE, wd.get("count", 0), cut.get("kind"), cut.get("value")))
            for row in wd.get("sample", []):
                print("[dry-run]   sample: audit_id=%s created_at=%s" % (row.get("audit_id"), row.get("created_at")))
        else:
            for phase in ("ceiling", "trim"):
                part = wd.get(phase)
                if part is None:
                    continue
                cutoff = part.get("floor") or db.get("effective_cutoff_ceiling")
                print("[dry-run] %s.events %s: would delete %d rows (cutoff %s)"
                      % (key, phase, part.get("count", 0), cutoff))
                for row in part.get("sample", []):
                    print("[dry-run]   sample: id=%s created_at=%s" % (row.get("id"), row.get("created_at")))
    print("[dry-run] total rows_deleted: %d (dry run deletes nothing)" % receipt.get("rows_deleted", 0))

def live_counts(con):
    out = {}
    for t in LIVE_TABLES:
        out[t] = con.execute("SELECT COUNT(*) FROM %s" % t).fetchone()[0]
    return out


def db_pragmas(con):
    return {
        "page_size": con.execute("PRAGMA page_size").fetchone()[0],
        "page_count": con.execute("PRAGMA page_count").fetchone()[0],
        "freelist_count": con.execute("PRAGMA freelist_count").fetchone()[0],
        "auto_vacuum": con.execute("PRAGMA auto_vacuum").fetchone()[0],
    }


def metric_p(connections):
    """Peak metric P on the S1 definition: sum over both DBs of
    page_count*page_size + measured WAL bytes."""
    total = 0
    for key, con in connections.items():
        pr = db_pragmas(con)
        total += pr["page_count"] * pr["page_size"] + fsize(DB_PATHS[key] + "-wal")
    return total


# ----------------------------------------------------------------------------- main run

def main(argv=None):
    apply_config(parse_args(argv))
    dry_run = DRY_RUN
    started_dt = now_local()
    started_mono = time.monotonic()
    receipt = {
        "schema_version": SCHEMA_VERSION,
        "run_id": "run-%s-%s" % (started_dt.strftime("%Y%m%d-%H%M%S"), uuid.uuid4().hex[:8]),
        "kind": "nightly",
        "started": iso(started_dt),
        "finished": None,
        "host": socket.gethostname(),
        "script_version": SCRIPT_VERSION,
        "status": "FAILED",
        "abort_reason": None,
        "alarms_fired": [],
        "databases": {},
        "statement_journal": [],
        "checkpoints": {},
        "incremental_vacuum": {},
        "live_state_counts": None,
        "spool_bytes_before": fsize(SPOOL) if os.path.exists(SPOOL) else None,
        "spool_bytes_after": None,
        "free_disk_after": None,
        "contention": {},
        "flags": {},
        "dry_run": dry_run,
        "rows_deleted": 0,
        "effective_config": {
            "repo": REPO, "cache": CACHE,
            "db_paths": dict(DB_PATHS), "spool": SPOOL, "receipts": RECEIPTS,
            "outbox_table": OUTBOX_TABLE,
            "outbox_floor_hours": OUTBOX_FLOOR_HOURS,
            "events_ceiling_days": EVENTS_CEILING_DAYS,
            "events_floor_days": EVENTS_FLOOR_DAYS,
        },
    }
    alarms_detail = {}

    def fire_alarm(name, lines):
        if name not in receipt["alarms_fired"]:
            receipt["alarms_fired"].append(name)
            alarms_detail[name] = lines

    def run_cap_hit():
        return (time.monotonic() - started_mono) > RUN_CAP_S

    exit_code = 1
    try:
        _run(receipt, fire_alarm, run_cap_hit, dry_run=dry_run)
        if receipt["status"] == "FAILED":
            receipt["status"] = "OK"
    except PhaseAbort as exc:
        receipt["status"] = "PARTIAL"
        receipt["abort_reason"] = str(exc)
        fire_alarm("CONTENTION", ["phase aborted under R-YIELD: %s" % exc])
    except Exception as exc:  # noqa: BLE001 -- receipt on every exit path
        receipt["status"] = "FAILED"
        receipt["abort_reason"] = "%s: %s" % (type(exc).__name__, exc)

    # closing measurements
    receipt["finished"] = iso(now_local())
    receipt["spool_bytes_after"] = fsize(SPOOL) if os.path.exists(SPOOL) else None
    try:
        receipt["free_disk_after"] = shutil.disk_usage("D:\\").free
    except Exception:
        receipt["free_disk_after"] = None

    # ALARM-SPOOL: spool grew across a run in which the sweep deleted rows
    deleted_any = any(db.get("rows_deleted", 0) for db in receipt["databases"].values())
    sb, sa = receipt["spool_bytes_before"], receipt["spool_bytes_after"]
    if deleted_any and sb is not None and sa is not None and sa > sb:
        fire_alarm("SPOOL", ["spool grew across a deleting run: %d -> %d bytes" % (sb, sa)])
    # ALARM-RUN-FAILED for non-clean exits not covered by a specific alarm
    if receipt["status"] in ("ABORTED", "FAILED") and not receipt["alarms_fired"]:
        fire_alarm("RUN-FAILED", ["status %s: %s" % (receipt["status"], receipt.get("abort_reason"))])

    receipt["rows_deleted"] = sum(db.get("rows_deleted", 0) or 0 for db in receipt["databases"].values())
    if dry_run:
        print_dry_run_summary(receipt)
    md = render_md(receipt)
    path, fallback = write_receipt_files(receipt, md)
    receipt["_receipt_path"] = path
    receipt["_receipt_fallback"] = fallback
    for name in receipt["alarms_fired"]:
        write_alarm(name, receipt, alarms_detail.get(name, []))

    if receipt["status"] == "OK" and not receipt["alarms_fired"]:
        exit_code = 0
    elif receipt["status"] == "OK-FLAGGED":
        exit_code = 0
    else:
        exit_code = 1
    return exit_code


def _run(receipt, fire_alarm, run_cap_hit, dry_run=False):
    started_dt = datetime.fromisoformat(receipt["started"])
    priors = prior_receipts("receipt-%s" % started_dt.strftime("%Y%m%d-%H%M%S"))

    # ---- run-start alarms + history-derived state
    free0 = shutil.disk_usage("D:\\").free
    if free0 < DISK_ALARM_FREE:
        fire_alarm("DISK", ["free space on D: at run start = %d B (< 1.5 GB)" % free0])
    for key, path in DB_PATHS.items():
        wal0 = fsize(path + "-wal")
        receipt["databases"].setdefault(key, {})["wal_bytes_start"] = wal0
        if wal0 > WAL_ALARM_BYTES:
            fire_alarm("WAL", ["%s WAL at run start = %d B (> 100 MB)" % (key, wal0)])
    spool0 = receipt["spool_bytes_before"]
    if spool0 is not None and spool0 > SPOOL_ALARM_BYTES:
        fire_alarm("SPOOL", ["spool at run start = %d B (> 10 MB)" % spool0])
    if priors:
        last_started = priors[-1][1].get("started")
        if last_started:
            try:
                age_h = (started_dt - datetime.fromisoformat(last_started)).total_seconds() / 3600.0
                if age_h > 36.0:
                    fire_alarm("NO-RECEIPT", ["previous receipt %s is %.1f h old (> 36 h)" % (last_started, age_h)])
            except Exception:
                pass

    # ---- conversion establishment (design S6, two sources)
    marker, marker_state = read_marker()
    established = {}
    established_from = {}
    converted_at_from_history = {}
    for key in DB_PATHS:
        est = False
        if marker_state == "ok" and marker is not None:
            try:
                if int(marker.get("per_db", {}).get(key, 0)) == 2:
                    est = True
                    established_from[key] = "marker"
            except Exception:
                pass
        if not est:
            for _p, r in priors:
                try:
                    if int(r.get("databases", {}).get(key, {}).get("auto_vacuum_mode", 0)) == 2:
                        est = True
                        established_from[key] = "receipt_history"
                        converted_at_from_history[key] = r.get("started")
                        break
                except Exception:
                    continue
        established[key] = est
    receipt["flags"]["marker_state"] = marker_state
    receipt["flags"]["conversion_established"] = established
    receipt["flags"]["conversion_source"] = established_from
    if marker_state == "unparseable":
        receipt["flags"]["unparseable_marker"] = True

    conns = {}
    try:
        for key, path in DB_PATHS.items():
            conns[key] = connect(path, readonly=dry_run)
            db = receipt["databases"].setdefault(key, {})
            sz = file_sizes(path)
            db["file_bytes_before"] = sz["file"]
            db["wal_bytes_before"] = sz["wal"]
            db["shm_bytes"] = sz["shm"]
            pr = db_pragmas(conns[key])
            db["freelist_before"] = pr["freelist_count"]
            db["auto_vacuum_mode"] = pr["auto_vacuum"]
            db["page_size"] = pr["page_size"]

        # ALARM-AV-OFF: conversion established but live mode reads 0
        for key in DB_PATHS:
            if established.get(key) and receipt["databases"][key]["auto_vacuum_mode"] == 0:
                fire_alarm("AV-OFF", ["%s auto_vacuum reads 0 (NONE) though conversion is established via %s"
                                      % (key, established_from.get(key))])

        # marker repair from receipt history (S6): rewrite only with live modes
        if not dry_run and any(established.get(k) and established_from.get(k) == "receipt_history" for k in DB_PATHS) \
                and marker_state in ("missing", "unparseable"):
            live_modes = {k: receipt["databases"][k]["auto_vacuum_mode"] for k in DB_PATHS}
            cat = None
            for k in DB_PATHS:
                if established_from.get(k) == "receipt_history":
                    cat = converted_at_from_history.get(k)
                    break
            written = write_marker(live_modes, cat or receipt["started"], "receipt_history repair by nightly run")
            if written:
                receipt["flags"]["marker_repaired"] = written

        # ---- (1) type census per database -- abort conditions
        cens = {}
        con = conns["durable_audit"]
        cens["durable_audit"] = census_outbox(con)
        cens["sessions"] = census_events(conns["sessions"])
        for key in DB_PATHS:
            receipt["databases"][key]["census"] = cens[key]
            receipt["databases"][key]["rows_before"] = cens[key]["rows"]
        mismatch = [k for k in DB_PATHS if cens[k]["nonconforming_created_at"] > 0]
        if mismatch:
            receipt["status"] = "ABORTED"
            receipt["abort_reason"] = "type census mismatch in %s -- run aborted before any delete" % ", ".join(mismatch)
            fire_alarm("TYPEOF", ["nonconforming created_at storage class in %s: %s"
                                  % (k, cens[k]["nonconforming_created_at"]) for k in mismatch])
            return
        if cens["sessions"]["unparseable_created_at"] > 0:
            receipt["flags"]["events_unparseable_created_at"] = cens["sessions"]["unparseable_created_at"]

        # ALARM-UNAPPLIED-48H (publisher stall; such rows are never deleted)
        cutoff48 = time.time() - OUTBOX_FLOOR_HOURS * 3600.0
        unapplied_old = con.execute(
            "SELECT COUNT(*) FROM %s WHERE state <> 'applied' AND created_at <= ?" % OUTBOX_TABLE,
            (cutoff48,)).fetchone()[0]
        receipt["flags"]["unapplied_older_48h"] = unapplied_old
        if unapplied_old > 0:
            fire_alarm("UNAPPLIED-48H", ["%d outbox rows with state <> 'applied' are older than 48 h" % unapplied_old])

        # ---- (3) outbox chunks
        ct = Contention("outbox")
        deleted_outbox = 0
        would_outbox = outbox_delete_preview(con, cutoff48) if dry_run else None
        while not dry_run:
            if run_cap_hit():
                raise PhaseAbort("whole-run cap exceeded during outbox phase", contention_s=ct.wait_s)
            def one_chunk():
                cur = con.execute(outbox_delete_sql(), (cutoff48,))
                con.commit()
                return cur.rowcount or 0
            n = ct.run(one_chunk)
            if n:
                deleted_outbox += n
                receipt["statement_journal"].append({"table": OUTBOX_TABLE, "kind": "outbox_chunk", "rowcount": n})
            if n < CHUNK:
                break
            time.sleep(CHUNK_PAUSE_S)
        receipt["databases"]["durable_audit"]["rows_deleted"] = deleted_outbox
        receipt["databases"]["durable_audit"]["effective_cutoff"] = {
            "kind": "epoch_float_T_minus_%gh" % OUTBOX_FLOOR_HOURS, "value": cutoff48}
        if would_outbox is not None:
            receipt["databases"]["durable_audit"]["would_delete"] = would_outbox
        receipt["contention"]["outbox_wait_s"] = round(ct.wait_s, 3)

        # ---- (4) events ceiling (Sunday runs only)
        scon = conns["sessions"]
        is_sunday = started_dt.weekday() == 6
        ceiling_cutoff_dt = started_dt - timedelta(days=EVENTS_CEILING_DAYS)
        floor_dt = started_dt - timedelta(days=EVENTS_FLOOR_DAYS)
        resume_id = None
        ceiling_deleted = 0
        if is_sunday and not dry_run:
            ct2 = Contention("events_ceiling")
            last_id = 0
            while True:
                if run_cap_hit():
                    raise PhaseAbort("whole-run cap exceeded during ceiling phase", contention_s=ct2.wait_s)
                rows = ct2.run(lambda: scon.execute(
                    "SELECT id, created_at, typeof(created_at) FROM events WHERE id > ? ORDER BY id LIMIT %d" % CHUNK,
                    (last_id,)).fetchall())
                if not rows:
                    resume_id = None
                    break
                prefix = []
                stopped_at = None
                for rid, value, tclass in rows:
                    if tclass == "text":
                        dt, _off = parse_events_ts(value)
                        if dt is not None and dt < ceiling_cutoff_dt:
                            prefix.append(rid)
                            continue
                    stopped_at = rid
                    break
                if prefix:
                    def del_prefix(ids=tuple(prefix)):
                        cur = scon.execute("DELETE FROM events WHERE id IN (%s)" % ",".join("?" * len(ids)), ids)
                        scon.commit()
                        return cur.rowcount or 0
                    n = ct2.run(del_prefix)
                    ceiling_deleted += n
                    receipt["statement_journal"].append({"table": "events", "kind": "ceiling", "rowcount": n})
                if stopped_at is not None:
                    resume_id = stopped_at
                    last_id = stopped_at - 1
                    break
                last_id = rows[-1][0]
                if len(rows) < CHUNK:
                    resume_id = None
                    break
                time.sleep(CHUNK_PAUSE_S)
            receipt["contention"]["events_ceiling_wait_s"] = round(ct2.wait_s, 3)
        receipt["databases"]["sessions"]["ceiling_deleted"] = ceiling_deleted
        if is_sunday:
            receipt["databases"]["sessions"]["effective_cutoff_ceiling"] = iso(ceiling_cutoff_dt)
        if dry_run and is_sunday:
            receipt["databases"]["sessions"].setdefault("would_delete", {})["ceiling"] = {
                "count": events_count_older(scon, ceiling_cutoff_dt),
                "sample": events_preview_sample(scon, ceiling_cutoff_dt),
            }

        # ---- (5) incremental vacuum (post-conversion only, S6)
        ivac_reclaimed = {}
        for key in DB_PATHS:
            ivac_reclaimed[key] = 0
            if established.get(key) and receipt["databases"][key]["auto_vacuum_mode"] == 2:
                ivac_reclaimed[key] = incremental_vacuum_loop(conns[key], dry_run=dry_run)
        receipt["incremental_vacuum"]["pages_reclaimed_step5"] = ivac_reclaimed

        # ---- (6) checkpoint
        ck = {}
        for key in DB_PATHS:
            ck[key] = checkpoint_db(conns[key], DB_PATHS[key], dry_run=dry_run)
        receipt["checkpoints"] = ck
        busy_now = any(v.get("final_busy") == 1 for v in ck.values())
        receipt["flags"]["checkpoint_busy"] = busy_now
        prev_busy = None
        for _p, r in reversed(priors):
            if "flags" in r and "checkpoint_busy" in r["flags"]:
                prev_busy = r["flags"]["checkpoint_busy"]
                break
        if busy_now and prev_busy:
            fire_alarm("CHECKPOINT-BUSY", ["checkpoint busy on 2 consecutive runs"])

        # ---- (7) measure P; trim if projected peak exceeds the trigger
        p_now = metric_p(conns)
        receipt["flags"]["P_after_cut"] = p_now
        trim_deleted = 0
        trim_last_deleted_ts = None
        would_trim = None
        if p_now + I_ALLOW > TRIM_TRIGGER:
            ct3 = Contention("events_trim")
            cur_id = (resume_id - 1) if resume_id is not None else 0
            exhausted = False
            if dry_run:
                # projection only: simulate the floor cut without deleting or vacuuming
                sim_p = p_now
                sim_count = 0
                sim_cur = cur_id
                sim_exhausted = False
                while sim_p + I_ALLOW > TRIM_TARGET:
                    if run_cap_hit():
                        raise PhaseAbort("whole-run cap exceeded during trim projection", contention_s=ct3.wait_s)
                    rows = scon.execute(
                        "SELECT id, created_at, typeof(created_at) FROM events WHERE id > ? ORDER BY id LIMIT %d" % CHUNK,
                        (sim_cur,)).fetchall()
                    if not rows:
                        sim_exhausted = True
                        break
                    deletable = []
                    for rid, value, tclass in rows:
                        if tclass == "text":
                            dt, _off = parse_events_ts(value)
                            if dt is not None and dt < floor_dt:
                                deletable.append((rid, value))
                    if not deletable and len(rows) == CHUNK:
                        sim_exhausted = True
                        break
                    if deletable:
                        sim_count += len(deletable)
                    sim_cur = rows[-1][0]
                    if len(rows) < CHUNK:
                        sim_exhausted = True
                        break
                would_trim = {
                    "count": sim_count,
                    "sample": events_preview_sample(scon, floor_dt),
                    "floor": iso(floor_dt),
                    "exhausted_before_target": sim_exhausted,
                }
                receipt["databases"]["sessions"].setdefault("would_delete", {})["trim"] = would_trim
                receipt["contention"]["events_trim_wait_s"] = round(ct3.wait_s, 3)
            else:
                while p_now + I_ALLOW > TRIM_TARGET:
                    if run_cap_hit():
                        raise PhaseAbort("whole-run cap exceeded during trim phase", contention_s=ct3.wait_s)
                    rows = ct3.run(lambda: scon.execute(
                        "SELECT id, created_at, typeof(created_at) FROM events WHERE id > ? ORDER BY id LIMIT %d" % CHUNK,
                        (cur_id,)).fetchall())
                    if not rows:
                        exhausted = True
                        break
                    deletable = []
                    for rid, value, tclass in rows:
                        if tclass == "text":
                            dt, _off = parse_events_ts(value)
                            if dt is not None and dt < floor_dt:
                                deletable.append((rid, value))
                    if not deletable and len(rows) == CHUNK:
                        exhausted = True
                        break
                    if deletable:
                        ids = tuple(rid for rid, _v in deletable)
                        def del_trim(ids=ids):
                            cur = scon.execute("DELETE FROM events WHERE id IN (%s)" % ",".join("?" * len(ids)), ids)
                            scon.commit()
                            return cur.rowcount or 0
                        n = ct3.run(del_trim)
                        trim_deleted += n
                        trim_last_deleted_ts = deletable[-1][1]
                        receipt["statement_journal"].append({"table": "events", "kind": "trim", "rowcount": n})
                    cur_id = rows[-1][0]
                    # one incremental_vacuum pass on sessions per batch (S5 trim paragraph)
                    if established.get("sessions") and receipt["databases"]["sessions"]["auto_vacuum_mode"] == 2:
                        try:
                            scon.execute("PRAGMA incremental_vacuum(%d)" % IVAC_PAGES_PER_CALL)
                        except sqlite3.OperationalError:
                            pass
                    p_now = metric_p(conns)
                    if len(rows) < CHUNK:
                        exhausted = True
                        break
                    time.sleep(CHUNK_PAUSE_S)
                receipt["contention"]["events_trim_wait_s"] = round(ct3.wait_s, 3)
                if p_now + I_ALLOW > TRIM_TARGET and exhausted:
                    fire_alarm("FLOOR-CAP", ["%g-day floor bound before the cap target was reached: P=%d, P+I=%d"
                                             % (EVENTS_FLOOR_DAYS, p_now, p_now + I_ALLOW)])
        receipt["databases"]["sessions"]["trim_deleted"] = trim_deleted
        receipt["databases"]["sessions"]["rows_deleted"] = ceiling_deleted + trim_deleted
        if trim_deleted:
            receipt["databases"]["sessions"]["effective_cutoff_trim"] = {
                "floor": iso(floor_dt), "last_deleted_created_at": trim_last_deleted_ts}

        # ---- (8) closing vacuum + checkpoint + final measurement (only if the trim ran)
        if trim_deleted and not dry_run:
            for key in DB_PATHS:
                if established.get(key) and receipt["databases"][key]["auto_vacuum_mode"] == 2:
                    incremental_vacuum_loop(conns[key])
            for key in DB_PATHS:
                try:
                    conns[key].execute("PRAGMA wal_checkpoint(PASSIVE)").fetchone()
                except sqlite3.OperationalError:
                    pass
            for key in DB_PATHS:
                pr = db_pragmas(conns[key])
                fs = fsize(DB_PATHS[key])
                if abs(fs - pr["page_count"] * pr["page_size"]) > ESTIMATOR_TOLERANCE:
                    receipt["flags"]["step8_estimator_disagreement_%s" % key] = {
                        "fs_bytes": fs, "page_bytes": pr["page_count"] * pr["page_size"]}
        p_final = metric_p(conns)
        receipt["flags"]["P_final"] = p_final

        # ---- (10) live-state counts (informational)
        receipt["live_state_counts"] = live_counts(scon)

        # per-DB after figures
        for key in DB_PATHS:
            db = receipt["databases"][key]
            sz = file_sizes(DB_PATHS[key])
            db["file_bytes_after"] = sz["file"]
            db["wal_bytes_after"] = sz["wal"]
            pr = db_pragmas(conns[key])
            db["freelist_after"] = pr["freelist_count"]
            db.setdefault("rows_deleted", 0)
            if key == "durable_audit":
                db["rows_after"] = conns[key].execute("SELECT COUNT(*) FROM %s" % OUTBOX_TABLE).fetchone()[0]
            else:
                db["rows_after"] = conns[key].execute("SELECT COUNT(*) FROM events").fetchone()[0]
            if dry_run:
                # nothing was deleted: after == before by construction
                db["rows_after"] = db["rows_before"]
                db["freelist_after"] = db["freelist_before"]

        # ---- ALARM-CAP-AFTER-RUN on the measured post-run state
        if p_final > CAP or p_final + I_ALLOW > TRIM_TRIGGER:
            fire_alarm("CAP-AFTER-RUN", ["post-run combined = %d B; P + I = %d B" % (p_final, p_final + I_ALLOW)])
            if dry_run:
                receipt["flags"]["cap_alarm_in_dry_run"] = True

        # ---- status resolution
        flagged = (cens["sessions"]["unparseable_created_at"] > 0
                   or marker_state == "unparseable"
                   or any(k.startswith("step8_estimator_disagreement") for k in receipt["flags"]))
        if flagged:
            receipt["status"] = "OK-FLAGGED"
        else:
            receipt["status"] = "OK"
    finally:
        for c in conns.values():
            try:
                c.close()
            except Exception:
                pass


def incremental_vacuum_loop(con, dry_run=False):
    """Loop incremental_vacuum(4096) until freelist <= target or the 60 s budget
    expires (design S6). Returns pages reclaimed. dry_run: report only, no PRAGMA."""
    initial = con.execute("PRAGMA freelist_count").fetchone()[0]
    if dry_run:
        return max(0, initial - IVAC_FREELIST_TARGET)
    current = initial
    t0 = time.monotonic()
    while current > IVAC_FREELIST_TARGET and (time.monotonic() - t0) < IVAC_TIME_BUDGET_S:
        try:
            con.execute("PRAGMA incremental_vacuum(%d)" % IVAC_PAGES_PER_CALL)
        except sqlite3.OperationalError:
            break
        after = con.execute("PRAGMA freelist_count").fetchone()[0]
        if after >= current:
            current = after
            break
        current = after
    return initial - current


def checkpoint_db(con, path, dry_run=False):
    out = {"attempts": []}
    if dry_run:
        out["dry_run"] = True
        out["final_busy"] = 0
        return out
    try:
        r = con.execute("PRAGMA wal_checkpoint(PASSIVE)").fetchone()
        out["attempts"].append({"mode": "PASSIVE", "busy": r[0], "log": r[1], "checkpointed": r[2]})
        out["final_busy"] = r[0]
    except sqlite3.OperationalError as exc:
        out["attempts"].append({"mode": "PASSIVE", "error": str(exc)})
        out["final_busy"] = 1
    if fsize(path + "-wal") > WAL_TRUNCATE_THRESHOLD:
        try:
            r = con.execute("PRAGMA wal_checkpoint(TRUNCATE)").fetchone()
            out["attempts"].append({"mode": "TRUNCATE", "busy": r[0], "log": r[1], "checkpointed": r[2]})
            out["final_busy"] = r[0]
        except sqlite3.OperationalError as exc:
            out["attempts"].append({"mode": "TRUNCATE", "error": str(exc)})
            out["final_busy"] = 1
    return out


def render_md(r):
    lines = []
    lines.append("# Ledger sweep receipt — %s" % r["run_id"])
    lines.append("")
    lines.append("- status: **%s**%s" % (r["status"], (" — " + r["abort_reason"]) if r.get("abort_reason") else ""))
    lines.append("- started: %s · finished: %s · host: %s · script: %s" % (r["started"], r.get("finished"), r["host"], r["script_version"]))
    lines.append("- alarms: %s" % (", ".join(r["alarms_fired"]) if r["alarms_fired"] else "none"))
    if r.get("dry_run"):
        lines.append("- **DRY RUN** - no deletion occurred; would-delete counts under each database")
    for key, db in r["databases"].items():
        lines.append("- %s: file %s -> %s B · WAL %s -> %s B · rows %s -> %s (deleted %s) · freelist %s -> %s · auto_vacuum %s"
                     % (key, db.get("file_bytes_before"), db.get("file_bytes_after"),
                        db.get("wal_bytes_before"), db.get("wal_bytes_after"),
                        db.get("rows_before"), db.get("rows_after"), db.get("rows_deleted"),
                        db.get("freelist_before"), db.get("freelist_after"), db.get("auto_vacuum_mode")))
        wd = db.get("would_delete")
        if wd:
            if key == "durable_audit":
                lines.append("  - would delete: %s rows (outbox predicate)" % wd.get("count", 0))
            else:
                for phase in ("ceiling", "trim"):
                    if wd.get(phase):
                        lines.append("  - would delete (%s): %s rows" % (phase, wd[phase].get("count", 0)))
    fl = r.get("flags", {})
    if "P_final" in fl:
        lines.append("- P_final (combined file+WAL): %s B · P + I: %s B · cap 1,000,000,000 B" % (fl["P_final"], fl["P_final"] + I_ALLOW))
    lines.append("- spool: %s -> %s B · free disk after: %s B" % (r.get("spool_bytes_before"), r.get("spool_bytes_after"), r.get("free_disk_after")))
    if r.get("live_state_counts"):
        lines.append("- live-state counts (informational): %s" % json.dumps(r["live_state_counts"]))
    lines.append("- statement journal: %d DELETE statements" % len(r.get("statement_journal", [])))
    return "\n".join(lines) + "\n"


if __name__ == "__main__":
    sys.exit(main())
