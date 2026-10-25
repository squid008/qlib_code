# -*- coding: utf-8 -*-
"""**客观评分 v2**（★ v2.11.52）—— 修掉第一版四个"看起来严、实际松"的结构性弱点。

## 为什么重写（第一版被指出的四条，逐条认）
| 弱点 | 第一版实况 ✗ | 现在 ✓ |
|---|---|---|
| 大多是**存在性检查** | `"MAX_ANY" in 文件内容` 就算过 ✓ | 每条标 `实测/静态`，并**分开报数** ✓ |
| #24 名不副实 | 只查 `"在册" in 文本` ⇒ **数字对不上也绿** ✗ | **真解析**表头数字 vs 表格行数 ✓ |
| 门槛可"改尺子"绕过 | 只读 `55` 这个数，**不读实测覆盖** ✗ | ① 读**实测**覆盖 ✓ ② 门槛存基线文件，**只许升** ✓ |
| **没跑 pytest** | "1926 个测试是否全绿"**不进指数** ✗ | `--full` **真跑** pytest + 前端 vitest ✓ |

## 口径（只有这一个公式）
`指数 = 通过条数 / 计入条数 × 10` ✓ —— **没有权重** ✓。
但**打印三组数**：总通过率、**实测组**通过率、静态组通过率 ✓
（并写明：`实测组` 才接近"绝对质量"，静态组只证明"配置还在"✗）。

## 用法
```bash
python scripts/score.py            # 快检（静态，几秒）
python scripts/score.py --full     # 全量（真跑 mypy/ruff/tsc/biome/bandit/pytest/覆盖率，约 4 分钟）
```
"""
from __future__ import annotations

import argparse
import ast
import json
import os
import re
import shutil
import subprocess
import sys
import time
import xml.etree.ElementTree as ET

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BACKEND = os.path.join(ROOT, "backend")
FRONTEND = os.path.join(ROOT, "frontend")
BASELINE = os.path.join(ROOT, "scripts", "score_baseline.json")

#: 每条闸门的"成色" —— **实测** 才接近绝对质量；**静态** 只证明配置还在（防止拿它刷分 ✗）
MEASURED, STATIC, RATCHET = "实测", "静态", "棘轮"

RESULTS: list[tuple[str, str, bool, str]] = []


def read(path: str) -> str:
    try:
        with open(path, encoding="utf-8") as f:
            return f.read()
    except OSError:
        return ""


def run(cmd, cwd, timeout=900):
    """跑命令 → (rc, 输出)。Windows 上 `npx` 实为 `npx.cmd` ⇒ 先用 which 解析 ✓。

    ⚠⚠ **已知局限（实测踩过）**：`subprocess.run(capture_output=True, timeout=…)` 在
    "子进程又拉起孙进程、且孙进程继承了 stdout 管道"时会**死锁** ✗ —— `timeout` 只杀直接子进程 ✓，
    而 `communicate()` 仍在等管道关闭 ✓ ⇒ 整个评分**静默挂死**（实测卡了 9 分钟、输出 0 字节 ✗）。
    本项目最容易触发的是 **pytest（spawn 子进程）** ✓。
    ⇒ 所以：`--full` **不是无脑能跑通的** ✓ —— 若发现长时间 0 输出，先看进程里有没有残留
    `score.py` ✓，必要时**把两条重腿分开手工跑**（`pytest … --junit-xml` / `npx vitest run --coverage`）✓。
    """
    exe = shutil.which(cmd[0])
    if exe is None and os.name == "nt":
        exe = shutil.which(cmd[0] + ".cmd") or shutil.which(cmd[0] + ".exe")
    if exe is None:
        return -1, "未找到可执行文件：%s" % cmd[0]
    try:
        p = subprocess.run([exe, *cmd[1:]], cwd=cwd, capture_output=True, text=True,
                           encoding="utf-8", errors="replace", timeout=timeout)
        return p.returncode, (p.stdout or "") + (p.stderr or "")
    except (OSError, subprocess.SubprocessError) as e:
        return -1, "运行失败：%r" % (e,)


