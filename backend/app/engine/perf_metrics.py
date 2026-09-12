# -*- coding: utf-8 -*-
"""绩效指标**纯函数**模块（年化 / 最大回撤 / 夏普 / 索提诺 / 卡玛）。

**为什么单独一个模块**：这些指标同时服务于
  · 单因子测试页「持仓期曲线」的**逐日盯市净值**（v1.18.48 起）；
  · 模型回测页的净值曲线（`engine/metrics.py` 现有实现）。
而 `metrics.py` 里的同类算法是**内联**在聚合函数里的（不可复用、两处极易漂移），
故抽成纯函数；口径与 `metrics.py` 对齐（夏普 = 日均/日标准差 × √252、**rf 默认 0**）。

口径定义
--------
- **净值**：`cumprod(1 + r)`，`r` 为日收益（小数）；NaN 视作 0（停牌/无持仓日 = 不动）。
- **年化收益** = `(末值/首值)^(252/n) − 1`（几何/复利口径）。
- **最大回撤** = `min(净值/累计峰值 − 1)`（负数，越小越差）。
- **夏普** = `(日均收益 − rf/252) / 日收益标准差 × √252`（**rf 默认 0**，与回测页一致）。
- **索提诺** = `(日均收益 − rf/252) / 下行标准差 × √252`；下行标准差用**半方差**口径
  （负收益平方后对**全部样本**取均值再开方，即正收益视作 0 偏差）—— 与
  `metrics.py`/常见平台一致，避免"只用负样本"导致分母口径随样本数漂移。
- **卡玛** = 年化收益 / |最大回撤|。

⚠ **统计噪声**：调仓期数少时（例如 61 期 / 4.8 年，逐日虽 1211 点但**独立样本远少于 1211**），
  夏普/索提诺/卡玛的置信区间很宽（±0.5 很常见）⇒ 界面必须与样本数一起展示，别单看数字。
⚠ 所有指标**不含**涨跌停 / 停牌 / 流动性冲击（与曲线同一简化口径）。
"""
import numpy as np

TRADING_DAYS_PER_YEAR = 252.0


def nav_from_rets(rets) -> np.ndarray:
    """日收益（小数，NaN 视作 0）→ 净值数组（起点隐含 1，长度与输入相同）。"""
    r = np.asarray(rets, dtype=np.float64)
    if r.size == 0:
        return np.empty(0, dtype=np.float64)
    r = np.where(np.isfinite(r), r, 0.0)
    return np.cumprod(1.0 + r)


def max_drawdown(nav) -> float:
    """最大回撤（负数；空/全 NaN/无回撤 → 0.0）。"""
    a = np.asarray(nav, dtype=np.float64)
    a = a[np.isfinite(a)]
    if a.size == 0:
        return 0.0
    peak = np.maximum.accumulate(a)
    with np.errstate(all="ignore"):
        dd = a / peak - 1.0
    dd = dd[np.isfinite(dd)]
    return float(np.min(dd)) if dd.size else 0.0


def annualized_return(nav, trading_days: float = TRADING_DAYS_PER_YEAR):
    """净值序列 → 年化收益（`n` = 净值点数；`n < 2` 或首值 ≤ 0 → None）。"""
    a = np.asarray(nav, dtype=np.float64)
    a = a[np.isfinite(a)]
    if a.size < 2 or a[0] <= 0 or a[-1] <= 0:
        return None
    n = a.size - 1
    years = n / float(trading_days)
    if years <= 0:
        return None
    try:
        return float((a[-1] / a[0]) ** (1.0 / years) - 1.0)
    except Exception:
        return None


def sharpe(rets, rf: float = 0.0, trading_days: float = TRADING_DAYS_PER_YEAR):
    """夏普比率（`rf` 为**年化**无风险利率，默认 0）；样本 < 2 或标准差为 0 → None。"""
    r = np.asarray(rets, dtype=np.float64)
    r = r[np.isfinite(r)]
    if r.size < 2:
        return None
    sd = float(r.std(ddof=1))
    if not np.isfinite(sd) or sd <= 0:
        return None
    excess = float(r.mean()) - float(rf) / float(trading_days)
    return float(excess / sd * np.sqrt(float(trading_days)))


def sortino(rets, rf: float = 0.0, trading_days: float = TRADING_DAYS_PER_YEAR):
    """索提诺比率（下行标准差用**半方差**口径：负收益平方、对全部样本取均值）。"""
    r = np.asarray(rets, dtype=np.float64)
    r = r[np.isfinite(r)]
    if r.size < 2:
        return None
    excess = r - float(rf) / float(trading_days)
    downside = np.minimum(excess, 0.0)
    dd = float(np.sqrt(np.mean(downside ** 2)))
    if not np.isfinite(dd) or dd <= 0:
        return None
    return float(np.mean(excess) / dd * np.sqrt(float(trading_days)))


def calmar(ann_return, mdd):
    """卡玛比率 = 年化收益 / |最大回撤|（任一缺失或回撤为 0 → None）。"""
    if ann_return is None or mdd is None:
        return None
    m = abs(float(mdd))
    if m <= 0 or not np.isfinite(m):
        return None
    return float(ann_return) / m


def compute_perf(rets, rf: float = 0.0,
                 trading_days: float = TRADING_DAYS_PER_YEAR) -> dict:
    """日收益序列 → 一整套绩效指标（JSON-ready；无值的项为 None）。

    返回键：`n_days / nav_end / total_return / annual_return / max_drawdown /
    sharpe / sortino / calmar / vol_annual / win_rate / max_dd_days`
      · `vol_annual` = 日收益标准差 × √252（年化波动，附带信息）；
      · `win_rate` = 日收益 > 0 的占比（**日**胜率，非事件胜率）；
      · `max_dd_days` = 从峰值到最深回撤点的交易日数（回撤持续时间，0 表示无回撤）。
    """
    r = np.asarray(rets, dtype=np.float64)
    r = r[np.isfinite(r)]
    if r.size == 0:
        return {"n_days": 0, "nav_end": None, "total_return": None,
                "annual_return": None, "max_drawdown": None, "sharpe": None,
                "sortino": None, "calmar": None, "vol_annual": None,
                "win_rate": None, "max_dd_days": None}
    nav = nav_from_rets(r)
    mdd = max_drawdown(nav)
    ann = annualized_return(nav, trading_days)
    sd = float(r.std(ddof=1)) if r.size > 1 else 0.0
    # 最长回撤持续（峰值 → 最深点）
    peak = np.maximum.accumulate(nav)
    with np.errstate(all="ignore"):
        dd = nav / peak - 1.0
    dd_days = None
    if dd.size:
        j = int(np.nanargmin(dd))
        if np.isfinite(dd[j]) and dd[j] < 0:
            i = int(np.nanargmax(nav[:j + 1]))
            dd_days = int(j - i)
    return {
        "n_days": int(r.size),
        "nav_end": round(float(nav[-1]), 6),
        "total_return": round(float(nav[-1] - 1.0), 6),
        "annual_return": (None if ann is None else round(float(ann), 6)),
        "max_drawdown": round(float(mdd), 6),
        "sharpe": (None if (sh := sharpe(r, rf, trading_days)) is None else round(float(sh), 4)),
        "sortino": (None if (so := sortino(r, rf, trading_days)) is None else round(float(so), 4)),
        "calmar": (None if (ca := calmar(ann, mdd)) is None else round(float(ca), 4)),
        "vol_annual": (None if not np.isfinite(sd) else round(float(sd * np.sqrt(trading_days)), 6)),
        "win_rate": round(float((r > 0).mean()), 6),
        "max_dd_days": dd_days,
    }
