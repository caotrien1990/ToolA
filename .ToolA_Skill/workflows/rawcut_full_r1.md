# Global execution contract
Broker workers must use only `[A]` HTTP at exact base URL `http://localhost:8083`; never guess another port, never health-check, never search source/state/markdown to discover tools, ports, paths, or workflow content. For every `[A]` HTTP call, use one worker `run_commands` action with `command="powershell.exe"` and `args=["-NoProfile","-NonInteractive","-Command","<PowerShell source>"]`; the PowerShell source must build `$body` as a hashtable, pipe it to `ConvertTo-Json -Depth 20`, then call `Invoke-RestMethod -Method Post -Uri 'http://localhost:8083/api/call/<tool_name>' -ContentType 'application/json' -Body $body`. If the call errors, stop immediately; do not retry with another endpoint/tool/field.

Submit contract is strict. Whenever any step says `submit Step N as done`, run exactly one worker `run_commands` action that performs both HTTP calls in this order inside the same PowerShell source: first `workflow_submit_step`, then `workflow_status`. Replace only `<N>` and `<EVIDENCE>`.

```text
command="powershell.exe"
args=["-NoProfile","-NonInteractive","-Command","$submit=@{name='rawcut_full_r1';step=<N>;status='done';evidence='<EVIDENCE>';project_path='D:\0. TRIEN\1. DATA\DATA\5. VIBE APP\1. APP\8. ToolA\project-orchestrator'}; $body=$submit|ConvertTo-Json -Depth 20; $submitResult=Invoke-RestMethod -Method Post -Uri 'http://localhost:8083/api/call/workflow_submit_step' -ContentType 'application/json' -Body $body; $status=@{name='rawcut_full_r1';project_path='D:\0. TRIEN\1. DATA\DATA\5. VIBE APP\1. APP\8. ToolA\project-orchestrator'}; $body=$status|ConvertTo-Json -Depth 20; $statusResult=Invoke-RestMethod -Method Post -Uri 'http://localhost:8083/api/call/workflow_status' -ContentType 'application/json' -Body $body; @{submit=$submitResult;status=$statusResult}|ConvertTo-Json -Depth 30"]
```

Do not split submit and status into two worker actions. Do not stop between submit and status. Do not use the `workflow_submit_step` response to decide or run the next step. Only `status.current_step_body` from the `workflow_status` result in the combined action is authoritative. The only workflow identifier field is `name`. Never use these fields in submit payload: `workflow`, `workflow_name`, `step_number`, `path`, `project`. `workflow_submit_step` is unprotected and does not require token; do not fetch `/api/token` for submit. Never call or guess `workflow_submit`.

## Step 1
### Enable A token gate
Run exactly one worker `run_commands` action:

```text
command="powershell.exe"
args=["-NoProfile","-NonInteractive","-Command","$body=@{}|ConvertTo-Json -Depth 20; $r=Invoke-RestMethod -Method Post -Uri 'http://localhost:8083/api/call/protected_tools_status' -ContentType 'application/json' -Body $body; $r|ConvertTo-Json -Depth 20"]
```

Do not use MCP, `tool_names`, `curl`, search, or token fetch in this step. Do not print any raw token.

Pass condition: response has `ok=true` and either includes protected tool/token policy fields or otherwise confirms protected-tool status without exposing a raw token.

Evidence: `ok=true`, `protected_tools_status_checked=true`, `raw_token_exposed=false`.

If evidence is OK: run exactly one worker `run_commands` action that submits Step 1 and immediately reads Step 2 in the same PowerShell source.

```text
command="powershell.exe"
args=["-NoProfile","-NonInteractive","-Command","$submit=@{name='rawcut_full_r1';step=1;status='done';evidence='ok=true; protected_tools_status_checked=true; raw_token_exposed=false';project_path='D:\0. TRIEN\1. DATA\DATA\5. VIBE APP\1. APP\8. ToolA\project-orchestrator'}; $body=$submit|ConvertTo-Json -Depth 20; $submitResult=Invoke-RestMethod -Method Post -Uri 'http://localhost:8083/api/call/workflow_submit_step' -ContentType 'application/json' -Body $body; $status=@{name='rawcut_full_r1';project_path='D:\0. TRIEN\1. DATA\DATA\5. VIBE APP\1. APP\8. ToolA\project-orchestrator'}; $body=$status|ConvertTo-Json -Depth 20; $statusResult=Invoke-RestMethod -Method Post -Uri 'http://localhost:8083/api/call/workflow_status' -ContentType 'application/json' -Body $body; @{submit=$submitResult;status=$statusResult}|ConvertTo-Json -Depth 30"]
```

Never replace `name` with `workflow`. Do not stop after submit. Do not use `submit.current_step_body`. Only `status.current_step_body` is authoritative. After this combined action succeeds, immediately run the returned Step 2 body. Do not stop to ask/report.

Stop if: the exact HTTP call errors or connection is refused. Report `[A] unavailable` and do not retry another port.

## Step 2
### Set fixed paths
Do: set these exact constants.

```text
BOT_ROOT=D:\0. TRIEN\1. DATA\DATA\5. VIBE APP\1. APP\6. Tr_Rawcut_Bot
AA_ROOT=D:\0. TRIEN\1. DATA\DATA\5. VIBE APP\1. APP\6. Tr_Rawcut_Bot\4. Rawcut_full
LIVE_NAME=AutoCutter
```

Do not use `tool_names`, `workflow_list`, or recursive search to find Rawcut paths.

Evidence: `BOT_ROOT`, `AA_ROOT`, `LIVE_NAME`.

If evidence is OK: run exactly one worker `run_commands` action that submits Step 2 and immediately reads Step 3 in the same PowerShell source.

