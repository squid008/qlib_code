# -*- coding: utf-8 -*-
"""「K 敏感度汇总表」的面板适配层：不同 K（持仓只数）下的换手 / 年化成本 / 成本吞噬比例。

职责边界（**只做适配与组装，算法一律在 `engine/cost_curves.py`**）：
  1. 把单因子面板 `tmp`（含 `_rk` 名次列）压成 **名次矩阵**（调仓日 × 股票）；
  2. 调 `cost_curves._turnover_by_k`（各 K 逐期换手）与 `cost_curves._annualize_cost`；
  3. 组装成前端可直接渲染的行列表（JSON-ready）。

**为什么单独一个模块**（而不是继续塞进 `single_test.py`，它已 1000+ 行）：
  · 用户明确要求禁止上帝模块 / 屎山，新逻辑默认独立小模块；
  · 分层回测页（`engine/analysis.py` 的调仓日分支）以后要做同一张表，可直接复用。

名次矩阵用 **MultiIndex 级码**（`index.codes`）构造，而不是 `factorize` / `get_indexer`：
级码是面板建索引时就已算好的整数 ⇒ 整段只是几次 O(n) gather；而事后对 200 万行
字符串（instrument）/日期重新哈希很贵（`Index._get_indexer` 曾是特征加载段最大单项）。

口径见 `engine/cost_curves` 模块头：
  · 只做 **Group1 纯多头**；费率 = **往返合计**；**按调仓期扣**、只扣实际调仓的股票；
  · **首期建仓不计费**；年化用 **252 交易日/年**；
  · ⚠ **简化估算：未考虑涨跌停、停牌、流动性冲击**。

与已上线的 `topk_curves` 的关系：**同一批股票、同一批调仓日**的两个视图
（`topk_curves` = 默认 K 的逐日三档曲线；本表 = 多个 K 的换手/成本汇总）。
`verify_turnover` 参数把「默认 K 那一行的逐期换手」钉在 `topk_curves` 上（**逐位**断言，
用于抓取分母/取整/停牌剔除等口径不一致 —— 两条独立路径算同一组合）。
"""
import numpy as np
import pandas as pd

from app.engine.cost_curves import (TRADING_DAYS_PER_YEAR, _annualize_cost,
                                    _turnover_by_k)

BASE_KS = (1, 3, 5, 10, 20, 50, 100, 200)     # 固定候选（只数）；另加 10%/20% 两档
NOTE = ("简化估算：未考虑涨跌停、停牌、流动性冲击；成本按调仓期扣、只扣实际调仓的股票；"
        "首期建仓不计费；K=1/3 仅供边界参考。"
        "⚠ 本表按**固定持仓只数 K**（同一方向：超额收益最强分位组所在的那一端）计算；"
        "默认档（最强分位组）是**逐日等分**、只数逐日变化，与固定 K 略有差异（实测成员中位差 4 只）。"
        "⚠ `gross_per_period` 是 **label（h 期前视）口径**的每期累加毛收益，"
        "**不是**可年化的真实收益 ⇒ `cost_eaten` 只作量级参考，须与曲线同口径解读。")


def k_candidates(median_daily):
    """候选 K = 固定档 ∪ {10%, 20% × 日均有效只数}（去重升序；K=1/3 仅供参考）。"""
    pct = [max(1, int(float(median_daily) * p)) for p in (0.1, 0.2)]
    return sorted({int(k) for k in BASE_KS if int(k) > 0} | set(pct))


