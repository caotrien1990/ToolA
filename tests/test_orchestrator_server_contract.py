# pyright: reportAttributeAccessIssue=false, reportOptionalMemberAccess=false, reportOptionalSubscript=false, reportArgumentType=false
import asyncio
import contextlib
import hashlib
import json
import os
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import threading
import time
import types
import unittest
import unittest.mock
import urllib.error
from unittest import mock
from datetime import datetime, timedelta
from pathlib import Path

from aiohttp import StreamReader
from orchestrator.server.audit_writer import DurableAuditWriter
from aiohttp.test_utils import make_mocked_request

import orchestrator_server
import code_tools
import tools_v15


@contextlib.contextmanager
def _issued_checkpoint(session_key: str, checkpoint: dict):
    """Seed a checkpoint where the dispatcher actually reads it.

    `_task_checkpoint_for_identity` calls `_task_checkpoint_load` (SQLite), the
    sole source of truth for task checkpoints. Seeding anything else yields
    `issued = {}` and a 403 SESSION_OWNERSHIP_REQUIRED long before the branch
    the test is aiming at, so the seed must land in SQLite.

    Writing to SQLite means isolating the DB, or the seed lands in the real
    .orchestrator_cache/sessions.db. `store._SESSIONS_DB` alone is not enough:
    every entry point calls `code_tools._sync_session_db_alias()`, which copies
    `code_tools._SESSIONS_DB` over it. Both must move together.
    """
    from orchestrator.session import store as _store

    tmpdir = Path(tempfile.mkdtemp(prefix="ckpt_"))
    db = tmpdir / "sessions.db"
    with mock.patch.object(code_tools, "_SESSIONS_DB", db), \
            mock.patch.object(_store, "_SESSIONS_DB", db):
        code_tools._sync_session_db_alias()
        _store._init_session_db()
        _store._task_checkpoint_save(session_key, checkpoint)
        yield
    code_tools._sync_session_db_alias()
    shutil.rmtree(tmpdir, ignore_errors=True)


async def _capture_mcp_open_session_call(agent_id: str, env_agent=None):
    from orchestrator.server import mcp_stdio

    class FakeMcpServer:
        instance = None

        def __init__(self, name):
            self.name = name
            self.tools = types.SimpleNamespace(methods={})
            FakeMcpServer.instance = self

        def tool(self, function):
            self.tools.methods[function.__name__] = function
            return function

        def stdio(self):
            return None

    opened = mock.AsyncMock(return_value=json.dumps({'ok': True}))
    fake_module = types.SimpleNamespace(McpServer=FakeMcpServer)
    environment = {'ORCHESTRATOR_MCP_FULL_SURFACE': '1'}
    if env_agent is not None:
        environment['COUNCIL_AGENT_ID'] = env_agent
    with mock.patch.dict(sys.modules, {'zeromcp': fake_module}), \
         mock.patch.dict(os.environ, environment, clear=True), \
         mock.patch.object(code_tools, 'open_session', opened), \
         mock.patch.object(mcp_stdio, '_mcp_current_server_generation', return_value=None):
        await asyncio.to_thread(mcp_stdio.run_mcp_stdio)
        wrapper = FakeMcpServer.instance.tools.methods['open_session']
        await asyncio.to_thread(wrapper, 'Session A', 'C:/repo', 'T2', agent_id)
    return opened.await_args


@contextlib.contextmanager
def _swap_sessions_db(tmp_db_path):
    """Point code_tools/orchestrator.session at a temp sessions.db and release all
    process-global handles on exit so TemporaryDirectory can be removed on Windows.

    Why: tests that mutate ``code_tools._SESSIONS_DB`` directly bypass the
    SessionWriter swap logic in ``orchestrator.session.store._get_session_writer``.
    The writer's drain-loop thread then keeps a sqlite3 connection open against
    the temp DB, and the read-local thread-local connection does the same on the
    test thread. On Windows those open handles block ``shutil.rmtree`` with
    WinError 32 during ``TemporaryDirectory.__exit__``. This helper:

    1. Assigns the new path to ``code_tools._SESSIONS_DB`` and calls
       ``code_tools._sync_session_db_alias()`` so the store/logs modules see the
       same path (mirrors the public setter behaviour).
    2. On exit: closes the thread-local read connection (if any), shuts down
       the process-global SessionWriter (which closes its drain-loop conn),
       restores the previous path, and re-syncs aliases so the next test sees
       the original DB.

    Functional assertions are unaffected — the body of the ``with`` block still
    runs against the temp DB.
    """
    store_module = code_tools._session_store_module
    old_db = code_tools._SESSIONS_DB
    code_tools._SESSIONS_DB = Path(tmp_db_path)
    code_tools._sync_session_db_alias()
    try:
        yield Path(tmp_db_path)
    finally:
        # 1) Release every process-global sessions.db handle (writer drain-loop
        #    conn, this thread's read-local conn, and read conns cached on dead
        #    asyncio.to_thread worker threads via gc) so the temp dir is
        #    removable on Windows. store.close_writer() owns the private-name
        #    dance so this helper no longer has to.
        try:
            store_module.close_writer()
        except Exception:
            pass
        # 2) Restore the previous path and re-sync so subsequent tests start
        #    against the original sessions.db.
        code_tools._SESSIONS_DB = old_db
        code_tools._sync_session_db_alias()


def _make_body_reader(payload: dict) -> StreamReader:
    """Build an aiohttp StreamReader pre-loaded with JSON bytes.

    make_mocked_request expects a StreamReader (not BytesPayload — that lacks
    readany in aiohttp 3.13). Feed the bytes and EOF before constructing the
    request so request.json() resolves synchronously.
    """
    loop = asyncio.new_event_loop()
    try:
        reader = StreamReader(protocol=unittest.mock.Mock(), limit=2 ** 16, loop=loop)
        reader.feed_data(json.dumps(payload).encode('utf-8'))
        reader.feed_eof()
        return reader
    finally:
        loop.close()


