"""Small authenticated loopback client for the restart control service."""

from __future__ import annotations

import json
import os
import sys
import time
import urllib.parse
import urllib.request
import urllib.error
import uuid
from typing import Callable

from .restart_protocol import encode_request, sign_request


class ControllerUnavailableBeforePost(ConnectionError):
    """The loopback connect was refused before any restart POST was attempted."""


def _is_connection_refused(exc: BaseException) -> bool:
    reason = exc.reason if isinstance(exc, urllib.error.URLError) else exc
    if isinstance(reason, ConnectionRefusedError):
        return True
    # WSAECONNREFUSED reaches us as `errno` on the socket-mapped paths and as
    # `winerror` on some raw Windows OSError paths. Checking only one of the two
    # let a genuine refusal fall through to the generic "unreachable" branch.
    return (getattr(reason, "errno", None) in {61, 10061}
            or getattr(reason, "winerror", None) == 10061)


def request_status(*, url: str, secret: bytes, request_id: str, timeout: float = 2.0,
                   opener: Callable[..., object] = urllib.request.urlopen) -> dict[str, object] | None:
    parsed = urllib.parse.urlparse(url)
    path = "/status/" + request_id
    status_url = urllib.parse.urlunparse((parsed.scheme, parsed.netloc, path, "", "", ""))
    request = urllib.request.Request(
        status_url, method="GET",
        headers={"X-Restart-Signature": sign_request(secret, "GET", path, b"")},
    )
    try:
        with opener(request, timeout=timeout) as response:
            return json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        if exc.code == 404:
            return None
        raise


def _poll_terminal(*, url: str, secret: bytes, request_id: str, deadline: float,
                   opener: Callable[..., object]) -> dict[str, object]:
    while time.monotonic() < deadline:
        try:
            status = request_status(
                url=url, secret=secret, request_id=request_id,
                timeout=min(2.0, max(0.1, deadline - time.monotonic())), opener=opener,
            )
            if status and status.get("terminal"):
                return status
        except urllib.error.URLError:
            pass
        time.sleep(0.1)
    return {"ok": False, "error_name": "RESTART_RESULT_TIMEOUT", "request_id": request_id,
            "status": "unknown", "terminal": False}


def request_restart(
    *,
    url: str,
    secret: bytes,
    expected_generation: str,
    request_id: str | None = None,
    timeout: float = 10.0,
    opener: Callable[..., object] = urllib.request.urlopen,
) -> dict[str, object]:
    if not url.startswith(("http://127.0.0.1:", "http://localhost:", "http://[::1]:")):
        raise ValueError("restart control URL must be loopback HTTP")
    request_id = request_id or uuid.uuid4().hex
    preflight = controller_health(url, timeout=min(1.0, timeout), opener=opener)
    if not preflight.get("reachable"):
        if preflight.get("connection_refused") is True:
            raise ControllerUnavailableBeforePost(str(preflight.get("error_message") or "connection refused"))
        return {"ok": False, "error_name": "RESTART_RESULT_UNKNOWN", "request_id": request_id,
                "status": "unknown", "terminal": False, "post_attempted": False}
    body = encode_request(request_id, expected_generation, time.time())
    request = urllib.request.Request(
        url,
        data=body,
        method="POST",
        headers={
            "Content-Type": "application/json",
            "X-Restart-Signature": sign_request(secret, "POST", urllib.parse.urlparse(url).path, body),
        },
    )
    deadline = time.monotonic() + timeout
    try:
        with opener(request, timeout=timeout) as response:
            accepted = json.loads(response.read().decode("utf-8"))
    except (urllib.error.URLError, TimeoutError, OSError):
        # POST response may be lost after acceptance. Prove controller availability
        # and recover this exact id before considering any legacy fallback.
        health = controller_health(url, timeout=min(1.0, timeout), opener=opener)
        try:
            status = request_status(url=url, secret=secret, request_id=request_id,
                                    timeout=min(1.0, timeout), opener=opener)
        except urllib.error.URLError:
            status = None
        if status is not None:
            return status if status.get("terminal") else _poll_terminal(
                url=url, secret=secret, request_id=request_id, deadline=deadline, opener=opener
            )
        if health.get("reachable"):
            # Retry the identical id only; controller accept is idempotent.
            try:
                with opener(request, timeout=min(2.0, max(0.1, deadline - time.monotonic()))) as response:
                    accepted = json.loads(response.read().decode("utf-8"))
            except Exception:
                return {"ok": False, "error_name": "RESTART_RESULT_UNKNOWN",
                        "request_id": request_id, "status": "unknown", "terminal": False,
                        "post_attempted": True}
        else:
            return {"ok": False, "error_name": "RESTART_RESULT_UNKNOWN",
                    "request_id": request_id, "status": "unknown", "terminal": False,
                    "post_attempted": True}
    if accepted.get("terminal"):
        return accepted
    return _poll_terminal(url=url, secret=secret, request_id=request_id,
                          deadline=deadline, opener=opener)


def _is_timeout(exc: BaseException) -> bool:
    reason = exc.reason if isinstance(exc, urllib.error.URLError) else exc
    if isinstance(reason, TimeoutError):
        return True
    return "timed out" in str(reason).lower()


