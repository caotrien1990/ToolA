"""Durable audit health must not fail solely on metrics TimeoutError."""
from __future__ import annotations

import unittest

import orchestrator_server as srv


class DurableAuditHealthOkTests(unittest.TestCase):
    def test_metrics_timeout_with_live_writer_is_ok(self) -> None:
        metrics = {
            "worker_alive": True,
            "admission_open": True,
            "last_error": None,
            "last_error_kind": None,
        }
        self.assertTrue(
            srv._durable_audit_ok_from_metrics(metrics, "TimeoutError: ")
        )

    def test_in_flight_metrics_with_live_writer_is_ok(self) -> None:
        metrics = {
            "worker_alive": True,
            "admission_open": True,
            "last_error": None,
            "last_error_kind": None,
        }
        self.assertTrue(
            srv._durable_audit_ok_from_metrics(
                metrics, "TimeoutError: metrics collection already in flight"
            )
        )

    def test_apply_timeout_is_not_writer_death(self) -> None:
        metrics = {
            "worker_alive": True,
            "admission_open": True,
            "last_error": "AuditApplyTimeout: audit_id 'x' remains pending",
            "last_error_kind": "apply_timeout",
        }
        self.assertTrue(srv._durable_audit_ok_from_metrics(metrics, None))

    def test_storage_error_is_not_ok(self) -> None:
        metrics = {
            "worker_alive": True,
            "admission_open": True,
            "last_error": "database is locked",
            "last_error_kind": "storage",
        }
        self.assertFalse(srv._durable_audit_ok_from_metrics(metrics, None))

    def test_recovered_sink_error_with_empty_outbox_is_ok(self) -> None:
        metrics = {
            "worker_alive": True,
            "admission_open": True,
            "pending": 0,
            "last_error": "SessionStoreContentionError: database is locked",
            "last_error_kind": "sink",
        }
        self.assertTrue(srv._durable_audit_ok_from_metrics(metrics, None))

    def test_sink_error_with_pending_outbox_is_not_ok(self) -> None:
        metrics = {
            "worker_alive": True,
            "admission_open": True,
            "pending": 1,
            "last_error": "SessionStoreContentionError: database is locked",
            "last_error_kind": "sink",
        }
        self.assertFalse(srv._durable_audit_ok_from_metrics(metrics, None))

    def test_dead_worker_is_not_ok(self) -> None:
        metrics = {
            "worker_alive": False,
            "admission_open": True,
            "last_error": None,
            "last_error_kind": None,
        }
        self.assertFalse(srv._durable_audit_ok_from_metrics(metrics, "TimeoutError: "))