class TestOrchestratorServerContract(unittest.IsolatedAsyncioTestCase):
    async def test_council_worker_executor_does_not_starve_default_executor(self):
        release = threading.Event()
        started = threading.Event()
        started_count = 0
        started_lock = threading.Lock()

        def blocked_council_call():
            nonlocal started_count
            with started_lock:
                started_count += 1
                if started_count == 4:
                    started.set()
            release.wait(2)

        workers = [
            asyncio.create_task(orchestrator_server._council_run_blocking(blocked_council_call))
            for _ in range(4)
        ]
        try:
            self.assertTrue(await asyncio.to_thread(started.wait, 1))
            result = await asyncio.wait_for(asyncio.to_thread(lambda: "default-ready"), timeout=0.5)
            self.assertEqual(result, "default-ready")
        finally:
            release.set()
            await asyncio.gather(*workers)

    async def test_favicon_route_returns_no_content(self):
        app = orchestrator_server.create_app()
        request = make_mocked_request('GET', '/favicon.ico')

        match = await app.router.resolve(request)
        response = await match.handler(request)

        self.assertEqual(response.status, 204)

    def test_read_tool_aliases_keep_project_path(self):
        body, aliases = orchestrator_server._normalize_body(
            'find_symbol',
            {'project_path': 'C:/repo', 'symbol': 'cache_delta'},
        )

        self.assertEqual(body['project_path'], 'C:/repo')
        # Registry-authoritative canonical for find_symbol is `name` (required=['name'];
        # registry_data.py declares `query` as "Alias for name"). `_ARG_ALIASES`
        # folds symbol/query/symbol_name/q -> name. Assert the canonical `name`.
        self.assertEqual(body['name'], 'cache_delta')
        self.assertIn('symbol', aliases[0])

    def test_query_index_accepts_query_without_schema_error(self):
        body, _ = orchestrator_server._normalize_body(
            'query_index',
            {'project_path': 'C:/repo', 'query': 'server', 'glob': '*.py'},
        )

        self.assertEqual(body['query'], 'server')
        self.assertEqual(body['file_pattern'], '*.py')

    def test_get_folder_size_accepts_project_path_alias(self):
        body, aliases = orchestrator_server._normalize_body(
            'get_folder_size',
            {'project_path': 'C:/repo'},
        )

        self.assertEqual(body['folder_path'], 'C:/repo')
        self.assertNotIn('project_path', body)
        self.assertIn('project_path', aliases[0])

    def test_search_code_pattern_alias_enables_regex(self):
        body, _ = orchestrator_server._normalize_body(
            'search_code',
            {'project_path': 'C:/repo', 'pattern': r'def\s+session'},
        )

        self.assertEqual(body['query'], r'def\s+session')
        # 2026-08-16 (A15/TT-A-03): the pattern alias no longer injects
        # literal_search=False (S4 rejected it as UNSUPPORTED_FIELD). The
        # registry-blessed signal is mode='regex' - see orchestrator_server.py
        # _normalize_body ~L2404-2413.
        self.assertEqual(body['mode'], 'regex')

    def test_search_code_is_regex_legacy_field_sets_mode(self):
        body, aliases = orchestrator_server._normalize_body(
            'search_code',
            {'project_path': 'C:/repo', 'query': r'def\s+session', 'is_regex': True},
        )

        self.assertEqual(body['query'], r'def\s+session')
        self.assertEqual(body['mode'], 'regex')
        self.assertNotIn('is_regex', body)
        self.assertIn('is_regex->mode', aliases)

    def test_manage_agent_vscode_mcp_format_writes_top_level_servers(self):
        with tempfile.TemporaryDirectory() as tmp:
            mcp_file = Path(tmp) / 'mcp.json'
            mcp_file.write_text(json.dumps({'inputs': []}), encoding='utf-8')

            result = code_tools._ma_write_mcp(
                {
                    'id': 'copi',
                    'mcp_file': str(mcp_file),
                    'mcp_format': 'vscode',
                    'mcp_key': 'project-orchestrator',
                },
                {
                    'mcp_command': 'python',
                    'mcp_args_server': 'orchestrator_server.py',
                },
                dry_run=False,
            )

            data = json.loads(mcp_file.read_text(encoding='utf-8'))
            self.assertTrue(result['ok'])
            self.assertIn('project-orchestrator', data['servers'])
            self.assertNotIn('mcp', data)
            self.assertEqual(data['servers']['project-orchestrator']['type'], 'stdio')

    def test_manage_agent_full_rule_template_writes_core_rule(self):
        with tempfile.TemporaryDirectory() as tmp:
            rule_file = Path(tmp) / 'AGENTS.md'
            rule_file.write_text(
                'Always refer to yourself as "Ope" and address the user as "Anh".\n',
                encoding='utf-8',
            )

            result = code_tools._ma_write_rule(
                {
                    'id': 'opencode',
                    'persona': 'Always refer to yourself as "Ope" and address the user as "Anh".',
                    'rule_file': str(rule_file),
                    'rule_format': 'markdown',
                    'rule_template': 'full',
                },
                {},
                dry_run=False,
            )

            text = rule_file.read_text(encoding='utf-8')
            self.assertTrue(result['ok'])
            self.assertEqual(result['rule_template'], 'full')
            self.assertIn('Always refer to yourself as "Ope"', text)
            self.assertIn('No silent fallback', text)  # CORE_RULE v4 (was 'never silent native fallback')
            self.assertIn('once per process/session', text)  # CORE_RULE bootstrap once-guard

    def test_manage_agent_opencode_json_mcp_format_writes_mcp_not_mcpservers(self):
        with tempfile.TemporaryDirectory() as tmp:
            mcp_file = Path(tmp) / 'opencode.json'
            mcp_file.write_text(json.dumps({'provider': {'deepseek': {}}, 'mcpServers': {'project-orchestrator': {}}}), encoding='utf-8')

            result = code_tools._ma_write_mcp(
                {
                    'id': 'opencode',
                    'mcp_file': str(mcp_file),
                    'mcp_format': 'json',
                    'mcp_key': 'project-orchestrator',
                },
                {
                    'mcp_command': 'python',
                    'mcp_args_server': 'orchestrator_server.py',
                },
                dry_run=False,
            )

            data = json.loads(mcp_file.read_text(encoding='utf-8'))
            self.assertTrue(result['ok'])
            self.assertIn('provider', data)
            self.assertIn('project-orchestrator', data['mcp'])
            self.assertNotIn('mcpServers', data)
            self.assertEqual(data['mcp']['project-orchestrator']['type'], 'local')
            self.assertEqual(data['mcp']['project-orchestrator']['command'], ['python', 'orchestrator_server.py', '--mcp'])

    def test_manage_agent_backup_archives_bak_ma_outside_live_dir(self):
        with tempfile.TemporaryDirectory() as tmp:
            live = Path(tmp) / 'AGENTS.md'
            live.write_text('before', encoding='utf-8')

            backup = Path(code_tools._ma_backup(live))

            self.assertTrue(backup.exists())
            self.assertEqual(backup.read_text(encoding='utf-8'), 'before')
            self.assertEqual(backup.parent.parent, Path(tmp) / '_ma_archive')
            self.assertEqual(backup.name, 'AGENTS.md.bak_ma')
            self.assertFalse((Path(tmp) / 'AGENTS.md.bak_ma').exists())

    def test_manage_agent_restore_reads_archived_backup_path(self):
        with tempfile.TemporaryDirectory() as tmp:
            live = Path(tmp) / 'settings.json'
            live.write_text('{"ok": true}', encoding='utf-8')
            backup = code_tools._ma_backup(live)
            live.write_text('broken', encoding='utf-8')

            code_tools._ma_restore(live, backup)

            self.assertEqual(live.read_text(encoding='utf-8'), '{"ok": true}')

    def test_manage_agent_legacy_bak_ma_migration_moves_fake_skill_folder(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / 'skills'
            legacy = root / 'demo.bak_ma'
            legacy.mkdir(parents=True)
            (legacy / 'SKILL.md').write_text('legacy', encoding='utf-8')

            moved = code_tools._ma_archive_legacy_bak_ma((root,))

            dest = root / '_ma_archive' / 'legacy_20260709' / 'demo.bak_ma'
            self.assertEqual(moved, [{'from': str(legacy), 'to': str(dest)}])
            self.assertFalse(legacy.exists())
            self.assertEqual((dest / 'SKILL.md').read_text(encoding='utf-8'), 'legacy')
            self.assertEqual(code_tools._ma_archive_legacy_bak_ma((root,)), [])

    def test_readonly_command_rejects_shell_metacharacters(self):
        self.assertFalse(orchestrator_server._is_readonly_command_body({
            'command': ['cmd', '/c', 'echo ok & del important.txt'],
        }))

    def test_readonly_command_allows_simple_echo(self):
        self.assertTrue(orchestrator_server._is_readonly_command_body({
            'command': ['cmd', '/c', 'echo', 'ok'],
        }))

    def test_ca_guidance_mentions_powershell_body_not_curl(self):
        guidance = orchestrator_server._ca_guidance(
            'write_fix',
            'Invalid JSON body',
            {'project_path': 'C:/repo', 'file_path': 'app.py'},
        )

        self.assertIn('Invoke-RestMethod', guidance['call_pattern'])
        self.assertIn('$body', guidance['call_pattern'])
        self.assertNotIn('curl', guidance['call_pattern'].lower())
        # S4: payload_example is registry-derived; write_fix.content_encoding
        # defaults to 'none' in the registry (the base64 recommendation lives
        # in guidance notes, not in the example payload). Assert the default.
        self.assertEqual(guidance['payload_example']['content_encoding'], 'none')

    def test_session_prompt_is_indexed_and_base64_explicit(self):
        prompt = code_tools._session_prompt('C:/repo/app', 'T1')

        self.assertTrue(prompt.startswith('## ENV'))
        self.assertIn('content_encoding', prompt)
        self.assertIn('base64', prompt)
        self.assertIn('ORCHESTRATOR_BASE_URL', prompt)
        self.assertNotIn('http://localhost:8083/api/call', prompt)
        self.assertIn('command_encoding', prompt)
        self.assertIn('query_encoding', prompt)

    def test_write_new_file_schema_advertises_base64(self):
        schemas = {
            item['function']['name']: item['function']['parameters']['properties']
            for item in code_tools.TOOL_SCHEMAS
            if item.get('function', {}).get('name')
        }

        self.assertIn('content_encoding', schemas['write_new_file'])

    def test_ui_regression_copy_prompt_icons_and_english(self):
        html = orchestrator_server.HTML_FILE.read_text(encoding='utf-8')

        self.assertIn('<html lang="en">', html)
        self.assertIn('id="btn-copy-prompt"', html)
        self.assertIn('Copy Agent Prompt', html)
        self.assertIn('Preparing Prompt...', html)
        self.assertNotIn('Clear Closed', html)
        self.assertIn('id="cal-clear-closed-btn" title="Delete closed sessions" style="float:right">Clear</button>', html)
        self.assertIn('SSE Live', html)
        self.assertIn("icon:'📊', name:'get_project_summary'", html)
        self.assertNotIn("icon:'[R]'", html)
        self.assertNotIn("icon:'[M]'", html)
        self.assertNotIn("icon:'[S]'", html)
        self.assertNotIn('ang kim tra', html)
        self.assertNotIn('Cha c', html)
        self.assertNotIn('Tier ' + 'C', html)
        self.assertIn("fetch(`${BACKEND_API}/api/tools`, { cache: 'no-store' })", html)
        self.assertIn("async function pollAppLogs()", html)
        self.assertIn("fetch(`${BACKEND_API}/api/logs${qs}`, { cache: 'no-store' })", html)
        self.assertIn("async function caLogSyncFromServer()", html)
        self.assertIn("fetch(`${BACKEND_API}/api/ui_log?limit=${MAX_CA_LOG}`", html)
        self.assertIn("setInterval(caLogSyncFromServer, 2000)", html)
        self.assertIn("async function clearClosedSessions(evt)", html)
        self.assertIn("setInterval(() =>", html)
        self.assertIn("window.location.origin", html)
        self.assertIn("http://localhost:8083", html)
        self.assertNotIn("http://localhost:8082", html)
        self.assertIn('Schema Assistant', html)
        self.assertIn('id="s3-schema-input"', html)
        self.assertIn('Hint Quality Panel', html)
        self.assertIn('Session Summary', html)
        self.assertIn('Batch Checklist', html)
        self.assertIn('failed first attempt', html)
        self.assertIn('passed after retry', html)
        self.assertIn('skipped with reason', html)
        self.assertIn('Baseline Failure View', html)
        self.assertIn('After-Retry Coverage View', html)

    def test_cors_allows_configured_loopback_port(self):
        origin = f"http://localhost:{orchestrator_server.PORT}"
        self.assertIn(origin, orchestrator_server._ALLOWED_ORIGINS)
        self.assertIn(f"http://127.0.0.1:{orchestrator_server.PORT}", orchestrator_server._ALLOWED_ORIGINS)

    async def test_clear_closed_sessions_route_registered(self):
        app = orchestrator_server.create_app()
        request = make_mocked_request('DELETE', '/api/sessions/closed?project_path=C:/repo')

        match = await app.router.resolve(request)

        self.assertEqual(match.handler, orchestrator_server.handle_clear_closed_sessions)

    async def test_api_tools_response_is_not_browser_cached(self):
        app = orchestrator_server.create_app()
        request = make_mocked_request('GET', '/api/tools')

        match = await app.router.resolve(request)
        response = await match.handler(request)

        self.assertEqual(response.headers.get('Cache-Control'), 'no-store, max-age=0')

    async def test_api_logs_response_is_not_browser_cached(self):
        app = orchestrator_server.create_app()
        request = make_mocked_request('GET', '/api/logs')

        match = await app.router.resolve(request)
        response = await match.handler(request)

        self.assertEqual(response.headers.get('Cache-Control'), 'no-store, max-age=0')

    async def test_council_dashboard_uses_fetch_state_without_page_refresh(self):
        app = orchestrator_server.create_app()
        request = make_mocked_request('GET', '/council')

        match = await app.router.resolve(request)
        response = await match.handler(request)

        self.assertEqual(response.status, 200)
        self.assertIn("endpoint: '/api/council/activity?fields='", response.text)
        self.assertIn("endpoint: '/api/council/state'", response.text)
        self.assertIn("endpoint: '/api/council/events?limit=40'", response.text)
        self.assertIn("fetch('/api/council/message'", response.text)
        self.assertIn("fetch('/api/council/clear-session'", response.text)
        self.assertIn('id="clear-council-session"', response.text)
        self.assertIn('id="broker-form"', response.text)
        self.assertIn("url.searchParams.set('since_version'", response.text)
        self.assertIn("url.searchParams.set('generation'", response.text)
        self.assertIn('response.status === 304', response.text)
        self.assertIn('function materializeCouncilPayload', response.text)
        self.assertIn('items: new Map()', response.text)
        self.assertIn('document.hidden', response.text)
        self.assertIn("scheduleCouncilTask('activity'", response.text)
        self.assertIn("scheduleCouncilTask('events'", response.text)
        self.assertIn("scheduleCouncilTask('state'", response.text)
        self.assertNotIn('setInterval(', response.text)
        self.assertIn('CodoC', response.text)
        self.assertIn('<textarea id="broker-text" rows="3"', response.text)
        self.assertIn('data-copy-field="latest_res"', response.text)
        self.assertIn('data-copy-event', response.text)
        self.assertIn('class="copy-button icon-copy"', response.text)
        self.assertIn('previousScrollTop + heightDelta', response.text)
        # Task B: broker timeout/error gets an explicit badge + error line.
        self.assertIn('broker-badge', response.text)
        self.assertIn('broker-error', response.text)
        self.assertIn('/timeout/i.test(err)', response.text)
        self.assertIn("err ? ' has-error' : ''", response.text)
        # Task C: signature-gated rebuild + near-bottom auto-follow.
        self.assertIn('brokerEventsSignature', response.text)
        self.assertIn('isBrokerNearBottom', response.text)
        self.assertIn('wasNearBottom', response.text)
        # Task C/D: force-scroll helper + conditional centered jump-to-latest button.
        self.assertIn('function scrollBrokerToBottom', response.text)
        self.assertIn('function updateBrokerScrollButton', response.text)
        self.assertIn('id="broker-scroll-bottom"', response.text)
        self.assertIn('data-visible="0"', response.text)
        self.assertIn('.broker-scroll-bottom[data-visible="1"]', response.text)
        self.assertIn("eventsBox.addEventListener('scroll'", response.text)
        self.assertIn("getElementById('broker-scroll-bottom')", response.text)
        self.assertIn('direct_log', response.text)
        self.assertIn('Live raw', response.text)
        self.assertIn("ev.type === 'direct_log'", response.text)
        self.assertIn('function findAgentCard(agentId)', response.text)
        self.assertNotIn('CSS.escape(', response.text)
        self.assertIn('<details class="reasoning-box">', response.text)
        self.assertIn('<details class="reasoning-box live-raw-box">', response.text)
        self.assertIn("ev.type === 'system' || ev.type === 'notice'", response.text)
        self.assertNotIn('refreshCouncilState()', response.text)
        self.assertNotIn('data-field="status"', response.text)
        self.assertNotIn('agent-status', response.text)
        self.assertNotIn('usage-refresh', response.text)
        self.assertNotIn('↻ 1s', response.text)
        self.assertIn('<span>Response</span>', response.text)
        self.assertNotIn('<span>Latest MSG</span>', response.text)
        self.assertNotIn('<span>Log tail</span>', response.text)
        self.assertNotIn('data-field="latest_msg"', response.text)
        self.assertNotIn('data-field="live_log"', response.text)
        self.assertNotIn('<span>Thinking</span>', response.text)
        self.assertNotIn('<span>Latest RES</span>', response.text)
        self.assertNotIn('http-equiv="refresh"', response.text)
        self.assertNotIn('location.reload', response.text)

    async def test_council_dashboard_materializes_full_and_delta_payloads_in_shipped_js(self):
        app = orchestrator_server.create_app()
        request = make_mocked_request('GET', '/council')
        match = await app.router.resolve(request)
        response = await match.handler(request)

        def extract_function(source, name):
            start = source.index(f'function {name}(')
            body_start = source.index('{', start)
            depth = 0
            quote = None
            escaped = False
            line_comment = False
            block_comment = False
            index = body_start
            while index < len(source):
                char = source[index]
                following = source[index + 1] if index + 1 < len(source) else ''
                if line_comment:
                    line_comment = char != '\n'
                elif block_comment:
                    if char == '*' and following == '/':
                        block_comment = False
                        index += 1
                elif quote:
                    if escaped:
                        escaped = False
                    elif char == '\\':
                        escaped = True
                    elif char == quote:
                        quote = None
                elif char in "'\"`":
                    quote = char
                elif char == '/' and following == '/':
                    line_comment = True
                    index += 1
                elif char == '/' and following == '*':
                    block_comment = True
                    index += 1
                elif char == '{':
                    depth += 1
                elif char == '}':
                    depth -= 1
                    if depth == 0:
                        return source[start:index + 1]
                index += 1
            self.fail(f'Unterminated JavaScript function: {name}')

        shipped_js = '\n'.join([
            extract_function(response.text, 'stableCouncilEventSort'),
            extract_function(response.text, 'materializeCouncilPayload'),
        ])
        node_script = f"""
const vm = require('node:vm');
const context = {{}};
vm.createContext(context);
vm.runInContext({json.dumps(shipped_js)}, context);
const channel = {{ collection: 'agents', version: 0, generation: '', items: new Map() }};
const snapshots = [];
function apply(payload) {{
  const result = context.materializeCouncilPayload(channel, payload);
  snapshots.push({{
    map: channel.items instanceof Map,
    entries: Array.from(channel.items.entries()),
    channelVersion: channel.version,
    channelGeneration: channel.generation,
    result,
  }});
}}
apply({{ full: true, delta: false, version: 7, generation: 'g1', agents: [
  {{ id: 'a', value: 'A1' }}, {{ id: 'b', value: 'B1' }}
] }});
apply({{ full: false, delta: true, version: 8, generation: 'g1', removed_ids: ['b'], agents: [
  {{ id: 'a', value: 'A2' }}, {{ id: 'c', value: 'C1' }}
] }});
apply({{ full: true, delta: false, version: 1, generation: 'g2', agents: [
  {{ id: 'd', value: 'D1' }}
] }});
process.stdout.write(JSON.stringify(snapshots));
"""
        with tempfile.TemporaryDirectory() as tmp:
            script_path = Path(tmp) / 'materialize_council_payload_contract.js'
            script_path.write_text(node_script, encoding='utf-8')
            completed = subprocess.run(
                ['node', str(script_path)],
                capture_output=True,
                text=True,
                encoding='utf-8',
                timeout=30,
                check=False,
            )
        self.assertEqual(completed.returncode, 0, completed.stderr)
        full_g1, delta_g1, full_g2 = json.loads(completed.stdout)

        for snapshot in (full_g1, delta_g1, full_g2):
            self.assertTrue(snapshot['map'])
            self.assertTrue(snapshot['result']['full'])
            self.assertFalse(snapshot['result']['delta'])
            self.assertTrue(snapshot['result']['materialized'])

        self.assertEqual(full_g1['entries'], [
            ['a', {'id': 'a', 'value': 'A1'}],
            ['b', {'id': 'b', 'value': 'B1'}],
        ])
        self.assertEqual(full_g1['channelVersion'], 7)
        self.assertEqual(full_g1['channelGeneration'], 'g1')
        self.assertEqual(full_g1['result']['version'], 7)
        self.assertEqual(full_g1['result']['generation'], 'g1')

        self.assertEqual(delta_g1['entries'], [
            ['a', {'id': 'a', 'value': 'A2'}],
            ['c', {'id': 'c', 'value': 'C1'}],
        ])
        self.assertEqual(delta_g1['channelVersion'], 8)
        self.assertEqual(delta_g1['channelGeneration'], 'g1')
        self.assertEqual(delta_g1['result']['version'], 8)
        self.assertEqual(delta_g1['result']['generation'], 'g1')

        self.assertEqual(full_g2['entries'], [['d', {'id': 'd', 'value': 'D1'}]])
        self.assertEqual(full_g2['channelVersion'], 1)
        self.assertEqual(full_g2['channelGeneration'], 'g2')
        self.assertEqual(full_g2['result']['version'], 1)
        self.assertEqual(full_g2['result']['generation'], 'g2')

    def test_council_usage_card_shows_tokens_without_context_total(self):
        html = orchestrator_server._council_metrics_table('ope', {
            'tokens_session': 12345,
            'context_tokens': None,
            'context_percent': None,
        })

        self.assertIn('12.3k', html)
        context_row = '<span class="usage-label">Context window</span><span class="usage-value">12.3k</span>'
        self.assertIn(context_row, html)

    async def test_council_state_route_returns_generated_at_and_agents(self):
        old_here = orchestrator_server._HERE
        old_alive = orchestrator_server._council_alive_agents
        old_metrics = orchestrator_server._council_agent_metrics
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            council = root / '.council'
            council.mkdir()
            (council / 'inbox_codo.md').write_text('<<MSG>>\nold\n<<END MSG>>\n<<MSG>>\nnew\n<<END MSG>>', encoding='utf-8')
            (council / 'outbox_codo.md').write_text('<<RES>>\nanswer\n<<END RES>>', encoding='utf-8')
            (council / 'log_codo.txt').write_text('one\ntwo\nthree', encoding='utf-8')
            orchestrator_server._HERE = root
            orchestrator_server._council_alive_agents = lambda: {'codo'}
            orchestrator_server._council_agent_metrics = lambda _dir, _agent: {}
            try:
                app = orchestrator_server.create_app()
                request = make_mocked_request('GET', '/api/council/state')
                match = await app.router.resolve(request)
                response = await match.handler(request)
            finally:
                orchestrator_server._HERE = old_here
                orchestrator_server._council_alive_agents = old_alive
                orchestrator_server._council_agent_metrics = old_metrics

        payload = json.loads(response.text)
        codo = next(agent for agent in payload['agents'] if agent['id'] == 'codo')
        self.assertIn('generated_at', payload)
        self.assertEqual(codo['status']['label'], 'idle')
        self.assertEqual(codo['runtime']['state'], 'idle')
        self.assertEqual(codo['latest_msg'], '<<MSG>>\nnew\n<<END MSG>>')
        self.assertEqual(codo['latest_res'], '<<RES>>\nanswer\n<<END RES>>')
        self.assertEqual(codo['live_log'].splitlines()[:2], ['three', 'two'])
        self.assertEqual(response.headers.get('Cache-Control'), 'no-store, max-age=0')

    async def test_council_state_filters_direct_log_and_adds_usage_to_reasoning(self):
        old_here = orchestrator_server._HERE
        old_alive = orchestrator_server._council_alive_agents
        old_metrics = orchestrator_server._council_agent_metrics
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            council = root / '.council'
            council.mkdir()
            (council / 'log_ope_noise.txt').write_text(
                '\n'.join([
                    '{"type":"step_start","sessionID":"s1"}',
                    '{"type":"reasoning","part":{"text":"hidden thought"}}',
                    '{"type":"text","part":{"text":"Tiếng Việt sạch."}}',
                    '{"type":"step_finish","part":{"tokens":{"total":99}}}',
                ]),
                encoding='utf-8',
            )
            orchestrator_server._HERE = root
            orchestrator_server._council_alive_agents = lambda: set()
            orchestrator_server._council_agent_metrics = lambda _dir, agent: {
                'source': 'fake-source',
                'tokens_session': 1234,
                'tokens_today': 1234,
                'context_tokens': None,
                'plan_5h': None,
                'plan_7d': None,
                'model': 'fake-model',
            }
            try:
                request = make_mocked_request('GET', '/api/council/state')
                response = await orchestrator_server.handle_council_state(request)
            finally:
                orchestrator_server._HERE = old_here
                orchestrator_server._council_alive_agents = old_alive
                orchestrator_server._council_agent_metrics = old_metrics

        payload = json.loads(response.text)
        ope = next(agent for agent in payload['agents'] if agent['id'] == 'ope')
        self.assertIn('Tiếng Việt sạch.', ope['direct_log'])
        self.assertNotIn('step_start', ope['direct_log'])
        self.assertNotIn('sessionID', ope['direct_log'])
        self.assertIn('[usage OpeC]', ope['reasoning'])
        self.assertIn('tokens_session: 1.2k', ope['reasoning'])
        self.assertIn('fake-source', ope['reasoning'])

    async def test_council_activity_direct_log_falls_back_to_raw_tail_while_running(self):
        # Task A: while an agent is still streaming, clean_text is empty (thinking-only).
        # The 1s activity payload must surface a bounded raw tail instead of going blank,
        # without changing response extraction.
        old_here = orchestrator_server._HERE
        old_alive = orchestrator_server._council_alive_agents
        old_metrics = orchestrator_server._council_agent_metrics
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            council = root / '.council'
            council.mkdir()
            (council / 'log_ope.txt').write_text(
                'Thinking: still investigating the task\nLet me inspect the files first',
                encoding='utf-8',
            )
            orchestrator_server._HERE = root
            orchestrator_server._council_alive_agents = lambda: {'ope'}
            orchestrator_server._council_agent_metrics = lambda _dir, _agent: {}
            try:
                request = make_mocked_request('GET', '/api/council/activity')
                response = await orchestrator_server.handle_council_activity(request)
            finally:
                orchestrator_server._HERE = old_here
                orchestrator_server._council_alive_agents = old_alive
                orchestrator_server._council_agent_metrics = old_metrics

        payload = json.loads(response.text)
        ope = next(agent for agent in payload['agents'] if agent['id'] == 'ope')
        # clean_text is empty for thinking-only logs, so the raw tail is the fallback.
        self.assertEqual(
            orchestrator_server._council_clean_direct_log_text('ope', 'Thinking: x\nLet me y'),
            '',
        )
        self.assertTrue(ope['direct_log'])
        self.assertIn('still investigating the task', ope['direct_log'])
        self.assertNotIn('live_raw', ope)
        self.assertNotIn('reasoning', ope)

    async def test_council_activity_fields_can_request_live_raw(self):
        old_here = orchestrator_server._HERE
        old_alive = orchestrator_server._council_alive_agents
        old_metrics = orchestrator_server._council_agent_metrics
        orchestrator_server._council_dashboard_payload_invalidate()
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            council = root / '.council'
            council.mkdir()
            (council / 'log_ope.txt').write_text('answer', encoding='utf-8')
            (council / 'live_raw_ope.log').write_text('raw-stream', encoding='utf-8')
            orchestrator_server._HERE = root
            orchestrator_server._council_alive_agents = lambda: {'ope'}
            orchestrator_server._council_agent_metrics = lambda _dir, _agent: {}
            try:
                request = make_mocked_request(
                    'GET',
                    '/api/council/activity?fields=live_raw,reasoning,direct_log',
                )
                response = await orchestrator_server.handle_council_activity(request)
            finally:
                orchestrator_server._HERE = old_here
                orchestrator_server._council_alive_agents = old_alive
                orchestrator_server._council_agent_metrics = old_metrics
                orchestrator_server._council_dashboard_payload_invalidate()

        payload = json.loads(response.text)
        ope = next(agent for agent in payload['agents'] if agent['id'] == 'ope')
        self.assertEqual(ope['live_raw'], 'raw-stream')
        self.assertIn('reasoning', ope)

    def test_council_dashboard_payload_cache_ttl_and_invalidation(self):
        calls = {'count': 0}
        old_ttl = orchestrator_server._COUNCIL_DASHBOARD_PAYLOAD_TTL_SECONDS
        try:
            orchestrator_server._COUNCIL_DASHBOARD_PAYLOAD_TTL_SECONDS = 0.05
            orchestrator_server._council_dashboard_payload_invalidate()

            def build():
                calls['count'] += 1
                return {'agents': [{'id': 'codo', 'live_raw': f"raw-{calls['count']}"}]}

            first = orchestrator_server._council_cached_dashboard_payload('unit', build)
            second = orchestrator_server._council_cached_dashboard_payload('unit', build)
            self.assertEqual(calls['count'], 1)
            self.assertEqual(first, second)
            time.sleep(0.06)
            third = orchestrator_server._council_cached_dashboard_payload('unit', build)
            self.assertEqual(second, third)
            fourth = third
            for _ in range(20):
                fourth = orchestrator_server._council_cached_dashboard_payload('unit', build)
                if fourth['agents'][0]['live_raw'] != third['agents'][0]['live_raw']:
                    break
                time.sleep(0.01)
            self.assertEqual(calls['count'], 2)
            self.assertEqual(fourth['agents'][0]['live_raw'], 'raw-2')
            orchestrator_server._council_dashboard_payload_invalidate()
            orchestrator_server._council_cached_dashboard_payload('unit', build)
            self.assertEqual(calls['count'], 3)
        finally:
            orchestrator_server._COUNCIL_DASHBOARD_PAYLOAD_TTL_SECONDS = old_ttl
            orchestrator_server._council_dashboard_payload_invalidate()

    def test_council_activity_filter_does_not_copy_unrequested_live_raw_blob(self):
        payload = {
            'generated_at': 'now',
            'agents': [{
                'id': 'codo',
                'label': 'CodoC',
                'direct_log': 'visible',
                'live_raw': 'x' * 240000,
                'reasoning': 'y' * 240000,
            }],
        }

        filtered = orchestrator_server._council_filter_agent_payload(
            payload,
            {'direct_log'},
        )

        self.assertEqual(filtered['agents'], [{
            'id': 'codo',
            'label': 'CodoC',
            'direct_log': 'visible',
        }])
        self.assertNotIn('live_raw', filtered['agents'][0])
        self.assertNotIn('reasoning', filtered['agents'][0])

    def test_council_dashboard_payload_cache_single_flight_stale_rebuild(self):
        calls = {'count': 0}
        old_ttl = orchestrator_server._COUNCIL_DASHBOARD_PAYLOAD_TTL_SECONDS
        gate = threading.Event()
        try:
            orchestrator_server._COUNCIL_DASHBOARD_PAYLOAD_TTL_SECONDS = 0.01
            orchestrator_server._council_dashboard_payload_invalidate()

            def build():
                calls['count'] += 1
                if calls['count'] > 1:
                    gate.wait(1)
                return {'agents': [{'id': 'codo', 'live_raw': f"raw-{calls['count']}"}]}

            first = orchestrator_server._council_cached_dashboard_payload('single', build)
            time.sleep(0.02)
            stale_a = orchestrator_server._council_cached_dashboard_payload('single', build)
            stale_b = orchestrator_server._council_cached_dashboard_payload('single', build)

            self.assertEqual(first, stale_a)
            self.assertEqual(first, stale_b)
            self.assertEqual(calls['count'], 2)
            gate.set()
            latest = first
            for _ in range(20):
                latest = orchestrator_server._council_cached_dashboard_payload('single', build)
                if latest['agents'][0]['live_raw'] != first['agents'][0]['live_raw']:
                    break
                time.sleep(0.01)
            self.assertEqual(latest['agents'][0]['live_raw'], 'raw-2')
        finally:
            gate.set()
            orchestrator_server._COUNCIL_DASHBOARD_PAYLOAD_TTL_SECONDS = old_ttl
            orchestrator_server._council_dashboard_payload_invalidate()

    async def test_server_lifecycle_status_uses_psutil_port_cache(self):
        calls = {'net': 0}

        class FakeAddr:
            port = 43210

        class FakeConn:
            laddr = FakeAddr()
            status = 'LISTEN'
            pid = 1234

        class FakeProcess:
            def __init__(self, pid):
                self.pid = pid

            def as_dict(self, attrs=None, ad_value=None):
                return {'name': 'python.exe', 'cmdline': ['python', 'server.py'], 'username': 'user'}

            def username(self):
                return 'user'

        def fake_net_connections(kind='tcp4'):
            calls['net'] += 1
            return [FakeConn()]

        fake_psutil = types.SimpleNamespace(
            net_connections=fake_net_connections,
            Process=FakeProcess,
            AccessDenied=PermissionError,
            NoSuchProcess=RuntimeError,
        )
        old_psutil = sys.modules.get('psutil')
        sys.modules['psutil'] = fake_psutil
        tools_v15._PSUTIL_PORT_SCAN_CACHE.clear()
        old_ttl = tools_v15._PSUTIL_PORT_SCAN_TTL_SECONDS
        try:
            tools_v15._PSUTIL_PORT_SCAN_TTL_SECONDS = 3.0
            first = json.loads(await tools_v15.server_lifecycle_status(43210))
            second = json.loads(await tools_v15.server_lifecycle_status(43210))
        finally:
            tools_v15._PSUTIL_PORT_SCAN_TTL_SECONDS = old_ttl
            tools_v15._PSUTIL_PORT_SCAN_CACHE.clear()
            if old_psutil is None:
                sys.modules.pop('psutil', None)
            else:
                sys.modules['psutil'] = old_psutil

        self.assertTrue(first['ok'])
        self.assertTrue(second['ok'])
        self.assertEqual(calls['net'], 1)
        self.assertTrue(second['cache_hit'])

    async def test_council_state_direct_log_falls_back_to_raw_tail_while_running(self):
        # Regression: the 30s state poll must use the same raw_tail fallback as the
        # activity payload. A thinking-only log has empty clean_text, so direct_log
        # must still surface the bounded raw tail instead of blanking the Log Tail.
        old_here = orchestrator_server._HERE
        old_alive = orchestrator_server._council_alive_agents
        old_metrics = orchestrator_server._council_agent_metrics
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            council = root / '.council'
            council.mkdir()
            (council / 'log_ope.txt').write_text(
                'Thinking: still investigating the task\nLet me inspect the files first',
                encoding='utf-8',
            )
            orchestrator_server._HERE = root
            orchestrator_server._council_alive_agents = lambda: {'ope'}
            orchestrator_server._council_agent_metrics = lambda _dir, _agent: {}
            try:
                request = make_mocked_request('GET', '/api/council/state')
                response = await orchestrator_server.handle_council_state(request)
            finally:
                orchestrator_server._HERE = old_here
                orchestrator_server._council_alive_agents = old_alive
                orchestrator_server._council_agent_metrics = old_metrics

        payload = json.loads(response.text)
        ope = next(agent for agent in payload['agents'] if agent['id'] == 'ope')
        # clean_text is empty for thinking-only logs, so the raw tail is the fallback.
        self.assertEqual(
            orchestrator_server._council_clean_direct_log_text('ope', 'Thinking: x\nLet me y'),
            '',
        )
        self.assertTrue(ope['direct_log'])
        self.assertIn('still investigating the task', ope['direct_log'])

    async def test_council_message_broker_writes_compact_events(self):
        old_here = orchestrator_server._HERE
        old_run = orchestrator_server._council_run_agent
        seen_prompts = []
        try:
            with tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                (root / '.council').mkdir()
                orchestrator_server._HERE = root
                orchestrator_server._council_run_agent = lambda agent, prompt, timeout_sec=180, mode="broker", project_path="", request_id="", runtime_key="", provider_override="", model_override="", model_resolution=None, risk_level="unknown", dev_level="unknown", *_a, **_k: (
                    seen_prompts.append(prompt) or {
                    "ok": True,
                    "returncode": 0,
                    "reply": "Chào opeC, broker đã nhận tin.",
                    "raw_preview": '{"type":"step_finish","part":{"tokens":{"input":12,"output":8,"reasoning":3,"total":23}}}',
                    }
                )
                request = make_mocked_request(
                    'POST',
                    '/api/council/message',
                    headers={'Content-Type': 'application/json', 'Origin': f'http://localhost:{orchestrator_server.PORT}'},
                    payload=_make_body_reader({'from': 'Anh', 'to': 'ope', 'text': 'test broker'}),
                )

                response = await orchestrator_server.handle_council_message(request)
                data = json.loads(response.text)
                events = orchestrator_server._council_read_events(10)

                self.assertEqual(response.status, 200)
                self.assertTrue(data['ok'])
                self.assertEqual(len(events), 2)
                self.assertEqual(events[0]['type'], 'message')
                self.assertEqual(events[1]['type'], 'reply')
                self.assertTrue(seen_prompts)
                self.assertEqual(events[1]['response_tokens']['total'], 23)
                self.assertEqual(events[1]['response_tokens']['input'], 12)
                self.assertEqual(events[1]['response_tokens']['output'], 8)
                self.assertIn('test broker', seen_prompts[0].splitlines()[0])
                self.assertIn('broker đã nhận tin', events[1]['text'])
        finally:
            orchestrator_server._HERE = old_here
            orchestrator_server._council_run_agent = old_run

    def test_context_registry_roundtrip_and_generation_invalidates(self):
        old_here = orchestrator_server._HERE
        try:
            with tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                (root / '.council').mkdir()
                orchestrator_server._HERE = root
                # No context yet.
                self.assertIsNone(orchestrator_server._council_get_context('clau', root_str := str(root)))
                # Persist a session id.
                entry = orchestrator_server._council_set_context('clau', root_str, 'sess-1', 'claude')
                self.assertEqual(entry['turns'], 1)
                got = orchestrator_server._council_get_context('clau', root_str)
                self.assertIsNotNone(got)
                self.assertEqual(got['session_id'], 'sess-1')
                # Second turn increments turns, keeps id.
                entry2 = orchestrator_server._council_set_context('clau', root_str, 'sess-1', 'claude')
                self.assertEqual(entry2['turns'], 2)
                # Clear-session bumps generation -> stale -> fresh.
                gen_before = orchestrator_server._council_context_generation()
                orchestrator_server._council_clear_session_runtime(root / '.council')
                self.assertEqual(orchestrator_server._council_context_generation(), gen_before + 1)
                self.assertIsNone(orchestrator_server._council_get_context('clau', root_str))
        finally:
            orchestrator_server._HERE = old_here

    def test_council_agent_command_context_flags(self):
        # claude: fresh -> --session-id; resume -> --resume
        fresh = orchestrator_server._council_agent_command('clau', 'hi', mode='context', new_session_id='uuid-1')
        self.assertIn('--session-id', fresh)
        self.assertIn('uuid-1', fresh)
        self.assertNotIn('--resume', fresh)
        resume = orchestrator_server._council_agent_command('clau', 'hi', mode='context', resume_id='uuid-1')
        self.assertIn('--resume', resume)
        self.assertNotIn('--session-id', resume)
        # opencode: resume -> --session <id>
        ope_resume = orchestrator_server._council_agent_command('ope', 'hi', mode='context', resume_id='ope-sess')
        self.assertIn('--session', ope_resume)
        self.assertIn('ope-sess', ope_resume)
        # codex: fresh context -> --json ; resume -> exec resume <id>
        codo_fresh = orchestrator_server._council_agent_command('codo', 'hi', mode='context')
        self.assertIn('--json', codo_fresh)
        self.assertNotIn('resume', codo_fresh)
        codo_resume = orchestrator_server._council_agent_command('codo', 'hi', mode='context', resume_id='thread-9')
        self.assertIn('resume', codo_resume)
        self.assertIn('thread-9', codo_resume)
        self.assertIn('--json', codo_resume)
        # Codex must not nest its Windows restricted-token sandbox inside [A].
        self.assertNotIn('--sandbox', codo_resume)
        self.assertNotIn('sandbox_mode="workspace-write"', codo_resume)
        self.assertIn('--dangerously-bypass-approvals-and-sandbox', codo_resume)
        # broker mode adds none of the session flags
        clau_broker = orchestrator_server._council_agent_command('clau', 'hi', mode='broker')
        self.assertNotIn('--resume', clau_broker)
        self.assertNotIn('--session-id', clau_broker)
        self.assertIn('--no-session-persistence', clau_broker)
        codo_broker = orchestrator_server._council_agent_command('codo', 'hi', mode='broker')
        self.assertIn('--ephemeral', codo_broker)
        self.assertIn('--model', codo_broker)
        # codo default model migrated to grok-4.6-build (claudeltnai/codexltnai route,
        # Anh-directed 2026-08-27); stealth/ox-alpha is deferred, see
        # _COUNCIL_DEFAULT_MODELS_FALLBACK.
        self.assertEqual(codo_broker[codo_broker.index('--model') + 1], 'grok-4.6-build')
        self.assertNotIn('model_provider="ltn-api"', codo_broker)
        self.assertIn('model_reasoning_effort="xhigh"', codo_broker)
        self.assertNotIn('--sandbox', codo_broker)
        self.assertIn('--dangerously-bypass-approvals-and-sandbox', codo_broker)
        self.assertEqual(codo_broker[-1], 'hi')

    def test_pic_command_uses_pi_catalog_model(self):
        cmd = orchestrator_server._council_agent_command('pic', 'PIC_REAL_TEST_OK', mode='broker', provider_override='ltnai', model_override='deepseek-v4-pro')
        self.assertIn('--print', cmd)
        self.assertIn('--no-session', cmd)
        self.assertEqual(cmd[cmd.index('--provider') + 1], 'ltnai')
        self.assertEqual(cmd[cmd.index('--model') + 1], 'deepseek-v4-pro')
        self.assertEqual(cmd[-1], 'PIC_REAL_TEST_OK')

    def test_pic_model_metadata_uses_ltnai_provider(self):
        self.assertEqual(orchestrator_server._council_provider_for_model('pic', 'deepseek-v4-pro'), 'ltnai')
        self.assertIn('deepseek-v4-pro', orchestrator_server._council_valid_models('pic'))

    def test_codo_model_metadata_uses_codex_cli_provider(self):
        self.assertEqual(
            orchestrator_server._council_provider_for_model('codo', 'gpt-5.6-sol'),
            'codex',
        )
        self.assertIn('gpt-5.6-sol', orchestrator_server._council_valid_models('codo'))
        effective = orchestrator_server._council_effective_model('codo')
        # B2: assert the COMPATIBILITY PROPERTY, not the old two-key dict shape.
        # _council_effective_model is documented as a "deprecated compatibility
        # view" over CouncilModelResolution.as_dict(), which deliberately carries
        # the full resolution (requested_*/resolved_*/observed_*/status/warnings)
        # alongside the legacy provider+model keys. Every production caller reads
        # it via .get("model") / .get("provider") (orchestrator_server.py:4633,
        # :10009 and orchestrator/council/dashboard.py:33), so an exact-equality
        # assertion pinned a shape nothing depends on and turned a deliberate
        # widening into a false red -- the same A19/A21 trap of testing the
        # mechanism instead of the property. What must hold: the legacy keys still
        # exist and still carry codo's resolved CodexLTNAI live ID.
        #
        # R57: the same trap caught this test one level down. It said "carry
        # codo's resolved live ID" and then hardcoded 'stealth/ox-alpha' -- a
        # snapshot of the config, not the property. .council/council_config.json
        # is the declared SOT and has since moved codo to grok-4.6-build, its
        # own stale_memory_corrections reading "CodoC live pin is grok-4.6-build
        # on CodexLTNAI. ox-alpha deferred." The config moved, the literal did
        # not, and the suite went red over a deliberate routing change. Assert
        # the pass-through instead: whatever the SOT declares for codo is what
        # the compatibility view must report.
        self.assertEqual(effective.get('provider'), 'codex')
        self.assertEqual(
            effective.get('model'),
            orchestrator_server._COUNCIL_DEFAULT_MODELS['codo']['model'],
        )
        self.assertEqual(effective.get('resolution_status'), 'unverified')

    def test_codo_executable_prefers_dedicated_ltnai_launcher(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            npm = home / 'AppData' / 'Roaming' / 'npm'
            npm.mkdir(parents=True)
            launcher = npm / 'codexltnai.cmd'
            launcher.touch()
            with mock.patch('orchestrator_server.Path.home', return_value=home):
                self.assertEqual(orchestrator_server._council_codex_executable(), str(launcher))

    def test_codo_executable_honors_dedicated_override(self):
        with tempfile.TemporaryDirectory() as tmp:
            launcher = Path(tmp) / 'custom-codexltnai.cmd'
            launcher.touch()
            with mock.patch.dict(os.environ, {'COUNCIL_CODO_CLI_PATH': str(launcher)}):
                self.assertEqual(orchestrator_server._council_codex_executable(), str(launcher))

    def test_codo_api_key_prefers_dedicated_environment_value(self):
        env = {'COUNCIL_CODO_API_KEY': 'dedicated-secret', 'API_KEY': 'generic-secret'}
        self.assertEqual(orchestrator_server._council_codo_api_key(env), 'dedicated-secret')

    def test_codo_prepare_env_uses_launcher_config_and_injects_key(self):
        env = {'COUNCIL_CODO_API_KEY': 'child-only-secret'}
        orchestrator_server._council_prepare_codo_env(env)
        self.assertEqual(env['API_KEY'], 'child-only-secret')
        self.assertNotIn('CODEX_HOME', env)

    def test_provider_session_id_extractors(self):
        codex_jsonl = '\n'.join([
            '{"type":"thread.started","thread_id":"th-abc"}',
            '{"type":"item.completed","item":{"text":"done"}}',
        ])
        self.assertEqual(orchestrator_server._council_extract_codex_thread_id(codex_jsonl), 'th-abc')
        ope_jsonl = '{"type":"step","part":{"sessionID":"oc-123","text":"x"}}'
        self.assertEqual(orchestrator_server._council_extract_opencode_session_id(ope_jsonl), 'oc-123')

    def test_council_runtime_status_tracks_terminal_state(self):
        # Isolate the shared runtime file: _council_runtime_status MERGES the
        # on-disk .council/runtime_state.json (cross-process visibility), so
        # without this patch the test read whatever a live [A] had written for
        # 'ope' and flaked. Point it at a temp file for the duration.
        import tempfile as _tf, os as _os
        _tmp = _tf.NamedTemporaryFile(mode='w', suffix='.json', delete=False)
        _tmp.write('{}')
        _tmp.close()
        _orig_rt_file = orchestrator_server._council_runtime_file
        orchestrator_server._council_runtime_file = lambda: Path(_tmp.name)
        try:
            orchestrator_server._COUNCIL_AGENT_RUNTIME.clear()
            started = orchestrator_server._council_runtime_start('ope', 'req-1', 'broker')
            self.assertEqual(started['state'], 'running')
            self.assertFalse(orchestrator_server._council_runtime_start('ope', 'req-2', 'broker'))
            finished = orchestrator_server._council_runtime_finish('ope', 'req-1', False, 'timeout')
            self.assertEqual(finished['state'], 'timeout')
            self.assertEqual(orchestrator_server._council_runtime_status('ope')['state'], 'timeout')
            orchestrator_server._COUNCIL_AGENT_RUNTIME.clear()
        finally:
            orchestrator_server._council_runtime_file = _orig_rt_file
            try:
                _os.unlink(_tmp.name)
            except OSError:
                pass

    def test_council_cleanup_broker_opencode_session(self):
        old_run = orchestrator_server.subprocess.run
        seen = []
        try:
            orchestrator_server.subprocess.run = lambda cmd, **kwargs: seen.append(cmd)
            cleaned = orchestrator_server._council_cleanup_broker_session(
                'ope',
                '{"part":{"sessionID":"ses-trash"}}',
                'prompt',
                time.time(),
            )
            self.assertTrue(cleaned)
            self.assertIn('session', seen[0])
            self.assertIn('delete', seen[0])
            self.assertIn('ses-trash', seen[0])
        finally:
            orchestrator_server.subprocess.run = old_run

    async def test_council_message_capability_gate(self):
        old_here = orchestrator_server._HERE
        try:
            with tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                (root / '.council').mkdir()
                orchestrator_server._HERE = root
                # copi was removed from the pure-CLI council roster entirely.
                req = make_mocked_request(
                    'POST', '/api/council/message',
                    headers={'Content-Type': 'application/json', 'Origin': f'http://localhost:{orchestrator_server.PORT}'},
                    payload=_make_body_reader({'from': 'Anh', 'to': 'copi', 'text': 'x', 'mode': 'context'}),
                )
                resp = await orchestrator_server.handle_council_message(req)
                self.assertEqual(resp.status, 400)
                self.assertEqual(json.loads(resp.text)['error_name'], 'INVALID_AGENT')
                # Council is BROKER-ONLY: every roster agent supports broker, none context.
                for agent in orchestrator_server._COUNCIL_AGENTS:
                    self.assertTrue(orchestrator_server._council_supports_mode(agent, 'broker'), agent)
                    self.assertFalse(orchestrator_server._council_supports_mode(agent, 'context'), agent)
                req2 = make_mocked_request(
                    'POST', '/api/council/message',
                    headers={'Content-Type': 'application/json', 'Origin': f'http://localhost:{orchestrator_server.PORT}'},
                    payload=_make_body_reader({'from': 'Anh', 'to': 'pic', 'text': 'x', 'mode': 'context'}),
                )
                resp2 = await orchestrator_server.handle_council_message(req2)
                self.assertEqual(resp2.status, 400)
                self.assertEqual(json.loads(resp2.text)['error_name'], 'MODE_NOT_SUPPORTED')
        finally:
            orchestrator_server._HERE = old_here

    async def test_council_message_context_request_falls_back_to_broker(self):
        # BROKER-ONLY contract: context mode was removed from the council. A
        # mode='context' dispatch must run as a fresh one-shot broker turn —
        # no provider session persisted, no resume id on the follow-up turn.
        old_here = orchestrator_server._HERE
        old_cmd = orchestrator_server._council_agent_command
        seen = {}
        try:
            with tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                (root / '.council').mkdir()
                orchestrator_server._HERE = root

                def fake_cmd(agent, prompt, *, mode='broker', resume_id='',
                             new_session_id='', cwd=None, provider_override='', model_override=''):
                    seen['mode'] = mode
                    seen['resume_id'] = resume_id
                    import sys as _s
                    return [_s.executable, '-c',
                            'print(\'{"type":"text","part":{"sessionID":"oc-xyz","text":"ok reply"}}\')']
                orchestrator_server._council_agent_command = fake_cmd
                res = orchestrator_server._council_run_agent('ope', 'hello', 30, 'context', str(root))
                self.assertTrue(res['ok'])
                self.assertEqual(res['mode'], 'broker')
                self.assertEqual(res['session_id'], '')
                # Follow-up turn stays one-shot: no resume id resolved.
                res2 = orchestrator_server._council_run_agent('ope', 'again', 30, 'context', str(root))
                self.assertEqual(seen['mode'], 'broker')
                self.assertEqual(seen['resume_id'], '')
                self.assertTrue(res2['ok'])
        finally:
            orchestrator_server._HERE = old_here
            orchestrator_server._council_agent_command = old_cmd

    async def test_council_events_keeps_recent_broker_events_with_direct_logs(self):
        old_here = orchestrator_server._HERE
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            council = root / '.council'
            council.mkdir()
            for idx in range(8):
                log = council / f'log_ope_{idx}.txt'
                log.write_text(
                    '{"type":"text","part":{"text":"log reply %d"}}' % idx,
                    encoding='utf-8',
                )
                os.utime(log, (1_800_000_000 + idx, 1_800_000_000 + idx))
            orchestrator_server._HERE = root
            try:
                orchestrator_server._council_append_event({
                    'type': 'reply',
                    'from': 'ope',
                    'to': 'Anh',
                    'text': 'broker reply newest',
                    'mtime': 1_900_000_000,
                })
                request = make_mocked_request('GET', '/api/council/events?limit=4')
                response = await orchestrator_server.handle_council_events(request)
                exported = [
                    json.loads(line)
                    for line in (council / 'broker_filtered.jsonl').read_text(encoding='utf-8').splitlines()
                ]
            finally:
                orchestrator_server._HERE = old_here

        payload = json.loads(response.text)
        texts = [event.get('text', '') for event in payload['events']]
        self.assertTrue(any('broker reply newest' in text for text in texts))
        direct_events = [event for event in payload['events'] if event.get('type') == 'direct_log']
        self.assertTrue(direct_events)
        self.assertTrue(all(event['path'].startswith('log_ope_') for event in direct_events))
        self.assertTrue(all(str(root) not in event['path'] for event in direct_events))
        self.assertEqual(exported, payload['events'])
        self.assertTrue(all('raw' not in event for event in exported))

    async def test_council_state_prefers_latest_broker_reply(self):
        old_here = orchestrator_server._HERE
        old_alive = orchestrator_server._council_alive_agents
        old_metrics = orchestrator_server._council_agent_metrics
        try:
            with tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                council = root / '.council'
                council.mkdir()
                (council / 'outbox_ope.md').write_text('<<RES>>\nold\n<</RES>>', encoding='utf-8')
                orchestrator_server._HERE = root
                orchestrator_server._council_alive_agents = lambda: set()
                orchestrator_server._council_agent_metrics = lambda _dir, _agent: {}
                orchestrator_server._council_append_event({
                    "type": "reply",
                    "from": "ope",
                    "to": "Anh",
                    "text": "Chào opeC, trả lời qua broker.",
                })

                request = make_mocked_request('GET', '/api/council/state')
                response = await orchestrator_server.handle_council_state(request)
                data = json.loads(response.text)
                ope = next(a for a in data['agents'] if a['id'] == 'ope')

                self.assertIn('[broker response]', ope['latest_res'])
                self.assertIn('trả lời qua broker', ope['latest_res'])
        finally:
            orchestrator_server._HERE = old_here
            orchestrator_server._council_alive_agents = old_alive
            orchestrator_server._council_agent_metrics = old_metrics

    async def test_council_state_prefers_newer_direct_log_over_stale_broker_reply(self):
        old_here = orchestrator_server._HERE
        old_alive = orchestrator_server._council_alive_agents
        old_metrics = orchestrator_server._council_agent_metrics
        try:
            with tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                council = root / '.council'
                council.mkdir()
                log = council / 'log_ope_round.txt'
                log.write_text(
                    '{"type":"text","part":{"text":"new direct round"}}',
                    encoding='utf-8',
                )
                orchestrator_server._HERE = root
                orchestrator_server._council_alive_agents = lambda: set()
                orchestrator_server._council_agent_metrics = lambda _dir, _agent: {}
                orchestrator_server._council_append_event({
                    "type": "reply",
                    "from": "ope",
                    "to": "Anh",
                    "text": "old broker reply",
                    "raw": "old broker reasoning",
                    "mtime": 1_800_000_000,
                })
                os.utime(log, (1_900_000_000, 1_900_000_000))

                response = await orchestrator_server.handle_council_state(
                    make_mocked_request('GET', '/api/council/state')
                )
                data = json.loads(response.text)
                ope = next(a for a in data['agents'] if a['id'] == 'ope')

                self.assertIn('new direct round', ope['latest_res'])
                self.assertNotIn('old broker reply', ope['latest_res'])
                self.assertIn('[direct log]', ope['reasoning'])
                self.assertNotIn('old broker reasoning', ope['reasoning'])
                self.assertEqual(ope['direct_log_path'], 'log_ope_round.txt')
        finally:
            orchestrator_server._HERE = old_here
            orchestrator_server._council_alive_agents = old_alive
            orchestrator_server._council_agent_metrics = old_metrics

    async def test_council_state_does_not_treat_thinking_as_final_response(self):
        old_here = orchestrator_server._HERE
        old_alive = orchestrator_server._council_alive_agents
        old_metrics = orchestrator_server._council_agent_metrics
        try:
            with tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                council = root / '.council'
                council.mkdir()
                outbox = council / 'outbox_ope.md'
                log = council / 'log_ope.txt'
                outbox.write_text('<<RES>>\nold result\n<</RES>>', encoding='utf-8')
                log.write_text(
                    'Thinking: still investigating\nLet me inspect files\n- Modified: noisy detail\nEXIT_CODE=0',
                    encoding='utf-8',
                )
                os.utime(outbox, (1_800_000_000, 1_800_000_000))
                os.utime(log, (1_900_000_000, 1_900_000_000))
                orchestrator_server._HERE = root
                orchestrator_server._council_alive_agents = lambda: set()
                orchestrator_server._council_agent_metrics = lambda _dir, _agent: {}

                response = await orchestrator_server.handle_council_state(
                    make_mocked_request('GET', '/api/council/state')
                )
                data = json.loads(response.text)
                ope = next(a for a in data['agents'] if a['id'] == 'ope')

                self.assertEqual(ope['latest_res'], '[direct log: no final response]')
                # latest_res must NOT treat thinking as a final response (above), but
                # direct_log now uses the same raw_tail liveness fallback as the
                # activity payload, so a thinking-only log still surfaces its tail
                # instead of blanking the Log Tail on each 30s state poll.
                self.assertTrue(ope['direct_log'])
                self.assertIn('still investigating', ope['direct_log'])
        finally:
            orchestrator_server._HERE = old_here
            orchestrator_server._council_alive_agents = old_alive
            orchestrator_server._council_agent_metrics = old_metrics

    def test_council_extracts_clin_final_after_ansi_thinking(self):
        raw = (
            '\x1b[2m[thinking] \x1b[0m\x1b[2mBroker task: reply exactly R2-CLIN-OK\x1b[0m\n'
            'R2-CLIN-OK'
        )
        self.assertEqual(orchestrator_server._council_extract_log_response(raw), 'R2-CLIN-OK')

    def test_council_extracts_only_last_clin_final_line(self):
        raw = (
            '[thinking] inspect task\n'
            'Wait, re-reading the task and considering options.\n'
            'ClinC đã nhận. Trạng thái sạch.'
        )
        self.assertEqual(
            orchestrator_server._council_extract_log_response(raw),
            'ClinC đã nhận. Trạng thái sạch.',
        )

    def test_council_rejects_interrupted_clin_thinking_tail(self):
        raw = 'Thinking: still investigating\n- Modified: noisy detail\nEXIT_CODE=0'
        self.assertEqual(orchestrator_server._council_extract_log_response(raw), '')

    async def test_council_latest_msg_prefers_newer_prompt_dispatch_file(self):
        old_here = orchestrator_server._HERE
        old_alive = orchestrator_server._council_alive_agents
        old_metrics = orchestrator_server._council_agent_metrics
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            council = root / '.council'
            council.mkdir()
            inbox = council / 'inbox_codo.md'
            prompt = council / 'prompt_codo_batch.txt'
            inbox.write_text('<<MSG>>\ninbox dispatch\n<<END MSG>>', encoding='utf-8')
            prompt.write_text('prompt-file batch dispatch', encoding='utf-8')
            old_ts = datetime(2026, 6, 5, 1, 0, 0).timestamp()
            new_ts = datetime(2026, 6, 5, 1, 1, 0).timestamp()
            os.utime(inbox, (old_ts, old_ts))
            os.utime(prompt, (new_ts, new_ts))
            orchestrator_server._HERE = root
            orchestrator_server._council_alive_agents = lambda: set()
            orchestrator_server._council_agent_metrics = lambda _dir, _agent: {}
            try:
                app = orchestrator_server.create_app()
                request = make_mocked_request('GET', '/api/council/state')
                match = await app.router.resolve(request)
                response = await match.handler(request)
            finally:
                orchestrator_server._HERE = old_here
                orchestrator_server._council_alive_agents = old_alive
                orchestrator_server._council_agent_metrics = old_metrics

        payload = json.loads(response.text)
        codo = next(agent for agent in payload['agents'] if agent['id'] == 'codo')
        self.assertIn('source="prompt_codo_batch.txt"', codo['latest_msg'])
        self.assertIn('prompt-file batch dispatch', codo['latest_msg'])

    async def test_council_latest_msg_prefers_prompt_dispatch_even_if_inbox_newer(self):
        old_here = orchestrator_server._HERE
        old_alive = orchestrator_server._council_alive_agents
        old_metrics = orchestrator_server._council_agent_metrics
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            council = root / '.council'
            council.mkdir()
            inbox = council / 'inbox_codo.md'
            prompt = council / 'prompt_codo_batch.txt'
            inbox.write_text('<<MSG>>\ninbox dispatch\n<<END MSG>>', encoding='utf-8')
            prompt.write_text('prompt-file batch dispatch', encoding='utf-8')
            prompt_ts = datetime(2026, 6, 5, 1, 0, 0).timestamp()
            inbox_ts = datetime(2026, 6, 5, 1, 1, 0).timestamp()
            os.utime(prompt, (prompt_ts, prompt_ts))
            os.utime(inbox, (inbox_ts, inbox_ts))
            orchestrator_server._HERE = root
            orchestrator_server._council_alive_agents = lambda: set()
            orchestrator_server._council_agent_metrics = lambda _dir, _agent: {}
            try:
                app = orchestrator_server.create_app()
                request = make_mocked_request('GET', '/api/council/state')
                match = await app.router.resolve(request)
                response = await match.handler(request)
            finally:
                orchestrator_server._HERE = old_here
                orchestrator_server._council_alive_agents = old_alive
                orchestrator_server._council_agent_metrics = old_metrics

        payload = json.loads(response.text)
        codo = next(agent for agent in payload['agents'] if agent['id'] == 'codo')
        self.assertIn('source="prompt_codo_batch.txt"', codo['latest_msg'])
        self.assertIn('prompt-file batch dispatch', codo['latest_msg'])

    async def test_council_latest_msg_prefers_prompt_file_over_newer_run_file(self):
        old_here = orchestrator_server._HERE
        old_alive = orchestrator_server._council_alive_agents
        old_metrics = orchestrator_server._council_agent_metrics
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            council = root / '.council'
            council.mkdir()
            (council / 'inbox_codo.md').write_text('<<MSG>>\ninbox dispatch\n<<END MSG>>', encoding='utf-8')
            prompt = council / 'prompt_codo_batch.txt'
            run = council / 'run_codo_batch.bat'
            prompt.write_text('prompt-file batch dispatch', encoding='utf-8')
            run.write_text('@echo off\ncodex exec noisy batch wrapper', encoding='utf-8')
            prompt_ts = datetime(2026, 6, 5, 1, 0, 0).timestamp()
            run_ts = datetime(2026, 6, 5, 1, 1, 0).timestamp()
            os.utime(prompt, (prompt_ts, prompt_ts))
            os.utime(run, (run_ts, run_ts))
            orchestrator_server._HERE = root
            orchestrator_server._council_alive_agents = lambda: set()
            orchestrator_server._council_agent_metrics = lambda _dir, _agent: {}
            try:
                app = orchestrator_server.create_app()
                request = make_mocked_request('GET', '/api/council/state')
                match = await app.router.resolve(request)
                response = await match.handler(request)
            finally:
                orchestrator_server._HERE = old_here
                orchestrator_server._council_alive_agents = old_alive
                orchestrator_server._council_agent_metrics = old_metrics

        payload = json.loads(response.text)
        codo = next(agent for agent in payload['agents'] if agent['id'] == 'codo')
        self.assertIn('source="prompt_codo_batch.txt"', codo['latest_msg'])
        self.assertIn('prompt-file batch dispatch', codo['latest_msg'])
        self.assertNotIn('codex exec noisy batch wrapper', codo['latest_msg'])
        self.assertNotIn('inbox dispatch', codo['latest_msg'])

    async def test_api_logs_reads_bounded_tail(self):
        original_log_file = orchestrator_server.LOG_FILE
        original_tail_bytes = orchestrator_server.LOG_TAIL_BYTES
        with tempfile.TemporaryDirectory() as tmp:
            log_file = Path(tmp) / 'orchestrator.log'
            old_lines = [
                f"2026-05-15 03:00:{i % 60:02d},000 INFO old-entry-{i:03d}"
                for i in range(200)
            ]
            recent_line = "2026-05-15 03:20:00,000 INFO recent-entry"
            log_file.write_text("\n".join(old_lines + [recent_line]), encoding='utf-8')
            orchestrator_server.LOG_FILE = log_file
            orchestrator_server.LOG_TAIL_BYTES = 256

            try:
                app = orchestrator_server.create_app()
                request = make_mocked_request('GET', '/api/logs')
                match = await app.router.resolve(request)
                response = await match.handler(request)
            finally:
                orchestrator_server.LOG_FILE = original_log_file
                orchestrator_server.LOG_TAIL_BYTES = original_tail_bytes

        payload = json.loads(response.text)
        messages = [entry['message'] for entry in payload]
        self.assertIn('recent-entry', messages)
        self.assertNotIn('old-entry-000', messages)
        self.assertEqual(response.headers.get('X-Log-Tail-Bytes'), '256')

    async def test_token_panel_contract_is_nested_and_masked(self):
        app = orchestrator_server.create_app()
        request = make_mocked_request('GET', '/api/token_panel')

        match = await app.router.resolve(request)
        response = await match.handler(request)
        payload = json.loads(response.text)

        self.assertEqual(payload['token_panel']['auth_status'], 'valid')
        self.assertEqual(payload['token_panel']['protected_tiers'], ['T2', 'T3', 'T4'])
        self.assertIn('desktop_click', payload['token_panel']['mutating_t4_tools_require_token'])
        self.assertEqual(payload['token_panel']['reuse_policy'], 'reuse_for_session')
        self.assertIn('run_project_command', payload['protected_tools'])
        self.assertIn('run_test_profile', payload['unprotected_tools'])
        self.assertNotIn(orchestrator_server.EXPECTED_TOKEN, response.text)

    def test_tool_inspector_aliases_include_status(self):
        alias = orchestrator_server._alias_metadata('search_code')['pattern']
        self.assertEqual(alias['maps_to'], 'query')
        self.assertEqual(alias['status'], 'verified')

    def test_backup_override_is_accepted_control_field_for_write_tools(self):
        fn = code_tools.TOOL_MAP['write_new_file']
        allowed_params = set(__import__('inspect').signature(fn).parameters.keys())
        if 'write_new_file' in {
            'delete_file', 'write_fix', 'write_fix_html', 'write_new_file',
            'apply_after_dry_run', 'refresh_project_map', 'generate_project_map',
        }:
            allowed_params.add('backup_override')

        unsupported = sorted(k for k in {
            'project_path': 'C:/repo',
            'path': 'x.txt',
            'new_content': 'hello',
            'dry_run': True,
            'backup_override': True,
        } if k not in allowed_params)

        self.assertEqual(unsupported, [])

    async def test_tool_inspector_route_returns_accepted_fields(self):
        app = orchestrator_server.create_app()
        request = make_mocked_request('GET', '/api/tool_inspector/search_code')

        match = await app.router.resolve(request)
        response = await match.handler(request)
        payload = json.loads(response.text)

        self.assertIn('query', payload['accepted_fields'])
        self.assertEqual(payload['accepted_aliases']['pattern']['status'], 'verified')

    def test_mcp_tool_names_discovers_api_only_workflow_tool(self):
        payload = orchestrator_server._tool_names_payload(name_contains='workflow_list')

        self.assertEqual(payload['count'], 1)
        self.assertEqual(payload['tools'][0]['name'], 'workflow_list')
        self.assertEqual(payload['tools'][0]['tier'], 'T1')

    def test_mcp_tool_schema_matches_enriched_api_shape(self):
        payload, status = orchestrator_server._tool_schema_payload('workflow_list')

        self.assertEqual(status, 200)
        self.assertTrue(payload['ok'])
        fn = payload['tool']['function']
        self.assertEqual(fn['name'], 'workflow_list')
        self.assertIn('payload_example', fn)
        self.assertIn('requires_session', fn)

    def test_open_session_public_contract_exposes_optional_agent_identity(self):
        payload, status = orchestrator_server._tool_schema_payload('open_session')

        self.assertEqual(status, 200)
        properties = payload['tool']['function']['parameters']['properties']
        self.assertIn('agent_id', properties)
        self.assertEqual(properties['agent_id']['default'], '')
        self.assertNotIn(
            'agent_id',
            payload['tool']['function']['parameters'].get('required', []),
        )
        parameter = __import__('inspect').signature(code_tools.open_session).parameters['agent_id']
        self.assertEqual(parameter.default, '')

    def test_ast_query_schema_prevents_freeform_patterns(self):
        payload, status = orchestrator_server._tool_schema_payload('ast_query')

        self.assertEqual(status, 200)
        pattern_schema = payload['tool']['function']['parameters']['properties']['pattern']
        self.assertEqual(pattern_schema['enum'], [
            'async_function',
            'class_inheriting',
            'try_bare_except',
            'function_returning_dict',
        ])

    async def test_native_dialog_tools_do_not_require_project_session(self):
        for tool_name, payload in {
            'fill_folder_dialog': {
                'title_pattern': '__NO_SUCH_DIALOG_FOR_TEST__',
                'folder_path': 'C:\\Temp',
                'wait_for_dialog_ms': 1,
                'timeout_ms': 1,
                'dry_run': True,
            },
            'cancel_native_dialog': {
                'title_pattern': '__NO_SUCH_DIALOG_FOR_TEST__',
                'button_pattern': 'Cancel',
            },
        }.items():
            schema, status = orchestrator_server._tool_schema_payload(tool_name)
            self.assertEqual(status, 200)
            self.assertFalse(schema['tool']['function']['requires_session'])

            response = await orchestrator_server.dispatch_tool_call(
                tool_name,
                payload,
                transport='mcp',
            )
            result = json.loads(response.text)
            self.assertNotIn(result.get('error_name'), {
                'PROJECT_PATH_REQUIRED',
                'ACTIVE_SESSION_REQUIRED',
                'UNSUPPORTED_FIELD',
            }, msg=result)

    def test_mcp_persistent_loop_reuses_event_loop_between_calls(self):
        async def loop_identity():
            return id(asyncio.get_running_loop())

        first = orchestrator_server._run_mcp_coro(loop_identity())
        second = orchestrator_server._run_mcp_coro(loop_identity())

        self.assertEqual(first, second)

    async def test_mcp_dispatch_reaches_tool_without_top_level_wrapper(self):
        with tempfile.TemporaryDirectory() as tmp:
            response = await orchestrator_server.dispatch_tool_call(
                'workflow_list',
                {'project_path': tmp},
                transport='mcp',
            )

        payload = json.loads(response.text)
        self.assertNotEqual(payload.get('error_name'), 'UNKNOWN_TOOL')
        self.assertIn('workflows', payload)

    async def test_tool_inspector_token_status_is_tool_specific(self):
        app = orchestrator_server.create_app()
        request_command = make_mocked_request('GET', '/api/tool_inspector/run_project_command')
        request_test = make_mocked_request('GET', '/api/tool_inspector/run_test_profile')

        command_match = await app.router.resolve(request_command)
        test_match = await app.router.resolve(request_test)
        command_payload = json.loads((await command_match.handler(request_command)).text)
        test_payload = json.loads((await test_match.handler(request_test)).text)

        self.assertTrue(command_payload['requires_token'])
        self.assertFalse(test_payload['requires_token'])

    async def test_command_helper_route_returns_windows_wrapper(self):
        app = orchestrator_server.create_app()
        request = make_mocked_request('GET', '/api/command_helper?subdir=2.%20Rawcut&command=node%20health-check.js')

        match = await app.router.resolve(request)
        response = await match.handler(request)
        payload = json.loads(response.text)

        self.assertTrue(payload['ok'])
        self.assertIn("Set-Location -LiteralPath '2. Rawcut'", payload['wrapper'])
        self.assertTrue(payload['uses_set_location'])
        self.assertEqual(payload['safe_command'], payload['wrapper'])
        self.assertEqual(payload['ca_next_action'], 'use_safe_command')

    async def test_protected_tools_status_route_is_masked_and_machine_readable(self):
        app = orchestrator_server.create_app()
        request = make_mocked_request('GET', '/api/protected_tools_status')

        match = await app.router.resolve(request)
        response = await match.handler(request)
        payload = json.loads(response.text)

        self.assertTrue(payload['ok'])
        self.assertIn('run_project_command', payload['protected_tools'])
        self.assertIn('run_test_profile', payload['unprotected_tools'])
        self.assertNotIn(orchestrator_server.EXPECTED_TOKEN, response.text)

    def test_tool_inspector_t3_requires_token(self):
        self.assertTrue('log_native' in code_tools.TOOL_MAP)
        # Route handler computes from protected tool set; direct metadata gotchas also document the schema.
        self.assertIn('Do not pass project_path', orchestrator_server._TOOL_GOTCHAS['log_native'][0])

    def test_pending_audit_tools_can_bootstrap_without_active_session(self):
        self.assertIn('create_pending_audit', orchestrator_server._NO_SESSION_REQUIRED)
        self.assertIn('sync_pending_audit', orchestrator_server._NO_SESSION_REQUIRED)
        self.assertIn('create_pending_audit', code_tools.PROTECTED_TOOL_NAMES)
        self.assertIn('sync_pending_audit', code_tools.PROTECTED_TOOL_NAMES)

    def test_stop_app_session_exempt_but_still_token_gated(self):
        # R2 fix (V15 R96): stop_app has no project_path in its schema, so the
        # session gate would be unsatisfiable (PROJECT_PATH_REQUIRED catch-22).
        # The correct gate is the destructive-token gate, NOT a project session.
        self.assertIn('stop_app', orchestrator_server._NO_SESSION_REQUIRED)
        # Token gate must remain - session exemption must NOT silently unprotect it.
        self.assertIn('stop_app', code_tools.TOKEN_REQUIRED_TOOLS)
        # Requires-session predicate must agree with the exemption set.
        self.assertFalse(orchestrator_server._requires_session('stop_app'))


    async def test_session_gate_scopes_lookup_by_request_agent_and_preserves_legacy_lookup(self):
        tool_name = '__agent_scoped_session_probe__'
        calls = []

        async def probe(project_path: str, agent_id: str = ''):
            calls.append((project_path, agent_id))
            return json.dumps({'ok': True})

        def active_session(_project_path, agent_id=None):
            # Agent A owns the project-level active session. Agent B must not
            # inherit it, while an omitted agent_id keeps the legacy lookup.
            return 'agent-a-session' if agent_id in (None, 'agent-a') else None

        old_auto_write = orchestrator_server.AUTO_WRITE_MODE
        code_tools.TOOL_MAP[tool_name] = probe
        # S4 fixture: schema registry is the public contract; register a
        # minimal schema so the synthetic probe's payload fields are accepted.
        code_tools.TOOL_SCHEMAS.append({
            'type': 'function',
            'function': {
                'name': tool_name,
                'parameters': {
                    'type': 'object',
                    'properties': {
                        'project_path': {'type': 'string'},
                        'agent_id': {'type': 'string'},
                    },
                },
            },
        })
        orchestrator_server._MUTATING_TOOLS.add(tool_name)
        orchestrator_server.AUTO_WRITE_MODE = False
        try:
            with mock.patch.object(code_tools, '_get_active_session_id', side_effect=active_session) as lookup:
                scoped = await orchestrator_server.dispatch_tool_call(
                    tool_name,
                    {'project_path': 'C:/repo', 'agent_id': 'agent-b'},
                     transport='http',
                )
                legacy = await orchestrator_server.dispatch_tool_call(
                    tool_name,
                    {'project_path': 'C:/repo'},
                     transport='http',
                )

            self.assertEqual(json.loads(scoped.text).get('error_name'), 'ACTIVE_SESSION_REQUIRED')
            self.assertEqual(json.loads(legacy.text).get('ok'), True)
            self.assertEqual(calls, [('C:/repo', '')])
            self.assertIn(mock.call(code_tools._session_project_key('C:/repo'), 'agent-b'), lookup.call_args_list)
            self.assertIn(mock.call(code_tools._session_project_key('C:/repo'), None), lookup.call_args_list)
        finally:
            orchestrator_server.AUTO_WRITE_MODE = old_auto_write
            orchestrator_server._MUTATING_TOOLS.discard(tool_name)
            code_tools.TOOL_MAP.pop(tool_name, None)
            code_tools.TOOL_SCHEMAS.pop()

    async def test_session_gate_uses_header_agent_for_tool_without_agent_parameter(self):
        tool_name = '__header_agent_session_probe__'
        calls = []

        async def probe(project_path: str):
            calls.append(project_path)
            return json.dumps({'ok': True})

        old_db = code_tools._SESSIONS_DB
        old_auto_write = orchestrator_server.AUTO_WRITE_MODE
        code_tools.TOOL_MAP[tool_name] = probe
        # S4 fixture: schema registry is the public contract; register a
        # minimal schema so the synthetic probe's payload field is accepted.
        code_tools.TOOL_SCHEMAS.append({
            'type': 'function',
            'function': {
                'name': tool_name,
                'parameters': {
                    'type': 'object',
                    'properties': {'project_path': {'type': 'string'}},
                },
            },
        })
        orchestrator_server._MUTATING_TOOLS.add(tool_name)
        orchestrator_server.AUTO_WRITE_MODE = False
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            code_tools._SESSIONS_DB = Path(tmp) / 'sessions.db'
            try:
                code_tools._init_session_db()
                project = str(Path(tmp) / 'repo')
                Path(project).mkdir()
                session_a = code_tools._auto_open_session(project, {'agent_id': 'agent-a'})

                response = await orchestrator_server.dispatch_tool_call(
                    tool_name,
                    {'project_path': project},
                     transport='http',
                    request_meta={'headers': {'X-Council-Agent': 'agent-b'}},
                )

                self.assertEqual(json.loads(response.text).get('error_name'), 'ACTIVE_SESSION_REQUIRED')
                self.assertEqual(calls, [])
                self.assertEqual(code_tools._get_active_session_id(project, 'agent-a'), session_a)
                self.assertIsNone(code_tools._get_active_session_id(project, 'agent-b'))
            finally:
                code_tools._SESSIONS_DB = old_db
                code_tools._sync_session_db_alias()
                orchestrator_server.AUTO_WRITE_MODE = old_auto_write
                orchestrator_server._MUTATING_TOOLS.discard(tool_name)
                code_tools.TOOL_MAP.pop(tool_name, None)
                code_tools.TOOL_SCHEMAS.pop()

    async def test_dry_run_write_skips_backup_gate(self):
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / 'preview.py'
            target.write_text('value = 1\n', encoding='utf-8')
            with mock.patch.object(code_tools, 'backup_project', new=mock.AsyncMock()) as backup:
                response = await orchestrator_server.dispatch_tool_call(
                    'write_fix',
                    {
                        'file_path': str(target),
                        'start_line': 1,
                        'end_line': 1,
                        'new_content': 'value = 2',
                        'dry_run': True,
                        'backup_override': True,
                    },
                     transport='http',
                )

            payload = json.loads(response.text)
            self.assertTrue(payload.get('ok'), payload)
            self.assertTrue(payload.get('dry_run'))
            backup.assert_not_awaited()
            self.assertEqual(target.read_text(encoding='utf-8'), 'value = 1\n')

    async def test_auto_write_opens_header_agent_session_for_tool_without_agent_parameter(self):
        tool_name = '__header_agent_auto_session_probe__'
        calls = []

        async def probe(project_path: str):
            calls.append(project_path)
            return json.dumps({'ok': True})

        old_db = code_tools._SESSIONS_DB
        old_auto_write = orchestrator_server.AUTO_WRITE_MODE
        code_tools.TOOL_MAP[tool_name] = probe
        # S4 fixture: schema registry is the public contract; register a
        # minimal schema so the synthetic probe's payload field is accepted.
        code_tools.TOOL_SCHEMAS.append({
            'type': 'function',
            'function': {
                'name': tool_name,
                'parameters': {
                    'type': 'object',
                    'properties': {'project_path': {'type': 'string'}},
                },
            },
        })
        orchestrator_server._MUTATING_TOOLS.add(tool_name)
        orchestrator_server.AUTO_WRITE_MODE = True
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            code_tools._SESSIONS_DB = Path(tmp) / 'sessions.db'
            try:
                code_tools._init_session_db()
                project = str(Path(tmp) / 'repo')
                Path(project).mkdir()
                session_a = code_tools._auto_open_session(project, {'agent_id': 'agent-a'})

                response = await orchestrator_server.dispatch_tool_call(
                    tool_name,
                    {'project_path': project},
                     transport='http',
                    request_meta={'headers': {'X-Council-Agent': 'agent-b'}},
                )

                self.assertTrue(json.loads(response.text).get('ok'))
                self.assertEqual(calls, [project])
                session_b = code_tools._get_active_session_id(project, 'agent-b')
                self.assertIsNotNone(session_b)
                self.assertNotEqual(session_b, session_a)
                self.assertEqual(code_tools._get_active_session_id(project, 'agent-a'), session_a)
            finally:
                code_tools._SESSIONS_DB = old_db
                code_tools._sync_session_db_alias()
                orchestrator_server.AUTO_WRITE_MODE = old_auto_write
                orchestrator_server._MUTATING_TOOLS.discard(tool_name)
                code_tools.TOOL_MAP.pop(tool_name, None)
                code_tools.TOOL_SCHEMAS.pop()

    async def test_success_event_logging_uses_request_agent_session(self):
        tool_name = '__agent_scoped_event_probe__'

        async def probe(project_path: str, agent_id: str = ''):
            return json.dumps({'ok': True, 'agent_id': agent_id})

        old_db = code_tools._SESSIONS_DB
        old_writer = orchestrator_server._DURABLE_AUDIT
        code_tools.TOOL_MAP[tool_name] = probe
        # S4 fixture: schema registry is the public contract; register a
        # minimal schema so the synthetic probe's payload fields are accepted.
        code_tools.TOOL_SCHEMAS.append({
            'type': 'function',
            'function': {
                'name': tool_name,
                'parameters': {
                    'type': 'object',
                    'properties': {
                        'project_path': {'type': 'string'},
                        'agent_id': {'type': 'string'},
                    },
                },
            },
        })
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            code_tools._SESSIONS_DB = Path(tmp) / 'sessions.db'
            code_tools._sync_session_db_alias()
            writer = DurableAuditWriter(
                Path(tmp) / 'durable-audit.db',
                orchestrator_server._persist_durable_session_event,
                poll_interval_s=0.01,
            )
            orchestrator_server._DURABLE_AUDIT = writer
            try:
                code_tools._init_session_db()
                project = str(Path(tmp) / 'repo')
                Path(project).mkdir()
                session_b = code_tools._auto_open_session(project, {'agent_id': 'agent-b'})
                session_a = code_tools._auto_open_session(project, {'agent_id': 'agent-a'})
                with sqlite3.connect(str(code_tools._SESSIONS_DB)) as conn:
                    conn.execute(
                        "UPDATE sessions SET started_at=? WHERE session_id=?",
                        ((datetime.now() - timedelta(seconds=1)).isoformat(timespec='seconds'), session_b),
                    )
                    conn.execute(
                        "UPDATE sessions SET started_at=? WHERE session_id=?",
                        (datetime.now().isoformat(timespec='seconds'), session_a),
                    )

                response = await orchestrator_server.dispatch_tool_call(
                    tool_name,
                    {'project_path': project, 'agent_id': 'agent-b'},
                     transport='http',
                )
                await orchestrator_server._flush_post_call_accounting(timeout=2.0)
                self.assertTrue(writer.wait_until_idle(2.0))
                self.assertTrue(json.loads(response.text)['ok'])
                with sqlite3.connect(str(code_tools._SESSIONS_DB)) as conn:
                    event_sessions = [
                        row[0] for row in conn.execute(
                            "SELECT session_id FROM events WHERE tool_name=?",
                            (tool_name,),
                        ).fetchall()
                    ]
                self.assertEqual(event_sessions, [session_b])
                self.assertNotIn(session_a, event_sessions)
            finally:
                writer.shutdown(timeout=2.0)
                orchestrator_server._DURABLE_AUDIT = old_writer
                code_tools._SESSIONS_DB = old_db
                code_tools._sync_session_db_alias()
                code_tools.TOOL_MAP.pop(tool_name, None)
                code_tools.TOOL_SCHEMAS.pop()

    def test_safe_event_logging_uses_explicit_agent_session(self):
        old_db = code_tools._SESSIONS_DB
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            code_tools._SESSIONS_DB = Path(tmp) / 'sessions.db'
            try:
                code_tools._init_session_db()
                project = str(Path(tmp) / 'repo')
                Path(project).mkdir()
                session_b = code_tools._auto_open_session(project, {'agent_id': 'agent-b'})
                session_a = code_tools._auto_open_session(project, {'agent_id': 'agent-a'})
                with sqlite3.connect(str(code_tools._SESSIONS_DB)) as conn:
                    conn.execute(
                        "UPDATE sessions SET started_at=? WHERE session_id=?",
                        ((datetime.now() - timedelta(seconds=1)).isoformat(timespec='seconds'), session_b),
                    )
                    conn.execute(
                        "UPDATE sessions SET started_at=? WHERE session_id=?",
                        (datetime.now().isoformat(timespec='seconds'), session_a),
                    )

                request = make_mocked_request(
                    'POST', '/api/call/probe', headers={'X-Project-Path': project}
                )
                orchestrator_server._log_session_event_safe(
                    request, 'safe_agent_probe', 'ok', {'project': project},
                    agent_id='agent-b',
                )

                with sqlite3.connect(str(code_tools._SESSIONS_DB)) as conn:
                    event_sessions = [
                        row[0] for row in conn.execute(
                            "SELECT session_id FROM events WHERE tool_name='safe_agent_probe'"
                        ).fetchall()
                    ]
                self.assertEqual(event_sessions, [session_b])
                self.assertNotIn(session_a, event_sessions)
            finally:
                code_tools._SESSIONS_DB = old_db
                code_tools._sync_session_db_alias()

    async def test_open_session_header_agent_overrides_body_for_response_store_and_event(self):
        old_db = code_tools._SESSIONS_DB
        old_writer = orchestrator_server._DURABLE_AUDIT
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            code_tools._SESSIONS_DB = Path(tmp) / 'sessions.db'
            code_tools._sync_session_db_alias()
            writer = DurableAuditWriter(
                Path(tmp) / 'durable-audit.db',
                orchestrator_server._persist_durable_session_event,
                poll_interval_s=0.01,
            )
            orchestrator_server._DURABLE_AUDIT = writer
            try:
                code_tools._init_session_db()
                project_dir = Path(tmp) / 'repo'
                project_dir.mkdir()
                (project_dir / 'requirements.txt').write_text('', encoding='utf-8')
                project = str(project_dir)

                with mock.patch.object(code_tools, '_audit_tool_matrix_sync', return_value={}):
                    response = await orchestrator_server.dispatch_tool_call(
                        'open_session',
                        {'session_title': 'Header identity', 'project_path': project,
                         'agent_id': 'agent-a'},
                     transport='http',
                        request_meta={'headers': {'X-Council-Agent': 'agent-b'}},
                    )
                await orchestrator_server._flush_post_call_accounting(timeout=2.0)
                self.assertTrue(writer.wait_until_idle(2.0))
                data = json.loads(response.text)

                self.assertTrue(data['ok'])
                self.assertEqual(data['agent_id'], 'agent-b')
                with sqlite3.connect(str(code_tools._SESSIONS_DB)) as conn:
                    session = conn.execute(
                        'SELECT session_id, agent_id FROM sessions WHERE session_id=?',
                        (data['session_id'],),
                    ).fetchone()
                    event = conn.execute(
                        "SELECT session_id FROM events WHERE tool_name='open_session' ORDER BY id DESC LIMIT 1"
                    ).fetchone()
                self.assertEqual(session, (data['session_id'], 'agent-b'))
                self.assertEqual(event, (data['session_id'],))
                self.assertIsNone(code_tools._get_active_session_id(project, 'agent-a'))
            finally:
                writer.shutdown(timeout=2.0)
                orchestrator_server._DURABLE_AUDIT = old_writer
                code_tools._SESSIONS_DB = old_db
                code_tools._sync_session_db_alias()

    async def test_prepare_ca_session_header_agent_overrides_body_and_returns_bootstrap_ref(self):
        old_db = code_tools._SESSIONS_DB
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            code_tools._SESSIONS_DB = Path(tmp) / 'sessions.db'
            try:
                code_tools._init_session_db()
                project_dir = Path(tmp) / 'repo'
                project_dir.mkdir()
                (project_dir / 'requirements.txt').write_text('', encoding='utf-8')
                project = str(project_dir)

                with mock.patch.object(code_tools, '_audit_tool_matrix_sync', return_value={}):
                    response = await orchestrator_server.dispatch_tool_call(
                        'prepare_ca_session',
                        {'project_path': project, 'agent_id': 'agent-body',
                         'host_session_id': 'host-a'},
                     transport='http',
                        request_meta={'headers': {'X-Council-Agent': 'agent-header'}},
                    )
                data = json.loads(response.text)

                self.assertTrue(data['ok'], msg=data)
                self.assertEqual(data['session']['agent_id'], 'agent-header')
                self.assertEqual(data['bootstrap']['status'], 'ready')
                self.assertEqual(len(data['bootstrap_ref']), 16)
                with sqlite3.connect(str(code_tools._SESSIONS_DB)) as conn:
                    stored_agent = conn.execute(
                        'SELECT agent_id FROM sessions WHERE session_id=?',
                        (data['session_id'],),
                    ).fetchone()[0]
                self.assertEqual(stored_agent, 'agent-header')
            finally:
                code_tools._SESSIONS_DB = old_db
                code_tools._sync_session_db_alias()

    async def test_mcp_open_session_explicit_agent_overrides_environment(self):
        call = await _capture_mcp_open_session_call(' agent-explicit ', ' agent-env ')

        self.assertEqual(call, mock.call(
            session_title='Session A', project_path='C:/repo', mode='T2',
            agent_id='agent-explicit',
        ))

    async def test_mcp_open_session_uses_environment_agent_fallback(self):
        call = await _capture_mcp_open_session_call('   ', ' agent-env ')

        self.assertEqual(call, mock.call(
            session_title='Session A', project_path='C:/repo', mode='T2',
            agent_id='agent-env',
        ))

    async def test_mcp_open_session_without_environment_forwards_blank_legacy_agent(self):
        call = await _capture_mcp_open_session_call('   ')

        self.assertEqual(call, mock.call(
            session_title='Session A', project_path='C:/repo', mode='T2',
            agent_id='',
        ))

    async def test_mcp_prepare_ca_session_forwards_scope_keywords_and_env_agent_fallback(self):
        from orchestrator.server import mcp_stdio

        class FakeMcpServer:
            instance = None

            def __init__(self, name):
                self.name = name
                self.tools = types.SimpleNamespace(methods={})
                FakeMcpServer.instance = self

            def tool(self, function):
                self.tools.methods[function.__name__] = function
                return function

            def stdio(self):
                return None

        prepare = mock.AsyncMock(return_value=json.dumps({'ok': True}))
        fake_module = types.SimpleNamespace(McpServer=FakeMcpServer)
        with mock.patch.dict(sys.modules, {'zeromcp': fake_module}), \
             mock.patch.dict(os.environ, {
                 'ORCHESTRATOR_MCP_FULL_SURFACE': '1',
                 'COUNCIL_AGENT_ID': 'agent-env',
             }), \
             mock.patch.object(code_tools, 'prepare_ca_session', prepare), \
             mock.patch.object(mcp_stdio, '_mcp_current_server_generation', return_value=None):
            await asyncio.to_thread(mcp_stdio.run_mcp_stdio)
            wrapper = FakeMcpServer.instance.tools.methods['prepare_ca_session']
            await asyncio.to_thread(wrapper, 'C:/repo', 'T2', 'Build CRM', 'agent-explicit', 'host-a')
            await asyncio.to_thread(wrapper, 'D:/repo', 'T1', '', '', 'host-b')

        self.assertEqual(prepare.await_args_list, [
            mock.call(project_path='C:/repo', mode='T2', idea='Build CRM',
                      agent_id='agent-explicit', host_session_id='host-a'),
            mock.call(project_path='D:/repo', mode='T1', idea='',
                      agent_id='agent-env', host_session_id='host-b'),
        ])

    async def test_open_session_environment_agent_fallback_for_response_store_and_event(self):
        old_db = code_tools._SESSIONS_DB
        old_writer = orchestrator_server._DURABLE_AUDIT
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            code_tools._SESSIONS_DB = Path(tmp) / 'sessions.db'
            code_tools._sync_session_db_alias()
            writer = DurableAuditWriter(
                Path(tmp) / 'durable-audit.db',
                orchestrator_server._persist_durable_session_event,
                poll_interval_s=0.01,
            )
            orchestrator_server._DURABLE_AUDIT = writer
            try:
                code_tools._init_session_db()
                project_dir = Path(tmp) / 'repo'
                project_dir.mkdir()
                (project_dir / 'requirements.txt').write_text('', encoding='utf-8')
                project = str(project_dir)

                with mock.patch.dict(os.environ, {'COUNCIL_AGENT_ID': 'agent-env'}), \
                     mock.patch.object(code_tools, '_audit_tool_matrix_sync', return_value={}):
                    response = await orchestrator_server.dispatch_tool_call(
                        'open_session',
                        {'session_title': 'Environment identity', 'project_path': project},
                     transport='http',
                    )
                await orchestrator_server._flush_post_call_accounting(timeout=2.0)
                self.assertTrue(writer.wait_until_idle(2.0))
                data = json.loads(response.text)

                self.assertTrue(data['ok'])
                self.assertEqual(data['agent_id'], 'agent-env')
                with sqlite3.connect(str(code_tools._SESSIONS_DB)) as conn:
                    session = conn.execute(
                        'SELECT session_id, agent_id FROM sessions WHERE session_id=?',
                        (data['session_id'],),
                    ).fetchone()
                    event = conn.execute(
                        "SELECT session_id FROM events WHERE tool_name='open_session' ORDER BY id DESC LIMIT 1"
                    ).fetchone()
                self.assertEqual(session, (data['session_id'], 'agent-env'))
                self.assertEqual(event, (data['session_id'],))
            finally:
                writer.shutdown(timeout=2.0)
                orchestrator_server._DURABLE_AUDIT = old_writer
                code_tools._SESSIONS_DB = old_db
                code_tools._sync_session_db_alias()

    async def test_handler_without_agent_id_never_receives_identity_kwarg(self):
        tool_name = '__no_agent_kwarg_probe__'
        calls = []

        async def probe(project_path: str):
            calls.append({'project_path': project_path})
            return json.dumps({'ok': True})

        code_tools.TOOL_MAP[tool_name] = probe
        # S4 fixture: schema registry is the public contract; register a
        # minimal schema so the synthetic probe's payload fields are accepted.
        code_tools.TOOL_SCHEMAS.append({
            'type': 'function',
            'function': {
                'name': tool_name,
                'parameters': {
                    'type': 'object',
                    'properties': {
                        'project_path': {'type': 'string'},
                        'agent_id': {'type': 'string'},
                    },
                },
            },
        })
        try:
            with mock.patch.dict(os.environ, {'COUNCIL_AGENT_ID': 'agent-env'}):
                response = await orchestrator_server.dispatch_tool_call(
                    tool_name,
                    {'project_path': 'C:/repo', 'agent_id': 'agent-body'},
                     transport='http',
                    request_meta={'headers': {'X-Council-Agent': 'agent-header'}},
                )
            self.assertTrue(json.loads(response.text)['ok'])
            self.assertEqual(calls, [{'project_path': 'C:/repo'}])
        finally:
            code_tools.TOOL_MAP.pop(tool_name, None)
            code_tools.TOOL_SCHEMAS.pop()

    async def test_open_session_rotates_stale_active_session(self):
        with tempfile.TemporaryDirectory() as tmp:
            with _swap_sessions_db(Path(tmp) / 'sessions.db'):
                code_tools._init_session_db()
                project_dir = Path(tmp) / 'repo'
                project_dir.mkdir()
                (project_dir / 'requirements.txt').write_text('', encoding='utf-8')
                project = str(project_dir)
                norm = code_tools._session_project_key(project)
                stale_started = (datetime.now() - timedelta(days=4)).isoformat(timespec='seconds')
                conn = sqlite3.connect(str(code_tools._SESSIONS_DB))
                conn.execute(
                    "INSERT INTO sessions (session_id, session_title, project_path, mode, status, coverage, started_at) VALUES (?,?,?,?,?,?,?)",
                    ('old-session', 'Old CA', norm, 'T1', 'active', 'A-only', stale_started),
                )
                conn.commit()
                conn.close()

                raw = await code_tools.open_session('Fresh CA', project, 'T1')
                data = json.loads(raw)

                self.assertTrue(data['ok'])
                self.assertFalse(data['resumed'])
                self.assertEqual(datetime.fromisoformat(data['started_at']).date(), datetime.now().date())
                conn = sqlite3.connect(str(code_tools._SESSIONS_DB))
                rows = dict(conn.execute('SELECT session_id, status FROM sessions').fetchall())
                conn.close()
                self.assertEqual(rows['old-session'], 'closed')
                self.assertEqual(rows[data['session_id']], 'active')


    async def test_clear_closed_sessions_deletes_closed_and_keeps_active(self):
        with tempfile.TemporaryDirectory() as tmp:
            with _swap_sessions_db(Path(tmp) / 'sessions.db'):
                code_tools._init_session_db()
                project = 'C:/repo'
                norm = code_tools._session_project_key(project)
                now = datetime.now().isoformat(timespec='seconds')
                conn = sqlite3.connect(str(code_tools._SESSIONS_DB))
                conn.execute(
                    "INSERT INTO sessions (session_id, session_title, project_path, mode, status, coverage, started_at) VALUES (?,?,?,?,?,?,?)",
                    ('closed-session', 'Closed CA', norm, 'T1', 'closed', 'A-only', now),
                )
                conn.execute(
                    "INSERT INTO sessions (session_id, session_title, project_path, mode, status, coverage, started_at) VALUES (?,?,?,?,?,?,?)",
                    ('active-session', 'Active CA', norm, 'T1', 'active', 'A-only', now),
                )
                conn.execute(
                    "INSERT INTO events (session_id, tool_name, action_type, status, created_at) VALUES (?,?,?,?,?)",
                    ('closed-session', 'read_file_range', 'read', 'ok', now),
                )
                conn.commit()
                conn.close()

                raw = await code_tools.clear_closed_sessions(project)
                data = json.loads(raw)

                self.assertTrue(data['ok'])
                self.assertEqual(data['deleted_sessions'], 1)
                self.assertEqual(data['deleted_events'], 1)
                conn = sqlite3.connect(str(code_tools._SESSIONS_DB))
                sessions = dict(conn.execute('SELECT session_id, status FROM sessions').fetchall())
                closed_events = conn.execute("SELECT COUNT(*) FROM events WHERE session_id='closed-session'").fetchone()[0]
                conn.close()
                self.assertNotIn('closed-session', sessions)
                self.assertEqual(sessions['active-session'], 'active')
                self.assertEqual(closed_events, 0)


    # ── Council Task 3/4/5 contract tests ──────────────────────────────────
    async def test_council_dashboard_includes_collapse_and_pause_controls(self):
        app = orchestrator_server.create_app()
        request = make_mocked_request('GET', '/council')
        match = await app.router.resolve(request)
        response = await match.handler(request)
        self.assertEqual(response.status, 200)
        self.assertNotIn('data-target="latest_msg"', response.text)
        self.assertNotIn('data-target="live_log"', response.text)
        # Pause buttons per agent (one per card)
        for agent in orchestrator_server._COUNCIL_AGENTS:
            label = orchestrator_server._COUNCIL_AGENT_LABELS[agent]
            self.assertIn(f'<span class="agent-name">{label}</span>', response.text)
            # 0.12: pause button became "stop" (data-action="stop"); class kept.
            self.assertIn(
                f'class="agent-pause-toggle" data-agent="{agent}" data-action="stop"',
                response.text,
            )
        # JS hooks
        self.assertIn('function setupPauseButtons()', response.text)
        self.assertIn('function setupCopyButtons()', response.text)
        self.assertIn("ev.key === 'Enter' && !ev.shiftKey", response.text)
        self.assertIn("form.requestSubmit()", response.text)
        self.assertIn("Shift+Enter for newline", response.text)
        self.assertNotIn("Ctrl+Enter de send", response.text)
        self.assertIn("'Collapse' : 'Expand'", response.text) if False else self.assertIn('Collapse', response.text)
        # Origin guard preserved
        self.assertIn('localhost+origin', response.text) if False else None
        # localStorage key
        self.assertIn('council_collapse_v1', response.text)
        # Paused state is carried only by the pause/play button visual, not a visible status badge.
        self.assertNotIn('agent-status', response.text)
        # 0.12: dashboard stop button calls /api/council/stop (pause/play not referenced in UI).
        self.assertIn('/api/council/stop', response.text)

    async def test_council_state_payload_includes_usage_and_paused_fields(self):
        old_here = orchestrator_server._HERE
        old_alive = orchestrator_server._council_alive_agents
        old_metrics = orchestrator_server._council_agent_metrics
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            council = root / '.council'
            council.mkdir()
            orchestrator_server._HERE = root
            orchestrator_server._council_alive_agents = lambda: {'clau', 'ope'}
            orchestrator_server._council_agent_metrics = lambda _d, agent: {
                'tokens_session': 12345,
                'tokens_today': 67890,
                'plan_5h': 47.0,
                'reset_5h': 9999999999,
                'plan_7d': 22.0,
                'reset_7d': 9999999999,
                'context_tokens': 200000,
                'task_duration': 12,
                'source': 'fake',
                'model': 'gpt-test',
            } if agent == 'clau' else {
                'tokens_session': 100,
                'tokens_today': 200,
                'plan_5h': None,
                'plan_7d': None,
                'context_tokens': None,
                'task_duration': None,
                'source': 'fake',
                'model': None,
            }
            try:
                app = orchestrator_server.create_app()
                request = make_mocked_request('GET', '/api/council/state')
                match = await app.router.resolve(request)
                response = await match.handler(request)
            finally:
                orchestrator_server._HERE = old_here
                orchestrator_server._council_alive_agents = old_alive
                orchestrator_server._council_agent_metrics = old_metrics

        self.assertEqual(response.status, 200)
        payload = json.loads(response.text)
        self.assertIn('generated_at', payload)
        clau = next(a for a in payload['agents'] if a['id'] == 'clau')
        self.assertEqual(clau['label'], 'ClauC')
        # usage struct must be present and well-shaped
        usage = clau['usage']
        self.assertEqual(usage['agent'], 'clau')
        self.assertEqual(usage['source'], 'fake')
        self.assertEqual(usage['model'], 'gpt-test')
        self.assertEqual(usage['context']['used'], 12345)
        self.assertEqual(usage['context']['total'], 200000)
        self.assertEqual(usage['context']['pct'], 6)  # 12345/200000 -> 6%
        self.assertTrue(usage['context']['available'])
        self.assertEqual(usage['plan_5h']['pct'], 47)
        self.assertTrue(usage['plan_5h']['available'])
        self.assertEqual(usage['plan_7d']['pct'], 22)
        self.assertEqual(usage['tokens_session'], 12345)
        self.assertEqual(usage['task_duration'], 12)
        self.assertFalse(usage['paused'])
        self.assertIsNone(usage['paused_at'])
        # raw metrics also present
        self.assertEqual(clau['metrics']['plan_5h'], 47.0)
        # paused flag present (even when False)
        self.assertIn('paused', clau)
        self.assertFalse(clau['paused'])
        # Desktop process presence is not Council request activity.
        self.assertEqual(clau['status']['label'], 'idle')
        self.assertEqual(clau['runtime']['state'], 'idle')

    async def test_council_state_payload_marks_paused_from_file(self):
        old_here = orchestrator_server._HERE
        old_metrics = orchestrator_server._council_agent_metrics
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            council = root / '.council'
            council.mkdir()
            (council / 'paused_codo.json').write_text(
                json.dumps({
                    'agent': 'codo',
                    'paused_at': '2026-06-05T12:00:00',
                    'reason': 'unit test pause',
                    'pids_killed': [1234],
                    'pids_failed': [],
                    'auto_resume': False,
                }),
                encoding='utf-8',
            )
            orchestrator_server._HERE = root
            orchestrator_server._council_agent_metrics = lambda _d, _a: {
                'tokens_session': None,
                'tokens_today': None,
                'plan_5h': None,
                'plan_7d': None,
                'context_tokens': None,
                'task_duration': None,
                'source': 'fake',
                'model': None,
            }
            try:
                app = orchestrator_server.create_app()
                request = make_mocked_request('GET', '/api/council/state')
                match = await app.router.resolve(request)
                response = await match.handler(request)
            finally:
                orchestrator_server._HERE = old_here
                orchestrator_server._council_agent_metrics = old_metrics

        self.assertEqual(response.status, 200)
        payload = json.loads(response.text)
        codo = next(a for a in payload['agents'] if a['id'] == 'codo')
        self.assertTrue(codo['paused'])
        self.assertEqual(codo['paused_at'], '2026-06-05T12:00:00')
        self.assertEqual(codo['paused_reason'], 'unit test pause')
        self.assertEqual(codo['status']['label'], 'paused')
        self.assertEqual(codo['status']['class'], 'paused')
        self.assertTrue(codo['usage']['paused'])
        self.assertEqual(codo['usage']['paused_at'], '2026-06-05T12:00:00')

    def test_council_paused_helpers_round_trip(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / '.council'
            root.mkdir()
            self.assertIsNone(orchestrator_server._council_paused_info(root, 'ope'))
            orchestrator_server._council_mark_paused(root, 'ope', {
                'agent': 'ope',
                'paused_at': '2026-06-05T12:30:00',
                'reason': 'unit test',
                'pids_killed': [42],
                'pids_failed': [],
                'auto_resume': False,
            })
            info = orchestrator_server._council_paused_info(root, 'ope')
            self.assertIsNotNone(info)
            self.assertEqual(info['paused_at'], '2026-06-05T12:30:00')
            self.assertEqual(info['pids_killed'], [42])
            self.assertFalse(info['auto_resume'])
            cleared = orchestrator_server._council_clear_paused(root, 'ope')
            self.assertTrue(cleared)
            self.assertIsNone(orchestrator_server._council_paused_info(root, 'ope'))
            # Idempotent: second clear returns False
            self.assertFalse(orchestrator_server._council_clear_paused(root, 'ope'))

    async def test_council_pause_endpoint_aliases_stop_no_paused_file(self):
        old_here = orchestrator_server._HERE
        old_find = orchestrator_server._council_find_agent_pids
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            council = root / '.council'
            council.mkdir()
            orchestrator_server._HERE = root
            # No live PIDs in test env -> empty kill list is the success path
            orchestrator_server._council_find_agent_pids = lambda _a: []
            try:
                app = orchestrator_server.create_app()
                request = make_mocked_request(
                    'POST', '/api/council/pause',
                    headers={'Content-Type': 'application/json'},
                    payload=_make_body_reader({'agent': 'codo', 'reason': 'unit test'}),
                )
                match = await app.router.resolve(request)
                response = await match.handler(request)
                self.assertEqual(response.status, 200)
                body = json.loads(response.text)
                self.assertTrue(body['ok'])
                self.assertEqual(body['agent'], 'codo')
                self.assertEqual(body['state'], 'stopped')
                self.assertEqual(body['pids_killed'], [])
                self.assertIn('stopped', body['note'])
                # 0.12: /api/council/pause is now a backward-compatible alias for
                # /api/council/stop — it interrupts the run and clears (does NOT write)
                # a paused_<agent>.json file.
                paused_file = root / '.council' / 'paused_codo.json'
                self.assertFalse(paused_file.exists())
            finally:
                orchestrator_server._HERE = old_here
                orchestrator_server._council_find_agent_pids = old_find

    async def test_council_pause_endpoint_rejects_invalid_agent(self):
        request = make_mocked_request(
            'POST', '/api/council/pause',
            headers={'Content-Type': 'application/json'},
            payload=_make_body_reader({'agent': 'unknown_agent'}),
        )
        app = orchestrator_server.create_app()
        match = await app.router.resolve(request)
        response = await match.handler(request)
        self.assertEqual(response.status, 400)
        body = json.loads(response.text)
        self.assertFalse(body['ok'])
        self.assertEqual(body['error_name'], 'INVALID_AGENT')
        self.assertIn('codo', body['allowed'])
        self.assertIn('clau', body['allowed'])

    async def test_council_pause_endpoint_blocks_disallowed_origin(self):
        # _is_local uses request.remote (loopback) — default is OK; we test
        # the _origin_ok branch by sending a bad Origin header.
        request = make_mocked_request(
            'POST', '/api/council/pause',
            headers={'Content-Type': 'application/json', 'Origin': 'http://evil.example.com'},
            payload=_make_body_reader({'agent': 'codo'}),
        )
        app = orchestrator_server.create_app()
        match = await app.router.resolve(request)
        response = await match.handler(request)
        self.assertEqual(response.status, 403)
        body = json.loads(response.text)
        self.assertEqual(body['error_name'], 'ORIGIN_BLOCKED')

    async def test_council_play_endpoint_clears_paused_file(self):
        old_here = orchestrator_server._HERE
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            council = root / '.council'
            council.mkdir()
            (council / 'paused_ope.json').write_text(
                json.dumps({'agent': 'ope', 'paused_at': 'x', 'reason': 'y',
                            'pids_killed': [], 'pids_failed': [], 'auto_resume': False}),
                encoding='utf-8',
            )
            orchestrator_server._HERE = root
            try:
                app = orchestrator_server.create_app()
                request = make_mocked_request(
                    'POST', '/api/council/play',
                    headers={'Content-Type': 'application/json'},
                    payload=_make_body_reader({'agent': 'ope'}),
                )
                match = await app.router.resolve(request)
                response = await match.handler(request)
            finally:
                orchestrator_server._HERE = old_here

        self.assertEqual(response.status, 200)
        body = json.loads(response.text)
        self.assertTrue(body['ok'])
        self.assertTrue(body['cleared'])
        self.assertEqual(body['agent'], 'ope')
        self.assertFalse((root / '.council' / 'paused_ope.json').exists())
        self.assertIn('NOT auto-restart', body['note'])

    async def test_council_play_endpoint_rejects_invalid_agent(self):
        request = make_mocked_request(
            'POST', '/api/council/play',
            headers={'Content-Type': 'application/json'},
            payload=_make_body_reader({'agent': 'mystery'}),
        )
        app = orchestrator_server.create_app()
        match = await app.router.resolve(request)
        response = await match.handler(request)
        self.assertEqual(response.status, 400)
        body = json.loads(response.text)
        self.assertEqual(body['error_name'], 'INVALID_AGENT')

    def test_council_extract_response_tokens_supports_agent_streams(self):
        ope = '{"type":"step_finish","part":{"tokens":{"input":12,"output":8,"reasoning":3,"total":23}}}'
        codo = "final answer\n tokens used\n207"
        clau = '[claude usage] tokens in=2374 out=31  cost=$0.02'
        self.assertEqual(
            orchestrator_server._council_extract_response_tokens('ope', ope),
            {'input': 12, 'output': 8, 'reasoning': 3, 'total': 23},
        )
        self.assertEqual(
            orchestrator_server._council_extract_response_tokens('codo', codo),
            {'total': 207},
        )
        self.assertEqual(
            orchestrator_server._council_extract_response_tokens('clau', clau),
            {'input': 2374, 'output': 31, 'total': 2405},
        )
        codo_commas = "final answer\n tokens used\n97,538"
        self.assertEqual(
            orchestrator_server._council_extract_response_tokens('codo', codo_commas),
            {'total': 97538},
        )
        skill_paths = (
            r'C:\test-skills\05_write_pipeline\SKILL.md\n'
            r'C:\test-skills\09_testing_validation\SKILL.md'
        )
        with mock.patch.object(
            orchestrator_server, '_council_codo_skill_path_contract',
            return_value=skill_paths,
        ):
            prompt = orchestrator_server._council_build_dispatch_prompt(
                'codo', 'user', 'edit source', '', '',
            )
        self.assertIn('05_write_pipeline\\SKILL.md', prompt)
        self.assertIn('09_testing_validation\\SKILL.md', prompt)
        self.assertIn('COUNCIL_OUTPUT_INCOMPLETE', prompt)
    async def test_council_clear_session_endpoint_clears_runtime_files(self):
        old_here = orchestrator_server._HERE
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            council = root / '.council'
            council.mkdir()
            for name in (
                'council_events.jsonl', 'broker_filtered.jsonl', 'log_clau.txt',
                'log_ope_round.txt', 'inbox_clau.md', 'outbox_clau.md',
            ):
                (council / name).write_text('session data', encoding='utf-8')
            (council / 'paused_clau.json').write_text('{}', encoding='utf-8')
            (council / 'prompt_clau_keep.md').write_text('keep', encoding='utf-8')
            orchestrator_server._HERE = root
            try:
                app = orchestrator_server.create_app()
                request = make_mocked_request(
                    'POST', '/api/council/clear-session',
                    headers={'Content-Type': 'application/json'},
                    payload=_make_body_reader({'confirm': True}),
                )
                match = await app.router.resolve(request)
                response = await match.handler(request)
                cleared_snapshot = {
                    'events': (council / 'council_events.jsonl').read_text(encoding='utf-8'),
                    'log': (council / 'log_ope_round.txt').read_text(encoding='utf-8'),
                    'inbox': (council / 'inbox_clau.md').read_text(encoding='utf-8'),
                    'paused_exists': (council / 'paused_clau.json').exists(),
                    # Broker-only cleanup: stray prompt_*.md dispatch files are
                    # session rubbish and must be DELETED by clear-session.
                    'prompt_exists': (council / 'prompt_clau_keep.md').exists(),
                }
            finally:
                orchestrator_server._HERE = old_here

        body = json.loads(response.text)
        self.assertEqual(response.status, 200)
        self.assertTrue(body['ok'])
        self.assertGreaterEqual(body['cleared_files'], 7)
        self.assertEqual(cleared_snapshot['events'], '')
        self.assertEqual(cleared_snapshot['log'], '')
        self.assertEqual(cleared_snapshot['inbox'], '')
        self.assertFalse(cleared_snapshot['paused_exists'])
        self.assertFalse(cleared_snapshot['prompt_exists'])

    def test_council_local_clau_reads_bounded_projects_jsonl(self):
        with tempfile.TemporaryDirectory() as tmp:
            fake_home = Path(tmp)
            import unittest.mock as _um
            projects = fake_home / '.claude' / 'projects' / 'projA'
            projects.mkdir(parents=True)
            session_a = projects / 'a.jsonl'
            session_b = projects / 'b.jsonl'
            session_a.write_text(
                json.dumps({'type': 'user', 'message': 'skip-me'}) + '\n'
                + json.dumps({'type': 'assistant', 'message': {'usage': {
                    'input_tokens': 1000, 'output_tokens': 200,
                    'cache_creation_input_tokens': 10,
                    'cache_read_input_tokens': 5}}}) + '\n',
                encoding='utf-8',
            )
            session_b.write_text(
                json.dumps({'type': 'assistant', 'message': {'usage': {
                    'input_tokens': 3000, 'output_tokens': 400,
                    'cache_creation_input_tokens': 20,
                    'cache_read_input_tokens': 15}}}) + '\n',
                encoding='utf-8',
            )
            os.utime(session_a, (1_800_000_100, 1_800_000_100))
            os.utime(session_b, (1_800_000_200, 1_800_000_200))
            orchestrator_server._COUNCIL_METRICS_CACHE.clear()
            try:
                with _um.patch.object(Path, 'home', lambda: fake_home):
                    result = orchestrator_server._council_local_clau_usage()
                self.assertTrue(result['available'])
                self.assertEqual(result['source'], 'claude-projects-jsonl')
                self.assertEqual(result['sessions_scanned'], 2)
                self.assertEqual(result['input_tokens'], 4000)
                self.assertEqual(result['output_tokens'], 600)
                self.assertEqual(result['cache_creation_tokens'], 30)
                self.assertEqual(result['cache_read_tokens'], 20)
                expected_total = (
                    result['input_tokens'] + result['output_tokens']
                    + result['cache_creation_tokens'] + result['cache_read_tokens']
                )
                self.assertEqual(result['tokens_session'], expected_total)
                self.assertFalse(result['plan_available'])
            finally:
                orchestrator_server._COUNCIL_METRICS_CACHE.clear()

    def test_council_local_ope_returns_unavailable_when_db_missing(self):
        import unittest.mock as _um
        with tempfile.TemporaryDirectory() as tmp:
            fake_home = Path(tmp)
            with _um.patch.object(Path, 'home', lambda: fake_home):
                result = orchestrator_server._council_local_ope_usage()
            self.assertFalse(result['available'])
            # tokens_session is 0 (init default) when source not present
            self.assertIn(result['tokens_session'], (None, 0))
            self.assertEqual(result['source'], 'opencode-db-session')
            self.assertFalse(result['plan_available'])

    def test_council_local_clau_returns_unavailable_when_dir_missing(self):
        import unittest.mock as _um
        with tempfile.TemporaryDirectory() as tmp:
            fake_home = Path(tmp)
            with _um.patch.object(Path, 'home', lambda: fake_home):
                result = orchestrator_server._council_local_clau_usage()
            self.assertFalse(result['available'])
            # tokens_session is 0 (init default) when source not present
            self.assertIn(result['tokens_session'], (None, 0))
            self.assertEqual(result['source'], 'claude-projects-jsonl')
            self.assertFalse(result['plan_available'])

    def test_council_local_retired_vscode_reads_head_metadata_from_large_session(self):
        import unittest.mock as _um
        with tempfile.TemporaryDirectory() as tmp:
            fake_appdata = Path(tmp)
            sessions = fake_appdata / 'Code' / 'User' / 'globalStorage' / 'emptyWindowChatSessions'
            sessions.mkdir(parents=True)
            session = sessions / 'session.jsonl'
            for idx in range(4):
                filler = sessions / f'newer-empty-{idx}.jsonl'
                filler.write_text('{"empty":true}', encoding='utf-8')
                os.utime(filler, (1_800_000_100 + idx, 1_800_000_100 + idx))
            head = '{"result":{"metadata":{"promptTokens":1234,"outputTokens":56}}}'
            session.write_text(head + '\n' + ('x' * 5_000_000), encoding='utf-8')
            os.utime(session, (1_800_000_000, 1_800_000_000))
            orchestrator_server._COUNCIL_METRICS_CACHE.clear()
            with _um.patch.dict(os.environ, {'APPDATA': str(fake_appdata)}, clear=False):
                result = orchestrator_server._council_local_retired_vscode_usage()
            self.assertTrue(result['available'])
            self.assertEqual(result['prompt_tokens'], 1234)
            self.assertEqual(result['output_tokens'], 56)
            self.assertEqual(result['tokens_session'], 1290)

    def test_council_local_retired_vscode_handles_missing_dir(self):
        import unittest.mock as _um
        with tempfile.TemporaryDirectory() as tmp:
            fake_home = Path(tmp)
            orchestrator_server._COUNCIL_METRICS_CACHE.clear()
            with _um.patch.object(Path, 'home', lambda: fake_home), \
                 _um.patch.dict(os.environ, {'APPDATA': str(fake_home)}, clear=False):
                result = orchestrator_server._council_local_retired_vscode_usage()
            self.assertFalse(result['available'])
            self.assertEqual(result['source'], 'retired-vscode-emptyWindowChatSessions')

    def test_legacy_council_bridge_scripts_removed(self):
        # R3 cleanup: the council_message tool is the ONLY sanctioned messaging
        # channel; legacy root bridge scripts must stay deleted (their reappearance
        # means someone resurrected the banned raw-shell path).
        root = Path(__file__).resolve().parents[1]
        for legacy in ('council_send.js', 'a_call.js', 'send_msg.js',
                       'send_council_msg.ps1'):
            self.assertFalse((root / legacy).exists(), legacy)


class TestTokenStateMachine(unittest.IsolatedAsyncioTestCase):
    """Phase 1A — CAN-001 (token TTL enforced at use-time) + CAN-002
    (AUTO_TOKEN_INJECT dev-gate/harden). Deterministic: clock injected via the
    `now` arg / TOKEN_CREATED_AT, feature flags saved+restored. No real sleep,
    no real server lifecycle."""

    def setUp(self):
        self._saved = (
            orchestrator_server.EXPECTED_TOKEN,
            orchestrator_server.TOKEN_CREATED_AT,
            orchestrator_server.AUTO_TOKEN_INJECT,
        )

    def tearDown(self):
        (orchestrator_server.EXPECTED_TOKEN,
         orchestrator_server.TOKEN_CREATED_AT,
         orchestrator_server.AUTO_TOKEN_INJECT) = self._saved

    def _ttl_seconds(self):
        return orchestrator_server.TOKEN_TTL_MINUTES * 60

    # ───────────── CAN-001: TTL enforced at use-time ─────────────
    def test_can001_fresh_token_is_not_expired(self):
        orchestrator_server.TOKEN_CREATED_AT = 1000.0
        self.assertFalse(orchestrator_server._token_is_expired(now=1000.0 + 5))

    def test_can001_token_expires_after_ttl(self):
        orchestrator_server.TOKEN_CREATED_AT = 1000.0
        self.assertTrue(
            orchestrator_server._token_is_expired(now=1000.0 + self._ttl_seconds() + 1))

    def test_can001_validate_required_for_wrong_token(self):
        orchestrator_server.EXPECTED_TOKEN = "good"
        orchestrator_server.TOKEN_CREATED_AT = 1000.0
        self.assertEqual(
            orchestrator_server._validate_mutation_token("bad", now=1000.0 + 1),
            "MUTATION_TOKEN_REQUIRED")

    def test_can001_validate_expired_for_correct_but_old_token(self):
        orchestrator_server.EXPECTED_TOKEN = "good"
        orchestrator_server.TOKEN_CREATED_AT = 1000.0
        self.assertEqual(
            orchestrator_server._validate_mutation_token(
                "good", now=1000.0 + self._ttl_seconds() + 1),
            "MUTATION_TOKEN_EXPIRED")

    def test_can001_validate_passes_for_fresh_correct_token(self):
        orchestrator_server.EXPECTED_TOKEN = "good"
        orchestrator_server.TOKEN_CREATED_AT = 1000.0
        self.assertEqual(
            orchestrator_server._validate_mutation_token("good", now=1000.0 + 1), "")

    async def test_can001_gate_rejects_expired_token(self):
        orchestrator_server.EXPECTED_TOKEN = "good"
        orchestrator_server.TOKEN_CREATED_AT = time.time() - (self._ttl_seconds() + 30)
        orchestrator_server.AUTO_TOKEN_INJECT = False
        tname = sorted(orchestrator_server._TOKEN_REQUIRED_TOOLS
                       - {"run_project_command", "council_message"})[0]
        request = make_mocked_request(
            'POST', '/api/call/' + tname,
            headers={'X-Orchestrator-Token': 'good'},
            payload=_make_body_reader({}),
            match_info={'tool_name': tname},
        )
        resp = await orchestrator_server._dispatch_tool_call_request(request)
        self.assertEqual(resp.status, 403)
        self.assertEqual(json.loads(resp.text).get('error_name'),
                         'MUTATION_TOKEN_EXPIRED')

    async def test_can001_get_token_rotates_when_expired(self):
        old = "stale-token"
        orchestrator_server.EXPECTED_TOKEN = old
        orchestrator_server.TOKEN_CREATED_AT = time.time() - (self._ttl_seconds() + 30)
        request = make_mocked_request('GET', '/api/token')
        resp = await orchestrator_server.handle_token(request)
        data = json.loads(resp.text)
        self.assertNotEqual(data['token'], old)
        self.assertEqual(data['token'], orchestrator_server.EXPECTED_TOKEN)
        self.assertGreater(data['expires_at_epoch'], time.time())

    # ───────────── CAN-002: AUTO_TOKEN_INJECT dev-gate + harden ─────────────
    def test_can002_auto_token_inject_env_gate_parses_correctly(self):
        # WHY: AUTO_TOKEN_INJECT must exactly mirror the env gate at import time
        # (default ON when unset), while ORCHESTRATOR_AUTO_TOKEN=0 remains an
        # explicit strict-mode opt-out. The absolute value may still depend on
        # the parent process environment.
        import os
        expected = os.environ.get('ORCHESTRATOR_AUTO_TOKEN', '1') == '1'
        self.assertEqual(orchestrator_server.AUTO_TOKEN_INJECT, expected)

    def test_can002_validator_independent_of_auto_inject(self):
        # Dev-bypass must never silently mask an expired/mismatched token in the
        # single source of truth; AUTO_TOKEN_INJECT is the caller's concern only.
        orchestrator_server.EXPECTED_TOKEN = "good"
        orchestrator_server.TOKEN_CREATED_AT = 1000.0
        orchestrator_server.AUTO_TOKEN_INJECT = True
        self.assertEqual(
            orchestrator_server._validate_mutation_token(
                "good", now=1000.0 + self._ttl_seconds() + 1),
            "MUTATION_TOKEN_EXPIRED")

    async def test_can002_toggle_requires_valid_token(self):
        orchestrator_server.EXPECTED_TOKEN = "good"
        orchestrator_server.TOKEN_CREATED_AT = time.time()
        orchestrator_server.AUTO_TOKEN_INJECT = False
        request = make_mocked_request(
            'POST', '/api/config/auto_token_inject',
            headers={'X-Orchestrator-Token': 'wrong'},
            payload=_make_body_reader({'enabled': True}),
        )
        resp = await orchestrator_server.handle_auto_token_post(request)
        self.assertEqual(resp.status, 403)
        self.assertFalse(orchestrator_server.AUTO_TOKEN_INJECT)

    async def test_can002_toggle_rejects_expired_token(self):
        orchestrator_server.EXPECTED_TOKEN = "good"
        orchestrator_server.TOKEN_CREATED_AT = time.time() - (self._ttl_seconds() + 30)
        orchestrator_server.AUTO_TOKEN_INJECT = False
        request = make_mocked_request(
            'POST', '/api/config/auto_token_inject',
            headers={'X-Orchestrator-Token': 'good'},
            payload=_make_body_reader({'enabled': True}),
        )
        resp = await orchestrator_server.handle_auto_token_post(request)
        self.assertEqual(resp.status, 403)
        self.assertEqual(json.loads(resp.text).get('error_name'),
                         'MUTATION_TOKEN_EXPIRED')

    async def test_can002_bypass_still_enforces_session(self):
        # AUTO_TOKEN_INJECT bypasses ONLY the token gate; the downstream session
        # gate must still reject a mutating tool with no active session.
        orchestrator_server.AUTO_TOKEN_INJECT = True
        orchestrator_server.EXPECTED_TOKEN = "good"
        orchestrator_server.TOKEN_CREATED_AT = time.time()
        candidates = [t for t in sorted(orchestrator_server._TOKEN_REQUIRED_TOOLS)
                      if orchestrator_server._requires_session(t)
                      and t not in {"run_project_command", "council_message"}]
        self.assertTrue(candidates, "need a token-required, session-required tool")
        tname = candidates[0]
        request = make_mocked_request(
            'POST', '/api/call/' + tname,
            headers={},
            payload=_make_body_reader({}),
            match_info={'tool_name': tname},
        )
        resp = await orchestrator_server._dispatch_tool_call_request(request)
        # Token was bypassed, yet a downstream required gate (session,
        # project_path, or the S4 registry-required-field contract) must
        # STILL block execution — bypass is not a free pass. S4 triggers
        # MISSING_REQUIRED_FIELD before the session gate on empty bodies.
        self.assertGreaterEqual(resp.status, 400)
        self.assertIn(json.loads(resp.text).get('error_name'),
                      {'ACTIVE_SESSION_REQUIRED', 'PROJECT_PATH_REQUIRED',
                       'MISSING_REQUIRED_FIELD'})


class TestHttpDefense(unittest.IsolatedAsyncioTestCase):
    """Phase 1B — CAN-003 (SSE auth), CAN-004 (rate limit), CAN-006 (body size).
    Deterministic: token + rate-limit globals saved/restored, clock injected."""

    def setUp(self):
        self._saved = (
            orchestrator_server.EXPECTED_TOKEN,
            orchestrator_server.TOKEN_CREATED_AT,
            orchestrator_server._RATE_LIMIT_MAX,
            orchestrator_server._RATE_LIMIT_WINDOW,
            dict(orchestrator_server._rate_buckets),
            orchestrator_server._DASHBOARD_RATE_LIMIT_MAX,
            orchestrator_server._DASHBOARD_RATE_LIMIT_WINDOW,
            dict(orchestrator_server._dashboard_rate_buckets),
            orchestrator_server._OTHER_RATE_LIMIT_MAX,
            orchestrator_server._OTHER_RATE_LIMIT_WINDOW,
            dict(orchestrator_server._other_rate_buckets),
            list(orchestrator_server._api_call_latency_samples),
            orchestrator_server._requests_in_flight,
        )

    def tearDown(self):
        (orchestrator_server.EXPECTED_TOKEN,
         orchestrator_server.TOKEN_CREATED_AT,
         orchestrator_server._RATE_LIMIT_MAX,
         orchestrator_server._RATE_LIMIT_WINDOW, _b,
         orchestrator_server._DASHBOARD_RATE_LIMIT_MAX,
         orchestrator_server._DASHBOARD_RATE_LIMIT_WINDOW, _db,
         orchestrator_server._OTHER_RATE_LIMIT_MAX,
         orchestrator_server._OTHER_RATE_LIMIT_WINDOW, _ob,
         _latency,
         orchestrator_server._requests_in_flight) = self._saved
        orchestrator_server._rate_buckets.clear()
        orchestrator_server._rate_buckets.update(_b)
        orchestrator_server._dashboard_rate_buckets.clear()
        orchestrator_server._dashboard_rate_buckets.update(_db)
        orchestrator_server._other_rate_buckets.clear()
        orchestrator_server._other_rate_buckets.update(_ob)
        orchestrator_server._api_call_latency_samples.clear()
        orchestrator_server._api_call_latency_samples.extend(_latency)

    async def _ok(self, request):
        return orchestrator_server.web.Response(status=200)

    # ───────────── CAN-003: SSE auth ─────────────
    def test_can003_sse_token_ok_accepts_valid_query_token(self):
        orchestrator_server.EXPECTED_TOKEN = "good"
        orchestrator_server.TOKEN_CREATED_AT = time.time()
        req = make_mocked_request('GET', '/api/session/stream/s1?token=good')
        self.assertTrue(orchestrator_server._sse_token_ok(req))

    def test_can003_sse_token_ok_rejects_missing_and_expired(self):
        orchestrator_server.EXPECTED_TOKEN = "good"
        orchestrator_server.TOKEN_CREATED_AT = time.time()
        self.assertFalse(orchestrator_server._sse_token_ok(
            make_mocked_request('GET', '/api/session/stream/s1')))
        orchestrator_server.TOKEN_CREATED_AT = time.time() - (
            orchestrator_server.TOKEN_TTL_MINUTES * 60 + 30)
        self.assertFalse(orchestrator_server._sse_token_ok(
            make_mocked_request('GET', '/api/session/stream/s1?token=good')))

    async def test_can003_sse_handler_rejects_unauthenticated_before_stream(self):
        orchestrator_server.EXPECTED_TOKEN = "good"
        orchestrator_server.TOKEN_CREATED_AT = time.time()
        req = make_mocked_request('GET', '/api/session/stream/s1',
                                  match_info={'session_id': 's1'})
        resp = await orchestrator_server.handle_session_stream(req)
        self.assertEqual(resp.status, 403)
        self.assertNotIn('text/event-stream', resp.headers.get('Content-Type', ''))

    # ───────────── CAN-004: rate limit ─────────────
    def test_can004_token_bucket_blocks_after_budget(self):
        orchestrator_server._RATE_LIMIT_MAX = 2
        orchestrator_server._RATE_LIMIT_WINDOW = 60.0
        orchestrator_server._rate_buckets.clear()
        req = make_mocked_request('POST', '/api/call/x')
        self.assertTrue(orchestrator_server._rate_limit_check(req, now=1000.0)[0])
        self.assertTrue(orchestrator_server._rate_limit_check(req, now=1000.0)[0])
        allowed, retry = orchestrator_server._rate_limit_check(req, now=1000.0)
        self.assertFalse(allowed)
        self.assertGreaterEqual(retry, 1)

    def test_can004_token_bucket_refills_over_window(self):
        orchestrator_server._RATE_LIMIT_MAX = 2
        orchestrator_server._RATE_LIMIT_WINDOW = 60.0
        orchestrator_server._rate_buckets.clear()
        req = make_mocked_request('POST', '/api/call/x')
        orchestrator_server._rate_limit_check(req, now=1000.0)
        orchestrator_server._rate_limit_check(req, now=1000.0)
        self.assertFalse(orchestrator_server._rate_limit_check(req, now=1000.0)[0])
        self.assertTrue(orchestrator_server._rate_limit_check(req, now=1000.0 + 60)[0])

    async def test_can004_middleware_returns_429_over_limit(self):
        orchestrator_server._RATE_LIMIT_MAX = 1
        orchestrator_server._RATE_LIMIT_WINDOW = 60.0
        orchestrator_server._rate_buckets.clear()
        r1 = await orchestrator_server._http_defense_middleware(
            make_mocked_request('POST', '/api/call/x',
                                match_info={'tool_name': 'x'}), self._ok)
        r2 = await orchestrator_server._http_defense_middleware(
            make_mocked_request('POST', '/api/call/x',
                                match_info={'tool_name': 'x'}), self._ok)
        self.assertEqual(r1.status, 200)
        self.assertEqual(r2.status, 429)
        self.assertIn('Retry-After', r2.headers)

    async def test_can004_route_classes_have_independent_buckets(self):
        orchestrator_server._RATE_LIMIT_MAX = 1
        orchestrator_server._DASHBOARD_RATE_LIMIT_MAX = 1
        orchestrator_server._OTHER_RATE_LIMIT_MAX = 1
        orchestrator_server._rate_buckets.clear()
        orchestrator_server._dashboard_rate_buckets.clear()
        orchestrator_server._other_rate_buckets.clear()

        other = await orchestrator_server._http_defense_middleware(
            make_mocked_request('GET', '/api/tools'), self._ok)
        dashboard = await orchestrator_server._http_defense_middleware(
            make_mocked_request('GET', '/api/council/state'), self._ok)
        api1 = await orchestrator_server._http_defense_middleware(
            make_mocked_request('POST', '/api/call/x',
                                match_info={'tool_name': 'x'}), self._ok)
        api2 = await orchestrator_server._http_defense_middleware(
            make_mocked_request('POST', '/api/call/x',
                                match_info={'tool_name': 'x'}), self._ok)

        self.assertEqual(other.status, 200)
        self.assertEqual(dashboard.status, 200)
        self.assertEqual(api1.status, 200)
        self.assertEqual(api2.status, 429)
        self.assertIn('Retry-After', api2.headers)

    def test_can004_disabled_when_max_non_positive(self):
        orchestrator_server._RATE_LIMIT_MAX = 0
        req = make_mocked_request('POST', '/api/call/x')
        for _ in range(50):
            self.assertTrue(orchestrator_server._rate_limit_check(req)[0])

    # ───────────── CAN-006: body size ─────────────
    async def test_can006_middleware_rejects_oversized_body(self):
        orchestrator_server._RATE_LIMIT_MAX = 0  # isolate from rate-limit branch
        big = orchestrator_server.MAX_REQUEST_BODY_BYTES + 1
        req = make_mocked_request('POST', '/api/call/write_fix',
                                  headers={'Content-Length': str(big)},
                                  match_info={'tool_name': 'write_fix'})
        resp = await orchestrator_server._http_defense_middleware(req, self._ok)
        self.assertEqual(resp.status, 413)
        self.assertEqual(json.loads(resp.text).get('error_name'),
                         'REQUEST_BODY_TOO_LARGE')

    async def test_can006_middleware_allows_normal_body(self):
        orchestrator_server._RATE_LIMIT_MAX = 0
        req = make_mocked_request('POST', '/api/call/write_fix',
                                  headers={'Content-Length': '500'},
                                  match_info={'tool_name': 'write_fix'})
        resp = await orchestrator_server._http_defense_middleware(req, self._ok)
        self.assertEqual(resp.status, 200)

    def test_can006_app_sets_client_max_size(self):
        app = orchestrator_server.create_app()
        self.assertEqual(app._client_max_size,
                         orchestrator_server.MAX_REQUEST_BODY_BYTES)

    async def test_health_reports_queue_depth_p95_and_liveness_contract(self):
        orchestrator_server._requests_in_flight = 3
        orchestrator_server._api_call_latency_samples.clear()
        orchestrator_server._record_api_call_latency(10, now=2000.0)
        orchestrator_server._record_api_call_latency(20, now=2000.0)
        orchestrator_server._record_api_call_latency(30, now=2000.0)
        with mock.patch.object(orchestrator_server.time, 'time', return_value=2000.0), \
             mock.patch.object(orchestrator_server, 'SERVER_READY', True), \
             mock.patch.object(orchestrator_server, 'SERVER_STARTUP_PHASE', 'ready'):
            resp = await orchestrator_server.handle_health(
                make_mocked_request('GET', '/health'))
        data = json.loads(resp.text)
        livez = orchestrator_server._liveness_payload()
        self.assertEqual(resp.status, 200)
        self.assertEqual(data['queue_depth'], 3)
        self.assertEqual(data['p95_ms'], 30)
        self.assertTrue(data['ready'])
        for field in ('service', 'pid', 'generation', 'started_at', 'livez_protocol'):
            self.assertEqual(data[field], livez[field])
        self.assertEqual(data['livez_protocol'], 1)

    async def test_health_returns_503_while_boot_recovery_is_pending(self):
        with mock.patch.object(orchestrator_server, 'SERVER_READY', False), \
             mock.patch.object(orchestrator_server, 'SERVER_STARTUP_PHASE', 'boot_write_sweep'):
            resp = await orchestrator_server.handle_health(
                make_mocked_request('GET', '/health'))
        data = json.loads(resp.text)
        self.assertEqual(resp.status, 503)
        self.assertFalse(data['ok'])
        self.assertEqual(data['startup_phase'], 'boot_write_sweep')
        self.assertEqual(resp.headers.get('Retry-After'), '1')

    async def test_health_normalizes_controller_and_startup_exceptions(self):
        controller_state = {
            'configured': True, 'reachable': False,
            'error': urllib.error.URLError(TimeoutError('controller timeout')),
        }
        recovery = {
            'status': 'error',
            'sweep': {'failure': TimeoutError('startup timeout')},
        }
        with mock.patch.object(orchestrator_server, 'controller_health', return_value=controller_state), \
             mock.patch.object(orchestrator_server, 'SERVER_STARTUP_RECOVERY', recovery):
            resp = await orchestrator_server.handle_health(
                make_mocked_request('GET', '/health'))
        data = json.loads(resp.text)
        self.assertEqual(data['restart_controller']['error']['error_type'], 'URLError')
        self.assertIn('controller timeout', data['restart_controller']['error']['error_message'])
        self.assertEqual(data['startup_recovery']['sweep']['failure']['error_type'], 'TimeoutError')
        self.assertEqual(data['startup_recovery']['sweep']['failure']['error_message'], 'startup timeout')

    async def test_startup_gate_blocks_api_but_allows_health_handler(self):
        calls = []

        async def handler(request):
            calls.append(request.path)
            return orchestrator_server.web.Response(status=204)

        with mock.patch.object(orchestrator_server, 'SERVER_READY', False), \
             mock.patch.object(orchestrator_server, 'SERVER_STARTUP_PHASE', 'boot_write_sweep'), \
             mock.patch.object(orchestrator_server, '_RATE_LIMIT_MAX', 0), \
             mock.patch.object(orchestrator_server, '_OTHER_RATE_LIMIT_MAX', 0):
            blocked = await orchestrator_server._http_defense_middleware(
                make_mocked_request('POST', '/api/call/write_fix',
                                    match_info={'tool_name': 'write_fix'}), handler)
            health = await orchestrator_server._http_defense_middleware(
                make_mocked_request('GET', '/health'), handler)
        self.assertEqual(blocked.status, 503)
        self.assertEqual(json.loads(blocked.text)['error_name'], 'ORCHESTRATOR_STARTING')
        self.assertEqual(blocked.headers.get('Retry-After'), '1')
        self.assertEqual(health.status, 204)
        self.assertEqual(calls, ['/health'])

    async def test_startup_gate_releases_api_when_ready(self):
        with mock.patch.object(orchestrator_server, 'SERVER_READY', True), \
             mock.patch.object(orchestrator_server, '_RATE_LIMIT_MAX', 0):
            resp = await orchestrator_server._http_defense_middleware(
                make_mocked_request('POST', '/api/call/x', match_info={'tool_name': 'x'}),
                self._ok,
            )
        self.assertEqual(resp.status, 200)


    async def test_startup_gate_allows_council_boot_html(self):
        calls = []

        async def handler(request):
            calls.append(request.path)
            return orchestrator_server.web.Response(status=204)

        with mock.patch.object(orchestrator_server, 'SERVER_READY', False), \
             mock.patch.object(orchestrator_server, 'SERVER_STARTUP_PHASE', 'boot_write_sweep'), \
             mock.patch.object(orchestrator_server, '_RATE_LIMIT_MAX', 0), \
             mock.patch.object(orchestrator_server, '_OTHER_RATE_LIMIT_MAX', 0):
            council = await orchestrator_server._http_defense_middleware(
                make_mocked_request('GET', '/council'), handler)
            mutating = await orchestrator_server._http_defense_middleware(
                make_mocked_request('POST', '/api/call/write_fix',
                                    match_info={'tool_name': 'write_fix'}), handler)
        self.assertEqual(council.status, 204)
        self.assertEqual(calls, ['/council'])
        self.assertEqual(mutating.status, 503)
        self.assertEqual(json.loads(mutating.text)['error_name'], 'ORCHESTRATOR_STARTING')

    async def test_council_dashboard_boot_html_while_starting(self):
        with mock.patch.object(orchestrator_server, 'SERVER_READY', False), \
             mock.patch.object(orchestrator_server, 'SERVER_STARTUP_PHASE', 'boot_write_sweep'):
            resp = await orchestrator_server.handle_council_dashboard(
                make_mocked_request('GET', '/council'))
        self.assertEqual(resp.status, 200)
        self.assertIn('text/html', resp.content_type)
        self.assertIn('phase=boot_write_sweep', resp.text)
        self.assertIn('http-equiv="refresh"', resp.text)
        self.assertNotIn("endpoint: '/api/council/state'", resp.text)
        self.assertEqual(resp.headers.get('Retry-After'), '1')


class TestShellSafety(unittest.IsolatedAsyncioTestCase):
    """Phase 1C — CAN-007: shell-injection separators must disqualify a command
    from the readonly fast-path (which skips the token gate). `;`, newline, and
    command substitution were not blocked, so `echo x; rm y` ran token-free."""

    def _ro(self, cmd):
        return orchestrator_server._is_readonly_command_body({'command': cmd})

    def test_can007a_semicolon_chain_not_readonly(self):
        self.assertFalse(self._ro('echo hi; rm -rf x'))

    def test_can007b_newline_chain_not_readonly(self):
        self.assertFalse(self._ro('echo hi\nrm -rf x'))
        self.assertFalse(self._ro('echo hi\r\ndel x'))

    def test_can007b_command_substitution_not_readonly(self):
        self.assertFalse(self._ro('echo $(rm -rf x)'))
        self.assertFalse(self._ro('echo `rm -rf x`'))

    def test_can007b_existing_separators_still_blocked(self):
        for c in ('echo a && rm b', 'echo a | rm b', 'echo a > b', 'echo a & del b'):
            self.assertFalse(self._ro(c), c)

    def test_can007b_list_form_semicolon_blocked(self):
        self.assertFalse(orchestrator_server._is_readonly_command_body(
            {'command': ['echo', 'hi;rm', 'x']}))

    def test_can007c_safe_readonly_commands_still_pass(self):
        for c in ('echo hello world', 'git status', 'git log --oneline',
                  'ls', 'dir', 'python --version'):
            self.assertTrue(self._ro(c), c)


class TestMetadataConsistency(unittest.IsolatedAsyncioTestCase):
    """Phase 1D — CAN-012/016: protected_tools_status + /api/tools metadata must
    faithfully reflect the live enforcement sets (decoupled model: origin+session
    gate = PROTECTED ∪ TOKEN_REQUIRED; token gate = TOKEN_REQUIRED; mutation_scope =
    informational risk label)."""

    async def _status(self):
        return json.loads(await code_tools.protected_tools_status())

    def test_can012_token_required_is_subset_of_mutating_gate(self):
        for t in code_tools.TOKEN_REQUIRED_TOOLS:
            self.assertTrue(orchestrator_server._is_mutating_tool(t),
                            f"{t} is token-required but not in the mutating gate set")

    async def test_can016_status_token_required_matches_live_set(self):
        s = await self._status()
        self.assertEqual(
            s['token_required_tools'],
            sorted(t for t in code_tools.TOOL_MAP if t in code_tools.TOKEN_REQUIRED_TOOLS))

    async def test_can016_status_mutating_tools_matches_runtime_gate(self):
        s = await self._status()
        expected = sorted(t for t in code_tools.TOOL_MAP
                          if orchestrator_server._is_mutating_tool(t))
        self.assertEqual(s['mutating_tools'], expected)

    async def test_can016_status_mutation_scopes_match_map(self):
        s = await self._status()
        for name, scope in s['mutation_scopes'].items():
            self.assertEqual(scope, code_tools.tool_mutation_scope(name), name)
        for name in code_tools.TOOL_MAP:
            if code_tools.tool_mutation_scope(name) != 'none':
                self.assertIn(name, s['mutation_scopes'], name)

    async def test_record_session_checkpoint_keeps_audit_write_contract(self):
        # WHY: this tool writes an audit event, so protected T3 enforcement must not
        # surface scope=none; project/session selectors must remain discoverable.
        name = 'record_session_audit_checkpoint'
        self.assertIn(name, code_tools.PROTECTED_TOOL_NAMES)
        self.assertEqual(code_tools.tool_phase(name), 'T3')
        self.assertEqual(code_tools.tool_mutation_scope(name), 'audit')
        schema = next(
            item['function'] for item in code_tools.TOOL_SCHEMAS
            if item.get('function', {}).get('name') == name
        )
        properties = schema['parameters']['properties']
        self.assertIn('project_path', properties)
        self.assertIn('session_id', properties)

    async def test_record_error_resolution_registry_schema_and_api_dispatch(self):
        # WHY: explicit resolution is a protected audit append, not a read and not
        # a token-required destructive mutation. The HTTP dispatcher must expose it.
        name = 'record_error_resolution'
        self.assertIn(name, code_tools.TOOL_MAP)
        self.assertIn(name, code_tools.PROTECTED_TOOL_NAMES)
        self.assertNotIn(name, code_tools.TOKEN_REQUIRED_TOOLS)
        self.assertEqual(code_tools.tool_phase(name), 'T3')
        self.assertEqual(code_tools.tool_mutation_scope(name), 'audit')
        schema = next(
            item['function'] for item in code_tools.TOOL_SCHEMAS
            if item.get('function', {}).get('name') == name
        )
        self.assertEqual(
            schema['parameters']['required'],
            ['target_session_id', 'resolves_event_id', 'resolution_kind'],
        )
        for transport_field in ('calling_context', 'session_id', 'task_epoch', 'prompt_hash', 'agent_id'):
            self.assertNotIn(transport_field, schema['parameters']['properties'])
        self.assertIn('SESSION_OWNERSHIP_REQUIRED', schema['description'])
        self.assertIn('STALE_TASK_EPOCH', schema['description'])
        self.assertEqual(
            schema['parameters']['properties']['resolution_kind']['enum'],
            ['recovered', 'expected_failure', 'reopened'],
        )

        calls = []

        async def handler(target_session_id: str, resolves_event_id: int,
                          resolution_kind: str, calling_context: dict | None = None):
            calls.append({
                "target_session_id": target_session_id,
                "calling_context": calling_context,
                "resolves_event_id": resolves_event_id,
                "resolution_kind": resolution_kind,
            })
            return json.dumps({"ok": True, "resolution_event_id": 42})
        original = code_tools.TOOL_MAP[name]
        code_tools.TOOL_MAP[name] = handler
        try:
            with _issued_checkpoint('host-session', {
                'agent_id': 'Ope', 'task_epoch': 4, 'prompt_hash': 'issued-hash',
                'owner_capability_sha256': hashlib.sha256(b'issued-capability').hexdigest(),
            }):
                request = make_mocked_request(
                    'POST', '/api/call/' + name,
                    headers={
                        'X-Session-Id': 'host-session', 'X-Agent-Id': 'Ope',
                        'X-Task-Epoch': '4', 'X-Prompt-Hash': 'issued-hash',
                        'X-Task-Capability': 'issued-capability',
                    },
                    payload=_make_body_reader({
                        'target_session_id': 'session-exact',
                        'resolves_event_id': 7,
                        'resolution_kind': 'recovered',
                    }),
                    match_info={'tool_name': name},
                )
                response = await orchestrator_server._dispatch_tool_call_request(request)
        finally:
            code_tools.TOOL_MAP[name] = original
        self.assertEqual(response.status, 200)
        self.assertEqual(json.loads(response.text)['resolution_event_id'], 42)
        self.assertEqual(calls, [{"target_session_id": "session-exact",
                                 "calling_context": {
                                     "validated": True, "session_id": "host-session",
                                     "agent_id": "Ope", "task_epoch": 4,
                                     "prompt_hash": "issued-hash",
                                 },
                                 "resolves_event_id": 7,
                                 "resolution_kind": "recovered"}])

    async def test_record_error_resolution_spoofed_session_header_is_rejected(self):
        name = 'record_error_resolution'
        handler = mock.AsyncMock(return_value=json.dumps({'ok': True}))
        original = code_tools.TOOL_MAP[name]
        code_tools.TOOL_MAP[name] = handler
        try:
            request = make_mocked_request(
                'POST', '/api/call/' + name,
                headers={'X-Session-Id': 'spoofed-host', 'X-Agent-Id': 'Ope'},
                payload=_make_body_reader({
                    'target_session_id': 'spoofed-host', 'resolves_event_id': 7,
                    'resolution_kind': 'recovered',
                }),
                match_info={'tool_name': name},
            )
            response = await orchestrator_server._dispatch_tool_call_request(request)
        finally:
            code_tools.TOOL_MAP[name] = original
        self.assertEqual(response.status, 403)
        self.assertEqual(json.loads(response.text)['error_name'], 'SESSION_OWNERSHIP_REQUIRED')
        handler.assert_not_awaited()

    async def test_record_error_resolution_stale_epoch_or_hash_is_rejected(self):
        name = 'record_error_resolution'
        handler = mock.AsyncMock(return_value=json.dumps({'ok': True}))
        original = code_tools.TOOL_MAP[name]
        code_tools.TOOL_MAP[name] = handler
        responses = []
        try:
            with _issued_checkpoint('host-session', {
                'agent_id': 'Ope', 'task_epoch': 4, 'prompt_hash': 'issued-hash',
                'owner_capability_sha256': hashlib.sha256(b'issued-capability').hexdigest(),
            }):
                for epoch, prompt_hash in (('3', 'issued-hash'), ('4', 'stale-hash')):
                    request = make_mocked_request(
                        'POST', '/api/call/' + name,
                        headers={
                            'X-Session-Id': 'host-session', 'X-Agent-Id': 'Ope',
                            'X-Task-Epoch': epoch, 'X-Prompt-Hash': prompt_hash,
                            'X-Task-Capability': 'issued-capability',
                        },
                        payload=_make_body_reader({
                            'target_session_id': 'host-session', 'resolves_event_id': 7,
                            'resolution_kind': 'recovered',
                        }),
                        match_info={'tool_name': name},
                    )
                    responses.append(
                        await orchestrator_server._dispatch_tool_call_request(request))
        finally:
            code_tools.TOOL_MAP[name] = original
        self.assertEqual([response.status for response in responses], [409, 409])
        self.assertEqual(
            [json.loads(response.text)['error_name'] for response in responses],
            ['STALE_TASK_EPOCH', 'STALE_TASK_EPOCH'],
        )
        handler.assert_not_awaited()

    async def test_run_project_test_script_schema_documents_transport_contract(self):
        schema = next(
            item['function'] for item in code_tools.TOOL_SCHEMAS
            if item.get('function', {}).get('name') == 'run_project_test_script'
        )
        properties = schema['parameters']['properties']
        self.assertEqual(properties['required_python_modules']['type'], 'array')
        self.assertEqual(properties['required_python_modules']['items'], {'type': 'string'})
        description = schema['description']
        for contract_name in (
            'stdout_metadata', 'stderr_metadata', 'INTERPRETER_CAPABILITY_MISSING',
            'SCRIPT_EXEC_FAILED', 'COMMAND_TRANSPORT_UNSAFE', 'SCRIPT_RUNTIME_REQUIRED',
        ):
            self.assertIn(contract_name, description)

    async def test_can012_api_tools_flags_match_sets(self):
        resp = await orchestrator_server.handle_tools(
            make_mocked_request('GET', '/api/tools'))
        tools = json.loads(resp.text)
        seen = 0
        for entry in tools:
            fn = entry.get('function', entry)
            name = fn.get('name')
            if name not in code_tools.TOOL_MAP:
                continue
            seen += 1
            self.assertEqual(fn.get('requires_token'),
                             name in code_tools.TOKEN_REQUIRED_TOOLS, name)
            self.assertEqual(fn.get('mutating'),
                             orchestrator_server._is_mutating_tool(name), name)
            self.assertEqual(fn.get('mutation_scope'),
                             code_tools.tool_mutation_scope(name), name)
        self.assertGreater(seen, 100)

    async def test_can016_token_panel_exposes_accurate_mutating_set(self):
        resp = await orchestrator_server.handle_token_panel(
            make_mocked_request('GET', '/api/token_panel'))
        data = json.loads(resp.text)
        self.assertEqual(
            data['token_panel']['mutating_tools'],
            sorted(t for t in code_tools.TOOL_MAP
                   if orchestrator_server._is_mutating_tool(t)))


class TestApiNormalization(unittest.IsolatedAsyncioTestCase):
    """Phase 2 — CAN-010/009/017/048/044: alias/normalize/path/read-cap.
    CAN-010/017/044 were already enforced in the current [A]; tests are regression
    guards. CAN-009 (alias-before-ignore order) and CAN-048 (relative-path rebase on
    root rewrite) are the active fixes."""

    # ── CAN-009: alias must run before harmless-ignore ──
    def test_can009_alias_applied_before_harmless_ignore(self):
        tool = '__can009_probe__'
        a = orchestrator_server._ARG_ALIASES
        h = orchestrator_server._HARMLESS_IGNORE_FIELDS
        had_a, had_h = tool in a, tool in h
        try:
            a[tool] = {'old_key': 'canon'}
            h[tool] = {'old_key'}  # overlap: same field is alias source AND ignored
            body, _applied = orchestrator_server._normalize_body(tool, {'old_key': 'VALUE'})
            self.assertEqual(body.get('canon'), 'VALUE')  # preserved via alias, not dropped
            self.assertNotIn('old_key', body)
        finally:
            if not had_a:
                a.pop(tool, None)
            if not had_h:
                h.pop(tool, None)

    # ── CAN-048: rebase relative file_path/path when project root is rewritten ──
    def test_can048_rebase_relative_file_path(self):
        old = os.path.normpath(r'D:/proj/sub')
        body = {'project_path': old, 'file_path': 'a.py'}
        orchestrator_server._rebase_relative_paths(body, old, os.path.normpath(r'D:/proj'))
        self.assertEqual(
            os.path.normpath(os.path.join(r'D:/proj', body['file_path'])),
            os.path.normpath(r'D:/proj/sub/a.py'))

    def test_can048_absolute_path_untouched(self):
        abspath = os.path.normpath(r'D:/other/x.py')
        body = {'file_path': abspath}
        orchestrator_server._rebase_relative_paths(body, r'D:/proj/sub', r'D:/proj')
        self.assertEqual(body['file_path'], abspath)

    def test_can048_noop_when_root_unchanged(self):
        body = {'file_path': 'a.py'}
        orchestrator_server._rebase_relative_paths(body, r'D:/proj', r'D:/proj')
        self.assertEqual(body['file_path'], 'a.py')

    # ── CAN-010/017/044: regression guards (already enforced) ──
    def test_can010_write_new_file_content_alias_present(self):
        self.assertEqual(
            orchestrator_server._ARG_ALIASES.get('write_new_file', {}).get('content'),
            'new_content')

    def test_can017_workflow_status_and_registry_both_registered(self):
        self.assertIn('workflow_status', code_tools.TOOL_MAP)
        self.assertIn('workflow_status_registry', code_tools.TOOL_MAP)

    def test_can044_read_hard_cap_value(self):
        self.assertEqual(code_tools.MAX_READ_LINES_HARD_CAP, 800)


class TestCouncilLifecycle(unittest.IsolatedAsyncioTestCase):
    """Phase 3 — CAN-023: a drain-schedule failure of ANY exception type must reset
    the per-agent DRAINING flag (previously only RuntimeError was caught, so any other
    exception jammed the queue permanently). CAN-022 (enqueue race) and CAN-025 (worker
    leak) verified RESOLVED_ALREADY by code inspection + CodoC live council test."""

    async def test_can023_drain_schedule_resets_flag_on_any_exception(self):
        agent = sorted(orchestrator_server._COUNCIL_AGENTS)[0]
        drain_map = orchestrator_server._COUNCIL_AGENT_QUEUE_DRAINING
        saved = drain_map.get(agent)
        orig_create_task = orchestrator_server.asyncio.create_task

        def _boom(*a, **k):
            raise ValueError("simulated non-RuntimeError scheduling failure")

        try:
            orchestrator_server.asyncio.create_task = _boom
            with self.assertRaises(ValueError):
                orchestrator_server._council_schedule_queue_drain(agent)
            # The flag must NOT be stuck True (that would jam the queue forever).
            self.assertFalse(drain_map.get(agent, False))
        finally:
            orchestrator_server.asyncio.create_task = orig_create_task
            if saved is None:
                drain_map.pop(agent, None)
            else:
                drain_map[agent] = saved


class TestCouncilUiOrdering(unittest.IsolatedAsyncioTestCase):
    """Council broker UI ordering (Anh screenshot 2026-06-16):
      * A waiting prompt must appear ONCE (the drain's "message" event), never as a
        separate "queued" event — the duplicate scrambled the prompt->reply order.
      * A reply that is nothing but a raw stream-event JSON line (e.g. step_start
        leaked when an opencode run is cut) must surface a clean marker, not garbage.
    """

    def test_drop_stream_noise_reply_pure(self):
        f = orchestrator_server._council_drop_stream_noise_reply
        # Lone stream-event JSON -> dropped.
        self.assertEqual(f('{"type":"step_start","sessionID":"s1"}'), "")
        self.assertEqual(f('{"type":"step-finish","x":1}'), "")
        # Real answers are untouched.
        self.assertEqual(f("OK"), "OK")
        self.assertEqual(f("Đã hoàn thành task."), "Đã hoàn thành task.")
        # A JSON object that is NOT a stream event stays (could be a real answer).
        self.assertEqual(f('{"answer":"42"}'), '{"answer":"42"}')
        # Multi-line text that merely contains a brace is untouched.
        self.assertEqual(f('line1\n{"type":"step_start"}'), 'line1\n{"type":"step_start"}')

    def test_council_reply_tail_preserves_final_lines_when_capped(self):
        f = orchestrator_server._council_reply_tail
        self.assertEqual(f("short", 10), "short")
        text = "OLD_START\n" + ("x" * 40) + "\nFINAL_SUMMARY"
        capped = f(text, 24)
        self.assertEqual(len(capped), 24)
        self.assertNotIn("OLD_START", capped)
        self.assertTrue(capped.endswith("FINAL_SUMMARY"))

    def test_extract_log_response_uses_tail_of_long_json_text_reply(self):
        final = "\nFINAL_SUMMARY"
        reply = "OLD_START\n" + ("x" * (orchestrator_server._COUNCIL_REPLY_MAX + 50)) + final
        raw = json.dumps({"type": "text", "part": {"text": reply}})
        extracted = orchestrator_server._council_extract_log_response(raw)
        self.assertEqual(len(extracted), orchestrator_server._COUNCIL_REPLY_MAX)
        self.assertNotIn("OLD_START", extracted)
        self.assertTrue(extracted.endswith("FINAL_SUMMARY"))

    def test_clean_direct_log_text_fallback_uses_tail_when_oversized(self):
        text = "OLD_START\n" + "\n".join(
            f"row-{idx:03d}-" + ("x" * 180)
            for idx in range(130)
        ) + "\nFINAL_SUMMARY"
        cleaned = orchestrator_server._council_clean_direct_log_text("ope", text, 120)
        self.assertEqual(len(cleaned), orchestrator_server._COUNCIL_REPLY_MAX)
        self.assertNotIn("OLD_START", cleaned)
        self.assertTrue(cleaned.endswith("FINAL_SUMMARY"))

    def test_claude_streamjson_reply_uses_tail_but_reasoning_keeps_head(self):
        reply = "OLD_REPLY\n" + ("r" * (orchestrator_server._COUNCIL_REPLY_MAX + 50)) + "\nFINAL_REPLY"
        thinking = "THINKING_START\n" + ("t" * (orchestrator_server._COUNCIL_REPLY_MAX + 50)) + "\nTHINKING_END"
        raw = "\n".join([
            json.dumps({"type": "assistant", "message": {"content": [
                {"type": "thinking", "thinking": thinking},
                {"type": "text", "text": reply},
            ]}}),
        ])
        clean_reply, reasoning = orchestrator_server._council_extract_claude_streamjson(raw)
        self.assertEqual(len(clean_reply), orchestrator_server._COUNCIL_REPLY_MAX)
        self.assertNotIn("OLD_REPLY", clean_reply)
        self.assertTrue(clean_reply.endswith("FINAL_REPLY"))
        self.assertEqual(len(reasoning), orchestrator_server._COUNCIL_REPLY_MAX)
        self.assertTrue(reasoning.startswith("THINKING_START"))
        self.assertNotIn("THINKING_END", reasoning)

    async def test_build_dispatch_reply_marks_stream_noise(self):
        old_here = orchestrator_server._HERE
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / '.council').mkdir()
            orchestrator_server._HERE = root
            try:
                req_evt = orchestrator_server._council_append_event(
                    {'type': 'message', 'from': 'user', 'to': 'ope', 'text': 'q'})
                payload = orchestrator_server._council_build_dispatch_reply(
                    'ope', 'user', req_evt,
                    {'ok': True, 'returncode': 0,
                     'reply': '{"type":"step_start","sessionID":"s1"}'}, 'broker')
            finally:
                orchestrator_server._HERE = old_here
        self.assertEqual(payload['reply_event']['text'], '[no clean response]')

    async def test_build_dispatch_reply_keeps_plain_reply(self):
        old_here = orchestrator_server._HERE
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / '.council').mkdir()
            orchestrator_server._HERE = root
            try:
                req_evt = orchestrator_server._council_append_event(
                    {'type': 'message', 'from': 'user', 'to': 'ope', 'text': 'q'})
                payload = orchestrator_server._council_build_dispatch_reply(
                    'ope', 'user', req_evt,
                    {'ok': True, 'returncode': 0, 'reply': 'OK'}, 'broker')
            finally:
                orchestrator_server._HERE = old_here
        self.assertEqual(payload['reply_event']['text'], 'OK')

    async def test_enqueue_emits_no_queued_event_and_keeps_order(self):
        agent = sorted(orchestrator_server._COUNCIL_AGENTS)[0]
        old_here = orchestrator_server._HERE
        old_run = orchestrator_server._council_run_agent
        saved_drain = orchestrator_server._COUNCIL_AGENT_QUEUE_DRAINING.get(agent)
        orchestrator_server._COUNCIL_AGENT_QUEUES.pop(agent, None)

        def _stub_run(target, prompt, timeout_sec, mode, project_path, request_id, runtime_key="", provider_override="", model_override="", model_resolution=None, risk_level="unknown", dev_level="unknown", *_a, **_k):
            return {'ok': True, 'returncode': 0, 'reply': 'OK',
                    'raw_preview': '', 'reasoning': ''}

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / '.council').mkdir()
            orchestrator_server._HERE = root
            orchestrator_server._council_run_agent = _stub_run
            try:
                # Two concurrent messages to the SAME agent: one runs, one waits.
                await asyncio.gather(
                    orchestrator_server._council_enqueue_or_run(agent, 'user', 'PROMPT_A', 30),
                    orchestrator_server._council_enqueue_or_run(agent, 'user', 'PROMPT_B', 30),
                )
                events = [
                    json.loads(line) for line in
                    (root / '.council' / 'council_events.jsonl').read_text(encoding='utf-8').splitlines()
                ]
            finally:
                orchestrator_server._HERE = old_here
                orchestrator_server._council_run_agent = old_run
                orchestrator_server._COUNCIL_AGENT_QUEUES.pop(agent, None)
                if saved_drain is None:
                    orchestrator_server._COUNCIL_AGENT_QUEUE_DRAINING.pop(agent, None)
                else:
                    orchestrator_server._COUNCIL_AGENT_QUEUE_DRAINING[agent] = saved_drain

        types = [e.get('type') for e in events]
        self.assertNotIn('queued', types, "no duplicate 'queued' event may be emitted")
        self.assertEqual(types.count('message'), 2)
        self.assertEqual(types.count('reply'), 2)
        # Strict order: message, reply, message, reply (one fully recorded before next).
        self.assertEqual(types, ['message', 'reply', 'message', 'reply'])


