# -*- coding: utf-8 -*-
"""交易信号测试的请求模型（pydantic）—— 只放 API 契约，业务口径留在 `engine/replay` 里。"""
from __future__ import annotations

from typing import Dict, List, Optional

from pydantic import BaseModel, Field


class SignalTestRequest(BaseModel):
    """一次信号测试的全部入参（同步接口：函数返回即结果，符合"秒出"的用户要求）。"""

    # ---- 文件 ----
    content: str = Field("", description="CSV 文本内容（UTF-8 文本；与 content_b64 二选一）")
    content_b64: Optional[str] = Field(
        None,
        description="CSV 原始字节的 base64（**推荐**）：实测用户文件是 **GBK**，"
                    "前端直接读文本会乱码；传字节让后端按 utf-8-sig→utf-8→gbk→gb18030 嗅探解码")
    filename: str = Field("", description="仅用于回显/诊断")
    perf_b64: Optional[str] = Field(
        None,
        description="（可选）聚宽《收益概述》result_1.csv 的 base64：有它就把**官方净值**叠加到图上做校准 "
                    "（同时逐日核对成交明细的买卖金额是否完整）")
    kind: Optional[str] = Field(None, description="强制指定格式：signal_list / jq_trades；默认自动识别")

    # ---- 通用 ----
    cost: float = Field(0.004, description="**往返（买+卖）合计**费率，与连续信号 0.004 同义")
    benchmark: str = Field("SH000300", description="基准：SH000300/SH000905/SH000852/SH000906/SH000985")
    price_mode: str = Field("forward", description="forward=后复权价（收益率=真实前复权）；none=真实价")

    # ---- 模式①：信号清单 ----
    horizon: int = Field(60, ge=1, le=250, description="预测周期 N：事件研究逐 k 1..N + 持有 N 日")
    # ★★ v2.1.74：**默认改 `None`** ✗→✓（审计 #9 ✓，与 `EventNavRequest.fill` 同一个坑 ✓）——
    #   原来写 `"t1_open"` ⇒ pydantic 把"没传"填成非空 ✗ ⇒ `req.fill or 文件里的 fill` 的兜底
    #   **永远不触发** ✗ ⇒ API 直连/老前端会**无声退回默认口径** ✓，与那份导出文件真正的口径分叉 ✓
    #   （用户 2026-10-12："信号测试里有没有漏掉" ✓）。`None` = 跟随导出文件里记录的口径 ✓。
    fill: Optional[str] = Field(
        None, description="成交时点：t_close(信号日收盘) / t1_open(次日开盘) / t1_close(次日收盘)；"
                          "None=跟随导出文件里记录的口径")
    capital: float = Field(1e9, description="初始资金（默认 10 亿：1000 只同时触发也能等权买）")
    strict_limit: bool = Field(True, description="严格涨跌停/停牌口径（买不进=放弃，卖不出=顺延）")
    # ★★ T1（v2.8.6，用户 2026-10-16 拍板 ✓）：**涨停判定口径**（审计 ② ✓）——
    #   `None` / `"legacy"`（**默认** ✓）= 老口径：成交日**收盘**涨停也拒 ⇒ 保守 ✓（= 历史逐位一致 ✓）；
    #   `"open_only"` = 新口径：**只拒「开盘一字涨停 / 停牌」** ✓（更贴实盘，通常提高收益 ✓）。
    #   ⚠ 老口径在"**开盘**成交"时用当日**收盘**信息 = 未来信息 ✗（用户原话："可能开盘 1 秒拉涨停，
    #     其实你吃不到" ⇒ 所以默认仍留保守口径 ✓）。
    #   ⚠ `open_only` **只在成交时点为开盘时生效** ✓（收盘成交没有"开盘一字板"概念 ⇒ 引擎
    #     自动退回 `legacy` 并写进 `diag["limit_judge"]` ✓，绝不静默分叉 ✗）。
    limit_judge: Optional[str] = Field(
        None,
        description="涨停判定口径：legacy(默认；收盘涨停也拒) / open_only(只拒开盘一字板)；"
                    "None=legacy；⚠ 仅成交时点为开盘时生效")
    alloc_default: str = Field("event_even", description="默认展示的资金方案：event_even / cash_even")
    rebal_band: float = Field(0.0, ge=0.0, le=0.5,
                              description="事件再平衡死区（相对目标市值的比例）：0=每次事件完全调平（默认），"
                                          "调大 1%~10% 可显著降低换手与费用")
    pool: str = Field("all", description="事件研究里「未触发组」的池：all/csi300/csi500/csi800/csi1000/@signals")
    pool_codes: Optional[List[str]] = Field(None, description="池=@signals 时可由后端填（信号标的并集）")
    signals_only: bool = Field(False, description="只跑信号清单部分（跳过事件研究，最快）")
    backtest_only: bool = Field(False, description="只跑回测（跳过事件研究）")
    # ★★ v2.1.54：**触发组的剔除开关**（用户 2026-10-12："要修。估计是这里有问题" ✓）——
    #   它们直接决定**触发组样本** ⇒ 直接决定事件研究的数字 ✓（实测同一份信号
    #   4.84% vs 5.9% ✓ 差的就是这条 ✓）。三个值语义：
    #     `None`（默认 ✓）= **用导出文件里记的那个** ✓（新文件已写 ✓；旧文件没有 ⇒ 视为 False ✓）；
    #     `True/False` = 显式覆盖 ✓（给手工对比用 ✓）。
    #   ⚠ 与单因子测试面板同名同义 ✓（口径必须一致，否则又变成"两个页面两个数" ✗）。
    exclude_limit_up_signal: Optional[bool] = Field(
        None, description="剔除信号日(T)涨停样本：None=按导出文件；True/False=显式覆盖")
    exclude_limit_up_trade: Optional[bool] = Field(
        None, description="剔除成交日(T+1)涨停样本（一字板买不进）")
    exclude_suspended: Optional[bool] = Field(None, description="剔除成交日(T+1)停牌样本")
    exclude_st_t1: Optional[bool] = Field(None, description="剔除成交日(T+1) ST（本侧缺 IS_ST 字段 ⇒ 暂跳过并提示）")
    exclude_stock_gem: Optional[bool] = Field(None, description="剔除创业板（SZ30 开头）")
    exclude_stock_kcb: Optional[bool] = Field(None, description="剔除科创板（SH688 开头）")

    # ---- 模式②：聚宽流水 ----
    jq_capital: Optional[float] = Field(None, description="聚宽账户初始资金；缺省=首日买入总额（可手改）")
    jq_include_exact: bool = Field(True, description="是否计算 C（成交价+实际手续费精确净值）")
    jq_scale_capital: bool = Field(True, description="A/B 是否按 capital/首日买入总额 等比缩放股数")


class SignalTestResponse(BaseModel):
    ok: bool = True
    mode: str = ""
    elapsed: float = 0.0
    parse: Dict = Field(default_factory=dict)
    event: Optional[Dict] = None
    backtest: Optional[Dict] = None
    replay: Optional[Dict] = None
    benchmarks: Dict = Field(default_factory=dict)
    warnings: List[str] = Field(default_factory=list)
