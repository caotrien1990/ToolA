"""R48 (2026-08-12): the restart busy-snapshot that the controller guard reads must
EXCLUDE the in-flight restart request itself. Otherwise a restart always sees
requests_in_flight>=1 (its own call) and the guard false-blocks with ORCHESTRATOR_BUSY
/ is_stale=false -- the friction that made restart_orchestrator_via_bat unreliable.
"""
import unittest

import orchestrator_server as srv


class TestRestartBusySnapshotR48(unittest.TestCase):
    def setUp(self):
        self._req = srv._requests_in_flight
        self._id = srv._RESTART_REQUEST_ID

    def tearDown(self):
        srv._requests_in_flight = self._req
        srv._RESTART_REQUEST_ID = self._id

    def test_excludes_restart_request(self):
        # only the restart call is in flight -> snapshot must report 0 busy work
        srv._requests_in_flight = 1
        srv._RESTART_REQUEST_ID = "restart-abc"
        snap = srv._restart_busy_snapshot_state()
        self.assertEqual(snap["requests_in_flight"], 0)
        self.assertEqual(snap["restart_request_id"], "restart-abc")

    def test_no_undercount_without_restart(self):
        # no restart in flight -> real load must NOT be undercounted
        srv._requests_in_flight = 2
        srv._RESTART_REQUEST_ID = ""
        snap = srv._restart_busy_snapshot_state()
        self.assertEqual(snap["requests_in_flight"], 2)
        self.assertIsNone(snap["restart_request_id"])

    def test_excludes_only_the_restart_during_restart(self):
        # restart + 2 real requests -> snapshot shows the 2 real ones
        srv._requests_in_flight = 3
        srv._RESTART_REQUEST_ID = "restart-xyz"
        snap = srv._restart_busy_snapshot_state()
        self.assertEqual(snap["requests_in_flight"], 2)


if __name__ == "__main__":
    unittest.main()
