# -*- coding: utf-8 -*-
"""**入场价型离场规则**回归（v2.6.0，用户 2026-10-15 定稿 ✓）。

用户要的写法（原话 ✓）：`止损: CLOSE < ENTRY_PRICE*0.95;` / `止盈: CLOSE >= ENTRY_PRICE*1.10;`
⇒ 引擎得"知道"该股票当前持仓的入场价 ✓。口径（用户拍板 ✓）：
  · `ENTRY_PRICE` = **移动平均成本**（含买入费 ✓；**买入会变、卖出不变** ✓）；
  · `ENTRY_FIRST` = **首次建仓那一笔**的每股成本 ✓（加仓不刷新 ✓）。

本文件守三件事：
  ① **编译/校验**：支持面内的写法能编 ✓；不支持的写法（MA/REF/未知字段…）**明确报错** ✓；
  ② **逐日求值**：`eval_row` 的数值对不对 ✓（含"未持仓 ⇒ 条件不成立" ✓）；
  ③ **回测端到端**：真跑 `run_period_roll(exit_rule=…)` ⇒ 该卖的卖、该顺延的顺延 ✓、
     且"不传规则时**零额外成本**（不维护入场价）" ✓。
"""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from app.factors.exit_rules import UnsupportedExitError, compile_entry_rule, uses_entry
from app.signals.period_roll import run_period_roll

CODES = ["S00", "S01"]
CAL = pd.bdate_range("2024-01-01", periods=9)


def _panel(close_by_code=None):
    """造离线面板：默认 10 元恒定；`close_by_code` 可指定某票逐日收盘（卖出/买入都按它 ✓）。"""
    close = pd.DataFrame(10.0, index=CAL, columns=CODES)
    if close_by_code:
        for c, vals in close_by_code.items():
            for d, v in vals.items():
                close.loc[CAL[d], c] = float(v)
    return {"CALENDAR": pd.DataFrame(index=CAL), "CLOSE": close, "OPEN": close.copy(),
            "CLOSE_RAW": close.copy(), "OPEN_RAW": close.copy(),
            "LIMIT_UP": close * 1.1, "LIMIT_DOWN": close * 0.9, "_limits_inferred": True}


def _signals(pairs) -> pd.DataFrame:
    return pd.DataFrame({"date": pd.to_datetime([CAL[d] for d, _c in pairs]),
                         "code": [c for _d, c in pairs], "side": 1})


# ---------------------------------------------------------------------------
# ① 编译与校验
# ---------------------------------------------------------------------------

def test_uses_entry_detection():
    assert uses_entry("Lt($close, Mul($entry_price,0.95))") is True
    assert uses_entry("Lt($close, Mul($entry_first,0.95))") is True
    assert uses_entry("Gt($close,0)") is False


def test_plain_exit_returns_none():
    """不含入场价 ⇒ 返回 `None` ✓（调用方继续走原来的"预计算矩阵"路径 ✓，数字逐位不变 ✓）。"""
    assert compile_entry_rule("Gt($close,0)") is None


def test_supported_forms_compile():
    """用户那两条写法（翻译后形态 ✓）必须能编 ✓。"""
    r1 = compile_entry_rule("Lt($close,Mul($entry_price,0.95))")
    r2 = compile_entry_rule("Ge($close,Mul($entry_first,1.1))")
    assert r1 is not None and r2 is not None


def test_between_and_logic_compile():
    r = compile_entry_rule("And(Lt($close,$entry_price),Or(Gt($close,1),Not(Gt($open,2))))")
    assert r is not None


@pytest.mark.parametrize("expr,why", [
    ("Lt($close,Mean($entry_price,5))", "带历史窗口算子"),
    ("Lt($close,Ref($entry_price,1))", "带 REF"),
    ("Lt($high,$entry_price)", "目前只允许 C/O"),
    ("Lt($volume,$entry_price)", "没有成交量列"),
])
def test_unsupported_forms_raise_clear_error(expr, why):
    """不支持的写法 ⇒ **编译期就报错** ✓（别等回测跑一半 ✗），且提示"改写成另一条离场公式" ✓。"""
    with pytest.raises(UnsupportedExitError) as ei:
        compile_entry_rule(expr)
    msg = str(ei.value)
    assert "不支持" in msg, why
    assert ("同时勾选" in msg) or ("只允许" in msg), msg