```text
command="powershell.exe"
args=["-NoProfile","-NonInteractive","-Command","$submit=@{name='rawcut_full_r1';step=2;status='done';evidence='BOT_ROOT=D:\0. TRIEN\1. DATA\DATA\5. VIBE APP\1. APP\6. Tr_Rawcut_Bot; AA_ROOT=D:\0. TRIEN\1. DATA\DATA\5. VIBE APP\1. APP\6. Tr_Rawcut_Bot\4. Rawcut_full; LIVE_NAME=AutoCutter';project_path='D:\0. TRIEN\1. DATA\DATA\5. VIBE APP\1. APP\8. ToolA\project-orchestrator'}; $body=$submit|ConvertTo-Json -Depth 20; $submitResult=Invoke-RestMethod -Method Post -Uri 'http://localhost:8083/api/call/workflow_submit_step' -ContentType 'application/json' -Body $body; $status=@{name='rawcut_full_r1';project_path='D:\0. TRIEN\1. DATA\DATA\5. VIBE APP\1. APP\8. ToolA\project-orchestrator'}; $body=$status|ConvertTo-Json -Depth 20; $statusResult=Invoke-RestMethod -Method Post -Uri 'http://localhost:8083/api/call/workflow_status' -ContentType 'application/json' -Body $body; @{submit=$submitResult;status=$statusResult}|ConvertTo-Json -Depth 30"]
```

Do not stop after submit. Only `status.current_step_body` is authoritative. After this combined action succeeds, immediately run the returned Step 3 body. Do not stop to ask/report.

## Step 3
### Ask input data
Ask Anh for input data if the current prompt did not already provide it. Ask only these fields:

```text
N = number of projects
For each project i:
SHEET_LINK_i
ROUND_ROOT_i
PREMIERE_FILE_NAME_i
```

Do not verify sheet links. If get-footage/data prep later fails, report the `[Aa]/[Ae]` error.

Evidence: `input_source=user_prompt|asked_Anh`, `N`, and every input item with the full, untruncated exact values: `P_i | SHEET_LINK | ROUND_ROOT | PREMIERE_FILE_NAME`. Never shorten links/IDs with `...`; later Broker steps require the full strings.

If evidence is OK: run exactly one worker `run_commands` action that submits Step 3 and immediately reads Step 4 in the same PowerShell source.

```text
command="powershell.exe"
args=["-NoProfile","-NonInteractive","-Command","$submit=@{name='rawcut_full_r1';step=3;status='done';evidence='input_source=user_prompt; N=2; P1 SHEET_LINK=https://docs.google.com/spreadsheets/d/1Dukdqo58GxeyDFq09oKsgms7LMWNZbpb7ykN8tyUw8w/edit?gid=0#gid=0; P1 ROUND_ROOT=D:\0. TRIEN\2. PROJECT\402. H. 615a; P1 PREMIERE_FILE_NAME=402. H. 615a.prproj; P2 SHEET_LINK=https://docs.google.com/spreadsheets/d/1OJiBBUGphtpGYbtJe00TGOyPihu_hUrNSjROjhnqWMM/edit?gid=0#gid=0; P2 ROUND_ROOT=D:\0. TRIEN\2. PROJECT\403. H. 617a; P2 PREMIERE_FILE_NAME=403. H. 617a.prproj';project_path='D:\0. TRIEN\1. DATA\DATA\5. VIBE APP\1. APP\8. ToolA\project-orchestrator'}; $body=$submit|ConvertTo-Json -Depth 20; $submitResult=Invoke-RestMethod -Method Post -Uri 'http://localhost:8083/api/call/workflow_submit_step' -ContentType 'application/json' -Body $body; $status=@{name='rawcut_full_r1';project_path='D:\0. TRIEN\1. DATA\DATA\5. VIBE APP\1. APP\8. ToolA\project-orchestrator'}; $body=$status|ConvertTo-Json -Depth 20; $statusResult=Invoke-RestMethod -Method Post -Uri 'http://localhost:8083/api/call/workflow_status' -ContentType 'application/json' -Body $body; @{submit=$submitResult;status=$statusResult}|ConvertTo-Json -Depth 30"]
```

Do not stop after submit. Only `status.current_step_body` is authoritative. After this combined action succeeds, immediately run the returned Step 4 body. Do not stop to ask/report.

## Step 4
### Build project list
Do: build `projects[]` in user order. Required per item: `SHEET_LINK`, `ROUND_ROOT`, `PREMIERE_FILE_NAME`; assign `P1..PN`.

Normalize `PREMIERE_FILE_NAME`:

```text
If PREMIERE_FILE_NAME already ends with .prproj: keep it.
If PREMIERE_FILE_NAME does not end with .prproj: append .prproj.
Do not ask Anh for PREMIERE_FILE_PATH.
```

For each project compute:

```text
PREMIERE_PROJECT_PATH=<ROUND_ROOT>\3. WORKING\<normalized PREMIERE_FILE_NAME>
SUMMARY_PATH=<P1 ROUND_ROOT>\2. DATA\Rawcut_Full\batch_SUMMARY.json
```

Do not check whether `PREMIERE_PROJECT_PATH` exists at this step. `[Aa]` owns project creation/opening after CA fills input and starts `run-full`.

Evidence: `N`, and for each project the full, untruncated exact values: `idx | SHEET_LINK | normalized PREMIERE_FILE_NAME | ROUND_ROOT | PREMIERE_PROJECT_PATH`; plus full `SUMMARY_PATH`. Never use `...` in evidence.

If evidence is OK: run exactly one worker `run_commands` action that submits Step 4 and immediately reads Step 5 in the same PowerShell source.

