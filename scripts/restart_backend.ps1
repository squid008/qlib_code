# restart_backend.ps1 - stop every backend instance, then start exactly one.
#
# ASCII ONLY. PowerShell 5.1 reads BOM-less UTF-8 as ANSI, so Chinese text in here
# turns into garbage and (if it lands in a string) can even break output. Keep this
# file plain ASCII; write explanations in md/ instead.
#
# WHY THIS SCRIPT EXISTS (v2.10.88, 2026-10-21)
# ---------------------------------------------
# The old version killed only the process *listening on 8001*. Every instance that
# failed to grab the port (or was mid-shutdown) stayed alive as a zombie:
#   observed 3 uvicorn instances - PID 7452 (1573 MB, started 09:08),
#   PID 12492 (222 MB, 13:55), PID 29676 (176 MB, 18:33, the one that owned 8001).
# => ~1.8 GB leaked, and old versions keep their own log handles + qlib caches.
# Fix: after killing the port owner, also sweep EVERY python process whose command
# line matches `uvicorn app.main:app` (this runs BEFORE we start the new one, so the
# new process can never be a target).
#
# Also kept from the previous version (all learned the hard way):
#   * kill the whole tree (taskkill /T) - qlib's joblib/loky workers become orphans
#     otherwise (~12 workers, 1-3 GB per restart).
#   * sweep orphan loky / spawn_main / resource_tracker processes.
#   * QLIB_JOBLIB_BACKEND=multiprocessing (loky deadlocks, see md/ for the py-spy notes).
#   * PYTHONIOENCODING/PYTHONUTF8=utf-8 (redirected stdout defaults to GBK and the
#     engine logs then die with UnicodeEncodeError).

$ErrorActionPreference = 'SilentlyContinue'

$ROOT = 'd:\quant\qlib_code'
$LOGDIR = Join-Path $ROOT 'backend\workdir'
$logOut = Join-Path $LOGDIR 'backend.log'
$logErr = Join-Path $LOGDIR 'backend_err.log'

# ★ v2.11.52：**日志轮转** ✗→✓ —— 原来 uvicorn 的 stdout 直接追加到 backend.log ✓，
#   长期运行会**无限增长** ✗（实测已 0 MB，但跑几个月就是几百 MB ✓，且没人会想起来清 ✓）。
#   策略：启动前若 > 20 MB ⇒ 依次后移（.3 丢弃）✓ 只保留 3 份 ✓。
#   ⚠ 为什么放在这里而不是 logging 里 ✗：这份日志是**uvicorn 的 stdout 重定向** ✓，
#     不经过 `app/logger.py` 的 handler ✓ ⇒ 在那加 `RotatingFileHandler` 管不到它 ✗。
$MAX_MB = 20
$KEEP = 3
foreach ($f in @($logOut, $logErr)) {
    if ((Test-Path $f) -and ((Get-Item $f).Length -gt ($MAX_MB * 1MB))) {
        for ($i = $KEEP - 1; $i -ge 1; $i--) {
            $src = if ($i -eq 1) { $f } else { "$f.$($i - 1)" }
            if (Test-Path $src) { Move-Item -Force $src "$f.$i" }
        }
        Write-Host "rotated $(Split-Path $f -Leaf) (> $MAX_MB MB)"
    }
}

# ---------- 1. kill the port owner (with its process tree) ----------
$conn = Get-NetTCPConnection -LocalPort 8001 -State Listen -ErrorAction SilentlyContinue
if ($conn) {
    $conn.OwningProcess | Sort-Object -Unique | ForEach-Object {
        taskkill /PID $_ /T /F 2>&1 | Out-Null
        Stop-Process -Id $_ -Force -ErrorAction SilentlyContinue
    }
    Write-Host "port 8001 owner killed (tree)"
} else {
    Write-Host "port 8001 not in use"
}

# ---------- 2. sweep stray uvicorn instances (the actual fix) ----------
# Runs before Start-Process, so it can only hit leftovers.
Get-CimInstance Win32_Process -Filter "Name='python.exe'" -ErrorAction SilentlyContinue |
    Where-Object { $_.CommandLine -like '*uvicorn*app.main:app*' } |
    ForEach-Object {
        taskkill /PID $_.ProcessId /T /F 2>&1 | Out-Null
        Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue
        Write-Host "  swept stray uvicorn $($_.ProcessId)"
    }

