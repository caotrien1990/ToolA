---
version: "1.0.0"
last_verified: "2026-11-22"
verified_head: "dd41e2fc8f78761f8f7b9a104fb5cd98fdf1cad0"
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

## Current token contract

Use the ToolA origin resolved by the launcher for every endpoint below. By default AUTO_TOKEN_INJECT supplies the token. Check requires_token on the tool schema. A protected or mutating label alone does not establish a token requirement.

Read references/token_refresh.md for the refresh and expiry contract shared with skill 14.

When a call returns MUTATION_TOKEN_REQUIRED, fetch GET [A:API]/api/token, keep the returned token in process memory, and retry that call once with the X-Orchestrator-Token header. If it still fails, report the error name and inspect the tool policy. A generic 403 can have another cause.

For HTTP clients, preserve the caller identity (X-Agent-Id) on every tool call. In PowerShell, resolve the configured origin before making the request:

~~~powershell
$base = $env:ORCHESTRATOR_BASE_URL
if (-not $base) { throw "Set ORCHESTRATOR_BASE_URL to the active local ToolA origin." }
$headers = @{ "X-Agent-Id" = $agentId }
# In the fallback branch only, after MUTATION_TOKEN_REQUIRED:
$headers["X-Orchestrator-Token"] = (Invoke-RestMethod "$base/api/token").token
# Retry the same authorized call using these headers.
~~~

Keep token values out of logs, saved scripts and reports. Use the masked token panel when inspecting expiry. The JWT proposal in skill 14 describes a future design.
