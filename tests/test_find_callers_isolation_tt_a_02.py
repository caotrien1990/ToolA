"""TT-A-02 regression: find_callers (find_symbol include='callers') must return
ONLY executable call sites — not the defining node itself, not full-line
comments, and not JSON/Markdown artifacts.

Why: prior behavior kept the definition list under `results` even when the
caller explicitly asked for include='callers', so find_callers was
indistinguishable from find_symbol plus a callers array. The regex also
matched `# name(...)` and `// name(...)` full-line comments and any
occurrence inside .json/.md/.log files, all of which are documentation or
data, not call edges.
"""
from __future__ import annotations

import asyncio
import json
import tempfile
import unittest
from pathlib import Path

from orchestrator.tools.search import find_symbol


class TestFindCallersIsolation(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        # Defining module
        (self.root / "mod.py").write_text(
            "def target_fn(x):\n"
            "    return x + 1\n",
            encoding="utf-8",
        )
        # Real caller in another file
        (self.root / "use_mod.py").write_text(
            "import mod\n"
            "def caller():\n"
            "    return mod.target_fn(42)\n",
            encoding="utf-8",
        )
        # Comment mentions (must NOT appear as caller)
        (self.root / "comments.py").write_text(
            "# target_fn(0) is referenced in a comment only\n"
            "// target_fn(0) JS-style comment\n"
            "x = 1\n",
            encoding="utf-8",
        )
        # JSON artifact (must NOT appear as caller)
        (self.root / "data.json").write_text(
            '{"example": "target_fn(1)"}\n',
            encoding="utf-8",
        )
        # Markdown doc (must NOT appear as caller)
        (self.root / "README.md").write_text(
            "Call `target_fn(5)` for great justice.\n",
            encoding="utf-8",
        )

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_callers_response_is_callers_only(self) -> None:
        """include='callers' must NOT surface the definition list at top level."""
        raw = asyncio.run(
            find_symbol(name="target_fn", project_path=str(self.root), include="callers")
        )
        data = json.loads(raw)
        self.assertIn("callers", data, f"missing callers key: {data}")
        self.assertNotIn(
            "results", data,
            f"include='callers' must not leak definition 'results' into response: {data}",
        )
        self.assertEqual(data.get("include"), "callers")

    def test_excludes_defining_node_from_callers(self) -> None:
        """The defining file/line itself must not be reported as a caller."""
        raw = asyncio.run(
            find_symbol(name="target_fn", project_path=str(self.root), include="callers")
        )
        data = json.loads(raw)
        callers = data.get("callers", [])
        def_hits = [c for c in callers if c["file"] == "mod.py"]
        self.assertEqual(
            def_hits, [],
            f"defining module mod.py must not appear as a caller: {def_hits}",
        )

    def test_real_caller_is_reported(self) -> None:
        """The genuine call site in use_mod.py must be present."""
        raw = asyncio.run(
            find_symbol(name="target_fn", project_path=str(self.root), include="callers")
        )
        data = json.loads(raw)
        callers = data.get("callers", [])
        files = {c["file"] for c in callers}
        self.assertIn("use_mod.py", files,
                      f"expected use_mod.py among callers, got files={files}")

    def test_excludes_comments_and_non_source_artifacts(self) -> None:
        """Comments-only file, JSON, and Markdown must not yield caller hits."""
        raw = asyncio.run(
            find_symbol(name="target_fn", project_path=str(self.root), include="callers")
        )
        data = json.loads(raw)
        callers = data.get("callers", [])
        files = {c["file"] for c in callers}
        self.assertNotIn("comments.py", files,
                         "full-line comment mentions are not call sites")
        self.assertNotIn("data.json", files,
                         "JSON artifacts are not call sites")
        self.assertNotIn("README.md", files,
                         "Markdown docs are not call sites")

    def test_callers_differ_from_definition(self) -> None:
        """find_callers response must be distinguishable from find_symbol(include='def')."""
        raw_def = asyncio.run(
            find_symbol(name="target_fn", project_path=str(self.root), include="def")
        )
        raw_call = asyncio.run(
            find_symbol(name="target_fn", project_path=str(self.root), include="callers")
        )
        d_def = json.loads(raw_def)
        d_call = json.loads(raw_call)
        self.assertIn("results", d_def)
        self.assertNotIn("results", d_call,
                         "callers response must not duplicate find_symbol's results key")
        self.assertIn("callers", d_call)
        self.assertNotIn("callers", d_def)


if __name__ == "__main__":
    unittest.main()
