@echo off
setlocal enableextensions enabledelayedexpansion
REM A2 (F4): set TOOLA_NO_PAUSE unconditionally so no automated path blocks on pause.
set "TOOLA_NO_PAUSE=1"
echo [*] Khoi dong Orchestrator moi...

set "KILL_PY="
if exist "%LOCALAPPDATA%\Programs\Python\Python314\python.exe" set "KILL_PY=%LOCALAPPDATA%\Programs\Python\Python314\python.exe"
if not defined KILL_PY if exist "%LOCALAPPDATA%\Programs\Python\Python310\python.exe" set "KILL_PY=%LOCALAPPDATA%\Programs\Python\Python310\python.exe"
if not defined KILL_PY if exist "%LOCALAPPDATA%\Python\bin\python.exe" set "KILL_PY=%LOCALAPPDATA%\Python\bin\python.exe"
if not defined KILL_PY if exist "%~dp0venv312\Scripts\python.exe" set "KILL_PY=%~dp0venv312\Scripts\python.exe"
if not defined KILL_PY set "KILL_PY=python.exe"
set "CLI_PY=%~dp0venv312\Scripts\python.exe"
if not exist "!CLI_PY!" set "CLI_PY=!KILL_PY!"
set "CLI_PYW=%~dp0venv312\Scripts\pythonw.exe"
if not exist "!CLI_PYW!" set "CLI_PYW=pythonw.exe"
if not defined TOOLA_AS_LOG set "TOOLA_AS_LOG=%TEMP%\toola_as.log"
set "PIDFILE=%TEMP%\toola_as_listen_pid.txt"

set "TASK_ORCH=ToolA_Orchestrator"
set "TASK_FORCE=ToolA_ForceRestart"
set "TASK_SUP=ToolA_RestartControl"

REM Hidden pythonw helpers replace console-subsystem children
REM so CREATE_NO_WINDOW on the HTTP helper is not undone by visible consoles.
pushd "%~dp0"
"!CLI_PYW!" -m orchestrator.lifecycle.as_bat_helpers schtasks-query "%TASK_ORCH%"
set "ORCH_TASK_RC=!ERRORLEVEL!"
popd
if not "!ORCH_TASK_RC!"=="0" goto INLINE

echo [*] Phat hien task '%TASK_ORCH%' - chay High IL qua Task Scheduler...

REM Anh 2026-08-11: capture the current :8083 owner PID up front so we can PROVE the
REM instance was actually replaced. At Medium-IL (a CA launching [A:S]) the taskkill of
REM the OLD High-IL pythonw fails with Access-denied, yet /livez still answers on the
REM surviving old instance -> the restart would report a false success. We detect that
REM by comparing the :8083 owner PID before/after and fail loud with clear guidance.
set "PID_BEFORE="
pushd "%~dp0"
"!CLI_PYW!" -m orchestrator.lifecycle.as_bat_helpers get-listen-pid 8083 --out "!PIDFILE!"
popd
if exist "!PIDFILE!" for /f "usebackq delims=" %%p in ("!PIDFILE!") do set "PID_BEFORE=%%p"
echo [*] Instance cu PID_BEFORE=!PID_BEFORE!

REM KHONG Disable/Stop ToolA_RestartControl. Path A can :8085 song.

REM ================================ Path A: signed POST :8085 via CLI ================================
echo [*] Path A: request_orchestrator_restart.py -^> 127.0.0.1:8085 ...
pushd "%~dp0"
"!CLI_PYW!" -m orchestrator.lifecycle.request_orchestrator_restart --timeout 90
set "CLI_RC=!ERRORLEVEL!"
popd
if "!CLI_RC!"=="0" (
    echo [OK] Path A CLI verified. Chung minh livez + PID.
    goto PROVE_LIVE
)
if "!CLI_RC!"=="3" (
    echo [LOI] Path A RESTART_GUARD_BLOCKED. Khong fall-through ForceRestart.
    goto FAIL_GUARD
)
echo [WARN] Path A skip/fail rc=!CLI_RC! ^(2=8085/secret/livez, 1=chua verify^). Fall through Path B.

