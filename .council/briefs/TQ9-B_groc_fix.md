# TQ9-B GroC — fix planner + graph + citation UI

Broker one-shot. Khong nho chat truoc.

You are GroC. Round 1. Khong peer-score.

TASK_ID: TQ9-B
OBJECTIVE_CLASS: scoped-bugfix-planner-ui
RISK_LEVEL: mid
DEV_LEVEL: mid
LOAD_BUDGET_SOURCE: conservative-heuristic
INDEPENDENT_QUESTIONS: 3
FILES_SUBSYSTEMS: 3
EXPECTED_TOOL_CALLS: 12
EXPECTED_WRITES: 3
DEPENDENCY_DEPTH: 0
EXPECTED_END_MARKER: END_GroC_R1_TQ9-B

ROOT: D:\\0. TRIEN\\1. DATA\\DATA\\5. VIBE APP\\1. APP\\21. ToolQ
ART: D:\\0. TRIEN\\1. DATA\\DATA\\5. VIBE APP\\1. APP\\8. ToolA\\project-orchestrator\\.council\\tmp_codo9_fix_3008\\DATA\\TQ9-B_groc_fix.md
REPRO_PY: D:\\0. TRIEN\\1. DATA\\DATA\\5. VIBE APP\\1. APP\\21. ToolQ\\.orchestrator_cache\\test_scripts\\codo_audit_3008_edges_868bd5dee494fe3d.py
REPRO_JS: D:\\0. TRIEN\\1. DATA\\DATA\\5. VIBE APP\\1. APP\\21. ToolQ\\.orchestrator_cache\\test_scripts\\codo_audit_3008_ui_100b909db2f799e2.js

CHI DUOC SUA 3 FILE:
1) D:\\0. TRIEN\\1. DATA\\DATA\\5. VIBE APP\\1. APP\\21. ToolQ\\bin\\clm_lib\\ask_planner.py
2) D:\\0. TRIEN\\1. DATA\\DATA\\5. VIBE APP\\1. APP\\21. ToolQ\\bin\\clm_lib\\routes_read.py
3) D:\\0. TRIEN\\1. DATA\\DATA\\5. VIBE APP\\1. APP\\21. ToolQ\\toolQ_UI\\Qwen_html_20260819_council.html
Duoc them test moi duoi D:\\0. TRIEN\\1. DATA\\DATA\\5. VIBE APP\\1. APP\\21. ToolQ\\tests\\ neu can.

BUG (ClauLTN da CONFIRM/PARTIAL, isolated repro 2026-08-30):
#7 PARTIAL zone CUSTOM: ALLOWED_ZONES=Z1..Z6 (ask_planner.py dong 13, 266-275). CUSTOM bi loc tham, pre_search khong nhan zones. KHONG mo allowlist tuy y. Fix: neu user gui zone khong hop le -> bao loi ro (error_name ZONE_NOT_ALLOWED) hoac giu [] + note; khong im lang. Khong them CUSTOM vao catalog.
#8 graph-only -> no_context: run_planned_retrieval build [G1] vao ctx_lines/sources nhung ask_prepare (routes_read.py 2123-2128) va handle_ask (2175-2182) chi nhin hits lexical. Neu hits rong MA graph_hits/ctx_lines co du lieu -> KHONG tra no_context; dung prepared/context do.
#9 citation UI: srcZoneBlock (Qwen_html_20260819_council.html 1735-1746) group+sort zone, bo [n], dao thu tu, nhan dong `:10 :10`. Fix: giu so [n] dung thu tu sources; khong sort mat so; khong ghep source_ref da co :line voi source_line_start lan nua.

CAM:
- KHONG sua clm_semantic_embed.py (PID 32232 dang ghi; #1 #4 DEFER).
- KHONG sua clm_librarian_api.py hay interactive_hold.py (shard TQ9-A).
- KHONG restart :8082. KHONG kill PID. KHONG rewrite qnight_state.json. KHONG xoa .bak. KHONG scoop leftover.
- Transport: HTTP [A] localhost:8083. Neu viet ngoai cwd: bootstrap [A] va dung write_fix/write_new_file.
- write_fix can read_file_range token truoc.

ACCEPT:
- Isolated repro #7#8#9 khong con reproduce.
- Artifact ART liet ke file:line + test + ket qua.
- Broker tra loi toi da 5 dong + duong dan ART.
- Ket thuc artifact bang dung chu: END_GroC_R1_TQ9-B
If this task must write outside cwd, bootstrap [A] and use [A] write tools.
