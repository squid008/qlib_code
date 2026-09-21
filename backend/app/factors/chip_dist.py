# -*- coding: utf-8 -*-
"""筹码分布内核：**WINNER / COST**（通达信、益盟那两个函数）的自研复刻（v1.19.83）。

## 背景：这两个函数没有官方公开的算法
- **赢家（WINNER）**：`WINNER(P)` = 当前**获利盘比例** —— 持仓成本低于现价 P 的筹码占全部筹码的比例；
- **成本（COST）**：`COST(q)` = **成本分位价** —— 使 `WINNER(P) = q` 的那个价格 P（q 用百分数，如 95）。

它们都属于**筹码分布（成本分布）**模型 ⇒ 需要"历史逐日递推的状态"，不是逐点函数：
  ① 每天有新筹码因换手而"换手"（旧筹码按换手率衰减）；
  ② 新筹码注入到**当日价格区间**（分布形状与区间取法各有说法）；
  ③ 逐日归一化后，累积分布即 `WINNER`，其反函数即 `COST`。

⚠ **准确性声明（务必先看）**：
  · **通达信没有公开源码/官方公式**；网上流传的是**逆向复刻**（主流口径 = 换手率衰减 + 当日区间内
    的**三角分布**），不同版本/参数（衰减系数、是否用换手率还是成交量、区间取 `(H+L)/2` 还是
    `(H+L+2C)/4`、分箱粒度）会给出**不同的数值** ⇒ 本实现**不可能与软件逐位一致**，只能保证
    **口径明确、方向合理、可调参**（"筹码集中/发散、成本上下沿"这类**相对判断**是可靠的）；
  · **益盟**那两个函数**没有公开算法**（连文档都很少）⇒ 本实现按通达信主流口径复刻，益盟方向存疑。
  ⇒ 用途定位：**选股信号的形态判断**（如本项目要实现的"筹码黏合后突破"）✓；
     **不要**当作"能复现软件读数"的精确工具 ✗。

## 本项目里的口径约定
- **价格口径用后复权价（`$close/$high/$low` 原生）**：筹码分布的绝对价位只在**同一价格口径**内
  有意义；用后复权价的好处是**除权日不跳空** ⇒ 筹码区间天然连续（若用真实价，除权日必须把
  整个筹码分布按比例重标，网上多数实现漏了这一步 ⇒ 高送转个股会失真）。
  ⚠ 因此 `COST()` 返回的是**后复权价**，与通达信界面上的真实价数值不同；但**与同表达式的 C 同口径**
  ⇒ 公式里 `C > COST(95)` 这类**比较**依然成立（信号一致），只有"打印出来的价位"不可直接对照。
- **换手率取 `$turn`**（数据里已有），不用自己拿流通股本算。
- 停牌日（价格 NaN / 换手 0）⇒ **不衰减也不注入**（筹码原样冻结）。

## 与软件对不齐的风险点（可调参数，默认取主流值）
| 参数 | 默认 | 说明 |
|---|---|---|
| `decay` | 1.0 | 衰减/注入强度：`k = decay × 换手率` |
| `peak` | `"hl2"` | 当日注入分布的峰值位置：`hl2=(H+L)/2`、`hlc3=(H+L+C)/3`、`ohlc4=(O+H+L+C)/4`、`close` |
| `shape` | `"tri"` | 区间内分布形状：`tri`（三角，通达信主流）/ `uni`（均匀） |
| `bins` | 128 | 每只股票的价格分箱数（越大越细、越慢） |
| `pad` | 0.5 | 价格网格相对历史 [min,max] 的外扩比例 |

## 实现要点（性能）
**按日、按"股票 × 分箱"矩阵向量化**（不要逐股 Python 循环 —— 那会慢两个数量级）：
全 A 5 年 ≈ 1200 天 × (5000 股 × 128 箱) ≈ 8 亿次元素运算 ⇒ numpy 下**几十秒**量级；
逐股循环则要 5000 次顺序递推，分钟~小时级 ✗。
"""

