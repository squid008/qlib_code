# -*- coding: utf-8 -*-
"""分段 / 滚动算子（★ v2.9.57，2026-10-19 ✓ —— 从 `panel_expr.py` 搬出的第二块 ✓）。

`panel_expr.py` 仍是全仓最大文件 ✗，但这一组是**完全自包含**的一块 ✓：
用脚本算过它们的**传递闭包**（实测 ✓），额外依赖只有 `numpy` / `pandas` / `contextlib`
与一个日志兜底 ✓ —— 没有任何"再往 panel_expr 里伸手"的引用 ✓。

包含 ✓：`_seg_roll` ✓、`_seg_roll_vanherk` ✓（van Herk/Gil-Werman 滑窗极值 ✓）、
`_seg_window_op` ✓、`_segment_bnd` ✓、`_by_group` ✓、`_seg_arrays` / `_seg_grid` ✓。

⚠ 源码**按原始行序、一字未改** ✓（这些算子对 NaN / 段边界很敏感 ✓，改写等于换语义 ✗）；
  `panel_expr` 里用 `from .seg_ops import …` **显式**引回 ✓（不是 `import *` ✗）。
"""

import numpy as np
import pandas as pd

# ★ v2.9.57：这两个缓存**归本模块所有** ✓ —— `panel_expr.reset_caches()` 也清它们 ✓
#   （dict 是同一个对象 ✓，`clear()` 跨模块照样有效 ✓）。
_SEG_CACHE: "dict" = {}
_SEG_RC_CACHE: "dict" = {}


def _log_swallowed(where, exc):
    """吞异常留痕 ✓（与 `panel_expr` / `chip_turn` 里那两份同语义 ✓，本模块自给自足 ✓）。"""
    import contextlib
    import logging as _logging

    with contextlib.suppress(Exception):
        lg = globals().get("logger") or globals().get("_LOGGER") or _logging.getLogger(__name__)
        lg.debug("[吞] %s: %r", where, exc)


def _seg_window_op(op: str, v: np.ndarray, N: int, q=None) -> np.ndarray:
    """单股段内核：IdxMax/IdxMin/Quantile/Rank/Slope/Rsquare/Resi。N>0 用 rolling，N=0 用 expanding。"""
    vv = np.asarray(v, dtype=np.float64)
    s = pd.Series(vv)
    if N <= 0:
        rolling = s.expanding(min_periods=1)
    else:
        rolling = s.rolling(int(N), min_periods=1)
    if op in ("IdxMax", "IdxMin"):
        is_max = op == "IdxMax"
        out = rolling.apply(lambda x: (x.argmax() if is_max else x.argmin()) + 1, raw=True)
        return out.to_numpy(dtype=np.float64)
    if op == "Quantile":
        return rolling.quantile(float(q)).to_numpy(dtype=np.float64)
    if op == "Rank":
        if hasattr(rolling, "rank"):
            return rolling.rank(pct=True).to_numpy(dtype=np.float64)
        # 旧 pandas 回退（同 qlib）：窗口内非 NaN 的百分位
        from scipy.stats import percentileofscore

        n = len(vv)
        nn = N if N > 0 else n
        out = np.full(n, np.nan)
        for i in range(n):
            w = vv[max(0, i - nn + 1): i + 1]
            wv = w[~np.isnan(w)]
            if wv.size == 0 or np.isnan(wv[-1]):
                continue
            out[i] = percentileofscore(wv, wv[-1]) / 100.0
        return out
    if op in ("Slope", "Rsquare", "Resi"):
        from qlib.data._libs.expanding import expanding_resi, expanding_rsquare, expanding_slope
        from qlib.data._libs.rolling import rolling_resi, rolling_rsquare, rolling_slope

        if N <= 0:
            fn = {"Slope": expanding_slope, "Rsquare": expanding_rsquare, "Resi": expanding_resi}[op]
            arr = np.asarray(fn(vv), dtype=np.float64)
        else:
            fn = {"Slope": rolling_slope, "Rsquare": rolling_rsquare, "Resi": rolling_resi}[op]
            arr = np.asarray(fn(vv, int(N)), dtype=np.float64)
        if op == "Rsquare":
            # qlib Rsquare 额外置 NaN：窗口内 X 滚动 std ≈ 0（atol 2e-05）
            sd = rolling.std().to_numpy(dtype=np.float64)
            arr = arr.copy()
            arr[np.isclose(sd, 0, atol=2e-05)] = np.nan
        return arr
    raise ValueError(f"panel_expr 不支持 {op}")

