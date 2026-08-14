---
name: token_jwt_redesign
description: |
  Future-state design for moving from shared-secret token to JWT. Currently a design
  spec — does NOT replace current /api/token flow (skill 06). Activate when User asks
  about future auth direction, rotation policy, or multi-CA permission scoping.
trigger:
  - task_pattern: "JWT | token redesign | rotation | expire | multi-CA permission | scope"
  - error_name: "(none — design only)"
preconditions:
  - "Discussion phase only — no R8 implementation"
related_skills:
  - 06_token_auth
  - 17_session_audit
risk_level: low
---

# 14 — Token / JWT Redesign (V16+ design spec)

## Current state (V15) — shared secret

- 1 random token generated per [A] lifecycle (file `<orchestrator_cache>/.session_token`)
- All CAs use same token; localhost-readable
- Rotation = restart only; no expiry, no revocation
- All-or-nothing: token holder has full T2/T3 mutate access

## Why migrate

| Limitation (V15) | V16+ JWT solution |
|------------------|--------------------|
| Single token, all-or-nothing | JWT claims encode `tier_max`, `tool_allowlist` |
| No expiry → must restart to rotate | `exp` claim → CA proactive refetch before expiry |
| No CA identity | `sub: <agent_id>`, audit trail per CA |
| Token leak = total compromise | Short-lived; revocation list possible |
| No scope on project | `proj: [Ab:P]` claim limits to specific project |

## Proposed JWT shape

```json
{
  "iss": "[A]",
  "sub": "agent_<id>",
  "iat": 1715846400,
  "exp": 1715850000,
  "tier_max": "T3",
  "tool_allowlist": ["write_fix","validate","close_session"],
  "proj": "D:/proj/myapp",
  "session_id": "sess_xyz"
}
```

Signed with HS256 + key in `[A]` config. Lifetime 1h (typical). Refresh via `/api/token/refresh` with existing token before expiry.

## Migration path

1. **V16 P0**: deploy `/api/token` returning either old shared secret OR JWT based on `Accept` header
2. **V16 P1**: CA opts in via `Accept: application/jwt`; server returns JWT
3. **V16 P2**: deprecate shared secret with warning header
4. **V17 P0**: remove shared secret path
5. **V17 P1**: add revocation list (`/api/token/revoke`)

## CA changes per phase

V15 (today) → V16:
- Keep using GET /api/token; will return either form transparently
- Read `exp` claim if JWT → proactive refresh

V16 → V17:
- Mandatory JWT
- New error_name=TOKEN_EXPIRED → refresh once → retry
- New error_name=TOKEN_REVOKED → STOP, ask User

## Anti-patterns to design out

- ❌ Storing JWT in cookies (CA is CLI, no browser cookies)
- ❌ Long-lived tokens (>24h) — defeat rotation purpose
- ❌ Refresh tokens (cycle gets complex; instead, shorten exp + auto-refresh)
- ❌ Embedding User PII in claims

## Risk + mitigation

| Risk | Mitigation |
|------|-----------|
| Clock skew CA vs [A] | Allow 60s leeway on exp validation |
| Refresh storm at hour boundaries | Jitter refresh in [exp - 5min, exp - 30s] window |
| Lost private key | Rotate, force re-auth all sessions |
| Multi-server scaling | Shared key file (NFS/secrets manager); HS256 simple |

## Guardrails (when implemented)

- HS256 only (no RS256 complexity for localhost-only [A])
- No `kid` rotation in V16 (single key)
- Token NEVER logged raw; first 16 chars prefix OK for audit
- All token check failures audited via `log_native_event(action_type="token_check_fail")`

## Out of scope for V15 R8

This skill is a placeholder; do NOT implement JWT in current round. Stick with shared-secret flow (skill 06). When [A] team decides to migrate, this doc serves as the spec.

## Harness (future)

- JWT sign + verify round-trip
- exp validation with clock skew tolerance
- Refresh flow before expiry
- Revocation propagation < 5s across server fleet (if scaled)
- Audit trail per `sub` claim
