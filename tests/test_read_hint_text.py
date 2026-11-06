"""Hint-text pins for the ToolA read path (hint-fix package, 2026-11-07).

The two prose surfaces a CA actually steers by, pinned as text:

  * the passive-chain ca_hint attached to a truncated read_file_range
    response. It used to name only the paging path (next_ranges); it
    must also name the token_budget alternative and its 12000 cap, or
    a CA that follows hints keeps paging a large file 200 lines at a
    time. The row's ca_next_action is pinned UNCHANGED here on
    purpose: ca_next_tool derives from it, and this package is
    prose-only.
  * the token_budget parameter description in the served
    read_file_range schema (built by build_tool_schemas from
    registry_data). It must state the shipped budget-mode facts:
    the budget replaces the default line caps, delivery stops at the
    budget, and the absolute cap is 12000.

In-process only, like the other read tests; never the live daemon.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest

import code_tools
from orchestrator.core.next_tool import annotate_next_tool
from orchestrator.core.passive_chain import PASSIVE_CHAINS, apply_passive_chain
from orchestrator.core.registry_data import build_tool_schemas
from orchestrator.tools.read import adaptive


def _clear_read_cache() -> None:
    with adaptive._CACHE_COND:
        adaptive._CACHE_BY_PATH.clear()
        adaptive._CACHE_BY_HASH.clear()
        adaptive._INFLIGHT.clear()
        adaptive._CACHE_BYTES = 0


@pytest.fixture(autouse=True)
def _deterministic_seed(monkeypatch):
    monkeypatch.setattr(
        code_tools, "_read_token_upsert", lambda *a, **k: True, raising=False)
    _clear_read_cache()
    yield
    _clear_read_cache()


def _read(tmp_path: Path, name: str, **kwargs) -> dict:
    raw = asyncio.run(code_tools.read_file_range(
        name, project_path=str(tmp_path), **kwargs))
    return json.loads(raw)


def _truncated_row():
    return next(
        row for row in PASSIVE_CHAINS
        if row.tool == "read_file_range" and row.trigger == "truncated")


def test_truncated_row_hint_names_the_budget_option():
    row = _truncated_row()
    assert "token_budget" in row.hint
    assert "12000" in row.hint
    assert row.next_action == "call_read_file_range_on_the_next_range"


def test_truncated_legacy_read_carries_the_budget_hint(tmp_path):
    path = tmp_path / "big.py"
    path.write_bytes(
        "".join(f"value_{i} = {i}\n" for i in range(600)).encode("utf-8"))
    payload = _read(tmp_path, "big.py")
    assert payload["ok"] is True
    assert payload["truncated"] is True
    fired = apply_passive_chain("read_file_range", payload)
    assert fired == "truncated"
    assert "token_budget" in payload["ca_hint"]
    assert "12000" in payload["ca_hint"]
    assert payload["ca_next_action"] == "call_read_file_range_on_the_next_range"
    annotate_next_tool(payload, code_tools.TOOL_MAP, "read_file_range")
    assert payload["ca_next_tool"] == "read_file_range"


def _token_budget_description() -> str:
    for entry in build_tool_schemas():
        function = entry.get("function") or {}
        if function.get("name") == "read_file_range":
            properties = function["parameters"]["properties"]
            return properties["token_budget"]["description"]
    raise AssertionError("read_file_range schema not found")


def test_token_budget_description_states_the_shipped_facts():
    description = _token_budget_description()
    lowered = description.lower()
    assert "12000" in description
    assert "line" in lowered and "replac" in lowered
    assert "result_ref" in description
    assert "0" in description and "line bounds" in lowered