def _seg_arrays(idx, n):
    """按 index 对象缓存 (bnd, seg_start, seg_end)。

    性能（v1.18.22）：`_by_group` 在一次求值内被调上百次（每个动态节点一次），
    每次都重算 `_segment_bnd(idx)` 与 `seg_start_arr/seg_end_arr`（后者是
    `np.repeat` 全表分配，cProfile 实测 1747 次 / 0.178s ≈ 3.8%）。而同一个
    evaluator 内 `idx` 是**稳定对象**（`_active_index` 已缓存），段划分完全不变，
    故可复用。**缓存键用 `(id(idx), n)` 且缓存中持有 idx 引用** —— 持有引用保证
    对象不被回收、`id` 不会被复用，从而杜绝"地址复用导致取错缓存"的隐患
    （idx 本就在 evaluator 生命周期内常驻，不产生额外内存）。缓存由 `eval_expr`
    开头清空（与 `_node_cache` 同生命周期）。
    """
    key = (id(idx), n)
    hit = _SEG_CACHE.get(key)
    if hit is not None and hit[0] is idx:
        return hit[1], hit[2], hit[3]
    from . import ops_ext

    bnd = _segment_bnd(idx)
    ss = ops_ext.seg_start_arr(bnd, n)
    se = ops_ext.seg_end_arr(bnd, n)
    if len(_SEG_CACHE) > 64:      # 兜底上限（正常每表达式只有 1~2 个 idx）
        _SEG_CACHE.clear()
    _SEG_CACHE[key] = (idx, bnd, ss, se)
    return bnd, ss, se

def _seg_grid(idx, n: int):
    """段→矩阵布局索引（缓存）：(row, col, counts, seg_pos)。row[i]=i 在段内偏移、
    col[i]=段序号、counts[c]=第 c 段行数、seg_pos 为各段在扁平数组中的起点（含末位 n）。"""
    key = (id(idx), n)
    hit = _SEG_RC_CACHE.get(key)
    if hit is not None and hit[0] is idx:
        return hit[1], hit[2], hit[3], hit[4]
    _bnd, ss, _se = _seg_arrays(idx, n)         # 段边界与 _by_group 共用同一份缓存
    row = np.arange(n, dtype=np.int64) - ss
    newseg = np.empty(n, dtype=bool)
    newseg[0] = True
    if n > 1:
        np.not_equal(ss[1:], ss[:-1], out=newseg[1:])
    col = np.cumsum(newseg, dtype=np.int64) - 1
    nseg = int(col[-1]) + 1 if n else 0
    counts = np.bincount(col, minlength=nseg).astype(np.int64) if n else np.zeros(0, np.int64)
    seg_pos = np.concatenate([[0], np.cumsum(counts)]).astype(np.int64)
    if len(_SEG_RC_CACHE) > 64:   # 兜底上限（正常每表达式只有 1~2 个 idx）
        _SEG_RC_CACHE.clear()
    _SEG_RC_CACHE[key] = (idx, row, col, counts, seg_pos)
    return row, col, counts, seg_pos

