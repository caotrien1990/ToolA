<#
.SYNOPSIS
  Orchestrator API example for write_fix dry-run/apply.

.PURPOSE
  Demonstrates the safe API pattern for write_fix:
  - Uses Invoke-RestMethod only.
  - Fetches token only because write_fix is protected.
  - Sends UTF-8 content as base64.
  - Runs dry-run by default.
  - Applies only with explicit -Apply.

.GUARDRAILS
  - This script is API transport only.
  - Do NOT use this script to read/edit project files directly.
  - Do NOT use Get-Content/Set-Content/Remove-Item/Select-String/Get-ChildItem for project files.
  - Prereq: health OK.
  - Prereq: open_session + get_project_summary completed for target project.
  - Prereq for write_fix: read_file_range first; replace sample start_line/end_line.
  - project_path = target project root, not necessarily [A:P].
  - file_path = relative path inside project_path; never absolute.
  - After dry-run OK, rerun with -Apply to send dry_run=false.

.EXAMPLE
  Dry-run:
    powershell -NoProfile -ExecutionPolicy Bypass -File ".\write_fix_dry_run.ps1" `
      -ProjectPath "D:/project" `
      -FilePath "src/app.js" `
      -StartLine 10 `
      -EndLine 20 `
      -NewContent "console.log('Xin chào tiếng Việt');"

.EXAMPLE
  Apply after dry-run OK:
    powershell -NoProfile -ExecutionPolicy Bypass -File ".\write_fix_dry_run.ps1" `
      -ProjectPath "D:/project" `
      -FilePath "src/app.js" `
      -StartLine 10 `
      -EndLine 20 `
      -NewContent "console.log('Xin chào tiếng Việt');" `
      -Apply

.EXAMPLE
  Use pre-encoded base64 content:
    powershell -NoProfile -ExecutionPolicy Bypass -File ".\write_fix_dry_run.ps1" `
      -ProjectPath "D:/project" `
      -FilePath "src/app.js" `
      -StartLine 10 `
      -EndLine 20 `
      -NewContentBase64 "<base64-utf8-content>"
#>

param(
  [Parameter(Mandatory = $true)]
  [string] $ProjectPath,

  [Parameter(Mandatory = $true)]
  [string] $FilePath,

  [Parameter(Mandatory = $true)]
  [int] $StartLine,

  [Parameter(Mandatory = $true)]
  [int] $EndLine,

  [Parameter(Mandatory = $false)]
  [string] $NewContent,

  [Parameter(Mandatory = $false)]
  [string] $NewContentBase64,

  [Parameter(Mandatory = $false)]
  [switch] $Apply,

  [Parameter(Mandatory = $false)]
  [string] $BaseUrl = $env:ORCHESTRATOR_BASE_URL
)

$ErrorActionPreference = "Stop"

function Fail($Message) {
  Write-Error $Message
  exit 1
}

if ([string]::IsNullOrWhiteSpace($BaseUrl)) {
  $BaseUrl = "http://localhost:8083"
}

$BaseUrl = $BaseUrl.TrimEnd("/")

if ([string]::IsNullOrWhiteSpace($ProjectPath)) {
  Fail "ProjectPath is required. It must be the absolute target project root."
}

if ([string]::IsNullOrWhiteSpace($FilePath)) {
  Fail "FilePath is required. It must be relative inside ProjectPath."
}

if ([System.IO.Path]::IsPathRooted($FilePath)) {
  Fail "FilePath must be relative, not absolute. Use ProjectPath for the root."
}

if ($StartLine -lt 1) {
  Fail "StartLine must be >= 1."
}

if ($EndLine -lt $StartLine) {
  Fail "EndLine must be >= StartLine."
}

if (-not [string]::IsNullOrWhiteSpace($NewContent) -and -not [string]::IsNullOrWhiteSpace($NewContentBase64)) {
  Fail "Provide either NewContent or NewContentBase64, not both."
}

if ([string]::IsNullOrWhiteSpace($NewContent) -and [string]::IsNullOrWhiteSpace($NewContentBase64)) {
  Fail "Provide NewContent or NewContentBase64."
}

if ([string]::IsNullOrWhiteSpace($NewContentBase64)) {
  $utf8NoBom = New-Object System.Text.UTF8Encoding($false)
  $NewContentBase64 = [Convert]::ToBase64String($utf8NoBom.GetBytes($NewContent))
} else {
  try {
    [void][Convert]::FromBase64String($NewContentBase64)
  } catch {
    Fail "NewContentBase64 is not valid base64."
  }
}

$DryRun = -not $Apply

Write-Host "Orchestrator API: $BaseUrl"
Write-Host "Tool: write_fix"
Write-Host "Mode: $(if ($DryRun) { 'DRY-RUN' } else { 'APPLY' })"
Write-Host "ProjectPath: $ProjectPath"
Write-Host "FilePath: $FilePath"
Write-Host "LineRange: $StartLine-$EndLine"

try {
  $tokenResponse = Invoke-RestMethod `
    -Uri "$BaseUrl/api/token" `
    -Method GET `
    -ErrorAction Stop

  $token = $tokenResponse.token

  if ([string]::IsNullOrWhiteSpace($token)) {
    Fail "Token response did not include token."
  }

  $bodyObject = @{
    project_path = $ProjectPath
    file_path = $FilePath
    start_line = $StartLine
    end_line = $EndLine
    new_content = $NewContentBase64
    content_encoding = "base64"
    dry_run = $DryRun
  }

  $body = $bodyObject | ConvertTo-Json -Depth 20

  $response = Invoke-RestMethod `
    -Method Post `
    -Uri "$BaseUrl/api/call/write_fix" `
    -ContentType "application/json; charset=utf-8" `
    -Body $body `
    -Headers @{ "X-Orchestrator-Token" = $token } `
    -ErrorAction Stop

  $response | ConvertTo-Json -Depth 20

  if ($DryRun) {
    Write-Host ""
    Write-Host "Dry-run completed. Review diff/warnings. To apply, rerun with -Apply."
  } else {
    Write-Host ""
    Write-Host "Apply completed. Run validate_file, validate_symbols if supported, then one cache_delta for the batch."
  }
}
catch {
  Write-Error "write_fix API call failed: $($_.Exception.Message)"
  exit 1
}