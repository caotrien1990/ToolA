"""A39: a restart must not be false-blocked by a busy-snapshot that is merely
STALE-BY-AGE while the publisher is still alive — WITHOUT ever judging a restart
safe on out-of-date content.

Root cause (measured, see tests/_a39_probe.py): the busy-snapshot publisher runs
on a plain thread that publishes every 0.5s, but its snapshot() callback grabs
locks a busy request handler can hold for seconds (executor_lane_status,
_semaphore_status_rows, accounting.status) and it can be GIL-starved by a
CPU-bound burst. During such a burst the on-disk snapshot ages past the 3.0s
guard deadline even though nothing is busy and the publisher is alive. The old
guard treated "old reading" as "busy", false-blocking a legitimate restart that
then healed by luck on retry.

Fix (round 2, after PH-1/PH-2 rejection):
  * PH-1: when the reading is stale-by-age, build_guard re-reads and JUDGES ON THE
    FRESHEST reading's CONTENT (busy, executor_in_flight, generation) — it does not
    learn "a fresher reading exists" and then trust the stale content. If the fresh
    reading shows work in flight, the restart BLOCKS.
  * PH-2: liveness that suppresses the stale-by-age block must survive an ongoing
    lock-block; captured_at-advance-within-0.6s does not and fails closed during the
    exact incident.
  * PH-5: the liveness signal is the publisher THREAD being live AND current,
    sampled from the lock-free /livez channel (Thread.is_alive()), NOT a process pid
    stamped into the snapshot. A dead thread freezes captured_at forever while the
    process pid stays alive, so a pid check fails OPEN; a live thread-liveness read
    off /livez flips to False the instant the thread dies. /health is NOT used — it
    takes _LANES_LOCK, the very lock held during the PH-2 block.
  * generation is judged on the fresh reading, so a mismatch still blocks.
  * PH-4: request_orchestrator_restart returns a distinct rc (4) for a stale-only
    block so [A:S] can retry Path A once; rc=3 stays "genuinely blocked".

These tests pin BEHAVIOUR, not implementation, and fail on the pre-fix code:
  * pre-fix evaluate_restart_guard had no snapshot_publisher_alive param;
  * pre-fix build_guard judged the stale reading and false-blocked;
  * pre-fix describe_blocking_guards had no retryable flag;
  * pre-fix CLI helper returned rc=3 for a stale-only block.
"""
from __future__ import annotations

import json
import os
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

from orchestrator.lifecycle import restart_control_service
from orchestrator.lifecycle.restart_control_service import (
    _refresh_busy_snapshot,
    _snapshot_publisher_alive,
    build_guard,
)
from orchestrator.lifecycle.restart_guard import (
    describe_blocking_guards,
    evaluate_restart_guard,
    is_retryable_stale_block,
)


def _fresh(**over):
    snap = {"captured_at": 99.0, "busy": False, "generation": "gen-a"}
    snap.update(over)
    return snap


def _guard(**over):
    values = {
        "busy_snapshot": _fresh(), "council_slots": [], "broker_lines": [],
        "now": 100.0, "snapshot_max_age_seconds": 3.0,
        "pid_alive": lambda _pid: False, "expected_generation": "gen-a",
    }
    values.update(over)
    return evaluate_restart_guard(**values)


class StaleButAliveDoesNotBlock(unittest.TestCase):
    def test_stale_reading_with_proven_alive_publisher_allows_restart(self):
        # captured_at is 10s old (past the 3.0s deadline) but publisher proven alive
        # and the fresh content is genuinely idle -> restart allowed.
        stale = _fresh(captured_at=90.0)
        decision = _guard(busy_snapshot=stale, snapshot_publisher_alive=True)
        self.assertNotIn("BUSY_SNAPSHOT_STALE", decision.reasons)
        self.assertTrue(decision.allowed)

    def test_stale_reading_with_dead_publisher_still_blocks(self):
        stale = _fresh(captured_at=90.0)
        decision = _guard(busy_snapshot=stale, snapshot_publisher_alive=False)
        self.assertIn("BUSY_SNAPSHOT_STALE", decision.reasons)
        self.assertFalse(decision.allowed)

    def test_unknown_liveness_is_fail_closed(self):
        stale = _fresh(captured_at=90.0)
        decision = _guard(busy_snapshot=stale, snapshot_publisher_alive=None)
        self.assertIn("BUSY_SNAPSHOT_STALE", decision.reasons)

    def test_fresh_reading_never_needs_liveness_and_allows(self):
        decision = _guard(busy_snapshot=_fresh(captured_at=99.5), snapshot_publisher_alive=None)
        self.assertNotIn("BUSY_SNAPSHOT_STALE", decision.reasons)
        self.assertTrue(decision.allowed)


