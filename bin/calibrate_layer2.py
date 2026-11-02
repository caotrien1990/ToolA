"""Calibrate a 30s trigger threshold from a measured storm and calm log day."""
from __future__ import annotations
import argparse
from collections import deque
import hashlib
import json
import math
from pathlib import Path
import re
import statistics

TRIGGER = re.compile(rb"store contention|busy budget", re.I)
STAMP = re.compile(rb"^(\d{4}-\d\d-\d\d)[ T](\d\d):(\d\d):(\d\d)")


def calibrate(paths, storm_day, calm_day):
    days = {storm_day: {"lines": 0, "times": []}, calm_day: {"lines": 0, "times": []}}
    sources = []
    for path in paths:
        path = Path(path).resolve()
        with path.open("rb") as stream:
            stat = path.stat()
            size = stat.st_size
            remaining = size
            digest = hashlib.sha256()
            while remaining:
                line = stream.readline(remaining)
                if not line:
                    raise RuntimeError("source truncated during calibration: " + str(path))
                remaining -= len(line)
                digest.update(line)
                match = STAMP.match(line)
                if not match or not line.endswith(b"\n"):
                    continue
                day = match[1].decode("ascii")
                if day not in days:
                    continue
                days[day]["lines"] += 1
                if TRIGGER.search(line):
                    h, m, s = map(int, match.groups()[1:])
                    days[day]["times"].append(h * 3600 + m * 60 + s)
            sources.append(dict(path=str(path), captured_bytes=size, sha256=digest.hexdigest()))
    for info in days.values():
        queue, counts = deque(), []
        for when in sorted(info.pop("times")):
            while queue and when - queue[0] > 30:
                queue.popleft()
            queue.append(when)
            counts.append(len(queue))
        info.update(trigger_lines=len(counts), max_30s=max(counts, default=0),
                    median_event_window=statistics.median(counts) if counts else 0)
    calm_max = days[calm_day]["max_30s"]
    storm_median = days[storm_day]["median_event_window"]
    if not days[calm_day]["lines"] or storm_median <= calm_max + 1:
        raise ValueError("calm/storm distributions do not separate; no threshold invented")
    threshold = math.ceil((calm_max + storm_median) / 2)
    return dict(storm_day=storm_day, calm_day=calm_day, days=days, sources=sources,
                window_seconds=30, threshold=threshold,
                formula="ceil((calm.max_30s + storm.median_event_window) / 2)",
                sampling="trailing inclusive 30s windows at matching log lines")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--storm-day", required=True)
    parser.add_argument("--calm-day", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("logs", nargs="+")
    args = parser.parse_args()
    report = calibrate(args.logs, args.storm_day, args.calm_day)
    Path(args.output).write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({key: report[key] for key in ("threshold", "days", "formula")}))


if __name__ == "__main__":
    main()
