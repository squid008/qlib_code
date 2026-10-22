# -*- coding: utf-8 -*-
"""信号驱动回测引擎（交易信号测试 · 模式①）—— 逐日事件驱动，不走 qlib PortAnaRecord。

规则（全部来自用户 2026-09-15 的口径确认）：
  · **出信号就买入**（当日多个信号等权分配资金）；**持有 N 个交易日后卖出**；
    持有时又来信号 ⇒ **持有天数刷新为 0**（又能拿 N 天）—— 与事件研究「T+1 买入、T+1+N 卖出」同口径；
  · **成交时点**三选一：信号日收盘 / 次日开盘 / 次日收盘；
  · **严格不可成交**（默认开）：成交日**收盘涨停**买不进；选"次日开盘"时**开盘一字涨停**也买不进；
    卖出侧镜像（跌停卖不出）；**停牌两边都做不了**。买不进 = **放弃该信号**（不追）；
    卖不出 = **顺延**到下一个能卖的日子（逐日重试）；
  · **成本** `cost` = 往返（买+卖）合计费率 ⇒ 单边 `cost/2`（与连续信号 0.004 同义）；
  · **两种资金方案都算**（用户选"两种都做、界面可切换"）：
      `cash_even`  —— 当日新买入在**可用现金**内等分，用完为止（不做任何再平衡）；
      `event_even` —— **只在"买入信号成交"或"到期卖出"当天**把全部持仓再平衡到等权
                     （用户明确：**绝不做每日再平衡**，否则手续费炸裂）。
  · 资金默认 10 亿（用户要求：1000 只同时触发也能等权买得起）；按**整手 100 股**撮合。

价格口径：`$close` 后复权（收益率与真实前复权逐位等价、含分红）；停牌/涨跌停判据用真实价
`$close/$factor`（NaN = 停牌；`close_raw >= 涨停价 − 1e-6 且涨停价 > 0` = 涨停）。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Optional, Sequence, Tuple

import numpy as np
import pandas as pd

# ---------------------------------------------------------------------------
# ★★ v2.9.7（P0-B，2026-10-18 ✓）：**撮合原子已抽到 `signals/matching.py`** ✗→✓
#
# 抽走的是「手数 / 费用 / 现金约束 / 拒单文案 / 成交时点表」✓ —— 它们原先住在本文件里，
# 而 `period_roll.py` **反向 import 整个 engine** 就为了拿这几个纯函数 ✗（依赖方向错 ✓）。
#
# ⚠ 这里保留**旧名字别名** ✓：全仓库有几十处 `from .engine import _fee_of` 之类，
#   别名让它们**零改动**继续工作 ✓；新代码应直接用 `from ..signals.matching import ...` ✓。
# ⚠ 费用**只有一处**实现（`matching.fee_of` ✓）—— 资金路径与成交明细必须同源 ✓，
#   否则"明细与账对不上" ✗（v2.1.16 定下的纪律 ✓）。
# ---------------------------------------------------------------------------
# noqa: E402 —— 这个 import 故意放在常量区之后（保持原文件结构 ✓）；
# ⚠ 别把说明写进 `noqa:` 里面 ✗（ruff 会报 "Invalid noqa directive" ✓，2026-10-18 实测 ✓）。
# ★ v2.11.12（技术债 P1-#3 第十四刀）：`batch_even` 的信号折叠（纯函数 ✓）
from .batch_execs import build_batch_execs  # noqa: E402
# ★ v2.11.13（第十五刀）：逐日循环的初始状态（含"按票记持有天数"的口径 ✓）
from .batch_state import init_batch_state  # noqa: E402
# ★ v2.11.28（第十八刀）：结果装配（纯装配 ✓，见该模块的两条口径 ⚠）
from .batch_result import build_batch_result  # noqa: E402

from .matching import (                       # noqa: E402
    FILL_LAG as _FILL_LAG,
    FILL_PRICE as _FILL_PRICE,
    LOT_DEFAULT, LOT_KCB, MIN_FEE, REJECT_TEXT,
    fee_of as _fee_of,
    fit_lots as _fit_lots,
    fit_real_lots as _fit_real_lots,
    lot_of as _lot_of,
)

from ..logger import _log_swallowed  # noqa: E402



def _split_mode(spec: str) -> Tuple[str, Optional[float], str]:
    """`alloc_modes` 元素 → (基础方案, 该方案死区或 None, 净值列名)。

    允许 `"event_even@0.05"` 这种写法 ⇒ 同一个方案可以跑**多档再平衡死区**做对比
    （用户 2026-09-15：「给个"死区 5%"的对比曲线」）⇒ 列名 `event_even_band5`。
    不带 `@` 时死区取函数参数 `rebal_band`（= 界面上那一档），列名就是方案名（向后兼容）。
    """
    base, _, band_s = str(spec).partition("@")
    if not band_s:
        return base, None, base
    b = float(band_s)
    tag = ("%g" % (b * 100)).replace(".", "p")
    return base, b, "%s_band%s" % (base, tag)


@dataclass
class BtResult:
    nav: pd.DataFrame = field(default_factory=pd.DataFrame)      # index=date, 各方案净值
    trades: pd.DataFrame = field(default_factory=pd.DataFrame)
    rejects: pd.DataFrame = field(default_factory=pd.DataFrame)
    stats: Dict = field(default_factory=dict)
    diag: Dict = field(default_factory=dict)


def _prepare(signals: pd.DataFrame, panel: Dict[str, pd.DataFrame]) -> dict:
    """把面板/信号整理成 numpy（列位置一次解析，主循环里只做整数索引）。"""
    cal = pd.DatetimeIndex(panel["CALENDAR"].index)
    close = panel["CLOSE"]
    codes = sorted(set(signals["code"]) & set(close.columns))
    missing = sorted(set(signals["code"]) - set(close.columns))
    if not codes:
        return {"cal": cal, "codes": [], "missing": missing}
    col_of = {c: i for i, c in enumerate(codes)}

    def _m(key, like):
        dfw = panel.get(key)
        if dfw is None:
            return like.copy()
        return dfw.reindex(index=cal, columns=codes).to_numpy(dtype=float)

    C = _m("CLOSE", np.full((len(cal), len(codes)), np.nan))
    O = _m("OPEN", C.copy())
    CR = _m("CLOSE_RAW", C.copy())
    OR = _m("OPEN_RAW", O.copy())
    LU = panel.get("LIMIT_UP")
    LD = panel.get("LIMIT_DOWN")
    LU = (LU.reindex(index=cal, columns=codes).to_numpy(dtype=float) if LU is not None
          else np.full_like(C, np.nan))
    LD = (LD.reindex(index=cal, columns=codes).to_numpy(dtype=float) if LD is not None
          else np.full_like(C, np.nan))
    pos_of = {d: i for i, d in enumerate(cal)}
    return {"cal": cal, "codes": codes, "col_of": col_of, "pos_of": pos_of,
            "C": C, "O": O, "CR": CR, "OR": OR, "LU": LU, "LD": LD, "missing": missing}


def _bucket_signals(signals: pd.DataFrame, prep: dict) -> dict:
    """信号 → 交易日位置分桶（非交易日**顺延**到下一交易日；数据范围外丢弃）。

    ⚠ v1.20.0（2026-09-18）**向量化**：原实现是**逐事件 Python 循环** ✗ —— 过顶一次有
    **14.88 万**条触发 ✓，每条都要 `pd.Timestamp(dt)` + 标量 `cal.searchsorted(d)`（微秒级）
    ⇒ 仅这一步就要**数秒** ✓。现改为批量：`DatetimeIndex` 一次解析 + `searchsorted(数组)`
    + `Series.map`（C 实现）+ `groupby` 分桶 ✓。
    ⚠ **语义必须与旧实现逐位相同**（`tests/test_bucket_signals.py` 用旧算法副本对拍 ✓）：
      · code 不在面板 ⇒ **直接丢弃且不计入 `beyond`** ✓（旧实现最先 `continue` ✓）；
      · 早于起始日 / 晚于末尾 ⇒ 计入 `beyond` ✓（**不能**映射到首日 ✗）；
      · 非交易日 ⇒ `deferred` 计数 ✓ 并顺延到下一交易日 ✓；
      · `buys` / `exits` **各自去重**（同一 (日,股) 只留一次，买卖互不影响 ✓）。
    """
    cal, col_of = prep["cal"], prep["col_of"]
    n = len(cal)
    if n == 0 or len(signals) == 0:
        return {"buys": {}, "exits": {}, "deferred": 0, "beyond": 0, "n_mapped": 0}
    dts = pd.DatetimeIndex(signals["date"])
    cols = pd.Series(signals["code"].to_numpy()).map(col_of)
    in_col = cols.notna()
    p = cal.searchsorted(dts)
    beyond = int(((p >= n) & in_col).sum()) + int(((dts < cal[0]) & in_col).sum())
    ok = (in_col & (dts >= cal[0]) & (p < n)).to_numpy()
    pp, cc = p[ok], cols[ok].to_numpy().astype(np.int64)
    defer = int((cal[pp] != dts[ok]).sum())
    side = signals["side"].to_numpy()[ok]
    df = pd.DataFrame({"p": pp, "c": cc, "s": side}).drop_duplicates(["p", "c", "s"])
    buys = {int(k): v for k, v in
            df[df["s"] > 0].groupby("p")["c"].apply(list).to_dict().items()}
    exits = {int(k): v for k, v in
             df[df["s"] <= 0].groupby("p")["c"].apply(list).to_dict().items()}
    return {"buys": buys, "exits": exits, "deferred": defer, "beyond": beyond,
            "n_mapped": int(len(df))}


def _interleave_rejects(by_mode: Dict[str, List[dict]], cap: int) -> List[dict]:
    """各方案的被拒/顺延明细**轮转**取到 `cap` 条（每方案先各取 1 条、循环）。

    ⚠ 为什么不能简单拼接后截断（用户 2026-09-16 报「导出里没有分批次分片的结果」）：
      多方案并列时前面的方案会把名额占满（实测 12 年 + 严格口径下 batch_even 自己有 3 万多条），
      拼接后截断 ⇒ **靠后的方案一条都看不到**。轮转能保证每个方案（含 `batch_even`）都被保留到。
    每个方案的**真实总数**另在 `stats[mode]["rejects_total"]`（界面据此如实说明"共 X 条"）。
    """
    keys = [k for k in by_mode if by_mode.get(k)]
    out: List[dict] = []
    idx = {k: 0 for k in keys}
    while keys and len(out) < cap:
        for k in list(keys):
            i = idx[k]
            if i >= len(by_mode[k]):
                keys.remove(k)
                continue
            out.append(by_mode[k][i])
            idx[k] = i + 1
            if len(out) >= cap:
                break
    return out


def _limit_ctx(prep: dict, px_kind: str, strict_limit: bool, limit_judge: str = "legacy"):
    """成交价与"能不能成交"的判定 —— **主引擎与 batch_even 共用同一套口径**（避免分叉）。

    ★★ T1（审计 ② / v2.8.6，用户 2026-10-16 拍板 ✓）：**`limit_judge` = 涨停判定口径** ✓
      · `"legacy"`（**默认** ✓ 老口径）：成交日**收盘**涨停也买不进（`LIM_UP_C` ✓）——
        理由（用户原话）：「**可能开盘 1 秒拉涨停，其实你吃不到**」⇒ 保守 ✓；
      · `"open_only"`（新口径 ✓）：**只拒「开盘一字涨停 / 停牌」** ✓（不再看当日收盘 ✗）——
        更贴实盘（开盘能买到的就买得到 ✓，通常**提高**收益 ✓）。
      ⚠⚠ 老口径在 `t1_open`（**次日开盘**成交）下用的是**当日收盘**信息 = 未来信息 ✗
        （审计原文：「开盘成交用当日收盘判涨跌停」✓）—— 留着是因为它**更保守**，属用户取舍 ✓。
      ⚠ `open_only` **只在成交价为开盘时才有意义** ✗（收盘成交没有"开盘一字"这回事 ✓）——
        非开盘成交时本函数**强制退回 legacy** ✓（并把**生效值**作为第 6 个返回值交给调用方落 diag ✓，
        绝不让"选了新口径但没生效"变成静默分叉 ✗）。
      ⚠ `strict_limit=False`（不启用严格涨跌停）时两种口径**都只按停牌判** ✓（老行为不变 ✓）。

    返回 (price, can_buy, can_sell, PX, RAW, judge)：
      · `judge`（T1 新增 ✓）：**实际生效**的涨停判定口径（`legacy` / `open_only`）⇒
        调用方**必须**把它写进 `diag` ✓（否则"选了新口径但被守卫退回"会静默分叉 ✗）；
      · `PX`（v1.20.37 新增）：**规范化后的成交价矩阵**（= `where(isfinite & >0, arr, nan)` ✓）。
        ⚠ 为什么要把它暴露出来：主循环里"逐日盯市 + 三处再平衡求和"是**纯算术聚合** ✗，
        却走了 `price(...)` **闭包调用**（`price` 实测被调 588 万次/轮 ✓ 其中 86% 来自这四处 ✓）
        ⇒ 允许调用方**每日取一次行切片 `PX[i]`** 直接做数组运算 ✓（见 `run_backtest`
        「逐日盯市」处注释 ✓）。`PX[i, c]` 与 `price(i, c)` 的**快路径逐位等价** ✓
        （后者就是 `float(PX[i, c])` ✓ 无损转换 ✓）。
      · `price(i, c, kind=None)`：成交价（kind='open' 用开盘，否则收盘；非正/NaN ⇒ NaN）；
      · `can_buy(i, c)`：None 可买；否则返回拒绝原因码（suspended / limit_up / limit_up_open）；
      · `can_sell(i, c)`：镜像（limit_down / limit_down_open）。
    口径与黑名单一致：停牌两边都做不了；涨停（收盘涨停，或 open 成交时开盘一字涨停）买不进；
    跌停镜像；`strict_limit=False` ⇒ 只按停牌判。
    """
    C, O, CR, OR, LU, LD = (prep["C"], prep["O"], prep["CR"], prep["OR"],
                            prep["LU"], prep["LD"])
    # ⚠ v1.20.4（2026-09-18 性能）：**预先把整块成交价矩阵规范化**（一次性 ✓）——
    #   原 `price(i,c)` 每次调用都要做 `np.isfinite(v) and v > 0` 两个判定 + `float()`
    #   转换 ✗，而主循环里它被调用**百万次**（每日盯市 + 三处再平衡遍历 × 两种资金方案 ✓）
    #   ⇒ 仅"函数调用 + 分支"这一项就是数秒 ✓（实测这是净值曲线慢的**大头**，不是 dict 遍历 ✓）。
    #   改为预计算 `PX = where(isfinite(arr) & arr > 0, arr, nan)` ✓ ⇒ 主循环走**纯数组索引** ✓
    #   ⇒ **零调用、零分支** ✓，且**语义逐位相同** ✓（同一条件、同一结果 ✓，
    #     由 `tests/test_backtest_semantics.py` 的冻结基准守门 ✓）。
    _raw = O if px_kind == "open" else C
    PX = np.where(np.isfinite(_raw) & (_raw > 0), _raw, np.nan)
    # ★★ v2.8.4（**审计 #8** ✓）：再暴露一份"**与成交时点匹配的真实价**"矩阵 ✓ ——
    #   只用于**整手取整**（一手 = 真实 100 股 ✓，科创板 200 由 `lot_arr` 给 ✓）：
    #   引擎内部持仓记的是**复权单位**（`PX` ✓），老代码 `int(预算/复权价/lot)*lot` 取的是
    #   "**复权单位**的整手" ✗ ⇒ 真实股数 = q/f 往往**不是整手** ✗
    #   （用户 2026-10-16 举的例：现金 685、真实 10、复权 6 ⇒ 老代码买"100 复权单位"= 60 真实股 ✗）。
    #   ⚠ 面板缺 `CLOSE_RAW`/`OPEN_RAW` ⇒ `_prepare` 已退回 `C`/`O` ⇒ 这里等于复权价 ⇒ `f=1`
    #     ⇒ **退化成老行为** ✓（**不复权模式**（默认口径之一 ✓）本来 f=1 ⇒ 逐位不变 ✓）。
    _rawraw = OR if px_kind == "open" else CR
    RAW = np.where(np.isfinite(_rawraw) & (_rawraw > 0), _rawraw, np.nan)

    def price(i: int, c: int, kind: "str | None" = None):
        if kind is None:
            # ⚠⚠ v1.20.9 **试过去掉 `float()`，实测反而变慢（1.28s → 1.72s ✗），已回退** ——
            #   原因是下游 `round(np.float64)` 比 `round(float)` **慢一个量级** ✗
            #   （`builtins.round` 的 tottime 从 0.128 飙到 **0.269** ✓）。
            #   ⇒ **收益/风险都不划算，保持 `float()` 装箱** ✓（这条如实记录，避免以后再试 ✓）。
            return float(PX[i, c])              # 快路径：主循环 100% 走这里 ✓
        arr = O if (kind or px_kind) == "open" else C
        v = arr[i, c]
        return float(v) if np.isfinite(v) and v > 0 else float("nan")

    # ⚠⚠ v1.20.8 性能（2026-09-18，cProfile 实测 −12%）：把「涨跌停 / 停牌」判定
    #   **预计算成 5 个布尔矩阵**（一次性 ✓）—— 原来 `_limit` 被调 **14.9 万次**、
    #   `_suspended` 7.5 万次，合计 ≈15% 总耗时 ✗（每次都要现查 4 个矩阵 + `isfinite` + 比较 ✓）；
    #   而「过顶」有 14.88 万笔 ⇒ 又被放大 37 倍 ✓ ⇒ 属于"按笔数计费"的大头之一 ✓。
    #   ⚠ 三个判据与原来**逐位等价**（同一条件 / 同一阈值 `1e-6` ✓）：
    #     停牌 = 真实收盘价缺失 ✓；涨停价无效（NaN 或 ≤0）⇒ 原实现直接 `return False` ✓
    #     ⇒ 这里用 `isfinite(LU) & (LU > 0)` 做门 ✓ 完全一致 ✓。
    _SUSP = ~np.isfinite(CR)
    _LU_ok = np.isfinite(LU) & (LU > 0)
    _LD_ok = np.isfinite(LD) & (LD > 0)
    LIM_UP_C = _LU_ok & np.isfinite(CR) & (CR >= LU - 1e-6)     # 收盘涨停 ⇒ 买不进
    LIM_UP_O = _LU_ok & np.isfinite(OR) & (OR >= LU - 1e-6)     # 开盘一字涨停 ✓
    LIM_DN_C = _LD_ok & np.isfinite(CR) & (CR <= LD + 1e-6)     # 收盘跌停 ⇒ 卖不出
    LIM_DN_O = _LD_ok & np.isfinite(OR) & (OR <= LD + 1e-6)
    _use_open = (px_kind == "open")
    # ★ T1：口径归一 + **生效性守卫** ✓ —— 新口径只在"开盘成交"时才允许生效 ✗→✓
    #   （非开盘成交没有"开盘一字板"概念 ⇒ 强行用新口径会变成"完全不判涨跌停" ✗ ⇒ 退回老口径 ✓）
    judge_want = limit_judge if limit_judge in ("legacy", "open_only") else "legacy"
    judge = judge_want if (_use_open and strict_limit) else "legacy"

    def can_buy(i: int, c: int) -> Optional[str]:
        if _SUSP[i, c]:
            return "suspended"
        if not strict_limit:
            return None
        # 成交日**收盘涨停** ⇒ 买不进（**老口径**：次日涨停时无论开盘/收盘都不买 ✓）
        if judge == "legacy" and LIM_UP_C[i, c]:
            return "limit_up"
        if _use_open and LIM_UP_O[i, c]:
            return "limit_up_open"                     # 开盘一字涨停（两种口径都拒 ✓）
        return None

    def can_sell(i: int, c: int) -> Optional[str]:
        if _SUSP[i, c]:
            return "suspended"
        if not strict_limit:
            return None
        if judge == "legacy" and LIM_DN_C[i, c]:
            return "limit_down"
        if _use_open and LIM_DN_O[i, c]:
            return "limit_down_open"
        return None

    return price, can_buy, can_sell, PX, RAW, judge


# 成交明细的列名与**顺序**（= 旧 `_trade_row` 里 dict 的键序 ✓ 必须保持一致 ✗）
_TRADE_KEYS = ("date", "code", "side", "shares", "price", "amount", "fee",
               "reason", "mode")


def _trades_frame(rows, date_arr, codes, half):
    """把「逐笔元组」构造为成交 DataFrame（v1.20.40 / 2a：**按列**构造 ✓）。

    为什么（cProfile 实测，`ai_test/prof_nav_bt.py`）：原来是造 2 万个小 dict 再交给
    `pd.DataFrame` ✗ ⇒ 走 pandas 的 `_list_of_dict_to_arrays`，它会**为每一列再遍历一遍
    dict 列表** ✗（实测 cum **0.70 s / 20 轮 = 3.0%** ✓）。本函数直接产出 9 个列 ✓ ⇒ 避开该路径 ✓。

    ⚠ **逐位不变** ✓：字段名与**顺序**固定一致 ✓；`round` 全部用 **Python 内建** ✓
    （不用 `np.round` ✗ —— 它的 banker 舍入在二进制上与 `round` 可能有末位差异 ✗）；
    `shares` 仍 `int()` ✓；日期仍取自 `_date_arr` ✓。
    `("__ROW__", dict)`（`batch_even` 分支的**已格式化行** ✓）按 `rows` **原位置**并入 ✓
    ⇒ 混排顺序与旧实现一致 ✓（该路径下列顺序以本表固定序为准 ✓）。
    """
    cols = {k: [] for k in _TRADE_KEYS}
    for t in rows:
        if t[0] == "__ROW__":
            d = t[1]
            for k in _TRADE_KEYS:
                cols[k].append(d.get(k))
            continue
        _i, _c, _s, _q, _p, _r, _m = t
        _amt = _q * _p
        cols["date"].append(date_arr[_i])
        cols["code"].append(codes[_c])
        cols["side"].append("买" if _s > 0 else "卖")
        cols["shares"].append(int(_q))
        cols["price"].append(round(_p, 4))
        cols["amount"].append(round(_amt, 2))
        cols["fee"].append(round(_fee_of(_amt, half), 2))      # v2.1.16：与资金路径同一处 ✓
        cols["reason"].append(_r)
        cols["mode"].append(_m)
    # ⚠ 空输入保持 `pd.DataFrame()`（= 旧 `pd.DataFrame([])` ⇒ 0×0 ✓，不是 0×9 ✗）
    if not rows:
        return pd.DataFrame()
    out = pd.DataFrame(cols)
    # ★★ v2.11.21（技术债 P2-#21 清偿 ✓）：**按全列稳定排序** ✗→✓ ——
    #   原来直接返回引擎**追加顺序** ✓，而 v2.11.18 的跨平台指纹实测发现：
    #   **同一个策略、同一份数据，Windows 与 ubuntu 的明细行序不同** ✗
    #   （NAV 逐位相同 ✓、行序无关的哈希也相同 ✓ ⇒ 内容一样、只是顺序 ✗）。
    #   影响：导出的 XLSX「成交明细」页**行序在不同机器上不同** ✓ ——
    #   数值全对 ✓，但"逐位对比两份导出文件"会失败 ✗（对账/回归都受影响 ✓）。
    #   ⇒ 按**全部列**排序（`kind="stable"` ✓）：排序键完整 ⇒ **完全确定** ✓；
    #     完全相同的行互相交换也无影响 ✓（它们本来就一样 ✓）。
    #   ⚠ 只影响**输出行序** ✓，**不碰 NAV / 成交笔数 / 任何金额** ✓（指纹可证 ✓）。
    return out.sort_values(by=list(out.columns), kind="stable").reset_index(drop=True)


def run_backtest(signals: pd.DataFrame, panel: Dict[str, pd.DataFrame], *,
                 hold_days: int = 20, fill: str = "t1_open", cost: float = 0.004,
                 capital: float = 1e9, strict_limit: bool = True,
                 limit_judge: str = "legacy",
                 alloc_modes: Sequence[str] = ("event_even", "cash_even"),
                 lot: int = LOT_DEFAULT, rebal_band: float = 0.0,
                 max_positions: int = 0,
                 exit_rule=None,
                 cancel_cb=None) -> BtResult:
    """跑回测，返回**两种资金方案**的净值 + 成交/被拒明细 + 统计。

    ★ T1（v2.8.6）：`limit_judge` = **涨停判定口径** ✓（`legacy` 默认 / `open_only` 新口径 ✓）——
      详细语义见 `_limit_ctx` 的 docstring ✓；**默认值保证老路径逐位不变** ✓。

    ★★ v2.8.8b（用户 2026-10-16 ✓，"接着做真算出来" ✓）：`exit_rule` = **含入场价的离场规则**
      （`factors/exit_rules.py::EntryRule` ✓）—— 与 `signals/period_roll.py` 的**同一套**语义 ✓：
        · 每天收盘后（**当日成交之后** ✓）用「当日价格行 + **当前入场价向量**」求值 ✓
          （骨架 + 预计算列 ✓，见 `exit_rules` ✓）；
        · 触发 ⇒ 该票**挂卖出**（`sell_q` ✓）⇒ 与进场同一套**滞后**执行 ✓（`sell_q` 里
          `i < dec + lag` 那道闸门 ✓ ⇒ 不前视 ✓）；
        · 与"信号离场"（`sell_q` 里 `sell_signal` ✓）共用同一套「跌停/停牌卖不出 ⇒ 逐日顺延」✓。
      ⚠ 只有传了它才维护入场价向量 ✓（不勾离场 ⇒ **零额外成本** ✓）；
      ⚠ 求值失败 ⇒ **关掉离场并写 `diag["exit_note"]`** ✗（绝不静默退化成"和对照一样" ✗ ——
        2026-10-16 那次 39 vs 36 的事故就是栽在这一点上 ✓）。
      ⚠⚠ 注意：**下面紧接着的原有说明段（`cancel_cb` 等）仍在本 docstring 内** ✓ ——
        2026-10-16 加这段时我一度在这里提前写了**三引号**（把字符串闭合掉）✗ ⇒ 后半段变成裸代码
        ⇒ `py_compile` 当场报 `invalid character` ✓（再加一次同样的错也被它抓住 ✓）。
        ⇒ **别在这里补三引号** ✗：本 docstring 到原有那段末尾才结束 ✓。

    `cancel_cb`（v1.20.4）：可选取消检查回调 —— 主循环里**每 20 个交易日**调一次 ✓，
    约定「应取消时直接抛异常」（与 `panel_features_parallel` 同一约定 ✓）。
    ⚠ 之前**完全没有取消检查点** ✗ ⇒ 用户改了持仓周期后，旧的那次回测只能跑完 ✓
    （净值曲线 + 两资金方案 + 全持仓遍历 ⇒ 数十秒白等 ✓）。加上之后配合路由层
    "新请求作废旧请求"的 token ⇒ **改 k 时旧计算立即停** ✓。
    """
    if not len(signals) or "CALENDAR" not in panel or "CLOSE" not in panel:
        return BtResult(diag={"error": "无可用信号或价格面板"})
    fill = fill if fill in _FILL_LAG else "t1_open"
    lag, px_kind = _FILL_LAG[fill], _FILL_PRICE[fill]
    hold_days = max(1, int(hold_days))
    half = float(cost) / 2.0
    # ★ v1.20.40（2a）**循环不变量外提** ✓ —— 原写法 `max(0, hold_days - lag)` 出现在
    #   「每个持仓 × 每个交易日」的内层 ✗（cProfile：`builtins.max` **26.7 万次/轮**，
    #   其中约 **19.7 万次**来自这里 ✓）⇒ 提到循环外 ✓（`hold_days`/`lag` 全程不变 ✓ 逐位等价 ✓）。
    _expiry_thr = max(0, hold_days - lag)

    prep = _prepare(signals, panel)
    cal, codes, col_of = prep["cal"], prep["codes"], prep["col_of"]
    if not codes:
        return BtResult(diag={"error": "信号标的不在行情数据里", "missing": prep["missing"][:20]})
    C, O, CR, OR, LU, LD = (prep["C"], prep["O"], prep["CR"], prep["OR"],
                            prep["LU"], prep["LD"])
    bk = _bucket_signals(signals, prep)
    n_row = len(cal)
    # ★ v1.20.37：`PX` = 规范化成交价矩阵（见 `_limit_ctx` 说明 ✓）—— 供逐日聚合走数组 ✓
    _price, can_buy, can_sell, PX, RAW, _judge = _limit_ctx(prep, px_kind, strict_limit, limit_judge)
    # ★ v2.1.18：**按列号预算"每个标的的整手股数"** ✓（科创板 688/689 ⇒ 200 股 ✓，其余 100 ✓）——
    #   与 `period_roll` 同一套 `_lot_of` ✓（一处定义 ✓）。⚠ 逐票循环里绝不能现算字符串判断 ✗。
    #   ⚠ 尚未切换的点：`event_even` 的"买新信号"取整、`rebalance_buy`、`run_batch_backtest`
    #     （都在用统一 `lot` ✗）；本次先切 `cash_even` 一处（**整个模式切完** ✓ 不留半改 ✓）。
    lot_arr = np.fromiter((_lot_of(c, lot) for c in codes), dtype=np.int64, count=len(codes))
    # ★ v1.20.37：**日期字符串一次预生成** ✓ —— 明细/拒绝逐条 `str(cal[i].date())` 会触发
    #   pandas `Timestamp` 装箱（实测 `_box_func`+3 层 `__getitem__` 合计 **11%** ✗）
    #   ⇒ 改走 numpy 字符串数组 O(1) 取值 ✓（格式 `%Y-%m-%d` 与 `str(...date())` **逐字相同** ✓）。
    _date_arr = np.asarray(cal.strftime("%Y-%m-%d"))

    out = BtResult(
        diag={"fill": fill, "cost": cost, "capital": capital, "hold_days": hold_days,
              "strict_limit": bool(strict_limit), "lot": lot,
              # ★ T1：**生效**口径 + 请求口径都落盘 ✓（排查"选了没生效"只需看这里 ✓）
              "limit_judge": _judge, "limit_judge_requested": limit_judge,
              "n_codes": len(codes), "missing_codes": prep["missing"][:20],
              "signals_mapped": bk["n_mapped"], "signals_deferred": bk["deferred"],
              "signals_beyond_data": bk["beyond"],
              "limits_from": "inferred" if "_limits_inferred" in panel else "exchange_tag"})
    all_trades: list = []; navs: dict = {}   # all_trades 混装 dict 与元组 ⇒ 标 list（2.10.6 ✓）
    mode_rejects: Dict[str, List[dict]] = {}      # 方案 → 明细（最后轮转合并，见 _interleave_rejects）

    for mode_spec in alloc_modes:
        _base, _band, mode = _split_mode(mode_spec)
        if _base == "batch_even":
            # v1.19.76：`batch_even` 是**另一套状态机**（按批次分片，见 run_batch_backtest）
            # ⇒ 这里必须跳过，否则会被当成通用方案再跑一遍（重复成交，单测抓到）
            continue
        band = float(rebal_band) if _band is None else float(_band)
        cash = float(capital)
        sh: Dict[int, int] = {}                       # 列号 → 股数
        # ★★ v1.20.41（2b）：`dsz` 由 dict 改为 **numpy 定长数组** ✓（列号 → 已持有交易日数）——
        #   dict 版在「每个持仓 × 每个交易日」上是 1 次 `get` + 1 次 `set` ✗
        #   （cProfile：`dict.get` **44.6 万次/轮** ✗）；数组版把整段自增压成 `dsz[_ai] += 1` ✓。
        #   ⚠ 语义等价 ✓：未持有处恒为 0 ⇒ `dsz.get(c, 0)` **就是** `dsz[c]` ✓；
        #     但**清仓时必须显式置 0** ✗（原来是 `pop` ⇒ 不置 0 的话复买会继承旧持有天数 ✗）。
        dsz = np.zeros(len(codes), dtype=np.int64)

        basis: Dict[int, float] = {}                  # 列号 → 持仓成本（用于胜率统计）
        # ★★ v2.8.8b（用户 2026-10-16 ✓）：**含入场价离场规则**的逐日状态（每个资金方案各一份 ✓）——
        #   与 `period_roll` 完全同一套（那边 v2.6.0 落地 ✓）：按**列号**索引的数组 ✓
        #     · `_avg_px[c]`   = 移动平均成本（含买入费 ✓ = 券商"成本价"；**卖出不改变** ✓）；
        #     · `_first_px[c]` = **首次建仓那一笔**的每股成本 ✓（清仓后再买重新起算 ✓）；
        #     · 未持有 ⇒ NaN ⇒ 规则里与它比较恒为假 ✓（不会凭空触发 ✓）。
        #   ⚠ 只有传了 `exit_rule` 才维护 ✓（不勾离场 ⇒ 连数组都不动 ⇒ 零成本 ✓）；
        #     求值失败 ⇒ `_rule_on=False` + 写 `diag["exit_note"]` ✗（**不许静默**退化 ✓）。
        _avg_px = np.full(len(codes), np.nan, dtype=np.float64)
        _first_px = np.full(len(codes), np.nan, dtype=np.float64)
        _rule_on = exit_rule is not None
        _rule_provided = exit_rule is not None        # 供 diag 回显（中途关掉也如实报 ✓）
        _exit_note = ""
        _exit_signals = 0
        _exit_sells = 0
        # ★★ v2.1.51：**连续持有口径**（用户 2026-10-12："统一成连续持有" ✓）——
        #   每只票的**现金净额累计**（买 −(含费)、卖 +(扣费)）✓，到"总持仓归零"时结算一笔 ✓；
        #   持有天数直接用现成的 `dsz` 计数器 ✓（它按日累加持有天数 ✓，**不再**在信号刷新时清零 ✗）。
        pnl_acc: Dict[int, float] = {}
        # ★ v2.1.51：**连续持有的起点**（列号 → 首次建仓的交易日位置 ✓）——
        #   只在"从无到有"时写 ✓、只在**总持仓归零**时结算并清 ✓（都是"逐笔"开销，不进逐日热循环 ✓）。
        entry_c: Dict[int, int] = {}
        buy_q: Dict[int, List[Tuple[int, int]]] = {}  # 成交日位置 → [(列号, 决策日), …]
        # ★★ v2.9.34（P2-C ✓ 2026-10-18）：**待成交 code 集合** ✗→✓ ——
        #   原先每来一个买信号都要 `any(c == cc for lst in buy_q.values() for cc, _ in lst)` ✗
        #   ⇒ **线性扫描整个待成交队列** ✓。cProfile 实测（2000 只 × 400 日）：
        #     `any` 4,028 次 ／ 生成器迭代 **4,002,056 次** ／ 占 `run_backtest` 的 **33%** ✗；
        #     队列长度 ∝ 票池 ✗ ⇒ 真实全 A 场景会退化成分钟级 ✓。
        #   ⇒ 用 set 记"队列里已有哪些 code" ✓ O(1) ✓；**语义等价** ✓
        #     （原判据就是"该 code 是否已在任一待成交队列里" ✓）。
        #   ⚠ 全文件只有 3 处写 `buy_q`（初建 / 入队 / 出队 ✓）⇒ 集合只在这三处维护 ✓，不会漏 ✓。
        buy_pending: set = set()
        sell_q: Dict[int, Tuple[int, str]] = {}       # 列号 → (决策日, 原因)
        trades: List[dict] = []
        rejects: List[dict] = []
        nav_vals: List[float] = []
        n_refresh = n_defer_sell = 0
        fee_sum = 0.0
        wins = losses = 0
        hold_sum = hold_n = 0
        min_cash = float(capital)          # 现金最低水位（<0 ⇒ 资金给少了，等效加杠杆）

        def _log_trade(i, c, side, qty, price, reason):
            nonlocal cash, fee_sum, wins, losses, hold_sum, hold_n
            amt = qty * price
            fee = _fee_of(amt, half)
            fee_sum += fee
            if side > 0:
                cash -= amt + fee
                if sh.get(c, 0) <= 0:
                    entry_c[c] = i                                 # ★ v2.1.51：从无到有 ⇒ 记连续持有起点 ✓
                    # ★ v2.8.8b：**首次入场价** ✓（只有"从无到有"那一笔记 ✓，加仓**不刷新** ✓ ——
                    #   与 `period_roll` 的 `first_px` 语义逐条一致 ✓）
                    if _rule_on:
                        _first_px[c] = ((amt + fee) / qty) if qty else np.nan
                sh[c] = sh.get(c, 0) + qty
                basis[c] = basis.get(c, 0.0) + amt + fee          # 含买入费的成本
                pnl_acc[c] = pnl_acc.get(c, 0.0) - (amt + fee)     # ★ v2.1.51：现金流累计 ✓
                if _rule_on:                                       # ★ v2.8.8b：移动平均成本（含费 = 券商成本价 ✓）
                    _avg_px[c] = (basis[c] / sh[c]) if sh[c] > 0 else np.nan
            else:
                cash += amt - fee
                held = sh.get(c, 0)
                b = basis.get(c, 0.0)
                avg = (b / held) if held > 0 else 0.0              # 每股成本（含费）
                pnl_acc[c] = pnl_acc.get(c, 0.0) + (amt - fee)     # ★ v2.1.51：现金流累计 ✓
                sh[c] = held - qty
                basis[c] = max(0.0, b - avg * qty)
                if _rule_on:
                    # ★ v2.8.8b：**卖出不改变"每股成本"** ✓ —— 按比例结转 ⇒ `(B−B·p/Q)/(Q−p) ≡ B/Q` ✓
                    #   （券商口径也是这个 ✓；与 `period_roll` 同一处逻辑 ✓）
                    _avg_px[c] = (basis[c] / sh[c]) if sh[c] > 0 else np.nan
                if sh[c] <= 0:
                    # ★★ v2.1.51：**总持仓归零 ⇒ 才结算一笔** ✗→✓（用户 2026-10-12："统一成连续持有" ✓）
                    #   原来：每次卖出都记一笔 ✗；再叠加"信号刷新就把 `dsz` 清零"✗ ⇒ 量出来的是
                    #   **每轮"买入→到期卖出"的往返**（恒 = h = 60 ✗），**不是**"一只票被连续持有
                    #   多少天" ✗（用户 2026-10-12 指出："事件驱动再平衡明显不是只持有 60 天" ✓）。
                    #   现在：`dsz[c]` 不再在刷新时清零 ✗ ⇒ 这里它就是**首次建仓 → 清仓的交易日数** ✓；
                    #   胜负也按**这一笔的现金净额**判 ✓（不再按每笔卖出判 ✓）。
                    hold_sum += max(0, i - int(entry_c.get(c, i)))   # ★ v2.1.51：连续持有的交易日数 ✓
                    hold_n += 1
                    if pnl_acc.get(c, 0.0) > 0:
                        wins += 1
                    else:
                        losses += 1
                    sh.pop(c, None)
                    dsz[c] = 0                 # ★ 2b：数组版 ⇒ 显式清 0 ✓（等价于原 `pop` ✓）
                    basis.pop(c, None)
                    if _rule_on:               # ★ v2.8.8b：清仓 ⇒ 入场价失效 ✓（下次买回重新起算 ✓）
                        _avg_px[c] = np.nan
                        _first_px[c] = np.nan
                    pnl_acc.pop(c, None)
                    entry_c.pop(c, None)
            # ⚠⚠ v1.20.7 性能（2026-09-18 cProfile 实测）：**逐笔明细构造占总耗时约 26%** ✗
            #   （`str(cal[i].date())` 触发 pandas `Timestamp` 装箱 0.185s + 4 个 `round()` 0.128s ✓，
            #   而过顶有 **14.88 万笔** ⇒ 这是**最大的"逐笔开销"** ✓）。
            #   ⇒ 循环里**只存元组**（7 个原生值，零格式化 ✓），`str`/`round`/dict 组装全部挪到
            #     **末尾一次性做** ✓ ⇒ 语义逐位相同（末端生成的正是原来那些字段 ✓）。
            # ★★ v2.8.4（审计 #8 ✓）：成交明细按**真实口径**输出（真实股数 × 真实价 = 真实金额 ✓）
            #   —— 账目仍按**复权单位**算（上面 `amt = qty*price` ✓，两者金额恒等 ✓）；
            #   ⚠ 为什么要换展示口径：复权单位与真实股数只差一个因子（`q_adj = n_real/f` ✓）⇒
            #     老写法明细里印的是复权单位 ✗ ⇒ 读者 `股数×价` 对不上金额 ✗、
            #     而且**不是整手倍数** ✗（`tests/test_lot_by_market.py` 一直守着这条 ✓ ——
            #     这轮就是它先把"明细写复权单位"挡回来的 ✓）。
            #   ⚠ 卖出是"折回真实股数后取整" ⇒ 可能差不到 1 股（⇒ 明细的费与账目差几分钱 ✓），
            #     **买入恒精确**（本来就是按真实整手买的 ✓）。
            _px_disp = float(RAW[i, c])
            if np.isfinite(_px_disp) and _px_disp > 0:
                trades.append((i, c, side,
                               int(round(float(qty) * float(price) / _px_disp)),
                               _px_disp, reason, mode))
            else:
                trades.append((i, c, side, int(qty), price, reason, mode))

        def _reject(i, c, reason, decision_pos=None):
            # ⚠⚠ v1.20.20 性能（2026-09-18，与 v1.20.7 给 `_log_trade` 做的同一件事 ✓）：
            #   原实现**在热循环里**就构造 dict + 调 `str(cal[i].date())` ✗ —— 后者触发
            #   pandas `Timestamp` 装箱与格式化；过顶这类公式 `rejects` 可达**几万条** ✗
            #   （严格口径下涨跌停/停牌/资金不足每次重试都记一条 ✓）。
            #   ⇒ 循环里**只存元组**（4 个原生值 ✓ 零格式化 ✓），`str`/dict 组装挪到
            #     `for mode_spec` 轮次结束后**一次性做** ✓（那里不在热路径 ✓）
            #   ⇒ 语义逐位相同（末尾生成的正是原来那些字段 ✓）。
            rejects.append((i, c, reason, decision_pos))

        _EMPTY_I = np.empty(0, dtype=np.int64)

        def _act_views(pxr):
            """当日**活跃持仓的保序视图** → `(列索引数组, 列索引列表, 股数列表, 价格列表)` ✓。

            ★★ v1.20.41（2b）：把「遍历 dict + 逐项 `_pxr[k]` + `np.isfinite`」✗ 换成
              **一次 C 层转换** ✓（`np.fromiter` / `ndarray.take` / `list`）——
              这是本引擎**最大的内联热点**（`run_backtest` 自身字节码占 **61.6%** ✗）的主要来源 ✓。

            契约（三条都必须守住 ✗）：
              · **顺序 = `sh` 的插入序** ✓ —— `np.fromiter` 走 dict 迭代器，顺序不变 ✓
                （`event_even` 的 ①减仓 → ②买新 → ③补低配**都按迭代序执行**，
                 顺序会改变现金与成交结果 ✗）；
              · 价格转成**原生 `float`** ✓ ⇒ 后续乘加是纯 Python float 运算 ✓，
                与原 `sh[k] * float(PX[i, k])` **逐位相同** ✓；
              · NaN 判定改用 `v == v` ✓ —— `PX` 已保证「非正 / 非有限 ⇒ NaN」 ✓
                ⇒ 与 `np.isfinite(v)` **完全等价** ✓，但**没有 numpy 调用开销** ✓
                （实测内联循环里 `np.isfinite` 是隐形大头 ✗）。
            """
            _n = len(sh)
            if not _n:
                return _EMPTY_I, [], [], []
            _ai = np.fromiter(sh.keys(), dtype=np.int64, count=_n)
            return (_ai, _ai.tolist(),
                    np.fromiter(sh.values(), dtype=np.int64, count=_n).tolist(),
                    pxr.take(_ai).tolist())

        # ★ v1.20.46 修 BUG（用户 2026-09-22 审计 ✓，真问题 ✓）：
        #   原式末尾多了个**常量 `[0]`** ✗ —— `min([a, b, 0])` 在 a, b ≥ 0 时**恒为 0** ✗
        #   ⇒ `i_start` 永远是 0 ⇒ 净值**恒从 `cal[0]` 起画** ✗（把事件前的空转天数也算进
        #   年化分母 ✓、曲线开头挂一段无意义的平段 ✓）。
        #   原意显然是"取**最早**事件的前一天" ✓ ⇒ 去掉那个 `[0]` 即可 ✓（`min` 本身就是下界 ✓），
        #   外层 `max(0, …)` 兜住"完全没有事件"（`default=0` ⇒ −1 ⇒ 0 ✓）的情况 ✓。
        i_start = max(0, min([min(bk["buys"], default=0) - 1,
                              min(bk["exits"], default=0) - 1]))
        for i in range(i_start, n_row):
            # 取消检查点（v1.20.4）：每 20 天一次 ⇒ 取消响应 ≤ 20 个交易日的计算量 ✓
            #   （⚠ 不能每天都调：回调本身有开销，而本循环要跑数千天 ✓）
            if cancel_cb is not None and (i - i_start) % 20 == 0:
                cancel_cb()
            # ★ v1.20.37 性能：**当日成交价行切片**（纯算术聚合直接用 `_pxr[k]` ✓）——
            #   实测 `price()` 闭包被调 **588 万次/轮**，其中 **86%** 来自
            #   「逐日盯市」+「两处市值求和」这四处纯算术 ✗ ⇒ 全部改走数组 ✓。
            _pxr = PX[i]
            # ---- 1) 到期/信号卖出 ----
            for c in list(sell_q.keys()):
                dec, reason = sell_q[c]
                if i < dec + lag or c not in sh:
                    continue
                bad = can_sell(i, c)
                if bad:
                    n_defer_sell += 1
                    _reject(i, c, bad, dec)
                    continue
                px = float(_pxr[c])            # ★ 2b：闭包 ⇒ 行切片（逐位相同 ✓）
                if px != px:                   # ★ 2b：`not np.isfinite(px)` ⟺ `px != px` ✓
                    _reject(i, c, "no_price", dec)
                    continue
                # ⚠★ v2.1.51：**删掉这里的 `hold_sum += dsz[c]; hold_n += 1`** ✗→✓ ——
                #   原来"每卖一次就记一笔" ✗ ⇒ 加上"刷新即清零" ⇒ 量出来是**每轮买卖往返**
                #   （恒 = h = 60 ✗）。连续持有的结算已挪进 `_log_trade` 的"**持仓归零**"分支 ✓。
                _log_trade(i, c, -1, sh[c], px, reason)
                # ★★ v2.8.9（用户 2026-10-17 报障 ✓）：**两条离场通道都要计入** ✗→✓ ——
                #   用户看到离场弹窗里「离场卖出 **0** 笔」✗、却同时有 1844 笔"顺延" ⇒ 自相矛盾 ✓。
                #   根因：引擎里两条离场通道的 `reason` **不同名** ✗ ——
                #     · 矩阵/掩码离场（S点这类**不带入场价**的公式 ✓）⇒ `sell_signal`（见上面
                #       第 2 步 `for c in bk["exits"]…` ✓）；
                #     · 含入场价的规则离场 ⇒ `exit_signal`（v2.8.8b 新加 ✓）。
                #   而我上一版的计数器**只数后者** ✗ ⇒ 走掩码的离场全都数不到 ⇒ 界面显示 0 ✓
                #   （用户据此完全无法判断"S 点到底卖没卖" ✓）。⇒ 两个都数 ✓。
                if reason in ("exit_signal", "sell_signal"):
                    _exit_sells += 1
                sell_q.pop(c, None)
            # ---- 2) 信号日：刷新持有天数（已持有 ⇒ 归零，卖单撤回）----
            for c in bk["buys"].get(i, []):
                if c in sh:
                    # ⚠★ v2.1.51：`dsz[c] = 0` **必须保留** ✗→✓（我一度删掉，实测会**天天挂到期卖单** ✗✗）——
                    #   因为 `dsz` **兼任**"距上次信号是多少天"（第 4 步的到期判据 `dsz[c] >= thr` ✓）：
                    #   不清零 ⇒ 刷新后第二天又满足到期条件 ⇒ 立刻卖出 ⇒ 行为被改坏 ✗。
                    #   ⇒ "连续持有"改用**另一个**表 `entry_c` 记（见下 ✓），与 `dsz` 职责分开 ✓。
                    dsz[c] = 0
                    sell_q.pop(c, None)
                    n_refresh += 1
                elif c not in buy_pending:          # ★ v2.9.34：O(1) 替代线性扫描 ✓（语义相同 ✓）
                    buy_q.setdefault(i + lag, []).append((c, i))
                    buy_pending.add(c)
            for c in bk["exits"].get(i, []):
                if c in sh and c not in sell_q:
                    sell_q[c] = (i, "sell_signal")
            # ---- 3) 买入成交（含再平衡）----
            pend = buy_q.pop(i, [])
            if pend:
                # ★ v2.9.34（P2-C ✓）：出队同步维护集合 ✓ —— 这些 code 已不在待成交队列里 ✓
                #   （原实现的 `any(...)` 也只看"队列里有没有"✓ ⇒ 出队后即可再次入队 ✓，语义一致 ✓）
                buy_pending.difference_update({_c for _c, _ in pend})
                ok: List[Tuple[int, int]] = []
                for c, dec in pend:
                    bad = can_buy(i, c)
                    if bad:
                        _reject(i, c, bad, dec)
                        continue
                    _pc = float(_pxr[c])            # ★ 2b：闭包 ⇒ 行切片 + `!=` 判 NaN ✓
                    if _pc != _pc:
                        _reject(i, c, "no_price", dec)
                        continue
                    if c in sh:                     # 排队期间已被别的信号买进/刷新
                        n_refresh += 1
                        dsz[c] = 0
                        sell_q.pop(c, None)
                        continue
                    ok.append((c, dec))
                # ★★ v2.9.3（用户 2026-10-17 ✓）：**最大同时持仓数**上限 ✗→✓ ——
                #   为什么需要它 ✗：信号若"每只票每 ~23 个交易日就重触发一次"，而持有期 60 天
                #   ⇒ 组合里**几乎每只票都一直持有** ⇒ 策略退化成"全市场等权满仓" ✗
                #   ⇒ 信号完全不体现在结果里 ✓（2026-10-17 实测：hold=60 时策略与参照线只差 0.33% ✓）。
                #   ⇒ 用 `max_positions` 卡住"同时持有多少只" ✓：**先到先得**（按信号到达顺序 ✓），
                #     超出的新信号**明确拒单**（reason=`max_positions` ✓，导出/明细可数 ✓），
                #     已持有的老仓不动 ✓（到期/卖出腾出名额后，新信号才能进 ✓）。
                #   ⚠ 只为"首次建仓"设卡 ✓；`c in sh`（续持/刷新）在上面的分支已 `continue` ✗ 不受影响 ✓。
                if max_positions > 0 and mode != "batch_even":
                    _room = max(0, int(max_positions) - len(sh))
                    if len(ok) > _room:
                        for _c, _dec in ok[_room:]:
                            _reject(i, _c, "max_positions", _dec)
                        ok = ok[:_room]
                if ok:
                    if mode == "cash_even":
                        # ⚠ 份额要按「价 ×(1+单边费)」算，否则把费漏掉会现金微负（单测抓到）
                        # ★ v2.8.3（审计 #9 ✓）：**再把最低佣金预留出来** ✓ —— 原来只预留比例费 ✗
                        #   ⇒ 小额单被 5 元顶破 ⇒ 现金变负 ✗（用户："肯定不能出现负数" ✓）
                        _n_ok = max(1, len(ok))
                        _per = max(0.0, (cash - MIN_FEE * _n_ok) / _n_ok) / (1.0 + half)
                        for c, _dec in ok:
                            px = _price(i, c)
                            _lt = int(lot_arr[c])           # v2.1.18：按标的自身手数取整 ✓
                            # ★ v2.8.4（审计 #8 ✓）：**按真实股数的一手**取整（含最低佣金 ✓）
                            q, _n = _fit_real_lots(RAW[i, c], px, _lt, half, cash, budget=_per)
                            if q <= 0:
                                _reject(i, c, "no_cash")
                                continue
                            _log_trade(i, c, 1, q, px, "buy_signal")
                    else:                            # event_even：只在成交当日把全体再平衡到等权
                        # ★ v1.20.37：改数组行切片（原 genexpr 里 `_price` 被调**两遍/项** ✗）；
                        #   保持 `list(sh)` 原顺序 + `mv` 从 0.0 起 ⇒ 浮点结合序不变 ✓ 逐位等价 ✓
                        mv = 0.0
                        for k in sh:             # ★ 2b：`_v == _v` 替代 `np.isfinite(_v)` ✓
                            _v = float(_pxr[k])
                            if _v == _v:
                                mv += sh[k] * _v
                        n_tgt = len(sh) + len(ok)
                        tgt = (cash + mv) / max(1, n_tgt)
                        # ⚠⚠ 顺序必须是「先减仓腾现金 → 再买新信号 → 最后补低配」，否则：
                        #   新信号先花现金 ⇒ 现金被首日信号吃光 ⇒ 后续信号全被判 no_cash 拒掉，
                        #   而"有信号就调平到等权"的本意正是**卖掉超配的老仓**去接新信号
                        #   （2026-09-15 单测 `test_event_even_rebalances_after_new_signal` 抓到）。
                        # 死区：偏差小于「目标市值的 rebal_band」就不动（默认 0 = 每次事件完全调平，
                        # 即用户口径"有信号/到期就平衡"）。实测全调平的代价是 9 年 45% 本金的费用
                        # ⇒ 想让成本下来就把 band 调到 1%~10%（界面上可调）。
                        # ★ v1.20.40（2a）：三处**等价但更快**的写法 ✓
                        #   ① 原 `gap_of(tgt, px)` = `max(band*tgt, px*lot)` **每个持仓现算** ✗
                        #      ⇒ `dlt > max(a, b)` ⟺ `dlt > a and dlt > b` ✓（a、b 均有限 ✓）
                        #      ⇒ `band*tgt` 外提 ✓（那个 lambda 也就不需要了 ✓）。
                        #   ② 原 `k in [c for c, _ in ok]` 在**每个持仓**上都重建一次列表 ✗
                        #      （O(持仓数 × 待买数) ✗）⇒ 改**集合** ✓（键都是 int ⇒ 判定等价 ✓）。
                        #   ③ `_price(i, k)` 闭包调用 ⇒ `float(_pxr[k])` ✓
                        #      （`_price` 快路径就是 `float(PX[i, k])` ✓ **逐位相同** ✓）。
                        #      ⚠ 保留 `float()` 装箱：下游 `round(np.float64)` 反而慢一档 ✗
                        #        （见 `_limit_ctx` 里 v1.20.9 的实测记录 ✓）。
                        _gap_bt = band * tgt
                        _okc = {c for c, _ in ok}
                        # ① 减仓超配（腾出现金给新信号）
                        for k in list(sh.keys()):
                            px = float(_pxr[k])
                            if px != px or k in _okc:     # ★ 2b：`!=` 判 NaN（等价更快 ✓）
                                continue
                            dlt = sh[k] * px - tgt
                            _lt = int(lot_arr[k])          # v2.1.19：按标的自身手数（200/100 ✓）
                            if dlt > _gap_bt and dlt > px * _lt and can_sell(i, k) is None:
                                # ★ v2.8.4（审计 #8 ✓）：**卖出量也按"真实股数的一手"** ✓
                                #   （且不超过持仓 ✓；`cash=inf` ⇒ 这里只管手数 ✓）
                                _qs, _ns = _fit_real_lots(RAW[i, k], px, _lt, half,
                                                          float("inf"), budget=dlt)
                                q = min(_qs, sh[k])
                                if q > 0:
                                    _log_trade(i, k, -1, q, px, "rebalance_sell")
                        # ② 买新信号（含费；现金不足则按比例缩减）
                        tgt_eff = tgt / (1.0 + half)
                        # ★ 同一项原来把 `_price(i, c)` **算了两遍** ✗（实测 0.70 s/20 轮 ✓）；
                        #   `need` 从 `0.0` 起、按 `ok` **原序**累加 ⇒ 与 `sum(...)` 结合序一致 ✓
                        need = 0.0
                        for c, _dec in ok:
                            _lt = int(lot_arr[c])      # v2.1.19：同上（预算是"该票的手数" ✓）
                            # ★ v2.8.4（审计 #8 ✓）：预算估算也要走**真实股数的一手** ✓
                            #   （否则 `scale` 按"复权单位整手"估 ⇒ 与实际买入粒度不一致 ⇒
                            #    估多了白缩量、估少了白拒单 ✓；`_pxr` 仍是复权价、留给下面用 ✓）
                            _vr = RAW[i, c]
                            if _vr != _vr or _vr <= 0:
                                continue
                            need += int(tgt_eff / _vr / _lt) * _lt * _vr * (1 + half)
                        scale = 1.0 if need <= cash * 1.0001 else max(0.0, cash / max(need, 1e-9))
                        for c, _dec in ok:
                            px = _price(i, c)
                            _lt = int(lot_arr[c])
                            # ★★ v2.1.30：**成交前按「真实费用」硬校验一次现金** ✗→✓（审计 #9 ✓）
                            #   —— 估算都用「成交额 × (1 + 半费)」✗，而真实费用是
                            #   `_fee_of(amt, half) = max(amt×half, 最低 5 元)` ✓ ⇒ 小额成交上两者不等
                            #   ⇒ 现金会被花成**负数**（实测 `event_even` 到 **−3216.71 元** ✗）。
                            #   v2.8.3：收敛到 `_fit_lots`（同一处逻辑 ✓，三处调用点不再各写一份 ✓）
                            #   v2.8.4（审计 #8 ✓）：再改成**真实股数的一手**（`_fit_real_lots` ✓）
                            q, _n = _fit_real_lots(RAW[i, c], px, _lt, half, cash,
                                                   budget=tgt_eff * scale)
                            if q <= 0:
                                _reject(i, c, "no_cash")
                                continue
                            _log_trade(i, c, 1, q, px, "buy_signal")
                        # ③ 补低配的老仓（先算新目标：新买入已计入 sh ⇒ tgt 用最新只数重算）
                        # ★ v1.20.37：同上（`_pxr` 行切片 + 原顺序 ✓ 逐位等价 ✓）
                        mv = 0.0
                        for k in sh:                 # ★ 2a：本循环不改 `sh` ⇒ 无需 list() ✓
                            _v = float(_pxr[k])      # ★ 2b：原生 float + `==` 判 NaN ✓
                            if _v == _v:
                                mv += sh[k] * _v
                        tgt = (cash + mv) / max(1, len(sh))
                        _gap_bt = band * tgt         # ★ 2a：`tgt` 已重算 ⇒ 死区同步重算 ✓
                        for k in list(sh.keys()):
                            px = float(_pxr[k])      # ★ 2a：闭包调用 ⇒ 行切片（逐位相同 ✓）
                            if px != px:             # ★ 2b：`!=` 判 NaN（等价更快 ✓）
                                continue
                            dlt = tgt - sh[k] * px
                            _lt = int(lot_arr[k])          # v2.1.19：补低配同样按标的自身手数 ✓
                            if dlt > _gap_bt and dlt > px * _lt and can_buy(i, k) is None:
                                # ★★ v2.1.30 / v2.8.3（审计 #9 ✓）：同 ② —— 用**真实费用**
                                #   （含最低佣金 ✓）取"买得起的最大手数" ⇒ 绝不透支 ✓
                                #   v2.8.4（审计 #8 ✓）：按**真实股数的一手**取整 ✓
                                q, _n = _fit_real_lots(RAW[i, k], px, _lt, half, cash,
                                                       budget=dlt / (1.0 + half))
                                if q > 0:
                                    _log_trade(i, k, 1, q, px, "rebalance_buy")
            # ---- 4) 到期卖出（在当日**开头状态**下判 days ≥ N − 成交延迟）----
            # ⚠ 决策日必须按成交延迟前移：`t1_open/t1_close` 的成交在决策日的**次日**，
            #   若仍写 `days >= N` 就会持有 N+1 个交易日（单测 test_basic_buy_hold_sell_and_cost
            #   抓到）。`t_close`（lag=0）不变 ⇒ 两种口径的**实际持有都正好 N 个交易日**，
            #   与事件研究「T+1 买入、T+1+N 卖出」一致。
            # ★★ v1.20.41（2b）：**向量化「该不该挂到期卖单」** ✓ —— 原为每个持仓逐项
            #   `c not in sell_q and dsz.get(c, 0) >= thr` ✗ ⇒ 先取好**活跃持仓的保序视图** ✓，
            #   再用 `dsz[_ai] >= thr` + `np.isin(_ai, sell_q.keys())` **一次筛出** ✓，
            #   **只有真正到期的**才进 Python 循环 ✓（通常远少于持仓数 ✓）。
            #   ⚠ 顺序等价 ✓：`_ai` 按 `sh` **插入序** ✓、布尔掩码与 `isin` 都**保序** ✓
            #     ⇒ 写进 `sell_q` 的**次序与旧实现逐条相同** ✓
            #     （`sell_q` 的顺序会影响后续成交顺序 ✗ ⇒ 这一步不能省 ✓）。
            #   ⚠ 这一份视图**「逐日盯市」继续复用** ✓（`sh` 在此期间不变 ✓）。
            _ai, _ak, _aq, _ap = _act_views(_pxr)
            # ⚠⚠⚠ v1.20.41 **实测教训（别再犯 ✗）**：这里**曾**写成
            #   `dsz[_ai] >= thr` + `np.isin(_ai, sell_q.keys())` 的"向量化筛选" ✗ ——
            #   实测 `numpy._in1d` 单独吃掉 **11.9%（1.99 s / 20 轮）** ✗✗，
            #   比原来的 Python 循环**慢得多** ✗。原因：**持仓规模只有几十~几百** ✓，
            #   `np.isin` 的 O(n log m) 常数 + 临时数组分配，远大于"几十次 dict 成员判定" ✗。
            #   ⇒ **该处退回普通循环** ✓。
            #   ★ 结论（写死在这里）：**numpy 只在大数组上才划算** ✗ ——
            #     几千次的"小集合逐项 Python 判定"反而更快 ✓；本文件里凡是 n≈持仓数 的地方，
            #     **不要**上 `np.isin`/`np.interp` 这类"每调用一次就建临时数组"的 API ✗。
            for c in sh:
                if c not in sell_q and dsz[c] >= _expiry_thr:
                    sell_q[c] = (i, "sell_expiry")
            # ---- 4b) ★★ v2.8.8b：**含入场价离场规则**求值（当日成交**之后** ✓ ⇒ 用的是"今天收盘时
            #      的持仓 + 入场价" ✓；`sell_q` 那道 `i < dec + lag` 闸门 ⇒ **滞后 lag 天执行** ✓
            #      ⇒ 与进场同一套滞后、不前视 ✓）----
            #   ⚠ 与 `period_roll` 的 4 条语义**逐条一致** ✓：调仓/信号刷新可撤单（上面 step 2 ✓）、
            #     跌停/停牌卖不出 ⇒ 逐日顺延（step 1 ✓）、持币期间触发 ⇒ 不产生卖单（只遍历 `sh` ✓）。
            if _rule_on:
                try:
                    _row_ex = exit_rule.eval_row(i, _avg_px, _first_px)
                    for c in sh:
                        if _row_ex[c] and c not in sell_q:
                            sell_q[c] = (i, "exit_signal")
                            _exit_signals += 1
                except Exception as _e:                                        # noqa: BLE001
                    # ★ 求值失败 ⇒ **关掉离场** + 留 note ✓（回测绝不崩 ✓；但**绝不静默** ✗ ——
                    #   2026-10-16 那次"39 vs 36"就是静默退化成"和对照一样"✗ ⇒ 现在一定会说出来 ✓）
                    _exit_note = _exit_note or ("入场价离场规则求值失败：%r" % (_e,))
                    _rule_on = False
            # ---- 5) 逐日盯市 ----
            # ★★ v1.20.37 性能（2026-09-21 cProfile 实测，工具 `ai_test/prof_nav_bt.py` ✓）：
            #   这一行是**单点最大热点** —— tottime 5.457s + cumtime 7.809s / 36.115s = **21.6%** ✗
            #   （`engine.py:454 <genexpr>` ✓）。三层浪费：
            #     ① 每项都走 `_price(i, k)` **闭包调用** ✗（`price` 实测 588 万次/轮 ✓）；
            #     ② 同一项把 `_price(i, k)` **算了两遍**（`np.isfinite()` 里又调一次 ✗）；
            #     ③ genexpr 逐项 yield 的生成器开销 ✗。
            #   ⇒ 改为「行切片 + 显式循环」✓，**逐位等价**：
            #     · `_pxr[k]` 与 `_price(i, k)` 的快路径同为 `PX[i, k]` ✓（后者只多一次 `float()` ✓）；
            #     · `mv` 从 `0.0` 起、按 `sh` **原迭代序**累加 ⇒ 与 `cash + sum(...)` 的浮点
            #       结合序**完全一致** ✓；NaN 项按原样加 `0.0` ✓（不改变和 ✓）。
            #     由 `tests/test_backtest_semantics.py`（冻结基准 ✓）逐位守门 ✓。
            # ★★ 2b：直接复用上面那份**保序视图** ✓（全是 Python 原生 float ⇒ 原生乘加 ✓，
            #   没有 numpy 标量运算、也没有 `np.isfinite` 调用 ✗）——
            #   求和**按 `sh` 原插入序**、`mv` 从 `0.0` 起 ✓
            #   ⇒ 与旧实现 `cash + sum(...)` 的浮点结合序**逐位相同** ✓。
            mv = 0.0
            for _q, _v in zip(_aq, _ap):
                mv += _q * _v if _v == _v else 0.0
            equity = cash + mv
            if cash < min_cash:
                min_cash = cash
            # ★ v1.20.37：`nav_dates` 去掉逐日 `cal[i]` **装箱** ✗（pandas `Timestamp`
            #   构造实测占 11% ✓）—— 净值索引在轮次末尾用 `cal[i_start:n_row]` **切片** ✓
            #   （每天必有且仅有一条 nav ✓ ⇒ 长度恒等 ✓）。
            nav_vals.append(equity / capital)
            # ★★ 2b：整段「逐股自增」压成**一次花式索引** ✓
            #   （整数加法 ⇒ 与顺序无关 ⇒ 逐位等价 ✓）
            if len(_ai):
                dsz[_ai] += 1
        # ★ v1.20.37：索引直接用**日历切片**（每日必有一条 nav ✓ ⇒ 长度恒等 ✓），
        #   省掉主循环里逐日 `cal[i]` 的 Timestamp 装箱 ✓
        navs[mode] = pd.Series(nav_vals, index=cal[i_start:n_row])
        all_trades += trades
        # ⚠ v1.20.20：这里（**热循环之外** ✓）一次性把元组展开成 dict ⇒ 字段与原来逐位相同 ✓
        mode_rejects[mode] = [
            {"date": _date_arr[_i], "code": codes[_c], "reason": _rs,
             "text": REJECT_TEXT.get(_rs, _rs),
             "signal_date": (_date_arr[_dp] if _dp is not None else None),
             "mode": mode}
            for (_i, _c, _rs, _dp) in rejects]
        closed = hold_n
        net = navs[mode].pct_change().fillna(0.0).to_numpy()
        perf = _perf(net)
        out.stats[mode] = {
            # ★★ v2.8.8b（用户 2026-10-16 ✓）：**含入场价离场通道**的回显 ✓ ——
            #   键名与 `period_roll` 的**逐字对齐** ✓（`exit_enabled`/`exit_rule`/`exit_signals`/
            #   `exit_sells`/`exit_note` ✓ ⇒ 前端与探针可用同一套读法 ✓）。
            #   ⚠ `exit_note` = **未生效原因** ✓（求值失败时非空 ⇒ 必须显示给用户 ✓，
            #     否则又回到"含离场与对照一模一样却不知道为什么"✗）。
            "exit_enabled": bool(_rule_provided or bk["exits"]),
            "exit_rule": bool(_rule_provided),
            "exit_signals": int(_exit_signals),
            "exit_sells": int(_exit_sells),
            "exit_note": _exit_note,
            "alloc": _base,
            "rebal_band": band,
            "min_cash": round(float(min_cash), 2),
            # ★ v2.8.3（审计 #9 ✓）：**现金绝不允许为负** —— 这里把它变成可断言的字段 ✓
            #   （用户 2026-10-16："肯定不能出现负数" ✓；已有单测守 ✓）。它出现在界面/导出里
            #   就等于"有 bug"，不是"资金给少了"的提示 ✓（老注释那句"等效加杠杆"已作废 ✗）。
            "min_cash_negative": bool(min_cash < -1e-6),
            "final_nav": round(float(navs[mode].iloc[-1]), 4) if len(navs[mode]) else None,
            "total_return": (round(float(navs[mode].iloc[-1] - 1), 4) if len(navs[mode]) else None),
            "trades": len(trades),
            # ⚠ 元组索引：`t[2]` = side（>0 买 / <0 卖 ✓）——比原来 `t["side"] == "买"` 快且等价 ✓
            "buys": sum(1 for t in trades if t[2] > 0),
            "sells": sum(1 for t in trades if t[2] < 0),
            "fees_paid": round(fee_sum, 2),
            "fee_ratio_of_capital": round(fee_sum / max(1.0, capital), 4),
            "hold_refreshes": n_refresh,
            "sell_deferrals": n_defer_sell,
            "closed_trades": closed,
            "avg_hold_days": round(hold_sum / closed, 1) if closed else None,
            "win_rate": round(wins / (wins + losses), 3) if (wins + losses) else None,
            "open_positions_end": len(sh),
            # ⚠ v1.20.20：`rejects` 现在是元组 `(i, c, reason, decision_pos)` ⇒ 取 `[2]` ✓
            "rejects_limit_up": sum(1 for r in rejects if r[2].startswith("limit_up")),
            "rejects_suspended": sum(1 for r in rejects if r[2] == "suspended"),
            "rejects_no_cash": sum(1 for r in rejects if r[2] == "no_cash"),
            "rejects_total": len(rejects),      # 该方案的真实总数（明细列表可能被截断，见下）
            "perf": perf,
            }
            # ---- batch_even（按批次分片）---- 单独一套状态机，结果并入同一张净值表
    if any(_split_mode(s)[0] == "batch_even" for s in alloc_modes):
        bres = run_batch_backtest(signals, panel, fill=fill, cost=cost, capital=capital,
                                  strict_limit=strict_limit, limit_judge=limit_judge, lot=lot)
        if len(bres.nav.columns):
            navs["batch_even"] = bres.nav["batch_even"]
            # ⚠ `batch_even` 出来的**已经是格式化后的 dict** ✓ ⇒ 与其他方案的元组混排 ✗
            #   ⇒ 统一转成同一种"元组表示"再入列（末尾按类型分别处理 ✓）
            all_trades += [("__ROW__", rec) for rec in bres.trades.to_dict("records")]
            mode_rejects["batch_even"] = bres.rejects.to_dict("records")
            if bres.stats.get("batch_even"):
                out.stats["batch_even"] = bres.stats["batch_even"]
            if bres.diag.get("batch"):
                out.diag["batch"] = bres.diag["batch"]
        elif bres.diag.get("error"):
            out.diag["batch_error"] = bres.diag["error"]

    nav_df = pd.DataFrame(navs)
    nav_df.index.name = "date"
    out.nav = nav_df
    # 明细上限（v1.19.77：用户要"都显示出来"）：各方案**真实总数**另存 `rejects_total`
    # ⇒ 就算明细被截断，界面也能如实写"共 X 条、明细含 Y 条"（别再出现"正好 800"这种误读）
    out.diag["rejects_total"] = int(sum((out.stats.get(k, {}) or {}).get("rejects_total", 0)
                                        for k in out.stats))
    # ⚠ v1.20.7：成交明细**在这里一次性格式化**（循环里只存元组 ✓，见 `_log_trade` 注释 ✓）。
    # ★ v1.20.40（2a）：改为调 `_trades_frame` **按列构造** ✓ —— 原来造 2 万个小 dict 再交给
    #   pandas 走 `_list_of_dict_to_arrays`（它会**为每一列再遍历一遍 dict 列表** ✗）
    #   ⇒ 该路径实测 cum **0.70 s / 20 轮（3.0%）** ✗。**字段/顺序/类型/格式逐位不变** ✓
    #   （`("__ROW__", dict)` 的 `batch_even` 行按**原位置**沿用 ✓）。
    out.trades = _trades_frame(all_trades[:20000], _date_arr, codes, half)
    out.rejects = pd.DataFrame(_interleave_rejects(mode_rejects, 30000))
    return out


def run_batch_backtest(signals: pd.DataFrame, panel: Dict[str, pd.DataFrame], *,
                       fill: str = "t1_open", cost: float = 0.004, capital: float = 1e9,
                       strict_limit: bool = True, limit_judge: str = "legacy",
                       lot: int = LOT_DEFAULT) -> BtResult:
    """**`batch_even`：按批次分片**（同事 2026-09-16 的口径，用户确认"加这个方案"）。

    规则（与同事的选股文件一一对应）：
      · 信号里的 `bucket` 列 = 批次（如 `W_0..W_4`）⇒ **资金按批数平分**（5 批 ⇒ 各 20%）；
        没有 bucket 列就当作单一批次（= 全部资金）。
      · **批间资金互不挪用**：每批从自己的 20% 起步、各自复利（他的"把资金分 5 份来买"）。
      · **只在该批名单刷新日动该批的票**，且**一切都按"目标资金占比"对齐**（用户 2026-09-16 确认：
        「实际上他**有目标资金占比**」）：每只的目标 = `该批当前权益 × 份数 / 本批总份数` ⇒
        移出名单（目标 0）⇒ **全卖**；份数变少或总份数变多 ⇒ **减仓**（卖掉超出的部分，不是清仓）；
        份数变多 ⇒ **加仓**（补买差额）。⚠ 只有**占比没变**的票才真的不动 —— 他的"留下的不动"
        是指"不因为股价波动去调平"，而不是"份额比例变了也不动"。
        顺序：**先卖（移出 + 减仓）→ 再买（新增 + 加仓）**，回款当天可用。
      · 批内**按份数等权**：信号里的 `weight` 列 = 份数（同一只票重复出现 = **有意加仓** ⇒ 2 份，
        与他的 `目标资金占比 = 0.2 ÷ 行数` 完全一致）；缺列 ⇒ 每只 1 份。
      · 买单的目标金额 = **该批当前权益 × 份数占比**（该批卖出回款 + 现金支付；不够则整体缩水
        并记 `no_cash`）。
      · 成交/限制口径与主引擎**共用同一套**（`_limit_ctx`）：T+1 开盘成交；**涨停买不进=放弃该笔**；
        **跌停/停牌卖不出=逐日顺延**；停牌两边都不做；整手 100 股；单边费率 cost/2。
    """
    mode = "batch_even"
    if not len(signals) or "CALENDAR" not in panel or "CLOSE" not in panel:
        return BtResult(diag={"error": "无可用信号或价格面板"})
    fill = fill if fill in _FILL_LAG else "t1_open"
    lag, px_kind = _FILL_LAG[fill], _FILL_PRICE[fill]
    half = float(cost) / 2.0
    prep = _prepare(signals, panel)
    cal, codes, col_of = prep["cal"], prep["codes"], prep["col_of"]
    if not codes:
        return BtResult(diag={"error": "信号标的不在行情数据里", "missing": prep["missing"][:20]})
    _price, can_buy, can_sell, _PX, _RAW, _judge = _limit_ctx(prep, px_kind, strict_limit, limit_judge)
    n_row = len(cal)
    # ★★ T7-b（v2.8.8，用户 2026-10-16："7 做" ✓）：**`batch_even` 也改成"真实股数的一手"** ✗→✓
    #   —— v2.8.4（审计 #8）只统一了 `cash_even` / `event_even`（新买 + 补低配 + 再平衡买卖）与
    #     `period_roll` **三处** ✓，本方案当时漏了 ✗ ⇒ 它仍在用 `int(金额 / 复权价 / lot) * lot` ✗
    #     （= "**复权单位**的整手" ✗ ⇒ 真实股数 = q/f 常常不是整手 ✗，与另三处口径分叉 ✗）。
    #   ⇒ 这里按标的预算**真实手数**（科创板 688/689 ⇒ 200 ✓，其余 100 ✓ —— 与主引擎同一套
    #     `_lot_of` ✓ 一处定义 ✓），并把下面的取整全部换成 `_fit_real_lots`（含**最低 5 元佣金** ✓）。
    #   ⚠ 影响面：**后复权**（f≥1）下本方案的历史数字会变 ✓（f=1 的不复权口径**逐位不变** ✓）。
    _lot_arr = np.fromiter((_lot_of(c, lot) for c in codes), dtype=np.int64, count=len(codes))

    # ★ v2.11.12（技术债 P1-#3 第十四刀 ✓）：信号折叠搬去
    #   `signals/batch_execs.build_batch_execs` ✓ —— 那里写清了四条语义（卖出信号忽略 /
    #   `defer` 只是统计不丢弃 / 执行日钳到最后一根 / 同(批次,日)权重累加 ✓）。
    buckets, execs, i_start, defer, beyond, _n_mapped, _n_refresh, _sig_err = build_batch_execs(
        signals, col_of=col_of, cal=cal, n_row=n_row, lag=lag)
    if _sig_err:
        return BtResult(diag={"error": _sig_err})
    share = 1.0 / max(1, len(buckets))

    # ★ v2.11.13（技术债 P1-#3 第十五刀 ✓）：初始状态整块搬去
    #   `signals/batch_state.init_batch_state` ✓ —— 那里写清了「现金按批次均分」
    #   与「持有天数按**票**记（不是按 (批次,票)）」两条口径 ✓，以及 sell_q / trim_q 的区别 ✓。
    _S = init_batch_state(capital, buckets)
    share = _S["share"]
    cash = _S["cash"]
    sh = _S["sh"]
    basis = _S["basis"]
    entry_c, nhold, pnl_c = _S["entry_c"], _S["nhold"], _S["pnl_c"]
    sell_q, trim_q = _S["sell_q"], _S["trim_q"]
    trades, rejects = _S["trades"], _S["rejects"]
    nav_dates, nav_vals = _S["nav_dates"], _S["nav_vals"]
    fee_sum = _S["fee_sum"]
    wins = losses = 0
    n_refresh = n_defer_sell = 0
    hold_sum = hold_n = 0
    min_cash = _S["min_cash"]

    def _log_trade(i, b, c, side, qty, px, reason):
        nonlocal fee_sum, wins, losses, hold_sum, hold_n
        amt = qty * px
        fee = _fee_of(amt, half)
        fee_sum += fee
        if side > 0:
            cash[b] -= amt + fee
            was = sh[b].get(c, 0)
            sh[b][c] = was + qty
            basis[b][c] = basis[b].get(c, 0.0) + amt + fee
            pnl_c[c] = pnl_c.get(c, 0.0) - (amt + fee)      # ★ v2.1.51：现金流累计 ✓
            if was <= 0:                                    # 该**批次**在这只票上新开一仓 ✓
                if nhold.get(c, 0) <= 0:
                    entry_c[c] = i                          # 所有批次合计从 0 起 ⇒ 记连续持有起点 ✓
                nhold[c] = nhold.get(c, 0) + 1
        else:
            cash[b] += amt - fee
            held = sh[b].get(c, 0)
            b0 = basis[b].get(c, 0.0)
            avg = (b0 / held) if held > 0 else 0.0
            pnl_c[c] = pnl_c.get(c, 0.0) + (amt - fee)      # ★ v2.1.51：现金流累计 ✓
            sh[b][c] = held - qty
            basis[b][c] = max(0.0, b0 - avg * qty)
            if sh[b][c] <= 0:
                sh[b].pop(c, None)
                if b0 > 0:
                    nhold[c] = nhold.get(c, 0) - 1
                if nhold.get(c, 0) <= 0:
                    # ★★ v2.1.51：**所有批次都不再持有 ⇒ 才结算一笔** ✗→✓
                    #   原来每笔卖出都记一次 ✗（同一票跨批次被拆成多笔、恒 ≈ 60 ✗）；
                    #   现在 = 该票**连续持有**的交易日数 ✓，胜负按整段现金净额判 ✓。
                    hold_sum += max(0, i - entry_c.get(c, i))
                    hold_n += 1
                    if pnl_c.get(c, 0.0) > 0:
                        wins += 1
                    else:
                        losses += 1
                    entry_c.pop(c, None)
                    nhold.pop(c, None)
                    pnl_c.pop(c, None)
                basis[b].pop(c, None)
        # ★ T7-b：**明细也按真实口径输出**（真实股数 × 真实价 = 真实金额 ✓）——
        #   与主引擎/`period_roll` 在 v2.8.4 定下的同一条标准 ✓（账目仍按复权单位算 ✓，金额恒等 ✓）；
        #   取不到真实价 ⇒ 退回复权口径（不装懂 ✓）；真实股数是**整手倍数** ✓（`tests/test_lot_by_market.py` 守着 ✓）。
        _pxd = float(_RAW[i, c])
        if np.isfinite(_pxd) and _pxd > 0:
            _sh_disp, _px_disp2 = int(round(float(qty) * float(px) / _pxd)), _pxd
        else:
            _sh_disp, _px_disp2 = int(qty), px
        trades.append({"date": str(cal[i].date()), "code": codes[c],
                       "side": "买" if side > 0 else "卖", "shares": _sh_disp,
                       "price": round(_px_disp2, 4), "amount": round(amt, 2),
                       "fee": round(fee, 2),
                       "reason": reason, "mode": mode, "batch": b})

    def _reject(i, b, c, reason, dec=None):
        rejects.append({"date": str(cal[i].date()), "code": codes[c], "reason": reason,
                        "text": REJECT_TEXT.get(reason, reason),
                        "signal_date": (str(cal[dec].date()) if dec is not None else None),
                        "mode": mode, "batch": b})

    for i in range(i_start, n_row):
        # 1) 先成交顺延的卖单（回款当日可用于本批买入）
        for key in list(sell_q.keys()):
            b, c = key
            dec, reason = sell_q[key]
            if i < dec + lag or c not in sh[b]:
                continue
            bad = can_sell(i, c)
            if bad:
                n_defer_sell += 1
                _reject(i, b, c, bad, dec)
                continue
            px = _price(i, c)
            if not np.isfinite(px):
                _reject(i, b, c, "no_price", dec)
                continue
            _log_trade(i, b, c, -1, sh[b][c], px, reason)
            sell_q.pop(key, None)
        # 1b) 待**减仓**（份数变少 ⇒ 超配部分）逐日重试：跌停/停牌当天卖不掉的，明天再卖
        #     每次都按**当前**权益重算目标金额（价格在动，目标也在动）
        for key in list(trim_q.keys()):
            b, c = key
            dec, units, tot_u_ = trim_q[key]
            if i < dec + lag or c not in sh[b]:
                continue
            px = _price(i, c)
            if not np.isfinite(px):
                _reject(i, b, c, "no_price", dec)
                continue
            mv = sum(sh[b][k] * (_price(i, k) if np.isfinite(_price(i, k)) else 0.0)
                     for k in sh[b])
            excess = sh[b][c] * px - (cash[b] + mv) * units / tot_u_
            if excess <= px * lot:
                trim_q.pop(key, None)                    # 已回到目标（或被加仓补上）⇒ 结束
                continue
            bad = can_sell(i, c)
            if bad:
                n_defer_sell += 1
                _reject(i, b, c, bad, dec)
                continue
            # ★ T7-b：按**真实股数的一手**取整（与主引擎 `rebalance_sell` 同一套 ✓）
            _lt = int(_lot_arr[c])
            q, _n = _fit_real_lots(_RAW[i, c], px, _lt, half, float("inf"), budget=excess)
            q = min(float(q), float(sh[b][c])) if q > 0 else 0.0
            if q <= 0:
                trim_q.pop(key, None)
                continue
            _log_trade(i, b, c, -1, q, px, "sell_trim_units")
        # 2) 本批名单刷新
        for (b, lst, dec) in execs.get(i, []):
            new_codes = set(lst.keys())
            held = set(sh[b].keys())
            # ① 移出 ⇒ **当场卖出**（回款当天可用于本批买入；卖不出才排队逐日顺延）
            #   ⚠ 不能先排队、次日才成交：那样"卖掉的钱"赶不上同日的买入 ⇒ 新增股会被判 no_cash
            #     而新增只在刷新日尝试一次 ⇒ 整批新增直接丢掉（单测 test_each_batch... 抓到）。
            for c in sorted(held - new_codes):
                trim_q.pop((b, c), None)               # 全卖在即 ⇒ 撤下"待减仓"
                if (b, c) in sell_q:
                    continue
                bad = can_sell(i, c)
                px = _price(i, c)
                if bad:
                    n_defer_sell += 1
                    _reject(i, b, c, bad, dec)
                    sell_q[(b, c)] = (i, "sell_out_of_list")   # 从今日起逐日重试
                    continue
                if not np.isfinite(px):
                    _reject(i, b, c, "no_price", dec)
                    sell_q[(b, c)] = (i, "sell_out_of_list")
                    continue
                _log_trade(i, b, c, -1, sh[b][c], px, "sell_out_of_list")
            for c in sorted(held & new_codes):         # 又回到名单 ⇒ 撤销**全卖**排队（见①）
                if (b, c) in sell_q:
                    sell_q.pop((b, c), None)
                    n_refresh += 1
            # 目标资金占比 = `equity × 份数 / 本批总份数` —— 本批的三件事都是它：
            #   移出名单（目标 0）⇒ 全卖 ①；份数变少 ⇒ 减仓 ②；份数变多/新增 ⇒ 补买 ③。
            tot_u = sum(float(lst[c]) for c in new_codes) or 1.0
            mv = sum(sh[b][k] * (_price(i, k) if np.isfinite(_price(i, k)) else 0.0)
                     for k in sh[b])
            equity = cash[b] + mv
            # ② 仍在名单但**超配**（份数变少）⇒ 卖掉多出来的那一部分（部分卖出，不是清仓）
            #   ⚠ 用户 2026-09-16 纠正：「份数变少要减的哈，比如原来 2 份、现在 1 份，要减掉 1 份的，
            #     实际上他**有目标资金占比**」⇒ 按占比对齐，而不是"留下的不动"。
            #     减仓回款**当天就用于本批买入**（顺序：先减仓/卖出 → 再买入）。
            for c in sorted(held & new_codes):
                px = _price(i, c)
                if not np.isfinite(px):
                    _reject(i, b, c, "no_price", dec)
                    continue
                excess = sh[b][c] * px - equity * float(lst[c]) / tot_u
                if excess <= px * lot:
                    trim_q.pop((b, c), None)             # 已回到目标 ⇒ 撤下待减仓
                    continue
                bad = can_sell(i, c)
                if bad:                                  # 跌停/停牌卖不出 ⇒ 登记后逐日顺延
                    n_defer_sell += 1
                    _reject(i, b, c, bad, dec)
                    trim_q[(b, c)] = (dec, float(lst[c]), tot_u)
                    continue
                # ★ T7-b：同上 —— 按**真实股数的一手**取整 ✓
                _lt = int(_lot_arr[c])
                q, _n = _fit_real_lots(_RAW[i, c], px, _lt, half, float("inf"), budget=excess)
                q = min(float(q), float(sh[b][c])) if q > 0 else 0.0
                if q > 0:
                    _log_trade(i, b, c, -1, q, px, "sell_trim_units")
                trim_q.pop((b, c), None)
            # ③ 新增 / **加仓** ⇒ 买入
            #   ⚠ 用户 2026-09-16 补：「如果有两条重复的，比原来多一条，他就**多买一份**」
            #     ⇒ 份数变多的持仓股也补买差额（旧实现只买 `new_codes - held`，把加仓漏了）。
            plans = []                                   # (列号, 本次拟买金额, 现价)
            for c in sorted(new_codes):
                px = _price(i, c)
                if not np.isfinite(px):
                    _reject(i, b, c, "no_price", dec)
                    continue
                want = equity * float(lst[c]) / tot_u    # 按**份数**占比的目标金额
                cur = sh[b].get(c, 0) * px
                if c not in sh[b]:
                    plans.append((c, want, px))          # 新增 ⇒ 买满目标
                elif want - cur > px * lot:
                    plans.append((c, want - cur, px))    # 份数变多 ⇒ 补差额（多买一份）
                    trim_q.pop((b, c), None)             # 目标调高了 ⇒ 撤下待减仓
            ok = []
            for c, amt, px in plans:
                bad = can_buy(i, c)
                if bad:
                    _reject(i, b, c, bad, dec)
                    continue
                ok.append((c, amt, px))
            if ok:
                need = sum(amt * (1 + half) for _c, amt, _px in ok)
                scale = 1.0 if need <= cash[b] * 1.0001 else max(0.0, cash[b] / max(need, 1e-9))
                for c, amt, px in ok:
                    # ★ T7-b：买入按**真实股数的一手** + **真实费用（含最低 5 元佣金）** 取整 ✓
                    #   （与主引擎 `event_even`/`cash_even` 的买入同一套 ✓；`cash[b]` 是硬约束 ⇒
                    #    小额批次的票会像另三处一样"买不起一手就不买" ✓，而不是买 0.6 手 ✗）
                    _lt = int(_lot_arr[c])
                    q, _n = _fit_real_lots(_RAW[i, c], px, _lt, half, float(cash[b]),
                                           budget=amt * scale / (1.0 + half))
                    if q <= 0:
                        _reject(i, b, c, "no_cash", dec)
                        continue
                    _log_trade(i, b, c, 1, q, px,
                               "buy_add_units" if c in sh[b] else "buy_list_refresh")
        # 3) 逐日盯市（各批合计）
        eq = sum(cash.values()) + sum(
            sh[b][k] * (_price(i, k) if np.isfinite(_price(i, k)) else 0.0)
            for b in buckets for k in sh[b])
        if cash and min(cash.values()) < min_cash:
            min_cash = min(cash.values())
        nav_dates.append(cal[i])
        nav_vals.append(eq / capital)

    # ★ v2.11.28（第十八刀）：装配搬去 `batch_result.build_batch_result` ✓
    #   ⚠ 计数器必须**显式传** —— `_S` 只是初始快照，`_log_trade` 改的是 `nonlocal` 局部变量 ✓
    return build_batch_result(
        nav_dates=nav_dates, nav_vals=nav_vals, buckets=buckets, share=share,
        codes=codes, prep=prep, panel=panel, capital=capital, mode=mode,
        fill=fill, cost=cost, strict_limit=strict_limit, lot=lot,
        n_mapped=_n_mapped, defer=defer, beyond=beyond,
        trades=trades, rejects=rejects, cash=cash, sh=sh,
        fee_sum=fee_sum, wins=wins, losses=losses, hold_sum=hold_sum, hold_n=hold_n,
        n_refresh=n_refresh, n_defer_sell=n_defer_sell, min_cash=min_cash,
    )


def _perf(net: np.ndarray) -> dict:
    """年化/回撤/夏普等（复用项目 `engine/perf_metrics.compute_perf`，与回测页同口径）。"""
    try:
        from app.engine.perf_metrics import compute_perf

        p = compute_perf(net)
        if isinstance(p, dict):
            return {k: (round(float(v), 4) if isinstance(v, (int, float)) else v)
                    for k, v in p.items() if isinstance(v, (int, float, str, type(None)))}
    except Exception as e:
        _log_swallowed("engine.py", e)
        pass
    return {}


def attach_benchmark(nav: pd.DataFrame, bench_wide: Optional[pd.DataFrame],
                     bench_code: str) -> pd.DataFrame:
    """把基准指数曲线**归一到与策略同一净值起点**（1.0）后并入 nav。"""
    if bench_wide is None or bench_code not in getattr(bench_wide, "columns", []) or not len(nav):
        return nav
    b = bench_wide[bench_code].reindex(nav.index).ffill()
    first = b.first_valid_index()
    if first is None:
        return nav
    base = float(b.loc[first])
    out = nav.copy()
    out["benchmark"] = (b / base).astype(float)
    return out


def nav_rows(nav: pd.DataFrame) -> list:
    """净值宽表 → Recharts 友好的行数组（列名即曲线名；NaN → None，前端断线不猜数）。

    ⚠ 由 `routers/signal_test.py` 移到这里（v1.19.60）：单因子「事件研究」面板也要画净值曲线
      ⇒ 序列化**只留一份**，避免两条路径各写一套（本项目最忌讳的格式/口径分叉）。
    """
    rows = []
    for d, row in nav.iterrows():
        # ★ 2026-10-20（mypy `assignment`）：`{"date": ...}` 把 `rec` 推成 `dict[str, str]` ✗
        #   ⇒ 后面写 `None` / `float` 全部不兼容 ✓ ⇒ 显式标 `dict` ✓。
        rec: dict = {"date": str(pd.Timestamp(d).date())}
        for c in nav.columns:
            v = row[c]
            rec[str(c)] = None if not pd.notna(v) else round(float(v), 5)
        rows.append(rec)
    return rows
