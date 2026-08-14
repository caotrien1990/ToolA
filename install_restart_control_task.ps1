$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $MyInvocation.MyCommand.Path
$wrapper = Join-Path $root 'start_restart_control_high_il.cmd'
if (-not (Test-Path -LiteralPath $wrapper)) { throw 'restart control wrapper missing' }
$action = New-ScheduledTaskAction -Execute "$env:SystemRoot\System32\cmd.exe" -Argument "/c `"$wrapper`"" -WorkingDirectory $root
$trigger = New-ScheduledTaskTrigger -AtLogOn
$principal = New-ScheduledTaskPrincipal -UserId $env:USERNAME -LogonType Interactive -RunLevel Highest
$settings = New-ScheduledTaskSettingsSet -MultipleInstances IgnoreNew -RestartCount 3 -RestartInterval (New-TimeSpan -Minutes 1)
Register-ScheduledTask -TaskName 'ToolA_RestartControl' -Action $action -Trigger $trigger -Principal $principal -Settings $settings -Description 'Independent loopback restart controller on 127.0.0.1:8085' -Force | Out-Null
$installed = Get-ScheduledTask -TaskName 'ToolA_RestartControl' -ErrorAction Stop
if ($installed.TaskName -ne 'ToolA_RestartControl') { throw 'restart control task verification failed' }
$installedAction = @($installed.Actions)[0]
$expectedExecute = "$env:SystemRoot\System32\cmd.exe"
$expectedArguments = "/c `"$wrapper`""
if ($installedAction.Execute -ne $expectedExecute -or
    $installedAction.Arguments -ne $expectedArguments -or
    $installedAction.WorkingDirectory -ne $root) {
    throw 'restart control task action verification failed'
}
Write-Host 'Installed ToolA_RestartControl. The existing ToolA_Orchestrator task was not modified.'
