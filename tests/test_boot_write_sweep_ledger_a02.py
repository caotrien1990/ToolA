# pyright: reportOptionalMemberAccess=false, reportArgumentType=false
"""A02: boot write sweep discovers backups from a write-time WAL ledger, not by
walking every indexed file.

Defect (measured): the production boot sweep (`_boot_sweep_scan(None)`) built its
candidate set from INDEX.json across all ~17 session roots -> ~40k candidates,
then probed a sibling `.bak` for each -> ~40k `exists()` calls, to recover a set
that is empty on every real boot. Cold disk cache pushed this past the hard 30s
recovery deadline (SERVER_STARTUP_DEGRADED). Complexity was O(every indexed file)
to serve an empty result.

Fix: `write_fix` journals each `.bak` into `.orchestrator_cache/write_backups.jsonl`
at write time. The production boot reads that WAL -> O(backups actually written).
A one-time, marker-gated seed folds pre-WAL legacy backups in so nothing is lost.

These tests pin BEHAVIOUR, not shape:
  * a real .bak that needs restoring is still restored via the ledger path;
  * ledger and legacy index paths recover the SAME set (semantic equivalence);
  * a corrupt/unreadable ledger fails LOUD (reported, never silently "clean");
  * the steady-state path does NOT walk the index.
"""
import asyncio
import json
import os
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import code_tools
from orchestrator.tools.write import core as write_core
from orchestrator.tools.write.core import _boot_write_sweep, _boot_sweep_scan


class _LedgerSandbox:
    """Redirect the module-level WAL + seed marker into an isolated temp dir so
    tests never touch the real .orchestrator_cache and never see each other."""

    def __init__(self, root: Path):
        self.led = root / "write_backups.jsonl"
        self.mk = root / ".write_backups.seeded"

    def __enter__(self):
        self._p1 = mock.patch.object(
            write_core, "_write_backup_ledger_path", lambda: self.led
        )
        self._p2 = mock.patch.object(
            write_core, "_write_backup_seed_marker_path", lambda: self.mk
        )
        self._p1.start()
        self._p2.start()
        return self

    def __exit__(self, *a):
        self._p1.stop()
        self._p2.stop()
        return False


def _make_restorable_bak(root: Path, name: str = "broken.py"):
    """A target whose on-disk copy is broken and whose .bak is the good, newer
    copy -> the sweep MUST restore it (this is the non-empty semantic case)."""
    target = root / name
    target.write_text("def ok():\n    return 1\n", encoding="utf-8")
    asyncio.run(code_tools.generate_project_map(str(root)))
    bak = root / f"{name}.bak"
    bak.write_text("def ok():\n    return 1\n", encoding="utf-8")
    target.write_text("def broken(:\n    pass\n", encoding="utf-8")
    now = code_tools.time.time()
    code_tools.os.utime(target, (now - 10, now - 10))
    code_tools.os.utime(bak, (now, now))
    return target, bak


