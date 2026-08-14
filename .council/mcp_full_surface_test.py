#!/usr/bin/env python3
"""
Full MCP tool surface test — invokes ALL 180 tools via HTTP POST /api/call/<tool>
Classifies: PASS (200/ok), FAIL (error), SKIP (mutating/protected — not invoked for safety)
Produces: .council/mcp_full_surface_report.json
"""
import json, sys, os, urllib.request, urllib.error, time

ORIGIN   = "http://localhost:8083"
BASE_URL = "http://127.0.0.1:8083"
COUNCIL  = r"D:\0. TRIEN\1. DATA\DATA\5. VIBE APP\1. APP\8. ToolA\project-orchestrator\.council"

# ---- phase 1: discovery -------------------------------------------------
def fetch_tool_names():
    url = f"{BASE_URL}/api/tools/names"
    req = urllib.request.Request(url, headers={"Origin": ORIGIN})
    with urllib.request.urlopen(req, timeout=15) as resp:
        return json.loads(resp.read().decode("utf-8"))

# ---- safe payload generators per tool -----------------------------------
def safe_payload(tool):
    """Return a safe dict payload for a given tool name, or None if SKIP."""
    name = tool["name"]
    protected = tool.get("protected", False)
    mutating  = tool.get("mutating", False)
    tier      = tool.get("tier", "?")

    # ======== SKIP mutating/protected ========
    if protected and mutating:
        return None  # skip all destructive tools

    # ======== Generic safe payloads per tier / category ========
    # T1 read-only tools — most accept empty body or simple params
    if tier == "T1":
        if name in ("search_code", "regex_search"):
            return {"query": "def main", "file_pattern": "*.py"}
        if name in ("read_file_range",):
            return {"file_path": "orchestrator_server.py", "start_line": 1, "end_line": 10}
        if name in ("find_symbol", "find_definition_and_usage", "find_callers"):
            return {"symbol_name": "handle_call", "file_pattern": "*.py"}
        if name in ("get_file_symbols",):
            return {"file_path": "orchestrator_server.py"}
        if name in ("query_index",):
            return {"query_text": "tool dispatch", "max_results": 5}
        if name in ("semantic_search",):
            return {"query_text": "MCP tool call HTTP dispatch"}
        if name == "get_project_summary":
            return {}
        if name == "list_sessions":
            return {}
        if name == "list_projects":
            return {}
        if name == "list_structure":
            return {}
        if name == "state":
            return {}
        if name == "get_rule_core_text":
            return {}
        if name == "read_all_logs":
            return {}
        if name == "get_session_events":
            return {}
        if name == "capability_status":
            return {}
        if name == "cache_status":
            return {}
        if name == "protected_tools_status":
            return {}
        if name == "get_token_meter":
            return {}
        if name == "get_usage_stats":
            return {}
        if name == "workflow_active_get":
            return {}
        if name == "workflow_get_current":
            return {}
        if name == "workflow_list":
            return {}
        if name == "workflow_status":
            return {}
        if name == "workflow_status_registry":
            return {}
        if name == "workflow_draft_get":
            return {}
        if name == "workflow_read_md":
            return {}
        if name == "domain_state_diff":
            return {}
        if name == "folder_diff":
            return {}
        if name == "folder_exists":
            return {}
        if name == "folder_stats":
            return {}
        if name == "backup_status":
            return {}
        if name == "bootstrap_session":
            return {}
        if name == "resolve_project_path":
            return {}
        if name == "check_server_path":
            return {}
        if name == "webfetch":
            return {}  # needs URL — will fail gracefully
        if name == "verify_active_electron_source":
            return {}
        if name == "tool_decision_hint":
            return {}
        if name == "suggest_missing_capabilities":
            return {}
        if name == "get_validation_queue":
            return {}
        if name == "get_skill_state":
            return {}
        if name == "read_skill":
            return {}
        if name == "extract_log_section":
            return {}
        if name == "parse_log_section":
            return {}
        if name == "path_quote_helper":
            return {}
        if name == "type_search":
            return {"type_name": "ToolRequest", "file_pattern": "*.py"}
        if name == "import_graph":
            return {}
        if name == "inspect_project_runtime":
            return {}
        if name == "inspect_project_usage":
            return {}
        if name == "mcp_surface_diff":
            return {}
        if name == "git_blame_symbol":
            return {}
        if name == "ce_dashboard":
            return {}
        if name == "optional_tool_probe":
            return {"tool_name": "search_code"}
        if name == "sample_unc_paths_with_root_check":
            return {}
        if name == "read_value":
            return {"key": "test"}
        if name == "cep_manifest_validate":
            return {}
        if name == "cep_extension_status":
            return {}
        if name == "wait_for_idle":
            return {}
        if name == "next_recommended_action":
            return {}
        if name == "convene_council":
            return {}
        if name == "council_config_diff":
            return {}
        if name == "council_message":
            return {}  # needs to, text — will get validation error
        if name == "describe_page":
            return {}
        if name == "declare_mission":
            return {}
        if name == "acceptance_dashboard":
            return {}
        if name == "alias_contract_test":
            return {}
        if name == "architecture_audit_compact":
            return {}
        if name == "audit_tool_matrix":
            return {}
        if name == "ui_state_summary":
            return {}
        if name == "folder_dialog_snapshot":
            return {}
        if name == "classify_get_footage_outcome":
            return {}
        if name == "verify_get_footage_copy_completion":
            return {}
        if name == "progressive_matrix_check":
            return {}
        if name == "query_dom_candidates":
            return {}
        if name == "ast_query":
            return {"query": "functions", "file_path": "orchestrator_server.py"}
        # default T1 — try empty body
        return {}

    # T2 tools
    if tier == "T2":
        if not mutating:
            if name == "cache_delta":
                return {}
            if name == "project_map":
                return {}
            if name == "run_test_profile":
                return {}
            if name == "post_write_batch_verify":
                return {}
            if name in ("validate", "validate_encoding", "validate_file", "validate_symbols"):
                return {}
        return None  # T2 mutating — skip

    # T3 tools
    if tier == "T3":
        # workflow tools (non-mutating)
        if name.startswith("workflow_") and not mutating:
            return {}
        if name == "manage_agent":
            return {"action": "list"}  # safe read
        return None  # T3 mutating or unknown — skip

    # T4 tools — all desktop/UI, mostly require session
    if tier == "T4":
        # read-only T4
        if name == "list_cdp_targets":
            return {}
        if name == "desktop_screenshot":
            return {"path": os.path.join(COUNCIL, "test_screenshot.png")}
        if name == "desktop_window_list":
            return {}
        if name == "electron_devtools_log":
            return {}
        if name == "check_window_visibility":
            return {}
        if name == "record_ui_flow":
            return {}
        if name == "server_lifecycle_status":
            return {}
        if name == "port_owner_inspect":
            return {"port": 8083}
        if name == "process_tree_inspect":
            return {}
        if name == "bundle_version_check":
            return {}
        if name == "detect_stale_server":
            return {}
        if name == "read_console_errors":
            return {}
        if name == "read_network_errors":
            return {}
        if name == "read_page":
            return {}
        if name == "screenshot_page":
            return {"path": os.path.join(COUNCIL, "test_page.png")}
        if name == "static_asset_cache_check":
            return {}
        if name == "cancel_pending_restart":
            return {}
        if name == "restart_server_via_bat_hint":
            return {}
        if name == "run_playwright_test":
            return {}
        if name == "query_selector":
            return {}
        if name == "wait_for_selector":
            return {}
        if name == "compare_dom_snapshot":
            return {}
        if name == "checkpoint_loop_dom_state":
            return {}
        if name == "assert_console_no_error":
            return {"url": "about:blank"}
        if name == "assert_text_visible":
            return {"text": "test", "timeout_ms": 100}
        if name == "cdp_attach_target":
            return {}
        return None  # mutating T4 — skip

    return None  # unknown — skip


