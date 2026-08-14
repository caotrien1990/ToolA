from __future__ import annotations

import asyncio
import json
import threading
import unittest
from unittest import mock

import orchestrator_server
from orchestrator.tools import search as search_module


class TestSearchServerAdmission(unittest.IsolatedAsyncioTestCase):
    """Server admission must not hold duplicate searches outside singleflight."""

    def setUp(self) -> None:
        orchestrator_server._TOOL_SEMS.clear()
        orchestrator_server._SEMAPHORE_HOLDERS.clear()
        orchestrator_server._SEMAPHORE_WAITERS.clear()
        orchestrator_server._INFLIGHT_OPERATIONS.clear()
        with search_module._SEARCH_SINGLEFLIGHT_LOCK:
            self.assertFalse(search_module._SEARCH_SINGLEFLIGHT)

    def tearDown(self) -> None:
        orchestrator_server._TOOL_SEMS.clear()
        orchestrator_server._SEMAPHORE_HOLDERS.clear()
        orchestrator_server._SEMAPHORE_WAITERS.clear()
        orchestrator_server._INFLIGHT_OPERATIONS.clear()

    async def _wait_thread_event(
        self,
        event: threading.Event,
        timeout: float = 2.0,
    ) -> None:
        loop = asyncio.get_running_loop()
        observed = await asyncio.wait_for(
            loop.run_in_executor(None, event.wait, timeout),
            timeout + 0.5,
        )
        self.assertTrue(observed, "timed out waiting for the search producer")

    async def _wait_for_entrants(self, expected: int) -> int:
        for _ in range(100):
            if self._entrants >= expected:
                break
            await asyncio.sleep(0.01)
        return self._entrants

    async def test_duplicate_http_dispatch_reaches_one_singleflight_before_release(self):
        read_limit = orchestrator_server._TOOL_SEM_LIMITS["read"]
        caller_count = read_limit + 4
        key = ("server-admission", id(self))
        producer_entered = threading.Event()
        producer_release = threading.Event()
        producer_count = 0
        producer_lock = threading.Lock()
        expected_results = [{"server_admission": "shared"}]
        self._entrants = 0

        def producer():
            nonlocal producer_count
            with producer_lock:
                producer_count += 1
            producer_entered.set()
            if not producer_release.wait(3.0):
                raise TimeoutError("test did not release the producer")
            return expected_results

        async def fake_search_code(query: str = "", **_kwargs) -> str:
            self._entrants += 1
            results = await search_module._run_search_singleflight(key, producer)
            return json.dumps({"results": results, "count": len(results)})

        responses = []
        with mock.patch.dict(
            orchestrator_server.ct.TOOL_MAP,
            {"search_code": fake_search_code},
            clear=False,
        ), mock.patch.object(orchestrator_server.ct, "_log_usage", return_value=None):
            tasks = [
                asyncio.create_task(
                    orchestrator_server.dispatch_tool_call(
                        "search_code",
                        {"query": "same-query"},
                        transport="mcp",
                    )
                )
                for _ in range(caller_count)
            ]
            try:
                await self._wait_thread_event(producer_entered)
                entrants = await self._wait_for_entrants(caller_count)
                self.assertEqual(
                    entrants,
                    caller_count,
                    "global read admission held duplicate callers outside singleflight",
                )
                self.assertEqual(producer_count, 1)
                self.assertEqual(
                    orchestrator_server._SEMAPHORE_WAITERS.get("read", 0),
                    0,
                )
            finally:
                producer_release.set()
                responses = await asyncio.wait_for(
                    asyncio.gather(*tasks),
                    5.0,
                )

        payloads = [json.loads(response.text) for response in responses]
        self.assertTrue(all(response.status == 200 for response in responses))
        self.assertTrue(
            all(
                {"results": payload["results"], "count": payload["count"]}
                == {"results": expected_results, "count": 1}
                for payload in payloads
            )
        )
        self.assertTrue(all("queue_wait_ms" not in payload for payload in payloads))
        with search_module._SEARCH_SINGLEFLIGHT_LOCK:
            self.assertFalse(search_module._SEARCH_SINGLEFLIGHT)


if __name__ == "__main__":
    unittest.main()