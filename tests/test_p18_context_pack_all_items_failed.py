"""P18: architecture_context_pack must not answer ok:true when every item read failed.

The aggregate answered `"ok": bool(batch.get("ok")) and bool(batch.get("items"))`.
A batch can report ok:true with a non-empty item list where *every* item carries
ok:false (e.g. all graph ranges stale) -- the pack then claimed success with
zero usable content, indistinguishable from a healthy pack.

The fix: ok requires at least one item with ok:true --
`bool(batch.get("ok")) and any(item.get("ok") for item in batch.get("items", []))`.
This pins all three sides: all-fail -> ok:false; mixed -> ok:true (as before);
all-ok -> ok:true (as before). No restart needed to exercise: the aggregate is
pure once read_context_batch/_graph_nodes are faked.
"""
from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from orchestrator.tools.read import context as mod


def _run_pack(monkeypatch, items):
    def fake_graph_nodes(*a, **k):
        return [], {"graph_used": False, "graph_error": None}

    async def fake_batch(*a, **k):
        return json.dumps({"ok": True, "items": items, "token_budget": 6000,
                           "used_tokens": 10, "remaining_tokens": 5990,
                           "within_budget": True, "next_ranges": []})

    monkeypatch.setattr(mod, "_graph_nodes", fake_graph_nodes)
    monkeypatch.setattr(mod, "read_context_batch", fake_batch)
    raw = asyncio.run(mod.architecture_context_pack(
        project_path="D:/proj", file_path="some/file.py"))
    return json.loads(raw)


def _item(ok):
    return {"ok": ok, "file_path": "some/file.py", "scope": "file_summary",
            "content": "x" if ok else "", "error": None if ok else "STALE"}


def test_all_items_failed_reports_not_ok(monkeypatch):
    payload = _run_pack(monkeypatch, [_item(False), _item(False)])
    assert payload["ok"] is False


def test_mixed_items_keeps_ok(monkeypatch):
    payload = _run_pack(monkeypatch, [_item(True), _item(False)])
    assert payload["ok"] is True


def test_all_items_ok_keeps_ok(monkeypatch):
    payload = _run_pack(monkeypatch, [_item(True), _item(True)])
    assert payload["ok"] is True
