# -*- coding: utf-8 -*-
"""`HHVBARS`/`LLVBARS` 的"**最右极值**"实现（★ v2.10.86 从 `ops_ext.py` 抽出 ✓）。

## 为什么单独一个模块
· 这三件套（145 行 ✓）**零外部函数依赖** ✓ —— 只用到 `numpy` 与下面这个常量 ✓
  ⇒ 是 `ops_ext`(2162 行) 里**最容易安全剥离**的一整块 ✓；
· 已有 **10 条测试**护着（`tests/test_ops_rightmost_bars.py` ✓，含 **朴素暴力参考实现**对拍 ✓）；
· 三条实现路径（`N==1` / `N<=16` 分块滑窗 / `N>16` **van Herk** ✓）本来就和别的算子无关 ✓。

## 语义（源码注释里那些推导，一起搬过来了 ✓）
`i - (段内窗口最右极值下标)`；**同值取最右** ✓；NaN 不参与比较 ✓；
`vals[i]` 为 NaN 或窗口内无有限值 ⇒ **NaN** ✓。
"""
from __future__ import annotations

import numpy as np

_BARS_SLIDE_MAX_N = 16


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
    """固定窗口 HHVBARS/LLVBARS 段感知：i - (段内窗口最右极值下标)。

    性能演进（HHVBARS/LLVBARS 的窗口 N 完全固定，不需要 RMQ）：
      · 初版：完整稀疏表做 RMQ（O(n·log n)）→ 实测 28 万行 ≈ 140ms/次。
      · v1.18.22：满窗口改「**分块** `sliding_window_view` + 反转 argmax」—— 但每行要扫
        过 N 个元素，整体是 **O(n·N)** 的内存流量（实测 n=354794：N=5 30ms、N=60 86ms、
        N=125 191ms、N=250 306ms、N=400 520ms，**随 N 近似线性**），N 一大就吃掉整次求值 30%。
      · v1.18.27（本版）：满窗口换 **van Herk / Gil-Werman「块内前缀-后缀极值」** ——
        按 N reshape 成 (m, N)，行内做前缀/后缀累积，长度恰为 N 的窗口被两块无缝拼成
        （推导见下方注释）→ 每点 O(1)、总 **O(n)**、纯向量化、复杂度与 N 无关。
      · 不满窗口（每段前 N-1 个，数量极少）：段内前缀累积（`fmax/fmin.accumulate` +
        下标 `maximum.accumulate`）取"到 r 为止的最右极值"（两版未变）。
    "最右极值下标"（同值取最右）三处共用同一套：前缀用「等值 + `maximum.accumulate`」
    （得到"最后一次达成"）、后缀在反转坐标下用「**严格递增(jump)** + `maximum.accumulate`」
    （得到"首次达成" → 映射回原坐标即最右）、两半合并用 `_rightmost_arg_best`。
    满窗口按 N 二选一：`N <= _BARS_SLIDE_MAX_N` 走分块滑窗、否则走 van Herk（见常量注释）。
    NaN 语义不变：NaN 位不参与比较；窗口内无有限值或 vals[i] 为 NaN → 输出 NaN。
    """
    n = len(vals)
    if n == 0:
        return np.zeros(0, dtype=float)
    N = max(1, int(nvals))
    idx = np.arange(n)
    r = idx - seg_start                      # 段内偏移（段起点为 0）
    best = np.full(n, -1, dtype=np.int64)    # 段内窗口"最右极值"的全局下标；-1=无效

    if N == 1:
        # 窗口退化为单点：极值即自身
        best[:] = np.where(np.isnan(vals), -1, idx)
    else:
        v = np.where(np.isnan(vals), -np.inf if is_max else np.inf, vals)
        full = r >= (N - 1)                  # 满窗口：窗口 = [i-N+1, i]，完全落在段内
        if full.any():
            if N <= _BARS_SLIDE_MAX_N:
                # 小 N：分块滑窗 + 反转 argmax。它虽是 O(n·N)，但 N 小时常数更小，
                # 比 van Herk 的固定开销划算（交叉点实测见 _BARS_SLIDE_MAX_N 注释）。
                BLK = 1 << 16
                fi = np.flatnonzero(full)
                swv = np.lib.stride_tricks.sliding_window_view
                for s in range(0, fi.size, BLK):
                    seg_idx = fi[s:s + BLK]
                    a = int(seg_idx[0]) - (N - 1)
                    b = int(seg_idx[-1]) + 1
                    win = swv(v[a:b], N)                      # (b-a-N+1, N)
                    rev = win[:, ::-1]                        # 反转后 argmax 即"最右"
                    pos_in_win = (N - 1) - (
                        rev.argmax(axis=1) if is_max else rev.argmin(axis=1))
                    k = seg_idx - (a + N - 1)
                    best[seg_idx] = seg_idx - (N - 1) + pos_in_win[k]
                    has = np.isfinite(win).any(axis=1)        # 窗口内全 NaN → 无有效极值
                    best[seg_idx] = np.where(has[k], best[seg_idx], -1)
            else:
                # van Herk / Gil-Werman「块内前缀-后缀极值」：
                # 把 v 补齐到 N 的整数倍后 reshape 成 (m, N)，行内分别做前缀/后缀累积。
                # 长度恰为 N 的窗口 [l, i] 中：l 落在块 q-1、i 落在块 q（或同块），而
                #   suf(l) = [l, 块尾(l)]  ⊆ [l, i]
                #   pre(i) = [块首(i), i]  ⊆ [l, i]
                # 两者**无缝拼成** [l, i]（相邻块边界处端点相接、不重叠不留缝），故合并即
                # 答案；且两半都不会越出窗口 → 天然满足"窗口不跨段"，无需按段建块。
                m = (n + N - 1) // N
                padded = np.full(m * N, -np.inf if is_max else np.inf, dtype=np.float64)
                padded[:n] = v                 # 尾部填充只在最后一块，且不会被读到
                A = padded.reshape(m, N)
                cols = np.arange(N)
                base = (np.arange(m) * N)[:, None]
                acc = np.fmax.accumulate if is_max else np.fmin.accumulate

                # 前缀：行内 [块首, j] 的"最右极值"下标（无有限值 → -1）
                cumP = acc(A, axis=1)
                okP = (A == cumP) & np.isfinite(cumP)
                locP = np.maximum.accumulate(np.where(okP, cols, -1), axis=1)
                pre_idx = np.where(locP >= 0, base + locP, -1).reshape(-1)

                # 后缀：行内 [j, 块尾] 的"最右极值"下标。
                # ⚠ 不能照搬前缀那套（"等值 + maximum.accumulate"）：前缀公式给的是
                # "**最后一次**达成"，搬到反转坐标上得到的是原坐标的"**最左**"，
                # 方向正好相反（此处曾写错，被 202 用例对拍抓出 68 处失败）。
                # 后缀要的是"原坐标最右" = "反转坐标**最左**" = "**首次**达成"
                # → 用**严格递增**（jump）判定，再取"最后一个 jump"即 Bpremax 首次达成位。
                Ar = A[:, ::-1]
                cumS = acc(Ar, axis=1)
                prevS = np.empty_like(cumS)
                prevS[:, 0] = -np.inf if is_max else np.inf
                prevS[:, 1:] = cumS[:, :-1]
                jumpS = (cumS > prevS) if is_max else (cumS < prevS)
                jumpS[:, 0] = True                  # 定义：块内首元素视为一次 jump
                lastJump = np.maximum.accumulate(np.where(jumpS, cols, -1), axis=1)
                locS = (N - 1) - lastJump[:, ::-1]  # 反转坐标 → 原坐标；恒在 [j, 块尾] 内
                suf_idx = (base + locS).reshape(-1)

                fi = np.flatnonzero(full)
                best[fi] = _rightmost_arg_best(suf_idx[fi - (N - 1)], pre_idx[fi], vals, is_max)

        # 不满窗口（每段前 N-1 个）：段内前缀"到 r 为止的最右极值"
        need = np.flatnonzero(~full)
        if need.size:
            # 段边界（need 的段起点即其所在段的首行）
            starts = np.flatnonzero(np.r_[True, seg_start[1:] != seg_start[:-1]])
            ends = np.r_[starts[1:], n]
            for a, b in zip(starts, ends):
                m = int(min(N - 1, b - a))
                if m <= 0:
                    continue
                vv = v[a:a + m]
                ar = np.arange(m)
                if is_max:
                    cm = np.fmax.accumulate(vv)
                    eq = vv == cm
                else:
                    cm = np.fmin.accumulate(vv)
                    eq = vv == cm
                eq &= ~np.isnan(cm)
                # ⚠ 变量名**不能叫 `acc`** ✗→✓：本函数上面（L118）已把 `acc` 绑成
                #   `np.fmax.accumulate` / `np.fmin.accumulate`（**函数** ✓），
                #   这里再赋成数组 ⇒ mypy 报 "ndarray 赋给 Callable" 与 "int <= Callable" ✓。
                #   ⇒ 改名 `acc_idx` ✓（纯命名，**逻辑一字未改** ✓）。
                acc_idx = np.maximum.accumulate(np.where(eq, ar, -1))
                best[a:a + m] = np.where(acc_idx >= 0, a + acc_idx, -1)

    ok = (best >= 0) & ~np.isnan(vals)
    return np.where(ok, (idx - best).astype(float), np.nan)


