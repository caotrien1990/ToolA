@echo off
REM ================================================================
REM toola_shell.bat -- 1 click mo PowerShell da nap san cong cu ToolA
REM Tu doc link tunnel hien tai trong %TEMP%\muse_tunnel_url.txt
REM Dung cho AI web chat (che do B): AI viet lenh, ban chay trong
REM cua so nay roi dan output tra lai cho AI.
REM ================================================================
setlocal EnableExtensions
set "URLFILE=%TEMP%\muse_tunnel_url.txt"
if not exist "%URLFILE%" (
  echo [LOI] Khong tim thay link tunnel.
  echo Chay muse_tunnel.bat truoc de lay link, roi chay lai file nay.
  pause
  exit /b 1
)
set /p TURL=<"%URLFILE%"
echo Mo PowerShell voi tunnel: %TURL%
powershell -NoExit -Command "$env:ORCHESTRATOR_BASE_URL='%TURL%'; . 'D:\\0. TRIEN\\1. DATA\\DATA\\5. VIBE APP\\1. APP\\8. ToolA\\project-orchestrator\\bin\\a.ps1'; $AProject='D:/0. TRIEN/1. DATA/DATA/5. VIBE APP/1. APP/8. ToolA/project-orchestrator'; Write-Host ''; Write-Host 'ToolA shell san sang. Dan lenh AI dua vao day.' -ForegroundColor Green; Write-Host ('Tunnel: ' + $env:ORCHESTRATOR_BASE_URL) -ForegroundColor Cyan"
