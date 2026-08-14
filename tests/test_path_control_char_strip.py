# pyright: reportArgumentType=false
"""P1 fix (2026-08-05): _normalize_body strips C0 control chars (\x00-\x1f)
at path edges for keys in _PATH_KEYS, BEFORE slash normalization.

Why: JSON transport can smuggle \0, \r, \n, \t into path values (e.g.
app-generated paths). Those paths then fail downstream as PATH_NOT_FOUND even
though the visible path text is correct. The fix strips edge control chars
only; interior control chars are kept as-is (never silently joined) and
non-path fields (code/content) are never touched.

Run:  ./venv312/Scripts/python.exe -m pytest tests/test_path_control_char_strip.py -v
"""

import unittest

import orchestrator_server as srv  # noqa: F401 — triggers late registrations


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

    def test_interior_control_char_kept_not_joined(self):
        # Interior control chars must NOT be removed (no silent segment join).
        norm, applied = self._norm({"file_path": "D:\\repo\x00foo.py"})
        self.assertEqual(norm["file_path"], "D:\\repo\x00foo.py")
        self.assertFalse(any("control chars stripped" in a for a in applied))

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

    def test_normalized_paths_audit_trail(self):
        norm, applied = self._norm({"file_path": "\x00D:\\repo\\foo.py\x00"})
        trail = norm.get("_normalized_paths", [])
        self.assertEqual(len(trail), 1)
        self.assertEqual(trail[0]["key"], "file_path")
        self.assertEqual(trail[0]["from"], "\x00D:\\repo\\foo.py\x00")
        self.assertEqual(trail[0]["to"], "D:\\repo\\foo.py")


if __name__ == "__main__":
    unittest.main()
