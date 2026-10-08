"""P9/P20/P21/P22 -- silent-ok defects now fail loud (audit 2026-10-07).

Four read tools answered ok:true on bad input instead of naming the failure:

- P20 validate: an unknown check name was silently skipped -- zero sub-checks
  ran, yet the response said ok:true. Now answers UNKNOWN_CHECK and names the
  accepted checks.
- P9 parse_log_section: inner extract_log_section's SECTION_TITLE_NOT_FOUND
  (ok:false) was converted into ok:true with 0 entries. Now the inner error
  object is propagated verbatim.
- P21 verify_active_electron_source: evaluate_js's PAGE_ID_INVALID was
  swallowed; the wrapper answered ok:true, document_url:"" and told the CA to
  proceed. Now the inner error is propagated verbatim.
- P22 cache_delta: a non-existent project_path fell through _load_index -> {}
  and answered ok:true with total_changed:0. Now answers INVALID_PROJECT_PATH
  up front, same guard shape as get_cache_status.

Run: ./venv312/Scripts/python.exe -m pytest tests/test_p9_p20_p21_p22_fail_loud.py -v
"""

from __future__ import annotations

import json
import unittest
from unittest.mock import patch

import code_tools as ct
import tools_v15


class ValidateRejectsUnknownCheckNames(unittest.IsolatedAsyncioTestCase):
    """P20: validate must not run zero checks and still say ok."""

    async def test_unknown_check_name_returns_unknown_check(self) -> None:
        out = json.loads(await ct.validate(
            file_path="irrelevant.py",
            project_path="irrelevant",
            checks=["bogus_xyz_check"],
        ))
        self.assertIs(out.get("ok"), False, out)
        self.assertEqual(out.get("error_name"), "UNKNOWN_CHECK", out)
        self.assertIn("bogus_xyz_check", out.get("error", ""), out)
        self.assertIn("file", out.get("accepted_checks", []), out)
        self.assertIn("symbols", out.get("accepted_checks", []), out)
        self.assertIn("encoding", out.get("accepted_checks", []), out)

    async def test_mixed_known_and_unknown_still_fails(self) -> None:
        out = json.loads(await ct.validate(
            file_path="irrelevant.py",
            project_path="irrelevant",
            checks=["file", "bogus_xyz_check"],
        ))
        self.assertIs(out.get("ok"), False, out)
        self.assertEqual(out.get("error_name"), "UNKNOWN_CHECK", out)

    async def test_known_check_names_still_dispatch(self) -> None:
        """The guard must not break the normal path: a known name dispatches."""

        async def _fake_validate_file(*args, **kwargs):
            return json.dumps({"ok": True, "check": "file"})

        with patch.object(ct, "validate_file", new=_fake_validate_file):
            out = json.loads(await ct.validate(
                file_path="x.py", project_path="y", checks=["file"]))
        self.assertIs(out.get("ok"), True, out)
        self.assertEqual(out.get("checks"), ["file"], out)


class ParseLogSectionPropagatesInnerError(unittest.IsolatedAsyncioTestCase):
    """P9: SECTION_TITLE_NOT_FOUND must surface, not become ok:true/0 entries."""

    async def test_inner_error_propagated_verbatim(self) -> None:
        inner = {"ok": False, "error_name": "SECTION_TITLE_NOT_FOUND",
                 "error": "section 'no_such_section' not found on page p1",
                 "page_id": "p1"}

        async def _fake_extract_log_section(**kwargs):
            return json.dumps(inner)

        with patch.object(tools_v15, "extract_log_section",
                          new=_fake_extract_log_section):
            raw = await ct.parse_log_section(page_id="p1",
                                             section_title="no_such_section")
        out = json.loads(raw)
        self.assertEqual(out, inner,
                         "the inner error must be propagated verbatim, not rewrapped")
        self.assertIs(out.get("ok"), False, out)


class VerifyElectronPropagatesPageIdInvalid(unittest.IsolatedAsyncioTestCase):
    """P21: PAGE_ID_INVALID must surface, not become ok:true + next_action proceed."""

    async def test_inner_error_propagated_verbatim(self) -> None:
        inner = {"ok": False, "error_name": "PAGE_ID_INVALID",
                 "error": "no such CDP target: bad_page"}

        async def _fake_evaluate_js(**kwargs):
            return json.dumps(inner)

        with patch.object(tools_v15, "evaluate_js", new=_fake_evaluate_js):
            raw = await ct.verify_active_electron_source(page_id="bad_page")
        out = json.loads(raw)
        self.assertEqual(out, inner,
                         "the inner error must be propagated verbatim, not rewrapped")
        self.assertIs(out.get("ok"), False, out)
        self.assertNotIn("proceed", json.dumps(out),
                         "a failed page lookup must never advise the CA to proceed")


class CacheDeltaRejectsBadProjectPath(unittest.IsolatedAsyncioTestCase):
    """P22: a dead project_path must fail loud, not report ok:true/0 changes."""

    async def test_nonexistent_path_returns_invalid_project_path(self) -> None:
        out = json.loads(await ct.cache_delta(
            project_path="D:/definitely/not/a/real/dir_xyz_123"))
        self.assertIs(out.get("ok"), False, out)
        self.assertEqual(out.get("error_name"), "INVALID_PROJECT_PATH", out)


if __name__ == "__main__":
    unittest.main()
