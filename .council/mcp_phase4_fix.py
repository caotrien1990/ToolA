#!/usr/bin/env python3
"""PHASE 4: Fix all remaining 23 FAILs with exact schema-derived payloads."""
import json, urllib.request, urllib.error, time

ORIGIN = "http://localhost:8083"
BASE   = "http://127.0.0.1:8083"
PROJ   = r"D:\0. TRIEN\1. DATA\DATA\5. VIBE APP\1. APP\8. ToolA\project-orchestrator"
COUNCIL = PROJ + r"\.council"

def call(name, payload, timeout=25):
    data = json.dumps(payload).encode("utf-8")
    r = urllib.request.Request(f"{BASE}/api/call/{name}", data=data,
        headers={"Origin": ORIGIN, "Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(r, timeout=timeout) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        body = json.loads(e.read().decode("utf-8")) if e.fp else {}
        return {"_http_error": e.code, **body}
    except Exception as e:
        return {"_error": str(e)}

# Each entry: (result_ok, result_body)
def check(name, payload, timeout=25):
    r = call(name, payload, timeout)
    ok = r.get("ok", False)
    err = r.get("_error") or r.get("_http_error") or r.get("error", r.get("message", ""))
    return ok, str(err)[:100]

print("PHASE 4: Fixing 23 remaining FAILs\n")

# ====== BATCH 1: Simple payload fixes ======
tests_batch1 = {
    "bundle_version_check": {"url": "http://127.0.0.1:8083/api/tools/names"},
    "cancel_pending_restart": {},  # no required fields per schema
    "cep_manifest_validate": {"manifest_path": PROJ + r"\orchestrator\.cep\manifest.xml"},
    "declare_mission": {"mission": "MCP full surface test coverage verification"},
    "folder_diff": {"path1": COUNCIL, "path2": COUNCIL},
    "get_rule_core_text": {"agent_id": "com", "force": False},
    "git_blame_symbol": {"symbol": "main", "project_path": PROJ, "file_pattern": "*.py"},
    "protected_tools_status": {},  # takes no arguments
    "read_skill": {"skill_name": "rune-cook"},
    "static_asset_cache_check": {"url": "http://example.com", "project_path": PROJ},
    "verify_get_footage_copy_completion": {"dest_dir": COUNCIL},
    "webfetch": {"url": "http://httpbin.org/get"},
}

for name, p in tests_batch1.items():
    ok, err = check(name, p)
    print(f"  {name:40s} {'PASS' if ok else 'FAIL'}{' — ' + err if not ok else ''}")
    time.sleep(0.1)

# ====== BATCH 2: Workflow tools ======
print()
tests_batch2 = {
    "workflow_activate": {"workflow_name": "test_flow", "project_path": PROJ},
    "workflow_draft_get": {"workflow_name": "test_draft", "project_path": PROJ},
    "workflow_get_current": {"project_path": PROJ},
    "workflow_goto_step": {"step": 1, "project_path": PROJ},
    "workflow_save_md": {"workflow_name": "phase4test", "md_content": "# MCP Test\n\nTest workflow", "project_path": PROJ},
    "workflow_submit_step": {"step": 1, "project_path": PROJ},
}

for name, p in tests_batch2.items():
    ok, err = check(name, p)
    print(f"  {name:40s} {'PASS' if ok else 'FAIL'}{' — ' + err if not ok else ''}")
    time.sleep(0.1)

# ====== BATCH 3: Runtime/timeout tools ======
print()
tests_batch3 = {
    "council_message": {"sender": "ComC", "to": "user", "text": "MCP surface test from ComC"},
    "fetch_sheet_csv_summary": {"url": "http://httpbin.org/get"},
    "run_node_script": {"script": "console.log('ok')", "project_path": PROJ, "timeout": 5},
    "run_ps1_script": {"script": "Write-Host 'ok'", "project_path": PROJ, "timeout": 5},
    "wait_for_idle": {"signals": [{"type": "process", "name": "python.exe"}]},
}

for name, p in tests_batch3.items():
    ok, err = check(name, p, timeout=15)
    print(f"  {name:40s} {'PASS' if ok else 'FAIL'}{' — ' + err if not ok else ''}")
    time.sleep(0.1)

print("\nPhase 4 done.")
