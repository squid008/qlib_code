# -*- coding: utf-8 -*-
"""
Qlib 回测引擎（适配新版 Qlib，从源码安装）。

基于 Qlib 标准工作流（参考 examples/workflow_by_code.py）：
    数据 -> 特征(Alpha158) -> 模型(LightGBM) -> 策略(TopK) -> 回测(PortAnaRecord)

流程：
1. qlib.init()
2. init_instance_by_config 构造 dataset（Alpha158）和 model
3. R.start(experiment) 内 model.fit + SignalRecord 生成预测
4. PortAnaRecord 完成回测并生成风险指标
5. 从 recorder 提取 report_normal / port_analysis 汇总结果

注意：本引擎在独立后台线程中运行（task_manager），不阻塞 FastAPI 主线程。
"""
from __future__ import annotations

import os
import warnings
from typing import Any, Dict, List, Optional

from ..models.backtest import BacktestRequest, BacktestResult
from .context import (
    set_progress_callback,
    set_artifact_dir,
    set_cancel_check,
    reset_progress,
    get_artifact_dir as _get_artifact_dir,
    check_cancel as _check_cancel,
    report as _report,
)
from .utils import (
    _default_qlib_uri,
    _default_exp_uri,
    _pick_benchmark,
    _fallback_benchmark,
    _offset_date,
    _add_period,
    _sanitize_name,
    _make_artifact_dir,
)
from .analysis import (
    _get_pred_label,
    _compute_benchmark_returns,
    _compute_layers,
    _compute_ic,
    _compute_analysis,
)
from .metrics import (
    _aggregate_from_nav,
    _extract_result,
    _find_report_fallback,
    _get_segment_end_account,
    _extract_trades,
    _extract_end_position,
)
from .artifacts import (
    _extract_model_artifacts,
    _extract_feature_importance,
    _save_model_artifacts,
    _save_model_object,
    _read_train_feature_names,
    _dataset_feature_names,
    _verify_reuse_feature_order,
    _load_model_object,
    _task_has_segment_models,
    _sanitize_json,
    _save_result_json,
    _save_backtest_params,
    _save_train_signature,
)
from .charts import (
    _save_curve_snapshot,
)
from ..datasource.factory import get_data_source, list_data_sources
# 导入即注册自定义算子 + patch register_all_ops（必须在 qlib.init() 之前生效）
from ..factors.ops_ext import ensure_ops_registered as _ensure_ops_registered

warnings.filterwarnings("ignore")


def _ensure_qlib_init(provider_uri):
    """线程安全的 qlib.init：只在第一次调用时真正初始化，后续线程直接跳过。

    背景：qlib.init() 会重置全局 C/D（config、数据模块）状态。若多个回测任务
    线程并发调用 qlib.init()，会互相覆盖导致 `KeyError('dataset_cache')`、
    `Please run qlib.init() first` 等错误。因此必须全局只 init 一次。

    统一委托 app.services.qlib_runtime.ensure_qlib_init：保证 custom_ops 恒非空，
    worker 子进程靠 C.custom_ops 导入外挂算子模块，避免 "operator is not registered"。
    """
    from ..services.qlib_runtime import ensure_qlib_init

    ensure_qlib_init(provider_uri)


def _resolve_data_source(req: BacktestRequest):
    """通过数据源工厂解析本次回测使用的数据源。

    - 走 get_data_source() 统一抽象，而不是散落硬编码。
    - 当前回测引擎依赖 qlib 数据目录格式（DatasetH / D.features / D.calendar），
      因此仅 qlib 数据源可用于回测；rqalpha 数据源已接入数据查询 API，
      但回测引擎尚未桥接，返回明确错误而非静默降级/误导。
    - 返回 (data_source_obj, provider_uri)。provider_uri 优先用请求显式指定的，
      否则用数据源自身的 provider_uri。
    """
    source_name = (getattr(req, "data_source", None) or "qlib").lower()
    try:
        ds = get_data_source(source_name)
    except KeyError:
        raise ValueError(
            f"数据源 '{source_name}' 不存在或未启用。可用数据源：{list_data_source_names()}"
        )

    # 回测引擎当前仅支持 qlib 数据目录
    if source_name != "qlib":
        raise ValueError(
            f"回测引擎当前仅支持 qlib 数据源（'{source_name}' 已接入数据查询 API，"
            "但回测尚未桥接到该数据源）。请将 data_source 设为 qlib 后再回测。"
        )

    provider_uri = getattr(req, "data_source_provider_uri", None) or getattr(ds, "provider_uri", None)
    return ds, provider_uri


def list_data_source_names() -> list:
    """返回当前可用数据源名称列表（供错误提示使用）。"""
    return list(list_data_sources().keys())


