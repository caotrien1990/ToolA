#!/usr/bin/env python3
"""
Phase 2: Fix 73 FAILs by fetching each tool's schema and generating correct payloads.
Then retest all 180 tools with accurate payloads.
"""
import json, sys, os, urllib.request, urllib.error, time

ORIGIN   = "http://localhost:8083"
BASE_URL = "http://127.0.0.1:8083"
COUNCIL  = r"D:\0. TRIEN\1. DATA\DATA\5. VIBE APP\1. APP\8. ToolA\project-orchestrator\.council"
REPORT_IN = os.path.join(COUNCIL, "mcp_full_surface_report.json")

def fetch_tool_schema(name):
    """GET /api/tools/{name}/schema"""
    url = f"{BASE_URL}/api/tools/{name}/schema"
    req = urllib.request.Request(url, headers={"Origin": ORIGIN})
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except Exception as e:
        print(f"    Schema FAIL for {name}: {e}")
        return None

def call_tool(name, payload):
    url = f"{BASE_URL}/api/call/{name}"
    headers = {
        "Origin": ORIGIN,
        "Content-Type": "application/json",
    }
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(url, data=data, headers=headers, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=20) as resp:
            body = resp.read().decode("utf-8", errors="replace")
            return {"status": resp.status, "ok": True, "body_preview": body[:300]}
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", errors="replace")[:500]
        return {"status": e.code, "ok": False, "error": f"HTTP {e.code}: {e.reason}", "body": body}
    except Exception as e:
        return {"status": 0, "ok": False, "error": str(e)[:200]}


def build_payload_from_schema(schema, name):
    """Given a tool schema, build a safe minimal payload."""
    if not schema or "tool" not in schema:
        return None
    
    func = schema["tool"].get("function", {})
    params = func.get("parameters", {}).get("properties", {})
    required = func.get("parameters", {}).get("required", [])
    
    payload = {}
    
    # Known safe values for common fields
    known_values = {
        "project_path": "D:\\0. TRIEN\\1. DATA\\DATA\\5. VIBE APP\\1. APP\\8. ToolA\\project-orchestrator",
        "file_path": "orchestrator_server.py",
        "folder_path": COUNCIL,
        "input_path": COUNCIL,
        "path": COUNCIL,
        "url": "about:blank",
        "page_id": "test",
        "selector": "#test",
        "pattern": "search_code",
        "query": "def main",
        "query_text": "search",
        "symbol": "handle_call",
        "symbol_name": "handle_call",
        "name": "handle_call",
        "task_description": "test task",
        "scope_hint": "test",
        "risk_hint": "low",
        "idea": "test idea",
        "mode": "status",
        "aspect": "all",
        "profile": "unittest",
        "checks": ["file"],
        "operations": [{"file_path": "orchestrator_server.py", "start_line": 1, "end_line": 1, "new_content": "# test"}],
        "changed_files": ["orchestrator_server.py"],
        "command": "echo test",
        "session_title": "test",
        "session_id": "test",
        "new_title": "test",
        "action_type": "read",
        "tool_name": "search_code",
        "tools": ["search_code"],
        "signature": "test_fn",
        "lang": "auto",
        "expected_encoding": "utf-8",
        "language_hint": "python",
        "include_coverage": False,
        "verbosity": "full",
        "native": [],
        "max_results": 5,
        "max_context_lines": 1,
        "max_files": 100,
        "start_line": 1,
        "end_line": 5,
        "new_content": "# test comment",
        "content": "# test comment",
        "start_anchor": "def main",
        "end_anchor": "return",
        "dry_run": True,
        "force": False,
        "timeout": 5,
        "port": 8083,
        "limit": 5,
        "last_n": 10,
    }
    
    for field in params:
        p = params[field]
        ptype = p.get("type", "string")
        
        if field in known_values:
            payload[field] = known_values[field]
        elif field == "end_line":
            payload[field] = 5
        elif ptype == "string":
            payload[field] = "test"
        elif ptype == "integer":
            payload[field] = 1
        elif ptype == "boolean":
            payload[field] = False
        elif ptype == "array":
            payload[field] = []
        elif ptype == "object":
            payload[field] = {}
        else:
            payload[field] = "test"
    
    return payload


