# pyright: reportArgumentType=false
"""P1 fix (2026-08-05): _normalize_body strips C0 control chars (\x00-\x1f)
at path edges for keys in _PATH_KEYS, BEFORE slash normalization.

Why: JSON transport can smuggle \0, \r, \n, \t into path values (e.g.
app-generated paths). Those paths then fail downstream as PATH_NOT_FOUND even
though the visible path text is correct. The fix strips edge control chars
only, and non-path fields (code/content) are never touched.

A46 (2026-08-16): two tests here asserted a policy that S5 SUPERSEDED, and were
red against the shipped code. Rewritten to pin the current contract, not
deleted -- the behaviours they guard still need guarding:

  1. INTERIOR control chars. P1 said "kept as-is, never silently joined". S5
     went further and REJECTS them (`reject_c0_control_characters`, raising
     before any pathlib/OS call). Rejecting strictly implies P1's "never
     silently joined" -- an embedded NUL can truncate a path at the Win32
     boundary, so a path that survives normalization looking clean is the
     dangerous outcome. The test now pins rejection.
  2. AUDIT TRAIL SHAPE. This file expected {"key", "from", "to"} -- i.e. the
     raw C0 echoed back inside "from". The edge-strip branch deliberately emits
     {"key", "stripped_control_codes"} with codepoint NAMES instead, because
     path_runtime's contract is to report control chars "without reflecting the
     raw character in errors or logs" (see
     test_session_audit_summary_does_not_reflect_raw_c0, which stores
     "<invalid path 0x0000>"). The old shape was the bug; the test now pins the
     redacted shape. The {"from", "to"} shape is still correct for the
     slash-normalization branch, which carries no control chars -- covered by
     test_strip_runs_before_slash_normalization.

Authority for both: tests/test_path_control_char_rejection.py, 6/6 green
against this same code, which asserts the trail shape at its line 65 and the
400 INVALID_PATH_CONTROL_CHAR dispatch rejection. Two suites contradicted each
other; the green one is the newer intent and the safer one.

Run:  ./venv312/Scripts/python.exe -m pytest tests/test_path_control_char_strip.py -v
"""

import unittest

import orchestrator_server as srv  # noqa: F401 — triggers late registrations
import path_runtime


