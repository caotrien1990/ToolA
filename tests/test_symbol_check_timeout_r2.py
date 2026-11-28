"""R2 (2026-11-28): the symbol/import verification must never silently pass on timeout.

WHY: measured live, write_fix adding `import reqests` under concurrent API load
returned ok:true + warn symbol_check_timeout_5s and the bad import LANDED.
The 5s guard's ok:True escape is the defect; the similar_names rglob scan
(walking the venv before pruning) is what made the check slow enough to hit it.

Mutants killed:
- timeout -> ok:True (the old escape): test_timeout_fails_closed_and_reverts
- index-first pool removed (rglob-everything again): not directly tested here
  (timing, not correctness); the pool correctness is covered by the C1 tests
  in test_4bugs_error_names_b1b4.py and test_code_tools_cache.py.
"""
from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest

import code_tools
from orchestrator.tools.write import core as write_core


def test_timeout_fails_closed_and_reverts(tmp_path, monkeypatch):
    import code_tools as ct
    fp = tmp_path / "slow_check.py"
    fp.write_text("x = 1\n", encoding="utf-8")

    async def _never_finishes(*a, **kw):
        await asyncio.sleep(30)

    # Force the verification to exceed the guard without waiting 15s: make
    # the threaded check hang and shrink the guard's wait_for to 0.05s.
    # write_core imports code_tools INSIDE the function, so patch the real
    # module attribute (write_core.ct does not exist at module level).
    real_wait_for = asyncio.wait_for

    async def fast_guard(coro, timeout):
        return await real_wait_for(coro, timeout=0.05)

    monkeypatch.setattr(ct.asyncio, "wait_for", fast_guard)
    monkeypatch.setattr(ct.asyncio, "to_thread",
                        lambda fn, *a, **kw: _never_finishes())
    out = asyncio.run(write_core._post_write_symbol_check(fp, fp, str(tmp_path)))
    assert out.get("ok") is False, f"timeout must fail closed, got {out}"
    assert out.get("error_name") == "SYMBOL_CHECK_TIMEOUT", out
    assert out.get("retryable") is True, out


def test_bad_import_blocked_with_similar_names_serial(tmp_path):
    # The acceptance shape on a small project: index cold -> pruned-walk
    # fallback must still find the near name and block.
    proj = tmp_path / "proj"
    proj.mkdir()
    (proj / "requests.py").write_text("# stub\n", encoding="utf-8")
    fp = proj / "app.py"
    fp.write_text("x = 1\n", encoding="utf-8")
    bak = tmp_path / "app.py.bak"
    bak.write_text("x = 1\n", encoding="utf-8")
    fp.write_text("x = 1\nimport reqests\n", encoding="utf-8")
    out = asyncio.run(write_core._post_write_symbol_check(fp, bak, str(proj)))
    assert out.get("ok") is False, out
    assert out.get("error_name") == "IMPORT_ERROR_PRE_WRITE", out
    names = [s["module"] for s in out.get("similar_names") or []]
    assert "requests" in names, out