class LivenessDoesNotOverrideRealBusy(unittest.TestCase):
    def test_active_council_worker_blocks_even_with_alive_publisher(self):
        decision = _guard(
            busy_snapshot=_fresh(captured_at=90.0),
            council_slots=[{"state": "running", "worker_pid": 44}],
            pid_alive=lambda pid: pid == 44,
            snapshot_publisher_alive=True,
        )
        self.assertIn("COUNCIL_SLOT_ACTIVE", decision.reasons)
        self.assertIn("COUNCIL_PID_ALIVE", decision.reasons)
        self.assertFalse(decision.allowed)

    def test_orchestrator_busy_blocks_even_with_alive_publisher(self):
        busy = _fresh(captured_at=90.0, busy=True, requests_in_flight=1)
        decision = _guard(busy_snapshot=busy, snapshot_publisher_alive=True)
        self.assertIn("ORCHESTRATOR_BUSY", decision.reasons)
        self.assertFalse(decision.allowed)


class RefreshBusySnapshot(unittest.TestCase):
    """PH-1 seam: the re-read returns the FRESHEST reading, not a bool."""

    def test_returns_newer_reading_when_it_advanced(self):
        first = {"captured_at": 100.0, "busy": False, "executor_in_flight": 0}
        second = {"captured_at": 100.6, "busy": True, "executor_in_flight": 3}
        with mock.patch.object(restart_control_service, "_read_json", return_value=second):
            out = _refresh_busy_snapshot(Path("x"), first, sleep=lambda _s: None)
        # Must return the CONTENT of read #2, not a bool.
        self.assertEqual(out["executor_in_flight"], 3)
        self.assertTrue(out["busy"])

    def test_keeps_first_when_reread_not_newer(self):
        first = {"captured_at": 100.0, "busy": False}
        with mock.patch.object(restart_control_service, "_read_json", return_value={"captured_at": 100.0}):
            out = _refresh_busy_snapshot(Path("x"), first, sleep=lambda _s: None)
        self.assertIs(out, first)

    def test_keeps_first_when_reread_unreadable(self):
        first = {"captured_at": 100.0, "busy": False}
        out = _refresh_busy_snapshot(
            Path("x"), first, sleep=lambda _s: None,
            read_json=lambda _p: (_ for _ in ()).throw(OSError("gone")),
        )
        self.assertIs(out, first)


def _livez(alive=True, generation="gen-a"):
    return {"ok": True, "generation": generation,
            "restart_busy_snapshot_alive": alive}


class SnapshotPublisherAlive(unittest.TestCase):
    """PH-5 seam: liveness = the publisher THREAD is live AND current, sampled from
    the lock-free /livez channel — NOT a pid stamped into the (freezable) snapshot."""

    def test_alive_when_livez_reports_thread_alive_and_generation_matches(self):
        self.assertTrue(_snapshot_publisher_alive({"busy": False}, "gen-a", _livez(True, "gen-a")))

    def test_dead_when_livez_reports_thread_dead(self):
        # PH-5 core: the publisher THREAD is dead (is_alive() False) even though the
        # process — and hence any stamped pid — is still alive. Must fail closed.
        self.assertFalse(_snapshot_publisher_alive({"busy": False}, "gen-a", _livez(False, "gen-a")))

    def test_dead_when_livez_unreachable(self):
        self.assertFalse(_snapshot_publisher_alive({"busy": False}, "gen-a", None))

    def test_dead_when_livez_generation_differs(self):
        # A NEW generation's healthy publisher must not vouch for an OLD generation's
        # frozen snapshot.
        self.assertFalse(_snapshot_publisher_alive({"busy": False}, "gen-a", _livez(True, "gen-NEW")))

    def test_fail_closed_when_alive_field_missing_or_not_true(self):
        self.assertFalse(_snapshot_publisher_alive({"busy": False}, "gen-a", {"generation": "gen-a"}))
        # truthy-but-not-True must not pass (strict is-True check).
        self.assertFalse(_snapshot_publisher_alive(
            {"busy": False}, "gen-a", {"generation": "gen-a", "restart_busy_snapshot_alive": 1}))

    def test_fail_closed_when_generation_invalid(self):
        self.assertFalse(_snapshot_publisher_alive(
            {"busy": False}, "gen-a", {"generation": "", "restart_busy_snapshot_alive": True}))


