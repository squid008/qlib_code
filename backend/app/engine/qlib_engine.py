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
)
from .charts import (
    _save_curve_snapshot,
)
from ..datasource.factory import get_data_source, list_data_sources
# 导入即注册自定义算子 + patch register_all_ops（必须在 qlib.init() 之前生效）
from ..factors.ops_ext import ensure_ops_registered as _ensure_ops_registered

warnings.filterwarnings("ignore")

# ---- qlib 全局初始化：进程内只执行一次（多线程并发时避免互相踩踏全局 C/D 状态）----
import threading as _threading

_qlib_init_lock = _threading.Lock()
_qlib_initialized = False


def _ensure_qlib_init(provider_uri):
    """线程安全的 qlib.init：只在第一次调用时真正初始化，后续线程直接跳过。

    背景：qlib.init() 会重置全局 C/D（config、数据模块）状态。若多个回测任务
    线程并发调用 qlib.init()，会互相覆盖导致 `KeyError('dataset_cache')`、
    `Please run qlib.init() first` 等错误。因此必须全局只 init 一次。
    """
    global _qlib_initialized
    if _qlib_initialized:
        return
    import qlib
    from qlib.constant import REG_CN
    with _qlib_init_lock:
        if not _qlib_initialized:
            # 通过官方 custom_ops 机制注册自定义算子：register_all_ops 会把它注册进
            # Operators，且 worker 进程通过 C.register_from_C(g_config) 时也会带上，
            # 解决多进程数据加载时 "operator is not registered"。
            from ..factors.ops_ext import _ALL_OPS as _custom_ops
            qlib.init(
                provider_uri=provider_uri,
                region=REG_CN,
                custom_ops=_custom_ops,
            )
            # 双保险：qlib.init 内部 reset Operators 后再次注册
            _ensure_ops_registered(force=True)
            _qlib_initialized = True


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
        art_dir = _make_artifact_dir(work_dir, task_id, req)
        set_artifact_dir(art_dir)
        # 保存完整回测参数快照（params.json + meta.json），供复现模式对照
        _save_backtest_params(art_dir, req)
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