def _seg_roll_vanherk(arr: np.ndarray, N: int, func: str, idx) -> np.ndarray:
    """`_seg_roll` 的 **max/min 快路**（v1.18.44）：固定窗口 van Herk / Gil-Werman。

    等价于逐段 `pd.Series.rolling(N, min_periods=1).max()/min()`，**逐位相同**
    （`ai_test/test_seg_roll.py` A 旧矩阵 vs 新快路 864 例｜A2 逐段 pandas vs 新 450 例｜
    B 朴素参考｜D 跨段污染｜E ±inf 专项；另 `bench_segroll_max.py` 在 n=3.17M 真实形状上
    验「old / rmq / vanherk / 逐段 pandas」**四者逐位一致**）。

    **算法**：窗口长度恒为 N ⇒ 以 **N 为块长、按 index 0 全局对齐**分块；块内做前向
    `prefix` 与后向 `suffix`，则长度恰为 N 的窗口 `[l, i]`（`l = i − N + 1`）满足
    `f(suf[l], pre[i])`。

    **为什么全局网格也不会取到邻段的值**（本算法最关键的一点）：
      · l 与 i **同块** ⇒ 由 `i − l = N − 1` 与块长 N 推得窗口**恰等于该块**
        ⇒ `suf[l]`/`pre[i]` 恰好就是窗口本身；
      · i 在 l 的**下一块** ⇒ `suf[l]` 止于块尾（= 下一块首 − 1 ≤ i）、`pre[i]` 始于
        该块首（≥ l）⇒ 两者都是窗口的**子集**。
      ⇒ 两个子集都落在窗口内，而窗口在段内 ⇒ 不会跨段（D 组实测确认）。

    **截断窗口**（左端被段起点截断，即每段前 `N−1` 行）不能用块公式（同块公式会把块内
    更右的值算进来），单独用段内 `f.accumulate` 覆盖 ⇒ 代价 ≤ `段数 × (N−1)` 个元素。

    **性能**（v1.18.44，n=3.17M / 1980 段等长 / N∈{14,34,120}，`bench_segroll_max.py`
    逐轮交错取比值中位）：**2.91~3.72×**（135 → 45 ns/元素）—— 而 v1.18.44 试过的
    「分段 RMQ」只有 1.0×（N 大 ⇒ 稀疏表 7 层 + 178MB 表随机 gather，见架构手册 #6）。
    **内存**：`M`+`suf` 各 n 长（≈50MB），比稀疏表的 ~178MB 省得多。

    ⚠ `M` 里 `nb×N − n` 个尾部 NaN 位与「段起点紧邻的错位窗口」都会被后续覆盖，
    故**必须先算 `suf` 再原地累加 `M`**（顺序不能反）。
    """
    n = arr.shape[0]
    if n == 0:
        return np.full(0, np.nan, dtype=np.float64)
    # pandas `Rolling._prep_values` 把 ±inf 当 NaN（use_inf_as_na 遗留行为）——必须复刻
    vals = np.where(np.isinf(arr), np.nan, arr)
    if N <= 1:
        return vals
    f = np.fmax if func == "max" else np.fmin
    nb = (n + N - 1) // N
    M = np.full((nb, N), np.nan, dtype=np.float64)
    M.reshape(-1)[:n] = vals
    # ① 后向（先算：它要读未被覆盖的 M）
    suf = np.ascontiguousarray(f.accumulate(M[:, ::-1], axis=1)[:, ::-1]).reshape(-1)
    # ② 前向（原地覆盖 M）→ 直接用它的前 n 个当输出缓冲
    f.accumulate(M, axis=1, out=M)
    out = M.reshape(-1)[:n]
    if n > N - 1:
        f(out[N - 1:], suf[:n - N + 1], out=out[N - 1:])          # 长度恰为 N 的窗口
    # ③ 逐段覆盖「截断窗口」（每段前 min(N−1, 段长) 行）
    bnd = _seg_arrays(idx, n)[0]                                  # 走缓存，零成本
    for k in range(len(bnd) - 1):
        s, e = int(bnd[k]), int(bnd[k + 1])
        m = min(N - 1, e - s)
        # ⚠ 必须是 `m >= 1`（不是 `> 1`）：`m == 1`（N == 2，或段长 == 1）时那唯一的
        #   截断位（段首行本身）也要覆盖 —— 它上方刚被「长度恰为 N 的窗口」公式写过，
        #   会取到段外的值。此 bug 在全尺度检查里看不见（N≥14 且段长 1602 ⇒ m≥13），
        #   是对拍网的短段/小 N 用例抓出来的。
        if m >= 1:
            out[s:s + m] = f.accumulate(vals[s:s + m])
    return out

def _seg_roll(arr: np.ndarray, N: int, func: str, idx) -> np.ndarray:
    """按段独立的 pandas rolling（等价 `groupby(level=0).rolling(N, min_periods=1).<f>()`）。

    做法（v1.18.30）：把各段**当作矩阵的列**（段尾 NaN 补齐），对整块做**一次**
    `DataFrame.rolling(N, min_periods=1)` —— pandas 的 2D 滚动内核**逐列独立累积**
    （每列有自己的累积链与 NaN 重置），所以与"逐段 rolling"**逐位相同**
    （实测 mean/max/min/sum/std/var 六个函数全部逐位相同），而 **2.1~2.6×** 快。

    ⚠ **不能**改成「全表 rolling + 只修段首 N-1 行」：pandas 的 mean/std/sum/var 是
    **增量累积、历史相关**的 —— 实测即使只看「距段首 ≥ N-1 的不跨段安全区」，全表值
    与逐段值仍有 **33 万处**不同（对拍 FAIL）；只有 max/min 是窗口局部的（那条路可行，
    但收益更小：56.3ms vs 本方案 25ms）。
    """
    n = arr.shape[0]
    if n == 0:
        return np.full(0, np.nan, dtype=np.float64)
    if N >= 1 and func in ("max", "min"):
        return _seg_roll_vanherk(arr, N, func, idx)      # v1.18.44 快路（van Herk，3~3.7×）
    out = np.full(n, np.nan, dtype=np.float64)
    row, col, counts, seg_pos = _seg_grid(idx, n)
    nseg = counts.shape[0]
    # 分块：控制「块内最长段 × 块内段数」规模，避免"一只超长 + 一堆超短"时矩阵爆内存
    budget = max(n, 1) * 4
    c0 = 0
    while c0 < nseg:
        c1 = c0
        m = 0
        while c1 < nseg:
            m2 = m if int(counts[c1]) < m else int(counts[c1])
            if c1 > c0 and (c1 + 1 - c0) * m2 > budget:
                break
            m = m2
            c1 += 1
        # 段在扁平数组中连续 → 块对应一段连续区间，直接切片（无需布尔掩码）
        a0, a1 = int(seg_pos[c0]), int(seg_pos[c1])
        r = row[a0:a1]
        c = col[a0:a1] - c0
        mat = np.full((m, c1 - c0), np.nan, dtype=np.float64)
        mat[r, c] = arr[a0:a1]
        res = getattr(pd.DataFrame(mat).rolling(N, min_periods=1), func)().to_numpy(
            dtype=np.float64)
        out[a0:a1] = res[r, c]
        c0 = c1
    return out

