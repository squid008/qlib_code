# -*- coding: utf-8 -*-
"""**入场价型离场规则**回归（v2.6.0 起，v2.7.0 改成"预计算列 + 骨架" ✓）。

用户口径（2026-10-15 ✓）：
  · `ENTRY_PRICE` = **移动平均成本**（含买入费 ✓；买入会变、卖出不变 ✓）；
  · `ENTRY_FIRST` = **首次建仓那一笔**的每股成本 ✓（加仓不刷新 ✓）；
  · ★ 用户当天还明确要求：**不能只支持 C/O** ✗ —— H/L/V、换手率、市值、MA、递推组…
    都要能跟入场价一起用 ✓（见 `factors/exit_rules.py` 的拆法 ✓）。

本文件守四件事：
  ① **拆分**：与入场价无关的子树 ⇒ 变成**预计算列**（于是任意字段/算子都能用 ✓）；
  ② **校验**：`REF(ENTRY_PRICE,1)` / `MA(ENTRY_PRICE,5)` 这种"对入场价取历史"⇒ **明确报错** ✓；
  ③ **逐日求值**：代入入场价后判定对不对 ✓（含"未持仓 ⇒ 条件不成立" ✓）；
  ④ **回测端到端**：真跑 `run_period_roll(exit_rule=…)` ⇒ 该卖的卖 ✓、对照无边单 ✓、
     规则求值炸了**不拖垮回测** ✓。
"""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from app.factors.exit_rules import (EntryRule, UnsupportedExitError, split_entry_rule,
                                    uses_entry)
from app.signals.period_roll import run_period_roll

CODES = ["S00", "S01"]
CAL = pd.bdate_range("2024-01-01", periods=9)


def _panel(close_by_code=None):
    """离线面板：默认 10 元恒定；`close_by_code` 指定某票逐日收盘 ✓。"""
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
# ① 拆分：能预计算的都当普通列（⇒ 任意字段/算子可用 ✓）
# ---------------------------------------------------------------------------

def test_uses_entry_detection():
    assert uses_entry("Lt($close, Mul($entry_price,0.95))") is True
    assert uses_entry("Lt($close, Mul($entry_first,0.95))") is True
    assert uses_entry("Gt($close,0)") is False
    assert uses_entry("Lt($market_cap,100000000)") is False, "不含入场价 ⇒ 走普通面板路径 ✓"


def test_plain_exit_returns_none():
    """不含入场价 ⇒ `None` ✓（调用方继续走原来的"整列预计算"路径 ✓，数字逐位不变 ✓）。"""
    assert split_entry_rule("Gt($close,0)") is None
    assert split_entry_rule("Lt($close,Mean($close,5))") is None


def test_ma_becomes_pre_column():
    """★ 用户点名的能力：**带历史窗口的算子**（MA）与入场价**混用** ✓ —— MA 走面板 ✓。"""
    r = split_entry_rule("Lt(Mean($close,5),Mul($entry_price,0.95))")
    assert r is not None
    assert r.pre_exprs == ["Mean($close,5)"], r.pre_exprs
    r.bind([np.array([[10.0], [9.0]], dtype=np.float64)])
    assert r.eval_row(0, np.array([10.0]), np.array([10.0])).tolist() == [False]   # 10 > 9.5
    assert r.eval_row(1, np.array([10.0]), np.array([10.0])).tolist() == [True]    # 9 < 9.5


def test_non_price_fields_become_pre_column():
    """★ 用户点名的能力：**市值 / 换手率 / 成交量** 等字段都能与入场价一起用 ✓。"""
    for expr, field in (("Lt($market_cap,Mul($entry_price,10))", "$market_cap"),
                        ("Gt($turnoverrate,Mul($entry_price,0))", "$turnoverrate"),
                        ("Gt($volume,Mul($entry_price,0))", "$volume"),
                        ("Gt($high,Mul($entry_price,1))", "$high"),
                        ("Lt($low,Mul($entry_price,1))", "$low")):
        r = split_entry_rule(expr)
        assert r is not None and r.pre_exprs == [field], (expr, r and r.pre_exprs)


