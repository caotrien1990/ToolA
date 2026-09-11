import json
import sys
import urllib.request

BASE = "http://localhost:8083/api/call"

def post(tool, payload):
    data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(
        f"{BASE}/{tool}",
        data=data,
        headers={"Content-Type": "application/json; charset=utf-8"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=120) as resp:
        body = resp.read().decode("utf-8")
    print(f"===== {tool} =====")
    print(body[:12000])
    return json.loads(body)

action = sys.argv[1] if len(sys.argv) > 1 else "diff"

if action == "log":
    post("log_native_event", {
        "action_type": "write",
        "tool_name": "native_edit",
        "target": r"D:\0. TRIEN\1. DATA\DATA\5. VIBE APP\1. APP\8. ToolA\project-orchestrator\.council\council_rule_leader.md",
        "status": "ok",
        "reason": "Suspend obsolete 2026-07-25 route table; SOT=Anh chi dao. py_compile ok. No [A] restart.",
        "instead_of": "write_fix",
        "native_command": "search_replace",
        "scope": "upgrade_orchestrator",
        "risk": "medium",
        "transport": "native",
        "project_path": r"D:\0. TRIEN\1. DATA\DATA\5. VIBE APP\1. APP\8. ToolA",
        "agent_id": "gro",
    })
elif action == "diff":
    post("council_config_diff", {
        "project_path": r"D:\0. TRIEN\1. DATA\DATA\5. VIBE APP\1. APP\8. ToolA\project-orchestrator"
    })
elif action == "dry_skill":
    name = sys.argv[2]
    post("manage_agent", {
        "action": "sync_skill",
        "target": "all",
        "skill_name": name,
        "dry_run": True,
    })
elif action == "apply_skill":
    name = sys.argv[2]
    post("manage_agent", {
        "action": "sync_skill",
        "target": "all",
        "skill_name": name,
        "dry_run": False,
    })
elif action == "sync_cfg":
    post("council_config_sync", {
        "project_path": r"D:\0. TRIEN\1. DATA\DATA\5. VIBE APP\1. APP\8. ToolA\project-orchestrator",
        "run_sync_rule": False,
    })
else:
    raise SystemExit(f"unknown action {action}")
