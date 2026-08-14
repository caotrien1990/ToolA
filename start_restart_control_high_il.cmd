@echo off
setlocal enableextensions
set "ROOT=%~dp0"
set "PYW=%ROOT%venv312\Scripts\pythonw.exe"
if not exist "%PYW%" set "PYW=pythonw.exe"
if not exist "%ROOT%.orchestrator_cache" mkdir "%ROOT%.orchestrator_cache"
REM Direct exec: inherit scheduled-task job so /end stops the controller.
"%PYW%" -m orchestrator.lifecycle.restart_control_service --port 8085
exit /b %ERRORLEVEL%
