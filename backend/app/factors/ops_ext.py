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
    "SR",
    "EMA_TDX",
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


# ---------------- 注册机制 ----------------

_ALL_OPS = [
    BARSLAST, BARSCOUNT, BARSSINCEN,
    DYN_MIN, DYN_MAX, DYN_COUNT, DYN_REF, DYN_SUM,
    SR,
    EMA_TDX,
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
