# a.ps1 -- thin HTTP client for [A] (project-orchestrator), Windows PowerShell 5.1 safe.
#
#   . "<repo>\bin\a.ps1"
#   $AProject = 'D:\path\to\project'          # absolute; [A] never resolves by cwd
#   Call 'git_status' @{project_path = $AProject} 'ok,result'
#   RFR 'src/app.py' 10 40                    # read_file_range; .result is LINE-NUMBERED
#   WF  'src/app.py' 12 14 -Text $newLines    # write_fix, one region per call, bottom-up
#   WN  'docs/new.md' -SourceFile $draft      # write_new_file, never overwrites
#   Schema 'write_fix'                        # .tool.function.parameters
#
# Why a shared helper: every CA used to hand-roll this client, and each copy re-hit the
# same traps. WF and WN send a default fields= projection when the caller passes none
# (measured 2026-10: only 6% of write_fix calls passed fields=; the median answer was
# 2156B vs 1144B with fields=). A caller-provided -Fields wins; an explicit '' or '*'
# still means the FULL response. It keeps to three rules:
#   * The request body is UTF-8 bytes, and every write sends its text as base64. A plain
#     JSON string reached disk with Vietnamese text and box-drawing characters turned
#     into '?' under ok:true.
#   * The answer is decoded as UTF-8 whatever the response headers say.
#   * An HTTP 4xx/5xx from [A] still carries its structured answer (error_name,
#     corrected_payload, ...), so that answer is returned, not a PowerShell exception.
#     Connection refused has no answer and throws: that is the STOP signal.
#   * An answer that is not JSON comes back as ok=$false, error_name=NON_JSON_RESPONSE.
#     It used to parse to $null, and a $null answer read as an EMPTY git_status
#     staged_hash (2026-09-29).
# Variable names are case-insensitive: `$r = Call ...` overwrites `$R`. Keep the repo
# path in $AProject, never in a one-letter name a result can clobber.
# This file stays pure ASCII: PowerShell 5.1 reads a BOM-less script as the ANSI code
# page, so any other byte would change meaning on load.

$script:ABase = if ($env:ORCHESTRATOR_BASE_URL) { $env:ORCHESTRATOR_BASE_URL.TrimEnd('/') } else { 'http://localhost:8083' }
if (-not (Get-Variable -Name AProject -Scope Global -ErrorAction SilentlyContinue)) {
    $global:AProject = (Get-Location).Path
}

function Send-ARequest {
    param([string]$Method, [string]$Path, [byte[]]$Body = $null, [int]$TimeoutSec = 180, [switch]$Raw)
    $request = [Net.WebRequest]::Create($script:ABase + $Path)
    $request.Method = $Method
    # .NET waits up to 350 ms for a '100 Continue' before every POST body otherwise.
    $request.ServicePoint.Expect100Continue = $false
    $request.Timeout = $TimeoutSec * 1000
    $request.ReadWriteTimeout = $TimeoutSec * 1000
    if ($null -ne $Body) {
        $request.ContentType = 'application/json; charset=utf-8'
        $request.ContentLength = $Body.Length
        $stream = $request.GetRequestStream()
        try { $stream.Write($Body, 0, $Body.Length) } finally { $stream.Close() }
    }
    try {
        $response = $request.GetResponse()
    } catch {
        $err = $_.Exception
        while ($null -ne $err -and -not ($err -is [Net.WebException])) { $err = $err.InnerException }
        if ($null -eq $err -or $null -eq $err.Response) { throw }
        $response = $err.Response
    }
    $status = [int]$response.StatusCode
    try {
        $reader = New-Object IO.StreamReader($response.GetResponseStream(), (New-Object Text.UTF8Encoding $false))
        $text = $reader.ReadToEnd()
    } finally {
        $response.Close()
    }
    if ($Raw) { return $text }
    # 5.1 turns an EMPTY body into $null without an error, and a non-JSON one into a
    # parse error: both become the one named answer below, never $null.
    if (-not [string]::IsNullOrWhiteSpace($text)) {
        try { return (ConvertFrom-Json $text -ErrorAction Stop) } catch { }
    }
    $head = if ($text) { $text.Substring(0, [Math]::Min(300, $text.Length)) } else { '<empty body>' }
    [pscustomobject]@{ ok = $false; error_name = 'NON_JSON_RESPONSE'; http_status = $status
        error = 'HTTP ' + $status + ' answered non-JSON: ' + $head
        ca_next_action = 'report_this_answer_do_not_read_missing_fields_as_empty' }
}

