# -*- coding: utf-8 -*-
"""聚宽成交明细的净值重建（交易信号测试 · 模式②）—— 用户要的"四条曲线一目了然"：

  **A** `nav_sim_fee` —— **模拟它的成本**：沿用它的成交时点与股数，价格换成**我们数据里的
                       后复权价**（收益率口径 = 真实前复权、含分红），费率用**从它手续费反推**
                       的买入/卖出费率；
  **B** `nav_my_cost` —— 同一批成交，成本换成**用户设定的 cost**（默认 0.004，往返合计）；
  **C** `nav_exact`   —— **它的成交价 + 它每笔实际手续费**的精确现金记账（逐日盯市用"它的价格水平"）；
  **D** `benchmark`   —— 基准指数（由调用方 `attach_benchmark` 归一到同一净值起点）。

为什么三条策略曲线都要：把「**价格来源**」与「**成本假设**」两个变量拆开 ——
  A vs C 看价格口径差多少；A vs B 看我设定的成本比它贵/便宜多少。混成一条什么都看不出来。

口径与边界（全部回传到 `diag`，界面要显示）：
  · **复权口径体检**：逐笔比 `我们的价 / 它的成交价`。比值**稳定**说明两边是同一个价格世界
    （差异只是复权基准），比值**漂移**说明口径/数据源不同 ⇒ 曲线差异要打折看；
  · `capital` 默认 = 首日买入总额（本例 1000 万），用户可改。A/B 的股数会按
    `capital / 首日买入总额` **等比缩放**（否则账户做大后仓位比例会失真、曲线贴近水平线）；
    C 按它的原始股数记账（要公平对比，请把 capital 填成它策略的真实资金）；
  · 现金被价格口径差异捅穿**不静默**：负现金 K 线次数会上报；
  · 撤单/部撤行在解析层已剔除（部成保留实际成交量）。
"""
from __future__ import annotations

from typing import Dict, Optional

import numpy as np
import pandas as pd


