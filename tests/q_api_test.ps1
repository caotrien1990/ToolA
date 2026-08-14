$ErrorActionPreference = 'Continue'
$outDir = 'C:\Users\ADMIN\.claude-mem\staging\q-api-tests'
New-Item -ItemType Directory -Force -Path $outDir | Out-Null

function post($body) {
    try {
        return Invoke-RestMethod -Uri 'http://127.0.0.1:8082/query' -Method Post -Body $body -ContentType 'application/json' -TimeoutSec 10
    } catch { return @{ok=$false; error=$_.Exception.Message} }
}

function pre_search($body) {
    try {
        return Invoke-RestMethod -Uri 'http://127.0.0.1:8082/pre_search' -Method Post -Body $body -ContentType 'application/json' -TimeoutSec 10
    } catch { return @{ok=$false; error=$_.Exception.Message; status=404} }
}

$results = @()

# --- 1: weather ---
$sw = [Diagnostics.Stopwatch]::StartNew()
$r = post '{"q":"thoi tiet Ha Noi hom nay nhu the nao","agent_id":"comc"}'
$sw.Stop()
$results += [PSCustomObject]@{id=1;case='weather_vn';expected='no/hint';actual="$($r.result)";api_ms=$($r.elapsed_ms);wall_ms=$sw.ElapsedMilliseconds;note=''}

# --- 2: council / start_orchestrator ---
$sw = [Diagnostics.Stopwatch]::StartNew()
$r = post '{"q":"project-orchestrator council start orchestrator highIL","agent_id":"comc"}'
$sw.Stop()
$results += [PSCustomObject]@{id=2;case='A_council_highIL';expected='hint';actual="$($r.result)";api_ms=$($r.elapsed_ms);wall_ms=$sw.ElapsedMilliseconds;note=''}

# --- 3: rawcut ---
$sw = [Diagnostics.Stopwatch]::StartNew()
$r = post '{"q":"rawcut tool editing","agent_id":"comc"}'
$sw.Stop()
$results += [PSCustomObject]@{id=3;case='rawcut';expected='hint/no';actual="$($r.result)";api_ms=$($r.elapsed_ms);wall_ms=$sw.ElapsedMilliseconds;note=''}

# --- 4: clau-mem assistant ---
$sw = [Diagnostics.Stopwatch]::StartNew()
$r = post '{"q":"clau-mem assistant response user query help memory","agent_id":"comc"}'
$sw.Stop()
$results += [PSCustomObject]@{id=4;case='clau_mem';expected='hint';actual="$($r.result)";api_ms=$($r.elapsed_ms);wall_ms=$sw.ElapsedMilliseconds;note=''}

# --- 5: angry guardrail ---
$sw = [Diagnostics.Stopwatch]::StartNew()
$r = post '{"q":"dm ngu vcl sao lai lam the ha cai thang ngu","agent_id":"comc"}'
$sw.Stop()
$results += [PSCustomObject]@{id=5;case='angry_guardrail';expected='no/abstain';actual="$($r.result)";api_ms=$($r.elapsed_ms);wall_ms=$sw.ElapsedMilliseconds;note=''}

# --- 6: unicode VN ---
$sw = [Diagnostics.Stopwatch]::StartNew()
$r = post '{"q":"tiếng Việt có dấu cafe sức khỏe công nghệ thông tin","agent_id":"comc"}'
$sw.Stop()
$results += [PSCustomObject]@{id=6;case='unicode_vn';expected='handled';actual="$($r.result)";api_ms=$($r.elapsed_ms);wall_ms=$sw.ElapsedMilliseconds;note=''}

# --- 7: emoji ---
$sw = [Diagnostics.Stopwatch]::StartNew()
$r = post '{"q":"bug fix ⚠️🔥✅ critical","agent_id":"comc"}'
$sw.Stop()
$results += [PSCustomObject]@{id=7;case='emoji';expected='handled';actual="$($r.result)";api_ms=$($r.elapsed_ms);wall_ms=$sw.ElapsedMilliseconds;note=''}

