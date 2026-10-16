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

★★ T5（审计 ⑪ / v2.8.7，用户 2026-10-16 已拍 ✓）：**"数组里含不含起点 1"这个隐含契约显式化** ✗→✓
------------------------------------------------------------------------------
审计原文：`nav_from_rets`（cumprod **不含**起点 1 ✗）、`max_drawdown`（第 0 点恒 0 ✗）、
`annualized_return`（以 `a[0]` 为基、`n = size - 1` ✗）⇒ 谁把**日收益数组**直接塞进来
（没先转净值、也没补首日）就会**漏首日 / 天数差一天** ✗ —— 而它**不会报错** ✓（静默差一点 ✗）。

⇒ 现在把契约写在明处 ✓：
  · **本模块的 `max_drawdown` / `annualized_return` 只接受"净值序列"** ✓（值域必须 **> 0** ✓，
    因为策略账户除以初始资金恒为正 ✓）——传入日收益数组时（含负值 ✓）**直接抛 `ValueError`** ✗✓，
    错误里给出该用的函数名 ✓（不再静默算错 ✓）；
  · **`annualized_return` 的天数约定**：`持有天数 = len(nav) - 1` ✓ ⇒ **nav 必须含起点** ✓；
    拿"收益序列"进来的人请先 `nav_with_start(rets)` ✓（补上首日 1.0 ✓，最不易错 ✓）。
⚠ 现网所有调用方（`signals/engine._perf` / `factors.py` / `replay.py` / `single_test.py` /
  `benchmark_curves.py` ✓）本来就是补过首日 0 的 ✓ ⇒ **本改动不改变任何现有数字** ✓（只是把陷阱堵住 ✓）。
"""
import numpy as np

TRADING_DAYS_PER_YEAR = 252.0


def nav_from_rets(rets) -> np.ndarray:
    """日收益（小数，NaN 视作 0）→ 净值数组（**长度与输入相同** ✓、起点 1 **隐含** ✗）。

    ⚠ 返回数组的第 i 个元素 = 第 i 天**收盘后**的净值 ⇒ `len == 天数` ✓ 而
      `持有天数 = len - 1` ✗（首日在数组里**没有**对应的"1.0"元素 ✓）。
      需要"含起点"的净值（做年化最不易错 ✓）请用 `nav_with_start` ✓。
    """
    r = np.asarray(rets, dtype=np.float64)
    if r.size == 0:
        return np.empty(0, dtype=np.float64)
    r = np.where(np.isfinite(r), r, 0.0)
    return np.cumprod(1.0 + r)


def nav_with_start(rets) -> np.ndarray:
    """★ T5：日收益 → **含起点 1.0** 的净值序列（`len = 天数 + 1` ✓）。

    为什么推荐它：`annualized_return` 的天数 = `len(nav) - 1` ✓ ⇒ 含起点时"天数"刚好等于
    收益个数 ✓（差一天都会被年化放大成百分比级误差 ✗，尤其是短窗口 ✓）。
    """
    r = np.asarray(rets, dtype=np.float64)
    if r.size == 0:
        return np.array([1.0], dtype=np.float64)
    r = np.where(np.isfinite(r), r, 0.0)
    return np.concatenate(([1.0], np.cumprod(1.0 + r)))


def _as_nav(a) -> np.ndarray:
    """★ T5：把入参当**净值序列**校验（负值 ⇒ 明确报错，别静默算错 ✗）。"""
    arr = np.asarray(a, dtype=np.float64)
    arr = arr[np.isfinite(arr)]
    if arr.size and float(np.min(arr)) < 0:
        raise ValueError(
            "这不是净值序列 ✗（含负值）—— 看起来你传的是**日收益数组** ✓。"
            "请先用 `nav_from_rets(rets)`（长度不变 ✓）或 `nav_with_start(rets)`（补上首日 1.0 ✓）转换。")
    return arr


def max_drawdown(nav) -> float:
    """最大回撤（负数；空/全 NaN/无回撤 → 0.0）。⚠ 入参必须是**净值序列**（见 `_as_nav` ✓）。"""
    a = _as_nav(nav)
    if a.size == 0:
        return 0.0
    peak = np.maximum.accumulate(a)
    with np.errstate(all="ignore"):
        dd = a / peak - 1.0
    dd = dd[np.isfinite(dd)]
    return float(np.min(dd)) if dd.size else 0.0


def annualized_return(nav, trading_days: float = TRADING_DAYS_PER_YEAR):
    """净值序列 → 年化收益（`n < 2` 或首值 ≤ 0 → None）。

    ★ T5 契约：**持有天数 = `len(nav) - 1`** ⇒ 传进来的净值**必须含起点** ✓
      （即"第 0 天 = 1.0" ✓）；用 `nav_with_start(rets)` 最稳 ✓。⚠ 入参必须是净值序列 ✓。
    """
    a = _as_nav(nav)
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
