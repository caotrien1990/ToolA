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
import asyncio
import json
import re
import unittest
from pathlib import Path
from unittest import mock

import code_tools

EXAMPLES = Path(code_tools.__file__).resolve().parent / "examples"

SOT_FILES = ("CORE_RULE.txt", "SLIM_RULE.txt", "SLIM_RULE_INLINE.txt")

# READ ROUTING rule added 2026-09-17: a native grep/glob that finds nothing on a
# path that certainly exists is a cwd bug, not an absence.
CWD_RULE = "is a cwd bug, NOT an absence"

# READ ROUTING second cause, added 2026-09-29: a 0-hit repo-wide rg is also
# ripgrep's documented default (dot-dirs and gitignored paths are skipped), and
# search_code indexes code only -- the rule must name the sweeps that see them.
HIDDEN_DIR_RULE = (
    "skips dot-dirs",
    "git grep -n -I",
    "rg -uu",
    "positive-control every 0-hit",
)

# The MCP-dispatcher sentence must name the MECHANISM and carry no tool count.
# A37 measured that [A] exposes TWO MCP surfaces with DIFFERENT shapes: the
# window/desktop host is a thin dispatcher, while orchestrator/server/mcp_stdio.py
# DOES register named per-tool wrappers. The single old fragment ("one named MCP
# tool per [A] tool") is therefore no longer true of both surfaces, and CORE_RULE
# stopped carrying it on purpose. Pin instead the two mechanism literals that ARE
# byte-identical in all three files -- a stricter gate, not a looser one.
MCP_MECHANISM = ("thin dispatcher", "call_tool(name=")

# The count that used to sit in that sentence. It rotted three campaigns in a
# row; the fix was to delete the digit, so its return is a regression.
RETIRED_TOOL_COUNT = "252"

# READ GATE (2026-10-04): a native read of a code file > 200 lines without
# limit<=200 and a symbol-shaped grep over code are DENIED by the local
# PreToolUse read-gate hook, and the deny reason must name the [A] replacement
# so the CA lands on the index order instead of retrying native. Pin the phrase
# that must survive in every SOT file -- by phrase, never sha.
READ_GATE_RULE = "the deny reason names the [A] replacement"


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

    def test_hidden_dir_rule_names_the_sweeps_in_every_sot_file(self):
        for name in SOT_FILES:
            text = _read(name)
            for fragment in HIDDEN_DIR_RULE:
                with self.subTest(f"{name}:{fragment}"):
                    self.assertIn(fragment, text)

    def test_mcp_dispatcher_sentence_names_the_mechanism(self):
        for name in SOT_FILES:
            text = _read(name)
            for fragment in MCP_MECHANISM:
                with self.subTest(f"{name}:{fragment}"):
                    self.assertIn(fragment, text)

    def test_no_stale_tool_count_in_any_sot_file(self):
        for name in SOT_FILES:
            with self.subTest(name):
                self.assertNotIn(
                    RETIRED_TOOL_COUNT,
                    _read(name),
                    f"{name} names a tool count again -- name the mechanism, not a number",
                )

    def test_read_gate_deny_names_the_replacement_in_every_sot_file(self):
        for name in SOT_FILES:
            with self.subTest(name):
                self.assertIn(READ_GATE_RULE, _read(name))


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
        for fragment in MCP_MECHANISM:
            self.assertIn(fragment, inline_full)
        self.assertNotIn("\n", inline_full)

    def test_every_propagated_template_carries_the_cwd_rule(self):
        for template in ("slim", "full", "core"):
            with self.subTest(template):
                self.assertIn(CWD_RULE, code_tools._ma_rule_body(template))

    def test_every_propagated_template_carries_the_hidden_dir_rule(self):
        for template in ("slim", "full", "core"):
            body = code_tools._ma_rule_body(template)
            for fragment in HIDDEN_DIR_RULE:
                with self.subTest(f"{template}:{fragment}"):
                    self.assertIn(fragment, body)


