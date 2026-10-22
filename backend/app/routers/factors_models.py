# -*- coding: utf-8 -*-
"""**请求模型**（从 `routers/factors.py` 抽出 ✓ P1-A-2）。

两组端点共用同一批模型 ✓（`EventStudyRequest.factor` 用的就是 `SingleFactorTestFactor` ✓
—— 这正是 2.9.12 那次拆不动的原因 ✓）。
"""
from typing import List, Optional

import numpy as np
from pydantic import BaseModel

class SingleFactorTestFactor(BaseModel):
    id: str = ""            # 前端标识（自定义公式 id 或 因子名）
    name: str = ""
    expression: str = ""
    source: str = "custom"  # custom / alpha158 / alpha360
    source_formula: str = ""  # 用户原文公式（custom=保存的原文；目录因子=表达式本身），仅随结果回传用于展示

class SingleFactorTestRequest(BaseModel):
    universe: str = "csi300"
    start_date: str = ""
    end_date: str = ""
    label_horizon: int = 2   # 未来 N 日收益作为预测目标（label_horizons 未传时的默认单周期）
    label_horizons: Optional[List[int]] = None  # 批量预测周期（如 [1,2,3,5,10,15,20] 或 range 展开）；
    #   传了则忽略 label_horizon；结果按"因子分组、组内周期升序"返回，每行带 horizon 字段
    parallel: bool = False   # [已弃用，仅保留兼容] 早期"多周期各占并发单元并行跑"已废弃：现为单执行体
    #   共享一次特征加载后逐周期串行统计（见 run_single_factor_tests），本字段不再生效
    factors: list[SingleFactorTestFactor] = []
    # 触发组剔除开关（默认全开，保持原行为 + 新增成交日口径）：
    exclude_limit_up_signal: bool = True  # 剔除信号日（T）涨停（选股过滤，无前视）
    exclude_limit_up_trade: bool = True   # 剔除成交日（T+1）涨停（调仓日封板买不到，与回测一致）
    exclude_suspended: bool = True        # 剔除成交日（T+1）停牌/无行情（同样买不到）
    exclude_st_t1: bool = False           # 剔除成交日（T+1）处于 ST/*ST/退市整理 的样本（日截面，T+1 当日状态）
    exclude_stock_gem: bool = False       # 剔除创业板（SZ30 段，20% 涨跌幅）
    exclude_stock_kcb: bool = False       # 剔除科创板（SH688，20% 涨跌幅）
    price_adjust: str = "backward"      # v1.19.97：默认改后复权（收益率含分红、无除权跳空 ✓）         # 复权方式：none/forward/backward（与回测对齐，默认前复权）
    freeze_suspended_price: bool = True   # 停牌日价格冻结计入未来收益（对齐聚宽口径 B）
    suspend_remove: bool = True           # 信号停牌行语义：True=SR删行(益盟/回测一致)；False=NaN占位(聚宽口径)
    price_round: bool = True              # 真实价按分取整参与因子计算（仅不复权生效，默认开；与益盟/聚宽对齐）
    warmup_days: Optional[int] = None     # 特征加载额外预热缓冲（交易日，v1.18.6）：None=按
    #   QLIB_SFT_WARMUP_DAYS（默认 250 ≈1 年）；0=关闭。长回看/动态窗口公式（DYN_*/
    #   BARSCOUNT/HHVBARS+Ref 嵌套）扩展天数无法静态推断，预热后区间首日即有收敛值；
    #   出口仍裁剪回 [start_date, ...]，固定窗口公式结果不变。
    # ---- 连续因子「分位 / 持仓期收益曲线」参数（v1.18.45 起由 API 传入，不再硬编码）----
    # ★ v2.1.23（B 主体 ①）：**成交时点** —— 决定"每期买入价 + 期内逐日盯市价"用哪个价 ✓。
    #   `t1_close`（默认 ✓ = 老口径，数字逐位不变 ✓）/ `t1_open`（次日开盘 ✓ 用户选的统一口径 ✓）。
    # ★ v2.1.34（用户 2026-10-12）：**默认改成次日开盘** ✗→✓（与面板净值引擎 `t1_open` 一致 ✓，
    #   也是行业通行做法 ✓）。⚠ 这会让**新任务**的持仓期曲线与旧结果不同 ✓（有意变更 ✓）；
    #   显式传 `t1_close` 仍得老口径 ✓。
    fill: str = "t1_open"
    # ★★ v2.1.27：**每期全仓滚动（实盘口径 · 逐点对拍专用）** —— **默认关** ✗→✓（慢 ✓）
    #   为什么要有它（用户 2026-10-11 定 ✓）：面板那条持仓曲线是**组合收益模型**（每期等权、
    #   按换手扣成本、复利；**无资金约束 / 无手数取整 / 无涨跌停** ✗），而「交易信号测试」
    #   回灌出来的是**真金白银引擎** ⇒ **原理上不可能逐点一致** ✗。
    #   打开本开关 ⇒ 用**与回灌侧同一个** `signals.period_roll.run_period_roll`（含资金约束/
    #   整手/涨跌停 ✓）把"每期名单"按实盘口径跑一遍 ✓ ⇒ 这两条才该**逐位一致** ✓（逐点对拍用 ✓）。
    #   ⚠ 代价：每种档位要多取一次全池行情面板 + 逐日回测（全 A 实测 10~20s 量级 ✗）
    #     ⇒ 故默认关、且不在任何统计口径里（纯附加 ✓，失败只落到 `error` 字段 ✓）。
    period_roll: bool = False
    # ★★ v2.4.3（用户 2026-10-15 拍板 ✓）：**离场因子**（单因子测试的"卖出侧"第一步 ✓）
    #   口径 4 条都是用户定的 ✓：
    #     ① 离场信号 = 该因子值 **> exit_threshold（默认 0）** ✓ —— 二值公式与连续因子**通用** ✓
    #        （不写"只能二值"的限制 ✗），并给**触发率预检**（日均触发占比 > 50% ⇒ 前端黄字 ✓）；
    #     ② **对照列必算** ✓ ⇒ 结果里永远同时有「不离场」（= 现有 `period_roll` ✓）与「含离场」两条 ✓；
    #     ③ 同一公式既进又出 ⇒ **直接拦** ✗（同日进+出：要么恒不交易（净值恒 1）、要么买完就卖
    #        （被手续费磨下去）——两种都没意义 ✓，前后端都拒 ✓）；
    #     ④ 与调仓日的关系：**调仓日以名单为准、离场只在期中生效** ✓（详见 `period_roll` 注释 ✓）；
    #        持币期间的离场信号 = **空转**（忽略但计数 ✓，否则用户会以为信号没生效 ✓）。
    #   ⚠ 勾了离场 ⇒ 路由里**自动打开 `period_roll`** ✓（离场只能作用在"真金白银"那条回测上 ✓）。
    #
    # ★★ v2.5.0（用户 2026-10-15：\"这个离场因子能不能搞成多选啊，要矩阵化\" ✓）：
    #   `exit_factors` = **多选** ✓ ⇒ 结果变成矩阵：**进场 × (1 + 离场数)** 个同口径回测
    #   （`1` = 必算的"不离场"对照列 ✓）⇒ 前端出对比表（含 Δ年化 / Δ回撤 ✓）。
    #   `exit_factor`（单数）= v2.4.3 兼容形态 ✓（传它等于传 `[它]` ✓）。
    #   ⚠ 多个离场**共用一个阈值** ✓（界面上就一个输入框 ✓；要各自阈值以后再加 ✓）。
    exit_factors: List[SingleFactorTestFactor] = []
    exit_factor: Optional[SingleFactorTestFactor] = None
    exit_threshold: float = 0.0
    # 下面三个只在 `period_roll=True` 时被用到（与「交易信号测试」回灌侧同名同义 ✓，
    # 默认值取回灌侧的默认 ⇒ 两边不显式指定时也对得齐 ✓）。
    cost: float = 0.004
    capital: float = 1e9
    strict_limit: bool = True
    # ★★ T1（v2.8.6，用户 2026-10-16 拍板 ✓）：**涨停判定口径**（审计 ② ✓）
    #   `None`/`"legacy"`（默认 ✓）= 老口径：成交日**收盘**涨停也拒 ✓（= 历史逐位一致 ✓）；
    #   `"open_only"` = 新口径：**只拒「开盘一字涨停/停牌」** ✓（更贴实盘 ✓）。
    #   ⚠ 仅在**成交时点为开盘**（`fill="t1_open"` ✓）时生效 ✓；否则引擎**自动退回 legacy**
    #     并把**生效值**写进 `diag["limit_judge"]` ✓（不静默分叉 ✗）。
    limit_judge: Optional[str] = None
    quantiles: int = 10                   # 分位组数（默认 10=十分位；5=旧五分位，保留兼容）
    rebalance_period: Optional[int] = None
    #   调仓期（交易日）；**None ⇒ 跟随预测周期 h**（= 「用预测周期调仓换股」，默认口径）；
    #   传值则按固定调仓期（如 5/10/20）覆盖
    topk_list: Optional[List[float]] = None

