"""R81: mojibake detection must confirm, and validate must name what failed.

_MOJIBAKE_MARKERS is a substring probe, so any file that DISCUSSES mojibake
matched itself — validate_encoding failed on code_tools.py, the module that
defines the markers, which trains CAs to ignore a real gate. The dispatcher then
reported a bare ok=false, so the audit row carried error_name=NULL.
"""

import asyncio
import json
import shutil
import tempfile
import unittest
from pathlib import Path

import code_tools

CANONICAL = "tiến hành kiểm tra tiếng Việt"
CORRUPTED = CANONICAL.encode("utf-8").decode("latin-1")
ORCHESTRATOR_ROOT = Path(code_tools.__file__).resolve().parent


def _run(coro):
    return json.loads(asyncio.run(coro))


class TestMojibakeConfirmation(unittest.TestCase):
    def setUp(self):
        self.tempdir = Path(tempfile.mkdtemp(prefix="r81_mojibake_"))

    def tearDown(self):
        shutil.rmtree(self.tempdir, ignore_errors=True)

    def test_real_mojibake_is_confirmed(self):
        self.assertTrue(code_tools._mojibake_confirmed(CORRUPTED))

    def test_text_merely_mentioning_markers_is_not_mojibake(self):
        prose = 'markers are ("Ã", "Ä", "áº", "á»") in this detector'
        self.assertTrue(any(m in prose for m in code_tools._MOJIBAKE_MARKERS))
        self.assertFalse(code_tools._mojibake_confirmed(prose))

    def test_clean_vietnamese_is_not_mojibake(self):
        self.assertFalse(code_tools._mojibake_confirmed(CANONICAL))

    def test_ascii_is_not_mojibake(self):
        self.assertFalse(code_tools._mojibake_confirmed("plain ascii text"))
        self.assertFalse(code_tools._mojibake_confirmed(""))

    def test_encoding_check_separates_markers_from_verdict(self):
        prose_check = code_tools._encoding_check_from_text('marker "Ã" mentioned here')
        self.assertTrue(prose_check["mojibake_markers_present"])
        self.assertFalse(prose_check["mojibake_risk"])
        real_check = code_tools._encoding_check_from_text(CORRUPTED)
        self.assertTrue(real_check["mojibake_markers_present"])
        self.assertTrue(real_check["mojibake_risk"])

    def test_validate_encoding_passes_on_the_detector_module_itself(self):
        result = _run(code_tools.validate_encoding(
            "code_tools.py", project_path=str(ORCHESTRATOR_ROOT)))
        self.assertTrue(result["ok"], msg=result.get("suspicious_sequences"))
        self.assertTrue(result["mojibake_markers_present"])
        self.assertFalse(result["mojibake_risk"])

    def test_validate_encoding_still_rejects_real_mojibake(self):
        bad = self.tempdir / "bad.md"
        bad.write_text(CORRUPTED, encoding="utf-8")
        result = _run(code_tools.validate_encoding(str(bad)))
        self.assertFalse(result["ok"])
        self.assertTrue(result["mojibake_confirmed"])

    def test_validate_encoding_accepts_clean_vietnamese(self):
        good = self.tempdir / "good.md"
        good.write_text(CANONICAL, encoding="utf-8")
        self.assertTrue(_run(code_tools.validate_encoding(str(good)))["ok"])

    def test_r54_council_detector_still_flags_mojibake(self):
        # The council transport probe is deliberately more eager (it guards a
        # send, not a write) and must keep flagging on markers alone.
        self.assertTrue(code_tools._r54_detect_mojibake(CORRUPTED)[0])


class TestValidateFailureAttribution(unittest.TestCase):
    def setUp(self):
        self.tempdir = Path(tempfile.mkdtemp(prefix="r81_validate_"))

    def tearDown(self):
        shutil.rmtree(self.tempdir, ignore_errors=True)

    def test_failure_names_the_offending_subcheck(self):
        bad = self.tempdir / "bad.md"
        bad.write_text(CORRUPTED, encoding="utf-8")
        result = _run(code_tools.validate(str(bad), checks=["encoding"]))
        self.assertFalse(result["ok"])
        self.assertEqual(result["failed_checks"], ["encoding"])
        self.assertEqual(result["error_name"], "VALIDATION_FAILED")
        self.assertEqual(result["ca_next_action"], "inspect_encoding_check_result")

    def test_success_carries_no_error_fields(self):
        good = self.tempdir / "good.py"
        good.write_text("def f():\n    return 1\n", encoding="utf-8")
        result = _run(code_tools.validate(str(good), checks=["file", "encoding"]))
        self.assertTrue(result["ok"], msg=result)
        self.assertNotIn("failed_checks", result)
        self.assertNotIn("error_name", result)

    def test_syntax_error_is_attributed_to_the_file_check(self):
        broken = self.tempdir / "broken.py"
        broken.write_text("def f(:\n", encoding="utf-8")
        result = _run(code_tools.validate(str(broken), checks=["file"]))
        self.assertFalse(result["ok"])
        self.assertEqual(result["failed_checks"], ["file"])
        self.assertTrue(result["error"], "error must describe the failure, not be empty")


if __name__ == "__main__":
    unittest.main()
