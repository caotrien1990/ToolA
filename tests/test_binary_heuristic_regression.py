"""Regression tests for BINARY_FILE_UNSUPPORTED false positive (F-A1/F-A6).

Covers the binary-heuristic fix campaign on
``orchestrator/fileops/common.py::is_binary_file`` (S1: straddle-safe decode,
S2: ``.jsonl`` whitelist) and the ``code_tools._is_binary_file`` shim
(S3: bounded stream read replacing whole-file ``read_bytes``).

Run from the project root, same convention as
``tests/test_churn2707_tooling_hardening.py``::

    python -m pytest tests/test_binary_heuristic_regression.py -v

Per-run expectations (SOT: repo @ 483e180, branch fix/campaign-1608):

- Pre-S3:  13 pass / 3 red fail / 2 shim fail.
- Post-S3: 15 pass / 3 red fail
  (``test_fp_vietnamese_straddle``, ``test_fp_4099_valid_completion``,
  ``test_jsonl_whitelist_invalid_utf8``).
- Post-S1: 17 pass / 1 fail (``test_jsonl_whitelist_invalid_utf8``).
- Post-S2: 18/18 pass.

All fixtures are constructed programmatically in-code (byte literals);
no ``.dat`` files are checked in and no live repo files are read.
``.dat`` suffix = non-whitelisted, hits the UTF-8 decode branch.
``.txt`` is used where the UTF-16 BOM branch checks ``text_exts``.
``.jsonl`` is used for the S2 whitelist behavior-change test.
"""
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import code_tools
from orchestrator.fileops import common


def _run_is_binary(data: bytes, suffix: str, **kwargs) -> bool:
    """Write ``data`` to a temp file with ``suffix`` and run the heuristic.

    Calls ``common.is_binary_file`` -- the canonical implementation that
    S1/S2 fix. (The ``code_tools._is_binary_file`` shim is covered by the
    dedicated shim tests below; post-S3 it delegates here.) The temp
    directory is removed on exit; no repo files are touched.
    """
    with tempfile.TemporaryDirectory() as temp_dir:
        path = Path(temp_dir) / ("fixture" + suffix)
        path.write_bytes(data)
        return common.is_binary_file(path, **kwargs)


class BinaryHeuristicRedGreenTests(unittest.TestCase):
    """Red -> green: FAIL pre-S1/S2, PASS post."""

    def test_fp_vietnamese_straddle(self):
        # Provenance: frozen from the live false positive
        # ``_vm_room_v3.archive.user.jsonl``
        # (bytes 4093-4099 = 63 68 E1 BB 89 20 63;
        # ỉ U+1EC9 straddles the 4096-byte sample boundary).
        # Synthetic stand-in: 'a' padding replaces the live bytes, but the
        # straddling state itself is pinned by the asserts below, so a stale
        # (non-straddling) fixture cannot silently stop testing Sprint 1.
        data = b"a" * 4095 + "ỉ".encode("utf-8") + b"b" * 14
        # In-test freeze asserts (cf. W3-B5 C2):
        self.assertEqual(data[4095], 0xE1)  # lead byte of ỉ (U+1EC9 = E1 BB 89)
        self.assertEqual(data[4096], 0xBB)  # first continuation byte
        self.assertEqual(data[4096:4098], b"\xbb\x89")
        self.assertFalse(_run_is_binary(data, ".dat"))

    def test_fp_4099_valid_completion(self):
        # 4-byte char (U+1F600 = F0 9F 98 80) starts at index 4095 and
        # completes at byte 4099 (1-indexed). No-overcorrection pin per
        # W3-B5 C1: guards against "fixing" 4097/4098 by returning True
        # whenever len > sample_size.
        data = b"A" * 4095 + b"\xf0\x9f\x98\x80" + b"B"
        self.assertEqual(len(data), 4100)
        self.assertFalse(_run_is_binary(data, ".dat"))

    def test_jsonl_whitelist_invalid_utf8(self):
        # S2 behavior-change: ``.jsonl`` joins the text-extension whitelist,
        # so a NUL-free invalid-UTF-8 .jsonl is admitted as text at the gate.
        # Cross-ref PB1-E6/F-W3-1 (parked): write-path handling of such files
        # is governed there, not by this gate.
        data = b'{"key": "caf\xe9"}'
        self.assertNotIn(b"\x00", data)
        self.assertFalse(_run_is_binary(data, ".jsonl"))