```text
command="powershell.exe"
args=["-NoProfile","-NonInteractive","-Command","$submit=@{name='rawcut_full_r1';step=4;status='done';evidence='N=2; P1 PREMIERE_PROJECT_PATH=D:\0. TRIEN\2. PROJECT\402. H. 615a\3. WORKING\402. H. 615a.prproj; P2 PREMIERE_PROJECT_PATH=D:\0. TRIEN\2. PROJECT\403. H. 617a\3. WORKING\403. H. 617a.prproj; SUMMARY_PATH=D:\0. TRIEN\2. PROJECT\402. H. 615a\2. DATA\Rawcut_Full\batch_SUMMARY.json';project_path='D:\0. TRIEN\1. DATA\DATA\5. VIBE APP\1. APP\8. ToolA\project-orchestrator'}; $body=$submit|ConvertTo-Json -Depth 20; $submitResult=Invoke-RestMethod -Method Post -Uri 'http://localhost:8083/api/call/workflow_submit_step' -ContentType 'application/json' -Body $body; $status=@{name='rawcut_full_r1';project_path='D:\0. TRIEN\1. DATA\DATA\5. VIBE APP\1. APP\8. ToolA\project-orchestrator'}; $body=$status|ConvertTo-Json -Depth 20; $statusResult=Invoke-RestMethod -Method Post -Uri 'http://localhost:8083/api/call/workflow_status' -ContentType 'application/json' -Body $body; @{submit=$submitResult;status=$statusResult}|ConvertTo-Json -Depth 30"]
```

Do not stop after submit. Only `status.current_step_body` is authoritative. After this combined action succeeds, immediately run the returned Step 5 body. Do not stop to ask/report.

## Step 5
### Verify Aa live build
Run exactly two `[A]` HTTP calls, in this order, using the global PowerShell HTTP recipe:

1. `POST http://localhost:8083/api/call/cep_manifest_validate` with body:
```text
@{ project_path='D:\0. TRIEN\1. DATA\DATA\5. VIBE APP\1. APP\6. Tr_Rawcut_Bot\4. Rawcut_full' }
```
2. `POST http://localhost:8083/api/call/cep_extension_status` with body:
```text
@{ project_path='D:\0. TRIEN\1. DATA\DATA\5. VIBE APP\1. APP\6. Tr_Rawcut_Bot\4. Rawcut_full'; live_name='AutoCutter' }
```

Do not call `tool_names`, `workflow_list`, `search_codebase`, `read_files`, or pass `BOT_ROOT` to either check.

Evidence: `project_path_checked=AA_ROOT`, `manifest_valid`, `com.auto.cutter.full in ExtensionList`, `live_in_sync`, `missing_count`.

If evidence is OK: run exactly one worker `run_commands` action that submits Step 5 and immediately reads Step 6 in the same PowerShell source.

```text
command="powershell.exe"
args=["-NoProfile","-NonInteractive","-Command","$submit=@{name='rawcut_full_r1';step=5;status='done';evidence='project_path_checked=AA_ROOT; manifest_valid=true; com.auto.cutter.full in ExtensionList=true; live_in_sync=true; missing_count=0';project_path='D:\0. TRIEN\1. DATA\DATA\5. VIBE APP\1. APP\8. ToolA\project-orchestrator'}; $body=$submit|ConvertTo-Json -Depth 20; $submitResult=Invoke-RestMethod -Method Post -Uri 'http://localhost:8083/api/call/workflow_submit_step' -ContentType 'application/json' -Body $body; $status=@{name='rawcut_full_r1';project_path='D:\0. TRIEN\1. DATA\DATA\5. VIBE APP\1. APP\8. ToolA\project-orchestrator'}; $body=$status|ConvertTo-Json -Depth 20; $statusResult=Invoke-RestMethod -Method Post -Uri 'http://localhost:8083/api/call/workflow_status' -ContentType 'application/json' -Body $body; @{submit=$submitResult;status=$statusResult}|ConvertTo-Json -Depth 30"]
```

Do not stop after submit. Only `status.current_step_body` is authoritative. After this combined action succeeds, immediately run the returned Step 6 body. Do not stop to ask/report.

Stop if: manifest invalid, missing `com.auto.cutter.full`, or live out-of-sync when checked with `AA_ROOT`.

## Step 6
### Check Premiere not already running
Run exactly one `[A]` HTTP call using the global PowerShell HTTP recipe:

```text
POST http://localhost:8083/api/call/desktop_window_list
body=@{ title_filter='Premiere' }
```

Set `premiere_preexisting=true` only if the response contains an Adobe Premiere Pro/Premiere window that represents a pre-existing user session. Record returned titles.

Evidence: `premiere_preexisting`, `open_window_titles`.

If `premiere_preexisting=false`: run exactly one worker `run_commands` action that submits Step 6 and immediately reads Step 7 in the same PowerShell source.

```text
command="powershell.exe"
args=["-NoProfile","-NonInteractive","-Command","$submit=@{name='rawcut_full_r1';step=6;status='done';evidence='premiere_preexisting=false; open_window_titles=[]';project_path='D:\0. TRIEN\1. DATA\DATA\5. VIBE APP\1. APP\8. ToolA\project-orchestrator'}; $body=$submit|ConvertTo-Json -Depth 20; $submitResult=Invoke-RestMethod -Method Post -Uri 'http://localhost:8083/api/call/workflow_submit_step' -ContentType 'application/json' -Body $body; $status=@{name='rawcut_full_r1';project_path='D:\0. TRIEN\1. DATA\DATA\5. VIBE APP\1. APP\8. ToolA\project-orchestrator'}; $body=$status|ConvertTo-Json -Depth 20; $statusResult=Invoke-RestMethod -Method Post -Uri 'http://localhost:8083/api/call/workflow_status' -ContentType 'application/json' -Body $body; @{submit=$submitResult;status=$statusResult}|ConvertTo-Json -Depth 30"]
```

Do not stop after submit. Only `status.current_step_body` is authoritative. After this combined action succeeds, immediately run the returned Step 7 body. Do not stop to ask/report.

Stop if: `premiere_preexisting=true`. Ask Anh and wait; do not close or save anything in this step.

## Step 7
### Create Premiere bridge script
Create exactly these two deterministic paths:

```text
bat_path=D:\temp_rawcut\launch_premiere.bat
log_path=D:\temp_rawcut\launch_premiere.log
```

Run exactly one worker `run_commands` action with `command="powershell.exe"` and `args=["-NoProfile","-NonInteractive","-Command", <PowerShell source below as the single final arg>]`.