def _build_dataset(req: BacktestRequest, instruments: list, train_seg, test_seg) -> dict:
    """构造 dataset 配置。
    train_seg: (start, end) 训练段；test_seg: (start, end) 测试段。
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
    }
    # 自定义公式因子（M2）：有公式时优先用 FormulaHandler，仅用公式生成的因子作为特征
    custom_formulas = getattr(req, "custom_formulas", None) or []
    if custom_formulas:
        handler_cls = "FormulaHandler"
        handler_module = "app.factors.handler"
        handler_kwargs["formulas"] = list(custom_formulas)
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
                    "test": [test_start, test_end],
                },
            },
        }

    if (req.feature or "Alpha158").lower() == "alpha360":
        handler_cls = "SelectedAlpha360"
        handler_module = "app.factors.handler"
    else:
        handler_cls = "SelectedAlpha158"
        handler_module = "app.factors.handler"
    # 支持自定义特征子集：选中了特征则传给 handler 的 fields
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
                "test": [test_start, test_end],
            },
        },
    }


def _build_port_config(req: BacktestRequest, benchmark: str, start_time: str, end_time: str,
                        account: float = 100000000, instruments: Optional[list] = None) -> dict:
    """构造 PortAnaRecord 回测配置（含交易成本、成交量限制、涨跌停等）。"""
    # 成交量限制：None=不限量理想成交；传入比例则限制单笔成交不超过"当日成交量 * 比例"
    volume_threshold = None
    if req.volume_threshold is not None:
        volume_threshold = {"all": ("current", "%s * $volume" % float(req.volume_threshold))}

    # 基准兜底：若指定 benchmark 在该时间段无数据，回退到第一个成分股或取消基准
    benchmark = _fallback_benchmark(benchmark, start_time, end_time, instruments)

    # 涨跌停限制：qlib 全局阈值不区分板块，主板约 10% / 创业板、科创板约 20% / 北交所约 30%，
    # 使用自定义 BoardAwareExchange 按股票代码前缀区分（用户传 None 时维持不限）。
    # 注意 get_exchange 的自定义分支只使用 exchange dict 内的 kwargs，因此完整参数需打包进 exchange 配置。
    exchange_kwargs = {
        "freq": "day",
        "start_time": start_time,
        "end_time": end_time,
        "limit_threshold": req.limit_threshold,
        "deal_price": req.deal_price,
        "open_cost": req.open_cost,
        "close_cost": req.close_cost,
        "min_cost": req.min_cost,
        "impact_cost": req.impact_cost,
        "volume_threshold": volume_threshold,
        "trade_unit": req.trade_unit,
    }

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
        port_analysis_config = _build_port_config(req, benchmark, req.start_date, req.end_date,
                                                  account=req.initial_capital, instruments=instruments)
        par = PortAnaRecord(recorder, port_analysis_config, "day")
        par.generate()

        _report(90, "提取回测结果...")
        result = _extract_result(par, recorder)
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
    carry_account = base_account   # 当前账户总值（连续传递）
    global_nav = 1.0      # 段起始的全局净值
    global_bench = 1.0    # 段起始的基准累计净值

    for idx, seg in enumerate(segments):
        seg_no = seg["seq"]
        train_start, train_end = seg["train"]
        test_start, test_end = seg["test"]
        # 每段开头检查取消（滚动训练停止的快速响应点）
        _check_cancel()
        _report(20 + int(70 * (idx + 1) / total), "段%d/%d: 训练 %s~%s, 测试 %s~%s" % (
            seg_no, total, train_start, train_end, test_start, test_end))

        dataset_config = _build_dataset(req, instruments, (train_start, train_end), (test_start, test_end))
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
                                         rebalance_period=getattr(req, "layer_rebalance", None) or 1)
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
            port_analysis_config = _build_port_config(req, benchmark, test_start, test_end,
                                                       account=carry_account, instruments=instruments)
            par = PortAnaRecord(recorder, port_analysis_config, "day")
            par.generate()

            seg_result = _extract_result(par, recorder)
            seg_trades = _extract_trades(par, recorder)
            end_account = _get_segment_end_account(par, recorder)

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
        for pt in seg_nav:
            all_nav.append({
                "date": pt["date"],
                "value": round(global_nav * pt["value"], 6),
                "benchmark": round(global_bench * pt["benchmark"], 6) if pt.get("benchmark") is not None else None,
            })
        all_trades.extend(seg_trades)
        seg_results.append(seg_result)

        # 更新连续账户与段起始全局净值
        if end_account is not None and end_account > 0:
            global_nav = end_account / base_account
            carry_account = end_account
        else:
            global_nav = global_nav * (1 + (seg_result.total_return or 0.0))
            carry_account = global_nav * base_account
        # 更新基准累计净值
        if seg_bench_end is not None:
            global_bench = global_bench * seg_bench_end

    # 汇总指标：基于拼接后的全局净值重新计算
    result = _aggregate_from_nav(all_nav, seg_results)
    result.trades = all_trades

    # 组装分层回测与 IC 分析（含汇总合成）
    _report(92, "合成分层与 IC 汇总曲线...")
    try:
        if layer_segments:
            merged_layers = None
            if all_test_pred_label:
                import pandas as pd
                try:
                    # 滚动各段日期区间可能重叠，concat 后会产生重复 (instrument, datetime) 行，
                    # 导致算法A分层在 reindex 时抛 "duplicate labels"，去重后再算汇总
                    merged_pl = pd.concat(all_test_pred_label)
                    merged_pl = merged_pl[~merged_pl.index.duplicated(keep="first")]
                    # 汇总也叠加基准线：用合并后全测试区间计算基准累计收益
                    merged_bench = None
                    if benchmark:
                        try:
                            b_start = merged_pl.index.get_level_values("datetime").min()
                            b_end = merged_pl.index.get_level_values("datetime").max()
                            merged_bench = _compute_benchmark_returns(benchmark, b_start, b_end)
                        except Exception:
                            merged_bench = None
                    merged_groups = _compute_layers(merged_pl, benchmark_ret=merged_bench,
                                                     rebalance_period=getattr(req, "layer_rebalance", None) or 1)
                    if merged_groups:
                        merged_layers = {"segment": "汇总", "groups": merged_groups, "benchmark": benchmark}
                except Exception:
                    merged_layers = None
            result.layer_returns = {"segments": layer_segments, "merged": merged_layers}

        if ic_test_list or ic_train_list:
            merged_test = None
            if all_test_pred_label:
                import pandas as pd
                try:
                    merged_pl = pd.concat(all_test_pred_label)
                    merged_pl = merged_pl[~merged_pl.index.duplicated(keep="first")]
                    merged_test = _compute_ic(merged_pl)
                except Exception:
                    merged_test = None
            merged_train = None
            if all_train_pred_label:
                import pandas as pd
                try:
                    merged_pl = pd.concat(all_train_pred_label)
                    merged_pl = merged_pl[~merged_pl.index.duplicated(keep="first")]
                    merged_train = _compute_ic(merged_pl)
                except Exception:
                    merged_train = None
            result.ic_analysis = {
                "train": ic_train_list,
                "test": ic_test_list,
                "merged_test": merged_test,
                "merged_train": merged_train,
            }
    except Exception:
        pass

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
