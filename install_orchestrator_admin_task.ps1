# V15 R13.1 — Cai Windows Task Scheduler task de [A] Orchestrator chay High IL
# (RunLevel=Highest) ma KHONG can UAC moi lan khoi dong.
#
# CACH CHAY: Right-click file nay > "Run with PowerShell".
# Script tu yeu cau UAC mot lan. Sau do moi start_orchestrator.bat / tool
# restart_orchestrator_via_bat se khong can UAC nua.
#
# Mot lan admin (lan cuoi): dang ky 3 task
#   ToolA_Orchestrator  Highest + StopExisting + AtLogOn
#   ToolA_ForceRestart  Highest + Queue + ~2 phut, KHONG logon
#   ToolA_RestartControl Highest + IgnoreNew + AtLogOn (pythonw la child truc tiep)
#
# Muc dich: fix UIPI block khi gui mouse message toi process High IL
# (Notepad, Task Manager, Explorer, Electron with admin manifest).

param([switch]$NonInteractive)

# ============================================================
# AUTO-ELEVATE: neu chua phai admin -> relaunch voi -Verb RunAs
# Su dung -NoExit de window mo cho User doc ket qua.
# ============================================================
$identity  = [System.Security.Principal.WindowsIdentity]::GetCurrent()
$principal = New-Object System.Security.Principal.WindowsPrincipal($identity)
if (-not $principal.IsInRole([System.Security.Principal.WindowsBuiltInRole]::Administrator)) {
    Write-Host ""
    Write-Host "[*] Script can quyen Administrator de tao Task Scheduler entry." -ForegroundColor Cyan
    Write-Host "    Dang yeu cau UAC... (chap nhan prompt 'Yes')" -ForegroundColor Cyan
    Write-Host ""
    try {
        $elevatedArgs = @(
            '-NoProfile',
            '-ExecutionPolicy', 'Bypass',
            '-File', "`"$PSCommandPath`""
        )
        if ($NonInteractive) { $elevatedArgs += '-NonInteractive' }
        Start-Process powershell.exe -Verb RunAs -ArgumentList $elevatedArgs -Wait:$NonInteractive
    } catch {
        Write-Host "[LOI] Khong yeu cau UAC duoc: $($_.Exception.Message)" -ForegroundColor Red
        Write-Host "      Chu dong: Mo PowerShell admin -> chay script nay thu cong." -ForegroundColor Yellow
        if (-not $NonInteractive) { Read-Host "Nhan Enter de thoat" }
    }
    exit
}

# ============================================================
# Tu day tro xuong: dang chay o context Administrator.
# Wrap try/catch + pause cuoi de window khong dong nhanh truoc khi User doc.
# ============================================================
try {
    $ErrorActionPreference = 'Stop'

    $taskName = 'ToolA_Orchestrator'
    $forceName = 'ToolA_ForceRestart'
    $controlName = 'ToolA_RestartControl'
    $scriptRoot = $PSScriptRoot
    $pyexe = Join-Path $scriptRoot 'venv312\Scripts\pythonw.exe'
    if (-not (Test-Path $pyexe)) {
        $pyexe = Join-Path $scriptRoot 'venv312\Scripts\python.exe'
    }
    $entry = Join-Path $scriptRoot 'orchestrator_server.py'

    Write-Host "=== Install Task Scheduler entry for [A] Orchestrator ===" -ForegroundColor Cyan
    Write-Host ""
    Write-Host "  Task name : $taskName" -ForegroundColor DarkGray
    Write-Host "  Python    : $pyexe" -ForegroundColor DarkGray
    Write-Host "  Entry     : $entry" -ForegroundColor DarkGray
    Write-Host "  User      : $env:USERNAME" -ForegroundColor DarkGray
    Write-Host ""

    if (-not (Test-Path $pyexe)) {
        Write-Host "[LOI] Khong tim thay python trong venv: $scriptRoot\venv312\Scripts\" -ForegroundColor Red
        throw "Python venv missing"
    }
    if (-not (Test-Path $entry)) {
        Write-Host "[LOI] Khong tim thay $entry" -ForegroundColor Red
        throw "orchestrator_server.py missing"
    }

    # Do not Unregister ToolA_Orchestrator while :8083 is live.
    # Unregister would kill the running [A] before ForceRestart/RC are registered.
    $orchLive = [bool](Get-NetTCPConnection -State Listen -LocalPort 8083 -ErrorAction SilentlyContinue)
    $existing = Get-ScheduledTask -TaskName $taskName -ErrorAction SilentlyContinue
    $skipOrchReregister = $false
    if ($existing -and $orchLive) {
        Write-Host "[*] :8083 dang song - giu task '$taskName', khong Unregister." -ForegroundColor Yellow
        $skipOrchReregister = $true
    } elseif ($existing) {
        Write-Host "[*] Task '$taskName' da ton tai, dang xoa de tao lai..." -ForegroundColor Yellow
        Unregister-ScheduledTask -TaskName $taskName -Confirm:$false
        $existing = $null
    }

    # pythonw-direct High-IL launcher: no cmd.exe, no start /b.
    # Task action is pythonw -m high_il_launch; that process stop-gates,
    # probes livez in-thread, then waits on pythonw orchestrator_server.py
    # without BREAKAWAY so /end owns the server.
    $launcher = Join-Path $scriptRoot 'orchestrator\lifecycle\high_il_launch.py'
    if (-not (Test-Path $launcher)) {
        Write-Host "[LOI] Thieu launcher: $launcher" -ForegroundColor Red
        throw "high_il_launch.py missing"
    }

    $principalObj = New-ScheduledTaskPrincipal -UserId $env:USERNAME -LogonType Interactive -RunLevel Highest
    $pyw = Join-Path $scriptRoot 'venv312\Scripts\pythonw.exe'
    if (-not (Test-Path $pyw)) { $pyw = 'pythonw.exe' }
    $orchArg = '-m orchestrator.lifecycle.high_il_launch'
    $forceArg = '-m orchestrator.lifecycle.high_il_launch --force'
    $controlArg = '-m orchestrator.lifecycle.restart_control_service --port 8085'
    if (-not $skipOrchReregister) {
    $action = New-ScheduledTaskAction -Execute $pyw -Argument $orchArg -WorkingDirectory $scriptRoot
    $settings = New-ScheduledTaskSettingsSet -Hidden -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -StartWhenAvailable -ExecutionTimeLimit (New-TimeSpan -Hours 0)
    # Hung-task restart: default IgnoreNew swallows schtasks /run while the old
    # pythonw is still Running. StopExisting lets [A:S] replace a stuck instance.
    # PS 5.1 MultipleInstancesEnum on this host is ONLY Parallel/Queue/IgnoreNew —
    # assigning "StopExisting" throws and used to leave the task deleted. Register
    # first, then patch via Task Scheduler COM (value 3 = TASK_INSTANCES_STOP_EXISTING)
    # which the XML schema accepts even when the PS enum does not.
    $trigger = New-ScheduledTaskTrigger -AtLogOn
    $task = New-ScheduledTask -Action $action -Trigger $trigger -Principal $principalObj -Settings $settings -Description "[A] Project Orchestrator High IL. Wrapper execs pythonw as a direct child so Task Scheduler /end authoritatively stops it. Auto-start at any user logon."

    Register-ScheduledTask -TaskName $taskName -InputObject $task | Out-Null

    $svc = New-Object -ComObject Schedule.Service
    $svc.Connect()
    $folder = $svc.GetFolder('\')
    $registered = $folder.GetTask($taskName)
    $def = $registered.Definition
    $def.Settings.MultipleInstances = 3
    $folder.RegisterTaskDefinition($taskName, $def, 4, $null, $null, $def.Principal.LogonType) | Out-Null
    $policyXml = Export-ScheduledTask -TaskName $taskName
    if ($policyXml -notmatch '<MultipleInstancesPolicy>StopExisting</MultipleInstancesPolicy>') {
        throw "Task registered but MultipleInstancesPolicy is not StopExisting"
    }

    Write-Host ""
    Write-Host "[OK] Da tao task '$taskName' chay o RunLevel=Highest." -ForegroundColor Green
    Write-Host "[OK] MultipleInstancesPolicy=StopExisting (COM patch; PS enum khong co StopExisting)." -ForegroundColor Green
    Write-Host ""
    } else {
        $policyXml = Export-ScheduledTask -TaskName $taskName
        if ($policyXml -notmatch '<MultipleInstancesPolicy>StopExisting</MultipleInstancesPolicy>') {
            throw "Live task '$taskName' is not StopExisting; will not mutate it while :8083 is up"
        }
        $svc = New-Object -ComObject Schedule.Service
        $svc.Connect()
        $folder = $svc.GetFolder('\')
        $registered = $folder.GetTask($taskName)
        $def = $registered.Definition
        $def.Settings.Hidden = $true
        $act = $def.Actions.Item(1)
        $act.Path = $pyw
        $act.Arguments = $orchArg
        $act.WorkingDirectory = $scriptRoot
        $folder.RegisterTaskDefinition($taskName, $def, 4, $null, $null, $def.Principal.LogonType) | Out-Null
        Write-Host "[OK] Giu task '$taskName' dang chay. MultipleInstancesPolicy=StopExisting. Hidden=true. Action=pythonw high_il_launch." -ForegroundColor Green
    }

    # ---------- ToolA_ForceRestart: short High-IL stop-gate then /run Orchestrator ----------
    $existingForce = Get-ScheduledTask -TaskName $forceName -ErrorAction SilentlyContinue
    if ($existingForce) {
        $svc = New-Object -ComObject Schedule.Service
        $svc.Connect()
        $folder = $svc.GetFolder('\')
        $registered = $folder.GetTask($forceName)
        $def = $registered.Definition
        $def.Settings.Hidden = $true
        $act = $def.Actions.Item(1)
        $act.Path = $pyw
        $act.Arguments = $forceArg
        $act.WorkingDirectory = $scriptRoot
        $folder.RegisterTaskDefinition($forceName, $def, 4, $null, $null, $def.Principal.LogonType) | Out-Null
        Write-Host "[OK] Cap nhat task '$forceName' COM TASK_UPDATE. Action=pythonw high_il_launch --force. Khong Unregister." -ForegroundColor Green
    } else {
        $forceAction = New-ScheduledTaskAction -Execute $pyw -Argument $forceArg -WorkingDirectory $scriptRoot
        $forceSettings = New-ScheduledTaskSettingsSet -Hidden -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -MultipleInstances Queue -ExecutionTimeLimit (New-TimeSpan -Minutes 2)
        $forceTask = New-ScheduledTask -Action $forceAction -Principal $principalObj -Settings $forceSettings -Description "[A] Short High-IL force restart. Runs high_il_launch --force (stop-gate then schtasks /run ToolA_Orchestrator). No logon trigger. Queue. ~2 min limit."
        Register-ScheduledTask -TaskName $forceName -InputObject $forceTask | Out-Null
        $forceXml = Export-ScheduledTask -TaskName $forceName
        if ($forceXml -match '<LogonTrigger>') {
            throw "ToolA_ForceRestart must not have a logon trigger"
        }
        Write-Host "[OK] Da tao task '$forceName' Highest + Queue, khong logon, ExecutionTimeLimit ~2 min." -ForegroundColor Green
    }

    # ---------- ToolA_RestartControl: direct pythonw child, no cmd.exe ----------
    $existingControl = Get-ScheduledTask -TaskName $controlName -ErrorAction SilentlyContinue
    if ($existingControl) {
        $svc = New-Object -ComObject Schedule.Service
        $svc.Connect()
        $folder = $svc.GetFolder('\')
        $registered = $folder.GetTask($controlName)
        $def = $registered.Definition
        $def.Settings.Hidden = $true
        $act = $def.Actions.Item(1)
        $act.Path = $pyw
        $act.Arguments = $controlArg
        $act.WorkingDirectory = $scriptRoot
        $folder.RegisterTaskDefinition($controlName, $def, 4, $null, $null, $def.Principal.LogonType) | Out-Null
        Write-Host "[OK] Cap nhat task '$controlName' COM TASK_UPDATE. Action=pythonw restart_control_service. Khong Unregister." -ForegroundColor Green
    } else {
        $controlAction = New-ScheduledTaskAction -Execute $pyw -Argument $controlArg -WorkingDirectory $scriptRoot
        $controlTrigger = New-ScheduledTaskTrigger -AtLogOn
        $controlSettings = New-ScheduledTaskSettingsSet -Hidden -MultipleInstances IgnoreNew -RestartCount 3 -RestartInterval (New-TimeSpan -Minutes 1)
        $controlTask = New-ScheduledTask -Action $controlAction -Trigger $controlTrigger -Principal $principalObj -Settings $controlSettings -Description "Independent loopback restart controller on 127.0.0.1:8085. pythonw is a direct child so /end owns the job."
        Register-ScheduledTask -TaskName $controlName -InputObject $controlTask | Out-Null
        $installedControl = Get-ScheduledTask -TaskName $controlName -ErrorAction Stop
        if ($installedControl.TaskName -ne $controlName) { throw 'restart control task verification failed' }
    }

    $installedControl = Get-ScheduledTask -TaskName $controlName -ErrorAction Stop
    if ($installedControl.TaskName -ne $controlName) { throw 'restart control task verification failed' }

    # Admin-kill :8085 orphan when that PID is not an :8083 owner (do not kill leftover --mcp).
    $own8083 = @()
    $c8083 = Get-NetTCPConnection -State Listen -LocalPort 8083 -ErrorAction SilentlyContinue
    if ($c8083) { $own8083 = @($c8083 | ForEach-Object { $_.OwningProcess } | Select-Object -Unique) }
    $c8085 = Get-NetTCPConnection -State Listen -LocalPort 8085 -ErrorAction SilentlyContinue
    if ($c8085) {
        foreach ($own in @($c8085 | ForEach-Object { $_.OwningProcess } | Select-Object -Unique)) {
            if ($own8083 -notcontains $own) {
                Write-Host "[*] Admin-kill :8085 orphan PID $own (khong so huu :8083)." -ForegroundColor Yellow
                & taskkill.exe /F /PID $own 2>$null | Out-Null
            }
        }
    }
    try {
        Start-ScheduledTask -TaskName $controlName -ErrorAction Stop
        Write-Host "[OK] Start-ScheduledTask '$controlName'." -ForegroundColor Green
    } catch {
        Write-Host "[WARN] Start-ScheduledTask '$controlName' fail: $($_.Exception.Message)" -ForegroundColor Yellow
    }

    Write-Host ""
    Write-Host "Tu gio:" -ForegroundColor Cyan
    Write-Host "  - Double-click start_orchestrator.bat -> Path A :8085 / Path B ForceRestart / Path C /run (KHONG UAC)." -ForegroundColor Cyan
    Write-Host "  - CA goi tool 'restart_orchestrator_via_bat' (luon goi [A:S], KHONG UAC)." -ForegroundColor Cyan
    Write-Host "  - desktop_click vao Notepad / Task Manager / Explorer admin -> OK qua UIPI." -ForegroundColor Cyan
    Write-Host ""
    Write-Host "Go bo task khi can: Unregister-ScheduledTask -TaskName $taskName -Confirm:`$false" -ForegroundColor DarkGray
    Write-Host ""

    # Kiem tra task vua tao
    $verify = Get-ScheduledTask -TaskName $taskName -ErrorAction SilentlyContinue
    if ($verify) {
        $trgCount = @($verify.Triggers).Count
        Write-Host "[OK] Verify: task ton tai, State=$($verify.State), RunLevel=Highest, Triggers=$trgCount (AtLogOn -> auto-start khi logon), MultipleInstances=StopExisting" -ForegroundColor Green
    } else {
        Write-Host "[WARN] Khong verify duoc task vua tao." -ForegroundColor Yellow
    }

    Write-Host ""
    if (-not $NonInteractive) { Read-Host "Hoan tat. Nhan Enter de dong cua so" }
    exit 0

} catch {
    Write-Host ""
    Write-Host "[LOI] Loi trong qua trinh cai task:" -ForegroundColor Red
    Write-Host "      $($_.Exception.Message)" -ForegroundColor Red
    Write-Host ""
    Write-Host "Stack trace:" -ForegroundColor DarkGray
    Write-Host $_.ScriptStackTrace -ForegroundColor DarkGray
    Write-Host ""
    if (-not $NonInteractive) { Read-Host "Nhan Enter de dong cua so" }
    exit 1
}
