# DEPRECATED — ClinC disabled; council v2 broker-only

Archived 2026-06-16: ClinC is disabled in `.council/council_config.json`. This file is kept only as history and must not be used as an active API guide.

Current active council SOT:
- `.council/council_config.json`
- `.council/council_rule_leader.md`
- HTTP `convene_council` response

Historical current council messaging:

```text
POST http://localhost:8083/api/call/council_message
Content-Type: application/json; charset=utf-8
Body: { "sender": "<you>", "to": "ope|codo|comc|copi|clin|hermes|clau|user", "text": "<message>" }
```

Rules:
- Use `sender`, not `from`; server may normalize aliases but callers should not rely on them
- Server stamps sender from PID trace; spoofed worker names from non-worker callers normalize to `user`
- Pin UTF-8 bytes in PowerShell before sending Vietnamese text
- `to:"user"` posts to the human dashboard without running an agent
- Do not write `broker_filtered.jsonl`, `council_events.jsonl`, inbox, outbox, or `STATE.md` manually
- Every dispatch is one-shot; context mode/file-bridge v1 is removed