@echo off
setlocal EnableExtensions EnableDelayedExpansion
REM ================================================================
REM muse_tunnel.bat -- 1 click mo Cloudflare Tunnel cho Muse ket noi
REM Dat file nay trong: ...\8. ToolA\project-orchestrator\
REM
REM Cach dung:
REM   muse_tunnel.bat        : che do HIEN - hien banner link, giu cua so
REM   muse_tunnel.bat hide   : che do AN   - khong cua so rac terminal,
REM                            link tu copy vao clipboard + luu
REM                            %TEMP%\muse_tunnel_url.txt, hien popup bao
REM   muse_tunnel.bat stop   : dung tunnel dang chay o che do AN
REM
REM Ket thuc che do HIEN: dong ca 2 cua so (MuseTunnel + Tunnel-Muse).
REM Ket thuc che do AN  : chay lai file voi tham so stop.
REM
REM CANH BAO: URL tunnel la cong khai; ai co link deu truy cap ToolA API
REM tai localhost:8083 (request qua tunnel den nhu localhost).
REM URL luu dang ro trong %TEMP%\muse_tunnel_url.txt; xoa file nay khi xong.
REM ================================================================

if /i "%~1"=="stop" goto :stop
if /i "%~1"=="hide" goto :rehide
if /i "%~1"=="hidden" goto :rehide
if /i "%~1"=="/hide" goto :rehide
if /i "%~1"=="-hide" goto :rehide
goto :main

:rehide
REM Tu chay lai o che do hoan toan an, khong de lai cua so terminal
set "MUSEBAT=%~f0"
powershell -NoProfile -WindowStyle Hidden -Command "Start-Process -FilePath 'cmd.exe' -ArgumentList '/c', $env:MUSEBAT, '_hidden' -WindowStyle Hidden"
exit /b 0

:stop
set "TPID="
if exist "%TEMP%\muse_tunnel.pid" set /p TPID=<"%TEMP%\muse_tunnel.pid"
if defined TPID (
  tasklist /FI "PID eq !TPID!" /FI "IMAGENAME eq cloudflared.exe" /NH 2>nul | find /I "cloudflared.exe" >nul
  if !ERRORLEVEL! EQU 0 (
    taskkill /PID "!TPID!" /FI "IMAGENAME eq cloudflared.exe" /F >nul 2>&1
    if !ERRORLEVEL! EQU 0 (
      echo Da dung tunnel ^(PID !TPID!^).
    ) else (
      echo Khong kill duoc PID !TPID! ^(co the tunnel da dung truoc do^).
    )
  ) else (
    echo PID !TPID! da cu hoac khong phai cloudflared.exe; khong dung tien trinh nao.
  )
  del "%TEMP%\muse_tunnel.pid" 2>nul
) else (
  echo Khong tim thay tunnel AN dang chay.
  echo ^(Che do HIEN: dong cua so 'Tunnel-Muse' de dung.^)
)
pause
exit /b 0

:main
set "HIDDEN=0"
if /i "%~1"=="_hidden" set "HIDDEN=1"

if %HIDDEN%==0 echo [*] Tim cloudflared ...
set "CFD="
if exist "%ProgramFiles(x86)%\cloudflared\cloudflared.exe" set "CFD=%ProgramFiles(x86)%\cloudflared\cloudflared.exe"
if not defined CFD if exist "%ProgramFiles%\cloudflared\cloudflared.exe" set "CFD=%ProgramFiles%\cloudflared\cloudflared.exe"
if not defined CFD (
  call :fail "Khong tim thay cloudflared.exe. Cai dat: winget install Cloudflare.cloudflared"
  exit /b 1
)
if %HIDDEN%==0 echo     OK: %CFD%