def run_backtest(req: BacktestRequest, work_dir: Optional[str] = None,
                 task_id: Optional[str] = None) -> BacktestResult:
    import qlib
    from qlib.constant import REG_CN
    from qlib.data import D
    from qlib.utils import init_instance_by_config
    from qlib.workflow import R

    # 通过数据源工厂解析数据源（抽象层打通；仅 qlib 可用于回测）
    _, provider_uri = _resolve_data_source(req)

    # 校验日期格式，避免不完整/非法日期导致日期计算崩溃
    from datetime import datetime
    for dfield in ("start_date", "end_date"):
        dval = getattr(req, dfield, None)
        if not dval:
            raise ValueError(f"缺少回测日期字段 {dfield}")
        try:
            datetime.strptime(str(dval), "%Y-%m-%d")
        except Exception:
            raise ValueError(f"回测日期字段 {dfield} 格式不合法: {dval!r}（应为 YYYY-MM-DD）")
    if str(req.end_date) < str(req.start_date):
        raise ValueError(f"结束日期({req.end_date})不能早于开始日期({req.start_date})")

    # 校验结束日期不能晚于数据的最后交易日（避免预测/回测区间超出数据范围）
    try:
        import pandas as _pd
        from qlib.data import D
        _ensure_qlib_init(provider_uri)
        _full_cal = D.calendar()  # 全量交易日历（含未来日期则过滤，qlib 会返回已有数据的日历）
        if _full_cal is not None and len(_full_cal) > 0:
            _last_day = str(_pd.Timestamp(_full_cal[-1]).strftime("%Y-%m-%d"))
            if str(req.end_date) > _last_day:
                raise ValueError(
                    f"结束日期({req.end_date})晚于数据最后交易日({_last_day})，请调整回测区间"
                )
    except ValueError:
        raise
    except Exception:
        # 获取日历失败不阻塞（可能数据源不同），由后续逻辑报错
        pass

    # 设置模型产物保存目录（可读目录名 + task_id 后缀保证唯一，用于复现/查看训练结果）
    if task_id and work_dir:
        resume_from = getattr(req, "resume_task_id", None)
        if resume_from:
            # 断点续跑：复用源任务目录（已完成的段 seg_result 会被 _run_rolling 检测跳过），
            # 这样续跑能接着未完成的部分继续，而不是新建空目录。
            from ..services import artifacts_service as _art_svc
            try:
                src_dir = _art_svc.find_artifact_dir(resume_from)
            except Exception:
                src_dir = None
            if src_dir and os.path.isdir(src_dir):
                art_dir = src_dir
            else:
                art_dir = _make_artifact_dir(work_dir, task_id, req)
        else:
            art_dir = _make_artifact_dir(work_dir, task_id, req)
        set_artifact_dir(art_dir)
        # 保存完整回测参数快照（params.json + meta.json），供复现模式对照
        _save_backtest_params(art_dir, req)
        # 保存训练签名快照（train_signature.json）：代码/数据/依赖/特征指纹，供历史模型追溯
        _save_train_signature(art_dir, req)
    else:
        set_artifact_dir(None)

    # 解决 mlflow filesystem 后端进入维护模式的问题：
    # 1) 允许使用文件存储（作为兜底）
    os.environ.setdefault("MLFLOW_ALLOW_FILE_STORE", "true")

    _report(3, "初始化 Qlib...")
    # 全局只 init 一次（多线程并发回测时避免重复初始化互相踩踏全局 C/D 状态）
    _ensure_qlib_init(provider_uri)

    # 2) 使用 sqlite 作为实验追踪后端，避免 filesystem 维护模式限制
    _exp_uri = _default_exp_uri(work_dir)
    R.set_uri(_exp_uri)

    # 确定股票池
    # 注意：必须使用 qlib 原生代码格式（大写，如 SH600000），
    # 因为 qlib 的 exchange / 回测引擎用大写格式匹配行情。转小写会导致
    # is_stock_tradable 查不到行情，策略无法建仓。
    _report(8, "确定股票池...")
    if req.instruments:
        instruments = [str(i).upper() for i in req.instruments]
    else:
        # 用 start_time 过滤：cn_data 是抽样数据，all.txt 里混有大量"无行情/未收录"
        # 的代码（如 SH600001/SH600002 等老八股）。带 start_time 后 qlib 只返回该
        # 日期前已上市且有效的股票，避免取到无数据股票导致训练集 Empty data。
        inst_scope = D.instruments(market=req.universe)
        instruments = [
            str(i).upper()
            for i in D.list_instruments(inst_scope, start_time=req.start_date, as_list=True)
        ]

        # 兜底边界处理（防止某些 market 返回异常）：
        # 排除北交所(BJ，2021-11 才开市，早期段无数据)与指数代码(SH000/SH88/SH89/SZ39)。
        filtered = []
        for i in instruments:
            c = str(i)
            if c.startswith("BJ"):
                continue
            if c.startswith(("SH000", "SH88", "SH89", "SZ39")):
                continue
            filtered.append(i)
        instruments = filtered

    # 不再限制股票数量：全 A 股票池直接用全部（覆盖 SH/SZ/BJ 三个市场），
    # 避免抽样/截断导致只剩单一市场。性能问题由模型复杂度与机器性能决定。

    benchmark = _pick_benchmark(req.universe, instruments)

    # 关键检查点：用户点停止后立即抛出
    _check_cancel()

    # 判断训练/测试划分模式
    split_mode = (req.split_mode or "single").lower()

    if split_mode != "custom":
        # 一次性训练（single）：用回测前 train 窗口训练，整个回测区间测试
        _report(12, "训练/测试划分：一次性训练")
        _check_cancel()  # 分发前再检查一次
        result = _run_single(req, instruments, benchmark)
    else:
        # 自定义滚动训练
        _report(12, "训练/测试划分：自定义滚动训练")
        _check_cancel()
        result = _run_rolling(req, instruments, benchmark)

    # 生成净值曲线 + 参数快照图（存入 artifacts）
    _report(96, "生成回测曲线快照...")
    art_dir = _get_artifact_dir()
    if art_dir:
        _save_curve_snapshot(art_dir, req, result)
        _save_result_json(art_dir, result)

    _report(100, "完成")
    return result


def _build_dataset(req: BacktestRequest, instruments: list, train_seg, test_seg,
                   predict_start: Optional[str] = None) -> dict:
    """构造 dataset 配置。
    train_seg: (start, end) 训练段；test_seg: (start, end) 测试段（=回测窗口）。
    predict_start: 可选。早于 test_seg[0] 的"预测起点"，用于滚动回测首段预热：
      预测（SignalRecord / model.predict）从该日起生成，从而覆盖回测起点前
      n_days_hold 个交易日，使回测首日调仓（shift=1，需用 T-1 信号）能拿到信号建仓，
      避免首段开头整段空仓。回测窗口仍为 test_seg（由 PortAnaRecord start_time 决定），
      预热期预测只被首日调仓的 T-1 取用，IC/分层统计由调用方用 clip_start 裁剪。
    handler 数据范围需覆盖 train + test + 特征窗口。
    """
    from qlib.utils import init_instance_by_config

    test_start = test_seg[0]
    test_end = test_seg[1]
    train_start, train_end = train_seg

    # handler 从训练段往前扩一段（特征窗口），结束到测试段末尾
    feature_extra = 10  # 额外预留的特征回看天数
    handler_start = _offset_date(train_start, -feature_extra)
    handler_kwargs = {
        "start_time": handler_start,
        "end_time": test_end,
        "fit_start_time": train_start,
        "fit_end_time": train_end,
        "instruments": instruments,
        "price_adjust": getattr(req, "price_adjust", "none") or "none",
    }
    # 特征来源（优先级）：混合(Mixed = Alpha158 子集 + Alpha360 子集 + 自定义公式)
    # > 单一 Alpha158/360（子集） > 历史行为（勾了自定义公式则只用公式特征）。
    # 混合模式下 selected_features 传带前缀名（A158_*/A360_*），custom_formulas 作为附加特征。
    custom_formulas = getattr(req, "custom_formulas", None) or []
    feature = (getattr(req, "feature", "Alpha158") or "Alpha158").lower()
    if feature == "mixed":
        handler_cls = "MixedHandler"
        handler_module = "app.factors.handler"
        if req.selected_features:
            handler_kwargs["fields"] = list(req.selected_features)
        if custom_formulas:
            handler_kwargs["formulas"] = list(custom_formulas)
    elif custom_formulas:
        # 非混合模式：勾了自定义公式则只用公式（历史行为）
        handler_cls = "FormulaHandler"
        handler_module = "app.factors.handler"
        handler_kwargs["formulas"] = list(custom_formulas)
    elif feature == "alpha360":
        handler_cls = "SelectedAlpha360"
        handler_module = "app.factors.handler"
        if req.selected_features:
            handler_kwargs["fields"] = list(req.selected_features)
    else:
        handler_cls = "SelectedAlpha158"
        handler_module = "app.factors.handler"
        if req.selected_features:
            handler_kwargs["fields"] = list(req.selected_features)
    # 预测周期：模型预测未来 N 日收益（label），与分层/IC 口径一致
    handler_kwargs["label_horizon"] = getattr(req, "label_horizon", None) or 2
    return {
        "class": "DatasetH",
        "module_path": "qlib.data.dataset",
        "kwargs": {
            "handler": {
                "class": handler_cls,
                "module_path": handler_module,
                "kwargs": handler_kwargs,
            },
            "segments": {
                "train": [train_start, train_end],
                "valid": [train_start, train_end],
                # 预测窗口：预热时从 predict_start（更早）起，回测窗口仍以 test_start 为准
                "test": [predict_start or test_start, test_end],
            },
        },
    }


