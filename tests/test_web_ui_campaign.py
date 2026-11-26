"""Regression checks for packaged pages and the integrated Monitor read API."""
import asyncio
import json
import os
import sqlite3
import tempfile
import threading
import time
import unittest
from contextlib import closing
from pathlib import Path
from unittest.mock import patch

from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer
from yarl import URL

try:
    from orchestrator import web_views
except ImportError:  # UI-lane module absent in this checkout (separate campaign)
    web_views = None

_WEB_VIEWS_PRESENT = web_views is not None
from path_runtime import resolve_ui_html


@unittest.skipUnless(_WEB_VIEWS_PRESENT, 'UI-lane module orchestrator.web_views is not part of this checkout (separate campaign)')
class PackagedPagesTests(unittest.TestCase):
    def test_bundle_wins_without_parent_ui(self):
        with tempfile.TemporaryDirectory() as root:
            checkout = Path(root) / "checkout"
            bundled = checkout / "orchestrator" / "web_ui" / "main.html"
            bundled.parent.mkdir(parents=True)
            bundled.touch()
            self.assertEqual(resolve_ui_html(checkout), bundled)

    def test_navigation_is_in_body_not_head_script(self):
        html = '<html><head><script>const tag = "<body>";</script></head><body><main/></body></html>'
        for page in ("main", "council", "monitor", "matrix"):
            with self.subTest(page=page):
                decorated = web_views.decorate_page(html, page)
                head, body = decorated.split("</head>")
                self.assertIn('const tag = "<body>";', head)
                self.assertIn('/assets/toola/theme.css', head)
                self.assertNotIn('<nav', head)
                self.assertIn(f'data-toola-page="{page}"', head)
                self.assertEqual(body.count('aria-current="page"'), 1)
                self.assertEqual(body.count('<nav'), 1)



