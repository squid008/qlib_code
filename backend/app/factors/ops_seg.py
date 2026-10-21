# -*- coding: utf-8 -*-
"""动态窗口算子的**段感知（`*_seg`）实现** ＋ 它们共用的 6 个内核 helper（★ v2.10.87 从 `ops_ext` 抽出）。

## 为什么是一整块
`ops_ext`(2027 行) 里这一族（约 350 行 ✓）**自成体系** ✓：
· 10 个 `*_seg` 把"整块多股"一次算完（`_by_group` 走这条 ✓），与单股的 `*_vec` 是**两套实现** ✓
  —— 二者的契约是"**逐位一致**"✓（`tests/test_ops_dyn_seg_consistency.py` 14 条在钉 ✓）；
· 6 个 helper 只依赖 `numpy` ✓（`_TLS` 是线程本地暂存 ✓），**不依赖本包任何其它模块** ✓
  ⇒ 搬出去是**单向依赖** ✓，不会循环导入 ✓。
⚠ 这 6 个 helper **非 seg 代码也在用** ✓（`_dyn_rmq_vec` / `_dyn_arg_idx_vec` / `_dyn_window_prefix` ✓）
  ⇒ 所以 `ops_ext` 里做了**回导** ✓，调用点一行不用改 ✓。

## 段边界怎么表示
`seg_start` / `seg_end` 都是**逐位置**数组（同一只股票的每个位置共享同一个起点 ✓），
`seg_end` **右开** ✓。构造方式见 `seg_start_arr()` / `seg_end_arr()`。
"""
from __future__ import annotations

import threading
from typing import cast

import numpy as np

_TLS = threading.local()

#: `np.arange(n)` 的小缓存（★ 随 `_arange_cached` 一起搬过来 ✓）——
#  ⚠ 它是**带注解的赋值** ✗，所以自动搬运脚本第一轮没认出它（只认普通赋值 ✓）
#  ⇒ 补搬时靠"静态检查报缺名"发现的 ✓（这类遗漏靠人工通读是查不出来的 ✗）。
_ARANGE_CACHE: "dict" = {}


def _build_sparse(vals: np.ndarray, func, k_max: int | None = None) -> np.ndarray:
    """构建稀疏表（RMQ 预处理），func 是 np.fmin / np.fmax（忽略 NaN）。

    返回 **2D 连续数组** `st[k, i]` = func(vals[i .. i + 2^k - 1])（尾部越界沿用原值），
    便于一次 fancy-index 查询（`st[js, l]`）替代"层列表 + 逐层布尔掩码"。

    `k_max`：只建到第 k_max 层（含）。查询端只读 `k <= floor(log2(max(lens)))` 这些层，
    固定建 `log2(n)+1` 层在长面板上是纯浪费 —— v1.18.26 实测 n=431028、窗口最长 400
    → 19 层里只有 9 层会被读到。
    """
    n = len(vals)
    if n == 0:
        return np.zeros((0, 0), dtype=float)
    k = int(np.log2(n)) + 1
    if k_max is not None:
        k = max(1, min(k, int(k_max) + 1))
    st = np.empty((k, n), dtype=float)
    st[0] = vals
    for j in range(1, k):
        prev = st[j - 1]
        half = 1 << (j - 1)
        st[j, : n - half] = func(prev[: n - half], prev[half:])
        st[j, n - half :] = prev[n - half :]
    return st


def _win_lens_vec(nvals: np.ndarray) -> np.ndarray:
    """把动态窗口序列向量化规整为 int 数组（等价逐元素 _win_len）：

    NaN → 1；<1 → 1；其余 int(v)（正数向零截断 = floor）。
    """
    nv = np.where(np.isnan(nvals), 1.0, nvals)
    n = np.floor(nv).astype(np.int64)
    return np.maximum(n, 1)