def check(kind: str, name: str, ok: bool, evidence: str) -> None:
    RESULTS.append((kind, name, bool(ok), evidence))


def py_func_lengths() -> list[tuple[int, str, str]]:
    rows = []
    for root, _, files in os.walk(os.path.join(BACKEND, "app")):
        for f in files:
            if not f.endswith(".py"):
                continue
            p = os.path.join(root, f)
            try:
                tree = ast.parse(read(p))
            except SyntaxError:
                continue
            for n in ast.walk(tree):
                if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    rows.append((n.end_lineno - n.lineno + 1,
                                 os.path.relpath(p, BACKEND).replace("\\", "/"), n.name))
    rows.sort(reverse=True)
    return rows


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--full", action="store_true", help="真跑工具与测试（约 4 分钟）")
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args()

    base = json.loads(read(BASELINE) or "{}")
    pyproj = read(os.path.join(BACKEND, "pyproject.toml"))
    ci = read(os.path.join(ROOT, ".github", "workflows", "ci.yml"))
    rest = read(os.path.join(ROOT, "scripts", "restart_backend.ps1"))
    debt = read(os.path.join(ROOT, "md", "技术债.md"))
    clog = read(os.path.join(ROOT, "md", "change_log.md"))
    readme = read(os.path.join(ROOT, "README.md"))
    mainpy = read(os.path.join(BACKEND, "app", "main.py"))
    loggerpy = read(os.path.join(BACKEND, "app", "logger.py"))
    vit = read(os.path.join(FRONTEND, "vitest.config.ts"))

    m = re.search(r'__version__\s*=\s*"([^"]+)"', read(os.path.join(BACKEND, "app", "__init__.py")))
    version = m.group(1) if m else ""

    be_thr = int((re.search(r"--cov-fail-under[= ](\d+)", pyproj + "\n" + ci) or [0, 0])[1] or 0)
    fe_thr = int((re.search(r"statements:\s*(\d+)", vit) or [0, 0])[1] or 0)

    # ================= 实测组（--full 才跑）=================
    if a.full:
        rc, out = run([sys.executable, "-m", "mypy"], BACKEND)
        check(MEASURED, "mypy **0 错误**", rc == 0 and "error:" not in out,
              "error 行数 %d" % out.count("error:"))
        rc, out = run([sys.executable, "-m", "ruff", "check", "app", "tests", "tools"], BACKEND)
        check(MEASURED, "ruff **0 违规**（与 CI 同命令）", rc == 0, (out.strip().splitlines() or ["?"])[-1][:60])
        rc, _ = run(["npx", "tsc", "-b"], FRONTEND)
        check(MEASURED, "`tsc -b` 0 错误", rc == 0, "exit=%d" % rc)
        rc, _ = run(["npx", "biome", "lint", "src"], FRONTEND)
        check(MEASURED, "biome lint 0 问题", rc == 0, "exit=%d" % rc)
        rc, out = run([sys.executable, "-m", "bandit", "-ll", "-r", "app", "-c", "pyproject.toml"], BACKEND)
        check(MEASURED, "bandit 中/高危 0 条", rc == 0,
              ("No issues identified" in out and "0 issues") or "exit=0")

        # ★★ 真跑后端测试 + 实测覆盖（第一版最大的洞 ✓ 现在补上）
        # ⚠⚠ **必须靠 `--junit-xml` 判定，不能靠 stdout 的 "N passed" 或 rc** ✗→✓ ——
        #   本机沙箱的 **safe-delete 守卫**在测试尾声要批量删 600+ 个
        #   `workdir/tasks/est/*.json` 时会**直接杀进程** ✓ ⇒ pytest 跑完全部（100%）
        #   却**既没有汇总行、rc 也是 1** ✗（实测：rc=1、stdout 只剩进度点 ✓）。
        #   ⇒ 若用 "有 N passed" 判，会**永久假红** ✓（我第一版就这么红的 ✗）；
        #   而 junit 文件在 sessionfinish 就落盘了 ✓ ⇒ 被杀之后仍然完整 ✓
        #   （实测 `tests=1926 failures=0` ✓）。
        cov_json = os.path.join(BACKEND, "_score_cov.json")
        junit = os.path.join(BACKEND, "_score_junit.xml")
        for f in (cov_json, junit):
            if os.path.exists(f):
                os.remove(f)
        # ⚠ 必须给**显式超时** ✗→✓ —— 实测某次 pytest 卡住不返回（进程在、无输出）✗，
        #   默认 900s 会让整个评分"静默挂死"✓ ⇒ 超时即判红并写明 ✓（卡住也是一种失败 ✓）。
        # ★★ v2.11.80：**必须与 CI 同款两步** ✗→✓ —— 原来这里只写 `not datareq`，
        #   把 `perf`（绝对耗时的 4 条）**混进 1900+ 条里跑** ⇒ 被负载污染 ⇒
        #   `test_batch_even_speed_is_guarded` **每次都假红** ✗（实测单独跑 4 passed ✓，
        #   而 CI 从 v2.11.60 起早就拆成「主跑排除 perf + 单独 `-m perf`」两步了 ✓）。
        #   ⇒ 评分脚本"与 CI 同命令"的承诺，就断在这一行 ✓。
        rc, out = run([sys.executable, "-m", "pytest", "tests", "-q", "-m",
                       "not datareq and not perf",
                       "--cov=app", "--cov-report=json:%s" % cov_json, "--cov-report=",
                       "--junit-xml=%s" % junit, "--tb=no"], BACKEND, timeout=420)
        n_tot = n_fail = 0
        try:
            root = ET.parse(junit).getroot()
            suites = [root] if root.tag == "testsuite" else list(root)
            n_tot = sum(int(s.get("tests", 0) or 0) for s in suites)
            n_fail = sum(int(s.get("failures", 0) or 0) + int(s.get("errors", 0) or 0)
                         for s in suites)
        except (OSError, ET.ParseError):
            pass
        contaminated = rc != 0 and "SAFE_DELETE_BULK_CONFIRM_REQUIRED" in out
        if os.path.exists(junit):
            os.remove(junit)
        # ★★ v2.11.80：**perf 单独串行跑** ✓（与 CI 的第二条命令一模一样 ✓）。
        #   ⚠ 不新增闸门条数：把它的 counts **合并**进上面那条"后端 pytests 全绿" ✓
        #   （多一条闸门会让 README/开发记录里"闸门 N/N"的数字全部要跟着改 ✗，而语义没变 ✓）。
        junit_perf = os.path.join(BACKEND, "_score_junit_perf.xml")
        if os.path.exists(junit_perf):
            os.remove(junit_perf)
        rc_perf, out_perf = run([sys.executable, "-m", "pytest", "tests", "-q", "-m", "perf",
                                 "--junit-xml=%s" % junit_perf, "--tb=no"], BACKEND, timeout=240)
        try:
            _r2 = ET.parse(junit_perf).getroot()
            _s2 = [_r2] if _r2.tag == "testsuite" else list(_r2)
            n_tot += sum(int(s.get("tests", 0) or 0) for s in _s2)
            n_fail += sum(int(s.get("failures", 0) or 0) + int(s.get("errors", 0) or 0)
                          for s in _s2)
        except (OSError, ET.ParseError):
            # ⚠ 解析不到 junit 时**按 rc 判** ✓（不能让"文件没落盘"变成静默通过 ✗）
            if rc_perf != 0:
                n_fail += 1
        if os.path.exists(junit_perf):
            os.remove(junit_perf)
        check(MEASURED, "后端 pytests **全绿**（含 perf 单独串行 ✓）", n_fail == 0 and n_tot > 0,
              "%d passed / %d%s" % (n_tot - n_fail, n_tot,
                                    "（rc=1 系本机 safe-delete ✓）" if contaminated else ""))
        be_cov = 0.0
        try:
            with open(cov_json, encoding="utf-8") as f:
                be_cov = float(json.load(f)["totals"]["percent_covered"])
        except (OSError, KeyError, ValueError):
            pass
        if os.path.exists(cov_json):
            os.remove(cov_json)
        check(MEASURED, "后端**实测**覆盖率 ≥ 门槛 %d" % be_thr, be_cov >= be_thr, "%.2f%%" % be_cov)
        check(RATCHET, "后端实测覆盖率 ≥ 基线 %.1f%%" % base.get("be_cov_measured", 0),
              be_cov + 0.01 >= base.get("be_cov_measured", 0), "%.2f%%" % be_cov)

        # 前端测试 + 实测覆盖
        # ⚠⚠ **必须每次用全新的 reportsDirectory** ✗→✓（v2.11.81/82 实测两次）：
        #   vitest 的 `V8CoverageProvider.clean` 会用 **trash** 删已存在的目录 ✓，
        #   而本机 **safe-delete 守卫**拦这个操作 ⇒
        #   `Unhandled Error: [safe-delete] 操作失败 ... Error during a trash operation` ✗
        #   ⇒ vitest rc=1、**没有 coverage-final.json** ⇒ 前端三条闸门全 0.00% ✗✗。
        #   ⚠ 我第一版"跑前用 shutil.rmtree(ignore_errors=True) 清掉"**没用** ✗ ——
        #     那个 rmtree 同样被守卫拦下，而 `ignore_errors=True` 把失败**静默吞了** ✓
        #     ⇒ 目录还在 ⇒ vitest 又崩 ✓（**"加了清理"看起来更稳妥，其实是假修** ✗）。
        #   ⇒ 正解：目录名带 **pid + 纳秒**，永远不存在旧目录 ⇒ vitest 压根不需要 clean ✓。
        #     （不删就不触发守卫 ✓；累积的目录很小，已在 .gitignore 里 ✓）
        _tag = "%d_%d" % (os.getpid(), time.time_ns())
        _rel = "_score_cov_%s" % _tag
        vc = os.path.join(FRONTEND, _rel)
        rc, out = run(["npx", "vitest", "run", "--coverage",
                       "--coverage.reporter=json", "--coverage.reportsDirectory=%s" % _rel],
                      FRONTEND, timeout=300)
        # ⚠ vitest 的输出带 **ANSI 颜色码**（`Tests  \x1b[1m\x1b[32m310 passed`）✗
        #   ⇒ 不先剥掉就永远匹配不到 ⇒ 证据里出现 "0 passed 却 ✓" ✓（**证据不实比没证据更糟** ✗）。
        plain = re.sub(r"\x1b\[[0-9;]*m", "", out)
        fe_pass = int((re.search(r"Tests\s+(\d+)\s+passed", plain) or [0, 0])[1] or 0)
        # ⚠ 判定**不依赖计数解析成功** ✗→✓（解析不到就如实写"rc=0，计数未解析"✓），
        #   否则"输出格式一变 ⇒ 闸门假红"✗。覆盖率另有一条独立闸门盯着 ✓。
        check(MEASURED, "前端 vitest **全绿**（含覆盖率门槛）", rc == 0,
              ("%d passed" % fe_pass) if fe_pass else "rc=0（计数未解析）")
        fe_cov = 0.0
        try:
            with open(os.path.join(vc, "coverage-final.json"), encoding="utf-8") as f:
                d = json.load(f)
            tot = sum(len(c.get("s") or {}) for c in d.values())
            hit = sum(sum(1 for v in (c.get("s") or {}).values() if v) for c in d.values())
            fe_cov = 100.0 * hit / tot if tot else 0.0
        except (OSError, ValueError):
            pass
        check(MEASURED, "前端**实测**覆盖率 ≥ 门槛 %d" % fe_thr, fe_cov >= fe_thr, "%.2f%%" % fe_cov)
        check(RATCHET, "前端实测覆盖率 ≥ 基线 %.1f%%" % base.get("fe_cov_measured", 0),
              fe_cov + 0.01 >= base.get("fe_cov_measured", 0), "%.2f%%" % fe_cov)

    # ================= 棘轮组（防"改尺子"，不依赖 --full）=================
    check(RATCHET, "后端覆盖率门槛**只许升**（基线 %d）" % base.get("be_cov_thr", 0),
          be_thr >= base.get("be_cov_thr", 0), "现在 %d" % be_thr)
    check(RATCHET, "前端覆盖率门槛**只许升**（基线 %d）" % base.get("fe_cov_thr", 0),
          fe_thr >= base.get("fe_cov_thr", 0), "现在 %d" % fe_thr)

    funcs = py_func_lengths()
    n_big = len([r for r in funcs if r[0] > 300])
    check(RATCHET, "`>300` 行函数 ≤ %d（实测 ast）" % base.get("max_big_funcs", 5),
          n_big <= base.get("max_big_funcs", 5),
          "现在 %d 个（最长 %d）" % (n_big, funcs[0][0] if funcs else 0))
    huge = [p for p in {r[1] for r in funcs} if read(os.path.join(BACKEND, p)).count("\n") + 1 > 2600]
    check(RATCHET, "巨型文件（>2600 行）≤ %d" % base.get("max_huge_files", 2),
          len(huge) <= base.get("max_huge_files", 2), "现在 %d 个" % len(huge))

    # ================= 真问题闸门（第一版没有的）=================
    # ★ v2.11.91：**远端优先** ✗→✓ —— 原来只看本地 `git describe` ⇒
    #   "打了 tag 但没 push"也算过 ✗（评审正是在这一点上是对的 ✓）。
    tag_local = run(["git", "describe", "--tags", "--abbrev=0"], ROOT)[1].strip()
    _rt = run(["git", "ls-remote", "--tags", "origin"], ROOT, timeout=25)
    tag_remote = ""
    if _rt[0] == 0:
        _names = sorted(
            ln.split("refs/tags/", 1)[1].replace("^{}", "").strip()
            for ln in (_rt[1] or "").splitlines() if "refs/tags/" in ln
        )
        # 远端"最大版本"要按**数值**排（字典序会把 v2.9.9 排在 v2.11.90 后面 ✗）
        def _key(t: str):
            m = __import__("re").match(r"v(\d+)\.(\d+)\.(\d+)$", t)
            return tuple(int(x) for x in m.groups()) if m else (0, 0, 0)
        tag_remote = max(_names, key=_key) if _names else ""
    tag = tag_remote or tag_local
    check(MEASURED, "**最新 tag = 当前版本**（远端优先 ✓）", tag == "v" + version,
          "远端 %s ｜ 本地 %s ｜ 版本 %s"
          % (tag_remote or "不可达", tag_local or "无", version))
    # ⚠ 这个坑第一版会写成"看 clone 命令存在 ⇒ 通过"✗ —— 恰恰相反：
    #   `clone --depth 1 <repo>` **不带 sha** 就等于**没锁** ✓ 必须找 `git checkout <sha>` ✓
    # ⚠ 判"锁了 sha"的正确写法是**在 qlib 相关的几百字符内出现 40 位 hex** ✓ ——
    #   我第一版写死要 `git checkout <sha>` ✗，而实际写法是 `fetch --depth 1 origin <sha>`
    #   + `checkout FETCH_HEAD` ✓ ⇒ 闸门自己误报红 ✗（**闸门也会写错，得跟着实现走** ✓）。
    qlib_pinned = re.search(r"microsoft/qlib[\s\S]{0,500}?[0-9a-f]{40}", ci)
    check(STATIC, "qlib **锁到 commit sha**（不锁 ⇒ 上游一变就红）",
          qlib_pinned is not None,
          "已锁 sha" if qlib_pinned else "ci.yml 仍 `clone --depth 1` 主干")
    # 轮转可以在两处：`logger.py` 的 handler（进程内日志 ✓）
    # 或 `restart_backend.ps1`（uvicorn 的 stdout 重定向那份 ✓ —— 本项目就是这一种 ✓）。
    rotate_in_ps = "rotated" in rest and "MAX_MB" in rest
    check(STATIC, "**日志轮转**存在（否则长期运行无限增长）",
          ("RotatingFileHandler" in loggerpy or "TimedRotating" in loggerpy) or rotate_in_ps,
          "ps1 轮转" if rotate_in_ps else "两处都没有")

    # 债务台账：**真解析**（第一版只查两个词 ✗）
    m_act = re.search(r"在册\s*(\d+)\s*条", debt)
    m_paid = re.search(r"已清偿\s*(\d+)\s*条", debt)
    # ⚠ 行数统计要和台账里"表格实际行"口径一致 ✓ —— 我先用 `^\| v[\d.]+\|` 漏掉了
    #   `| v2.10.81~90 |` 这类**区间版本号** ✓（少算 2 行 ✗）⇒ 改成只看行首 `| v` ✓。
    rows = len(re.findall(r"^\|\s*v", debt, re.M))
    n_act = int(m_act.group(1)) if m_act else -1
    n_paid = int(m_paid.group(1)) if m_paid else -1
    check(MEASURED, "债务台账**数字与表格行数自洽**",
          n_paid == rows and n_act > 0,
          "表头 已清偿 %d ｜ 表格实有 %d 行" % (n_paid, rows))

    # datareq 自动化比例（真实链路的自动化程度）
    tests_dir = os.path.join(BACKEND, "tests")
    n_dr = n_syn = 0
    for f in os.listdir(tests_dir):
        if f.endswith(".py"):
            txt = read(os.path.join(tests_dir, f))
            n_dr += txt.count("mark.datareq")
            n_syn += txt.count("mark.synthdata")
    ratio = (n_syn / n_dr) if n_dr else 0.0
    check(RATCHET, "`datareq` 自动化比例 ≥ %d%%" % int(100 * base.get("datareq_auto_min", 0.3)),
          ratio + 1e-9 >= base.get("datareq_auto_min", 0.3),
          "%d/%d = %.0f%%" % (n_syn, n_dr, 100 * ratio))

    # ================= 静态组（配置还在）=================
    check(STATIC, "mypy 覆盖**整个 app**",
          re.search(r'^files\s*=\s*\[\s*"app"\s*\]', pyproj, re.M) is not None, 'files=["app"]')
    sel = re.search(r"^select\s*=\s*\[(.+?)\]", pyproj, re.M)
    n_rules = len(re.findall(r'"\w+"', sel.group(1))) if sel else 0
    check(STATIC, "ruff 启用规则 ≥ 6 条", n_rules >= 6, "%d 条" % n_rules)
    n_biome = len(re.findall(r'"[a-z][A-Za-z]+"\s*:\s*"(error|warn|off)"',
                             read(os.path.join(FRONTEND, "biome.json"))))
    check(STATIC, "biome 显式白名单 ≥ 8 条", n_biome >= 8, "%d 条" % n_biome)
    check(STATIC, "前端 `strict: true`",
          re.search(r'"strict"\s*:\s*true', read(os.path.join(FRONTEND, "tsconfig.json"))) is not None, "")
    g = read(os.path.join(BACKEND, "tests", "test_engine_golden.py")) + \
        read(os.path.join(BACKEND, "tests", "test_engine_golden_strategies.py"))
    check(STATIC, "数值指纹 ≥ 20 个断言点", g.count("nav_sha") + g.count("trades_sha") >= 20,
          "%d 次" % (g.count("nav_sha") + g.count("trades_sha")))
    perf = read(os.path.join(BACKEND, "tests", "test_perf_absolute.py"))
    check(STATIC, "绝对性能门 ≥ 4 条",
          len(re.findall(r'^\s{4}"[^"]+":\s*[\d.]+', perf, re.M)) >= 4, "")
    check(STATIC, "三把棘轮都在（行数/复杂度/静默）",
          all(k in read(os.path.join(BACKEND, "tests", n)) for k, n in
              (("MAX_ANY", "test_code_health.py"), ("FROZEN", "test_complexity.py"),
               ("_SILENT_NODES", "test_except_ratchet.py"))), "")
    check(STATIC, "bandit / gitleaks / npm audit 在 CI",
          # ★ v2.11.80：`critical` → **`high`** ✗→✓ —— v2.11.68 把 npm 的 `xlsx` 移除后
          #   `npm audit --omit=dev` 归零 ⇒ CI 当场把阈值**收紧到 high** ✓（已核实 ci.yml:174 ✓）。
          #   而这条闸门还在找 `audit-level=critical` ⇒ **CI 是对的、闸门假红** ✗。
          "bandit -ll" in ci and "gitleaks" in ci and "audit-level=high" in ci, "ci.yml")
    check(STATIC, "后端**默认绑回环**",
          "BACKEND_HOST" in rest and "'127.0.0.1'" in rest, "restart_backend.ps1")
    # ★ v2.11.52c：`.ps1` 的**反引号续行后面不能有空行** ✗ —— PowerShell 会在那里断语句 ✓，
    #   而"多一个空行"这种损坏**看起来完全正常** ✓（我把它写进去过一次：后端起不来，
    #   但脚本语法校验还是 OK ✗）⇒ 用脚本盯住 ✓。
    bad_cont = []
    for name in ("restart_backend.ps1", "tag_release.ps1"):
        ls = read(os.path.join(ROOT, "scripts", name)).replace("\r\n", "\n").split("\n")
        for i, ln in enumerate(ls[:-1]):
            if ln.rstrip().endswith("`") and not ls[i + 1].strip():
                bad_cont.append("%s:%d" % (name, i + 1))
    check(STATIC, "`.ps1` 反引号续行后无空行（否则语句会断 ✗）",
          not bad_cont, "、".join(bad_cont[:3]) or "无")

    check(STATIC, "metrics + Prometheus 文本 + 慢请求告警",
          "/api/metrics" in mainpy and "_warn_if_slow" in mainpy, "main.py")
    check(STATIC, "版本三处一致",
          version and version in read(os.path.join(FRONTEND, "src", "version.ts")) and
          ('"version": "%s"' % version) in read(os.path.join(FRONTEND, "package.json")), version)
    check(STATIC, "当前版本在 change_log 有段", ("## [%s]" % version) in clog, "")
    check(STATIC, "README 有「当前基线」表", "当前基线" in readme, "")

    # ================= 输出 =================
    total = len(RESULTS)
    passed = sum(1 for _k, _n, ok, _e in RESULTS if ok)
    idx = 10.0 * passed / total if total else 0.0
    by_kind = {}
    for kind, _n, ok, _e in RESULTS:
        p, t = by_kind.get(kind, (0, 0))
        by_kind[kind] = (p + (1 if ok else 0), t + 1)

    print("\n  客观评分 v2（版本 %s，%s）" % (version, "全量" if a.full else "快检"))
    print("  " + "=" * 92)
    for i, (kind, name, ok, ev) in enumerate(RESULTS, 1):
        print("  %2d. [%s] %-42s %s  %s" % (i, kind, name, "✓" if ok else "✗", ev[:34]))
    print("  " + "=" * 92)
    print("  通过 %d / %d  ⇒  指数 %.1f / 10" % (passed, total, idx))
    for kind in (MEASURED, RATCHET, STATIC):
        if kind in by_kind:
            p, t = by_kind[kind]
            print("    %s组：%d / %d = %.0f%%" % (kind, p, t, 100.0 * p / t))
    print("  ⚠ **静态组只证明配置还在** ✗ —— 看绝对质量请只信 `实测组` 与 `棘轮组` 的通过率。")
    if not a.full:
        print("  ⚠ 未开 --full ⇒ 实测组为空（mypy/pytest/覆盖率都没真跑）✗ 指数不可与全量比较。")

    if a.json:
        print("\n" + json.dumps({
            "version": version, "full": a.full, "passed": passed, "total": total,
            "index": round(idx, 2),
            "by_kind": {k: {"passed": p, "total": t} for k, (p, t) in by_kind.items()},
            "gates": [{"kind": k, "name": n, "ok": o, "evidence": e} for k, n, o, e in RESULTS],
        }, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