class EventStudyRequest(BaseModel):
    universe: str = "csi300"
    start_date: str = ""
    end_date: str = ""
    factor: SingleFactorTestFactor = SingleFactorTestFactor()
    max_k: int = 40                  # 最长持有交易日（1~120）
    exclude_limit_up_signal: bool = True
    exclude_limit_up_trade: bool = True
    exclude_suspended: bool = True
    exclude_st_t1: bool = False
    exclude_stock_gem: bool = False
    exclude_stock_kcb: bool = False
    price_adjust: str = "backward"      # v1.19.97：默认改后复权（收益率含分红、无除权跳空 ✓）
    price_round: bool = True
    # ★★ v2.1.52：**成交时点**（用户 2026-10-12："事件研究要跟随『成交时点』" ✓）——
    #   与单因子测试面板同名字段/同取值 ✓；默认 `t1_open`（面板默认，v2.1.34 起 ✓）。
    fill: str = "t1_open"
    suspend_remove: bool = True
    freeze_suspended_price: bool = True
    warmup_days: Optional[int] = None

class EventNavRequest(BaseModel):
    """「事件研究」面板里再画一条净值曲线（两种资金方案 + 基准 + 未触发组等权）。"""
    # ★ v2.11.39：**删掉这里的 `task_id: str`** ✗→✓ —— 下面（v1.19.61 允许为空那段）
    #   又声明了一次 `task_id: str = ""` ✓ ⇒ mypy 报 `Name "task_id" already defined` ✗。
    #   保留**带默认值**的那一条 ✓（它才是最终语义 ✓）。
    # ★★ T1（v2.8.6，用户 2026-10-16 拍板 ✓）：**涨停判定口径**（审计 ② ✓）——
    #   `None`/`"legacy"`（默认 ✓）＝ 老口径（成交日**收盘**涨停也拒 ⇒ 保守 ✓，历史逐位一致 ✓）；
    #   `"open_only"` ＝ 新口径（**只拒「开盘一字涨停/停牌」** ✓，更贴实盘 ✓）。
    #   ⚠ 仅成交时点为**开盘**时生效 ✓（否则引擎自动退回 legacy 并把生效值写进 `bt.diag` ✓ 不静默分叉 ✗）。
    limit_judge: Optional[str] = None
    hold_days: int = 20
    cost: float = 0.004
    capital: float = 1e9
    benchmark: str = "SH000300"
    # ★★ v2.9.3（用户 2026-10-17 ✓）：**最大同时持仓数** ✓（`None`/0 = 不限 ✓，老行为逐位不变 ✓）
    #   为什么需要它：信号重触发间隔若远小于持有期 ⇒ 组合里几乎每只票一直在 ⇒ 退化成"全市场等权" ✗
    #   ⇒ 信号完全不体现在结果里 ✓（实测 hold=60 时策略与参照线只差 0.33% ✓）。设个上限才有"选择性" ✓。
    max_positions: Optional[int] = None
    # ⚠ 两种来源：① 独立事件研究任务（`/event-study`）⇒ 用 `_ev`；
    #   ② 单因子测试的"秒开"路径（弹窗直接用表格里的 event_study 结果，没有独立任务）
    #      ⇒ 任务状态里存的是**按表达式分组**的 `_evs`，用因子表达式取。
    #   `task_id` 允许为空（v1.19.61）：前端拿不到任务 id 时，仅凭 `factor_id` 也能在**最近的任务**里
    #   找回触发事件（用户 2026-09-15 报「净值曲线暂不可用：需要任务上下文」）。
    task_id: str = ""
    factor_id: Optional[str] = None
    # ★★ v2.1.65：**成交时点**（用户 2026-10-12："事件研究的净值曲线是跟随面板参数次日收盘/开盘走的
    #   还是写死了次日收盘价？我怎么测下来两个数一模一样" ✓ —— 观察**完全正确** ✓，实测根因 ✓）：
    #   本端点原来把 `fill` **写死 `t1_open`** ✗（见 `run_backtest(...)` 调用 ✓，只是当年为对拍
    #   硬编码的 ✗）⇒ 面板选"次日收盘"也照样开盘成交 ⇒ 两种设置**数字必然一模一样** ✗✗。
    #   现在跟随面板/请求 ✓。
    # ★★ v2.1.71：**默认从 `"t1_open"` 改成 `None`** ✗→✓ —— 否则"跟随面板那次口径"的兜底
    #   **永远不触发** ✗：pydantic 会把"没传"填成 `"t1_open"` ✗ ⇒ 下面
    #   `req.fill or rq["fill"]` 的左边恒为真 ✓。**实测证据**（`ai_test/check_export_fill2.log` ✓）：
    #   任务按"次日收盘"提交 ✓，而净值请求**故意不传 `fill`** ⇒ 拿到的仍是 `t1_open` 那条
    #   （期末 0.9981 vs 收盘那条 0.9999 ✗）⇒ 导出/净值一起写错口径 ✗（用户 2026-10-12 报的 ✓）。
    #   ⇒ 语义：`None` = **跟随那次任务（面板）的口径** ✓；显式传三个取值仍照用 ✓（可切换 ✓）。
    fill: Optional[str] = None

class EventNavCancelRequest(BaseModel):
    """关弹窗取消（v1.20.15）：前端只要把 task_id 报上来即可 ✓。"""
    task_id: str = ""