def test_multiple_subtrees_and_dedup():
    """与入场价无关的子树各成一列 ✓（**尽量整块预计算** ✓）；**同一个子树只算一次** ✓。

    ⚠ 注意粒度：`Gt(Div($close,$factor),0)` 这种**整块与入场价无关**的子树 ⇒ 会**整块**预计算 ✓
      （比"只预计算 Div 再逐日比较"更省 ✓）；带入场价的那一支才留在骨架里 ✓。
    """
    r = split_entry_rule("And(Lt(Div($close,$factor),Mul($entry_price,0.95)),"
                         "Gt(Div($close,$factor),0))")
    assert r.pre_exprs == ["Div($close,$factor)", "Gt(Div($close,$factor),0)"], r.pre_exprs
    r2 = split_entry_rule("And(Lt($close,Mul($entry_price,0.95)),Gt($turnoverrate,2))")
    assert r2.pre_exprs == ["$close", "Gt($turnoverrate,2)"], r2.pre_exprs
    # 真去重：同一个"与入场价无关"的子树在树里出现两次 ⇒ 只占一列 ✓
    # （`Gt($close,0)` 既是外层 AND 的一支、又是里层 Gt 的第一参 ⇒ 必须复用同一列 ✓）
    r3 = split_entry_rule("And(And(Gt($close,0),Lt($close,$entry_price)),"
                          "Gt($turnoverrate,Gt($close,0)))")
    assert r3.pre_exprs.count("Gt($close,0)") == 1, r3.pre_exprs
    assert len(r3.pre_exprs) == len(set(r3.pre_exprs)), r3.pre_exprs


def test_supported_forms_compile():
    for expr in ("Lt($close,Mul($entry_price,0.95))",
                 "Ge($close,Mul($entry_first,1.1))",
                 "And(Lt($close,$entry_price),Or(Gt($close,1),Not(Gt($open,2))))",
                 "Between($close,Mul($entry_price,0.9),Mul($entry_price,1.1))",
                 "If(Gt($close,Mul($entry_price,1.1)),1,0)"):
        assert split_entry_rule(expr) is not None, expr


# ---------------------------------------------------------------------------
# ② 校验：对"入场价本身"取历史 ⇒ 明确报错
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("expr", [
    "Lt($close,Ref($entry_price,1))",          # 上一根的入场价
    "Lt($close,Mean($entry_price,5))",         # 入场价的均线
    "Lt($close,Sum($entry_first,3))",
])
def test_history_on_entry_price_is_rejected(expr):
    with pytest.raises(UnsupportedExitError) as ei:
        split_entry_rule(expr)
    msg = str(ei.value)
    assert "入场价" in msg and "不支持" in msg, msg


def test_unsupported_message_mentions_workaround():
    """报错要能自助：告诉用户"与入场价无关的部分随便写" ✓。"""
    with pytest.raises(UnsupportedExitError) as ei:
        split_entry_rule("Lt($close,Mean($entry_price,5))")
    assert "与入场价无关" in str(ei.value)


def test_bind_count_mismatch_raises():
    r = split_entry_rule("Lt($close,Mul($entry_price,0.95))")
    with pytest.raises(UnsupportedExitError):
        r.bind([])                                   # 列数不符 ⇒ 报错（不静默 ✓）


# ---------------------------------------------------------------------------
# ③ 逐日求值
# ---------------------------------------------------------------------------

def test_eval_row_unheld_is_false():
    """未持仓（NaN 入场价）⇒ 条件恒不成立 ✓（不会凭空触发 ✓）。"""
    r = split_entry_rule("Lt($close,Mul($entry_price,0.95))")
    r.bind([np.array([[9.0], [9.0]], dtype=np.float64)])
    hit = r.eval_row(0, np.array([10.0, np.nan]), np.array([10.0, np.nan]))
    assert hit.tolist() == [True, False]