# ---------------------------------------------------------------------------
# ② 逐日求值
# ---------------------------------------------------------------------------

def test_eval_row_basic_and_unheld_nan():
    """未持仓（NaN 入场价）⇒ 条件恒不成立 ✓（不会凭空触发 ✓）。"""
    rule = compile_entry_rule("Lt($close,Mul($entry_price,0.95))")
    rows = {"C": np.array([9.0, 9.0]), "O": np.array([9.0, 9.0])}
    avg = np.array([10.0, np.nan])
    hit = rule.eval_row(rows, avg, np.array([10.0, np.nan]))
    assert hit.tolist() == [True, False]


def test_eval_row_threshold_and_first():
    """阈值默认 0 ✓；`ENTRY_FIRST` 与 `ENTRY_PRICE` 取值互不串 ✓。"""
    rule = compile_entry_rule("Gt($close,$entry_first)")
    rows = {"C": np.array([11.0]), "O": np.array([11.0])}
    assert rule.eval_row(rows, np.array([10.0]), np.array([12.0])).tolist() == [False]
    assert rule.eval_row(rows, np.array([12.0]), np.array([10.0])).tolist() == [True]


# ---------------------------------------------------------------------------
# ③ 回测端到端
# ---------------------------------------------------------------------------

def test_rule_sells_when_price_breaks_avg_cost():
    """★ 真跑：第 0 根买入（价 10）⇒ 第 3 根收盘跌到 9.4 ⇒ 触发 `C < 均价*0.95` ⇒ 卖出 ✓。"""
    rule = compile_entry_rule("Lt($close,Mul($entry_price,0.95))")
    sig = _signals([(0, "S00"), (0, "S01"), (8, "S00"), (8, "S01")])
    # S00 第 2 根起跌到 9.4（< 10*0.95 = 9.5 ✓）；S01 一直 10（不触发 ✓）
    panel = _panel({"S00": {d: 9.4 for d in range(2, 9)}})
    out = run_period_roll(sig, panel, fill="t1_open", cost=0.0, capital=1e7,
                          strict_limit=True, exit_rule=rule)
    tr = out.trades
    hit = tr[(tr["code"] == "S00") & (tr["reason"] == "exit_signal")]
    assert len(hit) == 1, tr
    # T 日信号 ⇒ T+1 执行 ✓（信号在第 2 根算出 ⇒ 第 3 根成交 ✓）
    assert str(hit.iloc[0]["date"])[:10] == str(CAL[3].date()), hit
    st = out.stats["period_roll"]
    assert st["exit_rule"] is True and st["exit_sells"] >= 1, st
    assert st["exit_enabled"] is True
    # 对照：同一份数据不传规则 ⇒ **不该**有离场卖单 ✓（两条曲线才有可比性 ✓）
    base = run_period_roll(sig, panel, fill="t1_open", cost=0.0, capital=1e7, strict_limit=True)
    assert not (base.trades["reason"] == "exit_signal").any(), base.trades
    assert base.stats["period_roll"]["exit_rule"] is False


def test_rule_reports_diag_when_eval_fails():
    """规则求值炸了 ⇒ **关掉离场、回测照跑完** ✓（附加信息绝不拖垮回测 ✗）。"""
    class _Bad:
        def eval_row(self, rows, avg, first):
            raise RuntimeError("boom")

    sig = _signals([(0, "S00"), (0, "S01"), (8, "S00"), (8, "S01")])
    out = run_period_roll(sig, _panel(), fill="t1_open", cost=0.0, capital=1e7,
                          strict_limit=True, exit_rule=_Bad())
    st = out.stats["period_roll"]
    assert st["final_nav"] is not None, "回测必须照常跑完 ✓"
    assert "求值失败" in str(out.diag.get("exit_note") or ""), out.diag
