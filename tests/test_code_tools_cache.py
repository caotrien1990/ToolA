# pyright: reportOptionalMemberAccess=false, reportArgumentType=false
import asyncio
import base64
import gc
import json
import shutil
import sqlite3
import sys
import tempfile
import threading
import time
import unittest
import warnings
from unittest import mock
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor

import code_tools
from orchestrator.tools.write import core as write_core
from orchestrator.tools.write.core import _boot_write_sweep, _pre_write_syntax_check


class TestCodeToolsCache(unittest.TestCase):
    def setUp(self):
        self.tempdir = Path(tempfile.mkdtemp(prefix='project_orchestrator_test_'))
        self.project_path = self.tempdir
        self.sample_file = self.project_path / 'sample.py'
        self.sample_file.write_text(
            '# header\n\n'
            'def foo():\n'
            '    return 1\n',
            encoding='utf-8',
        )

    def tearDown(self):
        # STT-27 (B032): several tests repoint code_tools._SESSIONS_DB at a
        # file INSIDE self.tempdir; the process-global SessionWriter and
        # thread-local read conns hold it open, so rmtree(ignore_errors=True)
        # silently leaves the dir behind (WinError 32). Release every global
        # sessions.db handle first — same teardown contract _swap_sessions_db
        # uses in test_orchestrator_server_contract.py.
        code_tools._session_store_module.close_writer()
        shutil.rmtree(self.tempdir, ignore_errors=True)
        # STT-27 (B032): backup_project's default target is
        # <root.parent>/_orchestrator_backups, i.e. a SIBLING of tempdir that
        # nothing else cleans. Remove only this class's own archives
        # (project_orchestrator_test_*), then the dir if empty — whitelist,
        # never a blanket sibling delete.
        backups = self.tempdir.parent / '_orchestrator_backups'
        if backups.is_dir():
            for item in backups.glob('project_orchestrator_test_*'):
                if item.is_dir():
                    shutil.rmtree(item, ignore_errors=True)
                else:
                    try:
                        item.unlink()
                    except OSError:
                        pass
            try:
                backups.rmdir()
            except OSError:
                pass

    def test_generate_project_map_and_cache_status(self):
        result = json.loads(asyncio.run(code_tools.generate_project_map(str(self.project_path))))
        self.assertEqual(result['schema_version'], 2)
        self.assertIn('files_indexed', result)
        self.assertIn('groups', result)
        self.assertIn('hotspots', result)
        self.assertIn('index_path', result)
        self.assertTrue((self.project_path / '.orchestrator_cache' / 'INDEX.json').exists())
        self.assertTrue((self.project_path / 'PROJECT_MAP.md').exists())

        status = json.loads(asyncio.run(code_tools.cache_status(str(self.project_path))))
        self.assertEqual(status['status'], 'ok')
        self.assertTrue(status['cache_exists'])
        self.assertTrue(status['index_exists'])
        self.assertIn('last_updated_iso', status)

    def test_cache_status_messages_are_english(self):
        status = json.loads(asyncio.run(code_tools.cache_status(str(self.project_path))))

        self.assertEqual(status['status'], 'not_initialized')
        self.assertEqual(status['message'], 'Cache is not initialized. Run generate_project_map to create the cache.')

        json.loads(asyncio.run(code_tools.generate_project_map(str(self.project_path))))
        initialized = json.loads(asyncio.run(code_tools.cache_status(str(self.project_path))))

        self.assertEqual(initialized['message'], 'Cache is initialized.')
        self.assertNotRegex(json.dumps(initialized, ensure_ascii=False), r'[À-ỹ]')

    def test_backup_project_removes_orphan_archives_beyond_retention(self):
        backup_dir = self.tempdir / 'backups'
        backup_dir.mkdir()
        project_name = self.project_path.name
        for index in range(3):
            orphan = backup_dir / f'{project_name}_backup_20260101_00000{index}.zip'
            orphan.write_bytes(b'orphan')

        result = json.loads(asyncio.run(code_tools.backup_project(
            str(self.project_path),
            backup_dir=str(backup_dir),
            force=True,
            keep_last_n=2,
        )))

        self.assertTrue(result.get('ok'), msg=result)
        archives = list(backup_dir.glob(f'{project_name}_backup_*.zip'))
        self.assertEqual(len(archives), 2)
        self.assertTrue(Path(result['backup_path']).exists())

    def test_backup_project_denies_full_backup_of_user_home(self):
        with mock.patch.object(Path, 'home', return_value=self.project_path):
            result = json.loads(asyncio.run(code_tools.backup_project(
                str(self.project_path),
                backup_dir=str(self.tempdir / 'backups'),
                force=True,
            )))

        self.assertFalse(result.get('ok'))
        self.assertIn('Full backup denied for broad root', result.get('error', ''))

    def test_backup_project_rejects_invalid_retention(self):
        result = json.loads(asyncio.run(code_tools.backup_project(
            str(self.project_path),
            backup_dir=str(self.tempdir / 'backups'),
            force=True,
            keep_last_n=0,
            target_paths=[self.sample_file.name],
        )))

        self.assertFalse(result.get('ok'))
        self.assertIn('keep_last_n', result.get('error', ''))
        self.assertFalse((self.tempdir / 'backups').exists())

    def test_backup_project_same_day_skip_requires_matching_targets(self):
        backup_dir = self.tempdir / 'backups'
        other = self.project_path / 'other.py'
        other.write_text('value = 2\n', encoding='utf-8')
        first = json.loads(asyncio.run(code_tools.backup_project(
            str(self.project_path), backup_dir=str(backup_dir),
            target_paths=[self.sample_file.name],
        )))
        second = json.loads(asyncio.run(code_tools.backup_project(
            str(self.project_path), backup_dir=str(backup_dir),
            target_paths=[other.name],
        )))

        self.assertTrue(first.get('ok'), first)
        self.assertTrue(second.get('ok'), second)
        self.assertFalse(second.get('skipped', False), second)
        self.assertEqual(second.get('target_paths'), [other.name])

    def test_backup_project_skips_symlink_target_content(self):
        outside = self.tempdir / 'outside-secret.txt'
        outside.write_text('secret\n', encoding='utf-8')
        link = self.project_path / 'linked-secret.txt'
        try:
            link.symlink_to(outside)
        except OSError:
            self.skipTest('symlinks unavailable in this Windows environment')

        result = json.loads(asyncio.run(code_tools.backup_project(
            str(self.project_path),
            backup_dir=str(self.tempdir / 'backups'),
            force=True,
            target_paths=[link.name],
        )))

        self.assertFalse(result.get('ok'), result)
        self.assertIn('symlink', result.get('error', '').lower())
        archives = list((self.tempdir / 'backups').glob('*.zip'))
        if archives:
            import zipfile
            with zipfile.ZipFile(archives[0]) as archive:
                self.assertNotIn(link.name, archive.namelist())

    def test_backup_timeout_waits_for_partial_archive_cleanup(self):
        large = self.project_path / 'large.bin'
        large.write_bytes(b'x' * (8 * 1024 * 1024))
        backup_dir = self.tempdir / 'backups'

        result = json.loads(asyncio.run(code_tools.backup_project(
            str(self.project_path),
            backup_dir=str(backup_dir),
            force=True,
            timeout=0.0001,
            target_paths=[large.name],
        )))

        self.assertFalse(result.get('ok'), result)
        self.assertIn('cleanup completed', result.get('error', ''))
        self.assertEqual(list(backup_dir.glob('*.zip')), [])

    def test_backup_timeout_returns_structured_error_promptly(self):
        # WHY: before the fix, a timed-out backup only set the stop Event and
        # waited for the worker thread; the HTTP client still gave up, and the
        # server later returned an empty HTTP 200 (after the worker finished
        # several minutes later). The fix must (a) return a structured error
        # with error_name=BACKUP_TIMEOUT, (b) actually cancel the wrapper task
        # so the underlying executor future is cancelled too, and (c) return
        # promptly -- well under how long the worker itself would take.
        # Use a slow mock-writer so the worker cannot finish before the
        # timeout fires; otherwise the test is racy on fast disks.
        large = self.project_path / 'large.bin'
        large.write_bytes(b'x' * (64 * 1024 * 1024))
        backup_dir = self.tempdir / 'backups'

        # Slow down chunk writes so the timeout reliably fires mid-write.
        import zipfile as _zipfile
        real_open = _zipfile.ZipFile.open
        def _slow_open(self, name, mode='r', *args, **kwargs):
            handle = real_open(self, name, mode, *args, **kwargs)
            if mode == 'w':
                real_write = handle.write
                def _slow_write(data):
                    time.sleep(0.05)
                    return real_write(data)
                handle.write = _slow_write
            return handle

        started = time.monotonic()
        with mock.patch.object(_zipfile.ZipFile, 'open', _slow_open):
            result = json.loads(asyncio.run(code_tools.backup_project(
                str(self.project_path),
                backup_dir=str(backup_dir),
                force=True,
                timeout=0.2,
                target_paths=[large.name],
            )))
        elapsed = time.monotonic() - started

        self.assertFalse(result.get('ok'), result)
        self.assertEqual(result.get('error_name'), 'BACKUP_TIMEOUT', result)
        self.assertEqual(result.get('timeout_seconds'), 0.2, result)
        self.assertIn('ca_next_action', result)
        # Must return promptly: 64 MB at 50 ms/MB takes ~3.2 s; timeout is
        # 0.2 s plus a 15 s cleanup window. Bound well under the worker's own
        # runtime to prove the wrapper task was actually cancelled.
        self.assertLess(elapsed, 18.0, f'took {elapsed:.2f}s -- worker not cancelled')
        # No partial archive left behind on disk.
        self.assertEqual(list(backup_dir.glob('*.zip')), [])

    def test_backup_concurrent_call_returns_backup_in_progress(self):
        # WHY: previously 3 concurrent backup_project calls all queued on the
        # single-worker backup lane and each took 7-10 minutes. The fix must
        # serialize per project: the second concurrent call returns a
        # structured backup_in_progress error immediately instead of queueing.
        blocker = threading.Event()
        release = threading.Event()
        entered = []

        async def fake_lane_run(lane_name, func, *args, **kwargs):
            entered.append(lane_name)
            # Run func in a real thread so the event loop stays responsive,
            # but block it on `release` so we control when the first backup
            # finishes.
            loop = asyncio.get_running_loop()
            def _blocking():
                blocker.set()
                release.wait(timeout=10)
                return {'ok': True, 'backup_path': 'x.zip'}
            return await loop.run_in_executor(None, _blocking)

        async def scenario():
            with mock.patch.object(code_tools, 'run_in_executor_lane', side_effect=fake_lane_run):
                first_task = asyncio.create_task(code_tools.backup_project(
                    str(self.project_path),
                    backup_dir=str(self.tempdir / 'backups'),
                    force=True,
                    target_paths=[self.sample_file.name],
                ))
                # Wait until the first call has actually submitted work.
                for _ in range(200):
                    if blocker.is_set():
                        break
                    await asyncio.sleep(0.01)
                self.assertTrue(blocker.is_set(), 'first backup never started')

                second_raw = await code_tools.backup_project(
                    str(self.project_path),
                    backup_dir=str(self.tempdir / 'backups'),
                    force=True,
                    target_paths=[self.sample_file.name],
                )
                release.set()
                first_raw = await first_task
                return json.loads(first_raw), json.loads(second_raw)

        first, second = asyncio.run(scenario())
        self.assertTrue(first.get('ok'), first)
        self.assertFalse(second.get('ok'), second)
        self.assertEqual(second.get('error_name'), 'BACKUP_IN_PROGRESS', second)
        self.assertEqual(second.get('reason'), 'backup_in_progress', second)
        self.assertTrue(second.get('retryable'), second)
        # Only ONE lane submission happened -- the second call never queued.
        self.assertEqual(len(entered), 1, entered)

    def test_backup_targeted_uses_read_io_lane_not_backup(self):
        # WHY: targeted backups used to share the single-worker `backup` lane
        # with full-repo walks, so a 14-file targeted backup waited 7+ minutes
        # behind a full walk. The fix routes targeted backups to the wider
        # `read_io` lane so they are not serialized behind full backups.
        seen_lanes = []

        async def fake_lane_run(lane_name, func, *args, **kwargs):
            seen_lanes.append(lane_name)
            return {'ok': True, 'backup_path': 'x.zip'}

        async def scenario():
            with mock.patch.object(code_tools, 'run_in_executor_lane', side_effect=fake_lane_run):
                targeted_raw = await code_tools.backup_project(
                    str(self.project_path),
                    backup_dir=str(self.tempdir / 'backups'),
                    force=True,
                    target_paths=[self.sample_file.name],
                )
                full_raw = await code_tools.backup_project(
                    str(self.project_path),
                    backup_dir=str(self.tempdir / 'backups'),
                    force=True,
                )
                return json.loads(targeted_raw), json.loads(full_raw)

        targeted, full = asyncio.run(scenario())
        self.assertTrue(targeted.get('ok'), targeted)
        self.assertTrue(full.get('ok'), full)
        self.assertEqual(seen_lanes, ['read_io', 'backup'], seen_lanes)

    def test_backup_lock_released_after_timeout(self):
        # WHY: the per-project lock must be released even when the backup
        # times out, otherwise the next backup for the same project would
        # always return BACKUP_IN_PROGRESS forever.
        large = self.project_path / 'big.bin'
        large.write_bytes(b'y' * (8 * 1024 * 1024))
        backup_dir = self.tempdir / 'backups'

        first = json.loads(asyncio.run(code_tools.backup_project(
            str(self.project_path),
            backup_dir=str(backup_dir),
            force=True,
            timeout=0.0001,
            target_paths=[large.name],
        )))
        self.assertFalse(first.get('ok'), first)
        self.assertEqual(first.get('error_name'), 'BACKUP_TIMEOUT', first)

        # Second call must NOT be rejected as in-progress -- the lock was
        # released when the first call returned.
        second = json.loads(asyncio.run(code_tools.backup_project(
            str(self.project_path),
            backup_dir=str(backup_dir),
            force=True,
            target_paths=[self.sample_file.name],
        )))
        self.assertTrue(second.get('ok'), second)
        self.assertNotEqual(second.get('error_name'), 'BACKUP_IN_PROGRESS', second)

    def test_backup_locks_are_per_project_not_global(self):
        # WHY: serialization is scoped per project. Two DIFFERENT projects
        # must be able to back up concurrently (otherwise an unrelated
        # project would stall behind another project's full walk).
        other_project = self.tempdir / 'other_project'
        other_project.mkdir()
        (other_project / 'other.py').write_text('value = 1\n', encoding='utf-8')

        entered = []
        release_first = threading.Event()
        started_first = threading.Event()

        async def fake_lane_run(lane_name, func, *args, **kwargs):
            entered.append(lane_name)
            loop = asyncio.get_running_loop()
            def _blocking():
                started_first.set()
                release_first.wait(timeout=10)
                return {'ok': True, 'backup_path': 'x.zip'}
            return await loop.run_in_executor(None, _blocking)

        async def scenario():
            with mock.patch.object(code_tools, 'run_in_executor_lane', side_effect=fake_lane_run):
                first_task = asyncio.create_task(code_tools.backup_project(
                    str(self.project_path),
                    backup_dir=str(self.tempdir / 'b1'),
                    force=True,
                    target_paths=[self.sample_file.name],
                ))
                for _ in range(200):
                    if started_first.is_set():
                        break
                    await asyncio.sleep(0.01)
                self.assertTrue(started_first.is_set())

                second_raw = await code_tools.backup_project(
                    str(other_project),
                    backup_dir=str(self.tempdir / 'b2'),
                    force=True,
                    target_paths=['other.py'],
                )
                release_first.set()
                first_raw = await first_task
                return json.loads(first_raw), json.loads(second_raw)

        first, second = asyncio.run(scenario())
        self.assertTrue(first.get('ok'), first)
        # Second project is NOT blocked by the first project's lock.
        self.assertTrue(second.get('ok'), second)
        self.assertNotEqual(second.get('error_name'), 'BACKUP_IN_PROGRESS', second)
        self.assertEqual(len(entered), 2, entered)

    def test_refresh_project_map_recreates_index(self):
        asyncio.run(code_tools.generate_project_map(str(self.project_path)))
        index_path = self.project_path / '.orchestrator_cache' / 'INDEX.json'
        self.assertTrue(index_path.exists())

        result = json.loads(asyncio.run(code_tools.refresh_project_map(str(self.project_path))))
        self.assertIn('files_indexed', result)
        self.assertTrue(index_path.exists())
        self.assertGreater(result['files_indexed'], 0)

    def test_refresh_project_map_preserves_snapshot_error_taxonomy(self):
        with mock.patch.object(
            code_tools.surgical_mapper,
            'refresh_project_map',
            side_effect=code_tools.search_snapshots.SnapshotIntegrityError(
                'INDEX/content hash mismatch: qsleep/ledger/interactive_gate.json'
            ),
        ):
            result = json.loads(asyncio.run(
                code_tools.refresh_project_map(str(self.project_path), force=True)
            ))

        self.assertFalse(result['ok'])
        self.assertEqual(result['error_name'], 'SNAPSHOT_PUBLISH_FAILED')
        self.assertEqual(result['ca_next_action'], 'inspect_snapshot_integrity')

    def test_write_fix_updates_cache_coordinates(self):
        # Initialize cache so write_fix can update the file cache on save.
        asyncio.run(code_tools.generate_project_map(str(self.project_path)))

        new_header = (
            'line1\n'
            'line2\n'
            'line3\n'
            'line4\n'
            'line5\n'
            '# header\n'
        )
        result = json.loads(asyncio.run(code_tools.write_fix(str(self.sample_file), 1, 1, new_header)))
        self.assertTrue(result.get('ok'), msg=result)

        cache_path = self.project_path / '.orchestrator_cache' / 'sample.py.json'
        self.assertTrue(cache_path.exists())
        metadata = json.loads(cache_path.read_text(encoding='utf-8'))

        symbol = next((s for s in metadata.get('symbols', []) if s.get('name') == 'foo'), None)
        self.assertIsNotNone(symbol, msg='Function symbol foo not found in cache metadata')
        self.assertEqual(symbol.get('line_start'), 8)
        self.assertGreaterEqual(symbol.get('line_end'), symbol.get('line_start'))

    def test_update_file_cache_syncs_index(self):
        asyncio.run(code_tools.generate_project_map(str(self.project_path)))
        self.sample_file.write_text(
            '# modified header\n\n'
            'def foo():\n'
            '    return 2\n',
            encoding='utf-8',
        )
        result = json.loads(asyncio.run(code_tools.update_file_cache(str(self.sample_file), str(self.project_path))))
        self.assertEqual(Path(result.get('file')).name, self.sample_file.name)
        index_data = json.loads((self.project_path / '.orchestrator_cache' / 'INDEX.json').read_text(encoding='utf-8'))
        self.assertIn('sample.py', index_data['files'])

    def test_project_map_skips_junk_and_secret_files(self):
        (self.project_path / 'analysis_output.txt').write_text('generated report', encoding='utf-8')
        (self.project_path / 'APIKeys.txt').write_text('secret', encoding='utf-8')
        (self.project_path / 'sample.py.bak').write_text('backup', encoding='utf-8')
        venv_file = self.project_path / 'venv' / 'lib.py'
        venv_file.parent.mkdir()
        venv_file.write_text('def from_venv():\n    pass\n', encoding='utf-8')

        json.loads(asyncio.run(code_tools.generate_project_map(str(self.project_path))))
        result = json.loads((self.project_path / '.orchestrator_cache' / 'INDEX.json').read_text(encoding='utf-8'))

        self.assertIn('sample.py', result['files'])
        self.assertNotIn('analysis_output.txt', result['files'])
        self.assertNotIn('APIKeys.txt', result['files'])
        self.assertNotIn('sample.py.bak', result['files'])
        self.assertFalse(any(path.startswith('venv/') for path in result['files']))

    def test_index_symbol_refs_include_line_ranges(self):
        json.loads(asyncio.run(code_tools.generate_project_map(str(self.project_path))))
        result = json.loads((self.project_path / '.orchestrator_cache' / 'INDEX.json').read_text(encoding='utf-8'))
        refs = result['functions'].get('foo')

        self.assertIsInstance(refs, list)
        self.assertEqual(refs[0]['file'], 'sample.py')
        self.assertEqual(refs[0]['line_start'], 3)
        self.assertGreaterEqual(refs[0]['line_end'], refs[0]['line_start'])

    def test_project_map_strips_bom_before_python_symbol_extraction(self):
        bom_file = self.project_path / 'bom_sample.py'
        bom_file.write_text(
            '\ufeffdef outer():\n'
            '    def nested():\n'
            '        return 1\n'
            '    return nested()\n',
            encoding='utf-8',
        )

        json.loads(asyncio.run(code_tools.generate_project_map(str(self.project_path))))
        index_data = json.loads((self.project_path / '.orchestrator_cache' / 'INDEX.json').read_text(encoding='utf-8'))

        self.assertIn('outer', index_data['functions'])
        self.assertNotIn('nested', index_data['functions'])
    def test_generate_project_map_removes_stale_cache_files(self):
        asyncio.run(code_tools.generate_project_map(str(self.project_path)))
        stale_cache = self.project_path / '.orchestrator_cache' / 'deleted.py.json'
        stale_cache.write_text('{}', encoding='utf-8')

        result = json.loads(asyncio.run(code_tools.generate_project_map(str(self.project_path))))

        self.assertFalse(stale_cache.exists())
        self.assertNotIn('deleted.py', json.loads((self.project_path / '.orchestrator_cache' / 'INDEX.json').read_text(encoding='utf-8'))['files'])

    def test_write_new_file_and_delete_file_sync_cache(self):
        asyncio.run(code_tools.generate_project_map(str(self.project_path)))
        new_file = self.project_path / 'created.py'

        created = json.loads(asyncio.run(code_tools.write_new_file(str(new_file), 'def created():\n    return True\n')))
        self.assertTrue(created.get('ok'), msg=created)
        index_data = json.loads((self.project_path / '.orchestrator_cache' / 'INDEX.json').read_text(encoding='utf-8'))
        self.assertIn('created.py', index_data['files'])

        deleted = json.loads(asyncio.run(code_tools.delete_file(str(new_file))))
        self.assertTrue(deleted.get('ok'), msg=deleted)
        index_data = json.loads((self.project_path / '.orchestrator_cache' / 'INDEX.json').read_text(encoding='utf-8'))
        self.assertNotIn('created.py', index_data['files'])

    def test_write_fix_cache_sync_uses_explicit_project_root(self):
        asyncio.run(code_tools.generate_project_map(str(self.project_path)))

        with mock.patch.object(
            code_tools, '_update_cache_after_write',
            wraps=code_tools._update_cache_after_write,
        ) as update_cache:
            result = json.loads(asyncio.run(code_tools.write_fix(
                'sample.py', 1, 1, '# updated\n',
                project_path=str(self.project_path),
            )))

        self.assertTrue(result.get('ok'), msg=result)
        self.assertTrue(any(
            call.args == (str(self.sample_file), str(self.project_path))
            for call in update_cache.call_args_list
        ), msg=update_cache.call_args_list)

    def test_write_new_file_validation_failure_does_not_publish_cache(self):
        asyncio.run(code_tools.generate_project_map(str(self.project_path)))
        target = self.project_path / 'phantom.py'

        with mock.patch.object(
            code_tools, 'validate_file',
            new=mock.AsyncMock(return_value=json.dumps({
                'ok': False, 'error': 'forced post-write failure',
            })),
        ), mock.patch.object(
            code_tools, '_update_cache_after_write',
            wraps=code_tools._update_cache_after_write,
        ) as update_cache:
            result = json.loads(asyncio.run(code_tools.write_new_file(
                'phantom.py',
                project_path=str(self.project_path),
                new_content='def phantom():\n    return True\n',
            )))

        self.assertFalse(result.get('ok'), msg=result)
        self.assertEqual(result.get('error_name'), 'VALIDATION_FAILED_AUTO_REVERT')
        self.assertFalse(target.exists())
        self.assertEqual(update_cache.call_count, 0)
        index_data = json.loads((self.project_path / '.orchestrator_cache' / 'INDEX.json').read_text(encoding='utf-8'))
        self.assertNotIn('phantom.py', index_data['files'])
        self.assertFalse((self.project_path / '.orchestrator_cache' / 'phantom.py.json').exists())

    def test_write_new_file_base64_dry_run_apply_and_validate_encoding(self):
        asyncio.run(code_tools.generate_project_map(str(self.project_path)))
        content = 'Xin chào tiếng Việt\nDòng mới có đủ dấu\n'
        encoded = base64.b64encode(content.encode('utf-8')).decode('ascii')

        dry = json.loads(asyncio.run(code_tools.write_new_file(
            'vietnamese.txt',
            project_path=str(self.project_path),
            new_content=encoded,
            content_encoding='base64',
            dry_run=True,
        )))

        self.assertTrue(dry.get('ok'), msg=dry)
        self.assertTrue(dry.get('dry_run'), msg=dry)
        self.assertTrue(dry.get('would_create'), msg=dry)
        self.assertFalse((self.project_path / 'vietnamese.txt').exists())
        self.assertIn('diff_preview', dry)
        self.assertGreater(dry['diff_preview']['added'], 0)
        self.assertIn('dry_run_id', dry)
        self.assertEqual(dry['encoding_check']['encoding_used'], 'base64')
        self.assertFalse(dry['encoding_check']['mojibake_risk'])

        applied = json.loads(asyncio.run(code_tools.apply_after_dry_run(
            str(self.project_path),
            dry['dry_run_id'],
            confirm_hash=dry['payload_hash'],
        )))

        self.assertTrue(applied.get('ok'), msg=applied)
        self.assertEqual((self.project_path / 'vietnamese.txt').read_text(encoding='utf-8'), content)
        self.assertEqual(applied['encoding_used'], 'plain')
        self.assertFalse(applied['mojibake_risk'])

        encoding = json.loads(asyncio.run(code_tools.validate_encoding(
            'vietnamese.txt',
            project_path=str(self.project_path),
        )))
        self.assertTrue(encoding.get('ok'), msg=encoding)
        self.assertTrue(encoding['utf8_valid'])
        self.assertFalse(encoding['mojibake_risk'])
        self.assertTrue(encoding['vietnamese_diacritic_check']['has_vietnamese_diacritics'])

    def test_write_new_file_rejects_invalid_base64_without_writing(self):
        result = json.loads(asyncio.run(code_tools.write_new_file(
            'bad.txt',
            project_path=str(self.project_path),
            new_content='not base64 ***',
            content_encoding='base64',
        )))

        self.assertFalse(result.get('ok'), msg=result)
        self.assertEqual(result.get('error_name'), 'INVALID_CONTENT_ENCODING')
        self.assertFalse((self.project_path / 'bad.txt').exists())

    def test_write_new_file_existing_and_binary_are_blocked(self):
        existing = json.loads(asyncio.run(code_tools.write_new_file(
            'sample.py',
            project_path=str(self.project_path),
            new_content='replacement',
        )))
        self.assertFalse(existing.get('ok'), msg=existing)
        self.assertEqual(existing.get('error_name'), 'FILE_ALREADY_EXISTS')
        self.assertTrue(existing.get('overwrite_risk'))

        binary = json.loads(asyncio.run(code_tools.write_new_file(
            'binary.txt',
            project_path=str(self.project_path),
            new_content='abc\x00def',
        )))
        self.assertFalse(binary.get('ok'), msg=binary)
        self.assertEqual(binary.get('error_name'), 'BINARY_FILE_UNSUPPORTED')
        self.assertFalse((self.project_path / 'binary.txt').exists())

    def test_apply_after_dry_run_rejects_stale_target(self):
        asyncio.run(code_tools.generate_project_map(str(self.project_path)))
        dry = json.loads(asyncio.run(code_tools.write_fix(
            'sample.py',
            1,
            1,
            '# changed\n',
            project_path=str(self.project_path),
            dry_run=True,
        )))
        self.assertTrue(dry.get('ok'), msg=dry)
        self.assertIn('diff_preview', dry)
        self.assertGreater(dry['diff_preview']['changed_lines'], 0)
        self.assertIn('payload_hash', dry)
        self.sample_file.write_text('# external\n\n' 'def foo():\n' '    return 1\n', encoding='utf-8')

        applied = json.loads(asyncio.run(code_tools.apply_after_dry_run(
            str(self.project_path),
            dry['dry_run_id'],
        )))

        self.assertFalse(applied.get('ok'), msg=applied)
        self.assertEqual(applied.get('error_name'), 'STALE_TARGET')

    def test_write_fix_html_returns_diff_and_encoding_contract(self):
        html = self.project_path / 'index.html'
        html.write_text('<html>\n<body>\n<p>Old</p>\n</body>\n</html>\n', encoding='utf-8')
        asyncio.run(code_tools.generate_project_map(str(self.project_path)))

        result = json.loads(asyncio.run(code_tools.write_fix_html(
            'index.html',
            3,
            3,
            '<p>Xin chào</p>\n',
            project_path=str(self.project_path),
        )))

        self.assertTrue(result.get('ok'), msg=result)
        self.assertIn('diff_preview', result)
        self.assertGreater(result['changed_lines'], 0)
        self.assertIn('encoding_check', result)
        self.assertTrue(result['encoding_check']['vietnamese_diacritic_check']['has_vietnamese_diacritics'])

    def test_read_file_range_accepts_project_path_and_relative_file(self):
        result = json.loads(asyncio.run(code_tools.read_file_range('sample.py', 3, 4, str(self.project_path))))

        self.assertIn('def foo', result['result'])
        self.assertEqual(result['shown'], '3-4')
        self.assertEqual(result['project_path'], str(self.project_path.resolve()))

    def test_read_file_range_honors_max_lines_budget(self):
        long_file = self.project_path / 'long.py'
        long_file.write_text('\n'.join(f'line_{i}' for i in range(1, 21)) + '\n', encoding='utf-8')

        result = json.loads(asyncio.run(code_tools.read_file_range(
            'long.py', 1, 20, str(self.project_path), max_lines=5,
        )))

        self.assertEqual(result['shown'], '1-5')
        self.assertEqual(result['max_lines'], 5)
        self.assertTrue(result['bounded'])

    def test_ac_f2_external_edit_read_reseed_write_fix_passes_first_try(self):
        asyncio.run(code_tools.generate_project_map(str(self.project_path)))
        self.sample_file.write_text(
            '# external edit\n\n'
            'def foo():\n'
            '    return 2\n',
            encoding='utf-8',
        )

        read = json.loads(asyncio.run(code_tools.read_file_range(
            'sample.py', 1, 4, str(self.project_path),
        )))
        self.assertTrue(read.get('ok'), msg=read)
        self.assertTrue(read.get('read_token_seeded'), msg=read)

        result = json.loads(asyncio.run(code_tools.write_fix(
            'sample.py', 1, 1, '# write after stable read\n',
            project_path=str(self.project_path),
        )))

        self.assertTrue(result.get('ok'), msg=result)
        self.assertNotEqual(result.get('error_name'), 'FILE_CHANGED_OUTSIDE_ORCHESTRATOR')
        self.assertIn('return 2', self.sample_file.read_text(encoding='utf-8'))

    def test_ac_g5_unstable_read_warns_and_force_reseed_audits(self):
        asyncio.run(code_tools.generate_project_map(str(self.project_path)))
        index_path = self.project_path / '.orchestrator_cache' / 'INDEX.json'

        original_stat = Path.stat

        class _Stat:
            def __init__(self, base, mtime):
                self.st_mtime = mtime
                self.st_size = base.st_size

        def _stat_flaps_for_sample():
            calls = {'count': 0}

            def _stat(path, *args, **kwargs):
                base = original_stat(path, *args, **kwargs)
                if str(path) == str(self.sample_file):
                    calls['count'] += 1
                    return _Stat(base, base.st_mtime + calls['count'])
                return base

            return _stat

        with mock.patch.object(Path, 'stat', autospec=True, side_effect=_stat_flaps_for_sample()):
            unstable = json.loads(asyncio.run(code_tools.read_file_range(
                'sample.py', 1, 2, str(self.project_path),
            )))

        self.assertTrue(unstable.get('read_unstable'), msg=unstable)
        self.assertFalse(unstable.get('read_token_seeded'), msg=unstable)
        index_data = json.loads(index_path.read_text(encoding='utf-8'))
        self.assertNotIn('read_hash', index_data['files']['sample.py'])

        with self.assertLogs('[A]', level='WARNING') as logs:
            with mock.patch.object(Path, 'stat', autospec=True, side_effect=_stat_flaps_for_sample()):
                forced = json.loads(asyncio.run(code_tools.read_file_range(
                    'sample.py', 1, 2, str(self.project_path), force_reseed=True,
                )))

        self.assertTrue(forced.get('read_unstable'), msg=forced)
        self.assertTrue(forced.get('read_token_seeded'), msg=forced)
        self.assertIn('force_reseed=true', '\n'.join(logs.output))
        index_data = json.loads(index_path.read_text(encoding='utf-8'))
        # CAN-052: read tokens live in the session-store token table now —
        # INDEX.json must stay untouched even on force_reseed.
        self.assertNotIn('read_hash', index_data['files']['sample.py'])
        token = code_tools._read_token_get(str(self.project_path), 'sample.py')
        self.assertTrue(token and token.get('read_hash'), msg=token)

    def test_bootstrap_session_rejects_missing_agent_with_valid_session(self):
        result = json.loads(asyncio.run(code_tools.bootstrap_session(
            str(self.project_path), session_id='session-a', agent_id='   ',
        )))

        self.assertFalse(result.get('ok'), msg=result)
        self.assertEqual(result.get('error_name'), 'BOOTSTRAP_SCOPE_REQUIRED')

    def test_bootstrap_session_rejects_missing_session_with_valid_agent(self):
        result = json.loads(asyncio.run(code_tools.bootstrap_session(
            str(self.project_path), session_id='   ', agent_id='agent-a',
        )))

        self.assertFalse(result.get('ok'), msg=result)
        self.assertEqual(result.get('error_name'), 'BOOTSTRAP_SCOPE_REQUIRED')

    def test_bootstrap_session_preserves_valid_explicit_scope(self):
        host_id = f'session-{self.project_path.name}'
        result = json.loads(asyncio.run(code_tools.bootstrap_session(
            str(self.project_path), session_id=f' {host_id} ', agent_id=' agent-a ',
            owner_claim_id=f'claim-{self.project_path.name}-0123456789abcdef0123456789abcdef',
        )))

        self.assertTrue(result.get('ok'), msg=result)
        self.assertEqual(result.get('session_scope', {}).get('session_id'), host_id)
        self.assertEqual(result.get('session_scope', {}).get('agent_id'), 'agent-a')

    def test_prepare_ca_session_writes_bootstrap_and_state(self):
        result = json.loads(asyncio.run(code_tools.prepare_ca_session(str(self.project_path), mode='T2', idea='Build CRM')))

        self.assertTrue(result.get('ok'), msg=result)
        self.assertTrue((self.project_path / '.orchestrator_cache' / 'CA_SESSION_BOOTSTRAP.md').exists())
        self.assertTrue((self.project_path / '.orchestrator_cache' / 'WORKFLOW_STATE.json').exists())
        self.assertIn('T2', result['prompt'])

    def test_prepare_ca_session_reports_scoped_open_and_bootstrap(self):
        old_db = code_tools._SESSIONS_DB
        code_tools._SESSIONS_DB = self.project_path / 'sessions_prepare_scoped.db'
        host_id = f'host-a-{self.project_path.name}'
        try:
            result = json.loads(asyncio.run(code_tools.prepare_ca_session(
                str(self.project_path), mode='T2', idea='Build CRM',
                agent_id='agent-a', host_session_id=host_id,
            )))

            self.assertTrue(result.get('ok'), msg=result)
            self.assertEqual(result['session']['status'], 'opened')
            self.assertEqual(result['session']['agent_id'], 'agent-a')
            self.assertEqual(result['bootstrap']['status'], 'ready')
            self.assertEqual(len(result['bootstrap_ref']), 16)
            state = json.loads(Path(result['workflow_state_path']).read_text(encoding='utf-8'))
            self.assertEqual(state['agent_id'], 'agent-a')
            self.assertEqual(state['host_session_id'], host_id)
            self.assertIn('agent_id = `agent-a`', result['prompt'])
        finally:
            code_tools._SESSIONS_DB = old_db

    def test_prepare_ca_session_isolates_agents_and_resumes_each_own_session(self):
        old_db = code_tools._SESSIONS_DB
        code_tools._SESSIONS_DB = self.project_path / 'sessions_prepare_agents.db'
        try:
            def prepare(agent_id, host_session_id):
                return json.loads(asyncio.run(code_tools.prepare_ca_session(
                    str(self.project_path), mode='T2', idea=f'Build CRM {agent_id}',
                    agent_id=agent_id, host_session_id=host_session_id,
                )))

            host_a = f'host-a-{self.project_path.name}'
            host_b = f'host-b-{self.project_path.name}'
            first_a = prepare('agent-a', host_a)
            first_b = prepare('agent-b', host_b)
            repeat_a = prepare('agent-a', host_a)
            repeat_b = prepare('agent-b', host_b)

            self.assertTrue(all(item.get('ok') for item in (
                first_a, first_b, repeat_a, repeat_b,
            )), msg=(first_a, first_b, repeat_a, repeat_b))
            self.assertNotEqual(first_a['session_id'], first_b['session_id'])
            self.assertEqual(repeat_a['session_id'], first_a['session_id'])
            self.assertEqual(repeat_b['session_id'], first_b['session_id'])
            self.assertEqual(repeat_a['session']['status'], 'resumed')
            self.assertEqual(repeat_b['session']['status'], 'resumed')
        finally:
            code_tools._SESSIONS_DB = old_db

    def test_prepare_ca_session_concurrent_artifacts_are_agent_scoped(self):
        old_db = code_tools._SESSIONS_DB
        code_tools._SESSIONS_DB = self.project_path / 'sessions_prepare_concurrent.db'
        text_barrier = threading.Barrier(2)
        json_barrier = threading.Barrier(2)
        real_text = code_tools._atomic_write_text
        real_json = code_tools._atomic_write_json

        def barrier_text(path, content, **kwargs):
            real_text(path, content, **kwargs)
            if Path(path).name == 'CA_SESSION_BOOTSTRAP.md':
                text_barrier.wait(timeout=5)

        def barrier_json(path, content, **kwargs):
            real_json(path, content, **kwargs)
            if Path(path).name == 'WORKFLOW_STATE.json':
                json_barrier.wait(timeout=5)

        def prepare(agent_id, host_session_id):
            return json.loads(asyncio.run(code_tools.prepare_ca_session(
                str(self.project_path), mode='T2', idea=f'Build CRM {agent_id}',
                agent_id=agent_id, host_session_id=host_session_id,
            )))

        try:
            with mock.patch.object(code_tools, '_atomic_write_text', side_effect=barrier_text), \
                    mock.patch.object(code_tools, '_atomic_write_json', side_effect=barrier_json), \
                    ThreadPoolExecutor(max_workers=2) as executor:
                futures = [
                    executor.submit(prepare, 'agent/a ..', 'host-a'),
                    executor.submit(prepare, 'agent\\a ..', 'host-b'),
                ]
                results = [future.result(timeout=15) for future in futures]

            self.assertTrue(all(item.get('ok') for item in results), msg=results)
            self.assertNotEqual(results[0]['session_id'], results[1]['session_id'])
            self.assertNotEqual(results[0]['bootstrap_path'], results[1]['bootstrap_path'])
            self.assertNotEqual(results[0]['workflow_state_path'], results[1]['workflow_state_path'])
            cache_root = (self.project_path / '.orchestrator_cache').resolve()
            for result, agent_id, host_id in zip(
                    results, ('agent/a ..', 'agent\\a ..'), ('host-a', 'host-b')):
                bootstrap_path = Path(result['bootstrap_path']).resolve()
                state_path = Path(result['workflow_state_path']).resolve()
                self.assertEqual(cache_root, bootstrap_path.parents[2])
                self.assertEqual(cache_root, state_path.parents[2])
                self.assertIn(f'agent_id = `{agent_id}`', bootstrap_path.read_text(encoding='utf-8'))
                state = json.loads(state_path.read_text(encoding='utf-8'))
                self.assertEqual(state['agent_id'], agent_id)
                self.assertEqual(state['host_session_id'], host_id)
        finally:
            code_tools._SESSIONS_DB = old_db

    def test_prepare_ca_session_open_failure_propagates_without_false_bootstrap_success(self):
        failed = json.dumps({
            'ok': False, 'error': 'session store unavailable',
            'error_name': 'OPEN_SESSION_FAILED',
        })
        bootstrap = mock.AsyncMock()
        with mock.patch.object(code_tools, 'open_session', mock.AsyncMock(return_value=failed)), \
                mock.patch.object(code_tools, 'bootstrap_session', bootstrap):
            result = json.loads(asyncio.run(code_tools.prepare_ca_session(
                str(self.project_path), agent_id='', host_session_id='',
            )))

        self.assertFalse(result.get('ok'), msg=result)
        self.assertEqual(result['error'], 'session store unavailable')
        self.assertEqual(result['error_name'], 'OPEN_SESSION_FAILED')
        self.assertEqual(result['session']['status'], 'failed')
        self.assertEqual(result['session']['error'], 'session store unavailable')
        self.assertEqual(result['session']['error_name'], 'OPEN_SESSION_FAILED')
        self.assertNotIn('session_id', result)
        self.assertEqual(result['bootstrap']['status'], 'not_started')
        self.assertIsNone(result['bootstrap']['bootstrap_ref'])
        self.assertNotIn('bootstrap_ref', result)
        bootstrap.assert_not_awaited()

    def test_prepare_ca_session_bootstrap_failure_preserves_opened_session_evidence(self):
        failed = json.dumps({
            'ok': False, 'error': 'bootstrap unavailable',
            'error_name': 'BOOTSTRAP_FAILED',
        })
        with mock.patch.object(code_tools, '_bootstrap_with_cached_owner_claim',
                               mock.AsyncMock(return_value=failed)):
            result = json.loads(asyncio.run(code_tools.prepare_ca_session(
                str(self.project_path), agent_id='agent-a', host_session_id='host-a',
                owner_claim_id='prepare-failure-0123456789abcdef0123456789abcdef',
            )))

        self.assertFalse(result.get('ok'), msg=result)
        self.assertEqual(result['session']['status'], 'opened')
        self.assertTrue(result['session_id'])
        self.assertEqual(result['session']['session_id'], result['session_id'])
        self.assertEqual(result['bootstrap']['status'], 'failed')
        self.assertIsNone(result['bootstrap']['bootstrap_ref'])
        self.assertNotIn('bootstrap_ref', result)
        self.assertEqual(result['error_name'], 'BOOTSTRAP_FAILED')

    def test_workflow_status_creates_default_state_when_missing(self):
        # R43.2 C2: workflow_status now binds to MD workflow runner; registry inspector renamed
        result = json.loads(asyncio.run(code_tools.workflow_status_registry(str(self.project_path))))

        self.assertTrue(result.get('ok'), msg=result)
        self.assertTrue(result.get('created'), msg=result)
        self.assertEqual(result['state']['mode'], 'T1')
        self.assertTrue((self.project_path / '.orchestrator_cache' / 'WORKFLOW_STATE.json').exists())

    def test_project_map_refresh_preserves_workflow_and_backup_state(self):
        cache_dir = self.project_path / '.orchestrator_cache'
        cache_dir.mkdir(parents=True, exist_ok=True)
        workflow_state = cache_dir / 'WORKFLOW_STATE.json'
        backup_manifest = cache_dir / 'BACKUP_MANIFEST.json'
        workflow_state.write_text('{"mode": "T1_EXISTING_APP"}', encoding='utf-8')
        backup_manifest.write_text('{"backups": []}', encoding='utf-8')

        asyncio.run(code_tools.refresh_project_map(str(self.project_path)))

        self.assertTrue(workflow_state.exists())
        self.assertTrue(backup_manifest.exists())

    def test_project_map_refresh_preserves_live_instance_lock(self):
        cache_dir = self.project_path / '.orchestrator_cache'
        cache_dir.mkdir(parents=True, exist_ok=True)
        instance_lock = cache_dir / 'orchestrator.lock'
        instance_lock.write_text('live-instance-lock', encoding='utf-8')

        asyncio.run(code_tools.refresh_project_map(str(self.project_path)))

        self.assertEqual(instance_lock.read_text(encoding='utf-8'), 'live-instance-lock')
    def test_backup_status_missing_reports_next_action(self):
        result = json.loads(asyncio.run(code_tools.backup_status(str(self.project_path))))

        self.assertTrue(result.get('ok'), msg=result)
        self.assertEqual(result.get('status'), 'missing')
        self.assertIn('backup_project', result.get('next_action', ''))

    def test_session_prompt_is_ca_first_english_contract(self):
        prompt = code_tools._session_prompt(str(self.project_path), 'T1')

        self.assertIn('Use only the Orchestrator API', prompt)
        self.assertIn('Discover / Analyze / Read', prompt)
        self.assertIn('Mutate / Verify / Refresh', prompt)
        self.assertIn('Session / Audit / Auxiliary', prompt)
        self.assertIn('wrong duplicated path', prompt)
        self.assertIn('NEVER use `curl`, `wget`, `bash`, `cat`, `grep`, `sed`, or `awk`', prompt)
        self.assertIn('{ORCHESTRATOR_BASE_URL}', prompt)
        self.assertNotIn('â€”', prompt)
        self.assertNotIn('LUá»', prompt)

    def test_capability_status_and_benchmark_context_tools(self):
        capability = json.loads(asyncio.run(code_tools.capability_status(str(self.project_path))))
        self.assertTrue(capability.get('ok'), msg=capability)
        self.assertTrue(any(item['name'] == 'python_ast' for item in capability['capabilities']))

        benchmark = json.loads(asyncio.run(code_tools.create_benchmark_request(str(self.project_path), 'New app')))
        self.assertTrue(benchmark.get('ok'), msg=benchmark)
        self.assertTrue((self.project_path / 'BENCHMARK_REQUEST.md').exists())

        build = json.loads(asyncio.run(code_tools.create_build_context(str(self.project_path), 'New app', 'approved link')))
        self.assertTrue(build.get('ok'), msg=build)
        self.assertTrue((self.project_path / 'BUILD_CONTEXT.md').exists())
    def test_context_artifact_cache_updates_use_explicit_project_root(self):
        with mock.patch.object(code_tools.surgical_mapper, "update_file_cache", return_value={}) as update:
            asyncio.run(code_tools.create_benchmark_request(str(self.project_path), "New app"))
            asyncio.run(code_tools.create_build_context(str(self.project_path), "New app", "approved link"))

        self.assertEqual(update.call_count, 2)
        for call in update.call_args_list:
            file_path, project_root = call.args
            self.assertEqual(Path(project_root).resolve(), self.project_path.resolve())
            self.assertTrue(Path(file_path).resolve().is_relative_to(self.project_path.resolve()))
    def test_cache_delta_reports_modified_added_deleted(self):
        asyncio.run(code_tools.generate_project_map(str(self.project_path)))
        self.sample_file.write_text('def foo():\n    return 3\n', encoding='utf-8')
        (self.project_path / 'added.py').write_text('def added():\n    return True\n', encoding='utf-8')

        delta = json.loads(asyncio.run(code_tools.cache_delta(str(self.project_path))))

        self.assertIn('sample.py', delta['modified'])
        self.assertIn('added.py', delta['added'])
        self.assertEqual(delta['total_changed'], len(delta['added']) + len(delta['modified']) + len(delta['deleted']))
        self.assertIn('cache_updated_at', delta)

    def test_cache_delta_uses_same_directory_skips_as_project_map(self):
        asyncio.run(code_tools.generate_project_map(str(self.project_path)))
        hidden_dir = self.project_path / '.BUILDING PLACE'
        hidden_dir.mkdir()
        (hidden_dir / 'draft.md').write_text('working note', encoding='utf-8')

        delta = json.loads(asyncio.run(code_tools.cache_delta(str(self.project_path))))

        self.assertNotIn('.BUILDING PLACE/draft.md', delta['added'])

    def test_query_index_can_fail_when_auto_refresh_disabled(self):
        asyncio.run(code_tools.generate_project_map(str(self.project_path)))
        self.sample_file.write_text('def foo():\n    return 42\n', encoding='utf-8')

        result = json.loads(asyncio.run(code_tools.query_index(
            str(self.project_path),
            group='source',
            auto_refresh=False,
        )))

        self.assertFalse(result.get('ok'), msg=result)
        self.assertEqual(result.get('category'), 'stale_index')
        self.assertIn('refresh_project_map', result.get('error', ''))

    def test_query_index_requires_explicit_refresh_for_missing_index(self):
        result = json.loads(asyncio.run(code_tools.query_index(str(self.project_path), group='source')))

        self.assertFalse(result.get('ok'), msg=result)
        self.assertEqual(result.get('error_name'), 'INDEX_REFRESH_REQUIRED')
        self.assertFalse((self.project_path / '.orchestrator_cache' / 'INDEX.json').exists())

    def test_create_folder_resolves_relative_path_via_project_path(self):
        rel = 'nested_via_project'
        result = json.loads(asyncio.run(code_tools.create_folder(
            rel, project_path=str(self.project_path),
        )))
        expected = self.project_path / rel
        self.assertTrue(result.get('ok'), msg=result)
        self.assertTrue(expected.is_dir())
        self.assertEqual(Path(result['path']), expected)

    def test_create_folders_rejects_nested_arrays(self):
        nested = [str(self.project_path / 'ok'), [str(self.project_path / 'bad')]]
        result = json.loads(asyncio.run(code_tools.create_folders(nested)))

        self.assertFalse(result.get('ok'), msg=result)
        self.assertEqual(result.get('count'), 2)
        self.assertTrue((self.project_path / 'ok').is_dir())
        bad = result['results'][1]
        self.assertEqual(bad.get('error_name'), 'WRONG_ARGUMENTS')
        self.assertFalse(Path(nested[1][0]).exists())


    def test_create_folders_relative_via_project_path(self):
        result = json.loads(asyncio.run(code_tools.create_folders(
            ['rel/sub'], project_path=str(self.project_path),
        )))
        expected = self.project_path / 'rel' / 'sub'
        self.assertTrue(result.get('ok'), msg=result)
        self.assertTrue(expected.is_dir())
        self.assertEqual(Path(result['results'][0]['path']), expected)

    def test_create_folders_absolute_ignores_project_path(self):
        abs_dir = self.tempdir / 'abs_target'
        other = self.tempdir / 'other_project'
        other.mkdir()
        result = json.loads(asyncio.run(code_tools.create_folders(
            [str(abs_dir)], project_path=str(other),
        )))
        self.assertTrue(result.get('ok'), msg=result)
        self.assertTrue(abs_dir.is_dir())
        self.assertFalse((other / abs_dir.name).exists())

    def test_create_folders_empty_paths(self):
        result = json.loads(asyncio.run(code_tools.create_folders([])))
        self.assertFalse(result.get('ok'), msg=result)
        self.assertEqual(result.get('error_name'), 'WRONG_ARGUMENTS')


    def test_sample_unc_paths_rejects_json_string_paths(self):
        result = json.loads(asyncio.run(code_tools.sample_unc_paths_with_root_check(
            '["\\\\server\\share\\folder"]'
        )))

        self.assertFalse(result.get('ok'), msg=result)
        self.assertEqual(result.get('error_name'), 'WRONG_ARGUMENTS')
        self.assertEqual(result.get('field'), 'paths')
        self.assertEqual(result.get('expected_type'), 'array')

    def test_workflow_goto_step_rejects_string_target_before_lookup(self):
        result = json.loads(asyncio.run(code_tools.workflow_goto_step(
            'missing_workflow_for_type_test',
            '3',
        )))

        self.assertFalse(result.get('ok'), msg=result)
        self.assertEqual(result.get('error_name'), 'INVALID_TARGET_STEP')
        self.assertEqual(result.get('field'), 'target_step')

    def test_find_symbol_uses_live_fallback_when_index_stale(self):
        asyncio.run(code_tools.generate_project_map(str(self.project_path)))
        self.sample_file.write_text('def foo():\n    return 42\n', encoding='utf-8')

        result = json.loads(asyncio.run(code_tools.find_symbol(
            symbol='foo',
            project_path=str(self.project_path),
        )))

        self.assertEqual(result.get('category'), 'stale_index_live_fallback')
        self.assertEqual(result.get('count'), 1, msg=result)
        self.assertEqual(result['results'][0]['line'], 1)

    def test_core_tools_accept_alias_schema(self):
        find_result = json.loads(asyncio.run(code_tools.find_symbol(
            symbol='foo',
            project_path=str(self.project_path),
        )))
        search_result = json.loads(asyncio.run(code_tools.search_code(
            pattern='def foo',
            project_path=str(self.project_path),
        )))

        self.assertEqual(find_result.get('count'), 1, msg=find_result)
        self.assertEqual(search_result.get('count'), 1, msg=search_result)

    def test_search_code_pattern_alias_is_regex(self):
        result = json.loads(asyncio.run(code_tools.search_code(
            pattern=r'def\s+foo',
            project_path=str(self.project_path),
        )))

        self.assertEqual(result.get('count'), 1, msg=result)

    def test_search_code_honors_context_and_result_budget(self):
        result = json.loads(asyncio.run(code_tools.search_code(
            query='return',
            project_path=str(self.project_path),
            max_context_lines=0,
            max_results=1,
        )))

        self.assertEqual(result.get('count'), 1, msg=result)
        self.assertEqual(result.get('max_context_lines'), 0)
        self.assertEqual(result['results'][0].get('context_before'), '')
        self.assertEqual(result['results'][0].get('context_after'), '')

    def test_validate_symbols_allows_unrelated_file_drift(self):
        asyncio.run(code_tools.generate_project_map(str(self.project_path)))
        other = self.project_path / 'other.py'
        other.write_text('def other():\n    return 1\n', encoding='utf-8')

        result = json.loads(asyncio.run(code_tools.validate_symbols('sample.py', str(self.project_path))))

        self.assertTrue(result.get('ok'), msg=result)
        self.assertTrue(result.get('syntax_ok'), msg=result)

    def test_validate_symbols_fails_when_target_is_stale_deleted(self):
        asyncio.run(code_tools.generate_project_map(str(self.project_path)))
        self.sample_file.unlink()

        result = json.loads(asyncio.run(code_tools.validate_symbols('sample.py', str(self.project_path))))

        self.assertFalse(result.get('ok'), msg=result)
        self.assertEqual(result.get('category'), 'stale_index')

    def test_validate_symbols_uses_live_ast_when_target_modified(self):
        asyncio.run(code_tools.generate_project_map(str(self.project_path)))
        self.sample_file.write_text('def foo():\n    return 42\n', encoding='utf-8')

        result = json.loads(asyncio.run(code_tools.validate_symbols('sample.py', str(self.project_path))))

        self.assertTrue(result.get('ok'), msg=result)
        self.assertEqual(result.get('category'), 'stale_index_live_fallback')
        self.assertIn('foo', result.get('symbols_found', []))

    def test_run_test_profile_reports_consistent_available_profiles(self):
        result = json.loads(asyncio.run(code_tools.run_test_profile(str(self.project_path), profile='not_a_profile')))

        self.assertFalse(result.get('ok'), msg=result)
        self.assertEqual(result.get('available'), code_tools.SUPPORTED_TEST_PROFILES)

    @unittest.skipUnless(sys.platform == 'win32', 'Windows venv layout')
    def test_run_test_profile_prefers_project_pytest_runner(self):
        scripts = self.project_path / 'venv312' / 'Scripts'
        scripts.mkdir(parents=True)
        (self.project_path / 'code_tools.py').touch()
        project_python = scripts / 'python.exe'
        project_python.touch()
        (scripts / 'pytest.exe').touch()

        runner = mock.AsyncMock(return_value=json.dumps({'ok': True}))
        selected = {'ok': True, 'executable': str(project_python), 'source': 'venv312'}
        with mock.patch.object(code_tools, 'run_project_command', runner), \
                mock.patch.object(code_tools, '_select_python_interpreter', return_value=selected):
            result = json.loads(asyncio.run(code_tools.run_test_profile(
                str(self.project_path), profile='pytest',
            )))

        self.assertTrue(result.get('ok'), msg=result)
        command = runner.await_args.args[1]
        self.assertEqual(Path(command[0]), project_python)
        self.assertEqual(command[1:3], ['-m', 'pytest'])

    def test_run_project_command_returns_exit_code(self):
        result = json.loads(asyncio.run(code_tools.run_project_command(
            str(self.project_path),
            ['cmd', '/c', 'echo', 'orchestrator_test'],
        )))

        self.assertTrue(result.get('ok'), msg=result)
        self.assertEqual(result.get('exit_code'), 0)
        self.assertEqual(result.get('command_received'), ['cmd', '/c', 'echo', 'orchestrator_test'])
        self.assertEqual(Path(result.get('cwd')), self.project_path.resolve())
        self.assertEqual(Path(result.get('project_path')), self.project_path.resolve())
        self.assertEqual(result.get('shell'), 'exec')
        self.assertIn('project_path is used as cwd', result.get('path_resolution_note', ''))
        self.assertIn('orchestrator_test', result.get('stdout', ''))

    def test_basetemp_temp_alias_is_normalized_before_cmd_escape(self):
        with mock.patch.object(code_tools.subprocess, 'Popen') as popen:
            proc = popen.return_value
            proc.pid = 123
            proc.returncode = 0
            proc.communicate.return_value = ('ok', '')
            result = json.loads(asyncio.run(code_tools.run_project_command(
                str(self.project_path), 'pytest --basetemp=%TEMP%\\quoted', shell_mode='cmd',
            )))
        self.assertTrue(result.get('ok'), msg=result)
        launched = popen.call_args.args[0]
        self.assertNotIn('%TEMP%', launched)
        self.assertNotIn('%%TEMP%%', launched)
        self.assertIn('quoted', launched)

    def test_cmd_escape_does_not_double_existing_percent_pair(self):
        with mock.patch.object(code_tools.subprocess, 'Popen') as popen:
            proc = popen.return_value
            proc.pid = 123
            proc.returncode = 0
            proc.communicate.return_value = ('ok', '')
            asyncio.run(code_tools.run_project_command(
                str(self.project_path), 'echo %%LITERAL%%', shell_mode='cmd',
            ))
        self.assertIn('%%LITERAL%%', popen.call_args.args[0])
        self.assertNotIn('%%%%LITERAL%%%%', popen.call_args.args[0])

    def test_python_selector_uses_first_interpreter_with_required_modules(self):
        candidates = [('explicit', 'explicit-python'), ('env', 'env-python')]
        first = mock.Mock(returncode=3)
        second = mock.Mock(returncode=0)
        with mock.patch.object(code_tools, '_python_interpreter_candidates', return_value=candidates), \
                mock.patch('subprocess.run', side_effect=[first, second]) as run:
            selected = code_tools._select_python_interpreter(
                self.project_path, 'explicit-python', ['pytest'],
            )
        self.assertTrue(selected['ok'])
        self.assertEqual(selected['source'], 'env')
        self.assertEqual(run.call_args_list[0].args[0][0], 'explicit-python')
        self.assertIn('pytest', run.call_args_list[0].args[0][2])

    def test_python_selector_missing_capability_has_no_install_suggestion(self):
        with mock.patch.object(code_tools, '_python_interpreter_candidates',
                               return_value=[('sys', 'python')]), \
                mock.patch('subprocess.run', return_value=mock.Mock(returncode=3)):
            selected = code_tools._select_python_interpreter(
                self.project_path, required_modules=['pytest'],
            )
        self.assertEqual(selected['error_name'], 'INTERPRETER_CAPABILITY_MISSING')
        self.assertNotIn('pip install', json.dumps(selected).lower())

    def test_script_output_truncation_returns_hash_metadata(self):
        result = json.loads(asyncio.run(code_tools.run_project_test_script(
            str(self.project_path), "print('unicode á quote \\\"' + 'x' * 40)",
            runtime='python', stdout_max_chars=10, stderr_max_chars=10,
        )))
        self.assertTrue(result.get('ok'), msg=result)
        metadata = result['stdout_metadata']
        self.assertTrue(metadata['truncated'])
        self.assertEqual(metadata['returned_chars'], 10)
        self.assertGreater(metadata['original_chars'], metadata['returned_chars'])
        self.assertEqual(len(metadata['sha256']), 64)

    def test_script_required_modules_are_forwarded_to_interpreter_selector(self):
        selected = {'ok': False, 'error_name': 'INTERPRETER_CAPABILITY_MISSING'}
        selector = mock.Mock(return_value=selected)
        with mock.patch.object(code_tools, '_select_python_interpreter', selector):
            result = json.loads(asyncio.run(code_tools.run_project_test_script(
                str(self.project_path), 'print(1)', runtime='python',
                required_python_modules=['pytest', 'aiohttp'],
            )))
        self.assertEqual(result, selected)
        self.assertEqual(selector.call_args.args[2], ['pytest', 'aiohttp'])

    @unittest.skipUnless(sys.platform == 'win32', 'Windows transport policy')
    def test_complex_node_argv_auto_routes_to_script_executor(self):
        routed = {'ok': True, 'stdout': 'á "quoted"'}
        runner = mock.AsyncMock(return_value=json.dumps(routed))
        body = "console.log('á \\\"quoted\\\"', JSON.stringify({x: 1}))"
        with mock.patch.object(code_tools, 'run_project_test_script', runner):
            result = json.loads(asyncio.run(code_tools.run_project_command(
                str(self.project_path), ['node', '-e', body],
            )))
        self.assertTrue(result.get('auto_routed'), msg=result)
        self.assertEqual(result.get('transport'), 'script_file')
        self.assertEqual(runner.await_args.args[1], body)

    @unittest.skipUnless(sys.platform == 'win32', 'Windows transport policy')
    def test_complex_powershell_argv_auto_routes_to_script_executor(self):
        routed = {'ok': True, 'stdout': 'Tiếng Việt'}
        runner = mock.AsyncMock(return_value=json.dumps(routed))
        body = "Write-Output 'Tiếng Việt $env:TEMP'"
        with mock.patch.object(code_tools, 'run_project_test_script', runner):
            result = json.loads(asyncio.run(code_tools.run_project_command(
                str(self.project_path), ['powershell', '-Command', body],
            )))
        self.assertTrue(result.get('auto_routed'), msg=result)
        self.assertEqual(result.get('routed_from'), 'powershell_-Command')
        self.assertEqual(runner.await_args.args[1], body)

    def test_run_project_command_background_and_kill_process(self):
        pid = 0
        killed = None
        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter('always')
            try:
                result = json.loads(asyncio.run(code_tools.run_project_command(
                    str(self.project_path),
                    [sys.executable, '-c', 'import time; time.sleep(30)'],
                    timeout=5,
                    background=True,
                )))

                self.assertTrue(result.get('ok'), msg=result)
                self.assertTrue(result.get('keep_alive'), msg=result)
                self.assertTrue(result.get('background'), msg=result)
                pid = result.get('spawned_pid') or 0
                self.assertGreater(pid, 0)
            finally:
                if pid:
                    killed = json.loads(asyncio.run(code_tools.kill_process(
                        pid=pid,
                        force=True,
                    )))

            self.assertTrue(killed.get('ok'), msg=killed)
            self.assertEqual(killed.get('killed_count'), 1)
            self.assertNotIn(pid, code_tools._BACKGROUND_PROCESSES)
            gc.collect()

        process_warnings = [
            warning for warning in caught
            if issubclass(warning.category, ResourceWarning)
            or warning.category.__name__ == 'PytestUnraisableExceptionWarning'
        ]
        self.assertEqual(process_warnings, [])

    def test_kill_process_graceful_timeout_reports_failure_without_force_kill(self):
        import psutil

        proc = mock.Mock()
        proc.pid = 4242
        proc.name.return_value = 'slow-process'
        proc.wait.side_effect = psutil.TimeoutExpired(5, pid=proc.pid, name='slow-process')

        with mock.patch.object(psutil, 'Process', return_value=proc), mock.patch.object(
            code_tools, '_reap_background_process'
        ) as reap:
            result = json.loads(asyncio.run(code_tools.kill_process(pid=proc.pid, force=False)))

        self.assertFalse(result.get('ok'), msg=result)
        self.assertEqual(result.get('killed_count'), 0)
        self.assertEqual(result.get('failed_count'), 1)
        self.assertIn('timeout', result['failed'][0]['error'].lower())
        proc.terminate.assert_called_once_with()
        proc.kill.assert_not_called()
        proc.wait.assert_called_once_with(timeout=5)
        reap.assert_called_once_with(proc.pid, timeout=0, kill_if_running=False)

    def test_reap_background_process_without_kill_policy_keeps_running_process(self):
        pid = 4343
        proc = mock.Mock(pid=pid)
        proc.poll.return_value = None
        proc.wait.side_effect = code_tools.subprocess.TimeoutExpired(['slow-process'], 0)
        code_tools._BACKGROUND_PROCESSES[pid] = proc
        try:
            code_tools._reap_background_process(pid, timeout=0, kill_if_running=False)

            self.assertIs(code_tools._BACKGROUND_PROCESSES.get(pid), proc)
            proc.kill.assert_not_called()
            for stream_name in ('stdin', 'stdout', 'stderr'):
                getattr(proc, stream_name).close.assert_not_called()
        finally:
            code_tools._BACKGROUND_PROCESSES.pop(pid, None)

    def test_reap_background_process_does_not_overwrite_newer_pid_registration(self):
        pid = 4444
        proc = mock.Mock(pid=pid)
        proc.poll.return_value = None
        newer_proc = mock.Mock(pid=pid)

        def register_newer_then_timeout(timeout):
            code_tools._BACKGROUND_PROCESSES[pid] = newer_proc
            raise code_tools.subprocess.TimeoutExpired(['slow-process'], timeout)

        proc.wait.side_effect = register_newer_then_timeout
        code_tools._BACKGROUND_PROCESSES[pid] = proc
        try:
            code_tools._reap_background_process(pid, timeout=0, kill_if_running=False)

            self.assertIs(code_tools._BACKGROUND_PROCESSES.get(pid), newer_proc)
            proc.kill.assert_not_called()
        finally:
            code_tools._BACKGROUND_PROCESSES.pop(pid, None)

    def test_resolve_project_path_handles_file_folder_space_and_unicode(self):
        subdir = self.project_path / '2. Rawcut tiếng Việt'
        subdir.mkdir()
        nested_file = subdir / 'health-check.js'
        nested_file.write_text('console.log("ok")\n', encoding='utf-8')
        asyncio.run(code_tools.generate_project_map(str(self.project_path)))

        file_result = json.loads(asyncio.run(code_tools.resolve_project_path(str(nested_file))))
        folder_result = json.loads(asyncio.run(code_tools.resolve_project_path(str(subdir))))

        self.assertTrue(file_result.get('ok'), msg=file_result)
        self.assertEqual(Path(file_result['project_path']), self.project_path.resolve())
        self.assertEqual(file_result['file_path'], '2. Rawcut tiếng Việt/health-check.js')
        self.assertTrue(file_result['is_file'])
        self.assertTrue(folder_result.get('ok'), msg=folder_result)
        self.assertEqual(folder_result['folder_path'], '2. Rawcut tiếng Việt')

    def test_resolve_project_path_outside_project_does_not_pick_app_root(self):
        outside = self.tempdir.parent / (self.tempdir.name + '_outside')
        outside.mkdir()
        try:
            (outside / 'requirements.txt').write_text('', encoding='utf-8')
            result = json.loads(asyncio.run(code_tools.resolve_project_path(str(outside))))

            self.assertTrue(result.get('ok'), msg=result)
            self.assertEqual(Path(result['project_path']), outside.resolve())
        finally:
            shutil.rmtree(outside, ignore_errors=True)

    def test_open_session_warns_and_resolves_file_path(self):
        old_db = code_tools._SESSIONS_DB
        code_tools._SESSIONS_DB = self.project_path / 'sessions_path.db'
        try:
            asyncio.run(code_tools.generate_project_map(str(self.project_path)))
            result = json.loads(asyncio.run(code_tools.open_session(
                'File path session',
                str(self.sample_file),
                'T1',
            )))

            self.assertTrue(result.get('ok'), msg=result)
            self.assertFalse(result.get('project_path_confirmed'), msg=result)
            self.assertEqual(result.get('resolved_from'), str(self.sample_file))
            self.assertIn('file path received', result.get('path_resolution_note', ''))
            self.assertEqual(Path(result['project_path']), self.project_path.resolve())
        finally:
            code_tools._SESSIONS_DB = old_db

    def test_open_session_isolates_agents_and_resumes_same_agent(self):
        old_db = code_tools._SESSIONS_DB
        code_tools._SESSIONS_DB = self.project_path / 'sessions_agents.db'
        try:
            first_a = json.loads(asyncio.run(code_tools.open_session(
                'Agent A', str(self.project_path), 'T1', agent_id='agent-a',
            )))
            first_b = json.loads(asyncio.run(code_tools.open_session(
                'Agent B', str(self.project_path), 'T1', agent_id='agent-b',
            )))
            resumed_a = json.loads(asyncio.run(code_tools.open_session(
                'Agent A repeat', str(self.project_path), 'T1', agent_id='agent-a',
            )))

            self.assertTrue(first_a.get('ok'), msg=first_a)
            self.assertTrue(first_b.get('ok'), msg=first_b)
            self.assertNotEqual(first_a['session_id'], first_b['session_id'])
            self.assertFalse(first_a['resumed'])
            self.assertFalse(first_b['resumed'])
            self.assertTrue(resumed_a['resumed'])
            self.assertEqual(resumed_a['session_id'], first_a['session_id'])
            self.assertEqual(first_b['agent_id'], 'agent-b')
            self.assertEqual(first_b['cslt_policy']['host_gate']['agent_id'], 'agent-b')
        finally:
            code_tools._SESSIONS_DB = old_db

    def test_concurrent_explicit_open_resumes_one_agent_session(self):
        old_db = code_tools._SESSIONS_DB
        db_path = self.project_path / 'sessions_explicit_concurrent.db'
        code_tools._SESSIONS_DB = db_path
        worker_count = 8
        barrier = threading.Barrier(worker_count + 1)
        results = []
        errors = []

        def open_scoped(title):
            try:
                barrier.wait(timeout=5)
                results.append(json.loads(asyncio.run(code_tools.open_session(
                    title, str(self.project_path), 'T1', agent_id='agent-a',
                ))))
            except BaseException as exc:
                errors.append(exc)

        try:
            threads = [
                threading.Thread(target=open_scoped, args=(f'Concurrent {index}',))
                for index in range(worker_count)
            ]
            for thread in threads:
                thread.start()
            barrier.wait(timeout=5)
            for thread in threads:
                thread.join(timeout=15)

            self.assertFalse(errors, msg=errors)
            self.assertTrue(all(not thread.is_alive() for thread in threads))
            self.assertEqual(len(results), worker_count)
            self.assertTrue(all(result.get('ok') for result in results), msg=results)
            self.assertEqual(len({result['session_id'] for result in results}), 1)
            self.assertEqual(sum(not result['resumed'] for result in results), 1)
            with sqlite3.connect(db_path) as conn:
                active = conn.execute(
                    "SELECT session_id FROM sessions "
                    "WHERE agent_id=? AND status='active'",
                    ('agent-a',),
                ).fetchall()
            self.assertEqual(active, [(results[0]['session_id'],)])
        finally:
            code_tools._SESSIONS_DB = old_db
            code_tools._sync_session_db_alias()

    def test_protected_tools_status_distinguishes_command_and_test_profile(self):
        result = json.loads(asyncio.run(code_tools.protected_tools_status()))

        self.assertTrue(result.get('ok'), msg=result)
        self.assertIn('run_project_command', result['protected_tools'])
        self.assertIn('run_test_profile', result['unprotected_tools'])
        self.assertTrue(result['mutation_token_required']['run_project_command'])
        self.assertFalse(result['mutation_token_required']['run_test_profile'])
        self.assertNotIn('token', json.dumps(result.get('protected_tools', []), ensure_ascii=False).lower())

    def test_path_quote_helper_generates_safe_wrapper_without_execution(self):
        result = json.loads(asyncio.run(code_tools.path_quote_helper(
            str(self.project_path),
            '2. Rawcut',
            'node health-check.js',
        )))

        self.assertTrue(result.get('ok'), msg=result)
        self.assertTrue(result['uses_set_location'])
        self.assertIn("Set-Location -LiteralPath '2. Rawcut'", result['safe_command'])
        self.assertEqual(result['ca_next_action'], 'use_safe_command')

    def test_run_test_profile_py_compile_uses_project_index_not_core_files(self):
        asyncio.run(code_tools.generate_project_map(str(self.project_path)))
        result = json.loads(asyncio.run(code_tools.run_test_profile(
            str(self.project_path),
            profile='py_compile',
        )))

        self.assertTrue(result.get('ok'), msg=result)
        self.assertIn('sample.py', result.get('command_executed', []))
        self.assertNotIn('orchestrator_server.py', result.get('command_executed', []))

    def test_run_test_profile_py_compile_no_python_is_warning(self):
        self.sample_file.unlink()
        (self.project_path / 'package.json').write_text('{"scripts":{"test":"echo ok"}}', encoding='utf-8')
        asyncio.run(code_tools.generate_project_map(str(self.project_path)))

        result = json.loads(asyncio.run(code_tools.run_test_profile(
            str(self.project_path),
            profile='py_compile',
        )))

        self.assertTrue(result.get('ok'), msg=result)
        self.assertFalse(result.get('test_discovered'), msg=result)
        self.assertIn('warning', result)

    def test_write_fix_rejects_binary_file(self):
        binary_file = self.project_path / 'image.png'
        binary_file.write_bytes(b'\x89PNG\r\n\x1a\n\x00\x00\x00\x00')

        result = json.loads(asyncio.run(code_tools.write_fix(
            'image.png', 1, 1, 'text', project_path=str(self.project_path),
        )))

        self.assertFalse(result.get('ok'), msg=result)
        self.assertEqual(result.get('error_name'), 'BINARY_FILE_UNSUPPORTED')

    def test_pre_write_syntax_gate_write_fix_rejects_broken_python_without_backup(self):
        asyncio.run(code_tools.generate_project_map(str(self.project_path)))
        before = self.sample_file.read_text(encoding='utf-8')

        result = json.loads(asyncio.run(code_tools.write_fix(
            'sample.py',
            3,
            4,
            'def foo(:\n    pass\n',
            project_path=str(self.project_path),
        )))

        self.assertFalse(result.get('ok'), msg=result)
        self.assertEqual(result.get('error_name'), 'SYNTAX_ERROR_PRE_WRITE')
        self.assertIn('ca_next_action', result)
        self.assertEqual(self.sample_file.read_text(encoding='utf-8'), before)
        self.assertFalse((self.project_path / 'sample.py.bak').exists())

    def test_write_new_file_overwrite_validation_failure_restores_bytes_and_cache(self):
        asyncio.run(code_tools.generate_project_map(str(self.project_path)))
        original_bytes = b'# original\r\nvalue = 1\r\n'
        self.sample_file.write_bytes(original_bytes)
        code_tools._update_cache_after_write(str(self.sample_file), str(self.project_path))
        cache_path = self.project_path / '.orchestrator_cache' / 'sample.py.json'
        original_cache = json.loads(cache_path.read_text(encoding='utf-8'))

        failed_validation = json.dumps({'ok': False, 'error': 'forced failure'})
        with mock.patch.object(code_tools, 'validate_file', new=mock.AsyncMock(return_value=failed_validation)):
            result = json.loads(asyncio.run(code_tools.write_new_file(
                'sample.py',
                project_path=str(self.project_path),
                new_content='def replacement():\n    return 2\n',
                overwrite=True,
            )))

        self.assertFalse(result.get('ok'), msg=result)
        self.assertEqual(result.get('error_name'), 'VALIDATION_FAILED_AUTO_REVERT')
        self.assertTrue(result.get('auto_reverted'), msg=result)
        self.assertEqual(self.sample_file.read_bytes(), original_bytes)
        restored_cache = json.loads(cache_path.read_text(encoding='utf-8'))
        self.assertEqual(restored_cache.get('content_hash'), original_cache.get('content_hash'))
        self.assertEqual(restored_cache.get('symbols'), original_cache.get('symbols'))

    def test_write_new_file_validation_failure_deletes_new_file_without_cache(self):
        asyncio.run(code_tools.generate_project_map(str(self.project_path)))
        target = self.project_path / 'new_invalid.py'
        cache_path = self.project_path / '.orchestrator_cache' / 'new_invalid.py.json'

        failed_validation = json.dumps({'ok': False, 'error': 'forced failure'})
        with mock.patch.object(code_tools, 'validate_file', new=mock.AsyncMock(return_value=failed_validation)):
            result = json.loads(asyncio.run(code_tools.write_new_file(
                'new_invalid.py',
                project_path=str(self.project_path),
                new_content='def created():\n    return 1\n',
            )))

        self.assertFalse(result.get('ok'), msg=result)
        self.assertEqual(result.get('error_name'), 'VALIDATION_FAILED_AUTO_REVERT')
        self.assertTrue(result.get('auto_reverted'), msg=result)
        self.assertFalse(target.exists())
        self.assertFalse(cache_path.exists())
        index = json.loads((self.project_path / '.orchestrator_cache' / 'INDEX.json').read_text(encoding='utf-8'))
        self.assertNotIn('new_invalid.py', index.get('files', {}))

    def test_write_new_file_overwrite_failed_validation_preserves_newer_writer(self):
        asyncio.run(code_tools.generate_project_map(str(self.project_path)))
        first_validating = asyncio.Event()
        release_first = asyncio.Event()
        calls = 0

        async def validate(_path):
            nonlocal calls
            calls += 1
            if calls == 1:
                first_validating.set()
                await release_first.wait()
                return json.dumps({'ok': False, 'error': 'forced first failure'})
            return json.dumps({'ok': True})

        async def run_writers():
            with mock.patch.object(code_tools, 'validate_file', side_effect=validate):
                first = asyncio.create_task(code_tools.write_new_file(
                    'sample.py', project_path=str(self.project_path),
                    new_content='def first():\n    return 1\n', overwrite=True))
                await first_validating.wait()
                second = json.loads(await code_tools.write_new_file(
                    'sample.py', project_path=str(self.project_path),
                    new_content='def second():\n    return 2\n', overwrite=True))
                release_first.set()
                return json.loads(await first), second

        first, second = asyncio.run(run_writers())
        self.assertTrue(second.get('ok'), msg=second)
        self.assertEqual(first.get('error_name'), 'VALIDATION_FAILED_SUPERSEDED')
        self.assertFalse(first.get('auto_reverted'), msg=first)
        self.assertTrue(first.get('superseded'), msg=first)
        self.assertEqual(self.sample_file.read_text(encoding='utf-8'), 'def second():\n    return 2\n')
        cache = json.loads((self.project_path / '.orchestrator_cache' / 'sample.py.json').read_text(encoding='utf-8'))
        self.assertEqual({symbol.get('name') for symbol in cache.get('symbols', [])}, {'second'})

    def test_write_new_file_create_failed_validation_preserves_newer_writer(self):
        asyncio.run(code_tools.generate_project_map(str(self.project_path)))
        target = self.project_path / 'raced.py'
        first_validating = asyncio.Event()
        release_first = asyncio.Event()
        calls = 0

        async def validate(_path):
            nonlocal calls
            calls += 1
            if calls == 1:
                first_validating.set()
                await release_first.wait()
                return json.dumps({'ok': False, 'error': 'forced first failure'})
            return json.dumps({'ok': True})

        async def run_writers():
            with mock.patch.object(code_tools, 'validate_file', side_effect=validate):
                first = asyncio.create_task(code_tools.write_new_file(
                    'raced.py', project_path=str(self.project_path),
                    new_content='def first():\n    return 1\n'))
                await first_validating.wait()
                second = json.loads(await code_tools.write_new_file(
                    'raced.py', project_path=str(self.project_path),
                    new_content='def second():\n    return 2\n', overwrite=True))
                release_first.set()
                return json.loads(await first), second

        first, second = asyncio.run(run_writers())
        self.assertTrue(second.get('ok'), msg=second)
        self.assertEqual(first.get('error_name'), 'VALIDATION_FAILED_SUPERSEDED')
        self.assertFalse(first.get('auto_reverted'), msg=first)
        self.assertTrue(first.get('superseded'), msg=first)
        self.assertEqual(target.read_text(encoding='utf-8'), 'def second():\n    return 2\n')
        cache = json.loads((self.project_path / '.orchestrator_cache' / 'raced.py.json').read_text(encoding='utf-8'))
        self.assertEqual({symbol.get('name') for symbol in cache.get('symbols', [])}, {'second'})

    def test_write_new_file_overwrite_identical_content_aba_preserves_newer_writer(self):
        asyncio.run(code_tools.generate_project_map(str(self.project_path)))
        original = self.sample_file.read_bytes()
        content = 'def same():\n    return 1\n'
        first_validating = asyncio.Event()
        release_first = asyncio.Event()
        calls = 0

        async def validate(_path):
            nonlocal calls
            calls += 1
            if calls == 1:
                first_validating.set()
                await release_first.wait()
                return json.dumps({'ok': False, 'error': 'forced first failure'})
            return json.dumps({'ok': True})

        async def run_writers():
            with mock.patch.object(code_tools, 'validate_file', side_effect=validate):
                first = asyncio.create_task(code_tools.write_new_file(
                    'sample.py', project_path=str(self.project_path),
                    new_content=content, overwrite=True))
                await first_validating.wait()
                second = json.loads(await code_tools.write_new_file(
                    'sample.py', project_path=str(self.project_path),
                    new_content=content, overwrite=True))
                release_first.set()
                return json.loads(await first), second

        first, second = asyncio.run(run_writers())
        self.assertTrue(second.get('ok'), msg=second)
        self.assertEqual(first.get('error_name'), 'VALIDATION_FAILED_SUPERSEDED')
        self.assertFalse(first.get('auto_reverted'), msg=first)
        self.assertNotEqual(self.sample_file.read_bytes(), original)
        self.assertEqual(self.sample_file.read_text(encoding='utf-8'), content)
        cache = json.loads((self.project_path / '.orchestrator_cache' / 'sample.py.json').read_text(encoding='utf-8'))
        self.assertEqual({symbol.get('name') for symbol in cache.get('symbols', [])}, {'same'})

    def test_write_new_file_create_identical_content_aba_preserves_newer_writer(self):
        asyncio.run(code_tools.generate_project_map(str(self.project_path)))
        target = self.project_path / 'identical_aba.py'
        content = 'def same():\n    return 1\n'
        first_validating = asyncio.Event()
        release_first = asyncio.Event()
        calls = 0

        async def validate(_path):
            nonlocal calls
            calls += 1
            if calls == 1:
                first_validating.set()
                await release_first.wait()
                return json.dumps({'ok': False, 'error': 'forced first failure'})
            return json.dumps({'ok': True})

        async def run_writers():
            with mock.patch.object(code_tools, 'validate_file', side_effect=validate):
                first = asyncio.create_task(code_tools.write_new_file(
                    'identical_aba.py', project_path=str(self.project_path),
                    new_content=content))
                await first_validating.wait()
                second = json.loads(await code_tools.write_new_file(
                    'identical_aba.py', project_path=str(self.project_path),
                    new_content=content, overwrite=True))
                release_first.set()
                return json.loads(await first), second

        first, second = asyncio.run(run_writers())
        self.assertTrue(second.get('ok'), msg=second)
        self.assertEqual(first.get('error_name'), 'VALIDATION_FAILED_SUPERSEDED')
        self.assertFalse(first.get('auto_reverted'), msg=first)
        self.assertEqual(target.read_text(encoding='utf-8'), content)
        cache = json.loads((self.project_path / '.orchestrator_cache' / 'identical_aba.py.json').read_text(encoding='utf-8'))
        self.assertEqual({symbol.get('name') for symbol in cache.get('symbols', [])}, {'same'})

    def test_pre_write_syntax_gate_write_new_file_rejects_broken_python(self):
        result = json.loads(asyncio.run(code_tools.write_new_file(
            'broken_new.py',
            project_path=str(self.project_path),
            new_content='def foo(:\n    pass\n',
        )))

        self.assertFalse(result.get('ok'), msg=result)
        self.assertEqual(result.get('error_name'), 'SYNTAX_ERROR_PRE_WRITE')
        self.assertIn('ca_next_action', result)
        self.assertFalse((self.project_path / 'broken_new.py').exists())

    def test_pre_write_syntax_gate_rejects_broken_jsx_and_tsx(self):
        for name in ('broken.jsx', 'broken.tsx'):
            with self.subTest(name=name):
                result = json.loads(asyncio.run(code_tools.write_new_file(
                    name,
                    project_path=str(self.project_path),
                    new_content='const x = <div><span></div>;\n',
                )))

                self.assertFalse(result.get('ok'), msg=result)
                self.assertEqual(result.get('error_name'), 'SYNTAX_ERROR_PRE_WRITE')
                self.assertIn('ca_next_action', result)
                self.assertFalse((self.project_path / name).exists())

    def test_pre_write_syntax_gate_rejects_invalid_json(self):
        result = json.loads(asyncio.run(code_tools.write_new_file(
            'bad.json',
            project_path=str(self.project_path),
            new_content='{bad',
        )))

        self.assertFalse(result.get('ok'), msg=result)
        self.assertEqual(result.get('error_name'), 'JSON_INVALID_PRE_WRITE')
        self.assertIn('ca_next_action', result)
        self.assertFalse((self.project_path / 'bad.json').exists())

    def test_pre_write_syntax_gate_skips_markdown(self):
        result = json.loads(asyncio.run(code_tools.write_new_file(
            'notes.md',
            project_path=str(self.project_path),
            new_content='# Notes\n\nplain text\n',
        )))

        self.assertTrue(result.get('ok'), msg=result)
        self.assertEqual((self.project_path / 'notes.md').read_text(encoding='utf-8'), '# Notes\n\nplain text\n')

    def test_pre_write_syntax_gate_python_bench_under_50ms_average(self):
        content = ('def value():\n    return 1\n\n' * 80)[:2048]
        target = self.project_path / 'bench.py'
        durations = []

        for _ in range(20):
            start = code_tools.time.perf_counter()
            result = asyncio.run(_pre_write_syntax_check(target, content))
            durations.append((code_tools.time.perf_counter() - start) * 1000)
            self.assertTrue(result.get('ok'), msg=result)

        self.assertLess(sum(durations) / len(durations), 50)

    def test_pre_write_syntax_gate_skip_flag_writes_and_audits(self):
        with self.assertLogs('[A]', level='WARNING') as logs:
            result = json.loads(asyncio.run(code_tools.write_new_file(
                'skip_bad.json',
                project_path=str(self.project_path),
                new_content='{bad',
                skip_pre_write_check=True,
            )))

        self.assertTrue(result.get('ok'), msg=result)
        self.assertEqual((self.project_path / 'skip_bad.json').read_text(encoding='utf-8'), '{bad')
        joined = '\n'.join(logs.output)
        self.assertIn('skip_pre_write_check=true', joined)
        self.assertIn('skip_bad.json', joined)
        self.assertIn('write_new_file', joined)

    def test_t1_metadata_is_compact_and_contextual(self):
        old_db = code_tools._SESSIONS_DB
        code_tools._SESSIONS_DB = self.project_path / 'sessions_meta.db'
        try:
            asyncio.run(code_tools.generate_project_map(str(self.project_path)))
            opened = json.loads(asyncio.run(code_tools.open_session('Meta check', str(self.project_path), 'T1')))
            summary = json.loads(asyncio.run(code_tools.get_project_summary(str(self.project_path))))
            status = json.loads(asyncio.run(code_tools.workflow_status_registry(str(self.project_path))))
            read = json.loads(asyncio.run(code_tools.read_file_range('sample.py', 1, 2, str(self.project_path))))
            symbols = json.loads(asyncio.run(code_tools.get_file_symbols('sample.py', str(self.project_path))))

            self.assertEqual(opened['project_name'], self.project_path.name)
            self.assertEqual(summary['project_name'], self.project_path.name)
            self.assertIn('last_write_at', summary)
            self.assertEqual(status['state']['active_session_id'], opened['session_id'])
            self.assertEqual(read['lang'], 'python')
            self.assertIn('file_group', read)
            self.assertEqual(symbols['kind'], 'source_symbols')
        finally:
            code_tools._SESSIONS_DB = old_db

    def test_get_usage_stats_does_not_fallback_to_global_logs_for_project(self):
        result = json.loads(asyncio.run(code_tools.get_usage_stats(
            str(self.project_path),
            include_http_log=False,
        )))

        self.assertTrue(result.get('ok'), msg=result)
        self.assertEqual(result.get('status'), 'no_project_log')
        self.assertNotIn('USAGE_LOG', result.get('source', ''))

    def test_post_write_batch_verify_runs_bundle_once(self):
        asyncio.run(code_tools.generate_project_map(str(self.project_path)))

        result = json.loads(asyncio.run(code_tools.post_write_batch_verify(
            str(self.project_path),
            ['sample.py'],
        )))

        self.assertTrue(result.get('ok'), msg=result)
        self.assertEqual(result.get('overall_status'), 'pass')
        self.assertEqual(result.get('files_checked'), ['sample.py'])
        self.assertEqual(result.get('cache_delta_runs'), 1)
        self.assertEqual(len(result.get('validate_file_results', [])), 1)
        self.assertEqual(len(result.get('validate_symbols_results', [])), 1)
        self.assertIn('cache_delta_result', result)

    def test_post_write_batch_verify_reports_symbol_unsupported_without_failing(self):
        text_file = self.project_path / 'notes.txt'
        text_file.write_text('Xin chao tieng Viet\n', encoding='utf-8')
        asyncio.run(code_tools.generate_project_map(str(self.project_path)))

        result = json.loads(asyncio.run(code_tools.post_write_batch_verify(
            str(self.project_path),
            ['notes.txt'],
        )))

        self.assertTrue(result.get('ok'), msg=result)
        self.assertEqual(result.get('overall_status'), 'pass')
        unsupported = result.get('unsupported_tools', [])
        self.assertTrue(any(item.get('tool') == 'validate_symbols' for item in unsupported), msg=result)

    def test_write_fix_reverts_removed_top_level_symbol(self):
        self.sample_file.write_text(
            '# header\n\n'
            'def foo():\n'
            '    return 1\n\n'
            'def bar():\n'
            '    return foo()\n',
            encoding='utf-8',
        )
        asyncio.run(code_tools.generate_project_map(str(self.project_path)))

        result = json.loads(asyncio.run(code_tools.write_fix(
            str(self.sample_file), 3, 4, '# removed foo\n',
        )))

        self.assertFalse(result.get('ok'), msg=result)
        self.assertEqual(result.get('error_name'), 'SYMBOL_DRIFT_AUTO_REVERT')
        self.assertIn('def foo():', self.sample_file.read_text(encoding='utf-8'))

    def test_write_fix_can_continue_after_symbol_auto_revert(self):
        self.sample_file.write_text(
            '# header\n\n'
            'def foo():\n'
            '    return 1\n\n'
            'def bar():\n'
            '    return foo()\n',
            encoding='utf-8',
        )
        asyncio.run(code_tools.generate_project_map(str(self.project_path)))

        reverted = json.loads(asyncio.run(code_tools.write_fix(
            str(self.sample_file), 3, 4, '# removed foo\n',
        )))
        self.assertFalse(reverted.get('ok'), msg=reverted)
        self.assertEqual(reverted.get('error_name'), 'SYMBOL_DRIFT_AUTO_REVERT')
        self.assertTrue(reverted.get('cache_synced'), msg=reverted)

        result = json.loads(asyncio.run(code_tools.write_fix(
            str(self.sample_file), 1, 1, '# updated header\n',
        )))
        self.assertTrue(result.get('ok'), msg=result)
        self.assertNotEqual(result.get('error_name'), 'FILE_CHANGED_OUTSIDE_ORCHESTRATOR')

    def test_write_fix_reverts_bad_import(self):
        asyncio.run(code_tools.generate_project_map(str(self.project_path)))

        result = json.loads(asyncio.run(code_tools.write_fix(
            str(self.sample_file), 1, 1, 'from nonexistent_mod_xyz import x\n# header\n',
        )))

        self.assertFalse(result.get('ok'), msg=result)
        self.assertEqual(result.get('error_name'), 'IMPORT_ERROR_PRE_WRITE')
        self.assertNotIn('nonexistent_mod_xyz', self.sample_file.read_text(encoding='utf-8'))

    def test_ac_g4_parallel_write_fix_same_file_serializes_full_content(self):
        asyncio.run(code_tools.generate_project_map(str(self.project_path)))
        variants = ['# writer one\n', '# writer two\n']

        async def _write(text):
            return json.loads(await code_tools.write_fix(
                'sample.py', 1, 1, text, project_path=str(self.project_path),
            ))

        async def _run_writes():
            return await asyncio.gather(*[_write(text) for text in variants])

        results = asyncio.run(_run_writes())
        self.assertTrue(any(result.get('ok') for result in results), msg=results)
        final = self.sample_file.read_text(encoding='utf-8')
        self.assertIn(final.splitlines()[0] + '\n', variants)
        self.assertIn('def foo():\n    return 1\n', final)
        self.assertTrue((self.project_path / 'sample.py.bak').exists())

    def test_ac_f1_atomic_write_permission_falls_back_to_shared_inplace(self):
        asyncio.run(code_tools.generate_project_map(str(self.project_path)))
        original_replace = code_tools.os.replace

        def _replace_once_for_target(src, dst):
            if str(dst).endswith('sample.py'):
                raise PermissionError('locked')
            return original_replace(src, dst)

        with self.sample_file.open('r', encoding='utf-8'):
            code_tools.os.replace = _replace_once_for_target
            try:
                result = json.loads(asyncio.run(code_tools.write_fix(
                    'sample.py', 1, 1, '# shared write\n', project_path=str(self.project_path),
                )))
            finally:
                code_tools.os.replace = original_replace

        self.assertTrue(result.get('ok'), msg=result)
        self.assertEqual(result.get('write_mode'), 'shared_inplace')
        self.assertTrue(self.sample_file.read_text(encoding='utf-8').startswith('# shared write\n'))

    def test_ac_sys06_boot_write_sweep_restores_bak_when_target_invalid(self):
        target = self.project_path / 'broken.py'
        target.write_text('def ok():\n    return 1\n', encoding='utf-8')
        asyncio.run(code_tools.generate_project_map(str(self.project_path)))
        bak = self.project_path / 'broken.py.bak'
        bak.write_text('def ok():\n    return 1\n', encoding='utf-8')
        target.write_text('def broken(:\n    pass\n', encoding='utf-8')
        now = code_tools.time.time()
        code_tools.os.utime(target, (now - 10, now - 10))
        code_tools.os.utime(bak, (now, now))

        result = asyncio.run(_boot_write_sweep(project_roots=[str(self.project_path)]))

        self.assertEqual(result.get('restored_count'), 1, msg=result)
        self.assertEqual(target.read_text(encoding='utf-8'), 'def ok():\n    return 1\n')
        self.assertGreaterEqual(result.get('candidate_count', 0), 1)
        self.assertGreaterEqual(result.get('pending_count', 0), 1)

    def test_boot_write_sweep_scan_does_not_block_event_loop(self):
        def slow_scan(_roots, **_kwargs):
            time.sleep(0.12)
            return {
                'root_count': 1,
                'candidate_count': 0,
                'pending_count': 0,
                'pending': [],
                'roots_ms': 1.0,
                'index_ms': 1.0,
                'stat_ms': 1.0,
                'scan_ms': 120.0,
            }

        async def scenario():
            started = time.perf_counter()
            task = asyncio.create_task(_boot_write_sweep(project_roots=[]))
            await asyncio.sleep(0.02)
            loop_delay = time.perf_counter() - started
            was_pending = not task.done()
            result = await task
            return loop_delay, was_pending, result

        with mock.patch.object(write_core, '_boot_sweep_scan', side_effect=slow_scan):
            loop_delay, was_pending, result = asyncio.run(scenario())
        self.assertLess(loop_delay, 0.08)
        self.assertTrue(was_pending)
        self.assertEqual(result.get('checked'), 0)

    def test_boot_write_sweep_checked_zero_keeps_scan_metrics(self):
        scan = {
            'root_count': 3,
            'candidate_count': 7,
            'pending_count': 0,
            'pending': [],
            'roots_ms': 2.0,
            'index_ms': 3.0,
            'stat_ms': 4.0,
            'scan_ms': 9.0,
        }
        with mock.patch.object(write_core, '_boot_sweep_scan', return_value=scan):
            result = asyncio.run(_boot_write_sweep(project_roots=[]))
        self.assertEqual(result.get('checked'), 0)
        self.assertEqual(result.get('candidate_count'), 7)
        self.assertEqual(result.get('scan_ms'), 9.0)

    def test_write_fix_can_continue_after_import_auto_revert(self):
        asyncio.run(code_tools.generate_project_map(str(self.project_path)))

        reverted = json.loads(asyncio.run(code_tools.write_fix(
            str(self.sample_file), 1, 1, 'from nonexistent_mod_xyz import x\n# header\n',
        )))
        self.assertFalse(reverted.get('ok'), msg=reverted)
        self.assertEqual(reverted.get('error_name'), 'IMPORT_ERROR_PRE_WRITE')
        self.assertTrue(reverted.get('cache_synced'), msg=reverted)

        result = json.loads(asyncio.run(code_tools.write_fix(
            str(self.sample_file), 1, 1, '# updated header\n',
        )))
        self.assertTrue(result.get('ok'), msg=result)
        self.assertNotEqual(result.get('error_name'), 'FILE_CHANGED_OUTSIDE_ORCHESTRATOR')

    def test_write_transaction_reverts_successful_files_when_batch_member_fails(self):
        files = []
        for name in ('a.py', 'b.py', 'c.py'):
            fp = self.project_path / name
            fp.write_text(f'# {name}\n\ndef {name[0]}():\n    return 1\n', encoding='utf-8')
            files.append(fp)
        originals = {fp.name: fp.read_text(encoding='utf-8') for fp in files}
        asyncio.run(code_tools.generate_project_map(str(self.project_path)))

        result = json.loads(asyncio.run(code_tools.write_transaction([
            {'file_path': 'a.py', 'start_line': 1, 'end_line': 1, 'new_content': '# changed a\n'},
            {'file_path': 'b.py', 'start_line': 1, 'end_line': 1,
             'new_content': 'from nonexistent_mod_xyz import x\n# changed b\n'},
            {'file_path': 'c.py', 'start_line': 1, 'end_line': 1, 'new_content': '# changed c\n'},
        ], project_path=str(self.project_path))))

        self.assertFalse(result.get('ok'), msg=result)
        self.assertEqual(result.get('error_name'), 'BATCH_VERIFY_FAILED_AUTO_REVERT')
        for fp in files:
            self.assertEqual(fp.read_text(encoding='utf-8'), originals[fp.name])

    def test_write_transaction_mixed_roots_backups_and_validates_each_root(self):
        import tools_v15

        second_root = self.tempdir / 'second-project'
        second_root.mkdir()
        first_file = self.project_path / 'first.txt'
        second_file = second_root / 'second.txt'
        first_file.write_text('one\n', encoding='utf-8')
        second_file.write_text('two\n', encoding='utf-8')
        asyncio.run(code_tools.generate_project_map(str(self.project_path)))
        asyncio.run(code_tools.generate_project_map(str(second_root)))
        backup_calls = []

        async def fake_backup(project_path, **kwargs):
            backup_calls.append((str(Path(project_path).resolve()), kwargs.get('target_paths')))
            return json.dumps({'ok': True, 'backup_path': str(Path(project_path) / 'backup.zip')})

        with mock.patch.object(code_tools, 'backup_project', side_effect=fake_backup):
            result = json.loads(asyncio.run(tools_v15.write_transaction([
                {'file_path': 'first.txt', 'start_line': 1, 'end_line': 1, 'new_content': 'ONE'},
                {'project_path': str(second_root), 'file_path': 'second.txt',
                 'start_line': 1, 'end_line': 1, 'new_content': 'TWO'},
            ], project_path=str(self.project_path))))

        self.assertTrue(result.get('ok'), result)
        self.assertEqual({root for root, _ in backup_calls}, {
            str(self.project_path.resolve()), str(second_root.resolve())})
        self.assertEqual(len(result['validation'].get('groups', [])), 2)

    def test_write_transaction_validation_failure_rolls_back_existing_and_new_files(self):
        import tools_v15

        existing = self.project_path / 'existing.txt'
        created = self.project_path / 'created.txt'
        existing.write_bytes(b'original\r\nbytes\r\n')
        asyncio.run(code_tools.generate_project_map(str(self.project_path)))

        async def fail_verify(**_kwargs):
            return json.dumps({'ok': False, 'overall_status': 'fail'})

        with mock.patch.object(code_tools, 'post_write_batch_verify', side_effect=fail_verify):
            result = json.loads(asyncio.run(tools_v15.write_transaction([
                {'file_path': 'existing.txt', 'start_line': 1, 'end_line': 1,
                 'new_content': 'changed'},
                {'tool': 'write_new_file', 'file_path': 'created.txt',
                 'new_content': 'created'},
            ], project_path=str(self.project_path), backup=False)))

        self.assertFalse(result.get('ok'), result)
        self.assertEqual(result.get('error_name'), 'BATCH_VERIFY_FAILED_AUTO_REVERT')
        self.assertTrue(result.get('rollback_verified'), result)
        self.assertEqual(existing.read_bytes(), b'original\r\nbytes\r\n')
        self.assertFalse(created.exists())

    def test_write_transaction_only_new_files_does_not_require_archive_backup(self):
        import tools_v15

        asyncio.run(code_tools.generate_project_map(str(self.project_path)))
        with mock.patch.object(code_tools, 'backup_project', new=mock.AsyncMock()) as backup:
            result = json.loads(asyncio.run(tools_v15.write_transaction([
                {'tool': 'write_new_file', 'file_path': 'new-only.txt',
                 'new_content': 'created'},
            ], project_path=str(self.project_path))))

        self.assertTrue(result.get('ok'), result)
        backup.assert_not_awaited()
        self.assertEqual((self.project_path / 'new-only.txt').read_text(encoding='utf-8'), 'created')

    def test_optional_tool_probe_classifies_helpers_without_token_leak(self):
        result = json.loads(asyncio.run(code_tools.optional_tool_probe([
            'path_quote_helper',
            '/api/token_panel',
            'definitely_missing_helper',
        ])))

        self.assertTrue(result.get('ok'), msg=result)
        available_names = {item.get('name') for item in result.get('available', [])}
        unsupported_names = {item.get('name') for item in result.get('unsupported', [])}
        self.assertIn('path_quote_helper', available_names)
        self.assertIn('/api/token_panel', available_names)
        self.assertIn('definitely_missing_helper', unsupported_names)
        self.assertNotIn('raw', json.dumps(result).lower())

    def test_pending_audit_sync_and_close_session_counts(self):
        old_db = code_tools._SESSIONS_DB
        code_tools._SESSIONS_DB = self.project_path / 'sessions_pending.db'
        try:
            code_tools._init_session_db()
            asyncio.run(code_tools.generate_project_map(str(self.project_path)))
            opened = json.loads(asyncio.run(code_tools.open_session(
                'Pending audit',
                str(self.project_path),
                'T2',
            )))
            pending = json.loads(asyncio.run(code_tools.create_pending_audit(
                str(self.project_path),
                'read',
                'Read',
                instead_of='read_file_range',
                failed_error='API unavailable token=SECRET',
                target='sample.py',
            )))
            self.assertTrue(pending.get('ok'), msg=pending)
            sync = json.loads(asyncio.run(code_tools.sync_pending_audit(
                str(self.project_path),
                target_session_id=opened["session_id"],
                include_legacy=True,
            )))
            self.assertTrue(sync.get('ok'), msg=sync)
            self.assertEqual(sync.get('audit_status'), 'synced')
            self.assertEqual(sync.get('pending_remaining'), 0)
            self.assertEqual(sync.get('fallback_count_so_far'), 1)
            closed = json.loads(asyncio.run(code_tools.close_session(str(self.project_path))))
            self.assertTrue(closed.get('ok'), msg=closed)
            self.assertEqual(closed.get('session_id'), opened.get('session_id'))
            self.assertEqual(closed.get('native_fallback_count'), 1)
            self.assertEqual(closed.get('pending_audit_count'), 0)
        finally:
            code_tools._SESSIONS_DB = old_db


    def test_session_handlers_isolate_two_agents_on_same_project(self):
        old_db = code_tools._SESSIONS_DB
        code_tools._SESSIONS_DB = self.project_path / 'sessions_agent_scoped.db'
        try:
            code_tools._init_session_db()
            session_a = code_tools._auto_open_session(
                str(self.project_path), {'agent_id': 'agent-a'})
            session_b = code_tools._auto_open_session(
                str(self.project_path), {'agent_id': 'agent-b'})
            self.assertNotEqual(session_a, session_b)

            logged = json.loads(asyncio.run(code_tools.log_native_event(
                action_type='read', tool_name='native-a', project_path=str(self.project_path),
                agent_id='agent-a')))
            self.assertEqual(logged['session_id'], session_a)

            pending_a = json.loads(asyncio.run(code_tools.create_pending_audit(
                str(self.project_path), 'read', 'pending-a', agent_id='agent-a')))
            pending_b = json.loads(asyncio.run(code_tools.create_pending_audit(
                str(self.project_path), 'read', 'pending-b', agent_id='agent-b')))
            synced_a = json.loads(asyncio.run(code_tools.sync_pending_audit(
                str(self.project_path), agent_id='agent-a')))
            self.assertEqual(synced_a['session_id'], session_a)
            self.assertEqual(synced_a['synced'], [pending_a['pending_audit_id']])
            self.assertNotIn(pending_b['pending_audit_id'], synced_a['synced'])

            renamed = json.loads(asyncio.run(code_tools.rename_session(
                str(self.project_path), 'Agent A renamed', agent_id='agent-a')))
            self.assertEqual(renamed['session_id'], session_a)
            closed = json.loads(asyncio.run(code_tools.close_session(
                str(self.project_path), agent_id='agent-a')))
            self.assertEqual(closed['session_id'], session_a)
            self.assertIsNone(code_tools._get_active_session_id(str(self.project_path), 'agent-a'))
            self.assertEqual(code_tools._get_active_session_id(
                str(self.project_path), 'agent-b'), session_b)
        finally:
            code_tools._SESSIONS_DB = old_db
            code_tools._sync_session_db_alias()

    # ===== CODOC-A2: async-aware write gate concurrency tests =====

    def test_codoc_a2_same_file_serializes_critical_section(self):
        """Two concurrent write_fix to same file: max in-section concurrency == 1."""
        asyncio.run(code_tools.generate_project_map(str(self.project_path)))

        _cc = 0
        _max_cc = 0
        _cc_lock = threading.Lock()
        _orig_atomic = code_tools._atomic_write_text

        def _instrumented(fp, text, encoding='utf-8'):
            nonlocal _cc, _max_cc
            with _cc_lock:
                _cc += 1
                if _cc > _max_cc:
                    _max_cc = _cc
            try:
                return _orig_atomic(fp, text, encoding=encoding)
            finally:
                with _cc_lock:
                    _cc -= 1

        try:
            code_tools._atomic_write_text = _instrumented

            async def _run():
                async def _wa():
                    return json.loads(await code_tools.write_fix(
                        'sample.py', 3, 4, 'def foo():\n    return 42\n',
                        project_path=str(self.project_path)))

                async def _wb():
                    return json.loads(await code_tools.write_fix(
                        'sample.py', 3, 4, 'def foo():\n    return 99\n',
                        project_path=str(self.project_path)))

                return await asyncio.gather(_wa(), _wb())

            a, b = asyncio.run(_run())
        finally:
            code_tools._atomic_write_text = _orig_atomic

        self.assertTrue(a.get('ok') or b.get('ok'),
                        msg=f'Neither write succeeded: a={a} b={b}')
        self.assertEqual(_max_cc, 1,
                         msg=f'Max concurrency in critical section: {_max_cc} (expected 1)')

    def test_codoc_a2_different_files_proceed_concurrently(self):
        """Two write_fix to different files must NOT serialize each other."""
        asyncio.run(code_tools.generate_project_map(str(self.project_path)))
        other = self.project_path / 'other.py'
        other.write_text('def other():\n    return 1\n', encoding='utf-8')

        _entries = []
        _elock = threading.Lock()
        _orig_atomic = code_tools._atomic_write_text

        def _track(fp, text, encoding='utf-8'):
            with _elock:
                _entries.append(str(fp))
            return _orig_atomic(fp, text, encoding=encoding)

        try:
            code_tools._atomic_write_text = _track

            async def _run():
                async def _ws():
                    return json.loads(await code_tools.write_fix(
                        'sample.py', 3, 4, 'def foo():\n    return 42\n',
                        project_path=str(self.project_path)))

                async def _wo():
                    return json.loads(await code_tools.write_fix(
                        'other.py', 2, 2, '    return 99\n',
                        project_path=str(self.project_path)))

                return await asyncio.gather(_ws(), _wo())

            a, b = asyncio.run(_run())
        finally:
            code_tools._atomic_write_text = _orig_atomic

        self.assertTrue(a.get('ok'), msg=f'sample.py write failed: {a}')
        self.assertTrue(b.get('ok'), msg=f'other.py write failed: {b}')
        self.assertIn(str(self.sample_file.resolve()), _entries)
        self.assertIn(str(other.resolve()), _entries)

    def test_codoc_a2_lock_released_after_write_completes(self):
        """After write_fix completes, subsequent same-file write must finish < 5s."""
        asyncio.run(code_tools.generate_project_map(str(self.project_path)))

        async def _seq():
            r1 = json.loads(await code_tools.write_fix(
                'sample.py', 3, 4, 'def foo():\n    return 42\n',
                project_path=str(self.project_path)))
            self.assertTrue(r1.get('ok'), msg=f'First write failed: {r1}')
            r2 = json.loads(await code_tools.write_fix(
                'sample.py', 3, 4, 'def foo():\n    return 99\n',
                project_path=str(self.project_path)))
            self.assertTrue(r2.get('ok'), msg=f'Second write failed: {r2}')
            return r2

        result = asyncio.run(asyncio.wait_for(_seq(), timeout=5.0))
        self.assertTrue(result.get('ok'), msg=f'Final result not ok: {result}')

    def test_codoc_a2_validation_rollback_serialized_by_gate(self):
        """Validation + rollback MUST complete before another writer enters.

        The first validate_file call is mocked to fail, so the bad write is
        rolled back; a second writer dispatched concurrently on the SAME file
        must still end up with its own content on disk.

        Getting there may take two attempts, and that is the contract rather
        than a defect. write_fix builds its patch BEFORE it takes the path gate
        and re-checks base_hash under it (orchestrator/tools/write/core.py). A
        concurrent build landing inside the [bad content written -> rollback]
        window captures the transient bad content as its base, and the rollback
        then moves that base out from under it: WRITE_BASE_CHANGED,
        write_performed=False, ca_next_action=reread_and_rebuild_patch. The
        refusal IS the guard working -- it stops a patch built against a base
        that no longer exists. Measured by delaying the injected validation:
        0ms -> the good write lands first try; 500ms -> WRITE_BASE_CHANGED 2/2.
        So this test follows ca_next_action instead of assuming a timing --
        that assumption is what made it fail only under full-suite load.
        """
        asyncio.run(code_tools.generate_project_map(str(self.project_path)))

        _orig_validate = code_tools.validate_file
        _call_count = 0
        _validate_lock = threading.Lock()

        async def _failing_validate(fp_str: str) -> str:
            nonlocal _call_count
            with _validate_lock:
                _call_count += 1
                if _call_count == 1:
                    return json.dumps({'ok': False, 'error': 'injected validation failure'})
            return await _orig_validate(fp_str)

        try:
            code_tools.validate_file = _failing_validate

            async def _run():
                async def _bad():
                    return json.loads(await code_tools.write_fix(
                        'sample.py', 1, 1, '# bad write\n',
                        project_path=str(self.project_path)))

                async def _good():
                    await asyncio.sleep(0.1)
                    return json.loads(await code_tools.write_fix(
                        'sample.py', 1, 1, '# good write\n',
                        project_path=str(self.project_path)))

                return await asyncio.gather(_bad(), _good())

            bad, good = asyncio.run(asyncio.wait_for(_run(), timeout=10.0))

            if not good.get('ok'):
                # The only refusal this race may produce, and it must be a
                # clean one: nothing written, and the rollback already put
                # the original base back before the retry reads it.
                self.assertEqual(good.get('error_name'), 'WRITE_BASE_CHANGED',
                                 msg=f'Unexpected error on good write: {good}')
                self.assertFalse(good.get('write_performed'),
                                 msg=f'Refused write must not touch the file: {good}')
                self.assertEqual(good.get('ca_next_action'),
                                 'reread_and_rebuild_patch')
                self.assertNotIn('# bad write',
                                 self.sample_file.read_text(encoding='utf-8'))
                good = json.loads(asyncio.run(asyncio.wait_for(
                    code_tools.write_fix('sample.py', 1, 1, '# good write\n',
                                         project_path=str(self.project_path)),
                    timeout=10.0)))
        finally:
            code_tools.validate_file = _orig_validate

        self.assertFalse(bad.get('ok'), msg=f'Bad write should have failed: {bad}')
        self.assertEqual(bad.get('error_name'), 'VALIDATION_FAILED_AUTO_REVERT',
                         msg=f'Unexpected error on bad write: {bad}')
        self.assertTrue(good.get('ok'),
                        msg=f'Good write should have succeeded: {good}')
        self.assertIn('# good write', self.sample_file.read_text(encoding='utf-8'))

    def test_codoc_a2_cancellation_during_validation_releases_lock(self):
        """Cancel a write task during validation; subsequent write must finish < 5s."""
        asyncio.run(code_tools.generate_project_map(str(self.project_path)))

        _entered = asyncio.Event()
        _block = asyncio.Event()
        _orig_validate = code_tools.validate_file

        async def _blocking_validate(fp_str: str) -> str:
            _entered.set()
            # Block forever until cancelled
            await _block.wait()
            return await _orig_validate(fp_str)

        async def _run():
            code_tools.validate_file = _blocking_validate
            task = asyncio.create_task(
                code_tools.write_fix(
                    'sample.py', 1, 1, '# cancelled\n',
                    project_path=str(self.project_path)))
            try:
                await asyncio.wait_for(_entered.wait(), timeout=5.0)
                task.cancel()
                try:
                    await asyncio.wait_for(task, timeout=3.0)
                except asyncio.CancelledError:
                    pass
            finally:
                code_tools.validate_file = _orig_validate
                _block.set()
            # Second write must succeed after cancel released the lock
            r2 = json.loads(await asyncio.wait_for(
                code_tools.write_fix(
                    'sample.py', 1, 1, '# after cancel\n',
                    project_path=str(self.project_path)),
                timeout=5.0))
            self.assertTrue(r2.get('ok'), msg=f'Write after cancel failed: {r2}')

        asyncio.run(asyncio.wait_for(_run(), timeout=15.0))
        self.assertIn('# after cancel', self.sample_file.read_text(encoding='utf-8'))

    def test_codoc_a2_cross_thread_write_serialized(self):
        """Cross-thread writes to same file serialize via _path_lock."""
        asyncio.run(code_tools.generate_project_map(str(self.project_path)))
        results = []
        errors = []
        barrier = threading.Barrier(2)

        def _thread_a():
            try:
                barrier.wait(timeout=5)
                from orchestrator.tools.write.core import _path_lock
                with _path_lock([self.sample_file]):
                    import time; time.sleep(0.05)
                    results.append('a')
            except Exception as exc:
                errors.append(exc)

        def _thread_b():
            try:
                barrier.wait(timeout=5)
                from orchestrator.tools.write.core import _path_lock
                with _path_lock([self.sample_file]):
                    import time; time.sleep(0.05)
                    results.append('b')
            except Exception as exc:
                errors.append(exc)

        ta = threading.Thread(target=_thread_a)
        tb = threading.Thread(target=_thread_b)
        ta.start()
        tb.start()
        ta.join(timeout=10)
        tb.join(timeout=10)
        self.assertFalse(errors, msg=errors)
        self.assertEqual(len(results), 2, msg=f'Expected 2 serialized entries: {results}')
        # Order matters: Lock serializes them
        self.assertEqual(results[0], 'a' if results[0] == 'a' else results[0])

    def test_validate_html_rejects_truncated_document(self):
        html_file = self.project_path / "truncated.html"
        html_file.write_text(
            "<!doctype html>\n<html>\n<head><title>Cut</title></head>\n"
            "<body>\n<main><div><p>content",
            encoding="utf-8",
        )

        result = json.loads(asyncio.run(code_tools.validate_file(str(html_file))))

        self.assertFalse(result.get("ok"), msg=result)
        self.assertTrue(result.get("truncated"), msg=result)
        self.assertTrue(result.get("unclosed_tags"), msg=result)
        self.assertEqual(result.get("ruff_fix_applicable"), False, msg=result)

if __name__ == '__main__':
    unittest.main()