class TestBootWriteSweepLedgerA02(unittest.TestCase):
    def setUp(self):
        self.tempdir = Path(tempfile.mkdtemp(prefix="a02_ledger_"))
        self.cache = self.tempdir / "cache"
        self.cache.mkdir()

    def tearDown(self):
        shutil.rmtree(self.tempdir, ignore_errors=True)

    # --- 1. WAL round-trip: a journalled real .bak is restored via the ledger ---
    def test_ledger_path_restores_journalled_backup(self):
        root = self.tempdir / "proj"
        root.mkdir()
        target, bak = _make_restorable_bak(root)
        with _LedgerSandbox(self.cache) as sb:
            # journal the backup exactly as write_fix does at write time
            write_core._record_write_backup(target, bak)
            # mark seed already done: we are exercising the STEADY-STATE path
            sb.mk.write_text("seeded", encoding="utf-8")
            result = asyncio.run(_boot_write_sweep(project_roots=None))

        self.assertEqual(result.get("discovery"), "ledger", msg=result)
        self.assertEqual(result.get("restored_count"), 1, msg=result)
        self.assertEqual(
            target.read_text(encoding="utf-8"), "def ok():\n    return 1\n"
        )

    # --- 2. Semantic equivalence: ledger set == legacy index set ---
    def test_ledger_and_index_paths_recover_same_pending_set(self):
        root = self.tempdir / "proj_eq"
        root.mkdir()
        target, bak = _make_restorable_bak(root, name="thing.py")

        # legacy index path: pass the root explicitly (bypasses the ledger)
        index_scan = _boot_sweep_scan([str(root)])
        index_pending = {str(fp) for fp, _b in index_scan.get("pending", [])}

        with _LedgerSandbox(self.cache) as sb:
            write_core._record_write_backup(target, bak)
            sb.mk.write_text("seeded", encoding="utf-8")
            ledger_scan = _boot_sweep_scan(None)
        ledger_pending = {str(fp) for fp, _b in ledger_scan.get("pending", [])}

        self.assertIn(str(target), index_pending, msg=index_scan)
        self.assertEqual(
            index_pending, ledger_pending,
            msg=f"index={index_pending} ledger={ledger_pending}",
        )

    # --- 3. One-time seed migration: a legacy .bak with NO ledger row is still
    #        recovered on first boot, then persisted into the WAL. ---
    def test_first_boot_seed_recovers_legacy_backup_and_persists(self):
        root = self.tempdir / "legacy"
        root.mkdir()
        target, bak = _make_restorable_bak(root, name="old.py")

        with _LedgerSandbox(self.cache) as sb:
            self.assertFalse(sb.mk.exists())  # first boot: no marker, no ledger
            # Route session-root discovery to our single legacy root so the seed
            # walk actually reaches it.
            with mock.patch.object(
                write_core, "_boot_sweep_project_roots",
                return_value=[root],
            ):
                result = asyncio.run(_boot_write_sweep(project_roots=None))

            self.assertTrue(result.get("seeded"), msg=result)
            self.assertEqual(result.get("restored_count"), 1, msg=result)
            self.assertEqual(
                target.read_text(encoding="utf-8"), "def ok():\n    return 1\n"
            )
            # After a COMPLETE seed the marker is dropped and the legacy backup is
            # now in the WAL -> next boot needs no index walk.
            self.assertTrue(sb.mk.exists(), msg="seed marker not written")
            rows = [
                json.loads(x)
                for x in sb.led.read_text(encoding="utf-8").splitlines()
                if x.strip()
            ]
            self.assertTrue(
                any(str(target) == r.get("target") for r in rows),
                msg=f"legacy backup not persisted into WAL: {rows}",
            )

    # --- 4. Fail loud: a corrupt ledger is REPORTED, never silently "clean" ---
    def test_corrupt_ledger_is_reported_not_swallowed(self):
        with _LedgerSandbox(self.cache) as sb:
            sb.mk.write_text("seeded", encoding="utf-8")  # steady state
            sb.led.write_text("{not valid json\n{also bad\n", encoding="utf-8")
            result = asyncio.run(_boot_write_sweep(project_roots=None))

        self.assertEqual(result.get("discovery"), "ledger", msg=result)
        self.assertGreaterEqual(result.get("root_error_count", 0), 1, msg=result)
        errors = result.get("root_errors") or []
        self.assertTrue(
            any("corrupt" in str(e.get("error", "")).lower() for e in errors),
            msg=f"corrupt ledger not reported: {result}",
        )

    # --- 5. Steady state does NOT walk the index (the whole point of A02) ---
    def test_steady_state_never_walks_index(self):
        with _LedgerSandbox(self.cache) as sb:
            sb.mk.write_text("seeded", encoding="utf-8")
            sb.led.write_text("", encoding="utf-8")  # empty WAL = the real boot case
            with mock.patch.object(
                write_core, "_boot_sweep_index_files",
                side_effect=AssertionError("index walk must not run in steady state"),
            ):
                result = asyncio.run(_boot_write_sweep(project_roots=None))
        self.assertEqual(result.get("discovery"), "ledger", msg=result)
        self.assertFalse(result.get("aborted"), msg=result)
        self.assertEqual(result.get("restored_count"), 0, msg=result)

    # --- 5b. An ABORTED seed must NOT be marked complete: next boot has to
    #         retry the index walk, else legacy backups are orphaned forever.
    #         (fail loud: never claim "seeded" on incomplete work) ---
    def test_aborted_seed_is_not_marked_complete(self):
        root = self.tempdir / "abort_seed"
        root.mkdir()
        target, bak = _make_restorable_bak(root, name="a.py")

        def _aborted_seed(stop_event, deadline_monotonic):
            # Emulate the deadline firing mid-walk: some candidates found, but
            # the pass did not complete.
            return {
                "candidates": {},
                "root_errors": [],
                "roots": [root],
                "aborted": True,
            }

        with _LedgerSandbox(self.cache) as sb:
            self.assertFalse(sb.mk.exists())
            with mock.patch.object(
                write_core, "_boot_sweep_seed_from_index", side_effect=_aborted_seed
            ):
                result = asyncio.run(_boot_write_sweep(project_roots=None))
            # honest reporting: not seeded, and the marker was NOT dropped
            self.assertFalse(result.get("seeded"), msg=result)
            self.assertFalse(
                sb.mk.exists(),
                msg="seed marker written despite an aborted seed -> next boot "
                    "would skip the index walk and orphan legacy backups",
            )

    # --- 6. write_fix actually journals the backup it creates ---
    def test_write_fix_appends_to_ledger(self):
        root = self.tempdir / "wf"
        root.mkdir()
        sample = root / "sample.py"
        sample.write_text("# a\n\ndef f():\n    return 1\n", encoding="utf-8")
        asyncio.run(code_tools.generate_project_map(str(root)))
        with _LedgerSandbox(self.cache) as sb:
            res = json.loads(asyncio.run(code_tools.write_fix(
                str(sample), 1, 1, "# changed\n", project_path=str(root),
            )))
            self.assertTrue(res.get("ok"), msg=res)
            self.assertTrue(sb.led.exists(), msg="ledger not created by write_fix")
            rows = [
                json.loads(x)
                for x in sb.led.read_text(encoding="utf-8").splitlines()
                if x.strip()
            ]
            self.assertTrue(
                any(str(sample) == r.get("target") for r in rows),
                msg=f"write_fix did not journal its backup: {rows}",
            )

    # --- 7. ClauLTN verify (Y1): the seed must persist EVERY surviving .bak,
    #        not just the ones pending right now. ---
    def test_seed_persists_stale_baks_not_only_pending_ones(self):
        """A .bak that is currently OLDER than its target is not pending today,
        but the legacy index scan re-evaluated mtime on every boot, so a later
        edit to the target could make that .bak the newest copy. The WAL replaces
        that re-evaluation, so it must carry the same set.

        Journalling `pending` instead of `existing_baks` in the seed loop is a
        one-identifier change that keeps every assertion above green: the stale
        .bak is silently dropped from the WAL, the marker is then written, and
        the index is never walked again -- so it is orphaned permanently. This is
        the exact hazard core.py's own comment at the seed loop warns about.
        """
        root = self.tempdir / "stale_seed"
        root.mkdir()
        # `hot` is pending (bak newer). `cold` is NOT pending (bak older) but its
        # .bak exists and must still be journalled.
        hot, hot_bak = _make_restorable_bak(root, name="hot.py")
        cold = root / "cold.py"
        cold.write_text("def cold():\n    return 2\n", encoding="utf-8")
        cold_bak = root / "cold.py.bak"
        cold_bak.write_text("def cold():\n    return 0\n", encoding="utf-8")
        asyncio.run(code_tools.generate_project_map(str(root)))
        now = code_tools.time.time()
        code_tools.os.utime(cold_bak, (now - 100, now - 100))  # bak is OLDER
        code_tools.os.utime(cold, (now, now))

        with _LedgerSandbox(self.cache) as sb:
            with mock.patch.object(
                write_core, "_boot_sweep_project_roots", return_value=[root]
            ):
                result = asyncio.run(_boot_write_sweep(project_roots=None))
            self.assertTrue(result.get("seeded"), msg=result)
            self.assertTrue(sb.mk.exists(), msg="seed marker not written")
            rows = [
                json.loads(x)
                for x in sb.led.read_text(encoding="utf-8").splitlines()
                if x.strip()
            ]
            targets = {r.get("target") for r in rows}

        self.assertIn(str(hot), targets, msg=f"pending backup not journalled: {rows}")
        self.assertIn(
            str(cold),
            targets,
            msg="a surviving-but-not-currently-pending .bak was dropped from the "
                "WAL during the one-time seed. The marker is now set, so the index "
                "will never be walked again and this backup is orphaned forever. "
                f"journalled={targets}",
        )

    # --- 8. ClauLTN verify (Y3): the real (unpatched) ledger + marker paths must
    #        both resolve INSIDE the same .orchestrator_cache. ---
    def test_real_ledger_and_marker_paths_share_the_cache_dir(self):
        """Every other test in this file monkeypatches both path helpers, so the
        real ones are never exercised. Moving the ledger out of the cache
        directory (parents[3] -> parents[2]) keeps all of them green while
        breaking the A02/A30 shared-fate invariant: the ledger would survive a
        cache wipe that still destroys the marker.

        Deliberately asserted as a relationship between the two helpers and the
        repo's own cache-dir name -- not as a hardcoded absolute path.

        The env var is removed explicitly: conftest's pytest_configure fills
        ORCHESTRATOR_WRITE_BACKUP_LEDGER_DIR with a tmp dir (the B031-style
        fence that keeps test .bak rows out of the real repo ledger), so with
        the default live the helpers resolve THERE. This test pins the UNSET
        default, so it must delenv -- never inherit.
        """
        import surgical_mapper as sm

        with mock.patch.dict(os.environ):
            os.environ.pop(write_core._WRITE_BACKUP_LEDGER_DIR_ENV, None)
            led = write_core._write_backup_ledger_path()
            mk = write_core._write_backup_seed_marker_path()

        self.assertEqual(
            led.parent,
            mk.parent,
            msg=f"ledger and seed marker are in different directories: "
                f"{led.parent} vs {mk.parent} -- they can no longer share one "
                "fate across a cache wipe (see test_a02_a30_interaction.py)",
        )
        self.assertEqual(
            led.parent.name,
            sm.CACHE_DIR_NAME,
            msg=f"WAL directory {led.parent} is not the orchestrator cache dir "
                f"({sm.CACHE_DIR_NAME}); a full refresh would not manage it",
        )
        self.assertEqual(led.name, write_core._WRITE_BACKUP_LEDGER_NAME)
        self.assertEqual(mk.name, write_core._WRITE_BACKUP_SEED_MARKER)
        # The cache dir must sit at the repo root, i.e. beside this package's
        # top-level module -- pinned structurally, not by absolute path.
        self.assertTrue(
            (led.parent.parent / "surgical_mapper.py").exists(),
            msg=f"WAL cache dir {led.parent} is not at the repo root",
        )

    # --- 9. ClauLTN verify (Y4): the journal is write-AHEAD, not write-behind. ---
    def test_backup_is_journalled_before_it_is_written(self):
        """`_record_write_backup`'s docstring claims the row is 'written as an
        *intent* (before the .bak is physically created) so a crash mid-write
        still leaves a recoverable pointer'. Swapping the two lines in
        `write_fix._write` keeps every steady-state test green -- the row still
        lands, just afterwards -- and silently deletes that guarantee.

        Simulated by making the .bak write itself blow up: with write-ahead
        ordering the ledger row already exists, so a later boot can still find
        and reason about the target. With write-behind, nothing was recorded.
        """
        root = self.tempdir / "wal_order"
        root.mkdir()
        sample = root / "order.py"
        sample.write_text("# a\n\ndef f():\n    return 1\n", encoding="utf-8")
        asyncio.run(code_tools.generate_project_map(str(root)))

        real_write = code_tools._atomic_write_text

        def _boom(path, text, **kw):
            if str(path).endswith(".bak"):
                raise OSError("simulated crash while creating the .bak")
            return real_write(path, text, **kw)

        with _LedgerSandbox(self.cache) as sb:
            with mock.patch.object(code_tools, "_atomic_write_text", side_effect=_boom):
                try:
                    asyncio.run(code_tools.write_fix(
                        str(sample), 1, 1, "# changed\n", project_path=str(root),
                    ))
                except OSError:
                    pass  # the crash itself is the point; ordering is what we pin
            rows = []
            if sb.led.exists():
                rows = [
                    json.loads(x)
                    for x in sb.led.read_text(encoding="utf-8").splitlines()
                    if x.strip()
                ]

        self.assertTrue(
            any(str(sample) == r.get("target") for r in rows),
            msg="the .bak was not journalled before the write that crashed -> the "
                "WAL is write-behind, so a crash between journal and copy leaves "
                "no recoverable pointer, contradicting _record_write_backup's "
                f"documented intent ordering. rows={rows}",
        )


