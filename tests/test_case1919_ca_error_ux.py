"""CASE-1919: four CA-facing error contracts, one per reported guessing trap.

Each test pins one item of the 2026-09-19 order. What every item has in common:
the daemon already KNEW the answer and answered with prose the CA then had to
test by trial, or -- item 3 -- did not answer at all.

  1. MISSING_REQUIRED_FIELD named no field and offered a "<required_field>"
     placeholder, so a CA that was never taught that payload had to guess which
     name was missing.
  2. expected_hash: read_file_range.content_hash and the raw-byte digest are both
     64 hex chars, so the old error could only WARN that the value MIGHT be the
     wrong domain. It now measures both digests on the current file and states
     which one was received.
  3. A relative project_path resolves against the DAEMON's working directory, not
     the caller's, and nothing said so -- a silent "answer about another project".
  4. UNKNOWN_TOOL: two of the three _unknown_tool_payload branches carry no
     discovery_url/suggestions, and _error_response indexed both -> KeyError ->
     HTTP 500 with no JSON body at all.

Item 4 needs the _error_response path specifically. Its existing coverage
(tests/test_ux_discovery_lifecycle_contract.py) calls _unknown_tool_payload
in-process, which is exactly why a green suite never saw the 500.

Run:  ./venv312/Scripts/python.exe -m pytest tests/test_case1919_ca_error_ux.py -v
"""
import asyncio
import hashlib
import json
import os

import pytest

import code_tools
import orchestrator_server as srv
import surgical_mapper
from orchestrator.tools.write.core import _write_fix_guard_result

# read_file_range is the anchor for item 1: required == exactly ["file_path"], a
# single stable name, so the assertions below pin behaviour and not a tally.
_ANCHOR_TOOL = "read_file_range"
_ANCHOR_REQUIRED = "file_path"


# --------------------------------------------------------------------------
# Item 1 -- name the field, do not describe it
# --------------------------------------------------------------------------
def test_missing_required_detail_names_the_field_and_seeds_the_example():
    body = {"project_path": "D:/project"}
    detail = srv._missing_required_detail(_ANCHOR_TOOL, "", body)
    assert detail["missing_fields"] == [_ANCHOR_REQUIRED]
    # The example is registry-built, so it carries the missing name AND keeps the
    # caller's own values -- a CA can resend it without re-deriving the payload.
    assert _ANCHOR_REQUIRED in detail["payload_example"]
    assert detail["payload_example"]["project_path"] == "D:/project"
    assert _ANCHOR_REQUIRED in detail["accepted_fields"]
    assert "project_path" in detail["accepted_fields"]
    assert f"'{_ANCHOR_REQUIRED}'" in detail["error_detail"]
    assert detail["ca_next_action"] == "rebuild_payload_using_required_and_accepted_fields"


def test_retry_sequence_no_longer_offers_a_placeholder_field_name():
    sequence = srv._retry_sequence(
        _ANCHOR_TOOL, "MISSING_REQUIRED_FIELD", {"project_path": "D:/project"})
    assert sequence[0]["action"] == "add_missing_required_fields"
    assert sequence[0]["missing_fields"] == [_ANCHOR_REQUIRED]
    assert _ANCHOR_REQUIRED in sequence[0]["payload"]
    # "<required_field>" was the whole defect: a payload the CA could not execute.
    assert "<required_field>" not in json.dumps(sequence)


# --------------------------------------------------------------------------
# Item 2 -- measure the hash domain instead of warning about it
# --------------------------------------------------------------------------
def _seeded(tmp_path, name, original):
    """Write CRLF bytes (raw digest != normalized-text digest) and seed a token."""
    fp = tmp_path / name
    fp.write_bytes(original)
    surgical_mapper.ensure_minimal_index(str(tmp_path))
    read = json.loads(asyncio.run(code_tools.read_file_range(
        str(fp), 1, 10, project_path=str(tmp_path), force_reseed=True)))
    assert read["read_token_seeded"]
    return fp, read["content_hash"]


