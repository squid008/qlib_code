# -*- coding: utf-8 -*-
"""
回测相关的数据模型（Pydantic）。
"""
from __future__ import annotations

from datetime import date
from typing import List, Optional, Dict, Any

from pydantic import BaseModel, Field


class BacktestRequest(BaseModel):
    """回测请求参数"""
    universe: str = Field("csi300", description="股票池：csi300/csi500/all 等")
    instruments: Optional[List[str]] = Field(None, description="自定义股票列表（如填了则优先使用）")
    start_date: str = Field("2022-01-01", description="回测开始日期")
    end_date: str = Field("2023-12-31", description="回测结束日期")
    # 模型
    model: str = Field("LightGBM", description="模型：LightGBM/XGBoost/Linear 等")
    model_params: Optional[Dict[str, Any]] = Field(
        None, description="模型超参（LightGBM/XGBoost 等）。未填的键使用 Qlib 默认值。例：{num_leaves: 210, max_depth: 8, min_child_samples: 20, learning_rate: 0.0421}"
    )
    topk: int = Field(50, description="TopK 选股数量")
    n_days_hold: int = Field(10, description="持仓周期（天）：每 N 个交易日调仓一次，1=每日调仓")
    # n_days_learn 已废弃：训练窗口实际由 train_win/train_unit 控制（见 _gen_rolling_segments）
    n_days_learn: Optional[int] = Field(None, description="[废弃] 训练窗口天数，请用 train_win/train_unit")
    # 数据源
    data_source: str = Field("qlib", description="数据源：qlib / rqalpha")
    data_source_provider_uri: Optional[str] = Field(
        None, description="数据源 provider_uri（如 Qlib 数据路径 / rqalpha bundle 路径）"
    )
    feature: str = Field("Alpha158", description="特征集：Alpha158 / Alpha360")
    selected_features: Optional[List[str]] = Field(
        None,
        description=(
            "自定义特征名子集（如 [\"KMID\", \"ROC5\", \"MA20\"]）。"
            "为空/None 时使用该特征集全量特征；非空时 handler 只计算这些特征。"
            "将来因子库扩展后，这里可传入任意目录中的因子名。"
        ),
    )
    # bins 保留用于兼容，但分层回测当前固定为 5 组（见 _compute_layers）
    bins: int = Field(5, description="[保留] 分层组数，当前固定为 5 组")
    # 交易成本与成交设置
    deal_price: str = Field("close", description="成交价基准：close / open / vwap")
    open_cost: float = Field(0.0005, description="买入手续费（如 0.0005 = 0.05%）")
    close_cost: float = Field(0.0015, description="卖出手续费（如 0.0015 = 0.15%）")
    min_cost: float = Field(5.0, description="单笔最低手续费（元）")
    impact_cost: float = Field(0.0005, description="滑点/市场冲击成本比例（如 0.0005 = 0.05%）")
    volume_threshold: Optional[float] = Field(
        None, description="成交量限制：单笔成交不超过当日成交量比例（如 0.25 = 25%）。None 表示不限量理想成交"
    )
    limit_threshold: Optional[float] = Field(
        None, description="涨跌停限制（绝对值比例，如 0.095 = 涨跌停 9.5% 无法交易）。None 表示不设涨跌停"
    )
    trade_unit: Optional[int] = Field(None, description="每手股数（如 100）。None 表示不按手数取整")
    # 训练/测试划分（滚动训练）
    split_mode: str = Field(
        "single", description="训练/测试划分方式：single=一次性训练(用回测前窗口)；custom=自定义滚动训练"
    )
    train_win: int = Field(12, description="训练窗口数值（配合 train_unit 使用）")
    train_unit: str = Field("month", description="训练窗口单位：day/week/month")
    test_win: int = Field(3, description="测试窗口数值（配合 test_unit 使用）")
    test_unit: str = Field("month", description="测试窗口单位：day/week/month")
    step_win: Optional[int] = Field(
        None, description="滚动步长数值（默认等于测试窗口大小）。None 表示每次推进一个测试窗口"
    )
    step_unit: Optional[str] = Field(None, description="滚动步长单位：day/week/month。None 表示与测试窗口相同")
    initial_capital: float = Field(
        100000000.0, description="回测起始总资产（元），如 1000000 = 100万"
    )
    load_model_task_id: Optional[str] = Field(
        None, description="复用某次回测训练好的模型权重（跳过训练，直接预测回测）。填该任务的 task_id"
    )


class BacktestTask(BaseModel):
    """回测任务状态"""
    task_id: str
    status: str = Field(..., description="pending/running/success/failed")
    progress: float = Field(0.0, description="进度 0-100")
    message: str = ""
    created_at: str = ""
    result: Optional["BacktestResult"] = None


class BacktestResult(BaseModel):
    """回测结果"""
    annualized_return: Optional[float] = None
    annualized_excess_return: Optional[float] = None
    sharpe: Optional[float] = None
    max_drawdown: Optional[float] = None
    win_rate: Optional[float] = None
    benchmark_return: Optional[float] = None
    total_return: Optional[float] = None
    report_df: Optional[Dict[str, Any]] = Field(None, description="完整回测报告（dict 形式）")
    nav: Optional[List[Dict[str, Any]]] = Field(None, description="净值曲线数据 [{date, value, benchmark}]")
    trades: Optional[List[Dict[str, Any]]] = Field(
        None,
        description=(
            "逐日调仓记录 [{date, instrument, direction, amount, deal_price, "
            "trade_value, trade_cost, ffr}]，含每笔订单的成交价与成本"
        ),
    )
    # 分层回测（5组）与 IC 曲线分析
    layer_returns: Optional[Dict[str, Any]] = Field(
        None,
        description=(
            "分层回测数据（5组）。结构：{segments: [{segment, groups:[{date, Group1..Group5, long_short, long_average}]}], "
            "merged: {groups:[...]} }。段标签从1开始。"
        ),
    )
    ic_analysis: Optional[Dict[str, Any]] = Field(
        None,
        description=(
            "IC 分析数据。结构：{train: [{segment, points:[{date, ic, rank_ic}], mean_ic, icir, mean_rank_ic, rank_icir}], "
            "test: [...], merged_test: {points:[...], mean_ic, icir}}。段标签从1开始。"
        ),
    )


class TaskIdResponse(BaseModel):
    task_id: str


# 前向引用解析
BacktestTask.model_rebuild()