def _build_port_config(req: BacktestRequest, benchmark: str, start_time: str, end_time: str,
                        account=100000000, instruments: Optional[list] = None,
                        rebalance_base: Optional[int] = None) -> dict:
    """构造 PortAnaRecord 回测配置（含交易成本、成交量限制、涨跌停等）。

    account: 初始账户。qlib 原生支持 float（纯现金）或 dict
    （{"cash": 现金, 股票代码: {"amount": 股数, "price": 价格}}，用于段间持仓跨段传递）。
    rebalance_base: 回测起点在 qlib 全局交易日历中的索引，用于策略按"全局交易日序号"确定调仓日
    （跨滚动段连续，与段长无关）。None 时策略回退旧逻辑（段内相对 step）。
    """
    # 成交量限制：None=不限量理想成交；传入比例则限制单笔成交不超过"当日成交量 * 比例"
    volume_threshold = None
    if req.volume_threshold is not None:
        volume_threshold = {"all": ("current", "%s * $volume" % float(req.volume_threshold))}

    # 基准兜底：若指定 benchmark 在该时间段无数据，回退到第一个成分股或取消基准
    benchmark = _fallback_benchmark(benchmark, start_time, end_time, instruments)

    # 涨跌停限制：qlib 全局阈值不区分板块，主板约 10% / 创业板、科创板约 20% / 北交所约 30%，
    # 使用自定义 BoardAwareExchange 按股票代码前缀区分（用户传 None 时维持不限）。
    # 注意 get_exchange 的自定义分支只使用 exchange dict 内的 kwargs，因此完整参数需打包进 exchange 配置。
    # 成交价基准：
    #  - close/open/vwap：qlib 原生支持（vwap 的 buy_price="$vwap" 会自动进入查询字段）
    #  - avg_co/avg_ohlc：复合均价，qlib deal_price 只认 quote 中的字段名，需订阅
    #    $open/$high/$low 并由 BoardAwareExchange 注入 $avg_co / $avg_ohlc 列（avg_mode 参数）。
    deal_price = req.deal_price
    avg_mode: Optional[str] = None
    subscribe_fields: List[str] = []
    if deal_price in ("avg_co", "avg_ohlc"):
        avg_mode = deal_price
        deal_price = "close"  # 注入列由 avg_mode 指定，deal_price 只需保证初始化不报错
        subscribe_fields = ["$open", "$high", "$low"]
    # 复权需订阅 $factor：qlib quote 价格列原生为后复权价（=真实价×$factor），
    # BoardAwareExchange 靠 factor 还原真实价 / 做前复权归一（见 adjust.py docstring）
    subscribe_fields = subscribe_fields + ["$factor"] if "$factor" not in subscribe_fields else subscribe_fields

    exchange_kwargs = {
        "freq": "day",
        "start_time": start_time,
        "end_time": end_time,
        "limit_threshold": req.limit_threshold,
        "deal_price": deal_price,
        "open_cost": req.open_cost,
        "close_cost": req.close_cost,
        "min_cost": req.min_cost,
        "impact_cost": req.impact_cost,
        "volume_threshold": volume_threshold,
        "trade_unit": req.trade_unit,
        "subscribe_fields": subscribe_fields,
        # 复权方式：none/forward/backward（BoardAwareExchange 在 quote 层调整价格）
        "price_adjust": getattr(req, "price_adjust", "none") or "none",
    }
    if avg_mode:
        exchange_kwargs["avg_mode"] = avg_mode

    return {
        "executor": {
            "class": "SimulatorExecutor",
            "module_path": "qlib.backtest.executor",
            "kwargs": {
                # 每天一个时间步；调仓频率由自定义策略 PeriodicTopKStrategy 内部控制
                "time_per_step": "day",
                "generate_portfolio_metrics": True,
            },
        },
        "strategy": {
            "class": "PeriodicTopKStrategy",
            "module_path": "app.engine.periodic_strategy",
            "kwargs": {
                "signal": "<PRED>",
                "topk": req.topk,
                "n_days_hold": req.n_days_hold,
                "only_tradable": False,
                # 调仓日按全局交易日序号判断（跨滚动段连续），None 时回退段内相对 step
                "rebalance_base": rebalance_base,
            },
        },
        "backtest": {
            "start_time": start_time,
            "end_time": end_time,
            "account": account,
            "benchmark": benchmark,
            "exchange_kwargs": {
                "exchange": {
                    "class": "BoardAwareExchange",
                    "module_path": "app.engine.board_exchange",
                    "kwargs": exchange_kwargs,
                },
            },
        },
    }


# 分层回测与 IC 计算已拆分到 .analysis 模块（见文件顶部 from .analysis import ...）