def _dyn_geom(nvals, seg_start, n):
    """DYN 窗口几何（v1.18.40）：返回 `(js, l1, l2)`。

    `lens_i = min(N_i, i - seg_start_i + 1)`（`seg_start=None` → 按段起点 0；N_i 由
    `_win_lens_vec` 的语义给出：NaN→1、<1→1、floor 截断），
    `js_i = floor(log2(lens_i))`、`l1_i = i - lens_i + 1`、`l2_i = i - 2^js_i + 1`。

    **性能**：原实现是四行链式表达式，逐条都要分配一个 n 长临时数组 ——
    `where / floor / astype / maximum / (idx-seg_start+1) / minimum / log2 / floor /
    astype / arange / (1<<js) / (idx-lens+1)` 共 **11 个**（n=3.17M 时约 275MB）。
    实测「窗口几何」占单次查询 291ms 中的 ~200ms，而它**只依赖 (nvals, seg_start, n)**。
    本函数改为**全程 `out=` 原地写**，只用 5 个缓冲（mask/A/B/C/D）+ 缓存的 arange。

    交错 A/B（`ai_test/bench_query.py`，8 轮，取**比值中位**以抑制 ±18% 跑间抖动）：
    整查询 **1.27×**（各轮 1.07~1.57），结果**逐位相同**。
    ⚠ `maximum(N, 1)` 必须放在**转 int 之后**（与 `_win_lens_vec` 同序）：`floor(±inf)`
    转 int64 会得到 INT64_MIN，若先在浮点上取 max 再转，会留下 INT64_MIN 而错。
    """
    mask = np.empty(n, dtype=bool)
    A = np.empty(n, dtype=np.float64)
    B = np.empty(n, dtype=np.int64)
    C = np.empty(n, dtype=np.int64)
    D = np.empty(n, dtype=np.int64)
    idx = _arange_cached(n)
    np.isnan(nvals, out=mask)
    np.copyto(A, nvals)
    A[mask] = 1.0                          # NaN → 1
    np.floor(A, out=A)
    np.copyto(C, A, casting="unsafe")      # 截断（值域已非负，等价 astype）
    np.maximum(C, 1, out=C)                # C = N（int64）
    if seg_start is None:
        np.add(idx, 1, out=B)              # B = i + 1（窗口左端截到数组起点）
    else:
        np.subtract(idx, seg_start, out=B)
        np.add(B, 1, out=B)                # B = i - seg_start + 1
    np.minimum(C, B, out=C)                # C = lens
    np.subtract(idx, C, out=B)
    np.add(B, 1, out=B)                    # B = l1
    np.log2(C, out=A)
    np.floor(A, out=A)
    np.copyto(D, A, casting="unsafe")      # D = js
    np.left_shift(1, D, out=C)
    np.subtract(idx, C, out=C)
    np.add(C, 1, out=C)                    # C = l2
    return D, B, C


def _make_dyn_ext(vals: np.ndarray) -> np.ndarray:
    """哨兵扩展数组（v1.18.36）：index 0 = NaN 哨兵，index i+1 = vals[i]。

    供 `_dyn_best_idx_ext` 用 `vals_ext[idx + 1]` 一次花式索引取候选值 —— 无效下标 -1
    直接落到 index 0 的 NaN 哨兵，等价于原实现的 `np.where(a_ok, vals[max(ai,0)], nan)`
    （省掉每层合并的 `np.maximum` ×2 与 `np.where` ×2）。
    **前置不变量**：传入的下标只可能是 -1（无效）或 [0, n)（有效），与建表 `st[0]` 一致。
    """
    out = np.empty(vals.size + 1, dtype=np.float64)
    out[0] = np.nan
    out[1:] = vals
    return out


def _dyn_best_idx_ext(ai: np.ndarray, bi: np.ndarray, vals_ext: np.ndarray,
                      is_max: bool) -> np.ndarray:
    """`_dyn_best_idx` 的扩展数组版（v1.18.36，语义逐位等价）。

    与 `_dyn_best_idx` 唯一区别：候选值由 `vals_ext[idx + 1]` 一次取出（NaN 哨兵承载
    「无效下标」语义），省掉 `np.maximum(ai, 0)` 与 `np.where(..., nan)` 各两次；
    NaN 判定改用 `av != av`（免一次 `np.isnan` 分派）。比较/等值取右等逻辑一字未动。
    """
    a_ok = ai >= 0
    b_ok = bi >= 0
    av = vals_ext[ai + 1]
    bv = vals_ext[bi + 1]
    a_nan = av != av
    b_nan = bv != bv
    if is_max:
        b_better = (bv > av) | (a_nan & ~b_nan)
    else:
        b_better = (bv < av) | (a_nan & ~b_nan)
    tie = av == bv
    b_wins = b_ok & ((~a_ok) | b_better | (tie & (bi > ai)))
    return np.where(b_wins, bi, ai)


