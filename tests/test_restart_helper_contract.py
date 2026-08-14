from __future__ import annotations

import json
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

import tools_v15


class RestartHelperContractTests(unittest.IsolatedAsyncioTestCase):
    def write_guard_state(self, root: Path, *, runtime=None, broker_lines=()):
        cache = root / '.orchestrator_cache'
        council = root / '.council'
        cache.mkdir(exist_ok=True)
        council.mkdir(exist_ok=True)
        (cache / 'restart_busy_snapshot.json').write_text(json.dumps({
            'captured_at': time.time(), 'busy': False, 'generation': 'gen-a',
        }), encoding='utf-8')
        (council / 'runtime_state.json').write_text(
            json.dumps({} if runtime is None else runtime), encoding='utf-8'
        )
        (council / 'broker_filtered.jsonl').write_text(
            '\n'.join(broker_lines), encoding='utf-8'
        )

    async def test_always_bat_skips_8085_when_guard_allows(self):
        with tempfile.TemporaryDirectory(prefix='restart-always-bat-') as tmp:
            root = Path(tmp)
            (root / 'start_orchestrator.bat').write_text('@echo off\r\nexit /b 0\r\n', encoding='utf-8')
            self.write_guard_state(root)
            with (
                mock.patch.object(tools_v15, '__file__', str(root / 'tools_v15.py')),
                mock.patch('orchestrator.lifecycle.request_orchestrator_restart.request_restart') as post_8085,
                mock.patch('subprocess.Popen') as popen,
            ):
                result = json.loads(await tools_v15.restart_orchestrator_via_bat(expected_generation='gen-a'))
            self.assertTrue(result['ok'])
            self.assertTrue(result['always_bat'])
            self.assertIn('start_orchestrator.bat', result['mechanism'])
            post_8085.assert_not_called()
            popen.assert_called_once()

    async def test_pre_bat_guard_exempts_exact_restart_request(self):
        with tempfile.TemporaryDirectory(prefix='restart-fallback-id-') as tmp:
            root = Path(tmp)
            (root / 'start_orchestrator.bat').write_text('@echo off\r\nexit /b 0\r\n', encoding='utf-8')
            cache = root / '.orchestrator_cache'
            cache.mkdir()
            request_id = 'fallback-request-id'
            (cache / 'restart_busy_snapshot.json').write_text(json.dumps({
                'captured_at': time.time(), 'busy': True, 'generation': 'gen-a',
                'restart_request_id': request_id, 'requests_in_flight': 1,
                'executor_in_flight': 0, 'accounting_pending': 0, 'semaphores': [],
            }), encoding='utf-8')
            council = root / '.council'
            council.mkdir()
            (council / 'runtime_state.json').write_text('{}', encoding='utf-8')
            (council / 'broker_filtered.jsonl').write_text('', encoding='utf-8')
            with (
                mock.patch.object(tools_v15, '__file__', str(root / 'tools_v15.py')),
                mock.patch('subprocess.Popen') as popen,
            ):
                result = json.loads(await tools_v15.restart_orchestrator_via_bat(
                    expected_generation='gen-a', request_id=request_id,
                ))
            self.assertTrue(result['ok'])
            popen.assert_called_once()

    async def test_empty_temp_dir_without_generation_skips_guard(self):
        with tempfile.TemporaryDirectory(prefix='restart-no-council-') as tmp:
            root = Path(tmp)
            (root / 'start_orchestrator.bat').write_text('@echo off\r\nexit /b 0\r\n', encoding='utf-8')
            with (
                mock.patch.object(tools_v15, '__file__', str(root / 'tools_v15.py')),
                mock.patch('subprocess.Popen') as popen,
            ):
                result = json.loads(await tools_v15.restart_orchestrator_via_bat())
            self.assertTrue(result['ok'])
            self.assertTrue(result['always_bat'])
            popen.assert_called_once()

    async def test_pre_bat_guard_rejects_live_council(self):
        with tempfile.TemporaryDirectory(prefix='restart-live-council-') as tmp:
            root = Path(tmp)
            (root / 'start_orchestrator.bat').write_text('@echo off\r\n', encoding='utf-8')
            self.write_guard_state(root, runtime={'ope::model': {'state': 'running'}})
            with (
                mock.patch.object(tools_v15, '__file__', str(root / 'tools_v15.py')),
                mock.patch('subprocess.Popen') as popen,
            ):
                result = json.loads(await tools_v15.restart_orchestrator_via_bat(expected_generation='gen-a'))
            self.assertEqual(result['error_name'], 'RESTART_GUARD_BLOCKED')
            self.assertIn('COUNCIL_SLOT_ACTIVE', result['reasons'])
            popen.assert_not_called()

    async def test_pre_bat_guard_rejects_unmatched_broker(self):
        with tempfile.TemporaryDirectory(prefix='restart-unmatched-broker-') as tmp:
            root = Path(tmp)
            (root / 'start_orchestrator.bat').write_text('@echo off\r\n', encoding='utf-8')
            self.write_guard_state(root, broker_lines=(json.dumps({'type': 'message', 'id': 'dispatch-1'}),))
            with (
                mock.patch.object(tools_v15, '__file__', str(root / 'tools_v15.py')),
                mock.patch('subprocess.Popen') as popen,
            ):
                result = json.loads(await tools_v15.restart_orchestrator_via_bat(expected_generation='gen-a'))
            self.assertEqual(result['error_name'], 'RESTART_GUARD_BLOCKED')
            self.assertIn('BROKER_DISPATCH_UNMATCHED', result['reasons'])
            popen.assert_not_called()

    async def test_always_bat_does_not_post_8085_or_leak_secret(self):
        with tempfile.TemporaryDirectory(prefix='restart-no-leak-') as tmp:
            root = Path(tmp)
            (root / 'start_orchestrator.bat').write_text('@echo off\r\nexit /b 0\r\n', encoding='utf-8')
            cache = root / '.orchestrator_cache'
            cache.mkdir()
            (cache / 'restart_control.secret').write_bytes(b'z' * 32)
            self.write_guard_state(root)
            with (
                mock.patch.object(tools_v15, '__file__', str(root / 'tools_v15.py')),
                mock.patch('orchestrator.lifecycle.request_orchestrator_restart.request_restart') as post_8085,
                mock.patch('subprocess.Popen') as popen,
            ):
                result = json.loads(await tools_v15.restart_orchestrator_via_bat(expected_generation='gen-a'))
            self.assertTrue(result['ok'])
            dumped = json.dumps(result).lower()
            self.assertNotIn('restart_control.secret', dumped)
            self.assertNotIn('secret_path', dumped)
            post_8085.assert_not_called()
            popen.assert_called_once()

    async def test_detached_helper_disables_interactive_pause_before_call(self):
        with tempfile.TemporaryDirectory(prefix='restart-helper-') as tmp:
            root = Path(tmp)
            (root / 'start_orchestrator.bat').write_text(
                '@echo off\r\nexit /b 0\r\n',
                encoding='utf-8',
            )
            with (
                mock.patch.object(tools_v15, '__file__', str(root / 'tools_v15.py')),
                mock.patch('subprocess.Popen') as popen,
            ):
                result = json.loads(
                    await tools_v15.restart_orchestrator_via_bat(delay_seconds=1)
                )

            self.assertTrue(result['ok'])
            helper = Path(result['helper_path']).read_text(encoding='utf-8')
            no_pause = 'set "TOOLA_NO_PAUSE=1"'
            from_helper = 'set "TOOLA_FROM_HTTP_HELPER=1"'
            bat_call = 'call "' + str(root / 'start_orchestrator.bat') + '"'
            self.assertIn(no_pause, helper)
            self.assertIn(from_helper, helper)
            self.assertIn(bat_call, helper)
            self.assertLess(helper.index(no_pause), helper.index(from_helper))
            self.assertLess(helper.index(from_helper), helper.index(bat_call))
            popen.assert_called_once()

    async def test_helper_spawn_uses_create_no_window_not_detached(self):
        with tempfile.TemporaryDirectory(prefix='restart-hidden-') as tmp:
            root = Path(tmp)
            (root / 'start_orchestrator.bat').write_text(
                '@echo off\r\nexit /b 0\r\n', encoding='utf-8',
            )
            with (
                mock.patch.object(tools_v15, '__file__', str(root / 'tools_v15.py')),
                mock.patch('subprocess.Popen') as popen,
            ):
                result = json.loads(
                    await tools_v15.restart_orchestrator_via_bat(delay_seconds=1)
                )
            self.assertTrue(result['ok'])
            popen.assert_called_once()
            flags = popen.call_args.kwargs['creationflags']
            create_no_window = 0x08000000
            detached_process = 0x00000008
            create_new_console = 0x00000010
            self.assertTrue(flags & create_no_window)
            self.assertFalse(flags & detached_process)
            self.assertFalse(flags & create_new_console)

    def test_start_batch_pauses_only_for_interactive_launches(self):
        script = Path(tools_v15.__file__).with_name(
            'start_orchestrator.bat'
        ).read_text(encoding='utf-8')
        self.assertIn(
            'if not "%TOOLA_NO_PAUSE%"=="1" pause',
            script,
        )


if __name__ == '__main__':
    unittest.main()