def main():
    print("=" * 70)
    print("PHASE 2: Fix 73 FAILs — fetch schemas + retest")
    print("=" * 70)
    
    # Load previous report
    with open(REPORT_IN, "r", encoding="utf-8") as f:
        prev = json.load(f)
    
    all_results = prev["results"]
    failures = [r for r in all_results if r["result"] == "FAIL"]
    print(f"Previous FAILs to fix: {len(failures)}")
    
    # Also load tools list for tier/metadata
    with open(os.path.join(COUNCIL, "tools_names.json"), "r", encoding="utf-8") as f:
        tools_data = json.load(f)
    tools_map = {t["name"]: t for t in tools_data["tools"]}
    
    fixed = 0
    still_fail = 0
    n = 0
    
    for fail_entry in failures:
        n += 1
        name = fail_entry["tool"]
        tier = fail_entry["tier"]
        
        print(f"\n  [{n}/{len(failures)}] {name} ({tier})")
        print(f"    Fetching schema...")
        
        schema = fetch_tool_schema(name)
        payload = build_payload_from_schema(schema, name)
        
        if payload is None:
            print(f"    Could not build payload — keeping as FAIL")
            still_fail += 1
            continue
        
        print(f"    Payload: {json.dumps(payload)[:150]}...")
        result = call_tool(name, payload)
        
        if result["ok"]:
            fixed += 1
            # Update the result entry
            for r in all_results:
                if r["tool"] == name:
                    r["result"] = "PASS"
                    r["status"] = result["status"]
                    r["body_preview"] = result["body_preview"]
                    if "error" in r: del r["error"]
                    break
            print(f"    PASS (200)")
        else:
            still_fail += 1
            # Try with only required fields if we had too many
            error_body = result.get("body", "")
            if "unsupported_fields" in error_body:
                try:
                    err = json.loads(error_body)
                    unsupported = err.get("unsupported_fields", [])
                    corrected = err.get("corrected_payload", {})
                    accepted = err.get("accepted_fields", [])
                    print(f"    Rejected fields: {unsupported}")
                    if corrected:
                        payload2 = corrected
                        print(f"    Retry with corrected payload: {json.dumps(payload2)[:150]}...")
                        result2 = call_tool(name, payload2)
                        if result2["ok"]:
                            fixed += 1
                            still_fail -= 1
                            for r in all_results:
                                if r["tool"] == name:
                                    r["result"] = "PASS"
                                    r["status"] = result2["status"]
                                    r["body_preview"] = result2["body_preview"]
                                    if "error" in r: del r["error"]
                                    break
                            print(f"    PASS (200) after correction")
                            continue
                
                except Exception:
                    pass
            print(f"    STILL FAIL ({result['status']}): {result.get('error','')}")
        
        time.sleep(0.1)
    
    # Final stats
    total = len(all_results)
    final_pass = sum(1 for r in all_results if r["result"] == "PASS")
    final_fail = sum(1 for r in all_results if r["result"] == "FAIL")
    final_skip = sum(1 for r in all_results if r["result"] == "SKIP")
    
    print("\n" + "=" * 70)
    print("PHASE 2 SUMMARY")
    print("=" * 70)
    print(f"  Fixed FAILs:     {fixed}")
    print(f"  Still failing:   {still_fail}")
    print(f"  Total PASS:      {final_pass}")
    print(f"  Total FAIL:      {final_fail}")
    print(f"  Total SKIP:      {final_skip}")
    print(f"  Total tools:     {total}")
    
    # Tier breakdown
    tier_stats = {}
    for r in all_results:
        t = r["tier"]
        tier_stats.setdefault(t, {"PASS": 0, "FAIL": 0, "SKIP": 0})
        tier_stats[t][r["result"]] += 1
    
    print("\nBY TIER:")
    for tier in sorted(tier_stats):
        s = tier_stats[tier]
        print(f"  {tier}: PASS={s['PASS']}, FAIL={s['FAIL']}, SKIP={s['SKIP']}")
    
    # Show remaining failures
    remaining = [r for r in all_results if r["result"] == "FAIL"]
    if remaining:
        print(f"\nREMAINING FAILURES ({len(remaining)}):")
        for f in remaining:
            print(f"  {f['tool']} ({f['tier']}): {f.get('error','?')}")
    
    # Save updated report
    final_report = {
        "tested_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "phase": 2,
        "total_tools": total,
        "directly_tested": final_pass + final_fail,
        "pass": final_pass,
        "fail": final_fail,
        "skip_safety": final_skip,
        "phase1_fixed": fixed,
        "phase2_still_fail": still_fail,
        "tier_breakdown": {t: dict(tier_stats[t]) for t in sorted(tier_stats)},
        "remaining_failures": remaining,
        "results": all_results,
    }
    
    report_path = os.path.join(COUNCIL, "mcp_full_surface_report_v2.json")
    with open(report_path, "w", encoding="utf-8") as f:
        json.dump(final_report, f, indent=2, ensure_ascii=False)
    print(f"\nReport saved: {report_path}")
    
    return final_report

if __name__ == "__main__":
    report = main()
    sys.exit(0)