PowerShell source:
```powershell
New-Item -ItemType Directory -Path 'D:\temp_rawcut' -Force | Out-Null
$batLines = @(
  '@echo off',
  'cd /d "D:\0. TRIEN\1. DATA\DATA\5. VIBE APP\1. APP\6. Tr_Rawcut_Bot\4. Rawcut_full"',
  'node tools\ade_bridge_client.js launch-premiere'
)
[System.IO.File]::WriteAllLines('D:\temp_rawcut\launch_premiere.bat', $batLines, [System.Text.ASCIIEncoding]::new())
if(!(Test-Path 'D:\temp_rawcut\launch_premiere.bat')){throw 'bat_missing'}
Write-Output 'bat_path=D:\temp_rawcut\launch_premiere.bat'
Write-Output 'log_path=D:\temp_rawcut\launch_premiere.log'
Write-Output 'bat_created=true'
```

Do not use editor, raw shell redirection, helper files beyond the required `.bat`, or any path discovery.

Evidence: `bat_path=D:\temp_rawcut\launch_premiere.bat`, `log_path=D:\temp_rawcut\launch_premiere.log`, `bat_created=true`.

If evidence is OK: submit Step 7 as `done`, then immediately read and run Step 8. Do not stop to ask/report.

## Step 8
### Run Premiere bridge script
Run exactly one worker `run_commands` action with `command="powershell.exe"` and this single `-Command` source. Do not call `[A] run_ps1_script` in this step. Do not create helper `.ps1` files. Do not retry with a different quoting strategy.

```text
command="powershell.exe"
args=["-NoProfile","-NonInteractive","-Command","$bat='D:\temp_rawcut\launch_premiere.bat'; $log='D:\temp_rawcut\launch_premiere.log'; $readyFile=Join-Path $env:TEMP 'tr_rawcut_bridge\ade_ready.json'; if(!(Test-Path $bat)){throw 'launch_bat_missing'}; if(Test-Path $readyFile){Remove-Item -LiteralPath $readyFile -Force}; $launch=& $bat 2>&1; Set-Location 'D:\0. TRIEN\1. DATA\DATA\5. VIBE APP\1. APP\6. Tr_Rawcut_Bot\4. Rawcut_full'; $ready=& node tools\ade_bridge_client.js ready --wait --timeout-ms 28000 2>&1; $lines=@($launch)+@($ready); $lines|Set-Content -LiteralPath $log -Encoding UTF8; $lines; Write-Output ('ready_file_reset=' + $readyFile); Write-Output ('bat_path=' + $bat); Write-Output ('log_path=' + $log); Write-Output 'run_commands_ok=true'"]

Read the returned stdout only. Continue only if it shows fresh ready evidence from `ready --wait` and no command error. `project_loaded=false` or `sequences_loaded=0` is diagnostic only here.

Evidence: `premiere_launch_ok`, `ready_file_reset`, `bridge_ready_fresh`, `ready_age_ms`, `script_version`, `pending_job_exists`, `project_loaded`, `sequences_loaded`, `bat_path`, `log_path`, `run_commands_ok=true`.

If evidence is OK: submit Step 8 as `done`, then immediately read and run Step 9. Do not stop to ask/report.

Stop if: the exact direct `run_commands` call fails or `bridge_ready_fresh=false` after the 28000ms wait. Report `STEP8_BRIDGE_READY_NOT_FRESH` with `ready_age_ms`, `premiere_launch_ok`, `bat_path`, and `log_path`. Do not ask Anh for retry.

## Step 9
### Clear blocking Premiere dialogs
Run exactly one worker `run_commands` action with `command="powershell.exe"` and this single `-Command` source. Do not call `[A] run_ps1_script` in this step. Do not create helper `.ps1` files. Do not retry with a different quoting strategy.

```text
command="powershell.exe"
args=["-NoProfile","-NonInteractive","-Command","Set-Location 'D:\0. TRIEN\1. DATA\DATA\5. VIBE APP\1. APP\6. Tr_Rawcut_Bot\4. Rawcut_full'; node tools\ade_bridge_client.js snapshot; Write-Output 'run_commands_ok=true'"]
```

Classify windows deterministically: visible `#32770` is blocking. `DroverLord - Window Class` is blocking only if it is a distinct small/meaningful-title dialog. A full-client-size empty-title DroverLord owned/parented by Premiere is normal shell, not blocking.

If a confirmed reopen/recovery prompt is present, run exactly one more direct worker `run_commands` action with `command="powershell.exe"` and source: `Set-Location 'D:\0. TRIEN\1. DATA\DATA\5. VIBE APP\1. APP\6. Tr_Rawcut_Bot\4. Rawcut_full'; node tools\ade_bridge_client.js dialog-click --key enter; node tools\ade_bridge_client.js snapshot; Write-Output 'run_commands_ok=true'`. For any other confirmed blocking dialog, use `--key esc` once. Do not send keys to a normal full-client-size empty-title DroverLord shell.

Evidence: `snapshot_ok`, `dialog_class`, `dialog_title`, `key_sent`, `dialog_cleared=true|false`, `run_commands_ok=true`.

If no blocking dialog OR the dialog was cleared: run exactly one worker `run_commands` action that submits Step 9 and immediately reads Step 10 in the same PowerShell source.

```text
command="powershell.exe"
args=["-NoProfile","-NonInteractive","-Command","$submit=@{name='rawcut_full_r1';step=9;status='done';evidence='snapshot_ok=true; no_blocking_dialog=true; dialog_cleared=true; run_commands_ok=true';project_path='D:\0. TRIEN\1. DATA\DATA\5. VIBE APP\1. APP\8. ToolA\project-orchestrator'}; $body=$submit|ConvertTo-Json -Depth 20; $submitResult=Invoke-RestMethod -Method Post -Uri 'http://localhost:8083/api/call/workflow_submit_step' -ContentType 'application/json' -Body $body; $status=@{name='rawcut_full_r1';project_path='D:\0. TRIEN\1. DATA\DATA\5. VIBE APP\1. APP\8. ToolA\project-orchestrator'}; $body=$status|ConvertTo-Json -Depth 20; $statusResult=Invoke-RestMethod -Method Post -Uri 'http://localhost:8083/api/call/workflow_status' -ContentType 'application/json' -Body $body; @{submit=$submitResult;status=$statusResult}|ConvertTo-Json -Depth 30"]
```

