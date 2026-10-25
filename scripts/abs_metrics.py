# -*- coding: utf-8 -*-
"""**绝对质量评分**要用的原始量（一次性测准，供 `score_abs.py` 用）。

⚠ 这个脚本只**测数**、不打分 ✓ —— 打分逻辑全在 `score_abs.py` 里，阈值公开、可争论 ✓。
"""
from __future__ import annotations

import ast
import json
import os
import re

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BE = os.path.join(ROOT, "backend")
FE = os.path.join(ROOT, "frontend")


def read(p):
    try:
        with open(p, encoding="utf-8") as f:
            return f.read()
    except OSError:
        return ""


def py_files(base):
    for r, _d, fs in os.walk(base):
        if "node_modules" in r or "__pycache__" in r:
            continue
        for f in fs:
            if f.endswith(".py"):
                yield os.path.join(r, f)


def main():
    out = {}

    # ---------- 后端规模 ----------
    app_files = list(py_files(os.path.join(BE, "app")))
    test_files = list(py_files(os.path.join(BE, "tests")))
    out["be_app_files"] = len(app_files)
    out["be_app_lines"] = sum(len(read(p).splitlines()) for p in app_files)
    out["be_test_files"] = len(test_files)
    out["be_test_lines"] = sum(len(read(p).splitlines()) for p in test_files)

    todo = 0
    for p in app_files:
        s = read(p)
        todo += len(re.findall(r"\b(TODO|FIXME|HACK|XXX)\b", s))
    out["be_todo"] = todo

    out["be_type_ignore"] = sum(read(p).count("type: ignore") for p in app_files)
    out["be_cast"] = sum(len(re.findall(r"\bcast\(", read(p))) for p in app_files)

    # 函数/文件规模
    funcs, files_len = [], []
    for p in app_files:
        try:
            tree = ast.parse(read(p))
        except SyntaxError:
            continue
        rel = os.path.relpath(p, BE).replace("\\", "/")
        n = len(read(p).splitlines()) + 1
        files_len.append((n, rel))
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                funcs.append((node.end_lineno - node.lineno + 1, rel, node.name))
    funcs.sort(reverse=True)
    files_len.sort(reverse=True)
    out["be_func_gt300"] = len([f for f in funcs if f[0] > 300])
    out["be_func_max"] = funcs[0][0] if funcs else 0
    out["be_file_gt1200"] = len([f for f in files_len if f[0] > 1200])
    out["be_file_gt2600"] = len([f for f in files_len if f[0] > 2600])
    out["be_file_max"] = files_len[0][0] if files_len else 0
    out["be_func_total"] = len(funcs)

    # ---------- 前端规模 ----------
    fe_files, fe_lines = [], 0
    for r, d, fs in os.walk(os.path.join(FE, "src")):
        if "__tests__" in r:
            continue
        for f in fs:
            if f.endswith((".ts", ".tsx")):
                p = os.path.join(r, f)
                n = len(read(p).splitlines()) + 1
                fe_lines += n
                fe_files.append((n, os.path.relpath(p, FE).replace("\\", "/")))
    fe_files.sort(reverse=True)
    out["fe_src_files"] = len(fe_files)
    out["fe_src_lines"] = fe_lines
    out["fe_comp_gt1300"] = len([f for f in fe_files if f[0] > 1300])
    out["fe_comp_max"] = fe_files[0][0] if fe_files else 0
    out["fe_top5"] = [[n, p] for n, p in fe_files[:5]]

    # E2E：真端到端还是拦住接口
    e2e = os.path.join(FE, "e2e")
    spec_txt = ""
    n_spec = n_test = 0
    if os.path.isdir(e2e):
        for f in os.listdir(e2e):
            if f.endswith(".spec.ts"):
                n_spec += 1
                t = read(os.path.join(e2e, f))
                spec_txt += t
                n_test += len(re.findall(r"^\s*test\(", t, re.M))
    out["e2e_specs"] = n_spec
    out["e2e_tests"] = n_test
    out["e2e_mock_routes"] = spec_txt.count("page.route")

    # ★ v2.11.54：**真后端 E2E**（不 mock 的那套 ✓）—— 单独数，因为它才是"联调通没通"的证据 ✓
    real = os.path.join(FE, "e2e-real")
    n_real = 0
    if os.path.isdir(real):
        for f in os.listdir(real):
            if f.endswith(".spec.ts"):
                n_real += len(re.findall(r"^\s*test\(", read(os.path.join(real, f)), re.M))
    out["e2e_real_tests"] = n_real
    out["e2e_real_mock_routes"] = sum(
        read(os.path.join(real, f)).count("page.route")
        for f in (os.listdir(real) if os.path.isdir(real) else []) if f.endswith(".spec.ts"))

    # ---------- 文档 ----------
    md = [os.path.join(r, f) for r, _d, fs in os.walk(os.path.join(ROOT, "md")) for f in fs if f.endswith(".md")]
    md += [os.path.join(ROOT, "README.md"), os.path.join(ROOT, "CONTRIBUTING.md")]
    md = [p for p in md if os.path.isfile(p)]
    out["doc_files"] = len(md)
    out["doc_lines"] = sum(len(read(p).splitlines()) for p in md)
    out["readme_lines"] = len(read(os.path.join(ROOT, "README.md")).splitlines())
    out["has_arch_doc"] = os.path.isfile(os.path.join(ROOT, "md", "architecture.md"))

    # ---------- 依赖 ----------
    req = read(os.path.join(BE, "requirements.txt"))
    deps = [ln.strip() for ln in req.splitlines() if ln.strip() and not ln.strip().startswith("#")]
    out["be_deps"] = len(deps)
    out["be_deps_pinned"] = len([d for d in deps if "==" in d])

    # ---------- 其它 ----------
    out["ci_jobs"] = len(re.findall(r"^  [a-z-]+:\s*$", read(os.path.join(ROOT, ".github", "workflows", "ci.yml")), re.M))
    # ★ v2.11.54：定时跑放在**独立 workflow** 里（`nightly.yml` ✓）——
    #   所以这里要看**所有** workflow，只看 ci.yml 会误判"没有定时跑"✗。
    wf_dir = os.path.join(ROOT, ".github", "workflows")
    all_wf = "".join(read(os.path.join(wf_dir, f)) for f in os.listdir(wf_dir) if f.endswith(".yml"))
    out["ci_has_schedule"] = "schedule:" in all_wf
    out["has_nightly_workflow"] = os.path.isfile(os.path.join(wf_dir, "nightly.yml"))
    # ★ v2.11.56：发版脚本是否**顺手建 GitHub Release**（notes 自动生成 ✓）——
    #   只看"有没有 release"会随时间漂 ✗，看"脚本里有没有这一步"才是能力 ✓。
    out["has_auto_release_notes"] = "release create" in read(os.path.join(ROOT, "scripts", "tag_release.ps1"))
    # ★ v2.11.91：远端同步的**实测**（见文件末尾 _remote_tag_state ✓）
    _ver = ""
    try:
        _m = re.search(r'__version__\s*=\s*"([^"]+)"', read(os.path.join(BE, "app", "__init__.py")))
        _ver = _m.group(1) if _m else ""
    except Exception:
        _ver = ""
    out["version"] = _ver
    out["remote_tag_state"] = _remote_tag_state(_ver) if _ver else "unknown"

    # ★ v2.11.56：**实测** ruff 启用了多少条规则 —— 原来这个数写在 `score_baseline.json` 里 ✗，
    #   改了 `select` 却忘了改基线 ⇒ 分数不动 ✓（"数字靠人维护"必然漂 ✗）。现在从配置里数 ✓。
    sel = re.search(r"^select\s*=\s*\[(.+?)\]", read(os.path.join(BE, "pyproject.toml")), re.M | re.S)
    out["ruff_rules"] = len(re.findall(r'"\w+"', sel.group(1))) if sel else 0
    out["perf_gates"] = len(re.findall(r'^\s{4}"[^"]+":\s*[\d.]+',
                                       read(os.path.join(BE, "tests", "test_perf_absolute.py")), re.M))
    # ★ v2.11.57：**内存基线**是否存在（tracemalloc 阈值 + 缓存逐出自检 ✓）——
    #   这是"性能与资源"里唯一能进常规 CI 的硬指标 ✓（真压测要起服务 ✗）。
    mem_test = os.path.join(BE, "tests", "test_mem_baseline.py")
    out["has_mem_baseline"] = os.path.isfile(mem_test) and "tracemalloc" in read(mem_test)

    # ★ v2.11.67：**可选鉴权 + 限流** ✓ —— 两个开关都要真的接在中间件上才算能力 ✓
    #   （只写一个 `security.py` 但没挂到 app 上，等于没有 ✗）。
    sec = read(os.path.join(BE, "app", "security.py"))
    mainpy = read(os.path.join(BE, "app", "main.py"))
    out["has_auth"] = "API_TOKEN" in sec and "token_ok" in sec and "_guard_mw" in mainpy
    out["has_rate_limit"] = "RATE_LIMIT_RPM" in sec and "LIMITER.check" in mainpy

    # ★ v2.11.68：前端**是否还挂着 Excel 库**（xlsx 已停维护 2 high 无 fix ✓；exceljs 更差 ✗）
    #   证据取 `package.json`（离线、确定 ✓）—— 真正的漏洞数由 CI 的 `npm audit --audit-level=high` 守 ✓。
    pkg = read(os.path.join(FE, "package.json"))
    out["fe_excel_lib_dep"] = ('"xlsx"' in pkg) or ('"exceljs"' in pkg)
    #: 后端是否有「通用二维表导出」端点（前端不引 Excel 库的**前提** ✓）
    out["has_aoa_export"] = "export/aoa-xlsx" in read(os.path.join(BE, "app", "routers", "export_xlsx.py"))
    # ★ v2.11.61：**指标抓取端**（拉 + 算人类关心的数 + 超阈值退出码 ✓）——
    #   "有点位但没人抓"和"有抓取端"是两件事 ✓（外部评审点过 ✓）。
    scrape = os.path.join(ROOT, "scripts", "scrape_metrics.py")
    out["has_scrape_tool"] = os.path.isfile(scrape) and "--max-avg-ms" in read(scrape)
    # ★ v2.11.62：**分层违规是否已冻结成棘轮** ✓ —— 有 19 处违规但**没闸门**时，
    #   "架构分层清楚"这句话就只是一句文档 ✗（下一个人再多写 5 处也没人知道 ✓）。
    lay = os.path.join(BE, "tests", "test_layering.py")
    laytxt = read(lay)
    out["has_layering_gate"] = os.path.isfile(lay) and "BASELINE: dict[str, int]" in laytxt
    out["layering_frozen"] = sum(int(n) for n in re.findall(r"->\s*\w+\":\s*(\d+)", laytxt))
    out["complexity_frozen"] = len(re.findall(r'^\s+"[^"]+::', read(os.path.join(BE, "tests", "test_complexity.py")), re.M))

    dst = os.path.join(ROOT, "scripts", "abs_metrics.json")
    with open(dst, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=2)
    print("  已写入 %s（%d 个指标）" % (dst, len(out)))


