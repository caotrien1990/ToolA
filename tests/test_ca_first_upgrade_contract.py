import asyncio
import json
import re
import sys
import types
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest import mock

import code_tools
import orchestrator_server
import tools_v15


class TestCaFirstUpgradeContract(unittest.TestCase):
    def test_all_public_tools_have_phase_metadata(self):
        public_tools = set(code_tools.TOOL_MAP)
        self.assertGreaterEqual(len(public_tools), 41)
        self.assertIn('resolve_project_path', public_tools)
        self.assertIn('protected_tools_status', public_tools)
        self.assertIn('path_quote_helper', public_tools)
        self.assertIn('progressive_matrix_check', public_tools)
        missing = sorted(name for name in public_tools if name not in code_tools.TOOL_PHASES)
        self.assertEqual(missing, [])
        schemas = {
            item['function']['name']: item['function']
            for item in code_tools.TOOL_SCHEMAS
            if item.get('function', {}).get('name') in public_tools
        }
        self.assertEqual(set(schemas), public_tools)
        for name, fn in schemas.items():
            self.assertEqual(fn.get('x_phase'), code_tools.tool_phase(name), name)
            self.assertIn(fn.get('x_phase_label'), code_tools.PHASE_LABELS.values())

    def test_prompt_lint_ca_first_no_curl(self):
        prompt = code_tools._session_prompt('D:/repo/app', 'T1')
        self.assertTrue(prompt.startswith('## ENV'))
        self.assertIn('PROJECT ORCHESTRATOR', prompt)
        self.assertIn('ORCHESTRATOR_BASE_URL', prompt)
        self.assertIn("http://localhost:8083", prompt)
        # New bootstrap contract: NO separate health-check/polling loop.
        self.assertIn("Do not run a separate health-check", prompt)
        self.assertNotIn("'/health'", prompt)
        self.assertIn("node -e", prompt)
        self.assertIn('query_encoding', prompt)
        self.assertIn('name_encoding', prompt)
        self.assertIn('command_encoding', prompt)
        self.assertIn('shell_mode', prompt)
        self.assertIn('Transport contract (V15 R12, unified across tools):', prompt)
        self.assertIn('ERROR/RETRY', prompt)  # CORE_RULE v4 header (was 'ERROR / RETRY')
        self.assertNotIn('OpenClaw', prompt)
        self.assertNotIn('host.docker.internal', prompt)
        self.assertNotIn('container localhost', prompt)
        for suffix in ('A', 'B', 'C'):
            self.assertNotIn('Tier ' + suffix, prompt)

    def test_error_response_shape_token_and_phase(self):
        response = orchestrator_server._error_response(
            'write_fix',
            'Mutation token required for this tool',
            403,
            {},
            {'project_path': 'D:/repo', 'file_path': 'a.py'},
        )
        payload = json.loads(response.text)
        self.assertFalse(payload['ok'])
        self.assertEqual(payload['error_name'], 'MUTATION_TOKEN_REQUIRED')
        self.assertIn('token', payload['ca_next_action'])
        self.assertEqual(payload['ca_guidance']['phase'], 'T2')
        self.assertIn('X-Orchestrator-Token', payload['hint'])
        self.assertTrue(payload['retry_sequence'])
        self.assertEqual(
            payload['retry_sequence'][1]['headers']['X-Orchestrator-Token'],
            'token from GET /api/token',
        )
        self.assertEqual(payload['type'], 'AUTH_ERROR')
        self.assertEqual(payload['severity'], 'error')
        self.assertTrue(payload['retryable'])
        self.assertEqual(payload['suggested_fallback'], 'FETCH_TOKEN')
        self.assertNotEqual(payload['error'], 'N/A')

    def test_error_response_logs_timezone_aware_utc_timestamp(self):
        with self.assertLogs(orchestrator_server.logger, level='WARNING') as captured:
            orchestrator_server._error_response('write_fix', 'failed', 500, {})

        record = json.loads(captured.output[-1].split(':', 2)[-1])
        timestamp = datetime.fromisoformat(record['ts'])
        self.assertEqual(timestamp.utcoffset(), timezone.utc.utcoffset(timestamp))

    def test_council_queue_drain_without_running_loop_constructs_no_coroutine(self):
        agent = 'test-no-loop'
        orchestrator_server._COUNCIL_AGENT_QUEUE_DRAINING.pop(agent, None)
        with mock.patch.object(orchestrator_server, '_council_queue_drain') as drain:
            with self.assertRaises(RuntimeError):
                orchestrator_server._council_schedule_queue_drain(agent)

        drain.assert_not_called()
        self.assertFalse(orchestrator_server._COUNCIL_AGENT_QUEUE_DRAINING[agent])

    def test_council_queue_drain_closes_coroutine_when_create_task_raises(self):
        agent = 'test-create-task-failure'
        created = []

        async def fake_drain(_agent):
            return None

        def capture_drain(_agent):
            coroutine = fake_drain(_agent)
            created.append(coroutine)
            return coroutine

        async def exercise():
            orchestrator_server._COUNCIL_AGENT_QUEUE_DRAINING.pop(agent, None)
            with mock.patch.object(orchestrator_server, '_council_queue_drain', capture_drain), \
                    mock.patch.object(asyncio, 'create_task', side_effect=ValueError('rejected')):
                with self.assertRaisesRegex(ValueError, 'rejected'):
                    orchestrator_server._council_schedule_queue_drain(agent)

        asyncio.run(exercise())
        self.assertEqual(len(created), 1)
        self.assertIsNone(created[0].cr_frame)
        self.assertFalse(orchestrator_server._COUNCIL_AGENT_QUEUE_DRAINING[agent])

    def test_write_new_file_dry_run_guidance(self):
        guidance = orchestrator_server._ca_guidance(
            'write_new_file',
            '',
            {'project_path': 'D:/repo', 'path': 'x.txt', 'new_content': 'hi', 'dry_run': True},
        )
        self.assertEqual(guidance['error_name'], 'TOOL_ERROR')
        self.assertIn('dry_run', guidance['payload_example'])
        self.assertIn('apply_after_dry_run', guidance['recommended_prompt_rule'])

    def test_schema_field_gotchas_are_documented(self):
        schemas = {
            item['function']['name']: item['function']
            for item in code_tools.TOOL_SCHEMAS
            if item.get('function', {}).get('name')
        }
        self.assertIn('max_lines', schemas['read_file_range']['parameters']['properties'])
        self.assertIn('max_context_lines', schemas['search_code']['parameters']['properties'])
        self.assertIn('dry_run', schemas['write_new_file']['parameters']['properties'])
        self.assertIn('new_content', schemas['write_new_file']['parameters']['properties'])
        self.assertIn('apply_after_dry_run', schemas)
        self.assertIn('validate_encoding', schemas)
        self.assertNotIn('dry_run', schemas['write_fix_html']['parameters']['properties'])
        self.assertIn('folder_path', schemas['get_folder_size']['parameters']['properties'])
        self.assertNotIn('project_path', schemas['get_folder_size']['parameters']['properties'])
        self.assertIn('native', schemas['log_native']['parameters']['properties'])
        self.assertNotIn('description', schemas['create_benchmark_request']['parameters']['properties'])
        self.assertNotIn('name', schemas['create_build_context']['parameters']['properties'])
        self.assertIn('enum', schemas['run_test_profile']['parameters']['properties']['profile'])
        matrix = schemas['progressive_matrix_check']
        matrix_text = json.dumps(matrix, ensure_ascii=False)
        self.assertIn('static matrix harness', matrix_text)
        self.assertIn('runtime T4 behavior still needs runtime tests', matrix_text)
        run_cmd = schemas['run_project_command']['parameters']['properties']
        self.assertIn('expected_port', run_cmd)
        self.assertIn('expected_process_name', run_cmd)
        self.assertIn('verify_after_launch_ms', run_cmd)
        self.assertIn('keep_alive', run_cmd)
        self.assertIn('background', run_cmd)
        self.assertIn('kill_process', schemas)
        self.assertEqual(code_tools.tool_phase('kill_process'), 'T2')
        self.assertIn('kill_process', code_tools.PROTECTED_TOOL_NAMES)
        self.assertIn('list_cdp_targets', schemas)
        self.assertEqual(code_tools.tool_phase('list_cdp_targets'), 'T4')
        desktop_focus = schemas['desktop_app_focus']['parameters']['properties']
        desktop_click = schemas['desktop_click']['parameters']['properties']
        for props in (desktop_focus, desktop_click):
            self.assertIn('match_mode', props)
            self.assertIn('fallback_to_win32', props)
            self.assertIn('expected_url', props)
            self.assertIn('wait_for_navigation', props)
        self.assertIn('interactive', run_cmd)
        self.assertIn('elevated', run_cmd)

    def test_t4_desktop_contracts_have_evidence_and_errors(self):
        root = Path(__file__).resolve().parents[1]
        source = (root / 'tools_v15.py').read_text(encoding='utf-8')
        skill = (root / '.ToolA_Skill' / 'skills' / '18_runtime_dom_T4' / 'SKILL.md').read_text(encoding='utf-8')
        reference = root / '.ToolA_Skill' / 'skills' / '18_runtime_dom_T4' / 'references' / 'cdp_driver_pattern.md'

        self.assertNotIn('win32process.GetCurrentThreadId', source)
        self.assertIn('win32api.GetCurrentThreadId', source)
        for marker in [
            'WINDOW_NOT_FOUND',
            'DESKTOP_INTERACTION_FAILED',
            'root_causes',
            'attach_thread_input',
            'bring_to_front_sent',
            'events_dispatched',
            'dispatch_results',
            'child_hwnd',
            'resolved_from',
            'WIN32_SEND_MESSAGE_FAILED',
            'legacy_error_name',
            'GetLastError',
            'matched_title',
            'final_url',
            'is_ui_access_enabled',
            'target_higher_than_orchestrator',
        ]:
            self.assertIn(marker, source)
        self.assertIn('open_browser_page', skill)
        self.assertIn('read_page', skill)
        self.assertIn('evaluate_js', skill)
        self.assertNotIn('browser_navigate', skill)
        self.assertNotIn('browser_snapshot', skill)
        self.assertTrue(reference.exists())

    def test_token_metadata_names_t4_protection(self):
        payload = json.loads(__import__('asyncio').run(code_tools.protected_tools_status()))
        self.assertIn('T4', payload['protected_tiers'])
        self.assertIn('desktop_click', payload['mutating_t4_tools_require_token'])
        self.assertIn('desktop_app_focus', payload['mutating_t4_tools_require_token'])
        self.assertIn('open_browser_page', payload['mutating_t4_tools_require_token'])

    def test_run_project_command_validates_explicit_readiness_inputs(self):
        root = str(Path(__file__).resolve().parents[1])
        cases = [
            ("invalid", {}, "INVALID_READINESS_PROTOCOL"),
            ("http", {}, "READINESS_INPUT_REQUIRED"),
            ("tcp", {}, "READINESS_INPUT_REQUIRED"),
            ("cdp", {}, "READINESS_INPUT_REQUIRED"),
        ]
        for protocol, kwargs, expected_error in cases:
            with self.subTest(protocol=protocol):
                payload = json.loads(asyncio.run(code_tools.run_project_command(
                    root, [sys.executable, "-c", "print('unused')"],
                    readiness_protocol=protocol, **kwargs,
                )))
                self.assertFalse(payload["ok"])
                self.assertEqual(payload["error_name"], expected_error)

    def test_run_project_command_path_quoting_error_is_structured(self):
        if sys.platform != 'win32':
            self.skipTest('Windows cmd error wording only')

        payload = json.loads(asyncio.run(code_tools.run_project_command(
            str(Path(__file__).resolve().parents[1]),
            'C:\\Program Files\\missing-tool-for-test.bat',
            timeout=5,
            shell_mode='cmd',
        )))

        self.assertFalse(payload['ok'])
        self.assertEqual(payload['error_name'], 'PATH_QUOTING_ERROR')
        self.assertEqual(payload['suggestion'], 'wrap path in double quotes')
        self.assertIn('path_quote_helper', payload['suggested_helper'])

    def test_desktop_click_unified_error_exercises_cdp_and_win32_failure(self):
        async def fake_pick_target(*args, **kwargs):
            return None, 'CDP unreachable: connection refused'

        def fake_send_message_timeout(hwnd, message, *args):
            if message in (0x0201, 0x0202):
                raise RuntimeError('send blocked')
            return 1

        fake_win32gui = types.SimpleNamespace(
            ChildWindowFromPoint=lambda hwnd, point: hwnd,
            SendMessageTimeout=fake_send_message_timeout,
        )
        last_error = {'value': 0}
        fake_win32api = types.SimpleNamespace(
            GetCurrentThreadId=lambda: 100,
            SetLastError=lambda value: last_error.__setitem__('value', value),
            GetLastError=lambda: last_error['value'] or 1400,
        )
        fake_win32con = types.SimpleNamespace(
            WM_MOUSEACTIVATE=0x0021,
            WM_LBUTTONDOWN=0x0201,
            WM_LBUTTONUP=0x0202,
            MK_LBUTTON=0x0001,
            HTCLIENT=1,
        )
        fake_win32process = types.SimpleNamespace(
            GetWindowThreadProcessId=lambda hwnd: (200, 1234),
            AttachThreadInput=lambda *args, **kwargs: True,
        )

        original_pick = tools_v15._cdp_pick_target
        original_available = tools_v15._pywin32_available
        original_modules = {name: sys.modules.get(name) for name in (
            'win32gui', 'win32api', 'win32con', 'win32process'
        )}
        try:
            tools_v15._cdp_pick_target = fake_pick_target
            tools_v15._pywin32_available = lambda: True
            sys.modules['win32gui'] = fake_win32gui
            sys.modules['win32api'] = fake_win32api
            sys.modules['win32con'] = fake_win32con
            sys.modules['win32process'] = fake_win32process

            payload = json.loads(asyncio.run(tools_v15.desktop_click(
                hwnd=12345,
                x=10,
                y=20,
                cdp_port=59999,
            )))
        finally:
            tools_v15._cdp_pick_target = original_pick
            tools_v15._pywin32_available = original_available
            for name, module in original_modules.items():
                if module is None:
                    sys.modules.pop(name, None)
                else:
                    sys.modules[name] = module

        self.assertFalse(payload['ok'])
        self.assertEqual(payload['error_name'], 'DESKTOP_INTERACTION_FAILED')
        self.assertTrue(payload['fallback_attempted'])
        self.assertIn('CDP_UNREACHABLE', payload['root_causes'])
        self.assertIn('WIN32_SEND_MESSAGE_FAILED', payload['root_causes'])
        self.assertIn('WIN32_POST_MESSAGE_FAILED', payload['root_causes'])
        self.assertIn('WM_LBUTTONDOWN', payload['root_causes'])
        self.assertIn('WM_LBUTTONUP', payload['root_causes'])
        self.assertTrue(payload['sequence'])
        self.assertEqual(payload['method'], 'win32_attach')
        self.assertIn('resolved_child_hwnd', payload)

    def test_desktop_click_win32_success_exposes_child_hwnd_and_method(self):
        async def fake_pick_target(*args, **kwargs):
            return None, 'CDP unreachable: connection refused'

        fake_win32gui = types.SimpleNamespace(
            ChildWindowFromPoint=lambda hwnd, point: 777,
            SendMessageTimeout=lambda *args: 1,
        )
        fake_win32api = types.SimpleNamespace(
            GetCurrentThreadId=lambda: 100,
            SetLastError=lambda value: None,
            GetLastError=lambda: 0,
        )
        fake_win32con = types.SimpleNamespace(
            WM_MOUSEACTIVATE=0x0021,
            WM_LBUTTONDOWN=0x0201,
            WM_LBUTTONUP=0x0202,
            MK_LBUTTON=0x0001,
            HTCLIENT=1,
            SMTO_NORMAL=0,
        )
        fake_win32process = types.SimpleNamespace(
            GetWindowThreadProcessId=lambda hwnd: (200, 1234),
            AttachThreadInput=lambda *args, **kwargs: True,
        )

        original_pick = tools_v15._cdp_pick_target
        original_available = tools_v15._pywin32_available
        original_modules = {name: sys.modules.get(name) for name in (
            'win32gui', 'win32api', 'win32con', 'win32process'
        )}
        try:
            tools_v15._cdp_pick_target = fake_pick_target
            tools_v15._pywin32_available = lambda: True
            sys.modules['win32gui'] = fake_win32gui
            sys.modules['win32api'] = fake_win32api
            sys.modules['win32con'] = fake_win32con
            sys.modules['win32process'] = fake_win32process

            payload = json.loads(asyncio.run(tools_v15.desktop_click(
                hwnd=12345,
                x=10,
                y=20,
                cdp_port=59999,
            )))
        finally:
            tools_v15._cdp_pick_target = original_pick
            tools_v15._pywin32_available = original_available
            for name, module in original_modules.items():
                if module is None:
                    sys.modules.pop(name, None)
                else:
                    sys.modules[name] = module

        self.assertTrue(payload['ok'])
        self.assertEqual(payload['method'], 'win32_attach')
        self.assertEqual(payload['resolved_hwnd'], 12345)
        self.assertEqual(payload['child_hwnd'], 777)
        self.assertEqual(payload['resolved_child_hwnd'], 777)
        self.assertEqual(payload['message_api'], 'SendMessageTimeout')
        self.assertTrue(payload['clicked'])

    def test_desktop_click_coerces_string_numeric_coordinates(self):
        async def fake_pick_target(*args, **kwargs):
            return None, 'CDP unreachable: connection refused'

        fake_win32gui = types.SimpleNamespace(
            ChildWindowFromPoint=lambda hwnd, point: hwnd,
            SendMessageTimeout=lambda *args: 1,
        )
        fake_win32api = types.SimpleNamespace(
            GetCurrentThreadId=lambda: 100,
            SetLastError=lambda value: None,
            GetLastError=lambda: 0,
        )
        fake_win32con = types.SimpleNamespace(
            WM_MOUSEACTIVATE=0x0021,
            WM_LBUTTONDOWN=0x0201,
            WM_LBUTTONUP=0x0202,
            MK_LBUTTON=0x0001,
            HTCLIENT=1,
            SMTO_NORMAL=0,
        )
        fake_win32process = types.SimpleNamespace(
            GetWindowThreadProcessId=lambda hwnd: (200, 1234),
            AttachThreadInput=lambda *args, **kwargs: True,
        )

        original_pick = tools_v15._cdp_pick_target
        original_available = tools_v15._pywin32_available
        original_modules = {name: sys.modules.get(name) for name in (
            'win32gui', 'win32api', 'win32con', 'win32process'
        )}
        try:
            tools_v15._cdp_pick_target = fake_pick_target
            tools_v15._pywin32_available = lambda: True
            sys.modules['win32gui'] = fake_win32gui
            sys.modules['win32api'] = fake_win32api
            sys.modules['win32con'] = fake_win32con
            sys.modules['win32process'] = fake_win32process

            payload = json.loads(asyncio.run(tools_v15.desktop_click(
                hwnd='12345',
                x='10',
                y='20',
                cdp_port='59999',
            )))
        finally:
            tools_v15._cdp_pick_target = original_pick
            tools_v15._pywin32_available = original_available
            for name, module in original_modules.items():
                if module is None:
                    sys.modules.pop(name, None)
                else:
                    sys.modules[name] = module

        self.assertTrue(payload['ok'], msg=payload)
        self.assertEqual(payload['resolved_hwnd'], 12345)
        self.assertEqual(payload['x'], 10)
        self.assertEqual(payload['y'], 20)

    def test_desktop_click_rejects_non_numeric_coordinates(self):
        payload = json.loads(asyncio.run(tools_v15.desktop_click(
            hwnd='12345',
            x='left',
            y='20',
            force_win32=True,
        )))

        self.assertFalse(payload['ok'])
        self.assertEqual(payload['error_name'], 'WRONG_ARGUMENTS')
        self.assertEqual(payload['field'], 'x')

    def test_token_refresh_endpoint_shape(self):
        request = types.SimpleNamespace(headers={}, remote='127.0.0.1')
        payload = json.loads(asyncio.run(orchestrator_server.handle_token_refresh(request)).text)
        self.assertTrue(payload['sensitive'])
        self.assertEqual(payload['use_header'], 'X-Orchestrator-Token')
        self.assertIn('ttl_minutes', payload)
        self.assertIn('expires_at_epoch', payload)

    def test_public_schema_copy_is_english(self):
        public_schemas = [
            item for item in code_tools.TOOL_SCHEMAS
            if item.get('function', {}).get('name') in code_tools.TOOL_MAP
        ]
        schema_text = json.dumps(public_schemas, ensure_ascii=False)

        self.assertIsNone(re.search(r'[À-ỹ]', schema_text))
        self.assertNotIn('khong', schema_text.lower())
        self.assertNotIn('duong dan', schema_text.lower())

    def test_prompt_has_current_v15_blocks_and_guardrails(self):
        prompt = code_tools._session_prompt('D:/repo/app', 'T2')
        expected_blocks = [
            'BOOTSTRAP',
            'MODE',
            'SAFETY (HARD)',
            'SKILLS',  # CORE_RULE v4 '## SKILLS (pointer)' (was 'SKILL ROUTING (Progressive Disclosure)')
            'Transport contract (V15 R12, unified across tools):',
            'ERROR/RETRY',  # CORE_RULE v4 header (was 'ERROR / RETRY')
            'RESPONSE CONTRACT',
        ]
        for block in expected_blocks:
            self.assertIn(block, prompt)
        self.assertIn('cache_delta', prompt)
        self.assertIn('query_encoding', prompt)
        self.assertIn('command_encoding', prompt)
        self.assertIn('shell_mode', prompt)

    def test_bootstrap_docs_keep_windows_defaults(self):
        root = Path(__file__).resolve().parents[1]
        docs = [
            (root / 'AGENT_BOOTSTRAP_PROMPT.md').read_text(encoding='utf-8'),
            (root / 'AGENT_WORKFLOW.md').read_text(encoding='utf-8'),
        ]
        combined = '\n'.join(docs)
        for bad in ('OpenClaw', 'host.docker.internal', 'container localhost boundary'):
            self.assertNotIn(bad, combined)
        self.assertIn('http://localhost:8083', combined)
        self.assertIn('ORCHESTRATOR_BASE_URL', combined)
        self.assertIn('curl', combined)


if __name__ == '__main__':
    unittest.main()
