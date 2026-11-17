"""P16 Sprint 6 (O-8): EMIT-NONE -- handler emits NO ca_guidance pre-set.

Filed branch: EMIT-NONE, rule R-O8-3 via the R-O8-5 emit-none
conjunction (filed decision: records/o8-decision.md in the campaign
home; evidence: records/o8-evidence-pack.md). The two handler
pre-sets in orchestrator/tools/write/core.py -- the binary-shape
ca_guidance kwarg (pre-fix :1662-1666) and the range-shape
ca_guidance dict entry (pre-fix :1700-1702) -- were DELETED, so the
funnel setdefault stamp (orchestrator_server.py:17093) applies the
full computed 13-key guidance. None-substitution (or any
present-value substitution) is FORBIDDEN: setdefault fills only
ABSENT keys, so a present None would survive and fail the leaf.

NAMED LOSS (decision record section 3, C1 R3): both tailored note
strings are LOST under emit-none, replaced by the funnel value's
own 4 generic notes:
  binary: "Do not use text diff tools on binary files."
  range:  "use start_line from the same read_file_range response;
           native editor line numbers are not accepted."
These tests assert the loss: funnel notes present, tailored
handler notes absent, on both paths. The binary shape's
non-demand keys (tool_category, retry_sequence) are likewise
absent from served answers; no code path reads them.

No gate/passthrough assertion is owed: no gate predicate exists
on this branch (the filed decision's scope-gate assertion governs
the funnel-merge branch's gated reach only).

Every test drives the REAL handler in-process (write_fix /
write_fix_html) on TemporaryDirectory scratch and applies the
REAL funnel stamp (setdefault with real _ca_guidance output).
No fixtures, per the F5-FIXTURE decision: shapes live here as
explicit assertions. DB env is redirected by the pytest
invocation (--basetemp under %TEMP% + ORCHESTRATOR_*_DB).
"""

from __future__ import annotations

import asyncio
import json
import tempfile
from pathlib import Path

import code_tools as ct
import orchestrator_server as srv  # noqa: F401  (assembles TOOL_SCHEMAS: 238 -> 260)
from orchestrator.core import returns_shape as rs
from orchestrator.tools.write.core import write_fix

_REPO_ROOT = Path(srv.__file__).resolve().parent
_BASELINE_FILE = _REPO_ROOT / "orchestrator" / "core" / "returns_baseline.json"

_DEMAND_13 = [
    "phase", "phase_label", "error_name", "ca_next_action",
    "call_pattern", "payload_example", "required_fields",
    "accepted_aliases", "notes", "known_gotchas",
    "recommended_prompt_rule", "see_also",
    "suggested_alternative_tool",
]
_FUNNEL_NOTES = [
    "Use PowerShell hashtable -> ConvertTo-Json; do not hand-write JSON for multiline content.",
    "Use project_path for the project root. Prefer relative file_path/path inside that root.",
    "For write_fix, write_fix_html, and write_new_file, use content_encoding='base64' when content has newlines, quotes, backslashes, HTML, JS, or large blocks.",
    "Read schema with GET http://localhost:8083/api/tools if a field is unclear.",
]
_TAILORED_BINARY = "Do not use text diff tools on binary files."
_TAILORED_RANGE = (
    "use start_line from the same read_file_range response; "
    "native editor line numbers are not accepted."
)
_WRITE_FIX_PIN = "f97f8a6c91a804913ee8ef3f98c114bbe3a6c23f495ebb6d057af7f8b9c554ec"
_WARN5 = [
    "missing:ca_guidance.phase",
    "missing:ca_guidance.phase_label",
    "missing:ca_guidance.error_name",
    "missing:ca_guidance.ca_next_action",
    "missing:ca_guidance.call_pattern",
]
_BINARY_SHAPE = {
    "tool_category": "mutating",
    "retry_sequence": [],
    "notes": [_TAILORED_BINARY],
}
_RANGE_SHAPE = {"notes": [_TAILORED_RANGE]}

_BINARY_KEYS = [
    "ca_next_action", "error", "error_name", "ok", "phase", "reason",
    "retryable", "severity", "suggested_fallback", "type",
]
_RANGE_KEYS = ["ca_next_action", "error", "error_name", "ok", "total_lines"]


def _raw_len(answer) -> int:
    return len(json.dumps(answer, ensure_ascii=False, separators=(",", ":")).encode("utf-8"))


def _check(tool, answer):
    return rs.check_returns(tool, answer, _raw_len(answer))


