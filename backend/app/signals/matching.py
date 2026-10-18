# -*- coding: utf-8 -*-
"""撮合原子（**手数 / 费用 / 现金约束**）—— 引擎与期初滚动共用的唯一实现 ✓。

## 为什么单独成模块（★ P0-B，2026-10-18 ✓）

原先这些函数住在 `signals/engine.py`（600+ 行）里 ✗，而 `signals/period_roll.py`
**反向 import 整个 engine** 只为拿这 4 个纯函数 ✓ ⇒

  · 依赖方向错 ✗：`period_roll`（每期全仓滚动）本来只是"另一个资金方案"，
    却拖着一个 600 行的状态机一起加载 ✓；两边稍有循环引用就炸 ✗；
  · 风险集中 ✗：**费用/手数**是全项目最"不能错"的一段（错了就是"账对不上"✗）——
    而它夹在状态机中间，改动时很容易被顺手改坏 ✓；
  · 不可单测 ✗：想单测"最低 5 元佣金 / 科创板 200 股整手"必须 import engine ✓。

⇒ 抽到本模块后：**engine 与 period_roll 都只依赖 matching** ✓（依赖方向变单向 ✓），
  且这 4 个函数可以**脱离引擎单测** ✓（`tests/test_matching.py` 守着 ✓）。

## 约定（都是踩过坑才写下的 ✓）

- **费用只有一处**（`fee_of` ✓）：资金路径（买卖扣款/回款）与成交明细的 `fee` 列必须同源 ✓，
  否则"明细与账不一致" ✗；
- **`half <= 0` ⇒ 费用恒 0** ✓（界面"无成本"档不能被"最低 5 元"污染 ✗）；
- **整手按标的**（`lot_of` ✓）：科创板 688/689 = **200 股**，其余 100 ✓；
- **现金绝不透支**（`fit_lots` ✓）：老写法用"成交额 ×(1+半费)"估费 ✗ ⇒ 被最低 5 元顶破 ⇒
  现金变负 ✗（实测 −3216.71 元 ✓）⇒ 现在按**真实费用**回退到手数为止 ✓。
"""
from __future__ import annotations

from typing import Optional, Tuple

LOT_DEFAULT = 100
#: A 股券商佣金**最低 5 元/笔** ✓（`MIN_FEE`；对小额单影响巨大 ✓）
MIN_FEE = 5.0
#: 科创板（688/689）整手 **200 股** ✓
LOT_KCB = 200

#: 成交时点 → 决策日到成交日的滞后（天）✓
FILL_LAG = {"t_close": 0, "t1_open": 1, "t1_close": 1}
#: 成交时点 → 用哪个价（`close` / `open`）✓
FILL_PRICE = {"t_close": "close", "t1_open": "open", "t1_close": "close"}

#: 拒单原因码 → 人话 ✓（前端/导出直接显示 ✓；**新增码必须同时在这里登记** ✗ 否则界面显示裸码 ✓）
REJECT_TEXT = {
    "limit_up": "涨停买不进",
    "limit_up_open": "开盘一字涨停买不进",
    "limit_down": "跌停卖不出（顺延）",
    "limit_down_open": "开盘一字跌停卖不出（顺延）",
    "suspended": "停牌",
    "no_cash": "资金不足",
    "no_price": "该日无行情",
    "max_positions": "已达最大同时持仓数（本次信号未建仓）",
}


def lot_of(code: str, lot_default: int = LOT_DEFAULT) -> int:
    """该**标的**的整手股数 ✓：科创板 688/689 ⇒ 200，其余用 `lot_default`（默认 100 ✓）。

    代码三种形态都要认 ✓（引擎内部是 `SH688001` ✓）：`SH688001` / `688001.SH` / `688001` ✓。
    """
    s = str(code).strip().upper()
    if "." in s:                                   # 688001.SH
        s = s.split(".")[0]
    elif len(s) > 6 and s[:2] in ("SH", "SZ", "BJ"):
        s = s[2:]                                  # SH688001
    return LOT_KCB if s.startswith(("688", "689")) else int(lot_default)


def fee_of(amount: float, half: float) -> float:
    """单笔手续费 = `max(成交额 × 单边费率, 最低 5 元)` ✓（`half` = 往返费率的一半 ✓）。

    ⚠ `half <= 0`（界面「**无成本**」档）⇒ **费用恒为 0** ✓ —— 否则"最低佣金 5 元"会凭空
      出现在"无成本"档里 ✗（那这一档就不再是无成本了 ✗；实测冻结基准 `k5_cost0` 当场抓到 ✓）。
    """
    if half <= 0:
        return 0.0
    fee = amount * half
    return fee if fee > MIN_FEE else MIN_FEE


def fit_lots(budget: float, px: float, lot: int, half: float, cash: float) -> int:
    """在**现金**约束下取"买得起的最大手数" ✓（v2.8.3，**审计 #9** ✓）。

    · `budget` = 这笔**打算花多少**（元 ✓；含不含费由调用方决定 ✓）；
    · 先按 `budget / px` 取整到手数 ✓，再用**真实费用**逐步回退到"买得起"为止 ✓；
    · 返回 0 ⇒ 调用方按 `no_cash` 拒单 ✓。
    ⚠ 正常路径（钱够）**一位数字都不变** ✓ —— 只有原本会透支的买单被缩量 ✓。
    """
    if px != px or px <= 0 or int(lot) <= 0:          # NaN / 非正价 / 非正手数 ⇒ 不买 ✓
        return 0
    _lot = int(lot)
    q = int(max(0.0, float(budget)) / float(px) / _lot) * _lot
    while q > 0 and q * px + fee_of(q * px, half) > cash + 1e-9:
        q -= _lot
    return int(q)


def fit_real_lots(px_raw: float, px_adj: float, lot: int, half: float, cash: float,
                  budget: Optional[float] = None) -> Tuple[float, int]:
    """按**真实股数的一手**取整 ✓（v2.8.4，**审计 #8** ✓）—— 返回 `(q_adj, n_real)`。

    **为什么必须按真实股数**：A 股"一手"是**真实 100 股**（科创板 200 ✓），而引擎内部持仓记的是
      **复权单位** ✗ ⇒ 老写法 `int(预算/复权价/lot)*lot` 取的是"复权单位的整手" ✗ ⇒
      真实股数 = `q/f` 常常不是整手 ✗（现金 685 / 真实价 10 / 复权价 6 ⇒ 老代码买 100 复权单位
      = 600 元 = **60 真实股** ✗；正解是**买不起一手就不买** ✓）。

    算法：① 在**真实价**上求最大整手（含最低 5 元佣金 ✓，见 `fit_lots` ✓）；
          ② 折回复权单位 `q_adj = n × px_raw / px_adj` ✓（金额一分不差 ✓）。
    ⚠ `px_adj == px_raw`（不复权 ✓）⇒ **逐位退化成老行为** ✓。
    """
    if px_raw != px_raw or px_adj != px_adj or px_raw <= 0 or px_adj <= 0:
        return 0.0, 0
    _budget = float(cash) if budget is None else min(float(budget), float(cash))
    n = fit_lots(_budget, float(px_raw), lot, half, cash)
    if n <= 0:
        return 0.0, 0
    return float(n) * float(px_raw) / float(px_adj), int(n)
