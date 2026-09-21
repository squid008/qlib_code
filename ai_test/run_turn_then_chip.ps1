# Chain: step2 (build turn) -> step3 (re-materialize chip). 2026-09-21, v1.20.44.
#
# ASCII ONLY on purpose: PowerShell 5.1 reads .ps1 as GBK unless there is a BOM,
# and a UTF-8-no-BOM file with Chinese text fails to parse ("string missing terminator").
#
# Why chained and not parallel: chip_turn_of() prefers the 'turn' field, so the
# materialize step must run AFTER turn is complete, otherwise the newly filled
# stocks would be materialized with the old (proxy) convention again.
$ErrorActionPreference = 'Continue'
$ai = 'D:\quant\qlib_code\ai_test'
$py = 'D:\miniconda3\envs\qlib\python.exe'
$log = Join-Path $ai 'build_turn.out.txt'
$chain = Join-Path $ai 'run_turn_then_chip.out.txt'
$env:PYTHONIOENCODING = 'utf-8'

function Say($m) {
    $line = ("[{0}] {1}" -f (Get-Date -Format 'HH:mm:ss'), $m)
    Add-Content -Path $chain -Value $line -Encoding utf8
}

Say "start: waiting for step2 (build_turn_field) to finish"
$deadline = (Get-Date).AddHours(8)
$done = $false
while ((Get-Date) -lt $deadline) {
    if (Test-Path $log) {
        $tail = Get-Content $log -Encoding utf8 -Tail 4 -ErrorAction SilentlyContinue
        if ($tail -match 'stage 6/6') { $done = $true; break }
    }
    $alive = Get-CimInstance Win32_Process -Filter "Name='python.exe'" -ErrorAction SilentlyContinue |
        Where-Object { $_.CommandLine -like '*build_turn_field*' }
    if (-not $alive -and (Test-Path $log)) {
        $t2 = Get-Content $log -Encoding utf8 -Tail 6 -ErrorAction SilentlyContinue
        if ($t2 -notmatch 'stage 6/6') {
            Say "WARN: build_turn_field process gone but log has no stage 6/6, tail:"
            $t2 | ForEach-Object { Say ("    " + $_) }
            Say "stop: step3 not launched"
            exit 1
        }
    }
    Start-Sleep -Seconds 30
}
if (-not $done) { Say "TIMEOUT after 8h - stop, step3 not launched"; exit 1 }

Say "step2 done -> launching step3: materialize_chip 400 --overwrite (full pool)"
Set-Location 'D:\quant\qlib_code'
& $py 'backend\tools\materialize_chip.py' 400 --overwrite 2>&1 |
    ForEach-Object { Add-Content -Path $chain -Value ("    " + $_) -Encoding utf8 }

Say "step3 finished -> running verify_materialized"
& $py 'backend\tools\verify_materialized.py' 2>&1 |
    ForEach-Object { Add-Content -Path $chain -Value ("    " + $_) -Encoding utf8 }

Say "ALL DONE: turn filled + chip re-materialized + verify -> restart backend to apply v1.20.44"
