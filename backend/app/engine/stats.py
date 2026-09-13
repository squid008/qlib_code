# -*- coding: utf-8 -*-
"""轻量时序统计工具（**纯函数、零副作用、无 app 内部依赖**）。

为什么单独成模块（v1.19.24）：
- 这些统计量被**两条路径共用** —— 单因子测试（`factors/single_test.py` 的日配对稳定性）与
  事件研究（`factors/event_study.py` 的 per-k 日配对稳定性，供弹窗第④条随「最长持有」取用）；
- ⚠ `factors/single_test.py` **import 时就会执行 `_engine_init(...)`**（模块级副作用）⇒ 事件研究
  **绝不能**从它那里 import，否则回测/事件研究的 worker 子进程会重复 init qlib，违反
  「`qlib.init()` 全局只 init 一次」铁律（见 `app/services/qlib_runtime.py` 与开发记录 v1.18.50）。
- 因此纯统计函数集中在本模块：只依赖 numpy，两边共用**同一实现** ⇒ 口径不会各写一份而漂移。

自检：`backend/tests/test_stats.py`。
"""
from __future__ import annotations

from typing import Optional

import numpy as np


def acf(x: np.ndarray, k: int) -> float:
    """lag-k 自相关系数（样本）。"""
    n = len(x)
    if n <= k:
        return 0.0
    xc = x - x.mean()
    denom = float(np.sum(xc ** 2))
    if denom <= 0:
        return 0.0
    return float(np.sum(xc[: n - k] * xc[k:]) / denom)


def hac_t(x: np.ndarray, maxlags: int = None) -> Optional[float]:
    """Newey-West HAC 稳健 t 统计量（修正自相关 + 异方差）。

    maxlags 默认按 Newey-West 建议：int(4 * (n/100)^(2/9))。
    方差 = γ0 + 2·Σ(1 - j/(L+1))·γj；t = mean / sqrt(修正方差/n)。
    序列方差被极端自相关压成非正时钳到极小值。
    """
    n = len(x)
    if n < 3:
        return None
    if maxlags is None:
        maxlags = int(4 * (n / 100.0) ** (2 / 9.0))
        maxlags = max(1, min(maxlags, n - 2))
    m = float(x.mean())
    xc = x - m
    gam = np.array([np.sum(xc[: n - k] * xc[k:]) / n for k in range(maxlags + 1)])
    var = gam[0] + 2.0 * np.sum((1 - np.arange(1, maxlags + 1) / (maxlags + 1)) * gam[1:])
    se = np.sqrt(max(var, 1e-18) / n)
    return float(m / se)


def pair_stability(x: np.ndarray) -> tuple:
    """日配对差值序列 → (HAC t, 日胜率, 有效配对日数)。

    与单因子测试表「日配对稳定」的口径逐条一致：HAC t 用 `hac_t`、胜率 = 差值 > 0 占比。
    returning 门限：**配对日 < 2** 时两者都无意义 ⇒ `(None, None, n)`；**配对日 = 2** 时 HAC 方差
    无法可靠估计（`hac_t` 要求 ≥3 点）⇒ `t=None` 但**胜率仍给出**（与行级"`daily_win` 在 n≥2 即有、
    `daily_t_hac` 需 n≥3"一致）。
    """
    x = np.asarray(x, dtype=float)
    x = x[np.isfinite(x)]
    n = int(x.size)
    if n < 2:
        return None, None, n
    t = hac_t(x)
    return t, float((x > 0).mean()), n