REM ================================ Path B: High-IL ForceRestart task ================================
:PATH_B
pushd "%~dp0"
"!CLI_PYW!" -m orchestrator.lifecycle.as_bat_helpers schtasks-query "%TASK_FORCE%"
set "FORCE_TASK_RC=!ERRORLEVEL!"
popd
if not "!FORCE_TASK_RC!"=="0" (
    echo [WARN] Task '%TASK_FORCE%' chua cai. Fall through Path C.
    goto PATH_C
)
echo [*] Path B: schtasks /run '%TASK_FORCE%' ^(High-IL stop-gate + /run Orchestrator^)...
pushd "%~dp0"
"!CLI_PYW!" -m orchestrator.lifecycle.as_bat_helpers schtasks-run "%TASK_FORCE%"
set "FORCE_RUN_RC=!ERRORLEVEL!"
popd
if not "!FORCE_RUN_RC!"=="0" (
    echo [WARN] Path B /run '%TASK_FORCE%' fail. Fall through Path C.
    goto PATH_C
)
echo [*] Cho instance moi sau ForceRestart ^(PID khac PID_BEFORE=!PID_BEFORE!^)...
goto WAIT_NEW_PID

REM ================================ Path C: /end + Ready + stop-gate + /run retry ================================
:PATH_C
REM A2 (F1): KHONG quiesce supervisor. /end ToolA_Orchestrator only.
pushd "%~dp0"
"!CLI_PYW!" -m orchestrator.lifecycle.as_bat_helpers schtasks-end "%TASK_ORCH%"
popd
REM A2 (F2): poll task state to Ready using non-localized TaskState enum (max ~10s).
echo [*] Cho task '%TASK_ORCH%' tro ve Ready ^(locale-independent^)...
pushd "%~dp0"
"!CLI_PYW!" -m orchestrator.lifecycle.as_bat_helpers wait-task-state "%TASK_ORCH%" Ready --timeout-s 10
set "READY_RC=!ERRORLEVEL!"
popd
if not "!READY_RC!"=="0" (
    echo [WARN] Task '%TASK_ORCH%' khong tro ve Ready sau 10s ^(hung pythonw / task van Running^).
    echo [WARN] KHONG fail-closed: [A:S] restart bat ke hung task. stop-gate + /run retry se kill instance cu.
    goto AFTER_READY
)
echo [OK] Task '%TASK_ORCH%' da Ready.
:AFTER_READY

REM ================================ pre-run stop gate (best-effort, NON-FATAL) ================================
REM Anh 2026-08-11: this pre-run gate runs at the caller's integrity level (Medium-IL
REM when a CA launches [A:S]). Its taskkill on the OLD High-IL pythonw returns Access
REM denied, so the gate reported clear=False and FAIL_TASK'd BEFORE reaching schtasks
REM /run — but /run -> high_il.cmd runs at HIGH-IL and its OWN strict stop gate kills
REM the old instance reliably before launching. So a non-clear result here is NOT
REM fatal: log it and proceed to /run, which does the authoritative High-IL kill.
echo [*] Pre-run stop gate ^(best-effort, caller IL^): PID + 8083 + 8084 + instance byte-lock...
pushd "%~dp0"
"!CLI_PYW!" -m orchestrator.lifecycle.as_bat_helpers run-hidden --log "!TOOLA_AS_LOG!" -- "!KILL_PY!" "%~dp0restart_orchestrator.py" --clear-timeout 20
set "STOP_RC=!ERRORLEVEL!"
popd
if not "!STOP_RC!"=="0" (
    echo [WARN] Pre-run gate rc=!STOP_RC! ^(Medium-IL taskkill co the Access-denied tren High-IL detached process^).
    echo [WARN] KHONG abort: schtasks /run se chay high_il.cmd o High-IL, no tu kill instance cu truoc khi launch.
)

