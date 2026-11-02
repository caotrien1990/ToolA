# Read-only capture: no daemon calls, file deletions, process kills or policy writes.
param(
    [Parameter(Mandatory=$true)][string]$Config,
    [Parameter(Mandatory=$true)][string]$PythonExe
)
$ErrorActionPreference = 'Stop'
$env:PYTHONIOENCODING = 'utf-8'
& $PythonExe (Join-Path $PSScriptRoot 'layer2_watch.py') --config $Config --capture
exit $LASTEXITCODE
