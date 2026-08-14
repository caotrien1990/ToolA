$pyexe  = Join-Path $PSScriptRoot "venv312\Scripts\python.exe"
$script = Join-Path $PSScriptRoot "orchestrator_server.py"
$apiBase = if ($env:ORCHESTRATOR_BASE_URL) { $env:ORCHESTRATOR_BASE_URL } else { "http://localhost:8083" }

# V15 R13 (2026-05-16): admin CHO PHEP (de [A] chay High IL, fix UIPI block desktop_click).
# Khuyen nghi: dung Task Scheduler ('install_orchestrator_admin_task.ps1') de admin
# tu dong, khong UAC moi lan. start_orchestrator.bat tu uu tien schtasks /run truoc.
# Voi inline launcher, ca admin va user context deu OK; taskkill /F xu ly cross-elevation kill.
$identity = [System.Security.Principal.WindowsIdentity]::GetCurrent()
$principal = New-Object System.Security.Principal.WindowsPrincipal($identity)
$isAdmin = $principal.IsInRole([System.Security.Principal.WindowsBuiltInRole]::Administrator)
if ($isAdmin) {
    Write-Host "[*] Dang chay High IL (Administrator). Desktop_click/admin-context UIPI: OK." -ForegroundColor Cyan
} else {
    Write-Host "[i] Dang chay Medium IL (User). UIPI se block click vao process High IL." -ForegroundColor DarkGray
    Write-Host "    Neu can dieu khien Notepad/Explorer/Task Manager admin, chay install_orchestrator_admin_task.ps1 (1 lan, admin)." -ForegroundColor DarkGray
}

Write-Host ""
Write-Host " =========================================="
Write-Host "  Project Orchestrator [FIX/UPGRADE COPY]"
Write-Host "  UI: $apiBase"
Write-Host "  Health: $apiBase/health"
Write-Host "  API base: $apiBase/api"
Write-Host " =========================================="
Write-Host ""

if (-not (Test-Path $pyexe)) {
    Write-Host "[WARN] Khong tim thay venv Python: $pyexe" -ForegroundColor Yellow
    $pyexe = $null
}

if ($pyexe) {
    & $pyexe -c "import sys; print(sys.executable)" *> $null
    if ($LASTEXITCODE -ne 0) {
        Write-Host "[WARN] venv Python khong chay duoc, fallback sang python.exe trong PATH" -ForegroundColor Yellow
        $pyexe = $null
    }
}

if (-not $pyexe) {
    $cmd = Get-Command python.exe -ErrorAction SilentlyContinue | Select-Object -First 1
    if (-not $cmd) {
        Write-Host "[LOI] Khong tim thay Python hop le" -ForegroundColor Red
        exit 1
    }
    $pyexe = $cmd.Source
}

Write-Host " Python : $pyexe"
Write-Host " Script : $script"
Write-Host ""

$pythonw = Join-Path (Split-Path -Parent $pyexe) "pythonw.exe"
if (Test-Path $pythonw) {
    $launchExe = $pythonw
} else {
    $launchExe = $pyexe
}

# ============================================================
# [PROTECTED BLOCK - DO NOT REMOVE OR WEAKEN]
# Chuc nang: Tat process cu dang giu port 8083 truoc khi khoi dong moi.
# Dam bao "kill old host -> open new host" hoat dong moi lan restart.
# v2 (2026-05-12): them admin detection, verify kill thuc su, xoa __pycache__,
# verify port 8083 free, fail fast neu khong kill duoc (khong silent fail).
# CANH BAO: Xoa hoac lam yeu block nay se khien instance moi khong start
# duoc khi port con bi giu (exit 1 hoac silent fail).
# Test sau moi lan chinh sua: chay bat 2 lan lien tiep, lan 2 phai start OK.
# ============================================================

# Step 0: V15 — Run as USER only. Orchestrator listens on unprivileged port 8083,
# pythonw is user-owned, Stop-Process from same User context works.
# If an old instance was once started elevated, end it once via Task Manager, then
# always launch via this .bat without admin going forward.