class BinaryHeuristicInvariantTests(unittest.TestCase):
    """Invariant: PASS pre-fix and post-fix (true positives / true negatives)."""

    def test_small_invalid_cafe(self):
        self.assertTrue(_run_is_binary(b"caf\xe9", ".dat"))

    def test_small_torn(self):
        self.assertTrue(_run_is_binary(b"\xe2\x82", ".dat"))

    def test_nul(self):
        self.assertTrue(_run_is_binary(b"hello\x00world", ".dat"))

    def test_png(self):
        self.assertTrue(_run_is_binary(b"\x89PNG\r\n\x1a\n" + b"x" * 100, ".dat"))

    def test_exe(self):
        # NOTE: fixture intentionally differs from the draft spec's
        # ``b"MZ" + b"x" * 100``. A pure-ASCII MZ payload decodes as valid
        # UTF-8 and classifies False (text) under the heuristic, so it
        # cannot be a PASS-pre invariant. Per campaign evidence (W3-B2,
        # R2 test review) the EXE invariant fixture is PE-like with NUL
        # bytes: True via the NUL branch, pre- and post-fix.
        self.assertTrue(
            _run_is_binary(b"MZ\x90\x00\x03\x00\x00\x00" + b"\x00" * 100, ".dat")
        )

    def test_empty(self):
        self.assertFalse(_run_is_binary(b"", ".dat"))

    def test_utf16_bom_default(self):
        data = b"\xff\xfe" + "hi".encode("utf-16-le")
        self.assertTrue(_run_is_binary(data, ".dat", allow_utf16_text=False))

    def test_utf16_bom_allowed(self):
        # Requires .txt: the BOM branch checks the suffix against text_exts.
        data = b"\xff\xfe" + "hi".encode("utf-16-le")
        self.assertFalse(_run_is_binary(data, ".txt", allow_utf16_text=True))

    def test_exact_4096_torn(self):
        # 4096 B; 3-byte char missing its last 2 bytes (EOF-short).
        data = b"A" * 4095 + b"\xe2"
        self.assertEqual(len(data), 4096)
        self.assertTrue(_run_is_binary(data, ".dat"))

    def test_4097_torn(self):
        # 4097 B; 3-byte char missing its last byte (EOF-short).
        data = b"A" * 4095 + b"\xe2\x82"
        self.assertEqual(len(data), 4097)
        self.assertTrue(_run_is_binary(data, ".dat"))

    def test_4098_torn(self):
        # 4098 B; 4-byte char missing its last byte (EOF-short).
        data = b"A" * 4095 + b"\xf0\x9f\x98"
        self.assertEqual(len(data), 4098)
        self.assertTrue(_run_is_binary(data, ".dat"))

    def test_4099_torn_invalid(self):
        # 4099 B; 4-byte lead path with a bad continuation byte at 4096
        # (W3-B2 C1). Must stay True: this is invalid UTF-8, not a torn tail.
        data = b"A" * 4095 + b"\xf0\x9f\x98" + b"B"
        self.assertEqual(len(data), 4099)
        self.assertTrue(_run_is_binary(data, ".dat"))

    def test_attack2(self):
        # Adversarial invariant carrier: a mid-sample invalid sequence must
        # NOT flip to False under Sprint 1 (bad-continuation closure, R1b).
        data = b"A" * 4095 + b"\xe2" + b"\x28" + b"C" * 5000
        self.assertTrue(_run_is_binary(data, ".dat"))


class BinaryHeuristicShimTests(unittest.TestCase):
    """Shim: FAIL pre-S3, PASS post-S3.

    S3 replaces the whole-file ``Path.read_bytes()`` in
    ``code_tools._is_binary_file`` with a bounded stream read (delegating to
    ``common.is_binary_file``), so ``read_bytes`` must never be called.
    """

    def test_shim_no_read_bytes(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "shim.dat"
            path.write_bytes(b"hello world")
            with mock.patch.object(
                Path, "read_bytes", side_effect=Exception("should not be called")
            ) as read_bytes_mock:
                code_tools._is_binary_file(path)
        read_bytes_mock.assert_not_called()

    def test_shim_keyword_passthrough(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "shim.txt"
            path.write_bytes(b"\xff\xfe" + "hi".encode("utf-16-le"))
            result = code_tools._is_binary_file(
                path, sample_size=32, allow_utf16_text=True
            )
            self.assertIsInstance(result, bool)


if __name__ == "__main__":
    unittest.main()
