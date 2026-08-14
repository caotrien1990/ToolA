@echo off
REM .council\utf8_runner.bat
REM Robust UTF-8 council runner. Forwards ALL args (logfile + command) to
REM utf8_runner.ps1 POSITIONALLY via %* — no quote-rebuild, no double cmd-parse.
REM
REM Usage:
REM   call .council\utf8_runner.bat "<logfile>" <exe> <args...>
REM Examples:
REM   call .council\utf8_runner.bat ".council\log_codo.txt" codex exec "..." --flags
REM   call .council\utf8_runner.bat ".council\log_ope.txt" opencode run "..." --model X
REM
REM Note: stdin is auto-closed; stdout+stderr captured as UTF-8 (no BOM) to logfile.
REM       Do NOT add >, 2>, *> redirection in the command.

setlocal
if "%~1"=="" (
    echo ERROR: No logfile specified
    exit /b 1
)
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0utf8_runner.ps1" %*
set EXIT_CODE=%ERRORLEVEL%
endlocal & exit /b %EXIT_CODE%
