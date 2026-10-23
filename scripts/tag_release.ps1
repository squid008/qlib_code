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
    # ★ v2.11.56：`-NoRelease` 跳过"建 GitHub Release"（默认建 ✓）
    [switch]$NoRelease,

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

        # ★ v2.11.56：**自动建 GitHub Release（notes 由 GitHub 自动生成）** ✗→✓
        #   为什么补它：绝对质量评分里"协作与变更"一直扣着一条「无自动 release notes」✗ ——
        #   实际上 tag 打了一百多个 ✓ 但**一个版本 release 都没有** ✓（`gh release list` 只有数据包 ✓）
        #   ⇒ 外部看到的是"只有 tag、没有 changelog 页面"✗。
        #   `--generate-notes` 会按 **commit / merged PR** 自动列出来源 ✓，不用手写 ✓；
        #   而本项目每版 change_log 已写得很细 ✓ ⇒ release 页只要**指过去**就够 ✓（见下面 body）。
        #   ⚠ 幂等：已存在就跳过 ✓（重复发版/补打 tag 时不会报错 ✓）。
        if (-not $NoRelease) {
            # ⚠⚠ `$ErrorActionPreference` 必须**临时放松** ✗→✓ —— 脚本开头是 `'Stop'` ✓，
            #   而 `gh release view <不存在的 tag>` 会把 `release not found` 写到 **stderr** ✗，
            #   PowerShell 会把"原生命令的 stderr"包成 error record ⇒ 在 Stop 下**直接抛** ✗
            #   ⇒ 实测第一次跑就中断在"查是否已存在"那行 ✓，release 根本没建 ✓。
            #   ⇒ 查询一律在 `Continue` 下做 + 只看 `$LASTEXITCODE` ✓，做完再恢复 ✓。
            $prevEap = $ErrorActionPreference
            $ErrorActionPreference = 'Continue'
            try {
                gh release view $Tag --json tagName *> $null
                $hasRelease = ($LASTEXITCODE -eq 0)
                if ($hasRelease) {
                    Write-Host "release $Tag 已存在，跳过"
                } else {
                    # ⚠ tag 是 `v2.11.55`、而 change_log 的段标题是 `## [2.11.55]`（**没有 v** ✗）——
                    #   直接拼 $Tag 会指向一个**不存在的标题** ✓（指向错了等于没指 ✗，实测确认过 ✓）。
                    $ver = $Tag -replace '^v', ''
                    $body = "本版详细记录见仓库 ``md/change_log.md`` 的 ``## [$ver]`` 段（现象 / 根因 / 实测数字）。"
                    gh release create $Tag --title $Tag --generate-notes --notes $body
                    if ($LASTEXITCODE -eq 0) {
                        Write-Host "已创建 release $Tag（notes 自动生成 + 指向 change_log）"
                    } else {
                        Write-Host "release 创建失败（不影响 tag 本身）"
                    }
                }
            } finally {
                $ErrorActionPreference = $prevEap
            }
        }

    } else {

        Write-Host "（未推送；要推就加 -Push）"

    }

} finally {

    Pop-Location

}
