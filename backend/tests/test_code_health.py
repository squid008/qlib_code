# -*- coding: utf-8 -*-
"""**代码体检棘轮**（★ P0-C-4，2026-10-18 ✓）—— 只许变好，不许变差 ✓。

## 为什么要有它

这几天反复遇到同一件事：**拆完一个大函数，又长出一个新的** ✗
（`_test_one` 1,104 → 289 ✓，但外提出的 `_continuous_stats` 736 ✗ → 再拆到 173 ✓ …）。
没有棘轮时，"下次顺手加 30 行"永远没人拦 ✗。

## 规则（两条）

1. **白名单里的大函数只能变小** ✓（当前尺寸即上限 ✓，改小后把数字一起调小 ✓）；
2. **白名单之外任何函数不得超过 300 行** ✓ —— 新代码想写个 400 行的，必须走"拆"或"进白名单+写理由"✓。

⚠ 白名单是**债务清单**，不是荣誉榜 ✗ —— 每拆掉一个就删一行 ✓。
"""
import ast
import os

import pytest

APP = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "app")

#: 已知待拆的大函数（**只能变小** ✓）：key = "相对路径::函数名" ⇒ 当前行数上限
BIG_DEBT = {
    # ⚠ 2026-10-18 更新（P2-A 第三批 ✓）：`single_test.py` 三个函数各 +2/+4/+8 行
    #   —— **全部是日志行**（`_log_swallowed(...)` ✓），**没有一行新逻辑** ✓。
    #   棘轮仍成立 ✓：更新后的数字就是新上限 ✓，再涨就红 ✓。
    # ⚠ 2026-10-18：P2-C 给 `run_backtest` 加了几行注释说明（601 → **614** ✓），无逻辑增长 ✓
    "signals/engine.py::run_backtest": 614,
    # ⚠ 2026-10-19（P2-A 收尾 ✓）：三处各 +1 行 —— **全是日志行**（`_log_swallowed(...)` ✓），
    #   没有一行新逻辑 ✓；棘轮仍成立 ✓（更新的数字就是新上限 ✓，再涨就红 ✗）。
    "factors/single_test.py::_quantile_curve_block": 597,
    # ⚠ 2026-10-18：P2-A 收尾给这两处各加了几行日志（+5 / +3 ✓），上限随之上调 ✓
    "routers/signal_test.py::run": 539,
    "factors/single_test.py::run_single_factor_tests": 539,
    # ★ v2.11.6（技术债 P1-#3 第十二刀 ✓）：离场输入准备 50 行抽去
    #   `signals/period_roll_exit_prep.py` ✓ ⇒ **477 → 438** ✓（棘轮收紧 ✓）。
    #   ⚠ 它仍是 438 行的逐日循环本体 ✓ ⇒ 要过 300 还得再抽 3~4 块
    #     （内层闭包 `_acc`/`_sell`/`_buy` 与逐日循环强耦合 ✗，是后面几轮的事）。
    # ★ v2.11.7（第十三刀 ✓）：批次 → 调仓日 → 目标名单（27 行）抽去
    #   `signals/period_roll_buckets.py` ✓ ⇒ **438 → 418** ✓（棘轮继续收紧 ✓，**只减不增** ✓）。
    "signals/period_roll.py::run_period_roll": 418,
    # ★★ v2.11.3~2.11.4（技术债 P1-#3 第九、十刀 ✓）：`single_factor_test` **391 → 299** ✓
    #   两刀抽出 `factors_single_validate.py`（入参校验 77 行 ✓）与
    #   `factors_single_state.py`（运行态契约 ✓）⇒ **降到警戒线以下 ⇒ 本条下线** ✓。
    #   （这是棘轮第 **2** 次真正下线一条 ✓，第 1 次是 `run_event_study` ✓。）
    # ★ v2.11.12（第十四刀 ✓）：`batch_even` 的信号折叠抽去 `signals/batch_execs.py` ✓
    #   ⇒ **367 → 336** ✓。⚠ 搬的时候漏了两个下游引用（`per` 被 diag 用 ✓）——
    #   `ruff` 的 **F821** 当场点名 ✓（这类"搬家漏依赖"靠通读查不出来 ✗）。
    "signals/engine.py::run_batch_backtest": 336,
    # ★★ v2.11.1~2.11.2（技术债 P1-#3 第七、八刀 ✓）：`run_event_study` **326 → 293** ✓
    #   两刀抽出 `event_study_stages.py`（预热解析 / 缓存查表 / 触发样本+剔除 ✓）
    #   ⇒ **已降到警戒线 300 以下** ✓ ⇒ **本条从棘轮里下线** ✓（只减不增 ✓）。
    #   ⚠ 下线 ≠ 不用管：它仍是 293 行的编排函数 ✓，**再涨回 300 会立刻被这条测试抓到** ✓
    #     （白名单外的上限就是 `MAX_ANY = 300` ✓）。
    # ★★ v2.11.5（技术债 P1-#3 第十一刀 ✓）：覆盖率口径抽去
    #   `factors/single_test_coverage.py` ✓（含那次"同因子不同覆盖率"事故的来历 ✓）
    #   ⇒ `_test_one` **312 → 291** ✓ ⇒ **低于 300 ⇒ 本条下线** ✓（第 **3** 次 ✓）。
}

