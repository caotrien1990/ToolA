"""Mirror of CPython PR 161238 (gh-93821) for the daemon's CPython 3.12.11.

The defect: when a client resets a connection whose AcceptEx is still
pending, the accept completes with WinError 64 (ERROR_NETNAME_DELETED).
In stock 3.12.11 the raw OSError reaches
``BaseProactorEventLoop._start_serving``, whose ``except OSError`` branch
closes the LISTENING socket and never posts a new accept: that address
family stops accepting forever while the process lives and
``is_serving()`` keeps returning True. Upstream fixed this in PR 161238
(merged 2026-11-04 for 3.14/3.15/main; no 3.12 backport and no release
contains it yet), so this module mirrors the PR's exact three touches in
daemon-owned subclasses instead of touching the stdlib:

1. ``IocpProactor.accept`` / ``finish_accept``: wrap ``ov.getresult()``
   in try/except OSError; close the failed accept socket; convert a
   WinError 64 failure into ``ConnectionResetError``.
2. ``accept_coro``: swallow ``ConnectionResetError`` (it is reported to
   the caller through the future).
3. ``BaseProactorEventLoop._start_serving``: a new
   ``except (ConnectionResetError, ConnectionAbortedError)`` branch
   BEFORE ``except OSError`` that keeps accepting (no socket close, no
   exception handler).

The ``accept`` and ``_start_serving`` bodies below are the installed
3.12.11 bodies verbatim apart from those touches and the plan-4.3
counter increment inside touch 1. G-SPEC-DIFF (in the drift test)
verifies that claim mechanically against the installed stdlib.

Plan 4.3 observability: the conversion point increments a process-wide
counter of swallowed resets (exposed via :func:`proactor_fix_status`
and the /health ``listener`` object), with one log line at the first
swallow after boot and at each multiple of 100. The counter exists
because the fix silences the supervisor's accept-failure signal: a
value > 0 proves the conversion branch ran; the absolute value is not
a health metric (completion batching makes it vary per burst).

Kill-switch: ``ORCHESTRATOR_PROACTOR_FIX`` = ``on`` (default) | ``off``.
``off`` (or an unrecognised value, or a G-DRIFT anchor mismatch) leaves
the stock event-loop policy untouched: the daemon runs pure stock code
plus the listener supervisor. Activation is fail-safe, never partial.
"""
from __future__ import annotations

import _overlapped
import ast
import asyncio
import hashlib
import logging
import os
import pathlib
import socket
import struct
from asyncio import exceptions, tasks, trsock
from asyncio.windows_events import IocpProactor

logger = logging.getLogger(__name__)

NULL = 0

_KILL_SWITCH_ENV = "ORCHESTRATOR_PROACTOR_FIX"

# G-DRIFT anchors: SHA-256 of the AST source segments of the two
# installed-stdlib functions this mirror is copied from, computed on the
# production interpreter (CPython 3.12.11) when the plan was executed.
# The drift test recomputes them from the installed stdlib, and
# install_proactor_fix() re-checks them before activating.
EXPECTED_ANCHORS = {
    "BaseProactorEventLoop._start_serving":
        "ab41477dfa4b1a340e060783ed185c83e6b142b406f42bd15d24e32d2b88ec86",
    "IocpProactor.accept":
        "3a0f3acfbc8f81a2705e54ccc7d40d990388202ae35ea53ad6c0e0cc60d5fb49",
}


def _segment_sha(path: str, qualname: str) -> str:
    """SHA-256 of one function's AST source segment in a stdlib file.

    Same extraction the plan's anchor calculator used: walk the AST by
    qualname, hash ``ast.get_source_segment`` as UTF-8.
    """
    src = pathlib.Path(path).read_text(encoding="utf-8")
    tree = ast.parse(src)
    parts = qualname.split(".")

    def find(nodes, name):
        for node in nodes:
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef,
                                 ast.ClassDef)) and node.name == name:
                return node
        return None

    node = find(tree.body, parts[0])
    for part in parts[1:]:
        node = find(node.body, part)
    seg = ast.get_source_segment(src, node)
    return hashlib.sha256(seg.encode("utf-8")).hexdigest()


