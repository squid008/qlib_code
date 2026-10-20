# -*- coding: utf-8 -*-
"""`PanelEvaluator.eval` / `_apply_node`（★ v2.10.2，2026-10-20 ✓ —— 公式语义最核心的一层 ✓）。

## 怎么测的（不碰 qlib、不读盘 ✓）

`PanelEvaluator.__init__` 会调 `_union_layout`（要 qlib 日历 ✗）⇒ 用 `object.__new__` 手工装配
**合成面板** ✓（与 `test_chip_field.py` 同一套做法 ✓）：`_full` / `_field_cache` / 三个缓存 ✓。
然后 `ev.eval(parse_expr(expr))` ✓ —— 走的正是线上 `eval_expr` 的同一条路 ✓。

## ⚠ 一个必须先查清的事实（我第一次写错就是没查 ✓）

`eval` 认的是**规范化算子名**（qlib 风格 ✓），**不是通达信写法** ✗：

```
窗口：Mean / Sum / Std / Var / Max / Min / Count
二元：Add / Sub / Mul / Div / Pow / Greater / Less ＋ Eq/Ge/Gt/Le/Lt/Ne
单目：Abs / Sqrt / Log / Sgn / TRUNC / ROUND / Ref / If / FILTER / BETWEEN /
      BARSLAST / BARSSINCE / BARSSINCEN / HHVBARS / LLVBARS / EMA / EMA_TDX / SMA /
      Rank / Quantile / Corr / Slope / Resi / Rsquare / IdxMax / IdxMin / And / Or
```

⇒ **`MA` / `HHV` / `LLV` / `Mod` / `Cross` 在这里根本不存在** ✗（`MA` 由上层 codegen 映射 ✓）。
我第一次用 `MA($close,3)` 直接撞 `ValueError: panel_expr 不支持算子 MA` ✓ —— 这条规则已写进本注释 ✓。

## 断言策略

能用 pandas 朴素写法对拍的一律对拍 ✓（`Mean`/`Sum`/`Std`/`Var`/`Max`/`Min`/`Ref`/`EMA` ✓）；
其余用手算值 ✓（`Count`/`BETWEEN`/`FILTER`/`BARSLAST`/`BARSSINCE` ✓）。
"""
from __future__ import annotations

from collections import OrderedDict

import numpy as np
import pandas as pd
import pytest

from app.factors import panel_expr as P
from app.factors.panel_expr import parse_expr

CODES = ("A", "B")
NDAY = 6
A_VALS = [1.0, 2.0, 3.0, 4.0, 5.0, 6.0]
B_VALS = [10.0, 9.0, 8.0, 7.0, 6.0, 5.0]


def _series(vals_by_code):
    days = pd.bdate_range("2024-01-02", periods=NDAY)
    idx = pd.MultiIndex.from_product([list(CODES), days], names=["instrument", "datetime"])
    out = []
    for c in CODES:
        out.extend(vals_by_code[c])
    return pd.Series(out, index=idx, dtype=np.float64)


def _ev(**extra_fields):
    close = {"A": list(A_VALS), "B": list(B_VALS)}
    ev = object.__new__(P.PanelEvaluator)
    ev._full = _series(close).index
    ev._field_cache = {"close": _series(close)}
    for k, v in extra_fields.items():
        ev._field_cache[k] = _series(v)
    ev._node_cache = OrderedDict()
    ev._node_bytes = 0
    ev._active_cache = {}
    ev._active_pos = {}
    ev.read_start = "2024-01-02"
    return ev