def test_content_hash_as_expected_hash_is_measured_and_retryable(tmp_path):
    original = b"value = 1\r\n"
    fp, content_hash = _seeded(tmp_path, "sample.py", original)
    raw_hash = hashlib.sha256(original).hexdigest()
    assert content_hash != raw_hash

    active, error, blocked = _write_fix_guard_result(
        fp, str(tmp_path), "sample.py", content_hash)
    assert active and blocked
    # error_name deliberately UNCHANGED: test_write_hash_domain_contract.py pins
    # EXPECTED_HASH_MISMATCH on this exact input. The fix is additive fields.
    assert error["error_name"] == "EXPECTED_HASH_MISMATCH"
    assert error["expected_hash_domain_measured"] is True
    assert error["expected_hash_is_read_file_range_content_hash"] is True
    assert error["file_raw_bytes_sha256"] == raw_hash
    assert error["file_normalized_text_sha256"] == content_hash
    assert error["file_text_unchanged_since_read"] is True
    assert error["retryable"] is True
    assert "MEASURED" in error["expected_hash_verdict"]
    assert fp.read_bytes() == original


def test_a_hash_from_neither_domain_is_not_laundered_into_retryable(tmp_path):
    original = b"value = 1\r\n"
    fp, _ = _seeded(tmp_path, "junk.py", original)
    active, error, blocked = _write_fix_guard_result(
        fp, str(tmp_path), "junk.py", "0" * 64)
    assert active and blocked
    assert error["expected_hash_is_read_file_range_content_hash"] is False
    assert error["file_text_unchanged_since_read"] is False
    assert error["retryable"] is False
    # Both current digests are still reported, so one re-read closes it.
    assert error["file_raw_bytes_sha256"] == hashlib.sha256(original).hexdigest()
    assert error["file_normalized_text_sha256"]
    assert fp.read_bytes() == original


# --------------------------------------------------------------------------
# Item 3 -- a relative project_path must never resolve silently
# --------------------------------------------------------------------------
def test_relative_project_path_is_disclosed_not_rewritten():
    body, applied = srv._normalize_body(
        "search_code", {"project_path": ".", "query": "anything"})
    disclosure = body["_relative_project_path"]
    assert disclosure["project_path_was_relative"] is True
    assert disclosure["sent"] == "."
    assert disclosure["resolved_against"] == os.getcwd()
    assert disclosure["ca_next_action"] == "resend_with_absolute_project_path"
    # Disclose, never rewrite: the multi-base resolver downstream keeps its own
    # fallbacks, and rewriting here would change ~every tool's resolution.
    assert body["project_path"] == "."
    assert all("_relative_project_path" not in str(a) for a in applied)


def test_absolute_project_path_carries_no_disclosure(tmp_path):
    body, _ = srv._normalize_body(
        "search_code", {"project_path": str(tmp_path), "query": "anything"})
    assert "_relative_project_path" not in body


def test_project_path_resolution_survives_a_fields_projection():
    payload = {"ok": True, "results": [],
               "project_path_resolution": {"project_path_was_relative": True}}
    projected = srv._r101_filter(payload, "results")
    # A projection that could hide this turns "wrong project" into "looks correct".
    assert "project_path_resolution" in projected
    assert "project_path_resolution" in projected["fields_force_kept"]
    assert projected["response_incomplete"]


# --------------------------------------------------------------------------
# Item 4 -- UNKNOWN_TOOL over HTTP answered 500 with no JSON
# --------------------------------------------------------------------------
@pytest.mark.parametrize("tool_name,surface", [
    ("call_tool", "mcp_dispatcher"),
    ("ca_next_tool", "response_field"),
])
def test_unknown_tool_branches_without_discovery_url_still_answer_json(tool_name, surface):
    branch = srv._unknown_tool_payload(tool_name)
    assert "discovery_url" not in branch  # the exact key _error_response indexed
    assert not branch["suggestions"]

    response = srv._error_response(
        tool_name, f"Unknown tool: {tool_name}", 404, {},
        {"project_path": "D:/project"}, error_name="UNKNOWN_TOOL")
    payload = json.loads(response.body)
    assert response.status == 404
    assert payload["error_name"] == "UNKNOWN_TOOL"
    assert payload["surface"] == surface
    assert isinstance(payload["payload_example"], dict) and payload["payload_example"]
    guidance = payload["ca_guidance"]
    assert guidance["surface"] == surface
    assert guidance["ca_next_action"]
    assert guidance["call_pattern"]
    assert guidance["see_also"] == {}


def test_unknown_tool_typo_still_gets_ranked_suggestions():
    response = srv._error_response(
        "read_file_rang", "Unknown tool: read_file_rang", 404, {}, {},
        error_name="UNKNOWN_TOOL")
    payload = json.loads(response.body)
    assert payload["suggestions"]
    see_also = payload["ca_guidance"]["see_also"]
    assert see_also
    assert all(url.startswith("/api/tools/") for url in see_also.values())
