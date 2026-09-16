"""A31 Q3 -- pin the rule-core SOT files by WORDING, never by sha.

The same `examples/CORE_RULE.txt` has two different sha256 values depending on
who read it: `write_fix` hashes the bytes on disk (CRLF), `manage_agent` hashes
an LF-normalized read, and the two sizes differ by exactly the file's line
count. A sha assertion here would pin the reader instead of the rule, and would
go red on any harmless line-ending round-trip. So assert the phrases.

Second half of this file closes the A31 root cause: nothing asserted that the
rule file shipped in the repo is the same text `manage_agent(sync_rule)`
propagates to the agent files. `_ma_rule_file_text` reads `examples/` at call
time, so the binding is real and cheap to pin.
"""
import unittest
from pathlib import Path

import code_tools

EXAMPLES = Path(code_tools.__file__).resolve().parent / "examples"

SOT_FILES = ("CORE_RULE.txt", "SLIM_RULE.txt", "SLIM_RULE_INLINE.txt")

# READ ROUTING rule added 2026-09-17: a native grep/glob that finds nothing on a
# path that certainly exists is a cwd bug, not an absence.
CWD_RULE = "is a cwd bug, NOT an absence"

# The MCP-dispatcher sentence must name the mechanism and carry no tool count.
# CORE_RULE/SLIM_RULE write "NOT one named ...", SLIM_RULE_INLINE writes
# "not one named ...", so pin only the fragment that is byte-identical in all
# three files.
MCP_MECHANISM = "one named MCP tool per [A] tool"

# The count that used to sit in that sentence. It rotted three campaigns in a
# row; the fix was to delete the digit, so its return is a regression.
RETIRED_TOOL_COUNT = "252"


def _read(name: str) -> str:
    return (EXAMPLES / name).read_text(encoding="utf-8")


class RuleCoreSotFilesCarryTheCwdRule(unittest.TestCase):
    def test_every_sot_file_exists(self):
        for name in SOT_FILES:
            with self.subTest(name):
                self.assertTrue(
                    (EXAMPLES / name).is_file(), f"{name} missing from {EXAMPLES}"
                )

    def test_cwd_rule_phrase_present_in_every_sot_file(self):
        for name in SOT_FILES:
            with self.subTest(name):
                self.assertIn(CWD_RULE, _read(name))

    def test_mcp_dispatcher_sentence_names_the_mechanism(self):
        for name in SOT_FILES:
            with self.subTest(name):
                self.assertIn(MCP_MECHANISM, _read(name))

    def test_no_stale_tool_count_in_any_sot_file(self):
        for name in SOT_FILES:
            with self.subTest(name):
                self.assertNotIn(
                    RETIRED_TOOL_COUNT,
                    _read(name),
                    f"{name} names a tool count again -- name the mechanism, not a number",
                )


class RuleCoreRuntimeReadsTheCommittedFile(unittest.TestCase):
    """Bind the committed bytes to the runtime accessors that propagate them."""

    def test_core_accessor_returns_the_core_rule_file(self):
        self.assertEqual(code_tools._ma_core_rule_body(), _read("CORE_RULE.txt").strip())

    def test_slim_accessor_returns_the_slim_rule_file(self):
        self.assertEqual(code_tools._ma_slim_rule_body(), _read("SLIM_RULE.txt").strip())

    def test_inline_accessor_returns_the_inline_file(self):
        self.assertEqual(
            code_tools._ma_rule_inline("slim"), _read("SLIM_RULE_INLINE.txt").strip()
        )

    def test_full_inline_is_the_core_rule_whitespace_collapsed(self):
        inline_full = code_tools._ma_rule_inline("full")
        self.assertIn(CWD_RULE, inline_full)
        self.assertIn(MCP_MECHANISM, inline_full)
        self.assertNotIn("\n", inline_full)

    def test_every_propagated_template_carries_the_cwd_rule(self):
        for template in ("slim", "full", "core"):
            with self.subTest(template):
                self.assertIn(CWD_RULE, code_tools._ma_rule_body(template))


if __name__ == "__main__":
    unittest.main()
