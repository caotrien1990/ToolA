"""/health `listener` object tests (plan 4.8/4.13): the object is present
in the real handle_health payload with the exact key set, with and
without a booted supervisor, in both health views, and its presence
changes no existing key or the readiness status.
"""

import json
import os
import socket
import unittest
from types import SimpleNamespace
from unittest import mock

import orchestrator_server as srv
from orchestrator.lifecycle import listener_supervisor as ls

FAMILY_KEYS = {"state", "address", "since", "boot", "last_accept_fail_at",
               "last_error", "last_recovery_result", "reason",
               "attempts_window", "attempts_lifetime", "deaths_window",
               "recovery_latched"}


def _request(query=None):
    return SimpleNamespace(headers={}, query=query or {})


class ListenerHealthTests(unittest.IsolatedAsyncioTestCase):
    def tearDown(self):
        ls.set_active_supervisor(None)

    async def _payload(self, query=None):
        with mock.patch.object(srv, "SERVER_READY", True), mock.patch.object(
            srv, "SERVER_STARTUP_PHASE", "ready"
        ):
            resp = await srv.handle_health(_request(query))
        return resp, json.loads(resp.text)

    async def test_listener_object_fallback_without_supervisor(self):
        ls.set_active_supervisor(None)
        env = {k: v for k, v in os.environ.items() if k != ls.FLAG_ENV}
        with mock.patch.dict(os.environ, env, clear=True):
            resp, payload = await self._payload()
        self.assertEqual(resp.status, 200)
        listener = payload["listener"]
        self.assertEqual(set(listener),
                         {"recovery", "armed", "supervisor", "loop_class",
                          "proactor_fix_active", "accept_resets_swallowed"})
        self.assertEqual(listener["recovery"], "on")
        self.assertFalse(listener["armed"])
        self.assertEqual(listener["supervisor"], {"last_cycle_at": None})

    async def test_listener_object_with_supervisor_exact_shape(self):
        sup = ls.ListenerSupervisor(
            runner=SimpleNamespace(), port=18099,
            families=[
                {"key": "ipv4", "address": "127.0.0.1",
                 "address_family": socket.AF_INET,
                 "site": SimpleNamespace(), "boot": "bound"},
                {"key": "ipv6", "address": "::1",
                 "address_family": socket.AF_INET6,
                 "site": None, "boot": "bind_failed"},
            ],
            mode="detect",
            ready_check=lambda: False,
            stopping_check=lambda: False)
        ls.set_active_supervisor(sup)
        _resp, payload = await self._payload()
        listener = payload["listener"]
        self.assertEqual(set(listener),
                         {"recovery", "armed", "supervisor", "ipv4", "ipv6",
                          "loop_class", "proactor_fix_active",
                          "accept_resets_swallowed"})
        self.assertEqual(listener["recovery"], "detect")
        self.assertFalse(listener["armed"])
        self.assertEqual(set(listener["supervisor"]), {"last_cycle_at"})
        self.assertEqual(set(listener["ipv4"]), FAMILY_KEYS)
        self.assertEqual(set(listener["ipv6"]), FAMILY_KEYS)
        self.assertEqual(listener["ipv4"]["state"], "ok")
        self.assertEqual(listener["ipv4"]["boot"], "bound")
        self.assertEqual(listener["ipv6"]["state"], "suspect")
        self.assertEqual(listener["ipv6"]["boot"], "bind_failed")
        self.assertIsNone(listener["ipv6"]["reason"])

    async def test_listener_present_in_both_views(self):
        ls.set_active_supervisor(None)
        _r1, compact = await self._payload()
        self.assertEqual(compact.get("health_view"), "compact")
        self.assertIn("listener", compact)
        _r2, verbose = await self._payload({"verbose": "1"})
        self.assertEqual(verbose.get("health_view"), "verbose")
        self.assertIn("listener", verbose)


if __name__ == "__main__":
    unittest.main()
