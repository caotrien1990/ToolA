#!/usr/bin/env python3
"""
MCP FULL SURFACE TEST — COMPLETE 180 tools via HTTP
"""
import json, sys, os, urllib.request, urllib.error, time

ORIGIN   = "http://localhost:8083"
BASE_URL = "http://127.0.0.1:8083"
COUNCIL  = r"D:\0. TRIEN\1. DATA\DATA\5. VIBE APP\1. APP\8. ToolA\project-orchestrator\.council"
PROJ     = r"D:\0. TRIEN\1. DATA\DATA\5. VIBE APP\1. APP\8. ToolA\project-orchestrator"

def req(url, data=None, method=None):
    h = {"Origin": ORIGIN}
    if data is not None:
        h["Content-Type"] = "application/json"
    body = json.dumps(data).encode("utf-8") if data else None
    r = urllib.request.Request(url, data=body, headers=h, method=method or ("POST" if data else "GET"))
    try:
        with urllib.request.urlopen(r, timeout=20) as resp:
            return json.loads(resp.read().decode("utf-8", errors="replace"))
    except urllib.error.HTTPError as e:
        raw = e.read().decode("utf-8", errors="replace") if e.fp else "{}"
        try: return json.loads(raw)
        except: return {"_error": f"HTTP {e.code}", "_raw": raw[:200]}

def get_tool_schema(name):
    return req(f"{BASE_URL}/api/tools/{name}/schema")

def call_tool(name, payload):
    return req(f"{BASE_URL}/api/call/{name}", payload)

ALL_TOOLS_DATA = None

def all_tools():
    global ALL_TOOLS_DATA
    if ALL_TOOLS_DATA is None:
        ALL_TOOLS_DATA = req(f"{BASE_URL}/api/tools/names")
    return ALL_TOOLS_DATA

def is_safe(tool):
    return not (tool.get("protected") and tool.get("mutating"))

# ---- Build payload from schema ----
def auto_payload(schema, name):
    if not schema or "tool" not in schema:
        return {"_error": "no schema"}
    func = schema["tool"]["function"]
    props = func.get("parameters", {}).get("properties", {})
    reqd  = func.get("parameters", {}).get("required", [])

    SMART = {
        "project_path": PROJ,
        "folder_path": COUNCIL,
        "input_path": COUNCIL,
        "file_path": "orchestrator_server.py",
        "path": COUNCIL,
        "url": "about:blank",
        "page_id": "none",
        "selector": "body",
        "pattern": "search_code",
        "query": "def",
        "query_text": "test",
        "symbol": "handle_call",
        "symbol_name": "handle_call",
        "name": "handle_call",
        "task_description": "test task",
        "scope_hint": "small",
        "risk_hint": "low",
        "idea": "test",
        "mode": "status",
        "aspect": "all",
        "profile": "unittest",
        "checks": ["file"],
        "operations": [{"file_path":"orchestrator_server.py","start_line":1,"end_line":1,"new_content":"# test"}],
        "changed_files": ["orchestrator_server.py"],
        "command": "echo test",
        "session_title": "auto_test",
        "session_id": "none",
        "new_title": "auto_test",
        "action_type": "read",
        "tool_name": "search_code",
        "tools": ["search_code"],
        "signature": "test_fn",
        "lang": "auto",
        "expected_encoding": "utf-8",
        "language_hint": "python",
        "native": [],
        "include_coverage": False, "verbosity": "full",
        "include": "def",
        "max_results": 5, "max_context_lines": 1, "max_files": 50,
        "max_chars": 100,
        "start_line": 1, "end_line": 3,
        "new_content": "# auto test",
        "content": "# auto test",
        "start_anchor": "def handle_call", "end_anchor": "return",
        "force": False, "dry_run": True, "overwrite": False,
        "timeout": 5, "port": 8083,
        "limit": 5, "last_n": 5, "depth": 1,
        "backup": False, "structural_check": False,
        "context_lines": 0,
        "flags": "i",
        "strict": False, "compact": False, "save_output": False,
        "auto_refresh": False,
        "include_http_log": False, "include_global_usage": False,
        "background": False, "keep_alive": False,
        "interactive": False, "elevated": False,
        "include_command": False,
        "run_validate_symbols": False, "run_cache_delta": False, "run_validate_encoding": False,
        "run_sync_rule": False,
        "include_details": False,
        "headless": False, "viewport_w": 800, "viewport_h": 600,
        "wait_for": "load",
        "attach_id": "",
        "content_encoding": "none",
        "end_anchor_inclusive": False,
        "start_anchor_is_regex": False, "end_anchor_is_regex": False,
        "clipboard_paste": False,
        "confirm_warnings": False,
        "confirm_hash": "",
        "ephemeral": False,
        "install_recommended": False,
        "backup_dir": "",
        "duration_ms": 0, "status": "ok",
        "transport": "native",
        "count": 5, "offset": 0,
        "keep_last_n": 2,
        "timeout_ms": 0, "verify_after_launch_ms": 0,
        "expected_port": 0,
    }

    payload = {}
    for field in props:
        if field in SMART:
            payload[field] = SMART[field]
        else:
            pt = props[field].get("type", "string")
            if pt == "string": payload[field] = "test"
            elif pt == "integer": payload[field] = 1
            elif pt == "boolean": payload[field] = False
            elif pt == "array": payload[field] = []
            elif pt == "object": payload[field] = {}
            else: payload[field] = "test"
    return payload