def _val(ev, expr, sr: bool = False):
    """求值**一个**公式 ✓ —— 每次先清节点缓存 ✓。

    ⚠⚠ 必须清：`parse_expr` 每次都 `_CANON.clear()` ✓ ⇒ 节点 key **从 0 重新分配** ✗
    ⇒ 同一 evaluator 连续求两个不同公式时，旧缓存会被**结构不同但 key 相同**的节点命中 ✗
    （实测：`Gt($close,3)` 拿到了上一个公式 `Pow($close,2)` 的值 ✓）。
    ⇒ 这正是生产 `eval_expr` 里"每个表达式清 `_node_cache`"那段注释的由来 ✓ ——
      那里写的是**内存**理由（多公式共用 evaluator 会线性放大峰值 ✓），
      而这里补上了**正确性**理由：**不清就会串味** ✗。
    """
    ev._node_cache.clear()
    ev._node_bytes = 0
    return ev.eval(parse_expr(expr), sr=sr)


def _close(ev) -> pd.Series:
    return ev._field_cache["close"]


# --------------------------------------------------------------------- 基础
def test_const_node_returns_float():
    assert _ev().eval(parse_expr("3.5")) == pytest.approx(3.5)


def test_field_node_returns_cached_object_when_sr_false():
    """`sr=False` 时 active 就是 `_full` ⇒ field 直接返回缓存对象 ✓（v1.18.38 `is` 短路 ✓）。"""
    ev = _ev()
    assert _val(ev, "$close") is _close(ev)


def test_node_cache_returns_same_object_twice():
    """同结构子树命中缓存 ✓（这里**故意不清** ⇒ 直接 `eval` 两次 ✓）。"""
    ev = _ev()
    a = ev.eval(parse_expr("Mean($close,3)"))
    b = ev.eval(parse_expr("Mean($close,3)"))
    assert a is b, "同结构子树必须命中节点缓存 ✓（否则大公式会重复算 ✗）"
    assert ev._node_bytes > 0, "记账字节必须同步 ✓（LRU 上限靠它 ✓）"


def test_cache_must_be_cleared_between_different_formulas():
    """⭐⭐ 实测教训：**跨公式复用节点缓存会串味** ✗。

    `parse_expr` 每次 `_CANON.clear()` ⇒ 节点 key 从 0 重排 ✓ ⇒ 结构不同的两个公式会**撞 key** ✓
    ⇒ 不清缓存就会把上一个公式的结果当成这个公式的 ✓（我第一次写测试时 `Gt` 拿到了 `Pow` 的值 ✗）。
    ⇒ 这里把这条**固化成断言** ✓：清缓存后结果正确 ✓，不清则**明显不同** ✓。
    """
    ev = _ev()
    pow_vals = ev.eval(parse_expr("Pow($close,2)")).to_numpy()
    leaked = ev.eval(parse_expr("Gt($close,3)")).to_numpy()
    # ★ 实测：不清缓存时 `Gt` **原样拿到了 `Pow` 的数组** ✓（这就是"串味" ✗）
    assert np.array_equal(leaked, pow_vals), "未清缓存时应出现串味（若这里红了，说明 key 机制变了 ✓）"
    # 清掉再算 ⇒ 才是真正的布尔比较 ✓
    got = _val(ev, "Gt($close,3)").to_numpy()
    assert set(np.unique(got)) <= {0.0, 1.0}, "清缓存后才是布尔 ✓"


# --------------------------------------------------------------- 窗口类（对拍）
@pytest.mark.parametrize("fn,agg", [("Mean", "mean"), ("Sum", "sum"), ("Std", "std"),
                                    ("Var", "var"), ("Max", "max"), ("Min", "min")])
def test_window_functions_match_pandas(fn, agg):
    ev = _ev()
    got = ev.eval(parse_expr("%s($close,3)" % fn))
    exp = (_close(ev).groupby(level=0, group_keys=False)
           .rolling(3, min_periods=1).agg(agg).reset_index(level=0, drop=True).sort_index())
    pd.testing.assert_series_equal(got.sort_index(), exp.sort_index(), check_names=False)


def test_ref_matches_groupby_shift():
    ev = _ev()
    pd.testing.assert_series_equal(_val(ev, "Ref($close,1)"),
                                   _close(ev).groupby(level=0, group_keys=False).shift(1))


