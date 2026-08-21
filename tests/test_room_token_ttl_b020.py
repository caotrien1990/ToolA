"""B020: a room token's TTL was truncated to the current second, not counted from now.

`_room_register` computed `expiry = int(time.time()) + ttl_sec` while
`_room_validate_token` compares `expiry <= int(time.time())`. Flooring the start
of the window but not the end means the token's real lifetime was
`ttl_sec - (time.time() % 1)` -- anywhere from the full ttl_sec down to
essentially zero, decided by where in the wall-clock second the register landed.

Measured on the live tree before the fix: registering at t=100.95 with ttl_sec=1
produced expires_at=101, and validating 60 ms later at t=101.01 raised
ROOM_TOKEN_INVALID. A one-second token had an effective life of 0.05s.

This is why `test_council_rooms.py::test_register_meta_token_and_expiry` passed
10/10 in isolation and failed inside the full suite: the suite is slower, so the
register call lands late in its second more often. The test was not flaky -- it
was sampling a real defect whose probability depends on timing, which is the
worst kind of test result to dismiss.

Two claims are pinned:
  1. A token registered anywhere in a second survives for its full ttl_sec.
  2. It still expires -- the fix must not make tokens immortal.
"""
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import orchestrator_server as srv


class RoomTokenTtlIsCountedFromNow(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.patch = mock.patch.object(srv, "_HERE", self.root)
        self.patch.start()

    def tearDown(self):
        self.patch.stop()
        self.tmp.cleanup()

    def test_a_one_second_token_survives_most_of_that_second(self):
        """Register at .95 of a second, validate 60 ms later. Was ROOM_TOKEN_INVALID."""
        with mock.patch.object(srv.time, "time", lambda: 100.95):
            reg = srv._room_register("room.b020", ttl_sec=1)
        with mock.patch.object(srv.time, "time", lambda: 101.01):
            got = srv._room_validate_token("room.b020", reg["room_token"])
        self.assertEqual(got["room_id"], "room.b020")

    def test_the_full_ttl_is_honoured_wherever_in_the_second_it_starts(self):
        """The effective lifetime must not depend on the sub-second phase of register."""
        for frac in (0.0, 0.25, 0.5, 0.75, 0.99):
            with self.subTest(frac=frac):
                start = 200.0 + frac
                with mock.patch.object(srv.time, "time", lambda: start):
                    reg = srv._room_register("room.b020.f", ttl_sec=10)
                # 9.5s later the token is still inside a 10s window, always.
                with mock.patch.object(srv.time, "time", lambda: start + 9.5):
                    got = srv._room_validate_token("room.b020.f", reg["room_token"])
                self.assertEqual(got["room_id"], "room.b020.f")

    def test_a_token_still_expires(self):
        """Bounding case: the fix must not buy validity by never expiring."""
        with mock.patch.object(srv.time, "time", lambda: 300.4):
            reg = srv._room_register("room.b020.exp", ttl_sec=2)
        with mock.patch.object(srv.time, "time", lambda: 300.4 + 2.6):
            with self.assertRaisesRegex(PermissionError, "ROOM_TOKEN_INVALID"):
                srv._room_validate_token("room.b020.exp", reg["room_token"])

    def test_expires_at_is_at_least_ttl_seconds_away(self):
        """The advertised expires_at must match what the caller asked for."""
        for frac in (0.0, 0.5, 0.99):
            with self.subTest(frac=frac):
                start = 400.0 + frac
                with mock.patch.object(srv.time, "time", lambda: start):
                    reg = srv._room_register("room.b020.adv", ttl_sec=30)
                self.assertGreaterEqual(
                    reg["expires_at"], start + 30,
                    "expires_at advertised a window shorter than the ttl_sec asked for")


if __name__ == "__main__":
    unittest.main()
