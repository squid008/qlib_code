# -*- coding: utf-8 -*-
"""**复杂度棘轮**（★ v2.11.41）—— 圈复杂度**只减不增** ✓。

## 为什么还要这把尺子
`tests/test_code_health.py` 只量**行数**（>300 行的函数 ✓）。但"行数短而分支多"同样难读 ✓ ——
一个 40 行却套了 12 层 `if/for` 的函数，比 300 行的直线编排更难改 ✓。

## 口径
· 工具：`ruff` 的 **C901**（mccabe 圈复杂度 ✓）—— CI 里 ruff 本来就是闸门 ✓，**不用加依赖** ✓；
· 阈值 **20** ✓：实测 15 会报几十条（只能"上线即红"✗）、35 还剩 8 条（太松✗）、
  20 正好 **31** 条 ⇒ 可以逐条冻结 ✓；
· 冻结 **(文件, 函数) → 复杂度数值** ✓ ⇒ 不只是"不许新增"，**"变复杂"也不许** ✓。

## 规矩（与 `test_code_health` 的 `BIG_DEBT` 完全一致 ✓）
1. 拆分后**必须**把这里的数字改小 ✓（第 3 条测试会列出"可下调"的项 ✓）；
2. ⚠ **不许**为了让测试变绿去调大数字或删条目 ✗ —— 那等于把尺子改短 ✓；
3. 新增复杂函数 ⇒ 这里红 ✓ ⇒ 要么拆（早返回 / 抽小函数 / dict 派发替掉长 if 链 ✓），
   要么在 PR 里说清为什么必须复杂 ✓ 再加进 `FROZEN` ✓。

基线：2026-10-23 v2.11.41 实测生成 ✓。
"""
import os
import re
import subprocess
import sys

THRESHOLD = 20

#: (文件, 函数) → 冻结的圈复杂度（**只许变小** ✓）
FROZEN = {
    ("app/engine/charts.py", "_save_curve_snapshot"): 29,
    ("app/engine/metrics.py", "_extract_result"): 22,
    ("app/engine/periodic_strategy.py", "generate_trade_decision"): 24,
    ("app/engine/qlib_engine.py", "run_backtest"): 22,
    ("app/engine/qlib_engine.py", "_run_rolling"): 35,
    ("app/exports/spec.py", "read_export"): 33,
    ("app/exports/topk_doc.py", "build_doc"): 27,
    ("app/factors/event_study.py", "compute_baseline_curves"): 22,
    ("app/factors/event_study.py", "run_event_study"): 26,
    ("app/factors/panel_expr.py", "parse_expr"): 24,
    ("app/factors/panel_expr.py", "_apply_node"): 63,
    ("app/factors/parser/codegen.py", "_const_fold"): 28,
    ("app/factors/parser/codegen.py", "_gen_func"): 81,
    ("app/factors/single_test.py", "_test_one"): 25,
    ("app/factors/single_test.py", "_load_feature_panel"): 25,
    ("app/factors/single_test.py", "run_single_factor_tests"): 54,
    ("app/factors/single_test.py", "_continuous_stats"): 24,
    ("app/factors/single_test.py", "_quantile_curve_block"): 63,
    ("app/factors/single_test.py", "_perf_of"): 27,
    ("app/factors/single_test.py", "_sft_horizon_loop"): 24,
    ("app/routers/factors_event.py", "event_study_nav"): 32,
    ("app/routers/factors_single.py", "_attach_period_roll"): 22,
    ("app/routers/factors_single.py", "single_factor_test"): 28,
    ("app/routers/factors_single.py", "_run"): 25,
    ("app/routers/signal_test.py", "_apply_excludes"): 24,
    ("app/routers/signal_test.py", "run"): 59,
    ("app/signals/engine.py", "run_backtest"): 72,
    ("app/signals/engine.py", "run_batch_backtest"): 47,
    ("app/signals/period_roll.py", "run_period_roll"): 57,
    ("app/signals/pricing.py", "load_price_panel"): 23,
    ("app/signals/replay.py", "replay_trades"): 26,
}

_PAT = re.compile(r"^(app[/\\][^:]+):\d+:\d+: C901 `(\w+)` is too complex \((\d+) > ")


def _measure():
    """跑 ruff 拿当前复杂度 ⇒ `{(文件, 函数): 复杂度}` ✓。"""
    be = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    out = subprocess.run(
        [sys.executable, "-m", "ruff", "check", "app", "--select", "C901",
         "--config", "lint.mccabe.max-complexity=%d" % THRESHOLD,
         "--output-format", "concise"],
        cwd=be, capture_output=True, text=True, encoding="utf-8", errors="replace")
    got = {}
    for line in (out.stdout or "").splitlines():
        m = _PAT.match(line.strip())
        if m:
            got[(m.group(1).replace("\\", "/"), m.group(2))] = int(m.group(3))
    return got


def test_known_complex_functions_do_not_grow():
    """⭐⭐ 已知复杂函数**只能变简单** ✓ —— 变复杂或冒出新函数都算红 ✓。

    （规矩见文件头 ✓：**不许**调大数字/删条目来让这条变绿 ✗。）
    """
    got = _measure()
    grew = {k: (FROZEN[k], got[k]) for k in FROZEN if k in got and got[k] > FROZEN[k]}
    assert not grew, (
        "这些函数**变复杂了** ✗（旧 → 新）：%s\n"
        "  ⇒ 把新分支抽成小函数 ✓（不许改 FROZEN 的数字 ✗）"
        % ", ".join("%s::%s %d→%d" % (k[0], k[1], v[0], v[1])
                    for k, v in sorted(grew.items())))


def test_no_new_complex_functions():
    """⭐⭐ **不许新增**超阈值（20）的函数 ✓ —— 新代码从一开始就得拆 ✓。"""
    got = _measure()
    new = sorted(k for k in got if k not in FROZEN)
    assert not new, (
        "这些**新函数**圈复杂度超过 %d ✗：%s\n"
        "  ⇒ 抽小函数 / 早返回 / 用 dict 派发替掉长 if 链 ✓；\n"
        "  ⇒ 若确实必须复杂，先在 PR 里说明理由，再把它加进 FROZEN ✓（并附一句为什么 ✓）"
        % (THRESHOLD, ", ".join("%s::%s (%d)" % (k[0], k[1], got[k]) for k in new)))


def test_lists_values_that_can_be_lowered():
    """⭐ 拆简单了就**该**把 FROZEN 的数字改小 ✓ —— 否则棘轮会慢慢失效 ✓。

    ⚠ 这条**不会失败** ✓（拆完忘改数字是常态 ✓，不该因此红 ✗），只把"可下调"的项打出来 ✓。
    """
    got = _measure()
    better = sorted((k, FROZEN[k], got[k]) for k in FROZEN if k in got and got[k] < FROZEN[k])
    if better:
        print("  可以下调的冻结值：%s" % ", ".join(
            "%s::%s %d→%d" % (k[0], k[1], old, new) for k, old, new in better))