# ---- phase 2: call each tool --------------------------------------------
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
            return {
                "status": resp.status,
                "ok": True,
                "body_preview": body[:500],
                "raw_status": resp.status
            }
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", errors="replace") if e.fp else ""
        return {
            "status": e.code,
            "ok": False,
            "body_preview": body[:500],
            "error": f"HTTP {e.code}: {e.reason}"
        }
    except Exception as e:
        return {
            "status": 0,
            "ok": False,
            "body_preview": "",
            "error": str(e)[:200]
        }


# ---- main ---------------------------------------------------------------
def main():
    print("=" * 70)
    print("MCP FULL SURFACE TEST — 180 tools via HTTP")
    print("=" * 70)

    # 1) Discovery
    print("\n[1] Fetching tool list via GET /api/tools/names ...")
    data = fetch_tool_names()
    tools = data["tools"]
    total = len(tools)
    print(f"    Total tools: {total}")
    assert total == 180, f"Expected 180, got {total}"

    # Stats
    protected = sum(1 for t in tools if t["protected"])
    mutating  = sum(1 for t in tools if t["mutating"])
    pm        = sum(1 for t in tools if t["protected"] and t["mutating"])
    print(f"    Protected: {protected}, Mutating: {mutating}, Protected+Mutating: {pm}")

    # 2) Test each tool
    print("\n[2] Testing ALL 180 tools ...\n")
    results = []
    pass_count = 0
    fail_count = 0
    skip_count = 0
    n = 0

    for tool in tools:
        n += 1
        name = tool["name"]
        payload = safe_payload(tool)

        if payload is None:
            skip_count += 1
            reason = "PROTECTED+MUTATING — safety skip"
            results.append({
                "tool": name,
                "tier": tool["tier"],
                "result": "SKIP",
                "reason": reason,
            })
            print(f"  [{n:3d}/180] {name:40s} SKIP — {reason}")
            continue

        result = call_tool(name, payload)
        status_code = result["status"]

        if result["ok"] and status_code == 200:
            pass_count += 1
            results.append({
                "tool": name,
                "tier": tool["tier"],
                "result": "PASS",
                "status": status_code,
                "body_preview": result["body_preview"],
            })
            print(f"  [{n:3d}/180] {name:40s} PASS (200)")
        else:
            fail_count += 1
            results.append({
                "tool": name,
                "tier": tool["tier"],
                "result": "FAIL",
                "status": status_code,
                "error": result.get("error", "unknown"),
                "body_preview": result["body_preview"],
            })
            print(f"  [{n:3d}/180] {name:40s} FAIL ({status_code}) — {result.get('error', '?')[:80]}")

        time.sleep(0.05)  # tiny delay, don't flood

    # 3) Summary
    print("\n" + "=" * 70)
    print("SUMMARY")
    print("=" * 70)
    print(f"  Total tools:      {total}")
    print(f"  Directly tested:  {pass_count + fail_count}")
    print(f"  PASS:             {pass_count}")
    print(f"  FAIL:             {fail_count}")
    print(f"  SKIP (safety):    {skip_count}")
    print("=" * 70)

    # Breakdown by tier
    tier_stats = {}
    for r in results:
        t = r["tier"]
        if t not in tier_stats:
            tier_stats[t] = {"PASS": 0, "FAIL": 0, "SKIP": 0}
        tier_stats[t][r["result"]] += 1

    print("\nBY TIER:")
    for tier in sorted(tier_stats):
        s = tier_stats[tier]
        print(f"  {tier}: PASS={s['PASS']}, FAIL={s['FAIL']}, SKIP={s['SKIP']}")

    # List failures in detail
    failures = [r for r in results if r["result"] == "FAIL"]
    if failures:
        print(f"\nFAILURES ({len(failures)}):")
        for f in failures:
            print(f"  {f['tool']} ({f['tier']}): {f.get('error','?')}")

    # 4) Write report
    report = {
        "tested_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "total_tools": total,
        "directly_tested": pass_count + fail_count,
        "pass": pass_count,
        "fail": fail_count,
        "skip_safety": skip_count,
        "tier_breakdown": {t: tier_stats[t] for t in sorted(tier_stats)},
        "failures_detail": failures,
        "results": results,
    }

    report_path = os.path.join(COUNCIL, "mcp_full_surface_report.json")
    with open(report_path, "w", encoding="utf-8") as f:
        json.dump(report, f, indent=2, ensure_ascii=False)
    print(f"\nReport saved: {report_path}")

    return report

if __name__ == "__main__":
    report = main()
    sys.exit(0 if report["fail"] == 0 else 1)
