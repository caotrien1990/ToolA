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
    return isinstance(reason, ConnectionRefusedError) or getattr(reason, "errno", None) in {61, 10061}


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


def controller_health(url: str, timeout: float = 1.0, opener: Callable[..., object] = urllib.request.urlopen) -> dict[str, object]:
    parsed = urllib.parse.urlparse(url)
    health_url = urllib.parse.urlunparse((parsed.scheme, parsed.netloc, "/health", "", "", ""))
    try:
        with opener(health_url, timeout=timeout) as response:
            payload = json.loads(response.read().decode("utf-8"))
        return {"configured": True, "reachable": bool(payload.get("ok")), "url": health_url}
    except (OSError, ValueError, urllib.error.URLError, json.JSONDecodeError) as exc:
        reason = exc.reason if isinstance(exc, urllib.error.URLError) else exc
        return {
            "configured": True,
            "reachable": False,
            "url": health_url,
            "error_type": type(exc).__name__,
            "error_message": str(exc),
            "connection_refused": _is_connection_refused(exc),
        }


def main(argv: list[str] | None = None) -> int:
    """[A:S] Path A: signed POST to :8085. Exit 0 verified, 2 skip, 3 guard, 1 failed."""
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
        print("[LOI] RESTART_GUARD_BLOCKED")
        return 3
    if result.get("ok") and (error_name == "RESTART_VERIFIED" or result.get("new_pid")):
        print("[OK] RESTART_VERIFIED pid=" + str(result.get("new_pid") or ""))
        return 0
    print("[WARN] control did not verify: " + (error_name or "unknown"))
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