def build_k_sensitivity(tmp, dt_pos, inst_pos, dts, rebalance_period,
                        median_daily=None, default_k=None, verify_turnover=None,
                        rk_col="_rk", rates=(0.0, 0.004, 0.008),
                        trading_days=TRADING_DAYS_PER_YEAR):
    """生成「K 敏感度汇总表」。

    入参
    ----
    `tmp`     —— 面板 DataFrame，含名次列（**1 = 该端最好**）与 `LABEL` 列；
                 索引必须是 `MultiIndex`（datetime, instrument），且已按剔除开关过滤。
    `dt_pos` / `inst_pos` —— `datetime` / `instrument` 在索引名里的位置。
    `dts`     —— **调仓日序列**（即 `topk_curves` 的日期轴；逐调仓期在 `dts[0::rebalance_period]`）。
    `rebalance_period` —— 调仓期（交易日）。
    `median_daily` —— 日均有效只数（用于定 10%/20% 两档；`None` 则现算）。
    `default_k` —— 默认档（会被 `verify_turnover` 钉住，也回传给前端做高亮）。
    `verify_turnover` —— **固定 K 档明细的 `turnover`**（逐日序列）；给了就做**逐位**自检。
    `rk_col`  —— 名次列名，默认 `"_rk"`。**方向由此列决定**：调用方按「超额收益最强的
                 分位组在哪一端」传 `"_rk"`（低分端）或镜像列 `"_rkd"`（高分端），
                 这样本表与默认档明细**同一方向**（否则会去分析最弱的那一端）。

    返回（JSON-ready；收益/成本均为**小数**，`0.0119` = 1.19%）
    ----
    `{rebalance_period, n_days, n_periods, trading_days, default_k, ks, rows, note}`
    其中 `rows[i] = {k, avg_turnover, gross_per_period, ann_cost{…}, cost_eaten{…}}`
    （`ann_cost` / `cost_eaten` 的键与 `topk_curves["curves"]` 一致：`"0.0040"` 等；
    `gross_per_period ≤ 0` 时 `cost_eaten` 为 `None` —— 吞噬比例对亏损组合无意义）。

    ⚠ **`gross_per_period` 不做年化**：它是「每期 label 口径毛收益」＝**每期**（`rebalance_period`
      个交易日）`LABEL` 的日截面均值之和。`LABEL` 是 **h 期前视**收益（本页曲线同口径），
      逐日累加本身**不是**可年化的真实收益序列（5 天的前视标签窗口重叠，`×252` 会把量级
      放大 ≈ h 倍）⇒ 早年化过的版本给出 −218%/年 这类荒谬值。成本吞噬比例与年化成本
      都按「每期」对齐后相除，比例本身与原口径无关，是自洽的。
    """
    idx = tmp.index
    if not isinstance(idx, pd.MultiIndex):
        raise ValueError("面板索引应为 MultiIndex(datetime, instrument)")
    rebal = int(rebalance_period)
    if rebal < 1:
        raise ValueError("rebalance_period 必须 >= 1")
    if median_daily is None:
        median_daily = float(tmp.groupby(level=dt_pos).size().median())
    ks = k_candidates(median_daily)

    # ---- 1. 名次矩阵（调仓日 × 股票）：用 MultiIndex 级码，全程 O(n) gather ----------
    n_inst = len(idx.levels[inst_pos])
    n_slot = len(range(0, len(dts), rebal))
    rank_mat = np.full((n_slot, n_inst), np.nan, dtype=np.float32)
    if n_slot > 0:
        # 「日期级码 → dts 位置」映射（dts 之外的日 = -1，不参与）
        slot_of_day = np.full(len(idx.levels[dt_pos]), -1, dtype=np.int64)
        day_pos = idx.levels[dt_pos].get_indexer(pd.Index(dts))
        keep = day_pos >= 0
        slot_of_day[day_pos[keep]] = np.flatnonzero(keep)
        dp = slot_of_day[np.asarray(idx.codes[dt_pos])]
        ok = dp >= 0
        rb = ok & ((dp % rebal) == 0)
        rank_mat[dp[rb] // rebal, np.asarray(idx.codes[inst_pos])[rb]] = \
            tmp[rk_col].to_numpy(dtype=np.float64)[rb]
    else:
        dp = np.zeros(len(tmp), dtype=np.int64)
        ok = np.zeros(len(tmp), dtype=bool)

    # ---- 2. 各 K 的逐期换手（纯函数）+ 逐位自检 --------------------------------
    turnover = _turnover_by_k(rank_mat, ks)
    if verify_turnover is not None and default_k is not None and int(default_k) in ks:
        ref = np.asarray(verify_turnover, dtype=np.float64)[rebal::rebal]
        got = turnover[ks.index(int(default_k))]
        if ref.shape != got.shape or not np.array_equal(ref, got):
            raise AssertionError(
                "默认 K=%s 的逐期换手与已上线的 topk_curves 不一致（两条路径口径应逐位相同）"
                % (default_k,))

    # ---- 3. 各 K 的「每期毛收益」（label 口径；只用于算「成本吞噬比例」）------------
    lab = tmp["LABEL"].to_numpy(dtype=np.float64)
    rk = tmp[rk_col].to_numpy(dtype=np.float64)
    valid = np.isfinite(lab) & ok
    n_days = len(dts)
    gross_pp = []
    for k in ks:
        m = (rk <= k) & valid
        if n_days == 0 or not m.any():
            gross_pp.append(None)
            continue
        # 与 `groupby(level=dt).mean()` 同义（skipna：只统计 LABEL 有效的样本）
        s = np.bincount(dp[m], weights=lab[m], minlength=n_days)
        c = np.bincount(dp[m], minlength=n_days)
        dm = np.divide(s, c, out=np.full(n_days, np.nan), where=c > 0)
        # ⚠ **不年化**：`LABEL` 是 h 期前视收益，逐日累加不是可年化序列（见 docstring）
        g = float(np.nanmean(dm)) * rebal if np.any(c > 0) else None
        gross_pp.append(None if (g is None or not np.isfinite(g)) else g)

    # ---- 4. 组装汇总行 ---------------------------------------------------------
    rows = []
    for j, k in enumerate(ks):
        tv = turnover[j]
        avg = float(tv.mean()) if tv.shape[0] else 0.0
        g = gross_pp[j]
        ann_cost, eaten = {}, {}
        for r in rates:
            key = "%.4f" % float(r)
            ann_cost[key] = round(_annualize_cost(avg, r, rebal, trading_days), 6)
            cpp = float(r) * avg                             # 每期成本（与毛收益同"每期"口径）
            eaten[key] = (round(cpp / g, 4) if (g is not None and g > 0) else None)
        rows.append({
            "k": int(k),
            "avg_turnover": round(avg, 6),
            "gross_per_period": (None if g is None else round(g, 6)),
            "ann_cost": ann_cost,
            "cost_eaten": eaten,
        })
    return {
        "rebalance_period": rebal,
        "n_days": int(n_days),
        "n_periods": int(turnover.shape[1]),
        "trading_days": float(trading_days),
        "default_k": (None if default_k is None else int(default_k)),
        "ks": [int(k) for k in ks],
        "rows": rows,
        "note": NOTE,
    }