REM ================================ F2 bounded /run retries ================================
REM MultipleInstancesPolicy=IgnoreNew means /run can be silently ignored while the task is still active.
REM Bounded retries with backoff so a single transient IgnoreNew does not collapse to INLINE or to a launcher failure.
set "RUN_OK=0"
set /a TRY=0
:RUN_RETRY
set /a TRY+=1
if !TRY! gtr 3 goto RUN_GIVE_UP
echo [*] schtasks /run ^(attempt !TRY!/3^)...
pushd "%~dp0"
"!CLI_PYW!" -m orchestrator.lifecycle.as_bat_helpers schtasks-run "%TASK_ORCH%"
set "RUN_RC=!ERRORLEVEL!"
popd
if not "!RUN_RC!"=="0" (
    echo [WARN] attempt !TRY!: schtasks /run tra errorlevel.
    ping 127.0.0.1 -n 3 >nul
    goto RUN_RETRY
)
REM Confirm task transitioned to Running (covers silent IgnoreNew) within ~6s.
pushd "%~dp0"
"!CLI_PYW!" -m orchestrator.lifecycle.as_bat_helpers wait-task-state "%TASK_ORCH%" Running --timeout-s 6
set "RUNNING_RC=!ERRORLEVEL!"
popd
if "!RUNNING_RC!"=="0" (
    REM Hung-task: IgnoreNew keeps the OLD Running instance. If :8083 is still
    REM PID_BEFORE, /run was swallowed — Stop-ScheduledTask + kill, then retry.
    if defined PID_BEFORE (
        set "PID_NOW="
        pushd "%~dp0"
        "!CLI_PYW!" -m orchestrator.lifecycle.as_bat_helpers get-listen-pid 8083 --out "!PIDFILE!"
        popd
        if exist "!PIDFILE!" for /f "usebackq delims=" %%p in ("!PIDFILE!") do set "PID_NOW=%%p"
        if defined PID_NOW if "!PID_NOW!"=="!PID_BEFORE!" (
            echo [WARN] attempt !TRY!: /run bi IgnoreNew — :8083 van PID !PID_NOW! ^(hung instance^).
            echo [WARN] Stop-ScheduledTask + restart_orchestrator.py roi retry. Khong dung schtasks /end lan 2.
            pushd "%~dp0"
            "!CLI_PYW!" -m orchestrator.lifecycle.as_bat_helpers stop-task "%TASK_ORCH%"
            "!CLI_PYW!" -m orchestrator.lifecycle.as_bat_helpers run-hidden --log "!TOOLA_AS_LOG!" -- "!KILL_PY!" "%~dp0restart_orchestrator.py" --clear-timeout 10
            popd
            ping 127.0.0.1 -n 3 >nul
            goto RUN_RETRY
        )
    )
    set "RUN_OK=1"
    goto RUN_DONE
)
echo [WARN] attempt !TRY!: task chua sang Running ^(co the bi MultipleInstancesPolicy=IgnoreNew^).
ping 127.0.0.1 -n 3 >nul
goto RUN_RETRY
:RUN_GIVE_UP
echo [LOI] schtasks /run khong dang ky sau 3 lan thu.
if /i "%TOOLA_FROM_HTTP_HELPER%"=="1" (
    echo [LOI] Dang o HTTP helper — khong fallback de tranh loop.
    goto FAIL_TASK
)
echo [*] Fallback Medium-IL: POST restart_orchestrator_via_bat de [A] High-IL tu restart.
> "%TEMP%\toola_as_restart.json" echo {"delay_seconds":2,"force_drain":true,"force_drain_reason":"AS_medium_il_ignorenew"}
curl.exe -sS --max-time 15 -X POST "http://localhost:8083/api/call/restart_orchestrator_via_bat" -H "Content-Type: application/json" --data-binary "@%TEMP%\toola_as_restart.json"
echo.
echo [*] Cho instance moi sau HTTP fallback ^(PID khac PID_BEFORE=!PID_BEFORE!^)...
pushd "%~dp0"
"!CLI_PYW!" -m orchestrator.lifecycle.as_bat_helpers wait-new-owner --port 8083 --before "!PID_BEFORE!" --livez http://127.0.0.1:8084/livez --timeout-s 90
set "FALLBACK_RC=!ERRORLEVEL!"
popd
if not "!FALLBACK_RC!"=="0" (
    echo [LOI] HTTP fallback khong thay instance moi trong 90s.
    goto FAIL_TASK
)
goto RUN_DONE

:WAIT_NEW_PID
pushd "%~dp0"
"!CLI_PYW!" -m orchestrator.lifecycle.as_bat_helpers wait-new-owner --port 8083 --before "!PID_BEFORE!" --livez http://127.0.0.1:8084/livez --timeout-s 90
set "WAIT_RC=!ERRORLEVEL!"
popd
if not "!WAIT_RC!"=="0" (
    echo [WARN] Path B ForceRestart khong chung minh PID moi trong 90s. Fall through Path C.
    goto PATH_C
)
goto PROVE_LIVE

:RUN_DONE
REM ================================ F5 livez readiness ================================
:PROVE_LIVE
REM A2 (F5): /livez on 127.0.0.1:8084 is the authoritative readiness signal (aiohttp-independent).
echo [*] Cho /livez tren 127.0.0.1:8084 ^(ok + service + livez_protocol==1^)...
pushd "%~dp0"
"!CLI_PYW!" -m orchestrator.lifecycle.as_bat_helpers wait-livez --url http://127.0.0.1:8084/livez --timeout-s 60
set "LIVEZ_RC=!ERRORLEVEL!"
popd
if not "!LIVEZ_RC!"=="0" (
    echo [LOI] /livez khong san sang trong 60s.
    goto FAIL_TASK
)
echo [OK] /livez san sang.