def anchors_status() -> dict:
    """Per-anchor comparison of the installed stdlib against the pins."""
    import asyncio.proactor_events as _pe
    import asyncio.windows_events as _we

    files = {
        "BaseProactorEventLoop._start_serving": _pe.__file__,
        "IocpProactor.accept": _we.__file__,
    }
    out = {}
    for qual, path in files.items():
        try:
            got = _segment_sha(path, qual)
        except Exception as exc:  # noqa: BLE001 - reported, not raised
            out[qual] = {"match": False,
                         "error": f"{type(exc).__name__}: {exc}"}
            continue
        out[qual] = {"match": got == EXPECTED_ANCHORS[qual],
                     "sha256": got}
    return out


class _SwallowedResets:
    """Process-wide count of WinError 64 accept resets converted by the
    mirror (plan 4.3). Incremented on the loop thread inside
    ``finish_accept``; read by /health."""

    def __init__(self) -> None:
        self.count = 0

    def increment(self) -> int:
        self.count += 1
        if self.count == 1 or self.count % 100 == 0:
            logger.info(
                "proactor accept fix: swallowed accept reset #%d "
                "(WinError 64 ERROR_NETNAME_DELETED)", self.count)
        return self.count


_SWALLOWED = _SwallowedResets()


def accept_resets_swallowed() -> int:
    return _SWALLOWED.count


class FixedIocpProactor(IocpProactor):
    """IocpProactor carrying PR 161238 touches 1 and 2.

    ``accept`` below is the installed 3.12.11 body verbatim except the
    two marked touches and the plan-4.3 counter increment.
    """

    def accept(self, listener):
        self._register_with_iocp(listener)
        conn = self._get_accept_socket(listener.family)
        ov = _overlapped.Overlapped(NULL)
        ov.AcceptEx(listener.fileno(), conn.fileno())

        def finish_accept(trans, key, ov):
            # PR 161238 touch 1: a reset of the pending accept becomes
            # ConnectionResetError instead of a raw OSError, so the
            # serving loop keeps the listening socket.
            try:
                ov.getresult()
            except OSError as exc:
                conn.close()
                # The peer reset the connection before it was accepted.
                if exc.winerror == _overlapped.ERROR_NETNAME_DELETED:
                    _SWALLOWED.increment()
                    raise ConnectionResetError(*exc.args)
                raise
            # Use SO_UPDATE_ACCEPT_CONTEXT so getsockname() etc work.
            buf = struct.pack('@P', listener.fileno())
            conn.setsockopt(socket.SOL_SOCKET,
                            _overlapped.SO_UPDATE_ACCEPT_CONTEXT, buf)
            conn.settimeout(listener.gettimeout())
            return conn, conn.getpeername()

        async def accept_coro(future, conn):
            # Coroutine closing the accept socket if the future is cancelled
            try:
                await future
            except exceptions.CancelledError:
                conn.close()
                raise
            # PR 161238 touch 2: the reset is reported to the caller
            # through the future; the side task stays silent.
            except ConnectionResetError:
                # Reported to the caller through the future.
                pass

        future = self._register(ov, listener, finish_accept)
        coro = accept_coro(future, conn)
        tasks.ensure_future(coro, loop=self._loop)
        return future


