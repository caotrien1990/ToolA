"""A down :8085 must report as REFUSED, not as a timeout.

The misreport: with nothing listening on the restart-control port, /health said

    "restart_controller": {"reachable": false, "error_type": "URLError",
                           "error_message": "<urlopen error timed out>",
                           "connection_refused": false}

`connection_refused: false` next to a timeout is the signature of a listener
that accepted the socket and went quiet -- i.e. a HUNG controller. Nothing was
listening at all. A reader acting on that answer would go looking for a stuck
process instead of a missing one.

The cause was self-inflicted, and the file's own documentation is what caused
it. `_controller_health_for_health` used to state that Windows refuses a
loopback connect in ~110ms, citing 0.109s of a 0.125s /health. The probe was
then given a 0.1s budget -- comfortably above "110ms", or so it read.

That 0.109s was never the refusal. It was the 0.1s deadline itself expiring.
Measured 2026-09-09 against a bound-then-released 127.0.0.1 port:

    connect timeout 0.1  ->  112ms  TimeoutError
    connect timeout 0.6  ->  609ms  TimeoutError
    connect timeout 3.0  -> 2020ms  ConnectionRefusedError WinError 10061

A refusal costs ~2.02s here, because Windows retransmits the SYN before
surfacing the RST. Any budget under that returns a timeout and the refusal never
reaches `_is_connection_refused` to be classified -- so a port with nothing on it
reports as a port that accepted and hung. The measurement was circular: the
timeout produced the number that justified the timeout.

These tests pin both halves: the budget must exceed the REAL refusal latency,
and the classification must name what actually happened.
"""
import email.message
import json
import socket
import sys
import unittest
import urllib.error
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import orchestrator_server as srv
from orchestrator.lifecycle.request_orchestrator_restart import (
    _classify_controller_error,
    _is_connection_refused,
    controller_health,
)

# Measured, not quoted: the time a refused 127.0.0.1 connect actually needs on
# this host. Any probe budget at or below this reports a timeout instead of the
# refusal. The figure the source used to carry -- 0.110 -- was the old budget
# expiring, and pinning the test to it is what let a still-broken budget pass.
MEASURED_WINDOWS_REFUSAL_SECONDS = 2.02

# The budget the bug shipped with. Below the real refusal latency by ~20x, so a
# closed port times out here deterministically -- not a race, as first assumed.
OLD_BROKEN_BUDGET_SECONDS = 0.1


