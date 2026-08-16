from __future__ import annotations

import json
import os
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

import tools_v15
from orchestrator.lifecycle import console_warp, via_bat_helper


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

    async def test_helper_disables_interactive_pause_before_calling_the_bat(self):
        """The bat ends in `pause` for humans; the helper must suppress it.

        A21 (2026-08-16): this test read `result['helper_path']` as a GENERATED
        .cmd file and grepped it for `set "TOOLA_NO_PAUSE=1"`. That architecture
        is gone -- the helper is now a real committed module invoked with
        `-m orchestrator.lifecycle.via_bat_helper`, so nothing is generated and
        the read raised FileNotFoundError against the temp root.

        The PROPERTY still matters exactly as much: if TOOLA_NO_PAUSE is not set
        before the bat runs, the bat blocks on `pause` forever and [A] never
        comes back up. Pinned here against the module that now owns it.
        """
        env_at_call = {}
        real_run = via_bat_helper.subprocess.run

        def capture(*args, **kwargs):
            env_at_call['TOOLA_NO_PAUSE'] = os.environ.get('TOOLA_NO_PAUSE')
            env_at_call['TOOLA_FROM_HTTP_HELPER'] = os.environ.get(
                'TOOLA_FROM_HTTP_HELPER'
            )
            env_at_call['args'] = kwargs.get('args') or (args[0] if args else None)
            return mock.Mock(returncode=0)

        with tempfile.TemporaryDirectory(prefix='restart-helper-') as tmp:
            root = Path(tmp)
            bat = root / 'start_orchestrator.bat'
            bat.write_text('@echo off\r\nexit /b 0\r\n', encoding='utf-8')
            preserved = {
                key: os.environ.get(key)
                for key in ('TOOLA_NO_PAUSE', 'TOOLA_FROM_HTTP_HELPER', 'TOOLA_AS_LOG')
            }
            for key in preserved:
                os.environ.pop(key, None)
            try:
                with (
                    mock.patch.object(via_bat_helper.subprocess, 'run', capture),
                    mock.patch.object(via_bat_helper, 'ensure_hidden_console'),
                    mock.patch.object(via_bat_helper.time, 'sleep'),
                ):
                    via_bat_helper.main([
                        '--delay', '1',
                        '--bat', str(bat),
                        '--log', str(root / 'helper.log'),
                        '--root', str(root),
                    ])
            finally:
                for key, value in preserved.items():
                    if value is None:
                        os.environ.pop(key, None)
                    else:
                        os.environ[key] = value

        self.assertEqual(env_at_call['TOOLA_NO_PAUSE'], '1')
        self.assertEqual(env_at_call['TOOLA_FROM_HTTP_HELPER'], '1')
        self.assertEqual(env_at_call['args'], ['cmd.exe', '/c', str(bat)])
        self.assertIsNotNone(real_run)

    def test_the_helper_is_a_real_module_not_a_generated_script(self):
        """`helper_path` must name a file that actually exists in the repo.

        The old design wrote the helper out at restart time, so a missing file
        was invisible until a restart was attempted. Now that it ships as a
        module, its absence is a packaging error worth failing on directly.
        """
        helper = Path(tools_v15.__file__).with_name(
            'orchestrator'
        ) / 'lifecycle' / 'via_bat_helper.py'
        self.assertTrue(helper.is_file(), f'helper module missing: {helper}')

    async def test_helper_spawn_never_opens_a_visible_window(self):
        """No visible cmd/powershell may ever appear during a restart.

        A21: this asserted `flags & CREATE_NO_WINDOW` on the OUTER spawn. The
        architecture changed and that bit is now deliberately ABSENT there --
        CREATE_NO_WINDOW would deny the helper a console, but the helper needs
        one it can AllocConsole + SW_HIDE so that cmd.exe and the bat inherit a
        single already-hidden console. Asserting the old bit would now force a
        REGRESSION rather than prevent one.

        So this pins the property (nothing visible) instead of the old
        mechanism: the outer spawn must hide via STARTUPINFO, and must never
        request a new or detached console -- either of which would escape the
        hidden-console warp and flash a real window on Anh's desktop.
        """
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
            detached_process = 0x00000008
            create_new_console = 0x00000010
            self.assertFalse(flags & detached_process, 'DETACHED escapes the warp')
            self.assertFalse(flags & create_new_console, 'a new console is visible')

            startupinfo = popen.call_args.kwargs['startupinfo']
            self.assertIsNotNone(startupinfo, 'no STARTUPINFO: window not hidden')
            self.assertTrue(startupinfo.dwFlags & 0x00000001)  # STARTF_USESHOWWINDOW
            self.assertEqual(startupinfo.wShowWindow, 0)       # SW_HIDE

            argv = popen.call_args.args[0]
            self.assertIn('-m', argv)
            self.assertIn('orchestrator.lifecycle.via_bat_helper', argv)

    def test_child_spawns_fall_back_to_create_no_window_without_a_console(self):
        """The warp's other half: when [A] owns no console there is nothing to
        inherit, so children MUST get CREATE_NO_WINDOW or they pop a window.
        Also pins that DETACHED is stripped -- Win32 rejects DETACHED|NO_WINDOW
        and the spawn would fail outright."""
        create_no_window = 0x08000000
        detached_process = 0x00000008

        with mock.patch.object(console_warp, 'has_console', return_value=False):
            flags = console_warp.child_creationflags(0)
            self.assertTrue(flags & create_no_window)
            self.assertFalse(flags & detached_process)

            flags = console_warp.child_creationflags(detached_process)
            self.assertFalse(
                flags & detached_process,
                'DETACHED survived; DETACHED|CREATE_NO_WINDOW is rejected by Win32',
            )

        with mock.patch.object(console_warp, 'has_console', return_value=True):
            flags = console_warp.child_creationflags(0)
            self.assertFalse(
                flags & create_no_window,
                'CREATE_NO_WINDOW would deny the child the hidden console',
            )

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
