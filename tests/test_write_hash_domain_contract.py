"""A normalized read hash must not trap a CA in a pointless reseed loop."""
import asyncio
import hashlib
import json

import pytest

import code_tools
import surgical_mapper
from orchestrator.tools.write.core import _write_fix_guard_result


@pytest.mark.parametrize("prefix,newline", [(b"", b"\r\n"), (b"\xef\xbb\xbf", b"\n")])
def test_normalized_hash_is_rejected_with_actionable_recovery(tmp_path, prefix, newline):
    fp = tmp_path / "sample.py"
    original = prefix + b"value = 1" + newline
    fp.write_bytes(original)
    surgical_mapper.ensure_minimal_index(str(tmp_path))

    def read():
        result = json.loads(asyncio.run(code_tools.read_file_range(
            str(fp), 1, 10, project_path=str(tmp_path), force_reseed=True)))
        assert result["read_token_seeded"]
        return result

    normalized = read()["content_hash"]
    assert normalized != hashlib.sha256(original).hexdigest()
    for _ in range(2):
        read()
        active, error, blocked = _write_fix_guard_result(
            fp, str(tmp_path), "sample.py", normalized)
        assert active and blocked
        assert error["error_name"] == "EXPECTED_HASH_MISMATCH"
        assert error["mismatch_field"] == "expected_hash"
        assert error["reseed_overrides_expected_hash"] is False
        assert "read_file_range.content_hash" in error["hint"]
        assert error["ca_next_action"] == "reread_and_review_patch"
        assert fp.read_bytes() == original

    # Recovery uses the stable read token, without weakening actual stale detection.
    assert _write_fix_guard_result(fp, str(tmp_path), "sample.py", "")[1] == {}
    fp.write_bytes(prefix + b"value = 2" + newline)
    assert _write_fix_guard_result(fp, str(tmp_path), "sample.py", "")[1][
        "error_name"] == "FILE_CHANGED_OUTSIDE_ORCHESTRATOR"


def test_raw_hash_from_successful_write_still_chains(tmp_path):
    fp = tmp_path / "chain.py"
    fp.write_bytes(b"value = 1\r\n")
    surgical_mapper.ensure_minimal_index(str(tmp_path))
    asyncio.run(code_tools.read_file_range(str(fp), 1, 10, project_path=str(tmp_path)))
    first = json.loads(asyncio.run(code_tools.write_fix(
        "chain.py", 1, 1, "value = 2", project_path=str(tmp_path))))
    assert first["ok"], first
    assert first["new_hash"] == hashlib.sha256(fp.read_bytes()).hexdigest()
    second = json.loads(asyncio.run(code_tools.write_fix(
        "chain.py", 1, 1, "value = 3", project_path=str(tmp_path),
        expected_hash=first["new_hash"])))
    assert second["ok"], second
    assert fp.read_bytes() == b"value = 3\r\n"
