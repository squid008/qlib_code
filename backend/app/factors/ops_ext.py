# -*- coding: utf-8 -*-
"""自定义 qlib 算子（外挂）：BARSLAST/BARSCOUNT/BARSSINCEN + DYN_* 动态窗口。

通过 qlib 的 Operators.register() 注册，使公式编译生成的表达式字符串
（如 BARSCOUNT($close) / BARSSINCEN(Gt($high,10),10) / DYN_MIN($close,BARSLAST(...))）
能被 qlib 解析执行。

- BARSLAST(X)：上一次 X 不为 0 到现在的天数（最近一次满足，不限窗口）
- BARSCOUNT(X)：上市以来交易日数（数据范围内有效值累计）
- BARSSINCEN(X, N)：N 周期内第一次 X 不为 0 到当前的周期数
- DYN_MIN/DYN_MAX/DYN_COUNT/DYN_REF/DYN_SUM：动态窗口版本，
  窗口大小 N 是序列（每个位置用该位置的 N 值），用于 LLV/HHV/COUNT/REF/SUM 的变量周期写法。
  DYN_MIN/DYN_MAX 用稀疏表 RMQ（O(1) 查询），DYN_COUNT/DYN_SUM 用前缀和（O(1) 查询），
  整体 O(n log n)，避免对每个位置循环窗口导致回测缓慢。

注册机制：除了 Operators.register，还 patch 了 qlib 的 register_all_ops，
保证 qlib.init() 内部 reset Operators 后总是重新注册自定义算子
（否则 qlib.init 会把它们清掉导致 "operator is not registered"）。
"""
from __future__ import annotations

import numpy as np
import pandas as pd
from qlib.data.base import Expression, ExpressionOps
from qlib.data.ops import Operators

__all__ = [
    "BARSLAST", "BARSCOUNT", "BARSSINCEN",
    "DYN_MIN", "DYN_MAX", "DYN_COUNT", "DYN_REF", "DYN_SUM",
    "ensure_ops_registered",
]


class BARSCOUNT(ExpressionOps):
    """上市以来交易日数。

    BARSCOUNT(CLOSE) 表示从上市第一根 K 线到当前的交易日数
    （按数据范围内有效值累计，停牌日 NaN 不计）。
    """

    def __init__(self, feature):
        self.feature = feature
        super().__init__()

    def _load_internal(self, instrument, start_index, end_index, *args):
        series = self.feature.load(instrument, start_index, end_index, *args)
        return series.notna().cumsum()

    def __str__(self):
        # 同 BARSLAST：必须带子表达式，否则所有 BARSCOUNT(X) 共享进程内缓存
        return "BARSCOUNT({})".format(self.feature)

    def get_longest_back_rolling(self):
        return self.feature.get_longest_back_rolling()

    def get_extended_window_size(self):
        return self.feature.get_extended_window_size()


class BARSLAST(ExpressionOps):
    """上一次 X 不为 0 到现在的天数（最近一次满足，不限窗口）。

    BARSLAST(CLOSE/REF(CLOSE,1)>=1.1) 表示上一个涨停板到当前的周期数；
    从数据起点至今从未满足则返回 0。
    """

    def __init__(self, feature):
        self.feature = feature
        super().__init__()

    def __str__(self):
        # 关键：qlib 的 Expression.load 用 str(self) 作为进程内缓存 key
        # （qlib/data/base.py Expression.load）。ExpressionOps 基类没有 __str__，
        # str 会退化成类名 "BARSLAST"，导致同一窗口内所有 BARSLAST(X) 共享缓存、
        # 后算的直接返回先算的结果（不同条件的 BARSLAST 互相污染）。必须带上子表达式。
        return "BARSLAST({})".format(self.feature)

    def _load_internal(self, instrument, start_index, end_index, *args):
        series = self.feature.load(instrument, start_index, end_index, *args)
        vals = series.to_numpy(dtype=float)
        n = len(vals)
        out = np.zeros(n, dtype=float)
        last = -1
        with np.errstate(invalid="ignore"):
            for i in range(n):
                v = vals[i]
                if v != 0 and not np.isnan(v):
                    last = i
                if last >= 0:
                    out[i] = i - last
        return pd.Series(out, index=series.index)

    def get_longest_back_rolling(self):
        return self.feature.get_longest_back_rolling()

    def get_extended_window_size(self):
        return self.feature.get_extended_window_size()


