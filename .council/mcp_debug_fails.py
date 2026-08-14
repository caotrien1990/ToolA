import json, urllib.request, urllib.error, sys
ORIG="http://localhost:8083"
BASE="http://127.0.0.1:8083"
PROJ=r"D:\0. TRIEN\1. DATA\DATA\5. VIBE APP\1. APP\8. ToolA\project-orchestrator"
COUNCIL=PROJ+r"\.council"

def call(name, payload):
    d = json.dumps(payload).encode()
    r = urllib.request.Request(f"{BASE}/api/call/{name}", data=d,
        headers={"Origin": ORIG, "Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(r, timeout=15) as resp:
            return True, json.loads(resp.read().decode())
    except urllib.error.HTTPError as e:
        body = json.loads(e.read().decode()) if e.fp else {}
        return False, body
    except Exception as e:
        return False, {"error": str(e)}

fails = [
    ("bundle_version_check", {"url": "http://127.0.0.1:8083/api/tools/names"}),
    ("cancel_pending_restart", {}),
    ("cep_manifest_validate", {"manifest_path": PROJ}),
    ("folder_diff", {}),
    ("git_blame_symbol", {"symbol": "main", "project_path": PROJ}),
    ("read_skill", {"skill_name": "rune-cook"}),
    ("static_asset_cache_check", {"url": "http://example.com"}),
    ("verify_get_footage_copy_completion", {}),
    ("workflow_activate", {}),
    ("workflow_draft_get", {}),
    ("workflow_get_current", {}),
    ("workflow_goto_step", {}),
    ("workflow_save_md", {}),
    ("workflow_submit_step", {}),
    ("run_node_script", {"script":"console.log(1)","project_path":PROJ,"timeout":5}),
    ("run_ps1_script", {"script":"Write-Host 1","project_path":PROJ,"timeout":5}),
    ("wait_for_idle", {"signals":[{"type":"process","name":"python.exe"}]}),
]

for name, p in fails:
    ok, r = call(name, p)
    uf = r.get("unsupported_fields", [])
    ac = r.get("accepted_fields", [])
    rf = r.get("required_fields", [])
    pe = r.get("payload_example", "")
    cp = r.get("corrected_payload")
    print(f"=== {name} ===")
    print(f"  ok={ok}") 
    print(f"  error={r.get('error','')}")
    print(f"  required={rf}")
    print(f"  unsupported={uf}")
    print(f"  accepted={ac}")
    print(f"  example={str(pe)[:120]}")
    if cp: print(f"  corrected_payload={cp}")
    print()
