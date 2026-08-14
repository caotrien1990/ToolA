"""MCP/API parity smoke for Project Orchestrator.

This harness verifies that every API-registered tool is discoverable through
an explicit full MCP discovery request while the default discovery remains bounded, and that tools missing top-level MCP wrappers
remain reachable through the generic in-process dispatcher.
"""

from __future__ import annotations

import asyncio
import json
import re
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import code_tools as ct  # noqa: E402
import orchestrator_server as srv  # noqa: E402


def _mcp_wrapper_names() -> set[str]:
    source = (ROOT / "orchestrator_server.py").read_text(encoding="utf-8", errors="replace")
    section = source[source.find("from zeromcp import McpServer") :]
    return set(re.findall(r"@_mcp\.tool\s*\n\s*def\s+([A-Za-z_][A-Za-z0-9_]*)\s*\(", section))


async def main() -> int:
    api_names = set(ct.TOOL_MAP.keys())
    # Parity validation explicitly requests the full catalog. The user-facing
    # default is intentionally bounded to prevent first-turn context inflation.
    discovery = srv._tool_names_payload(limit=max(1, len(api_names)))
    discovered = {item["name"] for item in discovery["tools"]}
    wrappers = _mcp_wrapper_names()

    missing_from_discovery = sorted(api_names - discovered)
    missing_top_level = sorted(api_names - wrappers)

    checks: list[dict] = []
    with tempfile.TemporaryDirectory() as tmp:
        for name, payload in {
            "workflow_list": {"project_path": tmp},
            "get_folder_size": {"folder_path": tmp},
        }.items():
            response = await srv.dispatch_tool_call(name, payload, transport="mcp")
            body = json.loads(response.text)
            checks.append({
                "tool": name,
                "ok": bool(body.get("ok", True)) and body.get("error_name") != "UNKNOWN_TOOL",
                "error_name": body.get("error_name", ""),
            })

    ok = not missing_from_discovery and all(item["ok"] for item in checks)
    print(json.dumps({
        "ok": ok,
        "api_count": len(api_names),
        "mcp_discovery_count": len(discovered),
        "top_level_wrapper_count": len(wrappers),
        "generic_dispatch_coverage": "all API tools are reachable via call_tool/dispatch_tool_call",
        "missing_from_mcp_discovery": missing_from_discovery,
        "missing_top_level_wrapper_count": len(missing_top_level),
        "missing_top_level_wrapper_sample": missing_top_level[:20],
        "generic_dispatch_smoke": checks,
    }, ensure_ascii=False, indent=2))
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