def _run_single(req: BacktestRequest, instruments: list, benchmark: str) -> BacktestResult:
    """一次性训练回测：用回测前 train 窗口训练，整个回测区间测试。"""
    import qlib
    from qlib.utils import init_instance_by_config, flatten_dict
    from qlib.workflow import R
    from qlib.workflow.record_temp import SignalRecord, PortAnaRecord

    # 训练窗口：默认回测开始前 380 天（约 1 年），可用 train_win/train_unit 覆盖
    train_window_days = 380
    if req.train_win:
        train_window_days = {
            "day": req.train_win,
            "week": req.train_win * 7,
            "month": req.train_win * 30,
        }.get((req.train_unit or "month").lower(), 380)
    data_start = _offset_date(req.start_date, -train_window_days)
    train_end = _offset_date(req.start_date, -1)

    dataset_config = _build_dataset(req, instruments, (data_start, train_end), (req.start_date, req.end_date))
    dataset = init_instance_by_config(dataset_config)
    model = init_instance_by_config(_model_config(req.model, req))

    # 判断是否复用已训练好的模型权重（跳过训练）
    load_from = getattr(req, "load_model_task_id", None)
    if load_from:
        _report(40, "复用模型权重（task %s）..." % load_from)
        model = _load_model_object(load_from, req.model)
        if model is None:
            if _task_has_segment_models(load_from):
                raise ValueError(
                    "源任务（task %s）是【滚动训练】任务，模型按段保存（segment_N）。\n"
                    "single 一次性训练模式无法复用它的权重（不知道用哪一段的模型）。\n"
                    "处理：把训练/测试划分改回【滚动训练】再复用；"
                    "或取消复用模型权重改为新训练。" % load_from
                )
            raise ValueError("未找到可复用的模型权重（task %s）" % load_from)
        # 方案B：校验特征顺序一致性，防止因子库顺序变化导致静默错位
        _verify_reuse_feature_order(_dataset_feature_names(dataset), load_from)

    _report(40, "开始训练与预测...")
    exp_name = "backtest_web"
    with R.start(experiment_name=exp_name):
        R.log_params(**flatten_dict({"model": req.model, "topk": req.topk,
                                     "mode": "single", "reuse": bool(load_from)}))
        if not load_from:
            _report(50, "训练模型...")
            _check_cancel()  # 训练前检查
            model.fit(dataset)
        else:
            _report(50, "已加载模型权重，跳过训练...")
        recorder = R.get_recorder()
        # 保存模型可复现交付物（公式/权重/超参数/模型文件）及模型对象
        _save_model_artifacts(recorder, _extract_model_artifacts(model, dataset, req))
        _save_model_object(model, _get_artifact_dir())

        _report(65, "生成预测信号...")
        _check_cancel()  # 预测前检查
        sr = SignalRecord(model, dataset, recorder)
        sr.generate()

        # 分层回测 + IC 分析（single 模式只有一段，段标签=段1）
        _report(68, "计算分层回测与 IC 分析...")
        _check_cancel()
        analysis = _compute_analysis(model, dataset, instruments, "段1", benchmark=benchmark,
                                     label_horizon=req.label_horizon,
                                     rebalance_period=getattr(req, "layer_rebalance", None) or 1)

        _report(75, "执行回测(PortAnaRecord)...")
        _check_cancel()  # 回测前检查
        # 一次性模式：调仓日也用"全局交易日序号"判断（回测起点即为全局基准，
        # global_step - rebalance_base == trade_step，与旧逻辑完全一致），保持统一。
        from qlib.data.data import Cal
        try:
            _, _, single_base, _ = Cal.locate_index(req.start_date, req.start_date, freq="day", future=True)
        except Exception:
            single_base = None
        port_analysis_config = _build_port_config(req, benchmark, req.start_date, req.end_date,
                                                  account=req.initial_capital, instruments=instruments,
                                                  rebalance_base=single_base)
        par = PortAnaRecord(recorder, port_analysis_config, "day")
        par.generate()

        _report(90, "提取回测结果...")
        result = _extract_result(par, recorder, initial_account=req.initial_capital)
        result.trades = _extract_trades(par, recorder)

    # 写入分层与 IC 分析结果（single：段1 既是自身也是汇总）
    if analysis:
        layers_seg = analysis.get("layers")
        if layers_seg:
            result.layer_returns = {"segments": [layers_seg], "merged": layers_seg}
        ic_train, ic_test = analysis.get("ic_train"), analysis.get("ic_test")
        if ic_train or ic_test:
            result.ic_analysis = {
                "train": [{"segment": "段1", **ic_train}] if ic_train else [],
                "test": [{"segment": "段1", **ic_test}] if ic_test else [],
                "merged_test": ic_test,
                "merged_train": ic_train,
            }

    return result


# ---------------------------------------------------------------------------
# 滚动回测：段结果持久化 / 断点续跑 / 中途 partial 结果
# 每段跑完把该段净值/调仓/分层/IC 存到 segment_N/seg_result.json（+test_pl.pkl），
# 并把"已跑段的全局汇总"写到任务目录 partial_result.json，供前端中途查看；
# 重新提交相同参数时，检测到已完成段则直接加载跳过（断点续跑，不重复计算）。
# ---------------------------------------------------------------------------


def _account_total_value(account) -> Optional[float]:
    """计算账户总值：float=纯现金；dict=现金 + Σ(股票股数×价格)。"""
    if isinstance(account, (int, float)):
        return float(account)
    if isinstance(account, dict):
        cash = float(account.get("cash") or 0.0)
        mv = 0.0
        for k, v in account.items():
            if k == "cash" or not isinstance(v, dict):
                continue
            amt = v.get("amount")
            price = v.get("price")
            if amt is not None and price is not None:
                mv += float(amt) * float(price)
        return cash + mv
    return None


def _seg_result_path(art_dir, seg_no):
    return os.path.join(art_dir, "segment_%s" % seg_no, "seg_result.json")


