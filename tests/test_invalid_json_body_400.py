"""F10/F11/F12/F16 -- what a corrupt request body and a blank dashboard model answer.

Measured in the 48h to 2026-09-27: every malformed POST body answered
error_name=TOOL_ERROR by fall-through, because the handler wrote the prose
"Invalid JSON body" and the marker list 10k lines away matched "invalid json
payload". The parser diagnostic was logged and dropped. Shape copied from
tests/test_path_control_char_rejection.py.
"""

from __future__ import annotations

import asyncio
import json
from unittest import mock

import pytest
from aiohttp import StreamReader
from aiohttp.test_utils import make_mocked_request

import code_tools
import orchestrator_server

# A truncated object: a plain structural parse error, no escape involved.
TRUNCATED = b'{"project_path": "D:/repo"'
# The Windows classic: a backslash path pasted straight into JSON, so "\0" and "\b"
# are read as escapes. This is the body shape that produced the real 400s.
MANGLED_BACKSLASH = b'{"project_path": "D:\\0. TRIEN\\bad"}'


def _raw_body(raw: bytes) -> StreamReader:
    loop = asyncio.new_event_loop()
    try:
        reader = StreamReader(protocol=mock.Mock(), limit=2 ** 16, loop=loop)
        reader.feed_data(raw)
        reader.feed_eof()
        return reader
    finally:
        loop.close()


def _post(raw: bytes, tool_name: str = "read_file_range"):
    request = make_mocked_request(
        "POST",
        f"/api/call/{tool_name}",
        headers={},
        payload=_raw_body(raw),
        match_info={"tool_name": tool_name},
    )
    response = asyncio.run(orchestrator_server._dispatch_tool_call_request(request))
    return response, json.loads(response.text)


# ---- F10: the producer names its error instead of encoding it in prose ------

@pytest.mark.parametrize("raw", [TRUNCATED, MANGLED_BACKSLASH, b"not json at all"])
def test_a_corrupt_body_is_named_invalid_json_body(raw):
    response, result = _post(raw)

    assert response.status == 400
    assert result["error_name"] == "INVALID_JSON_BODY"
    # The two names it used to answer with: TOOL_ERROR by fall-through, and
    # WRONG_ARGUMENTS if anyone had "fixed" this by widening the marker list.
    assert result["error_name"] not in {"TOOL_ERROR", "WRONG_ARGUMENTS"}


def test_the_name_does_not_depend_on_matching_the_message_text():
    # The defect was a string round-trip: the handler wrote prose, a matcher far away
    # re-derived a name from it, and the two drifted ("body" vs "payload"). Proof the
    # round-trip is gone: the name survives even with the inference path sabotaged.
    with mock.patch.object(
        orchestrator_server, "_infer_error_name", side_effect=AssertionError("must not run")
    ):
        _response, result = _post(TRUNCATED)

    assert result["error_name"] == "INVALID_JSON_BODY"


# ---- F11: the diagnostic the caller needs ----------------------------------

def test_the_parser_diagnostic_reaches_the_caller():
    _response, result = _post(MANGLED_BACKSLASH)

    # It was logged and then dropped, so the caller saw "Invalid JSON body" with no
    # column and no reason -- the one fact that repairs the payload.
    assert "Invalid JSON body:" in result["error"]
    assert "line 1 column" in result["error"]
    assert "escape" in result["error"].lower()


def test_the_diagnostic_is_never_empty_even_for_a_bare_parse_failure():
    _response, result = _post(TRUNCATED)

    assert result["error"].strip() != "Invalid JSON body:"
    assert len(result["error"]) > len("Invalid JSON body: ")


# ---- F12: one wording for "your backslashes broke it", two readers ---------

def test_a_mangled_backslash_reuses_the_single_forward_slash_hint():
    _response, result = _post(MANGLED_BACKSLASH)

    assert orchestrator_server._PATH_FORWARD_SLASH_HINT in result["hint"]


def test_the_graph_guard_reads_the_same_constant():
    # Both sites must quote ONE sentence. A copy is how the marker list drifted from
    # "body" to "payload" for months, so a second wording is the defect, not a detail.
    _cleaned, error = orchestrator_server._graph_clean_path("D:/repo\x00/child")

    assert error is not None
    assert orchestrator_server._PATH_FORWARD_SLASH_HINT in error


def test_a_structural_parse_error_does_not_blame_backslashes():
    _response, result = _post(TRUNCATED)

    assert orchestrator_server._PATH_FORWARD_SLASH_HINT not in result["hint"]
    assert "ConvertTo-Json" in result["hint"]


# ---- F13: the phase taxonomy has to route it as the caller's problem -------

def test_the_phase_is_schema_not_runtime():
    assert code_tools._phase_for_error_name("INVALID_JSON_BODY") == "schema"

    _response, result = _post(TRUNCATED)
    assert result["phase"] == "schema"


def test_the_contract_says_the_caller_can_retry():
    _response, result = _post(TRUNCATED)

    assert result["type"] == "SCHEMA_VIOLATION"
    # The 400 fall-through computed retryable from the status and answered False,
    # telling the CA to give up on an error it could fix in one edit.
    assert result["retryable"] is True
    assert result["ca_next_action"] == "fix_json_escaping_then_resend_body"


# ---- F3 must keep working: an EMPTY body is still the allowed fallback -----

def test_an_empty_body_is_not_a_corrupt_body():
    _response, result = _post(b"")

    assert result.get("error_name") != "INVALID_JSON_BODY"


# ---- F16: the dashboard showed a blank model --------------------------------

def test_the_usage_card_shows_the_configured_default_model():
    # Both dashboard sites passed ("", "") as the defaults into
    # _council_worker_model, which only reassigns provider/model when an override
    # VALIDATES -- so on any rejected or absent override the fallback WAS the empty
    # string and the card rendered blank. 17 warning rows since 2026-09-19 said
    # provider='' model='', naming nothing a reader could act on.
    defaults = {"probe-agent": {"provider": "probe-provider", "model": "probe-model-v9"}}
    with mock.patch.object(orchestrator_server, "_COUNCIL_DEFAULT_MODELS", defaults):
        html = orchestrator_server._council_metrics_table("probe-agent", {})

    assert "probe-model-v9" in html


def test_neither_dashboard_site_asks_for_empty_defaults_any_more():
    import inspect

    for fn in (
        orchestrator_server._council_metrics_table,
        orchestrator_server._council_build_agent_payloads,
    ):
        source = inspect.getsource(fn)
        assert '_council_worker_model(agent, "", "")' not in source
        assert "_council_requested_model(agent)" in source