class RuleTemplateHasNoInCodeFallback(unittest.TestCase):
    """2026-09-24: code_tools carried in-code copies of the SLIM bodies that had
    drifted from examples/ (retired agent ids and workers) and would have been
    rendered silently into every agent's rule file the day a template went
    missing. Now a missing or empty template stops sync_rule, naming the file."""

    def test_missing_template_raises_naming_the_path(self):
        with self.assertRaises(FileNotFoundError) as cm:
            code_tools._ma_rule_file_text("NO_SUCH_RULE.txt")
        self.assertIn("NO_SUCH_RULE.txt", str(cm.exception))

    def test_blank_templates_fail_sync_rule_loudly(self):
        # Through the layer the CA reads: the manage_agent answer, not the helper.
        real = Path.read_text

        def blank_templates(path, *a, **kw):
            return "  \n" if path.name in SOT_FILES else real(path, *a, **kw)

        with mock.patch.object(code_tools, "_ma_archive_legacy_bak_ma", return_value={}), \
                mock.patch.object(Path, "read_text", blank_templates):
            out = json.loads(asyncio.run(code_tools.manage_agent(
                action="sync_rule", target="all", dry_run=True)))
        self.assertIs(out["ok"], False)
        self.assertIn("rule template missing or empty", out.get("error", ""))
        # Positive control: the same call renders once the templates are real.
        with mock.patch.object(code_tools, "_ma_archive_legacy_bak_ma", return_value={}):
            ok = json.loads(asyncio.run(code_tools.manage_agent(
                action="sync_rule", target="all", dry_run=True)))
        self.assertNotIn("rule template missing", json.dumps(ok))


# P1 (2026-10-02): the first-fetch projection must carry the bootstrap payload.
# Measured root cause: SLIM/INLINE mandated fields=ok,already_fetched,text,sha256,
# which drops tool_purposes + bootstrap_epoch -- with the curated MCP surface that
# purpose map is the only discovery channel for the cbm/ocr index tools (measured:
# 3 cbm calls in 1659 tool calls). Pins are by PHRASE, never sha.
FIRST_FETCH_FIELDS = "ok,already_fetched,text,sha256,tool_purposes,bootstrap_epoch"
FIRST_FETCH_RE = re.compile(r"get_rule_core_text\?fields=([^`\s]+)")
BARE_OLD_PROJ_RE = re.compile(
    re.escape("ok,already_fetched,text,sha256") + r"(?!,tool_purposes)")
DISCOVERY_SENTENCE_PHRASES = (
    "is not DISCOVERY",
    "no route to the cbm/ocr tools",
)


class FirstFetchProjectionCarriesTheBootstrapPayload(unittest.TestCase):
    """Bind the documented first-fetch fields= list to the runtime projection."""

    def test_slim_and_inline_carry_the_new_first_fetch_literal(self):
        for name in ("SLIM_RULE.txt", "SLIM_RULE_INLINE.txt"):
            with self.subTest(name):
                self.assertIn(FIRST_FETCH_FIELDS, _read(name))

    def test_no_bare_old_projection_survives(self):
        """The old 4-key literal may remain ONLY as a prefix of the new one."""
        for name in ("SLIM_RULE.txt", "SLIM_RULE_INLINE.txt"):
            text = _read(name)
            with self.subTest(name):
                self.assertEqual(
                    BARE_OLD_PROJ_RE.findall(text), [],
                    f"{name} still projects a first fetch without tool_purposes",
                )

    def test_documented_projection_survives_the_runtime_filter(self):
        """Doc -> daemon: extract the SLIM fields= list, run a real full fetch,
        project it with the daemon's own _r101_filter, and prove the bootstrap
        payload keys the rule promises actually survive."""
        import orchestrator_server as srv

        m = FIRST_FETCH_RE.search(_read("SLIM_RULE.txt"))
        self.assertIsNotNone(
            m, "SLIM_RULE no longer documents a first-fetch fields= list")
        self.assertEqual(m.group(1), FIRST_FETCH_FIELDS)
        fields = srv._r101_parse_fields(m.group(1))
        agent = "pytest-p1-fields-binding"
        code_tools._RULE_CORE_FETCH_CACHE.pop(
            code_tools._rule_core_agent_key(agent), None)
        payload = json.loads(asyncio.run(
            code_tools.get_rule_core_text(agent_id=agent, force=True)))
        self.assertIs(payload["already_fetched"], False)
        projected = srv._r101_filter(payload, fields)
        self.assertTrue(projected.get("text"), "the projection dropped text")
        purposes = projected.get("tool_purposes")
        self.assertIsInstance(purposes, dict)
        self.assertTrue(purposes, "the projection dropped tool_purposes")
        self.assertIn("bootstrap_epoch", projected)

    def test_discovery_sentence_in_core_and_slim(self):
        for name in ("CORE_RULE.txt", "SLIM_RULE.txt"):
            text = _read(name)
            for phrase in DISCOVERY_SENTENCE_PHRASES:
                with self.subTest(f"{name}:{phrase}"):
                    self.assertIn(phrase, text)


if __name__ == "__main__":
    unittest.main()
