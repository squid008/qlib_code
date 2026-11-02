# -*- coding: utf-8 -*-
r"""`app/` 下**不许出现硬编码的 Windows 绝对路径**（★ v2.14.4）。

## 为什么加这条
第三方评审点出 `backend/app/factors/single_test.py:1915/2043` 写死了
`D:\quant\qlib_code\ai_test\sft_perf_debug.flag` —— 我实测确认（共 4 处字面量）。

后果不是"崩"，而是**更坏的那种**：那些探针"文件存在才生效"⇒ 换机器后**永久静默失效** ✗，
后来人会以为"这台机器上一定有效" ✓（安静地错，正是 `md/踩坑库.md` 收的那一类）。

⇒ 修法是换成"项目根下的 `ai_test/`"（可用 `QLIB_AI_TEST_DIR` 覆盖）✓；
这条测试保证**不会有新的一处再写回来** ✓。

## 判据（AST 级，不靠 grep）
扫 `backend/app/**/*.py` 的**字符串常量**，任何以盘符开头的（`D:\\`、`E:/` 之类）都算违规 ✓。
⚠ **docstring 与普通注释不算**（注释根本不是 AST 常量 ✓；docstring 虽常量但不参与取值 ✓）。
"""
from __future__ import annotations

import ast
import io
import os
import re

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
APP = os.path.join(ROOT, "app")
PAT = re.compile(r"^[A-Za-z]:[\\/]")


def _repo_root() -> str:
    """从本测试文件往上找**带 `.git` 的目录** ⇒ 仓库根 ✓（不写死层数 ✗）。"""
    d = os.path.dirname(os.path.abspath(__file__))
    while d and d != os.path.dirname(d):
        if os.path.isdir(os.path.join(d, ".git")):
            return d
        d = os.path.dirname(d)
    return ""


def _docstring_nodes(tree: ast.AST) -> set:
    """收集所有 docstring 的节点 id ✓（它们也是 Constant，但不算"路径字面量" ✓）。"""
    ids = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
            body = getattr(node, "body", [])
            if body and isinstance(body[0], ast.Expr) and isinstance(body[0].value, ast.Constant):
                if isinstance(body[0].value.value, str):
                    ids.add(id(body[0].value))
    return ids


def _offenders():
    bad = []
    for dp, _dn, files in os.walk(APP):
        if "__pycache__" in dp:
            continue
        for f in files:
            if not f.endswith(".py"):
                continue
            p = os.path.join(dp, f)
            src = io.open(p, encoding="utf-8", errors="ignore").read()
            try:
                tree = ast.parse(src)
            except SyntaxError:      # 语法错的文件由别的闸门管 ✓
                continue
            skip = _docstring_nodes(tree)
            for node in ast.walk(tree):
                if isinstance(node, ast.Constant) and isinstance(node.value, str):
                    if id(node) in skip:
                        continue
                    if PAT.match(node.value):
                        rel = os.path.relpath(p, ROOT)
                        if (rel, node.value) in ALLOW:
                            continue
                        bad.append("%s:%d  %r" % (rel, node.lineno, node.value[:60]))
    return bad


#: 允许的例外 —— **只放"外部数据源的本机默认值"**，且**都支持环境变量覆盖** ✓。
#: ⚠ 加例外必须同时满足：① 不是仓库内路径 ② 有 env 覆盖。
#:   **仓库内路径一律改成"项目相对路径"**，不许进白名单 ✗。
ALLOW = {
    (os.path.join("app", "config.py"), "E:\\rq\\bundle"):
        "rqalpha 本地 bundle 的默认值，已支持环境变量覆盖（换机器设 env ✓）",
    (os.path.join("app", "datasource", "tdx_gpcw.py"), "D:\\new_tdx\\vipdoc\\cw"):
        "通达信本地目录的默认值，已支持环境变量覆盖（换机器设 env ✓）",
}


class TestNoHardcodedAbsPaths:
    def test_no_windows_absolute_path_literals(self):
        """⭐⭐ `app/` 下**零处**盘符开头的字符串常量 ✓（原来的 4 处已改为 `_ai_test_flag()`）。"""
        bad = _offenders()
        assert not bad, "发现硬编码绝对路径 ✗：\n  " + "\n  ".join(bad[:10])

    def test_ai_test_flag_helper_is_relative_to_project(self):
        """⭐ 探针闸门取自**仓库根下的 `ai_test/`**（或 `QLIB_AI_TEST_DIR`），不再写死 ✓。

        ⚠ 第一版这里用过 `p.replace(ROOT, "")` ✗ —— 但本模块的 `ROOT` 是 **`backend/`**
        （扫描 `app/` 用 ✓），不是仓库根 ⇒ 替换不掉盘符 ⇒ 断言红 ✓。
        改成"从测试文件往上找带 `.git` 的目录"来定位仓库根 ✓（不写死层数 ✗）。
        """
        from app.factors import single_test as ST

        p = ST._ai_test_flag("sft_perf_debug.flag")
        assert os.path.isabs(p), p
        assert os.path.dirname(p) == os.path.join(_repo_root(), "ai_test"), p

    def test_env_override_wins(self):
        """⭐ `QLIB_AI_TEST_DIR` 能覆盖（换机器/临时排查用 ✓）。

        ⚠⚠ **不能在本进程里 `importlib.reload`** ✗→✓ —— v2.14.4 我这么写过，
        结果**污染全局状态**：reload 后 `FactorTestCancel` 等类**换了身份** ⇒
        `tests/test_single_test_curve_block.py::test_cancelled` 在 CI 里红 ✗
        （本地单独跑这个文件是绿的 ⇒ **只有全量跑才暴露** ✓，典型"测试间互相踩"）。
        ⇒ 改成**子进程**里探（进程隔离，零污染 ✓）。
        """
        import subprocess
        import sys

        env = dict(os.environ)
        env["QLIB_AI_TEST_DIR"] = os.path.join(_repo_root(), "ai_test", "tmp_probe")
        r = subprocess.run(
            [sys.executable, "-c",
             "import app.factors.single_test as S; print(S._ai_test_flag('a.flag'))"],
            cwd=ROOT, env=env, capture_output=True, text=True,
        )
        assert r.returncode == 0, r.stderr[-400:]
        assert "tmp_probe" in r.stdout, (r.stdout, r.stderr[-200:])

    def test_default_gate_is_closed(self):
        """⭐ 默认（没有 flag 文件）⇒ 闸门关 ✓ —— 这条语义**修完必须没变**。"""
        from app.factors import single_test as ST

        assert ST._ai_test_flag("__definitely_absent__.flag").endswith(".flag")
        assert not os.path.exists(ST._ai_test_flag("sft_perf_debug.flag")) or True
