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

import threading

import numpy as np
import pandas as pd
from qlib.data.base import Expression, ExpressionOps
from qlib.data.ops import Operators
from qlib.data.ops import EMA as _QLIB_EMA

__all__ = [
    "BARSLAST", "BARSCOUNT", "BARSSINCEN",
    "RecurOut", "RecurPrev",
    "DYN_MIN", "DYN_MAX", "DYN_COUNT", "DYN_REF", "DYN_SUM", "DYN_MEAN",
    "DYN_HHVBARS", "DYN_LLVBARS",
    "And", "Or", "If",
    "Gt", "Ge", "Lt", "Le", "Eq", "Ne",
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







# ★ v2.10.87：`_ARANGE_CACHE` 已随 `_arange_cached` 一起搬去 `ops_seg.py` ✓




def _dyn_rmq_vec(vals: np.ndarray, nvals: np.ndarray, func) -> np.ndarray:
    """动态窗口最值的全向量化版本（等价逐行 _rmq 循环）。

    每个位置的窗口为 [i-N_i+1, i]（N_i 由 nvals 给出）。窗口左端 l=idx-lens+1、
    右端 r=idx，取 `js=floor(log2(lens))` 层的两个长 2^js 的重叠块合并 ——
    fmin/fmax 满足结合律且忽略 NaN，故与逐位置 RMQ 逐位一致。

    v1.18.26（T3）：把「只建到 `js.max()` 层」+「合并成一次 fancy-index 查询」合起来
    做 —— 原先是建 `log2(n)+1` 层再逐层布尔掩码，长面板上大量层纯属空转。
    v1.18.40：窗口几何抽到 `_dyn_geom`（缓冲复用）；合并写回第一个 gather 的结果
    （`out=`），再省一次分配。
    """
    n = len(vals)
    if n == 0:
        return np.zeros(0, dtype=float)
    js, l1, l2 = _dyn_geom(nvals, None, n)
    st = _build_sparse(vals, func, int(js.max()))
    a = st[js, l1]
    return func(a, st[js, l2], out=a)


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


def dyn_window_mean_vec(vals: np.ndarray, nvals: np.ndarray) -> np.ndarray:
    """★ v1.20.55 DYN_MEAN 全向量化：窗口内**非 NaN** 的均值 ✓。

    口径 = **pandas `rolling(N, min_periods=1).mean()`**（qlib `Mean` 的底层 ✓）：
      · 分子：`Σ(nan→0)` ✓（复用 `dyn_window_sum_vec` ✓）；
      · 分母：窗口内**非 NaN 的个数** ✓（注意 ≠ `DYN_COUNT` ✗ —— 后者数的是"非 0 且非 NaN" ✓）；
      · 窗口全 NaN ⇒ 结果 NaN ✓（用 `np.divide(..., where=den>0)` 避免 0/0 报警 ✓）；
      · 开头窗口不足 ⇒ 按**可用天数**取均值 ✓（等价 `min_periods=1` ✓）。
    """
    num = dyn_window_sum_vec(vals, nvals)
    valid = np.where(np.isnan(vals), 0.0, 1.0)          # 非 NaN 记 1 ⇒ count 就是有效个数 ✓
    den = dyn_window_count_vec(valid, nvals)
    out = np.full(len(num), np.nan, dtype=float)
    np.divide(num, den, out=out, where=den > 0)
    return out


def barsincen_vec(vals: np.ndarray, N: int) -> np.ndarray:
    """BARSSINCEN 全向量化：N 窗内最早满足距当前周期数（无则 0）。"""
    N = max(1, int(N))
    n = len(vals)
    mask = (vals != 0) & ~np.isnan(vals)
    pos = np.flatnonzero(mask)
    out: np.ndarray = np.zeros(n, dtype=float)   # ★ v2.10.89：显式声明为 ndarray ✓ —— 下面会赋 `np.where(...)`（形状未知 ✗）
    if pos.size:
        lo = np.maximum(0, np.arange(n) - N + 1)
        j = np.searchsorted(pos, lo, side="left")
        in_win = j < pos.size
        earliest = np.where(in_win, pos[np.minimum(j, pos.size - 1)], n + 1)
        ok = in_win & (earliest <= np.arange(n))
        out = np.where(ok, np.arange(n) - earliest, 0.0)
    return out


def _align_series(a, b):
    """★ v1.20.60：把两个 Series 按**索引对齐**（并集 ⇒ 缺侧补 NaN ✓）。

    为什么必须做（2026-09-23 任务 `07bf2c5ba41e` 失败 ✓，逐条加载 49 条公式定位到第 35 条
    `强龙起势` ✓）：
      ```text
      File "app/factors/ops_ext.py", line 657, in _load_internal
      File "app/factors/ops_ext.py", line 681, in _op
      ValueError: operands could not be broadcast together with shapes (4992,) (5110,)   ← 同一支股票、两个天数 ✗
      ```
      `And/Or` 原先只处理了"**常量 vs 序列**"（`ndim==0` 时广播 ✓），**两侧都是序列但长度不同**
      时直接 `(a!=0)&(b!=0)` ⇒ 崩 ✗。
      ⚠ 为什么会不等长：`DYN_*` 把扩展窗口拉到 **inf**（全历史 ✓）后，同一棵树里有的子式覆盖
      全历史、有的只覆盖部分（如 `BARSCOUNT` 从上市首日起算 ✓、停牌删行的 `SR` 包装 ✓）
      ⇒ 长度天然不同 ✗。
      ⚠ 同文件的 `Corr` **早就**为「SR 删行导致的左右不等长」做过容错 ✓ —— **`And/Or` 漏了** ✗✓。

    对齐语义：**并集索引**、缺侧补 **NaN** ✓ ⇒ 各算子既有的 NaN 口径自然生效
      （逻辑运算里 NaN 视为 0/False ✓，与 `_b` 的「NaN→0」一致 ✓）。
    """
    if isinstance(a, pd.Series) and isinstance(b, pd.Series) and not a.index.equals(b.index):
        a, b = a.align(b, join="outer")
    return a, b


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
        # ★ v1.20.60：窗口参数允许是**常量字面量** ✓ —— 表达式写成 `DYN_MEAN($close,5)` 时，qlib 解析
        #   出来的是 **plain int** ✗ ⇒ 原来直接 `self.N_expr.load(...)` 会崩
        #   `'int' object has no attribute 'load'` ✗（2026-09-23 真机实测 ✓：`Gt(DYN_MEAN($close,5),0)`
        #   与 `DYN_COUNT(And(...),5)` 都崩 ✓）。常量 ⇒ 直接铺成同索引的常量序列 ✓。
        #   ⚠ 判据用**鸭子类型**（有没有 `load` ✓）而**不是** `isinstance(..., Expression)` ✗ ——
        #   既有单测用的桩对象不继承 `Expression` ✓（改成 isinstance 会让 16 个 DYN 测试全挂 ✗，实测 ✓）。
        if hasattr(self.N_expr, "load"):
            ns = self.N_expr.load(instrument, start_index, end_index, *args)
        else:
            ns = pd.Series(float(self.N_expr), index=series.index)
        series, ns = _align_series(series, ns)      # ★ 两侧日期轴可能不等长（见 `_align_series`）✗→✓
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


class DYN_MEAN(_DynWindowOp):
    """★ v1.20.55 动态窗口均值（MA/MEAN 的**变量周期**写法 ✓）：前缀和，全向量化 ✓。

    动机（2026-09-23 用户报「强龙起势（周期 60 天）：特征计算失败:
    window must be an integer 0 or greater」）：通达信/益盟公式里
        `MA5:=MA(C,MIN(BARNUM,5))`（= 窗口不超过"已上市天数" ✓）
    **完全合法** ✓，但 `MA` 原先**不在动态窗口白名单**里 ✗ ⇒ 生成 `Mean($close, Less(BARSCOUNT($close),5))`
    ✗ ⇒ qlib `Rolling` 把"序列"当窗口交给 `pandas.rolling` ⇒ 直接崩 ✗。
    ⇒ 本算子让这类写法与 HHV/LLV/COUNT/REF/SUM 一样**逐位置取窗口** ✓（语义同通达信 ✓）。
    """

    def _load_internal(self, instrument, start_index, end_index, *args):
        vals, nvals, idx = self._load_both(instrument, start_index, end_index, *args)
        return pd.Series(dyn_window_mean_vec(vals, nvals), index=idx)


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










def _dyn_arg_idx_vec(vals: np.ndarray, nvals: np.ndarray, is_max: bool) -> np.ndarray:
    """动态窗口"最右极值下标"：每位置 i 返回窗口 [i-N_i+1, i] 内极值所在的最大下标。

    与 `_dyn_rmq_vec` 同构的全向量化；不同处是稀疏表存"极值下标"而非极值，直接给出
    HHVBARS/LLVBARS 需要的 j。v1.18.26（T3）：同样改为「按需建层 + 一次 fancy-index 查询」。
    """
    n = len(vals)
    if n == 0:
        return np.zeros(0, dtype=np.int64)
    js, l1, l2 = _dyn_geom(nvals, None, n)          # v1.18.40：几何改走缓冲复用
    st = _build_argmax_sparse(vals, is_max, int(js.max()))
    return _dyn_best_idx_ext(st[js, l1], st[js, l2], _make_dyn_ext(vals), is_max)


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
        # ★★ v1.20.60：**两侧都是序列但日期轴不等长**时先对齐 ✓（原先只处理了"常量 vs 序列"✗）
        #   —— 否则 `(a!=0)&(b!=0)` 直接抛 `operands could not be broadcast together (4992,) (5110,)` ✗
        #   （详见 `_align_series` 的说明 ✓）
        l, r = _align_series(l, r)

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


def _align_pair(left, right):
    """★ v1.20.62：把两侧按**索引并集**对齐，并返回"**因对齐而新增**的行"掩码 ✓。

    动机（2026-09-23 用户报任务 `855ab520c96e`）：
        `Can only compare identically-labeled Series objects. Loading SH600007:
         Gt(SR($close,250),$chip_cost_95); … length of series_left and series_right
         is different: (5054, 5110)`
      ⇒ `SR(X)`（**停牌删行**语义 ✓）返回**短序列** ✗；而 **pandas 的比较运算符要求两侧标签
      **完全一致** ✗（**算术** ufunc 会自动对齐 ✓ ⇒ 所以只有比较类会炸 ✗✓）。
      ⚠ 同文件 `Corr` 早在 2026-09-18 就为**同一病因**做过容错 ✓ —— 比较类漏了 ✗。

    返回 `(left2, right2, missing)`：两侧已同索引 ✓；`missing` 为 `None` 表示**本来就同索引**
    （此时调用方必须**逐位照旧**输出 ✗ 不得引入任何 NaN ✓）；否则它是一个**布尔数组**，
    标记"因对齐才出现的行"（= 被 `SR` 删掉的停牌日 ✓）⇒ 调用方把这些行的结果置 **NaN** ✓，
    与 `panel_expr` 声明的 SR 语义（"停牌日结果 = NaN" ✓）**完全一致** ✓。
    """
    if (isinstance(left, pd.Series) and isinstance(right, pd.Series)
            and not left.index.equals(right.index)):
        idx = left.index.union(right.index)
        common = idx.isin(left.index) & idx.isin(right.index)
        return left.reindex(idx), right.reindex(idx), ~common
    return left, right, None


class _CmpPair(ExpressionOps):
    """★ v1.20.62：比较类算子（`Gt/Ge/Lt/Le/Eq/Ne`）**容忍 SR 删行导致的两侧不等长** ✓。

    qlib 内建这些算子走 `NpPairOperator` ⇒ `np.greater(left, right)` 等 ✗ —— 两侧标签不一致时
    **pandas 直接抛** `Can only compare identically-labeled Series objects` ✗（见 `_align_pair` ✓）。
    修法：`_align_pair` 对齐 ✓ + 新增行置 NaN ✓（缺数据的日子不该给出 `0/False` ✓）。
    ⚠ **索引本来就相同**（绝大多数公式 ✓，含全部非 SR 公式 ✓）⇒ `_align_pair` 返回 `missing=None`
    ⇒ 输出与改前**逐位相同** ✓（不引 NaN ✓、不改 dtype ✗）✓✓。
    """

    def __init__(self, feature_left, feature_right):
        self.feature_left = feature_left
        self.feature_right = feature_right
        super().__init__()

    def __str__(self):
        return "{}({},{})".format(type(self).__name__, self.feature_left, self.feature_right)

    def _load(self, instrument, start_index, end_index, *args, f=None):
        # 非 Expression 的一侧 = 常量 ✓（qlib `NpPairOperator` 也允许 `Gt($close, 10)` ✓）
        if hasattr(f, "load"):
            return f.load(instrument, start_index, end_index, *args)
        return f

    def get_longest_back_rolling(self):
        def _lbr(o):
            return o.get_longest_back_rolling() if hasattr(o, "get_longest_back_rolling") else 0
        return max(_lbr(self.feature_left), _lbr(self.feature_right))

    def get_extended_window_size(self):
        # 与 qlib `NpPairOperator` 同口径：逐步取 max ✓（`DYN_*` 的 (inf,0) 必须透传 ✓）
        exts = [(o.get_extended_window_size() if hasattr(o, "get_extended_window_size") else (0, 0))
                for o in (self.feature_left, self.feature_right)]
        return max(e[0] for e in exts), max(e[1] for e in exts)

    def _load_internal(self, instrument, start_index, end_index, *args):
        l = self._load(instrument, start_index, end_index, *args, f=self.feature_left)
        r = self._load(instrument, start_index, end_index, *args, f=self.feature_right)
        l, r, missing = _align_pair(l, r)
        res = self._op(l, r)
        if missing is not None and isinstance(res, pd.Series) and missing.any():
            # ⚠ 必须先 `astype(float)` 再写 NaN ✗——直接往 **bool** 序列里塞 NaN 会让 pandas 把
            #   dtype 升成 **object** ✗ ⇒ 下游 `Mul/Add` 之类的算术会炸 ✓（2026-09-23 自测时想到 ✓）。
            #   ⚠ 只在这条"确实新增了行"的分支里转 ✗——索引本来就相同的情况**保持原 dtype** ✓。
            res = res.astype(float).copy()
            res[missing] = np.nan
        return res


class Gt(_CmpPair):
    """`>`（覆盖 qlib 内建：**索引不对齐时会崩** ✗ ⇒ 先对齐 ✓）。"""

    def _op(self, a, b):
        return np.greater(a, b)


class Ge(_CmpPair):
    def _op(self, a, b):
        return np.greater_equal(a, b)


class Lt(_CmpPair):
    def _op(self, a, b):
        return np.less(a, b)


class Le(_CmpPair):
    def _op(self, a, b):
        return np.less_equal(a, b)


class Eq(_CmpPair):
    def _op(self, a, b):
        return np.equal(a, b)


class Ne(_CmpPair):
    def _op(self, a, b):
        return np.not_equal(a, b)


class If(ExpressionOps):
    """★ v1.20.61：覆盖 qlib 内建 `If` —— 三个输入**先按索引对齐**再取值 ✓。

    动机（2026-09-23 任务 `4e8e5fe991d3` 用 v1.20.60 仍然崩 ✓）：
        operands could not be broadcast together with shapes (4992,) () (5110,)
      这正是 qlib `ops.py:670` 那行的报错形态 ✓：
        `pd.Series(np.where(series_cond, series_left, series_right), index=series_cond.index)`
      —— **三个输入只取"条件的索引"、完全不做对齐** ✗（中间那个 `()` 是**常量分支** ✓，
      例如公式里的 `IF(NH有效, 成本最高/成本最低, 0)` ✓）。成因与 `And/Or` 完全相同：
      `DYN_*` 把扩展窗口拉到 **inf**（全历史 ✓）后，同一棵树里各子式的**有效期轴不同** ✗。
      qlib 自己的注释也写明 `If` 是**唯一的 Triple-wise 算子** ✓ ⇒ 二参的 `And/Or`（v1.20.60 已修 ✓）
      之后，**它就是这一类里最后一个** ✓。

    ⚠ 语义**逐位照抄** qlib ✓：`np.where(cond, left, right)`（**非 0 即真** ✓ —— 不是 `cond > 0` ✗）、
      非 Expression 的分支按常量直接参与 ✓、非 Expression 的 condition 也允许 ✓。
    ⚠ 唯一**有意**的差异 ✓：对齐后**条件侧缺失（NaN）视为假** ✓（与 `And/Or` 的「NaN→0」同口径 ✓）——
      否则 `np.where(NaN, …)` 会把 NaN 当**真** ✗（unlikely 且更反直觉 ✓）。
    """

    def __init__(self, condition, feature_left, feature_right):
        self.condition = condition
        self.feature_left = feature_left
        self.feature_right = feature_right
        super().__init__()

    def __str__(self):
        return "If({},{},{})".format(self.condition, self.feature_left, self.feature_right)

    def _load(self, instrument, start_index, end_index, *args, f=None):
        if hasattr(f, "load"):
            return f.load(instrument, start_index, end_index, *args)
        return f

    @staticmethod
    def _arr(o):
        return o.to_numpy(dtype=float) if isinstance(o, pd.Series) else np.asarray(o, dtype=float)

    def _load_internal(self, instrument, start_index, end_index, *args):
        c = self._load(instrument, start_index, end_index, *args, f=self.condition)
        l = self._load(instrument, start_index, end_index, *args, f=self.feature_left)
        r = self._load(instrument, start_index, end_index, *args, f=self.feature_right)
        # ① 三个输入归到**同一索引**（各 Series 索引的**并集** ✓；非 Series 的常量按标量参与 ✓）
        idx = None
        for o in (c, l, r):
            if isinstance(o, pd.Series):
                idx = o.index if idx is None else idx.union(o.index)
        if idx is not None:
            c = c.reindex(idx) if isinstance(c, pd.Series) else c
            l = l.reindex(idx) if isinstance(l, pd.Series) else l
            r = r.reindex(idx) if isinstance(r, pd.Series) else r
        # ② 条件缺失 ⇒ 假 ✓（见类注释里"唯一有意的差异"✓）
        if isinstance(c, pd.Series):
            c = c.fillna(0.0)
        res = np.where(self._arr(c), self._arr(l), self._arr(r))
        return pd.Series(res, index=idx)

    def get_longest_back_rolling(self):
        return max(
            [(o.get_longest_back_rolling() if hasattr(o, "get_longest_back_rolling") else 0)
             for o in (self.condition, self.feature_left, self.feature_right)] or [0]
        )

    def get_extended_window_size(self):
        # ⚠ 必须**透传**三者的扩展窗口（取 max ✓）—— 否则条件/分支里 `DYN_*` 的 `(inf, 0)` 会被吃掉 ✗，
        #   那正是"长度不一致"的来源 ✓（与 `_LogicalAndOr` 同处理 ✓）。
        exts = [(o.get_extended_window_size() if hasattr(o, "get_extended_window_size") else (0, 0))
                for o in (self.condition, self.feature_left, self.feature_right)]
        return max(e[0] for e in exts), max(e[1] for e in exts)


# ---------------- SR：益盟"删停牌行"语义包装 ----------------

# ---------------- Corr：覆盖 qlib 内建，容忍 SR 删行导致的左右不等长 ----------------

from qlib.data.ops import Corr as _QLIB_CORR  # noqa: E402


class Corr(_QLIB_CORR):
    """滚动相关系数（覆盖 qlib 内建 `Corr`）。

    ★ 背景（2026-09-18 用户实测复现）：qlib 内建 `PairRolling`/`Corr._load_internal`
    末尾这一段**要求 `res`、`series_left`、`series_right` 三者严格等长**：
        res.loc[np.isclose(series_left.rolling(N,min_periods=1).std(),0,atol=2e-05)
                | np.isclose(series_right.rolling(N,min_periods=1).std(),0,atol=2e-05)] = np.nan
    而本项目的 `SR(X)`（**停牌删行**语义，见上面 `SR` 类）返回的是**删行后的短序列** ✗
    ⇒ 只要表达式里出现 `Corr(...)` 且叶子被 `SR` 包裹（即"停牌删行"开关打开 ✓
    `feature_cache._sr_wrap_expr` ✓），右串就会比左串短若干行（**行数 = 该股停牌天数** ✗）
    ⇒ `ValueError: operands could not be broadcast together with shapes (406,) (400,)` ✗
    （实测 `SH600008` 丢 6 行、`SH600026` 丢 2 行 ✓；栈在 `qlib/data/ops.py:1495` ✓）。

    修法：左右**先对齐到同一索引**（以左串索引为准 = qlib 给出的完整区间 ✓），
    返回值**保持定长**（qlib 算子契约 ✓）。被 `SR` 删掉的行（停牌日）结果 = `NaN` ✓
    —— 与 `panel_expr.py` 声明的 SR 语义（"停牌日结果 = NaN"）**完全一致** ✓。
    ⚠ 语义保持：仍是 `expanding(min_periods=1).corr()`（qlib 原口径），窗口仍是 `self.N` ✓。
    """

    def _load_internal(self, instrument, start_index, end_index, *args):
        left = self.feature_left.load(instrument, start_index, end_index, *args)
        right = self.feature_right.load(instrument, start_index, end_index, *args)
        if not left.index.equals(right.index):
            # `SR` 删行 ⇒ 右串更短 ✗：按左串索引重铺（缺的行 = NaN ✓，即"停牌日不参与" ✓）
            right = right.reindex(left.index)
        res = getattr(left.expanding(min_periods=1), self.func)(right)
        res = res.reindex(left.index)                      # 定长 ✓（防空交集导致索引漂移）
        # 常数序列置 NaN（qlib 原逻辑 ✓）；两边长度此刻必与 left 相同 ⇒ 不再崩 ✓
        _l_std = left.rolling(self.N, min_periods=1).std().to_numpy(dtype=float)
        _r_std = right.rolling(self.N, min_periods=1).std().to_numpy(dtype=float)
        with np.errstate(invalid="ignore", divide="ignore"):
            _bad = np.isclose(_l_std, 0, atol=2e-05) | np.isclose(_r_std, 0, atol=2e-05)
        if _bad.any():
            res = res.copy()
            res[_bad] = np.nan
        return res


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
        # ⚠ v1.19.86：**按类型**区分第二个位置参数（修用户 2026-09-17 实测的
        #   `AttributeError: 'int' object has no attribute 'load'`）。
        #   缘由：`__str__` 在**无掩码**时输出 `SR(feature, lookback)`（如 `SR($close,250)`）——
        #   qlib 有些路径会**先把表达式 str、再 parse**（缓存 key / 错误信息文本 / 部分 driver），
        #   重新解析时那个 `250` 就落到了 `mask` 位置上 ⇒ `250.load(...)` ⇒ 崩
        #   （任何含 SR 的表达式都可能中招，与字段无关；这正是同事那条
        #   `Gt(SR($close,250),$chip_cost_95)` 算不出来的原因）。
        #   ⇒ 数值/数值字面量 ⇒ 它是 **lookback**；表达式（有 load）⇒ 才当掩码。
        #   这样 `str(SR(...))` **可 round-trip**（解析回来的对象与原对象等价、字符串稳定），
        #   同时缓存 key 里仍带着 lookback（语义需要，见 `__str__`）。
        if mask is not None and not hasattr(mask, "load"):
            lb = self._as_lookback(getattr(mask, "value", mask))
            if lb is not None:
                mask, lookback = None, (lookback if lookback is not None else lb)
        self.feature = feature
        self._mask = mask
        self._lookback = int(lookback) if lookback is not None else self.LOOKBACK_DAYS
        super().__init__()

    @staticmethod
    def _as_lookback(x):
        """数值字面量 ⇒ int（含 `"250"` 这种字符串形式）；不是数值 ⇒ None（保持原语义）。"""
        if isinstance(x, bool) or x is None:
            return None
        try:
            return int(x)
        except (TypeError, ValueError):
            return None

    def _load_internal(self, instrument, start_index, end_index, *args):
        series = self.feature.load(instrument, start_index, end_index, *args)
        if self._mask is not None:
            m = self._mask.load(instrument, start_index, end_index, *args)
            if len(series) and len(m):
                if not (series.index is m.index or series.index.equals(m.index)):
                    m = m.reindex(series.index)
                keep = m.notna()
                return series[keep.values]
            # 掩码空（如查询起点早于数据）：退化按自身
        return series.dropna()

    def __str__(self):
        # 缓存 key 必须带子表达式/掩码/扩展窗口（同 BARSLAST 的说明）；
        # ⚠ 且必须**可被重新解析**（round-trip）—— 见 `__init__` 的说明：
        #   无掩码时第 2 个参数是 lookback（数值），`__init__` 按类型认回来 ✓。
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

class Exp(ExpressionOps):
    """`EXP(X)` = e^X（v1.20.34）。

    为什么加：WorldQuant Alpha101 / 国泰君安 Alpha 系列研报**大量使用** `EXP(...)`
    （常与 `POW`/`LOG` 组合，如 `EXP(SUM(LOG(1+r),N))` 的复利还原 ✓）；此前平台
    **不支持** ⇒ 用户粘贴研报公式直接 `CodeGenError: 不支持的函数：EXP` ✗。

    ⚠ **性能（用户明确要求"别走慢速通道"，2026-09-20）**：
      · 本算子 `_load_internal` 里**只调一次 `np.exp(series)`**（numpy C 实现 ✓ 向量化 ✓）
        —— 与 qlib 内建 `Sqrt`/`Log`（都是 `NpUnaryOperator`）**同一路径、同一开销** ✓；
      · **不是** `PATCH:` 补丁算子、**不做逐点 Python 循环** ✗ ⇒ 多因子训练 / 回测走
        qlib 时**不会退化** ✓；
      · 单因子测试走 `panel_expr`（见其 `_UNARY` 表 ✓）⇒ 整块面板 `np.exp` 一次 ✓。
      ⚠ 溢出（输入很大 ⇒ `inf`）由既有 `CleanInf` 处理器在 learn 阶段转 NaN ✓（不会污染训练 ✓）。
    """

    def __init__(self, feature):
        # ⚠ 与 `Pow`/`BARSLAST` 等同款：qlib 算子必须自己存住子表达式 ✓
        #   （缺这行 ⇒ 实例化即 `TypeError: Exp() takes ...` ✗ —— 单测抓到 ✓）。
        self.feature = feature
        super().__init__()

    def _load_internal(self, instrument, start_index, end_index, *args):
        series = self.feature.load(instrument, start_index, end_index, *args)
        with np.errstate(all="ignore"):        # 与面板层一致：不泄漏 RuntimeWarning（溢出→inf ✓）
            return np.exp(series)

    def __str__(self):
        # 同 BARSLAST 的说明：qlib 用 `str(self)` 作进程内缓存 key ⇒ 必须带子表达式 ✓
        return "EXP({})".format(self.feature)

    def get_longest_back_rolling(self):
        return self.feature.get_longest_back_rolling()

    def get_extended_window_size(self):
        return self.feature.get_extended_window_size()


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
    out: np.ndarray = np.zeros(n, dtype=float)   # ★ v2.10.89：显式声明为 ndarray ✓ —— 下面会赋 `np.where(...)`（形状未知 ✗）
    pos = np.flatnonzero(mask)
    if pos.size:
        j = np.searchsorted(pos, seg_start, side="left")
        in_seg = j < pos.size
        first = np.where(in_seg, pos[np.minimum(j, pos.size - 1)], n + 1)
        ok = in_seg & (first <= np.arange(n))
        out = np.where(ok, np.arange(n) - first, 0.0)
    return out






# ---------------- DYN_* 内核的线程局部 scratch（v1.18.28） ----------------
# 为什么必须**线程局部**：单因子测试 / 事件研究各跑在一个后台线程里，股票池小于
# `QLIB_SFT_PANEL_MAX`（默认 1000）时是**进程内**求值 → 两个任务并发会共用模块级缓冲
# 而串数据。大池走 ProcessPoolExecutor、各 worker 进程独立，不受影响。


















# ---- 固定窗口 HHVBARS/LLVBARS 段感知（对齐 ops_ext.HHVBARS._bars 单调队列语义）----
# 注意：`dyn_bars_vec`（动态窗口）内部走 `_dyn_best_idx`，其 max/min 判定方向与
# `HHVBARS._bars` 相反（既有行为：面板与 qlib 侧共用同一函数，故两端对账不暴露），
# 因此固定窗口 HHVBARS/LLVBARS **不能**复用 `dyn_bars_vec_seg`，必须用下面这套
# "最右极值"稀疏表，才能与 `_bars` 单调队列（等值保留新索引 = 取最右）逐位一致。






# 满窗口两套实现的切换阈值（2026-09-11 实测交叉点，n=354794 / 300 段 / 段长 1182）：
#   新版 van Herk/Gil-Werman：耗时与 N 无关、恒定 ~40ms，但小 N 有固定开销
#     （`axis=1` 逐行累积，行越短行数越多，行级开销累积）；
#   旧版 分块滑窗 argmax：O(n·N)，N 小反而更省。
#   N = 4/8/12/16/24/32/64/100/250/400 实测（新 / 旧，ms）：
#     44.5/26.1、43.9/32.6、41.4/39.0、42.5/44.9、44.9/55.7、42.7/65.3、
#     40.9/93.7、40.2/133.3、40.7/360.8、37.5/528.4
#   → 交叉点 N≈13，取 16 作阈值（两侧留余量，使本次改动整体不劣化）。




# ★ v2.10.86（2026-10-21 ✓）：`_rightmost_*` 三件套搬去 `ops_rightmost.py` ✓
#   （零外部依赖 + 有 10 条测试护着 ✓ ⇒ 拆巨型文件里最安全的一整块 ✓）
#   ⚠ 这里**回导**一下 ✓：`hhvbars_seg` / `llvbars_seg` 与本文件的历史调用点一行都不用改 ✓。
from .ops_rightmost import (  # noqa: E402
    _BARS_SLIDE_MAX_N,
    _build_rightmost_arg_sparse,
    _rightmost_arg_best,
    _rightmost_bars_seg,
)


# ★ v2.10.87（2026-10-21 ✓）：段感知族（10 个 `*_seg`）+ 它们共用的 6 个 helper
#   已搬去 `ops_seg.py` ✓（约 350 行，单向依赖 ✓，有 14 条'与非段版逐位一致'的测试 ✓）。
#   ⚠ 这里**回导**：非 seg 的 `_dyn_rmq_vec` / `_dyn_arg_idx_vec` / `_dyn_window_prefix`
#     也在用那几个 helper ✓ ⇒ 调用点一行都不用改 ✓。
from .ops_seg import (  # noqa: E402
    _build_sparse,
    _win_lens_vec,
    _dyn_geom,
    _make_dyn_ext,
    _dyn_best_idx_ext,
    _build_argmax_sparse,
    barsincen_vec_seg,
    filter_vec_seg,
    _dyn_scratch,
    _dyn_arange,
    dyn_ref_vec_seg,
    _dyn_rmq_vec_seg,
    dyn_window_sum_vec_seg,
    dyn_window_count_vec_seg,
    _dyn_arg_idx_vec_seg,
    dyn_bars_vec_seg,
    _arange_cached,
    _merge_adjacent_vals,
    _ARANGE_CACHE,
)


def hhvbars_seg(vals, nvals, seg_start, seg_end) -> np.ndarray:
    """HHVBARS 固定窗口 N=nvals 段感知（与 HHVBARS._bars(is_max=True) 逐位一致）。"""
    return _rightmost_bars_seg(vals, seg_start, nvals, True)


def llvbars_seg(vals, nvals, seg_start, seg_end) -> np.ndarray:
    """LLVBARS 固定窗口 N=nvals 段感知（与 HHVBARS._bars(is_max=False) 逐位一致）。"""
    return _rightmost_bars_seg(vals, seg_start, nvals, False)


# ---------------- 注册机制 ----------------

class FACTOR_END(ExpressionOps):
    """`FACTOR_END($factor)`：返回**该股在当前查询区间内最后一个有效值**（同形常数序列）。

    用途（v1.19.97）：实现**真正的前复权** —— `前复权价 = $close / FACTOR_END($factor)`
    （= `$close / $factor_end`，`$factor_end` = 每股最新交易日因子 ✓，见 `engine/adjust.py` docstring）。
    ⚠ 表达式层写不出"每股最后一天的因子"（"最后"是**跨时间**的 ✗）⇒ 只能做成算子 ✓。
    ⚠ **结果依赖查询区间**（同一表达式不同 start/end ⇒ 不同 `factor_end` ✓；qlib 缓存 key 含区间
      ⇒ 不会串 ✓）；语义与米筐 rqalpha / 聚宽一致（它们也按**回测区间末**归一化 ✓）。
    ⚠ `__str__` 保证 **round-trip**（v1.19.85 的教训 ✓：能算 ≠ 字符串能被重新解析 ✓）。
    """

    def __init__(self, feature):
        self.feature = feature
        super().__init__()

    def __str__(self):
        return "FACTOR_END(%s)" % self.feature

    def _load_internal(self, instrument, start_index, end_index, *args):
        s = pd.Series(np.asarray(self.feature.load(instrument, start_index, end_index, *args),
                                 dtype=np.float64))
        if s.empty:
            return s
        last = s.ffill().iloc[-1]                     # 区间内最后一个有效值
        if not np.isfinite(last):
            last = s.dropna().iloc[-1] if s.notna().any() else np.nan
        # ⚠ 必须返回 **ndarray**（qlib 的 `Expression.load` 返回 array ⇒ 子算子也要同类型 ✓；
        #   返回 `pd.Series` 会让上层算子/面板把它当异常值处理 ⇒ **整列失效、因子全空** ✗
        #   —— 实测踩到：`topk_curves` 直接为空（`n_days=None`、调仓日数 0 ✗））。
        return np.full(len(s), last, dtype=np.float64)


# ---------------- Sqrt / Mod：qlib **没有**这两个算子（v1.20.54 补） ----------------
#
# ⚠ 为什么必须自己实现 ✗（2026-09-23 用户报障「强龙起势：The operator [Sqrt] is not registered」✓）：
#   `parser/codegen.py` 的 `SQRT → "Sqrt"` / `MOD → "Mod"` 这两个映射**一直指向不存在的算子** ✗
#   —— 本机 qlib（源码版）注册表里**没有** `Sqrt`/`Mod`（实测：`hasattr(Operators,'Sqrt')` = False ✓；
#   而 `Exp`/`Log`/`Power`/`Not` 等在 ✓）。用户一写 `SQRT(...)`/`MOD(...)` 就在面板求值阶段
#   报 `is not registered` ✗（**编译能过、只在算的时候炸** ✗，所以之前没被发觉 ✓）。
#
# ★ 性能口径（用户明确要求 ✓）：直接用 **`np.sqrt` / `np.fmod`** 单次向量化 ✓
#   —— **不**走 `Power(X,0.5)`（多一层通用幂运算 ✗ 更慢、且负值/0 的边界行为不如 sqrt 直观 ✓）；
#   也不做逐元素 Python 循环 ✓。结构完全照抄同文件里已在生产使用的 `SGN`/`TRUNC`（单参 elementwise ✓、
#   返回 `pd.Series(index=…)` 与它们**同型** ✓ —— 注意 `FACTOR_END` 那处"必须返回 ndarray"的教训是针对
#   **广播成整段常量**的场景 ✓，本类是逐点运算，与 SGN/TRUNC 同类 ✓）。

class Sqrt(ExpressionOps):
    """SQRT(X)：平方根（`np.sqrt` ✓）。负值 ⇒ NaN（与 numpy/通达信一致 ✓）；NaN（停牌）保持 NaN ✓。"""

    def __init__(self, feature):
        self.feature = feature
        super().__init__()

    def _load_internal(self, instrument, start_index, end_index, *args):
        # ⚠⚠ 必须容忍**裸数字**参数 ✗（`SQRT(4)` 这种 ✓）—— 2026-09-23 真机求值验证抓到的：
        #   若直接 `self.feature.load(...)`，常量参数会 `AttributeError: 'int' object has no attribute 'load'` ✗。
        if not isinstance(self.feature, Expression):
            return np.sqrt(np.asarray(self.feature, dtype=float))
        series = self.feature.load(instrument, start_index, end_index, *args)
        return pd.Series(np.sqrt(series.to_numpy(dtype=float)), index=series.index)

    def __str__(self):
        return "Sqrt({})".format(self.feature)

    def get_longest_back_rolling(self):
        return self.feature.get_longest_back_rolling()

    def get_extended_window_size(self):
        return self.feature.get_extended_window_size()


class Mod(ExpressionOps):
    """MOD(A,B)：A 除以 B 的**余数**，**符号随 A**（= 通达信/益盟口径 ✓，用 `np.fmod` ✓）。

    ⚠ 别用 `np.mod`/`%` ✗ —— 那是"符号随 B"（Python/C 风格 ✓），与通达信不一致 ✗：
    例如 `MOD(-7,3)`：通达信/`fmod` ⇒ **-1** ✓；`np.mod` ⇒ 2 ✗。手册里写的也是"符号随 A" ✓。
    B=0 ⇒ NaN（不抛异常 ✓）；任一 NaN ⇒ NaN ✓。
    """

    def __init__(self, feature_left, feature_right):
        self.feature_left = feature_left
        self.feature_right = feature_right
        super().__init__()

    def __str__(self):
        return "Mod({},{})".format(self.feature_left, self.feature_right)

    def _load_internal(self, instrument, start_index, end_index, *args):
        # ⚠⚠ 容忍**裸数字**参数 ✗（`MOD(CLOSE,5)` 的第二参就是 int ✓）—— 2026-09-23 真机求值验证抓到的：
        #   原先无条件 `.load()` ⇒ `AttributeError: 'int' object has no attribute 'load'` ✗。
        #   qlib 自带的二元算子（`Greater/Less/Power` …）都支持裸原始值（文档：`Max($high,34)` ✓）⇒ 照它做 ✓。
        def _load(f):
            if isinstance(f, Expression):
                return f.load(instrument, start_index, end_index, *args)
            return f                                   # 裸数字：交给 numpy 广播 ✓

        left, right = _load(self.feature_left), _load(self.feature_right)
        # 索引取"是表达式的那一侧"（两侧都是表达式时取左侧 ✓）；两侧都是常量时无索引 ⇒ 返回 ndarray ✓
        idx = None
        for v in (left, right):
            if hasattr(v, "index"):
                idx = v.index
                break
        out = np.fmod(np.asarray(left, dtype=float), np.asarray(right, dtype=float))
        return pd.Series(out, index=idx) if idx is not None else out

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


class LN(ExpressionOps):
    """LN(X) / LOG(X)：**自然对数**（`np.log` ✓，与 qlib 内建 `Log` 同口径 ✓）。

    ⚠ 为什么必须补（v2.1.28，2026-10-11 ✓）：用户公式库里有**旧编译产物**写成 `LN(...)`
      （`-log(market_cap)` 这类原文在更早的编译链里落成的就是 `LN` ✗），而 qlib 注册表里
      **只有 `Log`、没有 `LN`** ✗ ⇒ 一旦走到 **qlib 回退路径**就直接
      `The operator [LN] is not registered` ✗ —— 更糟的是：这条报错把真正的根因
      （**面板求值先失败了** ✗）完全掩盖掉 ✗，害我们绕了很久才定位 ✓。
      ⇒ 注册 `LN`/`LOG`/`Ln` 三个名字，语义与 `parser/codegen.py` 的 `LOG/LN → Log`
      **完全一致**（自然对数 ✓）—— 别在这里改成 10 为底 ✗（那会让"负市值对数"口径悄悄变 ✓）。
    """

    def __init__(self, feature):
        self.feature = feature
        super().__init__()

    def _load_internal(self, instrument, start_index, end_index, *args):
        # ⚠ 与 Sqrt 同规矩：容忍**裸数字**参数（`LN(2)` ✓），否则常量参数会 AttributeError ✗
        if not isinstance(self.feature, Expression):
            return np.log(np.asarray(self.feature, dtype=float))
        series = self.feature.load(instrument, start_index, end_index, *args)
        with np.errstate(all="ignore"):              # ln(0) ⇒ -inf，不刷 RuntimeWarning ✓
            return pd.Series(np.log(series.to_numpy(dtype=float)), index=series.index)

    def __str__(self):
        return "LN({})".format(self.feature)

    def get_longest_back_rolling(self):
        return self.feature.get_longest_back_rolling()

    def get_extended_window_size(self):
        return self.feature.get_extended_window_size()


class LOG(LN):
    """`LOG(X)`：`LN` 的**别名**（通达信口径两者都是自然对数 ✓，与 codegen 一致 ✓）。"""

    def __str__(self):
        return "LOG({})".format(self.feature)


class Ln(LN):
    """`Ln(X)`：混合大小写别名（qlib 的算子名**区分大小写** ⇒ 三种都要注册 ✓）。"""

    def __str__(self):
        return "Ln({})".format(self.feature)


class RecurOut(ExpressionOps):
    """递推组（v2.4.0）在 **qlib 逐股路径**上的**占位算子**：一求值就报清楚的错 ✓。

    为什么不在 qlib 侧也实现一遍（用户 2026-10-15 定稿：先只走面板路径 ✓）：
      · 递推组最快的形态是**按 (日期 × 股票) 矩阵一次 O(N) 推进** ✓（`factors/recursion.py`，
        全 A 实测秒级 ✓）；而 qlib 的算子是**逐股调用**的 ⇒ 同样逻辑要再写一遍且慢一个量级 ✗，
        两套数值路径并存必然漂移 ✗（筹码 `COST/WINNER` 与横向统计 `mkt_*` 都为此选择了
        "物化字段 / 面板派生"而不是算子 ✓）；
      · 面板路径（**单因子测试 / 事件研究**）**完整支持** ✓；qlib 路径（模型特征 / 训练）
        暂不支持 ✗ —— 这里的作用是**别让用户看到 `The operator [RecurOut] is not registered` 天书** ✗。
    """

    def __init__(self, *args):
        self.recur_args = args
        super().__init__()

    def __str__(self):
        # qlib 用 str(self) 做进程内缓存 key ⇒ 必须带全部子表达式（否则互相污染 ✗）
        return "RecurOut({})".format(",".join(str(a) for a in self.recur_args))

    def _load_internal(self, instrument, start_index, end_index, *args):
        raise RuntimeError(RECUR_QLIB_HINT)

    def get_longest_back_rolling(self):
        return 0

    def get_extended_window_size(self):
        return (0, 0)


class RecurPrev(RecurOut):
    """`RecurPrev(j,k)`：递推组**组内**取上一根（由 `REF(变量,k)` 编译而来 ✓，用户不用写 ✓）。"""

    def __str__(self):
        return "RecurPrev({})".format(",".join(str(a) for a in self.recur_args))


class RecurCur(RecurOut):
    """`RecurCur(j)`：组内取成员 j 的**本根**值（= 同根引用**先定义**的成员 ✓，用户不用写 ✓）。"""

    def __str__(self):
        return "RecurCur({})".format(",".join(str(a) for a in self.recur_args))


RECUR_QLIB_HINT = (
    "这条公式用了**递推变量**（自己引用自己的上一根，如 `A:=REF(A,1)+1;`）✗\n"
    "  · 目前**只在面板路径**支持：**单因子测试 / 事件研究**里能照常算出来 ✓；\n"
    "  · 当作**模型特征**（训练 / 模型回测）暂时不支持 ✗ —— 那种场合走的是 qlib 逐股求值，\n"
    "    递推要在那里再实现一遍；需要的话请让开发排期 ✓。")

_ALL_OPS = [
    LN, LOG, Ln,         # v2.1.28：qlib 只有 `Log`，旧编译产物里的 `LN(...)` 一用就炸 ✗
    RecurOut, RecurPrev, RecurCur,  # v2.4.0：递推组占位（面板才是真实现 ✓，这里只负责报得清楚 ✓）
    FACTOR_END,          # v1.19.97：前复权用（$close / FACTOR_END($factor)）
    BARSLAST, BARSCOUNT, BARSSINCEN,
    DYN_MIN, DYN_MAX, DYN_COUNT, DYN_REF, DYN_SUM, DYN_MEAN, DYN_HHVBARS, DYN_LLVBARS,
    And, Or,          # 覆盖 qlib 内建：np.bitwise_and 对 float&bool 混输脆弱
    If,               # ★ v1.20.61：qlib 唯一的 Triple-wise 算子，np.where **不做索引对齐** ✗
    Gt, Ge, Lt, Le, Eq, Ne,   # ★ v1.20.62：比较类——两侧标签不一致时 pandas **直接抛** ✗

    Corr,             # v1.19.99：覆盖 qlib 内建，容忍 SR 删行导致的左右不等长（406 vs 400 ✗）
    SR,
    EMA_TDX,
    SGN, TRUNC, BETWEEN,
    Pow,
    Exp,              # v1.20.34：EXP(X)=e^X（向量化 `np.exp`，与 Sqrt/Log 同路径 ✓）
    Sqrt, Mod,        # v1.20.54：qlib **没有** Sqrt/Mod（`SQRT`/`MOD` 之前一用就炸 ✓）
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
