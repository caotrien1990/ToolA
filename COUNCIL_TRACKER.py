#!/usr/bin/env python3
r"""Council tracker — track logs, output rich data to D:\BR\LIVE_COUNCIL\council_live.txt"""
import json
import os
import glob
import sys
import time
import zipfile

COUNCIL_DIR = r"D:\0. TRIEN\1. DATA\DATA\5. VIBE APP\1. APP\8. ToolA\project-orchestrator\.council"
STATE_FILE = os.path.join(COUNCIL_DIR, ".log_lines.json")
OUT_DIR = r"D:\0. TRIEN\1. DATA\DATA\5. VIBE APP\1. APP\8. ToolA\project-orchestrator\.council\Live_council"
OUT_FILE = os.path.join(OUT_DIR, "council_live.txt")
DELIVERY_THRESHOLD = 2
IDLE_PAUSE_LIMIT = 15
FULL_EVENT_TAIL = 8
FULL_RAW_TAIL_CHARS = 30000

LOG_FILES = [
    "live_raw_codo.log", "live_raw_clau.log", "live_raw_pic.log",
    "live_raw_hermes.log", "live_raw_ope.log",
    "broker_filtered.jsonl",
    "council_events.jsonl",
    "stall_alerts.jsonl",
]
for pattern in ("inbox_*.md", "outbox_*.md"):
    for f in sorted(glob.glob(os.path.join(COUNCIL_DIR, pattern))):
        fname = os.path.basename(f)
        if fname not in LOG_FILES:
            LOG_FILES.append(fname)


def load_state():
    try:
        with open(STATE_FILE) as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def save_state(state):
    with open(STATE_FILE, 'w') as f:
        json.dump(state, f)


def count_lines(filepath):
    try:
        with open(filepath, 'rb') as f:
            return sum(1 for _ in f)
    except FileNotFoundError:
        return 0


def read_tail(filepath, max_lines=10):
    try:
        with open(filepath, 'r', encoding='utf-8', errors='replace') as f:
            lines = f.readlines()
        return lines[-max_lines:]
    except FileNotFoundError:
        return []


def read_text_tail(filepath, max_chars=FULL_RAW_TAIL_CHARS):
    try:
        with open(filepath, 'r', encoding='utf-8', errors='replace') as f:
            data = f.read()
        return data[-max_chars:]
    except FileNotFoundError:
        return ""


def read_jsonl_tail(filepath, max_entries=FULL_EVENT_TAIL):
    entries = []
    for raw in read_tail(filepath, max_entries):
        raw = raw.strip()
        if not raw:
            continue
        try:
            entries.append(json.loads(raw))
        except json.JSONDecodeError:
            entries.append({"_parse_error": raw})
    return entries


def append_full_jsonl_section(lines, title, filepath, max_entries=FULL_EVENT_TAIL):
    entries = read_jsonl_tail(filepath, max_entries)
    if not entries:
        return
    lines.append("")
    lines.append("-- %s (last %d, full JSON) --" % (title, len(entries)))
    for idx, entry in enumerate(entries, 1):
        lines.append("")
        lines.append("### %s #%d" % (title, idx))
        lines.append(json.dumps(entry, ensure_ascii=False, indent=2))


def append_full_raw_section(lines, title, filepath, max_chars=FULL_RAW_TAIL_CHARS):
    data = read_text_tail(filepath, max_chars)
    if not data:
        return
    lines.append("")
    lines.append("-- %s (tail %d chars, untruncated) --" % (title, len(data)))
    lines.append(data.rstrip())


def format_timestamp(ts):
    return time.strftime("%H:%M:%S", time.localtime(ts))


def open_output_file(path):
    """No-op (auto-open REMOVED by Anh's request, 2026-06-17).

    The tracker must NEVER pop a desktop window for the text log: a CA cannot read
    a GUI-opened file and it disrupts the user. The collector only WRITES the
    snapshot; consumers (the [A] `council_tracker` tool / any CA) read it from the
    returned output path. Kept as a no-op so existing callers stay valid; the old
    COUNCIL_TRACKER_OPEN / COUNCIL_TRACKER_NO_OPEN env switches are intentionally
    ignored — no env value re-enables opening.
    """
    return