def _save_segment_result(art_dir, seg_no, nav_points, trades, total_return, end_account,
                         bench_end, analysis, test_start, test_end,
                         end_position=None):
    """保存某段完整结果（断点续跑 + 中途 partial 用）。失败不阻塞回测。

    end_position: 该段末持仓（qlib account dict，含现金+股票），供续跑/下一段做初始账户，
    实现滚动回测"持仓跨段传递"（修复只买不卖/免费清仓 BUG）。
    """
    import json as _json
    import pickle
    if not art_dir:
        return
    try:
        seg_dir = os.path.join(art_dir, "segment_%s" % seg_no)
        os.makedirs(seg_dir, exist_ok=True)
        data = {
            "seg_no": seg_no,
            "date_range": [str(test_start), str(test_end)],
            "nav": nav_points,          # 绝对净值点（已乘该段起始 global_nav）
            "trades": trades,
            "total_return": total_return,
            "end_account": end_account,  # 该段末账户总值（供恢复 global_nav）
            "bench_end": bench_end,      # 该段末基准相对值（供恢复 global_bench）
            "end_position": end_position,  # 该段末持仓（跨段传递用）
            "layers": analysis.get("layers") if analysis else None,
            "ic_train": analysis.get("ic_train") if analysis else None,
            "ic_test": analysis.get("ic_test") if analysis else None,
        }
        with open(_seg_result_path(art_dir, seg_no), "w", encoding="utf-8") as f:
            _json.dump(data, f, ensure_ascii=False, default=str)
        tpl = analysis.get("test_pl") if analysis else None
        if tpl is not None:
            with open(os.path.join(seg_dir, "test_pl.pkl"), "wb") as f:
                pickle.dump(tpl, f, protocol=4)
        trpl = analysis.get("train_pl") if analysis else None
        if trpl is not None:
            with open(os.path.join(seg_dir, "train_pl.pkl"), "wb") as f:
                pickle.dump(trpl, f, protocol=4)
    except Exception:
        # 段结果保存失败不阻塞回测（仅影响续跑/中途查看）
        pass


def _load_segment_result(art_dir, seg_no):
    """加载已完成段的结果。返回 (data, test_pl, train_pl) 或 None。"""
    import json as _json
    import pickle
    if not art_dir:
        return None
    path = _seg_result_path(art_dir, seg_no)
    if not os.path.exists(path):
        return None
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = _json.load(f)
        test_pl = None
        p = os.path.join(os.path.dirname(path), "test_pl.pkl")
        if os.path.exists(p):
            with open(p, "rb") as f:
                test_pl = pickle.load(f)
        train_pl = None
        p = os.path.join(os.path.dirname(path), "train_pl.pkl")
        if os.path.exists(p):
            with open(p, "rb") as f:
                train_pl = pickle.load(f)
        return data, test_pl, train_pl
    except Exception:
        return None


def _save_partial_result(art_dir, segments_done, segments_total, all_nav, layer_segments,
                         ic_train_list, ic_test_list, merged_layers, merged_test, merged_train,
                         target_end_date: Optional[str] = None):
    """把"已跑段"的全局汇总写到 partial_result.json（前端轮询展示）。

    target_end_date: 回测参数设定的结束日期。写入后，前端可在"未跑完"的收益曲线上把
    X 轴右界延伸到该日期（右侧留白 = 尚未跑到的区间），直观显示进度。
    """
    import json as _json
    if not art_dir:
        return
    try:
        partial = {
            "segments_done": segments_done,
            "segments_total": segments_total,
            "nav": all_nav,
            "end_date": str(target_end_date) if target_end_date else None,
            "layer_returns": {"segments": layer_segments, "merged": merged_layers},
            "ic_analysis": {
                "train": ic_train_list,
                "test": ic_test_list,
                "merged_test": merged_test,
                "merged_train": merged_train,
            },
        }
        path = os.path.join(art_dir, "partial_result.json")
        with open(path, "w", encoding="utf-8") as f:
            _json.dump(partial, f, ensure_ascii=False, default=str)
    except Exception:
        pass


def _build_merged_analysis(all_test_pred_label, all_train_pred_label, layer_segments,
                           benchmark, rebalance_period):
    """合并各段的分层/IC 汇总（merged_layers / merged_test / merged_train）。

    滚动各段日期区间可能重叠，concat 后会产生重复 (instrument, datetime) 行，
    导致算法A分层在 reindex 时抛 "duplicate labels"，先去重再算汇总。
    供任务结束时与"中途 partial_result"共用同一逻辑。
    """
    import pandas as pd
    merged_layers = None
    if layer_segments and all_test_pred_label:
        try:
            merged_pl = pd.concat(all_test_pred_label)
            merged_pl = merged_pl[~merged_pl.index.duplicated(keep="first")]
            merged_bench = None
            if benchmark:
                try:
                    b_start = merged_pl.index.get_level_values("datetime").min()
                    b_end = merged_pl.index.get_level_values("datetime").max()
                    merged_bench = _compute_benchmark_returns(benchmark, b_start, b_end)
                except Exception:
                    merged_bench = None
            merged_groups = _compute_layers(merged_pl, benchmark_ret=merged_bench,
                                            rebalance_period=rebalance_period)
            if merged_groups:
                merged_layers = {"segment": "汇总", "groups": merged_groups, "benchmark": benchmark}
        except Exception:
            merged_layers = None
    merged_test = None
    if all_test_pred_label:
        try:
            merged_pl = pd.concat(all_test_pred_label)
            merged_pl = merged_pl[~merged_pl.index.duplicated(keep="first")]
            merged_test = _compute_ic(merged_pl)
        except Exception:
            merged_test = None
    merged_train = None
    if all_train_pred_label:
        try:
            merged_pl = pd.concat(all_train_pred_label)
            merged_pl = merged_pl[~merged_pl.index.duplicated(keep="first")]
            merged_train = _compute_ic(merged_pl)
        except Exception:
            merged_train = None
    return merged_layers, merged_test, merged_train


def _first_segment_warm_start(train_start: str, test_start: str, n_days_hold: int) -> Optional[str]:
    """计算滚动回测首段的预热预测起点：test_start 之前的 n_days_hold 个交易日。

    回测首日恰是全局调仓网格第 0 天，该日调仓需要 T-1 的信号（shift=1，防前视），
    而预测若从 test_start 才开始，首日就无 T-1 信号可用 → 首段开头空仓约一个持仓
    周期（净值出现平段）。把预测起点前移 n_days_hold 个交易日即可让首日正常建仓。
    返回最早预热日 YYYY-MM-DD；区间不足/异常时返回 None（不预热，保持原行为）。
    """
    from qlib.data import D
    try:
        cals = D.calendar(start_time=train_start, end_time=test_start)
        pre = [str(d)[:10] for d in cals if str(d)[:10] < str(test_start)[:10]]
        if not pre:
            return None
        return pre[-min(int(n_days_hold or 1), len(pre))]
    except Exception:
        return None


def _extend_all_nav(all_nav: list, seg_nav_points: list) -> None:
    """把一段的绝对净值点拼到全局净值曲线，重复日期跳过（保留先出现的点）。

    滚动测试窗口按自然月叠加（_add_period 加月返回下月同一天）时，相邻两段会重叠
    1 个交易日（如段 N 的尾日 = 段 N+1 的首日都是 09-01），导致全局净值出现重复
    日期、同一天被描两次。这里按日期去重，兼容"从缓存加载"与"正常执行"两条路径。
    """
    existing = {p.get("date") for p in all_nav}
    for p in seg_nav_points:
        d = p.get("date")
        if d in existing:
            continue
        existing.add(d)
        all_nav.append(p)


