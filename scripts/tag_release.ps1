# 发版打附注 tag（★ v2.11.52）
#
# 为什么需要它：README 写着”每个版本递增都打附注 tag”，但实测**最新 tag 停在 v2.9.18**，
# 而版本已经 2.11.51 ⇒ 中间约 **120 个版本没打** ⇒ ”任意发版一键回退”这件事**事实上断了** ✗。
# 根因不是不想打，而是**靠记忆的步骤一定会漏** ✓ ⇒ 做成脚本，发版流程里跑一遍即可。
#
# 用法：
#   scripts\tag_release.ps1            # 按 backend/app/__init__.py 的版本打 tag
#   scripts\tag_release.ps1 -Push      # 打完后把 tag 推到 origin
#   scripts\tag_release.ps1 -Tag v2.9.19 -Commit <sha>   # 补打历史 tag（回溯用）
param(
    [string]$Tag = '',
    [string]$Commit = '',
    [switch]$Push
)

$ErrorActionPreference = 'Stop'
$ROOT = Split-Path $PSScriptRoot -Parent
Push-Location $ROOT
try {
    if (-not $Tag) {
        $verLine = Select-String -Path 'backend\app\__init__.py' -Pattern '__version__\s*=\s*"([^"]+)"' -Encoding utf8 |
            Select-Object -First 1
        if (-not $verLine) { throw '读不到 __version__（backend/app/__init__.py）' }
        $ver = [regex]::Match($verLine.Line, '"([^"]+)"').Groups[1].Value
        $Tag = "v$ver"
    }

    # 三处版本必须一致（否则 tag 名会指错版本）
    $fe = Select-String -Path 'frontend\src\version.ts' -Pattern "FE_VERSION = '([^']+)'" -Encoding utf8 |
        Select-Object -First 1
    if ($fe) {
        $feVer = [regex]::Match($fe.Line, "'([^']+)'").Groups[1].Value
        if ("v$feVer" -ne $Tag) {
            throw "版本不一致：后端 tag=$Tag ｜ 前端 $feVer ⇒ 先跑 test_version_sync"
        }
    }

    if (git rev-parse -q --verify "refs/tags/$Tag" 2>$null) {
        Write-Host "tag $Tag 已存在，跳过"
    } else {
        $msg = "${Tag}（自动打点：scripts/tag_release.ps1）"
        if ($Commit) {
            git tag -a $Tag $Commit -m $msg
            Write-Host "已打 tag $Tag → $Commit"
        } else {
            git tag -a $Tag -m $msg
            Write-Host "已打 tag $Tag → HEAD（$(git rev-parse --short HEAD)）"
        }
    }

    if ($Push) {
        git push origin "refs/tags/$Tag"
        Write-Host "已推送 tag $Tag"
    } else {
        Write-Host "（未推送；要推就加 -Push）"
    }
} finally {
    Pop-Location
}