class LivezExposesThreadLiveness(unittest.TestCase):
    """PH-5 depends on /livez carrying the LIVE publisher-thread liveness. Pin the
    real server payload builder against a real BusySnapshotPublisher."""

    def test_livez_payload_reports_publisher_thread_alive_then_dead(self):
        import orchestrator_server as srv
        from orchestrator.lifecycle.restart_protocol import BusySnapshotPublisher
        prev = srv._RESTART_SNAPSHOT_PUBLISHER
        try:
            with tempfile.TemporaryDirectory() as tmp:
                pub = BusySnapshotPublisher(Path(tmp) / "busy.json",
                                            lambda: {"busy": False, "generation": "gen-a"},
                                            interval=0.01)
                pub.start()
                srv._RESTART_SNAPSHOT_PUBLISHER = pub
                self.assertTrue(srv._liveness_payload()["restart_busy_snapshot_alive"])
                # Stop the THREAD; the PROCESS (this test) stays alive — the PH-5 shape.
                pub.stop()
                self.assertFalse(pub.alive)
                self.assertFalse(srv._liveness_payload()["restart_busy_snapshot_alive"])
        finally:
            srv._RESTART_SNAPSHOT_PUBLISHER = prev


class BuildGuardEndToEnd(unittest.TestCase):
    def _root(self, tmp):
        root = Path(tmp)
        (root / ".council").mkdir()
        cache = root / ".orchestrator_cache"
        cache.mkdir()
        (root / ".council" / "runtime_state.json").write_text(json.dumps({}), encoding="utf-8")
        return root, cache / "restart_busy_snapshot.json"

    def _write(self, path, **snap):
        path.write_text(json.dumps(snap), encoding="utf-8")

    def _guard(self, root, livez_payload):
        # Drive build_guard with a mocked /livez probe and a no-op settle sleep.
        with mock.patch.object(restart_control_service.time, "sleep", lambda _s: None), \
             mock.patch.object(restart_control_service, "_probe_livez",
                               return_value=livez_payload):
            return build_guard(root, "gen-a", max_age=3.0)()

    def test_stale_file_but_publisher_thread_alive_idle_allows_restart(self):
        with tempfile.TemporaryDirectory() as tmp:
            root, snap_path = self._root(tmp)
            self._write(snap_path, captured_at=time.time() - 10.0, busy=False,
                        generation="gen-a")
            decision = self._guard(root, _livez(True, "gen-a"))
            self.assertNotIn("BUSY_SNAPSHOT_STALE", decision.reasons)
            self.assertTrue(decision.allowed)

    def test_stale_file_dead_thread_still_blocks(self):
        with tempfile.TemporaryDirectory() as tmp:
            root, snap_path = self._root(tmp)
            self._write(snap_path, captured_at=time.time() - 10.0, busy=False,
                        generation="gen-a")
            decision = self._guard(root, _livez(False, "gen-a"))
            self.assertIn("BUSY_SNAPSHOT_STALE", decision.reasons)
            self.assertFalse(decision.allowed)

    def test_ACCEPTANCE_1_stale_idle_read1_busy_read2_blocks_on_inflight_workers(self):
        # Coordinator round-2 acceptance #1: read #1 stale + idle; read #2 (fresh)
        # shows executor_in_flight=3. Guard must judge read #2 and block with
        # IN_FLIGHT_WORKERS_ACTIVE. Publisher thread is alive throughout.
        with tempfile.TemporaryDirectory() as tmp:
            root, snap_path = self._root(tmp)
            self._write(snap_path, captured_at=time.time() - 10.0, busy=False,
                        executor_in_flight=0, generation="gen-a")

            def busy_sleep(_s):
                self._write(snap_path, captured_at=time.time(), busy=True,
                            executor_in_flight=3, generation="gen-a")

            with mock.patch.object(restart_control_service.time, "sleep", busy_sleep), \
                 mock.patch.object(restart_control_service, "_probe_livez",
                                   return_value=_livez(True, "gen-a")):
                decision = build_guard(root, "gen-a", max_age=3.0)()
            self.assertFalse(decision.allowed)
            self.assertIn("IN_FLIGHT_WORKERS_ACTIVE", decision.reasons)
            self.assertNotIn(decision.reasons, ((), []))

    def test_ACCEPTANCE_PH5_dead_thread_with_workers_in_flight_blocks(self):
        # Coordinator round-3 acceptance: the publisher THREAD is dead (livez alive
        # field False) while the process serves on and executor_in_flight=3 sits in
        # the frozen snapshot. Round-2 (pid alive forever) allowed this; round-3 must
        # BLOCK. Fails against the round-2 patch.
        with tempfile.TemporaryDirectory() as tmp:
            root, snap_path = self._root(tmp)
            # Frozen snapshot: stale, and it already shows 3 workers in flight.
            self._write(snap_path, captured_at=time.time() - 10.0, busy=True,
                        executor_in_flight=3, generation="gen-a")

            # The dead thread never rewrites the file; settle is a no-op.
            decision = self._guard(root, _livez(False, "gen-a"))
            self.assertFalse(decision.allowed)
            # Blocked because the reading is stale AND the thread is dead — the
            # worker-in-flight content is also visible, but the decisive point is it
            # is NOT allowed. Assert the stale block stands.
            self.assertIn("BUSY_SNAPSHOT_STALE", decision.reasons)

    def test_PH5_real_publisher_thread_death_blocks_end_to_end(self):
        # Strongest PH-5 proof: a REAL BusySnapshotPublisher writes a stale-then-busy
        # frozen snapshot, the THREAD is stopped while this process lives on, and the
        # guard reads live thread-liveness off the REAL _liveness_payload. Must BLOCK.
        import orchestrator_server as srv
        from orchestrator.lifecycle.restart_protocol import BusySnapshotPublisher
        prev = srv._RESTART_SNAPSHOT_PUBLISHER
        try:
            with tempfile.TemporaryDirectory() as tmp:
                root, snap_path = self._root(tmp)
                # busy snapshot with 3 workers; make it stale by backdating.
                self._write(snap_path, captured_at=time.time() - 10.0, busy=True,
                            executor_in_flight=3, generation="gen-a")
                pub = BusySnapshotPublisher(snap_path,
                                            lambda: {"busy": True, "executor_in_flight": 3,
                                                     "generation": "gen-a"}, interval=0.01)
                pub.start()
                srv._RESTART_SNAPSHOT_PUBLISHER = pub
                pub.stop()  # THREAD dead, process (this test) alive => PH-5 shape.
                self.assertFalse(pub.alive)
                # Re-backdate: the just-stopped thread may have written one fresh
                # snapshot before stopping; force the frozen-stale precondition.
                self._write(snap_path, captured_at=time.time() - 10.0, busy=True,
                            executor_in_flight=3, generation="gen-a")

                def real_livez(_url, timeout=1.0, opener=None):
                    return srv._liveness_payload()  # real, thread-dead => alive False

                with mock.patch.object(restart_control_service.time, "sleep", lambda _s: None), \
                     mock.patch.object(restart_control_service, "_probe_livez", real_livez):
                    decision = build_guard(root, "gen-a", max_age=3.0)()
                self.assertFalse(decision.allowed)
                self.assertIn("BUSY_SNAPSHOT_STALE", decision.reasons)
        finally:
            srv._RESTART_SNAPSHOT_PUBLISHER = prev

    def test_stale_read1_but_fresh_read2_generation_mismatch_still_blocks(self):
        # The fresh substitution must not smuggle a generation mismatch through.
        with tempfile.TemporaryDirectory() as tmp:
            root, snap_path = self._root(tmp)
            self._write(snap_path, captured_at=time.time() - 10.0, busy=False,
                        generation="gen-a")

            def wrong_gen_sleep(_s):
                self._write(snap_path, captured_at=time.time(), busy=False,
                            generation="gen-OTHER")

            with mock.patch.object(restart_control_service.time, "sleep", wrong_gen_sleep), \
                 mock.patch.object(restart_control_service, "_probe_livez",
                                   return_value=_livez(True, "gen-a")):
                decision = build_guard(root, "gen-a", max_age=3.0)()
            self.assertFalse(decision.allowed)
            self.assertIn("BUSY_SNAPSHOT_GENERATION_MISMATCH", decision.reasons)


