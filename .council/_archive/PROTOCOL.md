# COUNCIL PROTOCOL — v2 BROKER-ONLY

Archived 2026-06-16: deprecated v1/v2 summary kept for history only. Active council SOT is `.council/council_config.json` (data/model map/roster) + `.council/council_rule_leader.md` (procedure), returned live by HTTP `convene_council`.

Deprecated v1 file-bridge is removed. Do NOT use inbox/outbox dispatch files,
`STATE.md`, context mode, raw CLI, curl, or helper scripts for council messaging.

## Historical Transport Summary

- Only channel: HTTP `POST /api/call/council_message`
- Body: `{sender:"<you>", to:"<ope|codo|comc|copi|clin|hermes|clau|user>", text:"<message>"}`
- Use `sender`, not `from`; server stamps sender from PID trace and normalizes spoofed names
- Pin UTF-8 bytes for Vietnamese
- Every dispatch is one-shot; embed all needed context in `text`

## Historical Routing Summary

Codo desktop is leader after user-only trigger `bat hoi dong tr` / `bật hội đồng tr`.
ComC model routing uses HTTP `council_worker_model`; never Playwright DOM. HermC
defaults to web/file tasks; browser/T4 goes to ClauC unless explicitly required.