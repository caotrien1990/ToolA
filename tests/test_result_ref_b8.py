"""S2 (B8) tests for the result_ref envelope fixes (read-limits campaign).

SOT: PLAN.md (toola-read-limits-20261106) section 3.3 + section 4 step S2.
These tests run the real helpers in-process -- ``orchestrator_server``
``_shape_tool_response_text`` / ``_result_ref_store`` /
``handle_result_ref`` -- on a Vietnamese multi-byte fixture, the seam the
S1 golden tests established. They pin the B8 deltas:

  * chunking aligned to UTF-8 character boundaries, with ``next_offset``
    returning the aligned offset, so following it reassembles the stored
    payload losslessly (100% sha256 match);
  * a requested offset inside a multi-byte character is moved back to
    that character's first byte and the response reports the offset
    actually used;
  * every chunk response carries ``content_sha256`` -- the hex sha256 of
    the full stored payload bytes (the exact bytes being chunked, which
    is also the ref value);
  * the ref store holds 256 entries with access-LRU eviction (a GET or a
    re-store refreshes an entry's position) plus a total-byte cap of
    8 MB (8388608 bytes); a single item larger than the total-byte cap
    is still stored, alone, and can be read back.

The store caps are module constants read at store time, so the LRU and
byte-cap tests shrink them with ``monkeypatch`` instead of filling the
real 8 MB / 256 entries; the production defaults are pinned separately
in ``test_store_defaults_are_256_entries_and_8mb``.
"""

from __future__ import annotations

import asyncio
import hashlib
import json

import pytest
from aiohttp.test_utils import make_mocked_request

import orchestrator_server as srv

VIET_TEXT = "Đọc tiếng Việt — dữ liệu thử nghiệm cho envelope. "


def _fixture_raw(repeats: int = 200) -> str:
    return json.dumps(
        {"ok": True, "result": VIET_TEXT * repeats},
        ensure_ascii=False)


def _chunk(ref: str, offset: int, max_bytes: int) -> dict:
    request = make_mocked_request(
        "GET", f"/api/result/{ref}?offset={offset}&max_bytes={max_bytes}",
        match_info={"ref": ref})
    response = asyncio.run(srv.handle_result_ref(request))
    assert response.status == 200
    return json.loads(response.text)


def _store(text: str) -> str:
    ref = hashlib.sha256(text.encode("utf-8", errors="replace")).hexdigest()
    srv._result_ref_store(ref, text)
    return ref


@pytest.fixture(autouse=True)
def _clean_ref_cache():
    srv._RESULT_REF_CACHE.clear()
    yield
    srv._RESULT_REF_CACHE.clear()


def _reassemble(ref: str, max_bytes: int) -> tuple[str, list[dict]]:
    chunks = []
    offset = 0
    while True:
        chunk = _chunk(ref, offset, max_bytes)
        chunks.append(chunk)
        if chunk["next_offset"] is None:
            break
        offset = chunk["next_offset"]
    return "".join(c["content"] for c in chunks), chunks


# (i) Reassembly over UTF-8-boundary chunks is lossless at several
# max_bytes, and content_sha256 matches the full payload on every chunk.
@pytest.mark.parametrize("max_bytes", [1024, 1030, 20000])
def test_chunk_reassembly_vietnamese_lossless(max_bytes):
    raw = _fixture_raw()
    shaped = json.loads(srv._shape_tool_response_text(
        "read_file_range", raw, max_bytes=4096))
    ref = shaped["result_ref"]
    data = raw.encode("utf-8", errors="replace")
    expected_sha = hashlib.sha256(data).hexdigest()
    assert ref == expected_sha

    joined, chunks = _reassemble(ref, max_bytes)
    assert joined.encode("utf-8") == data
    assert hashlib.sha256(joined.encode("utf-8")).hexdigest() == expected_sha
    for chunk in chunks:
        assert chunk["content_sha256"] == expected_sha
        assert chunk["total_bytes"] == len(data)
        assert "\ufffd" not in chunk["content"]
        # Each chunk's bytes are exactly the slice its offsets claim.
        start = chunk["offset"]
        assert chunk["content"].encode("utf-8") == data[
            start:start + chunk["returned_bytes"]]
    # Offsets chain exactly: every chunk starts where the previous ended.
    for prev, nxt in zip(chunks, chunks[1:]):
        assert nxt["offset"] == prev["next_offset"]


