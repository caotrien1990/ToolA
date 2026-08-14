# R1 Footage Test — [E:U] Electron Handoff to User

[E:U] cannot be driven from Playwright MCP without `--remote-debugging-port` configured in main.js. Handing off to User for manual run.

## User steps to complete [E:U] test

1. **Verify [A] running** (open in another terminal nếu needed):
   ```cmd
   node -e "const http=require('http');http.get('http://localhost:8083/health',r=>{let d='';r.on('data',c=>d+=c);r.on('end',()=>console.log(d));});"
   ```
   Phải thấy `{"ok":true}`.

2. **Close any existing Electron Rawcut instance** (Task Manager → end `electron.exe` if running).

3. **Launch [E:U]**:
   ```
   D:\0. TRIEN\1. DATA\DATA\5. VIBE APP\1. APP\6. Tr_Rawcut_Bot\6. Tr_Rawcut_Bot-PREVIEW.bat
   ```
   (this runs `npx electron .` from `2. Rawcut/Source/`)

4. **Verify progress bar v3** đang dùng:
   - Idle: text bar empty (chỉ thấy chip `[0%]` xám nhạt narrow)
   - Workflow chạy: `▰▰▰▰▱…` chars hiện ra với glow xanh + chip pulse

5. **Fill source + target**:
   - Sheet URL: `https://docs.google.com/spreadsheets/d/15Te2WS6rDNpc_syKnpPcg_oFC_KfL1FFiGwSqesAqSg/edit?gid=0#gid=0`
   - Output Link: `D:\0. TRIEN\2. PROJECT\393. H. 599\1. FOOTAGE\New folder\TEST ELECTRON_UI`

6. **Click `① IMPORT + VALIDATE + CHECK SERVER`** → wait until btnStep3 unlocks.

7. **Click `③ GET FOOTAGE & RENAME`** → confirm target path in modal prompt.

8. **Wait copy complete** → verify `TEST ELECTRON_UI` folder has files.

## Expected result (parity với [H:U])

Same fixes applied to both UIs share the same backend (`Source/desktop-backend.js`):
- gviz endpoint → 27 visible rows (not 35 with CSV)
- fake-rename rejected → some footage entries fail with `ERR_COPY_RUNTIME: No exact file for X. Closest fuzzy candidate: Y. Update the sheet/Excel cell to the exact file name`
- E:U btnStep3 unlocks despite validation errors (V15 R1 fix)
- ~4 footage files copied (same as H:U)

## Anti-pattern check

Nếu thấy:
- `[E:U]` get nhiều hơn `[H:U]` → fake-rename fix chưa effective ở Electron (cần investigate)
- btnStep3 vẫn locked → V15 R1 line 2304 fix chưa được pickup (Electron cache)
- Progress bar không hiện text `▰▱` → CSS embedded chưa reload (cần đóng Electron hoàn toàn)

## After test

User báo lại:
- Số file copied trong `TEST ELECTRON_UI`
- Danh sách file (so sánh với 4 file của H:U)
- Bất kỳ error mới nào trong System Error Log

→ Round 1 PASS criteria: H:U + E:U cùng kết quả ±1 file.
