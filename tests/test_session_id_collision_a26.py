"""A26: session_id must survive concurrent opens by distinct agents.

Two independent defects made `_open_or_resume_session` raise
``UNIQUE constraint failed: sessions.session_id``:

1. The uniqueness tail was ``datetime.now().strftime('%m%d%H%M%S%f')``. On
   Windows ``datetime.now()`` has a ~1ms resolution, so ``%f`` is zero-padded
   rather than a real microsecond count: 2000 consecutive samples produce only
   4 distinct values, one of them repeated 741 times.
2. The agent slug lowercases, replaces every non-``[a-z0-9]`` run with ``-``,
   and truncates to 16. Distinct agent ids collapse onto the same slug --
   ``'agent/a ..'`` and ``'agent\\a ..'`` both become ``agent-a``.

Serializing writes does not help: the id is minted by the application, so two
requests that agree on the millisecond agree on the whole id.
"""
from __future__ import annotations

import re
import sys
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from orchestrator.session import store  # noqa: E402


class SessionIdEntropyTests(unittest.TestCase):
    """Properties of the minted id, independent of any database."""

    def test_distinct_agents_never_share_an_id_component(self):
        # The exact pair that collided in production.
        a = store._session_id_agent_component("agent/a ..")
        b = store._session_id_agent_component("agent\\a ..")
        self.assertNotEqual(a, b, "distinct agent ids must not collapse to one component")

    def test_agent_component_is_stable_for_the_same_agent(self):
        first = store._session_id_agent_component("agent/a ..")
        second = store._session_id_agent_component("agent/a ..")
        self.assertEqual(first, second, "component must be deterministic per agent")

    def test_agent_component_is_filesystem_and_sql_safe(self):
        for agent in ("agent/a ..", "agent\\a ..", "Clau C", "..", "///", "e" * 400):
            component = store._session_id_agent_component(agent)
            self.assertTrue(component, f"empty component for {agent!r}")
            self.assertRegex(component, r"^[a-z0-9-]+$", f"unsafe component for {agent!r}")

    def test_uniqueness_tail_does_not_depend_on_clock_resolution(self):
        # 2000 samples taken as fast as possible. The old strftime tail yielded
        # 4 distinct values here; anything clock-derived fails this.
        tails = {store._session_id_unique_tail() for _ in range(2000)}
        self.assertGreater(
            len(tails), 1990,
            f"tail is not high-entropy: only {len(tails)} distinct values in 2000 samples",
        )

    def test_uniqueness_tail_is_id_safe(self):
        for _ in range(50):
            self.assertRegex(store._session_id_unique_tail(), r"^[a-z0-9]+$")


class SessionIdConcurrencyTests(unittest.TestCase):
    """The real failure: concurrent opens against one database."""

    def setUp(self):
        import tempfile

        self._tmp = tempfile.TemporaryDirectory()
        self._old_db = store._SESSIONS_DB
        store._SESSIONS_DB = Path(self._tmp.name) / "sessions_a26.db"
        store._init_session_db()

    def tearDown(self):
        # The module keeps a process-wide writer thread holding the db open;
        # Windows refuses to unlink a file with a live handle, so close it
        # before restoring the path.
        writer = getattr(store, "_SESSION_WRITER", None)
        if writer is not None:
            try:
                writer.close()
            except Exception:
                pass
            store._SESSION_WRITER = None
        store._SESSIONS_DB = self._old_db
        try:
            self._tmp.cleanup()
        except (PermissionError, OSError):
            pass  # Windows may still hold the sqlite file; the tmp dir is disposable

    def test_colliding_agents_opening_at_once_both_succeed(self):
        project = str(Path(self._tmp.name) / "proj")

        def open_for(agent_id: str):
            return store._open_or_resume_session(project, "Build CRM", "T2", agent_id)

        with ThreadPoolExecutor(max_workers=2) as pool:
            futures = [
                pool.submit(open_for, "agent/a .."),
                pool.submit(open_for, "agent\\a .."),
            ]
            results = [f.result(timeout=30) for f in futures]

        ids = [session["session_id"] for session, _created in results]
        self.assertEqual(len(set(ids)), 2, f"session ids collided: {ids}")

    def test_many_opens_in_the_same_millisecond_stay_unique(self):
        # Hammer the path that used to mint identical ids: same project, same
        # title, distinct agents, all racing inside one clock tick.
        project = str(Path(self._tmp.name) / "proj2")
        agents = [f"agent-{i}" for i in range(24)]

        with ThreadPoolExecutor(max_workers=8) as pool:
            results = list(pool.map(
                lambda a: store._open_or_resume_session(project, "Same Title", "T1", a),
                agents,
            ))

        ids = [session["session_id"] for session, _created in results]
        self.assertEqual(len(set(ids)), len(agents), "duplicate session ids under load")

    def test_resume_still_returns_the_same_session(self):
        # Entropy must not break resume: a second open for one agent must reuse
        # the existing row rather than mint a new id.
        project = str(Path(self._tmp.name) / "proj3")
        first, created_first = store._open_or_resume_session(project, "Resume Me", "T1", "agent-x")
        second, created_second = store._open_or_resume_session(project, "Resume Me", "T1", "agent-x")

        self.assertTrue(created_first)
        self.assertFalse(created_second, "second open must resume, not create")
        self.assertEqual(first["session_id"], second["session_id"])

    def test_session_id_remains_human_readable(self):
        # The title must still be legible in the id; entropy is a suffix, not a
        # replacement. Operators grep these ids in logs.
        project = str(Path(self._tmp.name) / "proj4")
        session, _created = store._open_or_resume_session(project, "Audit Room 1608", "T1", "clau")
        self.assertIn("audit-room-1608", session["session_id"])
        self.assertRegex(session["session_id"], r"^[a-z0-9-]+$")


if __name__ == "__main__":
    unittest.main()