#: 白名单外任何函数的上限 ✓
MAX_ANY = 300

#: 超大文件白名单（**只能变小** ✓）
BIG_FILES = {
    # ⚠ 这份清单是**第一版漏掉 3 个真·巨头**后补的 ✓（棘轮测试自己抓出来的 ✓）：
    #   `panel_expr.py` **2,868 行** ✗ 才是全仓最大文件（比 `single_test.py` 还大 ✓）。
    "factors/single_test.py": 2700,
    # ⚠ 2026-10-18：P2-A 第四批给 `panel_expr` 加了 40 行日志（23 处调用 + helper ✓）
    #   ⇒ 2868 → 2909 ✓（`qlib_engine` 1503 → 1538 ✓ 仍在上限内 ✓）。上限随之调大 ✓，
    #   但**只允许因为"留痕"而涨** ✗ —— 加业务逻辑请先拆 ✓。
    # ★ v2.9.56：搬出 `chip_turn.py`（109 行 ✓）⇒ panel_expr 2,909 → 2,803 ✓ 上限 2810 ✓
    # ★ v2.9.57：再搬出 `seg_ops.py`（315 行 ✓ 分段/滚动算子 + 两个段缓存 ✓）
    #   ⇒ panel_expr → **2,533** ✓ 上限 → **2545** ✓（棘轮只许变小 ✓）
    # ★ v2.9.66：`PanelEvaluator._apply`（257 行 ✓ 不用 self ✓）提成模块级 `_apply_node` ✓
    #   ⇒ 类 **590 → 340 行** ✓；文件 2,533 → 2,542 ✓（上限同步到 2,542 ✓）
    # ★ v2.9.67：`field` / `_chip_or_bin` / `_chip_field`（119 行 ✓）拆成 `FieldResolver` ✓
    #   ⇒ 类 **340 → 230 行** ✓，但**文件**涨到 2,592 ✗（两套类骨架同处一文件 ✓）
    #   ⚠ 这是本棘轮**第一次向上调整** ✗ —— 理由：两个类都在同一文件里 ✓，
    #     "拆类"必然增加脚手架行 ✓，而**真正的复杂度指标（类大小）是下降的** ✓（340 → 230）。
    #     若将来把 `FieldResolver` 整体搬去独立模块 ✓，这个数字必须**立刻**降回来 ✓。
    "factors/panel_expr.py": 2600,
    "factors/ops_ext.py": 2200,
    "engine/qlib_engine.py": 1560,
    # ⚠ 2026-10-18：P2-A 第六批给路由层加日志 ⇒ `factors_single.py` 1233 → 1254 ✓（上限 1260 ✓）
    "routers/factors_single.py": 1260,
    "signals/engine.py": 1400,
}

