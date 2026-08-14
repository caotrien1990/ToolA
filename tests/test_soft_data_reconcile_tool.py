"""soft_data_reconcile registered as a native T1 [A] tool (R47).

Verifies the 4-layer registration + the async wrapper contract: compact by default
(drops the ~240-row per-tool matrix), full=True includes it, and the report keeps the
analyzer's structure. The build() runs off the event loop via asyncio.to_thread.
"""
import asyncio
import json
import sys
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

import code_tools as ct  # noqa: E402


class TestSoftDataReconcileTool(unittest.TestCase):
    NAME = "soft_data_reconcile"

    def test_registered_4_layers(self):
        self.assertIn(self.NAME, ct.TOOL_MAP)
        self.assertTrue(callable(ct.TOOL_MAP[self.NAME]))
        self.assertEqual(ct.TOOL_PHASES.get(self.NAME), "T1")
        names = {s.get("function", {}).get("name") for s in ct.TOOL_SCHEMAS}
        self.assertIn(self.NAME, names)

    def test_compact_default_drops_records(self):
        r = json.loads(asyncio.run(ct.TOOL_MAP[self.NAME]()))
        self.assertIn("summary", r)
        self.assertIn("param_concepts", r)
        self.assertNotIn("records", r)  # compact payload
        self.assertGreater(r["summary"]["total_tools"], 100)
        # the tool must see itself on the surface
        self.assertIn("defect_count", r["summary"])

    def test_full_includes_records(self):
        r = json.loads(asyncio.run(ct.TOOL_MAP[self.NAME](full=True)))
        self.assertIn("records", r)
        self.assertIn(self.NAME, r["records"])

    def test_not_protected_t1_read(self):
        self.assertNotIn(self.NAME, ct.PROTECTED_TOOL_NAMES)


if __name__ == "__main__":
    unittest.main()
