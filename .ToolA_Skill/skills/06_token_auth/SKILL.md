---
name: token_auth
description: |
  Use whenever a call returns MUTATION_TOKEN_REQUIRED/401/403 or before invoking
  a TOKEN_REQUIRED destructive tool. Manages X-Orchestrator-Token lifecycle,
  retries, rotation policy, and prevents raw token from leaking into logs/UI/reports.
trigger:
  - error_name: "MUTATION_TOKEN_REQUIRED | UNAUTHORIZED | 403"
  - task_pattern: "token | auth | TOKEN_REQUIRED | X-Orchestrator-Token"
  - tool: "any TOKEN_REQUIRED destructive tool first call this session"
preconditions:
  - "Health OK"
  - "[A:API] reachable"
related_skills:
  - 04_tool_tiers
  - 07_error_schema_hints
  - 14_token_jwt_redesign
risk_level: medium
---

# 06 — Token / Auth

## Token model (V15 current)

- Shared secret stored at `<orchestrator_cache>/.session_token` (localhost-readable)
- Sent via header `X-Orchestrator-Token: <secret>` on token-required requests
- `GET /api/token` returns the current token and rotates it when TTL elapsed
- TTL default is 25 minutes; override with `ORCHESTRATOR_TOKEN_TTL_MINUTES`
- `GET /api/token/refresh` extends TTL of the current value and does not rotate value
- [A] restart also invalidates CA cached assumptions; refetch on 401/403 once

## Endpoints (V15 R16.4)

| Endpoint | Behavior | Use when |
|----------|----------|----------|
| `GET /api/token` | Returns current token; rotates if TTL elapsed (default 25 min) | First fetch in session, or after MUTATION_TOKEN_REQUIRED error |
| `GET /api/token/refresh` | Extends TTL of current token (does NOT rotate value) | Long-running flow wants to keep same value cached in script |
| `GET /api/token_panel` | UI-only metadata (masked token + TTL countdown) | UI chip, never for CA mutation |

**Reuse rule (current):** trong cùng 1 CA process, token VALUE thường ổn định cho đến khi:
- [A] restart (taskkill + relaunch)
- TTL hết hạn (25 phút mặc định hoặc `ORCHESTRATOR_TOKEN_TTL_MINUTES`)
- `/api/token` được gọi sau khi TTL hết hạn và server tự xoay token

Nếu CA script hardcode token → fail sau restart hoặc TTL. **2 giải pháp:**

1. **Pattern A (preferred)**: fetch token mỗi lần script start
   ```js
   const tk = JSON.parse(await call('/api/token')).token;
   // use tk in all subsequent calls
   ```

2. **Pattern B (long-running)**: gọi `/api/token/refresh` định kỳ trước khi expire
   ```js
   setInterval(()=>fetch('/api/token/refresh'), 20*60*1000);  // every 20 min
   ```

**DO NOT** hardcode token in committed code or persisted scripts.

## Scope (DECOUPLED auth model)

Which tools need the token is the live set `code_tools.TOKEN_REQUIRED_TOOLS`, read per
tool as `requires_token` in `GET /api/tools/<tool>/schema` -- never a copied list. Other
mutating tools are token-free but still origin+session gated. `mutation_scope` is
informational risk metadata, not the auth gate.

AUTO_TOKEN_INJECT (`ORCHESTRATOR_AUTO_TOKEN`) defaults ON: [A] injects the token itself and
a CA sends none. Everything below applies only once a call answers MUTATION_TOKEN_REQUIRED,
which is how a daemon running with it OFF says so.

## HTTP defenses

- `/api/call` rate-limit: default 600 requests / 60s (`ORCHESTRATOR_RATE_LIMIT_MAX`); 429 returns `Retry-After`
- Request body cap: default 32MB (`ORCHESTRATOR_MAX_BODY_BYTES`); oversized body returns 413
- SSE `/api/session/stream` requires token via header or `?token=`; prefer header and never log URL tokens

## Client-side wrapper recipes (no backend change needed)

Không cần [A] backend auto-inject. CA tự cache token in-memory per session. 3 patterns:

### Recipe PS — PowerShell wrapper (Windows native)

```powershell
# Cache token in script-scope variable; refetch on 401 once
$script:OrchToken = $null

function Invoke-Orch {
    param(
        [Parameter(Mandatory)][string]$Tool,
        [hashtable]$Body = @{},
        [int]$RetriedAfter401 = 0
    )
    if (-not $script:OrchToken) {
        $script:OrchToken = (Invoke-RestMethod "http://localhost:8083/api/token").token
    }
    $json = $Body | ConvertTo-Json -Depth 20 -Compress
    try {
        return Invoke-RestMethod -Method Post `
            -Uri "http://localhost:8083/api/call/$Tool" `
            -ContentType "application/json" `
            -Headers @{ "X-Orchestrator-Token" = $script:OrchToken } `
            -Body $json
    } catch {
        if ($RetriedAfter401 -ge 1) { throw }
        if ($_.Exception.Response.StatusCode.value__ -in 401,403) {
            $script:OrchToken = (Invoke-RestMethod "http://localhost:8083/api/token").token
            return Invoke-Orch -Tool $Tool -Body $Body -RetriedAfter401 1
        }
        throw
    }
}

# Usage
Invoke-Orch -Tool "open_session" -Body @{ project_path = "D:/repo" }
Invoke-Orch -Tool "write_fix" -Body @{ file_path = "x.py"; content_encoding = "base64"; content = $b64 }
```

