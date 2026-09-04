# 本地一键检查（离线版 CI gate）：pytest + 后端 ruff + 前端 tsc --noEmit
#
# 用法（在项目根任意位置）：
#   powershell -File scripts/check.ps1             # 全量 pytest（含 datareq，本机有数据时跑）
#   powershell -File scripts/check.ps1 -SkipData   # 跳过依赖真实数据的用例（CI/无数据机器）
#   powershell -File scripts/check.ps1 -IncludeE2E # 额外跑 e2e golden（~3 分钟，拆分前后对跑用）
#
# 默认排除 e2e（datareq and e2e 标记），保持日常检查快速；
# 做 qlib_engine 大文件拆分等纯重构前后，用 -IncludeE2E 跑一遍对比基线。
#
# 注意：pytest 必须在 backend 目录下运行——若在 D:\quant 等含 qlib 源码仓库的目录运行，
# import qlib 会撞到源码仓库根的空 namespace，导致 "module qlib has no attribute init"。
param([switch]$SkipData, [switch]$IncludeE2E)

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot

# 可覆盖 python（如 conda 环境不在 PATH 时）：$env:QLIB_PYTHON="D:\...\python.exe"
$py = $null
foreach ($c in @($env:QLIB_PYTHON, "D:\miniconda3\envs\qlib\python.exe")) {
    if ($c -and (Test-Path $c)) { $py = $c; break }
}
if (-not $py) { $py = "python" }

Write-Host "== [1/3] 后端 pytest =="
Push-Location "$root\backend"
if ($SkipData) {
    & $py -m pytest tests -m "not datareq" -q
} elseif ($IncludeE2E) {
    & $py -m pytest tests -q
} else {
    & $py -m pytest tests -m "not e2e" -q
}
if ($LASTEXITCODE -ne 0) { throw "pytest 未通过" }
Pop-Location

Write-Host "== [2/3] 后端 ruff =="
Push-Location "$root\backend"
& $py -m ruff check app tests tools
if ($LASTEXITCODE -ne 0) { throw "ruff 未通过" }
Pop-Location

Write-Host "== [3/3] 前端 tsc --noEmit =="
Push-Location "$root\frontend"
npx tsc --noEmit -p tsconfig.json
if ($LASTEXITCODE -ne 0) { throw "tsc 未通过" }
Pop-Location

Write-Host "`n✅ 全部检查通过"