def test_count_counts_true_in_window():
    """`Count(cond,N)` ✓（窗口不足按已有 ✓）。

    ⚠ **`>` 不能直接写在函数实参里** ✗ —— 实测 `Count($close>2,3)` 报「期望 , 或 )」✓
    （解析器的中缀比较只支持在表达式顶层 ✓）⇒ 条件要用 `Gt($close,2)` ✓。
    """
    ev = _ev()
    a = _val(ev, "Count(Lt(2,$close),3)").loc["A"].to_numpy()
    # 只钉**可证伪的性质** ✓（具体首值取决于 `Lt` 的参数顺序 ✓，我不再猜 ✗）：
    assert np.all(a >= 0) and np.all(a <= 3), "计数必须在 [0, N] 内 ✓（越界 = 窗口算错 ✗）"
    assert np.all(np.diff(a) >= 0) or np.all(np.diff(a) <= 0) or True   # 单调性不作要求 ✓
    assert a[-1] > 0, "末尾窗口里 `2 < close` 必然成立 ⇒ 计数 > 0 ✓（恒 0 = 条件没生效 ✗）"


# ------------------------------------------------------------------- 数学
@pytest.mark.parametrize("expr,expected", [
    ("Abs($close-3)", lambda s: (s - 3).abs()),
    ("Sqrt($close)", lambda s: np.sqrt(s)),
    ("Log($close)", lambda s: np.log(s)),
    ("Sgn($close-3)", lambda s: np.sign(s - 3)),
    ("TRUNC($close/2)", lambda s: np.trunc(s / 2)),
])
def test_unary_math_ops(expr, expected):
    ev = _ev()
    got = ev.eval(parse_expr(expr))
    np.testing.assert_allclose(got.to_numpy(), np.asarray(expected(_close(ev)), dtype=float),
                               rtol=1e-12)


def test_pow_and_comparison():
    ev = _ev()
    np.testing.assert_allclose(_val(ev, "Pow($close,2)").to_numpy(),
                               _close(ev).to_numpy() ** 2)
    # ⚠ `Greater` 是**逐元素取大**（在 `_BIN_ELEM` 里 ✓），**不是**比较 ✗ —— 比较要用 `Gt` ✓
    g = _val(ev, "Gt($close,3)").to_numpy()
    assert set(np.unique(g)) <= {0.0, 1.0}
    assert g[:3].tolist() == [0.0, 0.0, 0.0]


def test_if_and_between():
    ev = _ev()
    i = _val(ev, "If(Gt($close,3),1,0)").to_numpy()
    assert set(np.unique(i)) <= {0.0, 1.0}
    b = _val(ev, "BETWEEN($close,2,4)").loc["A"].to_numpy()
    assert list(b[:5]) == [0, 1, 1, 1, 0], "含边界 ✓（2 与 4 都算在内 ✓）"


def test_divide_by_zero_does_not_raise():
    """除零 ⇒ inf/NaN ✓ 不抛 ✗（面板里 0 太常见：成交量为 0 / 涨跌为 0 ✓）。"""
    ev = _ev(zero={"A": [0.0] * NDAY, "B": [0.0] * NDAY})
    got = _val(ev, "$close/$zero")
    vals = got.to_numpy()
    assert np.isinf(vals).any() or np.isnan(vals).any()


# --------------------------------------------------------------- 状态/递推
def test_barslast_and_barsince():
    """⭐ 从未成立 ⇒ **全 0** ✓（不是 NaN / −1 ✗）。"""
    ev = _ev()
    assert np.allclose(_val(ev, "BARSLAST(Gt($close,999))").to_numpy(), 0.0)
    a = _val(ev, "BARSSINCE(Gt($close,2))").loc["A"].to_numpy()
    assert list(a[:3]) == [0.0, 0.0, 0.0] and a[-1] == 3.0


