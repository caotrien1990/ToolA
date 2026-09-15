"""Malformed reads must not claim success or refresh a writer's stale token."""
import asyncio
import json
from unittest import mock

import pytest

import code_tools
from orchestrator.tools.read import fs


def read(root, **kwargs):
    return json.loads(asyncio.run(code_tools.read_file_range(
        "sample.py", project_path=str(root), **kwargs)))


def test_live_schema_preserves_range_recovery_contract():
    schema = next(x["function"] for x in code_tools.TOOL_SCHEMAS
                  if x["function"]["name"] == "read_file_range")
    description = schema["parameters"]["properties"]["end_line"]["description"]
    assert "INVALID_RANGE" in description
    assert "without seeding" in description
    assert "not force_reseed" in description


@pytest.mark.parametrize("start,end", [(200, 120), (1, 0), (0, 2), (-1, 2), (200, -1)])
@pytest.mark.parametrize("force", [False, True])
def test_invalid_range_never_calls_token_seeder(tmp_path, start, end, force):
    (tmp_path / "sample.py").write_text("value = 1\n" * 437)
    with mock.patch.object(fs, "_seed_read_token_detailed") as seed:
        result = read(tmp_path, start_line=start, end_line=end, force_reseed=force)
    seed.assert_not_called()
    assert result["ok"] is False
    assert result["error_name"] == "INVALID_RANGE"
    assert result["read_token_seeded"] is False
    assert result["requested_start_line"] == start
    assert result["requested_end_line"] == end
    assert result["total_lines"] == 437
    assert result["ca_next_action"] == "correct_range"
    assert "range_ref" not in result
    assert "shown" not in result


def test_invalid_read_preserves_existing_stale_token(tmp_path):
    fp = tmp_path / "sample.py"
    fp.write_text("value = 1\n" * 437)
    assert read(tmp_path, start_line=1, end_line=1)["read_token_seeded"]
    before = code_tools._read_token_get(str(tmp_path), "sample.py")
    fp.write_text("value = 2\n" * 437)
    assert not read(tmp_path, start_line=200, end_line=120,
                    force_reseed=True)["read_token_seeded"]
    assert code_tools._read_token_get(str(tmp_path), "sample.py") == before
    corrected = read(tmp_path, start_line=200, end_line=200)
    assert corrected["shown"] == "200-200"
    assert corrected["result"] == "200: value = 2"
    assert corrected["read_token_seeded"]


def test_valid_range_cap_and_eof(tmp_path):
    (tmp_path / "sample.py").write_text("value = 1\n" * 437)
    assert read(tmp_path, start_line=224, end_line=437)["shown"] == "224-423"
    assert read(tmp_path, start_line=437, end_line=999)["shown"] == "437-437"
    past = read(tmp_path, start_line=438)
    assert past["error_name"] == "RANGE_PAST_EOF"
    assert past["read_token_seeded"] is False


def test_real_empty_file_is_explicit_and_has_no_inverted_reference(tmp_path):
    (tmp_path / "sample.py").write_text("")
    result = read(tmp_path)
    assert result["ok"] and result["empty_file"]
    assert result["result"] == "" and result["estimated_tokens"] == 0
    assert result["shown"] == "empty_file"
    assert result["range_ref"] is None
    assert result["selection"]["end_line"] is None
    assert result["read_token_seeded"]
    assert read(tmp_path, start_line=2, end_line=1)["error_name"] == "INVALID_RANGE"


def test_structural_selection_does_not_use_range_end(tmp_path):
    (tmp_path / "sample.py").write_text("def chosen():\n    return 1\n")
    result = read(tmp_path, scope="function", symbol="chosen", start_line=200, end_line=120)
    assert result["ok"] and result["shown"] == "1-2"


def test_batch_reports_invalid_item_without_seeding_it(tmp_path):
    (tmp_path / "sample.py").write_text("value = 1\n" * 437)
    with mock.patch.object(fs, "_seed_read_token_detailed") as seed:
        result = json.loads(asyncio.run(code_tools.read_context_batch(
            str(tmp_path), [{"file_path": "sample.py", "start_line": 200, "end_line": 120}])))
    seed.assert_not_called()
    assert result["items"][0]["error_name"] == "INVALID_RANGE"
