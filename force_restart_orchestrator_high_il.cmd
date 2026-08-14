@echo off
setlocal enableextensions
set "ROOT=%~dp0"
set "LOGDIR=%ROOT%.orchestrator_cache"
if not exist "%LOGDIR%" mkdir "%LOGDIR%"
set "LOG=%LOGDIR%\force_restart.log"
echo ===== %DATE% %TIME% force_restart =====>>"%LOG%"

set "PY=%ROOT%venv312\Scripts\python.exe"
if not exist "%PY%" set "PY=python.exe"

REM High-IL stop gate: taskkill of :8083 pythonw (skips leftover --mcp).
echo [1] High-IL restart_orchestrator.py --clear-timeout 25>>"%LOG%"
"%PY%" "%ROOT%restart_orchestrator.py" --clear-timeout 25 >>"%LOG%" 2>&1
set "KILL_RC=%ERRORLEVEL%"
echo [1] stop_gate_rc=%KILL_RC%>>"%LOG%"
if not "%KILL_RC%"=="0" (
  echo [FAIL] stop gate blocked ForceRestart rc=%KILL_RC%>>"%LOG%"
  exit /b %KILL_RC%
)

echo [2] schtasks /run ToolA_Orchestrator>>"%LOG%"
schtasks /run /tn "ToolA_Orchestrator" <nul >>"%LOG%" 2>&1
set "RUN_RC=%ERRORLEVEL%"
echo [2] run_rc=%RUN_RC%>>"%LOG%"
if not "%RUN_RC%"=="0" exit /b %RUN_RC%
exit /b 0