@unittest.skipUnless(_WEB_VIEWS_PRESENT, 'UI-lane module orchestrator.web_views is not part of this checkout (separate campaign)')
class MonitorReadTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        fixture_dir = tempfile.TemporaryDirectory(prefix="monitor sqlite ")
        self.addCleanup(fixture_dir.cleanup)
        root = Path(fixture_dir.name)
        self.audit = root / "audit #log.db"
        self.toolq = root / "toolq.db"
        sessions = root / "sessions.db"
        graph = root / "graphs" / "toolq" / "graphify-out" / "graph.json"
        graph.parent.mkdir(parents=True)
        graph.write_text(json.dumps({"nodes": [
            {"id": "symbol", "source_file": "source.py", "file_type": "code"},
        ]}), encoding="utf-8")
        migrations = {
            self.audit: "CREATE TABLE durable_audit_outbox (audit_id TEXT, payload_json TEXT, state TEXT, created_at REAL);",
            sessions: """
                CREATE TABLE sessions (session_id, agent_id, session_title, mode, status, started_at);
                INSERT INTO sessions VALUES ('s1', 'fixture', 'Monitor', 'T1', 'open', '2026-01-01');
                CREATE TABLE write_operations (tool, status, project_path, file_path, duration_ms, started_at);
                INSERT INTO write_operations VALUES ('write_fix', 'ok', 'project', 'source.py', 12, '2026-01-01');
                CREATE TABLE events (audit_id, tool_name, target, range_label, outcome, error_name,
                                     agent_id, status, duration_ms, queue_wait_ms, natural_log_line, result_json, session_id);
                INSERT INTO events VALUES ('rich-only', 'write_fix', 'source.py', '1:1', 'ok', NULL,
                                           'fixture', 'ok', 12, 0, '', '{}', 's1');
            """,
            self.toolq: """
                CREATE TABLE retrieval_log (id, query, rank, used, ts);
                INSERT INTO retrieval_log VALUES (1, 'fixture', 1, 1, '2026-01-01');
                CREATE TABLE distill_runs (id, started_at, finished_at, raw_seen, points_created, mode);
            """,
        }
        for path, sql in migrations.items():
            with closing(sqlite3.connect(path)) as conn:
                conn.executescript(sql)
        for i in range(5):
            self.add_event(f"event-{i}")
        with closing(sqlite3.connect(sessions)) as conn, conn:
            conn.executemany("INSERT INTO events (audit_id, tool_name, target, agent_id, status, session_id) "
                             "VALUES (?, 'read_file_range', 'source.py', 'fixture', 'ok', 's1')",
                             [(f"event-{i}",) for i in range(5)])
        (root / "agents.json").write_text('{"agents": {"fixture": {}}}', encoding="utf-8")
        app = web.Application()
        web_views.register_web_routes(app)
        self.client = TestClient(TestServer(app))
        probe = self.enterContext(patch.object(web_views._MonitorStore, "get_json", return_value={"_down": True}))
        self.addAsyncCleanup(self.client.close)
        with patch.dict(os.environ, {
            "MONITOR_TOOLA_CACHE": str(root), "MONITOR_TOOLA_REGISTRY": str(root / "agents.json"),
            "MONITOR_TOOLQ_DB": str(self.toolq), "MONITOR_GRAPH_DIR": str(root / "graphs"),
            "ORCHESTRATOR_SESSIONS_DB": str(sessions), "ORCHESTRATOR_DURABLE_AUDIT_DB": str(self.audit),
        }):
            await self.client.start_server()
            self.monitor = app[web_views._MONITOR]
            await self.monitor.run(self.monitor.store.tick)
        self.assertEqual([c.args[0].rsplit('/', 1)[-1] for c in probe.call_args_list], ['health', 'health'])

    def add_event(self, event_id):
        payload = {"tool_name": "read_file_range", "status": "ok", "session_id": "s1",
                   "body": {"file_path": "source.py"}, "result": {"ok": True, "path": "source.py", "_task_agent_id": "fixture"}}
        with closing(sqlite3.connect(self.audit)) as conn, conn:
            conn.execute("INSERT INTO durable_audit_outbox VALUES (?, ?, 'applied', ?)",
                         (event_id, json.dumps(payload), time.time()))

    async def get_monitor(self, endpoint, params=(), status=200):
        res = await self.client.get('/api/monitor/' + endpoint, params=params,
                                    headers={'Cookie': 'client=fixture', 'Authorization': 'Bearer fixture'})
        self.assertEqual(res.status, status)
        self.assertEqual(res.headers.get('Cache-Control'), 'no-store')
        self.assertNotIn('Set-Cookie', res.headers)
        self.assertNotIn('Access-Control-Allow-Origin', res.headers)
        return await res.json()


    async def test_assets_and_pages_from_bundle_only(self):
        for page, key in [("/monitor", "monitor"), ("/matrixtool", "matrix")]:
            res = await self.client.get(page)
            self.assertEqual(res.status, 200)
            body = await res.text()
            self.assertIn('data-toola-page="' + key + '"', body)
            self.assertIn('aria-current="page"', body)
        for asset in ('theme.css', 'matrix.js', 'd3.min.js'):
            res = await self.client.get('/assets/toola/' + asset)
            self.assertEqual(res.status, 200)
            await res.read()
        denied = await self.client.get('/assets/toola/main.html')
        self.assertEqual(denied.status, 404)

    async def test_matrix_horizontal_map_shell_and_controls(self):
        res = await self.client.get('/matrixtool')
        self.assertEqual(res.status, 200)
        document = await res.text()
        self.assertIn('id="mm-map" data-mm-view="horizontal"', document)
        self.assertIn('id="mm-canvas"', document)
        self.assertIn('id="mm-branch-wires" aria-hidden="true"', document)
        self.assertIn('aria-label="Mức thu phóng"', document)
        for action in ('zoom-out', 'zoom-in', 'reset', 'sound'):
            self.assertIn('data-mm-view-action="' + action + '"', document)
        # Hover audio is decoration on a 260-node map, so it ships off and says so.
        self.assertIn('data-mm-view-action="sound" aria-pressed="false"', document)
        # Wires are hoverable through invisible fat twins only. The SVG layer itself
        # has to stay click-through, or it eats the node buttons stacked on it.
        self.assertIn('.mm-wires .mm-wire-hit { stroke: transparent', document)
        self.assertIn('pointer-events: stroke', document)
        # Dimming is by colour, not alpha -- the Atlas demo's own rule (:1479) that
        # opacity lets a wire read straight through the nodes it crosses.
        # AMENDED 2026-11-23, and the amendment is the point rather than a rewording.
        # This asserted the literal "grayscale(1) brightness(.7)", which was the ONE dim
        # rule the file had, and it dimmed .mm-wires path and no card at all. So the lock
        # pinned a contract that made Anh's complaint true: he asked for the map to dim
        # around a hovered or selected node, the CSS delivered a greyscale wire layer
        # with the nodes untouched, and a green test said that was the job done. The
        # intent -- colour, never alpha -- stands and is now asserted twice as strictly:
        # the focus dim covers cards as well as wires, and the alpha form is gone from
        # .is-muted, which had been contradicting the comment above it for a round.
        self.assertIn('grayscale(100%) brightness(.72)', document)
        self.assertIn('.mm-wires .is-muted { filter:', document)
        self.assertNotIn('stroke-opacity: .25', document,
            msg='.is-muted dimmed by alpha again, which lets a wire read through a card')
        # matrix.js:2307 stamps data-mm-errors on a tier button. Without a rule that
        # reads it the signal lives only in the DOM, which is exactly the bug it was
        # filed for, so the CSS half of that pair is pinned here.
        self.assertIn('.mm-tier[data-mm-errors]', document)
        # The hard data-theme="dark" stamp made the prefers-color-scheme block
        # unreachable forever, and theme.css re-points every matrix token after
        # injection (theme.css:57-62), so order alone cannot fix it either.
        self.assertNotIn('<html lang="vi" data-theme', document)
        for tier in ('T1', 'T2', 'T3', 'T4'):
            with self.subTest(tier=tier):
                self.assertIn(f'aria-expanded="false" aria-controls="mm-groups-{tier}"', document)
                self.assertIn(f'id="mm-groups-{tier}"', document)
        self.assertNotIn('data-mm-view="3d"', document)
        self.assertEqual((await self.client.get('/assets/toola/matrix.js')).status, 200)
    async def test_monitor_navigation_stays_above_scrollable_controls(self):
        try:
            from playwright.async_api import async_playwright, expect
        except ImportError:
            self.skipTest("Playwright is not installed")

        async with async_playwright() as pw:
            if not Path(pw.chromium.executable_path).is_file():
                self.skipTest("Playwright Chromium is not installed")
            browser = await pw.chromium.launch()
            try:
                page = await browser.new_page(viewport={"width": 390, "height": 760}, reduced_motion="reduce")
                response = await page.goto(str(self.client.make_url("/monitor")), wait_until="domcontentloaded")
                self.assertEqual(response.status, 200)
                nav = page.get_by_role("navigation", name="ToolA", exact=True)
                for name, href in (("Main", "/"), ("Council", "/council"), ("Monitor", "/monitor"), ("Matrix", "/matrixtool")):
                    link = nav.get_by_role("link", name=name, exact=True)
                    await expect(link).to_have_attribute("href", href)
                    await expect(link).to_be_in_viewport(ratio=1)
                await expect(nav.locator('[aria-current="page"]')).to_have_text("Monitor")
                nav_box = await nav.bounding_box()
                bar = page.locator("#bar")
                self.assertEqual((nav_box["x"], nav_box["y"]), (0, 0))
                self.assertGreaterEqual((await bar.bounding_box())["y"], nav_box["height"])
                scroll = await bar.evaluate("el => { el.scrollTop = el.scrollHeight; return el.scrollTop; }")
                self.assertGreater(scroll, 0, "The narrow controls must actually scroll for this regression check")
                self.assertEqual(await nav.bounding_box(), nav_box)
            finally:
                await browser.close()


    async def test_activity_full_delta_idle_wake_and_cursor_reset(self):
        full = await self.get_monitor('activity')
        self.assertFalse(full['delta'])
        self.assertEqual({e['id'] for e in full['events']}, {f'event-{i}' for i in range(5)})
        self.assertEqual(full['sessions'][0]['session_id'], 's1')
        self.assertEqual(full['writes'][0]['file_path'], 'source.py')
        self.assertEqual(full['toolq_parts']['retrievals']['state'], 'fresh')
        self.assertIsNone(full['section_errors']['events'])
        cursor = {'boot_id': full['boot_id'], 'since_seq': full['snapshot_seq']}
        self.assertEqual((await self.get_monitor('activity', cursor))['events'], [])
        self.add_event('event-new')
        await self.monitor.run(self.monitor.store.last_attempt.pop, 'events')
        self.monitor.last_activity = asyncio.get_running_loop().time() - 61
        delta = await self.get_monitor('activity', cursor)
        self.assertTrue(delta['delta'])
        self.assertGreater(delta['snapshot_seq'], full['snapshot_seq'])
        self.assertEqual({e['id'] for e in delta['events']}, {'event-new'})
        for invalid in ({'boot_id': 'old'}, {'since_seq': 0}, {'since_seq': delta['snapshot_seq'] + 100}):
            reset = await self.get_monitor('activity', {**cursor, **invalid})
            self.assertEqual(reset['boot_id'], full['boot_id'])
            self.assertFalse(reset['delta'])
            self.assertTrue(reset['truncated'])
            self.assertEqual(len(reset['events']), 6)

    async def test_health_history_graph_and_event_details(self):
        health = await self.get_monitor('health')
        self.assertTrue(health['up'])
        self.assertEqual(health['boot_id'], (await self.get_monitor('activity'))['boot_id'])
        self.assertFalse(health['sources']['services']['ToolQ']['ok'])
        first = await self.get_monitor('history', {'tool': 'read_file_range', 'q': 'source.py', 'limit': 2})
        self.assertEqual(first['count'], 2)
        self.assertTrue(first['has_more'])
        second = await self.get_monitor('history', {'q': 'source.py', 'limit': 2,
            'before': first['next_before'], 'before_aid': first['next_before_aid']})
        self.assertEqual(second['count'], 2)
        self.assertTrue({e['id'] for e in first['events']}.isdisjoint(e['id'] for e in second['events']))
        many = await self.get_monitor('evdetail', [('id', 'event-0'), ('id', 'rich-only')])
        self.assertEqual(many['details']['event-0']['tool'], 'read_file_range')
        self.assertEqual(many['details']['rich-only']['status'], 'ok')
        self.assertEqual((await self.get_monitor('events/event-0'))['payload']['session_id'], 's1')
        self.assertEqual((await self.get_monitor('events/missing', status=404))['error'], 'not_found')
        graph = await self.get_monitor('graph', {'repo': 'toolq'})
        self.assertEqual((graph['repo'], graph['nodes'][0]['id']), ('toolq', 'F:source.py'))

    async def test_allowlist_and_request_boundaries(self):
        for method, path, headers, status in [
            ('GET', '/api/monitor/filemeta', {}, 404),
            ('GET', '/api/filemeta', {}, 404),
            ('GET', '/api/monitor/events/event-0?extra=1', {}, 400),
            ('GET', '/api/monitor/evdetail?' + '&'.join(['id=e'] * 201), {}, 400),
            ('GET', '/api/monitor/activity?extra=1', {}, 400),
            ('GET', '/api/monitor/activity?boot_id=a&boot_id=b', {}, 400),
            ('GET', '/api/monitor/graph?refresh=1', {}, 400),
            ('GET', '/api/monitor/activity', {'Origin': 'https://example.invalid'}, 403),
            ('GET', '/api/monitor/activity', {'Host': 'example.invalid'}, 403),
            ('GET', '/api/monitor/activity', {'Sec-Fetch-Site': 'same-site'}, 403),
            ('POST', '/api/monitor/activity', {}, 405),
            ('HEAD', '/api/monitor/activity', {}, 405),
        ]:
            with self.subTest(method=method, path=path, headers=headers):
                res = await self.client.request(method, path, headers=headers)
                self.assertEqual(res.status, status)

    async def test_assets_share_the_page_gate(self):
        # WHY (S2): /assets/toola/* only checked the filename allowlist, so the same
        # browser that got 403 on /monitor and /api/monitor/* could still fetch every
        # asset with a hostile Host, Origin and Sec-Fetch-Site.
        for name in ('theme.css', 'matrix.js', 'd3.min.js'):
            for headers in ({'Host': 'example.invalid'},
                            {'Origin': 'https://example.invalid'},
                            {'Sec-Fetch-Site': 'cross-site'}):
                with self.subTest(asset=name, headers=headers):
                    res = await self.client.get('/assets/toola/' + name, headers=headers)
                    self.assertEqual(res.status, 403)
            ok = await self.client.get('/assets/toola/' + name)
            self.assertEqual(ok.status, 200)
            await ok.read()
        unknown = await self.client.get('/assets/toola/main.html',
                                        headers={'Host': 'example.invalid'})
        self.assertEqual(unknown.status, 403)

    async def test_host_port_must_be_the_bound_port(self):
        # WHY (S6): _HOST accepted any port, so Host=localhost:9999 passed the gate.
        # The bound port comes from the accepted socket, which is why this test can
        # assert a mismatch without pinning 8083 (TestClient ports are ephemeral).
        bound = URL(str(self.client.make_url('/'))).port
        for host, status in (('localhost:' + str(bound + 1), 403),
                             ('127.0.0.1:' + str(bound), 200),
                             ('localhost', 200),
                             ('example.invalid:' + str(bound), 403)):
            with self.subTest(host=host):
                res = await self.client.get('/api/monitor/health', headers={'Host': host})
                self.assertEqual(res.status, status)

    async def test_store_fault_is_not_reported_as_a_dead_bridge(self):
        # WHY (S7): a SQLite/serialisation fault inside dispatch was returned as
        # MONITOR_BRIDGE_UNAVAILABLE, naming a bridge that no longer runs.
        with patch.object(self.monitor.store, 'dispatch', side_effect=RuntimeError('boom')):
            res = await self.client.get('/api/monitor/graph')
            self.assertEqual(res.status, 500)
            self.assertEqual((await res.json())['error_name'], 'MONITOR_INTERNAL_ERROR')
        with patch.object(self.monitor.store, 'dispatch', return_value=(500, b'{}')):
            res = await self.client.get('/api/monitor/graph')
            self.assertEqual(res.status, 500)
            self.assertEqual((await res.json())['error_name'], 'MONITOR_INTERNAL_ERROR')

    async def test_production_router_does_not_expose_filemeta(self):
        from aiohttp.test_utils import make_mocked_request
        from orchestrator_server import create_app

        router = create_app().router
        health = await router.resolve(make_mocked_request('GET', '/api/monitor/health'))
        self.assertIs(health.handler, web_views._monitor_read)
        self.assertNotIn('/api/filemeta', {resource.canonical for resource in router.resources()})
        for method in ('GET', 'HEAD', 'POST'):
            with self.subTest(method=method):
                match = await router.resolve(make_mocked_request(method, '/api/filemeta'))
                self.assertIsNotNone(match.http_exception)
                self.assertIn(match.http_exception.status, (404, 405))
    async def test_db_errors_keep_last_good_data_and_pages_available(self):
        store = self.monitor.store
        for path, table in ((self.audit, 'durable_audit_outbox'), (self.toolq, 'distill_runs')):
            with closing(sqlite3.connect(path)) as conn, conn:
                conn.execute(f'DROP TABLE {table}')

        def refresh():
            for name in ('events', 'toolq'):
                store.last_attempt.pop(name, None)
            store.tick(('events', 'toolq'))

        await self.monitor.run(refresh)
        data = await self.get_monitor('activity')
        self.assertEqual(len(data['events']), 5)
        self.assertEqual(data['section_state']['events'], 'stale')
        self.assertIsNotNone(data['section_errors']['events'])
        self.assertIsNone(data['section_errors']['sessions'])
        self.assertEqual(data['toolq_parts']['retrievals']['state'], 'fresh')
        self.assertEqual(data['toolq_parts']['distill_runs']['state'], 'stale')
        self.assertEqual((await self.get_monitor('events/event-0', status=500))['error_name'],
                         'MONITOR_INTERNAL_ERROR')
        page = await self.client.get('/monitor')
        self.assertEqual(page.status, 200)
        self.assertNotIn(':8099', await page.text())

        with closing(sqlite3.connect(self.audit)) as conn, conn:
            conn.execute('CREATE TABLE durable_audit_outbox (audit_id TEXT, payload_json TEXT, state TEXT, created_at REAL)')
        with closing(sqlite3.connect(self.toolq)) as conn, conn:
            conn.execute('CREATE TABLE distill_runs (id, started_at, finished_at, raw_seen, points_created, mode)')
        self.add_event('event-recovered')
        await self.monitor.run(refresh)
        recovered = await self.get_monitor('activity')
        self.assertEqual({event['id'] for event in recovered['events']}, {'event-recovered'})
        self.assertEqual(recovered['section_state']['events'], 'fresh')
        self.assertIsNone(recovered['section_errors']['events'])
        self.assertEqual(recovered['toolq_parts']['distill_runs']['state'], 'fresh')
        self.assertIsNone(recovered['section_errors']['toolq'])
        await self.get_monitor('events/event-recovered')

    async def test_response_limit_is_inclusive(self):
        self.assertEqual(web_views._MAX_BYTES, 16 * 1024 * 1024)
        body = b'{}' + b' ' * (web_views._MAX_BYTES - 2)
        with patch.object(self.monitor.store, 'dispatch', return_value=(200, body)):
            res = await self.client.get('/api/monitor/graph')
            self.assertEqual(res.status, 200)
            self.assertEqual(len(await res.read()), web_views._MAX_BYTES)
        with patch.object(self.monitor.store, 'dispatch', return_value=(200, body + b' ')):
            err = await self.get_monitor('graph', status=502)
            self.assertEqual(err['error_name'], 'MONITOR_BRIDGE_UNAVAILABLE')

    async def test_health_can_read_last_snapshot_past_graph_timeout(self):
        entered, release, finished = threading.Event(), threading.Event(), threading.Event()
        dispatch = self.monitor.store.dispatch

        def block_graph(path, params):
            if path == 'graph':
                entered.set()
                try:
                    if not release.wait(10):
                        raise TimeoutError('fixture did not release graph query')
                finally:
                    finished.set()
            return dispatch(path, params)

        with patch.object(self.monitor.store, 'dispatch', side_effect=block_graph):
            graph = asyncio.create_task(self.client.get('/api/monitor/graph', params={'repo': 'toola'}))
            try:
                self.assertTrue(await asyncio.to_thread(entered.wait, 2))
                baseline = await asyncio.wait_for(self.get_monitor('health'), timeout=1)
                self.assertIsNotNone(baseline['last_build_at'])
                response = await asyncio.wait_for(asyncio.shield(graph), timeout=6)
                self.assertEqual(response.status, 504)
                self.assertEqual((await response.json())['error_name'], 'MONITOR_BRIDGE_UNAVAILABLE')
                self.assertFalse(finished.is_set())
                health = await asyncio.wait_for(self.get_monitor('health'), timeout=1)
                self.assertFalse(finished.is_set())
                self.assertEqual(health['boot_id'], baseline['boot_id'])
                self.assertEqual(health['last_build_at'], baseline['last_build_at'])
                self.assertEqual(health['sources'], baseline['sources'])
                self.assertGreaterEqual(health['snapshot_age_s'], baseline['snapshot_age_s'])
            finally:
                release.set()
                await asyncio.gather(graph, return_exceptions=True)

    async def test_readonly_and_cleanup_waits_for_inflight_poll(self):
        monitor = self.monitor
        store = monitor.store
        entered, release = threading.Event(), threading.Event()
        order = []
        real_close = store.close

        def tick():
            entered.set()
            if not release.wait(5):
                raise TimeoutError('fixture did not release poller')
            order.append('poll')

        def close():
            real_close()
            order.append('close')

        def check_readonly():
            conn = store.connections[self.audit]
            conn.execute('PRAGMA query_only=OFF')
            with self.assertRaisesRegex(sqlite3.OperationalError, 'readonly'):
                conn.execute('CREATE TABLE must_not_write (id)')
            missing = self.audit.with_name('missing.db')
            corrupt = self.audit.with_name('corrupt.db')
            corrupt.write_text('invalid sqlite contents', encoding='utf-8')
            for path in (missing, corrupt):
                self.assertIn('_error', store.rows(path, 'SELECT * FROM durable_audit_outbox')[0])
                self.assertNotIn(path, store.connections)
            self.assertFalse(missing.exists())

        await monitor.run(check_readonly)
        with patch.object(store, 'tick', tick), patch.object(store, 'close', close):
            monitor.wake.set()
            try:
                self.assertTrue(await asyncio.to_thread(entered.wait, 3))
                closing_client = asyncio.create_task(self.client.close())
                await asyncio.sleep(0)
                self.assertFalse(closing_client.done())
                self.assertTrue(store.connections)
            finally:
                release.set()
            await closing_client
        self.assertTrue(monitor.poller.done())
        self.assertEqual(order, ['poll', 'close'])
        self.assertFalse(store.connections)

    async def test_matrix_keeps_inventory_and_recovers_with_a_fresh_cursor(self):
        try:
            from playwright.async_api import async_playwright, expect
        except ImportError:
            self.skipTest("Playwright is not installed")

        def event(name, tool, *, status="ok", epoch=995, state="applied"):
            return {"id": name, "tool": tool, "status": status, "epoch": epoch, "state": state}

        def snapshot(boot, seq, events, *, delta=False, age=1, service_age=1):
            return {
                "boot_id": boot, "snapshot_seq": seq, "delta": delta,
                "section_state": {"events": "fresh", "services": "fresh"},
                "section_built_at": {"events": 1000},
                "section_age_s": {"events": age, "services": service_age},
                "events": events,
                "services": [
                    {"name": "ToolA", "up": True},
                    {"name": "ToolQ", "up": True, "status": "degraded", "semantic_ready": False,
                     "reason": "semantic_ready false; status degraded", "missing_fields": ["rows"]},
                ],
            }

        samples = [
            None,
            snapshot("a", 10, [
                event("read", "read_file_range"), event("write", "write_fix", status="error"),
                event("pending", "write_fix", status="error", state="pending"),
                event("old", "write_fix", status="error", epoch=700),
                event("future", "write_fix", status="error", epoch=1010),
                event("unmapped", "outside-inventory"),
            ]),
            snapshot("a", 11, [event("write-2", "write_fix"), event("search", "search_code", status="error")], delta=True),
            snapshot("a", 12, [event("stale-write", "write_fix", status="error")], delta=True, age=25),
            snapshot("b", 1, [event("read-b", "read_file_range")], service_age=30),
            None,
            snapshot("c", 1, []),
        ]
        seen_queries = []
        async with async_playwright() as pw:
            if not Path(pw.chromium.executable_path).is_file():
                self.skipTest("Playwright Chromium is not installed")
            browser = await pw.chromium.launch()
            page = await browser.new_page(viewport={"width": 1440, "height": 900}, reduced_motion="reduce")
            if not hasattr(page, "clock"):
                await browser.close()
                self.skipTest("Playwright virtual clock is not available")
            await page.clock.install()
            requests = []
            errors = []
            page.on("request", lambda request: requests.append(URL(request.url)))
            page.on("pageerror", lambda error: errors.append(str(error)))

            async def activity(route):
                seen_queries.append(dict(URL(route.request.url).query))
                sample = samples[min(len(seen_queries), len(samples)) - 1]
                if sample is None:
                    await route.fulfill(status=502, json={})
                else:
                    await route.fulfill(json=sample)

            await page.route("**/api/monitor/activity*", activity)
            try:
                response = await page.goto(str(self.client.make_url('/matrixtool')))
                self.assertEqual(response.status, 200)
                await expect(page.locator('#panel-mindmap')).to_be_visible()
                await expect(page.locator('#panel-sunburst')).to_be_hidden()
                await expect(page.locator('#mm-inventory')).to_have_text('260 tool · 35 nhóm · 5 cụm')
                source = page.locator('#mm-source')
                total = page.locator('#mm-total')
                sample = page.locator('#mm-sample')
                t2 = page.locator('button.mm-tier[data-mm-tier="T2"]')
                t2_count = t2.locator('[data-mm-count]')
                group = page.locator('#mm-groups-T2 [data-mm-group="T2|write"]')
                group_count = group.locator('[data-mm-count]')

                async def geometry():
                    return await page.locator('#mm-root,.mm-tier').evaluate_all('''nodes => nodes.map(n => {
                        const r = n.getBoundingClientRect(), canvas = document.querySelector('#mm-canvas').getBoundingClientRect();
                        return [r.x - canvas.x, r.y - canvas.y, r.width, r.height];
                    })''')

                async def assert_anchors_stable(expected):
                    actual = await geometry()
                    self.assertEqual(len(actual), len(expected))
                    for index, (rect, baseline) in enumerate(zip(actual, expected)):
                        for component, value, reference in zip(("x", "y", "width", "height"), rect, baseline):
                            self.assertAlmostEqual(value, reference, delta=0.02, msg=f"anchor {index} {component}")

                await expect(source).to_have_attribute('data-state', 'offline')
                await expect(total).to_have_text('— lượt · — lỗi')
                closed_geometry = await geometry()
                self.assertEqual(len(closed_geometry), 5)
                await t2.click()
                write_cluster = page.locator('#mm-groups-T2 .mm-cluster:has(button[data-mm-group="T2|write"])')
                await write_cluster.locator('.mm-cluster-btn').click()
                await expect(group).to_have_attribute('aria-expanded', 'false')
                await group.click()
                await expect(group).to_have_attribute('aria-expanded', 'true')
                tier_color = await t2.evaluate('(node) => getComputedStyle(node).borderLeftColor')
                await expect(group).to_have_css('border-left-color', tier_color)
                await expect(group).not_to_have_css('background-color', 'rgba(0, 0, 0, 0)')
                # Telemetry and tool detail must not move the open tree.
                open_geometry = await geometry()
                tool = group.locator('..').locator('[data-mm-tool="write_fix"]')
                await tool.click()
                detail = page.locator('#mm-detail')
                await expect(detail.locator('.toola-matrix-meta')).to_be_visible()
                await expect(detail.locator('.toola-matrix-meta')).to_have_attribute('open', '')
                await detail.press('Escape')
                await expect(tool).to_be_focused()
                await assert_anchors_stable(open_geometry)
                # Wait for subsequent responses using the changed telemetry, not elapsed real time.
                await page.clock.run_for(5000)
                await expect(source).to_have_attribute('data-state', 'live')
                await expect(total).to_have_text('3 lượt · 1 lỗi')
                await expect(t2_count).to_have_text('1 lượt · 1 lỗi')
                await expect(t2_count).to_have_attribute('data-alert', 'true')
                await expect(sample).to_contain_text('1 lượt không phân tier')
                await expect(page.locator('#mm-service-q')).to_have_text('Suy giảm')
                await page.locator('.mm-service summary').nth(1).press('Enter')
                reason = page.locator('#mm-service-q-reason')
                await expect(reason).to_be_visible()
                await expect(reason).to_contain_text('tìm kiếm ngữ nghĩa chưa sẵn sàng')
                await expect(reason).to_contain_text('Monitor: semantic_ready false; status degraded')
                await expect(reason).to_contain_text('Thiếu trường: rows.')
                await tool.focus()
                await assert_anchors_stable(open_geometry)
                await page.clock.run_for(5000)
                await expect(total).to_have_text('5 lượt · 2 lỗi')
                await expect(t2_count).to_have_text('2 lượt · 1 lỗi')
                await expect(group_count).to_contain_text('2 lượt · 1 lỗi')
                await expect(tool).to_be_focused()
                await assert_anchors_stable(open_geometry)
                await page.clock.run_for(5000)
                await expect(source).to_have_attribute('data-state', 'stale')
                await expect(sample).to_contain_text('Giữ mẫu lúc')
                await expect(total).to_have_text('5 lượt · 2 lỗi')
                await expect(t2_count).not_to_have_attribute('data-alert', 'true')
                await page.clock.run_for(5000)
                await expect(total).to_have_text('1 lượt · 0 lỗi')
                await expect(t2_count).to_have_text('0 lượt · 0 lỗi')
                await expect(page.locator('#mm-service-q')).to_have_text('Dữ liệu cũ / thiếu')
                await page.clock.run_for(5000)
                await expect(source).to_have_attribute('data-state', 'offline')
                await expect(total).to_have_text('1 lượt · 0 lỗi')
                await expect(page.locator('#mm-service-q')).to_have_text('Chưa rõ')
                await expect(reason).to_contain_text('chưa thể kết luận dịch vụ đang chạy hay đã dừng')
                await expect(reason).not_to_contain_text('semantic_ready')
                await page.clock.run_for(5000)
                await expect(source).to_have_attribute('data-state', 'live')
                await expect(total).to_have_text('0 lượt · 0 lỗi')
                await t2.click()
                await expect(page.locator('#mm-groups-T2')).to_be_hidden()
                await assert_anchors_stable(closed_geometry)
                await page.locator('[data-tab="sunburst"]').click()
                await expect(page.locator('#sunburst-container svg')).to_be_visible()
                await expect(page.locator('#sb-detail .toola-matrix-meta > summary').first).to_contain_text('46')
                await page.locator('[data-tab="inventory"]').click()
                await expect(page.locator('#panel-inventory')).to_be_visible()
                self.assertEqual(seen_queries, [
                    {}, {}, {"since_seq": "10", "boot_id": "a"}, {"since_seq": "11", "boot_id": "a"},
                    {"since_seq": "12", "boot_id": "a"}, {"since_seq": "1", "boot_id": "b"}, {},
                ])
                self.assertFalse([url for url in requests if url.port == 8099])
                self.assertEqual(errors, [])
            finally:
                await browser.close()

    async def test_matrix_horizontal_edges_zoom_and_independent_branches(self):
        """The geometry contract that the Forest folds deliberately do NOT alter.

        The (a)/(b) choice Anh delegated is resolved here as (a): keep node-swell-
        in-place and the pill control strip OUT of this map. Two learnings from
        myforest.id.vn (swell on hover, a pill control strip) were catalogued but
        not folded, because both would move the geometry and contrast this test
        locks (font >= 14, ordered boxes, fit at scrollLeft 0, the wire endpoint
        census). The campaign brief was wires + unbounded drag; changing locked
        assertions to chase visual parity with a demo would trade a verified
        invariant for an unverified resemblance, so (b) is declined. A future
        fold of those two learnings needs its own named amendment to this
        contract, not a silent edit of its assertions.
        """
        try:
            from playwright.async_api import async_playwright, expect
        except ImportError:
            self.skipTest('Playwright is not installed')
        async with async_playwright() as pw:
            if not Path(pw.chromium.executable_path).is_file():
                self.skipTest('Playwright Chromium is not installed')
            browser = await pw.chromium.launch()
            try:
                for width, motion in ((1440, 'no-preference'), (390, 'reduce'), (320, 'reduce')):
                    with self.subTest(width=width, motion=motion):
                        page = await browser.new_page(viewport={'width': width, 'height': 800}, reduced_motion=motion)
                        errors = []
                        page.on('pageerror', lambda error: errors.append(str(error)))
                        await page.route('**/api/monitor/activity*', lambda route: route.fulfill(status=502, json={}))
                        await page.goto(str(self.client.make_url('/matrixtool')))
                        stage = page.locator('#mm-stage')
                        nodes = page.locator('#mm-root,.mm-tier')
                        await expect(nodes).to_have_count(5)
                        boxes = await nodes.evaluate_all('''nodes => nodes.map(n => {
                            const r = n.getBoundingClientRect();
                            return {left:r.left, right:r.right, top:r.top, bottom:r.bottom,
                                    font:parseFloat(getComputedStyle(n.querySelector('strong')).fontSize)};
                        })''')
                        for i, box in enumerate(boxes):
                            self.assertGreaterEqual(box['font'], 14)
                            self.assertTrue(0 <= box['left'] < box['right'] <= width)
                            self.assertTrue(0 <= box['top'] < box['bottom'] < 800)
                            if i:
                                self.assertGreater(box['left'], boxes[0]['right'])
                            if i > 1:
                                self.assertGreater(box['top'], boxes[i - 1]['bottom'])
                        # AMENDED, not dropped (Anh 2026-11-23: "sao cai nay khong de o
                        # giua ma lech o tren vay"). This line captured the root's
                        # offsetTop so that 681 could assert it never changed while tiers,
                        # clusters and groups opened and closed. That constancy WAS the
                        # pin: .mm-root carried margin-top: 107px on a canvas set to
                        # align-items: flex-start, so the root stood still however much
                        # the branch column grew. Measured across six states its centre sat
                        # between 84px BELOW and 1020px ABOVE the centre of everything
                        # else, and matched in exactly one -- the number was tuned to that
                        # one state, and the lock below was written to protect the tuning.
                        # The canvas now centres the whole flex line, so the invariant
                        # worth locking is the thing the pin was faking: the root is
                        # centred against the branch column in EVERY state.
                        async def assert_root_centred(tag):
                            delta = await page.evaluate('''() => {
                                const a = document.querySelector('.mm-root').getBoundingClientRect();
                                const b = document.querySelector('.mm-branches').getBoundingClientRect();
                                return (a.top + a.bottom) / 2 - (b.top + b.bottom) / 2;
                            }''')
                            self.assertLess(abs(delta), 1.5,
                                msg='root not centred against branches (' + tag + '): delta ' + str(delta))
                        await assert_root_centred('closed')
                        t1 = page.locator('.mm-tier[data-mm-tier="T1"]')
                        t2 = page.locator('.mm-tier[data-mm-tier="T2"]')
                        await t2.press('Enter')
                        t2_groups = page.locator('#mm-groups-T2')
                        await expect(t2_groups.locator('.mm-group > button')).to_have_count(11)
                        # The cluster wraps the groups now, so it has to open first.
                        write_cluster = t2_groups.locator('.mm-cluster:has(button[data-mm-group="T2|write"])')
                        await expect(write_cluster.locator('.mm-cluster-btn')).to_have_count(1)
                        await write_cluster.locator('.mm-cluster-btn').press('Enter')
                        await expect(write_cluster.locator('.mm-cluster-body')).to_be_visible()
                        write = t2_groups.locator('button[data-mm-group="T2|write"]')
                        await write.press('Enter')
                        # AMENDED (was '.mm-tools > button'): that selector is green only
                        # while this rail stays PLAIN. The bucketed rail puts chips inside a
                        # .mm-bucket body, so '> button' would start matching bucket NODES and
                        # the wire-count assert below would fail for a reason unrelated to wires.
                        # [data-mm-tool] is chip-specific in BOTH shapes, and the wire selector
                        # is scoped to this group so another open rail cannot inflate the count.
                        write_tools = write.locator('..').locator('[data-mm-tool]')
                        tool = write_tools.filter(has_text='write_fix').first
                        await expect(tool).to_be_visible()
                        await t1.press('Enter')
                        await expect(t1).to_have_attribute('aria-expanded', 'true')
                        await expect(t2).to_have_attribute('aria-expanded', 'true')
                        await expect(write).to_have_attribute('aria-expanded', 'true')
                        await assert_root_centred('branches open')
                        await t1.press('Enter')
                        await expect(t1).to_have_attribute('aria-expanded', 'false')
                        await expect(write).to_have_attribute('aria-expanded', 'true')
                        await assert_root_centred('T1 closed again')

                        for _ in range(2):
                            await page.get_by_role('button', name='Thu nhỏ').click()
                        await expect(page.locator('#mm-zoom-level')).to_have_text('80%')
                        await expect(page.locator('.mm-tier-wire')).to_have_count(4)
                        await expect(page.locator('.mm-cluster-wire')).to_have_count(5)
                        await expect(page.locator('.mm-group-wire')).to_have_count(3)
                        await expect(page.locator('.mm-tool-wire[data-mm-group="T2|write"]')).to_have_count(await write_tools.count())
                        geometry = await stage.evaluate('''stage => {
                            const svg = stage.querySelector('svg.mm-wires');
                            const ctm = svg.getScreenCTM();
                            const edges = [];
                            const check = (parent, child, path) => {
                                const p = parent.getBoundingClientRect(), c = child.getBoundingClientRect();
                                const total = path.getTotalLength();
                                const a = new DOMPoint(path.getPointAtLength(0).x, path.getPointAtLength(0).y).matrixTransform(ctm);
                                const b = new DOMPoint(path.getPointAtLength(total).x, path.getPointAtLength(total).y).matrixTransform(ctm);
                                edges.push([c.left - p.right, Math.hypot(a.x - p.right, a.y - (p.top + p.bottom) / 2),
                                            Math.hypot(b.x - c.left, b.y - (c.top + c.bottom) / 2)]);
                            };
                            const root = stage.querySelector('#mm-root');
                            for (const tier of stage.querySelectorAll('.mm-tier')) {
                                check(root, tier, svg.querySelector('.mm-tier-wire[data-mm-tier="' + tier.dataset.mmTier + '"]'));
                            }
                            const t2 = stage.querySelector('.mm-tier[data-mm-tier="T2"]');
                            for (const [i, cluster] of [...stage.querySelectorAll('#mm-groups-T2 .mm-cluster')].entries()) {
                                check(t2, cluster.querySelector('.mm-cluster-btn'), svg.querySelectorAll('.mm-cluster-wire')[i]);
                            }
                            // Group wires exist only inside an open cluster body, so pair them with the
                            // chip that owns that body rather than with every group in the panel.
                            const body = stage.querySelector('#mm-groups-T2 .mm-cluster-body:not([hidden])');
                            const clusterChip = body.closest('.mm-cluster').querySelector('.mm-cluster-btn');
                            for (const [i, group] of [...body.querySelectorAll('.mm-group > button')].entries()) {
                                check(clusterChip, group, svg.querySelectorAll('.mm-group-wire')[i]);
                            }
                            const write = stage.querySelector('button[data-mm-group="T2|write"]');
                            const rail = write.nextElementSibling;
                            // Filter to chips: a bucket head is not a direct child of the
                            // rail any more, and pairing by raw child index would zip
                            // every wire after the first head onto the card below it.
                            const chips = [...rail.querySelectorAll('[data-mm-tool]')]
                                .filter(n => n.getBoundingClientRect().width >= 1);
                            // THE GAP ASSERT BELOW ASSUMES parent-RIGHT -> child-LEFT,
                            // which is true of the arc router and FALSE of the prefix
                            // trunk (a trunk starts at the head's LEFT edge, so its
                            // start point sits left of the parent's right edge and
                            // 'gap > 0' would measure a negative). T2|write is under the
                            // bucket minimum and so stays a plain rail -- but that is a
                            // fact about the current tool inventory, not a law, and one
                            // tool refiled into this group would silently empty 'chips'
                            // and delete the leaf half of this contract while the test
                            // stayed green. So the premise is MEASURED here: a head
                            // appearing in this rail fails loudly and sends the geometry
                            // to the leaf-rail test, which owns the trunk shape.
                            const heads = rail.querySelectorAll('.mm-bucket');
                            if (heads.length || !chips.length) throw new Error(
                                'T2|write grew bucket heads (or lost its chips), so this '
                                + 'parent-right->child-left geometry no longer applies -- '
                                + 'move it to the leaf-rail test, which owns the trunk shape');
                            for (const [i, tool] of chips.entries()) {
                                check(write, tool, svg.querySelectorAll('.mm-tool-wire')[i]);
                            }
                            return {edges, pageFits:document.documentElement.scrollWidth === innerWidth};
                        }''')
                        for gap, start_error, end_error in geometry['edges']:
                            self.assertGreater(gap, 0)
                            self.assertLess(start_error, 1)
                            self.assertLess(end_error, 1)
                        self.assertTrue(geometry['pageFits'])
                        if width <= 390:
                            await stage.evaluate('(node) => node.scrollTo(0,0)')
                            await stage.press('ArrowRight')
                            self.assertGreater(await stage.evaluate('(node) => node.scrollLeft'), 0)
                        await page.get_by_role('button', name='Vừa khung').click()
                        self.assertEqual(await stage.evaluate('(node) => node.scrollLeft'), 0)
                        bounds = await stage.evaluate('''node => ({
                            width: node.scrollWidth - node.clientWidth,
                            height: node.scrollHeight - node.clientHeight,
                            top: node.scrollTop,
                        })''')
                        self.assertLessEqual(bounds['width'], 1)
                        self.assertLessEqual(bounds['height'], 1)
                        self.assertEqual(bounds['top'], 0)
                        await write.press('Enter')
                        await expect(write).to_have_attribute('aria-expanded', 'false')
                        await expect(page.locator('.mm-tool-wire')).to_have_count(0)
                        await expect(t2).to_have_attribute('aria-expanded', 'true')
                        # A closed cluster collapses its groups, so their wires go too.
                        await write_cluster.locator('.mm-cluster-btn').press('Enter')
                        await expect(write).to_have_attribute('aria-expanded', 'false')
                        await expect(page.locator('.mm-group-wire')).to_have_count(0)
                        await expect(page.locator('.mm-cluster-wire')).to_have_count(5)
                        await expect(t2).to_have_attribute('aria-expanded', 'true')
                        self.assertEqual(errors, [])
                        await page.close()
            finally:
                await browser.close()

    async def test_matrix_lookup_reveals_source_path_without_moving_anchors(self):
        try:
            from playwright.async_api import async_playwright, expect
        except ImportError:
            self.skipTest('Playwright is not installed')
        async with async_playwright() as pw:
            if not Path(pw.chromium.executable_path).is_file():
                self.skipTest('Playwright Chromium is not installed')
            browser = await pw.chromium.launch()
            try:
                for width in (1440, 390, 320):
                    with self.subTest(width=width):
                        page = await browser.new_page(viewport={'width': width, 'height': 800}, reduced_motion='reduce')
                        await page.route('**/api/monitor/activity*', lambda route: route.fulfill(status=502, json={}))
                        await page.goto(str(self.client.make_url('/matrixtool')))
                        search = page.get_by_role('combobox', name='Tìm tool hoặc chức năng')
                        hits = page.get_by_role('listbox', name='Kết quả tìm công cụ')
                        map_detail = page.locator('#mm-detail')
                        nodes = page.locator('#mm-root,.mm-tier')
                        measurements = '''nodes => nodes.map(n => {
                            const r = n.getBoundingClientRect(), canvas = document.querySelector('#mm-canvas').getBoundingClientRect();
                            return [r.left - canvas.left, r.top - canvas.top, r.width, r.height];
                        })'''
                        await search.fill('write_fix')
                        await expect(hits.get_by_role('option').first).to_contain_text('write_fix')
                        await search.press('Enter')
                        await expect(map_detail).to_be_focused()
                        initial = await nodes.evaluate_all(measurements)
                        self.assertEqual(len(initial), 5)

                        async def assert_anchors_stable():
                            actual = await nodes.evaluate_all(measurements)
                            self.assertEqual(len(actual), len(initial))
                            for index, (rect, baseline) in enumerate(zip(actual, initial)):
                                for component, value, reference in zip(('x', 'y', 'width', 'height'), rect, baseline):
                                    # Focus can scroll the page, leaving subpixel rounding in the measurements.
                                    self.assertAlmostEqual(value, reference, delta=0.02, msg=f'anchor {index} {component}')
                        await expect(page.locator('#mm-groups-T2')).to_be_visible()
                        await expect(page.locator('button[data-mm-group="T2|write"]')).to_have_attribute('aria-expanded', 'true')
                        # Five levels now: the cluster tier sits between tier and group.
                        self.assertEqual(await map_detail.locator('.mm-path-node strong').all_text_contents(),
                                         ['ToolA', 'Ghi & kiểm thử', 'Code & dữ liệu', 'Ghi dữ liệu', 'write_fix'])
                        await expect(page.locator('.mm-tier.is-focus')).to_have_attribute('data-mm-tier', 'T2')
                        await expect(page.locator('.mm-tier.is-muted')).to_have_count(3)
                        await expect(page.locator('.mm-group > button.is-focus')).to_have_attribute('data-mm-group', 'T2|write')
                        await expect(page.locator('[data-mm-tool="write_fix"]')).to_have_attribute('aria-current', 'true')
                        await expect(map_detail.locator('.toola-matrix-meta')).to_be_visible()
                        await expect(page.locator('.mm-trace')).to_have_count(0)
                        await assert_anchors_stable()
                        self.assertTrue(await page.evaluate('document.documentElement.scrollWidth === innerWidth'))
                        await map_detail.press('Escape')
                        await expect(page.locator('[data-mm-tool="write_fix"]')).to_be_focused()
                        await search.fill('read_file_range')
                        await expect(hits.get_by_role('option').first).to_contain_text('read_file_range')
                        await search.press('Escape')
                        await expect(search).to_have_attribute('aria-expanded', 'false')
                        await search.fill('does-not-exist-925205')
                        await expect(page.locator('#mm-search-status')).to_have_text('Không tìm thấy')
                        await expect(hits).to_be_hidden()
                        await expect(page.locator('.mm-tier.is-muted')).to_have_count(0)
                        await page.get_by_role('button', name='Xóa', exact=True).click()
                        await expect(search).to_have_value('')
                        await expect(search).to_be_focused()
                        await assert_anchors_stable()
                        await page.close()
            finally:
                await browser.close()

    async def test_matrix_wires_light_on_real_mouse_hover(self):
        try:
            from playwright.async_api import async_playwright, expect
        except ImportError:
            self.skipTest('Playwright is not installed')
        async with async_playwright() as pw:
            if not Path(pw.chromium.executable_path).is_file():
                self.skipTest('Playwright Chromium is not installed')
            browser = await pw.chromium.launch()
            try:
                page = await browser.new_page(viewport={'width': 1440, 'height': 900})
                errors = []
                page.on('pageerror', lambda error: errors.append(str(error)))
                await page.route('**/api/monitor/activity*', lambda route: route.fulfill(status=502, json={}))
                await page.goto(str(self.client.make_url('/matrixtool')))
                canvas = page.locator('#mm-canvas')
                # The wire layer stays click-through: it is stacked over the nodes.
                self.assertEqual(await page.locator('#mm-branch-wires')
                                 .evaluate('node => getComputedStyle(node).pointerEvents'), 'none')
                await page.locator('.mm-tier[data-mm-tier="T1"]').click()
                await page.locator('.mm-cluster[data-mm-cluster="code"] .mm-cluster-btn').click()
                await page.locator('.mm-cluster[data-mm-cluster="code"] .mm-group > button').first.click()
                await expect(canvas.locator('.mm-tool-wire')).not_to_have_count(0)
                # One invisible twin per visible wire, or that wire is unhittable at
                # 1.65px and the hover contract is a lie. .mm-trace is excluded because
                # it is a transient clone, not a wire the pointer can reach.
                census = await canvas.evaluate('''node => {
                    const sel = '#mm-branch-wires path:not(.mm-wire-hit):not(.mm-trace)';
                    return { wires: node.querySelectorAll(sel).length,
                             hits: node.querySelectorAll('.mm-wire-hit').length,
                             hitPointer: getComputedStyle(node.querySelector('.mm-wire-hit')).pointerEvents };
                }''')
                self.assertEqual(census['hits'], census['wires'])
                self.assertEqual(census['hitPointer'], 'stroke')
                # Drive the REAL mouse to a point on the curve, not the bbox centre:
                # a bezier's bounding box is mostly empty space.
                probe = await canvas.evaluate('''node => {
                    const wire = node.querySelector('.mm-tool-wire');
                    const hit = [...node.querySelectorAll('.mm-wire-hit')]
                        .find(h => h.getAttribute('d') === wire.getAttribute('d'));
                    const ctm = node.querySelector('#mm-branch-wires').getScreenCTM();
                    const p = hit.getPointAtLength(hit.getTotalLength() / 2);
                    const s = new DOMPoint(p.x, p.y).matrixTransform(ctm);
                    const at = document.elementFromPoint(s.x, s.y);
                    return { x: s.x, y: s.y, onHit: !!(at && at.classList.contains('mm-wire-hit')) };
                }''')
                self.assertTrue(probe['onHit'], 'the leaf wire midpoint is not reachable by pointer')
                await page.mouse.move(probe['x'], probe['y'])
                await expect(canvas.locator('.mm-wire-lit')).to_have_count(1)
                await expect(canvas.locator('[data-mm-lit]')).to_have_count(2)
                self.assertTrue(await canvas.evaluate('node => node.classList.contains("mm-wire-hover")'))
                # The lit wire thickens and glows; the rest dim by losing colour, never
                # by losing opacity (the demo's own rule, Atlas :1479).
                state = await canvas.evaluate('''node => {
                    const svg = node.querySelector('#mm-branch-wires');
                    const lit = svg.querySelector('.mm-wire-lit');
                    const other = [...svg.querySelectorAll('path:not(.mm-wire-hit):not(.mm-wire-lit):not(.mm-trace)')]
                        .find(p => !p.classList.contains('is-muted') && getComputedStyle(p).filter !== 'none');
                    return { litWidth: getComputedStyle(lit).strokeWidth,
                             litFilter: getComputedStyle(lit).filter,
                             otherFilter: other ? getComputedStyle(other).filter : null,
                             otherOpacity: other ? getComputedStyle(other).strokeOpacity : null };
                }''')
                self.assertEqual(state['litWidth'], '2.6px')
                self.assertIn('drop-shadow', state['litFilter'])
                self.assertIn('grayscale', state['otherFilter'])
                self.assertEqual(state['otherOpacity'], '0.65')
                # Hover paints a tooltip but writes no panel -- v5 1.8 excludes hover from
                # the #mm-detail writers (matrix.js:807).
                await expect(page.locator('#sb-leaf-tip')).to_be_visible()
                self.assertIn('\u2192', await page.locator('#sb-leaf-tip').text_content())
                await expect(page.locator('#mm-detail')).to_be_hidden()
                # The root is the far endpoint of every tier wire but carries no
                # --mm-tone of its own. Before the pin, an unresolved var() made
                # border-color collapse to currentColor and background to transparent,
                # so the root visibly lost its box on any tier-wire hover. The root is
                # still meant to light up -- so the invariant is not "unchanged" but
                # "did not collapse", stated without a literal colour because
                # Playwright Chromium reports prefers-color-scheme: light and this
                # page now really renders in that palette.
                root_style = '''node => {
                    const s = getComputedStyle(node);
                    return { border: s.borderColor, background: s.backgroundColor,
                             color: s.color, tone: s.getPropertyValue('--mm-tone').trim() };
                }'''
                await page.locator('.mm-tier[data-mm-tier="T2"]').click()
                root_before = await page.locator('#mm-root').evaluate(root_style)
                self.assertNotEqual(root_before['tone'], '', 'the root must carry a resolved --mm-tone')
                tier_probe = await canvas.evaluate('''node => {
                    const wire = node.querySelector('.mm-tier-wire[data-mm-tier="T2"]');
                    const hit = [...node.querySelectorAll('.mm-wire-hit')]
                        .find(h => h.getAttribute('d') === wire.getAttribute('d'));
                    const ctm = node.querySelector('#mm-branch-wires').getScreenCTM();
                    const p = hit.getPointAtLength(hit.getTotalLength() / 2);
                    const s = new DOMPoint(p.x, p.y).matrixTransform(ctm);
                    return { x: s.x, y: s.y };
                }''')
                await page.mouse.move(tier_probe['x'], tier_probe['y'])
                await expect(canvas.locator('[data-mm-lit]')).to_have_count(2)
                root_after = await page.locator('#mm-root').evaluate(root_style)
                self.assertNotEqual(root_after['background'], 'rgba(0, 0, 0, 0)')
                self.assertNotEqual(root_after['border'], root_after['color'])
                # The pin has to be load-bearing, not dead code: hovering the tier
                # wire must actually repaint the root.
                self.assertNotEqual(root_after['border'], root_before['border'])
                # Leaving clears everything: a stranded lit node accumulates until the
                # whole map glows.
                await page.mouse.move(4, 4)
                await expect(canvas.locator('.mm-wire-lit')).to_have_count(0)
                await expect(canvas.locator('[data-mm-lit]')).to_have_count(0)
                await expect(page.locator('#sb-leaf-tip')).to_be_hidden()
                self.assertEqual(errors, [])
                await page.close()
            finally:
                await browser.close()

    async def test_matrix_focus_dims_the_map_around_a_hover_and_a_selection(self):
        """Lock the node dim: what is not focused goes grey AND soft, and the road stays.

        Anh 2026-11-23: 'khi chon hoac hover 1 node thi cac node con lai toi xuong va
        blur nhe. xem lai html atom de dung co che'. The mechanism is Atlas :1470-1531 --
        one class on the container arms the dim, every element of the focused subtree
        carries its own marker, and a wire is kept when both its ends are kept.

        WHY THIS LOCK IS NEEDED ON TOP OF THE WIRE SUITE, which was already green: the
        dim that shipped before this one filtered .mm-wires path and touched no card at
        all, so every census in this file looked at wires, reported zero anomalies, and
        the map of nodes never changed. That is the same failure this campaign has hit
        a dozen times -- a meter that matches nothing reports the zero a clean page
        reports -- so the dim census here is positive-controlled: a stray filter is
        injected on one card and the count has to rise.

        What is pinned, in the order of the defects each clause catches:
          1. CARDS dim, not wires alone (the defect that made the complaint true).
          2. Every dim carries a blur, so 'blur nhe' is part of the contract.
          3. No card loses opacity. Atlas :1479 pins it and this page is the case it
             guards: a translucent card lets the wire behind it read straight through.
          4. A SELECTION keeps the whole road to the chip. The first focusMapSubtree
             walked parentElement, and chain_probe found seven DIV/SECTION ancestors of
             a chip and not one button -- the accordion is sibling plus aria-controls
             (ctl_probe: all 20 pairs came back rel='next-sibling'). A selected tool
             therefore kept 1 card of 22 and dimmed itself, the opposite of a focus.
          5. The dim survives a hover that leaves, because the selection owns it.
          6. The dim PAINTS without MOVING: same DOM, only the arming class differs, and
             every card rect must agree exactly, because the anchor lock above holds
             0.02px and the wire geometry reads getScreenCTM off these same boxes.
        """
        try:
            from playwright.async_api import async_playwright
        except ImportError:
            self.skipTest('Playwright is not installed')
        CENSUS = '''() => {
          const canvas = document.getElementById('mm-canvas');
          const SEL = '.mm-root, .mm-tier, .mm-cluster-btn, .mm-group > button, .mm-bucket-btn, .mm-tools > button, .mm-bucket-body > button';
          const cards = [...canvas.querySelectorAll(SEL)]
            .filter(n => n.getBoundingClientRect().width >= 1);
          const st = { armed: canvas.classList.contains('mm-has-focus')
            || canvas.classList.contains('mm-wire-hover'), cards: cards.length,
            dimmed: 0, kept: 0, blurred: 0, faded: 0, wDim: 0, wKeep: 0, wFaded: 0,
            keptNames: [] };
          for (const c of cards) {
            const s = getComputedStyle(c);
            const f = s.filter;
            if (f !== 'none' && f.includes('grayscale')) {
              st.dimmed++;
              if (f.includes('blur(') && !f.includes('blur(0')) st.blurred++;
              if (parseFloat(s.opacity) < 0.999) st.faded++;
            } else {
              st.kept++;
              st.keptNames.push(c.dataset.mmTool
                || (c.dataset.mmGroup ? 'grp:' + c.dataset.mmGroup : null)
                || (c.dataset.mmTier ? 'tier:' + c.dataset.mmTier : null)
                || c.id || c.className);
            }
          }
          for (const p of canvas.querySelectorAll('#mm-branch-wires path:not(.mm-wire-hit):not(.mm-trace)')) {
            const s = getComputedStyle(p);
            if (s.filter !== 'none' && s.filter.includes('grayscale')) st.wDim++;
            else st.wKeep++;
            if (!p.classList.contains('mm-wire-lit') && parseFloat(s.strokeOpacity) < 0.6) st.wFaded++;
          }
          return st;
        }'''
        RECTS = """() => {
          const canvas = document.getElementById('mm-canvas');
          const SEL = '.mm-root, .mm-tier, .mm-cluster-btn, .mm-group > button, .mm-bucket-btn, .mm-tools > button, .mm-bucket-body > button';
          const out = {};
          for (const c of canvas.querySelectorAll(SEL)) {
            const r = c.getBoundingClientRect();
            const k = c.dataset.mmTool || c.dataset.mmGroup || c.dataset.mmTier || c.id || c.className;
            out[String(k)] = [Math.round(r.x * 100) / 100, Math.round(r.y * 100) / 100,
                              Math.round(r.width * 100) / 100, Math.round(r.height * 100) / 100];
          }
          return out;
        }"""
        CENTER = """() => {
          const r = document.querySelector('.mm-tier[data-mm-tier="T1"]').getBoundingClientRect();
          return { x: r.x + r.width / 2, y: r.y + r.height / 2 };
        }"""
        INJECT = """() => { document.querySelector('.mm-root').style.filter = 'grayscale(100%) brightness(.5)'; }"""
        CLEAN = """() => { document.querySelector('.mm-root').style.filter = ''; }"""
        DISARM = """() => { const c = document.getElementById('mm-canvas');
          c.dataset.armed = String(c.classList.contains('mm-has-focus'));
          c.classList.remove('mm-has-focus'); }"""
        REARM = """() => { const c = document.getElementById('mm-canvas');
          if (c.dataset.armed === 'true') c.classList.add('mm-has-focus'); }"""
        async with async_playwright() as pw:
            if not Path(pw.chromium.executable_path).is_file():
                self.skipTest('Playwright Chromium is not installed')
            browser = await pw.chromium.launch()
            try:
                page = await browser.new_page(viewport={'width': 1440, 'height': 900},
                                              reduced_motion='reduce')
                errors = []
                page.on('pageerror', lambda error: errors.append(str(error)))
                await page.route('**/api/monitor/activity*',
                                 lambda route: route.fulfill(status=502, json={}))
                await page.goto(str(self.client.make_url('/matrixtool')))
                await page.wait_for_selector('#mm-canvas')
                await page.wait_for_timeout(900)

                # 1-3 on a real hover, and the control that proves the census can rise.
                probe = await page.evaluate(CENTER)
                await page.mouse.move(probe['x'], probe['y'])
                await page.wait_for_timeout(250)
                hov = await page.evaluate(CENSUS)
                self.assertTrue(hov['armed'], 'hovering a node did not arm the focus dim')
                self.assertGreater(hov['dimmed'], 0,
                    'no card was dimmed: the dim is wire-only again, the defect Anh photographed')
                self.assertEqual(hov['blurred'], hov['dimmed'],
                    'a card dimmed without a blur, so "toi xuong va blur nhe" is half delivered')
                self.assertEqual(hov['faded'], 0,
                    'a dimmed card lost opacity, which lets a wire read through it (Atlas :1479)')
                await page.evaluate(INJECT)
                ctrl = await page.evaluate(CENSUS)
                await page.evaluate(CLEAN)
                self.assertGreater(ctrl['dimmed'], hov['dimmed'],
                    'the dim census cannot rise, so its zero would mean nothing')

                await page.mouse.move(4, 4)
                await page.wait_for_timeout(250)
                idle = await page.evaluate(CENSUS)
                self.assertFalse(idle['armed'], 'the dim stranded after the pointer left')
                self.assertEqual(idle['dimmed'], 0)

                # 4: the road. Open T2 -> cluster 'code' -> group 'T2|write', select a chip.
                for target in ('.mm-tier[data-mm-tier="T2"]',
                               '#mm-groups-T2 .mm-cluster[data-mm-cluster="code"] .mm-cluster-btn',
                               'button[data-mm-group="T2|write"]'):
                    await page.locator(target).press('Enter')
                    await page.wait_for_timeout(250)
                await page.locator('[data-mm-tool="write_fix"]').click()
                await page.wait_for_timeout(400)
                await page.mouse.move(4, 4)
                await page.wait_for_timeout(250)
                sel = await page.evaluate(CENSUS)
                self.assertTrue(sel['armed'], 'a selection did not arm the focus dim')
                self.assertGreaterEqual(sel['kept'], 5,
                    'the selection kept ' + str(sel['kept']) + ' card(s) of ' + str(sel['cards'])
                    + ': the road to the chip was walked by parentElement and broke at the first div')
                for want in ('mm-root', 'tier:T2', 'grp:T2|write', 'write_fix'):
                    self.assertIn(want, sel['keptNames'],
                        'the selected tool\'s road is missing ' + want + ' -- kept ' + str(sel['keptNames']))
                self.assertGreaterEqual(sel['wKeep'], 4,
                    'the four runs of the lit road (root->tier->cluster->group->chip) are not kept')
                self.assertEqual(sel['blurred'], sel['dimmed'])
                self.assertEqual(sel['faded'], 0)
                self.assertEqual(sel['wFaded'], 0,
                    'a muted wire was dimmed by alpha instead of colour')

                # 6: paint-only, measured against the same DOM with the class toggled.
                on = await page.evaluate(RECTS)
                await page.evaluate(DISARM)
                off = await page.evaluate(RECTS)
                await page.evaluate(REARM)
                again = await page.evaluate(RECTS)
                self.assertEqual(off, on,
                    'the focus dim moved a card rect; the anchor lock holds 0.02px and the '
                    'wire geometry reads getScreenCTM off these boxes')
                self.assertEqual(again, on, 'restoring the arming class did not restore the layout')

                # 5: a hover that comes and goes must not steal the dim the panel claims.
                probe2 = await page.evaluate(CENTER)
                await page.mouse.move(probe2['x'], probe2['y'])
                await page.wait_for_timeout(250)
                await page.mouse.move(4, 4)
                await page.wait_for_timeout(300)
                after = await page.evaluate(CENSUS)
                self.assertTrue(after['armed'], 'leaving a hover stripped the dim a selection owns')
                self.assertEqual(sorted(after['keptNames']), sorted(sel['keptNames']),
                    'the focused road changed when a hover came and went')

                await page.locator('#mm-detail [data-mm-close]').first.click()
                await page.wait_for_timeout(300)
                closed = await page.evaluate(CENSUS)
                self.assertFalse(closed['armed'], 'the dim outlived the detail panel')
                self.assertEqual(closed['dimmed'], 0)
                self.assertEqual(errors, [])
                await page.close()
            finally:
                await browser.close()

    async def test_matrix_palette_contrast_meets_wcag_in_both_palettes(self):
        """Lock the measured WCAG floor for the dark and the light palette.

        Every ratio comes from painting the computed colour through a 1x1 canvas,
        so it is a pixel the browser composites rather than a hex looked up in
        theme.css. Two rows measure one border against two neighbours on purpose:
        WCAG 1.4.11 counts a control's boundary against the page AND against the
        control's own fill, and the first rung picked for --ta-control-border
        (#565d70) cleared the page at 3.03:1 and failed the fill at 2.70:1. The
        light semantic tones are the other half of the lock: the dark ones sat at
        2.76:1 as .mm-tier-label text on white, and the tooltip hard-coded a navy
        card so its text fell to 1.07:1, until the light block restated both.
        """
        try:
            from playwright.async_api import async_playwright
        except ImportError:
            self.skipTest('Playwright is not installed')
        T1_WIRE = '.mm-tier-wire' + '[data-mm-tier="T1"]'
        T3_WIRE = '.mm-tier-wire' + '[data-mm-tier="T3"]'
        RESET = '[data-mm-view-action="reset"]'
        # [label, fg selector, fg property, bg selector, minimum ratio]
        PAIRS = [
            # The chip selector is button[data-mm-tool], and inside a bucketed rail it
            # sits two levels below .mm-tools, so '.mm-tools > button' would measure a
            # plain rail only and pass while a bucketed one drifted.
            ['leaf tool label', 'button[data-mm-tool]', 'color', '.mm-tools button[data-mm-tool]', 4.5],
            # A bucket head is ink this campaign introduced, so it cannot sit outside
            # the lock. It is a CARD now rather than a caption painted on the stage:
            # .mm-bucket-btn sets var(--mm-tone) text on color-mix(--mm-tone 14%, --bg2),
            # so the ground is its OWN fill and #mm-stage would measure the wrong thing.
            # 0.74rem at weight 700 is 11.84px, which is NOT large scale -- that would
            # need 18.66px bold -- so the 4.5:1 text floor applies rather than the 3.0:1
            # floor a wire stroke is held to. OPEN's first group is T1|analysis, which
            # does grow heads, so the selector resolves instead of tripping the
            # missing-element assert. The meta line is var(--fg2) on the same fill and
            # is the weaker of the two, so it is locked separately: a head readable in
            # one theme only is exactly the drift this table exists to catch.
            ['bucket head label', '.mm-bucket-btn strong', 'color', '.mm-bucket-btn', 4.5],
            ['bucket head meta', '.mm-bucket-btn small', 'color', '.mm-bucket-btn', 4.5],
            ['tier label', '.mm-tier-label', 'color', '.mm-tier', 4.5],
            ['root label', '#mm-root strong', 'color', '#mm-root', 4.5],
            ['view hint', '.mm-view-hint', 'color', RESET, 4.5],
            ['reset label', RESET, 'color', RESET, 4.5],
            ['control border vs page', RESET, 'borderTopColor', 'body', 3.0],
            ['control border vs fill', RESET, 'borderTopColor', RESET, 3.0],
            ['source state', '#mm-source', 'color', 'body', 4.5],
            ['search status', '#mm-search-status', 'color', 'body', 4.5],
            ['T1 wire', T1_WIRE, 'stroke', '#mm-stage', 3.0],
            ['T3 wire', T3_WIRE, 'stroke', '#mm-stage', 3.0],
            ['tooltip label', '#sb-leaf-tip', 'color', '#sb-leaf-tip', 4.5],
            ['tooltip border', '#sb-leaf-tip', 'borderTopColor', 'body', 3.0],
        ]
        OPEN = """() => {
            const q = String.fromCharCode(34);
            const stage = document.getElementById('mm-stage');
            stage.querySelector('.mm-tier' + '[data-mm-tier=' + q + 'T1' + q + ']').click();
            const cl = document.querySelector('.mm-cluster' + '[data-mm-cluster=' + q + 'code' + q + ']');
            cl.querySelector('.mm-cluster-btn').click();
            cl.querySelector('.mm-group > button').click();
            return true;
        }"""
        HOVER = """() => {
            const leaf = document.querySelector('button[data-mm-tool]');
            if (!leaf) return false;
            leaf.dispatchEvent(new PointerEvent('pointerover',
                {bubbles: true, clientX: 300, clientY: 300}));
            return true;
        }"""
        MEASURE = """(pairs) => {
            const cv = document.createElement('canvas');
            cv.width = cv.height = 1;
            const cx = cv.getContext('2d');
            const painted = (top, bottom) => {
                cx.clearRect(0, 0, 1, 1);
                cx.fillStyle = bottom;
                cx.fillRect(0, 0, 1, 1);
                cx.fillStyle = top;
                cx.fillRect(0, 0, 1, 1);
                const d = cx.getImageData(0, 0, 1, 1).data;
                return [d[0], d[1], d[2]];
            };
            const lum = c => {
                const f = v => {
                    v /= 255;
                    return v <= 0.03928 ? v / 12.92 : Math.pow((v + 0.055) / 1.055, 2.4);
                };
                return 0.2126 * f(c[0]) + 0.7152 * f(c[1]) + 0.0722 * f(c[2]);
            };
            const kebab = p => p.replace(/[A-Z]/g, m => '-' + m.toLowerCase());
            const out = [];
            for (const row of pairs) {
                const label = row[0], fgSel = row[1], fgProp = row[2], bgSel = row[3], need = row[4];
                const fgEl = document.querySelector(fgSel);
                const bgEl = document.querySelector(bgSel);
                if (!fgEl || !bgEl) {
                    out.push({label: label, missing: !fgEl ? fgSel : bgSel});
                    continue;
                }
                const fgCol = getComputedStyle(fgEl).getPropertyValue(kebab(fgProp)).trim();
                const bgCol = getComputedStyle(bgEl).backgroundColor.trim();
                // A canvas keeps its previous fillStyle when it cannot parse one, so
                // an unsupported colour would measure the neighbour instead of
                // failing -- ask CSS directly whether the string is live.
                const rejected = !CSS.supports('color', fgCol) || !CSS.supports('color', bgCol);
                // Over black and over white must agree, or the background is
                // translucent and the ratio depends on what sits under it.
                const overWhite = painted(bgCol, 'rgb(255,255,255)');
                const translucent = overWhite.join(',') !== painted(bgCol, 'rgb(0,0,0)').join(',');
                const a = lum(painted(fgCol, bgCol));
                const b = lum(overWhite);
                out.push({label: label, need: need, fgCol: fgCol, bgCol: bgCol,
                          rejected: rejected, translucent: translucent,
                          ratio: Math.round(((Math.max(a, b) + 0.05) / (Math.min(a, b) + 0.05)) * 100) / 100});
            }
            return out;
        }"""
        async with async_playwright() as pw:
            if not Path(pw.chromium.executable_path).is_file():
                self.skipTest('Playwright Chromium is not installed')
            browser = await pw.chromium.launch()
            try:
                for scheme in ('dark', 'light'):
                    with self.subTest(scheme=scheme):
                        page = await browser.new_page(viewport={'width': 1440, 'height': 900},
                                                      color_scheme=scheme)
                        errors = []
                        page.on('pageerror', lambda error: errors.append(str(error)))
                        await page.route('**/api/monitor/activity*',
                                         lambda route: route.fulfill(status=502, json={}))
                        await page.goto(str(self.client.make_url('/matrixtool')))
                        await page.wait_for_selector('#mm-canvas')
                        await page.evaluate(OPEN)
                        await page.wait_for_timeout(400)
                        self.assertTrue(await page.evaluate(HOVER), 'no leaf to hover')
                        await page.wait_for_timeout(200)
                        rows = await page.evaluate(MEASURE, PAIRS)
                        for row in rows:
                            self.assertIn('ratio', row,
                                          'measured element vanished: ' + str(row))
                            self.assertFalse(row['rejected'],
                                             '%s: an unsupported colour %s / %s'
                                             % (row['label'], row['fgCol'], row['bgCol']))
                            self.assertFalse(row['translucent'],
                                             '%s: translucent background %s, ratio inconclusive'
                                             % (row['label'], row['bgCol']))
                            self.assertGreaterEqual(row['ratio'], row['need'],
                                                    '%s is %.2f:1 in %s, needs %.1f:1 (%s on %s)'
                                                    % (row['label'], row['ratio'], scheme,
                                                       row['need'], row['fgCol'], row['bgCol']))
                        self.assertEqual(errors, [])
                        await page.close()
            finally:
                await browser.close()

    async def test_matrix_reveal_pans_smoothly_and_yields_to_the_user(self):
        try:
            from playwright.async_api import async_playwright, expect
        except ImportError:
            self.skipTest('Playwright is not installed')
        QUERY = 'architecture_audit_compact'
        async def settled_positions(page):
            seen = []
            for _ in range(12):
                await page.wait_for_timeout(60)
                seen.append(await page.evaluate(
                    "() => Math.round(document.getElementById('mm-stage').scrollLeft)"))
            return seen
        async with async_playwright() as pw:
            if not Path(pw.chromium.executable_path).is_file():
                self.skipTest('Playwright Chromium is not installed')
            browser = await pw.chromium.launch()
            try:
                for motion in ('no-preference', 'reduce'):
                    with self.subTest(motion=motion):
                        # 900x700 is the narrowest desktop frame that still overflows the
                        # stage once T1/code/the first group are open; at 1440 the leaf is
                        # already inside the viewport and revealMapNode correctly does
                        # nothing, which would prove nothing here.
                        page = await browser.new_page(viewport={'width': 900, 'height': 700},
                                                      reduced_motion=motion)
                        errors = []
                        page.on('pageerror', lambda error: errors.append(str(error)))
                        await page.route('**/api/monitor/activity*', lambda route: route.fulfill(status=502, json={}))
                        await page.goto(str(self.client.make_url('/matrixtool')))
                        search = page.get_by_role('combobox', name='Tìm tool hoặc chức năng')
                        await search.fill(QUERY)
                        await page.evaluate('() => document.getElementById("mm-stage").scrollTo(0, 0)')
                        await page.wait_for_timeout(200)
                        await search.press('Enter')
                        await expect(page.locator('#mm-detail')).to_be_focused()
                        samples = await settled_positions(page)
                        # The reveal has to have had somewhere to go, or every assertion
                        # below passes on an empty scroll.
                        self.assertGreater(samples[-1], 0, 'the lookup revealed no off-screen node')
                        distinct = len(set(samples))
                        if motion == 'no-preference':
                            # easeOutCubic over 650ms, sampled at 60ms: several intermediate
                            # positions. A jump would be exactly one change, 2 distinct values.
                            self.assertGreaterEqual(distinct, 4,
                                'smooth pan produced only ' + str(distinct) + ' positions: ' + str(samples))
                            # decelerating: the biggest step comes early, not late
                            steps = [b - a for a, b in zip(samples, samples[1:]) if b - a]
                            self.assertGreater(steps[0], steps[-1], 'pan did not ease out: ' + str(steps))
                        else:
                            # reduced motion is one instant step -- the geometry contract
                            # reads scrollLeft on the line after pressing a key.
                            self.assertLessEqual(distinct, 2,
                                'reduced motion still tweened: ' + str(samples))
                        self.assertEqual(errors, [])
                        await page.close()
                # A pan still in flight must not outlive an act of the user's. This is the
                # regression the cancel helper exists for: without it the tween keeps
                # writing mapStage.scrollTo() under "Vừa khung" and the map snaps back.
                page = await browser.new_page(viewport={'width': 900, 'height': 700},
                                              reduced_motion='no-preference')
                errors = []
                page.on('pageerror', lambda error: errors.append(str(error)))
                await page.route('**/api/monitor/activity*', lambda route: route.fulfill(status=502, json={}))
                await page.goto(str(self.client.make_url('/matrixtool')))
                search = page.get_by_role('combobox', name='Tìm tool hoặc chức năng')
                await search.fill(QUERY)
                await page.evaluate('() => document.getElementById("mm-stage").scrollTo(0, 0)')
                await page.wait_for_timeout(200)
                await search.press('Enter')
                await expect(page.locator('#mm-detail')).to_be_focused()
                await page.wait_for_timeout(120)  # mid-flight, before the 650ms lands
                await page.get_by_role('button', name='Vừa khung').click()
                await page.wait_for_timeout(1200)  # longer than the whole tween
                self.assertEqual(await page.evaluate(
                    "() => Math.round(document.getElementById('mm-stage').scrollLeft)"), 0,
                    'an in-flight reveal pan overrode the reset control')
                self.assertEqual(errors, [])
                await page.close()
            finally:
                await browser.close()

    async def test_matrix_wheel_zoom_anchors_the_point_under_the_cursor(self):
        """The wheel is the multiplicative, cursor-anchored lever, and the locked zoom
        test only presses the additive buttons, so zoomAt had no coverage at all.


        The anchor is the CURSOR, not the node edge. Measured: with the pointer on the
        node centre the stage scrolls by exactly offset*(f-1) to 0.1px, and the node
        centre holds its client position -- which is the user-visible promise. A probe
        of mine once compared the node's LEFT EDGE against a centre anchor and reported
        an 18px slip that was entirely its own arithmetic error, so this test states the
        invariant rather than a magic number.
        """
        try:
            from playwright.async_api import async_playwright, expect
        except ImportError:
            self.skipTest('Playwright is not installed')
        async with async_playwright() as pw:
            if not Path(pw.chromium.executable_path).is_file():
                self.skipTest('Playwright Chromium is not installed')
            browser = await pw.chromium.launch()
            try:
                page = await browser.new_page(viewport={'width': 1440, 'height': 900})
                errors = []
                page.on('pageerror', lambda error: errors.append(str(error)))
                await page.route('**/api/monitor/activity*', lambda route: route.fulfill(status=502, json={}))
                await page.goto(str(self.client.make_url('/matrixtool')))
                await page.wait_for_selector('#mm-canvas')
                await page.wait_for_timeout(400)
                stage = page.locator('#mm-stage')
                tier = page.locator('.mm-tier[data-mm-tier="T2"]')
                await tier.press('Enter')
                await expect(page.locator('#mm-groups-T2')).to_be_visible()
                # Overflow has to come from the BRANCH, not the zoom lever. The canvas is
                # sized by its content, so a collapsed map is narrower than the 1440
                # stage and four zoom rungs still leave room 0 (measured); with the code
                # cluster and one group open the content sits at the container width,
                # where the next rung spills.
                await page.locator('#mm-groups-T2 .mm-cluster[data-mm-cluster="code"] .mm-cluster-btn').press('Enter')
                await page.locator('button[data-mm-group="T2|write"]').press('Enter')
                # The pan is now two channels (bounded scroll + unbounded translate),
                # so a bare scrollTo(0,0) leaves mmPan behind and the node can sit
                # outside the stage - which makes the wheel miss it entirely. Go
                # home through the reset button, the one action that zeroes BOTH.
                await page.click("[data-mm-view-action=reset]")
                ROOM = '() => {const s=document.getElementById("mm-stage");return s.scrollWidth-s.clientWidth}'
                for _ in range(20):
                    if await stage.evaluate(ROOM) > 80:
                        break
                    await page.click('[data-mm-view-action="zoom-in"]')
                    await page.wait_for_timeout(120)
                self.assertGreater(await stage.evaluate(ROOM), 80,
                    'the wheel test needs real scroll room to prove anchoring')
                await page.wait_for_timeout(200)
                SNAP = """() => {
                    const stage = document.getElementById('mm-stage');
                    const node = stage.querySelector('.mm-tier[data-mm-tier="T2"]');
                    const s = stage.getBoundingClientRect(), r = node.getBoundingClientRect();
                    return {zoom: parseFloat(document.getElementById('mm-canvas').style.zoom || '1'),
                            sl: stage.scrollLeft, top: stage.scrollTop,
                            pageY: Math.round(window.scrollY),
                            cx: (r.left + r.right) / 2 - s.left,
                            cy: (r.top + r.bottom) / 2 - s.top,
                            w: r.width, h: r.height};
                }"""
                before = await page.evaluate(SNAP)
                stage_box = await stage.bounding_box()
                tier_box = await tier.bounding_box()
                cur_x = tier_box['x'] + tier_box['width'] / 2
                cur_y = tier_box['y'] + tier_box['height'] / 2
                # zoomAt anchors on clientX - stageRect.left, so the expected offset is
                # measured the same way, from the stage box, not from the node box.
                ax = cur_x - stage_box['x']
                ay = cur_y - stage_box['y']
                self.assertAlmostEqual(ax, before['cx'], delta=2.0,
                    msg='the cursor is not over the node centre the snapshot describes')
                await page.mouse.move(cur_x, cur_y)
                await page.mouse.wheel(0, -100)
                await page.wait_for_timeout(400)
                after = await page.evaluate(SNAP)
                f = after['zoom'] / before['zoom']
                self.assertGreater(f, 1.02, 'the wheel did not zoom in')
                self.assertLess(f, 1.6, 'one wheel notch zoomed implausibly far')
                # zoomAt: scrollTo((s0 + ax) * f - ax) => the delta is ax * (f - 1).
                self.assertAlmostEqual(after['sl'] - before['sl'], ax * (f - 1), delta=1.5,
                    msg='horizontal anchor: ' + str(after['sl'] - before['sl']) + ' vs ' + str(ax * (f - 1)))
                self.assertAlmostEqual(after['top'] - before['top'], ay * (f - 1), delta=1.5,
                    msg='vertical anchor: ' + str(after['top'] - before['top']) + ' vs ' + str(ay * (f - 1)))
                # The user-facing promise: the content under the cursor stays put, so
                # the node centre keeps its client position while its width scales by f.
                self.assertAlmostEqual(after['cx'], before['cx'], delta=2.0,
                    msg='the point under the cursor slid ' + str(after['cx'] - before['cx']) + 'px')
                self.assertAlmostEqual(after['cy'], before['cy'], delta=2.0,
                    msg='the point under the cursor slid vertically ' + str(after['cy'] - before['cy']) + 'px')
                self.assertAlmostEqual(after['w'], before['w'] * f, delta=1.0)
                self.assertEqual(after['pageY'], before['pageY'],
                    'the wheel scrolled the document instead of zooming the map')
                self.assertEqual(errors, [])
                await page.close()
            finally:
                await browser.close()

    async def test_matrix_drag_pans_and_a_sub_slop_move_stays_a_click(self):
        """Pointer panning has no coverage either: the locked pan test drives the
        keyboard and the search reveal, never a mouse drag.


        A drag that STARTS ON A NODE must pan without toggling it -- pointerdown rides
        on the stage, so a node is a valid drag target, and the capture-phase squash is
        the only thing between the pan and activating the node underneath. The reverse
        matters just as much: under DRAG_SLOP the gesture must stay a click, or every
        slightly shaky click on the map stops working.
        """
        try:
            from playwright.async_api import async_playwright, expect
        except ImportError:
            self.skipTest('Playwright is not installed')
        async with async_playwright() as pw:
            if not Path(pw.chromium.executable_path).is_file():
                self.skipTest('Playwright Chromium is not installed')
            browser = await pw.chromium.launch()
            try:
                # 900x700 for the same reason the pan test picks it: the narrowest
                # desktop frame that still overflows the stage with one branch open.
                page = await browser.new_page(viewport={'width': 900, 'height': 700})
                errors = []
                page.on('pageerror', lambda error: errors.append(str(error)))
                await page.route('**/api/monitor/activity*', lambda route: route.fulfill(status=502, json={}))
                await page.goto(str(self.client.make_url('/matrixtool')))
                await page.wait_for_selector('#mm-canvas')
                stage = page.locator('#mm-stage')
                group = page.locator('button[data-mm-group="T2|write"]')
                SL = '() => document.getElementById("mm-stage").scrollLeft'
                SCALE = """() => {
                    const s = document.getElementById('mm-stage');
                    return s.getBoundingClientRect().width / s.offsetWidth || 1;
                }"""
                await page.locator('.mm-tier[data-mm-tier="T2"]').press('Enter')
                await expect(page.locator('#mm-groups-T2')).to_be_visible()
                await page.locator('#mm-groups-T2 .mm-cluster[data-mm-cluster="code"] .mm-cluster-btn').press('Enter')
                await group.press('Enter')
                await expect(group).to_have_attribute('aria-expanded', 'true')
                await stage.evaluate('(node) => node.scrollTo(0, 0)')
                # Zoom until the stage really has room instead of assuming one rung
                # does. The rail went one-column, which narrows the canvas, and the
                # single zoom-in that used to guarantee 229px now guarantees 113px --
                # less than the 120px this drag travels, so the pan would reach the
                # scroll wall mid-gesture and the delta assertion would be measuring
                # the clamp instead of the pan. Whatever rung the loop stops on, room
                # > 170 is what keeps scrollLeft the channel under test, and SCALE is
                # read after it so 120/scale tracks the live zoom.
                room = 0
                for _ in range(8):
                    await page.click('[data-mm-view-action="zoom-in"]')
                    await page.wait_for_timeout(250)
                    await group.scroll_into_view_if_needed()
                    room = await stage.evaluate(
                        '() => {const s=document.getElementById("mm-stage");return s.scrollWidth-s.clientWidth-s.scrollLeft}')
                    if room > 170:
                        break
                self.assertGreater(room, 170,
                    'no scroll room left for a drag to pan into after 8 zoom steps: ' + str(room));
                scale = await stage.evaluate(SCALE)
                start = await stage.evaluate(SL)
                box = await group.bounding_box()
                cx, cy = box['x'] + box['width'] / 2, box['y'] + box['height'] / 2
                # Drag the mouse left by 120px: content follows the cursor, so the
                # stage scrolls right by 120/scale (onPanMove divides by the zoom scale).
                await page.mouse.move(cx, cy)
                await page.mouse.down()
                await page.mouse.move(cx - 30, cy)
                await page.mouse.move(cx - 75, cy)
                panning_mid = await stage.evaluate(
                    '() => document.getElementById("mm-stage").classList.contains("is-panning")')
                expanded_mid = await group.get_attribute('aria-expanded')
                await page.mouse.move(cx - 120, cy)
                await page.mouse.up()
                await page.wait_for_timeout(300)
                self.assertTrue(panning_mid, 'the panning class never appeared, so no drag ran at all')
                self.assertEqual(expanded_mid, 'true', 'the drag collapsed the group mid-move')
                self.assertEqual(await group.get_attribute('aria-expanded'), 'true',
                    'a drag that started on a node also activated it')
                self.assertAlmostEqual(await stage.evaluate(SL) - start, 120 / scale, delta=2.0,
                    msg='drag did not pan by the cursor delta divided by the zoom scale')
                self.assertFalse(await stage.evaluate(
                    '() => document.getElementById("mm-stage").classList.contains("is-panning")'),
                    'the panning cursor class outlived pointerup')
                # Under DRAG_SLOP the same gesture must stay a click. 400ms of settle so
                # the previous drag's 250ms squash window has provably expired.
                await page.wait_for_timeout(400)
                # Scroll and the panning class are sampled BEFORE pointerup. The click
                # that follows collapses the group, which shrinks the canvas and clamps
                # scrollLeft -- comparing across the toggle read 18 vs 236 and blamed
                # that clamp for a pan. Between down and the 2px move the layout is
                # untouched, so any difference there can only be a real pan.
                scroll_before = await stage.evaluate(SL)
                box2 = await group.bounding_box()
                cx2, cy2 = box2['x'] + box2['width'] / 2, box2['y'] + box2['height'] / 2
                await page.mouse.move(cx2, cy2)
                await page.mouse.down()
                await page.mouse.move(cx2 + 2, cy2)
                mid_scroll = await stage.evaluate(SL)
                mid_panning = await stage.evaluate(
                    '() => document.getElementById("mm-stage").classList.contains("is-panning")')
                await page.mouse.up()
                await page.wait_for_timeout(300)
                self.assertEqual(mid_scroll, scroll_before,
                    'a sub-slop move panned the stage')
                self.assertFalse(mid_panning,
                    'a sub-slop move crossed DRAG_SLOP and started a pan')
                self.assertEqual(await group.get_attribute('aria-expanded'), 'false',
                    'a sub-slop move swallowed the click')
                self.assertEqual(errors, [])
                await page.close()
            finally:
                await browser.close()
    async def test_matrix_drag_is_unbounded_past_the_scroll_wall(self):
        """At the fit zoom the stage has zero horizontal range (measured), so a
        scroll-only pan cannot move at all: every drag died on its first pixel
        after "Vua khung". The pan now has two channels - the native scroll for
        the part it can express, and a translate on the canvas for the overflow.

        The assertion is on CONTENT position, not on internal state: after a
        600px drag with hRange still 0 the canvas must have moved exactly 600
        client px, scrollLeft must still read 0, and the translate must be
        -600/zoom so a later zoom does not slide a panned map sideways. A wire
        is sampled too: the SVG is a child of the canvas, so its ink has to
        travel with the cards without any redraw.
        """
        try:
            from playwright.async_api import async_playwright, expect
        except ImportError:
            self.skipTest('Playwright is not installed')
        async with async_playwright() as pw:
            if not Path(pw.chromium.executable_path).is_file():
                self.skipTest('Playwright Chromium is not installed')
            browser = await pw.chromium.launch()
            try:
                page = await browser.new_page(viewport={'width': 1440, 'height': 900})
                errors = []
                page.on('pageerror', lambda error: errors.append(str(error)))
                await page.route('**/api/monitor/activity*', lambda route: route.fulfill(status=502, json={}))
                await page.goto(str(self.client.make_url('/matrixtool')))
                await page.wait_for_selector('#mm-canvas')
                stage = page.locator('#mm-stage')
                # Open real content first, then go home: the fit is the case where
                # the scroll wall is absolute (hRange 0) and only the overflow
                # channel can carry the gesture.
                await page.locator('.mm-tier[data-mm-tier="T2"]').press('Enter')
                await page.locator('#mm-groups-T2 .mm-cluster[data-mm-cluster="code"] .mm-cluster-btn').press('Enter')
                await page.locator('button[data-mm-group="T2|write"]').press('Enter')
                await page.wait_for_timeout(800)
                await page.click('[data-mm-view-action="reset"]')
                await page.wait_for_timeout(200)
                SNAP = """() => {
                    const st = document.getElementById('mm-stage');
                    const cv = document.getElementById('mm-canvas');
                    const wire = document.querySelector('.mm-cluster-wire, .mm-tool-wire');
                    return {left: cv.getBoundingClientRect().left,
                            wireLeft: wire ? wire.getBoundingClientRect().left : null,
                            sl: st.scrollLeft,
                            hRange: st.scrollWidth - st.clientWidth,
                            zoom: parseFloat(cv.style.zoom || '1'),
                            transform: cv.style.transform || ''};
                }"""
                before = await page.evaluate(SNAP)
                self.assertEqual(before['hRange'], 0,
                    'the fit zoom was supposed to leave zero horizontal range')
                self.assertIsNotNone(before['wireLeft'], 'no wire was drawn on the map')

                box = await stage.bounding_box()
                sx, sy = box['x'] + box['width'] / 2, box['y'] + box['height'] / 2
                await page.mouse.move(sx, sy)
                await page.mouse.down()
                for i in range(1, 7):
                    await page.mouse.move(sx - 600 * i / 6, sy)
                await page.mouse.up()
                await page.wait_for_timeout(60)
                after = await page.evaluate(SNAP)

                self.assertEqual(after['sl'], before['sl'],
                    'the overflow moved the native scroll, not the translate')
                self.assertAlmostEqual(before['left'] - after['left'], 600, delta=3.0,
                    msg='content moved ' + str(before['left'] - after['left']) + 'px, wanted 600')
                self.assertIn('translate(', after['transform'],
                    'no overflow translate was applied')
                expected = -600 / after['zoom']
                self.assertAlmostEqual(
                    float(after['transform'].split('(')[1].split('px')[0]),
                    expected, delta=2.0,
                    msg='translate is ' + after['transform'] + ', expected ' + str(expected))
                self.assertAlmostEqual(before['wireLeft'] - after['wireLeft'], 600, delta=3.0,
                    msg='the wire did not ride with the canvas')
                self.assertEqual(errors, [])
                await page.close()
            finally:
                await browser.close()
    async def test_matrix_leaf_panel_is_capped_and_keeps_the_map_visible(self):
        """The panel is in flow, so its height comes straight out of the page.


        Uncapped, a nine-edge leaf painted 1225px into an 844px viewport and the stage
        measured 0 visible pixels (panel_cap_probe) -- opening a tool deleted the map
        you were looking at. 42vh measured 378px of panel with 430 surviving stage pixels
        at 1440x900, and 354/333 at 390x844. The census also has two mutually exclusive
        branches that only differ by whether the leaf has edges, so each needs its own
        leaf or the wrong one can pass for the right reason.
        """
        try:
            from playwright.async_api import async_playwright, expect
        except ImportError:
            self.skipTest('Playwright is not installed')
        MEASURE = """() => {
            const d = document.getElementById('mm-detail');
            const dr = d.getBoundingClientRect();
            const sr = document.getElementById('mm-stage').getBoundingClientRect();
            const vis = r => Math.max(0, Math.min(r.bottom, window.innerHeight) - Math.max(r.top, 0));
            return {h: Math.round(dr.height), panelVisible: Math.round(vis(dr)),
                    stageVisible: Math.round(vis(sr)), scrollH: d.scrollHeight,
                    clientH: d.clientHeight, pageY: Math.round(window.scrollY)};
        }"""
        async with async_playwright() as pw:
            if not Path(pw.chromium.executable_path).is_file():
                self.skipTest('Playwright Chromium is not installed')
            browser = await pw.chromium.launch()
            try:
                for width, height, min_stage in ((1440, 900, 300), (390, 844, 200)):
                    with self.subTest(width=width):
                        page = await browser.new_page(viewport={'width': width, 'height': height})
                        errors = []
                        page.on('pageerror', lambda error: errors.append(str(error)))
                        await page.route('**/api/monitor/activity*', lambda route: route.fulfill(status=502, json={}))
                        await page.goto(str(self.client.make_url('/matrixtool')))
                        await page.wait_for_selector('#mm-canvas')
                        await page.wait_for_timeout(400)
                        detail = page.locator('#mm-detail')
                        cap = round(height * 0.42)
                        # Branch A: write_fix carries nine census edges, so it is the
                        # tall leaf that used to swallow the viewport.
                        await page.locator('.mm-tier[data-mm-tier="T2"]').press('Enter')
                        await expect(page.locator('#mm-groups-T2')).to_be_visible()
                        await page.locator('#mm-groups-T2 .mm-cluster[data-mm-cluster="code"] .mm-cluster-btn').press('Enter')
                        await page.locator('button[data-mm-group="T2|write"]').press('Enter')
                        await page.locator('[data-mm-tool="write_fix"]').click()
                        await page.wait_for_timeout(450)
                        m = await page.evaluate(MEASURE)
                        self.assertLessEqual(m['h'], cap + 2,
                            msg='panel taller than its 42vh ceiling: ' + str(m))
                        self.assertEqual(m['panelVisible'], m['h'],
                            msg='part of the panel is off screen: ' + str(m))
                        self.assertGreaterEqual(m['stageVisible'], min_stage,
                            msg='opening a leaf hid the map: ' + str(m))
                        self.assertGreater(m['scrollH'], m['clientH'],
                            'a nine-edge leaf must overflow the ceiling and scroll inside the panel')
                        await expect(detail).to_be_focused()
                        # Slim structure: the machine facts hide behind summaries, so the
                        # answer to "what is this" is above the fold.
                        self.assertEqual(await detail.locator('.detail-sect').count(), 3)
                        self.assertEqual(await detail.locator('.detail-sect[open]').count(), 0)
                        self.assertGreater(await detail.locator('.chain-name').count(), 0)
                        meta = detail.locator('.toola-matrix-meta')
                        await expect(meta).to_be_visible()
                        await expect(meta).to_have_attribute('open', '')
                        # Scoped to the census ul: the evidence ul sits in a nested
                        # details, and an unscoped li count measured 9 = 5 + 4.
                        self.assertEqual(await detail.locator('.toola-matrix-meta > ul > li').count(), 5)
                        self.assertEqual(await detail.locator('.meta-empty').count(), 0)
                        await detail.press('Escape')
                        await expect(page.locator('[data-mm-tool="write_fix"]')).to_be_focused()
                        # Branch B: an island leaf has no edges at all, so the five zero
                        # rows must NOT be emitted -- one line says it instead.
                        search = page.get_by_role('combobox', name='Tìm tool hoặc chức năng')
                        await search.fill('cbm_cycle_point')
                        await expect(page.get_by_role('listbox', name='Kết quả tìm công cụ').get_by_role('option').first).to_contain_text('cbm_cycle_point')
                        await search.press('Enter')
                        await page.wait_for_timeout(900)   # the reveal pan is 650ms
                        m2 = await page.evaluate(MEASURE)
                        self.assertLessEqual(m2['h'], cap + 2, msg='island panel too tall: ' + str(m2))
                        self.assertGreaterEqual(m2['stageVisible'], min_stage,
                            msg='opening an island leaf hid the map: ' + str(m2))
                        self.assertEqual(await detail.locator('.toola-matrix-meta > ul > li').count(), 0)
                        self.assertEqual(await detail.locator('.meta-empty').count(), 1)
                        self.assertEqual(await detail.locator('.detail-island').count(), 1)
                        self.assertEqual(errors, [])
                        await page.close()
            finally:
                await browser.close()

    async def test_matrix_pulses_only_new_completed_activity(self):
        try:
            from playwright.async_api import async_playwright, expect
        except ImportError:
            self.skipTest('Playwright is not installed')

        pending = {'id': 'write', 'tool': 'write_fix', 'epoch': 997, 'state': 'pending', 'status': 'ok'}
        completed = dict(pending, state='applied')
        read = {'id': 'read', 'tool': 'read_file_range', 'epoch': 1003, 'state': 'applied', 'status': 'ok'}
        replay = dict(read, id='older', tool='desktop_screenshot', epoch=999)
        samples = [
            (1000, 1, [pending]), (1005, 1, [completed, read, replay]),
            (1010, 1, [completed, read]), (1015, 30, [dict(read, id='stale', epoch=1011)]),
        ]
        async with async_playwright() as pw:
            if not Path(pw.chromium.executable_path).is_file():
                self.skipTest('Playwright Chromium is not installed')
            browser = await pw.chromium.launch()
            try:
                for motion in ('no-preference', 'reduce'):
                    with self.subTest(motion=motion):
                        page = await browser.new_page(viewport={'width': 1440, 'height': 900}, reduced_motion=motion)
                        if not hasattr(page, 'clock'):
                            self.skipTest('Playwright virtual clock is not available')
                        await page.clock.install()
                        await page.add_init_script('''
                            window.matrixMotion = [];
                            new MutationObserver(records => {
                                for (const record of records) for (const node of record.addedNodes) {
                                    if (node.matches?.('.mm-wires .mm-trace')) {
                                        window.matrixMotion.push([node.dataset.mmTier,
                                            node.classList.contains('mm-trace-live') ? 'live' : 'open']);
                                    }
                                }
                            }).observe(document, {childList:true, subtree:true});
                        ''')
                        seq = 0

                        async def activity(route):
                            nonlocal seq
                            stamp, age, events = samples[min(seq, len(samples) - 1)]
                            seq += 1
                            await route.fulfill(json={'boot_id': 'pulse', 'snapshot_seq': seq, 'delta': seq > 1,
                                                        'section_state': {'events': 'fresh'}, 'section_built_at': {'events': stamp},
                                                        'section_age_s': {'events': age}, 'events': events})

                        await page.route('**/api/monitor/activity*', activity)
                        await page.goto(str(self.client.make_url('/matrixtool')))
                        source = page.locator('#mm-source')
                        await expect(source).to_have_attribute('data-state', 'live')
                        self.assertEqual(await page.evaluate('window.matrixMotion'), [])
                        await page.locator('.mm-tier[data-mm-tier="T2"]').press('Enter')
                        opened = [] if motion == 'reduce' else [['T2', 'open']]
                        self.assertEqual(await page.evaluate('window.matrixMotion'), opened)
                        await page.clock.run_for(5000)
                        await expect(page.locator('#mm-total')).to_have_text('3 lượt · 0 lỗi')
                        # The second poll completes a known pending event and includes a new T1 event.
                        # The replayed T4 event is counted but does not pulse.
                        pulses = [] if motion == 'reduce' else [['T1', 'live'], ['T2', 'live']]
                        trace = await page.evaluate('window.matrixMotion')
                        self.assertEqual(sorted(trace[len(opened):]), pulses)
                        sample = page.locator('#mm-sample')
                        previous_sample = await sample.inner_text()
                        await page.clock.run_for(5000)
                        # Wait until the third response has painted before advancing past its abort timer.
                        await expect(sample).not_to_have_text(previous_sample)
                        await expect(source).to_have_attribute('data-state', 'live')
                        self.assertEqual(await page.evaluate('window.matrixMotion'), trace)
                        await page.clock.run_for(5000)
                        await expect(source).to_have_attribute('data-state', 'stale')
                        self.assertEqual(await page.evaluate('window.matrixMotion'), trace)
                        await expect(page.locator('.mm-trace')).to_have_count(0)
                        await page.close()
            finally:
                await browser.close()

    async def test_matrix_second_track_wires_route_through_the_gutter(self):
        """No wire may travel across a column that is not its own.

        This instrument exists because the geometry lock samples only
        getPointAtLength(0) and getPointAtLength(total) -- exactly how the leaf rail
        shipped with strokes running across cards.

        ITS PREDICATE IS INVERTED HERE (Anh 2026-11-23: "wire bi chen thang qua cot
        tiep theo. sap xep lai het 100%"). This test used to prove the opposite, that a
        second GROUP track exists and that the bus router detours into it cleanly
        (groupCols == '2', secondTrack > 0, bus > 0, arcs < wires). The detour was the
        fix; the column was the bug. Measured on the state he screenshotted -- T1 open,
        cluster[1] open, both groups open -- .mm-cluster-body#mm-cluster-T1-1 carried
        two tracks at left edges 656 and 1027, and every mid-stroke census in the
        campaign still read zero, because midHits only asks whether a stroke lands on a
        CARD. A bus lane threads the gutter between cards while running inside the
        neighbouring column, so "no card hit" and "a wire cuts across the next column"
        were both true at once. That gap is the complaint: what was measured was not
        what he saw.

        So both axes are one column now (matrix.js syncMapBranchLayout) and this test
        locks the ABSENCE of a second track at every axis -- bus == 0, arcs == wires,
        midHits == 0, and the same at 2x zoom, because a reflow that re-columns is the
        regression to watch for. The name stays: renaming it would cut the lineage back
        to the premise it replaces, and the amendment is the point. leafCols is pinned
        here too, not left to the rail test alone -- the two levels share one router, so
        a change that re-columns either axis must break a stated expectation instead of
        passing quietly while the other axis stays green.
        """
        try:
            from playwright.async_api import async_playwright, expect
        except ImportError:
            self.skipTest('Playwright is not installed')
        MEASURE = """stage => {
            const svg = stage.querySelector('svg.mm-wires');
            const ctm = svg.getScreenCTM();
            const cards = [...stage.querySelectorAll('.mm-root, .mm-tier, .mm-cluster-btn, .mm-group > button, .mm-tools button')]
                .map(node => node.getBoundingClientRect());
            const inside = (x, y, r) => x > r.left + 1 && x < r.right - 1 && y > r.top + 1 && y < r.bottom - 1;
            const dstr = path => (path.getAttribute('d') || '');
            const out = {midHits: 0, byKind: {}};
            for (const kind of ['tier', 'cluster', 'group', 'bucket', 'tool']) {
                const wires = [...svg.querySelectorAll('.mm-' + kind + '-wire')];
                let hits = 0;
                for (const path of wires) {
                    const total = path.getTotalLength();
                    for (const f of [0.2, 0.35, 0.5, 0.65, 0.8]) {
                        const q = path.getPointAtLength(total * f);
                        const pt = new DOMPoint(q.x, q.y).matrixTransform(ctm);
                        if (cards.some(r => inside(pt.x, pt.y, r))) { hits++; break; }
                    }
                }
                out.byKind[kind] = {n: wires.length,
                                    bus: wires.filter(p => dstr(p).includes('L')).length,
                                    arcs: wires.filter(p => dstr(p).includes('C')).length,
                                    midHits: hits};
                out.midHits += hits;
            }
            const cv = stage.querySelector('#mm-canvas');
            const body = stage.querySelector('#mm-groups-T1 .mm-cluster-body:not([hidden])');
            const bl = body.getBoundingClientRect().left;
            // Per-ROUTER leaf census. A chip hanging straight off .mm-tools is wired by
            // the arc router; a chip inside an OPEN bucket body is wired by the trunk
            // router. A closed body draws no wire at all (the router skips it), so only
            // chips with a measured box may be counted, or the totals would not match
            // the path count for the wrong reason.
            const chips = [...cv.querySelectorAll('[data-mm-tool]')]
                .filter(c => c.getBoundingClientRect().width >= 1);
            const trunkChips = chips.filter(c => c.closest('.mm-bucket-body')).length;
            return {
                midHits: out.midHits,
                byKind: out.byKind,
                plainChips: chips.length - trunkChips,
                trunkChips: trunkChips,
                secondTrack: [...body.querySelectorAll(':scope > .mm-group > button')]
                    .filter(b => b.getBoundingClientRect().left > bl + 40).length,
                leafCols: getComputedStyle(cv).getPropertyValue('--mm-leaf-cols').trim(),
                groupCols: getComputedStyle(cv).getPropertyValue('--mm-group-cols').trim()
            };
        }"""
        async with async_playwright() as pw:
            if not Path(pw.chromium.executable_path).is_file():
                self.skipTest('Playwright Chromium is not installed')
            browser = await pw.chromium.launch()
            try:
                page = await browser.new_page(viewport={'width': 1440, 'height': 900})
                errors = []
                page.on('pageerror', lambda error: errors.append(str(error)))
                await page.route('**/api/monitor/activity*', lambda route: route.fulfill(status=502, json={}))
                await page.goto(str(self.client.make_url('/matrixtool')))
                await page.wait_for_selector('#mm-canvas')
                await page.wait_for_timeout(400)
                stage = page.locator('#mm-stage')
                await page.locator('.mm-tier[data-mm-tier="T1"]').press('Enter')
                await expect(page.locator('#mm-groups-T1')).to_be_visible()
                await page.locator('#mm-groups-T1 .mm-cluster[data-mm-cluster="ops"] .mm-cluster-btn').press('Enter')
                # Opening a group is what grows bucket heads, so the census measures
                # strokes against them as obstacles rather than against an empty rail.
                await page.locator('button[data-mm-group="T1|ops"]').press('Enter')
                await page.wait_for_timeout(450)
                m = await stage.evaluate(MEASURE)
                self.assertEqual(m['leafCols'], '1', msg='the leaf rail re-columned: ' + str(m))
                # INVERTED, not dropped (Anh 2026-11-23: "wire bi chen thang qua cot
                # tiep theo. sap xep lai het 100%"). These five lines used to assert that
                # a second GROUP track exists and that the bus router threads it cleanly.
                # The detour was the fix and the column was the bug, and the campaign
                # could not see the difference: midHits asks only whether a stroke lands
                # on a CARD, while a bus lane threads the gutter between cards travelling
                # INSIDE the neighbouring column. Measured on the state he screenshotted
                # (T1 open, cluster[1] open, both groups open) .mm-cluster-body
                # #mm-cluster-T1-1 carried two tracks at left edges 656 and 1027 while
                # every census in this file read zero -- so "no card hit" and "a wire
                # cuts the next column" were both true at once. One column at both axes
                # deletes the geometry that needed a detour, so the hazard watched here is
                # no longer "a second-track wire fails to detour" but "a second track
                # comes back", and the predicate states that instead.
                self.assertEqual(m['groupCols'], '1',
                    msg='the group axis re-columned, so a wire has to cross the next column again: ' + str(m))
                self.assertEqual(m['secondTrack'], 0,
                    msg='a group sits in track two: ' + str(m))
                grp = m['byKind']['group']
                self.assertEqual(grp['bus'], 0,
                    msg='a group wire was detoured, which means a second track exists: ' + str(m))
                self.assertEqual(grp['arcs'], grp['n'],
                    msg='a group wire is not a plain arc, so it did not travel its own gutter: ' + str(m))
                # AMENDED, not dropped. The original claim -- "no leaf wire is a bus" --
                # was a PROXY for "the leaf rail stayed one column", which leafCols above
                # now measures directly. The proxy broke because a prefix node added a
                # SECOND leaf router: a head shares the rail's left edge with its chips,
                # so its wires are trunks and L-only by construction (a bezier from the
                # head's right edge to a chip's left edge would run the chip's whole
                # width backwards across the head's own card). Counting a trunk as a
                # detour therefore fails on a canvas that is correct. The hazard this
                # lock exists to catch -- a rail re-columning so a chip lands in track
                # two and needs a bus -- survives as a per-ROUTER census: every plain
                # chip must still be an arc, every trunk chip still a trunk, and the two
                # must account for every wire, or a chip with no wire would hide exactly
                # the drift being watched. trunkChips > 0 keeps the narrowing from
                # passing on a rail that lost its stems; the plain half stays held by
                # the rail test, whose T2|write rail is under the bucket minimum and so
                # keeps the arc router and its arcs.
                self.assertGreater(m['trunkChips'], 0, msg='no prefix trunk here, so the split proves nothing: ' + str(m))
                self.assertEqual(m['byKind']['tool']['n'], m['plainChips'] + m['trunkChips'],
                    msg='a leaf chip has no wire, or a wire serves no chip: ' + str(m))
                self.assertEqual(m['byKind']['tool']['arcs'], m['plainChips'],
                    msg='a plain-rail wire is not an arc, so the rail re-columned: ' + str(m))
                self.assertEqual(m['byKind']['tool']['bus'], m['trunkChips'],
                    msg='a prefix->tool wire is not a trunk: ' + str(m))
                self.assertEqual(m['midHits'], 0, msg='a wire crosses a card mid-stroke: ' + str(m))
                # Zoom must not reintroduce a crossing. The bus lanes used to be
                # measured from the live grid so they would follow the reflow; with one
                # column at both axes there is no lane to follow, so what zoom has to
                # prove is that the reflow did not re-column anything either.
                for _ in range(2):
                    await page.locator('[data-mm-view-action="zoom-in"]').click()
                await page.wait_for_timeout(450)
                z = await stage.evaluate(MEASURE)
                self.assertEqual(z['midHits'], 0, msg='wire crosses a card after zooming in: ' + str(z))
                self.assertEqual(z['byKind']['group']['bus'], 0,
                    msg='zooming re-columned the group axis, so a wire needs a detour again: ' + str(z))
                self.assertEqual(z['groupCols'], '1',
                    msg='the group axis came back at 2x: ' + str(z))
                self.assertEqual(errors, [])
                await page.close()
            finally:
                await browser.close()

    async def test_matrix_leaf_rail_is_one_column_and_every_tool_lands_in_a_bucket(self):
        """The leaf rail is the level where routing around the layout was the wrong fix.

        Anh called the two-column rail lỗi: DESKTOP's wire to cdp_*, checkpoint_* and
        click_* had to cross the first track, and however cleanly the bus router
        threaded the gutter it still read as a gãy wire. One column deletes the
        geometry that needed a detour -- every card in the rail shares one left edge,
        so a parent-right to chip-left bezier spends its whole travel in the empty
        gutter. That shared edge is locked here as the PREMISE, not inferred from the
        mid-stroke count, because midHits==0 can also be produced by a lucky frame.

        The other half of the ask is that 31 chips must not be a plain column either:
        it is sectioned, and EVERY tool of the group lands in exactly one section.
        The expected set is read from TOOLS rather than typed out, so the lock
        survives the inventory growing. The child-kind string (H head, c chip, M tail)
        is asserted as a pattern instead of per-chip bookkeeping: `(Hc+)+M?` rejects an
        orphan chip, an empty section, and a head painted after its chips.

        Partition and coherence are two separate locks. The partition alone is not
        enough, and a negative control showed why: deleting one rule from the bucket
        table still files every tool, so the pattern and coverage asserts stay green
        on a rail that has lost a section. See the frozen-shape note below.
        """
        try:
            from playwright.async_api import async_playwright, expect
        except ImportError:
            self.skipTest('Playwright is not installed')
        CARDS = ('#mm-canvas .mm-root, #mm-canvas .mm-tier, #mm-canvas .mm-cluster-btn, '
                 '#mm-canvas .mm-group > button, #mm-canvas .mm-tools button[data-mm-tool], '
                 '#mm-canvas .mm-bucket-btn')
        RAIL = """key => {
            const btn = document.querySelector('button[data-mm-group="' + key + '"]');
            const grid = document.getElementById(btn.getAttribute('aria-controls'));
            const kids = [...grid.children];
            // The one-shared-left-edge premise now spans three card kinds: a plain-rail
            // chip, a bucket HEAD (it is a node with its own wire in and out), and a
            // chip nested inside a bucket body. All three are measured, because the
            // trunk geometry depends on the head sitting on the SAME edge as its chips
            // -- which is precisely why that level cannot be an arc.
            const edges = [];
            for (const n of grid.querySelectorAll('[data-mm-tool], .mm-bucket-btn')) {
                edges.push(Math.round(n.getBoundingClientRect().left));
            }
            const svg = document.querySelector('svg.mm-wires');
            const ctm = svg.getScreenCTM();
            const cards = [...document.querySelectorAll('%CARDS%')].map(n => n.getBoundingClientRect());
            const inside = (x, y, r) => x > r.left + 1 && x < r.right - 1 && y > r.top + 1 && y < r.bottom - 1;
            const wires = [...svg.querySelectorAll('.mm-tool-wire[data-mm-group="' + key + '"]')];
            let midHits = 0;
            for (const path of wires) {
                const total = path.getTotalLength();
                for (const f of [0.2, 0.35, 0.5, 0.65, 0.8]) {
                    const q = path.getPointAtLength(total * f);
                    const pt = new DOMPoint(q.x, q.y).matrixTransform(ctm);
                    if (cards.some(r => inside(pt.x, pt.y, r))) { midHits++; break; }
                }
            }
            const bucketKids = kids.filter(n => n.classList.contains('mm-bucket'));
            const allChips = [...grid.querySelectorAll('[data-mm-tool]')];
            return {
                // A bucket node is ONE grid child (head + body), so the chips are no
                // longer siblings a pattern could see and the run collapses to H.
                pattern: kids.map(n => n.classList.contains('mm-bucket') ? 'H'
                    : (n.dataset.mmTool ? 'c' : (n.classList.contains('mm-more') ? 'M' : '?'))).join(''),
                names: allChips.map(n => n.dataset.mmTool),
                labels: bucketKids.map(n => n.querySelector('.mm-bucket-btn strong').textContent.trim()),
                // The group -> prefix edge, counted apart from the chip edges because it is a
                // different class and nobody counted it. Emitted for a folded bucket too, so
                // this tracks HEADS rather than visible chips -- which is what lets a plain
                // rail use it as a negative control (a plain rail must report zero).
                bucketWires: svg.querySelectorAll(
                    '.mm-bucket-wire[data-mm-group="' + key + '"]').length,
                // Membership per section, read from OWNERSHIP rather than from "the last
                // head above it": the label-era rule misattributes a chip the moment a
                // head folds, and ownership is what the wires actually follow.
                sections: bucketKids.map(n => ({
                    head: n.querySelector('.mm-bucket-btn strong').textContent.trim(),
                    chips: [...n.querySelectorAll('[data-mm-tool]')].map(c => c.dataset.mmTool)
                })),
                bucketShape: bucketKids.map(n => {
                    const head = n.querySelector(':scope > .mm-bucket-btn');
                    const body = n.querySelector(':scope > .mm-bucket-body');
                    return {
                        heads: n.querySelectorAll('.mm-bucket-btn').length,
                        bodies: n.querySelectorAll('.mm-bucket-body').length,
                        chips: n.querySelectorAll('[data-mm-tool]').length,
                        expanded: head ? head.getAttribute('aria-expanded') : null,
                        controls: !!(head && body) && head.getAttribute('aria-controls') === body.id,
                        toggle: !!(head && head.querySelector('.mm-toggle'))
                    };
                }),
                moreText: (grid.querySelector('.mm-more span') || {}).textContent || null,
                edges: [...new Set(edges)],
                wires: wires.length,
                bus: wires.filter(p => (p.getAttribute('d') || '').includes('L')).length,
                arcs: wires.filter(p => (p.getAttribute('d') || '').includes('C')).length,
                midHits: midHits,
                // Every point of a trunk wire must sit ON the rail's right edge or
                // inside the runway to its right, and never past it. This INVERTS the
                // old lock, which demanded every vertex be at or left of railLeft: that
                // was the spine living in the LEFT gutter, and the left gutter is where
                // the group -> head arcs run, which is the overlap Anh reported. The
                // structural reason midHits is 0 rather than a lucky frame survives
                // unchanged -- the vertical run still lives in air belonging to no card.
                //
                // SAMPLED, NOT PARSED, and the change is load-bearing. The first version
                // read coordinates out of the d string with /[ML] ([^ ]+) ([^ ]+)/,
                // which is fine while the trunk is four 'L' vertices and becomes blind
                // the moment it is rounded: an elliptical arc carries seven parameters
                // before its endpoint, so a regex hunting pairs would read rx ry as a
                // coordinate and every rounded wire would score as off-runway -- or,
                // worse, a future producer could dodge the lock by choosing a command
                // the pattern does not know. getPointAtLength asks the renderer where
                // the ink actually goes, so it holds for L, A, C and Q alike, and it
                // checks the 2px BETWEEN vertices too: a leaning spine or a bulge
                // outside the runway is caught where the old corner-only test would
                // have passed.
                trunkOffRunway: (() => {
                    if (!allChips.length) return 0;
                    const railRight = Math.max(...allChips.map(c => c.getBoundingClientRect().right));
                    const runway = parseFloat(getComputedStyle(document.getElementById('mm-canvas'))
                        .getPropertyValue('--mm-trunk-runway')) || 0;
                    let off = 0;
                    for (const p of wires) {
                        const L = p.getTotalLength();
                        for (let s = 0; s <= L; s += 2) {
                            const q = p.getPointAtLength(s);
                            const pt = new DOMPoint(q.x, q.y).matrixTransform(ctm);
                            if (pt.x < railRight - 1 || pt.x > railRight + runway + 1) { off++; break; }
                        }
                    }
                    return off;
                })(),
                leafCols: getComputedStyle(document.getElementById('mm-canvas')).getPropertyValue('--mm-leaf-cols').trim()
            };
        }""".replace('%CARDS%', CARDS)
        async with async_playwright() as pw:
            if not Path(pw.chromium.executable_path).is_file():
                self.skipTest('Playwright Chromium is not installed')
            browser = await pw.chromium.launch()
            try:
                page = await browser.new_page(viewport={'width': 1440, 'height': 900},
                                              reduced_motion='reduce')
                errors = []
                page.on('pageerror', lambda error: errors.append(str(error)))
                await page.route('**/api/monitor/activity*', lambda route: route.fulfill(status=502, json={}))
                await page.goto(str(self.client.make_url('/matrixtool')))
                await page.wait_for_selector('#mm-canvas')
                expected = await page.evaluate(
                    "() => TOOLS.filter(t => t.t === 'T4' && t.fg === 'desktop').map(t => t.n)")
                self.assertGreater(len(expected), 20,
                    'this rail is only interesting while it is long: ' + str(len(expected)))
                await page.locator('.mm-tier[data-mm-tier="T4"]').press('Enter')
                await page.wait_for_timeout(200)
                await page.locator('#mm-groups-T4 .mm-cluster[data-mm-cluster="ui"] .mm-cluster-btn').press('Enter')
                await page.wait_for_timeout(200)
                await page.locator('button[data-mm-group="T4|desktop"]').press('Enter')
                await page.wait_for_timeout(600)

                first = await page.evaluate(RAIL, 'T4|desktop')
                self.assertEqual(first['leafCols'], '1', msg='the rail did not go one-column: ' + str(first))
                self.assertRegex(first['pattern'], r'^H+M$',
                    msg='a paged bucketed rail must be nodes then the tail: ' + first['pattern'])
                # The tail counts TOOLS, not rows: a header is not something Anh clicks
                # for, so promising rows would overstate what the button reveals.
                self.assertEqual(first['moreText'],
                                 'Còn ' + str(len(expected) - len(first['names'])) + ' tool ▸',
                    msg='pager text vs rendered chips: ' + str(first))
                for _ in range(12):
                    more = page.locator('button[data-mm-more="T4|desktop"]')
                    if not await more.count():
                        break
                    await more.first.click()
                    await page.wait_for_timeout(200)
                await page.wait_for_timeout(400)
                rail = await page.evaluate(RAIL, 'T4|desktop')
                self.assertEqual(rail['leafCols'], '1', msg='rail re-columned after paging: ' + str(rail))
                self.assertRegex(rail['pattern'], r'^H+$',
                    msg='exhausted rail must be bucket nodes only: ' + rail['pattern'])
                # (Hc+)+ is gone because the chips moved INSIDE the node, so they are
                # no longer siblings a pattern could see. The three things that
                # pattern guaranteed -- no orphan chip, no empty section, head before
                # its chips -- are locked directly on the DOM the router builds,
                # which is strictly stronger: it holds whether the bucket is open or
                # folded, and it catches a head whose aria-controls points nowhere.
                for s in rail['bucketShape']:
                    self.assertEqual((s['heads'], s['bodies']), (1, 1),
                        msg='a bucket node is not one head plus one body: ' + str(s))
                    self.assertGreater(s['chips'], 0, 'an empty bucket node: ' + str(s))
                    self.assertTrue(s['controls'], 'head aria-controls does not match its body id: ' + str(s))
                    self.assertTrue(s['toggle'], 'a bucket node has no toggle affordance: ' + str(s))
                    self.assertEqual(s['expanded'], 'true',
                        msg='a bucket node must be open on first paint, or the wire from it '
                            'to its tools is never drawn: ' + str(s))
                # No tool lost, none doubled -- the bucket table really covers the rail.
                self.assertEqual(sorted(rail['names']), sorted(expected),
                    msg='rail chips != group tools; missing='
                        + str(sorted(set(expected) - set(rail['names'])))
                        + ' extra=' + str(sorted(set(rail['names']) - set(expected))))
                self.assertEqual(len(rail['labels']), len(set(rail['labels'])),
                    'a section name is printed twice: ' + str(rail['labels']))
                # The partition alone is NOT enough, and a negative control proved it:
                # deleting the cdp rule from LEAF_BUCKET_RULES leaves every tool still
                # filed (cdp_* falls back to its leading token, so coverage stays green)
                # and this test passes on a rail that lost its CDP section. Freezing the
                # ordered head list WITH its counts is the cheap half that catches it --
                # a dropped rule removes a head, a misfiled tool moves a count.
                # THE ORDER IS RULE-TABLE ORDER NOW, not size-descending. Size order is
                # not stable under a rename: one tool refiled and every head reshuffles.
                # While the head was a caption nobody clicked, that was invisible; the
                # head is a NODE now, so the shuffle moves a card under Anh's cursor and
                # reads as the canvas jumping. This literal is meant to fail when the
                # rail's SHAPE changes, so it records the order the table defines.
                self.assertEqual([(s['head'], len(s['chips'])) for s in rail['sections']],
                    [('CDP & electron', 5), ('Hộp thoại', 3), ('Click', 4),
                     ('Cửa sổ', 3), ('DOM & trang', 9), ('Tool khác', 7)],
                    msg='rail shape drifted: ' + str([(s['head'], s['chips']) for s in rail['sections']]))
                # Counts catch a dropped rule and a misfiled tool, but two buckets of
                # the SAME size can trade a tool invisibly, and this rail has exactly
                # one such pair (Hộp thoại and Cửa sổ, both 3). Locking membership for
                # that pair closes the gap without typing out all thirty-one chips:
                # every other head has a unique count, so a tool entering or leaving
                # it moves a number the assert above already watches.
                byHead = {s['head']: sorted(s['chips']) for s in rail['sections']}
                self.assertEqual(byHead['Hộp thoại'],
                    ['cancel_native_dialog', 'click_and_handle_folder_dialog', 'fill_folder_dialog'],
                    msg='dialog membership drifted: ' + str(byHead['Hộp thoại']))
                self.assertEqual(byHead['Cửa sổ'],
                    ['check_window_visibility', 'desktop_window_list', 'desktop_window_state'],
                    msg='window membership drifted: ' + str(byHead['Cửa sổ']));
                # ONE shared left edge is what makes the straight arc safe, so it is
                # asserted as the premise rather than inferred from midHits.
                self.assertEqual(len(rail['edges']), 1,
                    msg='rail cards do not share one left edge: ' + str(rail['edges']))
                # HALF THE FEATURE WAS UNMEASURED until this assert. .mm-bucket-wire is the
                # group -> prefix edge; every census in this file counts .mm-tool-wire, which
                # is the prefix -> chip edge. A router that stopped emitting the FIRST edge
                # would leave the chip count, the rail shape, the frozen membership literal and
                # the left-edge lock all green while the prefix nodes Anh asked for sat on the
                # canvas with no parent wire -- detached on screen, invisible to the suite.
                self.assertEqual(rail['bucketWires'], len(rail['sections']),
                    msg='prefix nodes without a parent wire: ' + str(rail['bucketWires'])
                        + ' for ' + str(len(rail['sections'])) + ' heads')
                # The prefix->tool level is a TREE, not an arc. A bucket head shares
                # the rail's left edge with its chips -- that is the one-edge lock
                # above, and it is what stops the canvas growing sideways into the
                # neighbouring group column -- so a bezier from head.right to
                # chip.left would run the chip's whole width BACKWARDS across the
                # head's own card. Every tool wire is therefore a trunk: 'L'
                # vertices only, no 'C'. This INVERTS the old bus==0 lock rather
                # than dropping it, because the hazard it guarded (routing around a
                # card, the two-column defect) becomes "the trunk left the gutter"
                # once the level is a tree -- and trunkOffGutter plus the midHits
                # lock below are that guard, measured on every vertex.
                self.assertEqual(rail['arcs'], 0,
                    msg='a prefix->tool wire drew an arc back across its own bucket: ' + str(rail))
                self.assertEqual(rail['bus'], rail['wires'],
                    msg='not every prefix->tool wire is a trunk: ' + str(rail))
                self.assertEqual(rail['trunkOffRunway'], 0,
                    msg='a trunk vertex escaped its runway: ' + str(rail['trunkOffRunway']))
                self.assertEqual(rail['wires'], len(expected),
                    'every chip must carry exactly one wire: ' + str(rail))
                self.assertEqual(rail['midHits'], 0,
                    msg='a leaf wire crosses a card mid-stroke: ' + str(rail))

                # The deliberate exception: when the whole group falls into ONE bucket a
                # header would only repeat the chip above it, so that rail stays plain.
                await page.locator('.mm-tier[data-mm-tier="T2"]').press('Enter')
                await page.wait_for_timeout(200)
                await page.locator('#mm-groups-T2 .mm-cluster[data-mm-cluster="code"] .mm-cluster-btn').press('Enter')
                await page.wait_for_timeout(200)
                await page.locator('button[data-mm-group="T2|write"]').press('Enter')
                await page.wait_for_timeout(600)
                plain = await page.evaluate(RAIL, 'T2|write')
                self.assertEqual(plain['labels'], [],
                    msg='a single-bucket group must not grow a header: ' + str(plain))
                self.assertRegex(plain['pattern'], r'^c+$', 'plain rail must be chips only: ' + plain['pattern'])
                self.assertEqual(plain['bus'], 0, 'leaf wires stayed arcs on the plain rail too: ' + str(plain))
                self.assertEqual(plain['bucketWires'], 0,
                    'a plain rail has no prefix node, so it must carry no group -> prefix wire either: ' + str(plain['bucketWires']))
                self.assertEqual(errors, [])
                await page.close()
            finally:
                await browser.close()

    async def test_matrix_trunk_spine_and_fan_lanes_keep_their_clearance(self):
        """Anh 2026-11-23, second complaint: 'con rat nhieu khu vuc dinh loi cu, wire
        bi chen thang qua cot tiep theo. sap xep lai het 100% di'.

        What that measures is NOT a crossing. The temp slot census (slot_probe.py)
        walks every sample of every wire against every card in all 38 deep states at
        1440 and 390 and reports piercing_wires 0 and overlaps 0 -- and control_probe.py
        proves that zero means something, because an injected diagonal fires the census,
        an injected shared lane fires the fan census and an injected long near-miss
        fires the offender census, and removing all three returns the same page to 0.
        (The first version of that control reported FAIL while both meters were in fact
        working, by identifying the injected wire as one owning no endpoint card -- but
        a wire legitimately owns the card it leaves from. A zero from a meter nobody has
        made fire is not a pass, and this campaign produced nine of those.)

        What the trunk census found instead is LENGTH x PROXIMITY: bus_probe read his
        screenshot as one bus about 530px tall sitting 15px off a column, which the eye
        binds to that column and reads as a line driven straight down it. So two numbers
        are locked here, and neither is expressible as 'does a wire touch a card':

        1. The PREFIX -> TOOL spine runs in a RUNWAY to the RIGHT of its rail, and no
           curved wire comes within 3px of it. It used to run down the MIDDLE of the
           left gutter, on the claim that landing on the apex of the group -> head arc
           made a section read as one continuous line into the stem instead of a kink.
           That claim IS the defect, and it is arithmetic: the arc's control x is
           (groupRight + railLeft)/2 while trunkInset put the spine at
           railLeft - corridor/2 -- THE SAME X -- so six arcs ran along a ~530px spine.
           runway_probe.py measured OVERLAPPING_ARCS = 6 on this rail at BOTH 1440 and
           390, and Anh named the symptom: 'wire thang cua node ngon prefix chuyen sang
           ben phai di. hien tai dang chong cheo voi wire cong'. Two ink lines on one
           another is not a continuous line, it is a thicker one. The move hands the
           left gutter to those arcs alone and gives the spine its own air, where one
           column everywhere means there is no neighbouring column to mistake it for.
           Relocation, not re-shape: the wire is still four 'L' vertices, so the rail
           test's arcs == 0 and bus == wires locks stand untouched.

        2. Sibling arcs leaving ONE parent occupy DISTINCT lanes, each at least
           LANE_CLEAR (7px) off every card. The old flat cap Math.min(20, budget*0.75)
           failed twice on measurement: a 2px step between six bucket arcs at 1440, and
           2.9px off a card edge in the 16px mobile corridor. Six wires on six
           indistinguishable x-values is the ladder Anh photographs as one thick line.

        The expected spine is re-derived from the runway the CSS reserves rather than
        typed as a literal, so the lock survives a runway widening, fails if the spine
        leaves its air, and fails outright if the layout stops reserving a runway.
        """
        try:
            from playwright.async_api import async_playwright
        except ImportError:
            self.skipTest('Playwright is not installed')
        CARDS = ('#mm-canvas .mm-root, #mm-canvas .mm-tier, #mm-canvas .mm-cluster-btn, '
                 '#mm-canvas .mm-group > button, #mm-canvas .mm-tools button[data-mm-tool], '
                 '#mm-canvas .mm-bucket-btn')
        SPINE = """key => {
            const btn = document.querySelector('button[data-mm-group="' + key + '"]');
            const grid = document.getElementById(btn.getAttribute('aria-controls'));
            const svg = document.querySelector('svg.mm-wires');
            const ctm = svg.getScreenCTM();
            const loc = (x, y) => { const p = new DOMPoint(x, y).matrixTransform(ctm); return [p.x, p.y]; };
            const cards = [...document.querySelectorAll('CARDS_PLACEHOLDER')]
                .map(n => n.getBoundingClientRect()).filter(r => r.width >= 1 && r.height >= 1);
            // Nearest card BORDER to a vertical run, counting only cards BESIDE it.
            // Screen px throughout: mixing a canvas-local card rect with a CTM-mapped
            // wire sample is what made the temp slot meter invent 60 phantom crossings.
            const clearOf = (x, y0, y1) => {
                let best = 1e9;
                for (const c of cards) {
                    if (c.bottom <= y0 + 1 || c.top >= y1 - 1) continue;
                    for (const ex of [c.left, c.right]) best = Math.min(best, Math.abs(ex - x));
                }
                return best;
            };
            const groupRight = btn.getBoundingClientRect().right;
            const railCards = [...grid.querySelectorAll('[data-mm-tool], .mm-bucket-btn')]
                .map(n => n.getBoundingClientRect());
            const railLeft = Math.min(...railCards.map(r => r.left));
            const railRight = Math.max(...railCards.map(r => r.right));
            const corridor = railLeft - groupRight;
            // matrix.js:connectTrunk, re-derived from the LAYOUT rather than typed as a
            // literal: the spine is 0.625 of the CSS runway to the right of the rail, so
            // the lock survives a runway widening and still fails if the spine leaves
            // its air. Reading the variable is also what makes a CSS regression (pad
            // below the runway, so the stroke leaves the SVG box) a test failure.
            const runway = parseFloat(getComputedStyle(document.getElementById('mm-canvas'))
                .getPropertyValue('--mm-trunk-runway')) || 0;
            const expected = railRight + Math.round(runway * 0.625);
            const trunks = [];
            // SAMPLED, NOT PARSED, and the change is load-bearing rather than tidier.
            // This census used to sweep every number out of the d string and pair them
            // into points, which held while a trunk was four 'L' vertices and falls
            // apart the moment its corners are rounded: an elliptical arc carries rx ry
            // rotation large-arc sweep BEFORE its endpoint, so a number-pairing loop
            // reads '6 6' as a coordinate, the vertical run is never found, and
            // assertTrue(st['trunks']) then fails for a reason nothing in the message
            // points at -- or, the worse direction, a bogus pair lands on the expected
            // x and the lock passes on geometry it never measured. getPointAtLength
            // asks the renderer where the ink actually goes, so it holds for L and A
            // alike, and the run it reports is the run the eye sees.
            // A horizontal tick contributes nothing: consecutive samples 2px apart along
            // x break the run every step, so its length is 0 and the >= 20 gate drops it
            // -- which is the same 'the tick, not the spine' exclusion the parse had.
            for (const p of svg.querySelectorAll('.mm-tool-wire[data-mm-group="' + key + '"]')) {
                const L = p.getTotalLength();
                let runX = null, runY0 = 0, runY1 = 0;
                const flush = () => {
                    if (runX === null) return;
                    const y0 = Math.min(runY0, runY1), y1 = Math.max(runY0, runY1);
                    if (y1 - y0 >= 20) trunks.push({ x: runX, len: y1 - y0, y0: y0, y1: y1,
                                                     clear: clearOf(runX, y0, y1) });
                    runX = null;
                };
                for (let s = 0; s <= L; s += 2) {
                    const q = p.getPointAtLength(s);
                    const sp = new DOMPoint(q.x, q.y).matrixTransform(ctm);
                    if (runX !== null && Math.abs(sp.x - runX) <= 0.5) { runY1 = sp.y; }
                    else { flush(); runX = sp.x; runY0 = sp.y; runY1 = sp.y; }
                }
                flush();
            }
            // FAN: siblings are grouped by the point they LEAVE -- the parent's
            // right-midpoint. Grouping by (kind, group, tier) is how the temp bus census
            // went blind: the root -> tier fan is four wires with four different tiers
            // and no group attribute at all, so it formed no group and could never be
            // reported as fused.
            const byParent = new Map();
            for (const p of svg.querySelectorAll('path:not(.mm-wire-hit)')) {
                const d = p.getAttribute('d') || '';
                if (d.indexOf('C') < 0) continue;
                const nums = (d.match(/-?[0-9]+[.]?[0-9]*/g) || []).map(Number);
                if (nums.length < 8) continue;
                const s = loc(nums[0], nums[1]), e = loc(nums[6], nums[7]), c = loc(nums[2], nums[3]);
                const k = Math.round(s[0]) + ',' + Math.round(s[1]);
                if (!byParent.has(k)) byParent.set(k, []);
                byParent.get(k).push({ cx: c[0],
                    clear: clearOf(c[0], Math.min(s[1], e[1]), Math.max(s[1], e[1])) });
            }
            const fans = [];
            for (const [k, list] of byParent) {
                if (list.length < 2) continue;              // one child has no lane to share
                const xs = [...new Set(list.map(l => Math.round(l.cx * 10) / 10))];
                fans.push({ parent: k, n: list.length, distinct: xs.length,
                    minClear: Math.min(...list.map(l => l.clear)) });
            }
            // HUGGING: every curved wire, sampled every 2px, against every spine's
            // y-band. This is runway_probe.py's OVERLAPPING_ARCS promoted into the
            // suite. The fan census above cannot see it: fans compare siblings to EACH
            // OTHER and measure each lane's clearance to a CARD, so an arc lying exactly
            // on a straight wire registers as neither a shared lane nor a near-miss --
            // which is why every census in this campaign stood green while Anh saw six
            // arcs welded to the spine.
            let hugging = 0;
            const hugExamples = [];
            for (const p of svg.querySelectorAll('path:not(.mm-wire-hit)')) {
                if ((p.getAttribute('d') || '').indexOf('C') < 0) continue;
                const L = p.getTotalLength();
                for (let s = 0; s + 2 <= L; s += 2) {
                    const q = p.getPointAtLength(s);
                    const sp = new DOMPoint(q.x, q.y).matrixTransform(ctm);
                    for (const t of trunks) {
                        if (Math.abs(sp.x - t.x) < 3 && sp.y > t.y0 - 3 && sp.y < t.y1 + 3) {
                            hugging++;
                            if (hugExamples.length < 5) hugExamples.push({
                                kind: [...p.classList].find(c => c.endsWith('-wire')) || '?',
                                x: Math.round(sp.x), y: Math.round(sp.y),
                                spine: Math.round(t.x) });
                            break;
                        }
                    }
                }
            }
            return { corridor: Math.round(corridor), runway: Math.round(runway),
                     expected: Math.round(expected * 10) / 10, hugging: hugging,
                     hugExamples: hugExamples, trunks: trunks, fans: fans };
        }""".replace('CARDS_PLACEHOLDER', CARDS)
        async with async_playwright() as pw:
            if not Path(pw.chromium.executable_path).is_file():
                self.skipTest('Playwright Chromium is not installed')
            browser = await pw.chromium.launch()
            try:
                page = await browser.new_page(viewport={'width': 1440, 'height': 900},
                                              reduced_motion='reduce')
                errors = []
                page.on('pageerror', lambda error: errors.append(str(error)))
                await page.route('**/api/monitor/activity*',
                                 lambda route: route.fulfill(status=502, json={}))
                await page.goto(str(self.client.make_url('/matrixtool')))
                await page.wait_for_selector('#mm-canvas')
                await page.locator('.mm-tier[data-mm-tier="T4"]').press('Enter')
                await page.wait_for_timeout(200)
                await page.locator('#mm-groups-T4 .mm-cluster[data-mm-cluster="ui"] .mm-cluster-btn').press('Enter')
                await page.wait_for_timeout(200)
                await page.locator('button[data-mm-group="T4|desktop"]').press('Enter')
                await page.wait_for_timeout(600)
                for _ in range(12):
                    more = page.locator('button[data-mm-more="T4|desktop"]')
                    if not await more.count():
                        break
                    await more.first.click()
                    await page.wait_for_timeout(150)
                await page.wait_for_timeout(400)
                st = await page.evaluate(SPINE, 'T4|desktop')
                # Two gutters, two premises. The left one still belongs to the group ->
                # head arcs; the right one is the spine's own air, and below 12px a
                # 1.65px stroke has no clearance either side of it.
                self.assertGreater(st['corridor'], 12,
                    msg='no gutter left for the group -> head arcs: ' + str(st))
                self.assertGreater(st['runway'], 12,
                    msg='the canvas reserves no runway for a right-hand spine: ' + str(st))
                self.assertTrue(st['trunks'], 'the rail emitted no spine segment at all')
                for t in st['trunks']:
                    self.assertLess(abs(t['x'] - st['expected']), 1.5,
                        msg='spine not in its runway (expected '
                            + str(st['expected']) + ', got ' + str(round(t['x'], 1))
                            + ', runway ' + str(st['runway']) + 'px): ' + str(t))
                    self.assertGreaterEqual(t['clear'], 6,
                        msg='spine reads as a line against a neighbouring column: '
                            + str(round(t['clear'], 2)) + 'px clear on a '
                            + str(round(t['len'])) + 'px run')
                # THE COMPLAINT THIS MOVE ANSWERS, LOCKED AS A NUMBER. A position assert
                # alone would still pass on a spine the arcs coincide with, and that
                # coincidence is exactly what Anh saw: the old spine sat at
                # railLeft - corridor/2, which IS the arc control x
                # (groupRight + railLeft)/2, so six arcs ran along a ~530px spine
                # (runway_probe: OVERLAPPING_ARCS = 6, at BOTH widths). What is asserted
                # here is the outcome of the move, not its arithmetic.
                self.assertEqual(st['hugging'], 0,
                    msg='a curved wire hugs the spine again: ' + str(st['hugExamples']))
                self.assertTrue(st['fans'], 'no sibling fan was found to measure')
                for f in st['fans']:
                    self.assertEqual(f['distinct'], f['n'],
                        msg='two siblings of one parent share a lane, so the fan reads as '
                            'one fused line: ' + str(f))
                    self.assertGreaterEqual(f['minClear'], 6,
                        msg='a lane is closer to a card than LANE_CLEAR: ' + str(f))
                self.assertEqual(errors, [])
                await page.close()
            finally:
                await browser.close()

    async def test_matrix_trunk_corners_are_rounded_and_the_census_can_see_a_square(self):
        """Anh 2026-11-23: 'van con sot 1 so wire vuong' -- square wires are still left.

        elbow_probe named the producer exactly rather than by guess: 31 squared wires,
        every one of them mm-tool-wire, every one inside a bucketed rail, every one at
        90 degrees. They are connectTrunk's own polyline -- out along the tick, down the
        spine, back along the tick -- and it was the ONLY square-cornered producer left,
        because every other level of the map is a cubic. Atom draws all of its
        connectors as cubics and so has no corner at all.

        WHY AN ARC AND NOT A BEZIER: a cubic between two points on one vertical line
        bows sideways out of the runway, and the runway is 10px wide with the spine at
        10px -- the bulge would leave the air the rail test measures. A quarter ellipse
        keeps the spine exactly where it is and takes only the vertex away.

        WHY AN ARC AND NOT A CHAMFER, which is measured below rather than asserted here:
        the census differences headings 2px apart along the rendered path, so a 45
        degree cut turns 2/len rad per step and stays above its 0.6 rad threshold -- a
        chamfer would be reported as a square wire forever. The third arm of the control
        proves exactly that, which is why this fix rounds with A and not with more L.

        WHAT THE OTHER LOCKS IN THIS FILE ALREADY COVER, so this one does not repeat it:
        the spine's POSITION and its clearance are pinned by
        test_matrix_trunk_spine_and_fan_lanes_keep_their_clearance, whose trunk census
        and trunkOffRunway were both converted from parsing the d string to
        getPointAtLength sampling -- a parse would have read an arc's 'rx ry rotation
        large-arc sweep' as coordinates and scored every rounded wire as off-runway, or
        worse, passed on geometry it never looked at. arcs counts the character 'C' and
        bus counts 'L', so an A segment leaves arcs == 0 and bus == wires standing, and
        those asserts stand untouched rather than being amended.

        THE CONTROL IS THE POINT. This campaign read a zero from a meter that matched
        nothing twelve times, so a census reporting squared == 0 is worthless until it
        has been shown to fire. Three wires are injected into the same SVG in the same
        local space, each bent like a real trunk: SQUARE must raise the count, FILLET
        with the same 6px radius the router uses must NOT, CHAMFER must. The first
        version of this control failed its own fillet arm, and the defect was in the
        control -- its last leg ran right after the arc had already turned left, so the
        wire had a genuine cusp and the census was correct to fire. That is a control
        earning its place.
        """
        try:
            from playwright.async_api import async_playwright
        except ImportError:
            self.skipTest('Playwright is not installed')
        CENSUS = '''() => {
          const svg = document.getElementById('mm-branch-wires');
          const STEP = 2, TURN = 0.6;          // rad: ~34deg, so a 90deg elbow always fires
          let wires = 0, squared = 0;
          const byKind = {}, worst = [];
          for (const p of svg.querySelectorAll('path:not(.mm-wire-hit):not(.mm-trace)')) {
            const L = p.getTotalLength();
            if (L < 6) continue;
            let prev = null, corners = 0, maxDeg = 0;
            for (let s = 0; s + STEP <= L; s += STEP) {
              const a = p.getPointAtLength(s), b = p.getPointAtLength(s + STEP);
              const ang = Math.atan2(b.y - a.y, b.x - a.x);
              if (prev !== null) {
                let d = Math.abs(ang - prev);
                if (d > Math.PI) d = 2 * Math.PI - d;
                if (d > TURN) { corners++; maxDeg = Math.max(maxDeg, d); }
              }
              prev = ang;
            }
            const kind = [...p.classList].find(c => c.endsWith('-wire')) || '?';
            wires++;
            const k = byKind[kind] || (byKind[kind] = { wires: 0, squared: 0 });
            k.wires++;
            if (corners) {
              squared++; k.squared++;
              worst.push({ kind: kind, group: p.dataset.mmGroup || '', corners: corners,
                           deg: Math.round(maxDeg * 180 / Math.PI) });
            }
          }
          worst.sort((a, b) => b.corners - a.corners);
          return { wires: wires, squared: squared, byKind: byKind, worst: worst.slice(0, 6) };
        }'''
        ADD = """(a) => {
          const svg = document.getElementById('mm-branch-wires');
          const p = document.createElementNS('http://www.w3.org/2000/svg', 'path');
          p.setAttribute('d', a.d);
          p.setAttribute('class', 'mm-tool-wire');
          p.dataset.mmGroup = a.cls;
          svg.appendChild(p);
          return p.getTotalLength();
        }"""
        DROP = """(a) => {
          const p = document.querySelector('#mm-branch-wires [data-mm-group="' + a.cls + '"]');
          if (p) p.remove();
          return !!p;
        }"""
        # Out from the head's right edge to the spine, down it, then back LEFT into the
        # chip's right edge -- the real trunk shape, because the spine lives to the
        # right of the rail and the chip is to the left of it.
        ARMS = [
            ('SQUARE', 'M 120 120 L 260 120 L 260 340 L 120 340', True),
            ('FILLET', 'M 120 120 L 254 120 A 6 6 0 0 1 260 126 L 260 334'
                       ' A 6 6 0 0 1 254 340 L 120 340', False),
            ('CHAMFER', 'M 120 120 L 252 120 L 260 128 L 260 332 L 252 340 L 120 340', True),
        ]
        async with async_playwright() as pw:
            if not Path(pw.chromium.executable_path).is_file():
                self.skipTest('Playwright Chromium is not installed')
            browser = await pw.chromium.launch()
            try:
                for width, height in ((1440, 900), (390, 844)):
                    page = await browser.new_page(viewport={'width': width, 'height': height},
                                                  reduced_motion='reduce')
                    errors = []
                    page.on('pageerror', lambda error: errors.append(str(error)))
                    await page.route('**/api/monitor/activity*',
                                     lambda route: route.fulfill(status=502, json={}))
                    await page.goto(str(self.client.make_url('/matrixtool')))
                    await page.wait_for_selector('#mm-canvas')
                    await page.wait_for_timeout(900)
                    # The state Anh photographed: the longest bucketed rail on the map,
                    # paged to the end so every prefix node has its chips rendered.
                    await page.locator('.mm-tier[data-mm-tier="T4"]').press('Enter')
                    await page.wait_for_timeout(200)
                    await page.locator('#mm-groups-T4 .mm-cluster[data-mm-cluster="ui"]'
                                       ' .mm-cluster-btn').press('Enter')
                    await page.wait_for_timeout(200)
                    await page.locator('button[data-mm-group="T4|desktop"]').press('Enter')
                    await page.wait_for_timeout(600)
                    for _ in range(12):
                        more = page.locator('button[data-mm-more="T4|desktop"]')
                        if not await more.count():
                            break
                        await more.first.click()
                        await page.wait_for_timeout(150)
                    await page.wait_for_timeout(400)

                    base = await page.evaluate(CENSUS)
                    self.assertGreater(base['wires'], 0,
                        msg='no wire was measured at ' + str(width)
                            + ': a census over nothing reports squared == 0')
                    self.assertGreater(base['byKind'].get('mm-tool-wire', {}).get('wires', 0), 0,
                        msg='no trunk wire was rendered at ' + str(width)
                            + ', so the square-wire zero below measures a state that has no trunks')
                    self.assertEqual(base['squared'], 0,
                        msg='a square wire is still on the map at ' + str(width)
                            + ': ' + str(base['worst']))

                    for cls, d, must_fire in ARMS:
                        await page.evaluate(ADD, {'cls': cls, 'd': d})
                        got = await page.evaluate(CENSUS)
                        await page.evaluate(DROP, {'cls': cls})
                        back = await page.evaluate(CENSUS)
                        want = base['squared'] + (1 if must_fire else 0)
                        self.assertEqual(got['squared'], want,
                            msg=cls + ' at ' + str(width) + ' was '
                                + ('NOT seen as square, so the census cannot detect the '
                                   'defect it is locking' if must_fire else
                                 'seen as square, so the fillet radius is too small to '
                                 'clear the heading threshold and the fix does not hold')
                                + ': ' + str(got['worst']))
                        self.assertEqual(back['squared'], base['squared'],
                            msg=cls + ' at ' + str(width) + ' left the count changed after removal')
                    self.assertEqual(errors, [])
                    await page.close()
            finally:
                await browser.close()

    async def test_matrix_canvas_is_centred_in_the_stage_when_it_fits(self):
        """Anh 2026-11-23, first complaint: 'sao cai nay khong de o giua ma lech o tren
        vay'. His screenshot is the HALF-OPEN state, and the box that was wrong is the
        canvas inside the stage, not the root inside the canvas.

        .mm-stage was display:block, so a width:max-content canvas shorter and narrower
        than the stage anchored to the top-left corner and every pixel of slack pooled
        below-right: measured at 390 the root stood 93px ABOVE the stage centre
        (above 0 / below 187). Two boxes therefore had to be fixed and both are locked:
        the root against the branch column (assert_root_centred, which replaced the
        107px pin this file used to defend) and the canvas against the stage.

        justify-content plus align-items: center was measured and REJECTED: on a scroll
        container it clips an overflowing child on both sides and the left half then
        cannot be reached, because scrollLeft cannot go negative (a forced 3000x2000
        canvas put its left edge at -816px against maxScrollLeft 815). margin:auto
        absorbs FREE space only, so it centres when there is slack and resolves to 0 on
        overflow -- which is why the pan, ArrowRight and fit locks still hold.

        Overflow is the documented exemption, not a bug to chase. root_probe.py measured
        the root's centre against the stage's centre state by state: closed 190/190 at
        both widths, half-open 239/239 and 226/226, after 'Vua khung' 284/284 and
        204/204. Exactly one state reads off-centre -- all-tiers-open, canvas 945px in a
        576px stage at 1440 (root 165px below centre) and 925px in 540px at 390 (147px).
        Centring an overflowing scroll box would push content above the origin, which
        setPanTotal clamps away ('negative pan does not exist') and which the fit lock
        forbids outright by asserting scrollTop == 0.
        """
        try:
            from playwright.async_api import async_playwright, expect
        except ImportError:
            self.skipTest('Playwright is not installed')
        CENTRE = """() => {
            const st = document.getElementById('mm-stage');
            const root = document.querySelector('.mm-root');
            const s = st.getBoundingClientRect(), r = root.getBoundingClientRect();
            return {
                stageCentre: (s.top + s.bottom) / 2,
                rootCentre: (r.top + r.bottom) / 2,
                fits: st.scrollHeight <= st.clientHeight + 1,
                scrollTop: st.scrollTop,
            };
        }"""
        async with async_playwright() as pw:
            if not Path(pw.chromium.executable_path).is_file():
                self.skipTest('Playwright Chromium is not installed')
            browser = await pw.chromium.launch()
            try:
                for w, hgt in [(1440, 900), (1000, 800), (390, 844)]:
                    page = await browser.new_page(viewport={'width': w, 'height': hgt},
                                                  reduced_motion='reduce')
                    await page.route('**/api/monitor/activity*',
                                     lambda route: route.fulfill(status=502, json={}))
                    await page.goto(str(self.client.make_url('/matrixtool')))
                    await page.wait_for_selector('#mm-canvas')
                    await page.wait_for_timeout(400)
                    closed = await page.evaluate(CENTRE)
                    self.assertTrue(closed['fits'],
                        msg='w' + str(w) + ' closed state should fit the stage: ' + str(closed))
                    self.assertLess(abs(closed['rootCentre'] - closed['stageCentre']), 1.5,
                        msg='canvas not centred in the stage while closed at w' + str(w)
                            + ': root ' + str(round(closed['rootCentre'], 1))
                            + ' vs stage ' + str(round(closed['stageCentre'], 1)))
                    # Half-open is the state Anh photographed.
                    await page.locator('.mm-tier[data-mm-tier="T4"]').press('Enter')
                    await page.wait_for_timeout(200)
                    await page.locator('#mm-groups-T4 .mm-cluster[data-mm-cluster="ui"] .mm-cluster-btn').press('Enter')
                    await page.wait_for_timeout(200)
                    await page.locator('button[data-mm-group="T4|desktop"]').press('Enter')
                    await page.wait_for_timeout(500)
                    half = await page.evaluate(CENTRE)
                    if half['fits']:
                        self.assertLess(abs(half['rootCentre'] - half['stageCentre']), 1.5,
                            msg='canvas not centred while half-open at w' + str(w) + ': ' + str(half))
                    # And after a fit: the control that puts the map back on screen.
                    await page.get_by_role('button', name='Vừa khung').click()
                    await page.wait_for_timeout(400)
                    fitted = await page.evaluate(CENTRE)
                    self.assertEqual(fitted['scrollTop'], 0,
                        msg='fit scrolled away from the origin at w' + str(w) + ': ' + str(fitted))
                    self.assertLess(abs(fitted['rootCentre'] - fitted['stageCentre']), 1.5,
                        msg='canvas not centred after fit at w' + str(w) + ': ' + str(fitted))
                    await expect(page.locator('#mm-canvas')).to_be_visible()
                    await page.close()
            finally:
                await browser.close()

    async def test_matrix_relation_layer_draws_chains_under_the_cards(self):
        """Anh 2026-11-24: 'phan mind maps can the hien tool chain va cac tool lien
        quan, auto chain'. The map drew the taxonomy and nothing else, while the three
        tables that answer what a tool leads to -- CHAINS (8 routes), SB_LINKS (30
        edges), SB_AUTO (16 auto-fires) -- shipped as PROSE only, in the detail panel
        and in relationPanel. This locks the svg.mm-relations layer that changed that.

        WHY AN EQUATION AND NOT A COUNT. rel_probe3.py measured write_fix at 12 drawn
        edges out of 21 hops the data claims, and 12 on its own is not a result: an
        anchor ladder that silently lost endpoints would print a plausible number too.
        So the test recomputes EXPECTED from the page's own three tables, independently
        of the painter, and demands edges + collapsed + unanchored == expected. The two
        skip reasons are counted apart on purpose. COLLAPSED is benign: both ends of a
        hop folded onto one ancestor card (9 of 21 for write_fix, all inside a closed
        group), the road still runs through that card and the surviving hop carries the
        step number. UNANCHORED is a real loss -- a named tool with no laid-out card
        anywhere -- and measures 0 across all 78 endpoints. The first draft merged the
        two into one skipped figure, which would let a regression hide inside the benign
        half.

        THE CONTROL IS THE POINT. A census reporting unanchored == 0 and an unchanged
        wire count is worthless until shown to fire, so one foreign path is injected
        into the relation layer: the equation must break, and the hierarchy census must
        NOT move. That second half is the isolation the layer was built for -- its own
        id, its own class, no .mm-wire-hit, and every prior census scoped to
        svg.mm-wires / #mm-branch-wires.

        WHAT ELSE IS PINNED, in the order of the defects each clause catches:
          1. Empty with nothing selected, and emptied along with the selection.
          2. BEFORE #mm-branch-wires in the DOM at a LOWER z-index, so an edge crossing
             a rail is occluded by the cards instead of slashing over them -- Anh's own
             ruling, 'Layer SVG rieng, NGUnder the bai'.
          3. pointer-events: none. A stroke that eats a click on a 170px chip is the
             regression :908 exists to catch; this layer must not become a second one.
          4. aria-hidden, because the same relation is spelled out in the detail panel
             and one edge does not need announcing twice.
          5. Geometry: no NaN, no zero-length path, every edge really stroked.
          6. Labels on chain and auto edges only. An inventory edge has no order, so
             numbering one would assert a sequence the source data refuses to claim.
          7. The toolbar switch switches, and the stamped count agrees with the DOM.
        """
        try:
            from playwright.async_api import async_playwright
        except ImportError:
            self.skipTest('Playwright is not installed')
        CENSUS = """() => {
          const L = document.getElementById('mm-relation-wires');
          const W = document.getElementById('mm-branch-wires');
          if (!L || !W) return { missing: true };
          const paths = [...L.querySelectorAll(':scope > path')];
          const tally = {}, byDepth = {};
          let nan = 0, zeroLen = 0, unpainted = 0, arrowless = 0, dangling = 0;
          for (const p of paths) {
            tally[p.dataset.mmRelKind] = (tally[p.dataset.mmRelKind] || 0) + 1;
            byDepth[p.dataset.mmRelDepth] = (byDepth[p.dataset.mmRelDepth] || 0) + 1;
            if (/NaN|undefined/.test(p.getAttribute('d') || '')) nan++;
            if (p.getTotalLength() < 2) zeroLen++;
            const cs = getComputedStyle(p);
            if (cs.stroke === 'none' || cs.stroke === 'rgba(0, 0, 0, 0)') unpainted++;
            const mk = p.getAttribute('marker-end') || cs.markerEnd || '';
            const hash = mk.indexOf('#');
            const mid = hash < 0 ? '' : mk.slice(hash + 1).split('"')[0].split(')')[0];
            if (!mid) arrowless++;
            else if (!document.getElementById(mid)) dangling++;
          }
          const sel = (document.querySelector('#mm-detail strong') || {}).textContent || null;
          let expected = 0;
          if (sel) {
            for (const c of CHAINS) if (c.tools.includes(sel)) expected += c.tools.length - 1;
            const seen = new Set();
            for (const e of SB_LINKS) {
              if (e.f !== sel && e.t !== sel) continue;
              const k = JSON.stringify([e.k, ...(e.k === 'alias-shared-handler'
                ? [e.f, e.t].sort() : [e.f, e.t])]);
              if (!seen.has(k)) { seen.add(k); expected++; }
            }
            for (const e of SB_AUTO) {
              if (e.f !== sel && e.t !== sel) continue;
              const k = JSON.stringify(['auto-chain', e.f, e.t]);
              if (!seen.has(k)) { seen.add(k); expected++; }
            }
          }
          const ls = getComputedStyle(L), ws = getComputedStyle(W);
          const btn = document.querySelector('[data-mm-view-action="relations"]');
          return {
            edges: paths.length, labels: L.querySelectorAll(':scope > text').length, expected,
            tally, byDepth, nan, zeroLen, unpainted, arrowless, dangling,
            stamp: L.dataset.mmRelationEdges, collapsed: L.dataset.mmRelationCollapsed,
            unanchored: L.dataset.mmRelationUnanchored,
            skipped: L.dataset.mmRelationSkipped,
            wires: W.querySelectorAll('path:not(.mm-wire-hit):not(.mm-trace)').length,
            hits: W.querySelectorAll('.mm-wire-hit').length,
            pointer: ls.pointerEvents, lz: Number(ls.zIndex), wz: Number(ws.zIndex),
            relFirst: !!(L.compareDocumentPosition(W) & Node.DOCUMENT_POSITION_FOLLOWING),
            relHidden: L.getAttribute('aria-hidden'),
            pressed: btn ? btn.getAttribute('aria-pressed') : null,
          };
        }"""
        INJECT = """() => {
          const p = document.createElementNS('http://www.w3.org/2000/svg', 'path');
          p.setAttribute('d', 'M0 0 L12 12');
          p.dataset.mmRelKind = 'injected';
          document.getElementById('mm-relation-wires').appendChild(p);
          return 1;
        }"""
        BAD_MARKER = """() => {
          const p = document.createElementNS('http://www.w3.org/2000/svg', 'path');
          p.setAttribute('d', 'M0 0 L12 12');
          p.setAttribute('marker-end', 'url(#mm-rel-arrow-not-a-real-id)');
          p.dataset.mmRelKind = 'injected';
          document.getElementById('mm-relation-wires').appendChild(p);
          return 1;
        }"""
        REAL_MARKER_UNQUOTED = """() => {
          const p = document.createElementNS('http://www.w3.org/2000/svg', 'path');
          p.setAttribute('d', 'M0 0 L12 12');
          p.setAttribute('marker-end', 'url(#mm-rel-arrow-chain)');
          p.dataset.mmRelKind = 'injected';
          document.getElementById('mm-relation-wires').appendChild(p);
          return 1;
        }"""
        async with async_playwright() as pw:
            if not Path(pw.chromium.executable_path).is_file():
                self.skipTest('Playwright Chromium is not installed')
            browser = await pw.chromium.launch()
            try:
                for w, hgt in [(1440, 900), (390, 844)]:
                    page = await browser.new_page(viewport={'width': w, 'height': hgt},
                                                  reduced_motion='reduce')
                    await page.route('**/api/monitor/activity*',
                                     lambda route: route.fulfill(status=502, json={}))
                    await page.goto(str(self.client.make_url('/matrixtool')))
                    await page.wait_for_selector('#mm-relation-wires')
                    await page.wait_for_timeout(400)
                    tag = 'w' + str(w)
                    base = await page.evaluate(CENSUS)
                    self.assertFalse(base.get('missing'), msg=tag + ': relation layer absent')
                    # Clauses 1 to 4, read before anything is selected.
                    self.assertEqual(base['edges'], 0,
                        msg=tag + ': the layer painted ' + str(base['edges'])
                            + ' edges with nothing selected')
                    self.assertEqual(base['expected'], 0, msg=tag + ': baseline expectation')
                    # Same reasoning as the deselect check, one page-load earlier: nothing
                    # has been selected yet, so no counter may carry a number. Measured
                    # 2026-11-24 all three read '0' here, because the initial paint runs
                    # the guard in drawMapRelations and that guard calls the clear.
                    for key, attr in (('stamp', 'mmRelationEdges'),
                                       ('collapsed', 'mmRelationCollapsed'),
                                       ('unanchored', 'mmRelationUnanchored')):
                        self.assertEqual(base[key], '0',
                            msg=tag + ': dataset.' + attr + ' reads ' + str(base[key])
                                + ' before any tool was selected')
                    self.assertEqual(base['relHidden'], 'true', msg=tag + ': aria-hidden')
                    self.assertTrue(base['relFirst'],
                        msg=tag + ': relation layer must precede #mm-branch-wires in the DOM')
                    self.assertLess(base['lz'], base['wz'],
                        msg=tag + ': relation z-index ' + str(base['lz'])
                            + ' is not beneath the wire layer ' + str(base['wz']))
                    self.assertEqual(base['pointer'], 'none', msg=tag + ': layer is inert')
                    for tool, minChain in (('write_fix', 8), ('search_code', 5),
                                           ('desktop_screenshot', 5)):
                        await page.locator('#mm-search').fill(tool)
                        await page.wait_for_timeout(250)
                        await page.locator('#mm-hits [data-mm-hit="0"]').click()
                        await page.wait_for_timeout(450)
                        m = await page.evaluate(CENSUS)
                        where = tag + ' ' + tool
                        # THE PAINTER OWNS THESE THREE ATTRIBUTES. A missing one is not a
                        # counting disagreement -- it is drawMapRelations never having run
                        # for this selection. Checked first because the mutation probe
                        # caught the bare int() below failing with TypeError: int(None):
                        # the two arms that removed the painter call and unarmed the layer
                        # both went red for the right reason while naming the wrong
                        # culprit, which sends the next reader to the census instead of
                        # to the code that stopped painting.
                        for key, attr in (('stamp', 'mmRelationEdges'),
                                          ('collapsed', 'mmRelationCollapsed'),
                                          ('unanchored', 'mmRelationUnanchored')):
                            self.assertIsNotNone(m[key],
                                msg=where + ': dataset.' + attr + ' is absent, so '
                                    + 'drawMapRelations never ran for this selection')
                        self.assertEqual(m['edges'], int(m['stamp']),
                            msg=where + ': stamped count ' + m['stamp']
                                + ' disagrees with ' + str(m['edges']) + ' paths')
                        accounted = m['edges'] + int(m['collapsed']) + int(m['unanchored'])
                        self.assertEqual(accounted, m['expected'],
                            msg=where + ': drawn ' + str(m['edges']) + ' + collapsed '
                                + m['collapsed'] + ' + unanchored ' + m['unanchored']
                                + ' does not account for the ' + str(m['expected'])
                                + ' hops the tables claim')
                        self.assertEqual(int(m['unanchored']), 0,
                            msg=where + ': ' + m['unanchored'] + ' endpoint(s) had no card '
                                'anywhere, so a relation vanished from the map')
                        self.assertGreaterEqual(m['tally'].get('chain', 0), minChain,
                            msg=where + ': chain hops fell to ' + str(m['tally'].get('chain', 0))
                                + ', below the ' + str(minChain) + ' this tool owes')
                        self.assertEqual(m['nan'], 0, msg=where + ': NaN in a path')
                        self.assertEqual(m['zeroLen'], 0,
                            msg=where + ': a zero-length edge reads as a chain that skips '
                                'a step, which is the one thing a chain must not do')
                        self.assertEqual(m['unpainted'], 0, msg=where + ': unstroked edge')
                        # THE ARROW IS THE DIRECTED HALF OF A DIRECTED EDGE. A typo in a
                        # marker id, or a dropped marker-end, leaves the stroke painted and
                        # the arrow simply absent: Chromium draws nothing and reports
                        # nothing, so 'unpainted' above stays 0 and the chain reads as
                        # undirected. Measured 2026-11-24 -- write_fix carries 8 chain + 2
                        # link + 2 auto markers, search_code 5 + 7 + 1, no dangling id.
                        self.assertEqual(m['arrowless'], 0,
                            msg=where + ': ' + str(m['arrowless']) + ' edge(s) carry no '
                                + 'marker-end, so the direction of that hop is invisible')
                        self.assertEqual(m['dangling'], 0,
                            msg=where + ': ' + str(m['dangling']) + ' edge(s) name a marker '
                                + 'id that is not in the document')
                        self.assertEqual(m['labels'],
                            m['tally'].get('chain', 0) + m['tally'].get('auto-chain', 0),
                            msg=where + ': ' + str(m['labels']) + ' labels for '
                                + str(m['tally'].get('chain', 0)) + ' chain and '
                                + str(m['tally'].get('auto-chain', 0)) + ' auto edges')
                        self.assertEqual(m['hits'], m['wires'],
                            msg=where + ': hit/wire parity broken by this layer')
                        # CONTROL: the injected edge must break the equation while the
                        # hierarchy census stays exactly where it was.
                        await page.evaluate(INJECT)
                        inj = await page.evaluate(CENSUS)
                        self.assertEqual(inj['edges'], m['edges'] + 1,
                            msg=where + ': the census did not see the injected path, so its '
                                'zeroes prove nothing -- it cannot fire')
                        # POSITIVE CONTROL FOR THE MARKER CENSUS. The injected edge carries
                        # no marker-end by construction, so arrowless must rise by exactly
                        # one. If it does not, that counter is reading nothing at all and
                        # the two zeros asserted above it are decoration, not a check.
                        self.assertEqual(inj['arrowless'], m['arrowless'] + 1,
                            msg=where + ': an unmarked edge was injected and arrowless stayed '
                                + str(inj['arrowless']) + ', so the marker census cannot fire')
                        self.assertNotEqual(
                            inj['edges'] + int(inj['collapsed']) + int(inj['unanchored']),
                            inj['expected'],
                            msg=where + ': the equation survived an injected edge')
                        self.assertEqual(inj['wires'], m['wires'],
                            msg=where + ': the hierarchy wire count moved because of a '
                                'relation-layer injection')
                        self.assertEqual(inj['hits'], m['hits'], msg=where + ': hit count')
                        # CONTROL FOR THE DANGLING COUNTER, which the injection above cannot
                        # serve by construction -- an unmarked edge returns in the arrowless
                        # branch and never reaches the id lookup, so a bogus marker id would
                        # go unexercised and that zero would be unchecked. The three real ids
                        # are mm-rel-arrow-{chain,auto,link} (matrix.html:718-720), applied
                        # from CSS at :349-360 rather than as attributes, which is exactly why
                        # the census reads computed style as well as the attribute.
                        await page.evaluate(BAD_MARKER)
                        inj2 = await page.evaluate(CENSUS)
                        self.assertEqual(inj2['dangling'], m['dangling'] + 1,
                            msg=where + ': an edge naming a non-existent marker id left '
                                + 'dangling at ' + str(inj2['dangling']) + ', so the zero '
                                + 'above is a reading, not a check')
                        self.assertEqual(inj2['arrowless'], inj['arrowless'],
                            msg=where + ': a marked edge was also counted as arrowless, so '
                                + 'the two branches are not exclusive')
                        # THE EXTRACTION ITSELF, GUARDED. A REAL id in the unquoted attribute
                        # form must resolve, so dangling must NOT rise again. Delete the
                        # split(')') cut above and this fires at m+2 instead of m+1: the
                        # extraction hands back 'mm-rel-arrow-chain)', which finds no element.
                        # Neither control can see that edit -- a BOGUS id fails the lookup with
                        # or without a trailing paren, so both counters move identically either
                        # way. This is the only arm that distinguishes the two extractions, and
                        # it is not hypothetical: the shipped relation layer sets markers from
                        # CSS quoted (matrix.html:349-360) while the hierarchy wire layer uses
                        # the unquoted ATTRIBUTE form (matrix.js:1408, :1809), so a sibling
                        # layer already speaks the form that would false-positive.
                        await page.evaluate(REAL_MARKER_UNQUOTED)
                        inj3 = await page.evaluate(CENSUS)
                        self.assertEqual(inj3['dangling'], inj2['dangling'],
                            msg=where + ': an unquoted marker-end naming a REAL id read as '
                                + 'dangling (' + str(inj3['dangling']) + ' vs '
                                + str(inj2['dangling']) + '), so the id extraction kept a '
                                + 'trailing paren and would false-positive on every edge')
                        self.assertEqual(inj3['arrowless'], inj2['arrowless'],
                            msg=where + ': a marked edge counted as arrowless')
                        # Clause 7. Restoring repaints the layer, so the injected path is
                        # gone by construction rather than by a cleanup to trust.
                        await page.locator('[data-mm-view-action="relations"]').click()
                        await page.wait_for_timeout(300)
                        off = await page.evaluate(CENSUS)
                        self.assertEqual(off['edges'], 0,
                            msg=where + ': switch off left ' + str(off['edges'])
                                + ' edges on the map')
                        self.assertEqual(off['pressed'], 'false', msg=where + ': aria-pressed')
                        await page.locator('[data-mm-view-action="relations"]').click()
                        await page.wait_for_timeout(300)
                        on = await page.evaluate(CENSUS)
                        self.assertEqual(on['edges'], m['edges'],
                            msg=where + ': switch on repainted ' + str(on['edges'])
                                + ' edges instead of ' + str(m['edges']))
                        self.assertEqual(on['pressed'], 'true', msg=where + ': aria-pressed')
                        self.assertEqual(int(on['unanchored']), 0, msg=where + ': after retoggle')
                    # The roads to a tool nobody picked have to go with the selection.
                    await page.locator('#mm-detail [data-mm-close]').click()
                    await page.wait_for_timeout(300)
                    gone = await page.evaluate(CENSUS)
                    self.assertEqual(gone['edges'], 0,
                        msg=tag + ': ' + str(gone['edges']) + ' ghost edges outlived the '
                            'selection')
                    # THE STAMPS OWE THE SAME RESET AS THE PATHS. This clause was missing
                    # when clearMapRelationLayer shipped resetting only mmRelationEdges and
                    # mmRelationSkipped: the layer went visually empty while still
                    # advertising collapsed=9 for a deselected tool, and the whole lock
                    # stayed green, because its only equation runs with something
                    # selected. An empty map must read 0/0/0, not 0/9/0.
                    for key, attr in (('collapsed', 'mmRelationCollapsed'),
                                       ('unanchored', 'mmRelationUnanchored'),
                                       ('skipped', 'mmRelationSkipped')):
                        # Presence BEFORE value: int(gone[key] or 0) would score a missing
                        # attribute as 0 and pass, and a stamp the clear stopped writing is
                        # the very failure this clause exists to catch.
                        self.assertIsNotNone(gone[key],
                            msg=tag + ': after deselect dataset.' + attr + ' is gone, so the '
                                + 'clear no longer stamps what it just emptied')
                        self.assertEqual(int(gone[key]), 0,
                            msg=tag + ': after deselect, dataset.' + attr + ' still reads '
                                + str(gone[key]) + ' on a layer holding no paths')
                    await page.close()
            finally:
                await browser.close()

    def test_sb_auto_rank_is_a_group_key_not_a_registry_row(self):
        """The digit on an auto-chain edge is a snapshot-local GROUP KEY, and this lock
        stops anyone reading it as a row of AUTO_CHAIN_RULES again.

        Why it exists. Two source comments and three user-visible labels called the digit
        a "rule rank", which is an instruction: go look up registry row N. Measured
        2026-11-24 against the live registry (code_tools.py:3297, 15 rows) that lookup
        returns the wrong thing every time -- rank 2 is cbm_dead_code -> cbm_index while
        row 2 is ca_hint_commit -- and ranks 3, 7, 8 and 11 name tools the registry never
        lists as fields. The note this grew out of also claimed the table had 20 rows; it
        has 15, and that figure was never measured, which is the reason a lock is being
        written here instead of a correction to a comment.

        WHAT THE DIGIT IS. Sixteen edges carry ten distinct ranks and the map rank ->
        trigger tool is a bijection: no rank holds two triggers, no trigger hides behind
        two ranks. Ranks 9 and 10 do not occur, which is why that density is recorded in
        this docstring but NOT asserted -- a future rank 9 would be a harmless addition,
        and a lock that fires on harmless change is a lock that gets deleted. The
        load-bearing facts are the bijection and the disjointness below.

        WHY THE CONTROLS ARE LOUD. Every zero in this test is the trap this campaign has
        fallen into fourteen times: a regex matching nothing reports a clean pass. So the
        parser is measured against the literal text it read (the count of rank keys on
        the SB_AUTO line must equal the row count), both carry floors, at least ten ranks
        must fall INSIDE the registry so the mismatch loop compares two real things
        instead of an empty set, and the prose assertions demand the digit is STILL
        rendered after the wording changed -- deleting the label would otherwise satisfy
        the notIn for free.
        """
        import re
        import sys
        root = Path(__file__).resolve().parents[1]
        if str(root) not in sys.path:
            sys.path.insert(0, str(root))
        import code_tools as ct
        src = (root / "orchestrator" / "web_ui" / "matrix.js").read_text(encoding="utf-8")
        line = next((ln for ln in src.splitlines() if ln.startswith("const SB_AUTO = ")), None)
        self.assertIsNotNone(line,
            msg="no top-level const SB_AUTO line in matrix.js; this lock reads the table "
                "where it lives, so move the lock if the table moves")
        rows = [{"f": a, "t": b, "r": int(c)} for a, b, c in
                re.findall(r'"f":"([^"]+)","t":"([^"]+)","r":([0-9]+)', line)]
        self.assertGreaterEqual(len(rows), 16,
            msg="parsed only " + str(len(rows)) + " SB_AUTO rows from a table shipped with "
                "16, so the row shape changed under this regex and every count below it "
                "would be vacuous")
        self.assertEqual(line.count('"r":'), len(rows),
            msg="the SB_AUTO line holds " + str(line.count('"r":')) + " rank keys but the "
                "parser returned " + str(len(rows)) + " rows: a partial read, not a pass")
        by_rank = {}
        for row in rows:
            by_rank.setdefault(row["r"], set()).add(row["f"])
        split = {r: sorted(s) for r, s in by_rank.items() if len(s) > 1}
        self.assertEqual(split, {},
            msg="rank(s) " + str(split) + " now carry more than one trigger tool, so the "
                "digit is no longer a one-to-one group key and the label needs re-thinking")
        ranks = sorted(by_rank)
        fields = tuple(rule[0] for rule in ct.AUTO_CHAIN_RULES)
        self.assertGreaterEqual(len(fields), 15,
            msg="AUTO_CHAIN_RULES has " + str(len(fields)) + " rows; the 15 measured "
                "2026-11-24 is what makes rank 2 and row 2 comparable at all")
        endpoints = {row["f"] for row in rows} | {row["t"] for row in rows}
        self.assertGreaterEqual(len(endpoints), 19,
            msg="only " + str(len(endpoints)) + " distinct endpoint tools, expected 19 -- "
                "the table shrank and the disjointness below would pass on nothing")
        both = sorted(endpoints & set(fields))
        self.assertEqual(both, [],
            msg="SB_AUTO endpoint(s) " + str(both) + " is also an AUTO_CHAIN_RULES field "
                "name, so a rank-keyed lookup could coincidentally land on something -- "
                "re-measure the label before letting that happen")
        in_range = [r for r in ranks if r <= len(fields)]
        self.assertGreaterEqual(len(in_range), 10,
            msg="only " + str(len(in_range)) + " of " + str(len(ranks)) + " ranks index a "
                "real registry row, so the mismatch loop could pass on almost nothing")
        for r in in_range:
            ends = ({row["f"] for row in rows if row["r"] == r}
                    | {row["t"] for row in rows if row["r"] == r})
            self.assertNotIn(fields[r - 1], ends,
                msg="rank " + str(r) + " and registry row " + str(r) + " (" + fields[r - 1]
                    + ") now agree. If that reconciliation is deliberate the label can be "
                    "revisited with the evidence written here; otherwise the digit is still "
                    "not a row number and still must not be called one")
        # THE PROSE WAS THE DEFECT, not the data: a viewer was told to look up a row.
        self.assertNotIn("rule ' + a.r", src,
            msg="a radial auto-chain label still spells the group key as a rule rank; the "
                "registry lookup it invites returns the wrong field")
        self.assertNotIn("rule ' + e.r", src,
            msg="the relation panel still spells the group key as a rule rank")
        self.assertIn("' + a.r", src,
            msg="the digit is no longer rendered on a radial auto-chain wire, so the "
                "notIn above would pass by deletion instead of by correct wording")
        self.assertIn("' + e.r", src,
            msg="the digit is no longer rendered in the relation panel, same free pass")

    # ------------------------------------------------------------------
    # IMPROVE CAMPAIGN (frozen plan 2026-11-24). T-B1..T-B7, T-A1..T-A4,
    # T-C1..T-C5. T-A5 is the EXISTING wheel-anchor lock above (never edited).
    # Behavioral tests were run against baseline code first and FAILED
    # (pytest_improve_red.txt); guard tests (T-B4, T-B5, T-A2) pass on
    # baseline by design and protect against over-broad regressions.
    # ------------------------------------------------------------------

    async def test_matrix_empty_canvas_dblclick_resets_full_state(self):
        """T-B1: a bare-canvas double-click returns the WHOLE map to page-load state
        (detail, chain panel, focus/lit, search, every branch, relations, zoom,
        pan) while the glide shield holds the document scroll still."""
        try:
            from playwright.async_api import async_playwright, expect
        except ImportError:
            self.skipTest('Playwright is not installed')
        EMPTY = """() => {
            const st = document.getElementById('mm-stage');
            const cv = document.getElementById('mm-canvas');
            const s = st.getBoundingClientRect(), r = cv.getBoundingClientRect();
            const left = Math.max(s.left, r.left) + 3, right = Math.min(s.right, r.right) - 3;
            const top = Math.max(s.top, r.top) + 3, bottom = Math.min(s.bottom, r.bottom) - 3;
            for (let y = top; y < bottom; y += 5) {
                for (let x = left; x < right; x += 5) {
                    const el = document.elementFromPoint(x, y);
                    if (!el || !el.closest('#mm-canvas')) continue;
                    if (el.closest('button, a, .mm-wires, .mm-relations')) continue;
                    return {x: x, y: y};
                }
            }
            return null;
        }"""
        STATE = """() => {
            const cv = document.getElementById('mm-canvas');
            const st = document.getElementById('mm-stage');
            const q = (s) => cv.querySelectorAll(s).length;
            const open = (list) => list.filter(b => b.getAttribute('aria-expanded') === 'true').length;
            return {
                detailHidden: document.getElementById('mm-detail').hidden,
                chainPanels: document.querySelectorAll('.mm-chain-panel').length,
                ariaCurrent: q('[aria-current]'),
                hasFocus: cv.classList.contains('mm-has-focus'),
                wireHover: cv.classList.contains('mm-wire-hover'),
                lit: q('[data-mm-lit]'), wireLit: q('.mm-wire-lit'), mates: q('.mm-chain-mate'),
                search: document.getElementById('mm-search').value,
                clearHidden: document.getElementById('mm-search-clear').hidden,
                hitsHidden: document.getElementById('mm-hits').hidden,
                tiersOpen: open([...cv.querySelectorAll('.mm-tier')]),
                panelsOpen: [...document.querySelectorAll('[id^="mm-groups-"]')].filter(p => !p.hidden).length,
                clustersOpen: open([...cv.querySelectorAll('.mm-cluster-btn')]),
                groupsOpen: open([...cv.querySelectorAll('[data-mm-group]')]),
                bucketsOpen: [...cv.querySelectorAll('.mm-bucket-body')].filter(b => !b.hidden).length,
                listsOpen: [...cv.querySelectorAll('.mm-tools')].filter(l => !l.hidden).length,
                relPressed: document.querySelector('[data-mm-view-action="relations"]').getAttribute('aria-pressed'),
                relEdges: document.getElementById('mm-relation-wires').dataset.mmRelationEdges,
                zoomLabel: document.getElementById('mm-zoom-level').textContent,
                zoom: parseFloat(cv.style.zoom || '1'),
                sl: st.scrollLeft, top: st.scrollTop, transform: cv.style.transform || '',
                spacer: !!document.getElementById('mm-glide-spacer')
            };
        }"""
        async with async_playwright() as pw:
            if not Path(pw.chromium.executable_path).is_file():
                self.skipTest('Playwright Chromium is not installed')
            browser = await pw.chromium.launch()
            try:
                page = await browser.new_page(viewport={'width': 1440, 'height': 900})
                errors = []
                page.on('pageerror', lambda error: errors.append(str(error)))
                await page.route('**/api/monitor/activity*', lambda route: route.fulfill(status=502, json={}))
                await page.goto(str(self.client.make_url('/matrixtool')))
                await page.wait_for_selector('#mm-canvas')
                await page.wait_for_timeout(400)
                stage = page.locator('#mm-stage')
                await page.locator('.mm-tier[data-mm-tier="T1"]').click()
                await page.locator('#mm-groups-T1 .mm-cluster[data-mm-cluster="ops"] .mm-cluster-btn').click()
                leaf_group = page.locator('button[data-mm-group="T1|ops"]')
                await leaf_group.click()
                leaf_list = page.locator('#' + await leaf_group.get_attribute('aria-controls'))
                leaf_initial_shown = await leaf_list.get_attribute('data-shown')
                await leaf_list.locator('.mm-more').click()
                leaf_advanced_shown = await leaf_list.get_attribute('data-shown')
                self.assertNotEqual(leaf_advanced_shown, leaf_initial_shown,
                    'setup did not advance the leaf list beyond its initial page')
                self.assertGreater(int(leaf_advanced_shown), 0,
                    'setup did not produce a positive leaf pagination counter')
                await page.locator('.mm-tier[data-mm-tier="T4"]').click()
                await expect(page.locator('#mm-groups-T4')).to_be_visible()
                await page.locator('#mm-groups-T4 .mm-cluster[data-mm-cluster="ui"] .mm-cluster-btn').click()
                await page.locator('button[data-mm-group="T4|desktop"]').click()
                await page.wait_for_timeout(600)
                self.assertGreater(await page.locator('#mm-groups-T4 .mm-bucket-body:not([hidden])').count(), 0,
                    'the desktop rail grew no open bucket, so the reset has no bucket state to clear')
                # Search query active BEFORE selecting (updateLookup closes the panel).
                await page.locator('#mm-search').fill('desktop')
                await page.wait_for_timeout(200)
                # Close the lookup dropdown (keep the query): it overlays the rail
                # and would otherwise intercept the chip click for the full timeout.
                await page.locator('#mm-search').press('Escape')
                await page.locator('#mm-groups-T4 [data-mm-tool]').first.click()
                await expect(page.locator('#mm-detail')).to_be_visible()
                self.assertEqual(await page.locator('#mm-canvas > .mm-chain-panel').count(), 1)
                await page.locator('.mm-tier[data-mm-tier="T1"]').hover()
                await page.wait_for_timeout(150)
                self.assertEqual(await page.locator('[data-mm-view-action="relations"]').get_attribute('aria-pressed'), 'true')
                box = await stage.bounding_box()
                await page.mouse.move(box['x'] + box['width'] / 2, box['y'] + box['height'] / 2)
                await page.mouse.wheel(0, -100)
                await page.wait_for_timeout(300)
                pt = await page.evaluate(EMPTY)
                self.assertIsNotNone(pt, 'no bare canvas point found for the drag')
                await page.mouse.move(pt['x'], pt['y'])
                await page.mouse.down()
                await page.mouse.move(pt['x'] - 80, pt['y'])
                await page.mouse.up()
                await page.wait_for_timeout(300)
                await page.evaluate('window.scrollTo(0, 150)')
                before = await page.evaluate(STATE)
                self.assertNotEqual(before['zoomLabel'], '100%', 'setup left zoom at 100%')
                self.assertTrue(before['sl'] > 0 or before['transform'] != '', 'setup left no pan to reset')
                self.assertFalse(before['detailHidden'], 'setup left the detail panel closed')
                pt = await page.evaluate(EMPTY)
                self.assertIsNotNone(pt, 'no bare canvas point found to click')
                await page.mouse.dblclick(pt['x'], pt['y'])
                y_now = await page.evaluate('window.scrollY')
                self.assertEqual(y_now, 150, 'the collapse clamped the page scroll instead of shielding it')
                await page.wait_for_timeout(1500)
                a = await page.evaluate(STATE)
                self.assertTrue(a['detailHidden'], 'detail panel outlived the empty-click reset')
                self.assertEqual(a['chainPanels'], 0, 'chain panel outlived the reset')
                self.assertEqual(a['ariaCurrent'], 0, 'aria-current outlived the reset')
                self.assertFalse(a['hasFocus'], 'mm-has-focus outlived the reset')
                self.assertFalse(a['wireHover'], 'mm-wire-hover outlived the reset')
                self.assertEqual(a['lit'], 0, 'data-mm-lit outlived the reset')
                self.assertEqual(a['wireLit'], 0, 'mm-wire-lit outlived the reset')
                self.assertEqual(a['mates'], 0, 'mm-chain-mate outlived the reset')
                self.assertEqual(a['search'], '', 'search value outlived the reset')
                self.assertTrue(a['clearHidden'], 'search clear button still shown')
                self.assertTrue(a['hitsHidden'], 'lookup dropdown still open')
                self.assertEqual(a['tiersOpen'], 0, 'a tier stayed open')
                self.assertEqual(a['panelsOpen'], 0, 'a branch panel stayed visible')
                self.assertEqual(a['clustersOpen'], 0, 'a cluster stayed open')
                self.assertEqual(a['groupsOpen'], 0, 'a group stayed open')
                self.assertEqual(a['bucketsOpen'], 0, 'a bucket stayed open')
                self.assertEqual(a['listsOpen'], 0, 'a leaf list stayed visible')
                self.assertEqual(a['relPressed'], 'true', 'relations aria-pressed not restored to load-state true')
                self.assertEqual(a['relEdges'], '0', 'relation layer not cleared')
                self.assertEqual(a['zoomLabel'], '100%', 'zoom label not reset to 100%')
                self.assertEqual(a['zoom'], 1, 'zoom not reset to 1')
                self.assertEqual(a['sl'], 0, 'scrollLeft not reset')
                self.assertEqual(a['top'], 0, 'scrollTop not reset')
                self.assertEqual(a['transform'], '', 'pan translate not reset')
                self.assertFalse(a['spacer'], 'glide spacer left mounted')
                self.assertEqual(await leaf_list.get_attribute('data-shown'), '0',
                    'empty reset did not clear the leaf pagination counter')
                await page.locator('.mm-tier[data-mm-tier="T1"]').click()
                await page.locator('#mm-groups-T1 .mm-cluster[data-mm-cluster="ops"] .mm-cluster-btn').click()
                await leaf_group.click()
                await expect(leaf_list).to_be_visible()
                reopened_shown = await leaf_list.get_attribute('data-shown')
                self.assertEqual(reopened_shown, leaf_initial_shown,
                    'reopened leaf list did not return to its initial page after reset')
                self.assertEqual(errors, [])
                await page.close()
            finally:
                await browser.close()

    async def test_matrix_empty_stage_margin_dblclick_resets(self):
        """T-B2: a double-click on the stage MARGIN (canvas narrower than stage,
        target === mapStage) resets to initial state like a bare-canvas dblclick."""
        try:
            from playwright.async_api import async_playwright
        except ImportError:
            self.skipTest('Playwright is not installed')
        EMPTY = """() => {
            const st = document.getElementById('mm-stage');
            const cv = document.getElementById('mm-canvas');
            const s = st.getBoundingClientRect(), r = cv.getBoundingClientRect();
            const left = Math.max(s.left, r.left) + 3, right = Math.min(s.right, r.right) - 3;
            const top = Math.max(s.top, r.top) + 3, bottom = Math.min(s.bottom, r.bottom) - 3;
            for (let y = top; y < bottom; y += 5) {
                for (let x = left; x < right; x += 5) {
                    const el = document.elementFromPoint(x, y);
                    if (!el || !el.closest('#mm-canvas')) continue;
                    if (el.closest('button, a, .mm-wires, .mm-relations')) continue;
                    return {x: x, y: y};
                }
            }
            return null;
        }"""
        SNAP = """() => {
            const st = document.getElementById('mm-stage');
            const cv = document.getElementById('mm-canvas');
            return {zoomLabel: document.getElementById('mm-zoom-level').textContent,
                    zoom: parseFloat(cv.style.zoom || '1'), sl: st.scrollLeft, top: st.scrollTop,
                    transform: cv.style.transform || '',
                    tiersOpen: [...cv.querySelectorAll('.mm-tier')].filter(b => b.getAttribute('aria-expanded') === 'true').length};
        }"""
        async with async_playwright() as pw:
            if not Path(pw.chromium.executable_path).is_file():
                self.skipTest('Playwright Chromium is not installed')
            browser = await pw.chromium.launch()
            try:
                page = await browser.new_page(viewport={'width': 1440, 'height': 900}, reduced_motion='reduce')
                errors = []
                page.on('pageerror', lambda error: errors.append(str(error)))
                await page.route('**/api/monitor/activity*', lambda route: route.fulfill(status=502, json={}))
                await page.goto(str(self.client.make_url('/matrixtool')))
                await page.wait_for_selector('#mm-canvas')
                await page.wait_for_timeout(400)
                stage = page.locator('#mm-stage')
                margin = await page.evaluate("""() => {
                    const s = document.getElementById('mm-stage').getBoundingClientRect();
                    const r = document.getElementById('mm-canvas').getBoundingClientRect();
                    return {left: r.left - s.left, right: s.right - r.right};
                }""")
                self.assertGreater(margin['right'], 40, 'the closed canvas left no stage margin to click')
                cvbox = await page.locator('#mm-canvas').bounding_box()
                await page.mouse.move(cvbox['x'] + cvbox['width'] / 2, cvbox['y'] + cvbox['height'] / 2)
                await page.mouse.wheel(0, -100)
                await page.wait_for_timeout(250)
                pt = await page.evaluate(EMPTY)
                self.assertIsNotNone(pt)
                await page.mouse.move(pt['x'], pt['y'])
                await page.mouse.down()
                await page.mouse.move(pt['x'] - 60, pt['y'])
                await page.mouse.up()
                await page.wait_for_timeout(300)
                before = await page.evaluate(SNAP)
                self.assertNotEqual(before['zoomLabel'], '100%')
                self.assertTrue(before['sl'] > 0 or before['transform'] != '', 'drag left no pan to reset')
                sbox = await stage.bounding_box()
                mx = sbox['x'] + sbox['width'] - 25
                my = sbox['y'] + sbox['height'] / 2
                target = await page.evaluate('(p) => { const el = document.elementFromPoint(p.x, p.y); return el ? (el.id || el.className) : null; }', {'x': mx, 'y': my})
                self.assertEqual(target, 'mm-stage', 'the margin probe did not land on the stage itself: ' + str(target))
                await page.mouse.dblclick(mx, my)
                await page.wait_for_timeout(300)
                a = await page.evaluate(SNAP)
                self.assertEqual(a['zoomLabel'], '100%', 'margin click did not reset zoom')
                self.assertEqual(a['zoom'], 1)
                self.assertEqual(a['sl'], 0)
                self.assertEqual(a['top'], 0)
                self.assertEqual(a['transform'], '', 'margin click left a pan translate')
                self.assertEqual(a['tiersOpen'], 0)
                self.assertEqual(errors, [])
                await page.close()
            finally:
                await browser.close()

    async def test_matrix_drag_squash_guards_the_empty_click_reset(self):
        """T-B3: a >250ms drag never resets; release on a node squashes its click."""
        try:
            from playwright.async_api import async_playwright
        except ImportError:
            self.skipTest('Playwright is not installed')
        EMPTY = """() => {
            const st = document.getElementById('mm-stage');
            const cv = document.getElementById('mm-canvas');
            const s = st.getBoundingClientRect(), r = cv.getBoundingClientRect();
            const left = Math.max(s.left, r.left) + 3, right = Math.min(s.right, r.right) - 3;
            const top = Math.max(s.top, r.top) + 3, bottom = Math.min(s.bottom, r.bottom) - 3;
            for (let y = top; y < bottom; y += 5) {
                for (let x = left; x < right; x += 5) {
                    const el = document.elementFromPoint(x, y);
                    if (!el || !el.closest('#mm-canvas')) continue;
                    if (el.closest('button, a, .mm-wires, .mm-relations')) continue;
                    return {x: x, y: y};
                }
            }
            return null;
        }"""
        SNAP = """() => ({
            zoom: document.getElementById('mm-zoom-level').textContent,
            tier: document.querySelector('.mm-tier[data-mm-tier="T2"]').getAttribute('aria-expanded')
        })"""
        async with async_playwright() as pw:
            if not Path(pw.chromium.executable_path).is_file():
                self.skipTest('Playwright Chromium is not installed')
            browser = await pw.chromium.launch()
            try:
                page = await browser.new_page(viewport={'width': 1440, 'height': 900}, reduced_motion='reduce')
                errors = []
                page.on('pageerror', lambda error: errors.append(str(error)))
                await page.route('**/api/monitor/activity*', lambda route: route.fulfill(status=502, json={}))
                await page.goto(str(self.client.make_url('/matrixtool')))
                await page.wait_for_selector('#mm-canvas')
                await page.wait_for_timeout(400)
                tier = page.locator('.mm-tier[data-mm-tier="T2"]')
                await tier.click()
                self.assertEqual(await tier.get_attribute('aria-expanded'), 'true', 'setup did not open T2')
                box = await page.locator('#mm-stage').bounding_box()
                await page.mouse.move(box['x'] + box['width'] / 2, box['y'] + box['height'] / 2)
                await page.mouse.wheel(0, -100)
                await page.wait_for_timeout(250)
                before_zoom = await page.locator('#mm-zoom-level').inner_text()
                self.assertNotEqual(before_zoom, '100%', 'setup left zoom at 100%')

                # A long drag ending on blank canvas is never an intentional reset gesture.
                pt = await page.evaluate(EMPTY)
                self.assertIsNotNone(pt, 'no empty canvas point found for the long drag')
                await page.mouse.move(pt['x'], pt['y'])
                await page.mouse.down()
                await page.mouse.move(pt['x'] + 60, pt['y'])
                await page.wait_for_timeout(350)
                await page.mouse.up()
                after_empty_drag = await page.evaluate(SNAP)
                self.assertNotEqual(after_empty_drag['zoom'], '100%', 'long empty-canvas drag reset zoom')
                self.assertEqual(after_empty_drag['tier'], 'true', 'long empty-canvas drag collapsed the tier')

                # Hold past the old squash window, return to the same node, then release.
                node_box = await tier.bounding_box()
                node_pt = {'x': node_box['x'] + node_box['width'] / 2,
                           'y': node_box['y'] + node_box['height'] / 2}
                await page.mouse.move(node_pt['x'], node_pt['y'])
                await page.mouse.down()
                await page.mouse.move(node_pt['x'] + 60, node_pt['y'])
                await page.wait_for_timeout(350)
                await page.mouse.move(node_pt['x'], node_pt['y'])
                await page.wait_for_timeout(350)
                await page.mouse.up()
                self.assertEqual(await tier.get_attribute('aria-expanded'), 'true',
                    'long-drag release toggled the node after the move window expired')
                # A deliberate follow-up click must still be captured from endPan's release refresh.
                await page.mouse.click(node_pt['x'], node_pt['y'])
                after_node_click = await page.evaluate(SNAP)
                self.assertEqual(after_node_click['tier'], 'true', 'long-drag release did not squash the node click')
                self.assertNotEqual(after_node_click['zoom'], '100%', 'long drag followed by node click reset the map')
                await page.wait_for_timeout(300)
                await tier.click()
                self.assertEqual(await tier.get_attribute('aria-expanded'), 'false',
                    'positive control: a normal node click did not toggle after the squash window')
                self.assertNotEqual(await page.locator('#mm-zoom-level').inner_text(), '100%',
                    'a normal node click reset the map')
                self.assertEqual(errors, [])
                await page.close()
            finally:
                await browser.close()

    async def test_matrix_non_empty_clicks_never_reset(self):
        """T-B4: node single clicks still toggle; non-empty chip/wire/control-bar/anchor
        clicks do not reset, and one empty-canvas click remains a no-op."""
        try:
            from playwright.async_api import async_playwright, expect
        except ImportError:
            self.skipTest('Playwright is not installed')
        async with async_playwright() as pw:
            if not Path(pw.chromium.executable_path).is_file():
                self.skipTest('Playwright Chromium is not installed')
            browser = await pw.chromium.launch()
            try:
                page = await browser.new_page(viewport={'width': 1440, 'height': 900}, reduced_motion='reduce')
                errors = []
                page.on('pageerror', lambda error: errors.append(str(error)))
                await page.route('**/api/monitor/activity*', lambda route: route.fulfill(status=502, json={}))
                await page.goto(str(self.client.make_url('/matrixtool')))
                await page.wait_for_selector('#mm-canvas')
                await page.wait_for_timeout(400)
                await page.locator('.mm-tier[data-mm-tier="T2"]').press('Enter')
                await page.locator('#mm-groups-T2 .mm-cluster[data-mm-cluster="code"] .mm-cluster-btn').press('Enter')
                await page.locator('button[data-mm-group="T2|write"]').press('Enter')
                await page.wait_for_timeout(400)
                box = await page.locator('#mm-stage').bounding_box()
                await page.mouse.move(box['x'] + box['width'] / 2, box['y'] + box['height'] / 2)
                await page.mouse.wheel(0, -100)
                await page.wait_for_timeout(250)
                chip = page.locator('button[data-mm-tool="write_fix"]')
                await chip.click()
                await expect(page.locator('#mm-detail')).to_be_visible()
                zoom_sel = await page.evaluate("() => document.getElementById('mm-zoom-level').textContent")
                self.assertNotEqual(zoom_sel, '100%')
                # A wire click bubbles through the canvas delegate but is excluded.
                await page.evaluate("""() => {
                    const hit = document.querySelector('#mm-branch-wires .mm-wire-hit');
                    if (!hit) throw new Error('no wire hit to click');
                    hit.dispatchEvent(new MouseEvent('click', {bubbles: true, cancelable: true}));
                }""")
                self.assertEqual(await page.locator('.mm-tier[data-mm-tier="T2"]').get_attribute('aria-expanded'), 'true')
                self.assertNotEqual(await page.evaluate("() => document.getElementById('mm-zoom-level').textContent"), '100%')
                # Control-bar zoom-in changes zoom but never resets state.
                await page.click('[data-mm-view-action="zoom-in"]')
                await page.wait_for_timeout(150)
                self.assertEqual(await page.locator('.mm-tier[data-mm-tier="T2"]').get_attribute('aria-expanded'), 'true')
                await expect(page.locator('#mm-detail')).to_be_visible()
                # Fit is NOT the initial-state reset: branches stay open.
                await page.click('[data-mm-view-action="reset"]')
                await page.wait_for_timeout(250)
                self.assertEqual(await page.locator('.mm-tier[data-mm-tier="T2"]').get_attribute('aria-expanded'), 'true',
                    'the fit button collapsed branches like an initial-state reset')
                self.assertEqual(await page.evaluate("() => document.getElementById('mm-stage').scrollLeft"), 0)
                zoom_fit = await page.evaluate("() => document.getElementById('mm-zoom-level').textContent")
                # An <a> inside the canvas is excluded by the guard.
                await page.evaluate("""() => {
                    const cv = document.getElementById('mm-canvas');
                    const a = document.createElement('a');
                    a.href = '#'; a.id = 'mm-test-anchor'; a.textContent = 'x';
                    cv.appendChild(a);
                    a.dispatchEvent(new MouseEvent('click', {bubbles: true, cancelable: true}));
                    a.remove();
                }""")
                self.assertEqual(await page.locator('.mm-tier[data-mm-tier="T2"]').get_attribute('aria-expanded'), 'true')
                self.assertEqual(await page.evaluate("() => document.getElementById('mm-zoom-level').textContent"), zoom_fit)
                # Node single clicks remain ordinary toggles; empty clicks are distinct no-ops.
                tier = page.locator('.mm-tier[data-mm-tier="T2"]')
                await tier.click()
                await page.wait_for_timeout(100)
                self.assertEqual(await tier.get_attribute('aria-expanded'), 'false', 'node single click did not toggle closed')
                await tier.click()
                await page.wait_for_timeout(100)
                self.assertEqual(await tier.get_attribute('aria-expanded'), 'true', 'node single click did not toggle open')
                zoom_before_empty = await page.evaluate("() => document.getElementById('mm-zoom-level').textContent")
                empty = await page.evaluate("""() => {
                    const st = document.getElementById('mm-stage'), cv = document.getElementById('mm-canvas');
                    const s = st.getBoundingClientRect(), r = cv.getBoundingClientRect();
                    for (let y = Math.max(s.top, r.top) + 3; y < Math.min(s.bottom, r.bottom) - 3; y += 5) {
                        for (let x = Math.max(s.left, r.left) + 3; x < Math.min(s.right, r.right) - 3; x += 5) {
                            const el = document.elementFromPoint(x, y);
                            if (el && el.closest('#mm-canvas') && !el.closest('button, a, .mm-wires, .mm-relations')) return {x, y};
                        }
                    }
                    return null;
                }""")
                self.assertIsNotNone(empty, 'no empty canvas point found for the B4 single-click control')
                await page.mouse.click(empty['x'], empty['y'])
                await page.wait_for_timeout(100)
                self.assertEqual(await tier.get_attribute('aria-expanded'), 'true', 'empty single click changed branch state')
                self.assertEqual(await page.evaluate("() => document.getElementById('mm-zoom-level').textContent"), zoom_before_empty,
                    'empty single click changed the fitted zoom')
                self.assertEqual(errors, [])
                await page.close()
            finally:
                await browser.close()

    async def test_matrix_dblclick_on_node_toggles_twice_without_reset(self):
        """T-B5: a node dblclick is two ordinary toggles, not the empty-map reset."""
        try:
            from playwright.async_api import async_playwright
        except ImportError:
            self.skipTest('Playwright is not installed')
        async with async_playwright() as pw:
            if not Path(pw.chromium.executable_path).is_file():
                self.skipTest('Playwright Chromium is not installed')
            browser = await pw.chromium.launch()
            try:
                page = await browser.new_page(viewport={'width': 1440, 'height': 900}, reduced_motion='reduce')
                errors = []
                page.on('pageerror', lambda error: errors.append(str(error)))
                await page.route('**/api/monitor/activity*', lambda route: route.fulfill(status=502, json={}))
                await page.goto(str(self.client.make_url('/matrixtool')))
                await page.wait_for_selector('#mm-canvas')
                await page.wait_for_timeout(300)
                tier = page.locator('.mm-tier[data-mm-tier="T2"]')
                await tier.click()
                self.assertEqual(await tier.get_attribute('aria-expanded'), 'true', 'setup did not open T2')
                box = await page.locator('#mm-stage').bounding_box()
                await page.mouse.move(box['x'] + box['width'] / 2, box['y'] + box['height'] / 2)
                await page.mouse.wheel(0, -100)
                await page.wait_for_timeout(250)
                before_zoom = await page.locator('#mm-zoom-level').inner_text()
                self.assertNotEqual(before_zoom, '100%', 'setup did not make an unintended reset observable')
                await tier.dblclick()
                await page.wait_for_timeout(100)
                self.assertEqual(await tier.get_attribute('aria-expanded'), 'true',
                    'a node dblclick did not leave the node at its original state after two toggles')
                self.assertEqual(await page.locator('#mm-zoom-level').inner_text(), before_zoom,
                    'node dblclick reset the map instead of toggling the node twice')
                self.assertEqual(errors, [])
                await page.close()
            finally:
                await browser.close()

    async def test_matrix_reset_is_instant_under_reduced_motion(self):
        """Reduced-motion reset is instant, mounts no glide spacer, and lands on the
        same final numbers as the non-reduce path (zoom 1, pan 0, all closed)."""
        try:
            from playwright.async_api import async_playwright
        except ImportError:
            self.skipTest('Playwright is not installed')
        EMPTY = """() => {
            const st = document.getElementById('mm-stage');
            const cv = document.getElementById('mm-canvas');
            const s = st.getBoundingClientRect(), r = cv.getBoundingClientRect();
            const left = Math.max(s.left, r.left) + 3, right = Math.min(s.right, r.right) - 3;
            const top = Math.max(s.top, r.top) + 3, bottom = Math.min(s.bottom, r.bottom) - 3;
            for (let y = top; y < bottom; y += 5) {
                for (let x = left; x < right; x += 5) {
                    const el = document.elementFromPoint(x, y);
                    if (!el || !el.closest('#mm-canvas')) continue;
                    if (el.closest('button, a, .mm-wires, .mm-relations')) continue;
                    return {x: x, y: y};
                }
            }
            return null;
        }"""
        STATE = """() => {
            const cv = document.getElementById('mm-canvas');
            const st = document.getElementById('mm-stage');
            return {zoomLabel: document.getElementById('mm-zoom-level').textContent,
                    zoom: parseFloat(cv.style.zoom || '1'), sl: st.scrollLeft, top: st.scrollTop,
                    transform: cv.style.transform || '', detailHidden: document.getElementById('mm-detail').hidden,
                    tiersOpen: [...cv.querySelectorAll('.mm-tier')].filter(b => b.getAttribute('aria-expanded') === 'true').length,
                    spacer: !!document.getElementById('mm-glide-spacer'), scrollY: window.scrollY};
        }"""
        async with async_playwright() as pw:
            if not Path(pw.chromium.executable_path).is_file():
                self.skipTest('Playwright Chromium is not installed')
            browser = await pw.chromium.launch()
            try:
                page = await browser.new_page(viewport={'width': 1440, 'height': 900}, reduced_motion='reduce')
                errors = []
                page.on('pageerror', lambda error: errors.append(str(error)))
                await page.route('**/api/monitor/activity*', lambda route: route.fulfill(status=502, json={}))
                await page.goto(str(self.client.make_url('/matrixtool')))
                await page.wait_for_selector('#mm-canvas')
                await page.wait_for_timeout(400)
                await page.locator('.mm-tier[data-mm-tier="T2"]').click()
                await page.wait_for_timeout(300)
                box = await page.locator('#mm-stage').bounding_box()
                await page.mouse.move(box['x'] + box['width'] / 2, box['y'] + box['height'] / 2)
                await page.mouse.wheel(0, -100)
                await page.wait_for_timeout(250)
                pt = await page.evaluate(EMPTY)
                self.assertIsNotNone(pt)
                await page.mouse.move(pt['x'], pt['y'])
                await page.mouse.down()
                await page.mouse.move(pt['x'] + 70, pt['y'])
                await page.mouse.up()
                await page.wait_for_timeout(300)
                await page.evaluate('window.scrollTo(0, 150)')
                pt = await page.evaluate(EMPTY)
                self.assertIsNotNone(pt)
                await page.mouse.dblclick(pt['x'], pt['y'])
                # No wait: reduce is the OLD instant path.
                a = await page.evaluate(STATE)
                self.assertEqual(a['zoomLabel'], '100%', 'reduce reset not instant')
                self.assertEqual(a['zoom'], 1)
                self.assertEqual(a['sl'], 0)
                self.assertEqual(a['top'], 0)
                self.assertEqual(a['transform'], '')
                self.assertTrue(a['detailHidden'])
                self.assertEqual(a['tiersOpen'], 0)
                self.assertFalse(a['spacer'], 'reduce mounted a glide spacer')
                self.assertLessEqual(a['scrollY'], 150, 'reduce overshoot the pre-click scroll')
                await page.wait_for_timeout(150)
                b = await page.evaluate(STATE)
                self.assertFalse(b['spacer'], 'a glide spacer appeared after the instant reset')
                self.assertEqual(b['zoomLabel'], '100%')
                self.assertEqual(b['transform'], '')
                self.assertEqual(errors, [])
                await page.close()
            finally:
                await browser.close()
    async def test_matrix_empty_canvas_single_click_is_noop(self):
        """T-B6: a single empty-canvas click is not an intentional reset gesture."""
        try:
            from playwright.async_api import async_playwright
        except ImportError:
            self.skipTest('Playwright is not installed')
        EMPTY = """() => {
            const st = document.getElementById('mm-stage');
            const cv = document.getElementById('mm-canvas');
            const s = st.getBoundingClientRect(), r = cv.getBoundingClientRect();
            const left = Math.max(s.left, r.left) + 3, right = Math.min(s.right, r.right) - 3;
            const top = Math.max(s.top, r.top) + 3, bottom = Math.min(s.bottom, r.bottom) - 3;
            for (let y = top; y < bottom; y += 5) {
                for (let x = left; x < right; x += 5) {
                    const el = document.elementFromPoint(x, y);
                    if (!el || !el.closest('#mm-canvas')) continue;
                    if (el.closest('button, a, .mm-wires, .mm-relations')) continue;
                    return {x: x, y: y};
                }
            }
            return null;
        }"""
        async with async_playwright() as pw:
            if not Path(pw.chromium.executable_path).is_file():
                self.skipTest('Playwright Chromium is not installed')
            browser = await pw.chromium.launch()
            try:
                page = await browser.new_page(viewport={'width': 1440, 'height': 900}, reduced_motion='reduce')
                errors = []
                page.on('pageerror', lambda error: errors.append(str(error)))
                await page.route('**/api/monitor/activity*', lambda route: route.fulfill(status=502, json={}))
                await page.goto(str(self.client.make_url('/matrixtool')))
                await page.wait_for_selector('#mm-canvas')
                await page.wait_for_timeout(300)
                tier = page.locator('.mm-tier[data-mm-tier="T2"]')
                await tier.click()
                box = await page.locator('#mm-stage').bounding_box()
                await page.mouse.move(box['x'] + box['width'] / 2, box['y'] + box['height'] / 2)
                await page.mouse.wheel(0, -100)
                await page.wait_for_timeout(250)
                before = await page.evaluate("""() => ({
                    zoom: document.getElementById('mm-zoom-level').textContent,
                    tier: document.querySelector('.mm-tier[data-mm-tier="T2"]').getAttribute('aria-expanded')
                })""")
                self.assertEqual(before['tier'], 'true', 'setup did not open T2')
                self.assertNotEqual(before['zoom'], '100%', 'setup did not make a reset observable')
                pt = await page.evaluate(EMPTY)
                self.assertIsNotNone(pt, 'no empty canvas point found for the single-click probe')
                await page.mouse.click(pt['x'], pt['y'])
                await page.wait_for_timeout(250)
                after = await page.evaluate("""() => ({
                    zoom: document.getElementById('mm-zoom-level').textContent,
                    tier: document.querySelector('.mm-tier[data-mm-tier="T2"]').getAttribute('aria-expanded')
                })""")
                self.assertEqual(after, before, 'one empty click reset state despite the explicit double-click gesture')
                self.assertEqual(errors, [])
                await page.close()
            finally:
                await browser.close()

    async def test_matrix_long_drag_empty_canvas_does_not_reset(self):
        """T-B7: a >250ms drag release arms squash through the following double-click window."""
        try:
            from playwright.async_api import async_playwright
        except ImportError:
            self.skipTest('Playwright is not installed')
        EMPTY = """() => {
            const st = document.getElementById('mm-stage');
            const cv = document.getElementById('mm-canvas');
            const s = st.getBoundingClientRect(), r = cv.getBoundingClientRect();
            const left = Math.max(s.left, r.left) + 3, right = Math.min(s.right, r.right) - 3;
            const top = Math.max(s.top, r.top) + 3, bottom = Math.min(s.bottom, r.bottom) - 3;
            for (let y = top; y < bottom; y += 5) {
                for (let x = left; x < right; x += 5) {
                    const el = document.elementFromPoint(x, y);
                    if (!el || !el.closest('#mm-canvas')) continue;
                    if (el.closest('button, a, .mm-wires, .mm-relations')) continue;
                    return {x: x, y: y};
                }
            }
            return null;
        }"""
        async with async_playwright() as pw:
            if not Path(pw.chromium.executable_path).is_file():
                self.skipTest('Playwright Chromium is not installed')
            browser = await pw.chromium.launch()
            try:
                page = await browser.new_page(viewport={'width': 1440, 'height': 900}, reduced_motion='reduce')
                errors = []
                page.on('pageerror', lambda error: errors.append(str(error)))
                await page.route('**/api/monitor/activity*', lambda route: route.fulfill(status=502, json={}))
                await page.goto(str(self.client.make_url('/matrixtool')))
                await page.wait_for_selector('#mm-canvas')
                await page.wait_for_timeout(300)
                tier = page.locator('.mm-tier[data-mm-tier="T2"]')
                await tier.click()
                box = await page.locator('#mm-stage').bounding_box()
                await page.mouse.move(box['x'] + box['width'] / 2, box['y'] + box['height'] / 2)
                await page.mouse.wheel(0, -100)
                await page.wait_for_timeout(250)
                before = await page.evaluate("""() => ({
                    zoom: document.getElementById('mm-zoom-level').textContent,
                    tier: document.querySelector('.mm-tier[data-mm-tier="T2"]').getAttribute('aria-expanded')
                })""")
                self.assertEqual(before['tier'], 'true', 'setup did not open T2')
                self.assertNotEqual(before['zoom'], '100%', 'setup did not make a reset observable')
                pt = await page.evaluate(EMPTY)
                self.assertIsNotNone(pt, 'no empty canvas point found for the long drag')
                await page.mouse.move(pt['x'], pt['y'])
                await page.mouse.down()
                await page.mouse.move(pt['x'] + 80, pt['y'], steps=4)
                await page.wait_for_timeout(350)
                await page.mouse.up()
                # A deliberate dblclick immediately after release tests that endPan,
                # not merely the last move, keeps the 250ms guard armed.
                pt = await page.evaluate(EMPTY)
                self.assertIsNotNone(pt, 'no empty canvas point found after the drag')
                await page.mouse.dblclick(pt['x'], pt['y'])
                await page.wait_for_timeout(250)
                after = await page.evaluate("""() => ({
                    zoom: document.getElementById('mm-zoom-level').textContent,
                    tier: document.querySelector('.mm-tier[data-mm-tier="T2"]').getAttribute('aria-expanded')
                })""")
                self.assertEqual(after, before, 'long-drag release did not squash the following dblclick')
                # Positive control: once the 250ms post-release guard expires, an intentional dblclick resets.
                await page.wait_for_timeout(300)
                pt = await page.evaluate(EMPTY)
                self.assertIsNotNone(pt, 'no empty canvas point found for the post-window reset control')
                await page.mouse.dblclick(pt['x'], pt['y'])
                reset_state = await page.evaluate("""() => ({
                    zoom: document.getElementById('mm-zoom-level').textContent,
                    tier: document.querySelector('.mm-tier[data-mm-tier="T2"]').getAttribute('aria-expanded')
                })""")
                self.assertEqual(reset_state['zoom'], '100%', 'positive-control dblclick did not reset zoom')
                self.assertEqual(reset_state['tier'], 'false', 'positive-control dblclick did not collapse the branch')
                self.assertEqual(errors, [])
                await page.close()
            finally:
                await browser.close()

    async def test_matrix_zoom_buttons_anchor_at_last_pointer(self):
        """T-A1: with the pointer over the stage, +/- anchor the point under it
        (additive .1 steps preserved); 2x zoom-out from 100% still reads 80%."""
        try:
            from playwright.async_api import async_playwright
        except ImportError:
            self.skipTest('Playwright is not installed')
        SNAP = """() => {
            const st = document.getElementById('mm-stage');
            const cv = document.getElementById('mm-canvas');
            const t = cv.querySelector('.mm-tier[data-mm-tier="T2"]');
            const r = t.getBoundingClientRect();
            return {cx: (r.left + r.right) / 2, cy: (r.top + r.bottom) / 2,
                    zoom: parseFloat(cv.style.zoom || '1'), sl: st.scrollLeft,
                    transform: cv.style.transform || ''};
        }"""
        ROOM = '() => {const s=document.getElementById("mm-stage");return s.scrollWidth-s.clientWidth}'
        async with async_playwright() as pw:
            if not Path(pw.chromium.executable_path).is_file():
                self.skipTest('Playwright Chromium is not installed')
            browser = await pw.chromium.launch()
            try:
                page = await browser.new_page(viewport={'width': 1440, 'height': 900}, reduced_motion='reduce')
                errors = []
                page.on('pageerror', lambda error: errors.append(str(error)))
                await page.route('**/api/monitor/activity*', lambda route: route.fulfill(status=502, json={}))
                await page.goto(str(self.client.make_url('/matrixtool')))
                await page.wait_for_selector('#mm-canvas')
                await page.wait_for_timeout(400)
                await page.locator('.mm-tier[data-mm-tier="T2"]').press('Enter')
                await page.locator('#mm-groups-T2 .mm-cluster[data-mm-cluster="code"] .mm-cluster-btn').press('Enter')
                await page.locator('button[data-mm-group="T2|write"]').press('Enter')
                await page.wait_for_timeout(400)
                await page.click('[data-mm-view-action="reset"]')
                stage = page.locator('#mm-stage')
                for _ in range(20):
                    if await stage.evaluate(ROOM) > 80:
                        break
                    await page.click('[data-mm-view-action="zoom-in"]')
                    await page.wait_for_timeout(120)
                self.assertGreater(await stage.evaluate(ROOM), 80, 'setup left no scroll room')
                tier = page.locator('.mm-tier[data-mm-tier="T2"]')
                tb = await tier.bounding_box()
                await page.mouse.move(tb['x'] + tb['width'] / 2, tb['y'] + tb['height'] / 2)
                await page.wait_for_timeout(120)
                before = await page.evaluate(SNAP)
                await page.click('[data-mm-view-action="zoom-in"]')
                await page.wait_for_timeout(250)
                after = await page.evaluate(SNAP)
                self.assertGreater(after['zoom'], before['zoom'], 'zoom-in did not zoom')
                self.assertAlmostEqual(after['cx'], before['cx'], delta=2.0,
                    msg='zoom-in did not anchor the point under the pointer: ' + str(after['cx'] - before['cx']))
                self.assertAlmostEqual(after['cy'], before['cy'], delta=2.0)
                self.assertTrue(after['sl'] != before['sl'] or after['transform'] != before['transform'],
                    'anchored zoom-in left the pan untouched (legacy path fired with a pointer present)')
                await page.click('[data-mm-view-action="zoom-out"]')
                await page.wait_for_timeout(250)
                back = await page.evaluate(SNAP)
                self.assertAlmostEqual(back['zoom'], before['zoom'], delta=0.001, msg='additive .1 round trip broke')
                self.assertAlmostEqual(back['cx'], after['cx'], delta=2.0, msg='zoom-out did not anchor')
                self.assertAlmostEqual(back['cy'], after['cy'], delta=2.0)
                # 80% lock with the pointer over the stage (anchored path keeps steps).
                await page.reload()
                await page.wait_for_selector('#mm-canvas')
                await page.wait_for_timeout(400)
                cvbox = await page.locator('#mm-canvas').bounding_box()
                await page.mouse.move(cvbox['x'] + cvbox['width'] / 2, cvbox['y'] + cvbox['height'] / 2)
                await page.click('[data-mm-view-action="zoom-out"]')
                await page.wait_for_timeout(150)
                await page.click('[data-mm-view-action="zoom-out"]')
                await page.wait_for_timeout(150)
                self.assertEqual(await page.locator('#mm-zoom-level').text_content(), '80%')
                self.assertEqual(errors, [])
                await page.close()
            finally:
                await browser.close()

    async def test_matrix_zoom_buttons_keyboard_only_keep_pan(self):
        """T-A2 (guard): with no pointer ever over the stage, +/- change zoom
        with NO pan change (legacy path), protecting the wheel-test setup."""
        try:
            from playwright.async_api import async_playwright
        except ImportError:
            self.skipTest('Playwright is not installed')
        PAN = """() => {
            const st = document.getElementById('mm-stage');
            const cv = document.getElementById('mm-canvas');
            return {sl: st.scrollLeft, top: st.scrollTop, transform: cv.style.transform || '',
                    zoom: parseFloat(cv.style.zoom || '1')};
        }"""
        ROOM = '() => {const s=document.getElementById("mm-stage");return s.scrollWidth-s.clientWidth}'
        async with async_playwright() as pw:
            if not Path(pw.chromium.executable_path).is_file():
                self.skipTest('Playwright Chromium is not installed')
            browser = await pw.chromium.launch()
            try:
                page = await browser.new_page(viewport={'width': 1440, 'height': 900}, reduced_motion='reduce')
                errors = []
                page.on('pageerror', lambda error: errors.append(str(error)))
                await page.route('**/api/monitor/activity*', lambda route: route.fulfill(status=502, json={}))
                await page.goto(str(self.client.make_url('/matrixtool')))
                await page.wait_for_selector('#mm-canvas')
                await page.wait_for_timeout(400)
                await page.locator('.mm-tier[data-mm-tier="T2"]').press('Enter')
                await page.locator('#mm-groups-T2 .mm-cluster[data-mm-cluster="code"] .mm-cluster-btn').press('Enter')
                await page.locator('button[data-mm-group="T2|write"]').press('Enter')
                await page.wait_for_timeout(400)
                await page.click('[data-mm-view-action="reset"]')
                stage = page.locator('#mm-stage')
                for _ in range(20):
                    if await stage.evaluate(ROOM) > 80:
                        break
                    await page.click('[data-mm-view-action="zoom-in"]')
                    await page.wait_for_timeout(120)
                before = await page.evaluate(PAN)
                await page.click('[data-mm-view-action="zoom-in"]')
                await page.wait_for_timeout(150)
                mid = await page.evaluate(PAN)
                self.assertGreater(mid['zoom'], before['zoom'])
                self.assertEqual(mid['sl'], before['sl'], 'mouse-activated zoom with no stage pointer moved scroll')
                self.assertEqual(mid['top'], before['top'])
                self.assertEqual(mid['transform'], before['transform'], 'legacy zoom changed the pan translate')
                await page.locator('[data-mm-view-action="zoom-out"]').press('Enter')
                await page.wait_for_timeout(150)
                a = await page.evaluate(PAN)
                self.assertAlmostEqual(a['zoom'], before['zoom'], delta=0.001)
                self.assertEqual(a['sl'], before['sl'])
                self.assertEqual(a['top'], before['top'])
                self.assertEqual(a['transform'], before['transform'])
                self.assertEqual(errors, [])
                await page.close()
            finally:
                await browser.close()

    async def test_matrix_overview_zoom_buttons_anchor_and_fallback(self):
        """T-A3: overview +/- anchor the dot under the pointer after a pan; the
        keyboard-only fallback zooms about the TRUE viewport centre."""
        try:
            from playwright.async_api import async_playwright
        except ImportError:
            self.skipTest('Playwright is not installed')
        DOT = """() => {
            const svg = document.querySelector('#sunburst-container svg');
            const s = svg.getBoundingClientRect();
            const cx = (s.left + s.right) / 2, cy = (s.top + s.bottom) / 2;
            let best = null, bd = 1e9;
            for (const c of svg.querySelectorAll('circle.radial-tool')) {
                const r = c.getBoundingClientRect();
                if (r.width < 2) continue;
                const x = (r.left + r.right) / 2, y = (r.top + r.bottom) / 2;
                const d = Math.hypot(x - cx, y - cy);
                if (d < bd) { bd = d; best = c; }
            }
            if (!best) return null;
            best.setAttribute('data-mm-test-dot', '1');
            const r = best.getBoundingClientRect();
            return {x: (r.left + r.right) / 2, y: (r.top + r.bottom) / 2};
        }"""
        DOT_AFTER = """() => {
            const c = document.querySelector('[data-mm-test-dot]');
            if (!c) return null;
            const svg = document.querySelector('#sunburst-container svg');
            const s = svg.getBoundingClientRect();
            const r = c.getBoundingClientRect();
            // Relative to the svg viewport: isolates the zoom anchor from any whole-svg
            // translation (document scroll / layout), which is not the zoom behaviour.
            return {x: (r.left + r.right) / 2 - s.left, y: (r.top + r.bottom) / 2 - s.top};
        }"""
        TRANSFORM = """() => {
            const tr = document.getElementById('radial-zoom').getAttribute('transform') || '';
            const tPart = tr.slice(tr.indexOf('(') + 1, tr.indexOf(')')).split(' ');
            const kPart = tr.slice(tr.lastIndexOf('(') + 1, tr.lastIndexOf(')'));
            return {tx: parseFloat(tPart[0]), ty: parseFloat(tPart[1]), k: parseFloat(kPart)};
        }"""
        async with async_playwright() as pw:
            if not Path(pw.chromium.executable_path).is_file():
                self.skipTest('Playwright Chromium is not installed')
            browser = await pw.chromium.launch()
            try:
                page = await browser.new_page(viewport={'width': 1440, 'height': 900}, reduced_motion='reduce')
                errors = []
                page.on('pageerror', lambda error: errors.append(str(error)))
                await page.route('**/api/monitor/activity*', lambda route: route.fulfill(status=502, json={}))
                await page.goto(str(self.client.make_url('/matrixtool')))
                await page.wait_for_selector('#mm-canvas')
                await page.click('[data-tab="sunburst"]')
                await page.wait_for_selector('#sunburst-container svg circle.radial-tool')
                await page.wait_for_timeout(500)
                svg = page.locator('#sunburst-container svg')
                sb = await svg.bounding_box()
                # Pan via a background drag (>4px), then let suppressClick expire.
                await page.mouse.move(sb['x'] + 40, sb['y'] + 40)
                await page.mouse.down()
                await page.mouse.move(sb['x'] + 140, sb['y'] + 60)
                await page.mouse.up()
                await page.wait_for_timeout(200)
                dot = await page.evaluate(DOT)
                self.assertIsNotNone(dot, 'no overview dot found to anchor')
                DOT_ABS = """() => {
                    const c = document.querySelector('[data-mm-test-dot]');
                    const r = c.getBoundingClientRect();
                    return {x: (r.left + r.right) / 2, y: (r.top + r.bottom) / 2};
                }"""
                await page.mouse.move(dot['x'], dot['y'])
                # Hovering a dot runs focusTool, which rewrites the breadcrumb and shifts
                # the whole svg down a few px. The mousemove that feeds lastOverviewPointer
                # fires BEFORE that shift, so it captures a stale point. Let the hover
                # settle, then re-centre the pointer on the SETTLED dot so the button
                # anchors the dot it actually sits on. (No mouseleave fires on element
                # move alone, so the hover state is stable once settled.)
                await page.wait_for_timeout(300)
                settled = await page.evaluate(DOT_ABS)
                await page.mouse.move(settled['x'], settled['y'])
                await page.wait_for_timeout(150)
                before = await page.evaluate(DOT_AFTER)
                await page.evaluate("() => document.getElementById('sb-zoom-in').click()")
                await page.wait_for_timeout(300)
                after = await page.evaluate(DOT_AFTER)
                self.assertIsNotNone(after)
                self.assertAlmostEqual(after['x'], before['x'], delta=2.0,
                    msg='overview zoom-in did not anchor the dot under the pointer: ' + str(after['x'] - before['x']))
                self.assertAlmostEqual(after['y'], before['y'], delta=2.0)
                # Keyboard-only fallback from identity: zoom about true viewport centre.
                await page.reload()
                await page.wait_for_selector('#mm-canvas')
                await page.evaluate("() => document.querySelector('[data-tab=\"sunburst\"]').click()")
                await page.wait_for_selector('#sunburst-container svg circle.radial-tool')
                await page.wait_for_timeout(500)
                await page.evaluate("() => document.getElementById('sb-zoom-in').focus()")
                await page.keyboard.press('Enter')
                await page.wait_for_timeout(300)
                t = await page.evaluate(TRANSFORM)
                # zoomAt(CX=450, CY=380, 1.25) from identity: tx=-112.5 ty=-95 k=1.25.
                self.assertAlmostEqual(t['k'], 1.25, delta=0.001, msg='fallback zoom factor wrong: ' + str(t))
                self.assertAlmostEqual(t['tx'], -112.5, delta=0.05, msg='fallback did not anchor the viewport centre X: ' + str(t))
                self.assertAlmostEqual(t['ty'], -95.0, delta=0.05, msg='fallback did not anchor the viewport centre Y: ' + str(t))
                self.assertEqual(errors, [])
                await page.close()
            finally:
                await browser.close()

    async def test_matrix_wheel_zoom_out_holds_anchor_in_slack(self):
        """T-A4: in the slack (margin:auto) state, wheel zoom-out holds the
        anchor when the computed pan is >=0, and clamps exactly at the scroll
        wall (panTotal 0, canvas never displaced off origin) when it is <0."""
        try:
            from playwright.async_api import async_playwright
        except ImportError:
            self.skipTest('Playwright is not installed')
        MEASURE = """(p) => {
            const st = document.getElementById('mm-stage'), cv = document.getElementById('mm-canvas');
            const t = cv.querySelector('.mm-tier[data-mm-tier="T1"]');
            const s = st.getBoundingClientRect(), r = cv.getBoundingClientRect(), b = t.getBoundingClientRect();
            const k = parseFloat(cv.style.zoom || '1');
            return {cx: (b.left + b.right) / 2, cy: (b.top + b.bottom) / 2,
                    crLeft: r.left, crTop: r.top, crWidth: r.width, stLeft: s.left, stTop: s.top,
                    zoom: k, sl: st.scrollLeft, top: st.scrollTop, transform: cv.style.transform || '',
                    ux: (p.x - r.left) / k, uy: (p.y - r.top) / k};
        }"""
        async with async_playwright() as pw:
            if not Path(pw.chromium.executable_path).is_file():
                self.skipTest('Playwright Chromium is not installed')
            browser = await pw.chromium.launch()
            try:
                page = await browser.new_page(viewport={'width': 1440, 'height': 900}, reduced_motion='reduce')
                errors = []
                page.on('pageerror', lambda error: errors.append(str(error)))
                await page.route('**/api/monitor/activity*', lambda route: route.fulfill(status=502, json={}))
                await page.goto(str(self.client.make_url('/matrixtool')))
                await page.wait_for_selector('#mm-canvas')
                await page.wait_for_timeout(400)
                # Zoom IN centred first (keeps pan 0), cursor at canvas centre.
                cvbox = await page.locator('#mm-canvas').bounding_box()
                await page.mouse.move(cvbox['x'] + cvbox['width'] / 2, cvbox['y'] + cvbox['height'] / 2)
                await page.mouse.wheel(0, -100)
                await page.wait_for_timeout(300)
                # Phase 1: cursor in the LEFT half (raw pan >= 0), anchor must hold.
                # The anchor invariant is measured AT the cursor's own content point,
                # not at a node centre that sits elsewhere on the canvas.
                cvb = await page.locator('#mm-canvas').bounding_box()
                cur1 = {'x': cvb['x'] + 30, 'y': cvb['y'] + cvb['height'] / 2}
                await page.mouse.move(cur1['x'], cur1['y'])
                await page.wait_for_timeout(120)
                b1 = await page.evaluate(MEASURE, cur1)
                self.assertLess(b1['ux'], b1['crWidth'] / b1['zoom'] / 2, 'phase-1 cursor not in the left half')
                await page.mouse.wheel(0, 100)
                await page.wait_for_timeout(300)
                a1 = await page.evaluate(MEASURE, cur1)
                self.assertLess(a1['zoom'], b1['zoom'], 'phase 1 did not zoom out')
                sx = a1['crLeft'] + b1['ux'] * a1['zoom']
                sy = a1['crTop'] + b1['uy'] * a1['zoom']
                self.assertAlmostEqual(sx, cur1['x'], delta=2.0,
                    msg='slack zoom-out did not hold the anchor: ' + str(sx - cur1['x']))
                self.assertAlmostEqual(sy, cur1['y'], delta=2.0)
                self.assertGreaterEqual(a1['sl'], 0)
                # Phase 2: cursor near the RIGHT edge (raw pan < 0) -> exact clamp.
                await page.reload()
                await page.wait_for_selector('#mm-canvas')
                await page.wait_for_timeout(400)
                cvbox = await page.locator('#mm-canvas').bounding_box()
                await page.mouse.move(cvbox['x'] + cvbox['width'] / 2, cvbox['y'] + cvbox['height'] / 2)
                await page.mouse.wheel(0, -100)
                await page.wait_for_timeout(300)
                cvbox2 = await page.locator('#mm-canvas').bounding_box()
                cur2 = {'x': cvbox2['x'] + cvbox2['width'] - 5, 'y': cvbox2['y'] + cvbox2['height'] - 5}
                await page.mouse.move(cur2['x'], cur2['y'])
                await page.wait_for_timeout(120)
                b2 = await page.evaluate(MEASURE, cur2)
                await page.mouse.wheel(0, 100)
                await page.wait_for_timeout(300)
                a2 = await page.evaluate(MEASURE, cur2)
                self.assertLess(a2['zoom'], b2['zoom'])
                # raw pan the formula wanted (negative here), and the clamp it got.
                raw = (a2['crLeft'] - b2['crLeft']) + b2['ux'] * (a2['zoom'] - b2['zoom'])
                self.assertLess(raw, 0, 'phase-2 premise broke: computed pan was not negative: ' + str(raw))
                self.assertEqual(a2['sl'], 0, 'clamp let scroll go negative or non-zero')
                self.assertEqual(a2['transform'], '', 'clamp left a pan translate (panTotal not 0)')
                self.assertGreaterEqual(a2['crLeft'], a2['stLeft'], 'canvas displaced off the stage origin')
                drift = a2['cx'] - b2['cx']
                expect = (a2['crLeft'] - b2['crLeft']) + ((b2['cx'] - b2['crLeft']) / b2['zoom']) * (a2['zoom'] - b2['zoom'])
                self.assertAlmostEqual(drift, expect, delta=2.0,
                    msg='clamp drift not exact: ' + str(drift) + ' vs ' + str(expect))
                self.assertEqual(errors, [])
                await page.close()
            finally:
                await browser.close()

    async def test_matrix_selected_chip_ring_is_paint_only(self):
        """T-C1: the selected chip carries a static ring (box-shadow) with ZERO
        rect/transform/filter delta (0.02px census style)."""
        try:
            from playwright.async_api import async_playwright
        except ImportError:
            self.skipTest('Playwright is not installed')
        RECTS = """() => {
            const SEL = '.mm-root, .mm-tier, .mm-cluster-btn, .mm-group > button, .mm-bucket-btn, .mm-tools > button, .mm-bucket-body > button';
            const c = document.getElementById('mm-canvas').getBoundingClientRect();
            const out = [];
            for (const n of document.querySelectorAll('#mm-canvas ' + SEL)) {
                const r = n.getBoundingClientRect();
                // Hidden cards (collapsed branch) return a zero rect at the absolute
                // origin, so subtracting the scrolling canvas origin would make them
                // scroll-dependent. Skip them; visible cards relative rects cancel
                // document scroll, stage scroll and the pan transform on #mm-canvas.
                if (r.width < 1 || r.height < 1) continue;
                out.push([r.left - c.left, r.top - c.top, r.right - c.left, r.bottom - c.top]);
            }
            return out;
        }"""
        CHIP = """() => {
            const c = document.querySelector('button[data-mm-tool="write_fix"]');
            const cs = getComputedStyle(c);
            return {current: c.getAttribute('aria-current'), shadow: cs.boxShadow, transform: cs.transform, filter: cs.filter};
        }"""
        async with async_playwright() as pw:
            if not Path(pw.chromium.executable_path).is_file():
                self.skipTest('Playwright Chromium is not installed')
            browser = await pw.chromium.launch()
            try:
                page = await browser.new_page(viewport={'width': 1440, 'height': 900}, reduced_motion='reduce')
                errors = []
                page.on('pageerror', lambda error: errors.append(str(error)))
                await page.route('**/api/monitor/activity*', lambda route: route.fulfill(status=502, json={}))
                await page.goto(str(self.client.make_url('/matrixtool')))
                await page.wait_for_selector('#mm-canvas')
                await page.wait_for_timeout(400)
                await page.locator('.mm-tier[data-mm-tier="T2"]').press('Enter')
                await page.locator('#mm-groups-T2 .mm-cluster[data-mm-cluster="code"] .mm-cluster-btn').press('Enter')
                await page.locator('button[data-mm-group="T2|write"]').press('Enter')
                await page.wait_for_timeout(400)
                before = await page.evaluate(RECTS)
                chip0 = await page.evaluate(CHIP)
                await page.locator('button[data-mm-tool="write_fix"]').click()
                await page.wait_for_timeout(250)
                chip1 = await page.evaluate(CHIP)
                self.assertEqual(chip1['current'], 'true', 'selection did not stamp aria-current')
                self.assertNotEqual(chip1['shadow'], 'none', 'the selected chip carries no emphasis ring')
                self.assertEqual(chip1['transform'], 'none', 'the ring changed transform')
                self.assertEqual(chip1['filter'], chip0['filter'], 'the ring changed filter')
                after = await page.evaluate(RECTS)
                self.assertEqual(len(after), len(before))
                delta = max(abs(after[i][j] - before[i][j]) for i in range(len(before)) for j in range(4))
                self.assertLessEqual(delta, 0.02, 'selection emphasis moved a rect by ' + str(delta))
                await page.locator('#mm-detail [data-mm-close]').click()
                await page.wait_for_timeout(250)
                again = await page.evaluate(RECTS)
                delta2 = max(abs(again[i][j] - before[i][j]) for i in range(len(before)) for j in range(4))
                self.assertLessEqual(delta2, 0.02, 'deselect moved a rect by ' + str(delta2))
                self.assertEqual(errors, [])
                await page.close()
            finally:
                await browser.close()

    async def test_matrix_keyboard_shortcuts(self):
        """T-C2: '/' focuses search, 't' toggles theme, digits switch tabs; none
        fire while typing; Arrow pan and Escape contexts stay unchanged."""
        try:
            from playwright.async_api import async_playwright, expect
        except ImportError:
            self.skipTest('Playwright is not installed')
        async with async_playwright() as pw:
            if not Path(pw.chromium.executable_path).is_file():
                self.skipTest('Playwright Chromium is not installed')
            browser = await pw.chromium.launch()
            try:
                page = await browser.new_page(viewport={'width': 1440, 'height': 900}, reduced_motion='reduce')
                errors = []
                page.on('pageerror', lambda error: errors.append(str(error)))
                await page.route('**/api/monitor/activity*', lambda route: route.fulfill(status=502, json={}))
                await page.goto(str(self.client.make_url('/matrixtool')))
                await page.wait_for_selector('#mm-canvas')
                await page.wait_for_timeout(400)
                await page.keyboard.press('/')
                focus = await page.evaluate("() => document.activeElement.id")
                self.assertEqual(focus, 'mm-search', "'/' did not focus the search box")
                # Typing guards: none of the shortcuts fire inside the input.
                await page.keyboard.type('t/2')
                self.assertEqual(await page.locator('#mm-search').input_value(), 't/2')
                self.assertIsNone(await page.evaluate("() => document.documentElement.getAttribute('data-theme')"),
                    "'t' fired while typing in the search box")
                self.assertTrue(await page.locator('#panel-mindmap').evaluate("n => n.classList.contains('active')"),
                    "a digit switched tabs while typing")
                await page.locator('#mm-search').fill('')
                await page.locator('h1').click()
                # 't' toggles theme.
                await page.keyboard.press('t')
                theme1 = await page.evaluate("() => document.documentElement.getAttribute('data-theme')")
                self.assertIn(theme1, ('light', 'dark'), "'t' did not stamp data-theme")
                await page.keyboard.press('t')
                theme2 = await page.evaluate("() => document.documentElement.getAttribute('data-theme')")
                self.assertIn(theme2, ('light', 'dark'))
                self.assertNotEqual(theme1, theme2, "'t' did not flip the theme")
                # Digits switch tabs through the existing click path.
                await page.keyboard.press('2')
                await page.wait_for_timeout(150)
                self.assertTrue(await page.locator('#panel-inventory').evaluate("n => n.classList.contains('active')"),
                    "'2' did not activate the inventory tab")
                await page.keyboard.press('1')
                await page.wait_for_timeout(150)
                self.assertTrue(await page.locator('#panel-mindmap').evaluate("n => n.classList.contains('active')"))
                await page.keyboard.press('9')
                await page.wait_for_timeout(150)
                self.assertTrue(await page.locator('#panel-mindmap').evaluate("n => n.classList.contains('active')"),
                    "'9' activated a non-existent tab")
                # Arrow pan unchanged (needs overflow).
                await page.locator('.mm-tier[data-mm-tier="T2"]').press('Enter')
                await page.locator('#mm-groups-T2 .mm-cluster[data-mm-cluster="code"] .mm-cluster-btn').press('Enter')
                await page.locator('button[data-mm-group="T2|write"]').press('Enter')
                await page.wait_for_timeout(400)
                vroom = await page.evaluate("() => {const s=document.getElementById('mm-stage');return s.scrollHeight-s.clientHeight}")
                self.assertGreater(vroom, 0, 'setup left no vertical scroll room for Arrow pan')
                await page.evaluate("() => document.getElementById('mm-stage').focus()")
                await page.keyboard.press('ArrowDown')
                await page.wait_for_timeout(150)
                self.assertGreater(await page.evaluate("() => document.getElementById('mm-stage').scrollTop"), 0,
                    'ArrowDown no longer pans the stage')
                # Escape contexts unchanged.
                await page.locator('#mm-search').fill('write')
                await page.wait_for_timeout(200)
                await expect(page.locator('#mm-hits')).to_be_visible()
                await page.locator('#mm-search').press('Escape')
                await expect(page.locator('#mm-hits')).to_be_hidden()
                await page.locator('button[data-mm-tool="write_fix"]').click()
                await expect(page.locator('#mm-detail')).to_be_visible()
                await page.locator('#mm-detail').press('Escape')
                await expect(page.locator('#mm-detail')).to_be_hidden()
                await expect(page.locator('button[data-mm-tool="write_fix"]')).to_be_focused()
                self.assertEqual(errors, [])
                await page.close()
            finally:
                await browser.close()

    async def test_matrix_touch_pinch_zooms_and_drag_squashes(self):
        """T-C3: 2-finger pinch zooms about the midpoint, touch-action is
        pan-x pan-y, and a >12px touch move squashes the following click."""
        try:
            from playwright.async_api import async_playwright
        except ImportError:
            self.skipTest('Playwright is not installed')
        PINCH = """(a) => {
            const st = document.getElementById('mm-stage');
            const mk = (id, x, y) => new Touch({identifier: id, target: st, clientX: x, clientY: y});
            const fire = (type, pts) => st.dispatchEvent(new TouchEvent(type, {bubbles: true, cancelable: true, touches: pts, targetTouches: pts, changedTouches: pts}));
            fire('touchstart', [mk(1, a.x1, a.y1), mk(2, a.x2, a.y2)]);
            fire('touchmove', [mk(1, a.x3, a.y3), mk(2, a.x4, a.y4)]);
            fire('touchend', []);
        }"""
        SWIPE = """(a) => {
            const st = document.getElementById('mm-stage');
            const mk = (id, x, y) => new Touch({identifier: id, target: st, clientX: x, clientY: y});
            const fire = (type, pts) => st.dispatchEvent(new TouchEvent(type, {bubbles: true, cancelable: true, touches: pts, targetTouches: pts, changedTouches: pts}));
            fire('touchstart', [mk(1, a.x, a.y)]);
            fire('touchmove', [mk(1, a.x + 20, a.y)]);
            fire('touchend', []);
        }"""
        EMPTY = """() => {
            const st = document.getElementById('mm-stage');
            const cv = document.getElementById('mm-canvas');
            const s = st.getBoundingClientRect(), r = cv.getBoundingClientRect();
            const left = Math.max(s.left, r.left) + 3, right = Math.min(s.right, r.right) - 3;
            const top = Math.max(s.top, r.top) + 3, bottom = Math.min(s.bottom, r.bottom) - 3;
            for (let y = top; y < bottom; y += 5) {
                for (let x = left; x < right; x += 5) {
                    const el = document.elementFromPoint(x, y);
                    if (!el || !el.closest('#mm-canvas')) continue;
                    if (el.closest('button, a, .mm-wires, .mm-relations')) continue;
                    return {x: x, y: y};
                }
            }
            return null;
        }"""
        CENTRE = """() => {
            const t = document.querySelector('.mm-tier[data-mm-tier="T1"]');
            const r = t.getBoundingClientRect();
            return {x: (r.left + r.right) / 2, y: (r.top + r.bottom) / 2,
                    label: document.getElementById('mm-zoom-level').textContent};
        }"""
        async with async_playwright() as pw:
            if not Path(pw.chromium.executable_path).is_file():
                self.skipTest('Playwright Chromium is not installed')
            browser = await pw.chromium.launch()
            try:
                page = await browser.new_page(viewport={'width': 1440, 'height': 900}, reduced_motion='reduce')
                errors = []
                page.on('pageerror', lambda error: errors.append(str(error)))
                await page.route('**/api/monitor/activity*', lambda route: route.fulfill(status=502, json={}))
                await page.goto(str(self.client.make_url('/matrixtool')))
                await page.wait_for_selector('#mm-canvas')
                await page.wait_for_timeout(400)
                ta = await page.evaluate("() => getComputedStyle(document.getElementById('mm-stage')).touchAction")
                self.assertEqual(ta, 'pan-x pan-y', 'stage touch-action is not pan-x pan-y: ' + str(ta))
                c0 = await page.evaluate(CENTRE)
                await page.evaluate(PINCH, {'x1': c0['x'] - 50, 'y1': c0['y'], 'x2': c0['x'] + 50, 'y2': c0['y'],
                                            'x3': c0['x'] - 80, 'y3': c0['y'], 'x4': c0['x'] + 80, 'y4': c0['y']})
                await page.wait_for_timeout(300)
                c1 = await page.evaluate(CENTRE)
                self.assertNotEqual(c1['label'], '100%', 'pinch did not zoom')
                self.assertAlmostEqual(c1['x'], c0['x'], delta=2.0, msg='pinch did not anchor the midpoint X')
                self.assertAlmostEqual(c1['y'], c0['y'], delta=2.0, msg='pinch did not anchor the midpoint Y')
                # >12px touch move squashes the following click.
                await page.evaluate(SWIPE, {'x': c0['x'], 'y': c0['y']})
                pt = await page.evaluate(EMPTY)
                self.assertIsNotNone(pt)
                await page.mouse.click(pt['x'], pt['y'])
                await page.wait_for_timeout(100)
                mid = await page.evaluate(CENTRE)
                self.assertNotEqual(mid['label'], '100%', 'a click after a touch swipe reset the map (squash missing)')
                await page.wait_for_timeout(300)
                pt2 = await page.evaluate(EMPTY)
                self.assertIsNotNone(pt2)
                await page.mouse.click(pt2['x'], pt2['y'])
                await page.wait_for_timeout(100)
                single = await page.evaluate(CENTRE)
                self.assertEqual(single['label'], mid['label'], 'an empty single click changed the zoom')
                self.assertNotEqual(single['label'], '100%', 'the pinch zoom was lost after an empty single click')
                await page.mouse.dblclick(pt2['x'], pt2['y'])
                await page.wait_for_timeout(300)
                a = await page.evaluate(CENTRE)
                self.assertEqual(a['label'], '100%', 'an empty double click did not reset the zoom')
                open_nodes = await page.locator('#mm-canvas [aria-expanded="true"]').count()
                self.assertEqual(open_nodes, 0, 'an empty double click did not close every map node')
                self.assertEqual(errors, [])
                await page.close()
            finally:
                await browser.close()

    async def test_matrix_return_flash_on_panel_close(self):
        """T-C4: Escape/X return-to-chip adds a paint-only .mm-return-flash,
        removed after ~2500ms; rects unchanged; focus contract intact; under
        reduce there is no flash."""
        try:
            from playwright.async_api import async_playwright, expect
        except ImportError:
            self.skipTest('Playwright is not installed')
        RECTS = """() => {
            const SEL = '.mm-root, .mm-tier, .mm-cluster-btn, .mm-group > button, .mm-bucket-btn, .mm-tools > button, .mm-bucket-body > button';
            const c = document.getElementById('mm-canvas').getBoundingClientRect();
            const out = [];
            for (const n of document.querySelectorAll('#mm-canvas ' + SEL)) {
                const r = n.getBoundingClientRect();
                // Hidden cards return a zero rect at the absolute origin; skip them so
                // the census is scroll-independent. Visible cards relative rects cancel
                // document scroll, stage scroll and the pan transform on #mm-canvas.
                if (r.width < 1 || r.height < 1) continue;
                out.push([r.left - c.left, r.top - c.top, r.right - c.left, r.bottom - c.top]);
            }
            return out;
        }"""
        FLASH = """() => {
            const c = document.querySelector('button[data-mm-tool="write_fix"]');
            return {flash: c.classList.contains('mm-return-flash'), transform: getComputedStyle(c).transform};
        }"""
        async with async_playwright() as pw:
            if not Path(pw.chromium.executable_path).is_file():
                self.skipTest('Playwright Chromium is not installed')
            browser = await pw.chromium.launch()
            try:
                for reduce in (False, True):
                    page = await browser.new_page(viewport={'width': 1440, 'height': 900},
                                                  reduced_motion='reduce' if reduce else 'no-preference')
                    errors = []
                    page.on('pageerror', lambda error: errors.append(str(error)))
                    await page.route('**/api/monitor/activity*', lambda route: route.fulfill(status=502, json={}))
                    await page.goto(str(self.client.make_url('/matrixtool')))
                    await page.wait_for_selector('#mm-canvas')
                    await page.wait_for_timeout(400)
                    await page.locator('.mm-tier[data-mm-tier="T2"]').press('Enter')
                    await page.locator('#mm-groups-T2 .mm-cluster[data-mm-cluster="code"] .mm-cluster-btn').press('Enter')
                    await page.locator('button[data-mm-group="T2|write"]').press('Enter')
                    await page.wait_for_timeout(400)
                    before = await page.evaluate(RECTS)
                    await page.locator('button[data-mm-tool="write_fix"]').click()
                    await expect(page.locator('#mm-detail')).to_be_visible()
                    await page.locator('#mm-detail').press('Escape')
                    await expect(page.locator('button[data-mm-tool="write_fix"]')).to_be_focused()
                    f = await page.evaluate(FLASH)
                    self.assertEqual(f['transform'], 'none', 'the flash changed transform')
                    if reduce:
                        self.assertFalse(f['flash'], 'reduce should skip the return flash')
                    else:
                        self.assertTrue(f['flash'], 'Escape return did not add .mm-return-flash')
                        after = await page.evaluate(RECTS)
                        delta = max(abs(after[i][j] - before[i][j]) for i in range(len(before)) for j in range(4))
                        self.assertLessEqual(delta, 0.02, 'the flash moved a rect by ' + str(delta))
                        await page.wait_for_timeout(2700)
                        g = await page.evaluate(FLASH)
                        self.assertFalse(g['flash'], 'the return flash was not removed after its timeout')
                    self.assertEqual(errors, [])
                    await page.close()
            finally:
                await browser.close()

    async def test_matrix_theme_toggle_persists(self):
        """T-C5: the theme button and 't' flip data-theme + localStorage; reload
        persists the stored choice over the system scheme. The existing WCAG
        both-palette lock stays green unmodified (fresh contexts, no stamp)."""
        try:
            from playwright.async_api import async_playwright
        except ImportError:
            self.skipTest('Playwright is not installed')
        BG = "() => getComputedStyle(document.body).backgroundColor"
        THEME = "() => document.documentElement.getAttribute('data-theme')"
        STORE = "() => localStorage.getItem('toola_matrix_theme')"
        PRESSED = "() => document.querySelector('[data-mm-view-action=\"theme\"]').getAttribute('aria-pressed')"
        async with async_playwright() as pw:
            if not Path(pw.chromium.executable_path).is_file():
                self.skipTest('Playwright Chromium is not installed')
            browser = await pw.chromium.launch()
            try:
                page = await browser.new_page(viewport={'width': 1440, 'height': 900}, color_scheme='dark')
                errors = []
                page.on('pageerror', lambda error: errors.append(str(error)))
                await page.route('**/api/monitor/activity*', lambda route: route.fulfill(status=502, json={}))
                await page.goto(str(self.client.make_url('/matrixtool')))
                await page.wait_for_selector('#mm-canvas')
                await page.wait_for_timeout(400)
                self.assertEqual(await page.locator('[data-mm-view-action="theme"]').count(), 1,
                    'no theme toggle button in the control bar')
                self.assertIsNone(await page.evaluate(THEME), 'load stamped data-theme with nothing stored')
                bg0 = await page.evaluate(BG)
                await page.click('[data-mm-view-action="theme"]')
                await page.wait_for_timeout(200)
                self.assertEqual(await page.evaluate(THEME), 'light', 'toggle from dark scheme did not go light')
                self.assertEqual(await page.evaluate(STORE), 'light')
                self.assertEqual(await page.evaluate(PRESSED), 'true')
                bg1 = await page.evaluate(BG)
                self.assertNotEqual(bg1, bg0, 'the light theme did not change the page background')
                await page.reload()
                await page.wait_for_selector('#mm-canvas')
                await page.wait_for_timeout(400)
                self.assertEqual(await page.evaluate(THEME), 'light', 'stored theme did not survive reload')
                self.assertEqual(await page.evaluate(BG), bg1)
                self.assertEqual(await page.evaluate(PRESSED), 'true', 'reload did not restore the toggle aria-pressed')
                await page.locator('h1').click()
                await page.keyboard.press('t')
                await page.wait_for_timeout(200)
                self.assertEqual(await page.evaluate(THEME), 'dark', "'t' did not flip back to dark")
                self.assertEqual(await page.evaluate(STORE), 'dark')
                self.assertEqual(await page.evaluate(PRESSED), 'false')
                bg2 = await page.evaluate(BG)
                self.assertNotEqual(bg2, bg1, 'forced dark kept the light background')
                await page.reload()
                await page.wait_for_selector('#mm-canvas')
                await page.wait_for_timeout(400)
                self.assertEqual(await page.evaluate(THEME), 'dark', 'forced dark did not survive reload')
                self.assertEqual(errors, [])
                await page.close()
            finally:
                await browser.close()

    # ------------------------------------------------------------------
    # A3 PANEL REWORK (direction B, Anh 2026-11-25): the chain panel is a NODE
    # IN THE MAP -- absolute inside #mm-canvas, anchored in map coords, pans and
    # zooms WITH the canvas. T-P1 mount+anchor, T-P2 moves-with-map / not
    # viewport-pinned, T-P3 census (max-content + overflow) @320/390/1440,
    # T-P4 mate/x survive the canvas click path incl. the post-drag squash.
    # ------------------------------------------------------------------

    async def test_matrix_chain_panel_is_an_absolute_node_inside_the_canvas(self):
        """T-P1: mount + position + map-coord anchor below the chip, clamped inside
        the canvas right edge (no scrollable-overflow feed)."""
        try:
            from playwright.async_api import async_playwright, expect
        except ImportError:
            self.skipTest('Playwright is not installed')
        ANCHOR = """() => {
            const cv = document.getElementById('mm-canvas');
            const p = cv.querySelector(':scope > .mm-chain-panel');
            const chip = cv.querySelector('[data-mm-tool="write_fix"]');
            if (!p || !chip) return null;
            const k = parseFloat(cv.style.zoom || '1') || 1;
            const cr = chip.getBoundingClientRect(), pr = p.getBoundingClientRect(),
                  vr = cv.getBoundingClientRect();
            return {position: getComputedStyle(p).position,
                    parent: p.parentElement.id,
                    dx: (pr.left - cr.left) / k,
                    dy: (pr.top - cr.bottom) / k,
                    left: (pr.left - vr.left) / k,
                    chipLeft: (cr.left - vr.left) / k,
                    width: pr.width / k,
                    canvasW: cv.offsetWidth};
        }"""
        async with async_playwright() as pw:
            if not Path(pw.chromium.executable_path).is_file():
                self.skipTest('Playwright Chromium is not installed')
            browser = await pw.chromium.launch()
            try:
                page = await browser.new_page(viewport={'width': 1440, 'height': 900}, reduced_motion='reduce')
                errors = []
                page.on('pageerror', lambda error: errors.append(str(error)))
                await page.route('**/api/monitor/activity*', lambda route: route.fulfill(status=502, json={}))
                await page.goto(str(self.client.make_url('/matrixtool')))
                await page.wait_for_selector('#mm-canvas')
                await page.locator('.mm-tier[data-mm-tier="T2"]').press('Enter')
                await page.locator('#mm-groups-T2 .mm-cluster[data-mm-cluster="code"] .mm-cluster-btn').press('Enter')
                await page.locator('button[data-mm-group="T2|write"]').press('Enter')
                await page.locator('[data-mm-tool="write_fix"]').click()
                await expect(page.locator('#mm-detail')).to_be_visible()
                a = await page.evaluate(ANCHOR)
                self.assertIsNotNone(a, 'panel or anchor chip missing')
                self.assertEqual(a['position'], 'absolute', 'panel is not position:absolute')
                self.assertEqual(a['parent'], 'mm-canvas', 'panel is not a direct child of #mm-canvas')
                # left = chip left, clamped so the 268px panel never overflows the canvas
                # right edge (item 6). Near the right edge the clamp legitimately pulls the
                # panel left of the chip; the invariant is the clamp formula, not dx==0.
                expected_left = max(0, min(a['chipLeft'], a['canvasW'] - 268))
                self.assertAlmostEqual(a['left'], expected_left, delta=0.5,
                    msg='panel left != min(chip left, canvasW-268): ' + str(a))
                self.assertAlmostEqual(a['dy'], 4, delta=0.5, msg='panel not one node (4px) below the chip')
                self.assertLessEqual(a['left'] + a['width'], a['canvasW'] + 0.5,
                    msg='panel overflows the canvas right edge: ' + str(a))
                self.assertEqual(errors, [])
                await page.close()
            finally:
                await browser.close()

    async def test_matrix_chain_panel_travels_with_the_map_not_the_viewport(self):
        """T-P2: after pan, zoom and a viewport resize the panel's MAP coords relative
        to its chip are unchanged, while its viewport rect moves -- it is anchored in
        the map, not pinned to the screen."""
        try:
            from playwright.async_api import async_playwright, expect
        except ImportError:
            self.skipTest('Playwright is not installed')
        REL = """() => {
            const cv = document.getElementById('mm-canvas');
            const p = cv.querySelector(':scope > .mm-chain-panel');
            const chip = cv.querySelector('[data-mm-tool="write_fix"]');
            const k = parseFloat(cv.style.zoom || '1') || 1;
            const cr = chip.getBoundingClientRect(), pr = p.getBoundingClientRect();
            return {dx: (pr.left - cr.left) / k, dy: (pr.top - cr.bottom) / k,
                    vx: pr.left, vy: pr.top, zoom: k};
        }"""
        async with async_playwright() as pw:
            if not Path(pw.chromium.executable_path).is_file():
                self.skipTest('Playwright Chromium is not installed')
            browser = await pw.chromium.launch()
            try:
                page = await browser.new_page(viewport={'width': 1440, 'height': 900}, reduced_motion='reduce')
                errors = []
                page.on('pageerror', lambda error: errors.append(str(error)))
                await page.route('**/api/monitor/activity*', lambda route: route.fulfill(status=502, json={}))
                await page.goto(str(self.client.make_url('/matrixtool')))
                await page.wait_for_selector('#mm-canvas')
                await page.locator('.mm-tier[data-mm-tier="T2"]').press('Enter')
                await page.locator('#mm-groups-T2 .mm-cluster[data-mm-cluster="code"] .mm-cluster-btn').press('Enter')
                await page.locator('button[data-mm-group="T2|write"]').press('Enter')
                await page.locator('[data-mm-tool="write_fix"]').click()
                await expect(page.locator('.mm-chain-panel')).to_be_visible()
                base = await page.evaluate(REL)
                box = await page.locator('#mm-stage').bounding_box()
                sx, sy = box['x'] + box['width'] / 2, box['y'] + box['height'] / 2
                await page.mouse.move(sx, sy)
                await page.mouse.down()
                await page.mouse.move(sx - 120, sy - 40)
                await page.mouse.up()
                await page.wait_for_timeout(300)
                pan = await page.evaluate(REL)
                self.assertAlmostEqual(pan['dx'], base['dx'], delta=0.5, msg='pan changed panel map-x relative to chip')
                self.assertAlmostEqual(pan['dy'], base['dy'], delta=0.5, msg='pan changed panel map-y relative to chip')
                self.assertTrue(pan['vx'] != base['vx'] or pan['vy'] != base['vy'],
                    'panel did not move on screen with the pan -- still viewport-pinned?')
                await page.click('[data-mm-view-action="zoom-in"]')
                await page.wait_for_timeout(250)
                zoom = await page.evaluate(REL)
                self.assertGreater(zoom['zoom'], base['zoom'], 'zoom-in did not raise the canvas zoom')
                self.assertAlmostEqual(zoom['dx'], base['dx'], delta=0.5, msg='zoom changed panel map-x relative to chip')
                self.assertAlmostEqual(zoom['dy'], base['dy'], delta=0.5, msg='zoom changed panel map-y relative to chip')
                await page.set_viewport_size({'width': 1000, 'height': 760})
                await page.wait_for_timeout(300)
                res = await page.evaluate(REL)
                self.assertAlmostEqual(res['dx'], base['dx'], delta=0.5, msg='viewport resize changed panel map-x relative to chip')
                self.assertAlmostEqual(res['dy'], base['dy'], delta=0.5, msg='viewport resize changed panel map-y relative to chip')
                self.assertEqual(errors, [])
                await page.close()
            finally:
                await browser.close()

    async def test_matrix_chain_panel_feeds_no_canvas_width_or_overflow(self):
        """T-P3: the max-content risk, measured. Opening the panel must not grow the
        canvas intrinsic width (offsetWidth), its right edge at scrollLeft 0, or the
        stage's scrollWidth -- absolute is out of flow and left is clamped inside the
        canvas. Census :667/:1007 premise, re-verified @320/390/1440."""
        try:
            from playwright.async_api import async_playwright, expect
        except ImportError:
            self.skipTest('Playwright is not installed')
        CENSUS = """() => {
            const cv = document.getElementById('mm-canvas');
            const st = document.getElementById('mm-stage');
            st.scrollTo(0, 0);
            const vr = cv.getBoundingClientRect();
            const chip = cv.querySelector('[data-mm-tool="write_fix"]');
            const cr = chip ? chip.getBoundingClientRect() : null;
            return {offsetW: cv.offsetWidth, right: vr.right,
                    scrollW: st.scrollWidth, clientW: st.clientWidth,
                    chipDx: cr ? cr.left - vr.left : null,
                    chipDy: cr ? cr.top - vr.top : null,
                    panels: cv.querySelectorAll(':scope > .mm-chain-panel').length,
                    pageFits: document.documentElement.scrollWidth === innerWidth};
        }"""
        async with async_playwright() as pw:
            if not Path(pw.chromium.executable_path).is_file():
                self.skipTest('Playwright Chromium is not installed')
            browser = await pw.chromium.launch()
            try:
                for width in (1440, 390, 320):
                    with self.subTest(width=width):
                        page = await browser.new_page(viewport={'width': width, 'height': 800}, reduced_motion='reduce')
                        errors = []
                        page.on('pageerror', lambda error: errors.append(str(error)))
                        await page.route('**/api/monitor/activity*', lambda route: route.fulfill(status=502, json={}))
                        await page.goto(str(self.client.make_url('/matrixtool')))
                        await page.wait_for_selector('#mm-canvas')
                        await page.locator('.mm-tier[data-mm-tier="T2"]').press('Enter')
                        await page.locator('#mm-groups-T2 .mm-cluster[data-mm-cluster="code"] .mm-cluster-btn').press('Enter')
                        await page.locator('button[data-mm-group="T2|write"]').press('Enter')
                        await page.wait_for_timeout(200)
                        closed = await page.evaluate(CENSUS)
                        self.assertEqual(closed['panels'], 0)
                        await page.locator('[data-mm-tool="write_fix"]').click()
                        await expect(page.locator('.mm-chain-panel')).to_be_visible()
                        await page.wait_for_timeout(200)
                        opened = await page.evaluate(CENSUS)
                        self.assertEqual(opened['panels'], 1)
                        self.assertEqual(opened['offsetW'], closed['offsetW'],
                            msg='w' + str(width) + ': panel grew the canvas max-content width')
                        self.assertAlmostEqual(opened['right'], closed['right'], delta=0.5,
                            msg='w' + str(width) + ': panel moved the canvas right edge')
                        self.assertAlmostEqual(opened['scrollW'], closed['scrollW'], delta=1,
                            msg='w' + str(width) + ': panel fed the stage scrollable overflow')
                        self.assertAlmostEqual(opened['chipDx'], closed['chipDx'], delta=0.5,
                            msg='w' + str(width) + ': selection moved the anchor chip (census :1007)')
                        self.assertAlmostEqual(opened['chipDy'], closed['chipDy'], delta=0.5,
                            msg='w' + str(width) + ': selection moved the anchor chip (census :1007)')
                        self.assertTrue(opened['pageFits'], 'w' + str(width) + ': page no longer fits the viewport')
                        self.assertEqual(errors, [])
                        await page.close()
            finally:
                await browser.close()

    async def test_matrix_chain_panel_mate_and_close_survive_the_canvas_click_path(self):
        """T-P4: the document delegate still catches mate/x now that the panel is a
        canvas child -- including inside the 250ms post-drag squash window, which the
        capture handler exempts for panel controls."""
        try:
            from playwright.async_api import async_playwright, expect
        except ImportError:
            self.skipTest('Playwright is not installed')
        async with async_playwright() as pw:
            if not Path(pw.chromium.executable_path).is_file():
                self.skipTest('Playwright Chromium is not installed')
            browser = await pw.chromium.launch()
            try:
                page = await browser.new_page(viewport={'width': 1440, 'height': 900}, reduced_motion='reduce')
                errors = []
                page.on('pageerror', lambda error: errors.append(str(error)))
                await page.route('**/api/monitor/activity*', lambda route: route.fulfill(status=502, json={}))
                await page.goto(str(self.client.make_url('/matrixtool')))
                await page.wait_for_selector('#mm-canvas')
                await page.locator('.mm-tier[data-mm-tier="T2"]').press('Enter')
                await page.locator('#mm-groups-T2 .mm-cluster[data-mm-cluster="code"] .mm-cluster-btn').press('Enter')
                await page.locator('button[data-mm-group="T2|write"]').press('Enter')
                await page.locator('[data-mm-tool="write_fix"]').click()
                await expect(page.locator('.mm-chain-panel')).to_be_visible()
                await page.locator('.mm-chain-panel-close').click()
                await expect(page.locator('.mm-chain-panel')).to_have_count(0)
                await page.locator('[data-mm-tool="write_fix"]').click()
                await expect(page.locator('.mm-chain-panel')).to_be_visible()
                box = await page.locator('#mm-stage').bounding_box()
                sx, sy = box['x'] + box['width'] / 2, box['y'] + box['height'] / 2
                await page.mouse.move(sx, sy)
                await page.mouse.down()
                await page.mouse.move(sx - 90, sy)
                await page.mouse.up()
                mate = await page.evaluate("""() => {
                    const b = document.querySelector('.mm-chain-panel .mm-chain-step:not(.is-current)');
                    if (!b) return null;
                    const name = b.dataset.mmChainTool;
                    b.click();
                    return name;
                }""")
                self.assertIsNotNone(mate, 'no mate button to jump to')
                await expect(page.locator('[data-mm-tool="' + mate + '"]')).to_have_attribute('aria-current', 'true')
                await expect(page.locator('.mm-chain-panel')).to_have_count(1)
                box = await page.locator('#mm-stage').bounding_box()
                sx, sy = box['x'] + box['width'] / 2, box['y'] + box['height'] / 2
                await page.mouse.move(sx, sy)
                await page.mouse.down()
                await page.mouse.move(sx - 90, sy)
                await page.mouse.up()
                await page.evaluate("() => document.querySelector('.mm-chain-panel-close').click()")
                await expect(page.locator('.mm-chain-panel')).to_have_count(0)
                self.assertEqual(errors, [])
                await page.close()
            finally:
                await browser.close()