def _segment_bnd(idx) -> np.ndarray:
    """计算按 level0（instrument）分组的段边界数组 bnd。

    第 k 段为 bnd[k]:bnd[k+1]，即"相邻 level0 标签变化处"的切分。

    性能（v1.18.4，巨型公式最大瓶颈）：原实现用
    `idx.get_level_values(0).to_numpy()` 得到 **object 字符串数组**，再做
    `np.not_equal(lv[1:], lv[:-1])` 相邻比较——object dtype 会退化成逐元素的 Python
    对象富比较（93000 行 ≈ 60ms）。而 `_by_group` 在一次 CUP_POOL 求值中被调用上百次
    （每个 dyn 节点一次），累计 8s+（单块 42% 的"逐段调度"开销）——这是真正的热点，
    而非逐段循环本身（内部 ops_ext 内核早已全向量化）。

    改用 `MultiIndex.codes[0]`（int 数组，整数比较 ~0.1ms）：codes 唯一标识 label，
    相邻 codes 相等 ⟺ 同 instrument，段边界与标签比较**逐位等价**（仅速度不同，
    数值结果不变）。codes 含 -1（缺失）或非 MultiIndex 时回退原标签比较路径。
    """
    n = len(idx)
    if n == 0:
        return np.zeros(1, dtype=np.int64)
    codes = None
    if isinstance(idx, pd.MultiIndex):
        try:
            c = np.asarray(idx.codes[0])
        except Exception as e:
            _log_swallowed("panel_expr.py", e)
            c = None
        if c is not None and len(c) == n and (c >= 0).all():
            codes = c
    if codes is None:
        lv = idx.get_level_values(0).to_numpy()
        change = np.empty(n, dtype=bool)
        change[0] = True
        np.not_equal(lv[1:], lv[:-1], out=change[1:])
    else:
        change = np.empty(n, dtype=bool)
        change[0] = True
        np.not_equal(codes[1:], codes[:-1], out=change[1:])
    starts = np.flatnonzero(change)
    bnd = np.empty(len(starts) + 1, dtype=np.int64)
    bnd[:-1] = starts
    bnd[-1] = n
    return bnd

def _by_group(s: pd.Series, ns, fn, seg_fn=None) -> pd.Series:
    """按组（股票）把 fn 应用到每个连续 segment（ns 可为 None）。

    性能（v1.17.7）：原实现逐行 `inst[i]` 判组界 + 每股 `s.iloc[a:b]` pandas 切片
    （profile：322 万次 Index.__getitem__ + 数千次 MultiIndex _slice 是逐组执行层的
    隐藏大头）。改为 numpy 向量化组界 + 单次 to_numpy 后的纯 numpy 切片，语义不变。
    性能（v1.18.4）：组界计算改走 `_segment_bnd`（codes 整数比较），消除 object
    字符串逐元素比较（详见 `_segment_bnd`）。另新增 `seg_fn` 段感知快捷路径：把
    全部段拼成一个长数组 + 段起止数组，一次性调用内核（免去数百次逐段 numpy 调用
    调度开销，巨型公式主要瓶颈）；`seg_fn` 缺省时回退逐段循环。
    """
    idx = s.index
    n = len(s)
    if n == 0:
        return pd.Series(dtype=np.float64, index=idx)
    arr = s.to_numpy(dtype=np.float64)
    if ns is None:
        ns_arr = None
    else:
        ns_arr = ns.to_numpy(dtype=np.float64)
    bnd, ss, se = _seg_arrays(idx, n)
    if seg_fn is not None:
        res = seg_fn(arr, ns_arr, ss, se)
        if res is not None:
            out = np.asarray(res, dtype=np.float64)
            if out.shape != arr.shape:
                out = np.broadcast_to(out, arr.shape).astype(np.float64)
            return pd.Series(out, index=idx)
    out = np.empty(n, dtype=np.float64)
    for gi in range(len(bnd) - 1):
        a = bnd[gi]
        b = bnd[gi + 1]
        if ns_arr is None:
            out[a:b] = fn(arr[a:b])
        else:
            out[a:b] = fn(arr[a:b], ns_arr[a:b])
    return pd.Series(out, index=idx)