# Step 0b: Snapshot log file size de verify server moi co thuc su startup
$logFile = Join-Path $PSScriptRoot "orchestrator.log"
$logSnapshot = if (Test-Path $logFile) { (Get-Item $logFile).Length } else { 0 }

# Step 1: Kill bat ky process nao dang giu port 8083 + VERIFY tung process thuc su chet
$ownersOnPort = Get-NetTCPConnection -LocalPort 8083 -ErrorAction SilentlyContinue |
    Select-Object -ExpandProperty OwningProcess -Unique

if ($ownersOnPort) {
    foreach ($pid8083 in $ownersOnPort) {
        Write-Host "[*] Tat process cu tren port 8083 (PID $pid8083)..." -ForegroundColor Yellow
        Stop-Process -Id $pid8083 -Force -ErrorAction SilentlyContinue
        Start-Sleep -Milliseconds 300
        $stillAlive = Get-Process -Id $pid8083 -ErrorAction SilentlyContinue
        if ($stillAlive) {
            Write-Host "[!] PID $pid8083 van song. Thu taskkill /F /T..." -ForegroundColor Red
            $tk = & taskkill /F /T /PID $pid8083 2>&1
            Start-Sleep -Milliseconds 500
            $stillAlive2 = Get-Process -Id $pid8083 -ErrorAction SilentlyContinue
            if ($stillAlive2) {
                Write-Host "[LOI] Khong the kill PID $pid8083 - process duoc start boi User khac/elevated context." -ForegroundColor Red
                Write-Host "      Mo Task Manager, end process pythonw.exe PID $pid8083, roi chay lai bat nay." -ForegroundColor Red
                Write-Host "      taskkill output: $tk" -ForegroundColor Red
                        exit 1
            }
        }
    }
}

# Step 2: Kill stale orchestrator_server.py processes khong con giu port
$escapedScript = [regex]::Escape($script)
try {
    Get-CimInstance Win32_Process -Filter "Name = 'python.exe' OR Name = 'pythonw.exe' OR Name = 'python3.12.exe' OR Name = 'pythonw3.12.exe'" -ErrorAction Stop |
        Where-Object { $_.CommandLine -and ($_.CommandLine -match $escapedScript) -and ($_.CommandLine -notmatch '--mcp') -and ($_.ProcessId -ne $PID) } |
        ForEach-Object {
            Write-Host "[*] Tat stale orchestrator (PID $($_.ProcessId))..." -ForegroundColor Yellow
            Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue
        }
} catch {
    Write-Host "[WARN] Khong doc duoc process command line; da kill theo port 8083 neu co." -ForegroundColor Yellow
}

# Step 3: Verify port 8083 THUC SU free (tranh race condition)
$portRetries = 0
$busy = $null
while ($portRetries -lt 10) {
    $busy = Get-NetTCPConnection -LocalPort 8083 -State Listen -ErrorAction SilentlyContinue
    if (-not $busy) { break }
    Start-Sleep -Milliseconds 300
    $portRetries++
}
if ($busy) {
    Write-Host "[LOI] Port 8083 van busy sau 10 retries." -ForegroundColor Red
    Write-Host "      Tim PID giu port: Get-NetTCPConnection -LocalPort 8083 | Select-Object OwningProcess" -ForegroundColor Yellow
    Write-Host "      Roi end process do qua Task Manager, chay lai bat nay (khong can admin)." -ForegroundColor Yellow
    exit 1
}

# Step 4: Xoa __pycache__ de force fresh bytecode (tranh stale .pyc)
$pycache = Join-Path $PSScriptRoot "__pycache__"
if (Test-Path $pycache) {
    Remove-Item -Recurse -Force $pycache -ErrorAction SilentlyContinue
    Write-Host "[*] Da xoa __pycache__/" -ForegroundColor DarkYellow
}

Start-Sleep -Milliseconds 500
Write-Host "[OK] Dang khoi dong Orchestrator moi..." -ForegroundColor Green

