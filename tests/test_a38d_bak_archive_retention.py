"""A38-D: the .bak archive must age out on its own, and never outside its bin.

WHY this test exists: nothing ever collected the sibling .bak that every
write_fix drops, so 88 files / 4.35 MB had piled up across the worktree (one of
them a 924 KB code_tools.py.bak). The fix is two-stage on purpose -- CASE-2008:
no single sweep may both CHOOSE and DESTROY -- and each stage has a way to go
quietly wrong that only a test catches:

  - PROMOTE reads its threshold from _WRITE_LEDGER_TTL_DAYS, so the age that
    makes a backup collectable is DERIVED from the moment its ledger row -- and
    with it every write_status / auto-revert path that could still reach that
    backup -- has expired. A literal 7 here would keep passing after the real
    threshold moved.
  - EXPIRE deletes. Three guards are pinned below: dry_run defaults to True so a
    caller who forgets the flag gets a preview, the resolved path must sit under
    _recycle_bin, and the suffix must be whitelisted.
  - The 14-day clock starts at ARCHIVAL. shutil.move preserves mtime, so without
    the stamp stage 1 writes, a .bak already older than the expire TTL would be
    promoted and deleted in the SAME pass -- the collapse the two stages exist to
    prevent. test_the_clock_starts_at_archival is that pin.

Every age is computed FROM the live module constants and fed through the
injected `now`, never from a literal day count and never by sleeping: an exported
ORCHESTRATOR_BAK_ARCHIVE_TTL_DAYS must not be able to turn a correct sweep into
a failing test.
"""
from __future__ import annotations

import asyncio
import os
import subprocess

import pytest

import orchestrator_server as srv

DAY = 86400.0
NOW = 1700000000.0


