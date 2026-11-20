"""V-1 (S4 claim, P16_S4_CRASH_CLAIM_OPE_20261120.md §5): EXECUTE the call.

D1's functional test simulated the dict-literal merge and never executed
_error_response, which is exactly how the double-spread TypeError shipped.
This file executes BOTH forms:
  - negative control: the CRASH form (**material, **duplicate) must raise
    TypeError at argument binding (a property of the Python call, identical
    pre- and post-fix);
  - fixed form: merge-first ({**material, **duplicate}) must return a
    web.Response with status 409 and the full material + guard key sets.
"""
import json
import sys
import uuid
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import orchestrator_server as m

TOOL = "cbm_template_query"
ERR_NAME = "UNSUPPORTED_FIELD"
MATERIAL_13 = [
    "hint", "payload_example", "retry_sequence", "ca_guidance", "phase",
    "type", "severity", "suggested_fallback", "corrected_payload",
    "corrected_payload_source", "alias_remediable", "accepted_fields",
    "unsupported_fields",
]
GUARD_7 = [
    "original_error_name", "reason", "fingerprint", "retry_count",
    "cached", "first_seen_at", "seconds_since_first_seen",
]


def _fresh_material_and_duplicate():
    """Call-site arguments per :16564-16569; unique identity keeps the
    fingerprint pristine (guard first call returns None and arms state)."""
    body = {
        "project_path": str(Path(__file__).resolve().parents[1]),
        "template": "callers",
        "name": "build_correction_material",
        "from_symbol": "ope-v1-" + uuid.uuid4().hex,
    }
    unsupported = ["from_symbol"]
    message = "Unsupported field(s) for '%s': %s" % (TOOL, ", ".join(unsupported))
    allowed = sorted(m._registry_props_for_tool(TOOL))
    accepted = sorted(m._accepted_fields_for_tool(TOOL))
    material = m.build_correction_material(
        TOOL, ERR_NAME, body, allowed, unsupported, accepted,
        m._payload_example_for_tool(TOOL, m._schema_for_tool(TOOL)),
        None, error_message=message, status=400,
    )
    identity = "ope-v1-" + uuid.uuid4().hex
    assert m._duplicate_retry_guard(identity, TOOL, body, ERR_NAME) is None
    duplicate = m._duplicate_retry_guard(identity, TOOL, body, ERR_NAME)
    assert isinstance(duplicate, dict)
    return body, message, material, duplicate


def test_overlap_is_only_retryable_and_value_preserving():
    _, _, material, duplicate = _fresh_material_and_duplicate()
    assert sorted(set(material) & set(duplicate)) == ["retryable"]
    assert material["retryable"] is False
    assert duplicate["retryable"] is False


def test_negative_control_crash_form_raises_typeerror():
    body, message, material, duplicate = _fresh_material_and_duplicate()
    with pytest.raises(TypeError, match="multiple values for keyword argument 'retryable'"):
        m._error_response(
            TOOL, message, 409, {}, body,
            **material, **duplicate,
            accepted_aliases=m._alias_metadata(TOOL),
        )


def test_fixed_merged_form_returns_409_full_payload():
    body, message, material, duplicate = _fresh_material_and_duplicate()
    _merged = {**material, **duplicate}
    resp = m._error_response(
        TOOL, message, 409, {}, body,
        **_merged,
        accepted_aliases=m._alias_metadata(TOOL),
    )
    assert resp.status == 409
    payload = json.loads(resp.text)
    assert payload["error_name"] == "DUPLICATE_RETRY_BLOCKED"
    assert payload["ok"] is False
    assert payload["retryable"] is False
    assert payload["original_error_name"] == "UNSUPPORTED_FIELD"
    for key in MATERIAL_13:
        assert key in payload, "missing material key: " + key
    assert "ca_next_action" in payload
    for key in GUARD_7:
        assert key in payload, "missing guard key: " + key


def test_fc8_accepted_fields_include_definition_version():
    """§4 (F-C8): the ONE derivation now derives from the served schema."""
    accepted = m._accepted_fields_for_tool("cbm_graph_status")
    assert "definition_version" in accepted
    served = m._enriched_tool_schema("cbm_graph_status")
    props = set(served["function"]["parameters"]["properties"])
    assert set(accepted) == props | set(m._ARG_ALIASES.get("cbm_graph_status", {}) or {})
    # unknown tool keeps the pre-existing empty-for-unknown fallback
    assert m._accepted_fields_for_tool("no_such_tool_xyz_ope") == []
