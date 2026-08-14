"""B-02 regression: find_callers must isolate genuine call sites even when the
definition-only graph adapter is enabled.

Why: with GRAPH_ENABLED=true, find_symbol's early return on
``find_symbol_as_search_response`` fired for every ``include`` value. The
adapter only carries the definition ``results`` array — no callers list, no
``include='callers'`` tag — so find_callers returned the same payload as
find_symbol(include='def') plus any noise the graph had indexed (registry
strings, documentation tables, archived copies under _orchestrator_backups).
The fix gates the graph adapter on ``include == 'def'`` so callers/usage/all
fall through to the live scanner, which has the TT-A-02 isolation logic
(pop results, attach callers[], drop non-source artifacts, filter comment
lines, skip should_skip_dir trees including _orchestrator_backups /
.orchestrator_cache / __pycache__ / node_modules).

This test exercises the exact regression: a definition, a real caller, a
registry-style string entry (dict literal containing the symbol name as a
key, no call parens), and an archived copy under _orchestrator_backups. The
archived copy matches the definition regex and the caller regex but must be
excluded by the should_skip_dir traversal guard. The registry-string entry
must NOT match the caller regex (no ``name(``), and the definition is
popped from the callers response anyway.
"""
from __future__ import annotations

import asyncio
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from orchestrator.tools import search as search_module
from tools_v15 import find_callers


class TestFindCallersLiveIsolationB02(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        # Definition.
        (self.root / "mod.py").write_text(
            "def target_fn(x):\n"
            "    return x + 1\n",
            encoding="utf-8",
        )
        # Real caller.
        (self.root / "use_mod.py").write_text(
            "import mod\n"
            "def caller():\n"
            "    return mod.target_fn(42)\n",
            encoding="utf-8",
        )
        # Registry-string entry: dict literal mapping the symbol name to a
        # description. Contains no `target_fn(` call, so the caller regex
        # must not match. Guards against regressions where find_callers
        # leaks registry entries via the definition payload.
        (self.root / "registry.py").write_text(
            "TOOL_ALIASES = {\n"
            "    \"target_fn\": \"Alias for some_module(mode='fn')\",\n"
            "}\n",
            encoding="utf-8",
        )
        # Archived copy under _orchestrator_backups: an identical duplicate of
        # the definition + a call site. should_skip_dir must exclude this
        # whole subtree from both the definition walk and the caller walk.
        backup = self.root / "_orchestrator_backups" / "snapshot_v1"
        backup.mkdir(parents=True)
        (backup / "mod.py").write_text(
            "def target_fn(x):\n"
            "    return x + 1\n",
            encoding="utf-8",
        )
        (backup / "use_mod.py").write_text(
            "import mod\n"
            "def archived_caller():\n"
            "    return mod.target_fn(0)\n",
            encoding="utf-8",
        )

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def _force_graph_enabled_with_definition_only_adapter(self):
        """Simulate the B-02 live topology: graph enabled AND the adapter
        returns a non-None definition payload. Without the fix, find_symbol
        would return that payload verbatim for include='callers'.
        """
        adapter_response = json.dumps({
            "results": [{
                "file": "mod.py", "line": 1,
                "match": "function target_fn -> mod.target_fn",
                "range_id": "mod.py:1",
            }],
            "count": 1,
            "source": "graph",
        })
        return mock.patch.multiple(
            search_module,
            # Pretend the graph feature flag and per-tool opt-in are on.
            # The adapter is imported lazily inside find_symbol, so patch
            # the symbols it looks up on the source modules instead.
        )

    def test_find_callers_with_graph_adapter_enabled_returns_isolated_callers(self) -> None:
        """Live B-02 path: graph enabled, adapter would otherwise fire."""
        adapter_response = json.dumps({
            "results": [{
                "file": "mod.py", "line": 1,
                "match": "function target_fn -> mod.target_fn",
                "range_id": "mod.py:1",
            }],
            "count": 1,
            "source": "graph",
        })
        # Patch the graph seam inside find_symbol so the definition-only
        # adapter is artificially available and returns a payload. With the
        # B-02 fix this payload is IGNORED for include='callers'; without
        # the fix it would be returned verbatim (no callers key, results
        # present, include missing).
        with mock.patch(
            "orchestrator.graph.config.use_graph_for", return_value=True
        ), mock.patch(
            "orchestrator.graph.query.find_symbol_as_search_response",
            return_value=adapter_response,
        ):
            raw = asyncio.run(find_callers(
                symbol="target_fn", project_path=str(self.root),
            ))
        data = json.loads(raw)
        self.assertIn("callers", data,
                      f"callers key missing — graph adapter leaked: {data}")
        self.assertNotIn("results", data,
                         f"definition results leaked into callers response: {data}")
        self.assertEqual(data.get("include"), "callers")

    def test_find_callers_excludes_backup_tree_and_registry_strings(self) -> None:
        """The archived copy under _orchestrator_backups and the dict-string
        registry entry must not appear among callers.
        """
        with mock.patch(
            "orchestrator.graph.config.use_graph_for", return_value=False
        ):
            raw = asyncio.run(find_callers(
                symbol="target_fn", project_path=str(self.root),
            ))
        data = json.loads(raw)
        callers = data.get("callers", [])
        files = {c["file"] for c in callers}
        # Archived copies under _orchestrator_backups must be excluded
        # (should_skip_dir guard).
        archived = [f for f in files if "_orchestrator_backups" in f]
        self.assertEqual(archived, [],
                         f"archived copy leaked into callers: {archived}")
        # Registry-string entry contains no `target_fn(` call, so it must
        # not match the caller regex. (Definition results would have
        # matched it, but those are popped for include='callers'.)
        self.assertNotIn("registry.py", files,
                         "registry-string entry is not a call site")
        # Real caller IS reported.
        self.assertIn("use_mod.py", files,
                      f"genuine caller missing: files={files}")

    def test_find_callers_differs_from_find_symbol_with_graph_on(self) -> None:
        """Material-difference check: with graph enabled, find_callers output
        must differ from find_symbol(include='def') output. Before the fix
        they were identical (both returned the graph adapter payload).
        """
        adapter_response = json.dumps({
            "results": [{
                "file": "mod.py", "line": 1,
                "match": "function target_fn -> mod.target_fn",
                "range_id": "mod.py:1",
            }],
            "count": 1,
            "source": "graph",
        })
        with mock.patch(
            "orchestrator.graph.config.use_graph_for", return_value=True
        ), mock.patch(
            "orchestrator.graph.query.find_symbol_as_search_response",
            return_value=adapter_response,
        ):
            raw_def = asyncio.run(search_module.find_symbol(
                name="target_fn", project_path=str(self.root), include="def",
            ))
            raw_call = asyncio.run(search_module.find_symbol(
                name="target_fn", project_path=str(self.root), include="callers",
            ))
        d_def = json.loads(raw_def)
        d_call = json.loads(raw_call)
        # Definition path uses the graph adapter (source='graph' key).
        self.assertEqual(d_def.get("source"), "graph")
        # Callers path bypasses the graph adapter (no 'source' key, has
        # callers key, no results key).
        self.assertNotEqual(d_call.get("source"), "graph")
        self.assertIn("callers", d_call)
        self.assertNotIn("results", d_call)
        self.assertNotIn("callers", d_def)
        self.assertIn("results", d_def)


if __name__ == "__main__":
    unittest.main()