# ⚠ 入口调用**必须放到文件最末** ✗→✓ —— 我 v2.11.91 把 `_remote_tag_state` 等 helper
# 追加在文件末尾，而 `main()` 在上面就执行了 ⇒ `NameError: _remote_tag_state is not defined` ✓
# （模块是自顶向下执行的 ✓）。


# ===== ★ v2.11.91：远端同步的**实测**（评审指出"只测能力存在"✗） =====


def _git_ls_remote_tags(timeout: int = 20) -> "set[str] | None":
    """远端 tag 集合；**不可达就返回 None**（不抛 ⇒ 由调用方决定降级 ✓）。

    为什么要试三种姿势 ✗→✓：本机访问 GitHub 需要代理（我平时 push 都是
    `git -c http.proxy=...` ✓），而评分脚本不能假设一定配了 ⇒
    ① 直接跑；② 带上 `HTTP_PROXY/HTTPS_PROXY` 环境变量（如果有）；
    ③ 都没有 ⇒ `None`（**判"未知"，不给满分也不冤枉** ✓）。
    """
    import os as _os
    import subprocess as _sp

    attempts = [[]]
    for env_name in ("HTTPS_PROXY", "https_proxy", "HTTP_PROXY", "http_proxy"):
        proxy = _os.environ.get(env_name)
        if proxy:
            attempts.append(["-c", "http.proxy=%s" % proxy, "-c", "https.proxy=%s" % proxy])
            break
    for extra in attempts:
        try:
            r = _sp.run(["git", *extra, "ls-remote", "--tags", "origin"],
                        cwd=ROOT, capture_output=True, text=True, timeout=timeout)
        except Exception:                                       # noqa: BLE001
            continue
        if r.returncode != 0:
            continue
        tags = set()
        for ln in (r.stdout or "").splitlines():
            if "refs/tags/" in ln:
                tags.add(ln.split("refs/tags/", 1)[1].replace("^{}", "").strip())
        if tags:
            return tags
    return None


def _remote_tag_state(version: str) -> str:
    """`"ok"` / `"missing"` / `"unknown"` —— 见文件头那张表 ✓。"""
    tags = _git_ls_remote_tags()
    if tags is None:
        return "unknown"
    return "ok" if ("v" + version) in tags else "missing"


if __name__ == "__main__":
    main()
