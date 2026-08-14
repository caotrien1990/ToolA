# OPE REAL-TEST — 13 tool moi cua [A] — KET QUA
- TASK_ID: A_REALTEST_P134_20260807 | Worker: OpeC | Ngay: 2026-08-07
- [A]: http://localhost:8083 gen f492ae687307 (/health ok=true) | Kenh: POST /api/call/<tool> (goi that, khong import self-test)
- Token: GET /api/token (khong in ra) | Scratch: %TEMP%\opencode\ope_realtest_p134 (da don dep)

## Tong ket: 12/13 PASS — FAIL: stop_app

| # | Tool (item) | Ket qua | Bang chung thuc |
|---|---|---|---|
| 1 | query_sqlite (1) | PASS | Schema 37 bang + cot tu librarian.db; SELECT id,started_at,mode FROM distill_runs LIMIT 3 -> 3 row du that; DROP TABLE -> ok=false error_name=WRITE_QUERY_REJECTED (structured, khong crash) |
| 2 | file_profile (1) | PASS | tools_v15.py: 415,481 B, utf-8, 8,629 dong, python, is_binary=false; librarian.db: 360,235,008 B (343.5 MB), is_binary=true, encoding=binary, line_count=null |
| 3 | sample_data_file (1) | PASS | CSV 16 dong: mode=head -> 3 row parsed; mode=tail -> 3 row cuoi; JSONL 12 dong mode=random -> 3 object parsed (1 row fallback _raw vi BOM dau file) |
| 4 | read_chunks (1) | PASS | by=line start=1 count=5 -> 5 dong has_more=true; start=18 count=10 -> 2 dong has_more=false (bien OK); by=byte start=0 count=50 -> returned_bytes=50/334 has_more=true |
| 5 | insert_text (3) | PASS | Khong chi vi tri -> insert tai dong 1 (changed_range 1-1, co .bak); at_line=5 (cuoi) -> append OK; at_line=3 -> insert dung giua (verify bang Get-Content); after_line don le -> AMBIGUOUS_INSERT_POINT (structured reject) |
| 6 | replace_symbol (3) | PASS | File .py ASCII: thay var top-level VAR_COUNT=1->999 va thay ham top_fn noi dung nhieu dong (verify that); symbol thieu + symbol nested (def trong class) -> SYMBOL_NOT_FOUND (thiet ke chi match top-level) |
| 7 | move_file (3) | PASS | move src->dst: src_exists=False, dst_exists=True, payload nguyen ven; path da move -> loi structured PATH_NOT_FOUND |
| 8 | copy_range (3) | PASS | Copy dong 2-4 copy_src.txt -> append vao range_dst.txt (DST-HEAD, COPY-2, COPY-3, COPY-4), source giu 10 dong |
| 9 | cut_range (3) | PASS | dry_run=true -> removed_preview "CUT-3..CUT-5", file khong doi; that -> xoa dong 3-5 khoi source (verify), co .bak |
| 10 | http_check (4) | PASS | GET http://127.0.0.1:8082/health (NGOAI, khong :8083) -> status 200 ok=true elapsed 0.032s + body_preview; expect_status=404 -> ok=false HTTP_STATUS_MISMATCH structured; text_contains hoat dong |
| 11 | start_app (4) | PASS | "python -m http.server 8791" cwd scratch -> launch_verified=true, port_probe reachable (owner python.exe pid 15160), health_probe 200, spawned_pid=67964 detached |
| 12 | stop_app (4) | FAIL | 4 lan goi ({"pid":X}, +force, +project_path, sau open_session tuong minh) -> TAT CA HTTP 400 body RONG (khong structured JSON), khong giu process nao; kill_process (tool goc) kill cung pid OK -> loi rieng stop_app |
| 13 | browser_smoke_test (4) | PASS | Headless Chromium tai http://127.0.0.1:8791/ -> title "Directory listing for /", body_length=1220 dung listing that; page tu dong (finally _close_browser_page) |

## Loi / quan sat
1. stop_app FAIL (nghiem trong nhat): HTTP 400 + body rong moi payload — vi pham hop dong structured-error, khong dung duoc de stop bat ky app nao. Nguyen nhan kha di (doc source read-only): schema thieu project_path nhung requires_session=true (protected, mutation_scope=process) -> session gate khong the thoa man; error response mat body truoc envelope. De xuat: them project_path vao schema stop_app HOAC bo requires_session (kill_process khong can session).
2. replace_symbol: file co BOM UTF-8 dau dong -> SYMBOL_NOT_FOUND cho var top-level ngay sau BOM (file khong BOM thi OK) -> regex can strip BOM. Nho.
3. insert_text: khong chi vi tri -> insert tai DONG 1 chu khong append cuoi file; muon append phai dung at_line=<cuoi+1>. Nen ghi chu schema hoac doi default. Nho.
4. insert_text chi truyen after_line -> AMBIGUOUS_INSERT_POINT voi message "mutually exclusive" hoi gay hieu nham (co the do default at_line=0 xung dot). Nho.

## Don dep
- python PID 15160 (8791) + PID 75140 (8792) da kill bang kill_process; port 8791/8792 giai phong (verify khong listener).
- Browser session cua browser_smoke_test tu dong close; khong browser ton dong.
- Scratch %TEMP%\opencode\ope_realtest_p134 da xoa sach; session test da close_session.
- Khong sua code [A], khong restart [A]/controller; chi doc source + goi tool + scratch %TEMP%.

END_OPE_REALTEST_P134