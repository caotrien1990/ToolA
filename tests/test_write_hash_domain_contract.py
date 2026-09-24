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


# [A]'s OWN writes. Measured 2026-09-24 before _seal_read_token existed: each of these
# left a token the guard could not read back, so the CA got FILE_CHANGED_OUTSIDE_ORCHESTRATOR
# for a change [A] made -- write_new_file sealed nothing, the reverts and the rollback sealed
# the RAW sha256 (no BOM or CRLF file matches it) under find_project_root instead of the
# project_path the CA writes with, and the write_fix revert folded CRLF to LF.
# The "pkg" row puts the file under a nested marker, where find_project_root != project_path.
_OWN_WRITES = [(b"", b"\r\n", ""), (b"\xef\xbb\xbf", b"\n", ""), (b"", b"\n", "pkg")]


def _project(tmp_path, sub, files):
    folder = tmp_path / sub if sub else tmp_path
    folder.mkdir(exist_ok=True)
    if sub:
        (folder / "requirements.txt").write_text("", encoding="utf-8")
    for name, data in files.items():
        (folder / name).write_bytes(data)
    surgical_mapper.ensure_minimal_index(str(tmp_path))
    rels = [f"{sub}/{name}" if sub else name for name in files]
    for rel in rels:
        assert json.loads(asyncio.run(code_tools.read_file_range(
            str(tmp_path / rel), 1, 20, project_path=str(tmp_path),
            force_reseed=True)))["read_token_seeded"]
    return rels


def _writable(tmp_path, rel):
    return _write_fix_guard_result(tmp_path / rel, str(tmp_path), rel, "")[1] == {}


@pytest.mark.parametrize("prefix,newline,sub", _OWN_WRITES)
def test_write_new_file_overwrite_leaves_a_reader_writable(tmp_path, prefix, newline, sub):
    (rel,) = _project(tmp_path, sub, {"made.py": prefix + b"value = 1" + newline})
    body = (prefix + b"a = 1" + newline + b"b = 2" + newline).decode("utf-8")
    made = json.loads(asyncio.run(code_tools.write_new_file(
        rel, new_content=body, project_path=str(tmp_path), overwrite=True)))
    assert made["ok"], made
    fp = tmp_path / rel
    assert fp.read_bytes() == body.encode("utf-8")
    assert made["new_hash"] == hashlib.sha256(fp.read_bytes()).hexdigest()
    assert _writable(tmp_path, rel)  # before the pin: an expected_hash match re-seals the token itself
    assert _write_fix_guard_result(fp, str(tmp_path), rel, made["new_hash"])[1] == {}
    fixed = json.loads(asyncio.run(code_tools.write_fix(
        rel, 2, 2, "b = 3", project_path=str(tmp_path))))
    assert fixed["ok"], fixed
    assert _writable(tmp_path, rel)


@pytest.mark.parametrize("prefix,newline,sub", _OWN_WRITES)
def test_auto_revert_restores_bytes_and_leaves_the_file_writable(tmp_path, prefix, newline, sub):
    original = prefix + newline.join([b"def f():", b"    return 1", b"", b"", b"value = f()", b""])
    (rel,) = _project(tmp_path, sub, {"keep.py": original})
    res = json.loads(asyncio.run(code_tools.write_fix(
        rel, 1, 2, "x = 1", project_path=str(tmp_path))))
    assert res["auto_reverted"] and not res["ok"], res
    assert (tmp_path / rel).read_bytes() == original
    assert _writable(tmp_path, rel)


@pytest.mark.parametrize("prefix,newline,sub", _OWN_WRITES)
def test_write_new_file_validation_revert_leaves_the_file_writable(tmp_path, monkeypatch, prefix, newline, sub):
    original = prefix + b"value = 1" + newline
    (rel,) = _project(tmp_path, sub, {"back.py": original})

    async def failing(*_a, **_k):
        return json.dumps({"ok": False, "error": "forced"})

    monkeypatch.setattr(code_tools, "validate_file", failing)
    res = json.loads(asyncio.run(code_tools.write_new_file(
        rel, new_content="value = 2\n", project_path=str(tmp_path), overwrite=True)))
    assert res["error_name"] == "VALIDATION_FAILED_AUTO_REVERT", res
    assert (tmp_path / rel).read_bytes() == original
    assert _writable(tmp_path, rel)


@pytest.mark.parametrize("prefix,newline,sub", _OWN_WRITES)
def test_transaction_rollback_leaves_the_file_writable(tmp_path, prefix, newline, sub):
    import tools_v15

    original = prefix + b"value = 1" + newline
    one, two = _project(tmp_path, sub, {
        "one.py": original, "two.py": b"def g():\n    return 2\n\n\nvalue = g()\n"})
    res = json.loads(asyncio.run(tools_v15.write_transaction([
        {"file_path": one, "start_line": 1, "end_line": 1, "new_content": "value = 2\n"},
        {"file_path": two, "start_line": 1, "end_line": 2, "new_content": "x = 1\n"},
    ], project_path=str(tmp_path), backup=False)))
    assert res["error_name"] == "BATCH_VERIFY_FAILED_AUTO_REVERT", res
    assert (tmp_path / one).read_bytes() == original
    assert _writable(tmp_path, one)
    assert json.loads(asyncio.run(code_tools.cache_delta(str(tmp_path))))["modified"] == []
    fixed = json.loads(asyncio.run(code_tools.write_fix(
        one, 1, 1, "value = 3", project_path=str(tmp_path))))
    assert fixed["ok"], fixed


@pytest.mark.parametrize("prefix,newline,sub", _OWN_WRITES)
def test_a_pinned_expected_hash_seals_the_token_the_guard_reads(tmp_path, prefix, newline, sub):
    (rel,) = _project(tmp_path, sub, {"pin.py": prefix + b"value = 1" + newline})
    raw = hashlib.sha256((tmp_path / rel).read_bytes()).hexdigest()
    assert _write_fix_guard_result(tmp_path / rel, str(tmp_path), rel, raw)[1] == {}
    assert _writable(tmp_path, rel)


def test_restore_reports_a_cache_sync_it_did_not_get(tmp_path, monkeypatch):
    from orchestrator.tools.write.core import _restore_backup_and_reseed

    fp = tmp_path / "r.py"
    fp.write_bytes(b"value = 2\n")
    bak = tmp_path / "r.py.bak"
    bak.write_bytes(b"value = 1\r\n")
    surgical_mapper.ensure_minimal_index(str(tmp_path))
    monkeypatch.setattr(code_tools, "_update_cache_after_write", lambda *a, **k: False)
    assert _restore_backup_and_reseed(fp, bak, str(tmp_path)) is False
    assert fp.read_bytes() == b"value = 1\r\n"
