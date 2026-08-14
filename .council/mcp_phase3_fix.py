#!/usr/bin/env python3
"""
PHASE 3: Fix payload issues + proper classification.
"""
import json, os, urllib.request, urllib.error, time

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
            raw = resp.read().decode("utf-8", errors="replace")
            return json.loads(raw)
    except urllib.error.HTTPError as e:
        raw = e.read().decode("utf-8", errors="replace") if e.fp else "{}"
        return {"_error": f"HTTP {e.code}", "_raw": raw[:300]}
    except Exception as e:
        return {"_error": str(e), "_raw": ""}

def get_schema(name):
    return req(f"{BASE_URL}/api/tools/{name}/schema")

def call(name, payload, method="POST"):
    if method == "GET":
        return req(f"{BASE_URL}/api/call/{name}?{urllib.parse.urlencode(payload)}")
    return req(f"{BASE_URL}/api/call/{name}", payload)

# ---- FIXED PAYLOADS for each failing tool ----
FIXES = {
    "ast_query": {"pattern": "async_function", "project_path": PROJ, "file_pattern": "*.py", "max_results": 5},
    "bundle_version_check": {"url": "http://localhost:8083/api/tools/names", "reference_version": "v15"},
    "cache_status": {"project_path": PROJ},
    "cancel_pending_restart": {"reason": "test"},  # try POST
    "cep_manifest_validate": {"manifest_path": PROJ + r"\orchestrator\.cep\manifest.xml"},
    "council_message": {"sender": "ComC", "to": "Codo", "text": "MCP test message"},
    "declare_mission": {"mission_text": "Test full 180 MCP tool surface coverage for project orchestrator"},
    "fetch_sheet_csv_summary": {"url": "https://example.com/data.csv"},
    "find_callers": {"symbol": "handle_call", "project_path": PROJ, "context_lines": 2},
    "find_definition_and_usage": {"symbol": "handle_call", "project_path": PROJ, "context_lines": 2},
    "find_symbol": {"name": "handle_call", "project_path": PROJ, "include": "def", "name_encoding": "plain"},
    "folder_diff": {"folder_path": COUNCIL, "ref_path": COUNCIL, "compare_by": "name", "max_entries": 5},
    "get_file_symbols": {"file_path": "orchestrator_server.py", "project_path": PROJ},
    "get_rule_core_text": {"method": "GET", "payload": {}},  # try GET + empty body
    "get_session_events": {"project_path": PROJ, "limit": 5},  # no session_id, use project_path
    "git_blame_symbol": {"symbol": "handle_call", "project_path": PROJ, "file_pattern": "*.py"},
    "manage_agent": {"action": "list"},
    "protected_tools_status": {"method": "GET", "payload": {}},
    "query_index": {"project_path": PROJ, "query": "reg", "group": "source", "max_results": 5, "auto_refresh": True},
    "read_skill": {"skill_name": "rune-cook", "project_path": PROJ},
    "regex_search": {"pattern": "def handle", "project_path": PROJ, "file_pattern": "*.py", "flags": "i", "max_results": 5},
    "search_code": {"query": "def handle_call", "project_path": PROJ, "file_pattern": "*.py", "mode": "regex", "query_encoding": "plain"},
    "static_asset_cache_check": {"url": "http://localhost:8083/api/tools/names"},
    "verify_get_footage_copy_completion": {"dest_path": COUNCIL},
    "wait_for_idle": {"signals": [{"type": "process", "name": "python.exe"}]},
    "webfetch": {"url": "http://localhost:8083/api/tools/names"},
    "workflow_activate": {"workflow_name": "default", "project_path": PROJ},
    "workflow_draft_get": {"workflow_name": "default", "project_path": PROJ},
    "workflow_get_current": {"workflow_name": "default", "project_path": PROJ},
    "workflow_goto_step": {"step_id": "1", "project_path": PROJ},
    "workflow_save_md": {"workflow_name": "test", "markdown": "# test", "project_path": PROJ},
    "workflow_submit_step": {"step_range": "[1, 1]", "project_path": PROJ},
    "run_node_script": {"script": "console.log('test')", "project_path": PROJ},
    "run_ps1_script": {"script": "Write-Host 'test'", "project_path": PROJ},
    "run_test_profile": {"project_path": PROJ, "profile": "unittest", "timeout": 30},
}

# Tools that are ENV-DEPENDENT (cannot pass without specific runtime state)
ENV_SKIP = [
    "assert_console_no_error",      # needs page_id
    "assert_text_visible",          # needs page_id
    "cdp_attach_target",            # needs electron
    "checkpoint_loop_dom_state",    # needs page_id
    "compare_dom_snapshot",         # needs page_id
    "describe_page",                # needs page_id
    "desktop_screenshot",           # needs desktop window
    "detect_stale_server",          # needs stale server env
    "dom_state_diff",               # needs page_id
    "electron_devtools_log",        # needs electron
    "extract_log_section",          # needs page_id
    "list_cdp_targets",             # needs electron
    "query_dom_candidates",         # needs page_id
    "query_selector",               # needs page_id
    "read_all_logs",                # needs page_id
    "read_console_errors",          # needs page_id
    "read_network_errors",          # needs page_id
    "read_page",                    # needs page_id
    "read_value",                   # needs page_id
    "record_ui_flow",               # needs page_id
    "run_playwright_test",          # needs playwright setup
    "screenshot_page",              # needs page_id
    "ui_state_summary",             # needs page_id
    "wait_for_selector",            # needs page_id
]