def _build_argmax_sparse(vals: np.ndarray, is_max: bool, k_max: int | None = None) -> np.ndarray:
    """稀疏表（RMQ）变体：每层存"区间极值的最右下标"，同值取更右（等值取最近）。

    与 `_build_sparse`（存极值）同构，同样返回 **2D 连续数组** 且支持 `k_max` 只建所需层。
    NaN 位用 -1 占位。

    性能演进：
      · v1.18.36：层合并改用**哨兵扩展数组**（`_make_dyn_ext`）—— 免去每层
        `np.maximum` ×2 / `np.where` ×2，但候选值仍靠 `vals_ext[idx+1]` **花式索引**
        （随机 gather）。
      · v1.18.39（本版）：**并行维护一张极值表**，层合并改为读它的**连续切片**，
        彻底去掉 gather；合并逻辑同时简化（见 `_merge_adjacent_vals`）；下标表降为
        **int32**（n < 2^31 恒成立）。实测同形状（n=3172661、k_max=6、22.2M 元素）：
        **718ms → 290ms（2.2~2.3×）**，与旧实现**逐位相同**。

    ⚠ 内存**更省**：极值表只需**相邻两层**（ping-pong 两个 n 长缓冲），下标表由 int64
    降 int32 ⇒ 峰值从 ~250MB（int64 表 178MB + 25MB 哨兵 + 逐层临时量）降到 ~160MB。
    """
    n = len(vals)
    if n == 0:
        return np.zeros((0, 0), dtype=np.int64)
    k = int(np.log2(n)) + 1
    if k_max is not None:
        k = max(1, min(k, int(k_max) + 1))
    dt = np.int32 if n < (1 << 31) else np.int64
    si = np.empty((k, n), dtype=dt)
    si[0] = np.where(np.isnan(vals), dt(-1), np.arange(n, dtype=dt))
    if k > 1:
        f = np.fmax if is_max else np.fmin
        # 极值表 ping-pong：只需第 j-1 层，不必存满 k 层
        vb = np.array(vals, dtype=np.float64)      # 第 0 层极值 = 原值（复制一份作可写缓冲）
        vc = np.empty(n, dtype=np.float64)
        for j in range(1, k):
            pi = si[j - 1]
            half = 1 << (j - 1)
            m = n - half
            av, bv = vb[:m], vb[half:half + m]     # 同一缓冲的相邻两半（与旧实现一致）
            vc[:m] = f(av, bv)                     # 极值：单遍顺序流（≈4.8 ns/元素）
            vc[m:] = vb[m:]
            si[j, :m] = _merge_adjacent_vals(pi[:m], pi[half:half + m], av, bv, is_max, dt)
            si[j, m:] = pi[m:]
            vb, vc = vc, vb
    return si


def barsincen_vec_seg(vals: np.ndarray, nvals: float | None, seg_start, seg_end) -> np.ndarray:
    """BARSSINCEN 段感知：N 窗内最早成立距当前周期数（窗口下界 clip 到段起点）。"""
    vals = np.asarray(vals, dtype=float)
    # ⚠ `np.isscalar` 是 **TypeGuard** ✗ —— 它会把类型"收窄"成 numpy 的一堆标量联合 ✓，
    #   反而让 `int(nvals)` 报错 ✓。`cast(float, ...)` 只是**声明意图** ✓（运行时零影响 ✓）。
    N = max(1, int(cast(float, nvals))) if nvals is not None and np.isscalar(nvals) else 1
    n = len(vals)
    i = np.arange(n)
    mask = (vals != 0) & ~np.isnan(vals)
    pos = np.flatnonzero(mask)
    out: np.ndarray = np.zeros(n, dtype=float)
    if pos.size:
        lo = np.maximum(seg_start, i - N + 1)
        j = np.searchsorted(pos, lo, side="left")
        in_win = j < pos.size
        earliest = np.where(in_win, pos[np.minimum(j, pos.size - 1)], n + 1)
        ok = in_win & (earliest <= i)
        out = np.where(ok, i - earliest, 0.0)
    return out


def filter_vec_seg(vals: np.ndarray, nvals: float | None, seg_start, seg_end) -> np.ndarray:
    """FILTER 段感知：逐段贪心抑制（只在触发位置上前进，段内 reopen 重置）。

    与逐段 `filter_vec` 逻辑完全一致（reopen 用全局下标：段起点即段内 reopen=0）。
    """
    vals = np.asarray(vals, dtype=float)
    # ⚠ `np.isscalar` 是 **TypeGuard** ✗ —— 它会把类型"收窄"成 numpy 的一堆标量联合 ✓，
    #   反而让 `int(nvals)` 报错 ✓。`cast(float, ...)` 只是**声明意图** ✓（运行时零影响 ✓）。
    N = max(1, int(cast(float, nvals))) if nvals is not None and np.isscalar(nvals) else 1
    n = len(vals)
    out = np.zeros(n, dtype=float)
    mask = (vals != 0) & ~np.isnan(vals)
    pos = np.flatnonzero(mask)
    if pos.size == 0:
        return out
    starts = np.flatnonzero(np.r_[True, seg_start[1:] != seg_start[:-1]])
    ends = np.r_[starts[1:], n]
    # ⚠ 明确成 1D 数组：`searchsorted` 的返回类型随入参而变（标量/数组）✗
    #   ⇒ mypy 认为 `lo[g]` 是在给标量取下标 ✓。`atleast_1d` 不改运行时结果 ✓。
    lo = np.atleast_1d(np.searchsorted(pos, starts, side="left"))
    hi = np.atleast_1d(np.searchsorted(pos, ends, side="left"))
    for g in range(len(starts)):
        reopen = int(starts[g])
        for p in pos[lo[g]:hi[g]]:
            if p >= reopen:
                out[p] = 1.0
                reopen = int(p) + N
    return out


