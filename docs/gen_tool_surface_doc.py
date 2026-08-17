#!/usr/bin/env python3
"""Generate a canonical, distill-friendly doc of ToolA's current tool surface.

Why: the live tool count is runtime-computed (orchestrator_server total_available
= len(...)), so it exists nowhere as text that ToolQ's folder-watcher can distill
into a memory point. Stale hardcoded counts in ad-hoc scripts then win retrieval.
This script writes docs/TOOL_SURFACE_CURRENT.md from the live /api/tools/names so
the watcher (Z2 watches this project) distills the CURRENT number as a high-signal
fact. Re-run manually or on a schedule; the doc is overwritten in place.

Usage: python docs/gen_tool_surface_doc.py [--base http://localhost:8083]
"""
from __future__ import annotations

import argparse
import json
import urllib.request
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

DOC = Path(__file__).resolve().parent / "TOOL_SURFACE_CURRENT.md"


def fetch(base: str) -> dict:
    req = urllib.request.Request(base.rstrip("/") + "/api/tools/names", method="GET")
    with urllib.request.urlopen(req, timeout=15) as r:
        return json.loads(r.read())


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", default="http://localhost:8083")
    args = ap.parse_args()
    data = fetch(args.base)
    tools = data.get("tools") or []
    total = int(data.get("total_available") or len(tools))
    by_tier = Counter(str(t.get("tier") or "?") for t in tools)
    tier_line = ", ".join(f"{k}={by_tier[k]}" for k in sorted(by_tier))
    now = datetime.now(timezone.utc).isoformat()
    lines = [
        "# ToolA — Tool Surface (canonical, live-generated)",
        "",
        f"ToolA (Orchestrator [A]) hiện có **{total} tool** tính đến {now}.",
        "",
        f"Phân bố theo tier: {tier_line}.",
        "",
        "> Nguồn chuẩn duy nhất cho số lượng tool của ToolA. Sinh tự động từ",
        "> `/api/tools/names` bởi `docs/gen_tool_surface_doc.py`. KHÔNG sửa tay —",
        "> số cứng ở nơi khác (script test, tài liệu cũ) là lỗi thời, dùng số ở đây.",
        "",
        f"Tổng số tool hiện tại của ToolA: {total}.",
        "",
    ]
    DOC.write_text("\n".join(lines), encoding="utf-8")
    print(f"wrote {DOC} total={total} tiers={tier_line}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())