from __future__ import annotations

from typing import Dict, Iterable, Optional, Sequence

import numpy as np

DEFAULT_BINS = 128
DEFAULT_PAD = 0.5
_EPS = 1e-12


def _tri_cdf(edges: np.ndarray, low: np.ndarray, peak: np.ndarray,
             high: np.ndarray) -> np.ndarray:
    """当日注入分布的**累积分布** F(price)（行 = 股票，列 = 箱边界）。

    三角分布（峰值在 `peak`）：
      · x ≤ peak: F = (x-low)^2 / ((high-low)(peak-low))
      · x ≥ peak: F = 1 - (high-x)^2 / ((high-low)(high-peak))
    `low == high`（一字板）⇒ 退化为阶跃（全部筹码落在该价位）。
    """
    span = np.maximum(high - low, _EPS)
    left = np.maximum(edges - low[:, None], 0.0)
    right = np.maximum(high[:, None] - edges, 0.0)
    p1 = np.maximum(peak - low, _EPS)[:, None]
    p2 = np.maximum(high - peak, _EPS)[:, None]
    f_lo = (left ** 2) / (span[:, None] * p1)
    f_hi = 1.0 - (right ** 2) / (span[:, None] * p2)
    f = np.where(edges <= peak[:, None], f_lo, f_hi)
    return np.clip(f, 0.0, 1.0)


def _uni_cdf(edges: np.ndarray, low: np.ndarray, high: np.ndarray) -> np.ndarray:
    """均匀分布口径（`shape="uni"`）：F = clip((x-low)/(high-low), 0, 1)。"""
    span = np.maximum(high - low, _EPS)
    return np.clip((edges - low[:, None]) / span[:, None], 0.0, 1.0)


def _injection(edges: np.ndarray, low: np.ndarray, high: np.ndarray,
               peak: np.ndarray, shape: str, flat: np.ndarray) -> np.ndarray:
    """当日注入到各箱的筹码占比（每行和 ≈ 1；一字板走阶跃兜底）。"""
    cdf = _uni_cdf(edges, low, high) if shape == "uni" else _tri_cdf(edges, low, peak, high)
    out = np.diff(cdf, axis=1)
    if flat.any():                       # low==high：把全部筹码塞进包含该价的箱
        mask = (edges[:, :-1] <= low[:, None]) & (edges[:, 1:] >= low[:, None])
        idx = np.argmax(mask, axis=1)
        step = np.zeros_like(out)
        step[np.arange(out.shape[0]), idx] = 1.0
        out = np.where(flat[:, None], step, out)
    return out


def _cum_at(edges: np.ndarray, cum: np.ndarray, v: np.ndarray) -> np.ndarray:
    """价格 `v` 处的**累积筹码占比**（箱内线性插值）⇒ 即 `WINNER(v)`。

    ⚠ 必须插值而不是"取该箱的右边界累积值"：分箱再细也有宽度，直接取箱值是**阶梯近似**，
      会让 `WINNER(COST(q))` 的系统偏差达到"一个箱的筹码质量"（筹码集中时可达百分之几）⇒
      与 `_levels_at` 反函数对不上（自测里抓到过）。
    """
    n_stk, nbins = cum.shape
    rows = np.arange(n_stk)
    width = edges[:, -1] - edges[:, 0]
    dr = np.maximum(width / nbins, _EPS)
    idx = np.clip(np.floor((v - edges[:, 0]) / dr).astype(np.int64), 0, nbins - 1)
    base = np.where(idx > 0, cum[rows, np.maximum(idx - 1, 0)], 0.0)
    e0 = edges[rows, idx]
    e1 = edges[rows, idx + 1]
    w = np.clip((v - e0) / np.maximum(e1 - e0, _EPS), 0.0, 1.0)
    return base + w * (cum[rows, idx] - base)