def main():
    try:
        sys.stdout.reconfigure(encoding='utf-8', errors='replace')  # type: ignore[reportAttributeAccessIssue]
    except Exception:
        pass

    state = load_state()
    total_delta = 0
    per_file = {}

    for fname in LOG_FILES:
        fpath = os.path.join(COUNCIL_DIR, fname)
        current = count_lines(fpath)
        prev = state.get(fname, current)
        delta = current - prev
        per_file[fname] = delta
        total_delta += delta

    now = time.time()
    ts = format_timestamp(now)
    lines = []
    lines.append("=== COUNCIL TRACKER %s ===" % ts)

    if total_delta < DELIVERY_THRESHOLD:
        idle_ticks = state.get("_idle_ticks", 0) + 1
        state["_idle_ticks"] = idle_ticks
        lines.append("STATUS: IDLE (%d/%d)" % (idle_ticks, IDLE_PAUSE_LIMIT))
    else:
        state["_idle_ticks"] = 0
        lines.append("STATUS: ACTIVE (+%d new lines)" % total_delta)

    # File deltas
    lines.append("")
    lines.append("-- LINE DELTAS --")
    for fname in LOG_FILES:
        d = per_file[fname]
        fpath = os.path.join(COUNCIL_DIR, fname)
        total = count_lines(fpath)
        if d != 0:
            lines.append("  %s → %+d (total %d)" % (fname, d, total))

    # Broker filtered entries (latest)
    bpath = os.path.join(COUNCIL_DIR, "broker_filtered.jsonl")
    tail = read_tail(bpath, 5)
    if tail:
        lines.append("")
        lines.append("-- BROKER FILTERED (last 5) --")
        for raw in tail:
            raw = raw.strip()
            if not raw:
                continue
            try:
                e = json.loads(raw)
            except json.JSONDecodeError:
                lines.append("  [parse error: %s]" % raw[:80])
                continue
            ts_e = e.get("ts", "?")
            from_ = e.get("from", "?")
            to_ = e.get("to", "?")
            typ = e.get("type", "?")
            ok = e.get("ok", "")
            text = e.get("text", "")
            preview = text.strip().split("\n")[0][:200] if text else ""
            lines.append("  [%s] %s → %s  type=%s  ok=%s" % (ts_e, from_, to_, typ, ok))
            if preview:
                lines.append("    \"%s\"" % preview)

    # Inbox/Outbox
    for fname in LOG_FILES:
        if not fname.startswith("inbox_") and not fname.startswith("outbox_"):
            continue
        d = per_file[fname]
        fpath = os.path.join(COUNCIL_DIR, fname)
        tail = read_tail(fpath, 3)
        if not tail:
            continue
        label = fname.replace("inbox_", "").replace("outbox_", "").replace(".md", "")
        direction = "INBOX" if fname.startswith("inbox_") else "OUTBOX"
        lines.append("")
        lines.append("-- %s %s (last 3) --" % (direction, label))
        for line in tail:
            line = line.strip()
            if line:
                lines.append("  %s" % line[:200])

    # Council events
    epath = os.path.join(COUNCIL_DIR, "council_events.jsonl")
    tail = read_tail(epath, 5)
    if tail:
        lines.append("")
        lines.append("-- COUNCIL EVENTS (last 5) --")
        for raw in tail:
            raw = raw.strip()
            if not raw:
                continue
            try:
                e = json.loads(raw)
            except json.JSONDecodeError:
                lines.append("  [parse error]")
                continue
            lines.append("  %s" % json.dumps(e, ensure_ascii=False)[:300])

    # Full audit sections: keep these untruncated so prompt/ARGV issues are visible.
    append_full_jsonl_section(
        lines,
        "BROKER FILTERED FULL",
        os.path.join(COUNCIL_DIR, "broker_filtered.jsonl"),
        FULL_EVENT_TAIL,
    )
    append_full_jsonl_section(
        lines,
        "COUNCIL EVENTS FULL",
        os.path.join(COUNCIL_DIR, "council_events.jsonl"),
        FULL_EVENT_TAIL,
    )
    append_full_jsonl_section(
        lines,
        "STALL ALERTS",
        os.path.join(COUNCIL_DIR, "stall_alerts.jsonl"),
        FULL_EVENT_TAIL,
    )
    append_full_raw_section(
        lines,
        "LIVE RAW COMC FULL",
        os.path.join(COUNCIL_DIR, "live_raw_pic.log"),
        FULL_RAW_TAIL_CHARS,
    )

    # Live raw logs summary (latest 2 lines per agent)
    for agent in ("codo", "clau", "pic", "hermes", "ope"):
        fname = "live_raw_%s.log" % agent
        fpath = os.path.join(COUNCIL_DIR, fname)
        tail = read_tail(fpath, 2)
        if tail:
            lines.append("")
            lines.append("-- LIVE RAW %s (last 2) --" % agent.upper())
            for line in tail:
                line = line.strip()
                if line:
                    lines.append("  %s" % line[:200])

    # Runtime state. A persisted "running" row can outlive its worker process
    # after a crash/restart, so report it as stale unless worker_pid is live.
    rpath = os.path.join(COUNCIL_DIR, "runtime_state.json")
    try:
        with open(rpath) as f:
            rs = json.load(f)
        def worker_alive(item):
            pid = item.get("worker_pid")
            if not isinstance(pid, int) or pid <= 0:
                return False
            try:
                os.kill(pid, 0)
                return True
            except OSError:
                return False
        running = [k for k, v in rs.items() if v.get("state") == "running" and worker_alive(v)]
        stale_running = [k for k, v in rs.items() if v.get("state") == "running" and not worker_alive(v)]
        completed = [k for k, v in rs.items() if v.get("state") == "completed"]
        if running or stale_running or completed:
            lines.append("")
            lines.append("-- RUNTIME STATE --")
            if running:
                lines.append("  Running: %s" % ", ".join(running))
            if stale_running:
                lines.append("  Stale running records (worker process exited): %s" % ", ".join(stale_running))
            if completed:
                lines.append("  Completed: %s" % ", ".join(completed))
    except (FileNotFoundError, json.JSONDecodeError):
        pass
        pass

    # Idle pause suggestion
    idle_ticks = state.get("_idle_ticks", 0)
    if idle_ticks >= IDLE_PAUSE_LIMIT:
        lines.append("")
        lines.append("!! IDLE PAUSE SUGGESTED (%d ticks)" % idle_ticks)

    lines.append("")

    # Persist state
    for fname in LOG_FILES:
        state[fname] = count_lines(os.path.join(COUNCIL_DIR, fname))
    save_state(state)

    # Archive old file if exists (zip + delete original)
    if os.path.exists(OUT_FILE):
        archive_dir = os.path.join(OUT_DIR, "archives")
        os.makedirs(archive_dir, exist_ok=True)
        arc_name = "council_live_%s.zip" % time.strftime("%Y%m%d_%H%M%S", time.localtime(now))
        arc_path = os.path.join(archive_dir, arc_name)
        try:
            with zipfile.ZipFile(arc_path, 'w', zipfile.ZIP_DEFLATED) as zf:
                zf.write(OUT_FILE, "council_live.txt")
            os.remove(OUT_FILE)
        except Exception:
            pass

    # Write output
    os.makedirs(OUT_DIR, exist_ok=True)
    with open(OUT_FILE, 'w', encoding='utf-8') as f:
        f.write("\n".join(lines))

    open_output_file(OUT_FILE)

    # Also stdout for cron data-collection injection
    print("\n".join(lines))


if __name__ == "__main__":
    main()
