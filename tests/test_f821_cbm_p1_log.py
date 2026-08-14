"""Regression tests for F821-2: the CBM graph P1 registration block at
code_tools.py:13809-13811 caught ``Exception as _cbm_p1_exc`` and imported
``logging as _cbm_p1_log``, but the warning call referenced the previous
block's ``_cbm_log`` and ``_cbm_exc`` — both already out of scope by the
time the P1 handler ran — so a real P1 registration failure raised
``NameError`` instead of logging the actual exception.

The invariant: when the P1 registration block raises, the exception handler
must log the P1 exception via the P1 logger and fail open (module import
succeeds), never raise a secondary NameError.
"""

from __future__ import annotations

import re
import unittest
from pathlib import Path


class TestCbmP1ExceptionHandler(unittest.TestCase):
    """Source-level contract for the P1 exception handler."""

    def test_p1_handler_logs_p1_exception_via_p1_logger(self):
        # WHY: pre-fix, the handler referenced ``_cbm_exc`` / ``_cbm_log``
        # from the earlier P0 block (line ~13656), which Python clears when
        # that except-block exits. Reaching for them in the P1 handler is a
        # guaranteed NameError on any P1 registration failure — masking the
        # real error and breaking module import. The fix pins the handler
        # to its own ``_cbm_p1_exc`` / ``_cbm_p1_log``.
        code_tools_path = (
            Path(__file__).resolve().parent.parent / "code_tools.py"
        )
        src = code_tools_path.read_text(encoding="utf-8")

        # Locate the P1 except-block exactly.
        m = re.search(
            r"except Exception as _cbm_p1_exc:\s*\n"
            r"\s*import logging as _cbm_p1_log\s*\n"
            r"\s*(?P<logline>[^\n]+)",
            src,
        )
        self.assertIsNotNone(m, "P1 exception handler not found in code_tools.py")
        logline = m.group("logline")

        # The handler must use the P1 logger AND the P1 exception.
        self.assertIn(
            "_cbm_p1_log.warning", logline,
            f"P1 handler must log via _cbm_p1_log, got: {logline}",
        )
        self.assertIn(
            "_cbm_p1_exc", logline,
            f"P1 handler must reference _cbm_p1_exc, got: {logline}",
        )
        # And must NOT leak the previous block's cleared names.
        self.assertNotRegex(
            logline, r"(?<![_a-zA-Z0-9])_cbm_log(?![_a-zA-Z0-9])",
            f"P1 handler still references stale _cbm_log: {logline}",
        )
        self.assertNotRegex(
            logline, r"(?<![_a-zA-Z0-9])_cbm_exc(?![_a-zA-Z0-9])",
            f"P1 handler still references stale _cbm_exc: {logline}",
        )

    def test_p1_handler_is_fail_open(self):
        # WHY: the P1 registration block is additive — if it fails, the
        # rest of code_tools must still import. The except-block swallows
        # the exception after logging; the regression test pins that the
        # handler does not re-raise.
        code_tools_path = (
            Path(__file__).resolve().parent.parent / "code_tools.py"
        )
        src = code_tools_path.read_text(encoding="utf-8")
        m = re.search(
            r"except Exception as _cbm_p1_exc:\s*\n"
            r"\s*import logging as _cbm_p1_log\s*\n"
            r"(?P<body>(?:\s*[^\n]+\n)+?)"
            r"(?=^\S|\Z)",
            src,
            flags=re.MULTILINE,
        )
        self.assertIsNotNone(m, "P1 exception handler not found")
        body = m.group("body")
        self.assertNotIn("raise", body,
                         f"P1 handler must fail open (no raise), got: {body}")

    def test_module_imports_cleanly(self):
        # WHY: ultimate end-to-end proof — after the fix, importing
        # code_tools must succeed (no NameError at module scope).
        import code_tools  # noqa: F401


if __name__ == "__main__":
    unittest.main()
