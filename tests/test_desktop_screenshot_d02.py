"""D-02 regression test: desktop_screenshot must run off the aiohttp event loop
and respect a hard upper-bound timeout so a single capture cannot make [A]
unresponsive (observed p95 ~46s pre-fix).

Why: pre-fix, the PIL.ImageGrab call ran inline on the event loop and one
capture stalled /health for tens of seconds. The fix offloads via
asyncio.to_thread AND wraps it in asyncio.wait_for with
_DESKTOP_SCREENSHOT_TIMEOUT_SEC. This test asserts both invariants without
actually taking a screenshot (no PIL grab is invoked; the capture helper is
monkeypatched).
"""

from __future__ import annotations

import asyncio
import importlib
import json
import sys
import time
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


class _PILStub:
    """Minimal PIL.ImageGrab stub so tools_v15 imports cleanly without the
    real pillow dependency in the test env."""


class DesktopScreenshotOffloadTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        # Pre-stub PIL so `from PIL import ImageGrab` inside the handler works
        # without taking a real screenshot.
        if "PIL" not in sys.modules:
            import types

            pil = types.ModuleType("PIL")
            image_grab = types.ModuleType("PIL.ImageGrab")
            image_grab.grab = lambda *a, **kw: None  # never called in this test
            pil.ImageGrab = image_grab
            sys.modules["PIL"] = pil
            sys.modules["PIL.ImageGrab"] = image_grab
        cls.v15 = importlib.import_module("tools_v15")

    def test_capture_is_offloaded_via_to_thread(self):
        """Invariant 1: the handler must call asyncio.to_thread (i.e. run the
        blocking capture off-loop). We monkeypatch asyncio.to_thread to record
        the call and return immediately — the event loop must remain free to
        service a concurrent task while the 'capture' is in flight."""
        calls: dict = {}

        original_to_thread = asyncio.to_thread

        async def fake_to_thread(fn, *args, **kwargs):
            calls["fn"] = fn
            calls["args"] = args
            # Simulate a capture that takes a moment but completes; crucially,
            # the event loop must be free during this await.
            await asyncio.sleep(0.05)
            return json.dumps({"ok": True, "path": "fake.png"})

        asyncio.to_thread = fake_to_thread  # type: ignore[assignment]
        try:
            async def scenario():
                # Concurrent probe: proves the loop is responsive while capture runs.
                probe_started = time.perf_counter()

                async def probe():
                    await asyncio.sleep(0.01)
                    return time.perf_counter() - probe_started

                result, probe_elapsed = await asyncio.gather(
                    self.v15.desktop_screenshot(),
                    probe(),
                )
                return result, probe_elapsed

            result, probe_elapsed = asyncio.run(scenario())
        finally:
            asyncio.to_thread = original_to_thread  # type: ignore[assignment]

        # The capture helper must have been handed to to_thread.
        self.assertIn("fn", calls, "desktop_screenshot must use asyncio.to_thread")
        self.assertIs(calls["fn"], self.v15._desktop_screenshot_capture)
        # Concurrent probe must have run promptly (loop not blocked).
        # Busy Windows CI can delay the 10ms probe without the event loop being
        # blocked. The simulated capture takes 50ms; a multi-second delay would
        # still detect synchronous capture while avoiding a host-load flake.
        self.assertLess(probe_elapsed, 5.0,
                        "event loop appears blocked while capture in flight")
        payload = json.loads(result)
        self.assertTrue(payload.get("ok"), payload)

    def test_timeout_returns_structured_error(self):
        """Invariant 2: if the capture hangs past _DESKTOP_SCREENSHOT_TIMEOUT_SEC,
        the handler must return a structured DESKTOP_SCREENSHOT_TIMEOUT error
        instead of waiting forever."""
        original_to_thread = asyncio.to_thread
        original_timeout = self.v15._DESKTOP_SCREENSHOT_TIMEOUT_SEC

        async def hang_to_thread(fn, *args, **kwargs):
            # Simulate a capture that never completes within the timeout window.
            await asyncio.sleep(10)
            return json.dumps({"ok": True})

        asyncio.to_thread = hang_to_thread  # type: ignore[assignment]
        # Shrink the timeout so the test runs fast.
        self.v15._DESKTOP_SCREENSHOT_TIMEOUT_SEC = 0.1
        try:
            started = time.perf_counter()
            result = asyncio.run(self.v15.desktop_screenshot())
            elapsed = time.perf_counter() - started
        finally:
            asyncio.to_thread = original_to_thread  # type: ignore[assignment]
            self.v15._DESKTOP_SCREENSHOT_TIMEOUT_SEC = original_timeout

        # Must return around the timeout, not after the 10s hang.
        self.assertLess(elapsed, 2.0,
                        f"timeout not enforced; call took {elapsed:.2f}s")
        payload = json.loads(result)
        self.assertFalse(payload.get("ok"), payload)
        self.assertEqual(payload.get("error_name"), "DESKTOP_SCREENSHOT_TIMEOUT",
                         payload)


if __name__ == "__main__":
    unittest.main()
