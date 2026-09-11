---
name: bootstrap_health
description: |
  Use at the start of every session to verify [A] orchestrator is reachable + ready.
  Activate when: first turn of session, after suspected [A] restart, after error
  ORCHESTRATOR_NOT_RUNNING, or whenever [A:API] base URL is uncertain.
trigger:
  - task_pattern: "health | bootstrap | is [A] running | start session | check API"
  - error_name: "ORCHESTRATOR_NOT_RUNNING | BOOTSTRAP_HEALTH_FAIL"
preconditions:
  - "CA has access to Bash/Node runtime"
related_skills:
  - 02_mode_control
  - 16_server_lifecycle
risk_level: low
---

# 01 — Bootstrap & Health

## V15 R16 — Node precondition

`node -e` là exemption duy nhất pre-health. Nếu Node không có trên PATH → bootstrap fail trước khi User thấy [A] error.

**Quick check** (1 line):
```bash
node -v
# Expected: v18.x or newer
```

Nếu CA shell trả `'node' is not recognized`:
- STOP bootstrap.
- Report to User: `Node.js v18+ required on PATH. Install from https://nodejs.org/ or use winget install OpenJS.NodeJS.LTS`.
- KHÔNG attempt native Python/curl/Invoke-WebRequest fallback (đó là violation của BOOTSTRAP exemption scope).

## Decision tree

```
Did I already get {"ok":true} from /health this turn or within the current task chain?
├─ yes → skip; proceed with task
└─ no →
    Run node-only health command (NO PowerShell inline)
    ├─ OK → enter DEFAULT T1 mode
    ├─ ORCHESTRATOR_NOT_RUNNING → STOP + report (no retry/fallback unless the user starts [A])
    └─ running but hung/unresponsive → follow RESTART gate; never restart unprompted
```

### Rule-core bootstrap is separate from health

- After health succeeds, call `POST /api/call/get_rule_core_text {"agent_id":"<id>"}` exactly once per **new process or new conversation**.
- Compaction is NOT a new CA session and does NOT authorize `get_rule_core_text` or `force:true`.
- After compaction: reuse slim already in context; optional hash-check only (`fields=ok,already_fetched,sha256`); sha match → do not request `text`.
- `already_fetched:true` without `text` is the expected stub — not an error and not staleness.
- `force:true` only when (a) Anh asks to verify, (b) sha mismatch / explicit rule-upgrade evidence, or (c) NEW [A] PID/generation and text is missing.
- Do not substitute native project reads for this HTTP bootstrap.

## Health command (canonical)

```cmd
node -e "const http=require('http');http.get((process.env.ORCHESTRATOR_BASE_URL||'http://localhost:8083')+'/health',r=>{let d='';r.on('data',c=>d+=c);r.on('end',()=>console.log(d));}).on('error',e=>console.log('ORCHESTRATOR_NOT_RUNNING:'+e.message));"
```

OK output: `{"ok":true, "port":8083, ...}` (also note `generation` when present). FAIL output: `ORCHESTRATOR_NOT_RUNNING:<errno>`.

## When health fails

1. **Connection refused**: STOP and report `[A]` unavailable. Do not retry unchanged and do not fall back to native project operations.
2. Ask Anh to launch `[A:S]` only when recovery is needed; after Anh does so, probe `/health` once.
3. **Running but hung/unresponsive**: use skill `16_server_lifecycle` and obey the restart approval/in-flight-worker gate.
4. If the post-launch probe still fails, report `error_name=BOOTSTRAP_HEALTH_FAIL` and STOP.
5. **NO_8083 after schtasks/restart helper**: do **not** loop raw `/Run` forever — diagnose High-IL `&&` short-circuit, import crash in `orchestrator.log`, or lock race (skill 16 + `references/high_il_restart_pitfalls.md`).

## Recovery patterns

| Symptom | Likely cause | Action |
|---------|--------------|--------|
| Connection refused, port 8083 free | [A] not started | Ask User run `[A:S]` |
| Connection refused, port 8083 BUSY | Stale process owning port | Skill 16_server_lifecycle → port_owner_inspect |
| Health 200 but `/api/tools` 404 | Old build, missing routes | Verify orchestrator.log timestamp matches today's restart |
| Health 200 but prompt missing V15 markers | [A] restarted before V15 deploy | Notify User to re-run `[A:S]` after files committed |
| schtasks LastResult=1, NO_8083 | Task action `restart && start` short-circuit | Skill 16 High-IL pitfall; admin rebind wrapper |
| Restart helper ran, still no listen | Import crash (e.g. registry_data JSON `true`/`false` in Python) | Tail orchestrator.log; fix source; [A:S] again |
| Health ok but governance tools missing | Process never reloaded new registry | Restart via [A:S]; verify tool names + **new generation** |

## After health OK

- Enter DEFAULT T1 read-only mode (no token, no T2/T3)
- After a genuinely new session, first/force `get_rule_core_text` must include `text`. After compaction, do not re-read `CORE_RULE.txt` or re-fetch merely because context shrank.
- For non-trivial task, consult `.ToolA_Skill\SKILLS.md` router (Progressive Disclosure)
- After restart waves that register tools: optional quick check that expected tool names appear in `/api/tools/names` (do not assume count alone)

## Guardrails

- Never use PowerShell inline for health (broken quoting). Use `node -e "..."` only.
- Never retry > 1 time silently. After 2 fails → STOP + ❓.
- Never assume health from age of UI page; always re-probe `/health` at turn start.
- Never claim restart success without **new** `generation` (or clear process start evidence) when generation is available.

## Efficiency Notes

- Health probe = ~80 bytes request + ~150 bytes response (cheap, run every turn)
- Cache `health=OK` for current turn — don't re-probe for the same task chain
- If health was OK in last 60s of session activity (server log), can skip re-probe

## Harness

- Test 1: kill [A] manually → run health → expect `ORCHESTRATOR_NOT_RUNNING`
- Test 2: restart [A] → run health → expect `{"ok":true}` (+ generation change if pre-captured)
- Test 3: run health 5× in a row → all OK, total bytes < 1 KB
- Test 4: corrupt `start_orchestrator.bat` line → health fail → recovery path triggers
