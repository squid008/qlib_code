# -*- coding: utf-8 -*-
"""筹码换手相关实现（★ v2.9.56，2026-10-19 ✓ —— 从 `panel_expr.py` 搬出的第一块 ✓）。

`panel_expr.py` 有 **2,909 行** ✗（全仓最大 ✓ 且是性能热点 ✓），71 个顶层函数挤在一起 ✓。
这一组（`chip_turn_of` / `_chip_turn_proxy` / `_CHIP_BASE_FIELDS`，共 **109 行** ✓）
是其中最内聚的一块 ✓ —— 实测**只依赖 `numpy`** ✓（外加日志兜底 ✓），
搬出来不需要任何别的内部符号 ✓。

⚠ 源码**一字未改** ✓（含注释里的口径说明 ✓）；`panel_expr` 仍**显式再导出**这三个名字 ✓，
  所以 `chip_store.py` 那边 `from ..factors.panel_expr import chip_turn_of` **继续有效** ✓。
"""

import numpy as np
# ⚠ `pd` 只出现在**字符串注解**里（`-> "pd.Series"` ✓）—— 但 ruff 会按名字解析注解 ✓
#   ⇒ 不导入就是 F821 ✗（实测抓到 2 处 ✓）。
import pandas as pd  # noqa: F401


from ..logger import _log_swallowed  # noqa: E402


# 筹码派生字段（`$chip_cost_*` / `$chip_win_*`）依赖的基础字段（v1.19.83）：
#   close/high/low  → 当日价格区间（筹码注入的范围）
#   volume/factor/market_cap → **换手率**（⚠ 数据里**没有** turn.day.bin，必须用
#                              成交量 × 真实价 / 市值 反推，见 `PanelEvaluator._chip_field`）
_CHIP_BASE_FIELDS = ("close", "high", "low", "volume", "amount", "factor", "market_cap")

def _chip_turn_proxy(ev, _px_raw, _vol, _mc) -> "pd.Series":
    """**反推**换手率 = 成交量(校准成股) × 真实价 ÷ 市值（v1.20.44 的逐股手/股自校准 ✓）。

    ★ v1.20.81：从 `chip_turn_of` 里抽出来 ✓ —— 这样"有 `$turn` 的股票用真值、缺的**按股票**兜底"
      可以共用同一段代码 ✓（原实现整体二选一 ⇒ 没有 turn 的股票换手率全 NaN ✗，见 `chip_turn_of`）。
    """
    _vol_sh = _vol
    try:
        _amt = ev.field("$amount")
        # ★★ v1.20.44 **逐股**手/股判据（原来全池取**一个**中位数 ✗）——
        #   判据 `(成交额/成交量)/真实价`：≈1 ⇒ volume 是"股" ✓；≈0.01 ⇒ 是"手"（×100 ✓）。
        #   实测（400 只抽样）：**p50=0.0105、<0.1 的占 99.4%** ✓ ⇒ 全池单标量**大体**够用 ✓，
        #   但**边缘个股**（新股 / 低流动性 / 北交所 ✗）会落到另一侧 ⇒ 池级标量把它们
        #   **整只判错** ✗（换手率差 100 倍 ✗）。⇒ 改**按 `instrument` 取中位数** ✓
        #   （每只股票自己定标 ✓；成本 = 一次 `groupby.transform` ✓，与筹码递推同量级 ✓）。
        #   ⚠ 对齐：`_rs` 的 index 可能与 `_vol` 不完全一致 ⇒ 用 `Series.where` **按标签对齐** ✓。
        _ratio = (_amt / _vol) / _px_raw
        _rs = _ratio
        try:
            _lv = ("instrument" if "instrument" in _ratio.index.names
                   else _ratio.index.names[0])
            _rs = _ratio.groupby(level=_lv).transform("median")
        except Exception as e:  # noqa: BLE001 —— 分组失败就用原值（退化为池级行为 ✓）
            _log_swallowed("panel_expr.py", e)
            _rs = _ratio
        _vol_sh = (_vol * 100.0).where(_rs < 0.1, _vol)   # 判为"手"⇒×100 ✓，否则原样（"股" ✓）
        try:
            ev._chip_turn_meta = {"mode": "proxy",
                                  "lots_ratio": float((_rs < 0.1).to_numpy().mean())}
        except Exception as e:  # noqa: BLE001
            _log_swallowed("panel_expr.py", e)
            ev._chip_turn_meta = {"mode": "proxy"}
    except Exception as e:  # noqa: BLE001 —— 无 amount 就按"股"处理
        _log_swallowed("panel_expr.py", e)
        pass
    return (_vol_sh * _px_raw / _mc).where(_mc > 0)