def _run_rolling(req: BacktestRequest, instruments: list, benchmark: str) -> BacktestResult:
    """自定义滚动训练回测：按训练/测试窗口切分，每段用截至该段的训练数据重训，再预测测试段回测。

    各段用相同的起始账户独立回测，得到每段的累计收益率；再用收益率累乘得到整体净值曲线，
    从而保证净值连续（每段期初 = 上一段期末）。
    """
    import qlib
    from qlib.utils import init_instance_by_config, flatten_dict
    from qlib.workflow import R
    from qlib.workflow.record_temp import SignalRecord, PortAnaRecord

    # 用交易日历生成滚动分段
    from qlib.data import D
    cal = D.calendar(start_time=req.start_date, end_time=req.end_date)
    if len(cal) == 0:
        raise ValueError("回测区间内没有交易日，请检查日期范围")

    segments = _gen_rolling_segments(req, cal)
    total = len(segments)
    _report(15, "滚动训练共 %d 段" % total)

    # 汇总结果（连续账户方案：下一段期初 = 上一段期末，账户资金自然延续）
    all_nav = []          # 全局净值曲线（相对初始账户 1.0 的累计）
    all_trades = []       # 全部调仓记录
    seg_results = []      # 每段的独立结果
    # 分层回测 / IC 分析收集器
    layer_segments = []   # 每段分层数据 {segment, groups}
    ic_train_list = []    # 每段训练集 IC
    ic_test_list = []     # 每段测试集 IC
    all_test_pred_label = []  # 各段 test 预测+label（用于汇总合成）
    all_train_pred_label = []  # 各段 train 预测+label（用于训练集 IC 汇总）
    exp_name = "backtest_web_rolling"
    base_account = req.initial_capital if req.initial_capital else 100000000.0
    carry_account = base_account   # 当前账户总值（连续传递，float=纯现金）
    carry_position = None          # 上一段末持仓（qlib account dict，含现金+股票），供段间跨段传递
    global_nav = 1.0      # 段起始的全局净值
    global_bench = 1.0    # 段起始的基准累计净值

    # 调仓日基准：回测起点在 qlib 全局交易日历中的索引。
    # 滚动各段用"全局交易日序号"判断调仓日 → 调仓节奏跨段连续（与段长无关），
    # 避免"段长 <= n_days_hold 时每段只买不卖、段末免费清仓"的收益虚高 BUG。
    from qlib.data.data import Cal
    try:
        _, _, rebalance_base, _ = Cal.locate_index(req.start_date, req.start_date, freq="day", future=True)
    except Exception:
        rebalance_base = None

    for idx, seg in enumerate(segments):
        seg_no = seg["seq"]
        train_start, train_end = seg["train"]
        test_start, test_end = seg["test"]
        # 每段开头检查取消（滚动训练停止的快速响应点）
        _check_cancel()

        # 断点续跑：该段已完成（seg_result.json 存在）则恢复结果并跳过，不重复计算
        art_dir = _get_artifact_dir()
        loaded = _load_segment_result(art_dir, seg_no)
        # 防误跳：缓存段的日期窗口必须与当前段完全一致才可复用。
        # 否则（如参数被误带 resume_task_id）会把他人的段结果当作本段缓存 → 秒完成 + 结果造假。
        if loaded is not None:
            _cached_range = loaded[0].get("date_range") or []
            if _cached_range != [str(test_start), str(test_end)]:
                loaded = None
        if loaded is not None:
            data, tpl, trpl = loaded
            _extend_all_nav(all_nav, data.get("nav") or [])
            all_trades.extend(data.get("trades") or [])
            seg_results.append(BacktestResult(
                total_return=data.get("total_return"),
                nav=data.get("nav") or [],
            ))
            if data.get("layers"):
                layer_segments.append(data["layers"])
            if data.get("ic_train"):
                ic_train_list.append({"segment": "段%d" % seg_no, **data["ic_train"]})
            if data.get("ic_test"):
                ic_test_list.append({"segment": "段%d" % seg_no, **data["ic_test"]})
            if tpl is not None and len(tpl):
                all_test_pred_label.append(tpl)
            if trpl is not None and len(trpl):
                all_train_pred_label.append(trpl)
            # 复用段：用已保存的段末绝对净值更新 global_nav。
            # 不用 end_account/base_account：report_normal 的 account 列在部分日数据异常时
            # 与净值曲线不一致，会造成段间净值断层（如 2019-07、2023-07 的 -27%/-87% 跳变）。
            _cached_nav = data.get("nav") or []
            if _cached_nav and _cached_nav[-1].get("value"):
                global_nav = _cached_nav[-1]["value"]
                carry_account = global_nav * base_account
            # 恢复段末持仓（跨段传递）：续跑时下一段以此持仓作为初始账户
            if data.get("end_position"):
                carry_position = data["end_position"]
            if data.get("bench_end") is not None:
                global_bench = global_bench * data["bench_end"]
            _report(20 + int(70 * (idx + 1) / total), "段%d/%d: 已完成，跳过（缓存 %s~%s）" % (
                seg_no, total, test_start, test_end))
            continue

        _report(20 + int(70 * (idx + 1) / total), "段%d/%d: 训练 %s~%s, 测试 %s~%s" % (
            seg_no, total, train_start, train_end, test_start, test_end))

        # 首段预热：把预测起点前移 n_days_hold 个交易日（回测窗口不变）。
        # 回测首日恰是全局调仓网格第 0 天，需要 T-1 信号，若预测从段首才开始则首日无信号
        # → 首段开头空仓一个持仓周期（净值平段）。预热后首日即可正常建仓。
        warm_start = None
        if seg_no == 1:
            warm_start = _first_segment_warm_start(
                train_start, test_start, max(1, int(req.n_days_hold or 1)))
        dataset_config = _build_dataset(req, instruments, (train_start, train_end), (test_start, test_end),
                                        predict_start=warm_start)
        dataset = init_instance_by_config(dataset_config)

        # 复用模式：加载对应段的模型权重，跳过训练
        load_from = getattr(req, "load_model_task_id", None)
        reuse_model = False
        model = None
        if load_from:
            model = _load_model_object(load_from, req.model, seg_no=seg_no)
            if model is not None:
                # 方案B：校验特征顺序一致性，防止因子库顺序变化导致静默错位
                _verify_reuse_feature_order(_dataset_feature_names(dataset), load_from, seg_no=seg_no)
                reuse_model = True
        if model is None:
            model = init_instance_by_config(_model_config(req.model, req))

        with R.start(experiment_name=exp_name):
            R.log_params(**flatten_dict({"model": req.model, "topk": req.topk, "mode": "rolling",
                                         "seg": seg_no, "reuse": reuse_model}))
            _check_cancel()  # 段内训练前检查
            if not reuse_model:
                model.fit(dataset)
            recorder = R.get_recorder()
            # 保存该段模型的可复现交付物
            _save_model_artifacts(recorder, _extract_model_artifacts(
                model, dataset, req, seg_label="seg%d" % seg_no))
            # 保存该段模型对象（供后续复用/对照）
            art_dir = _get_artifact_dir()
            if art_dir:
                _save_model_object(model, os.path.join(art_dir, "segment_%s" % seg_no))
            _check_cancel()  # 预测前检查
            sr = SignalRecord(model, dataset, recorder)
            sr.generate()

            # 分层回测 + IC 分析（段标签从"段1"开始）
            _report(25 + int(60 * (idx + 1) / total), "段%d/%d: 计算分层与IC..." % (seg_no, total))
            _check_cancel()
            seg_label = "段%d" % seg_no
            analysis = _compute_analysis(model, dataset, instruments, seg_label, benchmark=benchmark,
                                         label_horizon=req.label_horizon,
                                         rebalance_period=getattr(req, "layer_rebalance", None) or 1,
                                         # IC/分层只统计回测窗口内的点，裁剪掉预热期预测
                                         clip_start=str(test_start) if warm_start else None)
            if analysis:
                layers = analysis.get("layers")
                if layers:
                    layer_segments.append(layers)
                if analysis.get("ic_train"):
                    ic_train_list.append({"segment": seg_label, **analysis["ic_train"]})
                if analysis.get("ic_test"):
                    ic_test_list.append({"segment": seg_label, **analysis["ic_test"]})
                # 复用 _compute_analysis 已算好的 test 预测+label，避免重复 predict
                tpl = analysis.get("test_pl")
                if tpl is not None and len(tpl):
                    all_test_pred_label.append(tpl)
                # 训练集预测+label 也收集，用于训练集 IC 汇总
                trpl = analysis.get("train_pl")
                if trpl is not None and len(trpl):
                    all_train_pred_label.append(trpl)

            _check_cancel()  # 回测前检查
            # 段间持仓跨段传递：本段初始账户 = 上一段末持仓（若存在），否则纯现金。
            # qlib 原生支持 account 传 dict（{"cash": 现金, 股票: {"amount","price"}}），不动内核。
            # 这样段首调仓时 current_stock_list 非空 → 能卖出不在新 topk 的旧持仓，
            # 修复"段长<=n_days_hold 时只买不卖、段末免费清仓"的收益虚高 BUG。
            seg_account = carry_position if carry_position else carry_account
            seg_initial_value = _account_total_value(seg_account)
            port_analysis_config = _build_port_config(req, benchmark, test_start, test_end,
                                                       account=seg_account, instruments=instruments,
                                                       rebalance_base=rebalance_base)
            par = PortAnaRecord(recorder, port_analysis_config, "day")
            par.generate()

            # 传入段初账户总值：用 account 列计算段内收益，绕开 qlib return 列
            # 在带初始持仓时把"持仓市值误算为收益"的问题（否则段收益虚高爆炸）。
            seg_result = _extract_result(par, recorder, initial_account=seg_initial_value)
            seg_trades = _extract_trades(par, recorder)
            end_account = _get_segment_end_account(par, recorder)
            # 提取段末持仓，作为下一段的初始账户（跨段传递）
            end_position = _extract_end_position(recorder)
            if end_position:
                carry_position = end_position

            # 为该段单独生成曲线+参数快照图（参数横排在下方）
            art_dir = _get_artifact_dir()
            if art_dir:
                seg_dir = os.path.join(art_dir, "segment_%s" % seg_no)
                os.makedirs(seg_dir, exist_ok=True)
                _save_curve_snapshot(seg_dir, req, seg_result)

        # 拼接净值：段内各点 = 段起始全局净值 * (该点相对段起始账户的净值)
        seg_nav = seg_result.nav or []
        # 段末基准净值（相对段起始）
        seg_bench_end = None
        for pt in reversed(seg_nav):
            if pt.get("benchmark") is not None:
                seg_bench_end = pt["benchmark"]
                break
        seg_nav_abs = []
        for pt in seg_nav:
            ap = {
                "date": pt["date"],
                "value": round(global_nav * pt["value"], 6),
                "benchmark": round(global_bench * pt["benchmark"], 6) if pt.get("benchmark") is not None else None,
            }
            seg_nav_abs.append(ap)
        # 按日期去重拼接：相邻段测试窗口按自然月叠加可能重叠 1 个交易日（如 09-01 出现两次）
        _extend_all_nav(all_nav, seg_nav_abs)
        all_trades.extend(seg_trades)
        seg_results.append(seg_result)

        # 更新连续账户与段起始全局净值
        # 不用 end_account（report_normal 的 account 列在部分日数据异常时与净值曲线不一致，
        # 直接用它会导致段间净值断层）；统一用段收益率累乘，与段内 nav 严格一致。
        seg_growth = 1 + (seg_result.total_return or 0.0)
        global_nav = global_nav * seg_growth
        carry_account = global_nav * base_account
        # 更新基准累计净值
        if seg_bench_end is not None:
            global_bench = global_bench * seg_bench_end

        # 保存该段结果（供断点续跑）并刷新 partial_result（供前端中途查看）
        _save_segment_result(art_dir, seg_no, seg_nav_abs, seg_trades, seg_result.total_return,
                             end_account, seg_bench_end, analysis, test_start, test_end,
                             end_position=end_position if end_position else None)
        merged_layers, merged_test, merged_train = _build_merged_analysis(
            all_test_pred_label, all_train_pred_label, layer_segments, benchmark,
            getattr(req, "layer_rebalance", None) or 1)
        _save_partial_result(art_dir, idx + 1, total, all_nav, layer_segments,
                             ic_train_list, ic_test_list, merged_layers, merged_test, merged_train,
                             target_end_date=req.end_date)

    # 汇总指标：基于拼接后的全局净值重新计算
    result = _aggregate_from_nav(all_nav, seg_results)
    result.trades = all_trades

    # 组装分层回测与 IC 分析（含汇总合成）
    _report(92, "合成分层与 IC 汇总曲线...")
    merged_layers, merged_test, merged_train = _build_merged_analysis(
        all_test_pred_label, all_train_pred_label, layer_segments, benchmark,
        getattr(req, "layer_rebalance", None) or 1)
    if layer_segments:
        result.layer_returns = {"segments": layer_segments, "merged": merged_layers}
    if ic_test_list or ic_train_list:
        result.ic_analysis = {
            "train": ic_train_list,
            "test": ic_test_list,
            "merged_test": merged_test,
            "merged_train": merged_train,
        }

    return result


