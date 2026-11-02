# Standalone observer. BUILD ONLY: deployment is a separately approved action.
param(
    [Parameter(Mandatory=$true)][string]$Config,
    [Parameter(Mandatory=$true)][string]$PythonExe,
    [ValidateRange(0,2147483647)][int]$MaxPolls = 0
)
$ErrorActionPreference = 'Stop'
$env:PYTHONIOENCODING = 'utf-8'
& $PythonExe (Join-Path $PSScriptRoot 'layer2_watch.py') --config $Config --max-polls $MaxPolls
exit $LASTEXITCODE