class BARSSINCEN(ExpressionOps):
    """N 周期内第一次 X 不为 0 到当前的周期数。

    BARSSINCEN(HIGH>10, 10)：10 个周期内股价首次超过 10 元到当前的周期数；
    N 周期内从未满足则返回 0。
    """

    def __init__(self, feature, N):
        self.feature = feature
        self.N = int(N)
        super().__init__()

    def __str__(self):
        # 同 BARSLAST：必须带子表达式与窗口参数，否则进程内缓存互相污染
        return "BARSSINCEN({},{})".format(self.feature, self.N)

    def _load_internal(self, instrument, start_index, end_index, *args):
        series = self.feature.load(instrument, start_index, end_index, *args)
        vals = series.to_numpy(dtype=float)
        N = self.N
        n = len(vals)
        out = np.zeros(n, dtype=float)
        with np.errstate(invalid="ignore"):
            for i in range(n):
                lo = max(0, i - N + 1)
                win = vals[lo : i + 1]
                mask = (win != 0) & ~np.isnan(win)
                idx = np.flatnonzero(mask)
                if len(idx):
                    out[i] = (i - lo) - int(idx[0])  # 最早满足距当前的天数
                # 窗口内无满足 → 0
        return pd.Series(out, index=series.index)

    def get_longest_back_rolling(self):
        return self.feature.get_longest_back_rolling() + self.N - 1

    def get_extended_window_size(self):
        lft, rght = self.feature.get_extended_window_size()
        return lft + self.N - 1, rght


# ---------------- 动态窗口算子（O(n log n) / O(n)） ----------------

def _build_sparse(vals: np.ndarray, func) -> list:
    """构建稀疏表（RMQ 预处理），func 是 np.fmin / np.fmax（忽略 NaN）。"""
    n = len(vals)
    if n == 0:
        return []
    k = int(np.log2(n)) + 1
    st = [np.asarray(vals, dtype=float).copy()]
    for j in range(1, k):
        prev = st[-1]
        half = 1 << (j - 1)
        cur = np.empty(n, dtype=float)
        cur[: n - half] = func(prev[: n - half], prev[half:])
        cur[n - half :] = prev[n - half :]
        st.append(cur)
    return st


def _rmq(st: list, l: int, r: int, func) -> float:
    """稀疏表区间最值查询（闭区间 [l, r]）。"""
    length = r - l + 1
    j = int(np.log2(length))
    return float(func(st[j][l], st[j][r - (1 << j) + 1]))


class _DynWindowOp(ExpressionOps):
    """动态窗口算子基类：窗口大小 N 是序列（每个位置用该位置的 N 值）。

    用于 LLV/HHV/COUNT/REF/SUM 的变量周期写法，如 LLV(CLOSE, 触发后周期数)。
    需扩展全量历史（窗口可能很大），get_extended_window_size 返回 inf。
    """

    def __init__(self, feature, N_expr):
        self.feature = feature
        self.N_expr = N_expr
        super().__init__()

    def __str__(self):
        # 关键：同 BARSLAST。qlib Expression.load 的进程内缓存 key 是 str(self)，
        # ExpressionOps 基类没有 __str__，str 会退化成类名（如 "DYN_MIN"），
        # 导致同窗口内所有 DYN_*(X,Y) 共享缓存互相污染。必须带上子表达式。
        return "{}({},{})".format(type(self).__name__, self.feature, self.N_expr)

    def _load_both(self, instrument, start_index, end_index, *args):
        series = self.feature.load(instrument, start_index, end_index, *args)
        ns = self.N_expr.load(instrument, start_index, end_index, *args)
        return series.to_numpy(dtype=float), ns.to_numpy(dtype=float), series.index

    @staticmethod
    def _win_len(v: float) -> int:
        if v is None or (isinstance(v, float) and np.isnan(v)):
            return 1
        n = int(v)
        return n if n >= 1 else 1

    def get_longest_back_rolling(self):
        return np.inf

    def get_extended_window_size(self):
        return np.inf, 0