class RetryableContract(unittest.TestCase):
    def test_is_retryable_only_when_stale_is_the_sole_reason(self):
        self.assertTrue(is_retryable_stale_block(["BUSY_SNAPSHOT_STALE"]))
        self.assertFalse(is_retryable_stale_block(["BUSY_SNAPSHOT_STALE", "COUNCIL_SLOT_ACTIVE"]))
        self.assertFalse(is_retryable_stale_block(["ORCHESTRATOR_BUSY"]))
        self.assertFalse(is_retryable_stale_block([]))

    def test_describe_stale_advertises_retry_not_useless_clear(self):
        guards = {g["name"]: g for g in describe_blocking_guards(["BUSY_SNAPSHOT_STALE"])}
        stale = guards["BUSY_SNAPSHOT_STALE"]
        self.assertTrue(stale["retryable"])
        self.assertNotEqual(stale["how_to_clear"], "reload guard state after clearing the condition")
        self.assertIn("retry", stale["how_to_clear"].lower())

    def test_non_stale_reason_is_not_retryable(self):
        guards = {g["name"]: g for g in describe_blocking_guards(["COUNCIL_SLOT_ACTIVE"])}
        self.assertFalse(guards["COUNCIL_SLOT_ACTIVE"]["retryable"])


class ToolResponseContract(unittest.TestCase):
    def _run_tool(self, decision, tmp):
        import asyncio
        import tools_v15
        root = Path(tmp)
        (root / ".council").mkdir()
        (root / ".council" / "runtime_state.json").write_text("{}", encoding="utf-8")
        (root / "start_orchestrator.bat").write_text("echo x", encoding="utf-8")
        with mock.patch.object(tools_v15, "__file__", str(root / "tools_v15.py")), \
             mock.patch("orchestrator.lifecycle.restart_control_service.build_guard",
                        return_value=lambda: decision):
            out = asyncio.run(tools_v15.restart_orchestrator_via_bat(expected_generation="gen-a"))
        return json.loads(out)

    def test_stale_only_block_is_retryable_with_next_action(self):
        from orchestrator.lifecycle.restart_guard import GuardDecision, describe_blocking_guards
        decision = GuardDecision(
            False, ("BUSY_SNAPSHOT_STALE",), describe_blocking_guards(["BUSY_SNAPSHOT_STALE"])
        )
        with tempfile.TemporaryDirectory() as tmp:
            payload = self._run_tool(decision, tmp)
        self.assertEqual(payload["error_name"], "RESTART_GUARD_BLOCKED")
        self.assertTrue(payload["retryable"])
        self.assertEqual(payload["ca_next_action"], "retry_restart_call")

    def test_real_busy_block_is_not_retryable(self):
        from orchestrator.lifecycle.restart_guard import GuardDecision, describe_blocking_guards
        decision = GuardDecision(
            False, ("COUNCIL_SLOT_ACTIVE",), describe_blocking_guards(["COUNCIL_SLOT_ACTIVE"])
        )
        with tempfile.TemporaryDirectory() as tmp:
            payload = self._run_tool(decision, tmp)
        self.assertEqual(payload["error_name"], "RESTART_GUARD_BLOCKED")
        self.assertFalse(payload["retryable"])
        self.assertNotIn("ca_next_action", payload)