def _assert_leaf_pass(tool, answer):
    verdict, _reason, mismatches = _check(tool, answer)
    assert verdict == "pass", (tool, verdict, mismatches, answer)
    assert mismatches == [], (tool, mismatches, answer)


def _stamp(tool, answer, body):
    """The REAL funnel stamp: setdefault with REAL _ca_guidance output."""
    stamped = dict(answer)
    stamped.setdefault(
        "ca_guidance",
        srv._ca_guidance(tool, str(answer.get("error", "")), dict(body), str(answer.get("error_name", ""))),
    )
    return stamped


def _run(fn, file_name, start_line, end_line, project_path):
    return json.loads(asyncio.run(fn(file_name, start_line, end_line, "x", project_path=project_path)))


def _binary_input(tmp):
    (Path(tmp) / "probe.bin").write_bytes(bytes(range(256)) * 4)
    return "probe.bin"


def _range_input(tmp):
    (Path(tmp) / "probe.txt").write_bytes(b"line one\nline two\nline three\n")
    return "probe.txt"


def _assert_emit_none(tool, fn, file_name, start_line, end_line, tmp, error_name, expected_keys):
    """Handler emits NO ca_guidance; the funnel stamp applies the full 13 keys."""
    body = {
        "project_path": tmp, "file_path": file_name,
        "start_line": start_line, "end_line": end_line, "new_content": "x",
    }
    answer = _run(fn, file_name, start_line, end_line, tmp)
    assert answer.get("ok") is False, answer
    assert answer.get("error_name") == error_name, answer
    assert "ca_guidance" not in answer, answer
    assert sorted(answer.keys()) == expected_keys, answer
    assert "tool_category" not in answer, answer
    assert "retry_sequence" not in answer, answer
    stamped = _stamp(tool, answer, body)
    guidance = stamped["ca_guidance"]
    assert sorted(guidance.keys()) == sorted(_DEMAND_13), guidance
    assert len(guidance) == 13, guidance
    assert guidance["notes"] == _FUNNEL_NOTES, guidance
    dumped = json.dumps(stamped, ensure_ascii=False)
    assert _TAILORED_BINARY not in dumped, dumped
    assert _TAILORED_RANGE not in dumped, dumped
    _assert_leaf_pass(tool, stamped)
    return stamped


def test_binary_shape_emits_no_guidance_write_fix():
    """Pre-fix core.py:1662-1666 -- the binary kwarg is gone; funnel stamps."""
    with tempfile.TemporaryDirectory() as tmp:
        name = _binary_input(tmp)
        _assert_emit_none("write_fix", write_fix, name, 1, 1, tmp, "BINARY_FILE_UNSUPPORTED", _BINARY_KEYS)


def test_range_past_eof_emits_no_guidance_write_fix():
    """Pre-fix core.py:1700-1702 -- the range entry is gone; funnel stamps."""
    with tempfile.TemporaryDirectory() as tmp:
        name = _range_input(tmp)
        _assert_emit_none("write_fix", write_fix, name, 999, 999, tmp, "RANGE_PAST_EOF", _RANGE_KEYS)


def test_range_out_of_bounds_emits_no_guidance_write_fix():
    """Same range shape, second error name (start_line < 1)."""
    with tempfile.TemporaryDirectory() as tmp:
        name = _range_input(tmp)
        _assert_emit_none("write_fix", write_fix, name, 0, 0, tmp, "RANGE_OUT_OF_BOUNDS", _RANGE_KEYS)


def test_binary_shape_emits_no_guidance_write_fix_html():
    """Shared handler via the verbatim non-HTML route (code_tools.py:1680)."""
    with tempfile.TemporaryDirectory() as tmp:
        name = _binary_input(tmp)
        _assert_emit_none("write_fix_html", ct.write_fix_html, name, 1, 1, tmp, "BINARY_FILE_UNSUPPORTED", _BINARY_KEYS)


def test_range_shape_emits_no_guidance_write_fix_html():
    """Shared handler via the verbatim non-HTML route (code_tools.py:1680)."""
    with tempfile.TemporaryDirectory() as tmp:
        name = _range_input(tmp)
        _assert_emit_none("write_fix_html", ct.write_fix_html, name, 999, 999, tmp, "RANGE_PAST_EOF", _RANGE_KEYS)