Start-Sleep -Seconds 2

# ---------- 3. sweep orphan loky / spawn workers ----------
Get-CimInstance Win32_Process -Filter "Name='python.exe'" -ErrorAction SilentlyContinue |
    Where-Object {
        $_.CommandLine -like '*loky*' -or
        $_.CommandLine -like '*spawn_main*' -or
        $_.CommandLine -like '*resource_tracker*'
    } |
    ForEach-Object {
        $parentAlive = Get-Process -Id $_.ParentProcessId -ErrorAction SilentlyContinue
        if (-not $parentAlive) {
            Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue
            Write-Host "  cleaned orphan worker $($_.ProcessId)"
        }
    }

# ---------- 4. start exactly one backend ----------
$env:QLIB_JOBLIB_BACKEND = 'multiprocessing'
$env:QLIB_TASK_JOBS = '4'
$env:PYTHONIOENCODING = 'utf-8'
$env:PYTHONUTF8 = '1'

# ★ v2.11.50：**默认只绑本机回环** ✗→✓ —— 原来是 `--host 0.0.0.0` ✗，
#   等于"同一局域网的任何机器都能打开本服务"✓，而服务**没有鉴权 / 没有限流** ✗
#   （安全评审点 ✓）。本项目定位是**本机单用户工具** ✓ ⇒ 默认 `127.0.0.1` ✓。
#   ⚠ 真要在局域网里给别的机器用，显式设环境变量即可 ✓（不隐藏这个能力 ✓）：
#       $env:BACKEND_HOST = '0.0.0.0' ; scripts\restart_backend.ps1
#   ⚠ 配套：`frontend/vite.config.ts` 的代理目标同步成 `127.0.0.1` ✓ ——
#     否则 `localhost` 可能先解析到 `::1` ⇒ 打不通 ✓（Windows 上实测过这种情况 ✓）。
$bindHost = if ($env:BACKEND_HOST) { $env:BACKEND_HOST } else { '127.0.0.1' }
Write-Host "binding backend to $bindHost`:8001"

$p = Start-Process -FilePath 'D:\miniconda3\envs\qlib\python.exe' `
    -ArgumentList '-m', 'uvicorn', 'app.main:app', '--host', $bindHost, '--port', '8001' `
    -WorkingDirectory (Join-Path $ROOT 'backend') `
    -WindowStyle Hidden `
    -RedirectStandardOutput $logOut `
    -RedirectStandardError $logErr `
    -PassThru

# ---------- 5. verify by POLLING (fixed sleep is not enough) ----------
# v2.10.88: the old version slept 10s and checked once. On this machine the first
# startup (uvicorn import + qlib import) often needs 10-15s, so the script printed
# "FAILED to listen on 8001" while the server came up a second later.
# Observed: log line "Started server process [47916]" + 8001 listening on 47916
# => the script lied; the backend was fine. Poll instead, up to 60s.
$sw = [Diagnostics.Stopwatch]::StartNew()
Write-Host "waiting for 8001 (up to 60s)..."
$check = $null
while ($sw.Elapsed.TotalSeconds -lt 60) {
    $check = Get-NetTCPConnection -LocalPort 8001 -State Listen -ErrorAction SilentlyContinue
    if ($check) { break }
    Start-Sleep -Milliseconds 2000
}

# success line FIRST (so a slow follow-up query can never look like a hang)
if ($check) {
    Write-Host "backend up on 8001, pid=$($check.OwningProcess -join ',') (after $([int]$sw.Elapsed.TotalSeconds)s)"
}

$instances = @(Get-CimInstance Win32_Process -Filter "Name='python.exe'" -ErrorAction SilentlyContinue |
    Where-Object { $_.CommandLine -like '*uvicorn*app.main:app*' })

if ($check) {
    Write-Host "uvicorn instances=$($instances.Count) (must be 1)"
    if ($instances.Count -ne 1) {
        Write-Host "WARN: more than one instance - sweep step did not fully work"
        $instances | ForEach-Object { Write-Host "  pid=$($_.ProcessId) $($_.CreationDate)" }
    }
} else {
    Write-Host "FAILED to listen on 8001; tail of err log:"
    if (Test-Path $logErr) { Get-Content $logErr -Tail 40 }
}
