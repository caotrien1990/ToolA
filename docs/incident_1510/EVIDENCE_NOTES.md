# Evidence Notes — Incident 1510 (2026-10-15)

## Files preserved

| File | Size | SHA256 | Source |
|------|------|--------|--------|
| `orchestrator.log` | 7,226,699 bytes | `f9b8d73d46c22ff685bb7b9dd1851590adbd8921a1461180df4114ad2f9a88ad` | repo root |
| `pyspy_52372.txt` | 11,726 bytes | `065afd04c6b8b6bd84a434fdcc137bfc229737e8c816e6616ced387d52f62f3e` | `C:\Users\ADMIN\AppData\Local\Temp\` |

Both copied via ToolA `copy_file` (server-side, sha256 verified) on 2026-10-15.

## Missing: `restart_control_audit.jsonl`

The recovery plan (TOOLA_1510_RECOVERY, R0) lists
`.orchestrator_cache/restart_control_audit.jsonl` as the third evidence file.

**Status: NOT FOUND on disk — fate confirmed by ClauLTN (2026-10-16).**
An independent audit (2026-10-15) scanned:
- the entire `.orchestrator_cache/` directory (75 entries), and
- the whole repo tree —

and the file does not exist anywhere.

**ClauLTN confirmation:** the file existed at ~21:55 and had disappeared
before 22:15 — most likely rotated away by the service's own file rotation.
Its content was quoted in ClauLTN's incident report: 5 lines of
`auto_heal_triggered` / `auto_heal_result` audit events.

**Replacement:** use the quoted 5 lines from ClauLTN's report in place of
the original file.
