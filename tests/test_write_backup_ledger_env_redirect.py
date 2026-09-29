"""2026-09-28: ORCHESTRATOR_WRITE_BACKUP_LEDGER_DIR redirects the write-backup WAL.

Defect (measured): `_write_backup_ledger_path` / `_write_backup_seed_marker_path`
(orchestrator/tools/write/core.py:1146-1151) were hardwired to
`parents[3] / ".orchestrator_cache"`, so EVERY pytest run performing a real
write_fix appended its tmp_path .bak row into the REAL repo ledger -- the one
boot recovery (`_boot_sweep_ledger_candidates`) reads (measured before the
fence: 212 rows, 54928 bytes). No env override existed.

Fix: ONE directory helper `_write_backup_ledger_dir` (core.py:1131-1143) reads
`ORCHESTRATOR_WRITE_BACKUP_LEDGER_DIR` at CALL time (line 1140, so both a
pytest_configure default and monkeypatch.setenv land); set non-empty -> that
directory (1141-1142), unset/empty -> the byte-for-byte old default (1143).
conftest.py:158-161 fills a per-run `orch_pytest_bak_ledger_` tmp dir exactly
like the SESSIONS_DB / GENERATION_FILE blocks and registers it in
_B031_OWNED_TEMP_DIRS, so pytest_unconfigure removes it.

The ledger and its seed marker keep sharing ONE directory under the redirect
too (A02/A30 shared fate, pinned by test_a02_a30_interaction.py).
"""
from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path

import pytest

import code_tools
import surgical_mapper
from orchestrator.tools.write import core as write_core

_ENV = write_core._WRITE_BACKUP_LEDGER_DIR_ENV
_REPO_CACHE = Path(write_core.__file__).resolve().parents[3] / ".orchestrator_cache"