### Recipe PY — Python wrapper (cross-platform CA tooling)

```python
import json, urllib.request

class OrchClient:
    def __init__(self, base="http://localhost:8083"):
        self.base = base
        self._token = None

    def _fetch_token(self):
        with urllib.request.urlopen(f"{self.base}/api/token") as r:
            self._token = json.loads(r.read())["token"]

    def call(self, tool: str, body: dict, _retry=False):
        if not self._token:
            self._fetch_token()
        req = urllib.request.Request(
            f"{self.base}/api/call/{tool}",
            data=json.dumps(body).encode("utf-8"),
            headers={"Content-Type": "application/json",
                     "X-Orchestrator-Token": self._token},
        )
        try:
            with urllib.request.urlopen(req) as r:
                return json.loads(r.read())
        except urllib.error.HTTPError as e:
            if e.code in (401, 403) and not _retry:
                self._fetch_token()
                return self.call(tool, body, _retry=True)
            raise

# Usage
oc = OrchClient()
oc.call("open_session", {"project_path": "D:/repo"})
oc.call("write_fix", {"file_path": "x.py", "content_encoding": "base64", "content": b64})
```

### Recipe NODE — Inline node -e wrapper (no file write needed)

```bash
node -e "const h=require('http');let TK=null;async function call(t,b){if(!TK){TK=await new Promise((res,rej)=>{h.get('http://localhost:8083/api/token',r=>{let d='';r.on('data',c=>d+=c);r.on('end',()=>res(JSON.parse(d).token))})})}const data=JSON.stringify(b);return new Promise((res,rej)=>{const req=h.request({hostname:'localhost',port:8083,path:'/api/call/'+t,method:'POST',headers:{'Content-Type':'application/json','X-Orchestrator-Token':TK,'Content-Length':data.length}},r=>{let d='';r.on('data',c=>d+=c);r.on('end',()=>res(d))});req.write(data);req.end()})}call('open_session',{project_path:'D:/repo'}).then(console.log);"
```

**Properties of all 3 wrappers:**
- Token cached in process memory ONLY (never written to disk by CA)
- One refetch on 401/403, then surface error (avoid infinite loop)
- Token never printed in stdout/log/error message (prefix-only allowed)
- Wrapper lives in CA harness, NOT in [A] backend → keeps security boundary

**Why client-side wrapper, not backend auto-inject?**

Backend auto-injecting token defeats the purpose of the header (server-side anti-CSRF gate). The token should be held by the *caller* (CA harness) and presented per-call. Client wrappers preserve this contract while removing CA repetition.

## Fetch flow

```
GET http://localhost:8083/api/token
→ { "token": "<base64>", "issued_at": "...", "reuse_policy": "fetch_once_reuse_for_session" }
```

Send subsequent token-required calls with:
```
Headers: { "X-Orchestrator-Token": <secret>, "Content-Type": "application/json" }
```

## Error → action

| error_name | Action |
|------------|--------|
| MUTATION_TOKEN_REQUIRED | GET /api/token → cache → resend with header | retry once |
| 403 (any) | Same as above; if still fails after retry → token rotated, refetch + retry once more (max 2) |
| Token cached but request unauthorized | Server restarted mid-session → refetch |

## Reuse policy

- Cache token in CA memory (not in file, not in any User-visible buffer)
- Reuse across all token-required calls in the session
- Never put raw token in:
  - Reports, summaries, harness logs
  - UI elements (UI uses server-side proxy)
  - Audit `reason` field in log_native_event
  - JSON dumps from CA back to User
- OK to log a **prefix** (first 8 chars + `...`) for debugging

## Anti-patterns

- ❌ Re-fetching token on every token-required call (server load + token churn)
- ❌ Sending token in URL query string except `/api/session/stream?token=` when EventSource cannot set headers
- ❌ Hardcoding token literal in scripts (rotates on TTL expiry or restart)
- ❌ Logging full token in any audit reason or User-visible output
- ❌ Sending token to remote (non-[A:API]) endpoints

## Workflow

```
1. First token-required call → no token → 403 MUTATION_TOKEN_REQUIRED
2. GET /api/token → cache `token`
3. Retry original call with header → expect 2xx
4. All subsequent token-required calls → reuse cached token
5. On 403 again → token rotated; refetch once more (last chance)
6. On 403 third time → STOP, report to User; do NOT silent-fallback to native
```

## Guardrails

- Maximum 2 retries per call before escalation
- Never expose raw token (substring max 8 chars + `...` for prefix display in panels)
- Token rotation = [A] restart OR TTL-expired `/api/token`; CA must refetch transparently
- Token requirement is per TOKEN_REQUIRED list, not equal to every mutating/protected tool

## Operational Impact

- ~80 bytes per token fetch; saved by caching → 1 fetch per session
- Avoid re-fetching wastes ~150 bytes per protected call
- 403 retry adds ~400 bytes overhead; valid token saves this

## Future (skill 14_token_jwt_redesign)

JWT-style `exp` remains a future redesign; current production behavior is shared-secret
TTL rotation through `/api/token`.

## Harness

- Test 1: clean session → first protected call → 403 → fetch → retry → 200
- Test 2: 10 token-required calls with cached token → 1 fetch, 10 success
- Test 3: restart [A] mid-session → next call → 403 → refetch → 200
- Test 4: grep all CA outputs for raw token chars → 0 hits (only prefix)
