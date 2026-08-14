"""DRIFT: RANGE_PAST_EOF + write_fix hint + dashboard AGENT_ORDER comment."""
from __future__ import annotations

import asyncio
import json
import tempfile
import unittest
from pathlib import Path

import code_tools


class TestLineDriftHints(unittest.TestCase):
    def test_dashboard_documents_agent_order_lives_on_server(self):
        dash = Path(code_tools.__file__).resolve().parent / "orchestrator" / "council" / "dashboard.py"
        head = dash.read_text(encoding="utf-8")[:800]
        self.assertIn("DO NOT put AGENT_ORDER", head)
        self.assertIn("orchestrator_server.py _COUNCIL_DASHBOARD_AGENT_ORDER", head)

    def test_write_fix_past_eof_returns_guidance(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            target = root / "tiny.py"
            target.write_text("def foo():\n    return 1\n", encoding="utf-8")
            result = json.loads(asyncio.run(code_tools.write_fix(
                str(target),
                99,
                99,
                "x = 1\n",
                project_path=str(root),
            )))
            self.assertFalse(result.get("ok"), msg=result)
            self.assertEqual(result.get("error_name"), "RANGE_PAST_EOF")
            self.assertEqual(result.get("total_lines"), 2)
            notes = (result.get("ca_guidance") or {}).get("notes") or []
            self.assertTrue(any("read_file_range" in str(n) for n in notes), result)
            self.assertIn("native editor line numbers", " ".join(str(n) for n in notes))