@pytest.fixture
def project(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """Fresh sessions DB + a real project root holding one small file
    (same shape as tests/test_read_token_contention.py::project)."""
    db_path = tmp_path / "sessions_bakledger.db"
    monkeypatch.setattr(code_tools, "_SESSIONS_DB", db_path)
    code_tools._sync_session_db_alias()
    code_tools._init_session_db()
    root = tmp_path / "proj_bak"
    root.mkdir()
    (root / ".git").mkdir()
    target = root / "mod.py"
    target.write_text("x = 1\n", encoding="utf-8")
    yield root, target
    monkeypatch.undo()
    code_tools._sync_session_db_alias()


def _read(target: Path, root: Path, **kwargs) -> dict:
    return json.loads(asyncio.run(code_tools.read_file_range(
        str(target), 1, 1, project_path=str(root), **kwargs)))


def _repo_ledger_stat() -> tuple | None:
    """(size, mtime_ns) of the REAL repo ledger, or None when absent."""
    led = _REPO_CACHE / write_core._WRITE_BACKUP_LEDGER_NAME
    if not led.exists():
        return None
    st = led.stat()
    return (st.st_size, st.st_mtime_ns)


# -- (a) env set -> BOTH paths resolve under the override ---------------------

def test_env_set_redirects_ledger_and_marker_together(tmp_path, monkeypatch):
    """Pins core.py:1140-1142 (call-time env read, truthy -> Path(override))
    and 1146-1151 (BOTH path functions go through the one directory helper, so
    the redirect can never split ledger and marker -- A02/A30 shared fate).
    Reading env at IMPORT time would keep this green only if the module were
    re-imported; the call-time read is what makes monkeypatch.setenv work."""
    wal = tmp_path / "wal_override"
    monkeypatch.setenv(_ENV, str(wal))

    led = write_core._write_backup_ledger_path()
    mk = write_core._write_backup_seed_marker_path()

    assert led == wal / write_core._WRITE_BACKUP_LEDGER_NAME
    assert mk == wal / write_core._WRITE_BACKUP_SEED_MARKER
    assert led.parent == mk.parent == wal, "redirect split ledger and marker"


# -- (b) env unset / empty -> byte-for-byte the old default --------------------

def test_env_unset_resolves_the_repo_cache_default(monkeypatch):
    """Pins core.py:1141-1143: falsy env falls through to
    `Path(__file__).resolve().parents[3] / ".orchestrator_cache"`, the exact
    pre-override expression. Explicit delenv: conftest fills the default for
    every pytest process, so 'unset' must be manufactured, never inherited."""
    monkeypatch.delenv(_ENV, raising=False)

    led = write_core._write_backup_ledger_path()
    mk = write_core._write_backup_seed_marker_path()

    assert led == _REPO_CACHE / "write_backups.jsonl"
    assert mk == _REPO_CACHE / ".write_backups.seeded"
    assert led.parent == _REPO_CACHE


def test_env_empty_string_is_treated_as_unset(tmp_path, monkeypatch):
    """Pins the truthiness check at core.py:1141 (`if override:`), not an
    `is not None` check: an EMPTY value must take the default branch, or a
    caller exporting VAR= would silently relocate the WAL to Path("") -- cwd.
    """
    monkeypatch.setenv(_ENV, "")

    led = write_core._write_backup_ledger_path()
    mk = write_core._write_backup_seed_marker_path()

    assert led == _REPO_CACHE / "write_backups.jsonl"
    assert mk == _REPO_CACHE / ".write_backups.seeded"


# -- (c) a REAL write_fix journals into the redirect, repo ledger untouched ----

def test_real_write_fix_journals_into_redirect_and_leaves_repo_ledger_alone(
        project, tmp_path, monkeypatch):
    """The production defect, end to end. Pins core.py:1147 through the real
    `_record_write_backup` call inside write_fix: with the env pointed at a tmp
    dir, the .bak row lands THERE and the real repo ledger's (size, mtime_ns)
    are unchanged. Before the fix this test's write_fix would have appended a
    tmp_path row to the ledger boot recovery (_boot_sweep_ledger_candidates)
    reads."""
    root, target = project
    wal = tmp_path / "wal_live"
    wal.mkdir()
    monkeypatch.setenv(_ENV, str(wal))

    surgical_mapper.ensure_minimal_index(str(root))
    assert _read(target, root)["read_token_seeded"] is True

    before = _repo_ledger_stat()
    out = json.loads(asyncio.run(code_tools.write_fix(
        "mod.py", 1, 1, "x = 2", project_path=str(root))))
    assert out["ok"] is True, out
    assert target.read_text(encoding="utf-8") == "x = 2\n"

    led = wal / write_core._WRITE_BACKUP_LEDGER_NAME
    assert led.exists(), "write_fix did not create the redirected ledger"
    rows = [json.loads(x) for x in
            led.read_text(encoding="utf-8").splitlines() if x.strip()]
    assert any(Path(r.get("target", "")).name == "mod.py" for r in rows), rows
    assert all(str(r.get("bak", "")).endswith(".bak") for r in rows), rows

    assert _repo_ledger_stat() == before, (
        "the REAL repo ledger changed while the env redirect was live: "
        f"{before} -> {_repo_ledger_stat()}")


# -- (d) the conftest default is live in THIS pytest process --------------------

def test_conftest_default_is_live_for_this_pytest_process():
    """Pins conftest.py:158-161: pytest_configure filled the env with a
    per-run `orch_pytest_bak_ledger_` tmp dir BEFORE any test module import,
    and both helpers now resolve under it -- proof the default (not the
    hardcoded repo path) is what every other test in this run inherits."""
    env = os.environ.get(_ENV, "")
    assert env, "conftest did not fill ORCHESTRATOR_WRITE_BACKUP_LEDGER_DIR"
    assert Path(env).name.startswith("orch_pytest_bak_ledger_"), env

    led = write_core._write_backup_ledger_path()
    mk = write_core._write_backup_seed_marker_path()
    assert led.parent == Path(env) == mk.parent