function Invoke-A {
    param([Parameter(Mandatory = $true, Position = 0)][string]$Tool,
          [Parameter(Position = 1)][Collections.IDictionary]$Body = @{},
          [Parameter(Position = 2)][string]$Fields = '',
          [int]$TimeoutSec = 180, [switch]$Raw)
    $path = '/api/call/' + [uri]::EscapeDataString($Tool)
    if ($Fields) { $path += '?fields=' + [uri]::EscapeDataString($Fields) }
    $json = ConvertTo-Json -InputObject $Body -Compress -Depth 20
    Send-ARequest 'POST' $path ([Text.Encoding]::UTF8.GetBytes($json)) $TimeoutSec -Raw:$Raw
}

function Get-ASchema {
    param([Parameter(Mandatory = $true, Position = 0)][string]$Tool)
    Send-ARequest 'GET' ('/api/tools/' + [uri]::EscapeDataString($Tool) + '/schema')
}

function Get-ANames {
    param([Parameter(Position = 0)][string]$NameContains = '')
    $path = '/api/tools/names?fields=' + [uri]::EscapeDataString('tools,count')
    if ($NameContains) { $path += '&name_contains=' + [uri]::EscapeDataString($NameContains) }
    Send-ARequest 'GET' $path
}

# -Project defaults to $AProject as the CALLER sees it (PowerShell scoping is dynamic),
# so a script that sets its own $AProject after dot-sourcing this file is honored.
function Read-ARange {
    param([Parameter(Mandatory = $true, Position = 0)][string]$File,
          [Parameter(Position = 1)][int]$Start = 1,
          [Parameter(Position = 2)][int]$End = 0,
          [string]$Project = $AProject,
          [string]$Fields = 'ok,result,total_lines,error_name,error,ca_next_action',
          [switch]$ForceReseed)
    $body = @{ project_path = $Project; file_path = $File; start_line = $Start }
    if ($End -gt 0) { $body.end_line = $End }
    if ($ForceReseed) { $body.force_reseed = $true }
    Invoke-A 'read_file_range' $body $Fields
}

function ConvertTo-ABase64 {
    param([string]$Text, [string]$SourceFile)
    if ($SourceFile) { $Text = [IO.File]::ReadAllText($SourceFile, (New-Object Text.UTF8Encoding $false)) }
    [Convert]::ToBase64String([Text.Encoding]::UTF8.GetBytes($Text))
}

# write_fix takes the new text of lines Start..End as ONE region. Line numbers come from
# Read-ARange on the same file, and several regions go bottom-up, because an earlier
# region renumbers every line below it.
function Write-AFix {
    param([Parameter(Mandatory = $true, Position = 0)][string]$File,
          [Parameter(Mandatory = $true, Position = 1)][int]$Start,
          [Parameter(Mandatory = $true, Position = 2)][int]$End,
          [string]$Text, [string]$SourceFile,
          [string]$Project = $AProject, [string]$ExpectedHash = '',
          [string]$Fields = 'ok,new_hash,validation,error_name,error,ca_next_action,operation_id',
          [int]$TimeoutSec = 300)
    $body = @{ project_path = $Project; file_path = $File; start_line = $Start; end_line = $End
               new_content = (ConvertTo-ABase64 -Text $Text -SourceFile $SourceFile)
               content_encoding = 'base64' }
    if ($ExpectedHash) { $body.expected_hash = $ExpectedHash }
    Invoke-A 'write_fix' $body $Fields -TimeoutSec $TimeoutSec
}

function New-AFile {
    param([Parameter(Mandatory = $true, Position = 0)][string]$Path,
          [string]$Text, [string]$SourceFile,
          [string]$Project = $AProject,
          [string]$Fields = 'ok,file,new_hash,lines_written,utf8_valid,error_name,error,ca_next_action,operation_id',
          [int]$TimeoutSec = 300)
    $body = @{ project_path = $Project; path = $Path
               new_content = (ConvertTo-ABase64 -Text $Text -SourceFile $SourceFile)
               content_encoding = 'base64' }
    Invoke-A 'write_new_file' $body $Fields -TimeoutSec $TimeoutSec
}

Set-Alias -Name Call -Value Invoke-A
Set-Alias -Name Schema -Value Get-ASchema
Set-Alias -Name Names -Value Get-ANames
Set-Alias -Name RFR -Value Read-ARange
Set-Alias -Name WF -Value Write-AFix
Set-Alias -Name WN -Value New-AFile
