"""R56 real-use guard contracts discovered from the disposable round."""

import asyncio
import json
import tempfile
from pathlib import Path

import tools_v15


def _json(raw):
    return json.loads(raw)


def test_bundle_regex_requires_capture_group_before_network():
    result = _json(asyncio.run(
        tools_v15.bundle_version_check(
            "http://127.0.0.1:9/no-request",
            marker_regex=r"version=[^ ]+",
        )
    ))
    assert result["ok"] is False
    assert result["error_name"] == "VERSION_REGEX_CAPTURE_REQUIRED"


def test_page_id_rejects_numeric_process_id_as_caller_error():
    result = _json(asyncio.run(tools_v15._get_page("33704"))[1])
    assert result["ok"] is False
    assert result["error_name"] == "PAGE_ID_INVALID"


def test_page_id_with_pg_shape_preserves_not_found_state_error():
    result = _json(asyncio.run(tools_v15._get_page("pg-r56-missing"))[1])
    assert result["ok"] is False
    assert result["error_name"] == "PAGE_NOT_FOUND"


def test_playwright_runner_rejects_python_fixture_before_node():
    with tempfile.TemporaryDirectory() as tmp:
        fixture = Path(tmp) / "fixture.py"
        fixture.write_text("print('fixture')\n", encoding="utf-8")
        result = _json(asyncio.run(tools_v15.run_playwright_test(str(fixture), tmp)))
    assert result["ok"] is False
    assert result["error_name"] == "RUNNER_LANGUAGE_MISMATCH"


def test_playwright_runner_reports_missing_dependency_before_spawn():
    with tempfile.TemporaryDirectory() as tmp:
        fixture = Path(tmp) / "fixture.js"
        fixture.write_text("require('playwright');\n", encoding="utf-8")
        result = _json(asyncio.run(tools_v15.run_playwright_test(str(fixture), tmp)))
    assert result["ok"] is False
    assert result["error_name"] == "MODULE_NOT_FOUND"
