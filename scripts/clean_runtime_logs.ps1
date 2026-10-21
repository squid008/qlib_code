param([switch]$DryRun)

# Clean runtime / debug artifacts under backend\ and frontend\ (added 2026-10-21).
#
# These files are NOT source and NONE of them is tracked by git
# (.gitignore already covers *.log, server.log.*, frontend/*.out.txt,
#  *.tsbuildinfo, .coverage) -> deleting them produces zero git changes.
#
# They come from four places (all local-only):
#   1) start/restart redirection : uvicorn_*.log, server_std*.log, backend_restart*.log
#   2) data materialize/checks   : materialize_*.log, dump_*.log, check_fin.log, pytest_fin.log
#   3) frontend dev/build        : dev.*.log|txt, vite_*.log, build.out.txt, tsc_*.log
#   4) tool output               : .coverage (pytest-cov), tsconfig.tsbuildinfo (tsc cache)
#
# NEVER touched: requirements*.txt, constraints.txt, pyproject.toml
#
# NOTE: keep this file ASCII-only on purpose -- Windows PowerShell 5.1 reads
# non-BOM UTF-8 scripts as ANSI, which turns Chinese strings into mojibake.
#
# Usage:
#   powershell -NoProfile -ExecutionPolicy Bypass -File scripts\clean_runtime_logs.ps1 -DryRun
#   powershell -NoProfile -ExecutionPolicy Bypass -File scripts\clean_runtime_logs.ps1

$ErrorActionPreference = 'Stop'

$root = Split-Path -Parent $PSScriptRoot
if (-not (Test-Path (Join-Path $root 'backend'))) { $root = (Get-Location).Path }

# Whitelist only: never do "delete everything that is not source".
$patterns = @(
    'backend\*.log',
    'backend\*.err',
    'backend\*.out.txt',
    'backend\.coverage',
    'backend\coverage.json',
    'frontend\*.log',
    'frontend\*.out.txt',
    'frontend\*.err.txt',
    'frontend\tsconfig.tsbuildinfo'
)

$files = @()
foreach ($p in $patterns) {
    $files += Get-ChildItem -Path (Join-Path $root $p) -File -ErrorAction SilentlyContinue
}
$files = @($files | Sort-Object FullName -Unique)

if ($files.Count -eq 0) {
    Write-Host '  nothing to clean.' -ForegroundColor Green
    exit 0
}

$totalKB = [math]::Round((($files | Measure-Object Length -Sum).Sum / 1KB), 1)
Write-Host ("  matched {0} file(s), {1} KB" -f $files.Count, $totalKB) -ForegroundColor Cyan
$files | Sort-Object Length -Descending | Select-Object -First 8 | ForEach-Object {
    Write-Host ("    {0,-32} {1,8:N1} KB" -f $_.Name, ($_.Length / 1KB))
}

if ($DryRun) {
    Write-Host '  [DryRun] nothing deleted.' -ForegroundColor Yellow
    exit 0
}

$failed = 0
foreach ($f in $files) {
    try {
        Remove-Item $f.FullName -Force -ErrorAction Stop
    } catch {
        # Locked by a running process: report and continue.
        $failed++
        Write-Host ("  skipped (locked): {0}" -f $f.Name) -ForegroundColor DarkYellow
    }
}
Write-Host ("  deleted {0} file(s), freed {1} KB" -f ($files.Count - $failed), $totalKB) -ForegroundColor Green
if ($failed) { Write-Host ("  {0} file(s) locked (normal while a service is running)" -f $failed) -ForegroundColor DarkYellow }