Do not stop after submit. Only `status.current_step_body` is authoritative. After this combined action succeeds, immediately run the returned Step 10 body. Do not stop to ask/report.

Stop if: snapshot call fails or a blocking dialog remains after the allowed single key attempt.

## Step 10
### Create Aa config JSON
Create `D:\temp_rawcut\rawcut_full_config.json` containing an object with one `projects` property whose value is the exact full project array from the current prompt or Step 4 evidence. Required fields per item: `SHEET_LINK`, `ROUND_ROOT`, `PREMIERE_FILE_NAME`. Never write a bare top-level array. Never use truncated values containing `...`.

Run exactly one worker `run_commands` action with `command="powershell.exe"` and `args=["-NoProfile","-NonInteractive","-Command", <PowerShell source below as the single final arg>]`.

PowerShell source:
```powershell
New-Item -ItemType Directory -Path 'D:\temp_rawcut' -Force | Out-Null
$p1=@{SHEET_LINK='https://docs.google.com/spreadsheets/d/1Dukdqo58GxeyDFq09oKsgms7LMWNZbpb7ykN8tyUw8w/edit?gid=0#gid=0';ROUND_ROOT='D:\0. TRIEN\2. PROJECT\402. H. 615a';PREMIERE_FILE_NAME='402. H. 615a.prproj'}
$p2=@{SHEET_LINK='https://docs.google.com/spreadsheets/d/1OJiBBUGphtpGYbtJe00TGOyPihu_hUrNSjROjhnqWMM/edit?gid=0#gid=0';ROUND_ROOT='D:\0. TRIEN\2. PROJECT\403. H. 617a';PREMIERE_FILE_NAME='403. H. 617a.prproj'}
$config=@{projects=@($p1,$p2)}
$json=$config|ConvertTo-Json -Depth 10
[System.IO.File]::WriteAllText('D:\temp_rawcut\rawcut_full_config.json',$json,[System.Text.UTF8Encoding]::new($false))
$parsed=Get-Content 'D:\temp_rawcut\rawcut_full_config.json' -Raw|ConvertFrom-Json
if($null -eq $parsed.projects){throw 'top_level_projects_missing'}
if($parsed.projects.Count -ne 2){throw 'project_count_mismatch'}
foreach($p in $parsed.projects){if($p.SHEET_LINK.Contains('...')){throw 'truncated_sheet_link'}}
Write-Output 'config_path=D:\temp_rawcut\rawcut_full_config.json'
Write-Output 'top_level_projects=true'
Write-Output ('project_count=' + $parsed.projects.Count)
Write-Output 'full_sheet_links_preserved=true'
Write-Output 'runtime_artifact_write_ok=true'
```

Do not use editor, raw shell redirection, helper files, token fetch, regex, or any `[A]` protected call in this step. Stop immediately without retry if the one structured command returns any error.

Evidence: `config_path=D:\temp_rawcut\rawcut_full_config.json`, `top_level_projects=true`, `project_count=2`, `full_sheet_links_preserved=true`, `runtime_artifact_write_ok=true`.

If evidence is OK: submit Step 10 as `done`, then immediately read and run Step 11. Do not stop to ask/report.

## Step 11
### Create Aa start script
Create exactly these two deterministic paths:

```text
bat_path=D:\temp_rawcut\run_rawcut_full.bat
log_path=D:\temp_rawcut\run_rawcut_full.log
config_path=D:\temp_rawcut\rawcut_full_config.json
```

Run exactly one worker `run_commands` action with `command="powershell.exe"` and `args=["-NoProfile","-NonInteractive","-Command", <PowerShell source below as the single final arg>]`.

PowerShell source:
```powershell
$batLines = @(
  '@echo off',
  'cd /d "D:\0. TRIEN\1. DATA\DATA\5. VIBE APP\1. APP\6. Tr_Rawcut_Bot\4. Rawcut_full"',
  'node tools\ade_bridge_client.js run-full --config "D:\temp_rawcut\rawcut_full_config.json" --no-wait',
  'node tools\ade_bridge_client.js full-status'
)
[System.IO.File]::WriteAllLines('D:\temp_rawcut\run_rawcut_full.bat',$batLines,[System.Text.ASCIIEncoding]::new())
if(!(Test-Path 'D:\temp_rawcut\run_rawcut_full.bat')){throw 'bat_missing'}
Write-Output 'bat_path=D:\temp_rawcut\run_rawcut_full.bat'
Write-Output 'log_path=D:\temp_rawcut\run_rawcut_full.log'
Write-Output 'config_path=D:\temp_rawcut\rawcut_full_config.json'
Write-Output 'bat_created=true'
```

Evidence: `bat_path=D:\temp_rawcut\run_rawcut_full.bat`, `log_path=D:\temp_rawcut\run_rawcut_full.log`, `config_path=D:\temp_rawcut\rawcut_full_config.json`, `bat_created=true`.

If evidence is OK: submit Step 11 as `done`, then immediately read and run Step 12. Do not stop to ask/report.

## Step 12
### Submit Aa run
Run exactly one `[A]` HTTP call to `run_ps1_script` using the global PowerShell HTTP recipe.

Tool body:
```text
@{
  project_path='D:\0. TRIEN\1. DATA\DATA\5. VIBE APP\1. APP\8. ToolA\project-orchestrator'
  script_label='rawcut-step12-run-full'
  timeout=180
  return_full_stdout=$true
  script_content=@'
$bat='D:\temp_rawcut\run_rawcut_full.bat'
$log='D:\temp_rawcut\run_rawcut_full.log'
if(!(Test-Path $bat)){throw 'run_bat_missing'}
$lines = & $bat 2>&1
$lines | Set-Content -LiteralPath $log -Encoding UTF8
$lines
Write-Output ('log_path=' + $log)
Write-Output 'surface=run-full'
Write-Output 'run_ps1_script_ok=true'
'@
}
```