def _levels_at(edges: np.ndarray, cum: np.ndarray, q: np.ndarray) -> np.ndarray:
    """累积分布 `cum` 达到 `q`（0~1）时的价格（箱内线性插值）⇒ 即 `COST(q×100)`。

    ⚠ 箱内插值必须用**同一个箱**的两侧边界：`cum[j]` 是"价格 ≤ edges[j+1]"的累积 ⇒
      目标落在箱 j ⇒ 左端点取 `edges[j]`、左累积取 `cum[j-1]`（j=0 时取 0）。
      早期写成 `edges[j-1]`（差一个箱宽）⇒ 筹码集中时 `WINNER(COST(q))` 会差到 30%（自测抓到）。
    """
    n_stk = cum.shape[0]
    rows = np.arange(n_stk)
    idx = np.argmax(cum >= q[:, None], axis=1)
    c_lo = np.where(idx > 0, cum[rows, np.maximum(idx - 1, 0)], 0.0)
    c_hi = cum[rows, idx]
    e_lo = edges[rows, idx]
    e_hi = edges[rows, idx + 1]
    w = np.where(c_hi - c_lo > _EPS, (q - c_lo) / np.maximum(c_hi - c_lo, _EPS), 0.0)
    out = e_lo + np.clip(w, 0.0, 1.0) * (e_hi - e_lo)
    # 尚无筹码（未上市 / 全程无成交）⇒ NaN；有筹码就必须有值（停牌日筹码冻结 ⇒ 仍给值）
    return np.where(cum[:, -1] > _EPS, out, np.nan)