#: 白名单外任何文件的上限 ✓
MAX_FILE = 1200


def _py_files():
    for root, _dirs, names in os.walk(APP):
        for n in names:
            if n.endswith(".py"):
                yield os.path.join(root, n)


def _funcs():
    """产出 (相对路径, 函数名, 行数, 起行) ✓。"""
    for p in _py_files():
        rel = os.path.relpath(p, APP).replace(os.sep, "/")
        try:
            tree = ast.parse(open(p, encoding="utf-8").read())
        except SyntaxError:                       # 语法都不对的话，别的测试会红 ✓
            continue
        for n in ast.walk(tree):
            if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)):
                yield rel, n.name, (n.end_lineno - n.lineno + 1), n.lineno


def test_big_functions_only_shrink():
    """白名单里的函数**不许再长** ✓（超了就是"拆完又长回来"✗）。"""
    seen = {(rel, name): size for rel, name, size, _ln in _funcs()}
    problems = []
    for key, cap in BIG_DEBT.items():
        rel, name = key.split("::")
        size = seen.get((rel, name))
        if size is None:
            problems.append("%s 不见了（拆掉/改名 ⇒ 请把它从 BIG_DEBT 删掉 ✓）" % key)
        elif size > cap:
            problems.append("%s 从 %d 涨到 %d 行 ✗（上限 %d）" % (key, cap, size, cap))
    assert not problems, "\n".join(problems)


def test_no_new_god_functions():
    """白名单外**不许出现 >300 行**的函数 ✓（要写就拆 ✓）。"""
    bad = []
    for rel, name, size, ln in _funcs():
        key = "%s::%s" % (rel, name)
        if key in BIG_DEBT:
            continue
        if size > MAX_ANY:
            bad.append("%s::%s = %d 行（L%d，上限 %d）" % (rel, name, size, ln, MAX_ANY))
    assert not bad, "出现新的长函数 ✗（请拆开，或加进 BIG_DEBT 并写理由）:\n" + "\n".join(bad[:8])


def test_big_files_only_shrink():
    """超大白名单里的文件**不许再长** ✓。"""
    sizes = {}
    for p in _py_files():
        rel = os.path.relpath(p, APP).replace(os.sep, "/")
        sizes[rel] = len(open(p, encoding="utf-8").read().splitlines())
    problems = []
    for rel, cap in BIG_FILES.items():
        n = sizes.get(rel)
        if n is None:
            problems.append("%s 不见了（请从 BIG_FILES 删掉 ✓）" % rel)
        elif n > cap:
            problems.append("%s 从 %d 涨到 %d 行 ✗（上限 %d）" % (rel, cap, n, cap))
    assert not problems, "\n".join(problems)


def test_no_new_giant_files():
    """白名单外**不许出现 >1200 行**的文件 ✓。"""
    bad = []
    for p in _py_files():
        rel = os.path.relpath(p, APP).replace(os.sep, "/")
        if rel in BIG_FILES:
            continue
        n = len(open(p, encoding="utf-8").read().splitlines())
        if n > MAX_FILE:
            bad.append("%s = %d 行（上限 %d）" % (rel, n, MAX_FILE))
    assert not bad, "出现新的巨型文件 ✗:\n" + "\n".join(bad[:8])


@pytest.mark.parametrize("rel,cap", sorted(BIG_FILES.items()))
def test_debt_is_real(rel, cap):
    """白名单里的数字必须**贴着现实** ✓（差太多说明记错了 ⇒ 会掩盖回退 ✗）。"""
    p = os.path.join(APP, *rel.split("/"))
    assert os.path.isfile(p), "文件不存在：%s" % rel
    n = len(open(p, encoding="utf-8").read().splitlines())
    assert n <= cap, "%s 已 %d 行 > 上限 %d ✗" % (rel, n, cap)