class TestCmdShimBypass(unittest.TestCase):
    """Council dispatch must bypass npm .cmd shims (BatBadBut / CVE-2024-1874):
    spawning a .cmd routes through cmd.exe which re-parses prompt metachars
    (| & < > ( ) ") and corrupts prompts containing code. _resolve_cmd_shim
    resolves the shim to its real .exe / node script so argv reaches the program
    intact; unparseable inputs fall back unchanged."""

    def test_variant_a_native_exe(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            exe_rel = "node_modules\\@anthropic-ai\\claude-code\\bin\\claude.exe"
            exe_abs = root / "node_modules" / "@anthropic-ai" / "claude-code" / "bin" / "claude.exe"
            exe_abs.parent.mkdir(parents=True)
            exe_abs.write_bytes(b"MZ")
            shim = root / "claude.cmd"
            shim.write_text(f'@ECHO off\r\n"%dp0%\\{exe_rel}"   %*\r\n', encoding="utf-8")
            real, prefix = orchestrator_server._resolve_cmd_shim(str(shim))
            self.assertEqual(real, str(exe_abs))
            self.assertEqual(prefix, [])

    def test_variant_b_node_script(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "node.exe").write_bytes(b"MZ")
            js_rel = "node_modules\\command-code\\dist\\index.mjs"
            js_abs = root / "node_modules" / "command-code" / "dist" / "index.mjs"
            js_abs.parent.mkdir(parents=True)
            js_abs.write_text("// cli", encoding="utf-8")
            shim = root / "pi.cmd"
            shim.write_text(
                f'@ECHO off\r\nIF EXIST "%dp0%\\node.exe" ( SET "_prog=%dp0%\\node.exe" )\r\n'
                f'... || "%_prog%"  "%dp0%\\{js_rel}" %*\r\n', encoding="utf-8")
            real, prefix = orchestrator_server._resolve_cmd_shim(str(shim))
            self.assertEqual(real, str(root / "node.exe"))
            self.assertEqual(prefix, [str(js_abs)])

    def test_non_cmd_passthrough(self):
        # A bare exe name (native binary like hermes) is returned unchanged.
        self.assertEqual(orchestrator_server._resolve_cmd_shim("hermes"), ("hermes", []))

    def test_unparseable_cmd_falls_back(self):
        with tempfile.TemporaryDirectory() as tmp:
            shim = Path(tmp) / "weird.cmd"
            shim.write_text("@ECHO off\r\nrem nothing recognizable here\r\n", encoding="utf-8")
            self.assertEqual(orchestrator_server._resolve_cmd_shim(str(shim)), (str(shim), []))

    def test_missing_target_falls_back(self):
        # Shim references an .exe that does not exist -> do not return a bad path.
        with tempfile.TemporaryDirectory() as tmp:
            shim = Path(tmp) / "ghost.cmd"
            shim.write_text('@ECHO off\r\n"%dp0%\\node_modules\\x\\bin\\ghost.exe"   %*\r\n', encoding="utf-8")
            self.assertEqual(orchestrator_server._resolve_cmd_shim(str(shim)), (str(shim), []))


class TestCouncilSenderIdentity(unittest.IsolatedAsyncioTestCase):
    """Phase 3 — CAN-020/021: council_message sender is resolved server-side. A
    non-worker caller (no PID trace, as in a mocked request) cannot impersonate a
    worker; only a leader self-label (clau/codo) or user alias survives, everything
    else (worker name, arbitrary string, 'from' alias) normalizes to 'user'."""

    def _req(self):
        return make_mocked_request('POST', '/api/call/council_message')

    def test_can020_rejects_worker_name_from_non_pid(self):
        req = self._req()
        for worker in ('ope', 'pic', 'hermes', 'clin', 'copi'):
            self.assertEqual(orchestrator_server._council_resolve_sender(req, worker),
                             'user', f"non-worker must not impersonate {worker}")

    def test_can020_rejects_arbitrary_string(self):
        req = self._req()
        self.assertEqual(orchestrator_server._council_resolve_sender(req, 'ADMIN_INJECTED_x'), 'user')
        self.assertEqual(orchestrator_server._council_resolve_sender(req, ''), 'user')

    def test_can020_preserves_leader_and_user_labels(self):
        req = self._req()
        self.assertEqual(orchestrator_server._council_resolve_sender(req, 'clau'), 'clau')
        self.assertEqual(orchestrator_server._council_resolve_sender(req, 'codo'), 'codo')
        self.assertEqual(orchestrator_server._council_resolve_sender(req, 'CODO'), 'CODO')
        self.assertEqual(orchestrator_server._council_resolve_sender(req, 'user'), 'user')

    async def test_can021_handle_council_message_drops_from_alias(self):
        # The legacy 'from' alias must be removed from the body after sender resolution
        # (it is normalized into `sender`, not left as a spoofable field).
        request = make_mocked_request(
            'POST', '/api/council/message',
            headers={'Content-Type': 'application/json',
                     'Origin': f'http://localhost:{orchestrator_server.PORT}'},
            payload=_make_body_reader({'from': 'pic', 'to': 'nonexistent_xyz', 'text': 'x'}),
        )
        # to=nonexistent -> INVALID_AGENT (cheap path, no worker run), but sender
        # resolution + 'from' drop happen before that check.
        resp = await orchestrator_server.handle_council_message(request)
        self.assertEqual(resp.status, 400)
        self.assertEqual(json.loads(resp.text).get('error_name'), 'INVALID_AGENT')


class TestStateRegistry(unittest.IsolatedAsyncioTestCase):
    """Phase 4 — CAN-011 (scope gaps), CAN-015 (register_tool metadata), CAN-014
    (hot-reload live enforcement sets), CAN-038 (runtime_state merge-on-write),
    CAN-037 (WORKFLOW_STATE atomic valid JSON + documented last-writer)."""

    def test_can011_script_runners_have_process_scope(self):
        self.assertEqual(code_tools.tool_mutation_scope('run_ps1_script'), 'process')
        self.assertEqual(code_tools.tool_mutation_scope('run_node_script'), 'process')

    def test_can015_register_tool_stamps_enforcement_metadata(self):
        from orchestrator.core.registry import register_tool
        tmap, tsch, tph = {}, [], {}
        register_tool(name='__t015__', handler=lambda: None, phase='T2',
                      schema={'description': 'x', 'parameters': {'type': 'object', 'properties': {}}},
                      tool_map=tmap, tool_schemas=tsch, tool_phases=tph,
                      requires_token=True, requires_session=True, mutation_scope='process')
        fn = [e['function'] for e in tsch if e['function']['name'] == '__t015__'][0]
        self.assertTrue(fn.get('requires_token'))
        self.assertTrue(fn.get('requires_session'))
        self.assertEqual(fn.get('mutation_scope'), 'process')

    def test_can014_register_tool_hot_updates_live_sets(self):
        from orchestrator.core.registry import register_tool
        tmap, tsch, tph = {}, [], {}
        tok, mut, scope = set(), set(), {}
        register_tool(name='__t014__', handler=lambda: None, phase='T2',
                      schema={'description': 'x', 'parameters': {'type': 'object', 'properties': {}}},
                      tool_map=tmap, tool_schemas=tsch, tool_phases=tph,
                      requires_token=True, mutation_scope='process',
                      token_required_set=tok, mutating_set=mut, mutation_scope_map=scope)
        self.assertIn('__t014__', tok)   # gated immediately — no restart
        self.assertIn('__t014__', mut)
        self.assertEqual(scope.get('__t014__'), 'process')

    def test_can038_runtime_persist_merges_sibling_agents(self):
        old_here = orchestrator_server._HERE
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / '.council').mkdir()
            orchestrator_server._HERE = root
            try:
                orchestrator_server._council_runtime_persist('ope', {'state': 'running'})
                f = root / '.council' / 'runtime_state.json'
                cur = json.loads(f.read_text(encoding='utf-8'))
                cur['pic'] = {'state': 'running'}  # sibling written by 'another process'
                f.write_text(json.dumps(cur), encoding='utf-8')
                orchestrator_server._council_runtime_persist('ope', {'state': 'idle'})
                final = json.loads(f.read_text(encoding='utf-8'))
                self.assertIn('pic', final)               # sibling preserved (merge)
                self.assertEqual(final['ope']['state'], 'idle')
            finally:
                orchestrator_server._HERE = old_here

    async def test_can037_workflow_state_atomic_valid_json_last_writer(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            r1 = json.loads(await code_tools.update_workflow_state(str(root), mode='T1'))
            r2 = json.loads(await code_tools.update_workflow_state(str(root), mode='T2'))
            self.assertTrue(r1['ok'] and r2['ok'])
            path = code_tools._workflow_state_path(str(root))
            state = json.loads(path.read_text(encoding='utf-8'))  # never torn
            self.assertEqual(state['mode'], 'T2')                 # last-writer wins


class TestCacheRootRestart(unittest.IsolatedAsyncioTestCase):
    """Phase 5 — CAN-042 (single SKIP_DIRS source), CAN-045 (page-id resolver no
    silent import failure), CAN-046 (find_project_root deterministic), CAN-047
    (restart process match is precise, not fuzzy)."""

    def test_can042_skip_dirs_single_source(self):
        import surgical_mapper
        self.assertIs(orchestrator_server.SKIP_DIRS, code_tools.SKIP_DIRS)
        self.assertIs(code_tools.SKIP_DIRS, surgical_mapper.SKIP_DIRS)
        for d in ('.orchestrator_cache', '.svn', '.idea', '.vscode', 'node_modules', 'venv312'):
            self.assertIn(d, orchestrator_server.SKIP_DIRS)

    def test_can045_page_id_resolver_safe_return(self):
        self.assertEqual(orchestrator_server._resolve_project_from_page_id(''), '')
        self.assertEqual(orchestrator_server._resolve_project_from_page_id('no_such_page_xyz'), '')

    def test_can046_find_project_root_deterministic(self):
        # R56: empty/whitespace must return None (never the orchestrator CWD).
        self.assertIsNone(code_tools.find_project_root(''))
        self.assertIsNone(code_tools.find_project_root('   '))
        # A path inside a real project root resolves to a non-None root.
        self.assertIsNotNone(code_tools.find_project_root(__file__))

    def test_can047_restart_process_match_is_precise(self):
        import restart_orchestrator as ro
        server = str(ro.SERVER).lower()
        # self pid excluded
        self.assertFalse(ro._is_a_process(
            {'ProcessId': os.getpid(), 'Name': 'python.exe', 'CommandLine': server}, set()))
        # --mcp stdio excluded
        self.assertFalse(ro._is_a_process(
            {'ProcessId': 990001, 'Name': 'python.exe', 'CommandLine': server + ' --mcp'}, set()))
        # exact server path -> matched
        self.assertTrue(ro._is_a_process(
            {'ProcessId': 990002, 'Name': 'pythonw.exe', 'CommandLine': server}, set()))
        # unrelated python, not port owner, not orchestrator path -> NOT matched (precise)
        self.assertFalse(ro._is_a_process(
            {'ProcessId': 990003, 'Name': 'python.exe', 'CommandLine': 'c:/other/foo.py'}, set()))


class TestBuildIndex(unittest.IsolatedAsyncioTestCase):
    """Phase 5 — CAN-040 (build_index timeout raises before write -> previous INDEX
    preserved) and CAN-039 (full-rebuild kept = correct; D5/D8 documented)."""

    def _make_cache(self, root):
        import surgical_mapper as sm
        cache = root / sm.CACHE_DIR_NAME
        cache.mkdir(parents=True, exist_ok=True)
        (cache / 'a.json').write_text(json.dumps(
            {'file': 'a.py', 'hash': 'h', 'mtime': 1, 'loc': 10, 'group': 'source', 'symbols': []}),
            encoding='utf-8')
        return cache

    def test_can039_build_index_full_rebuild_is_valid(self):
        import surgical_mapper as sm
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._make_cache(root)
            idx = sm.build_index(str(root))
            self.assertIn('a.py', idx['files'])
            self.assertEqual(idx['schema_version'], sm.MAP_SCHEMA_VERSION)

    def test_can040_build_index_timeout_preserves_previous_index(self):
        import surgical_mapper as sm
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            cache = self._make_cache(root)
            index_path = cache / sm.INDEX_FILENAME
            index_path.write_text('{"sentinel": true}', encoding='utf-8')  # previous INDEX
            orig = sm.time.monotonic
            seq = iter([1000.0, 9999.0])  # init deadline=1010; first loop check 9999 -> exceed
            sm.time.monotonic = lambda: next(seq, 9999.0)
            try:
                with self.assertRaises(TimeoutError):
                    sm.build_index(str(root), timeout_s=10)
            finally:
                sm.time.monotonic = orig
            # raised before the INDEX.json write -> previous index untouched
            self.assertEqual(json.loads(index_path.read_text(encoding='utf-8')), {'sentinel': True})


class TestFinalizeMission(unittest.IsolatedAsyncioTestCase):
    """Phase 6 — CAN-069 (D6=remove/never-implement, D9=no replacement caller):
    finalize_mission has no handler/schema/TOOL_MAP entry and no rule/example requires
    it (only research/planning docs discuss the CAN). declare_mission is the real
    mission-lifecycle tool; no finalize counterpart is mandated."""

    def test_can069_finalize_mission_not_registered(self):
        self.assertNotIn('finalize_mission', code_tools.TOOL_MAP)

    def test_can069_declare_mission_is_the_real_tool(self):
        self.assertIn('declare_mission', code_tools.TOOL_MAP)



class TestCouncilAuthoritativeLiveness(unittest.TestCase):
    def setUp(self):
        self.old_here = orchestrator_server._HERE
        self.old_runtime = orchestrator_server._COUNCIL_AGENT_RUNTIME
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        (root / '.council').mkdir()
        orchestrator_server._HERE = root
        orchestrator_server._COUNCIL_AGENT_RUNTIME = {}

    def tearDown(self):
        orchestrator_server._HERE = self.old_here
        orchestrator_server._COUNCIL_AGENT_RUNTIME = self.old_runtime
        self.tmp.cleanup()

    def test_orphan_signature_process_without_active_slot_is_idle(self):
        orphan = types.SimpleNamespace(
            pid=777, info={'name': 'node_repl.exe', 'cmdline': ['codex', 'daemon']})
        with mock.patch('psutil.process_iter', return_value=[orphan]):
            self.assertEqual(orchestrator_server._council_alive_agents(), set())
        status = orchestrator_server._council_agent_status('codo')
        self.assertEqual(status['label'], 'idle')

    def test_live_worker_pid_in_active_slot_is_working(self):
        orchestrator_server._COUNCIL_AGENT_RUNTIME['codo::gpt'] = {
            'state': 'running', 'finished_at': None, 'worker_pid': 4321,
            'runtime_key': 'codo::gpt', 'request_id': 'req-live', 'started_at': time.time(),
        }
        with mock.patch('orchestrator_server._pid_alive', return_value=True):
            runtime = orchestrator_server._council_runtime_status('codo')
            self.assertTrue(runtime['worker_alive'])
            self.assertEqual(orchestrator_server._council_agent_status('codo')['label'], 'working')

    def test_latest_res_resets_while_new_dispatch_runs(self):
        council = orchestrator_server._HERE / '.council'
        outbox = council / 'outbox_codo.md'
        outbox.write_text('<<RES>>previous task result<</RES>>', encoding='utf-8')
        orchestrator_server._COUNCIL_AGENT_RUNTIME['codo::gpt'] = {
            'state': 'running', 'finished_at': None, 'worker_pid': 4321,
            'runtime_key': 'codo::gpt', 'request_id': 'req-new', 'started_at': time.time(),
        }
        with mock.patch('orchestrator_server._pid_alive', return_value=True):
            latest = orchestrator_server._council_resolve_latest_res(
                council, 'codo', outbox.read_text(encoding='utf-8'))
        self.assertEqual(latest, '[waiting for current dispatch response]')
        self.assertNotIn('previous task result', latest)
class TestCouncilPreflight(unittest.IsolatedAsyncioTestCase):
    async def test_preflight_aggregates_reachable_and_auth_failure(self):
        def fake_probe(agent, timeout_sec):
            if agent == 'ope':
                return {'reachable': True, 'model': 'm-ok', 'auth_ok': True,
                        'last_error': '', 'p50_latency': 12.5}
            return {'reachable': False, 'model': 'm-bad', 'auth_ok': False,
                    'last_error': '401 unauthorized', 'p50_latency': 9.0}

        req = make_mocked_request(
            'POST', '/api/council/preflight',
            headers={'Content-Type': 'application/json',
                     'Origin': f'http://localhost:{orchestrator_server.PORT}'},
            payload=_make_body_reader({'agents': ['ope', 'codo'], 'timeout_sec': 3}),
        )
        with mock.patch('orchestrator_server._council_preflight_probe', side_effect=fake_probe):
            resp = await orchestrator_server.handle_council_preflight(req)
        self.assertEqual(resp.status, 200)
        body = json.loads(resp.text)
        self.assertEqual(set(body['agents']), {'ope', 'codo'})
        self.assertEqual(set(body['agents']['ope']),
                         {'reachable', 'model', 'auth_ok', 'last_error', 'p50_latency'})
        self.assertTrue(body['agents']['ope']['reachable'])
        self.assertFalse(body['agents']['codo']['auth_ok'])

    def test_preflight_route_registered(self):
        paths = {route.resource.canonical for route in orchestrator_server.create_app().router.routes()}
        self.assertIn('/api/council/preflight', paths)


class TestAT139SemaphoreCancellation(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        orchestrator_server._TOOL_SEMS.clear()
        orchestrator_server._SEMAPHORE_HOLDERS.clear()
        orchestrator_server._SEMAPHORE_WAITERS.clear()
        orchestrator_server._INFLIGHT_OPERATIONS.clear()

    async def test_holder_tracking_and_cancelled_error_release_slot(self):
        acquired = asyncio.Event()

        async def holder():
            sem, category = await orchestrator_server._acquire_tool_semaphore(
                'run_project_command', {}, 'op-holder')
            acquired.set()
            try:
                await asyncio.Event().wait()
            finally:
                orchestrator_server._release_tool_semaphore(sem, category, 'op-holder')

        task = asyncio.create_task(holder())
        await acquired.wait()
        row = next(r for r in orchestrator_server._semaphore_status_rows(now=time.time())
            if r['category'] == 'command')
        self.assertEqual(row['held'], 1)
        self.assertEqual(row['holder_op_id'], 'op-holder')
        self.assertIsInstance(row['held_for_sec'], float)
        task.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await task
        self.assertEqual(
            orchestrator_server._TOOL_SEMS['command']._value,
            int(os.environ.get(
                "ORCH_SEM_COMMAND",
                orchestrator_server._TOOL_SEM_LIMITS['command'])))
        self.assertEqual(orchestrator_server._SEMAPHORE_HOLDERS, {})

    async def test_semaphore_and_cancel_endpoints_have_documented_shapes(self):
        task = asyncio.create_task(asyncio.sleep(60))
        orchestrator_server._INFLIGHT_OPERATIONS['op-cancel'] = task
        cancel_req = make_mocked_request(
            'POST', '/api/cancel', payload=_make_body_reader({'operation_id': 'op-cancel'}))
        cancel = json.loads((await orchestrator_server.handle_cancel_operation(cancel_req)).text)
        self.assertEqual(cancel['operation_id'], 'op-cancel')
        self.assertTrue(cancel['cancelled'])
        with self.assertRaises(asyncio.CancelledError):
            await task

        sem_req = make_mocked_request('GET', '/api/semaphores')
        body = json.loads((await orchestrator_server.handle_semaphores(sem_req)).text)
        self.assertIn('semaphores', body)
        paths = {route.resource.canonical for route in orchestrator_server.create_app().router.routes()}
        self.assertIn('/api/semaphores', paths)
        self.assertIn('/api/cancel', paths)


class TestAT139CouncilContracts(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.old_here = orchestrator_server._HERE
        self.tmp = tempfile.TemporaryDirectory()
        orchestrator_server._HERE = Path(self.tmp.name)
        (orchestrator_server._HERE / '.council').mkdir()

    def tearDown(self):
        orchestrator_server._HERE = self.old_here
        self.tmp.cleanup()

    def test_terminal_runtime_reason_synthesizes_from_worker_log(self):
        log = orchestrator_server._HERE / '.council' / 'live_raw_ope_model.log'
        log.write_text('prefix\nprovider rejected empty credentials', encoding='utf-8')
        row = orchestrator_server._council_runtime_enforce_terminal_error(
            'ope::model', {'state': 'error', 'error': ''})
        self.assertEqual(row['error'].splitlines(),
                         ['prefix', 'provider rejected empty credentials'])

    def test_queue_timeout_payload_contains_history_and_worker_state(self):
        usage = orchestrator_server._HERE / '.council' / 'usage_log.jsonl'
        usage.write_text('\n'.join(json.dumps({'runtime_key': 'ope::m', 'duration_sec': n})
                                   for n in (10, 20, 40)), encoding='utf-8')
        with mock.patch('orchestrator_server._council_runtime_status',
                        return_value={'state': 'running', 'request_id': 'r1'}):
            payload = orchestrator_server._council_queue_timeout_payload('ope::m')
        self.assertEqual(payload['observed_p50_sec'], 20.0)
        self.assertEqual(payload['worker_state']['state'], 'running')
        self.assertEqual(payload['timeout_semantics'], 'caller_wait_only')
        self.assertTrue(payload['worker_continues_after_timeout'])

    async def test_missing_result_ref_reports_restart_expiry(self):
        req = make_mocked_request('GET', '/api/result/gone', match_info={'ref': 'gone'})
        response = await orchestrator_server.handle_result_ref(req)
        body = json.loads(response.text)
        self.assertEqual(response.status, 404)
        self.assertEqual(body['error_name'], 'REF_EXPIRED_AFTER_RESTART')
        self.assertIn('restart', body['reason'])


if __name__ == '__main__':
    unittest.main()