class DYN_MIN(_DynWindowOp):
    """动态窗口最小值（LLV(X, 变量)）：稀疏表 RMQ，O(1) 查询。"""

    def _load_internal(self, instrument, start_index, end_index, *args):
        vals, nvals, idx = self._load_both(instrument, start_index, end_index, *args)
        n = len(vals)
        st = _build_sparse(vals, np.fmin)
        out = np.full(n, np.nan, dtype=float)
        for i in range(n):
            N = self._win_len(nvals[i])
            out[i] = _rmq(st, max(0, i - N + 1), i, np.fmin)
        return pd.Series(out, index=idx)


class DYN_MAX(_DynWindowOp):
    """动态窗口最大值（HHV(X, 变量)）：稀疏表 RMQ，O(1) 查询。"""

    def _load_internal(self, instrument, start_index, end_index, *args):
        vals, nvals, idx = self._load_both(instrument, start_index, end_index, *args)
        n = len(vals)
        st = _build_sparse(vals, np.fmax)
        out = np.full(n, np.nan, dtype=float)
        for i in range(n):
            N = self._win_len(nvals[i])
            out[i] = _rmq(st, max(0, i - N + 1), i, np.fmax)
        return pd.Series(out, index=idx)


class DYN_COUNT(_DynWindowOp):
    """动态窗口计数（COUNT(条件, 变量)）：前缀和，O(1) 查询。"""

    def _load_internal(self, instrument, start_index, end_index, *args):
        vals, nvals, idx = self._load_both(instrument, start_index, end_index, *args)
        n = len(vals)
        mask = (vals != 0) & ~np.isnan(vals)
        pre = np.concatenate([[0.0], np.cumsum(mask.astype(float))])
        out = np.zeros(n, dtype=float)
        for i in range(n):
            N = self._win_len(nvals[i])
            lo = max(0, i - N + 1)
            out[i] = pre[i + 1] - pre[lo]
        return pd.Series(out, index=idx)


class DYN_REF(_DynWindowOp):
    """动态前移（REF(X, 变量)）：取 N 天前的值，O(1)。"""

    def _load_internal(self, instrument, start_index, end_index, *args):
        vals, nvals, idx = self._load_both(instrument, start_index, end_index, *args)
        n = len(vals)
        out = np.full(n, np.nan, dtype=float)
        for i in range(n):
            nv = nvals[i]
            N = int(nv) if not np.isnan(nv) else 0
            j = i - N
            if 0 <= j < n:
                out[i] = vals[j]
        return pd.Series(out, index=idx)


class DYN_SUM(_DynWindowOp):
    """动态窗口求和（SUM(X, 变量)）：前缀和，O(1) 查询。"""

    def _load_internal(self, instrument, start_index, end_index, *args):
        vals, nvals, idx = self._load_both(instrument, start_index, end_index, *args)
        n = len(vals)
        v = np.nan_to_num(vals, nan=0.0)
        pre = np.concatenate([[0.0], np.cumsum(v)])
        out = np.full(n, np.nan, dtype=float)
        for i in range(n):
            N = self._win_len(nvals[i])
            lo = max(0, i - N + 1)
            out[i] = pre[i + 1] - pre[lo]
        return pd.Series(out, index=idx)


# ---------------- 注册机制 ----------------

_ALL_OPS = [
    BARSLAST, BARSCOUNT, BARSSINCEN,
    DYN_MIN, DYN_MAX, DYN_COUNT, DYN_REF, DYN_SUM,
]

_registered = False


def ensure_ops_registered(force: bool = False) -> None:
    """把自定义算子注册进 qlib。

    force=True 时无条件重新注册（用于 qlib.init() reset 之后）。
    """
    global _registered
    if _registered and not force:
        return
    Operators.register(_ALL_OPS)
    _registered = True


# patch qlib 的 register_all_ops：qlib.init() 内部会 reset Operators 再注册内置算子，
# 如果不 patch，我们注册的 DYN_* 会在 reset 时被清掉 → "operator is not registered"。
import qlib.data.ops as _qlib_ops  # noqa: E402

_ORIG_REGISTER_ALL_OPS = _qlib_ops.register_all_ops


def _patched_register_all_ops(C):
    _ORIG_REGISTER_ALL_OPS(C)
    Operators.register(_ALL_OPS)


_qlib_ops.register_all_ops = _patched_register_all_ops

# 模块导入时也注册一次（覆盖未走 qlib.init 的路径）
ensure_ops_registered()
