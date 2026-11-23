---
version: "1.0.0"
last_verified: "2026-11-22"
verified_head: "dd41e2fc8f78761f8f7b9a104fb5cd98fdf1cad0"
name: bootstrap_health
description: |
  Bootstrap a new ToolA session and diagnose an unavailable or unresponsive daemon.
trigger:
  - task_pattern: "health | bootstrap | is [A] running | start session | check API"
  - error_name: "ORCHESTRATOR_NOT_RUNNING | BOOTSTRAP_HEALTH_FAIL"
  - tool: "get_rule_core_text"
preconditions:
  - "CA has access to Bash/Node runtime"
related_skills:
  - 02_mode_control
  - 16_server_lifecycle
risk_level: low
---

# 01 — Bootstrap & Health

## Start a session

1. Call get_rule_core_text with your agent_id once per new process/session. The full reply includes text, bootstrap_epoch and tool_purposes.
2. After compaction reuse the existing epoch. An already_fetched stub is expected. Request the full rule again only under the force conditions in CORE_RULE.
3. For work on files or folders open_session with an absolute project_path. Inspect the returned action and ownership fields before further calls.

## Diagnose connectivity

For shell diagnostics check Node 18+ with node -v. The node -e HTTP probe is the narrowly scoped pre-health exemption. Resolve ORCHESTRATOR_BASE_URL from the active ToolA launcher before running:

~~~cmd
node -e "const u=new URL('/health',process.env.ORCHESTRATOR_BASE_URL);require(u.protocol==='https:'?'https':'http').get(u,r=>{let b='';r.on('data',s=>b+=s);r.on('end',()=>console.log(r.statusCode,b))}).on('error',e=>console.error(e.code))"
~~~

Probe only when connectivity or generation is in doubt. Connection refused: stop and report the address and error. For a running but unresponsive daemon follow 16_server_lifecycle. A restart requires the owner's go-ahead and the restart tool; verify both a new PID and a changed generation. See references/high_il_restart_pitfalls.md for a failed restart diagnostic.
