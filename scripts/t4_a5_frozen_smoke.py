"""Frozen A5 smoke: fresh real MCP host, exact Codex configuration, no daemon restart."""
import argparse
import asyncio
import json
import os
from pathlib import Path
import subprocess
import sys
import time
import tomllib

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

parser = argparse.ArgumentParser()
parser.add_argument("--output", required=True)
args = parser.parse_args()
output = Path(args.output)
output.parent.mkdir(parents=True, exist_ok=True)
config = tomllib.loads((Path.home() / ".codex" / "config.toml").read_text(encoding="utf-8"))
host = config["mcp_servers"]["project-orchestrator"]
root = str(Path(host["args"][0]).parent)
project = str(Path(__file__).resolve().parents[1])
record = {"started_at": time.time(), "host_command": host["command"],
          "host_args": host["args"], "agent": host["env"]["COUNCIL_AGENT_ID"],
          "calls": [], "fresh_host": True, "comparison": "fresh operational/schema baseline; pre-bridge unverified"}


def unpack(response):
    for block in response.content:
        if getattr(block, "type", None) == "text":
            try:
                obj = json.loads(block.text)
                if isinstance(obj, dict):
                    return obj
            except (ValueError, TypeError):
                pass
    return {"unparsed": response.model_dump(mode="json")}


async def main():
    env = dict(os.environ)
    env.update(host.get("env", {}))
    params = StdioServerParameters(command=host["command"], args=host["args"], env=env, cwd=root)
    with output.with_suffix(".stderr.log").open("w", encoding="utf-8") as stderr:
        async with stdio_client(params, errlog=stderr) as (reader, writer):
            async with ClientSession(reader, writer) as session:
                await session.initialize()
                listing = await session.list_tools()
                record["surface"] = [tool.name for tool in listing.tools]
                assert "call_tool" in record["surface"]
                async def call(name, payload):
                    response = await session.call_tool("call_tool", {"name": name, "payload": payload})
                    obj = unpack(response)
                    record["calls"].append({"time": time.time(), "name": name,
                                            "payload": payload, "response": response.model_dump(mode="json")})
                    return obj
                await call("t4_host_status", {})
                await call("open_session", {"project_path": project, "mode": "T1"})
                page = await call("open_browser_page", {
                    "project_path": project, "url": "data:text/html,<html><body>T4_A5_FROZEN_MARKER</body></html>",
                    "headless": True})
                page_id = page.get("page_id")
                assert page_id, page
                try:
                    dom = await call("read_page", {"page_id": page_id})
                    assert "T4_A5_FROZEN_MARKER" in json.dumps(dom), dom
                    described = await call("describe_page", {"page_id": page_id})
                    assert not described.get("ok") and described.get("error_name") == "PAGE_NOT_FOUND", described
                    # The HTTP control stays a visible literal PowerShell Invoke-RestMethod.
                    body = json.dumps({"page_id": page_id})
                    command = (
                        "$ErrorActionPreference='Stop'\n"
                        "$body=@'\n" + body + "\n'@\n"
                        "Invoke-RestMethod -Uri 'http://localhost:8083/api/call/read_page' "
                        "-Method Post -Headers @{'X-Agent-Id'='codo'} -ContentType 'application/json' "
                        "-Body ([Text.Encoding]::UTF8.GetBytes($body)) | ConvertTo-Json -Depth 12"
                    )
                    http = subprocess.run(["powershell.exe", "-NoProfile", "-Command", command],
                                          capture_output=True, text=True, encoding="utf-8", timeout=30,
                                          creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
                    record["http_negative_control"] = {"command": command, "stdout": http.stdout,
                                                       "stderr": http.stderr, "exit_code": http.returncode}
                    control = json.loads(http.stdout)
                    assert control.get("error_name") == "PAGE_NOT_FOUND", control
                finally:
                    closed = await call("close_browser_page", {"page_id": page_id})
                    assert closed.get("ok"), closed
                after = await call("read_page", {"page_id": page_id})
                assert not after.get("ok") and after.get("error_name") == "PAGE_NOT_FOUND", after
                record["ok"] = True


try:
    asyncio.run(asyncio.wait_for(main(), timeout=100))
except Exception as exc:
    record.update(ok=False, failure=repr(exc))
finally:
    record["finished_at"] = time.time()
    output.write_text(json.dumps(record, ensure_ascii=False, indent=2), encoding="utf-8")
print(json.dumps({"ok": record.get("ok"), "failure": record.get("failure"), "output": str(output),
                  "surface": record.get("surface"), "calls": len(record["calls"])}))
sys.exit(0 if record.get("ok") else 1)