def _dyn_scratch(key: str, n: int, dtype):
    """取线程局部 scratch 缓冲（容量不足或 dtype 变化则重建；返回的可能更大缓冲的视图）。"""
    st = getattr(_TLS, "dyn", None)
    if st is None:
        st = _TLS.dyn = {}
    b = st.get(key)
    if b is None or b.size < n or b.dtype != dtype:
        b = np.empty(n, dtype=dtype)
        st[key] = b
    return b[:n]


def _dyn_arange(n: int) -> np.ndarray:
    """缓存 `arange(n)`：每次调用重建 n×8 字节数组纯属浪费。只读，天然线程安全。"""
    st = getattr(_TLS, "ar", None)
    if st is None:
        st = _TLS.ar = {}
    a = st.get(n)
    if a is None:
        a = np.arange(n, dtype=np.int64)
        st[n] = a
        while len(st) > 4:                      # 兜底：只保留最近几个 n
            st.pop(next(iter(st)))
    return a


def dyn_ref_vec_seg(vals: np.ndarray, nvals, seg_start, seg_end) -> np.ndarray:
    """DYN_REF 段感知：全局下标回退 N_i，越出本段 [seg_start, seg_end) 则 NaN。

    性能（v1.18.28）：DYN_REF 曾是整次求值的**最大单项**（126 次 × 9.9ms ≈ 12.8%）。
    三处等价改动，n=354794 实测（随机动态 N / 恒定 N）：**7.42→4.05ms / 7.23→3.34ms**：
      · 去掉冗余 `np.trunc` —— float→int64 转换本身就**向零截断**，原先 trunc 与 astype
        各扫一遍全表；
      · 去掉**布尔掩码三连** —— `j[ok]` 压缩、`vals[j[ok]]` 非连续 gather、`out[ok]=`
        掩码写回，每处都要一次全表 nonzero 扫描；改为全量 `np.take(mode="clip")`
        （越界先 clip 保安全，随后用掩码置 NaN）+ 一次 `np.where`；
      · 复用**线程局部** scratch 缓冲 + 缓存 `arange(n)`，消除每趟多 MB 临时数组的
        malloc/free —— 微基准显示这一项独占了一半以上的收益。
    返回值始终是新数组（`np.where` 产出），不会把 scratch 泄露给调用方。
    """
    n = len(vals)
    nvf = _dyn_scratch("ref_nvf", n, np.float64)
    np.copyto(nvf, nvals)
    mb = _dyn_scratch("ref_mask", n, bool)
    np.isnan(nvf, out=mb)
    np.copyto(nvf, 0.0, where=mb)             # NaN→0（避免整型转换得到未定义值）
    nvi = _dyn_scratch("ref_nvi", n, np.int64)
    np.copyto(nvi, nvf, casting="unsafe")     # float→int64 = 向零截断（等价原 trunc+astype）
    j = _dyn_scratch("ref_j", n, np.int64)
    np.subtract(_dyn_arange(n), nvi, out=j)
    g = _dyn_scratch("ref_g", n, np.float64)
    np.take(vals, j, out=g, mode="clip")
    bad = _dyn_scratch("ref_bad", n, bool)
    tb = _dyn_scratch("ref_tmp", n, bool)
    np.less(j, seg_start, out=bad)
    np.greater_equal(j, seg_end, out=tb)
    np.logical_or(bad, tb, out=bad)
    return np.where(bad, np.nan, g)