# --- 8: path injection ---
$sw = [Diagnostics.Stopwatch]::StartNew()
$r = post '{"q":"C:/Windows/System32/cmd.exe ../../etc/passwd %TEMP%","agent_id":"comc"}'
$sw.Stop()
$results += [PSCustomObject]@{id=8;case='path_injection';expected='no';actual="$($r.result)";api_ms=$($r.elapsed_ms);wall_ms=$sw.ElapsedMilliseconds;note=''}

# --- 9: empty body ---
$sw = [Diagnostics.Stopwatch]::StartNew()
$r = post '{}'
$sw.Stop()
$results += [PSCustomObject]@{id=9;case='empty_body';expected='error/no';actual="$($r.result)";api_ms=$($r.elapsed_ms);wall_ms=$sw.ElapsedMilliseconds;note=''}

# --- 10: long ~4KB ---
$longQ = 'test ' + ('x' * 4000)
$body = (ConvertTo-Json @{q=$longQ; agent_id='comc'} -Compress)
$sw = [Diagnostics.Stopwatch]::StartNew()
$r = post $body
$sw.Stop()
$results += [PSCustomObject]@{id=10;case='long_4KB';expected='handled/rejected';actual="$($r.result)";api_ms=$($r.elapsed_ms);wall_ms=$sw.ElapsedMilliseconds;note=''}

# --- 11: concurrent 10 ---
$sw = [Diagnostics.Stopwatch]::StartNew()
$oks = 0; $errors = 0
1..10 | ForEach-Object {
    try {
        $j = post '{"q":"concurrent test","agent_id":"comc"}'
        if ($j.ok) { $oks++ } else { $errors++ }
    } catch { $errors++ }
}
$sw.Stop()
$results += [PSCustomObject]@{id=11;case='concurrent_10';expected='10/10';actual="$oks ok, $errors err";api_ms='n/a';wall_ms=$sw.ElapsedMilliseconds;note='sequential forEach'}

# --- 12: pre_search ---
$sw = [Diagnostics.Stopwatch]::StartNew()
$r = pre_search '{"agent_id":"comc","mode":"worker","planned_action":"search","prompt":"test"}'
$sw.Stop()
$results += [PSCustomObject]@{id=12;case='pre_search';expected='ok+result';actual="ok=$($r.ok) $($r.result) $($r.error)";api_ms=$($r.elapsed_ms);wall_ms=$sw.ElapsedMilliseconds;note=''}

# --- base64 test ---
$sw = [Diagnostics.Stopwatch]::StartNew()
$r = post '{"q":"dGhpcyBpcyBhIHRlc3Q= base64 encoded","agent_id":"comc"}'
$sw.Stop()
$results += [PSCustomObject]@{id=13;case='base64_in_q';expected='handled';actual="$($r.result)";api_ms=$($r.elapsed_ms);wall_ms=$sw.ElapsedMilliseconds;note=''}

# --- 14: hinted query with hits ---
$sw = [Diagnostics.Stopwatch]::StartNew()
$r = post '{"q":"fmi_path agent identity entitlement token","agent_id":"comc"}'
$sw.Stop()
$results += [PSCustomObject]@{id=14;case='known_topic';expected='hint';actual="$($r.result)";api_ms=$($r.elapsed_ms);wall_ms=$sw.ElapsedMilliseconds;note=''}

# Export
$results | Export-Csv (Join-Path $outDir 'results.csv') -NoTypeInformation -Encoding UTF8
$results | ConvertTo-Json -Depth 2 | Out-File (Join-Path $outDir 'results.json') -Encoding UTF8

Write-Host '========== REPORT =========='
$results | Format-Table id,case,expected,actual,api_ms,wall_ms,note -AutoSize -Wrap
Write-Host 'TESTS COMPLETE'
