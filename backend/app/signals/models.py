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
    kind: Optional[str] = Field(None, description="强制指定格式：signal_list / jq_trades；默认自动识别")

    # ---- 通用 ----
    cost: float = Field(0.004, description="**往返（买+卖）合计**费率，与连续信号 0.004 同义")
    benchmark: str = Field("SH000300", description="基准：SH000300/SH000905/SH000852/SH000906/SH000985")
    price_mode: str = Field("forward", description="forward=后复权价（收益率=真实前复权）；none=真实价")

    # ---- 模式①：信号清单 ----
    horizon: int = Field(60, ge=1, le=250, description="预测周期 N：事件研究逐 k 1..N + 持有 N 日")
    fill: str = Field("t1_open", description="成交时点：t_close(信号日收盘) / t1_open(次日开盘) / t1_close(次日收盘)")
    capital: float = Field(1e9, description="初始资金（默认 10 亿：1000 只同时触发也能等权买）")
    strict_limit: bool = Field(True, description="严格涨跌停/停牌口径（买不进=放弃，卖不出=顺延）")
    alloc_default: str = Field("event_even", description="默认展示的资金方案：event_even / cash_even")
    rebal_band: float = Field(0.0, ge=0.0, le=0.5,
                              description="事件再平衡死区（相对目标市值的比例）：0=每次事件完全调平（默认），"
                                          "调大 1%~10% 可显著降低换手与费用")
    pool: str = Field("all", description="事件研究里「未触发组」的池：all/csi300/csi500/csi800/csi1000/@signals")
    pool_codes: Optional[List[str]] = Field(None, description="池=@signals 时可由后端填（信号标的并集）")
    signals_only: bool = Field(False, description="只跑信号清单部分（跳过事件研究，最快）")
    backtest_only: bool = Field(False, description="只跑回测（跳过事件研究）")

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