# (ii) A requested offset inside a multi-byte character is moved back to
# the character boundary and the response reports the offset used.
def test_offset_inside_character_moves_back_to_boundary():
    raw = _fixture_raw()
    ref = _store(raw)
    data = raw.encode("utf-8", errors="replace")

    # 1030 lands inside the 2-byte character whose first byte is 1029 in
    # this fixture (pinned by the S1/S2 golden analysis); the server must
    # report 1029 and serve the chunk starting there.
    chunk = _chunk(ref, 1030, 1030)
    assert chunk["offset"] == 1029
    assert chunk["content"].encode("utf-8") == data[1029:1029 + chunk["returned_bytes"]]
    assert not chunk["content"].startswith("\ufffd")

    # Independent derivation on a fresh mid-character offset: decoding
    # the bytes before it with errors="ignore" drops the partial
    # character, so the re-encoded length is the boundary offset.
    mid = next(i for i in range(100, len(data)) if (data[i] & 0xC0) == 0x80)
    expected_start = len(data[:mid].decode("utf-8", errors="ignore").encode("utf-8"))
    assert expected_start < mid
    chunk2 = _chunk(ref, mid, 1024)
    assert chunk2["offset"] == expected_start
    assert chunk2["content"].encode("utf-8") == data[
        expected_start:expected_start + chunk2["returned_bytes"]]
    assert chunk2["content_sha256"] == hashlib.sha256(data).hexdigest()


def test_store_defaults_are_256_entries_and_8mb():
    assert srv._RESULT_REF_MAX_ENTRIES == 256
    assert srv._RESULT_REF_MAX_BYTES == 8 * 1024 * 1024 == 8388608


# (iii) A single item larger than the total-byte cap is still stored --
# alone, every other entry evicted -- and can be read back whole.
def test_single_item_larger_than_total_byte_cap_still_stored(monkeypatch):
    monkeypatch.setattr(srv, "_RESULT_REF_MAX_BYTES", 1024)
    small_ref = _store("x" * 100)
    assert small_ref in srv._RESULT_REF_CACHE

    big_text = VIET_TEXT * 100  # ~6.7 KB of payload, far over the 1 KB cap
    big_ref = _store(big_text)
    assert list(srv._RESULT_REF_CACHE) == [big_ref]
    assert srv._RESULT_REF_CACHE[big_ref] == big_text

    joined, chunks = _reassemble(big_ref, 1024)
    assert joined == big_text
    assert chunks[0]["content_sha256"] == hashlib.sha256(
        big_text.encode("utf-8", errors="replace")).hexdigest()


# The total-byte cap evicts least-recently-used entries until the store
# total fits, when no single item exceeds the cap.
def test_total_byte_cap_evicts_oldest(monkeypatch):
    monkeypatch.setattr(srv, "_RESULT_REF_MAX_BYTES", 3000)
    refs = [_store(ch * 1000) for ch in "abcd"]
    # 4 x 1000 bytes over a 3000-byte cap: the oldest entry is evicted.
    assert refs[0] not in srv._RESULT_REF_CACHE
    assert refs[1:] == list(srv._RESULT_REF_CACHE)
    total = sum(len(v.encode("utf-8")) for v in srv._RESULT_REF_CACHE.values())
    assert total <= 3000


# (iv) Access-LRU: a GET refreshes an entry, so it survives an eviction
# that drops an older, untouched entry.
def test_lru_get_refreshes_entry(monkeypatch):
    monkeypatch.setattr(srv, "_RESULT_REF_MAX_ENTRIES", 3)
    ref_a = _store("a" * 10)
    ref_b = _store("b" * 10)
    ref_c = _store("c" * 10)
    assert list(srv._RESULT_REF_CACHE) == [ref_a, ref_b, ref_c]

    _chunk(ref_a, 0, 1024)  # GET refreshes A to the newest position
    assert list(srv._RESULT_REF_CACHE) == [ref_b, ref_c, ref_a]

    ref_d = _store("d" * 10)  # overflows capacity 3: evict the LRU entry
    assert ref_b not in srv._RESULT_REF_CACHE
    assert list(srv._RESULT_REF_CACHE) == [ref_c, ref_a, ref_d]


# (iv) Access-LRU: re-storing an entry also refreshes its position.
def test_lru_restore_refreshes_entry(monkeypatch):
    monkeypatch.setattr(srv, "_RESULT_REF_MAX_ENTRIES", 3)
    ref_a = _store("a" * 10)
    ref_b = _store("b" * 10)
    ref_c = _store("c" * 10)

    srv._result_ref_store(ref_a, "a" * 10)  # re-store refreshes A
    assert list(srv._RESULT_REF_CACHE) == [ref_b, ref_c, ref_a]

    ref_d = _store("d" * 10)
    assert ref_b not in srv._RESULT_REF_CACHE
    assert list(srv._RESULT_REF_CACHE) == [ref_c, ref_a, ref_d]