def retry_with_error_hints(name, payload, error_body):
    """If error says unsupported_fields, remove them and retry."""
    if not isinstance(error_body, dict):
        return None
    unsupported = error_body.get("unsupported_fields", [])
    corrected  = error_body.get("corrected_payload")
    if corrected:
        return corrected
    if unsupported:
        p2 = {k: v for k, v in payload.items() if k not in unsupported}
        return p2
    return None

# ====================== MAIN ======================
print("=" * 70)
print("MCP FULL SURFACE TEST — 180 tools with schema-aware payloads")
print("=" * 70)

data = all_tools()
tools = data["tools"]
total = len(tools)
print(f"Total tools: {total}")

# Partition
safe_tools = [t for t in tools if is_safe(t)]
skip_tools = [t for t in tools if not is_safe(t)]
print(f"  Safe (test directly): {len(safe_tools)}")
print(f"  SKIP (protected+mutating): {len(skip_tools)}")

# Fetch all schemas for SAFE tools first
print("\nFetching schemas for all safe tools...")
schemas = {}
n = 0
for t in safe_tools:
    n += 1
    s = get_tool_schema(t["name"])
    schemas[t["name"]] = s
    if n % 30 == 0:
        print(f"  Fetched {n}/{len(safe_tools)} schemas...")

print(f"  Done: {len(schemas)} schemas loaded.")

# Test
results = []
pass_count = 0
fail_count = 0
skip_count = 0

print(f"\nTesting ALL {total} tools...\n")

for i, tool in enumerate(tools):
    name = tool["name"]
    tier = tool["tier"]
    
    if not is_safe(tool):
        skip_count += 1
        results.append({"tool": name, "tier": tier, "result": "SKIP", "reason": "PROTECTED+MUTATING"})
        print(f"  [{i+1:3d}/180] {name:40s} SKIP")
        continue

    schema = schemas.get(name)
    payload = auto_payload(schema, name)
    
    result = call_tool(name, payload)
    status = result.get("_error") or result.get("ok")
    
    if isinstance(result, dict) and result.get("ok") == True:
        pass_count += 1
        results.append({"tool": name, "tier": tier, "result": "PASS"})
        print(f"  [{i+1:3d}/180] {name:40s} PASS")
    elif isinstance(result, dict) and result.get("_error"):
        # Try error correction
        hint_result = retry_with_error_hints(name, payload, result)
        if hint_result:
            result2 = call_tool(name, hint_result)
            if isinstance(result2, dict) and result2.get("ok") == True:
                pass_count += 1
                results.append({"tool": name, "tier": tier, "result": "PASS"})
                print(f"  [{i+1:3d}/180] {name:40s} PASS (retry)")
                continue
        
        fail_count += 1
        results.append({"tool": name, "tier": tier, "result": "FAIL", "error": str(result.get("_error","?"))})
        print(f"  [{i+1:3d}/180] {name:40s} FAIL — {result.get('_error','?')}")
    elif isinstance(result, dict) and result.get("ok") == False:
        # Schema validation error - try corrected_payload
        hint_result = retry_with_error_hints(name, payload, result)
        if hint_result:
            result2 = call_tool(name, hint_result)
            if isinstance(result2, dict) and result2.get("ok") == True:
                pass_count += 1
                results.append({"tool": name, "tier": tier, "result": "PASS"})
                print(f"  [{i+1:3d}/180] {name:40s} PASS (retry)")
                continue
        
        fail_count += 1
        err = result.get("message", result.get("error", str(result)[:100]))
        results.append({"tool": name, "tier": tier, "result": "FAIL", "error": err})
        print(f"  [{i+1:3d}/180] {name:40s} FAIL — {str(err)[:80]}")
    else:
        fail_count += 1
        results.append({"tool": name, "tier": tier, "result": "FAIL", "error": "unknown"})
        print(f"  [{i+1:3d}/180] {name:40s} FAIL — unknown response")
    
    time.sleep(0.05)

# Summary
print("\n" + "=" * 70)
print("FINAL SUMMARY")
print("=" * 70)
print(f"  Total tools:      {total}")
print(f"  Directly tested:  {pass_count + fail_count}")
print(f"  PASS:             {pass_count}")
print(f"  FAIL:             {fail_count}")
print(f"  SKIP (safety):    {skip_count}")

tier_stats = {}
for r in results:
    t = r["tier"]
    tier_stats.setdefault(t, {"PASS":0,"FAIL":0,"SKIP":0})
    tier_stats[t][r["result"]] += 1

print("\nBY TIER:")
for tier in sorted(tier_stats):
    s = tier_stats[tier]
    print(f"  {tier}: PASS={s['PASS']}, FAIL={s['FAIL']}, SKIP={s['SKIP']}")

remaining = [r for r in results if r["result"] == "FAIL"]
if remaining:
    print(f"\nFAILURES ({len(remaining)}):")
    for f in remaining:
        print(f"  {f['tool']} ({f['tier']}): {f.get('error','?')}")

report = {
    "tested_at": time.strftime("%Y-%m-%d %H:%M:%S"),
    "total_tools": total,
    "directly_tested": pass_count + fail_count,
    "pass": pass_count, "fail": fail_count, "skip_safety": skip_count,
    "tier_breakdown": {t: dict(tier_stats[t]) for t in sorted(tier_stats)},
    "failures_detail": remaining,
    "results": results,
}

os.makedirs(COUNCIL, exist_ok=True)
rp = os.path.join(COUNCIL, "mcp_full_surface_report_final.json")
with open(rp, "w", encoding="utf-8") as f:
    json.dump(report, f, indent=2, ensure_ascii=False)
print(f"\n Report: {rp}")

if fail_count > 0:
    sys.exit(1)
