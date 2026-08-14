"""L3 compact /health + C5 FILE_CHANGED retry must not call refresh_project_map."""
from __future__ import annotations

import json
import unittest
from types import SimpleNamespace
from unittest.mock import patch

import orchestrator_server as srv


class HealthCompactTests(unittest.IsolatedAsyncioTestCase):
    async def test_default_health_compacts_lanes(self):
        with patch.object(srv, "SERVER_READY", True), patch.object(
            srv, "SERVER_STARTUP_PHASE", "ready"
        ):
            resp = await srv.handle_health(SimpleNamespace(headers={}, query={}))
        payload = json.loads(resp.text)
        self.assertEqual(payload.get("health_view"), "compact")
        self.assertIn("executor_lanes", payload)
        for row in payload["executor_lanes"]:
            self.assertNotIn("fair_admission", row)
            self.assertIn("name", row)

    async def test_verbose_health_keeps_fair_admission(self):
        with patch.object(srv, "SERVER_READY", True), patch.object(
            srv, "SERVER_STARTUP_PHASE", "ready"
        ):
            resp = await srv.handle_health(
                SimpleNamespace(headers={}, query={"verbose": "1"})
            )
        payload = json.loads(resp.text)
        self.assertEqual(payload.get("health_view"), "verbose")
        self.assertTrue(payload.get("executor_lanes"))
        self.assertIn("fair_admission", payload["executor_lanes"][0])

    async def test_fields_filter(self):
        with patch.object(srv, "SERVER_READY", True), patch.object(
            srv, "SERVER_STARTUP_PHASE", "ready"
        ):
            resp = await srv.handle_health(
                SimpleNamespace(
                    headers={},
                    query={"fields": "ok,pid,startup_degraded,durable_audit_ok"},
                )
            )
        payload = json.loads(resp.text)
        self.assertTrue(payload.get("health_fields_filtered"))
        self.assertIn("ok", payload)
        self.assertIn("pid", payload)
        self.assertNotIn("executor_lanes", payload)


class FileChangedNoRefreshTests(unittest.TestCase):
    def test_file_changed_retry_skips_refresh(self):
        seq = srv._retry_sequence(
            "write_fix",
            "FILE_CHANGED_OUTSIDE_ORCHESTRATOR",
            {"project_path": r"D:\proj", "file_path": "a.py"},
        )
        tools = [row.get("tool") for row in seq]
        self.assertIn("read_file_range", tools)
        self.assertNotIn("refresh_project_map", tools)
        self.assertTrue(seq[0]["payload"].get("force_reseed"))

    def test_stale_index_retry_skips_refresh(self):
        seq = srv._retry_sequence(
            "find_symbol",
            "STALE_INDEX",
            {"project_path": r"D:\proj", "name": "x"},
        )
        tools = [row.get("tool") for row in seq]
        self.assertNotIn("refresh_project_map", tools)
        self.assertIn("cache_delta", tools)

    def test_default_hint_does_not_recommend_refresh(self):
        hint = srv._default_hint("write_fix", "FILE_CHANGED_OUTSIDE_ORCHESTRATOR")
        self.assertNotIn("refresh_project_map(project_path)", hint)
        self.assertIn("force_reseed", hint)