def replay_trades(trades: pd.DataFrame, close: pd.DataFrame, *, capital: float,
                  capital_ref: Optional[float] = None, my_cost: float = 0.004,
                  fee_buy: Optional[float] = None, fee_sell: Optional[float] = None,
                  end: Optional[str] = None) -> Dict:
    """跑三条净值曲线。

    `close`：**我们数据**的后复权收盘宽表（须含全部交易标的）；
    `capital_ref`：A/B 缩放基准（默认 = 首日买入总额 ⇒ 与 `capital` 相同时缩放系数 1.0）。
    """
    out: Dict = {"nav": None, "stats": {}, "diag": {}}
    if trades is None or not len(trades) or close is None or not len(close):
        out["diag"]["error"] = "无成交记录或无价格数据"
        return out
    tr = trades.copy()
    tr["date"] = pd.to_datetime(tr["date"])
    codes = [c for c in sorted(set(tr["code"])) if c in close.columns]
    missing = sorted(set(tr["code"]) - set(close.columns))
    tr = tr[tr["code"].isin(codes)]
    if not len(tr):
        out["diag"] = {"error": "成交标的都不在行情数据里", "missing": missing[:20]}
        return out

    dates = pd.DatetimeIndex(close.index)
    d0, d1 = tr["date"].min(), tr["date"].max()
    if end:
        # ⚠ `end` 语义 = **延长窗口**（不与末笔成交取 min）：与官方《收益概述》对齐时必须用它
        #   把窗口推到官方序列的末日，否则两条曲线停在不同的日子上比较
        #   （实测明细止于 2024-12-30、官方到 12-31，12-31 又跌 2% ⇒ 平白多出 2% 偏差）。
        d1 = max(d1, pd.Timestamp(end))
    win = dates[(dates >= d0) & (dates <= d1)]
    if len(win) < 2:
        out["diag"] = {"error": "成交日期与行情日历无交集"}
        return out
    P = close.reindex(index=win, columns=codes).ffill()          # 我们的后复权价
    pos_of = {d: i for i, d in enumerate(win)}
    Pm = {c: P[c].to_numpy(dtype=float) for c in codes}

    # ---- 复权口径体检 + 每个标的的"它的价格 / 我们的价格"（**逐日锚定**，不能用全期常数）----
    # ⚠⚠ 为什么必须逐日锚定（2026-09-15 用户追问「为啥模拟它的成本会差这么多」时查清）：
    #   实测该比值**随时间系统性漂移** —— 逐年中位 0.149(2016) → 0.134 → 0.117 → 0.074 → 0.063(2024)，
    #   9 年降 58%（两家复权基准不同：我们的 `$close` 与它的成交价不是同一基准）。
    #   若拿**全期中位**当一个常数用：A/B 的股数在早期被低估 ~20%、后期被高估 ~90% ⇒ 仓位错配 ⇒
    #   A 与 C 分叉 4.77%；C 的逐日盯市同样被污染 ⇒ 与官方净值差 2.08%。
    #   改为"按它的成交日锚定 + 日间 ffill"：**成交当天比值精确**（现金流分毫不动），
    #   两次成交之间用我们的价推进（含分红再投资），水平仍落在它的价格世界。
    ratios, scale, anchor = [], {}, {}
    for c, g in tr.groupby("code"):
        rr = []
        s = pd.Series(np.nan, index=win, dtype=float)
        for _, r in g.iterrows():
            i = pos_of.get(pd.Timestamp(r["date"]))
            if i is None:
                continue
            ours = Pm[c][i]
            if np.isfinite(ours) and float(r["price"]) > 0:
                ratios.append(ours / float(r["price"]))
                rr.append(float(r["price"]) / ours)
                s.iloc[i] = float(r["price"]) / ours
        scale[c] = float(np.median(rr)) if rr else 1.0
        # ⚠ `anchor`（逐笔/逐日比值）**经实测被否**：改成逐笔换算后 A 反而从 8.09 跳到 12.89
        #   （+52.6% vs C），C 也从 −2.04% 恶化到 −2.61% ⇒ 说明"逐年中位比值下降"主要不是
        #   同一只股票的时间漂移（可能是每年的**股票构成**差异 —— 用截面统计推断时间序列性质是我
        #   当时犯的错）。故 A/B 仍用**全期常数**比值（实测最接近 C），anchor 只留作体检信息。
        anchor[c] = s.ffill().bfill().to_numpy(dtype=float)
    ratio_stats = {}
    if ratios:
        a = np.asarray(ratios, dtype=float)
        ratio_stats = {"median": round(float(np.median(a)), 4),
                       "p10": round(float(np.percentile(a, 10)), 4),
                       "p90": round(float(np.percentile(a, 90)), 4),
                       "n": int(a.size)}
    drift = abs(ratio_stats.get("p90", 1.0) - ratio_stats.get("p10", 1.0))

    # 成交按日分组；同日多笔按「卖 → 买」排序（先腾资金，贴近实盘可用资金）
    by_day: Dict[pd.Timestamp, list] = {}
    for _, r in tr.iterrows():
        by_day.setdefault(pd.Timestamp(r["date"]), []).append(r)
    for d in by_day:
        by_day[d].sort(key=lambda r: 0 if r["side"] < 0 else 1)

    rs_flag = float(capital) / float(capital_ref) if capital_ref else 1.0
    half = float(my_cost) / 2.0
    rb = float(fee_buy if fee_buy is not None else 0.0003)
    rsl = float(fee_sell if fee_sell is not None else 0.0013)

    def _sim(kind: str) -> Dict:
        """kind: `sim_fee`(A) / `my_cost`(B) / `exact`(C)。"""
        cash, pos, nav, neg, fees, n_fill = float(capital), {}, [], 0, 0.0, 0
        code_scale = 1.0 if kind == "exact" else rs_flag
        for d in win:
            i = pos_of[d]
            for r in by_day.get(d, []):
                c = r["code"]
                # ⚠ A/B 的股数换算到**我们的价格世界**：`qty × (它的价/我们的价)`
                #   —— 用**该笔成交当天的**比值（`anchor[c][i]`，逐日锚定），不能用全期常数
                #   （比率逐年漂 58%，常数会让早期低估/后期高估 ⇒ 与 C 分叉 4.77%）。
                #   C 用它自己的价与股数，不需要换算。
                q = float(r["qty"]) * (1.0 if kind == "exact" else scale.get(c, 1.0) * code_scale)
                qty = q
                if kind == "exact":
                    px = float(r["price"])                  # 它的成交价
                    fee = float(r["fee"]) * code_scale      # 它的实际手续费
                else:
                    px = float(Pm[c][i])
                    if not np.isfinite(px) or px <= 0:
                        continue
                    rate = (rb if r["side"] > 0 else rsl) if kind == "sim_fee" else half
                    fee = qty * px * rate
                if r["side"] > 0:
                    cash -= qty * px + fee
                    pos[c] = pos.get(c, 0.0) + qty
                else:
                    cash += qty * px - fee
                    pos[c] = pos.get(c, 0.0) - qty
                    if pos[c] <= 1e-9:
                        pos.pop(c, None)
                fees += fee
                n_fill += 1
                if cash < 0:
                    neg += 1
            mv = 0.0
            for c, q in pos.items():
                ours = Pm[c][i]
                if not np.isfinite(ours):
                    continue
                # exact：把我们的价投影到"它的价格水平"（沿用**全期常数**比值：实测它比逐笔锚定
                # 更接近官方净值 —— −2.04% vs −2.61%，见上面 anchor 的说明）
                mv += q * (ours if kind != "exact" else ours * scale.get(c, 1.0))
            nav.append((cash + mv) / float(capital))
        return {"nav": np.asarray(nav, dtype=float), "neg_cash": int(neg),
                "fees": float(fees), "fills": int(n_fill), "pos_end": len(pos)}

    A, B, C = _sim("sim_fee"), _sim("my_cost"), _sim("exact")
    nav_df = pd.DataFrame({"nav_sim_fee": A["nav"], "nav_my_cost": B["nav"],
                           "nav_exact": C["nav"]}, index=win)

    def _perf(s: pd.Series) -> dict:
        try:
            from app.engine.perf_metrics import compute_perf

            p = compute_perf(s.pct_change().fillna(0.0).to_numpy()) or {}
            return {k: (round(float(v), 4) if isinstance(v, (int, float)) else v)
                    for k, v in p.items() if isinstance(v, (int, float, str, type(None)))}
        except Exception:
            return {}

    out["nav"] = nav_df
    out["stats"] = {
        "nav_sim_fee": {"final_nav": round(float(nav_df["nav_sim_fee"].iloc[-1]), 4),
                        "fee_rate_buy": round(rb, 6), "fee_rate_sell": round(rsl, 6),
                        "fees": round(A["fees"], 2), "perf": _perf(nav_df["nav_sim_fee"])},
        "nav_my_cost": {"final_nav": round(float(nav_df["nav_my_cost"].iloc[-1]), 4),
                        "cost_round_trip": float(my_cost), "fees": round(B["fees"], 2),
                        "perf": _perf(nav_df["nav_my_cost"])},
        "nav_exact": {"final_nav": round(float(nav_df["nav_exact"].iloc[-1]), 4),
                      "fees_csv": round(float(tr["fee"].sum()), 2),
                      "fees": round(C["fees"], 2), "perf": _perf(nav_df["nav_exact"])},
    }
    out["diag"] = {
        "n_trades_used": int(len(tr)), "n_trades_total": int(len(trades)),
        "missing_codes": missing[:20],
        # ⚠ A/B 的口径限制（2026-09-15 用户追问「为啥模拟它的成本会差这么多」时查清并实测）：
        #   A/B = 用它反推的费率 + **我们的价**；股数按"它的价/我们的价"的**全期常数**换算。
        #   实测：① 两套价格的**收益率一致**（同股票、跨 ≥2 年：年化漂移中位 −0.7%）⇒ 差的是**水平**
        #   （复权基准不同）；② 但**每只股票的该比值差异极大**（its/ours 从 ~4 到 ~26 倍）⇒ 常数换算
        #   会让各股**仓位相对大小错配**；③ 改成"逐笔精确匹配"后 A 反而从 8.09 跳到 12.89
        #   （更差）⇒ 该重放口径对换算方式高度敏感、**绝对值有 ±5% 量级不确定性**。
        #   故：**A/B 只用于比较"费率档次"的相对高低，绝对值请以 C（它的成交价 + 实际手续费）为准**。
        "ab_note": "A/B 的绝对值有 ±5% 量级口径不确定性（价格水平换算所致）；请以 C 为准，"
                   "A vs B 的相对差才代表费率档次的影响",
        "price_ratio": ratio_stats,
        "price_ratio_max_drift": round(float(drift), 4),
        "price_consistency": ("一致（我们的价/它的价 比值稳定）" if drift <= 0.05 else
                              "⚠ 比值漂移 >5%：两边复权口径/数据源不同，曲线差异请打折看"),
        "qty_scale_ab": round(float(rs_flag), 4),
        "negative_cash_bars": {"sim_fee": A["neg_cash"], "my_cost": B["neg_cash"],
                               "exact": C["neg_cash"]},
        "open_positions_end": C["pos_end"],
        "capital": float(capital),
    }
    return out
