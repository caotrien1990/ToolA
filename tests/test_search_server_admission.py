from __future__ import annotations

import asyncio
import json
import threading
import unittest
from unittest import mock

import orchestrator_server
from orchestrator.core import call_cache
from orchestrator.tools import search as search_module


class TestSearchServerAdmission(unittest.IsolatedAsyncioTestCase):
    """Server admission must not hold duplicate callers outside every dedupe layer."""

    def setUp(self) -> None:
        orchestrator_server._TOOL_SEMS.clear()
        orchestrator_server._SEMAPHORE_HOLDERS.clear()
        orchestrator_server._SEMAPHORE_WAITERS.clear()
        orchestrator_server._INFLIGHT_OPERATIONS.clear()
        # The call cache joins duplicate concurrent calls, so a key left behind by an
        # earlier test turns THIS test's leader into a joiner that waits
        # CALL_SINGLEFLIGHT_WAIT_S on a leader that is already gone -- the exact trap
        # call_cache documents at its own reset helper. Clearing it is test isolation,
        # not a workaround: this test owns the dedupe state it measures.
        with call_cache._INFLIGHT_LOCK:
            call_cache._INFLIGHT.clear()
        with search_module._SEARCH_SINGLEFLIGHT_LOCK:
            self.assertFalse(search_module._SEARCH_SINGLEFLIGHT)

    def tearDown(self) -> None:
        orchestrator_server._TOOL_SEMS.clear()
        orchestrator_server._SEMAPHORE_HOLDERS.clear()
        orchestrator_server._SEMAPHORE_WAITERS.clear()
        orchestrator_server._INFLIGHT_OPERATIONS.clear()
        with call_cache._INFLIGHT_LOCK:
            call_cache._INFLIGHT.clear()

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
                         transport="http",
                    )
                )
                for _ in range(caller_count)
            ]
            try:
                await self._wait_thread_event(producer_entered)
                entrants = await self._wait_for_entrants(1)
                # INVARIANT, not mechanism. This used to assert entrants == caller_count:
                # that EVERY duplicate caller reaches the tool, so they could dedupe at the
                # search singleflight one level down. Since fb2574c the call cache joins
                # duplicates one layer HIGHER (call_cache._INFLIGHT, whitelisted to
                # CACHEABLE_TOOLS, and search_code is in it), so the leader executes and the
                # other callers never enter the tool at all. Measured 2026-09-22 with
                # read_limit=8 / caller_count=12: entrants=1, producer_count=1,
                # _SEMAPHORE_WAITERS={}, cache _INFLIGHT=1 key, and 12/12 responses 200 with
                # the identical payload and no queue_wait_ms. One execution serving twelve
                # callers is strictly better than twelve entrants each taking read admission
                # to do redundant work, so re-pinning the old count would force the join
                # back out of the product.
                # What must never regress is the bug this test was actually written for:
                # admission holding duplicate callers OUTSIDE every dedupe layer. That is the
                # _SEMAPHORE_WAITERS assert below, plus these bounds -- at least one caller
                # must get through, and admission must never admit more than its own limit.
                self.assertGreaterEqual(
                    entrants,
                    1,
                    "no caller reached the tool at all",
                )
                self.assertLessEqual(
                    entrants,
                    read_limit,
                    "read admission let more callers in than its own limit",
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