class FixedProactorEventLoop(asyncio.ProactorEventLoop):
    """ProactorEventLoop running FixedIocpProactor and carrying PR
    161238 touch 3 in ``_start_serving`` (installed body verbatim apart
    from the marked branch)."""

    def __init__(self, proactor=None):
        if proactor is None:
            proactor = FixedIocpProactor()
        super().__init__(proactor)

    def _start_serving(self, protocol_factory, sock,
                       sslcontext=None, server=None, backlog=100,
                       ssl_handshake_timeout=None,
                       ssl_shutdown_timeout=None):

        def loop(f=None):
            try:
                if f is not None:
                    conn, addr = f.result()
                    if self._debug:
                        logger.debug("%r got a new connection from %r: %r",
                                     server, addr, conn)
                    protocol = protocol_factory()
                    if sslcontext is not None:
                        self._make_ssl_transport(
                            conn, protocol, sslcontext, server_side=True,
                            extra={'peername': addr}, server=server,
                            ssl_handshake_timeout=ssl_handshake_timeout,
                            ssl_shutdown_timeout=ssl_shutdown_timeout)
                    else:
                        self._make_socket_transport(
                            conn, protocol,
                            extra={'peername': addr}, server=server)
                if self.is_closed():
                    return
                f = self._proactor.accept(sock)
            # PR 161238 touch 3: keep accepting after a reset; the
            # listening socket is still usable.
            except (ConnectionResetError, ConnectionAbortedError):
                # The peer closed the connection before it was accepted.
                # The listening socket is still usable: keep accepting.
                if self._debug:
                    logger.debug("Accept failed on socket %r",
                                 sock, exc_info=True)
                self.call_soon(loop)
            except OSError as exc:
                if sock.fileno() != -1:
                    self.call_exception_handler({
                        'message': 'Accept failed on a socket',
                        'exception': exc,
                        'socket': trsock.TransportSocket(sock),
                    })
                    sock.close()
                elif self._debug:
                    logger.debug("Accept failed on socket %r",
                                 sock, exc_info=True)
            except exceptions.CancelledError:
                sock.close()
            else:
                self._accept_futures[sock.fileno()] = f
                f.add_done_callback(loop)

        self.call_soon(loop)


class _FixedPolicy(asyncio.WindowsProactorEventLoopPolicy):
    """Event-loop policy whose new_event_loop() returns the fixed loop."""

    def new_event_loop(self):
        return FixedProactorEventLoop()


_INSTALLED = False


def install_proactor_fix() -> dict:
    """Install the fixed event-loop policy, subject to the kill-switch
    and the G-DRIFT anchor check. Any refusal leaves the stock policy
    untouched and is logged loudly; the daemon then runs stock code."""
    global _INSTALLED
    mode = os.environ.get(_KILL_SWITCH_ENV, "on").strip().lower()
    if mode == "off":
        logger.info("proactor accept fix NOT installed: kill-switch "
                    "%s=off", _KILL_SWITCH_ENV)
        return {"installed": False, "reason": "kill_switch_off"}
    if mode != "on":
        logger.error("proactor accept fix NOT installed: unrecognised "
                     "%s=%r (expected on|off); running stock",
                     _KILL_SWITCH_ENV, mode)
        return {"installed": False, "reason": "bad_mode"}
    status = anchors_status()
    if not all(v.get("match") for v in status.values()):
        logger.error("proactor accept fix NOT installed: stdlib anchor "
                     "mismatch %s; running stock (re-anchor per plan "
                     "G-DRIFT)", status)
        return {"installed": False, "reason": "anchor_mismatch",
                "anchors": status}
    asyncio.set_event_loop_policy(_FixedPolicy())
    _INSTALLED = True
    logger.info("proactor accept fix installed: loop=%s proactor=%s",
                FixedProactorEventLoop.__name__,
                FixedIocpProactor.__name__)
    return {"installed": True, "reason": "ok", "anchors": status}


def proactor_fix_active() -> bool:
    return _INSTALLED


def proactor_fix_status() -> dict:
    """The plan-4.3 observability values for the /health listener
    object (the loop class name is added by the caller, which runs on
    the loop it describes)."""
    return {"accept_resets_swallowed": _SWALLOWED.count,
            "proactor_fix_active": _INSTALLED}
