import os
os.environ["GRAPH_ENABLED"] = "1"

import sqlite3
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from orchestrator.graph.config import graph_db_path
from orchestrator.graph.indexer import index_project


CASES = [
    (
        "asyncio_to_thread_arg",
        """
import asyncio


def _f():
    return 1


def caller():
    return asyncio.to_thread(_f)
""",
        "callable_arg",
    ),
    (
        "thread_target_kwarg",
        """
import threading


def _f():
    return 1


def caller():
    return threading.Thread(target=_f)
""",
        "callable_kwarg",
    ),
    (
        "sort_key_kwarg",
        """
def _f(value):
    return value


def caller(values):
    return sorted(values, key=_f)
""",
        "callable_kwarg",
    ),
    (
        "module_assign_call",
        """
def _f():
    return 1


_X = _f()
""",
        "callable_assign_call",
    ),
    (
        "hasattr_string_ref",
        """
def _f():
    return 1


def caller(obj):
    return hasattr(obj, "_f")
""",
        "callable_string_ref",
    ),
    (
        "register_handler_kwarg",
        """
def _f():
    return 1


def register(**kwargs):
    return kwargs


def caller():
    return register(handler=_f)
""",
        "callable_kwarg",
    ),
]


@pytest.mark.parametrize(("case_name", "source", "metadata_marker"), CASES)
def test_python_callable_reference_edges_indexed(tmp_path, monkeypatch, case_name, source, metadata_marker):
    graph_dir = tmp_path / "graph"
    project = tmp_path / case_name
    project.mkdir()
    (project / "mod.py").write_text(source.lstrip(), encoding="utf-8")
    monkeypatch.setenv("GRAPH_DB_DIR", str(graph_dir))

    result = index_project(str(project).replace("\\", "/"), scope="full")

    assert result["status"] == "completed"
    conn = sqlite3.connect(graph_db_path(str(project)))
    try:
        rows = conn.execute(
            """
            SELECT e.confidence, COALESCE(e.metadata, '')
            FROM edges e
            JOIN nodes n ON n.id = e.target
            WHERE e.kind = 'calls'
              AND n.name = '_f'
              AND e.confidence >= 0.7
            """
        ).fetchall()
    finally:
        conn.close()

    assert rows, f"missing inbound calls edge to _f for {case_name}"
    assert any(metadata_marker in metadata for _, metadata in rows)