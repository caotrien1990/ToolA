# TQ9-A ClauC — fix Ask stream + hold

Broker one-shot. Khong nho chat truoc.

You are ClauC. Round 1. Khong peer-score.

TASK_ID: TQ9-A
OBJECTIVE_CLASS: scoped-bugfix-ask-hold
RISK_LEVEL: mid
DEV_LEVEL: mid
LOAD_BUDGET_SOURCE: conservative-heuristic
INDEPENDENT_QUESTIONS: 4
FILES_SUBSYSTEMS: 2
EXPECTED_TOOL_CALLS: 12
EXPECTED_WRITES: 2
DEPENDENCY_DEPTH: 0
EXPECTED_END_MARKER: END_ClauC_R1_TQ9-A

ROOT: D:\\0. TRIEN\\1. DATA\\DATA\\5. VIBE APP\\1. APP\\21. ToolQ
ART: D:\\0. TRIEN\\1. DATA\\DATA\\5. VIBE APP\\1. APP\\8. ToolA\\project-orchestrator\\.council\\tmp_codo9_fix_3008\\DATA\\TQ9-A_clauc_fix.md
REPRO: D:\\0. TRIEN\\1. DATA\\DATA\\5. VIBE APP\\1. APP\\21. ToolQ\\.orchestrator_cache\\test_scripts\\codo_audit_3008_edges_868bd5dee494fe3d.py

CHI DUOC SUA 2 FILE:
1) D:\\0. TRIEN\\1. DATA\\DATA\\5. VIBE APP\\1. APP\\21. ToolQ\\bin\\clm_librarian_api.py
2) D:\\0. TRIEN\\1. DATA\\DATA\\5. VIBE APP\\1. APP\\21. ToolQ\\bin\\clm_lib\\interactive_hold.py
Duoc them test moi duoi D:\\0. TRIEN\\1. DATA\\DATA\\5. VIBE APP\\1. APP\\21. ToolQ\\tests\\ neu can.

BUG (ClauLTN da CONFIRM, isolated repro 2026-08-30):
#2 Ask stream drop anh: _ask_stream_local chi gui user_prompt text (khoang dong 2742-2744). /ask JSON gan anh (routes_read.py 2274-2280) NHUNG KHONG SUA routes_read. do_POST dong 2818 noi MAX_ASK_BODY_BYTES chi /ask /api/ask — them /ask_stream /api/ask_stream.
#3 SSE timeout van done ok:true: dong 2767 emit ASK_LOCAL_IDLE_TIMEOUT roi 2804 van _emit done ok True. Can done.ok=false khi timeout/error.
#5 hold hoi sinh holder het han: hold() dong 130-141 khong prune holders het TTL. Crash holder @t=100 + new+release @t=1000 -> holders=1 until=1180 active=true. Prune het han TRUOC khi append/refresh.
#6 hold I/O khong fail-open: InteractiveHold.__enter__ goi hold()->_write khong boc. PermissionError lam Ask fail. active() da fail-open. __enter__/hold/_write phai fail-open: neu khong ghi duoc gate thi Ask van chay (khong raise).

CAM:
- KHONG sua clm_semantic_embed.py (PID 32232 dang ghi slot; #1 #4 DEFER).
- KHONG restart :8082. KHONG kill PID. KHONG rewrite qnight_state.json. KHONG xoa .bak. KHONG scoop leftover (store.py/vector.py/q_agent).
- KHONG implement Phase 1 schema.
- Transport: HTTP [A] localhost:8083. Neu viet ngoai cwd: bootstrap [A] va dung write_fix/write_new_file.
- write_fix can read_file_range token truoc. Cung file: expected_hash=new_hash tuan tu.

ACCEPT:
- Isolated repro #2#3#5#6 khong con reproduce (sua script hoac test moi; khong sua production DB).
- Artifact ART liet ke file:line da sua + test command + ket qua.
- Broker tra loi toi da 5 dong + duong dan ART.
- Ket thuc artifact bang dung chu: END_ClauC_R1_TQ9-A
If this task must write outside cwd, bootstrap [A] and use [A] write tools.