class LedgerRowEndsAtItsNewlineOnlyTest(unittest.TestCase):
    """Both boot-sweep ledgers are written one json.dumps(ensure_ascii=False) row
    per "\\n", so a path holding U+2028 / U+0085 -- legal in a file name on NTFS
    and POSIX -- is written raw. str.splitlines() also breaks at both: the row
    read as corrupt fragments, the .bak it points at was never offered for
    restore, and the cross-root write was never re-validated."""

    NAME = "x" + chr(0x2028) + "y" + chr(0x85) + "z.py"

    def setUp(self):
        self.root = Path(tempfile.mkdtemp(prefix="a02_nl_"))
        self.addCleanup(shutil.rmtree, self.root, ignore_errors=True)
        self.target = self.root / self.NAME

    def test_a_backup_row_for_an_odd_name_is_a_candidate(self):
        with _LedgerSandbox(self.root):
            write_core._record_write_backup(self.target, Path(str(self.target) + ".bak"))
            candidates, errors = write_core._boot_sweep_ledger_candidates()
        self.assertEqual(errors, [])
        self.assertEqual(list(candidates.values()),
                         [write_core._boot_sweep_abs_path(self.target)])

    def test_a_cross_root_row_for_an_odd_name_is_revalidated(self):
        from orchestrator.fileops import xroot
        fake_core = self.root / "orchestrator" / "tools" / "write" / "core.py"
        with mock.patch.object(xroot, "PROJECT_ROOT", self.root), \
                mock.patch.object(write_core, "__file__", str(fake_core)):
            xroot.ledger_append(phase="result", path=self.target,
                                caller_tool="write_fix", ok=True)
            files = write_core._boot_sweep_xroot_files()
        self.assertEqual(files, [write_core._boot_sweep_abs_path(self.target)])


