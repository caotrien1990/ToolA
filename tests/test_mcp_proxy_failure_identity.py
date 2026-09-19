"""A failed MCP proxy hop must not leave a mutating call without an identity.

Reported from the field on 2026-08-20: a `write_fix` through the MCP transport came
back as `MCP_PROXY_FAILED` with three keys -- `ok`, `error_name`, `error` -- and a 502.
For a write that is the worst possible answer. The proxy hop happens AFTER the request
may already have reached the daemon, so the write may well have committed and only the
response was lost; the caller was handed nothing to resolve that with. No
`operation_id`, no `write_status_url`, no `new_hash`, not even the `_mcp_proxy`
metadata the sibling `HTTPError` branch already attaches. A CA in that state can only
guess, and guessing "retry" is exactly what double-writes a file.

The two transport failures are also NOT the same answer, and collapsing them into one
502 is the substance of the bug:

* connection refused -- nothing reached the daemon, nothing ran, an identical retry is
  free and should be encouraged;
* timeout -- the daemon accepted the request and may still be running it, so the same
  retry can double-write.

These tests pin the distinction and the presence of a resolution path, not the exact
prose. They drive the real `dispatch_tool_call` proxy branch rather than calling the
payload builder alone, because the defect was that the branch did not USE a richer
payload -- a builder test alone would have passed against the broken code.
"""
from __future__ import annotations

import asyncio
import json
import socket
import sys
import threading
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import pytest

_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

import orchestrator_server as srv  # noqa: E402

# A schema-valid write_fix body: an invalid one is kept LOCAL by the dispatcher and
# would never reach the proxy branch under test.
_WRITE_BODY = {
    "project_path": str(_ROOT),
    "file_path": "tests/does_not_matter.py",
    "start_line": 1,
    "end_line": 1,
    "new_content": "pass\n",
}


def _closed_port() -> int:
    """A port nothing listens on, so connect() is REFUSED rather than hanging."""
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    return port


@pytest.fixture()
def blackhole_port():
    """A listener that accepts and then never answers, to force a read timeout."""
    server = socket.socket()
    server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    server.bind(("127.0.0.1", 0))
    server.listen(8)
    held: list[socket.socket] = []
    stop = threading.Event()

    def accept_and_stall() -> None:
        server.settimeout(0.25)
        while not stop.is_set():
            try:
                conn, _ = server.accept()
            except OSError:
                continue
            held.append(conn)  # keep it open: no response, no close

    thread = threading.Thread(target=accept_and_stall, daemon=True)
    thread.start()
    try:
        yield server.getsockname()[1]
    finally:
        stop.set()
        thread.join(timeout=2)
        for conn in held:
            try:
                conn.close()
            except OSError:
                pass
        server.close()


def _proxy_failure(monkeypatch, port, tool, body, timeout="180") -> dict:
    """Run one tool through the MCP proxy branch pointed at a broken endpoint.

    A37: the budget is pinned with the PER-TOOL knob, not ``MCP_PROXY_HTTP_TIMEOUT``.
    That one is a FLOOR -- ``_mcp_timeout_budget`` only ever raises with it (schema
    declaration + margin, long-class ceiling), so asking for 0.35 there bought the full
    180s ceiling and every black-hole test below really waited it out. The per-tool knob
    returns, so it is the only one that can LOWER a budget.
    """
    monkeypatch.setenv("ORCHESTRATOR_BASE_URL", "http://127.0.0.1:%d" % port)
    monkeypatch.setenv("MCP_PROXY_HTTP_TIMEOUT", timeout)
    monkeypatch.setenv("MCP_PROXY_TIMEOUT__" + tool.upper(), timeout)

    async def run():
        response = await srv.dispatch_tool_call(tool, dict(body), transport="mcp")
        return response.status, json.loads(response.text)

    status, payload = asyncio.run(run())
    assert payload.get("error_name") == "MCP_PROXY_FAILED", (
        "expected the proxy branch, got %r" % (payload.get("error_name"),)
    )
    assert status == 502
    return payload


