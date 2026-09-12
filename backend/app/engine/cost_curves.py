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
  · 年化用 **252 交易日/年**（与回测页 `engine/metrics.py` 口径一致）；
  · ⚠ **简化估算：未考虑涨跌停、停牌、流动性冲击**（展示时必须注明）。

自检：`ai_test/test_cost_curves.py`（总失败 0；含随机 200 期与朴素实现逐位对拍、
`_turnover_by_k` 与集合版 `_topgroup_cost_curves` 逐位交叉验证）。
"""
import numpy as np

TRADING_DAYS_PER_YEAR = 252.0        # 年化换算（与 `engine/metrics.py` 的 252 一致）


def _turnover_by_k(rank_mat, ks):
    """名次矩阵 → **各 K 的逐期单边换手**（一次掩码计数；**不逐 K 重排、不用 Python 集合**）。

    `rank_mat` —— `(n_rebal, n_inst)`：**行 = 调仓日（按时间序）**、**列 = 同一股票轴**
      （当日没有该股：未上市/停牌/被剔除/无行情 ⇒ 填 **NaN**，NaN 一律视为"未持有"）。
    `ks` —— 目标持仓只数（如 `[1, 3, …, 200, 187, 374]`），可含重复，按序输出。

    返回 `(n_k, n_rebal - 1)`：第 j 行 = `K = ks[j]` 的逐期换手，第 t 列 = 第 t → t+1 个调仓期
    （`n_rebal < 2` 时形状为 `(n_k, 0)`）。

    **口径与 `_topgroup_cost_curves` 完全一致**（两者必须同口径，否则同一组合两张表对不上）：
      · `换手_t = |持仓_{t-1} \\ 持仓_t| / K` —— 分子是**卖出只数**、分母是**目标持仓数 K**；
      · **昨日没有、今日持有**（新建仓，含首期建仓）**不计入换手** ⇒ 天然满足"首期建仓不计费"；
      · **当期持仓为空**（清仓/全不可买；名次列全 NaN）⇒ **该期不计费**（与
        `_topgroup_cost_curves` 的"当期空集"行为一致，见自检 B4）。
    ⇒ 「K = 默认 10%」那一行能与已上线的 `topk_curves` **逐位对上**（生产侧内建该断言，
      见 `factors/topk_sensitivity.py` 的 `verify_turnover`）。

    ⚠ 与 `_topgroup_cost_curves` 的分工：本函数吃**名次矩阵**、一次算**全部 K**
      （K 只影响一次 `<=` 比较，10 个 K 都只是几次 n 长布尔运算），服务「K 敏感度汇总表」；
      后者吃**持仓集合**、产出**三档累计曲线 + 单期明细**，服务「持仓期收益曲线」。
    """
    rm = np.asarray(rank_mat, dtype=np.float64)
    ks_arr = np.asarray(ks, dtype=np.float64)
    n_slot = rm.shape[0]
    out = np.zeros((ks_arr.shape[0], max(n_slot - 1, 0)), dtype=np.float64)
    if n_slot < 2:
        return out
    for j in range(ks_arr.shape[0]):
        k = ks_arr[j]
        if not (k > 0):                      # K ≤ 0 / NaN：无持仓 ⇒ 换手 0（不除零）
            continue
        held = rm <= k                       # NaN → False ✓
        sold = np.count_nonzero(held[:-1] & ~held[1:], axis=1)   # 昨持有、今不持有 = 卖出
        n_cur = np.count_nonzero(held[1:], axis=1)               # 当期实际持仓只数
        out[j] = np.where(n_cur > 0, sold / k, 0.0)              # 当期空仓 ⇒ 不计费
    return out


def _annualize_cost(avg_turnover, rate, rebalance_period, trading_days=TRADING_DAYS_PER_YEAR):
    """单期平均换手 → **年化成本**（小数；`0.0119` = 1.19%/年）。

    `年化 = 单期平均换手 × 往返费率 × 每年调仓期数`，`每年调仓期数 = 交易日 / 调仓期`。
    用**算术**累加（成本是每期固定扣减，不随净值复利），与 `metrics.py` 的几何年化略有不同。
    """
    return float(avg_turnover) * float(rate) * (float(trading_days) / float(rebalance_period))


def _topgroup_cost_curves(period_ret, holdings, n_hold, rates=(0.0, 0.004, 0.008)):
    """Group1 纯多头的三档成本曲线（按调仓期扣费，只扣**实际调仓**的那部分）。

    `period_ret` —— 逐期毛收益（**按调仓期**的序列，或逐日序列但只在调仓期换手）；
    `holdings`  —— 与之对齐的**在手持仓**（list/set，逐期或逐日；某期为空集 ⇒ 不计费）；
    `n_hold`    —— 换手分母（目标持仓只数）：
                   · **标量**：固定只数组合（TopK=K，停牌/剔除导致的**实际**只数不改变分母口径）；
                   · **逐期数组**：只数逐期变化的组合（如**分位组**，组内只数逐日不同）
                     ⇒ 用「**当期在手只数**」做分母（`换手_t = 当期卖出只数 / 当期在手只数`）。
                   `n_hold ≤ 0` 或当期空仓 ⇒ 该期不计费。

    `成本_t = rate × |A_t \\ A_{t-1}| / n_hold`；**首期不计费**；
    返回 `{"turnover": 逐期换手, "curves": {"0.0040": 累计曲线, ...}}`（cumsum 口径）。

    例：50 只每周换 10 只 ⇒ 换手 20% ⇒ 每期 0.08%（0.004 档）⇒ 年化 ≈ 4%/年。

    返回 `{"turnover": …, "curves": {算术累加}, "curves_compound": {复利累乘}}`：
      · `curves`          —— 逐期净收益**算术累加**（`cumsum(ret − 费率×换手)`），"平均每期赚多少"的视角；
      · `curves_compound` —— **复利累乘**（默认展示口径，v1.18.47）：每期净收益
        `(1+r_t)(1−费率×换手_t) − 1`，即「收益先滚入本金、再按往返费率扣费」= **实盘满仓复投口径**。
    ⚠ 两口径**不可混比**、绝对值差异可很大：`log(1+复利) ≈ Σr − ½Σr²` ⇒ 强策略（期收益均值
      远大于波动罚项）复利**高于**算术（实测 csi1000 负市值对数 5 年：净值 4.38 vs 2.67），
      弱/无效策略则复利**低于**算术（波动损耗）。年化维度两者接近，累计总收益差一倍很正常。
    """
    ret = np.asarray(period_ret, dtype=np.float64)
    n = ret.shape[0]
    nh = (None if np.isscalar(n_hold)
          else np.asarray(n_hold, dtype=np.float64))
    tv = np.zeros(n, dtype=np.float64)
    prev = None
    for i in range(n):
        h = holdings[i] if i < len(holdings) else None
        # ⚠ 持仓标识可为 int **或字符串**（真实 instrument 名如 'SH600559'）
        #   —— 曾写 `set(map(int, h))`，被 check_curves.py 的真实验证当场抓到
        cur = set(h) if h is not None else set()
        den = float(n_hold) if nh is None else (float(nh[i]) if i < nh.shape[0] else 0.0)
        if prev is not None and cur and den > 0:
            tv[i] = len(prev - cur) / den                # 单边换手（卖出比例）
        prev = cur
    curves = {}
    curves_cmp = {}
    for r in rates:
        rf = float(r)
        curves["%.4f" % rf] = np.cumsum(ret - rf * tv)
        # 复利口径（v1.18.47，前端默认展示）：首期 tv=0 ⇒ 建仓不计费（与算术口径同规则）
        per = (1.0 + ret) * (1.0 - rf * tv) - 1.0
        curves_cmp["%.4f" % rf] = np.cumprod(1.0 + per) - 1.0
    return {"turnover": tv, "curves": curves, "curves_compound": curves_cmp}
