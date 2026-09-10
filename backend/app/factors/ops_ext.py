# -*- coding: utf-8 -*-
"""自定义 qlib 算子（外挂）：BARSLAST/BARSCOUNT/BARSSINCEN + DYN_* 动态窗口。

通过 qlib 的 Operators.register() 注册，使公式编译生成的表达式字符串
（如 BARSCOUNT($close) / BARSSINCEN(Gt($high,10),10) / DYN_MIN($close,BARSLAST(...))）
能被 qlib 解析执行。

- BARSLAST(X)：上一次 X 不为 0 到现在的天数（最近一次满足，不限窗口）
- BARSCOUNT(X)：上市以来交易日数（数据范围内有效值累计）
- BARSSINCEN(X, N)：N 周期内第一次 X 不为 0 到当前的周期数
- SR(X)：益盟"删行"语义包装——把 X 序列中的 NaN 行（停牌/无值日）剔除后返回，
  使后续 Ref/窗口/算术按"有效交易日连续序列"计算（复牌首日 Ref=停牌前收盘，
  窗口不把停牌日计入），等价于益盟/聚宽"行情无停牌行"的数据语义。
  用法：SR($close)、SR($mf_pct_main)。
- DYN_MIN/DYN_MAX/DYN_COUNT/DYN_REF/DYN_SUM：动态窗口版本，
  窗口大小 N 是序列（每个位置用该位置的 N 值），用于 LLV/HHV/COUNT/REF/SUM 的变量周期写法。
  DYN_MIN/DYN_MAX 用稀疏表 RMQ（O(1) 查询），DYN_COUNT/DYN_SUM 用前缀和（O(1) 查询），
  整体 O(n log n)，避免对每个位置循环窗口导致回测缓慢。
- EMA_TDX(X, N)：通达信/聚宽递归语义的 EMA（ewm(alpha=2/(N+1), adjust=False)），
  对齐通达信公式系统的递归式 EMA；公式翻译层（parser/codegen.py）已把公式里的 EMA
  指到本算子（qlib 内建 EMA 是 pandas ewm adjust=True 归一化口径，序列开头初值不同）。

注册机制：除了 Operators.register，还 patch 了 qlib 的 register_all_ops，
保证 qlib.init() 内部 reset Operators 后总是重新注册自定义算子
（否则 qlib.init 会把它们清掉导致 "operator is not registered"）。
"""
from __future__ import annotations

import numpy as np
import pandas as pd
from qlib.data.base import Expression, ExpressionOps
from qlib.data.ops import Operators
from qlib.data.ops import EMA as _QLIB_EMA