def _a_closed_loopback_port() -> int:
    """Bind port 0, learn the port, release it. Nothing is listening after."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class ProbeBudgetTests(unittest.TestCase):
    def test_probe_budget_exceeds_the_measured_refusal_latency(self):
        """The root cause, pinned as a number.

        Fails at the shipped 0.1, and equally at the 0.6 that was tried first on
        the strength of the stale ~110ms figure. Only a budget above the real
        ~2.02s passes -- which is the whole point of measuring instead of
        quoting.
        """
        self.assertGreater(
            srv._CONTROLLER_HEALTH_PROBE_TIMEOUT_SECONDS,
            MEASURED_WINDOWS_REFUSAL_SECONDS,
            "probe budget must let the OS finish refusing, or a down port "
            "reports as a timeout",
        )

    def test_probe_budget_stays_inside_the_cache_ttl(self):
        """Off-loop is not a licence to be unbounded: a probe that outlives its
        own cache TTL would keep a refresh permanently in flight."""
        self.assertLess(srv._CONTROLLER_HEALTH_PROBE_TIMEOUT_SECONDS,
                        srv._CONTROLLER_HEALTH_TTL_SECONDS)


class ClassificationTests(unittest.TestCase):
    def test_refused_is_named_refused_and_says_nothing_listens(self):
        exc = urllib.error.URLError(ConnectionRefusedError(10061, "refused"))
        self.assertEqual(_classify_controller_error(exc), ("refused", False))

    def test_a_timeout_is_not_reported_as_a_present_listener(self):
        """The misreport in miniature. A timeout must not claim a listener --
        but it must not claim absence either, because it proves neither."""
        exc = urllib.error.URLError(TimeoutError("timed out"))
        classification, listener = _classify_controller_error(exc)
        self.assertEqual(classification, "connect_timeout")
        self.assertIsNone(listener)
        self.assertFalse(_is_connection_refused(exc))

    def test_an_http_answer_proves_a_listener(self):
        exc = urllib.error.HTTPError("http://127.0.0.1:8085/health", 503,
                                     "unavailable", email.message.Message(),
                                     None)
        self.assertEqual(_classify_controller_error(exc), ("http_error", True))

    def test_unparseable_body_proves_a_listener(self):
        exc = json.JSONDecodeError("no json", "<<<", 0)
        self.assertEqual(_classify_controller_error(exc), ("bad_payload", True))

    def test_other_os_failures_stay_unknown_rather_than_guessing(self):
        exc = urllib.error.URLError(OSError(99, "cannot assign address"))
        self.assertEqual(_classify_controller_error(exc), ("unreachable", None))

    def test_winerror_only_refusal_is_still_a_refusal(self):
        """WSAECONNREFUSED arrives as `errno` on the socket-mapped paths and as
        `winerror` on some raw Windows OSError paths. Reading one and not the
        other let a genuine refusal fall through to `unreachable`."""
        class _WinOnly(Exception):
            errno = None
            winerror = 10061

        exc = urllib.error.URLError(_WinOnly("refused"))
        self.assertTrue(_is_connection_refused(exc))
        self.assertEqual(_classify_controller_error(exc), ("refused", False))


class EndToEndTests(unittest.TestCase):
    def test_a_genuinely_closed_port_reports_refused(self):
        """No mocks: a real connect to a real closed loopback port, given the
        real post-fix budget, must come back `refused` -- the exact scenario
        that used to answer `connect_timeout`."""
        port = _a_closed_loopback_port()
        out = controller_health(
            f"http://127.0.0.1:{port}/restart",
            timeout=srv._CONTROLLER_HEALTH_PROBE_TIMEOUT_SECONDS,
        )
        self.assertFalse(out["reachable"])
        self.assertEqual(out["classification"], "refused", out)
        self.assertIs(out["listener_present"], False, out)
        self.assertIs(out["connection_refused"], True, out)
        self.assertEqual(out["probe_timeout_s"],
                         srv._CONTROLLER_HEALTH_PROBE_TIMEOUT_SECONDS)

    def test_the_old_budget_is_what_produced_the_misreport(self):
        """Same port, same code, two budgets -- and only the budget differs.

        This was first written as a tolerant characterisation, on the assumption
        that 100ms vs "~110ms" was a coin flip the RST could win. It is not a
        race: the refusal needs ~2.02s, so the old budget times out every time
        and the assertion can be exact. Pinning both halves here is what proves
        the fix is the number and not the classification code -- the two calls
        share everything else.
        """
        port = _a_closed_loopback_port()
        url = f"http://127.0.0.1:{port}/restart"
        old = controller_health(url, timeout=OLD_BROKEN_BUDGET_SECONDS)
        new = controller_health(
            url, timeout=srv._CONTROLLER_HEALTH_PROBE_TIMEOUT_SECONDS)
        self.assertEqual(old["classification"], "connect_timeout", old)
        self.assertIsNone(old["listener_present"], old)
        self.assertEqual(new["classification"], "refused",
                         f"old={old} new={new}")
        self.assertIs(new["listener_present"], False, new)

    def test_existing_keys_are_preserved(self):
        """`classification` and `listener_present` are additive. Every field a
        reader already depended on must still be there with the same meaning."""
        port = _a_closed_loopback_port()
        out = controller_health(f"http://127.0.0.1:{port}/restart",
                                timeout=OLD_BROKEN_BUDGET_SECONDS)
        for key in ("configured", "reachable", "url", "error_type",
                    "error_message", "connection_refused"):
            self.assertIn(key, out)
        self.assertIs(out["configured"], True)
        self.assertTrue(out["url"].endswith("/health"))


if __name__ == "__main__":
    unittest.main()
