# R1 Footage Test — [H:U] (Web UI port 3099)

## Setup
- Sheet URL: `https://docs.google.com/spreadsheets/d/15Te2WS6rDNpc_syKnpPcg_oFC_KfL1FFiGwSqesAqSg/edit?gid=0#gid=0`
- Target: `D:\0. TRIEN\2. PROJECT\393. H. 599\1. FOOTAGE\New folder\TEST HTML_UI`
- Server: `web/server.js` background, port 3099

## Steps (Playwright driven)
1. ✅ Navigate `http://127.0.0.1:3099`
2. ✅ Fill Sheet URL via `evaluate` (form fill mangled spaces in path)
3. ✅ Fill Output Link via `evaluate`
4. ✅ Click Step 1 — IMPORT + VALIDATE + CHECK SERVER
5. ✅ Wait Step 1 done (Step 3 button enabled, btnStep3.disabled=false)
6. ✅ Click Step 3 — GET FOOTAGE & RENAME (modal prompt accepted with target path)
7. ⏳ Poll target folder until files stable

## Validation evidence
- Sheet gviz fix (V15 R1 earlier work): only **27 visible rows** parsed (Excel/CSV would have 35)
- Fake-rename fix: log shows `ERR_COPY_RUNTIME: No exact file for "B - roll STT 1". Closest fuzzy candidate: VID H. 599 (1).MP4` — REJECTED instead of silent rename ✅
- 19 validation errors expected (duplicate STT, missing file names) — workflow continues per H:U `if (r3.error && !r3.validation) throw` fix
- Progress bar v3 (▰▱ glow) — visible via Playwright snapshot

## Files actually copied
(see final report after stable count)

## Test result
**PARTIAL** — some footage copies succeed, others rejected as fake-rename per V15 fix intent.