class CliHelperReturnCode(unittest.TestCase):
    """PH-4: the CLI helper distinguishes a stale-only (retryable) block (rc=4)
    from a genuine block (rc=3), derived from response fields."""

    def _run_main(self, controller_result):
        import orchestrator.lifecycle.request_orchestrator_restart as ror

        class Resp:
            def __init__(self, body):
                self._b = body
            def __enter__(self):
                return self
            def __exit__(self, *_a):
                return None
            def read(self):
                return self._b

        def fake_urlopen(req, timeout=0):
            url = req if isinstance(req, str) else req.full_url
            if url.endswith("/health"):
                return Resp(b'{"ok":true,"service":"restart-control","port":8085}')
            if url.endswith("/livez"):
                return Resp(b'{"ok":true,"pid":123,"generation":"gen-a"}')
            raise AssertionError("unexpected url " + url)

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / ".orchestrator_lifecycle").mkdir()
            (root / ".orchestrator_lifecycle" / "restart_control.secret").write_bytes(b"s" * 32)
            with mock.patch.object(ror, "__file__",
                                   str(root / "orchestrator" / "lifecycle" / "request_orchestrator_restart.py")), \
                 mock.patch.object(ror.urllib.request, "urlopen", fake_urlopen), \
                 mock.patch.object(ror, "controller_health",
                                   return_value={"configured": True, "reachable": True}), \
                 mock.patch.object(ror, "request_restart", return_value=controller_result):
                return ror.main([])

    def test_stale_only_block_returns_rc4(self):
        rc = self._run_main({"ok": False, "error_name": "RESTART_GUARD_BLOCKED",
                             "reasons": ["BUSY_SNAPSHOT_STALE"]})
        self.assertEqual(rc, 4)

    def test_worker_in_flight_block_returns_rc3(self):
        rc = self._run_main({"ok": False, "error_name": "RESTART_GUARD_BLOCKED",
                             "reasons": ["IN_FLIGHT_WORKERS_ACTIVE"]})
        self.assertEqual(rc, 3)

    def test_council_slot_block_returns_rc3(self):
        rc = self._run_main({"ok": False, "error_name": "RESTART_GUARD_BLOCKED",
                             "reasons": ["COUNCIL_SLOT_ACTIVE", "COUNCIL_PID_ALIVE"]})
        self.assertEqual(rc, 3)


