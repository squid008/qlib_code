# -*- coding: utf-8 -*-
"""「多空/纯多头收益 + 调仓换手 + 交易成本」的**纯函数**小模块。

**架构约定（请不要破坏）**：这里只放**与具体调用方无关**的纯函数（输入数组/集合，
输出数组），不碰取数、不碰 qlib、不碰展示、不读全局状态。调用方（单因子测试
`factors/single_test.py`、模型回测 `engine/analysis.py`）各自负责取数与组装。

这样做的原因：成本曲线会被**两条独立路径**用到 ——
  · 单因子测试页（连续因子的持仓期收益曲线，与事件研究并列展示）；
  · 模型回测页（分层回测的 Group1 曲线）。
若把这段逻辑直接塞进 `single_test.py`（已 1000+ 行）或 `analysis.py`，就会两边各写一份、
或让某一方变成"什么都装的上帝模块"。**新逻辑默认放这里。**

口径（2026-09-12 用户定稿）：
  · 只做**收益最强组（Group1）的纯多头**（A 股空头收益拿不到）；
  · `rate` = **往返（买+卖）合计**费率，默认三档 `0 / 0.004 / 0.008`；
  · **按调仓期扣**、**只扣实际调仓的股票** ⇒ `成本_t = rate × 换手_t`；
  · 默认 K = **10%**（最强一档）；十分位第 1 组与「TopK=10%」是同一批股票；
  · **首期建仓不计费**；
  · 曲线与既有分层展示同口径用 **cumsum**；
  · ⚠ **简化估算：未考虑涨跌停、停牌、流动性冲击**（展示时必须注明）。

自检：`ai_test/test_cost_curves.py`（总失败 0；含随机 200 期与朴素实现逐位对拍）。
"""
import numpy as np


def _turnover_by_k(rank_cur, rank_prev, ks):
    """各 K 的**单边换手**（一次掩码计数；**不逐 K 重排、不用 Python 集合**）。

    `换手_K = |{今在前 K 名} \\ {昨在前 K 名}| / |今在前 K 名|`

    · `rank` = **组内名次，1 = 最好**（一次 argsort 得到，供 TopK 与十分位**共用**）；
    · **NaN（不可买/停牌/数据缺失）视为"未持有"** ⇒ 昨日 NaN 一律算"新建仓"；
    · 分母用**实际持仓数**（并列/样本不足时 ≠ K），保证换手 ∈ [0,1]；
    · `rank_prev=None`（首期）⇒ 全 0（**首期建仓不计费**）；
    · 内存 O(n)：按 K 循环（每个 K 3 遍 n 长掩码），10 个 K ≈ 30~50ms。
    """
    rc = np.asarray(rank_cur, dtype=np.float64)
    ks = np.asarray(ks, dtype=np.float64)
    if rank_prev is None:
        return np.zeros(ks.shape[0], dtype=np.float64)
    rp = np.asarray(rank_prev, dtype=np.float64)
    out = np.zeros(ks.shape[0], dtype=np.float64)
    for j in range(ks.shape[0]):
        k = ks[j]
        held_cur = rc <= k                 # NaN → False ✓
        n_cur = int(np.count_nonzero(held_cur))
        if n_cur == 0:
            continue
        held_prev = rp <= k                # NaN → False ✓（昨日未持有）
        out[j] = np.count_nonzero(held_cur & ~held_prev) / float(n_cur)
    return out


def _topgroup_cost_curves(period_ret, holdings, n_hold, rates=(0.0, 0.004, 0.008)):
    """Group1 纯多头的三档成本曲线（按调仓期扣费，只扣**实际调仓**的那部分）。

    `period_ret` —— 逐期毛收益（**按调仓期**的序列，或逐日序列但只在调仓期换手）；
    `holdings`  —— 与之对齐的**在手持仓**（list/set，逐期或逐日；某期为空集 ⇒ 不计费）；
    `n_hold`    —— 目标持仓只数（换手分母；停牌/剔除导致的**实际**只数不改变分母口径）。

    `成本_t = rate × |A_t \\ A_{t-1}| / n_hold`；**首期不计费**；
    返回 `{"turnover": 逐期换手, "curves": {"0.0040": 累计曲线, ...}}`（cumsum 口径）。

    例：50 只每周换 10 只 ⇒ 换手 20% ⇒ 每期 0.08%（0.004 档）⇒ 年化 ≈ 4%/年。
    """
    ret = np.asarray(period_ret, dtype=np.float64)
    n = ret.shape[0]
    tv = np.zeros(n, dtype=np.float64)
    prev = None
    for i in range(n):
        h = holdings[i] if i < len(holdings) else None
        # ⚠ 持仓标识可为 int **或字符串**（真实 instrument 名如 'SH600559'）
        #   —— 曾写 `set(map(int, h))`，被 check_curves.py 的真实验证当场抓到
        cur = set(h) if h is not None else set()
        if prev is not None and cur and n_hold > 0:
            tv[i] = len(prev - cur) / float(n_hold)     # 单边换手（卖出比例）
        prev = cur
    curves = {}
    for r in rates:
        curves["%.4f" % float(r)] = np.cumsum(ret - float(r) * tv)
    return {"turnover": tv, "curves": curves}