class TestBootSeedLedgerDedupeA02(unittest.TestCase):
    """WHY: the one-time seed used to re-append EVERY existing .bak into the
    WAL even when its target was already journaled. The ledger reader
    (_boot_sweep_ledger_candidates) collapses rows by _boot_sweep_ledger_key,
    so a second row for the same target restores nothing -- it is pure growth
    (measured: after a ledger wipe one boot seed took the ledger from 224 to
    760 rows, 163 duplicate pairs). These tests pin the dedupe: after the seed
    the ledger holds exactly ONE row per normcase target."""

    def setUp(self):
        self.tempdir = Path(tempfile.mkdtemp(prefix="a02_dedupe_"))
        self.cache = self.tempdir / "cache"
        self.cache.mkdir()

    def tearDown(self):
        shutil.rmtree(self.tempdir, ignore_errors=True)

    @staticmethod
    def _row_key_counts(led: Path) -> dict:
        """Rows per _boot_sweep_ledger_key, read back from the ledger file."""
        counts: dict = {}
        for line in led.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            row = json.loads(line)
            key = write_core._boot_sweep_ledger_key(row.get("target") or "")
            counts[key] = counts.get(key, 0) + 1
        return counts

    def test_seed_does_not_reappend_targets_already_in_ledger(self):
        root = self.tempdir / "proj"
        root.mkdir()
        t_target, t_bak = _make_restorable_bak(root, name="t.py")
        u_target, u_bak = _make_restorable_bak(root, name="u.py")

        with _LedgerSandbox(self.cache) as sb:
            # T is ALREADY journaled (as write_fix would have done at write
            # time); U is a legacy .bak on disk with NO ledger row.
            write_core._record_write_backup(t_target, t_bak)
            self.assertFalse(sb.mk.exists())  # first boot: the seed must run
            with mock.patch.object(
                write_core, "_boot_sweep_project_roots", return_value=[root],
            ):
                result = asyncio.run(_boot_write_sweep(project_roots=None))

            self.assertTrue(result.get("seeded"), msg=result)
            self.assertTrue(sb.mk.exists(), msg="seed marker not written")
            counts = self._row_key_counts(sb.led)

        t_key = write_core._boot_sweep_ledger_key(t_target)
        u_key = write_core._boot_sweep_ledger_key(u_target)
        self.assertEqual(
            1, counts.get(t_key),
            msg=f"already-journaled target re-appended by the seed: {counts}",
        )
        self.assertEqual(
            1, counts.get(u_key),
            msg=f"legacy target not journaled exactly once: {counts}",
        )
        self.assertEqual(2, sum(counts.values()), msg=f"rows={counts}")

    @unittest.skipUnless(os.name == "nt", "normcase folds case and '/' only on Windows")
    def test_ledger_key_is_case_and_separator_insensitive(self):
        """WHY: the dedupe above is only sound if reader and seed derive the
        SAME key from different spellings of one path. On Windows a ledger row
        may store the target with different case or forward slashes than the
        index walk yields; _boot_sweep_ledger_key (normcase + abspath) must
        collapse all spellings, else the skip never fires and the ledger grows
        again. Windows semantics -- the production dedupe relies on the same
        normcase behaviour."""
        root = self.tempdir / "keys"
        root.mkdir()
        p = root / "MixedCase.py"
        p.write_text("x = 1\n", encoding="utf-8")
        variants = [
            str(p),
            str(p).upper(),
            str(p).lower(),
            str(p).replace("\\", "/"),
            str(p).upper().replace("\\", "/"),
        ]
        keys = {write_core._boot_sweep_ledger_key(v) for v in variants}
        self.assertEqual(1, len(keys), msg=f"keys diverged: {keys}")
        self.assertEqual(write_core._boot_sweep_ledger_key(p), keys.pop())


if __name__ == "__main__":
    unittest.main()
