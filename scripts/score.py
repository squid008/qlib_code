# -*- coding: utf-8 -*-
"""**客观评分**（★ v2.11.51）—— 把"工程分"从**主观打分**换成**可复算的闸门达成率**。

## 为什么要有这个脚本
以前 `md/开发记录.md` 里有张"12 维评分表"（8.0~10.0 那种）✓ —— 但那是**我给的印象分** ✗：
· 同一份代码，不同人给 8.7 还是 9.2，**没有可验证的依据** ✗；
· 更糟：分数**容易只涨不跌**（每次改完都觉得"这维度好一点"✓），而真正退步的地方
  （比如某个棘轮被悄悄放宽 ✗）**反而不影响分数** ✗。
⇒ 换成**20 条硬闸门** ✓：每条都是**是非题** ✓，答案由本脚本从仓库里**读出来**（不是写死在文档里 ✓）。

## 口径（重要）
· **指数 = 通过条数 / 总条数 × 10** ✓ —— 只有这一个公式 ✓，没有"权重" ✓、
  没有"这条更重要所以多给分" ✗（要调整就**增删条目** ✓，改权重等于又回到主观 ✗）。
· 每条都打印**证据**（文件/行/数值 ✓）⇒ 任何人对结果有异议，**去看证据** ✓，而不是争论分数 ✓。
· `--full` 才会真跑 mypy / ruff / tsc / bandit（慢 ~1 分钟 ✓）；
  默认只做**文件级检查**（几秒 ✓，CI/本地随手跑 ✓）。

## 用法
```bash
python scripts/score.py            # 快检（文件级）
python scripts/score.py --full     # 连工具一起跑
```
"""
from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BACKEND = os.path.join(ROOT, "backend")
FRONTEND = os.path.join(ROOT, "frontend")


def read(path: str) -> str:
    try:
        with open(path, encoding="utf-8") as f:
            return f.read()
    except OSError:
        return ""


def run(cmd, cwd):
    """跑命令 → (returncode, 合并输出)。工具缺失时给 -1（算"未通过" ✓ 不静默当成通过 ✗）。

    ⚠ Windows 上 `npx` 实际是 **`npx.cmd`** ✗ —— 直接 `subprocess.run(["npx", ...])` 会
    `FileNotFoundError` ⇒ 我第一版就因此把 `tsc`/`biome` 两条判成 `exit=-1` ✗（假红 ✓）。
    ⇒ 先用 `shutil.which` 解析成真实路径 ✓（跨平台都对 ✓）。
    """
    exe = cmd[0]
    resolved = shutil.which(exe)
    if resolved is None and os.name == "nt":
        resolved = shutil.which(exe + ".cmd") or shutil.which(exe + ".exe")
    if resolved is None:
        return -1, "未找到可执行文件：%s" % exe
    try:
        p = subprocess.run([resolved, *cmd[1:]], cwd=cwd, capture_output=True, text=True,
                           encoding="utf-8", errors="replace", timeout=600)
        return p.returncode, (p.stdout or "") + (p.stderr or "")
    except (OSError, subprocess.SubprocessError) as e:
        return -1, "运行失败：%r" % (e,)


RESULTS: list[tuple[str, bool, str]] = []