def _classify_controller_error(exc: BaseException) -> tuple[str, bool | None]:
    """Name what actually happened, and say whether anything is listening.

    Returns (classification, listener_present).

    The misreport this ends: a loopback port with NOTHING listening came back as
    `URLError: <urlopen error timed out>` with `connection_refused: false`, which
    a reader can only interpret as "the controller is up but hung" -- the
    opposite of the truth. The cause was a probe budget shorter than the time
    the OS itself needs to deliver the refusal, so the probe's own deadline
    fired before the RST arrived and the refusal was never classified.

    `refused`         nothing is listening; the OS said so.        -> False
    `http_error`      something answered with a status.            -> True
    `bad_payload`     something answered, but not our JSON.        -> True
    `connect_timeout` the deadline hit before any answer. On
                      loopback a connect completes or is refused
                      well inside a second, so this points at too
                      small a budget far more often than at a
                      silent listener -- but it proves neither.    -> None
    `unreachable`     any other socket/OS failure.                 -> None
    """
    if _is_connection_refused(exc):
        return "refused", False
    if isinstance(exc, urllib.error.HTTPError):
        return "http_error", True
    if isinstance(exc, ValueError):  # includes json.JSONDecodeError
        return "bad_payload", True
    if _is_timeout(exc):
        return "connect_timeout", None
    return "unreachable", None


def controller_health(url: str, timeout: float = 1.0, opener: Callable[..., object] = urllib.request.urlopen) -> dict[str, object]:
    parsed = urllib.parse.urlparse(url)
    health_url = urllib.parse.urlunparse((parsed.scheme, parsed.netloc, "/health", "", "", ""))
    try:
        with opener(health_url, timeout=timeout) as response:
            payload = json.loads(response.read().decode("utf-8"))
        reachable = bool(payload.get("ok"))
        return {"configured": True, "reachable": reachable, "url": health_url,
                "classification": "ok" if reachable else "not_ok",
                "listener_present": True, "probe_timeout_s": timeout}
    except (OSError, ValueError, urllib.error.URLError, json.JSONDecodeError) as exc:
        classification, listener_present = _classify_controller_error(exc)
        return {
            "configured": True,
            "reachable": False,
            "url": health_url,
            "error_type": type(exc).__name__,
            "error_message": str(exc),
            "connection_refused": _is_connection_refused(exc),
            # `connection_refused: false` alone cannot distinguish "nothing is
            # listening" from "something accepted and went quiet". These two
            # fields can, and are additive: no existing reader loses a key.
            "classification": classification,
            "listener_present": listener_present,
            "probe_timeout_s": timeout,
        }


def main(argv: list[str] | None = None) -> int:
    """[A:S] Path A: signed POST to :8085.

    Exit 0 verified, 2 skip, 3 guard (genuine block, do NOT force), 4 guard
    (stale-snapshot-only, retryable — [A:S] retries Path A once), 1 failed.
    """
    import argparse
    from pathlib import Path

    from .restart_protocol import canonical_secret_path

    log_path = os.environ.get("TOOLA_AS_LOG")
    if log_path:
        try:
            handle = open(log_path, "a", encoding="utf-8")
            try:
                if sys.stdout is None:
                    raise OSError("no stdout")
                sys.stdout.write("")
                sys.stdout.flush()
            except Exception:
                sys.stdout = handle
                sys.stderr = handle
        except OSError:
            pass

    parser = argparse.ArgumentParser(prog="request_orchestrator_restart")
    parser.add_argument("--timeout", type=float, default=90.0)
    parser.add_argument("--livez", default="http://127.0.0.1:8084/livez")
    parser.add_argument("--control", default="http://127.0.0.1:8085/restart")
    args = parser.parse_args(argv)

    health = controller_health(args.control, timeout=1.0)
    if not health.get("reachable"):
        print("[SKIP] restart-control unreachable")
        return 2

    try:
        with urllib.request.urlopen(args.livez, timeout=3) as response:
            live = json.loads(response.read().decode("utf-8"))
        generation = live.get("generation")
        if not live.get("ok") or not generation:
            print("[SKIP] livez has no generation")
            return 2
    except Exception:
        print("[SKIP] livez unread")
        return 2

    root = Path(__file__).resolve().parents[2]
    secret_path = canonical_secret_path(root)
    try:
        secret = secret_path.read_bytes()
    except OSError:
        print("[SKIP] secret unreadable")
        return 2
    if len(secret) < 32:
        print("[SKIP] secret too short")
        return 2

    result = request_restart(
        url=args.control,
        secret=secret,
        expected_generation=str(generation),
        timeout=args.timeout,
    )
    error_name = str(result.get("error_name") or "")
    if error_name == "RESTART_GUARD_BLOCKED":
        # A39/PH-4: a block whose ONLY reason is a stale busy-snapshot *reading*
        # (nothing actually busy) is self-clearing — the next read gets a fresh
        # snapshot. Distinguish it with rc=4 so [A:S] can retry Path A ONCE instead
        # of hard-stopping at FAIL_GUARD. A genuine block (worker in flight, council
        # slot active, etc.) keeps rc=3 = "do not force". The distinction is derived
        # from the response fields, never from string-matching in the .bat.
        from .restart_guard import is_retryable_stale_block
        reasons = result.get("reasons")
        if isinstance(reasons, list) and is_retryable_stale_block(reasons):
            print("[RETRY] RESTART_GUARD_BLOCKED stale-snapshot only; retryable")
            return 4
        print("[LOI] RESTART_GUARD_BLOCKED")
        return 3
    if result.get("ok") and (error_name == "RESTART_VERIFIED" or result.get("new_pid")):
        print("[OK] RESTART_VERIFIED pid=" + str(result.get("new_pid") or ""))
        return 0
    print("[WARN] control did not verify: " + (error_name or "unknown"))
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