Pass if stdout/log shows `run-full` submitted or `full-status` can be read after submission. Record nonce if present.

Forbidden bypass examples: `sheet_scanner.py`, `desktop-backend.js`, `ade_bridge_client.js submit`, `compare-cli.js`.

Evidence: `surface=run-full`, `submitted=true|false`, `nonce if present`, `bridge_ready_fresh`, `log_path`, `run_ps1_script_ok=true`.

If evidence is OK: submit Step 12 as `done`, then immediately read and run Step 13. Do not stop to ask/report.

Stop if: submit itself fails before `full-status` can be read. Report the log excerpt.

## Step 13
### Read initial Aa status
Read the Step 12 log and/or run `full-status` via `.bat` or `[A]` script-file wrapper.

While `full-status.state=running`, `[Aa]` owns Premiere and the shared bridge. Do not run `[Ab]` deploy/reload/restart/manual ADE submit.

Do not treat `bridge_ready=true` alone as valid readiness. Record `bridge_ready_fresh` and `ready_age_ms`; stale ready evidence must branch to Step 14/reload handling or stop, not continue as ready.

Evidence: `state`, `current_stage`, `project_count`, `per-project status`, `bridge_ready_fresh`, `ready_age_ms`, `ownership_lock=[Aa]` if running.

If evidence is recorded: choose `NEXT_STEP` exactly from this table and include it in `workflow_submit_step` payload as `next_step=NEXT_STEP`.

```text
state=queued -> NEXT_STEP=14
state=busy or error=RAWCUT_FULL_BUSY -> NEXT_STEP=15
state=running -> NEXT_STEP=16
state=done or state=failed -> NEXT_STEP=19
```

Submit Step 13 using the global Action S1 shape plus field `next_step=NEXT_STEP`. Then run global Action S2 `workflow_status`. Stop with `DIFF_BRANCH_STEP13` if the returned `current_step` is not `NEXT_STEP`. Do not use `workflow_goto_step`. Do not stop to ask/report.

## Step 14
### Handle queued status
If `full-status` remains `queued` >15s, run exactly once with a `.bat` or `[A]` script-file wrapper:

```bat
@echo off
cd /d "D:\0. TRIEN\1. DATA\DATA\5. VIBE APP\1. APP\6. Tr_Rawcut_Bot\4. Rawcut_full"
node tools\ade_bridge_client.js reload-panel --no-launch
node tools\ade_bridge_client.js full-status
```

Then poll `full-status` every 2s for up to 90s after the reload request. This grace window is required because the panel may consume `rawcut_full_reload.json` and transition from `queued` to `running` after a short delay.

Evidence: `queued_age_ms`, `reload_no_launch=attempted_once`, `post_reload_poll_ms`, `post_reload_poll_count`, `state_after_reload`, `bridge_ready_fresh`, `ready_age_ms`.

If evidence is recorded: choose `NEXT_STEP` exactly from this table and include it in `workflow_submit_step` payload as `next_step=NEXT_STEP`.

```text
state=running -> NEXT_STEP=16
state=busy or error=RAWCUT_FULL_BUSY -> NEXT_STEP=15
state=done or state=failed -> NEXT_STEP=19
```

Submit Step 14 using the global Action S1 shape plus field `next_step=NEXT_STEP`. Then run global Action S2 `workflow_status`. Stop with `DIFF_BRANCH_STEP14` if the returned `current_step` is not `NEXT_STEP`; do not continue to Step 15 merely because it is numerically next. Do not use `workflow_goto_step`. Do not stop to ask/report.

Stop if: `queued` persists after the full 90s post-reload grace window. Run `full-cancel --reason queued_after_reload_grace` before reporting blocked.

## Step 15
### Handle busy status
This step is valid only if the current recorded `full-status` is `busy` or error is `RAWCUT_FULL_BUSY`. If the state is `queued`, `running`, `done`, or `failed`, stop with `DIFF_BRANCH_STEP15_WRONG_ENTRY`; do not submit Step 15.

If `full-status` is `busy` or `RAWCUT_FULL_BUSY`, run once with a `.bat` or `[A]` script-file wrapper:

```bat
@echo off
cd /d "D:\0. TRIEN\1. DATA\DATA\5. VIBE APP\1. APP\6. Tr_Rawcut_Bot\4. Rawcut_full"
node tools\ade_bridge_client.js full-cancel --reason busy_stale
node tools\ade_bridge_client.js full-status
node tools\ade_bridge_client.js reload-panel --no-launch
node tools\ade_bridge_client.js ready
node tools\ade_bridge_client.js run-full --config "<config.json>" --no-wait
node tools\ade_bridge_client.js full-status
```

Do not assume `launch-premiere` can open GUI from `[A]/Node` context. If `bridge_ready_fresh=false` before resubmit, stop and request one user-assisted Premiere/panel open or reload, then re-read `full-status`.

Evidence: `busy_recovery=attempted_once`, `bridge_ready_fresh_before_resubmit`, `ready_after_reload`, `state_after_resubmit`.

If evidence is recorded: choose `NEXT_STEP` exactly from this table and include it in `workflow_submit_step` payload as `next_step=NEXT_STEP`.

```text
state=running -> NEXT_STEP=16
state=queued -> NEXT_STEP=14
state=done or state=failed -> NEXT_STEP=19
```

Submit Step 15 using the global Action S1 shape plus field `next_step=NEXT_STEP`. Then run global Action S2 `workflow_status`. Stop with `DIFF_BRANCH_STEP15` if the returned `current_step` is not `NEXT_STEP`. Do not use `workflow_goto_step`. Do not stop to ask/report.

Stop if: `busy` persists after this one resubmit. Run `full-cancel --reason busy_after_resubmit` before reporting blocked.

## Step 16
### Monitor data prep
Poll using a `.bat`, not an inline shell:

```bat
@echo off
cd /d "D:\0. TRIEN\1. DATA\DATA\5. VIBE APP\1. APP\6. Tr_Rawcut_Bot\4. Rawcut_full"
node tools\ade_bridge_client.js full-status
```

Watch project statuses until each project is `data_ready`, `blocked`, `cutting`, `cut_done`, `diff`, or `done`.

If get-footage fails, record the short `[Aa]/[Ae]` error and continue ready projects.

Evidence per project: `data_ready`, `cep_trim_count`, `footage_count`, `blocked_error`.

If evidence is OK or ready projects remain: submit Step 16 as `done`, then immediately read and run Step 17. Do not stop to ask/report.

Stop if: status shows no progress beyond command timeout. First run `node tools/ade_bridge_client.js snapshot` and clear any blocking dialog (reopen -> `dialog-click --key enter`, otherwise `--key esc`); if still no progress, run `full-cancel --reason data_prep_timeout` before reporting blocked.

## Step 17
### Monitor Premiere validate/cut
Let `[Aa]` validate/cut ready projects sequentially. `[Aa]` itself verifies bridge before each ADE submit and retries one bridge recovery.

CA only observes `full-status` and logs. Do not submit ADE jobs directly.

Evidence per project while cutting: `stage`, `current_stage`, `bridge_recover if shown`, `ADE_error if shown`.

If evidence is OK: submit Step 17 as `done`, then immediately read and run Step 18. Do not stop to ask/report.

Stop if: any attempt to run multiple Premiere validate/cut jobs in parallel.

## Step 18
### Verify per-project cut evidence
After a project reaches cut done or blocked, verify evidence from `[Aa]` status/report paths.

For each project record:

```text
prproj_exists=<PREMIERE_PROJECT_PATH from Step 4>
cep_trim_count=<count from status/report>
OK_STT_count=<count from status/report>
cut_expected=<count from status/report>
ADE_error=<short error or empty>
```

Evidence per project: `PREMIERE_PROJECT_PATH`, `prproj_exists`, `cep_trim_count`, `OK_STT_count`, `cut_expected`, `ADE_error`.

If evidence is OK or blocked reason is recorded: submit Step 18 as `done`, then immediately read and run Step 19. Do not stop to ask/report.

Stop if: a project remains `CUT_FAILED` after `[Aa]` bridge recovery/retry; do not bypass with direct `ade_bridge_client.js submit`.

## Step 19
### Wait for diff and done state
Wait until `[Aa]` finishes compare and cleanup after all non-blocked projects finish.

Continue if `full-status.state=done` and `ok=true`, or if state is `failed` with per-project blocked reasons available.

Evidence: `state`, `ok`, `summary path if present`, per-project `verdict|blocked`.

If evidence is OK or failed state has per-project blocked reasons: submit Step 19 as `done`, then immediately read and run Step 20. Do not stop to ask/report.

Stop if: cleanup requested while `[Aa]` still running.

## Step 20
### Verify final reports from JSON
Run exactly one `[A]` HTTP call to `run_ps1_script` using the global PowerShell HTTP recipe. Do not use native `read_files`, editor, raw filesystem read, or non-[A] file tools.

Tool body:
```text
@{
  project_path='D:\0. TRIEN\1. DATA\DATA\5. VIBE APP\1. APP\8. ToolA\project-orchestrator'
  script_label='rawcut-step20-read-summary'
  timeout=60
  return_full_stdout=$true
  script_content=@'
$summary='D:\0. TRIEN\2. PROJECT\402. H. 615a\2. DATA\Rawcut_Full\batch_SUMMARY.json'
if(Test-Path $summary){
  $json=Get-Content -LiteralPath $summary -Raw | ConvertFrom-Json
  Write-Output ('summary_path=' + $summary)
  Write-Output 'summary_exists=true'
  Write-Output 'json_valid=true'
  foreach($p in $json.projects){
    Write-Output ('project=' + $p.idx + '; reports.txt_exists=' + $p.reports.txt_exists + '; reports.png_exists=' + $p.reports.png_exists + '; verdict=' + $p.verdict + '; blocked=' + $p.blocked)
  }
}else{
  Write-Output ('summary_path=' + $summary)
  Write-Output 'summary_exists=false'
  Write-Output 'json_valid=false'
  Write-Output 'reports_unavailable_due_to_blocked=true'
}
Write-Output 'run_ps1_script_ok=true'
'@
}
```

Evidence from stdout: `summary_path`, `summary_exists`, `json_valid`, per-project `reports.txt_exists`, `reports.png_exists`, `verdict|blocked` if JSON exists, or `reports_unavailable_due_to_blocked=true` if Step 19 state was `failed` and every project had a blocked reason.

If JSON exists and evidence is OK: submit Step 20 as `done`, then immediately read and run Step 21. If JSON is missing but Step 19 evidence showed all projects blocked, submit Step 20 as `done` with evidence `summary_exists=false; reports_unavailable_due_to_blocked=true`, then immediately read and run Step 21. Do not stop to ask/report.

Stop if: JSON is missing and Step 19 did not prove all projects blocked, JSON is invalid, or a non-blocked project has `reports.txt_exists=false` or `reports.png_exists=false`.

## Step 21
### Verify cleanup from JSON
Use Step 20 stdout only. Do not use native `read_files`, editor, raw filesystem read, or non-[A] file tools.

Evidence per project if JSON exists: `cleanup.scratch_remaining`, `cleanup.scratch_examples`, `cleanup_done`.

If Step 20 evidence has `summary_exists=false` and `reports_unavailable_due_to_blocked=true`, record cleanup evidence as `cleanup_not_applicable_all_projects_blocked=true`; this is valid for Step 21.

If evidence is OK: submit Step 21 as `done`, then immediately read and run Step 22. Do not stop to ask/report.

Stop if: cleanup check is attempted while `[Aa]` still running, JSON is missing without `reports_unavailable_due_to_blocked=true`, or any project has `cleanup.scratch_remaining>0`.

## Step 22
### Read batch summary JSON
Use Step 20 stdout only. Do not use native `read_files`, editor, raw filesystem read, or non-[A] file tools.