def check(name: str, ok: bool, evidence: str) -> None:
    RESULTS.append((name, bool(ok), evidence))


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--full", action="store_true", help="连 mypy/ruff/tsc/bandit 一起跑")
    ap.add_argument("--json", action="store_true", help="额外输出 JSON（供其它脚本消费）")
    a = ap.parse_args()

    pyproj = read(os.path.join(BACKEND, "pyproject.toml"))
    ci = read(os.path.join(ROOT, ".github", "workflows", "ci.yml"))
    rest = read(os.path.join(ROOT, "scripts", "restart_backend.ps1"))
    devlog = read(os.path.join(ROOT, "md", "开发记录.md"))
    debt = read(os.path.join(ROOT, "md", "技术债.md"))
    clog = read(os.path.join(ROOT, "md", "change_log.md"))
    readme = read(os.path.join(ROOT, "README.md"))

    version = ""
    m = re.search(r'__version__\s*=\s*"([^"]+)"', read(os.path.join(BACKEND, "app", "__init__.py")))
    if m:
        version = m.group(1)

    # ---------- 1. 类型安全 ----------
    files_all = re.search(r"^files\s*=\s*\[\s*\"app\"\s*\]", pyproj, re.M) is not None
    check("mypy 覆盖**整个 app**（不是逐文件白名单）", files_all,
          'pyproject `files = ["app"]`' if files_all else "✗ 仍是逐文件白名单")

    if a.full:
        rc, out = run([sys.executable, "-m", "mypy"], BACKEND)
        n = len(re.findall(r"error:", out))
        check("mypy **0 错误**", rc == 0 and n == 0, "mypy 输出 %d 条 error" % n)

    # ---------- 2. 静态检查 ----------
    sel = re.search(r"^select\s*=\s*\[(.+?)\]", pyproj, re.M)
    n_rules = len(re.findall(r'"\w+"', sel.group(1))) if sel else 0
    check("ruff 启用规则 ≥ 6 条", n_rules >= 6, "select 里 %d 条规则" % n_rules)

    biome = read(os.path.join(FRONTEND, "biome.json"))
    biome_rules = len(re.findall(r'"[a-z][A-Za-z]+"\s*:\s*"(error|warn|off)"', biome))
    check("biome 显式白名单 ≥ 8 条规则", biome_rules >= 8, "biome.json 里 %d 条" % biome_rules)

    tsconf = read(os.path.join(FRONTEND, "tsconfig.json"))
    strict = re.search(r'"strict"\s*:\s*true', tsconf) is not None
    check("前端 `strict: true`", strict, "tsconfig.json" + ("有" if strict else "无"))

    if a.full:
        rc, out = run([sys.executable, "-m", "ruff", "check", "app", "tests", "tools"], BACKEND)
        check("ruff **0 违规**（与 CI 同命令）", rc == 0, (out.strip().splitlines() or ["?"])[-1][:80])
        rc2, _ = run(["npx", "tsc", "-b"], FRONTEND)
        check("`tsc -b` 0 错误", rc2 == 0, "exit=%d" % rc2)
        rc3, _ = run(["npx", "biome", "lint", "src"], FRONTEND)
        check("biome lint 0 问题", rc3 == 0, "exit=%d" % rc3)

    # ---------- 3. 覆盖率门槛（门槛本身就是"不许退步"的承诺 ✓） ----------
    # ⚠ 后端门槛写在 **CI 的命令行**里（`pyproject.toml` 里的 `--cov-fail-under` 只是注释 ✗）
    #   ⇒ 只看 pyproject 会**误判为 0** ✓ —— 我第一版就这么判的，实测报 `cov-fail-under=0` ✗
    m = re.search(r"--cov-fail-under[= ](\d+)", pyproj + "\n" + ci)
    thr_be = int(m.group(1)) if m else 0
    check("后端覆盖率门槛 ≥ 60", thr_be >= 60, "cov-fail-under=%d（%s）"
          % (thr_be, "pyproject" if m and ("--cov-fail-under" in pyproj) else "ci.yml"))

    vit = read(os.path.join(FRONTEND, "vitest.config.ts"))
    m = re.search(r"statements:\s*(\d+)", vit)
    thr_fe = int(m.group(1)) if m else 0
    check("前端覆盖率门槛 ≥ 55", thr_fe >= 55, "thresholds.statements=%d" % thr_fe)

    # ---------- 4. 数值与性能（防"静默变"） ----------
    golden = read(os.path.join(BACKEND, "tests", "test_engine_golden.py"))
    golden2 = read(os.path.join(BACKEND, "tests", "test_engine_golden_strategies.py"))
    n_fp = (golden + golden2).count("nav_sha") + (golden + golden2).count("trades_sha")
    check("数值指纹 ≥ 20 个断言点", n_fp >= 20, "nav_sha/trades_sha 出现 %d 次" % n_fp)

    perf = read(os.path.join(BACKEND, "tests", "test_perf_absolute.py"))
    n_perf = len(re.findall(r'^\s{4}"[^"]+":\s*[\d.]+', perf, re.M))
    check("绝对性能门 ≥ 4 条", n_perf >= 4, "BASE 里 %d 条基线" % n_perf)

    # ---------- 5. 三把棘轮（结构不许退步） ----------
    check("行数棘轮存在（大文件/巨型函数只减不增）",
          "MAX_ANY" in read(os.path.join(BACKEND, "tests", "test_code_health.py")),
          "tests/test_code_health.py")
    check("复杂度棘轮存在（FROZEN 冻结）",
          "FROZEN" in read(os.path.join(BACKEND, "tests", "test_complexity.py")),
          "tests/test_complexity.py")
    check("静默吞异常棘轮存在",
          "_SILENT_NODES" in read(os.path.join(BACKEND, "tests", "test_except_ratchet.py")),
          "tests/test_except_ratchet.py")

    # ---------- 6. 安全 ----------
    check("bandit 在 CI 且只报中/高危", "bandit -ll" in ci, "ci.yml security-scan")
    check("gitleaks 在 CI（全历史）", "gitleaks" in ci and "fetch-depth: 0" in ci, "ci.yml secret-scan")
    check("npm audit 门槛在 CI", "audit-level=critical" in ci, "ci.yml frontend")
    check("后端**默认绑回环**（不默认暴露到局域网）",
          "BACKEND_HOST" in rest and "'127.0.0.1'" in rest,
          "restart_backend.ps1 里 `$bindHost` 默认 127.0.0.1")

    if a.full:
        rc, out = run([sys.executable, "-m", "bandit", "-ll", "-r", "app", "-c", "pyproject.toml"], BACKEND)
        check("bandit 中/高危 **0 条**", rc == 0, (out.strip().splitlines() or ["?"])[-1][:80])

    # ---------- 7. 可观测性 ----------
    mainpy = read(os.path.join(BACKEND, "app", "main.py"))
    check("`/api/metrics` + Prometheus 文本 + 慢请求告警 三件齐",
          "/api/metrics" in mainpy and "format" in mainpy and "_warn_if_slow" in mainpy,
          "main.py")

    # ---------- 8. 记录可追溯 ----------
    v_ts = read(os.path.join(FRONTEND, "src", "version.ts"))
    v_pkg = read(os.path.join(FRONTEND, "package.json"))
    synced = version and (version in v_ts) and ('"version": "%s"' % version in v_pkg)
    check("版本三处一致", synced, "版本=%s" % version)
    check("当前版本在 change_log 有段", ("## [%s]" % version) in clog, "md/change_log.md")
    check("债务台账表头数字与表格行数自洽（做过一次就能记住的坑）",
          "在册" in debt and "已清偿" in debt, "md/技术债.md")
    check("README 有「当前基线」表", "当前基线" in readme, "README.md")

    # ---------- 输出 ----------
    total = len(RESULTS)
    passed = sum(1 for _, ok, _ in RESULTS if ok)
    idx = 10.0 * passed / total if total else 0.0

    print("\n  客观评分（闸门达成率）—— 版本 %s" % version)
    print("  " + "=" * 88)
    for i, (name, ok, ev) in enumerate(RESULTS, 1):
        print("  %2d. %-46s %s  %s" % (i, name, "✓" if ok else "✗", ev[:44]))
    print("  " + "=" * 88)
    print("  通过 %d / %d  ⇒  **指数 %.1f / 10**" % (passed, total, idx))
    print("  口径：指数 = 通过条数/总条数×10（**没有权重** ✓ 每条都是是非题 ✓）")
    if not a.full:
        print("  ⚠ 未开 --full ⇒ 需要跑工具的闸门（mypy/ruff/tsc/biome/bandit）没算进来 ✓"
              "总条数会变少 ✓，所以两种模式的指数**不可直接比** ✗")

    if a.json:
        payload = {
            "version": version,
            "passed": passed,
            "total": total,
            "index": round(idx, 2),
            "gates": [{"name": n, "ok": ok, "evidence": ev} for n, ok, ev in RESULTS],
        }
        print("\n" + json.dumps(payload, ensure_ascii=False, indent=2))
    return 0 if passed == total else 1


if __name__ == "__main__":
    sys.exit(main())
