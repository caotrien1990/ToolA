@echo off
REM V15 R15 — Install pre-commit git hook for tool matrix drift gate.
REM Usage: scripts\install_precommit.bat (run once after `git clone`).

setlocal enableextensions

set SCRIPT_DIR=%~dp0
set PROJECT_DIR=%SCRIPT_DIR%..
set GIT_DIR=%PROJECT_DIR%\.git

if not exist "%GIT_DIR%" (
    echo [INFO] No .git folder at %PROJECT_DIR%
    echo        Skipping pre-commit hook install ^(repo not git-initialized yet^).
    echo        Run 'git init' first, then re-run this script.
    exit /b 0
)

set HOOK_DIR=%GIT_DIR%\hooks
if not exist "%HOOK_DIR%" mkdir "%HOOK_DIR%"

set HOOK_FILE=%HOOK_DIR%\pre-commit
echo #!/bin/sh> "%HOOK_FILE%"
echo # V15 R15 — auto-installed pre-commit hook for tool matrix drift gate.>> "%HOOK_FILE%"
echo # Calls scripts\precommit_check.bat. Exit non-zero blocks the commit.>> "%HOOK_FILE%"
echo cmd //c "%SCRIPT_DIR%precommit_check.bat">> "%HOOK_FILE%"
echo RC=$?>> "%HOOK_FILE%"
echo exit $RC>> "%HOOK_FILE%"

echo [OK] Installed pre-commit hook at %HOOK_FILE%
echo [INFO] On every `git commit`, sync_tool_matrix.py --ci runs first.
echo        If drift_count ^> 0, commit is blocked.
echo        Bypass once with `git commit --no-verify` ^(NOT recommended^).
exit /b 0
