param(
  [string]$Room = "D:\BR",
  [string]$ABase = "http://localhost:8083",
  [int]$TimeoutSec = 6000
)

$ErrorActionPreference = "Stop"

function Invoke-A {
  param(
    [string]$Tool,
    [hashtable]$Payload
  )
  $json = $Payload | ConvertTo-Json -Compress -Depth 20
  $bytes = [Text.Encoding]::UTF8.GetBytes($json)
  Invoke-RestMethod -Method Post -Uri "$ABase/api/call/$Tool" -ContentType "application/json; charset=utf-8" -Body $bytes
}

function Set-CouncilWorkerModel {
  param(
    [ValidateSet("codo","pic","ope","hermes","kilo","clau")]
    [string]$Agent,
    [string]$Model
  )
  if ([string]::IsNullOrWhiteSpace($Model)) { throw "Model is required" }
  Invoke-A "council_worker_model" @{ action="set"; agent=$Agent; model=$Model } | Out-Null
  $current = Invoke-A "council_worker_model" @{ action="get"; agent=$Agent }
  if ($current.current.model -ne $Model) {
    throw "$Agent model mismatch. Expected $Model, got $($current.current.model)"
  }
  return $current
}

function Send-CouncilMessage {
  param(
    [string]$To,
    [string]$Text,
    [string]$Sender = "codo"
  )
  Invoke-A "council_message" @{
    sender=$Sender
    to=$To
    text=$Text
    timeout_sec=$TimeoutSec
    queue=$true
    queue_wait_timeout_sec=$TimeoutSec
  }
}

function Add-BrokerDispatchLedgerEntry {
  param(
    [string]$LedgerPath = "$Room\final\BROKER_DISPATCH_LEDGER.md",
    [string]$Worker,
    [string]$Identity,
    [string]$Sender,
    [string]$PromptFile,
    [string]$OutputPath,
    [object]$BrokerResponse,
    [object]$ArtifactStatus,
    [string]$NextAction
  )
  $sessionId = ""
  if ($BrokerResponse -and $BrokerResponse.PSObject.Properties.Name -contains "session_id") {
    $sessionId = [string]$BrokerResponse.session_id
  }
  if ([string]::IsNullOrWhiteSpace($sessionId)) {
    $sessionId = "unknown/not_exposed"
  }

  $ok = if ($BrokerResponse -and $BrokerResponse.PSObject.Properties.Name -contains "ok") { $BrokerResponse.ok } else { "unknown" }
  $returncode = if ($BrokerResponse -and $BrokerResponse.PSObject.Properties.Name -contains "returncode") { $BrokerResponse.returncode } else { "unknown" }
  $eventId = if ($BrokerResponse -and $BrokerResponse.PSObject.Properties.Name -contains "id") { $BrokerResponse.id } else { "unknown" }
  $replyTo = if ($BrokerResponse -and $BrokerResponse.PSObject.Properties.Name -contains "in_reply_to") { $BrokerResponse.in_reply_to } else { "unknown" }
  $text = if ($BrokerResponse -and $BrokerResponse.text) { [string]$BrokerResponse.text } else { "" }
  $class = "unknown"
  if ($text -match "usage limit|hit your usage limit") { $class = "usage_limit" }
  elseif ($text -match "529|Overloaded") { $class = "overload" }
  elseif ($text -match "AGENT_ERROR|CRITICAL|Uncaught Exception") { $class = "agent_error" }
  elseif ($text -match "\$promptFile|\$own|\$peer|__OWN__|PROMPT_FILE_PATH") { $class = "placeholder_leak" }
  elseif ($ArtifactStatus -and $ArtifactStatus.ok) { $class = "artifact_ok" }
  elseif ($ArtifactStatus) { $class = "artifact_missing" }

  New-Item -ItemType Directory -Force -Path (Split-Path -Parent $LedgerPath) | Out-Null
  $artifactLine = if ($ArtifactStatus) { "$($ArtifactStatus.reason); length=$($ArtifactStatus.length); path=$($ArtifactStatus.path)" } else { "not_checked" }
  $snippet = ($text -replace "`r?`n"," ").Trim()
  if ($snippet.Length -gt 500) { $snippet = $snippet.Substring(0,500) + "..." }
  $entry = @"

## $(Get-Date -Format "yyyy-MM-dd HH:mm:ss")
- worker: $Worker
- identity: $Identity
- sender: $Sender
- prompt_file: $PromptFile
- output_path: $OutputPath
- broker_event_id: $eventId
- broker_in_reply_to: $replyTo
- ok: $ok
- returncode: $returncode
- session_id: $sessionId
- response_class: $class
- artifact: $artifactLine
- next_action: $NextAction
- response_excerpt: $snippet
"@
  Add-Content -LiteralPath $LedgerPath -Value $entry -Encoding UTF8
  return [pscustomobject]@{ session_id=$sessionId; response_class=$class; ledger=$LedgerPath }
}

function Test-Artifact {
  param([string]$Path)
  if (!(Test-Path -LiteralPath $Path)) {
    return [pscustomobject]@{ ok=$false; reason="missing"; path=$Path; length=0 }
  }
  $item = Get-Item -LiteralPath $Path
  if ($item.Length -le 0) {
    return [pscustomobject]@{ ok=$false; reason="zero_length"; path=$Path; length=$item.Length }
  }
  return [pscustomobject]@{ ok=$true; reason="ok"; path=$Path; length=$item.Length }
}

function Get-HermCRequests {
  param([string]$RoundPath)
  Get-ChildItem -LiteralPath $RoundPath -Filter "*.md" | ForEach-Object {
    $matches = Select-String -LiteralPath $_.FullName -Pattern "HermC request|Optional HermC" -Context 0,8
    if ($matches) {
      [pscustomobject]@{ file=$_.FullName; excerpt=($matches | ForEach-Object { $_.ToString() }) -join "`n" }
    }
  }
}

function Get-CouncilRuntimeState {
  param([string]$OutPath = "")
  $resp = Invoke-A "read_file_range" @{ path=".council\runtime_state.json"; start_line=1; end_line=200 }
  if ($OutPath) {
    $resp | ConvertTo-Json -Depth 20 | Set-Content -LiteralPath $OutPath -Encoding UTF8
  }
  return $resp
}

function New-RoomTree {
  param([int]$Rounds = 5)
  New-Item -ItemType Directory -Force -Path "$Room\DATA" | Out-Null
  foreach ($i in 1..$Rounds) {
    New-Item -ItemType Directory -Force -Path "$Room\round$i" | Out-Null
  }
  New-Item -ItemType Directory -Force -Path "$Room\final" | Out-Null
}

Write-Output "Loaded council brainstorm helper. Available functions:"
Write-Output "- New-RoomTree"
Write-Output "- Set-CouncilWorkerModel"
Write-Output "- Send-CouncilMessage"
Write-Output "- Add-BrokerDispatchLedgerEntry"
Write-Output "- Test-Artifact"
Write-Output "- Get-HermCRequests"
Write-Output "- Get-CouncilRuntimeState"