If Step 20 evidence has `summary_exists=true`, evidence must include: `summary_path`, `schema=rawcut_full.batch_summary.v1`, `total=N`, each input appears once, each project has verdict or blocked reason.

If Step 20 evidence has `summary_exists=false` and `reports_unavailable_due_to_blocked=true`, evidence must include: `summary_path`, `schema=not_available_summary_missing`, `total=2`, `each_project_blocked_reason_from_step19=true`.

If evidence is OK: submit Step 22 as `done`, then immediately read and run Step 23. Do not stop to ask/report.

Stop if: Step 20 did not provide either valid JSON evidence or `reports_unavailable_due_to_blocked=true`.

## Step 23
### Build short report
Prepare final table from `[Aa]/[Ae]` outputs only.

Columns:

```text
idx/name | footage missing/error | cut missing/OK_STT shortfall | verdict | special_case
```

Rules:

```text
If no footage missing/error: write OK
If no cut shortfall: write OK
If no special case: write none
Do not include raw JSON unless Anh asks
```

Evidence: `table_draft`, `issue_count`.

If evidence is OK: submit Step 23 as `done`, then immediately read and run Step 24. Do not stop to ask/report.

## Step 24
### Verify safe close condition
Before closing Premiere, verify all:

```text
full-status.state != running
pending_job_exists=false from ready/status snapshot
premiere_preexisting=false from Step 6
```

Also verify no non-input project is known to be open. If uncertain, leave Premiere open.

Also verify every loaded/target project path known to `[Aa]` is one of the `PREMIERE_PROJECT_PATH` values computed in Step 4. If any loaded or active Premiere project is not in the input batch, leave Premiere open.

Evidence: `full_status_state`, `pending_job_exists`, `premiere_preexisting`, `target_project_count`, `loaded_projects_all_in_batch=true|false`, `safe_to_close=true|false`, `reason_if_not_safe`.

If evidence is OK: submit Step 24 as `done`, then immediately read and run Step 25. Do not stop to ask/report.

Stop if: `premiere_preexisting=true`. Leave Premiere open and set `safe_to_close=false`; CA did not launch a clean instance and cannot guarantee other open projects are safe to close.

## Step 25
### Save active project before close
Do: save the active Premiere project before any close, through an `[A]` script-file wrapper:

```text
node tools/ade_bridge_client.js save-active --timeout-ms 60000
```

This calls `app.project.save()` on the active project only. Premiere ExtendScript cannot enumerate every open project; `[Aa]` already saves each batch project right after its cut, so combined with this save the batch projects are persisted.

Do not: close Premiere in this step. Do not force-kill. Do not run `save-active` while `full-status.state=running`.

Evidence: `save_active_ok`, `saved_project_path`.

If `save_active_ok=true`: submit Step 25 as `done`, then immediately read and run Step 26. Do not stop to ask/report.

Stop if: `save-active` fails or times out. Report the error and leave Premiere open.

## Step 26
### Close Premiere if safe
If Step 24 says `safe_to_close=true`, close only the Premiere instance/window that owns the workflow target projects by normal window close using a `.ps1` file. Do not use inline PowerShell with `$` variables.

`.ps1` content:

```powershell
Get-Process "Adobe Premiere Pro" -ErrorAction SilentlyContinue | ForEach-Object { $_.CloseMainWindow() | Out-Null }
```

If Step 24 says not safe, leave Premiere open.

Evidence: `Premiere_closed=true|false`, `closed_target_project_count`, `reason_if_open`.

If evidence is OK: submit Step 26 as `done`, then immediately read and run Step 27. Do not stop to ask/report.

Stop if: closing requires force-kill while unrelated/unsaved project may exist.

## Step 27
### Build final response draft
Prepare the final response draft, but do not send it until Step 29 is complete.

Use this format:

```text
total=<N> completed=<count> blocked=<count> review_needed=<count>
summary_json=<SUMMARY_PATH>
reports:
P1 <txt path> | <png path>
P2 <txt path> | <png path>
issues:
- <short issue or none>
Premiere_closed=<true|false> reason=<short reason>
round_note=<summary line or unavailable>
```

If MCP transport fails while closing the session, use `[A]` HTTP API with token. For mutation call `GET /api/token` and send `X-Orchestrator-Token`. Do not retry broken MCP after HTTP succeeds.

Evidence: `final_draft_ready=true`.

If evidence is OK: submit Step 27 as `done`, then immediately read and run Step 28. Do not stop to ask/report.

## Step 28
### Close token free
Close token-free/token-auth surface after all required `[A]` work is complete.

If Step 1 only used MCP `call_tool(name="protected_tools_status", payload={})`, no raw token was opened; record `token_free_close=not_required`.

If CA opened any `[A]` token/free-token UI manually, close that UI now. Do not print, paste, or report any raw token.

Evidence: `token_free_close=closed|not_required`, `raw_token_exposed=false`.

If evidence is OK: submit Step 28 as `done`, then immediately read and run Step 29. Do not stop to ask/report.

Stop if: closing token/free-token UI would interrupt a pending `[A]` mutation. Finish the mutation first, then close it.

## Step 29
### Restart workflow in workflow_editor
Restart `rawcut_full_r1` inside `[A]` `workflow_editor` so the next run starts from Step 1.

Backend equivalent if the workflow editor action is exposed through `[A]` tools:

```text
call_tool(name="workflow_reset", payload={"name":"rawcut_full_r1","project_path":"D:\\0. TRIEN\\1. DATA\\DATA\\5. VIBE APP\\1. APP\\8. ToolA\\project-orchestrator"})
```

Do not use `workflow_list` or search to find the workflow. Do not start a new production run unless Anh explicitly asks.

Evidence: `workflow_editor_restart=true`, `workflow_name=rawcut_full_r1`, `current_step=1`, `reset_id`.

Do not submit Step 29 after reset. Reset is the terminal operation. After evidence is OK, send the final response from Step 27 and close the `[A]` session if required.
