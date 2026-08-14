"""ComC real tool-usage harness for Project Orchestrator.

This is not a unit test and not a parity smoke test. It discovers live tools
from GET /api/tools, creates a disposable real project fixture, then calls every
tool through POST /api/call/<tool>. PASS means the live endpoint accepted a
concrete payload. SKIP is reported separately and never counted as PASS.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_BASE_URL = os.environ.get("ORCH_URL", "http://localhost:8083")
REPORT_DIR = ROOT / "reports" / "real_tool_usage"

SAFE_ENV_ERRORS = {
    "AGENT_BUSY",
    "BROWSER_NOT_READY",
    "CDP_NOT_AVAILABLE",
    "CDP_TARGET_NOT_FOUND",
    "EXTERNAL_APP_NOT_FOUND",
    "MISSING_RUNTIME",
    "NO_ACTIVE_PAGE",
    "NO_BROWSER_PAGE",
    "NO_ELECTRON_TARGET",
    "NO_WINDOW",
    "PORT_NOT_LISTENING",
    "PROCESS_NOT_FOUND",
    "SERVER_NOT_RUNNING",
    "TOKEN_REQUIRED",
    "UNAVAILABLE",
    "WINDOW_NOT_FOUND",
}

UNSAFE_BY_DEFAULT = {
    "cancel_native_dialog": "native dialog automation can affect the operator session",
    "cancel_pending_restart": "changes [A] restart state",
    "cep_deploy": "deploys to Adobe CEP live extension directory",
    "click_and_handle_folder_dialog": "native dialog automation can affect the operator session",
    "desktop_click": "real desktop click can affect the operator session",
    "desktop_send_text": "real text injection can affect the operator session",
    "fill_folder_dialog": "native dialog automation can affect the operator session",
    "kill_process": "can terminate an OS process",
    "restart_orchestrator_via_task": "restarts [A]",
}


@dataclass
class ToolResult:
    name: str
    status: str
    http_status: int | None
    error_name: str
    reason: str
    payload: dict[str, Any]
    response_sample: Any
    elapsed_ms: int


def http_json(method: str, url: str, payload: dict[str, Any] | None = None, timeout: int = 120) -> Any:
    data = None if payload is None else json.dumps(payload, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(url, data=data, method=method)
    if payload is not None:
        req.add_header("Content-Type", "application/json; charset=utf-8")
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        raw = resp.read().decode("utf-8", errors="replace")
    return json.loads(raw) if raw else None


def call_tool(base_url: str, name: str, payload: dict[str, Any], timeout: int = 120) -> tuple[int, Any]:
    req = urllib.request.Request(
        f"{base_url.rstrip('/')}/api/call/{name}",
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        method="POST",
    )
    req.add_header("Content-Type", "application/json; charset=utf-8")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read().decode("utf-8", errors="replace")
            return resp.status, json.loads(raw) if raw else {}
    except urllib.error.HTTPError as exc:
        raw = exc.read().decode("utf-8", errors="replace")
        try:
            return exc.code, json.loads(raw) if raw else {"error": str(exc)}
        except json.JSONDecodeError:
            return exc.code, {"error": raw or str(exc)}


def make_fixture() -> Path:
    base = Path(tempfile.mkdtemp(prefix="comc_real_tool_usage_"))
    for rel in ("src/pkg", "logs", "web", "data", "CSXS"):
        (base / rel).mkdir(parents=True, exist_ok=True)
    (base / "src/pkg/sample.py").write_text(
        "VALUE = 41\n\n"
        "def add_one(value: int) -> int:\n"
        "    return value + 1\n\n"
        "class Worker:\n"
        "    def run(self) -> str:\n"
        "        return 'fixture-ok'\n",
        encoding="utf-8",
    )
    (base / "src/pkg/__init__.py").write_text("from .sample import Worker, add_one\n", encoding="utf-8")
    (base / "README.md").write_text("# Fixture\n\nneedle-alpha for real tool search.\n", encoding="utf-8")
    (base / "data/items.json").write_text(json.dumps({"items": [1, 2, 3]}, indent=2), encoding="utf-8")
    (base / "logs/app.log").write_text("INFO start\nERROR fixture-error\nINFO done\n", encoding="utf-8")
    (base / "web/index.html").write_text(
        "<!doctype html><html><head><title>Fixture</title></head><body>"
        "<h1 id='title'>Fixture Page</h1><input id='name' value=''>"
        "<button id='go' onclick=\"document.body.dataset.clicked='yes'\">Go</button>"
        "<script>console.log('fixture-ready')</script></body></html>",
        encoding="utf-8",
    )
    (base / "CSXS/manifest.xml").write_text(
        "<?xml version='1.0' encoding='UTF-8'?><ExtensionManifest "
        "ExtensionBundleId='com.fixture.test' ExtensionBundleVersion='1.0.0' Version='7.0'>"
        "<ExtensionList><Extension Id='com.fixture.panel' Version='1.0.0'/></ExtensionList>"
        "<ExecutionEnvironment><HostList><Host Name='PPRO' Version='[14.0,99.9]'/></HostList>"
        "</ExecutionEnvironment><DispatchInfoList><Extension Id='com.fixture.panel'><DispatchInfo>"
        "<Resources><MainPath>./web/index.html</MainPath></Resources></DispatchInfo></Extension>"
        "</DispatchInfoList></ExtensionManifest>",
        encoding="utf-8",
    )
    try:
        subprocess.run(["git", "init"], cwd=base, check=False, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        subprocess.run(["git", "add", "."], cwd=base, check=False, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except Exception:
        pass
    return base


def props(schema: dict[str, Any]) -> set[str]:
    return set(schema.get("function", {}).get("parameters", {}).get("properties", {}))


def example_value(key: str, fixture: Path) -> Any:
    if key in {"project_path", "folder_path", "path", "cwd", "root"}:
        return str(fixture)
    if key in {"file_path", "target_file", "relative_path"}:
        return "src/pkg/sample.py"
    if key == "src":
        return str(fixture / "README.md")
    if key == "dst":
        return str(fixture / "copied_README.md")
    if key in {"query", "pattern", "regex", "text"}:
        return "needle-alpha"
    if key in {"name", "symbol"}:
        return "add_one"
    if key == "selector":
        return "#title"
    if key == "url":
        return (fixture / "web/index.html").as_uri()
    if key == "command":
        return [sys.executable, "--version"]
    if key in {"script", "code"}:
        return "print('fixture script ok')"
    if key in {"content", "message"}:
        return "COMC real tool usage fixture content\n"
    if key == "content_encoding":
        return "plain"
    if key in {"start_line", "line", "depth", "max_results", "max_context_lines", "limit"}:
        return 1
    if key == "end_line":
        return 20
    if key in {"timeout_sec", "queue_wait_timeout_sec"}:
        return 5
    if key == "port":
        return 8083
    if key == "checks":
        return ["syntax", "encoding"]
    if key == "files":
        return ["README.md"]
    if key == "sender":
        return "comc"
    if key == "to":
        return "user"
    if key == "value":
        return "ComC"
    if key in {"compact", "strict", "save_output", "overwrite", "backup_existing", "validate_first", "queue", "no_queue"}:
        return False
    return "fixture"


def clean_example(raw: dict[str, Any], fixture: Path) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for key, value in raw.items():
        if key.startswith("_"):
            continue
        if isinstance(value, str) and (value.startswith("<") or "D:\\path\\to\\project" in value):
            out[key] = example_value(key, fixture)
        else:
            out[key] = value
    return out


def payload_for(name: str, schema: dict[str, Any], fixture: Path) -> dict[str, Any]:
    fn = schema.get("function", {})
    payload = clean_example(fn.get("payload_example") or schema.get("payload_example") or {}, fixture)
    for key in props(schema):
        payload.setdefault(key, example_value(key, fixture))
    if "project_path" in props(schema):
        payload["project_path"] = str(fixture)

    manual = {
        "ast_query": {"project_path": str(fixture), "file_path": "src/pkg/sample.py", "query": "FunctionDef"},
        "click": {"selector": "#go"},
        "click_selector": {"selector": "#go"},
        "compare_dom_snapshot": {"name": "fixture"},
        "copy_file": {"project_path": str(fixture), "src": str(fixture / "README.md"), "dst": str(fixture / "copy/README.md")},
        "council_message": {"sender": "comc", "to": "user", "text": "COMC real tool usage harness ping.", "timeout_sec": 5, "queue": False},
        "delete_file": {"project_path": str(fixture), "file_path": "delete_me.txt", "dry_run": True},
        "desktop_app_focus": {"title": "__nonexistent_fixture_window__"},
        "desktop_screenshot": {"path": str(fixture / "desktop.png")},
        "evaluate_js": {"script": "document.title"},
        "extract_log_section": {"project_path": str(fixture), "file_path": "logs/app.log", "pattern": "ERROR", "context_lines": 1},
        "fetch_sheet_csv_summary": {"url": (fixture / "data/items.json").as_uri()},
        "fill": {"selector": "#name", "value": "ComC"},
        "fill_value": {"selector": "#name", "value": "ComC"},
        "find_callers": {"project_path": str(fixture), "name": "add_one"},
        "find_definition_and_usage": {"project_path": str(fixture), "name": "add_one"},
        "find_symbol": {"project_path": str(fixture), "name": "add_one", "context_lines": 2},
        "folder_diff": {"left": str(fixture), "right": str(fixture)},
        "get_file_symbols": {"project_path": str(fixture), "file_path": "src/pkg/sample.py"},
        "git_blame_symbol": {"project_path": str(fixture), "file_path": "src/pkg/sample.py", "name": "add_one"},
        "import_graph": {"project_path": str(fixture), "file_path": "src/pkg/sample.py"},
        "list_structure": {"project_path": str(fixture), "path": ".", "depth": 2, "compact": True},
        "open_browser_page": {"url": (fixture / "web/index.html").as_uri()},
        "parse_log_section": {"project_path": str(fixture), "file_path": "logs/app.log", "pattern": "ERROR", "context_lines": 1},
        "port_owner_inspect": {"port": 8083},
        "process_tree_inspect": {"pid": os.getpid()},
        "query_index": {"project_path": str(fixture), "query": "add_one", "file_pattern": "*.py"},
        "query_selector": {"selector": "#title"},
        "read_file_range": {"project_path": str(fixture), "file_path": "src/pkg/sample.py", "start_line": 1, "end_line": 20},
        "regex_search": {"project_path": str(fixture), "query": r"def\s+add_one", "file_pattern": "*.py"},
        "run_node_script": {"project_path": str(fixture), "script": "console.log('fixture node ok')", "timeout_sec": 10},
        "run_project_command": {"project_path": str(fixture), "command": [sys.executable, "--version"], "timeout_sec": 10},
        "run_project_test_script": {"project_path": str(fixture), "command": [sys.executable, "-c", "print('fixture test ok')"], "timeout_sec": 10},
        "run_ps1_script": {"project_path": str(fixture), "script": "Write-Output 'fixture ps ok'", "timeout_sec": 10},
        "save_log_snapshot": {"project_path": str(fixture), "file_path": "logs/app.log", "label": "real_tool_usage"},
        "search_code": {"project_path": str(fixture), "query": "needle-alpha", "file_pattern": "*.md", "literal_search": True},
        "screenshot_page": {"path": str(fixture / "page.png")},
        "validate": {"project_path": str(fixture), "file_path": "src/pkg/sample.py", "checks": ["syntax", "encoding"]},
        "validate_encoding": {"project_path": str(fixture), "file_path": "src/pkg/sample.py", "expected_encoding": "utf-8"},
        "validate_file": {"project_path": str(fixture), "file_path": "src/pkg/sample.py", "lang": "python"},
        "validate_symbols": {"project_path": str(fixture), "file_path": "src/pkg/sample.py"},
        "wait_for_selector": {"selector": "#title", "timeout_sec": 5},
        "webfetch": {"url": f"{DEFAULT_BASE_URL}/api/tools", "max_bytes": 2000},
        "write_fix": {"project_path": str(fixture), "file_path": "README.md", "start_line": 1, "end_line": 1, "content": "# Fixture Updated\n", "dry_run": True},
        "write_fix_html": {"project_path": str(fixture), "file_path": "web/index.html", "old": "Fixture Page", "new": "Fixture Page Updated", "dry_run": True},
        "write_new_file": {"project_path": str(fixture), "file_path": "generated/new_file.txt", "content": "real write_new_file content\n", "content_encoding": "plain"},
    }
    payload.update(manual.get(name, {}))
    if name == "delete_file":
        (fixture / "delete_me.txt").write_text("delete fixture\n", encoding="utf-8")
    return payload


def sample_response(body: Any) -> Any:
    if not isinstance(body, dict):
        return str(body)[:300]
    picked = {k: body.get(k) for k in ("ok", "error_name", "reason", "hint", "count", "status") if k in body}
    if picked:
        return picked
    return {k: (body[k] if isinstance(body[k], (str, int, float, bool, type(None))) else type(body[k]).__name__) for k in list(body)[:5]}


def run_case(base_url: str, schema: dict[str, Any], fixture: Path, args: argparse.Namespace) -> ToolResult:
    name = schema.get("function", {}).get("name", "")
    if name in UNSAFE_BY_DEFAULT and not args.allow_unsafe:
        return ToolResult(name, "SKIP_SAFETY", None, "", UNSAFE_BY_DEFAULT[name], {}, {}, 0)
    payload = payload_for(name, schema, fixture)
    started = time.perf_counter()
    try:
        http_status, body = call_tool(base_url, name, payload, timeout=args.timeout)
    except Exception as exc:
        return ToolResult(name, "FAIL", None, type(exc).__name__, str(exc), payload, {}, 0)
    elapsed_ms = int((time.perf_counter() - started) * 1000)
    error_name = body.get("error_name", "") if isinstance(body, dict) else ""
    ok = bool(body.get("ok", True)) if isinstance(body, dict) else http_status < 400
    if http_status == 404 or error_name == "UNKNOWN_TOOL":
        status, reason = "FAIL", "discovered tool is not callable through /api/call"
    elif ok and http_status < 400:
        status, reason = "PASS_REAL", "live endpoint accepted concrete fixture payload"
    elif error_name in SAFE_ENV_ERRORS:
        status, reason = "SKIP_ENV", "tool invoked but required runtime/token/window/server is unavailable"
    elif error_name in {"INVALID_REQUEST", "MISSING_REQUIRED_FIELD", "SCHEMA_VIOLATION", "UNSUPPORTED_FIELD"}:
        status, reason = "FAIL", "payload/schema mismatch; not a valid real-use case"
    else:
        status, reason = "FAIL", "unexpected live tool failure"
    return ToolResult(name, status, http_status, error_name, reason, payload, sample_response(body), elapsed_ms)


def write_reports(results: list[ToolResult], fixture: Path, base_url: str) -> tuple[Path, Path, dict[str, int]]:
    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    counts: dict[str, int] = {}
    for item in results:
        counts[item.status] = counts.get(item.status, 0) + 1
    stamp = time.strftime("%Y%m%d_%H%M%S")
    json_path = REPORT_DIR / f"comc_real_tool_usage_{stamp}.json"
    md_path = REPORT_DIR / f"comc_real_tool_usage_{stamp}.md"
    json_path.write_text(json.dumps({
        "base_url": base_url,
        "fixture_path": str(fixture),
        "total_tools": len(results),
        "counts": counts,
        "results": [asdict(item) for item in results],
    }, ensure_ascii=False, indent=2), encoding="utf-8")
    lines = [
        "# ComC Real Tool Usage Report",
        "",
        f"- Base URL: `{base_url}`",
        f"- Fixture: `{fixture}`",
        f"- Total discovered tools: **{len(results)}**",
        f"- PASS_REAL: **{counts.get('PASS_REAL', 0)}**",
        f"- FAIL: **{counts.get('FAIL', 0)}**",
        f"- SKIP_ENV: **{counts.get('SKIP_ENV', 0)}**",
        f"- SKIP_SAFETY: **{counts.get('SKIP_SAFETY', 0)}**",
        "",
        "| Tool | Status | Error | Reason |",
        "|---|---:|---|---|",
    ]
    for item in sorted(results, key=lambda r: (r.status != "FAIL", r.status, r.name)):
        lines.append(f"| `{item.name}` | {item.status} | `{item.error_name}` | {item.reason} |")
    md_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return json_path, md_path, counts


def main() -> int:
    parser = argparse.ArgumentParser(description="Invoke every live [A] tool with real fixture payloads.")
    parser.add_argument("--base-url", default=DEFAULT_BASE_URL)
    parser.add_argument("--timeout", type=int, default=120)
    parser.add_argument("--allow-unsafe", action="store_true", help="Also run desktop input, restart, deploy, and kill-process cases.")
    parser.add_argument("--fail-on-skip", action="store_true", help="Return non-zero when any tool is skipped.")
    parser.add_argument("--keep-fixture", action="store_true")
    args = parser.parse_args()

    fixture = make_fixture()
    try:
        tools = http_json("GET", f"{args.base_url.rstrip('/')}/api/tools", timeout=args.timeout)
        if not isinstance(tools, list):
            raise RuntimeError("GET /api/tools did not return a list")
        try:
            call_tool(args.base_url, "open_browser_page", {"url": (fixture / "web/index.html").as_uri()}, timeout=20)
        except Exception:
            pass
        results = [run_case(args.base_url, schema, fixture, args) for schema in tools if schema.get("function", {}).get("name")]
        json_path, md_path, counts = write_reports(results, fixture, args.base_url)
        summary = {
            "ok": counts.get("FAIL", 0) == 0 and (not args.fail_on_skip or not (counts.get("SKIP_ENV", 0) or counts.get("SKIP_SAFETY", 0))),
            "total_tools": len(results),
            "counts": counts,
            "json_report": str(json_path),
            "md_report": str(md_path),
            "fixture_path": str(fixture),
        }
        print(json.dumps(summary, ensure_ascii=False, indent=2))
        if counts.get("FAIL", 0):
            return 1
        if args.fail_on_skip and (counts.get("SKIP_ENV", 0) or counts.get("SKIP_SAFETY", 0)):
            return 2
        return 0
    finally:
        if not args.keep_fixture:
            shutil.rmtree(fixture, ignore_errors=True)


if __name__ == "__main__":
    raise SystemExit(main())