if %HIDDEN%==0 echo [*] Kiem tra orchestrator http://localhost:8083/health ...
set "ORCH=ORCH_DOWN"
powershell -NoProfile -WindowStyle Hidden -Command "try { $r = Invoke-RestMethod -Uri http://localhost:8083/health -TimeoutSec 5; if ($r.ok) { 'ORCH_OK gen=' + $r.generation } else { 'ORCH_NOT_OK' } } catch { 'ORCH_DOWN' }" > "%TEMP%\muse_orch.txt" 2>nul
set /p ORCH=<"%TEMP%\muse_orch.txt"
if %HIDDEN%==0 echo     -^> !ORCH!
if "!ORCH!"=="ORCH_DOWN" (
  call :fail "Orchestrator chua chay tren localhost:8083. Chay start_orchestrator truoc, roi chay lai file nay."
  exit /b 2
)

set "LOG=%TEMP%\muse_tunnel.log"
if exist "%LOG%" del "%LOG%"
if exist "%TEMP%\muse_tunnel.pid" del "%TEMP%\muse_tunnel.pid"

if %HIDDEN%==1 (
  powershell -NoProfile -WindowStyle Hidden -Command "$p = Start-Process -FilePath '%CFD%' -ArgumentList @('tunnel','--url','http://localhost:8083','--logfile',(Join-Path $env:TEMP 'muse_tunnel.log')) -WindowStyle Hidden -PassThru; $p.Id | Out-File -FilePath (Join-Path $env:TEMP 'muse_tunnel.pid') -NoNewline"
) else (
  echo [*] Mo tunnel (cua so 'Tunnel-Muse') ...
  start "Tunnel-Muse" /min "%CFD%" tunnel --url http://localhost:8083 --logfile "%LOG%"
)

if %HIDDEN%==0 echo [*] Doi URL (toi da ~90s) ...
set "TURL=TIMEOUT"
powershell -NoProfile -WindowStyle Hidden -Command "$t=0; $url=''; $lp=Join-Path $env:TEMP 'muse_tunnel.log'; while($t -lt 90){ if(Test-Path $lp){ $m = Select-String -Path $lp -Pattern 'https://[a-z0-9-]+\.trycloudflare\.com' | Select-Object -First 1; if($m){ $url=$m.Matches.Value; break } }; Start-Sleep -Seconds 2; $t+=2 }; if($url){ $url } else { 'TIMEOUT' }" > "%TEMP%\muse_url.txt" 2>nul
set /p TURL=<"%TEMP%\muse_url.txt"

if "!TURL!"=="TIMEOUT" (
  call :fail "Khong lay duoc URL sau 90s. Xem log: %TEMP%\muse_tunnel.log"
  exit /b 3
)
if "!TURL!"=="" (
  call :fail "Khong doc duoc URL. Xem log: %TEMP%\muse_tunnel.log"
  exit /b 3
)

echo !TURL!> "%TEMP%\muse_tunnel_url.txt"
echo !TURL!| clip

if %HIDDEN%==1 (
  powershell -NoProfile -WindowStyle Hidden -Command "Add-Type -AssemblyName System.Windows.Forms; [System.Windows.Forms.MessageBox]::Show('Link tunnel da copy vao clipboard:' + [Environment]::NewLine + '!TURL!' + [Environment]::NewLine + [Environment]::NewLine + 'Nhan link cho Muse.' + [Environment]::NewLine + 'Dung tunnel: chay lai file nay voi tham so: stop', 'Muse Tunnel')"
  exit /b 0
)

echo.
echo ================================================================
echo   LINK GUI CHO MUSE  (da copy vao clipboard):
echo   !TURL!
echo ================================================================
echo.
echo   1. Nhan link tren cho Muse.
echo   2. GIU NGUYEN cua so 'Tunnel-Muse' trong luc audit.
echo   3. Xong viec: dong ca 2 cua so de dung tunnel.
echo.
pause
exit /b 0

:fail
REM Hien loi: popup neu che do an, echo+pause neu che do hien
if %HIDDEN%==1 (
  powershell -NoProfile -WindowStyle Hidden -Command "Add-Type -AssemblyName System.Windows.Forms; [System.Windows.Forms.MessageBox]::Show('%~1', 'Muse Tunnel - LOI')"
) else (
  echo [LOI] %~1
  pause
)
exit /b 0