# F5: ORCHESTRATOR_AUTO_TOKEN removed from inline launch.
# Trust-mode is now opt-in only — set ORCHESTRATOR_AUTO_TOKEN=1 in your
# environment BEFORE running this script if you need protected T4 tools
# (launch_electron_with_cdp/fill_value/click/...) auto-injected.
# This aligns with CORE_RULE: trust-mode = explicit opt-in, not default.

# ============================================================
# [END PROTECTED BLOCK]
# ============================================================

$psi = [System.Diagnostics.ProcessStartInfo]::new()
$psi.FileName = $launchExe
$psi.Arguments = "`"$script`""
$psi.WorkingDirectory = $PSScriptRoot
# R44 NOTE: child process inherits $env:* from the current PS session, so the
# ORCHESTRATOR_AUTO_TOKEN=1 set above propagates even with UseShellExecute=$true.
$psi.UseShellExecute = $true
$psi.WindowStyle = [System.Diagnostics.ProcessWindowStyle]::Hidden
$proc = [System.Diagnostics.Process]::new()
$proc.StartInfo = $psi
$null = $proc.Start()

# Health-check loop: cho phep cold start dai (up to 60s).
# In tien do theo giay de User biet bat dang lam viec, khong phai treo.
Write-Host "[*] Cho server san sang (tien hanh /health probe, timeout 60s)..." -ForegroundColor Cyan
$healthy = $false
$maxSeconds = 60
$startTs = Get-Date
for ($i = 0; $i -lt $maxSeconds; $i++) {
    Start-Sleep -Seconds 1
    try {
        $health = Invoke-RestMethod -Uri "$apiBase/health" -Method GET -TimeoutSec 2
        if ($health.ok) {
            $healthy = $true
            Write-Host ""
            Write-Host "[OK] Server san sang sau $([int]((Get-Date) - $startTs).TotalSeconds)s." -ForegroundColor Green
            break
        }
    } catch {
        # Verify pythonw van con song. Neu chet -> exit som.
        $alive = Get-Process -Id $proc.Id -ErrorAction SilentlyContinue
        if (-not $alive) {
            Write-Host ""
            Write-Host "[LOI] Process pythonw chet ngay khi start (PID $($proc.Id))." -ForegroundColor Red
            Write-Host "      Chay foreground de xem traceback:" -ForegroundColor Yellow
            Write-Host "      `"$pyexe`" `"$script`"" -ForegroundColor Yellow
            exit 1
        }
    }
    Write-Host -NoNewline "."
    if (($i + 1) % 10 -eq 0) { Write-Host " ${i}s" -ForegroundColor DarkGray }
}

if ($healthy) {
    Write-Host "[OK] Health: $apiBase/health" -ForegroundColor Green
    Write-Host "[OK] Server PID: $($proc.Id)" -ForegroundColor Green
    # Verify server moi thuc su startup bang cach so sanh log file size
    if (Test-Path $logFile) {
        $newSize = (Get-Item $logFile).Length
        if ($newSize -gt $logSnapshot) {
            Write-Host "[OK] orchestrator.log tang $($newSize - $logSnapshot) bytes (startup log moi)." -ForegroundColor Green
        } else {
            Write-Host "[WARN] orchestrator.log khong tang size - health pass nhung server moi chua log. Co the server cu van song." -ForegroundColor Yellow
        }
    }
    try {
        Start-Process "$apiBase/council" -ErrorAction Stop
        Write-Host "[OK] Da mo trinh duyet -> $apiBase/council" -ForegroundColor Green
    } catch {
        Write-Host "[WARN] Khong mo duoc trinh duyet: $($_.Exception.Message)" -ForegroundColor Yellow
        Write-Host "      Mo thu cong: $apiBase" -ForegroundColor Yellow
    }
    exit 0
}

Write-Host ""
Write-Host "[LOI] Server chua san sang sau ${maxSeconds}s." -ForegroundColor Red
Write-Host "      Kiem tra orchestrator.log de xem loi." -ForegroundColor Yellow
Write-Host "      Hoac chay foreground de xem traceback truc tiep:" -ForegroundColor Yellow
Write-Host "      `"$pyexe`" `"$script`"" -ForegroundColor Yellow
exit 1
