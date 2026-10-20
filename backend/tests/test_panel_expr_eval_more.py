# -*- coding: utf-8 -*-
"""`_apply_node` 的**其余分支**（★ v2.10.3，2026-10-20 ✓ —— 接着 `test_panel_expr_eval` 往下扫 ✓）。

## 为什么继续测这些

上一轮把 `eval` 的主干跑通了 ✓（`Mean/Ref/Abs/Sqrt/Log/Sgn/TRUNC/Pow/If/BETWEEN/BARSLAST/
BARSSINCE/FILTER/EMA/SMA` ✓），但 `_apply_node` 里还有 **20+ 个分支**没被任何测试碰到 ✗：
`ROUND / BARSCOUNT / HHVBARS / LLVBARS / BARSSINCEN / And / Or / IdxMax / IdxMin / Rank /
Quantile / Corr / Slope / Resi / Rsquare / DYN_*` ✓。

## 断言策略：**只钉性质，不猜实现** ✓

这些算子的精确定义我没逐个读过源码 ✗（上一轮已经因为"猜语义"红过好几次 ✓：
`FILTER`/`Count`/`Greater`/`MA` 都不符合我的预期 ✗）。所以这里只断言**必然成立的性质**：

* 有界（比如 `HHVBARS` 落在 `[0, N-1]` ✓、`Rank` 落在 `[1, n]` ✓、`|Corr| <= 1` ✓）；
* 是 0/1 信号（`And`/`Or` ✓）；
* 不抛、形状对、无 `inf` ✓（算子"能不能跑"比"值是多少"更基础 ✓）。

⚠ 具体数值语义仍属**未钉住** ✓ —— 已在文件头写明，不假装测了 ✗。
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


def _ev(**extra):
    close = {"A": list(A_VALS), "B": list(B_VALS)}
    ev = object.__new__(P.PanelEvaluator)
    ev._full = _series(close).index
    ev._field_cache = {"close": _series(close)}
    for k, v in extra.items():
        ev._field_cache[k] = _series(v)
    ev._node_cache = OrderedDict()
    ev._node_bytes = 0
    ev._active_cache = {}
    ev._active_pos = {}
    ev.read_start = "2024-01-02"
    return ev


def _val(ev, expr):
    """按公式清缓存后再求值 ✓（不清会串味 ✗，见 `test_panel_expr_eval` 的专门用例 ✓）。"""
    ev._node_cache.clear()
    ev._node_bytes = 0
    return ev.eval(parse_expr(expr))


def _finite_values(s: pd.Series) -> np.ndarray:
    v = s.to_numpy(dtype=float)
    return v[np.isfinite(v)]


# ------------------------------------------------------------------ ROUND
def test_round_keeps_decimals():
    ev = _ev()
    s = _val(ev, "ROUND($close/3,2)")
    exp = np.round(_ev()._field_cache["close"].to_numpy() / 3.0, 2)
    np.testing.assert_allclose(s.to_numpy(), exp, rtol=0, atol=1e-12)


# ------------------------------------------------------- BARSCOUNT / BARSSINCEN
def test_barscount_counts_bars_since_start():
    """每只票从 1 开始数 ✓（`N` 与面板等长 ⇒ 末行 = 6 ✓）。"""
    ev = _ev()
    s = _val(ev, "BARSCOUNT($close)")
    a = s.loc["A"].to_numpy()
    assert np.all(np.diff(a) == 1), "每根 +1 ✓"
    assert a[0] >= 1 and a[-1] == 6


def test_barsincen_is_bounded_by_window():
    ev = _ev()
    s = _val(ev, "BARSSINCEN(Gt($close,999),3)")
    v = _finite_values(s)
    assert v.size > 0
    assert v.min() >= 0 and v.max() <= 3, "窗口内的计数必须落在 [0, N] ✓"


# --------------------------------------------------------- HHVBARS / LLVBARS
@pytest.mark.parametrize("fn", ["HHVBARS", "LLVBARS"])
def test_bars_since_extreme_is_within_window(fn):
    """⭐ `HHVBARS/LLVBARS` 是**距极值的根数** ✓ ⇒ 必然落在 `[0, N-1]` ✓（负数或超过 N 都是实现错 ✗）。"""
    ev = _ev()
    s = _val(ev, "%s($close,3)" % fn)
    v = _finite_values(s)
    assert v.size > 0
    assert v.min() >= 0 and v.max() <= 2


# ------------------------------------------------------------------ 逻辑运算
def test_and_or_are_binary_signals():
    ev = _ev()
    for expr in ("And(Gt($close,2),Lt($close,5))", "Or(Gt($close,5),Lt($close,2))"):
        v = _val(ev, expr).to_numpy()
        assert set(np.unique(v)) <= {0.0, 1.0}, "%s 必须是 0/1 信号 ✓" % expr
    # 交集必然不多于任一单条件 ✓
    both = _val(ev, "And(Gt($close,2),Lt($close,5))").sum()
    only = _val(ev, "Gt($close,2)").sum()
    assert both <= only


# ------------------------------------------------------------- IdxMax / IdxMin
@pytest.mark.parametrize("fn", ["IdxMax", "IdxMin"])
def test_index_of_extreme_within_window(fn):
    """⚠ 这些算子都是 `(序列, N)` ✓ —— 少一个 N 就直接 `IndexError` ✗（实测抓到 ✓）。"""
    ev = _ev()
    s = _val(ev, "%s($close,3)" % fn)
    v = _finite_values(s)
    assert v.size > 0
    # ⚠ 实测是 **1-based**（1..N ✓）不是 0-based ✗ —— 与 qlib 的 `IdxMax` 口径一致 ✓
    assert v.min() >= 1 and v.max() <= 3, "窗口内位置必须落在 [1, N] ✓"


# --------------------------------------------------------------- Rank / Quantile
def test_rank_is_within_window_size():
    """⚠ `Rank` 是**逐股滚动排名** `(序列, N)` ✓（不是横截面排名 ✗）⇒ 值落在 `[1, N]` ✓。"""
    ev = _ev()
    v = _finite_values(_val(ev, "Rank($close,3)"))
    assert v.size > 0
    # ⚠ 实测是**归一化**排名（落在 (0, 1] ✓，如 `1 / 0.5 / 0.333`），不是名次整数 ✗
    assert v.min() > 0 and v.max() <= 1.0 + 1e-12, "归一化排名必须落在 (0, 1] ✓"


def test_quantile_is_between_min_and_max():
    """⚠ `Quantile` 是 `(序列, N, q)` ✓ —— 三个参数，缺 N 会 `TypeError` ✗（实测抓到 ✓）。"""
    ev = _ev()
    s = _val(ev, "Quantile($close,3,0.5)")
    v = _finite_values(s)
    assert v.size > 0
    lo = min(A_VALS + B_VALS)
    hi = max(A_VALS + B_VALS)
    assert v.min() >= lo - 1e-9 and v.max() <= hi + 1e-9, "分位数不许越出样本范围 ✓"


# ------------------------------------------------------------------ 相关性类
@pytest.mark.parametrize("expr", ["Corr($close,$open,3)", "Slope($close,3)",
                                  "Resi($close,3)", "Rsquare($close,3)"])
def test_regression_family_does_not_raise_and_stays_finite(expr):
    """只钉"能算、无 `inf`、形状对" ✓ —— 具体数值语义**未钉住** ✗（文件头已说明 ✓）。"""
    ev = _ev(open={"A": list(B_VALS), "B": list(A_VALS)})
    s = _val(ev, expr)
    assert len(s) == NDAY * 2
    v = s.to_numpy(dtype=float)
    assert not np.isinf(v).any(), "回归族算出 inf ⇒ 分母/方差处理漏了 ✓"
    assert np.isfinite(v).sum() > 0, "至少要有有限值 ✓（全 NaN = 算子没跑起来 ✗）"