def test_a_refused_connection_is_reported_as_nothing_having_run(monkeypatch) -> None:
    """Refused means the daemon never saw it, so a retry must be declared SAFE.

    Telling CA "undetermined" here would be needlessly expensive -- it would spend a
    write_status poll and a read-back to learn what the transport already proved.
    """
    payload = _proxy_failure(monkeypatch, _closed_port(), "write_fix", _WRITE_BODY)
    assert payload.get("commit_state") == "not_committed"
    assert payload.get("safe_to_retry") is True
    assert payload.get("write_performed") is False
    assert payload.get("ca_next_action") == "retry_same_call"


def test_a_timeout_must_not_claim_the_write_did_not_happen(monkeypatch, blackhole_port) -> None:
    """The whole point: a timed-out write is UNDETERMINED, never "not committed".

    This is the assertion that fails against the original three-key payload, which
    said nothing at all and so left a retry looking free.
    """
    payload = _proxy_failure(
        monkeypatch, blackhole_port, "write_fix", _WRITE_BODY, timeout="0.35"
    )
    assert payload.get("commit_state") == "unknown", (
        "a timeout was classified %r; the daemon may have completed the write"
        % (payload.get("commit_state"),)
    )
    assert payload.get("safe_to_retry") is False
    assert payload.get("retryable") is False
    assert payload.get("write_performed") is None, (
        "a timeout must not assert write_performed either way"
    )


def test_a_timed_out_write_hands_back_a_way_to_resolve_it(monkeypatch, blackhole_port) -> None:
    """An "undetermined" verdict is only useful with a route to determine it.

    The ledger is keyed by (project_path, file_path) as well as operation_id, and
    `handle_write_status` supports that lookup -- so the target query is the one that
    can actually answer "did my write land", from the side of the wire that knows.
    """
    payload = _proxy_failure(
        monkeypatch, blackhole_port, "write_fix", _WRITE_BODY, timeout="0.35"
    )
    url = payload.get("write_status_url") or ""
    assert url.startswith("/api/write_status?"), (
        "no write_status_url on a timed-out write: %r" % (url,)
    )
    query = parse_qs(urlparse(url).query)
    assert query.get("file_path") == [_WRITE_BODY["file_path"]], (
        "the write_status query does not name the file that was being written: %r" % query
    )
    assert query.get("project_path") == [_WRITE_BODY["project_path"]]
    assert "write_status" in (payload.get("fallback_sequence") or [])


def test_the_operation_id_is_not_invented_on_the_proxy_side(monkeypatch, blackhole_port) -> None:
    """A fabricated operation_id would be worse than none.

    `_write_ledger_operation_id` hashes the body the DISPATCHER sees, and the
    dispatcher pops or rewrites several fields (task_epoch, prompt_hash, fields,
    resolved project_path, agent_id) before opening the ledger. An id computed out
    here would be a plausible key for an entry that does not exist, and a 404 from
    write_status reads as "no write happened" -- the exact wrong conclusion.
    """
    payload = _proxy_failure(
        monkeypatch, blackhole_port, "write_fix", _WRITE_BODY, timeout="0.35"
    )
    if "operation_id" in payload:
        record = srv._WRITE_LEDGER.get(payload["operation_id"])
        assert record is not None, (
            "the response carries operation_id=%r, which is not in the ledger; a "
            "write_status lookup on it would 404 and read as 'no write happened'"
            % (payload["operation_id"],)
        )


def test_a_read_only_tool_is_told_to_just_retry(monkeypatch) -> None:
    """No ledger entry exists for a read, so a write_status_url would only 404."""
    payload = _proxy_failure(
        monkeypatch, _closed_port(), "search_code",
        {"project_path": str(_ROOT), "query": "def "},
    )
    assert payload.get("commit_state") == "not_applicable"
    assert payload.get("safe_to_retry") is True
    assert "write_status_url" not in payload, (
        "a read-only tool was handed a write_status_url, which cannot resolve"
    )


def test_the_failure_names_the_tool_and_the_underlying_cause(monkeypatch) -> None:
    """The sibling HTTPError branch stamps `_mcp_proxy`; this one used not to.

    Without it a log-only diagnosis is the only option, and the report that started
    this said the response gave no clue which hop broke.
    """
    payload = _proxy_failure(monkeypatch, _closed_port(), "write_fix", _WRITE_BODY)
    meta = payload.get("_mcp_proxy")
    assert isinstance(meta, dict) and meta.get("tool") == "write_fix", (
        "no _mcp_proxy metadata naming the tool: %r" % (meta,)
    )
    assert payload.get("reason"), "the underlying exception is not reported at all"