def test_funnel_output_passes_leaf_all_named_pairs():
    """E3/R0 gate pinned: REAL _ca_guidance output passes the leaf, 5/5."""
    body = {"project_path": str(_REPO_ROOT), "file_path": "x.txt", "start_line": 1, "end_line": 1, "new_content": "x"}
    cases = [
        ("write_fix", "Binary file unsupported by write_fix: x.bin", "BINARY_FILE_UNSUPPORTED"),
        ("write_fix", "start_line 99 out of range (file has 10 lines)", "RANGE_PAST_EOF"),
        ("write_fix", "start_line 0 out of range (file has 10 lines)", "RANGE_OUT_OF_BOUNDS"),
        ("write_fix_html", "Binary file unsupported by write_fix: x.bin", "BINARY_FILE_UNSUPPORTED"),
        ("write_fix_html", "start_line 99 out of range (file has 10 lines)", "RANGE_PAST_EOF"),
    ]
    for tool, err_text, err_name in cases:
        value = srv._ca_guidance(tool, err_text, dict(body), err_name)
        assert sorted(value.keys()) == sorted(_DEMAND_13), (tool, err_name, value)
        assert value["notes"] == _FUNNEL_NOTES, (tool, err_name, value)
        answer = {"ok": False, "error": "probe", "error_name": err_name, "ca_guidance": value}
        _assert_leaf_pass(tool, answer)


def test_named_loss_funnel_notes_replace_tailored_notes():
    """Decision record section 3: the loss is named and asserted, both paths."""
    with tempfile.TemporaryDirectory() as tmp:
        bin_name = _binary_input(tmp)
        range_name = _range_input(tmp)
        combos = [
            ("write_fix", write_fix, bin_name, 1, 1),
            ("write_fix", write_fix, range_name, 999, 999),
            ("write_fix_html", ct.write_fix_html, bin_name, 1, 1),
            ("write_fix_html", ct.write_fix_html, range_name, 999, 999),
        ]
        for tool, fn, name, start, end in combos:
            body = {"project_path": tmp, "file_path": name, "start_line": start, "end_line": end, "new_content": "x"}
            answer = _run(fn, name, start, end, tmp)
            assert "ca_guidance" not in answer, (tool, answer)
            stamped = _stamp(tool, answer, body)
            assert stamped["ca_guidance"]["notes"] == _FUNNEL_NOTES, (tool, stamped)
            dumped = json.dumps(stamped, ensure_ascii=False)
            assert _TAILORED_BINARY not in dumped, (tool, dumped)
            assert _TAILORED_RANGE not in dumped, (tool, dumped)


def test_write_fix_block_sha_triple_untouched():
    """Schema-untouched: x-audit == canonical == baseline pin, depth_D 4.

    A stable property holding identically pre- and post-commit; the
    diff-zero verification itself is a sprint-record check, not a
    runtime assertion (PLAN.md section 8 Tests).
    """
    baseline = json.loads(_BASELINE_FILE.read_text(encoding="utf-8"))
    assert len(baseline) == 38, len(baseline)
    for tool in ("write_fix", "write_fix_html"):
        block = rs.resolve_returns(tool)
        assert block is not None, tool
        assert rs.schema_state(block) == "audited", tool
        assert block["properties"]["ca_guidance"]["required"] == _DEMAND_13, tool
    block = rs.resolve_returns("write_fix")
    pin = baseline["write_fix"]
    assert block["x-audit"]["schema_sha256"] == rs.canonical_schema_sha(block)
    assert pin["sha256"] == rs.canonical_schema_sha(block)
    assert pin["sha256"] == _WRITE_FIX_PIN
    assert block["x-audit"]["depth_D"] == 4
    assert pin["depth_D"] == 4
    assert block["required"] == ["ok"]


def test_partial_shapes_still_warn():
    """Control: the leaf still warns on the OLD partial shapes (5 strings).

    A future partial pre-set fails the leaf -- and fails the
    key-absence assertions above, which pin the handler output.
    """
    for tool, shape, err_name in (
        ("write_fix", _BINARY_SHAPE, "BINARY_FILE_UNSUPPORTED"),
        ("write_fix", _RANGE_SHAPE, "RANGE_PAST_EOF"),
        ("write_fix_html", _BINARY_SHAPE, "BINARY_FILE_UNSUPPORTED"),
        ("write_fix_html", _RANGE_SHAPE, "RANGE_PAST_EOF"),
    ):
        answer = {"ok": False, "error": "probe", "error_name": err_name, "ca_guidance": shape}
        verdict, _reason, mismatches = _check(tool, answer)
        assert verdict == "warn", (tool, verdict, mismatches)
        assert mismatches == _WARN5, (tool, mismatches)