def chip_turn_of(ev) -> "pd.Series":
    """筹码递推用的**换手率**（真 `$turn` 优先；否则按「成交额 ÷ 市值」反推 + 手/股自校准）。

    v1.19.92：从 `PanelEvaluator._chip_field` 里抽成**公共函数** ——
    **物化（`chip_store.materialize`）必须与面板同一口径**，否则会出现
    "面板算得出、物化算不出 ⇒ 整只股票被跳过"✗（2026-09-17 实测：只物化出 2425/6075 只，
    被跳过的股票还留着**旧日期轴**的文件 ⇒ 多股票加载时 `Gt($close,$chip_cost_95)` 崩 ✗）。

    口径与量纲说明（沿用原文，勿随手改）：
    - 本仓行情数据里**没有换手率字段**（`features/*/` 无 `turn.day.bin`，2026-09-17 核实）
      ⇒ 用等价式反推：换手率 = 成交量 / 流通股本 ≈ (成交量 × 真实价) / 市值 = 成交额 / 市值
      （量纲自洽 元/元）。⚠ 若 `market_cap` 是**总市值**（非流通市值），对流通比例低的个股
      会**低估**换手率 ⇒ 筹码衰减偏慢（已知口径近似，不改变信号的相对形态）。
    - v1.19.85：**优先用真换手率字段**（`features/<inst>/turn.day.bin`，`build_turn_field.py`
      物化）；只有该字段缺失/全空时才退回反推（含手/股自校准）。
    - ⚠ 成交量单位**自校准**（2026-09-17 实测）：本仓 volume 疑为"手"（100 股）——判据：
      成交额/成交量 = 均价 ⇒ 再除以真实价 ≈1 说明是"股"、≈0.01 说明是"手"；取面板中位数
      （一次标量，成本可忽略）。
    """
    c = ev.field("$close")
    _px_raw = c / ev.field("$factor")            # 真实价（不复权），与市值同口径
    _vol = ev.field("$volume")
    _mc = ev.field("$market_cap")
    _turn_field = None
    try:
        _t = ev.field("$turn")
        if bool(_t.notna().any()):
            _turn_field = _t
    except Exception as e:  # noqa: BLE001
        _log_swallowed("panel_expr.py", e)
        _turn_field = None
    if _turn_field is not None:
        # ⚠⚠⚠ v1.20.44 **单位自适应（修 100 倍 BUG ✗，2026-09-21 实测确认）**：
        #   本仓 `turn.day.bin` 存的是**百分数**而不是小数 ✗ —— 实测（164 只 / 48 万个值）：
        #     `p5=0.070  p50=0.8957  p90=3.6675  p99=10.50  max=43.91`
        #   只有"百分数"解释在物理上成立 ✓（若当**小数** ⇒ 日换手 89.6%、p99 超 1000% ✗
        #   **不可能** ✓；当百分数 ⇒ 0.90% / 3.67% / 10.5% ✓ 正是 A 股全市场中位量级 ✓）。
        #   而 `chip_dist.chip_run` 的契约是**小数**（`0.03` ✓ 见其 docstring 与本文件 `_chip_field` ✓）
        #   ⇒ **这里必须 /100** ✗。旧实现 `return _turn_field.where(...)` **原样返回** ✗
        #   ⇒ **有 turn 的那批股票换手率被放大 100 倍** ✗✗ ⇒ 筹码衰减快 100 倍 ⇒
        #     分布**塌缩到最近几天** ✗；⚠ 而且**单调性检查抓不到** ✗（COST(5)<COST(30)<… 仍成立 ✓）
        #     —— 这就是 v1.20.44 之前那次"数值看起来合理"的检查漏掉它的原因 ✓。
        #   判据用 **`max > 1`** ✓（而不是 `median > 1` ✗）：换手率若为"小数"**不可能超过 1** ✓，
        #   实测该字段 `max=43.91` ✗ ⇒ 判为百分数 ✓；将来数据源若改成小数 ⇒ `max ≤ 1` ⇒ **不缩放** ✓
        #   ⇒ **两个方向都自洽** ✓（不会因为换源而误除 ✓）。
        _tv = _turn_field.to_numpy(dtype=float)
        _mx = float(np.nanmax(_tv)) if np.isfinite(_tv).any() else float("nan")
        # ★ v1.20.45：把「本口径用了什么」记在评测器上 ✓，物化时写进 `_chip_meta.json` ✓
        #   （这样"这次物化到底是按什么单位算的"有**留痕** ✗ ⇒ 换机器/pull 后能自查 ✓）
        ev._chip_turn_meta = {"mode": "turn", "scale": 100.0 if _mx > 1.0 else 1.0,
                              "turn_max": _mx,
                              "turn_median": (float(np.nanmedian(_tv))
                                              if np.isfinite(_tv).any() else float("nan"))}
        _real = ((_turn_field / 100.0) if (np.isfinite(_mx) and _mx > 1.0)
                 else _turn_field).where(_turn_field > 0)
        # ★★ 2026-10-09 **逐股兜底**（修一个"静默、且随批次变化"的坑 ✗）：
        #   `turn.day.bin` 在本机**只覆盖部分股票**（实测 2450/6141，且**只有 sh** ✓；sz/bj 没有 ✗）
        #   ⇒ 原实现"面板只要**有一处**非空就整体用 `$turn`"✗ ⇒ 没有该字段的股票换手率**全 NaN**
        #     ⇒ 筹码整条**静默全 NaN** ✗（COST/WINNER 对那批股票直接用不了，而界面上毫无提示 ✗）；
        #     更糟的是**物化是按批做的**（400 只/批 ✓）⇒ 批次里"有没有 sh 股票"会改变该批 sz 股票的
        #     结果 ⇒ **同一只股票在不同批次里算出不同值** ✗✗（实测：sz300750 有值、sz000001 全 NaN ✓）。
        #   ⇒ 缺失的按**标签对齐**回退到反推口径 ✓（有 turn 的股票**一个数都不变** ✓）。
        _proxy = _chip_turn_proxy(ev, _px_raw, _vol, _mc)
        _filled = _real.where(_real.notna(), _proxy)
        try:
            ev._chip_turn_meta["turn_missing_ratio"] = float(
                (_real.isna()).to_numpy().mean())
        except Exception as e:  # noqa: BLE001
            _log_swallowed("panel_expr.py", e)
            pass
        return _filled
    return _chip_turn_proxy(ev, _px_raw, _vol, _mc)
