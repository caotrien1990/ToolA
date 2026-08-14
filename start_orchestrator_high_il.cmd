@echo off
setlocal enableextensions
set "ROOT=%~dp0"

REM ===== Anh 2026-08-09 BK34: bounded concurrency (~20%% CPU/RAM budget; 20C/28T, 64GB) =====
REM Semaphores cap expensive categories globally; subprocess-heavy work stays below CPU-thread count.
set "ORCH_SEM_READ=48"
set "ORCH_SEM_COMMAND=24"
set "ORCH_SEM_GIT=16"
set "ORCH_SEM_SUBPROCESS=16"
set "ORCH_SEM_TEST=6"
set "ORCH_SEM_BROWSER=2"
set "ORCH_SEM_BACKUP=4"
REM Cross-lane cap prevents the larger I/O pools from oversubscribing the host together.
set "ORCH_GLOBAL_CAP=96"
REM Fair admission: authenticated principals keep headroom; anonymous local callers share a wider fallback.
REM The executor queues remain the final backpressure boundary, so extra waiters do not create unbounded work.
set "ORCH_FAIR_ANONYMOUS_QUOTA=32"
set "ORCH_FAIR_MAX_WAITERS_PER_PRINCIPAL=256"
set "ORCH_FAIR_READ_IO_QUOTA=16"
set "ORCH_FAIR_SEARCH_QUOTA=12"
set "ORCH_FAIR_COMMAND_QUOTA=8"
set "ORCH_FAIR_GIT_QUOTA=8"
set "ORCH_FAIR_TEST_QUOTA=4"
set "ORCH_FAIR_BACKUP_QUOTA=2"
set "ORCH_FAIR_COUNCIL_PROVIDER_QUOTA=8"
set "ORCH_FAIR_ANONYMOUS_READ_IO_QUOTA=32"
set "ORCH_FAIR_ANONYMOUS_SEARCH_QUOTA=24"
set "ORCH_FAIR_ANONYMOUS_COMMAND_QUOTA=16"
set "ORCH_FAIR_ANONYMOUS_GIT_QUOTA=12"
set "ORCH_FAIR_ANONYMOUS_TEST_QUOTA=6"
set "ORCH_FAIR_ANONYMOUS_BACKUP_QUOTA=4"
set "ORCH_FAIR_ANONYMOUS_COUNCIL_PROVIDER_QUOTA=16"
REM Queue wait budgets favor backpressure over 429s while remaining hard-bounded.
set "ORCH_EXEC_READ_IO_QUEUE_WAIT_SECONDS=15"
set "ORCH_EXEC_SEARCH_QUEUE_WAIT_SECONDS=15"
set "ORCH_EXEC_COMMAND_QUEUE_WAIT_SECONDS=20"
set "ORCH_EXEC_GIT_QUEUE_WAIT_SECONDS=20"
set "ORCH_EXEC_TEST_QUEUE_WAIT_SECONDS=30"
set "ORCH_EXEC_BACKUP_QUEUE_WAIT_SECONDS=30"
set "ORCH_EXEC_COUNCIL_PROVIDER_QUEUE_WAIT_SECONDS=30"
REM Executor lanes: I/O-bound lanes are wide; CPU/spawn-heavy lanes remain conservative.
set "ORCH_EXEC_READ_IO_WORKERS=48"
set "ORCH_EXEC_READ_IO_QUEUE=192"
set "ORCH_EXEC_SEARCH_WORKERS=32"
set "ORCH_EXEC_SEARCH_QUEUE=128"
set "ORCH_EXEC_COMMAND_WORKERS=24"
set "ORCH_EXEC_COMMAND_QUEUE=96"
set "ORCH_EXEC_GIT_WORKERS=20"
set "ORCH_EXEC_GIT_QUEUE=80"
set "ORCH_EXEC_TEST_WORKERS=8"
set "ORCH_EXEC_TEST_QUEUE=24"
set "ORCH_EXEC_COUNCIL_PROVIDER_WORKERS=24"
set "ORCH_EXEC_COUNCIL_PROVIDER_QUEUE=96"
set "ORCH_EXEC_BACKUP_WORKERS=4"
set "ORCH_EXEC_BACKUP_QUEUE=12"
REM Default asyncio.to_thread pool serves auth/session/ledger I/O offloads.
set "ORCH_DEFAULT_EXECUTOR_WORKERS=56"
set "LOGDIR=%ROOT%.orchestrator_cache"
if not exist "%LOGDIR%" mkdir "%LOGDIR%"
set "LOG=%LOGDIR%\high_il_start.log"
if /I "%~1"=="--probe-readiness" goto PROBE_READINESS
echo ===== %DATE% %TIME% high_il start =====>>"%LOG%"