__all__ = [
    "BARSLAST", "BARSCOUNT", "BARSSINCEN",
    "DYN_MIN", "DYN_MAX", "DYN_COUNT", "DYN_REF", "DYN_SUM",
    "DYN_HHVBARS", "DYN_LLVBARS",
    "And", "Or",
    "SR",
    "EMA_TDX",
    "SGN", "TRUNC", "BETWEEN",
    "ROUND",
    "FILTER", "SMA", "BARSSINCE", "HHVBARS", "LLVBARS",
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
        return pd.Series(barslast_vec(series.to_numpy(dtype=float)), index=series.index)

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
        return pd.Series(barsincen_vec(series.to_numpy(dtype=float), self.N), index=series.index)

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


def _win_lens_vec(nvals: np.ndarray) -> np.ndarray:
    """把动态窗口序列向量化规整为 int 数组（等价逐元素 _win_len）：

    NaN → 1；<1 → 1；其余 int(v)（正数向零截断 = floor）。
    """
    nv = np.where(np.isnan(nvals), 1.0, nvals)
    n = np.floor(nv).astype(np.int64)
    return np.maximum(n, 1)


def _dyn_rmq_vec(vals: np.ndarray, nvals: np.ndarray, func) -> np.ndarray:
    """动态窗口最值的全向量化版本（等价逐行 _rmq 循环）。

    每个位置的窗口为 [i-N_i+1, i]（N_i 由 nvals 给出）。RMQ 层级 j 随窗口长度
    而异，按层级分组后每层一次批量查询（总 O(n·log n) 的向量化实现，消灭
    逐位置 Python 循环）。
    """
    n = len(vals)
    if n == 0:
        return np.zeros(0, dtype=float)
    st = _build_sparse(vals, func)
    Ns = _win_lens_vec(nvals)
    lens = np.minimum(Ns, np.arange(1, n + 1))  # 窗口实际长度（截到数组起点）
    js = np.floor(np.log2(lens)).astype(np.int64)
    idx = np.arange(n)
    l_arr = idx - lens + 1
    out = np.full(n, np.nan, dtype=float)
    for k, layer in enumerate(st):
        sel = js == k
        if not sel.any():
            continue
        span = 1 << k
        lk = l_arr[sel]
        rk = idx[sel]
        out[sel] = func(layer[lk], layer[rk - span + 1])
    return out


def barslast_vec(vals: np.ndarray) -> np.ndarray:
    """BARSLAST 全向量化：距最近一次"非 0 且非 NaN"的周期数（无则 0）。"""
    n = len(vals)
    mask = (vals != 0) & ~np.isnan(vals)
    idx = np.where(mask, np.arange(n), -1)
    last = np.maximum.accumulate(idx)  # 每个位置最近满足的绝对下标（-1=从未满足）
    return np.where(last >= 0, np.arange(n) - last, 0.0)


def dyn_ref_vec(vals: np.ndarray, nvals: np.ndarray) -> np.ndarray:
    """DYN_REF 全向量化：第 i 位取 i - int(N_i)（向零截断）前的值；NaN 窗口→0。"""
    n = len(vals)
    nv = np.where(np.isnan(nvals), 0.0, np.trunc(nvals))
    j = np.arange(n) - nv.astype(np.int64)
    out = np.full(n, np.nan, dtype=float)
    ok = (j >= 0) & (j < n)
    out[ok] = vals[j[ok]]
    return out


def _dyn_window_prefix(vals: np.ndarray, nvals: np.ndarray, weights: np.ndarray) -> np.ndarray:
    """动态窗口前缀和基元：区间和 = pre[i+1] - pre[max(0, i-N_i+1)]。"""
    n = len(vals)
    pre = np.concatenate([[0.0], np.cumsum(weights)])
    Ns = _win_lens_vec(nvals)
    lo = np.maximum(0, np.arange(n) - Ns + 1)
    return pre[np.arange(n) + 1] - pre[lo]


def dyn_window_sum_vec(vals: np.ndarray, nvals: np.ndarray) -> np.ndarray:
    """DYN_SUM 全向量化（NaN 视为 0 参与和）。"""
    return _dyn_window_prefix(vals, nvals, np.nan_to_num(vals, nan=0.0))


def dyn_window_count_vec(vals: np.ndarray, nvals: np.ndarray) -> np.ndarray:
    """DYN_COUNT 全向量化：窗口内非 0 且非 NaN 计数。"""
    w = ((vals != 0) & ~np.isnan(vals)).astype(float)
    return _dyn_window_prefix(vals, nvals, w)


def barsincen_vec(vals: np.ndarray, N: int) -> np.ndarray:
    """BARSSINCEN 全向量化：N 窗内最早满足距当前周期数（无则 0）。"""
    N = max(1, int(N))
    n = len(vals)
    mask = (vals != 0) & ~np.isnan(vals)
    pos = np.flatnonzero(mask)
    out = np.zeros(n, dtype=float)
    if pos.size:
        lo = np.maximum(0, np.arange(n) - N + 1)
        j = np.searchsorted(pos, lo, side="left")
        in_win = j < pos.size
        earliest = np.where(in_win, pos[np.minimum(j, pos.size - 1)], n + 1)
        ok = in_win & (earliest <= np.arange(n))
        out = np.where(ok, np.arange(n) - earliest, 0.0)
    return out


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

    # 逐元素窗口规整语义（NaN→1、<1→1、int 截断）已向量化为 _win_lens_vec；
    # 各 DYN_* 的 _load_internal 直接使用 _win_lens_vec。
    def get_longest_back_rolling(self):
        return np.inf

    def get_extended_window_size(self):
        return np.inf, 0


class DYN_MIN(_DynWindowOp):
    """动态窗口最小值（LLV(X, 变量)）：稀疏表 RMQ，全向量化。"""

    def _load_internal(self, instrument, start_index, end_index, *args):
        vals, nvals, idx = self._load_both(instrument, start_index, end_index, *args)
        out = _dyn_rmq_vec(vals, nvals, np.fmin)
        return pd.Series(out, index=idx)


class DYN_MAX(_DynWindowOp):
    """动态窗口最大值（HHV(X, 变量)）：稀疏表 RMQ，全向量化。"""

    def _load_internal(self, instrument, start_index, end_index, *args):
        vals, nvals, idx = self._load_both(instrument, start_index, end_index, *args)
        out = _dyn_rmq_vec(vals, nvals, np.fmax)
        return pd.Series(out, index=idx)


class DYN_COUNT(_DynWindowOp):
    """动态窗口计数（COUNT(条件, 变量)）：前缀和，全向量化。"""

    def _load_internal(self, instrument, start_index, end_index, *args):
        vals, nvals, idx = self._load_both(instrument, start_index, end_index, *args)
        return pd.Series(dyn_window_count_vec(vals, nvals), index=idx)


class DYN_REF(_DynWindowOp):
    """动态前移（REF(X, 变量)）：取 N 天前的值，全向量化。"""

    def _load_internal(self, instrument, start_index, end_index, *args):
        vals, nvals, idx = self._load_both(instrument, start_index, end_index, *args)
        return pd.Series(dyn_ref_vec(vals, nvals), index=idx)


class DYN_SUM(_DynWindowOp):
    """动态窗口求和（SUM(X, 变量)）：前缀和，全向量化。"""

    def _load_internal(self, instrument, start_index, end_index, *args):
        vals, nvals, idx = self._load_both(instrument, start_index, end_index, *args)
        return pd.Series(dyn_window_sum_vec(vals, nvals), index=idx)


def _dyn_best_idx(ai: np.ndarray, bi: np.ndarray, vals: np.ndarray, is_max: bool) -> np.ndarray:
    """稀疏表层合并：从两个候选"极值下标"中选更优（-1 = 无效/NaN）。

    max：值更大者胜；min：值更小者胜；NaN 视为缺失（无效）；值相等 → 取更大下标
    （等值取最近/最右，与 HHVBARS/LLVBARS 语义一致）。
    """
    a_ok = ai >= 0
    b_ok = bi >= 0
    av = np.where(a_ok, vals[np.maximum(ai, 0)], np.nan)
    bv = np.where(b_ok, vals[np.maximum(bi, 0)], np.nan)
    # [v1.18.5 修复] 原文写的是 `better = (av > bv)`（语义="a 更优"），却直接放进
    # `b_wins` 当作"b 胜"使用 → is_max/is_min 整体反转：DYN_HHVBARS 实际取到窗口
    # 最小值位置、DYN_LLVBARS 取到最大值位置。
    # 引入版本：v1.17.7 (e6a3cb5) 把 `dyn_bars_vec` 从"逐位 while 扫描"重写为稀疏表时
    # 写反；v1.17.3~v1.17.6 的旧实现先 `_dyn_rmq_vec(..., np.fmax if is_max else np.fmin)`
    # 求窗口极值再回找最右位置，语义正确。注意 tie/无效 两个分支原本就是对的，只有
    # "两候选都有效且值不等"这一支反向。
    if is_max:
        b_better = (bv > av) | (np.isnan(av) & ~np.isnan(bv))
    else:
        b_better = (bv < av) | (np.isnan(av) & ~np.isnan(bv))
    tie = av == bv
    b_wins = b_ok & ((~a_ok) | b_better | (tie & (bi > ai)))
    return np.where(b_wins, bi, ai)


def _build_argmax_sparse(vals: np.ndarray, is_max: bool) -> list:
    """稀疏表（RMQ）变体：每层存"区间极值的最右下标"，同值取更右（等值取最近）。

    与 _build_sparse（存极值）同构；层合并比较见 _dyn_best_idx。NaN 位用 -1 占位。
    """
    n = len(vals)
    if n == 0:
        return []
    k = int(np.log2(n)) + 1
    st = [np.where(np.isnan(vals), -1, np.arange(n))]
    for j in range(1, k):
        prev = st[-1]
        half = 1 << (j - 1)
        cur = np.empty(n, dtype=np.int64)
        cur[: n - half] = _dyn_best_idx(prev[: n - half], prev[half:], vals, is_max)
        cur[n - half :] = prev[n - half :]
        st.append(cur)
    return st


def _dyn_arg_idx_vec(vals: np.ndarray, nvals: np.ndarray, is_max: bool) -> np.ndarray:
    """动态窗口"最右极值下标"：每位置 i 返回窗口 [i-N_i+1, i] 内极值所在的最大下标。

    与 _dyn_rmq_vec 同构的全向量化（按窗口长度分组、每层一次批量查询，O(n log n)）；
    不同处是稀疏表存"极值下标"而非极值，直接给出 HHVBARS/LLVBARS 需要的 j。
    """
    n = len(vals)
    if n == 0:
        return np.zeros(0, dtype=np.int64)
    st = _build_argmax_sparse(vals, is_max)
    Ns = _win_lens_vec(nvals)
    lens = np.minimum(Ns, np.arange(1, n + 1))
    js = np.floor(np.log2(lens)).astype(np.int64)
    idx = np.arange(n)
    l_arr = idx - lens + 1
    out = np.full(n, -1, dtype=np.int64)
    for k, layer in enumerate(st):
        sel = js == k
        if not sel.any():
            continue
        span = 1 << k
        lk = l_arr[sel]
        rk = idx[sel]
        out[sel] = _dyn_best_idx(layer[lk], layer[rk - span + 1], vals, is_max)
    return out


def dyn_bars_vec(vals: np.ndarray, nvals: np.ndarray, is_max: bool) -> np.ndarray:
    """动态 HHVBARS/LLVBARS：每位置 i 在窗口 [i-N_i+1, i] 内取极值（等值取最近）所在位置的距今天数。

    用于通达信/益盟变量周期写法 HHVBARS(X, N_i)/LLVBARS(X, N_i)，其中 N 是序列
    （如 AT+1）。全向量化实现：稀疏表 RMQ 存"极值最右下标"（_build_argmax_sparse /
    _dyn_arg_idx_vec），每位置 O(log n) 内获得 j（窗口内最近极值下标），
    HHVBARS = i - j；与 _dyn_rmq_vec 同构按窗口长度分组批量查询，总 O(n log n)、
    无逐位置 Python 循环（旧版对每个 i 向后 while 扫描窗口，最坏 O(n×w)）。
    语义与旧版完全一致：当日值 NaN → 当日 NaN；窗口全 NaN → NaN。
    """
    n = len(vals)
    if n == 0:
        return np.zeros(0, dtype=float)
    j = _dyn_arg_idx_vec(vals, nvals, is_max)
    i = np.arange(n)
    ok = (j >= 0) & ~np.isnan(vals)
    return np.where(ok, (i - j).astype(float), np.nan)


class DYN_HHVBARS(_DynWindowOp):
    """动态 HHVBARS(X, N_i)：变量周期写法（HHVBARS(X, AT+1)），窗口随行变。"""

    def _load_internal(self, instrument, start_index, end_index, *args):
        vals, nvals, idx = self._load_both(instrument, start_index, end_index, *args)
        return pd.Series(dyn_bars_vec(vals, nvals, is_max=True), index=idx)


class DYN_LLVBARS(_DynWindowOp):
    """动态 LLVBARS(X, N_i)：变量周期写法（LLVBARS(X, AT+1)），窗口随行变。"""

    def _load_internal(self, instrument, start_index, end_index, *args):
        vals, nvals, idx = self._load_both(instrument, start_index, end_index, *args)
        return pd.Series(dyn_bars_vec(vals, nvals, is_max=False), index=idx)


# ---------------- And / Or：qlib 内建 np.bitwise_and 的 dtype 脆弱性覆盖 ----------------

class _LogicalAndOr(ExpressionOps):
    """And/Or 的健壮实现：两侧先转数值布尔（≠0 且非 NaN → True）再逻辑运算，输出 0/1。

    背景：qlib 内建 And/Or 用 `np.bitwise_and/or_(left, right)`，不归一 dtype。当一侧来自
    动态算子/字段运算（float32 0/1）而另一侧是比较算子结果（可能 bool）时，numpy 抛
    `unsupported operand type(s) for &: 'float' and 'bool'`（实测 And(DYN_REF(...),Ge(...))
    报错、顺序反过来则通过——与操作数 numpy 类型提升顺序有关）。巨型布尔公式（杯柄
    突破 CUP_POOL 等大量 `A AND B AND C` 链 + 动态窗口）必触发。
    本实现与面板 _apply 的 And/Or 语义一致（a!=0 & b!=0 → 0/1 float），对任意数值/bool
    输入健壮。注册时覆盖 qlib 内建 And/Or（项目已有 DYN_* override 先例）。
    """

    def __init__(self, feature_left, feature_right):
        self.feature_left = feature_left
        self.feature_right = feature_right
        super().__init__()

    def _load(self, instrument, start_index, end_index, *args, f=None):
        from qlib.data.base import Expression as _E
        if isinstance(f, _E):
            return f.load(instrument, start_index, end_index, *args)
        return f

    def _load_internal(self, instrument, start_index, end_index, *args):
        l = self._load(instrument, start_index, end_index, *args, f=self.feature_left)
        r = self._load(instrument, start_index, end_index, *args, f=self.feature_right)

        def _b(v):
            if isinstance(v, pd.Series):
                arr = v.to_numpy(dtype=float)
                idx = v.index
            elif isinstance(v, np.ndarray):
                arr = v.astype(float)
                idx = None
            else:  # 常量
                arr = np.asarray(float(v))
                idx = None
            arr = np.where(np.isnan(arr), 0.0, (arr != 0).astype(float))
            return arr, idx

        la, lidx = _b(l)
        ra, ridx = _b(r)
        if la.ndim == 0 and ra.ndim == 0:
            out = float(self._op(la, ra))
            return out
        # 广播（常量 vs 序列）
        if la.ndim == 0:
            la = np.broadcast_to(la, ra.shape)
        if ra.ndim == 0:
            ra = np.broadcast_to(ra, la.shape)
        res = self._op(la, ra).astype(float)
        idx = lidx if lidx is not None else ridx
        return pd.Series(res, index=idx)

    def __str__(self):
        return f"{type(self).__name__}({self.feature_left},{self.feature_right})"

    def get_longest_back_rolling(self):
        from qlib.data.base import Expression as _E
        def _lbr(f):
            return f.get_longest_back_rolling() if isinstance(f, _E) else 0
        return max(_lbr(self.feature_left), _lbr(self.feature_right))

    def get_extended_window_size(self):
        from qlib.data.base import Expression as _E
        def _ext(f):
            return f.get_extended_window_size() if isinstance(f, _E) else (0, 0)
        ll, lr = _ext(self.feature_left)
        rl, rr = _ext(self.feature_right)
        return max(ll, rl), max(lr, rr)


class And(_LogicalAndOr):
    def _op(self, a, b):
        return (a != 0) & (b != 0)


class Or(_LogicalAndOr):
    def _op(self, a, b):
        return (a != 0) | (b != 0)


# ---------------- SR：益盟"删停牌行"语义包装 ----------------

class SR(ExpressionOps):
    """SR(X, [M])：按停牌掩码剔除行，返回"只有有效交易日"的连续序列。

    qlib bin 在股票上市区间内"每个日历日一行"，停牌日以 NaN 行存在，导致
    Ref/窗口在复牌初期把停牌日当"一天"参与运算（复牌首日 Ref=NaN、窗口被截断）。
    包在叶子字段上后，后续 qlib 内建的 Ref/Mean/Max/算术自然按连续交易日计算
    → 与益盟/聚宽（行情无停牌行）一致。

    两种用法：
    - SR($close)：按字段自身 NaN 剔除（行情价格/量字段停牌日为空，等价于停牌掩码）；
    - SR($factor,$close)：按掩码字段（$close）剔除——factor/market_cap 等停牌日
      仍有值、自身没有 NaN，必须显式按行情掩码剔除，否则与已删行的价格字段
      组合时会把停牌行"外对齐"回来。
    """

    # 删行后为让 Ref/窗口能取到"停牌前的有效值"，读取需向前扩展覆盖可能的停牌段。
    # 交易日起算；停牌超过该长度的极端情况 Ref 仍会断（罕见，可调大）。
    LOOKBACK_DAYS = 250

    def __init__(self, feature, mask=None, lookback=None):
        self.feature = feature
        self._mask = mask
        self._lookback = int(lookback) if lookback is not None else self.LOOKBACK_DAYS
        super().__init__()

    def _load_internal(self, instrument, start_index, end_index, *args):
        series = self.feature.load(instrument, start_index, end_index, *args)
        if self._mask is not None:
            m = self._mask.load(instrument, start_index, end_index, *args)
            if len(series) and len(m):
                if not series.index.equals(m.index):
                    m = m.reindex(series.index)
                keep = m.notna()
                return series[keep.values]
            # 掩码空（如查询起点早于数据）：退化按自身
        return series.dropna()

    def __str__(self):
        # 缓存 key 必须带子表达式/掩码/扩展窗口（同 BARSLAST 的说明）
        if self._mask is not None:
            return "SR({},{},{})".format(self.feature, self._mask, self._lookback)
        return "SR({},{})".format(self.feature, self._lookback)

    def get_longest_back_rolling(self):
        return self.feature.get_longest_back_rolling()

    def get_extended_window_size(self):
        # 向前扩展 lookback 天：删行后仍能跨停牌段取到"前一个有效交易日"的值
        lft, rght = self.feature.get_extended_window_size()
        return lft + self._lookback, rght


# ---------------- EMA 通达信递归语义版（备选，默认已切回 qlib 内建 EMA） ----------------

class EMA_TDX(_QLIB_EMA):
    """通达信/聚宽语义的 EMA（递归式）：
    Y_t = (2·X_t + (N−1)·Y_{t−1}) / (N+1)   →   pandas ewm(alpha=2/(N+1), adjust=False)。

    qlib 内建 EMA 用 ewm(span=N, adjust=True, min_periods=1)（pandas 默认，从序列起点
    整段归一化指数加权），与通达信公式系统的递归口径在【序列开头】存在初值差异
    （随后指数收敛，N=4 约 30+ 交易日后可忽略），对上市初期（次新股）的公式信号判定
    有明显影响。

    2026-09-04 归因验证：两语义对趋势顶底离开底部整体日截面统计仅差 ~0.01pp。
    对账基准 = 聚宽/同事 notebook（qlib 内建 EMA、adjust=True），故翻译层默认已
    切回 qlib 内建 EMA（parser/codegen.py 的 EMA_SEMANTICS="qlib"）；本 EMA_TDX 算子
    保留，作为需要通达信递归语义时的备选（改 codegen.EMA_SEMANTICS="tdx" 并重存公式）。
    """

    def _load_internal(self, instrument, start_index, end_index, *args):
        series = self.feature.load(instrument, start_index, end_index, *args)
        if isinstance(self.N, int) and self.N == 0:
            # 通达信 EMA 无 N=0 语义；防御性等价 expanding 均值
            return series.expanding(min_periods=1).mean()
        if 0 < self.N < 1:
            return series.ewm(alpha=self.N, adjust=False, min_periods=1).mean()
        # N>=1：span=N 等价 alpha=2/(N+1)，显式 adjust=False 走通达信递归式
        return series.ewm(alpha=2.0 / (self.N + 1), adjust=False, min_periods=1).mean()


# ---------------- POW 幂运算（qlib 内建名 Power；Pow 为旧编译别名） ----------------

class Pow(ExpressionOps):
    """POW(X, Y) = X^Y（逐元素幂）。

    qlib 内建算子名为 Power（NpPairOperator → np.power）。早期 codegen 曾把 POW
    映射成不存在的 "Pow" → 已存公式里的 Pow(...) 执行报 "operator [Pow] is not
    registered"。本类注册 Pow 别名（同一 np.power 语义），让旧编译公式无需重存即可跑；
    新编译公式走 Power（见 parser/codegen.py）。两个名字结果一致。
    """

    def __init__(self, feature_left, feature_right):
        self.feature_left = feature_left
        self.feature_right = feature_right
        super().__init__()

    def _load_internal(self, instrument, start_index, end_index, *args):
        from qlib.data.base import Expression as _E

        def _load(f):
            if isinstance(f, _E):
                return f.load(instrument, start_index, end_index, *args)
            return f

        x = _load(self.feature_left)
        y = _load(self.feature_right)

        def _arr(v):
            if isinstance(v, (pd.Series, np.ndarray)):
                return np.asarray(v, dtype=float)
            return np.full(1, float(v), dtype=float) if np.ndim(v) == 0 else np.asarray(v, dtype=float)

        xv = _arr(x)
        yv = _arr(y)
        yv = np.broadcast_to(yv, xv.shape) if yv.size == 1 else yv
        res = np.power(xv, yv)
        idx = x.index if isinstance(x, pd.Series) else None
        return pd.Series(res, index=idx)

    def __str__(self):
        return "Pow({},{})".format(self.feature_left, self.feature_right)

    def get_longest_back_rolling(self):
        def _lbr(f):
            return f.get_longest_back_rolling() if isinstance(f, Expression) else 0
        return max(_lbr(self.feature_left), _lbr(self.feature_right))

    def get_extended_window_size(self):
        def _ext(f):
            return f.get_extended_window_size() if isinstance(f, Expression) else (0, 0)
        ll, lr = _ext(self.feature_left)
        rl, rr = _ext(self.feature_right)
        return max(ll, rl), max(lr, rr)


# ---------------- SGN / INT(TRUNC) / BETWEEN：通达信基础函数 ----------------

class SGN(ExpressionOps):
    """SGN(X)：取符号。X>0→1，X<0→-1，X=0→0；NaN（停牌）保持 NaN。"""

    def __init__(self, feature):
        self.feature = feature
        super().__init__()

    def _load_internal(self, instrument, start_index, end_index, *args):
        series = self.feature.load(instrument, start_index, end_index, *args)
        return pd.Series(np.sign(series.to_numpy(dtype=float)), index=series.index)

    def __str__(self):
        return "SGN({})".format(self.feature)

    def get_longest_back_rolling(self):
        return self.feature.get_longest_back_rolling()

    def get_extended_window_size(self):
        return self.feature.get_extended_window_size()


class TRUNC(ExpressionOps):
    """TRUNC(X)（通达信 INT 函数）：向零方向截断取整（X 的整数部分）。
    np.trunc：3.7→3、-3.7→-3；NaN 保持 NaN。"""

    def __init__(self, feature):
        self.feature = feature
        super().__init__()

    def _load_internal(self, instrument, start_index, end_index, *args):
        series = self.feature.load(instrument, start_index, end_index, *args)
        return pd.Series(np.trunc(series.to_numpy(dtype=float)), index=series.index)

    def __str__(self):
        return "TRUNC({})".format(self.feature)

    def get_longest_back_rolling(self):
        return self.feature.get_longest_back_rolling()

    def get_extended_window_size(self):
        return self.feature.get_extended_window_size()


class BETWEEN(ExpressionOps):
    """BETWEEN(X,A,B)：X 是否介于 A 与 B 之间（含边界；A、B 大小任意 → min/max 语义）。
    条件成立=1，否则=0；X 为 NaN（停牌）保持 NaN。"""

    def __init__(self, feature_x, feature_a, feature_b):
        self.feature_x = feature_x
        self.feature_a = feature_a
        self.feature_b = feature_b
        super().__init__()

    def _load_internal(self, instrument, start_index, end_index, *args):
        def _load(f):
            if isinstance(f, Expression):
                return f.load(instrument, start_index, end_index, *args)
            return f

        x = _load(self.feature_x)
        a = _load(self.feature_a)
        b = _load(self.feature_b)

        def _arr(v):
            if isinstance(v, (pd.Series, np.ndarray)):
                return np.asarray(v, dtype=float)
            return np.full(1, float(v), dtype=float) if np.ndim(v) == 0 else np.asarray(v, dtype=float)

        xv = _arr(x)
        av = _arr(a)
        bv = _arr(b)
        # A/B 为常量时广播
        av = np.broadcast_to(av, xv.shape) if av.size == 1 else av
        bv = np.broadcast_to(bv, xv.shape) if bv.size == 1 else bv
        lo = np.minimum(av, bv)
        hi = np.maximum(av, bv)
        cond = (xv >= lo) & (xv <= hi)
        res = np.where(np.isnan(xv), np.nan, cond.astype(float))
        idx = x.index if isinstance(x, pd.Series) else None
        return pd.Series(res, index=idx)

    def __str__(self):
        return "BETWEEN({},{},{})".format(self.feature_x, self.feature_a, self.feature_b)

    def get_longest_back_rolling(self):
        out = 0
        for f in (self.feature_x, self.feature_a, self.feature_b):
            if isinstance(f, Expression):
                out = max(out, f.get_longest_back_rolling())
        return out

    def get_extended_window_size(self):
        lft = rght = 0
        for f in (self.feature_x, self.feature_a, self.feature_b):
            if isinstance(f, Expression):
                l, r = f.get_extended_window_size()
                lft = max(lft, l)
                rght = max(rght, r)
        return lft, rght


class ROUND(ExpressionOps):
    """ROUND(X, N)：四舍五入到 N 位小数（np.round，IEEE 五舍六入对整分价格无差）。

    用于"真实价按分取整"（ROUND(($close/$factor), 2)）：A 股价格本为整分，
    后复权 float32 bin ÷ factor 还原会引入 ~1e-4 元浮点尾差，在指标阈值
    （如短期线<5/<15）边界会把本应恰为 5.00/15.00 的值推过阈值，导致触发日
    差一天。round 到分后与益盟/聚宽（整分原始价）完全对齐。
    """

    def __init__(self, feature, ndigits=0):
        self.feature = feature
        self.ndigits = int(ndigits)
        super().__init__()

    def _load_internal(self, instrument, start_index, end_index, *args):
        series = self.feature.load(instrument, start_index, end_index, *args)
        vals = series.to_numpy(dtype=float)
        with np.errstate(invalid="ignore"):
            out = np.round(vals, self.ndigits)
        return pd.Series(out, index=series.index)

    def __str__(self):
        # 缓存 key 必须带子表达式与位数（同 BARSLAST 的说明）
        return "ROUND({},{})".format(self.feature, self.ndigits)

    def get_longest_back_rolling(self):
        return self.feature.get_longest_back_rolling()

    def get_extended_window_size(self):
        return self.feature.get_extended_window_size()


# ---------------- 通达信有状态算子（FILTER/SMA/BARSSINCE/HHVBARS/LLVBARS） ----------------

def filter_vec(vals: np.ndarray, N: int) -> np.ndarray:
    """FILTER(X, N) 纯向量实现（供 qlib/面板共用同一数值源）：条件成立输出 1 后，
    其后 N-1 个周期抑制重复触发；X 为 NaN/0 视为未触发。"""
    vals = np.asarray(vals, dtype=float)
    n = len(vals)
    Nn = max(1, int(N))
    out = np.zeros(n, dtype=float)
    reopen = 0  # 下一次允许输出的绝对索引
    with np.errstate(invalid="ignore"):
        for i in range(n):
            v = vals[i]
            if v != 0 and not np.isnan(v) and i >= reopen:
                out[i] = 1.0
                reopen = i + Nn  # 其后 N-1 个周期抑制
    return out


class FILTER(ExpressionOps):
    """FILTER(X, N)：信号过滤。条件 X 成立输出 1 后，其后 N-1 个周期不再输出（抑制）。

    通达信语义：X 触发一次只记一次，随后 N 周期内过滤掉重复触发，
    下次可输出要等到距本次触发 >= N 个周期（且 X 再次成立）。X 为 NaN/0 视为未触发。
    """

    def __init__(self, feature, N):
        self.feature = feature
        self.N = int(N)
        super().__init__()

    def _load_internal(self, instrument, start_index, end_index, *args):
        series = self.feature.load(instrument, start_index, end_index, *args)
        return pd.Series(filter_vec(series.to_numpy(dtype=float), self.N), index=series.index)

    def __str__(self):
        return "FILTER({},{})".format(self.feature, self.N)

    def get_longest_back_rolling(self):
        return self.feature.get_longest_back_rolling()

    def get_extended_window_size(self):
        return self.feature.get_extended_window_size()


class SMA(ExpressionOps):
    """SMA(X, N, M)：通达信递归加权均线（非简单平均 MA）。

    Y_t = (M·X_t + (N-M)·Y_{t-1}) / N   →   ewm(alpha=M/N, adjust=False)，
    初值 Y_1 = X_1。N 为平滑周期，M 为权重（1<=M<=N；M=1 时对 X 平滑，越小越平滑）。
    """

    def __init__(self, feature, N, M):
        self.feature = feature
        self.N = int(N)
        self.M = int(M)
        super().__init__()

    def _load_internal(self, instrument, start_index, end_index, *args):
        series = self.feature.load(instrument, start_index, end_index, *args)
        N = max(1, self.N)
        M = max(0, min(self.M, N))
        if N == 0 or M == 0:
            return series * np.nan
        return pd.Series(
            series.ewm(alpha=M / N, adjust=False, min_periods=1).mean(),
            index=series.index)

    def __str__(self):
        return "SMA({},{},{})".format(self.feature, self.N, self.M)

    def get_longest_back_rolling(self):
        return self.feature.get_longest_back_rolling()

    def get_extended_window_size(self):
        return self.feature.get_extended_window_size()


def barsince_vec(vals: np.ndarray) -> np.ndarray:
    """BARSSINCE(X) 纯向量实现（供 qlib/面板共用同一数值源）：距首次成立周期数。

    数据起点前未成立返回 0；首成立位置记为 first 后，out[i] = i - first。
    """
    vals = np.asarray(vals, dtype=float)
    n = len(vals)
    out = np.zeros(n, dtype=float)
    first = -1
    with np.errstate(invalid="ignore"):
        for i in range(n):
            v = vals[i]
            if v != 0 and not np.isnan(v):
                if first < 0:
                    first = i
            if first >= 0:
                out[i] = i - first
    return out


class BARSSINCE(ExpressionOps):
    """BARSSINCE(X)：X 第一次成立到当前的周期数（不限窗口，与 BARSLAST 相对）。

    BARSLAST(X)=距最近一次成立；BARSSINCE(X)=距最早一次成立（数据起点起首个成立）；
    从未成立返回 0。
    """

    def __init__(self, feature):
        self.feature = feature
        super().__init__()

    def _load_internal(self, instrument, start_index, end_index, *args):
        series = self.feature.load(instrument, start_index, end_index, *args)
        return pd.Series(barsince_vec(series.to_numpy(dtype=float)), index=series.index)

    def __str__(self):
        return "BARSSINCE({})".format(self.feature)

    def get_longest_back_rolling(self):
        return self.feature.get_longest_back_rolling()

    def get_extended_window_size(self):
        return self.feature.get_extended_window_size()


class HHVBARS(ExpressionOps):
    """HHVBARS(X, N)：距 N 周期内最高值所在位置的周期数（含当日，当日为最高 → 0）。

    多日同为最高时取最近一日（越近越小）；X 为 NaN（停牌）当日输出 NaN。
    单调队列 O(n)，窗口内值递减、等值保留新索引。
    """

    def __init__(self, feature, N):
        self.feature = feature
        self.N = int(N)
        super().__init__()

    def _load_internal(self, instrument, start_index, end_index, *args):
        series = self.feature.load(instrument, start_index, end_index, *args)
        vals = series.to_numpy(dtype=float)
        return pd.Series(self._bars(vals, self.N, is_max=True), index=series.index)

    @staticmethod
    def _bars(vals, N, is_max):
        from collections import deque
        n = len(vals)
        N = max(1, N)
        res = np.full(n, np.nan, dtype=float)
        dq = deque()  # 存索引；值严格递减（等值保留新的→最近）
        for i in range(n):
            v = vals[i]
            if not np.isnan(v):
                if is_max:
                    while dq and vals[dq[-1]] <= v:
                        dq.pop()
                else:
                    while dq and vals[dq[-1]] >= v:
                        dq.pop()
                dq.append(i)
            lo = i - N + 1
            while dq and dq[0] < lo:
                dq.popleft()
            if not np.isnan(v) and dq:
                res[i] = i - dq[0]
        return res

    def __str__(self):
        return "HHVBARS({},{})".format(self.feature, self.N)

    def get_longest_back_rolling(self):
        return self.feature.get_longest_back_rolling() + self.N - 1

    def get_extended_window_size(self):
        lft, rght = self.feature.get_extended_window_size()
        return lft + self.N - 1, rght


class LLVBARS(ExpressionOps):
    """LLVBARS(X, N)：距 N 周期内最低值所在位置的周期数（含当日，当日为最低 → 0）。"""

    def __init__(self, feature, N):
        self.feature = feature
        self.N = int(N)
        super().__init__()

    def _load_internal(self, instrument, start_index, end_index, *args):
        series = self.feature.load(instrument, start_index, end_index, *args)
        vals = series.to_numpy(dtype=float)
        return pd.Series(HHVBARS._bars(vals, self.N, is_max=False), index=series.index)

    def __str__(self):
        return "LLVBARS({},{})".format(self.feature, self.N)

    def get_longest_back_rolling(self):
        return self.feature.get_longest_back_rolling() + self.N - 1

    def get_extended_window_size(self):
        lft, rght = self.feature.get_extended_window_size()
        return lft + self.N - 1, rght


# ---------------- 段感知（multi-segment）内核 ----------------
# 背景（v1.18.4）：面板 `_by_group` 逐段调用"单段"内核，每段仅 200~250 行（一只股票
# 一次），而每个内核内部有 ~10 次 numpy 调用（arange/isnan/where/花式索引…），小数组
# 下调用调度开销远大于计算本身（实测 dyn_ref_vec 单段约 10μs）。巨型公式（CUP_POOL）
# 一次求值有上百个动态节点 × 数百只股票 = 数万次内核调用，纯调度开销占单块 40%+。
#
# 此处提供"一次处理全部段"的等价实现：各段拼成一个长数组 + 段边界（seg_start/seg_end
# 为"每位置所属段起/止"数组），在全局长数组上做向量化运算，并用段边界 mask 掉跨段污染。
# 数值与逐段调用**逐位一致**（见 ai_test 单元对拍与面板快照对拍）。
# 统一签名：(vals, nvals, seg_start, seg_end) -> ndarray(len(vals))。


def seg_start_arr(bnd: np.ndarray, n: int) -> np.ndarray:
    """由段边界 bnd（长度 nseg+1）生成"每位置所属段起点"数组（长度 n）。"""
    return np.repeat(np.asarray(bnd[:-1], dtype=np.int64), np.diff(bnd))


def seg_end_arr(bnd: np.ndarray, n: int) -> np.ndarray:
    """由段边界 bnd 生成"每位置所属段终点（不含）"数组（长度 n）。"""
    return np.repeat(np.asarray(bnd[1:], dtype=np.int64), np.diff(bnd))


def bars_count_seg(vals: np.ndarray, nvals, seg_start, seg_end) -> np.ndarray:
    """BARSCOUNT 段感知：全局 cumsum 减去段起点前的累计值（段内重置）。"""
    v = np.asarray(vals, dtype=float)
    c = np.cumsum(~np.isnan(v)).astype(np.float64)
    off = np.where(seg_start > 0, c[np.maximum(seg_start - 1, 0)], 0.0)
    return c - off


def barslast_vec_seg(vals: np.ndarray, nvals, seg_start, seg_end) -> np.ndarray:
    """BARSLAST 段感知：全局"最近成立下标"前缀最大值，落在段起点之前则记 0。"""
    n = len(vals)
    mask = (vals != 0) & ~np.isnan(vals)
    idx = np.where(mask, np.arange(n), -1)
    last = np.maximum.accumulate(idx)
    return np.where(last >= seg_start, np.arange(n) - last, 0.0)


def barsince_vec_seg(vals: np.ndarray, nvals, seg_start, seg_end) -> np.ndarray:
    """BARSSINCE 段感知：段内首个成立位置（searchsorted 从段起点起）。"""
    vals = np.asarray(vals, dtype=float)
    n = len(vals)
    mask = (vals != 0) & ~np.isnan(vals)
    out = np.zeros(n, dtype=float)
    pos = np.flatnonzero(mask)
    if pos.size:
        j = np.searchsorted(pos, seg_start, side="left")
        in_seg = j < pos.size
        first = np.where(in_seg, pos[np.minimum(j, pos.size - 1)], n + 1)
        ok = in_seg & (first <= np.arange(n))
        out = np.where(ok, np.arange(n) - first, 0.0)
    return out


def barsincen_vec_seg(vals: np.ndarray, nvals, seg_start, seg_end) -> np.ndarray:
    """BARSSINCEN 段感知：N 窗内最早成立距当前周期数（窗口下界 clip 到段起点）。"""
    vals = np.asarray(vals, dtype=float)
    N = max(1, int(nvals)) if np.isscalar(nvals) else 1
    n = len(vals)
    i = np.arange(n)
    mask = (vals != 0) & ~np.isnan(vals)
    pos = np.flatnonzero(mask)
    out = np.zeros(n, dtype=float)
    if pos.size:
        lo = np.maximum(seg_start, i - N + 1)
        j = np.searchsorted(pos, lo, side="left")
        in_win = j < pos.size
        earliest = np.where(in_win, pos[np.minimum(j, pos.size - 1)], n + 1)
        ok = in_win & (earliest <= i)
        out = np.where(ok, i - earliest, 0.0)
    return out


def filter_vec_seg(vals: np.ndarray, nvals, seg_start, seg_end) -> np.ndarray:
    """FILTER 段感知：逐段贪心抑制（只在触发位置上前进，段内 reopen 重置）。

    与逐段 `filter_vec` 逻辑完全一致（reopen 用全局下标：段起点即段内 reopen=0）。
    """
    vals = np.asarray(vals, dtype=float)
    N = max(1, int(nvals)) if np.isscalar(nvals) else 1
    n = len(vals)
    out = np.zeros(n, dtype=float)
    mask = (vals != 0) & ~np.isnan(vals)
    pos = np.flatnonzero(mask)
    if pos.size == 0:
        return out
    starts = np.flatnonzero(np.r_[True, seg_start[1:] != seg_start[:-1]])
    ends = np.r_[starts[1:], n]
    lo = np.searchsorted(pos, starts, side="left")
    hi = np.searchsorted(pos, ends, side="left")
    for g in range(len(starts)):
        reopen = int(starts[g])
        for p in pos[lo[g]:hi[g]]:
            if p >= reopen:
                out[p] = 1.0
                reopen = int(p) + N
    return out


def dyn_ref_vec_seg(vals: np.ndarray, nvals, seg_start, seg_end) -> np.ndarray:
    """DYN_REF 段感知：全局下标回退 N_i，越出本段 [seg_start, seg_end) 则 NaN。"""
    n = len(vals)
    nv = np.where(np.isnan(nvals), 0.0, np.trunc(nvals))
    j = np.arange(n) - nv.astype(np.int64)
    out = np.full(n, np.nan, dtype=float)
    ok = (j >= seg_start) & (j < seg_end)
    out[ok] = vals[j[ok]]
    return out


def _dyn_rmq_vec_seg(vals, nvals, func, seg_start) -> np.ndarray:
    """DYN_MIN/MAX 段感知：全局稀疏表 + 窗口下界 clip 到段起点。

    仅读取"完整落在某段内"的稀疏表区间（l, r 均在段内 → 其 2 个子区间也都在段内），
    故与逐段建表结果一致；fmin/fmax 满足结合律，建表顺序不影响数值。
    """
    n = len(vals)
    if n == 0:
        return np.zeros(0, dtype=float)
    st = _build_sparse(vals, func)
    Ns = _win_lens_vec(nvals)
    idx = np.arange(n)
    lens = np.minimum(Ns, idx - seg_start + 1)
    js = np.floor(np.log2(lens)).astype(np.int64)
    l_arr = idx - lens + 1
    out = np.full(n, np.nan, dtype=float)
    for k, layer in enumerate(st):
        sel = js == k
        if not sel.any():
            continue
        span = 1 << k
        lk = l_arr[sel]
        rk = idx[sel]
        out[sel] = func(layer[lk], layer[rk - span + 1])
    return out


def dyn_window_sum_vec_seg(vals: np.ndarray, nvals, seg_start, seg_end) -> np.ndarray:
    """DYN_SUM 段感知：全局前缀和（窗口下界 clip 到段起点即可，无需按段重置）。"""
    n = len(vals)
    pre = np.concatenate([[0.0], np.cumsum(np.nan_to_num(vals, nan=0.0))])
    Ns = _win_lens_vec(nvals)
    lo = np.maximum(seg_start, np.arange(n) - Ns + 1)
    return pre[np.arange(n) + 1] - pre[lo]


def dyn_window_count_vec_seg(vals: np.ndarray, nvals, seg_start, seg_end) -> np.ndarray:
    """DYN_COUNT 段感知：同 DYN_SUM，权重改为"非 0 且非 NaN"计数。"""
    n = len(vals)
    w = ((vals != 0) & ~np.isnan(vals)).astype(float)
    pre = np.concatenate([[0.0], np.cumsum(w)])
    Ns = _win_lens_vec(nvals)
    lo = np.maximum(seg_start, np.arange(n) - Ns + 1)
    return pre[np.arange(n) + 1] - pre[lo]


def _dyn_arg_idx_vec_seg(vals, nvals, is_max, seg_start) -> np.ndarray:
    """段感知的"最右极值下标"稀疏表查询（返回全局下标，落在段内）。"""
    n = len(vals)
    if n == 0:
        return np.zeros(0, dtype=np.int64)
    st = _build_argmax_sparse(vals, is_max)
    Ns = _win_lens_vec(nvals)
    idx = np.arange(n)
    lens = np.minimum(Ns, idx - seg_start + 1)
    js = np.floor(np.log2(lens)).astype(np.int64)
    l_arr = idx - lens + 1
    out = np.full(n, -1, dtype=np.int64)
    for k, layer in enumerate(st):
        sel = js == k
        if not sel.any():
            continue
        span = 1 << k
        lk = l_arr[sel]
        rk = idx[sel]
        out[sel] = _dyn_best_idx(layer[lk], layer[rk - span + 1], vals, is_max)
    return out


def dyn_bars_vec_seg(vals, nvals, is_max, seg_start) -> np.ndarray:
    """DYN_HHVBARS/LLVBARS 段感知：全局下标差 i - j（j 在段内）。"""
    n = len(vals)
    if n == 0:
        return np.zeros(0, dtype=float)
    j = _dyn_arg_idx_vec_seg(vals, nvals, is_max, seg_start)
    i = np.arange(n)
    ok = (j >= 0) & ~np.isnan(vals)
    return np.where(ok, (i - j).astype(float), np.nan)


# ---- 固定窗口 HHVBARS/LLVBARS 段感知（对齐 ops_ext.HHVBARS._bars 单调队列语义）----
# 注意：`dyn_bars_vec`（动态窗口）内部走 `_dyn_best_idx`，其 max/min 判定方向与
# `HHVBARS._bars` 相反（既有行为：面板与 qlib 侧共用同一函数，故两端对账不暴露），
# 因此固定窗口 HHVBARS/LLVBARS **不能**复用 `dyn_bars_vec_seg`，必须用下面这套
# "最右极值"稀疏表，才能与 `_bars` 单调队列（等值保留新索引 = 取最右）逐位一致。


def _rightmost_arg_best(ai, bi, vals, is_max):
    """合并两个候选"极值最右下标"（-1 = 无效/NaN）；同值取更右（max(ai, bi)）。"""
    a_ok = ai >= 0
    b_ok = bi >= 0
    av = np.where(a_ok, vals[np.maximum(ai, 0)], np.nan)
    bv = np.where(b_ok, vals[np.maximum(bi, 0)], np.nan)
    if is_max:
        b_better = (bv > av) | (np.isnan(av) & ~np.isnan(bv))
    else:
        b_better = (bv < av) | (np.isnan(av) & ~np.isnan(bv))
    tie = av == bv
    return np.where(b_ok & ((~a_ok) | b_better | (tie & (bi > ai))), bi, ai)


def _build_rightmost_arg_sparse(vals, is_max):
    """稀疏表：每层存"区间极值的最右下标"（NaN 位用 -1 占位）。"""
    n = len(vals)
    if n == 0:
        return []
    k = int(np.log2(n)) + 1
    st = [np.where(np.isnan(vals), -1, np.arange(n))]
    for j in range(1, k):
        prev = st[-1]
        half = 1 << (j - 1)
        cur = np.empty(n, dtype=np.int64)
        cur[: n - half] = _rightmost_arg_best(prev[: n - half], prev[half:], vals, is_max)
        cur[n - half:] = prev[n - half:]
        st.append(cur)
    return st


def _rightmost_bars_seg(vals, seg_start, nvals, is_max) -> np.ndarray:
    """固定窗口 HHVBARS/LLVBARS 段感知：i - (段内窗口最右极值下标)。"""
    n = len(vals)
    if n == 0:
        return np.zeros(0, dtype=float)
    N = max(1, int(nvals))
    st = _build_rightmost_arg_sparse(vals, is_max)
    idx = np.arange(n)
    lens = np.minimum(N, idx - seg_start + 1)
    js = np.floor(np.log2(lens)).astype(np.int64)
    l_arr = idx - lens + 1
    out = np.full(n, -1, dtype=np.int64)
    for k, layer in enumerate(st):
        sel = js == k
        if not sel.any():
            continue
        span = 1 << k
        lk = l_arr[sel]
        rk = idx[sel]
        out[sel] = _rightmost_arg_best(layer[lk], layer[rk - span + 1], vals, is_max)
    ok = (out >= 0) & ~np.isnan(vals)
    return np.where(ok, (idx - out).astype(float), np.nan)


def hhvbars_seg(vals, nvals, seg_start, seg_end) -> np.ndarray:
    """HHVBARS 固定窗口 N=nvals 段感知（与 HHVBARS._bars(is_max=True) 逐位一致）。"""
    return _rightmost_bars_seg(vals, seg_start, nvals, True)


def llvbars_seg(vals, nvals, seg_start, seg_end) -> np.ndarray:
    """LLVBARS 固定窗口 N=nvals 段感知（与 HHVBARS._bars(is_max=False) 逐位一致）。"""
    return _rightmost_bars_seg(vals, seg_start, nvals, False)


# ---------------- 注册机制 ----------------

_ALL_OPS = [
    BARSLAST, BARSCOUNT, BARSSINCEN,
    DYN_MIN, DYN_MAX, DYN_COUNT, DYN_REF, DYN_SUM, DYN_HHVBARS, DYN_LLVBARS,
    And, Or,          # 覆盖 qlib 内建：np.bitwise_and 对 float&bool 混输脆弱
    SR,
    EMA_TDX,
    SGN, TRUNC, BETWEEN,
    Pow,
    ROUND,
    FILTER, SMA, BARSSINCE, HHVBARS, LLVBARS,
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
