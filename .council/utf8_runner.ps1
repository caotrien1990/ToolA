# .council\utf8_runner.ps1
# Robust UTF-8 worker runner for council CLI commands.
# Captures stdout+stderr as UTF-8 (no BOM); closes stdin; preserves arg quoting
# by receiving the command as POSITIONAL args (no fragile -Command string rebuild).
#
# Primary usage (preferred, via utf8_runner.bat which forwards %* verbatim):
#   powershell -NoProfile -ExecutionPolicy Bypass -File ".council\utf8_runner.ps1" <logfile> <exe> <args...>
#
# Dot-source helper:
#   . '.council\utf8_runner.ps1'; Invoke-Utf8Captured -ScriptBlock { codex exec "..." } -LogPath <log>

param(
    [Parameter(Position = 0)][string]$LogPath = "",
    [string]$B64 = "",
    [Parameter(ValueFromRemainingArguments = $true)][string[]]$Rest = @()
)

# === UTF-8 preamble (decode child stdout/stderr as UTF-8, emit UTF-8) ===
[Console]::OutputEncoding = [Console]::InputEncoding = [Text.Encoding]::UTF8
$global:OutputEncoding = [Text.Encoding]::UTF8
$env:PYTHONIOENCODING = 'UTF-8'
& chcp 65001 | Out-Null

# Quote a single token for a cmd.exe /c command line (handles spaces, quotes, backslashes).
function global:Quote-CmdArg([string]$a) {
    if ($null -eq $a -or $a -eq '') { return '""' }
    if ($a -match '[ \t"]') {
        $e = ($a -replace '(\\*)"', '$1$1\"')
        $e = ($e -replace '(\\*)$', '$1$1')
        return '"' + $e + '"'
    }
    return $a
}

# Dot-source form (UTF-8 captured scriptblock).
function global:Invoke-Utf8Captured {
    param([scriptblock]$ScriptBlock, [string]$LogPath, [switch]$Passthru)
    [Console]::OutputEncoding = [Console]::InputEncoding = [Text.Encoding]::UTF8
    $global:OutputEncoding = [Text.Encoding]::UTF8
    $env:PYTHONIOENCODING = 'UTF-8'
    & chcp 65001 | Out-Null
    $output = & $ScriptBlock 2>&1
    $exitcode = $LASTEXITCODE
    $text = $output | Out-String
    if ($Passthru) { return $text }
    [System.IO.File]::WriteAllText($LogPath, $text, [System.Text.UTF8Encoding]::new($false))
    [System.IO.File]::AppendAllText($LogPath, "`r`nEXIT_CODE=${exitcode}", [System.Text.UTF8Encoding]::new($false))
    if ($global:? -and $exitcode -eq 0) { return $true } else { return $false }
}

# === Standalone mode ===
# Command source: -B64 <base64 utf-8 full command> (robust, preferred for prompts
# with flags/spaces/Unicode) OR positional <exe> <args...> (simple ASCII only).
$cmdline = ""
if ($B64) {
    try { $cmdline = [Text.Encoding]::UTF8.GetString([Convert]::FromBase64String($B64)) } catch { $cmdline = "" }
} elseif ($Rest.Count -ge 1) {
    $cmdline = (@($Rest) | ForEach-Object { Quote-CmdArg $_ }) -join ' '
}

if ($LogPath -and $cmdline) {
    $psi = New-Object System.Diagnostics.ProcessStartInfo
    $psi.FileName = "cmd.exe"
    $psi.Arguments = "/c " + $cmdline
    $psi.UseShellExecute = $false
    $psi.RedirectStandardOutput = $true
    $psi.RedirectStandardError = $true
    $psi.RedirectStandardInput = $true
    $psi.CreateNoWindow = $true
    $psi.WorkingDirectory = (Get-Location).Path
    $psi.StandardOutputEncoding = [Text.Encoding]::UTF8
    $psi.StandardErrorEncoding = [Text.Encoding]::UTF8

    $p = [System.Diagnostics.Process]::Start($psi)
    try { $p.StandardInput.Close() } catch {}

    $utf8NoBom = [System.Text.UTF8Encoding]::new($false)
    $stderrPath = $LogPath + ".stderr.tmp"
    $stdoutStream = [System.IO.FileStream]::new($LogPath, [System.IO.FileMode]::Create, [System.IO.FileAccess]::Write, [System.IO.FileShare]::ReadWrite, 1, [System.IO.FileOptions]::WriteThrough)
    $stderrStream = [System.IO.FileStream]::new($stderrPath, [System.IO.FileMode]::Create, [System.IO.FileAccess]::Write, [System.IO.FileShare]::ReadWrite, 1, [System.IO.FileOptions]::WriteThrough)
    $stdoutTask = $p.StandardOutput.BaseStream.CopyToAsync($stdoutStream)
    $stderrTask = $p.StandardError.BaseStream.CopyToAsync($stderrStream)
    $p.WaitForExit()
    $stdoutTask.Wait()
    $stderrTask.Wait()
    $exitcode = $p.ExitCode
    $stdoutStream.Dispose()
    $stderrStream.Dispose()

    if ((Get-Item $stderrPath).Length -gt 0) {
        [System.IO.File]::AppendAllText($LogPath, "`r`n", $utf8NoBom)
        $targetStream = [System.IO.File]::Open($LogPath, [System.IO.FileMode]::Append, [System.IO.FileAccess]::Write, [System.IO.FileShare]::ReadWrite)
        $sourceStream = [System.IO.File]::OpenRead($stderrPath)
        $sourceStream.CopyTo($targetStream)
        $sourceStream.Dispose()
        $targetStream.Dispose()
    }
    Remove-Item -LiteralPath $stderrPath -Force -ErrorAction SilentlyContinue
    [System.IO.File]::AppendAllText($LogPath, "`r`nEXIT_CODE=${exitcode}`r`n", $utf8NoBom)
    exit $exitcode
}

Write-Host "utf8_runner.ps1 - usage: -File utf8_runner.ps1 <logfile> <exe> <args...>"
