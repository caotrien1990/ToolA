"""Tests for orchestrator.lifecycle.proactor_accept_fix (gh-93821 root
fix, plan S1): the two upstream PR 161238 tests ported to this repo's
harness, plus unit tests for the conversion branch, the swallowed-reset
counter / health status, and the kill-switch install matrix.

The ported tests select their event loop through the real install path
and the ORCHESTRATOR_PROACTOR_FIX switch: with the switch "off" they
run on the STOCK proactor loop, where the survival assertions FAIL --
that asymmetry is the G-SUITE side-check proving the tests measure the
defect and not something else. Engagement is statistical -- a
reset only converts if it lands while its accept is still pending
-- so bursts repeat (at most 3) until the dose demonstrably
engages: in fixed mode the swallow counter must move, in stock
mode the server must stop answering; only then do the survival
assertions decide.
"""
import asyncio
import os
import socket
import struct
import threading
import time
import unittest
from unittest import mock

from orchestrator.lifecycle import proactor_accept_fix as paf


def _rst_burst(port, count=300, threads=8):
    """Parallel connect -> SO_LINGER(1,0) -> close against 127.0.0.1.

    Parallel on purpose: a sequential burst lets the loop complete
    each accept before that connection's reset lands, so the dose
    can miss the pending-accept window entirely; firing from
    several threads keeps the backlog full of reset connections
    whose accepts the loop still has to process. Returns
    (connected, errors)."""
    per_thread = max(1, count // threads)
    results = []
    lock = threading.Lock()

    def worker():
        connected = 0
        errors = 0
        for _ in range(per_thread):
            s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            s.settimeout(1.0)
            try:
                s.connect(("127.0.0.1", port))
                s.setsockopt(socket.SOL_SOCKET, socket.SO_LINGER,
                             struct.pack("ii", 1, 0))
                connected += 1
            except OSError:
                errors += 1
            finally:
                s.close()
        with lock:
            results.append((connected, errors))

    workers = [threading.Thread(target=worker) for _ in range(threads)]
    for w in workers:
        w.start()
    for w in workers:
        w.join()
    return (sum(c for c, _e in results),
            sum(e for _c, e in results))


def _try_greeting(port, timeout=3.0):
    """One real client: returns True iff the server accepted it and
    answered b"ok"."""
    try:
        client = socket.create_connection(("127.0.0.1", port),
                                          timeout=timeout)
    except OSError:
        return False
    try:
        return client.recv(2) == b"ok"
    except OSError:
        return False
    finally:
        client.close()


def _greet_eventually(port, attempts=3):
    """_try_greeting with retries: right after a burst the loop thread
    may still be draining accept/transport backlog, so one slow probe
    must not fail a healthy server on a loaded machine."""
    for attempt in range(attempts):
        if _try_greeting(port):
            return True
        if attempt + 1 < attempts:
            time.sleep(1.0)
    return False


class _LoopThread:
    """Runs one event loop (stock or fixed, per the current policy) in a
    thread, the way a real server process would."""

    def __init__(self):
        self.loop = None
        self.thread = None
        self.ready = threading.Event()

    def __enter__(self):
        def run():
            self.loop = asyncio.new_event_loop()
            asyncio.set_event_loop(self.loop)
            self.ready.set()
            self.loop.run_forever()

        self.thread = threading.Thread(target=run, daemon=True)
        self.thread.start()
        self.ready.wait(10)
        return self.loop

    def __exit__(self, *exc):
        if self.loop is not None:
            self.loop.call_soon_threadsafe(self.loop.stop)
        self.thread.join(10)
        if self.loop is not None:
            self.loop.close()
        return False


class _PolicyGuard(unittest.TestCase):
    """Saves/restores the process-wide loop policy, the module install
    flag, the global swallow counter and the kill-switch env so tests
    cannot leak state into the rest of the suite."""

    def setUp(self):
        self._policy = asyncio.get_event_loop_policy()
        self._installed = paf._INSTALLED
        self._count = paf._SWALLOWED.count
        self._env = os.environ.get("ORCHESTRATOR_PROACTOR_FIX")

    def tearDown(self):
        asyncio.set_event_loop_policy(self._policy)
        paf._INSTALLED = self._installed
        paf._SWALLOWED.count = self._count
        if self._env is None:
            os.environ.pop("ORCHESTRATOR_PROACTOR_FIX", None)
        else:
            os.environ["ORCHESTRATOR_PROACTOR_FIX"] = self._env

    def install_per_env(self):
        """Install via the real path unless the switch says off, in
        which case pin the stock policy explicitly."""
        mode = os.environ.get(
            "ORCHESTRATOR_PROACTOR_FIX", "").strip().lower()
        if mode == "off":
            asyncio.set_event_loop_policy(
                asyncio.WindowsProactorEventLoopPolicy())
            return {"installed": False, "reason": "kill_switch_off"}
        result = paf.install_proactor_fix()
        self.assertTrue(result["installed"], result)
        return result

    def assert_server_survives(self, port, fixed_mode):
        """The survival verdict of the ported tests: deliver bursts
        until the dose demonstrably engages (fixed mode: the swallow
        counter moves; stock mode: the server stops answering), at
        most 3 bursts, then probe with a real client and decide.
        Engagement is statistical -- a burst whose resets all land
        after their accept completed never touches the defect -- so
        a single unengaged burst must decide nothing on either side.
        Fixed mode must end with the counter moved."""
        counter0 = paf.accept_resets_swallowed()
        connected_total = 0
        for _burst in range(3):
            connected, _errors = _rst_burst(port)
            connected_total += connected
            if fixed_mode and paf.accept_resets_swallowed() > counter0:
                break
            if not fixed_mode and not _greet_eventually(port):
                break
        self.assertGreaterEqual(connected_total, 10)
        alive = _greet_eventually(port)
        self.assertTrue(
            alive, "server stopped accepting after the reset burst")
        if fixed_mode:
            self.assertGreater(
                paf.accept_resets_swallowed(), counter0,
                "burst never engaged the conversion branch")


class PortedResetTests(_PolicyGuard):
    """The two upstream PR 161238 tests, adapted: a server whose
    pending accepts are reset by a burst must keep accepting."""

    def _fixed_mode(self):
        mode = os.environ.get(
            "ORCHESTRATOR_PROACTOR_FIX", "").strip().lower()
        return mode != "off"

    def test_accept_connection_reset(self):
        self.install_per_env()

        class Proto(asyncio.Protocol):
            def connection_made(self, transport):
                transport.write(b"ok")

        with _LoopThread() as loop:
            server = asyncio.run_coroutine_threadsafe(
                loop.create_server(Proto, "127.0.0.1", 0), loop).result(10)
            port = server.sockets[0].getsockname()[1]
            try:
                self.assert_server_survives(port, self._fixed_mode())
            finally:
                # Close on the loop thread: a cross-thread close()
                # races Server._detach/_wakeup (base_events) while
                # burst transports are still detaching.
                async def _close_server():
                    server.close()
                    await server.wait_closed()

                asyncio.run_coroutine_threadsafe(
                    _close_server(), loop).result(10)

    def test_create_server_connection_reset(self):
        self.install_per_env()

        async def handler(reader, writer):
            writer.write(b"ok")
            await writer.drain()
            writer.close()

        with _LoopThread() as loop:
            server = asyncio.run_coroutine_threadsafe(
                asyncio.start_server(handler, "127.0.0.1", 0),
                loop).result(10)
            port = server.sockets[0].getsockname()[1]
            try:
                self.assert_server_survives(port, self._fixed_mode())
            finally:
                # Close on the loop thread: a cross-thread close()
                # races Server._detach/_wakeup (base_events) while
                # burst transports are still detaching.
                async def _close_server():
                    server.close()
                    await server.wait_closed()

                asyncio.run_coroutine_threadsafe(
                    _close_server(), loop).result(10)


class ConversionUnitTests(unittest.TestCase):
    """Deterministic drive of finish_accept via a fake overlapped:
    the SAME fake whose getresult raises is passed back into the
    captured callback. WinError 64 converts to ConnectionResetError
    (+1 counter, accept socket closed); any other OSError propagates
    unconverted and uncounted."""

    def setUp(self):
        self._count = paf._SWALLOWED.count

    def tearDown(self):
        paf._SWALLOWED.count = self._count

    def _drive(self, getresult_error):
        proactor = paf.FixedIocpProactor()
        listener = mock.Mock()
        listener.family = socket.AF_INET
        listener.fileno.return_value = 4321
        listener.gettimeout.return_value = None
        conn = mock.Mock()
        captured = {}

        class FakeOverlapped:
            def __init__(self, _null):
                captured["ov"] = self

            def AcceptEx(self, *_args):
                pass

            def getresult(self):
                raise getresult_error

        def fake_register(ov, _listener, callback):
            captured["callback"] = callback
            # A real future on the proactor's own loop: accept_coro
            # awaits it; the finally block cancels it deterministically.
            return loop.create_future()

        loop = asyncio.new_event_loop()
        try:
            with mock.patch.object(paf, "_overlapped") as ov_mod, \
                    mock.patch.object(proactor, "_register_with_iocp",
                                      lambda *_a: None), \
                    mock.patch.object(proactor, "_get_accept_socket",
                                      lambda _fam: conn), \
                    mock.patch.object(proactor, "_register",
                                      side_effect=fake_register):
                ov_mod.Overlapped = FakeOverlapped
                ov_mod.ERROR_NETNAME_DELETED = 64
                ov_mod.SO_UPDATE_ACCEPT_CONTEXT = 28683
                proactor._loop = loop
                proactor.accept(listener)
        finally:
            pending = asyncio.all_tasks(loop)
            for task in pending:
                task.cancel()
            if pending:
                loop.run_until_complete(
                    asyncio.gather(*pending, return_exceptions=True))
            loop.close()
            proactor.close()
        return captured["callback"], captured["ov"], conn

    def test_winerror64_converts_and_counts(self):
        err = OSError(22, "The specified network name is no longer "
                          "available", None, 64)
        before = paf.accept_resets_swallowed()
        callback, ov, conn = self._drive(err)
        with self.assertRaises(ConnectionResetError) as ctx:
            callback(None, None, ov)
        self.assertEqual(ctx.exception.winerror, 64)
        self.assertEqual(ctx.exception.args, err.args)
        self.assertEqual(paf.accept_resets_swallowed(), before + 1)
        conn.close.assert_called_once_with()

    def test_other_oserror_propagates_unconverted(self):
        err = OSError(5, "Access is denied", None, 5)
        before = paf.accept_resets_swallowed()
        callback, ov, conn = self._drive(err)
        with self.assertRaises(OSError) as ctx:
            callback(None, None, ov)
        self.assertIs(ctx.exception, err)
        self.assertNotIsInstance(ctx.exception, ConnectionResetError)
        self.assertEqual(paf.accept_resets_swallowed(), before)
        conn.close.assert_called_once_with()


class CounterAndStatusTests(unittest.TestCase):
    def setUp(self):
        self._count = paf._SWALLOWED.count

    def tearDown(self):
        paf._SWALLOWED.count = self._count

    def test_status_shape(self):
        status = paf.proactor_fix_status()
        self.assertEqual(set(status), {"accept_resets_swallowed",
                                       "proactor_fix_active"})
        self.assertIsInstance(status["accept_resets_swallowed"], int)
        self.assertIsInstance(status["proactor_fix_active"], bool)

    def test_first_swallow_logs(self):
        counter = paf._SwallowedResets()
        with self.assertLogs(paf.logger, level="INFO") as logs:
            counter.increment()
        self.assertEqual(counter.count, 1)
        self.assertTrue(any("swallowed accept reset #1" in line
                            for line in logs.output))

    def test_hundredth_swallow_logs(self):
        counter = paf._SwallowedResets()
        counter.count = 99
        with self.assertLogs(paf.logger, level="INFO") as logs:
            counter.increment()
        self.assertEqual(counter.count, 100)
        self.assertTrue(any("swallowed accept reset #100" in line
                            for line in logs.output))

    def test_global_counter_reflects_in_status(self):
        before = paf.accept_resets_swallowed()
        paf._SWALLOWED.increment()
        self.assertEqual(
            paf.proactor_fix_status()["accept_resets_swallowed"],
            before + 1)


class InstallMatrixTests(_PolicyGuard):
    def test_off_leaves_stock_policy(self):
        os.environ["ORCHESTRATOR_PROACTOR_FIX"] = "off"
        result = paf.install_proactor_fix()
        self.assertFalse(result["installed"])
        self.assertEqual(result["reason"], "kill_switch_off")
        self.assertIs(asyncio.get_event_loop_policy(), self._policy)
        self.assertFalse(paf.proactor_fix_active())

    def test_bad_mode_leaves_stock_policy(self):
        os.environ["ORCHESTRATOR_PROACTOR_FIX"] = "banana"
        result = paf.install_proactor_fix()
        self.assertFalse(result["installed"])
        self.assertEqual(result["reason"], "bad_mode")
        self.assertIs(asyncio.get_event_loop_policy(), self._policy)

    def test_on_installs_fixed_types(self):
        os.environ["ORCHESTRATOR_PROACTOR_FIX"] = "on"
        result = paf.install_proactor_fix()
        self.assertTrue(result["installed"], result)
        loop = asyncio.get_event_loop_policy().new_event_loop()
        try:
            self.assertIsInstance(loop, paf.FixedProactorEventLoop)
            self.assertIsInstance(loop._proactor, paf.FixedIocpProactor)
        finally:
            loop.close()
        self.assertTrue(paf.proactor_fix_active())


if __name__ == "__main__":
    unittest.main()