class TestPathControlCharStrip(unittest.TestCase):
    """Edge control chars on _PATH_KEYS values are stripped in _normalize_body."""

    def _norm(self, body):
        norm, applied = srv._normalize_body("read_file_range", dict(body))
        return norm, applied

    def test_nul_char_stripped_at_edges(self):
        norm, applied = self._norm({"file_path": "\x00D:\\repo\\foo.py\x00"})
        self.assertEqual(norm["file_path"], "D:\\repo\\foo.py")
        self.assertTrue(any("control chars stripped" in a for a in applied))

    def test_cr_lf_stripped_at_edges(self):
        norm, applied = self._norm({"file_path": "\r\nD:\\repo\\foo.py\r\n"})
        self.assertEqual(norm["file_path"], "D:\\repo\\foo.py")

    def test_tab_stripped_at_edges(self):
        norm, applied = self._norm({"project_path": "\tD:\\repo\t"})
        self.assertEqual(norm["project_path"], "D:\\repo")

    def test_mixed_control_chars_stripped(self):
        norm, applied = self._norm({"file_path": "\x00\t\r\nD:\\repo\\a.py\n\r\t\x00"})
        self.assertEqual(norm["file_path"], "D:\\repo\\a.py")

    def test_all_path_keys_covered(self):
        # Use an alias-free tool name: read_file_range aliases path->file_path
        # BEFORE the path-key loop (correct CAN-009 behavior), which would hide
        # the "path" key from this coverage check.
        for key in srv._PATH_KEYS:
            norm, applied = srv._normalize_body("nonexistent_tool_xyz", {key: "\nD:\\repo\n"})
            self.assertEqual(norm[key], "D:\\repo", f"{key} not stripped")

    def test_clean_path_unchanged(self):
        clean = "D:\\repo\\foo.py"
        norm, applied = self._norm({"file_path": clean})
        self.assertEqual(norm["file_path"], clean)
        self.assertFalse(any("control chars stripped" in a for a in applied))
        self.assertNotIn("_normalized_paths", norm)

    def test_clean_forward_slash_path_unchanged(self):
        clean = "D:/repo/foo.py"
        norm, applied = self._norm({"file_path": clean})
        self.assertEqual(norm["file_path"], clean)
        self.assertFalse(any("control chars stripped" in a for a in applied))

    def test_interior_control_char_is_rejected_not_silently_joined(self):
        """An interior C0 must never pass normalization looking clean.

        P1 required "kept, not joined"; S5 rejects outright. Rejection is the
        stronger guarantee -- a surviving embedded NUL can truncate the path at
        the Win32 boundary, so the silently-clean path is the dangerous one.
        """
        with self.assertRaises(path_runtime.PathControlCharacterError) as caught:
            self._norm({"file_path": "D:\\repo\x00foo.py"})
        self.assertEqual(caught.exception.field, "file_path")
        self.assertEqual(caught.exception.codepoint, 0)

    def test_interior_control_char_is_rejected_even_after_edge_strip(self):
        """Edge-stripping must not shadow an interior control char.

        Ordering trap: the edge strip runs FIRST and reports success, so if
        rejection were skipped once anything was stripped, this path would sail
        through carrying an interior NUL.
        """
        with self.assertRaises(path_runtime.PathControlCharacterError):
            self._norm({"file_path": "\r\nD:\\repo\x00foo.py\r\n"})

    def test_every_interior_c0_is_rejected_not_just_nul(self):
        """The guard is the whole C0 range, not the one character that is easy
        to think of. Caught by mutation: narrowing the check to `== 0x00` left
        the NUL-only tests green while \x01-\x1f sailed through.
        """
        for code in (0x01, 0x07, 0x0A, 0x0D, 0x1B, 0x1F):
            with self.subTest(code=code):
                with self.assertRaises(path_runtime.PathControlCharacterError) as caught:
                    self._norm({"file_path": f"D:\\repo{chr(code)}foo.py"})
                self.assertEqual(caught.exception.codepoint, code)

    def test_the_boundary_character_space_is_not_a_control_char(self):
        """0x20 is the first legal codepoint -- paths here really do contain
        spaces ("D:\\0. TRIEN\\..."), so an off-by-one on the bound would break
        every real path on this machine."""
        clean = "D:\\0. TRIEN\\repo\\foo.py"
        norm, applied = self._norm({"file_path": clean})
        self.assertEqual(norm["file_path"], clean)

    def test_rejection_does_not_echo_the_raw_control_character(self):
        """The error names the codepoint; it must not carry the raw byte."""
        with self.assertRaises(path_runtime.PathControlCharacterError) as caught:
            self._norm({"file_path": "D:\\repo\x00foo.py"})
        self.assertIn("U+0000", str(caught.exception))
        self.assertNotIn("\x00", str(caught.exception))

    def test_non_path_fields_never_stripped(self):
        # Content/code fields keep their control chars untouched.
        body = {"file_path": "D:\\repo\\a.py", "content": "line1\nline2\t\x00end"}
        norm, applied = self._norm(body)
        self.assertEqual(norm["content"], "line1\nline2\t\x00end")

    def test_non_string_path_value_no_crash(self):
        norm, applied = self._norm({"file_path": 123, "folder_path": None})
        self.assertEqual(norm["file_path"], 123)
        self.assertIsNone(norm["folder_path"])

    def test_strip_runs_before_slash_normalization(self):
        # "\n//server/share\n" -> edge strip first, then UNC slash normalize.
        # Alias-free tool so "path" is not renamed to "file_path" first.
        norm, applied = srv._normalize_body("nonexistent_tool_xyz", {"path": "\n//server/share\n"})
        self.assertEqual(norm["path"], "\\\\server\\share")
        kinds = [a for a in applied]
        self.assertTrue(any("control chars stripped" in a for a in kinds))
        self.assertTrue(any("forward-slash normalized" in a for a in kinds))

    def test_normalized_paths_audit_trail_names_codes_without_echoing_them(self):
        """The edge-strip trail reports WHICH controls were removed, by name.

        Not {"from","to"}: that shape would round-trip the raw C0 back to the
        caller and into any log that renders the trail, which is exactly what
        path_runtime's contract forbids.
        """
        norm, applied = self._norm({"file_path": "\x00D:\\repo\\foo.py\x00"})
        trail = norm.get("_normalized_paths", [])
        self.assertEqual(len(trail), 1)
        self.assertEqual(trail[0]["key"], "file_path")
        self.assertEqual(trail[0]["stripped_control_codes"], ["U+0000"])
        self.assertNotIn("from", trail[0])
        self.assertNotIn("\x00", repr(trail[0]))

    def test_audit_trail_reports_each_distinct_code_once_sorted(self):
        norm, applied = self._norm({"file_path": "\x00\t\r\nD:\\repo\\a.py\n\r\t\x00"})
        trail = norm["_normalized_paths"]
        self.assertEqual(
            trail[0]["stripped_control_codes"],
            ["U+0000", "U+0009", "U+000A", "U+000D"],
        )


if __name__ == "__main__":
    unittest.main()
