@echo off
REM V15 R15 — Manual pre-commit gate trigger.
REM Runs two read-only gates; either non-zero exit blocks the commit:
REM   1. sync_tool_matrix.py --ci        4-layer drift (missing/ghost/stale refs)
REM   2. soft_data_reconcile.py --ci     cross-layer overlap DEFECTS ONLY
REM                                      (dangling see_also / broken alias target).
REM Advisory overlap findings (param-name conflicts, redundancy, bloat) NEVER block.
REM Use directly: scripts\precommit_check.bat
REM Or install as git hook via scripts\install_precommit.bat

setlocal enableextensions

set SCRIPT_DIR=%~dp0
set PROJECT_DIR=%SCRIPT_DIR%..
set PY=%PROJECT_DIR%\venv312\Scripts\python.exe

if not exist "%PY%" (
    set PY=python
)

"%PY%" "%SCRIPT_DIR%sync_tool_matrix.py" --ci
set RC_MATRIX=%ERRORLEVEL%
if not "%RC_MATRIX%"=="0" (
    echo.
    echo [BLOCK] Commit blocked: tool matrix drift detected ^(exit %RC_MATRIX%^).
    echo         Run: scripts\sync_tool_matrix.py --check --output md
    echo         to see full report and suggested fixes.
)

"%PY%" "%SCRIPT_DIR%soft_data_reconcile.py" --ci
set RC_SOFT=%ERRORLEVEL%
if not "%RC_SOFT%"=="0" (
    echo.
    echo [BLOCK] Commit blocked: soft-data DEFECT detected ^(exit %RC_SOFT%^).
    echo         Run: scripts\soft_data_reconcile.py
    echo         to see the dangling see_also / broken alias target.
)

set RC=%RC_MATRIX%
if "%RC%"=="0" set RC=%RC_SOFT%
exit /b %RC%