REM wipe top-level pycache (stale .pyc)
if exist "%ROOT%__pycache__" (
  echo [1] rmdir __pycache__>>"%LOG%"
  rmdir /s /q "%ROOT%__pycache%" >>"%LOG%" 2>&1
)

set "PY=%ROOT%venv312\Scripts\python.exe"
set "PYW=%ROOT%venv312\Scripts\pythonw.exe"
if not exist "%PY%" set "PY=python.exe"
if not exist "%PYW%" set "PYW=pythonw.exe"

echo [2] restart_orchestrator.py strict stop via %PY%>>"%LOG%"
pushd "%ROOT%"
"%PYW%" -m orchestrator.lifecycle.as_bat_helpers run-hidden --log "%LOG%" -- "%PY%" "%ROOT%restart_orchestrator.py" --clear-timeout 20
set "KILL_RC=%ERRORLEVEL%"
popd
echo [2] stop_gate_rc=%KILL_RC%>>"%LOG%"
if not "%KILL_RC%"=="0" (
  echo [FAIL] strict stop gate blocked launch rc=%KILL_RC%>>"%LOG%"
  exit /b %KILL_RC%
)

echo [3] launch scheduler-owned pythonw orchestrator_server.py>>"%LOG%"
REM probe-livez is in-thread in high_il_launch.py; do not spawn untitled start /b console.
REM Direct exec so pythonw inherits the scheduled-task job. The old wait-wrapper
REM broke away; ending the task then left an orphan High-IL pythonw on :8083.
"%PYW%" "%ROOT%orchestrator_server.py"
set "SERVER_RC=%ERRORLEVEL%"
echo [5] scheduler-owned server exited rc=%SERVER_RC%>>"%LOG%"
exit /b %SERVER_RC%

:PROBE_READINESS
REM Readiness gate: /livez is independent of the aiohttp event loop. A listening
REM 8083 port with no /livez yet means the new process is still starting, not dead.
REM Kept for --probe-readiness callers; launch path uses pythonw probe-livez.
set "OK=0"
for /L %%i in (1,1,120) do (
  "%PYW%" -c "import json,urllib.request,sys; r=urllib.request.urlopen('http://127.0.0.1:8084/livez',timeout=2); d=json.loads(r.read()); sys.exit(0 if r.status==200 and d.get('ok') and d.get('service')=='project-orchestrator' and d.get('livez_protocol')==1 else 1)" >>"%LOG%" 2>&1
  if not errorlevel 1 (
    set "OK=1"
    echo [4] livez ok at iter %%i>>"%LOG%"
    goto PROBE_DONE
  )
  ping 127.0.0.1 -n 2 >nul
)
echo [4] livez timeout after extended 120-probe gate>>"%LOG%"
:PROBE_DONE
if "%OK%"=="1" (
  echo [OK] high_il readiness complete; scheduled task retains child ownership>>"%LOG%"
  exit /b 0
)
echo [FAIL] high_il readiness incomplete; scheduled task still owns child lifetime>>"%LOG%"
exit /b 1
