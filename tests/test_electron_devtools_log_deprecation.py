"""D-07 regression test: electron_devtools_log deprecation metadata must be
coherent — see_also values are short action hints (not the deprecation banner),
the runtime deprecation dict must point to the live replacement
(`list_cdp_targets`), and the deprecated-since revision must be consistent
between tools_v15.py, code_tools.py, and the tools manifest.

Why: pre-fix, the schema see_also value for `list_cdp_targets` carried the
deprecation banner text ("DEPRECATED R41 — use list_cdp_targets instead")
instead of an action hint, and the runtime deprecation metadata in
tools_v15.py said "DEPRECATED V15 R15" while code_tools.py / the manifest
said R41. The migration guidance also did not name list_cdp_targets as the
drop-in enumeration replacement.
"""

from __future__ import annotations

import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TOOLS_V15 = ROOT / "tools_v15.py"
CODE_TOOLS = ROOT / "code_tools.py"
MANIFEST = ROOT / ".council" / "tools_manifest.md"


class ElectronDevtoolsLogDeprecationTest(unittest.TestCase):
    def test_see_also_values_are_action_hints_not_banner(self):
        """code_tools.py: see_also values for electron_devtools_log must be
        short action-oriented hints and must NOT contain the deprecation
        banner (which lives in the schema description + `deprecated: true`
        flag)."""
        src = CODE_TOOLS.read_text(encoding="utf-8")
        # Extract the see_also block for electron_devtools_log.
        match = re.search(
            r'"electron_devtools_log":\s*\{(?P<body>.*?)\},',
            src,
            flags=re.DOTALL,
        )
        self.assertIsNotNone(match, "electron_devtools_log see_also block missing")
        body = match.group("body")
        self.assertNotIn(
            "DO NOT USE",
            body,
            "see_also values must not carry the deprecation banner; "
            "deprecation belongs in schema description + deprecated flag",
        )
        self.assertNotRegex(
            body,
            r"DEPRECATED\s+R\d+\s+—\s+use\s+\w+\s+instead",
            "see_also values must not be the 'DEPRECATED Rx — use X instead' banner",
        )
        # Must still point to list_cdp_targets as the live replacement.
        self.assertIn("list_cdp_targets", body)

    def test_runtime_deprecation_metadata_coherent(self):
        """tools_v15.py electron_devtools_log runtime deprecation dict must:
        - name list_cdp_targets as the replacement for enumeration,
        - use the same revision as code_tools.py (R41, not R15),
        - include a migration hint that mentions list_cdp_targets."""
        src = TOOLS_V15.read_text(encoding="utf-8")
        fn_match = re.search(
            r"async def electron_devtools_log\(.*?(?=^async def |\Z)",
            src,
            flags=re.DOTALL | re.MULTILINE,
        )
        self.assertIsNotNone(fn_match, "electron_devtools_log handler not found")
        body = fn_match.group(0)
        self.assertIn(
            '"deprecated_since": "V15 R41"',
            body,
            "deprecation revision must be R41 to match code_tools.py + manifest",
        )
        self.assertIn("list_cdp_targets", body)
        # The replacement field must lead with list_cdp_targets (live successor),
        # not cdp_attach_target (which is a helper, not a replacement).
        repl_match = re.search(r'"replacement":\s*"([^"]+)"', body)
        self.assertIsNotNone(repl_match)
        self.assertTrue(
            repl_match.group(1).startswith("list_cdp_targets"),
            f"replacement must name list_cdp_targets first, got: {repl_match.group(1)!r}",
        )

    def test_revision_consistent_across_files(self):
        """All three sources of truth must agree on the deprecation revision."""
        tools_src = TOOLS_V15.read_text(encoding="utf-8")
        code_src = CODE_TOOLS.read_text(encoding="utf-8")
        manifest_src = MANIFEST.read_text(encoding="utf-8")

        # code_tools.py marks electron_devtools_log with R41 in _DEPRECATED_TOOLS comment
        # and see_also. tools_v15.py must not contradict with an older revision.
        self.assertNotIn(
            '"deprecated_since": "V15 R15"',
            tools_src,
            "stale 'V15 R15' revision in tools_v15.py deprecation dict",
        )
        # Manifest (auto-generated) should still describe electron_devtools_log
        # as deprecated and pointing at list_cdp_targets.
        self.assertIn("electron_devtools_log", manifest_src)
        self.assertRegex(
            manifest_src,
            r"electron_devtools_log[^\n]*DEPRECATED",
            "manifest entry for electron_devtools_log must mark it deprecated",
        )


if __name__ == "__main__":
    unittest.main()
