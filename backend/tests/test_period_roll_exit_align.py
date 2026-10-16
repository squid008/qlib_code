# -*- coding: utf-8 -*-
"""★ v2.8.8（用户 2026-10-16 报障 ✓）：**离场矩阵的列轴必须对齐到"引擎自己的列"** ✗→✓

现象（真实任务 `cefb7bfc3f41` / `1461057f8af1` ✓）：含入场价的离场公式**一次都没触发**、
含离场曲线与"对照"**逐位相同**、五个离场计数全 0 ✗；引擎 `diag.exit_note` 里躺着
`ValueError: operands could not be broadcast together with shapes (39,) (36,)` ✓
（用户原话："我止损价设置得很紧不可能不触发的，所以回测大概率有问题" ✓ —— 判断完全正确 ✓）。

**根因**：调用方（`routers/factors.py::_exit_matrix` / `_exit_pre_mats` ✓）按 `panel["CLOSE"]`
的**全部列**建矩阵 ✓，而引擎内部列轴是 `codes = sorted(set(信号 ∩ CLOSE 列))` ✗ ⇒ **39 vs 36** ✓。
普通离场矩阵被引擎的形状自检拦下（还算响 ✓），**含入场价的规则没有自检** ✗ ⇒ 逐日求值抛异常
⇒ 引擎按"规则失败 ⇒ 关掉离场"处理 ⇒ **退化成对照** ✓（看起来就是"止损没生效" ✓）。

本文件钉住两条：
  ① **规则**（`exit_rule` ✓）的预计算列按整面板给 ⇒ 仍须**正确取列并触发** ✓；
  ② **普通离场矩阵**（`exit_mat` ✓）同理 ✓（旧实现会被形状自检直接关掉 ✗）。

⚠ 纯合成数据 ⇒ CI（无数据）也能跑 ✓。
"""
import numpy as np
import pandas as pd

from app.factors.exit_rules import split_entry_rule
from app.signals.period_roll import run_period_roll

CAL = pd.bdate_range("2024-01-01", periods=60)
# ⚠ 关键构造：面板给 **5** 列，信号只涉及 **3** 只 ⇒ 引擎内部 `codes`(3) ≠ 面板列数(5) ✓
#   —— 这正是真实事故的形状（39 vs 36 ✓），**列数相等就测不出这个 bug** ✗。
PANEL_CODES = ["SH600001", "SH600002", "SH600003", "SH600004", "SH600005"]
SIG_CODES = PANEL_CODES[:3]


def _panel(px: float = 10.0) -> dict:
    n, m = len(CAL), len(PANEL_CODES)
    arr = np.tile(np.array([px] * m, dtype=float), (n, 1))
    for i in range(40, n):                       # 第 40 天起每天 −1% ⇒ 跌 2% 的止损必然触发 ✓
        arr[i] = px * (0.99 ** (i - 39))
    df = pd.DataFrame(arr, index=CAL, columns=PANEL_CODES)
    return {"CALENDAR": pd.DataFrame(index=CAL), "CLOSE": df, "OPEN": df,
            "CLOSE_RAW": df, "OPEN_RAW": df,
            "LIMIT_UP": df * 1.1, "LIMIT_DOWN": df * 0.9, "_limits_inferred": True}


def _signals() -> pd.DataFrame:
    return pd.DataFrame({"date": [CAL[0]] * len(SIG_CODES), "code": SIG_CODES,
                         "side": [1] * len(SIG_CODES)})


def test_rule_pre_mats_with_full_panel_columns_still_triggers():
    """★ 规则的预计算列按**整面板列**给（= 调用方的真实行为 ✓）⇒ 仍须触发 ✓。"""
    panel = _panel()
    rule = split_entry_rule("Lt($close,Mul($entry_first,0.98))", 0.0)
    assert rule is not None, "拆分失败 ✗（`$entry_first` 应被识别 ✓）"
    rule.bind([panel["CLOSE"].to_numpy(dtype=float)] * len(rule.pre_exprs))
    out = run_period_roll(_signals(), panel, fill="t1_open", cost=0.004, capital=1e6,
                          strict_limit=True, exit_rule=rule)
    assert out.diag.get("exit_note") == "", "规则求值失败 ✗：%s" % out.diag.get("exit_note")
    st = (out.stats or {}).get("period_roll") or {}
    assert int(st.get("exit_signals") or 0) > 0, "一次都没触发 ⇒ 列轴又没对齐 ✗"
    assert int(st.get("exit_sells") or 0) > 0, "没真卖出 ✗"


def test_exit_mat_with_full_panel_columns_still_triggers():
    """★ 普通离场矩阵同理 ✓（旧实现在这里会被形状自检**直接关掉** ✗）。"""
    panel = _panel()
    mat = np.zeros((len(CAL), len(PANEL_CODES)), dtype=bool)
    mat[45:, :] = True
    out = run_period_roll(_signals(), panel, fill="t1_open", cost=0.004, capital=1e6,
                          strict_limit=True, exit_mat=mat)
    st = (out.stats or {}).get("period_roll") or {}
    assert bool(st.get("exit_enabled")) is True, "离场被形状自检关掉了 ✗（列轴没对齐 ✓）"
    assert int(st.get("exit_sells") or 0) > 0, "离场矩阵没生效 ✗"


def test_clean_call_unchanged():
    """★ 反向护栏：**不带离场**时行为不变 ✓（不改老路径 ✗）。"""
    panel = _panel()
    out = run_period_roll(_signals(), panel, fill="t1_open", cost=0.004, capital=1e6,
                          strict_limit=True)
    st = (out.stats or {}).get("period_roll") or {}
    assert bool(st.get("exit_enabled")) is False
    assert int(st.get("exit_signals") or 0) == 0
    assert out.diag.get("exit_note") == ""
