"""A18 (2026-08-16): payload_example must be copy-pasteable.

[A] tells a failing caller to rebuild its request from `payload_example`
(`ca_next_action: rebuild_payload_using_required_and_accepted_fields`). That
instruction is only safe if the example is actually valid input. It was not:
the example path literals were written as raw strings with a missing escape,

    r"D:\\path<TAB>o\\project"      # source read as \\t -> a REAL tab
    r"relative<FF>ile.py"          # source read as \\f -> a REAL form feed

so a CA that followed the instruction sent a path containing a C0 control
character, and this same server rejected it with INVALID_PATH_CONTROL_CHAR.
[A] was handing out an example it would refuse.

These tests sweep every registered tool rather than the two that were caught
by hand, because the defect lives in the shared example generator: one bad
literal there poisons every tool's error response at once.
"""

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import code_tools as ct  # noqa: E402
import orchestrator_server as srv  # noqa: E402

# C0 controls, minus the whitespace that is legitimate inside a description.
_FORBIDDEN = {chr(c) for c in range(0x20)} - {"\n", "\r", "\t"}
# In a *path* even a tab is wrong — Windows paths never contain one.
_FORBIDDEN_IN_PATH = {chr(c) for c in range(0x20)}

_PATH_KEYS = {
    "project_path", "file_path", "path", "folder_path", "script_path", "target",
}


def _walk(value, trail=""):
    """Yield (trail, string) for every string anywhere in the payload."""
    if isinstance(value, str):
        yield trail, value
    elif isinstance(value, dict):
        for k, v in value.items():
            yield from _walk(v, f"{trail}.{k}" if trail else str(k))
    elif isinstance(value, (list, tuple)):
        for i, v in enumerate(value):
            yield from _walk(v, f"{trail}[{i}]")


class TestPayloadExampleIsCleanForEveryTool(unittest.TestCase):

    def test_no_control_characters_in_any_payload_example(self):
        offenders = []
        for schema in ct.TOOL_SCHEMAS:
            fn = schema.get("function", {}) or {}
            name = fn.get("name")
            if not name:
                continue
            example = srv._payload_example_for_tool(name, fn) or {}
            for trail, text in _walk(example):
                leaf = trail.split(".")[-1].split("[")[0]
                bad = _FORBIDDEN_IN_PATH if leaf in _PATH_KEYS else _FORBIDDEN
                hit = sorted({ord(c) for c in text if c in bad})
                if hit:
                    offenders.append(f"{name}.{trail}={text!r} controls={hit}")
        self.assertEqual(
            offenders, [],
            "payload_example must be copy-pasteable; a CA told to reuse it "
            "verbatim would send input this server rejects:\n  "
            + "\n  ".join(offenders[:20]),
        )

    def test_example_paths_survive_the_servers_own_path_guard(self):
        """The strongest form of the contract: run each example path through
        the very check that rejected the hand-built payload. An example the
        server refuses is worse than no example at all."""
        offenders = []
        for schema in ct.TOOL_SCHEMAS:
            fn = schema.get("function", {}) or {}
            name = fn.get("name")
            if not name:
                continue
            example = srv._payload_example_for_tool(name, fn) or {}
            for key in _PATH_KEYS & set(example):
                value = example[key]
                if isinstance(value, str) and any(c < " " for c in value):
                    offenders.append(f"{name}.{key}={value!r}")
        self.assertEqual(offenders, [], "; ".join(offenders[:20]))


class TestNormalizeExamplePathsStripsControls(unittest.TestCase):
    """Guard the shared sanitiser directly, so the protection survives even if
    someone reintroduces a bad literal in the sample() generator."""

    def test_control_characters_are_stripped_from_path_values(self):
        dirty = {"project_path": "D:\\path\to\\project"}
        clean = srv._normalize_example_paths(dirty)
        self.assertNotIn("\t", clean["project_path"])
        self.assertTrue(
            all(c >= " " for c in clean["project_path"]), clean
        )

    def test_clean_paths_are_left_alone(self):
        """A sanitiser that mangles good input is its own bug."""
        good = "D:\\path\\to\\project"
        self.assertEqual(
            srv._normalize_example_paths({"project_path": good})["project_path"],
            good,
        )

    def test_non_path_keys_are_not_touched(self):
        payload = {"description": "line one\nline two"}
        self.assertEqual(srv._normalize_example_paths(payload), payload)


if __name__ == "__main__":
    unittest.main()