REM ================================ instance-replaced proof (PID changed) ================================
REM Anh 2026-08-11: /livez passing is NOT proof of a restart — the OLD instance can still
REM own :8083 when a Medium-IL caller could not kill the High-IL pythonw. Require a NEW PID.
set "PID_AFTER="
pushd "%~dp0"
"!CLI_PYW!" -m orchestrator.lifecycle.as_bat_helpers get-listen-pid 8083 --out "!PIDFILE!"
popd
if exist "!PIDFILE!" for /f "usebackq delims=" %%p in ("!PIDFILE!") do set "PID_AFTER=%%p"
if defined PID_BEFORE if "!PID_AFTER!"=="!PID_BEFORE!" (
    echo [LOI] Instance KHONG duoc thay the: :8083 van do PID !PID_AFTER! so huu ^(= PID_BEFORE^).
    echo [LOI] Nguyen nhan: Medium-IL KHONG kill duoc [A] High-IL ^(Windows integrity level^).
    echo [LOI] Cach dung dung: restart qua [A] tool 'restart_orchestrator_via_bat' ^(helper chay High-IL qua [A]^),
    echo [LOI]   hoac Path B ToolA_ForceRestart ^(High-IL stop-gate^). KHONG tra exit 0 de tranh false-success.
    goto FAIL_TASK
)
echo [OK] Instance moi: :8083 do PID !PID_AFTER! so huu ^(khac PID_BEFORE=!PID_BEFORE!^).

REM ================================ F5 /health generation verify ================================
REM A2 (F5): sau livez stable moi doc generation tu /health ^(verify-only; livez da co generation^).
pushd "%~dp0"
"!CLI_PYW!" -m orchestrator.lifecycle.as_bat_helpers wait-health-gen --url http://localhost:8083/health --timeout-s 10
set "HEALTH_RC=!ERRORLEVEL!"
popd
if not "!HEALTH_RC!"=="0" (
    echo [WARN] /health generation verify fail sau livez ok; van tiep tuc ^(livez la authoritative^).
)
echo [OK] Orchestrator up ^(instance moi^). RestartControl khong bi disable.
exit /b 0

:FAIL_GUARD
echo.
echo [LOI] Restart bi RESTART_GUARD_BLOCKED. Khong ForceRestart de tranh giet worker.
exit /b 13

:FAIL_TASK
REM A2 (F3): khong INLINE khi task High-IL da ton tai; fail loud.
REM KHONG rearm/disable RestartControl — RC phai song cho Path A lan sau.
echo.
echo [LOI] Khong the dua Orchestrator High-IL len; xem chi tiet o tren.
echo [LOI] Khong fallback INLINE vi task '%TASK_ORCH%' da ton tai ^(tranh mat High-IL^).
exit /b 11

REM ================================ INLINE branch (task not installed) ================================
:INLINE
REM INLINE chi khi 'ToolA_Orchestrator' chua duoc cai = fallback Medium-IL. Giu nguyen.
if not "%TOOLA_SKIP_RESTART_KILL%"=="1" (
    set "TOOLA_SKIP_RESTART_KILL=1"
    echo [*] Pre-kill [A] python host qua restart_orchestrator.py ^(interp: !KILL_PY!^)...
    pushd "%~dp0"
    "!CLI_PYW!" -m orchestrator.lifecycle.as_bat_helpers run-hidden --log "!TOOLA_AS_LOG!" -- "!KILL_PY!" "%~dp0restart_orchestrator.py" --clear-timeout 20
    set "KILL_RC=!ERRORLEVEL!"
    popd
    if not "!KILL_RC!"=="0" (
        echo [LOI] restart_orchestrator.py stop gate fail, exit code=!KILL_RC!
        exit /b !KILL_RC!
    )
)
pushd "%~dp0"
"!CLI_PYW!" -m orchestrator.lifecycle.as_bat_helpers run-ps1 "%~dp0start_orchestrator.ps1"
set "RC=!ERRORLEVEL!"
popd
if not "!RC!"=="0" (
    echo.
    echo [LOI] start_orchestrator exit code=!RC!. Doc thong bao tren va xu ly.
    echo       1 = python thieu / stop gate busy / health fail.
    echo.
    echo  De [A] chay High IL ^(cho phep desktop_click admin-context^):
    echo      Chuot phai vao install_orchestrator_admin_task.ps1 -^> Run with PowerShell ^(admin^) 1 lan.
    echo      Sau do bat nay tu chay qua Task Scheduler, KHONG can admin moi lan.
)
exit /b !RC!
if not "%TOOLA_NO_PAUSE%"=="1" pause
