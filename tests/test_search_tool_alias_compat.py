import json
import tempfile
import unittest
from pathlib import Path

import code_tools

LEGACY = [
    "find_symbol",
    "regex_search",
    "find_callers",
    "find_definition_and_usage",
    # CAN-050 (2026-07-16): query_index is a FIRST-CLASS tool again — it maps to
    # its real handler (accepts empty query) instead of borrowing search_code,
    # which demanded `query` and produced codo's 6 MISSING_REQUIRED_FIELD errors.
    "query_dom_candidates",
]


class TestSearchToolAliasCompat(unittest.IsolatedAsyncioTestCase):
    def test_legacy_names_in_tool_map(self):
        for name in LEGACY:
            self.assertIn(
                name,
                code_tools.TOOL_MAP,
                msg=f"legacy alias {name!r} missing from code_tools.TOOL_MAP",
            )

    def test_legacy_aliases_map_to_search_code(self):
        base = code_tools.TOOL_MAP["search_code"]
        spec_pairs = set(code_tools.BASE_TOOL_MAP_SPECS)
        for name in LEGACY:
            entry = code_tools.TOOL_MAP[name]
            self.assertTrue(callable(entry), msg=f"TOOL_MAP[{name!r}] is not callable")
            self.assertIn(
                (name, "search_code"),
                spec_pairs,
                msg=f"spec pair ({name!r}, 'search_code') not in BASE_TOOL_MAP_SPECS",
            )
            self.assertIs(
                entry,
                base,
                msg=f"TOOL_MAP[{name!r}] is not the same callable as TOOL_MAP['search_code']",
            )

    async def test_legacy_callables_behaviour_async(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "sample_alias_mod.py").write_text(
                "def some_fn():\n"
                "    return 42\n"
                "\n"
                "class Widget:\n"
                "    def hello(self):\n"
                "        return 'hi'\n",
                encoding="utf-8",
            )
            project_path = str(root)

            raw_regex = await code_tools.TOOL_MAP["regex_search"](
                project_path=project_path, query="def .*"
            )
            parsed_regex = json.loads(raw_regex) if isinstance(raw_regex, str) else raw_regex
            self.assertIsInstance(parsed_regex, dict)
            self.assertTrue(
                "ok" in parsed_regex or "results" in parsed_regex,
                msg=f"regex_search returned unexpected shape: {sorted(parsed_regex.keys())}",
            )

            raw_find = await code_tools.TOOL_MAP["find_symbol"](
                name="some_fn", project_path=project_path
            )
            parsed_find = json.loads(raw_find) if isinstance(raw_find, str) else raw_find
            self.assertIsInstance(parsed_find, dict)
            self.assertTrue(
                "ok" in parsed_find or "results" in parsed_find,
                msg=f"find_symbol returned unexpected shape: {sorted(parsed_find.keys())}",
            )


if __name__ == "__main__":
    unittest.main()