def test_eval_row_threshold_and_first():
    """阈值默认 0 ✓；`ENTRY_FIRST` 与 `ENTRY_PRICE` 取值互不串 ✓。"""
    r = split_entry_rule("Gt($close,$entry_first)")
    r.bind([np.array([[11.0]], dtype=np.float64)])
    assert r.eval_row(0, np.array([10.0]), np.array([12.0])).tolist() == [False]
    assert r.eval_row(0, np.array([12.0]), np.array([10.0])).tolist() == [True]


# ---------------------------------------------------------------------------
# ④ 回测端到端
# ---------------------------------------------------------------------------

def _close_rule(panel):
    """构造"跌破均价 0.95 倍就离场"的规则 ✓（它的预计算列就是 CLOSE ✓）。"""
    r = split_entry_rule("Lt($close,Mul($entry_price,0.95))")
    close = panel["CLOSE"]
    r.bind([close.to_numpy(dtype=np.float64)])
    return r


def test_rule_sells_when_price_breaks_avg_cost():
    """★ 真跑：第 0 根买入（10 元）⇒ 第 2 根起收 9.4（< 9.5）⇒ 触发 ⇒ 次日卖出 ✓。"""
    panel = _panel({"S00": {d: 9.4 for d in range(2, 9)}})
    sig = _signals([(0, "S00"), (0, "S01"), (8, "S00"), (8, "S01")])
    out = run_period_roll(sig, panel, fill="t1_open", cost=0.0, capital=1e7,
                          strict_limit=True, exit_rule=_close_rule(panel))
    tr = out.trades
    hit = tr[(tr["code"] == "S00") & (tr["reason"] == "exit_signal")]
    assert len(hit) == 1, tr
    assert str(hit.iloc[0]["date"])[:10] == str(CAL[3].date()), "T 日信号 ⇒ T+1 成交 ✓\n%s" % hit
    st = out.stats["period_roll"]
    assert st["exit_rule"] is True and st["exit_sells"] >= 1 and st["exit_enabled"] is True, st
    # 对照：同一份数据不传规则 ⇒ **不该**有离场卖单 ✓
    base = run_period_roll(sig, panel, fill="t1_open", cost=0.0, capital=1e7, strict_limit=True)
    assert not (base.trades["reason"] == "exit_signal").any(), base.trades
    assert base.stats["period_roll"]["exit_rule"] is False


def test_unbound_pre_mats_reports_diag_not_crash():
    """规则没绑定预计算列（内部漏绑）⇒ 关掉离场、回测照跑完 ✓（附加信息不拖垮回测 ✗）。"""
    r = split_entry_rule("Lt($close,Mul($entry_price,0.95))")
    sig = _signals([(0, "S00"), (0, "S01"), (8, "S00"), (8, "S01")])
    out = run_period_roll(sig, _panel(), fill="t1_open", cost=0.0, capital=1e7,
                          strict_limit=True, exit_rule=r)
    assert out.stats["period_roll"]["final_nav"] is not None, "回测必须照常跑完 ✓"
    assert "求值失败" in str(out.diag.get("exit_note") or ""), out.diag


def test_rule_is_optional_zero_cost_path():
    """不传规则 ⇒ 入场价一个数都不维护 ✓（用户要的性能口径 ✓）。"""
    sig = _signals([(0, "S00"), (0, "S01"), (8, "S00"), (8, "S01")])
    out = run_period_roll(sig, _panel(), fill="t1_open", cost=0.0, capital=1e7,
                          strict_limit=True)
    assert out.diag.get("exit_rule") is False and out.diag.get("exit_enabled") is False


def test_scalar_skeleton_broadcasts_to_code_axis():
    """纯常量骨架（既无预计算列也无入场价）⇒ 结果要**广播成按标的的向量** ✓
    （否则引擎那边 `_sig[c]` 会炸 ✗ —— 2026-10-15 单测抓到 ✓）。"""
    r = EntryRule(("Gt", [("c", 1.0), ("c", 0.0)]), [], 0.0, [])
    got = r.eval_row(0, np.array([1.0, 2.0, 3.0]), np.array([1.0, 1.0, 1.0]))
    assert got.shape == (3,) and got.tolist() == [True, True, True]