def chip_run(close: np.ndarray, high: np.ndarray, low: np.ndarray,
             turn: np.ndarray, *, qs: Sequence[float] = (5.0, 30.0, 75.0, 95.0),
             prices: Optional[np.ndarray] = None, bins: int = DEFAULT_BINS,
             pad: float = DEFAULT_PAD, decay: float = 1.0, peak: str = "hl2",
             shape: str = "tri", open_: Optional[np.ndarray] = None,
             turn_unit: str = "auto") -> Dict[str, np.ndarray]:
    """逐日递推筹码分布，返回 `COST(q)` / `WINNER(P)` 面板。

    参数（**都是 (n_days, n_stocks) 矩阵，列 = 股票池，行 = 交易日**；NaN = 停牌/无数据）：
      `close/high/low` —— **后复权价**（见模块文档：用后复权价 ⇒ 除权无跳空，筹码区间连续）；
      `turn`           —— 换手率（小数，如 `0.03`；`$turn` 已是百分数则调用方先 /100）；
      `qs`             —— 要算的成本分位（百分数，如 `(5,30,75,95)`）；
      `prices`         —— （可选）要算获利盘的价格矩阵，返回每个位置对应的 `WINNER`；
      `peak`           —— 注入分布峰值：`hl2` / `hlc3` / `ohlc4` / `close`（需传 `open_`）；
      `shape` / `decay`/ `bins` / `pad` —— 见模块文档的"可调参数"表。

    返回 `{"cost_5": …, "cost_95": …, "winner": …}`（键：`cost_<q>`、`winner`、`winner_max`）。
    """
    close = np.asarray(close, dtype=float)
    high = np.asarray(high, dtype=float)
    low = np.asarray(low, dtype=float)
    turn = np.asarray(turn, dtype=float)
    if close.ndim != 2:
        raise ValueError("close/high/low/turn 必须是 (n_days, n_stocks) 矩阵")
    n_day, n_stk = close.shape
    if high.shape != close.shape or low.shape != close.shape or turn.shape != close.shape:
        raise ValueError("close/high/low/turn 形状必须一致")
    # 换手率单位自适应（`turn_unit="auto"`）：有的数据源给小数（0.03）、有的给百分数（3.0）。
    # ⚠⚠⚠ v1.20.44 **修判据**（2026-09-21 实测 ✗）：旧判据是 **`中位数 > 1.5`** ✗，
    #   而本仓 `turn.day.bin` 是**百分数**且中位数只有 **0.896**（= 0.90% ✓，A 股全市场中位量级 ✓）
    #   ⇒ `0.896 < 1.5` ⇒ 被误判成"小数" ✗✗ ⇒ **换手率被放大 100 倍** ⇒ 筹码衰减快 100 倍 ⇒
    #   分布**塌缩到最近几天** ✗。⚠ 而 `chip_turn_of` 那侧**也没有 /100** ✗ ⇒ **两层都错、互相掩盖** ✓
    #   （所以"旧 vs 新"曾经跑出一模一样的结果 ✗ —— 两条路都被这个坏判据吞掉了 ✓）。
    #   ⇒ 判据改为 **`max > 1`** ✓ ：换手率若真是"小数"**不可能超过 1**（100% ✓），
    #     与"中位数"无关 ⇒ **不受 A 股换手率量级影响** ✓，且数据源换成小数时也**不会误除** ✓
    #     （两个方向都自洽 ✓）。⚠ 中位数判据对"低换手率市场/低频字段"天生脆弱 ✗，别再回去用 ✓。
    if turn_unit == "auto":
        pos = turn[np.isfinite(turn) & (turn > 0)]
        turn_unit = "pct" if (pos.size and float(pos.max()) > 1.0) else "frac"
    if turn_unit == "pct":
        turn = turn / 100.0

    # ---- 每只股票一套价格网格（覆盖整段历史，一字板兜底最小宽度）----
    lo_h = np.nanmin(low, axis=0)
    hi_h = np.nanmax(high, axis=0)
    nolist = ~np.isfinite(lo_h) | ~np.isfinite(hi_h)
    lo_h = np.where(nolist, 1.0, lo_h)
    hi_h = np.where(nolist, 1.0, hi_h)
    span_h = np.maximum(hi_h - lo_h, np.maximum(hi_h * 1e-6, _EPS))
    frac = np.linspace(0.0, 1.0, int(bins) + 1)[None, :]
    edges = (np.maximum(lo_h - pad * span_h, _EPS))[:, None] + \
            (hi_h + pad * span_h - np.maximum(lo_h - pad * span_h, _EPS))[:, None] * frac

    chips = np.zeros((n_stk, int(bins)), dtype=float)
    qs_arr = np.asarray(qs, dtype=float)
    out: Dict[str, np.ndarray] = {("cost_%g" % q): np.full((n_day, n_stk), np.nan) for q in qs_arr}
    # `prices`：单矩阵 ⇒ 键 `winner`（向后兼容）；**多个矩阵的序列** ⇒ 键 `winner_0/1/…`
    #   ⚠ 这样一次递推就能同时产出多个价位的获利盘 —— 否则"每个价位各跑一遍全池递推"
    #   （物化 7 个字段时实测慢 3~7 倍，v1.19.85）
    _pmats = None
    if prices is not None:
        _pmats = list(prices) if isinstance(prices, (list, tuple)) else [prices]
        for _i in range(len(_pmats)):
            out[("winner" if len(_pmats) == 1 else "winner_%d" % _i)] = np.full((n_day, n_stk), np.nan)
    out["winner_max"] = np.full((n_day, n_stk), np.nan)   # WINNER(当日最高价)：打进"最乐观"的获利盘

    for t in range(n_day):
        c, h, l = close[t], high[t], low[t]
        ok = np.isfinite(c) & np.isfinite(h) & np.isfinite(l)
        tr = np.clip(np.where(ok, np.nan_to_num(turn[t], nan=0.0), 0.0), 0.0, 1.0)
        k = np.clip(float(decay) * tr, 0.0, 1.0)
        # ① 衰减（停牌 ⇒ k=0 ⇒ 筹码冻结）
        chips *= (1.0 - k)[:, None]
        # ② 注入当日区间（三角/均匀；一字板走阶跃）
        if peak == "hlc3":
            pk = (h + l + c) / 3.0
        elif peak == "ohlc4":
            o = open_ if open_ is not None else c
            pk = (np.asarray(o, dtype=float)[t] + h + l + c) / 4.0
        elif peak == "close":
            pk = c
        else:                                     # "hl2"（默认，通达信主流）
            pk = (h + l) / 2.0
        pk = np.clip(pk, l, h)
        flat = (h - l) <= np.maximum(np.abs(c) * 1e-9, _EPS)
        inj = _injection(edges, l, h, pk, shape, flat)
        # ⚠ 停牌日（k=0）的注入量是 NaN（价格全 NaN）⇒ `0 × NaN = NaN` 会**污染整个筹码向量**
        #   （一旦污染，后面每天都 NaN）⇒ 必须先清零 NaN 再乘
        inj = np.nan_to_num(inj, nan=0.0, posinf=0.0, neginf=0.0)
        chips += k[:, None] * inj
        # ③ 归一化（数值误差兜底）
        s = chips.sum(axis=1, keepdims=True)
        chips = np.divide(chips, np.where(s > _EPS, s, 1.0))
        cum = np.cumsum(chips, axis=1)
        for j, q in enumerate(qs_arr):
            out[("cost_%g" % q)][t] = _levels_at(edges, cum, np.full(n_stk, q / 100.0))
        # WINNER(当日最高价)：最高价之下的筹码占比（"当日成交价全在盈亏线之上"的粗略上限）
        # ⚠ 停牌日（价格 NaN）筹码**冻结** ⇒ COST 仍有值（上面），但"当日获利盘"无意义 ⇒ NaN
        wmax = _cum_at(edges, cum, np.where(ok, h, edges[:, 0]))
        out["winner_max"][t] = np.where(ok, np.clip(wmax, 0.0, 1.0), np.nan)
        if _pmats is not None:
            # ⚠ 尚无筹码的股票（没有换手率 ⇒ 从未注入）必须给 **NaN**，不能给 0
            #   （0 = "0% 获利盘" 是个"看起来正常"的假值，会被回测当成真信号 ✗，v1.19.85）
            has_chips = s.reshape(-1) > _EPS if np.ndim(s) else s > _EPS
            for _i, _pm in enumerate(_pmats):
                p = np.asarray(_pm, dtype=float)[t]
                pok = np.isfinite(p) & has_chips
                w = _cum_at(edges, cum, np.where(pok, p, edges[:, 0]))
                out[("winner" if len(_pmats) == 1 else "winner_%d" % _i)][t] = np.where(
                    pok, np.clip(w, 0.0, 1.0), np.nan)
    if prices is None:
        out.pop("winner", None)
    out["edges"] = edges                    # 便于调用方/体检（末尾边界）
    return out


def winner_at(close: np.ndarray, high: np.ndarray, low: np.ndarray, turn: np.ndarray,
              prices: np.ndarray, **kw) -> np.ndarray:
    """`WINNER(P)`（获利盘比例，0~1）—— 便捷包装。"""
    kw.pop("prices", None)
    return chip_run(close, high, low, turn, prices=prices, **kw)["winner"]


def cost_levels(close: np.ndarray, high: np.ndarray, low: np.ndarray, turn: np.ndarray,
                qs: Iterable[float] = (5, 30, 75, 95), **kw) -> Dict[str, np.ndarray]:
    """`COST(q)`（成本分位价，q 为百分数）—— 便捷包装，返回 `{"cost_5": …, …}`。"""
    kw.pop("qs", None)
    res = chip_run(close, high, low, turn, qs=tuple(qs), **kw)
    res.pop("edges", None)
    res.pop("winner", None)
    return res
