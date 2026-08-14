# LESSONS_LOG — council pitfall recall (append-only)
# Format: | keyword | symptom | fix |
# Inject auto into council_message_async text when keyword matches task text.
# Append new lessons at the bottom; never edit existing rows (audit trail).

| winerror5 | write_fix WinError 5 Access denied | [A] run_ps1_script FileShare.ReadWrite in-place exact-substring replace; KHONG kill server |
| glm-timeout | GLM lane chet ~546s 2 lan lien tiep | timeout_sec=360, batch max 10, log incremental sau tung mon |
| file-lock | 2 worker cung file -> WinError 5 | doc quyen 1 file = 1 worker/wave |
| unicode-write | Vietnamese/box-drawing qua [A] | base64 encode, khong raw native-write |
| stale-test | test fail reference symbol da doi | replace minimal-equivalent, khong rename blind |
| deadcode-fp | dead_code FP | check bare-ref + getattr(self,'_council_'+name) dispatch |
| ps1-ie | Invoke-WebRequest treo IE DOM | curl.exe hoac -UseBasicParsing |
| a-hung | [A] /health timeout 45s | kill PID cu the qua kill_process, khong restart blind |
| output-first | worker timeout mat report | tao OUTPUT tu mutation dau, update incremental |
| bak-rollback | xoa .bak mat rollback session | check timestamp >=07-07 truoc xoa |
| 3set-diff | map/schemas/phases lech | in symmetric difference, ap hunks thieu |
| stale-db | bug audit code dung DB sai | forced re-index, khong incremental |
| test-council | pytest flaky under live broker | CI Final chay luc council idle |
| model-priority | GLM vs DeepSeek dev | GLM-5.2 cho dev, DeepSeek cho judgement |
| lsp-0.3 | LSP 0.3 bucket toan orphan | skip 0.3, chi apply 0.9+ (1824 confirmed) |