def _dyn_rmq_vec_seg(vals, nvals, func, seg_start) -> np.ndarray:
    """DYN_MIN/MAX 段感知：全局稀疏表 + 窗口下界 clip 到段起点。

    仅读取"完整落在某段内"的稀疏表区间（l, r 均在段内 → 其 2 个子区间也都在段内），
    故与逐段建表结果一致；fmin/fmax 满足结合律，建表顺序不影响数值。

    v1.18.26（T3）：改为「按需建层 + 一次 fancy-index 查询」。实测 n=431028、窗口最长 400
    → 原先建 19 层（实际只用得到 9 层），且查询循环在 10 个空层上空转。
    """
    n = len(vals)
    if n == 0:
        return np.zeros(0, dtype=float)
    js, l1, l2 = _dyn_geom(nvals, seg_start, n)     # v1.18.40：几何改走缓冲复用
    st = _build_sparse(vals, func, int(js.max()))
    a = st[js, l1]
    return func(a, st[js, l2], out=a)


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
    """段感知的"最右极值下标"稀疏表查询（返回全局下标，落在段内）。

    v1.18.26（T3）：同 `_dyn_rmq_vec_seg` —— 改为「按需建层 + 一次 fancy-index 查询」。
    """
    n = len(vals)
    if n == 0:
        return np.zeros(0, dtype=np.int64)
    js, l1, l2 = _dyn_geom(nvals, seg_start, n)     # v1.18.40：几何改走缓冲复用
    st = _build_argmax_sparse(vals, is_max, int(js.max()))
    return _dyn_best_idx_ext(st[js, l1], st[js, l2], _make_dyn_ext(vals), is_max)


def dyn_bars_vec_seg(vals, nvals, is_max, seg_start) -> np.ndarray:
    """DYN_HHVBARS/LLVBARS 段感知：全局下标差 i - j（j 在段内）。"""
    n = len(vals)
    if n == 0:
        return np.zeros(0, dtype=float)
    j = _dyn_arg_idx_vec_seg(vals, nvals, is_max, seg_start)
    i = np.arange(n)
    ok = (j >= 0) & ~np.isnan(vals)
    return np.where(ok, (i - j).astype(float), np.nan)




def _arange_cached(n: int) -> np.ndarray:
    """n 长的 `0..n-1` 下标数组（缓存）。

    面板求值里 n 只取决于股票池×区间，同一轮内反复取同一个 n；`np.arange` 每次
    要分配 8n 字节（n=3.17M 时 25MB、约 8ms）。缓存后零成本。
    **只读共享**（各调用点只把 idx 当输入，从不写入），故进程内共享安全。
    """
    a = _ARANGE_CACHE.get(n)
    if a is None:
        a = np.arange(n, dtype=np.int64)
        if len(_ARANGE_CACHE) > 8:      # 兜底上限
            _ARANGE_CACHE.clear()
        _ARANGE_CACHE[n] = a
    return a



def _merge_adjacent_vals(ai, bi, av, bv, is_max, dt):
    """稀疏表**相邻两半**的合并（v1.18.39）：6 遍逐元素运算，**不做 gather**。

    **前置不变量**（本函数成立的前提，也是它比 `_dyn_best_idx_ext` 激进的原因）：
    第 j 层的左右两半是**相邻区间** `[p, p+half)` 与 `[p+half, p+2half)`，且
    `si[j-1][q] ∈ [q, q+2^(j-1)-1] ∪ {-1}`（对 j 归纳可证，含尾部「沿用 prev」的近似位）。
    ⇒ 两半下标**只要都有效必有 `ai < bi`** ⇒ `_dyn_best_idx_ext` 里
    `(tie & (bi > ai))` 的 `bi > ai` **恒真**，可删；再与 `b_better` 合并即 `bv >= av`
    （等值自然取右）。

    真值表（a/b 是否有效）：
      VV → `bv >= av`（等值取 b ✓）｜NV → `a_nan & b_ok` ✓｜VN → False → 取 a ✓
      ｜NN → False → 取 a(=-1) ✓。`±inf`：`inf >= inf` 真 → 取右 ✓。

    候选值 `av`/`bv` 由调用方以**连续切片**给出（极值表），故无 `vals_ext[ai+1]` 的
    随机 gather —— 那正是 v1.18.36 实现在 25MB 数组上每层两次、共 ~2.7 亿次随机访问
    的来源。实测 22.2M 元素：28.9~33.4 ns/元素 → **13.1~14.3 ns/元素（2.2~2.3×）**，
    且与旧实现**逐位相同**（`ai_test/bench_argmax.py` 五变体对拍全 True）。
    """
    a_nan = av != av
    b_ok = bv == bv
    win = ((bv >= av) if is_max else (bv <= av)) | (a_nan & b_ok)
    return np.where(win, bi, ai).astype(dt, copy=False)