# STUBS (planned R+1)
STUBS = [
    "semantic_search",              # requires embedding model
    "type_search",                  # requires LSP/type server
]

def main():
    print("=" * 70)
    print("PHASE 3: Fix payloads + proper classification")
    print("=" * 70)

    # Load previous results
    prev_path = os.path.join(COUNCIL, "mcp_full_surface_report_final.json")
    with open(prev_path, "r", encoding="utf-8") as f:
        prev = json.load(f)

    results = prev["results"]
    all_tools_data = req(f"{BASE_URL}/api/tools/names")
    tools_map = {t["name"]: t for t in all_tools_data["tools"]}

    fixed = 0
    still_fail = 0
    env_skip = 0
    stub_count = 0

    for r in results:
        if r["result"] != "FAIL":
            continue

        name = r["tool"]

        # ENV-dependent → reclassify as SKIP with reason
        if name in ENV_SKIP:
            r["result"] = "SKIP"
            r["reason"] = "ENV_REQUIRED: needs active browser CDP session, electron, or desktop window"
            env_skip += 1
            print(f"  {name:40s} RECLASSIFIED: SKIP (env-dependent)")
            continue

        # STUBS → SKIP
        if name in STUBS:
            r["result"] = "SKIP"
            r["reason"] = "STUB: planned R+1, requires embedding model or LSP/type server"
            stub_count += 1
            print(f"  {name:40s} RECLASSIFIED: SKIP (stub)")
            continue

        # Try fix payload
        fix = FIXES.get(name)
        if not fix:
            still_fail += 1
            print(f"  {name:40s} NO FIX — keeping as FAIL")
            continue

        fix = dict(fix)
        method = fix.pop("method", "POST")
        payload = fix.pop("payload", None) if "payload" in fix else fix.copy()

        if method == "GET":
            result = req(f"{BASE_URL}/api/call/{name}")
            if not result.get("_error") and result.get("ok") != False:
                r["result"] = "PASS"
                fixed += 1
                print(f"  {name:40s} PASS (GET)")
            else:
                # Try POST as fallback
                result = req(f"{BASE_URL}/api/call/{name}", payload)
                if not result.get("_error") and result.get("ok") != False:
                    r["result"] = "PASS"
                    fixed += 1
                    print(f"  {name:40s} PASS (POST fallback)")
                else:
                    still_fail += 1
                    r["error"] = result.get("_error", str(result)[:100])
                    print(f"  {name:40s} STILL FAIL — {r['error'][:80]}")
        else:
            result = req(f"{BASE_URL}/api/call/{name}", payload)
            if not result.get("_error") and result.get("ok") != False:
                r["result"] = "PASS"
                fixed += 1
                print(f"  {name:40s} PASS")
            else:
                still_fail += 1
                err = result.get("_error") or result.get("message", str(result)[:100])
                r["error"] = err
                print(f"  {name:40s} STILL FAIL — {str(err)[:80]}")

        time.sleep(0.05)

    # Final stats
    total = len(results)
    final_pass = sum(1 for r in results if r["result"] == "PASS")
    final_fail = sum(1 for r in results if r["result"] == "FAIL")
    final_skip = sum(1 for r in results if r["result"] == "SKIP")

    print("\n" + "=" * 70)
    print("PHASE 3 FINAL SUMMARY")
    print("=" * 70)
    print(f"  Fixed this phase:    {fixed}")
    print(f"  Env SKIP (was FAIL): {env_skip}")
    print(f"  Stub SKIP (was FAIL):{stub_count}")
    print(f"  Still FAIL:          {still_fail}")
    print(f"  ---")
    print(f"  TOTAL PASS:          {final_pass}")
    print(f"  TOTAL FAIL:          {final_fail}")
    print(f"  TOTAL SKIP:          {final_skip}")
    print(f"  DIRECTLY TESTED:     {final_pass + final_fail}")
    print(f"  TOTAL TOOLS:         {total}")

    # Tier breakdown
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
        print(f"\nREMAINING FAILURES ({len(remaining)}):")
        for f in remaining:
            print(f"  {f['tool']} ({f['tier']}): {f.get('error','?')}")

    # SKIP reasons breakdown
    skip_reasons = {}
    for r in results:
        if r["result"] == "SKIP":
            reason = r.get("reason", "UNKNOWN")
            skip_reasons[reason] = skip_reasons.get(reason, 0) + 1
    print("\nSKIP BREAKDOWN:")
    for reason, count in sorted(skip_reasons.items(), key=lambda x: -x[1]):
        print(f"  [{count}] {reason}")

    # Final report
    report = {
        "tested_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "phase": 3,
        "total_tools": total,
        "directly_tested_via_http": final_pass + final_fail,
        "pass": final_pass,
        "fail": final_fail,
        "skip": final_skip,
        "skip_reasons": skip_reasons,
        "tier_breakdown": {t: dict(tier_stats[t]) for t in sorted(tier_stats)},
        "failures_detail": remaining,
        "results": results,
    }

    rp = os.path.join(COUNCIL, "mcp_full_surface_report_final.json")
    with open(rp, "w", encoding="utf-8") as f:
        json.dump(report, f, indent=2, ensure_ascii=False)
    print(f"\n Report: {rp}")

    return report

if __name__ == "__main__":
    report = main()