# 指标/净值/调仓提取已拆分到 .metrics 模块（见文件顶部 from .metrics import ...）
# 模型产物/交付物/复用加载/参数与结果持久化已拆分到 .artifacts 模块
# 曲线/参数快照绘图已拆分到 .charts 模块


def _gen_rolling_segments(req: BacktestRequest, calendar) -> list:
    """生成滚动训练/测试的日期分段。

    每个元素: {"train": (start, end), "test": (start, end), "seq": 段序号}
    训练窗口 = 截止每段测试开始前 train 窗口；测试窗口 = 每段覆盖的 test 窗口。
    步长默认等于测试窗口；每次推进一个步长。
    """
    step_win = req.step_win if req.step_win else req.test_win
    step_unit = req.step_unit if req.step_unit else req.test_unit

    test_start = req.start_date
    test_end = req.end_date
    segments = []
    seq = 1  # 段标签从 1 开始（原为 0）
    cursor = test_start
    while True:
        # 测试段 = [cursor, cursor + test_win - 1unit]
        cur_test_end = _add_period(cursor, req.test_win, req.test_unit)
        if cur_test_end > test_end:  # 不要超出回测结束
            cur_test_end = test_end
        if cur_test_end < cursor:  # 防止异常（极短窗口）
            cur_test_end = cursor
        # 测试段必须包含至少一个交易日，否则跳过（纯节假日段无行情/基准，回测无意义）。
        # 典型：test 窗口整周落在春节/国庆假期（如 2025-01-28~02-04），
        # 会导致基准(SH000300)查询为空 → qlib 报 "benchmark does not exist" → 整个回测失败。
        c0, c1 = str(cursor), str(cur_test_end)
        has_trade_day = any(c0 <= str(d.date()) <= c1 for d in calendar)
        if not has_trade_day:
            # 跳过该段，按正常步长推进（不占段序号）
            if cur_test_end >= test_end:
                break
            next_cursor = _offset_date(cur_test_end, 1)
            step_end = _add_period(cursor, step_win, step_unit)
            if step_end <= test_end:
                cursor = _add_period(cursor, step_win, step_unit)
            else:
                cursor = next_cursor
            continue
        # 训练段 = [cursor - train_win, cursor - 1天]
        train_end = _offset_date(cursor, -1)
        train_start = _add_period(cursor, -req.train_win, req.train_unit)
        segments.append({
            "seq": seq,
            "train": (train_start, train_end),
            "test": (cursor, cur_test_end),
        })
        seq += 1
        # 推进：默认每次推进一个测试窗口（不重叠）；若指定步长则按步长推进（可重叠）
        if cur_test_end >= test_end:
            break
        next_cursor = _offset_date(cur_test_end, 1)
        step_end = _add_period(cursor, step_win, step_unit)
        if step_end <= test_end:
            cursor = _add_period(cursor, step_win, step_unit)
        else:
            cursor = next_cursor
    return segments