def _touch(path, age_days, now=NOW):
    """Create a file and backdate its mtime by age_days relative to `now`."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("x", encoding="utf-8")
    stamp = now - (age_days * DAY)
    os.utime(path, (stamp, stamp))
    return path


def _promote_ttl():
    return srv._WRITE_LEDGER_TTL_DAYS


def _expire_ttl():
    return srv._BAK_ARCHIVE_TTL_DAYS


@pytest.fixture()
def root(tmp_path):
    return tmp_path


def test_the_archive_ttl_is_14_days_and_clamped():
    """The window asked for, read through the env contract instead of a literal."""
    raw = os.environ.get("ORCHESTRATOR_BAK_ARCHIVE_TTL_DAYS", "14")
    assert _expire_ttl() == max(1, min(365, int(raw)))
    assert 1 <= _expire_ttl() <= 365


def test_promote_selects_only_backups_past_the_ledger_ttl(root):
    old = _touch(root / "pkg" / "mod.py.bak", _promote_ttl() + 2)
    fresh = _touch(root / "pkg" / "other.py.bak", 0)
    out = srv._bak_archive_sweep(str(root), now=NOW)
    assert out["ok"] is True
    assert out["promote_after_days"] == _promote_ttl()
    assert out["promoted"] == [os.path.join("pkg", "mod.py.bak")]
    assert out["promoted_count"] == 1
    assert old.exists() and fresh.exists(), "a preview must move nothing"


def test_dry_run_is_the_default(root):
    src = _touch(root / "a.py.bak", _promote_ttl() + 5)
    out = srv._bak_archive_sweep(str(root))
    assert out["dry_run"] is True
    assert src.exists()


def test_promote_preserves_the_relative_path(root):
    src = _touch(root / "deep" / "nest" / "x.py.bak", _promote_ttl() + 1)
    out = srv._bak_archive_sweep(str(root), dry_run=False, now=NOW)
    dst = root / "_recycle_bin" / "bak_archive" / "deep" / "nest" / "x.py.bak"
    assert out["promoted_count"] == 1
    assert dst.is_file()
    assert not src.exists()


def test_the_clock_starts_at_archival(root):
    """An ancient .bak survives its first sweep: it is archived, not destroyed."""
    _touch(root / "ancient.py.bak", _expire_ttl() + _promote_ttl() + 99)
    out = srv._bak_archive_sweep(str(root), dry_run=False, now=NOW)
    dst = root / "_recycle_bin" / "bak_archive" / "ancient.py.bak"
    assert out["promoted_count"] == 1
    assert dst.is_file(), "promoted and expired in one pass -- stages collapsed"
    assert out["expired"] == []
    assert dst.stat().st_mtime == pytest.approx(NOW, abs=2)


def test_a_collision_rename_keeps_the_bak_suffix(root):
    _touch(root / "m.py.bak", _promote_ttl() + 1)
    _touch(root / "_recycle_bin" / "bak_archive" / "m.py.bak", 1)
    srv._bak_archive_sweep(str(root), dry_run=False, now=NOW)
    names = sorted(p.name for p in (root / "_recycle_bin" / "bak_archive").iterdir())
    assert len(names) == 2, names
    assert all(n.endswith(".bak") for n in names), names


def test_the_bin_is_never_re_promoted(root):
    """_recycle_bin is skipped on the walk, or stage 1 would eat its own output."""
    _touch(root / "_recycle_bin" / "bak_archive" / "old.py.bak", _promote_ttl() + 9)
    out = srv._bak_archive_sweep(str(root), now=NOW)
    assert out["promoted"] == []


def test_venv_and_git_are_skipped(root):
    _touch(root / "venv312" / "lib" / "v.py.bak", _promote_ttl() + 9)
    _touch(root / ".git" / "g.py.bak", _promote_ttl() + 9)
    out = srv._bak_archive_sweep(str(root), now=NOW)
    assert out["promoted"] == []


def test_expire_only_past_the_archive_ttl(root):
    archive = root / "_recycle_bin" / "bak_archive"
    doomed = _touch(archive / "gone.py.bak", _expire_ttl() + 1)
    kept = _touch(archive / "stay.py.bak", _expire_ttl() - 1)
    zipped = _touch(root / "_recycle_bin" / "bak_archive_20260920.zip", _expire_ttl() + 1)
    preview = srv._bak_archive_sweep(str(root), now=NOW)
    assert sorted(preview["expired"]) == sorted(
        [str(doomed.resolve()), str(zipped.resolve())]
    )
    assert doomed.exists() and zipped.exists(), "a preview must delete nothing"
    out = srv._bak_archive_sweep(str(root), dry_run=False, now=NOW)
    assert out["expired_count"] == 2
    assert not doomed.exists() and not zipped.exists()
    assert kept.exists()
    assert out["errors"] == []


def test_expire_ignores_a_non_whitelisted_suffix(root):
    src = _touch(root / "_recycle_bin" / "notes.py", _expire_ttl() + 40)
    out = srv._bak_archive_sweep(str(root), dry_run=False, now=NOW)
    assert out["expired"] == []
    assert src.exists()


def test_a_junction_cannot_steer_the_sweep_out_of_the_bin(root, tmp_path_factory):
    """Whitelist by resolved ROOT: nothing outside _recycle_bin may be deleted."""
    outside = tmp_path_factory.mktemp("outside")
    victim = _touch(outside / "precious.py.bak", _expire_ttl() + 50)
    link = root / "_recycle_bin" / "escape"
    link.parent.mkdir(parents=True, exist_ok=True)
    proc = subprocess.run(
        ["cmd", "/c", "mklink", "/J", str(link), str(outside)],
        capture_output=True,
        text=True,
    )
    if proc.returncode != 0 or not link.exists():
        pytest.skip("no directory junction here: " + (proc.stderr or "").strip())
    out = srv._bak_archive_sweep(str(root), dry_run=False, now=NOW)
    assert victim.exists(), "a path outside _recycle_bin must never be deleted"
    assert str(victim.resolve()) not in out["expired"]


def test_retention_is_opt_out_by_env():
    """The boot wrapper is switchable off without editing code."""
    var = "ORCHESTRATOR_BAK_RETENTION_ON_BOOT"
    prior = os.environ.get(var)
    os.environ[var] = "0"
    try:
        out = asyncio.run(srv._run_bak_archive_retention())
    finally:
        if prior is None:
            os.environ.pop(var, None)
        else:
            os.environ[var] = prior
    assert out == {"skipped": "disabled"}