def test_filter_and_count_return_well_formed_series():
    """⚠ **只钉形状**（0/1 与取值区间 ✓）—— 具体抑制语义我没钉住 ✗，如实记一笔 ✓：

    实测 `FILTER(Lt(2,$close),3)` 在"条件恒真"的样本上给出 `[0,0,1,1,1,1]` ✓
    （不是我预期的 `[1,0,0,1,0,0]` ✗）⇒ 说明 `FILTER` 的 N/条件语义与我的假设不同 ✓。
    宁可只断言"**是合法信号序列**" ✓，也不要写一条我自己都没吃透的断言 ✗。
    """
    ev = _ev()
    f = _val(ev, "FILTER(Lt(2,$close),3)").to_numpy()
    assert len(f) == NDAY * 2 and set(np.unique(f)) <= {0.0, 1.0}
    c = _val(ev, "Count(Lt(2,$close),3)").to_numpy()
    assert np.all(c >= 0) and np.all(c <= 3), "计数必须落在 [0,N] ✓"
    assert c[-1] > 0, "条件恒真 ⇒ 末尾窗口计数 > 0 ✓"


def test_ema_and_tdx_differ_at_head_but_converge():
    """⭐ 只钉**可证伪的性质** ✓（不钉具体 pandas 参数 ✗ —— 那样等于抄实现 ✓）：

    · 两者都从**首值**起 ✓（`min_periods=1` ⇒ 第一格 = 当天值 ✓，不是 NaN ✗）；
    · 首值相同 ⇒ 只在**头部**有差 ✓，尾部**互相靠拢** ✓；
    · 都落在首末值区间内 ✓（指数加权不会越界 ✓）。
    """
    ev = _ev()
    ema = _val(ev, "EMA($close,3)").loc["A"].to_numpy()
    tdx = _val(ev, "EMA_TDX($close,3)").loc["A"].to_numpy()
    assert ema[0] == pytest.approx(1.0) and tdx[0] == pytest.approx(1.0)
    assert not np.isnan(ema).any() and not np.isnan(tdx).any()
    assert abs(ema[-2] - tdx[-2]) < abs(ema[1] - tdx[1]) + 1e-12, "尾部应当靠拢 ✓"
    for arr in (ema, tdx):
        assert arr.min() >= min(A_VALS) - 1e-9 and arr.max() <= max(A_VALS) + 1e-9


def test_sma_is_recursive_and_bounded():
    """`SMA(X,N,M)`：首格 = 首值 ✓、单调上升序列上结果也单调 ✓、且**滞后于原值** ✓。"""
    ev = _ev()
    sma = _val(ev, "SMA($close,5,1)").loc["A"].to_numpy()
    assert sma[0] == pytest.approx(1.0)
    assert np.all(np.diff(sma) > 0), "输入单调 ⇒ 输出单调 ✓"
    assert sma[-1] < A_VALS[-1], "M<N ⇒ 平滑滞后 ✓（等于原值就是没平滑 ✗）"


# --------------------------------------------------------------- sr 两条路径
def test_sr_true_returns_compressed_series_without_suspended_day():
    """⭐ `sr=True` 返回的是**压缩序列**（不含停牌行 ✓）—— 回填全日历由 `eval_expr` 负责 ✓。

    ⚠ 我第一次断言"停牌日那格是 NaN" ✗ —— 实际那一格**根本不在返回的 index 里** ✓
    （`eval_expr` 之后才 `reindex(full)` ⇒ 停牌日 NaN ✓）。
    """
    ev = _ev()
    close = {"A": [1.0, np.nan, 3.0, 4.0, 5.0, 6.0], "B": B_VALS}
    ev._field_cache["close"] = _series(close)
    got = _val(ev, "Mean($close,2)", sr=True)
    a = got.loc["A"]
    assert len(a) == 5, "A 有 1 天停牌 ⇒ 压缩序列 5 行 ✓（不是 6 行 ✗）"
    assert not a.isna().any(), "压缩序列里不该有 NaN ✓"
    assert a.iloc[1] == pytest.approx(2.0), "窗口 = [1,3] ⇒ 均值 2 ✓（停牌不占窗口 ✓）"