class LivezPortIsConfigurable(unittest.TestCase):
    """A39 follow-up: build_guard / main must resolve the /livez port from
    ORCHESTRATOR_LIVEZ_PORT (default 8084) instead of a hardcoded 8084, so a
    moved liveness port is probed correctly. tools_v15 relies on this because it
    calls build_guard WITHOUT passing livez_url."""

    def _root(self, tmp):
        root = Path(tmp)
        (root / ".council").mkdir()
        cache = root / ".orchestrator_cache"
        cache.mkdir()
        (root / ".council" / "runtime_state.json").write_text(json.dumps({}), encoding="utf-8")
        return root, cache / "restart_busy_snapshot.json"

    def test_resolve_livez_url_uses_env_port(self):
        with mock.patch.dict(os.environ, {"ORCHESTRATOR_LIVEZ_PORT": "19084"}):
            self.assertEqual(
                restart_control_service._resolve_livez_url(),
                "http://127.0.0.1:19084/livez",
            )

    def test_resolve_livez_url_defaults_to_8084(self):
        env = {k: v for k, v in os.environ.items() if k != "ORCHESTRATOR_LIVEZ_PORT"}
        with mock.patch.dict(os.environ, env, clear=True):
            self.assertEqual(
                restart_control_service._resolve_livez_url(),
                "http://127.0.0.1:8084/livez",
            )

    def test_build_guard_probes_env_port_when_livez_url_omitted(self):
        # This is the tools_v15 code path: build_guard(...) with no livez_url.
        # A stale snapshot forces the /livez probe, so we can capture the URL the
        # guard actually builds and assert it carries the non-default port.
        seen = {}

        def _capture(url, *a, **k):
            seen["url"] = url
            return _livez(True, "gen-a")

        with tempfile.TemporaryDirectory() as tmp, \
                mock.patch.dict(os.environ, {"ORCHESTRATOR_LIVEZ_PORT": "19084"}):
            root, snap_path = self._root(tmp)
            snap_path.write_text(
                json.dumps({"captured_at": time.time() - 10.0, "busy": False,
                            "generation": "gen-a"}),
                encoding="utf-8",
            )
            with mock.patch.object(restart_control_service.time, "sleep", lambda _s: None), \
                    mock.patch.object(restart_control_service, "_probe_livez", _capture):
                build_guard(root, "gen-a", max_age=3.0)()
        self.assertEqual(seen.get("url"), "http://127.0.0.1:19084/livez")


if __name__ == "__main__":
    unittest.main()
