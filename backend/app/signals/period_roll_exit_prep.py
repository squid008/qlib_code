# -*- coding: utf-8 -*-
"""「每期全仓滚动」的**离场输入准备**（★ v2.11.6 从 `signals/period_roll.py` 抽出）。

## 这里做什么（三件，都在逐日循环之前 ✓）
1. **把离场矩阵对齐到引擎自己的列轴** ✓
2. **每个标的的整手股数**（科创板 688/689 ⇒ 200 股 ✓，其余 100 ✓）
3. **离场矩阵形状自检** ✓ —— 对不上就**关掉离场**并给一条 diag ✓

## ⭐ 为什么第 1 件必须存在（v2.8.8，用户 2026-10-16 报障 ✓）
调用方（`routers/factors.py` 的 `_exit_matrix` / `_exit_pre_mats`）是按 `panel["CLOSE"]`
的**全部列**建矩阵 ✓，而引擎内部的列轴是 `codes = sorted(set(信号 ∩ CLOSE 列))` ✗
⇒ 两者**列数/顺序都可能不同**（实测 **39 vs 36** ✓）⇒
· 普通离场矩阵：被形状自检拦下 ⇒ "本次不启用离场"（还算**响** ✓）；
· **含入场价的规则**：当时**没有自检** ✗ ⇒ 逐日 `eval_row` 直接抛
  `ValueError: operands could not be broadcast together with shapes (39,) (36,)` ✗
  ⇒ 引擎按"规则求值失败 ⇒ 关掉离场"处理 ⇒ **含离场曲线与对照逐位相同、五个离场计数全 0** ✗✗。
  （用户原话：「我止损价设置得很紧不可能不触发，回测大概率有问题」✓ —— **判断完全正确** ✓。）

修法 = 按**列名**把矩阵取成引擎要的那些列 ✓（矩阵列序 = `panel["CLOSE"].columns` ✓，
这是调用方用 `reindex(columns=…)` 保证的 ✓）；**列名对不上 ⇒ 保持原样** ✓，交给形状自检 ✓
（**不猜** ✗ —— 猜错列等于把 A 股的离场信号套到 B 股 ✓）。

## ⚠ 形状自检为什么是"关掉"而不是"抛"
对拍/回测**绝不能因为"附加信息"崩** ✗（与 `perf` / `period_roll` 同规矩 ✓）。
但"关掉"必须**留痕** ✓（`exit_off` 文案进 diag ✓），否则就是上一个 bug 的老路 ✗。
"""
from __future__ import annotations

from typing import Optional, Tuple

import numpy as np


def align_exit_inputs(*, exit_mat, exit_rule, close_cols, codes, n_row,
                      lot: int, lot_of) -> Tuple[Optional[object], np.ndarray, str]:
    """→ `(对齐后的 exit_mat, 每标的整手股数, 关掉离场的原因文案或 "")` ✓。

    ⚠ `exit_rule.bind(...)` 是**原地副作用**（把对齐后的预计算列挂回规则 ✓）——
    必须在这里做完 ✓，逐日循环里才能直接 `eval_row`。
    """
    _col_sel = None
    if len(close_cols) != len(codes):
        try:
            _idx_of = {c: k for k, c in enumerate(close_cols)}
            _col_sel = np.asarray([_idx_of[c] for c in codes], dtype=np.int64)
        except KeyError:                                  # 列名不全对不上 ⇒ 不猜（交给自检 ✓）
            _col_sel = None
    if _col_sel is not None:
        if exit_mat is not None:
            _em0 = np.asarray(exit_mat)
            if _em0.ndim == 2 and _em0.shape[1] == len(close_cols):
                exit_mat = _em0[:, _col_sel]
        if exit_rule is not None:
            _pms = list(getattr(exit_rule, "pre_mats", None) or [])
            if _pms and all(np.asarray(m).ndim == 2 and np.asarray(m).shape[1] == len(close_cols)
                            for m in _pms):
                exit_rule.bind([np.asarray(m)[:, _col_sel] for m in _pms])
    # 整手股数：按列号预算一次 ✓（`_lot_of` 是字符串判断，别放进逐票循环里 ✗）
    lot_arr = np.fromiter((lot_of(c, lot) for c in codes), dtype=np.int64, count=len(codes))

    # 形状自检 —— 对不上就**关掉离场**并留痕 ✓（不崩 ✓，也不静默 ✓）
    exit_off = ""
    if exit_mat is not None:
        try:
            _em = np.asarray(exit_mat)
            if _em.shape != (n_row, len(codes)):
                exit_off = "离场信号矩阵形状不符（%s vs (%d,%d)）⇒ 本次不启用离场" % (
                    tuple(_em.shape), n_row, len(codes))
                exit_mat = None
            else:
                exit_mat = _em.astype(bool, copy=False)
        except Exception as e:                                     # noqa: BLE001
            exit_off = "离场信号矩阵不可用（%r）⇒ 本次不启用离场" % (e,)
            exit_mat = None
    return exit_mat, lot_arr, exit_off