def _model_config(model_name: str, req: BacktestRequest) -> Dict[str, Any]:
    """构造模型配置。用户通过 req.model_params 覆盖超参，未填的键用 Qlib 默认值。

    说明：model_params 里键名需与 qlib 模型接受的参数一致（如 num_leaves/max_depth/
    min_child_samples/learning_rate/n_estimators 等）。前端表单用统一的字段名，这里做映射。
    """
    model_name = (model_name or "LightGBM").lower()
    user_params: Dict[str, Any] = dict(req.model_params or {})

    def _apply(kwargs: Dict[str, Any], key_map: Dict[str, str]) -> Dict[str, Any]:
        """把用户参数（前端字段名）映射为 qlib 参数名后覆盖默认值。"""
        for front_key, qlib_key in key_map.items():
            v = user_params.get(front_key)
            if v is not None and v != "":
                kwargs[qlib_key] = v
        return kwargs

    if "xgboost" in model_name:
        # XGBModel（qlib 的 xgboost 模型）默认不指定超参；这里给出常见默认供覆盖
        cfg = {
            "class": "XGBModel",
            "module_path": "qlib.contrib.model.xgboost",
            "kwargs": _apply({}, {
                "learning_rate": "learning_rate",
                "max_depth": "max_depth",
                "min_child_weight": "min_child_weight",
                "n_estimators": "n_estimators",
                "subsample": "subsample",
                "colsample_bytree": "colsample_bytree",
                "gamma": "gamma",
                "reg_alpha": "reg_alpha",
                "reg_lambda": "reg_lambda",
            }),
        }
        return cfg
    if "linear" in model_name:
        return {
            "class": "LinearModel",
            "module_path": "qlib.contrib.model.linear",
            "kwargs": {"fit_intercept": True},
        }
    # 默认 LightGBM（使用 qlib 标准 GBDT 超参）
    kwargs: Dict[str, Any] = {
        "loss": "mse",
        "colsample_bytree": 0.8879,
        "learning_rate": 0.0421,
        "subsample": 0.8789,
        "lambda_l1": 205.6999,
        "lambda_l2": 580.9768,
        "max_depth": 8,
        "num_leaves": 210,
        "num_threads": 0,
    }
    # 用户覆盖：min_child_samples 是前端/lightgbm 常用名；qlib LGBModel 内部会透传给 lightgbm
    for front_key, lgb_key in {
        "max_depth": "max_depth",
        "num_leaves": "num_leaves",
        "min_child_samples": "min_child_samples",
        "learning_rate": "learning_rate",
        "n_estimators": "n_estimators",
        "subsample": "subsample",
        "colsample_bytree": "colsample_bytree",
        "reg_alpha": "reg_alpha",
        "reg_lambda": "reg_lambda",
    }.items():
        v = user_params.get(front_key)
        if v is not None and v != "":
            kwargs[lgb_key] = v
    return {
        "class": "LGBModel",
        "module_path": "qlib.contrib.model.gbdt",
        "kwargs": kwargs,
    }
