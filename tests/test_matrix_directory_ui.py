"""Browser regressions for the Matrix directory and the width of its six views."""
import unittest
from pathlib import Path

from aiohttp import web
from aiohttp.test_utils import TestServer

from orchestrator.web_views import register_web_routes


class MatrixDirectoryTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        try:
            from playwright.async_api import async_playwright
        except ImportError:
            self.skipTest("Playwright is not installed")
        driver = await async_playwright().start()
        self.addAsyncCleanup(driver.stop)
        if not Path(driver.chromium.executable_path).is_file():
            self.skipTest("Playwright Chromium is not installed")
        browser = await driver.chromium.launch()
        self.addAsyncCleanup(browser.close)
        app = web.Application()
        register_web_routes(app)
        server = TestServer(app)
        await server.start_server()
        self.addAsyncCleanup(server.close)
        self.page = await browser.new_page(viewport={"width": 390, "height": 844})
        await self.page.route("**/api/monitor/**", lambda route: route.fulfill(status=503))
        await self.page.goto(str(server.make_url("/matrixtool")))

    async def test_filtered_details_focus_and_empty_state(self):
        from playwright.async_api import expect

        page = self.page
        await page.locator('[data-tab="mindmap"]').focus()
        await page.keyboard.press("ArrowRight")
        inventory = page.locator('[data-tab="inventory"]')
        await expect(inventory).to_have_attribute("aria-selected", "true")
        await page.keyboard.press("/")
        search = page.locator("#search")
        await expect(search).to_be_focused()
        await search.fill("read file range")
        selected = page.locator("#tool-grid .tool-card").first
        await expect(selected).to_have_attribute("data-tool", "read_file_range")
        await selected.focus()
        await page.keyboard.press("Enter")
        detail = page.locator("#detail-panel")
        await expect(detail).to_be_focused()
        await expect(detail.locator(".detail-name")).to_have_text("read_file_range")
        await page.keyboard.press("Escape")
        await expect(selected).to_be_focused()
        await selected.click()
        await search.fill("zzzznotatool9937")
        await expect(detail).to_be_hidden()
        await expect(page.locator("#inventory-empty")).to_be_visible()
        await page.locator("#inventory-reset").click()
        await expect(search).to_be_focused()
        total = await page.locator("#tool-grid .tool-card").count()
        self.assertGreater(total, 1)
        await page.locator("#inventory-category").select_option("git")
        filtered = await page.locator("#tool-grid .tool-card").count()
        self.assertTrue(0 < filtered < total)
        await search.fill("git_status")
        await expect(page.locator("#tool-grid .tool-card").first).to_have_attribute("data-tool", "git_status")
        await page.locator("#tool-grid .tool-card").first.click()
        await page.locator('.inventory-tiers [data-tier="T1"]').click()
        await expect(detail).to_be_hidden()
        await expect(page.locator('.inventory-tiers [data-tier="T1"]')).to_have_attribute("aria-pressed", "false")

    async def test_map_reveals_tool_despite_old_inventory_filters(self):
        from playwright.async_api import expect

        page = self.page
        await page.locator('[data-tab="inventory"]').click()
        await page.locator("#inventory-category").select_option("git")
        await page.locator('.inventory-tiers [data-tier="T1"]').click()
        await page.locator('[data-tab="chains"]').click()
        await page.keyboard.press("/")
        lookup = page.locator("#mm-search")
        await expect(lookup).to_be_focused()
        await lookup.fill("architecture_context_pack")
        await page.locator('#mm-hits [role="option"]').first.click()
        await page.locator("[data-mm-inspect]").click()
        detail = page.locator("#mm-detail")
        await detail.get_by_text("Ghi & nguồn", exact=True).click()
        await expect(detail.get_by_text("Nguồn dẫn", exact=True)).to_be_visible()
        await detail.get_by_role("button", name="Xem trong danh mục").click()
        await expect(page.locator("#detail-panel")).to_be_focused()
        await expect(page.locator("#detail-panel .detail-name")).to_have_text("architecture_context_pack")
        await expect(page.locator("#search")).to_have_value("architecture_context_pack")

    async def test_camera_crosses_home_in_both_directions_and_returns(self):
        page = self.page
        stage = page.locator("#mm-stage")
        canvas = page.locator("#mm-canvas")
        await stage.scroll_into_view_if_needed()

        for zoom_out in (False, True):
            if zoom_out:
                await page.locator('[data-mm-view-action="zoom-out"]').click()
            before = await canvas.bounding_box()
            dx = dy = 0
            for delta_x, delta_y in [(90, 110)] * 7 + [(-100, -105)] * 10 + [(100, 105)] * 10 + [(-90, -110)] * 7:
                x, y = await self.empty_map_point(margin_x=104, margin_y=114)
                await page.mouse.move(x, y)
                await page.mouse.down()
                await page.mouse.move(x + delta_x, y + delta_y, steps=2)
                await page.mouse.up()
                dx += delta_x
                dy += delta_y
                actual = await canvas.bounding_box()
                self.assertAlmostEqual(actual["x"] - before["x"], dx, delta=2)
                self.assertAlmostEqual(actual["y"] - before["y"], dy, delta=2)
            await page.locator('[data-mm-view-action="reset"]').click()

    async def test_touch_swipe_moves_the_map_beyond_top_left(self):
        page = self.page
        await page.locator("#mm-stage").scroll_into_view_if_needed()
        box = await page.locator("#mm-stage").bounding_box()
        x, y = box["x"] + 20, box["y"] + 30
        canvas = page.locator("#mm-canvas")
        before = await canvas.bounding_box()
        page_y = await page.evaluate("scrollY")
        cdp = await page.context.new_cdp_session(page)
        for _ in range(3):
            for kind, points in (("touchStart", [(x, y)]), ("touchMove", [(x + 8, y + 8)]),
                                 ("touchMove", [(x + 82, y + 100)]), ("touchEnd", [])):
                await cdp.send("Input.dispatchTouchEvent", {"type": kind, "touchPoints": [
                    {"id": 1, "x": px, "y": py} for px, py in points]})
        after = await canvas.bounding_box()
        self.assertAlmostEqual(after["x"] - before["x"], 246, delta=2)
        self.assertAlmostEqual(after["y"] - before["y"], 300, delta=2)
        self.assertEqual(await page.evaluate("scrollY"), page_y)

    async def test_touch_placement_survives_reopening_and_connects_to_the_moved_panel(self):
        from playwright.async_api import expect

        page = self.page
        await page.emulate_media(reduced_motion="reduce")
        search = page.locator("#mm-search")
        await search.fill("cbm_edges_query")
        await search.press("Enter")
        await page.locator('[data-mm-view-action="reset"]').click()
        leaf = page.locator('button[data-mm-tool="cbm_edges_query"]')
        key = await leaf.evaluate("el => el.closest('.mm-group').querySelector('button').dataset.mmGroup")
        group = page.locator('button[data-mm-group="' + key + '"]')
        cdp = await page.context.new_cdp_session(page)

        async def drag(node, dx, dy):
            await node.scroll_into_view_if_needed()
            before = await node.bounding_box()
            x, y = before["x"] + before["width"] / 2, before["y"] + before["height"] / 2
            for kind, points in (("touchStart", [(x, y)]), ("touchMove", [(x + dx/2, y + dy/2)]),
                                 ("touchMove", [(x + dx, y + dy)]), ("touchEnd", [])):
                await cdp.send("Input.dispatchTouchEvent", {"type": kind, "touchPoints": [
                    {"id": 1, "x": px, "y": py} for px, py in points]})
            await page.evaluate("new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve)))")
            after = await node.bounding_box()
            self.assertAlmostEqual(after["x"] - before["x"], dx, delta=1)
            self.assertAlmostEqual(after["y"] - before["y"], dy, delta=1)

        await drag(leaf, -45, -32)
        map_position = """el => {const c = document.querySelector('#mm-canvas'), r = el.getBoundingClientRect();
            const origin = c.getBoundingClientRect(), z = parseFloat(c.style.zoom || 1);
            return [(r.left - origin.left)/z, (r.top - origin.top)/z]; }"""
        pin = await leaf.evaluate(map_position)
        await page.wait_for_timeout(270)
        await group.press("Enter")
        await group.press("Enter")
        await expect(leaf).to_be_visible()
        for saved, actual in zip(pin, await leaf.evaluate(map_position)):
            self.assertAlmostEqual(actual, saved, delta=1)
        await leaf.click()
        await drag(page.locator(".mm-chain-panel-head .mm-grip"), -48, 65)
        attached = await page.locator(".mm-chain-connector").evaluate("""el => {
            const line = el.getBoundingClientRect(), m = new DOMMatrix(getComputedStyle(el).transform);
            const x = m.a >= 0 ? line.right : line.left, y = m.b >= 0 ? line.bottom : line.top;
            const card = document.querySelector('.mm-chain-panel').getBoundingClientRect();
            return Math.min(Math.abs(x-card.left),Math.abs(x-card.right)) < 2 && y >= card.top && y <= card.bottom;
        }""")
        self.assertTrue(attached, "connector must end on the panel after dragging the grip")

    async def test_route_order_readability_and_shared_step_navigation(self):
        from playwright.async_api import expect

        page = self.page
        await page.emulate_media(reduced_motion="reduce")
        await page.locator("#mm-search").fill("cbm_edges_query")
        await page.locator('#mm-hits [role="option"]').first.click()
        tray = page.locator(".mm-chain-panel")
        await expect(tray.locator(".mm-chain-progress")).to_have_text("Bước 4 / 6")
        steps = tray.locator(".mm-chain-step")
        self.assertEqual(await steps.evaluate_all("rows => rows.map(r => r.dataset.mmChainTool)"), [
            "cbm_index", "cbm_graph_status", "cbm_nodes_query", "cbm_edges_query",
            "cbm_trace_path", "cbm_get_architecture"])
        positions = await steps.evaluate_all("rows => rows.map(r => r.getBoundingClientRect().top)")
        self.assertTrue(all(y > x for x, y in zip(positions, positions[1:])))
        await expect(tray.locator('[aria-current="step"]')).to_contain_text("cbm_edges_query")
        await expect(tray.locator(".mm-chain-panel-rel")).to_have_count(0)
        stage = await page.locator("#mm-stage").bounding_box()
        card = await tray.bounding_box()
        self.assertGreaterEqual(card["y"], max(0, stage["y"]) - 1)
        self.assertLessEqual(card["y"] + card["height"], stage["y"] + stage["height"] + 1)
        await page.locator('[data-mm-view-action="reset"]').click()
        self.assertGreaterEqual((await tray.bounding_box())["width"], 254)
        await steps.first.click()
        picker = tray.get_by_role("combobox", name="Chọn chuỗi tool")
        await expect(picker).to_have_value("Graph Analysis")
        await picker.select_option("Quality Pipeline")
        await tray.locator('[data-mm-chain-tool="cbm_dead_code"]').first.click()
        await expect(tray.locator(".mm-chain-progress")).to_have_text("Bước 2 / 6")
        await expect(tray.locator('[aria-current="step"]')).to_be_focused()

    async def test_selection_stays_in_place_and_highlight_follows_the_next_node(self):
        from playwright.async_api import expect

        page = self.page
        await page.set_viewport_size({"width": 1440, "height": 900})
        await page.emulate_media(reduced_motion="reduce")
        search = page.locator("#mm-search")
        await search.fill("workflow_read_md")
        await search.press("Enter")
        old = page.locator('button[data-mm-tool="workflow_read_md"]')
        new = old.locator("..").locator('button[data-mm-tool]:not([data-mm-tool="workflow_read_md"])').first
        name = await new.get_attribute("data-mm-tool")
        detail = page.locator("#mm-detail")
        await expect(detail).to_be_hidden()
        await new.scroll_into_view_if_needed()
        await new.hover()
        self.assertTrue(await new.evaluate("el => el.classList.contains('mm-focus-keep')"))
        # Rebuilding the wires used to wipe the hover and restore the previous selection.
        await page.locator('[data-mm-view-action="relations"]').evaluate("el => el.click()")
        self.assertTrue(await new.evaluate("el => el.classList.contains('mm-focus-keep')"))
        geom = """el => { const r = el.getBoundingClientRect(), s = document.querySelector('#mm-stage');
            return [scrollY, s.getBoundingClientRect().top, r.left, r.top]; }"""
        before = await new.evaluate(geom)
        box = await new.bounding_box()
        await page.mouse.click(box["x"] + box["width"]/2, box["y"] + box["height"]/2)
        await expect(new).to_have_attribute("aria-current", "true")
        await expect(detail).to_be_hidden()
        for a, b in zip(before, await new.evaluate(geom)):
            self.assertAlmostEqual(a, b, delta=1)
        self.assertIsNone(await old.get_attribute("aria-current"))
        await page.mouse.move(3, 3)
        self.assertFalse(await old.evaluate("el => el.classList.contains('mm-focus-keep')"))
        branch = page.locator('.mm-tier[data-mm-tier="T2"]')
        await branch.click()
        await page.mouse.move(3, 3)
        await expect(branch).to_have_attribute("aria-current", "location")
        self.assertTrue(await branch.evaluate("el => el.classList.contains('mm-focus-keep')"))
        await expect(page.locator('.mm-chain-panel')).to_have_count(0)
        self.assertIsNone(await new.get_attribute("aria-current"))
        await self.click_empty_map()
        await expect(new).to_have_attribute("aria-current", "true")
        inspect = page.get_by_role("button", name="Xem chi tiết", exact=True)
        await inspect.scroll_into_view_if_needed()
        frame = """() => [scrollY, document.documentElement.scrollHeight,
            document.querySelector('#mm-stage').getBoundingClientRect().top]"""
        before_info = await page.evaluate(frame)
        await inspect.click()
        await expect(detail).to_be_focused()
        await expect(detail.locator(".mm-detail-id strong")).to_have_text(name)
        self.assertEqual(await page.evaluate(frame), before_info)
        await detail.press("Escape")
        await expect(detail).to_be_hidden()
        self.assertEqual(await page.evaluate(frame), before_info)

    async def test_stage_has_no_scrollbar_gutters_across_zoom_threshold(self):
        from playwright.async_api import expect

        page = self.page
        stage = page.locator("#mm-stage")
        dims = """el => [el.clientWidth, el.clientHeight, el.offsetWidth - el.clientWidth,
            el.offsetHeight - el.clientHeight, getComputedStyle(el).scrollbarWidth]"""
        for width in (390, 1440):
            await page.set_viewport_size({"width": width, "height": 900})
            before = await stage.evaluate(dims)
            self.assertEqual(before[2:], [0, 0, "none"])
            for action in ("zoom-in", "zoom-in", "zoom-out", "zoom-out"):
                await page.locator('[data-mm-view-action="' + action + '"]').click()
                self.assertEqual(await stage.evaluate(dims), before)
            await expect(page.locator("#mm-zoom-level")).to_have_text("100%")

    async def empty_map_point(self, margin_x=12, margin_y=12):
        stage = self.page.locator("#mm-stage")
        await stage.scroll_into_view_if_needed()
        point = await stage.evaluate("""(stage, [mx, my]) => {
            const r = stage.getBoundingClientRect();
            for (let y = Math.max(0, r.top) + my; y < Math.min(r.bottom, innerHeight) - my; y += 10) {
                for (let x = Math.max(0, r.left) + mx; x < Math.min(r.right, innerWidth) - mx; x += 10) {
                    const el = document.elementFromPoint(x, y);
                    if (el && stage.contains(el) && !el.closest(
                        'button, a, select, details, .mm-root, .mm-wires, .mm-relations, .mm-chain-panel')) return [x, y];
                }
            }
        }""", [margin_x, margin_y])
        self.assertIsNotNone(point, "an empty part of the visible map is required")
        return point

    async def click_empty_map(self, double=False):
        point = await self.empty_map_point()
        click = self.page.mouse.dblclick if double else self.page.mouse.click
        await click(*point)

    async def test_empty_click_retraces_chain_jump_and_tiers(self):
        from playwright.async_api import expect

        page = self.page
        await page.emulate_media(reduced_motion="reduce")
        await page.locator("#mm-search").fill("cbm_edges_query")
        await page.locator('#mm-hits [role="option"]').first.click()
        card = page.locator(".mm-chain-panel")
        await card.locator('[data-mm-chain-tool="cbm_index"]').click()
        await expect(card).to_have_attribute("aria-label", "Tool chain của cbm_index")
        await self.click_empty_map()
        await expect(card).to_have_attribute("aria-label", "Tool chain của cbm_edges_query")
        await expect(card.locator('[aria-current="step"]')).to_contain_text("cbm_edges_query")
        await self.click_empty_map()
        await expect(card).to_have_count(0)
        await expect(page.locator('.mm-tier[aria-expanded="true"]')).to_have_count(0)
        first = page.locator('.mm-tier[data-mm-tier="T1"]')
        second = page.locator('.mm-tier[data-mm-tier="T2"]')
        await first.click()
        await second.click()
        await self.click_empty_map()
        await expect(first).to_have_attribute("aria-expanded", "true")
        await expect(second).to_have_attribute("aria-expanded", "false")
        await self.click_empty_map(double=True)
        await expect(page.locator('.mm-tier[aria-expanded="true"]')).to_have_count(0)
        await expect(page.locator("#mm-zoom-level")).to_have_text("100%")

    async def test_wheel_stays_in_panel_at_both_scroll_limits(self):
        from playwright.async_api import expect

        page = self.page
        await page.emulate_media(reduced_motion="reduce")
        await page.locator("#mm-search").fill("search_code")
        await page.locator('#mm-hits [role="option"]').first.click()
        card = page.locator(".mm-chain-panel")
        await card.locator("summary").click()
        await card.evaluate("el => el.scrollTop = 0")
        zoom = page.locator("#mm-zoom-level")
        z = await zoom.text_content()
        limits = await card.evaluate("el => el.scrollHeight - el.clientHeight")
        self.assertGreater(limits, 0)
        await card.hover(position={"x": 50, "y": 40})
        for dy, goal in [(800, limits), (800, limits), (-800, 0), (-800, 0)]:
            await page.mouse.wheel(0, dy)
            await page.wait_for_function(
                "n => Math.abs(document.querySelector('.mm-chain-panel').scrollTop - n) < 1", arg=goal)
            await expect(zoom).to_have_text(z)
        stage = page.locator("#mm-stage")
        before = await stage.evaluate("el => [el.scrollLeft, el.scrollTop, scrollY]")
        await page.mouse.wheel(0, -450)
        await page.evaluate("new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve)))")
        self.assertEqual(await stage.evaluate("el => [el.scrollLeft, el.scrollTop, scrollY]"), before)
        await expect(zoom).to_have_text(z)
        await self.click_empty_map()
        await page.mouse.move(*(await self.empty_map_point()))
        await page.mouse.wheel(0, -180)
        await expect(zoom).not_to_have_text(z)

    async def test_all_views_fit_and_open_details_remain_reachable(self):
        page = self.page
        for width in (320, 390, 768, 1440):
            await page.set_viewport_size({"width": width, "height": 840})
            for tab in await page.locator("#tabs .tab").all():
                await tab.click()
                name = await tab.get_attribute("data-tab")
                if name == "inventory":
                    await page.locator("#tool-grid .tool-card").first.click()
                    box = await page.locator("#detail-panel").bounding_box()
                    self.assertIsNotNone(box)
                    self.assertGreaterEqual(box["x"], 0)
                    self.assertLessEqual(box["x"] + box["width"], width + 1)
                    await page.locator("#inventory-close").click()
                self.assertLessEqual(
                    await page.evaluate("document.documentElement.scrollWidth"),
                    width + 1, (width, name),
                )
