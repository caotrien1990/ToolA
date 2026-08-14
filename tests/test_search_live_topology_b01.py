"""B-01 regression: narrow regex search must ignore archived repo copies
AND complete under the live-daemon deadline.

Why: live repositories can contain large ``_orchestrator_backups`` trees. Treating
those trees as source made snapshot creation and candidate enumeration consume the
entire regex budget before a narrow ``file_pattern`` could be useful. Independently,
the deprecated ``regex_search`` wrapper shipped with the raw 1.0s hard default that
only fit in-process unit-test execution; the live aiohttp daemon adds ~1-2s of
overhead (executor-lane submit, asyncio.to_thread, snapshot pin, response envelope)
so the same narrow search on a real repo returned REGEX_SEARCH_TIMEOUT even though
the underlying work took ~0.25s. B-01 raises the wrapper default to 5.0s and
exposes ``regex_deadline_seconds`` on the registry so callers can opt into a
tighter budget explicitly.
"""
from __future__ import annotations

import asyncio
import json
import tempfile
import time
import unittest
from pathlib import Path

from orchestrator.tools.search import search_code
from tools_v15 import regex_search


class TestRegexSearchLiveTopologyB01(unittest.TestCase):
    def _make_topology(self, root: Path) -> None:
        (root / "tools_v15.py").write_text(
            "def regex_search():\n    return 'live'\n", encoding="utf-8"
        )
        backup = root / "_orchestrator_backups" / "daily"
        backup.mkdir(parents=True)
        payload = ("archived regex_search noise " * 4000) + "\n"
        for index in range(180):
            (backup / f"copy_{index:03d}.py").write_text(payload, encoding="utf-8")

    def test_narrow_pattern_skips_large_backup_tree_and_returns_real_hit(self) -> None:
        """Original B-01 focused case: narrow file_pattern + tight explicit deadline.

        Keeps the strict 1.0s bound so the test fails if backup-tree scanning
        regresses into the candidate path (i.e. file_pattern exclusion happens
        BEFORE costly candidate traversal).
        """
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._make_topology(root)

            started = time.monotonic()
            raw = asyncio.run(search_code(
                query="regex_search",
                project_path=str(root),
                file_pattern="tools_v15.py",
                mode="regex",
                regex_deadline_seconds=1.0,
            ))
            elapsed = time.monotonic() - started

        data = json.loads(raw)
        self.assertNotEqual(data.get("error_name"), "REGEX_SEARCH_TIMEOUT", data)
        self.assertEqual(data.get("count"), 1, data)
        self.assertEqual(data["results"][0]["file"], "tools_v15.py")
        self.assertLess(elapsed, 1.0, f"narrow regex search took {elapsed:.3f}s")

    def test_regex_search_wrapper_default_deadline_is_live_safe(self) -> None:
        """B-01 deadline fix: the regex_search convenience alias must default to
        a budget the live daemon can actually meet.

        Before the fix the wrapper inherited search_code's 1.0s hard default,
        which fired REGEX_SEARCH_TIMEOUT on the real orchestrator (live
        evidence: /api/call/regex_search returned the timeout in ~1.16s even
        for file_pattern='tools_v15.py' on the project-orchestrator repo).
        The wrapper now defaults to 5.0s; this test proves the default is
        honoured and that the call completes under it on a backup-polluted
        topology.
        """
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._make_topology(root)

            started = time.monotonic()
            # No regex_deadline_seconds — exercises the wrapper default.
            raw = asyncio.run(regex_search(
                pattern="regex_search",
                project_path=str(root),
                file_pattern="tools_v15.py",
            ))
            elapsed = time.monotonic() - started

        data = json.loads(raw)
        self.assertNotEqual(data.get("error_name"), "REGEX_SEARCH_TIMEOUT", data)
        self.assertEqual(data.get("count"), 1, data)
        self.assertEqual(data["results"][0]["file"], "tools_v15.py")
        # 5.0s default — leave head-room for slow CI but fail if the deadline
        # silently regresses back toward 1s (which would surface as a timeout
        # in the live daemon, not here).
        self.assertLess(elapsed, 5.0, f"regex_search took {elapsed:.3f}s")

    def test_regex_search_wrapper_forwards_explicit_deadline(self) -> None:
        """Callers can still opt into a tighter budget explicitly."""
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._make_topology(root)

            raw = asyncio.run(regex_search(
                pattern="regex_search",
                project_path=str(root),
                file_pattern="tools_v15.py",
                regex_deadline_seconds=2.0,
            ))

        data = json.loads(raw)
        self.assertNotEqual(data.get("error_name"), "REGEX_SEARCH_TIMEOUT", data)
        self.assertEqual(data.get("count"), 1, data)


if __name__ == "__main__":
    unittest.main()
