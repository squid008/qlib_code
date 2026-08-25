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

warnings.filterwarnings("ignore")

# 进度回调（由 task_manager 注入）
_progress_cb: Optional[Any] = None
_artifact_dir: Optional[str] = None
# 取消检查（由 task_manager 注入，返回 True 表示任务被取消）
_cancel_check: Optional[Any] = None


def set_progress_callback(cb):
    global _progress_cb
    _progress_cb = cb


def set_artifact_dir(path: Optional[str]):
    """设置当前任务的模型产物保存目录（按 task_id 组织）。"""
    global _artifact_dir
    _artifact_dir = path


def set_cancel_check(fn):
    """设置当前任务的取消检查函数（返回 True 表示任务被取消）。"""
    global _cancel_check
    _cancel_check = fn


def _check_cancel():
    """在关键检查点调用：若用户已点停止，抛 TaskCancelledError 让上层捕获并终止。"""
    if _cancel_check is not None:
        try:
            if _cancel_check():
                from .task_manager import TaskCancelledError
                raise TaskCancelledError("cancelled by user")
        except Exception as e:
            # 只传递取消异常，其他异常忽略
            if type(e).__name__ == "TaskCancelledError":
                raise
            # 非取消异常，吞掉以免误终止


def _report(p, msg):
    if _progress_cb is not None:
        try:
            _progress_cb(p, msg)
        except Exception:
            pass


def run_backtest(req: BacktestRequest, work_dir: Optional[str] = None,
                 task_id: Optional[str] = None) -> BacktestResult:
    import qlib
    from qlib.constant import REG_CN
    from qlib.data import D
    from qlib.utils import init_instance_by_config
    from qlib.workflow import R

    provider_uri = getattr(req, "data_source_provider_uri", None) or _default_qlib_uri()

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

    # 设置模型产物保存目录（可读目录名 + task_id 后缀保证唯一，用于复现/查看训练结果）
    global _artifact_dir
    if task_id and work_dir:
        _artifact_dir = _make_artifact_dir(work_dir, task_id, req)
        # 保存完整回测参数快照（params.json + meta.json），供复现模式对照
        _save_backtest_params(_artifact_dir, req)
    else:
        _artifact_dir = None

    # 解决 mlflow filesystem 后端进入维护模式的问题：
    # 1) 允许使用文件存储（作为兜底）
    os.environ.setdefault("MLFLOW_ALLOW_FILE_STORE", "true")

    _report(3, "初始化 Qlib...")
    qlib.init(provider_uri=provider_uri, region=REG_CN)

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
        instruments = [
            str(i).upper()
            for i in D.list_instruments(D.instruments(market=req.universe), as_list=True)
        ]
    # 限制股票数量，避免首次运行过慢
    if len(instruments) > 300:
        instruments = instruments[:300]

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
    if _artifact_dir:
        _save_curve_snapshot(_artifact_dir, req, result)
        _save_result_json(_artifact_dir, result)

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
    if (req.feature or "Alpha158").lower() == "alpha360":
        handler_cls = "SelectedAlpha360"
        handler_module = "app.factors.handler"
    else:
        handler_cls = "SelectedAlpha158"
        handler_module = "app.factors.handler"
    # 支持自定义特征子集：选中了特征则传给 handler 的 fields
    if req.selected_features:
        handler_kwargs["fields"] = list(req.selected_features)
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

    return {
        "executor": {
            "class": "SimulatorExecutor",
            "module_path": "qlib.backtest.executor",
            "kwargs": {
                "time_per_step": "day",
                "generate_portfolio_metrics": True,
            },
        },
        "strategy": {
            "class": "TopkDropoutStrategy",
            "module_path": "qlib.contrib.strategy.signal_strategy",
            "kwargs": {
                "signal": "<PRED>",
                "topk": req.topk,
                "n_drop": 1,
                "only_tradable": False,
            },
        },
        "backtest": {
            "start_time": start_time,
            "end_time": end_time,
            "account": account,
            "benchmark": benchmark,
            "exchange_kwargs": {
                "freq": "day",
                "limit_threshold": req.limit_threshold,
                "deal_price": req.deal_price,
                "open_cost": req.open_cost,
                "close_cost": req.close_cost,
                "min_cost": req.min_cost,
                "impact_cost": req.impact_cost,
                "volume_threshold": volume_threshold,
                "trade_unit": req.trade_unit,
            },
        },
    }


def _get_pred_label(model, dataset, instruments, segment: str):
    """获取某段（train/test）的预测分 + 未来收益 label，合并成一个 DataFrame。

    返回 None 表示该段没有可用的预测或 label。返回 DataFrame columns=['score','label']，
    index 为 MultiIndex=[instrument, datetime]。
    """
    import pandas as pd
    from qlib.data import D

    try:
        pred = model.predict(dataset, segment=segment)
    except Exception:
        pred = None
    if pred is None or len(pred) == 0:
        return None
    if isinstance(pred, pd.Series):
        pred = pred.to_frame("score")
    pred = pred[["score"]]

    try:
        start = pred.index.get_level_values("datetime").min()
        end = pred.index.get_level_values("datetime").max()
        label_df = D.features(
            instruments, ["Ref($close, -2) / Ref($close, -1) - 1"],
            start_time=start, end_time=end,
        )
        label_df.columns = ["label"]
        label_df.index.names = ["instrument", "datetime"]
        merged = pred.join(label_df, how="inner")
    except Exception:
        return None
    if merged is None or len(merged) == 0:
        return None
    return merged


def _compute_benchmark_returns(benchmark: str, start, end):
    """计算 benchmark 指数在 [start, end] 的累计收益曲线。

    口径与分层回测一致：单日收益 cumsum。返回 {date_str: cum_ret}；失败返回 {}。
    """
    import pandas as pd
    from qlib.data import D

    try:
        df = D.features(
            [benchmark],
            ["$close"],
            start_time=start,
            end_time=end,
        )
        if df is None or len(df) == 0:
            return {}
        close = df["$close"]
        # D.features 返回 MultiIndex=[instrument, datetime]，按 datetime 层做日收益累加
        s = close.droplevel("instrument").sort_index()
        ret = s.pct_change().fillna(0.0)
        cum = ret.cumsum()
        result = {}
        for dt, v in cum.items():
            result[pd.Timestamp(dt).strftime("%Y-%m-%d")] = round(float(v), 6)
        return result
    except Exception:
        return {}


def _compute_layers(pred_label, N: int = 5, benchmark_ret: Optional[dict] = None):
    """按预测分每日横截面均分 N 组，返回每组累计收益曲线数据（复刻 qlib _group_return，但返回数据）。

    benchmark_ret: {date_str: cum_ret}，可选的基准累计收益，用于在分层图上叠加基准线。
    返回 list[{date, Group1..GroupN, long_short, long_average, benchmark?}]；失败返回 None。
    """
    import pandas as pd

    if pred_label is None or len(pred_label) == 0:
        return None
    df = pred_label.copy()
    df = df.sort_values("score", ascending=False)
    df = df.dropna(subset=["score"])
    if df.empty:
        return None
    try:
        t_df = pd.DataFrame({
            "Group%d" % (i + 1): df.groupby(level="datetime", group_keys=False)["label"].apply(
                lambda x: x[len(x) // N * i: len(x) // N * (i + 1)].mean()  # noqa: B023
            )
            for i in range(N)
        })
        t_df["long_short"] = t_df["Group1"] - t_df["Group%d" % N]
        t_df["long_average"] = t_df["Group1"] - df.groupby(level="datetime", group_keys=False)["label"].mean()
        t_df = t_df.dropna(how="all")
        cum = t_df.cumsum()
    except Exception:
        return None
    points = []
    for dt, row in cum.iterrows():
        date_str = pd.Timestamp(dt).strftime("%Y-%m-%d")
        pt = {"date": date_str}
        for i in range(N):
            pt["Group%d" % (i + 1)] = round(float(row["Group%d" % (i + 1)]), 6)
        pt["long_short"] = round(float(row["long_short"]), 6)
        pt["long_average"] = round(float(row["long_average"]), 6)
        if benchmark_ret is not None:
            pt["benchmark"] = benchmark_ret.get(date_str)
        points.append(pt)
    return points


def _compute_ic(pred_label):
    """计算 IC / RankIC 时序 + 平均IC + ICIR。返回 dict 或 None。"""
    import pandas as pd

    if pred_label is None or len(pred_label) == 0:
        return None
    df = pred_label.copy()
    df = df.dropna(subset=["score", "label"])
    if df.empty:
        return None
    try:
        ic = df.groupby(level="datetime", group_keys=False).apply(
            lambda x: x["score"].corr(x["label"])
        ).dropna()
        ric = df.groupby(level="datetime", group_keys=False).apply(
            lambda x: x["score"].corr(x["label"], method="spearman")
        ).dropna()
    except Exception:
        return None
    if len(ic) == 0:
        return None
    points = []
    for d, v in ic.items():
        rv = ric.get(d, None)
        points.append({
            "date": pd.Timestamp(d).strftime("%Y-%m-%d"),
            "ic": round(float(v), 6),
            "rank_ic": round(float(rv), 6) if rv is not None and not pd.isna(rv) else None,
        })
    mean_ic = float(ic.mean())
    std = float(ic.std())
    icir = round(mean_ic / std, 6) if std and std > 0 else None
    mean_rank_ic = float(ric.mean()) if len(ric) else None
    rstd = float(ric.std()) if len(ric) else 0.0
    rank_icir = round(mean_rank_ic / rstd, 6) if mean_rank_ic is not None and rstd and rstd > 0 else None
    return {
        "points": points,
        "mean_ic": round(mean_ic, 6),
        "icir": icir,
        "mean_rank_ic": round(mean_rank_ic, 6) if mean_rank_ic is not None else None,
        "rank_icir": rank_icir,
    }


def _compute_analysis(model, dataset, instruments, seg_label: str, benchmark: Optional[str] = None):
    """计算某段的分析数据：分层回测（test 段）+ 训练/测试集 IC 曲线。

    benchmark: 基准指数代码（如 SH000300），用于在分层图上叠加基准累计收益线。
    返回 dict:
      { "layers": {segment, groups}, "ic_train": {...}, "ic_test": {...} }
    失败项为 None。
    """
    test_pl = _get_pred_label(model, dataset, instruments, "test")
    train_pl = _get_pred_label(model, dataset, instruments, "train")

    # 基准累计收益（与分层同日期区间）
    benchmark_ret = None
    if benchmark:
        try:
            start = test_pl.index.get_level_values("datetime").min()
            end = test_pl.index.get_level_values("datetime").max()
            benchmark_ret = _compute_benchmark_returns(benchmark, start, end)
        except Exception:
            benchmark_ret = None

    layers = None
    if test_pl is not None:
        layers = _compute_layers(test_pl, benchmark_ret=benchmark_ret)
        if layers:
            layers = {"segment": seg_label, "groups": layers, "benchmark": benchmark}

    ic_train = _compute_ic(train_pl)
    ic_test = _compute_ic(test_pl)

    return {"layers": layers, "ic_train": ic_train, "ic_test": ic_test}


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
            raise ValueError("未找到可复用的模型权重（task %s）" % load_from)

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
        _save_model_object(model, _artifact_dir)

        _report(65, "生成预测信号...")
        _check_cancel()  # 预测前检查
        sr = SignalRecord(model, dataset, recorder)
        sr.generate()

        # 分层回测 + IC 分析（single 模式只有一段，段标签=段1）
        _report(68, "计算分层回测与 IC 分析...")
        _check_cancel()
        analysis = _compute_analysis(model, dataset, instruments, "段1", benchmark=benchmark)

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
            if _artifact_dir:
                _save_model_object(model, os.path.join(_artifact_dir, "segment_%s" % seg_no))
            _check_cancel()  # 预测前检查
            sr = SignalRecord(model, dataset, recorder)
            sr.generate()

            # 分层回测 + IC 分析（段标签从"段1"开始）
            _report(25 + int(60 * (idx + 1) / total), "段%d/%d: 计算分层与IC..." % (seg_no, total))
            _check_cancel()
            seg_label = "段%d" % seg_no
            analysis = _compute_analysis(model, dataset, instruments, seg_label, benchmark=benchmark)
            if analysis:
                layers = analysis.get("layers")
                if layers:
                    layer_segments.append(layers)
                if analysis.get("ic_train"):
                    ic_train_list.append({"segment": seg_label, **analysis["ic_train"]})
                if analysis.get("ic_test"):
                    ic_test_list.append({"segment": seg_label, **analysis["ic_test"]})
                tpl = _get_pred_label(model, dataset, instruments, "test")
                if tpl is not None and len(tpl):
                    all_test_pred_label.append(tpl)

            _check_cancel()  # 回测前检查
            port_analysis_config = _build_port_config(req, benchmark, test_start, test_end,
                                                       account=carry_account, instruments=instruments)
            par = PortAnaRecord(recorder, port_analysis_config, "day")
            par.generate()

            seg_result = _extract_result(par, recorder)
            seg_trades = _extract_trades(par, recorder)
            end_account = _get_segment_end_account(par, recorder)

            # 为该段单独生成曲线+参数快照图（参数横排在下方）
            if _artifact_dir:
                seg_dir = os.path.join(_artifact_dir, "segment_%s" % seg_no)
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
                    merged_pl = pd.concat(all_test_pred_label)
                    merged_groups = _compute_layers(merged_pl)
                    if merged_groups:
                        merged_layers = {"segment": "汇总", "groups": merged_groups}
                except Exception:
                    merged_layers = None
            result.layer_returns = {"segments": layer_segments, "merged": merged_layers}

        if ic_test_list or ic_train_list:
            merged_test = None
            if all_test_pred_label:
                import pandas as pd
                try:
                    merged_pl = pd.concat(all_test_pred_label)
                    merged_test = _compute_ic(merged_pl)
                except Exception:
                    merged_test = None
            result.ic_analysis = {
                "train": ic_train_list,
                "test": ic_test_list,
                "merged_test": merged_test,
            }
    except Exception:
        pass

    return result


def _aggregate_from_nav(all_nav: list, seg_results: list) -> BacktestResult:
    """根据拼接的全局净值曲线与各段结果，汇总最终指标。"""
    import pandas as pd
    result = BacktestResult()
    if not all_nav:
        return result

    df = pd.DataFrame(all_nav)
    df["value"] = df["value"].astype(float)
    # 总收益
    result.total_return = float(df["value"].iloc[-1] - 1)
    # 年化
    n = len(df)
    years = n / 252.0
    if years > 0:
        result.annualized_return = float((1 + result.total_return) ** (1 / years) - 1)
    # 最大回撤
    running_max = df["value"].cummax()
    dd = (df["value"] / running_max - 1).min()
    result.max_drawdown = float(dd)
    # 日收益（用于夏普/胜率）
    ret = df["value"].pct_change().dropna()
    if len(ret) > 0 and ret.std() and ret.std() > 0:
        result.sharpe = float(ret.mean() / ret.std() * (252 ** 0.5))
    result.win_rate = float((ret > 0).mean()) if len(ret) else None

    # 基准收益（benchmark 已在拼接时按段累乘，直接取最后一个有效值）
    bench_values = [pt.get("benchmark") for pt in all_nav if pt.get("benchmark") is not None]
    if bench_values:
        result.benchmark_return = float(bench_values[-1] - 1)
    else:
        result.benchmark_return = None
    # 年化超额收益
    if result.annualized_return is not None and result.benchmark_return is not None and years > 0:
        bench_years = n / 252.0
        if bench_years > 0:
            result.annualized_excess_return = (
                result.annualized_return
                - (float((1 + result.benchmark_return) ** (1 / bench_years) - 1))
            )

    result.nav = all_nav[:2000]
    # 备注滚动段数（用 report_df 附带，避免改动模型字段）
    try:
        result.report_df = {"rolling_segments": len(seg_results)}
    except Exception:
        pass
    return result


def _extract_result(par, recorder) -> BacktestResult:
    """从 PortAnaRecord 的回测产物中提取风险指标与净值。"""
    result = BacktestResult()
    report = None

    # 方式一：通过 recorder.load_object 按 artifact 路径读取
    for key in ["portfolio_analysis/report_normal_1day.pkl", "report_normal_1day.pkl",
                "portfolio_analysis/report_normal_day.pkl"]:
        try:
            report = recorder.load_object(key)
            if report is not None:
                break
        except Exception:
            continue

    # 方式二：par.load
    if report is None:
        for key in ["report_normal_1day.pkl", "report_normal_day.pkl"]:
            try:
                report = par.load(key)
                if report is not None:
                    break
            except Exception:
                continue

    # 方式三：全局搜索 mlruns 下的报告文件
    if report is None:
        report = _find_report_fallback()

    if report is not None and hasattr(report, "columns"):
        import pandas as pd

        try:
            ret = report["return"] if "return" in report.columns else None
            bench = report["bench"] if "bench" in report.columns else None

            if ret is not None:
                ret = ret.astype(float)
                cum = (1 + ret).cumprod()
                result.total_return = float(cum.iloc[-1] - 1) if len(cum) else None
                years = len(ret) / 252.0
                if years > 0 and result.total_return is not None:
                    result.annualized_return = float((1 + result.total_return) ** (1 / years) - 1)
                running_max = cum.cummax()
                dd = (cum / running_max - 1).min()
                result.max_drawdown = float(dd) if not pd.isna(dd) else None
                std = ret.std()
                if std and std > 0:
                    result.sharpe = float(ret.mean() / std * (252 ** 0.5))
                result.win_rate = float((ret > 0).mean()) if len(ret) else None

                if bench is not None:
                    bench = bench.astype(float)
                    bench_cum = (1 + bench).cumprod()
                    result.benchmark_return = float(bench_cum.iloc[-1] - 1) if len(bench_cum) else None
                    if result.benchmark_return is not None and result.total_return is not None \
                            and result.annualized_return is not None:
                        years_b = len(bench) / 252.0
                        if years_b > 0:
                            result.annualized_excess_return = (
                                result.annualized_return
                                - (float((1 + result.benchmark_return) ** (1 / years_b) - 1))
                            )

                # 净值曲线（index 可能为单层 datetime 或 MultiIndex）
                nav = []
                if isinstance(ret.index, pd.MultiIndex):
                    dates = [ts.strftime("%Y-%m-%d") for ts in ret.index.get_level_values("datetime")]
                else:
                    dates = [pd.Timestamp(d).strftime("%Y-%m-%d") if not isinstance(d, str) else str(d)[:10]
                             for d in ret.index]
                nav_values = (1 + ret).cumprod()
                bench_nav = (1 + bench).cumprod() if bench is not None else None
                for i, d in enumerate(dates):
                    pt = {"date": d, "value": round(float(nav_values.iloc[i]), 6)}
                    if bench_nav is not None:
                        pt["benchmark"] = round(float(bench_nav.iloc[i]), 6)
                    nav.append(pt)
                result.nav = nav[:800]
        except Exception as e:
            result.message = f"指标提取异常: {e}"
    else:
        result.message = "未找到回测报告(report_normal)"
    return result


def _find_report_fallback():
    """兜底：全局搜索 mlruns 目录下的 report_normal 文件并读取最新的。"""
    import glob
    import pickle
    try:
        from ..config import WORK_DIR
        base = WORK_DIR
    except Exception:
        base = "."
    patterns = [
        os.path.join(base, "mlruns", "*", "*", "artifacts", "portfolio_analysis", "report_normal*.pkl"),
        os.path.join(base, "..", "mlruns", "*", "*", "artifacts", "portfolio_analysis", "report_normal*.pkl"),
    ]
    files = []
    for p in patterns:
        files.extend(glob.glob(p))
    if not files:
        return None
    files.sort(key=lambda f: os.path.getmtime(f))
    try:
        with open(files[-1], "rb") as f:
            return pickle.load(f)
    except Exception:
        return None


def _extract_model_artifacts(model, dataset, req: BacktestRequest, seg_label: str = "") -> dict:
    """提取模型的可复现交付物（公式/权重/超参数/特征/模型文件）。

    返回 dict，其中可能包含：
      - model_info: 模型类型、训练配置摘要
      - feature_names: 使用的特征列表
      - linear: 线性模型的系数与截距（coef_/intercept_）—— 即公式
      - params: 模型超参数
      - model_file: 树模型的序列化文本（LightGBM/XGBoost，可用于加载复现）
    """
    import json

    artifacts = {
        "model_info": {
            "model": req.model,
            "feature": req.feature,
            "topk": req.topk,
            "segment": seg_label,
        },
        "feature_names": [],
        "params": {},
        "linear": None,
        "model_file": None,
        "feature_importance": None,
    }

    # 1) 特征列表（从 handler 取特征列名）
    try:
        handler = getattr(dataset, "handler", None)
        if handler is not None and hasattr(handler, "get_cols"):
            cols = handler.get_cols("feature")
            if cols:
                artifacts["feature_names"] = [str(c) for c in cols]
    except Exception:
        pass

    # 2) 线性模型：系数 + 截距 = 完整线性公式
    model_name = (req.model or "").lower()
    if "linear" in model_name:
        coef = getattr(model, "coef_", None)
        intercept = getattr(model, "intercept_", None)
        features = artifacts["feature_names"]
        if coef is not None:
            weights = [float(x) for x in coef]
            if features and len(weights) == len(features):
                artifacts["linear"] = {
                    "formula": "score = intercept + sum(w_i * feature_i)",
                    "intercept": float(intercept) if intercept is not None else 0.0,
                    "weights": weights,
                    "feature_weights": [{"feature": f, "weight": w} for f, w in zip(features, weights)],
                }
            else:
                artifacts["linear"] = {
                    "formula": "score = intercept + sum(w_i * feature_i)",
                    "intercept": float(intercept) if intercept is not None else 0.0,
                    "weights": weights,
                    "feature_weights": None,
                }

    # 3) 树模型（LightGBM/XGBoost）：保存模型文件 + 超参数
    elif "lightgbm" in model_name or "xgb" in model_name:
        try:
            # qlib LGBModel 用 self.model (Booster)，params 为 self.params
            booster = getattr(model, "model", None)
            params = getattr(model, "params", None)
            if booster is not None:
                # 优先用 model_to_string()（无需文件名，返回序列化文本）
                if hasattr(booster, "model_to_string"):
                    model_txt = booster.model_to_string(num_iteration=None)
                    artifacts["model_file"] = model_txt
                elif hasattr(booster, "save_model"):
                    import tempfile
                    tmp = tempfile.NamedTemporaryFile(suffix=".txt", delete=False, mode="w")
                    tmp.close()
                    try:
                        # LightGBM 支持 num_iteration；XGBoost 的 save_model 只接受 fname
                        booster.save_model(tmp.name, num_iteration=None)
                    except TypeError:
                        booster.save_model(tmp.name)
                    with open(tmp.name, "r", encoding="utf-8", errors="ignore") as f:
                        artifacts["model_file"] = f.read()
                    os.unlink(tmp.name)
                # 树数量
                try:
                    if hasattr(booster, "num_trees"):
                        artifacts["model_info"]["num_trees"] = int(booster.num_trees())
                except Exception:
                    pass
            if params:
                safe = {}
                for k, v in dict(params).items():
                    if isinstance(v, (str, int, float, bool)) or v is None:
                        safe[k] = v
                artifacts["params"] = safe
            # 树模型的“权重”→ 特征重要性（gain）。XGBModel 有 get_feature_importance；
            # LGBModel 用 booster.feature_importance()。
            artifacts["feature_importance"] = _extract_feature_importance(
                model, booster, feature_names=artifacts.get("feature_names")
            )
        except Exception:
            pass

    return artifacts


def _extract_feature_importance(model, booster, feature_names=None):
    """提取树模型的特征重要性（按值降序），返回 [{feature, importance}] 或 None。

    兼容 XGBoost（booster.get_score()，key 可能为 f0/f1 索引）与 LightGBM
    （booster.feature_importance()，返回 f0/f1 索引数组）。若 key 是 f<i> 形式，
    尝试映射到真实特征名（feature_names），否则保留 f<i>。
    """
    try:
        import numpy as np
        fi = None
        # 1) XGBModel 自带接口
        if hasattr(model, "get_feature_importance"):
            try:
                fi = model.get_feature_importance()
            except Exception:
                fi = None
        # 2) XGBoost Booster：get_score()
        if fi is None and booster is not None and hasattr(booster, "get_score"):
            try:
                fi = booster.get_score()
            except Exception:
                fi = None
        # 3) LightGBM Booster：feature_importance()
        if fi is None and booster is not None and hasattr(booster, "feature_importance"):
            try:
                fi = booster.feature_importance(importance_type="gain")
            except Exception:
                fi = None
        if fi is None:
            return None

        # 归一化为 [(key, value)]
        if hasattr(fi, "items"):
            items = [(str(k), float(v)) for k, v in fi.items()]
        elif isinstance(fi, (list, np.ndarray)):
            items = [("f%d" % i, float(v)) for i, v in enumerate(fi)]
        else:
            return None
        if not items:
            return None

        # 特征索引 key → 真实特征名映射。
        # 兼容多种格式：f<i>（旧 xgboost）、Column_N / column_N（新 xgboost / lightgbm 默认列名）、
        # 纯数字。索引 N 即特征在 DataFrame 中的位置下标。
        def _real_name(k):
            idx = None
            if k.startswith("f") and k[1:].isdigit():
                idx = int(k[1:])
            else:
                # Column_9 / column_9 / f_9 等，取最后一个下划线后的数字
                lower = k.lower()
                if ("column_" in lower or lower.startswith("f_")) and "_" in k:
                    tail = k.rsplit("_", 1)[-1]
                    if tail.isdigit():
                        idx = int(tail)
                elif k.isdigit():
                    idx = int(k)
            if idx is not None and feature_names and 0 <= idx < len(feature_names):
                return feature_names[idx]
            return k

        items = sorted(items, key=lambda t: t[1], reverse=True)
        return [{"feature": _real_name(k), "importance": round(v, 4)} for k, v in items]
    except Exception:
        return None


def _save_model_artifacts(recorder, artifacts: dict):
    """把模型交付物保存到 mlflow artifact 与本地文件，方便后续查看/复现。

    滚动训练时，各段写入主目录下的 segment_XX 子目录；single 模式写入主目录。
    """
    import json
    # 保存到 mlflow artifact
    try:
        recorder.save_objects(**{"model_artifacts.json": artifacts}, artifact_path="model_artifacts")
    except Exception:
        pass

    global _artifact_dir
    if not _artifact_dir:
        return
    try:
        # 确定保存子目录：滚动段(seg1/seg2/...)写入独立子目录，single 写入主目录
        seg = (artifacts.get("model_info") or {}).get("segment") or ""
        if seg:
            sub = os.path.join(_artifact_dir, "segment_%s" % str(seg).replace("seg", ""))
        else:
            sub = _artifact_dir
        os.makedirs(sub, exist_ok=True)

        # 交付物 JSON
        meta = {k: v for k, v in artifacts.items() if k != "model_file"}
        with open(os.path.join(sub, "model_artifacts.json"), "w", encoding="utf-8") as f:
            json.dump(meta, f, ensure_ascii=False, indent=2, default=str)
        # 树模型文件单独保存
        if artifacts.get("model_file"):
            with open(os.path.join(sub, "model.txt"), "w", encoding="utf-8") as f:
                f.write(artifacts["model_file"])
    except Exception:
        pass


def _save_model_object(model, dir_path):
    """把训练好的 qlib 模型对象保存为 pickle，供复用模式直接加载（跳过训练）。"""
    import pickle
    if not dir_path:
        return
    try:
        with open(os.path.join(dir_path, "model.pkl"), "wb") as f:
            pickle.dump(model, f)
    except Exception:
        pass


def _load_model_object(task_id: str, model_name: str, seg_no=None):
    """从某次回测的 artifacts 加载模型对象（model.pkl）。
    seg_no 指定时，加载滚动训练的对应段模型（segment_{seg_no}/model.pkl）；
    否则加载主目录模型（single 模式）。
    """
    import pickle
    import glob
    try:
        from ..config import WORK_DIR
        artifacts_root = os.path.join(WORK_DIR, "artifacts")
    except Exception:
        artifacts_root = os.path.join(os.path.abspath("."), "artifacts")

    # 定位该任务的 artifacts 目录
    dirs = glob.glob(os.path.join(artifacts_root, "*_" + task_id))
    if not dirs:
        dirs = glob.glob(os.path.join(artifacts_root, task_id))
    if not dirs:
        return None
    base = dirs[-1]

    # 若指定段号，优先加载段模型；否则加载主目录模型
    candidates = []
    if seg_no is not None:
        candidates.append(os.path.join(base, "segment_%s" % seg_no, "model.pkl"))
    candidates.append(os.path.join(base, "model.pkl"))
    for cp in candidates:
        if os.path.exists(cp):
            try:
                with open(cp, "rb") as f:
                    return pickle.load(f)
            except Exception:
                continue
    return None


def _get_segment_end_account(par, recorder) -> Optional[float]:
    """获取该段回测的期末账户总值。优先从 report_normal 的 account 列取最后一天。"""
    import glob
    import pickle

    report = None
    for key in ["portfolio_analysis/report_normal_1day.pkl", "report_normal_1day.pkl"]:
        try:
            report = recorder.load_object(key)
            if report is not None:
                break
        except Exception:
            continue
    if report is None or not hasattr(report, "columns") or "account" not in report.columns:
        return None
    try:
        acct = report["account"].dropna()
        if len(acct) > 0:
            return float(acct.iloc[-1])
    except Exception:
        pass
    return None


def _extract_trades(par, recorder) -> list:
    """从回测的 Indicator 对象中提取逐日逐笔调仓记录（含成交价、成本、滑点）。"""
    import glob
    import pickle
    import pandas as pd

    def _load_from_file() -> object:
        """从 mlruns 目录加载最新 indicators 对象；失败返回 None。"""
        try:
            from ..config import WORK_DIR
            # WORK_DIR 形如 backend/app/../workdir，需 normpath 后取 dirname 得到 backend 目录
            base = os.path.normpath(os.path.dirname(WORK_DIR))
        except Exception:
            base = os.path.abspath(".")
        patterns = [
            os.path.join(base, "mlruns", "*", "*", "artifacts", "portfolio_analysis",
                         "indicators_normal*_obj.pkl"),
            os.path.join(base, "..", "mlruns", "*", "*", "artifacts", "portfolio_analysis",
                         "indicators_normal*_obj.pkl"),
        ]
        files = []
        for p in patterns:
            files.extend(glob.glob(p))
        if not files:
            return None
        files.sort(key=lambda f: os.path.getmtime(f))
        try:
            with open(files[-1], "rb") as f:
                return pickle.load(f)
        except Exception:
            return None

    indicator = None
    # 方式一：从 recorder 加载 indicators 对象
    for key in ["portfolio_analysis/indicators_normal_1day_obj.pkl",
                "indicators_normal_1day_obj.pkl",
                "portfolio_analysis/indicators_normal_day_obj.pkl"]:
        try:
            indicator = recorder.load_object(key)
            if indicator is not None:
                break
        except Exception:
            continue

    # 校验方式一的 indicator 是否包含订单历史；否则回退到文件
    def _get_his(ind) -> dict:
        if ind is None:
            return {}
        h = getattr(ind, "order_indicator_his", None)
        return h if h else {}

    his = _get_his(indicator)
    if not his:
        indicator = _load_from_file()
        his = _get_his(indicator)

    if not his:
        return []

    # 从 Indicator 的 order_indicator_his 提取每笔订单
    trades = []
    try:
        for trade_date, oi in his.items():
            td = oi.get_index_data("trade_dir")
            if td is None or len(td.index) == 0:
                continue
            deal_amount = oi.get_index_data("deal_amount")
            trade_price = oi.get_index_data("trade_price")
            trade_cost = oi.get_index_data("trade_cost")
            trade_value = oi.get_index_data("trade_value")
            amount = oi.get_index_data("amount")
            ffr = oi.get_index_data("ffr")

            date_str = pd.Timestamp(trade_date).strftime("%Y-%m-%d")
            for i in range(len(td.index)):
                sid = td.index[i]
                # qlib OrderDir: SELL=0, BUY=1；这里统一转成 1=买入, -1=卖出
                raw_dir = td.data[i] if td.data[i] is not None else None
                direction = None
                if raw_dir is not None:
                    try:
                        direction = 1 if float(raw_dir) > 0 else -1
                    except Exception:
                        direction = None
                trades.append({
                    "date": date_str,
                    "instrument": str(sid),
                    "direction": direction,          # 1=买入, -1=卖出
                    "amount": float(amount.data[i]) if amount is not None and i < len(amount.data) else None,
                    "deal_price": float(trade_price.data[i]) if trade_price is not None and i < len(trade_price.data) else None,
                    "trade_value": float(trade_value.data[i]) if trade_value is not None and i < len(trade_value.data) else None,
                    "trade_cost": float(trade_cost.data[i]) if trade_cost is not None and i < len(trade_cost.data) else None,
                    "ffr": float(ffr.data[i]) if ffr is not None and i < len(ffr.data) else None,
                })
    except Exception:
        pass
    return trades


def _default_qlib_uri() -> str:
    from ..config import QLIB_PROVIDER_URI
    return QLIB_PROVIDER_URI


def _default_exp_uri(work_dir: Optional[str] = None) -> str:
    """返回 mlflow 实验追踪后端 uri（sqlite）。"""
    if work_dir is None:
        from ..config import WORK_DIR
        work_dir = WORK_DIR
    os.makedirs(work_dir, exist_ok=True)
    db_path = os.path.join(work_dir, "mlflow.db")
    return f"sqlite:///{db_path}"


def _pick_benchmark(universe: str, instruments: List[str]) -> str:
    """根据股票池选一个基准（示意：用指数代码或第一个成分）。"""
    bench_map = {
        "csi300": "SH000300",
        "csi500": "SH000905",
        "csi800": "SH000906",
        "csi1000": "SH000852",
    }
    if universe in bench_map:
        return bench_map[universe]
    # 回退：用第一个成分股做基准
    return instruments[0] if instruments else "SH000300"


def _fallback_benchmark(benchmark: str, start_time: str, end_time: str,
                        instruments: Optional[list] = None) -> str:
    """验证 benchmark 在指定时间段内是否有数据；若没有，回退到第一个成分股。

    若回退也失败，返回原 benchmark（会抛错让上层处理），确保不静默取消基准。
    """
    if not benchmark:
        return benchmark
    try:
        from qlib.data import D
        df = D.features([benchmark], ["$close"], start_time=start_time, end_time=end_time)
        if df is not None and len(df) > 0:
            return benchmark
    except Exception:
        pass
    # benchmark 无数据，回退到成分股
    if instruments:
        for code in instruments:
            try:
                from qlib.data import D
                df = D.features([code], ["$close"], start_time=start_time, end_time=end_time)
                if df is not None and len(df) > 0:
                    return str(code)
            except Exception:
                continue
    return benchmark


def _offset_date(date_str: str, days: int) -> str:
    from datetime import datetime, timedelta
    return (datetime.strptime(date_str, "%Y-%m-%d") + timedelta(days=days)).strftime("%Y-%m-%d")


def _sanitize_name(s: str) -> str:
    """清理字符串，只保留安全字符，用于目录名。"""
    return "".join(c if (c.isalnum() or c in "-_.") else "_" for c in s)


def _make_artifact_dir(work_dir: str, task_id: str, req: BacktestRequest) -> str:
    """生成可读、唯一的回测产物目录名。

    格式：{日期}-{时间}_{模型}_{股票池}_{起始年}_{结束年}_{task_id前8位}
    例：20260823-154500_LightGBM_csi300_2022_2026_ab12cd34
    """
    from datetime import datetime
    ts = datetime.now().strftime("%Y%m%d-%H%M%S")
    model = _sanitize_name(req.model or "unknown")
    universe = _sanitize_name(req.universe or "custom")
    start_y = (req.start_date or "")[:4]
    end_y = (req.end_date or "")[:4]
    # 目录名末尾带完整 task_id，保证唯一且可反查
    name = f"{ts}_{model}_{universe}_{start_y}_{end_y}_{task_id}"
    path = os.path.join(work_dir, "artifacts", name)
    os.makedirs(path, exist_ok=True)
    return path


def _save_curve_snapshot(dir_path: str, req: BacktestRequest, result: BacktestResult):
    """回测完成后，用 matplotlib 生成一张「曲线 + 参数横排」快照图，存入 artifacts。

    - summary.png: 上半部策略净值（红粗） vs 基准（蓝）曲线；下半部参数横排（多列网格）
    - 兼容旧命名：也生成 nav_curve.png（仅曲线）与 params_snapshot.png（仅参数）
    """
    try:
        import matplotlib
        matplotlib.use("Agg")  # 无界面后端
        import matplotlib.pyplot as plt
        from matplotlib.gridspec import GridSpec

        # 尝试使用中文字体（找不到就退化为英文标签）
        plt.rcParams["axes.unicode_minus"] = False
        try:
            plt.rcParams["font.sans-serif"] = ["Microsoft YaHei", "SimHei", "Arial Unicode MS", "DejaVu Sans"]
        except Exception:
            pass

        if not dir_path:
            return
        nav = (result.nav or []) if result else []
        if not nav:
            return

        dates = [p.get("date") for p in nav]
        values = [float(p.get("value", 1.0)) for p in nav]
        bench = [p.get("benchmark") for p in nav]

        # 收集结果指标与参数
        metrics = []
        if result.annualized_return is not None:
            metrics.append(("年化收益", "%.2f%%" % (result.annualized_return * 100)))
        if result.annualized_excess_return is not None:
            metrics.append(("年化超额", "%.2f%%" % (result.annualized_excess_return * 100)))
        if result.sharpe is not None:
            metrics.append(("夏普比率", "%.2f" % result.sharpe))
        if result.max_drawdown is not None:
            metrics.append(("最大回撤", "%.2f%%" % (result.max_drawdown * 100)))
        if result.win_rate is not None:
            metrics.append(("胜率", "%.2f%%" % (result.win_rate * 100)))
        if result.total_return is not None:
            metrics.append(("累计收益", "%.2f%%" % (result.total_return * 100)))
        if result.benchmark_return is not None:
            metrics.append(("基准收益", "%.2f%%" % (result.benchmark_return * 100)))

        # 格式化参数值（None → "不限"），便于图片展示
        def _fmt_val(v, digits=None):
            if v is None:
                return "不限"
            if digits is not None and isinstance(v, (int, float)):
                return ("%." + str(digits) + "f") % v
            return str(v)

        # 训练窗 / 测试窗：算出对应的具体日期区间（基于回测 start_date 反推训练起点）
        try:
            train_start_date = _add_period(req.start_date, -req.train_win, req.train_unit)
            train_label = f"{req.train_win}{req.train_unit[0]} ({train_start_date} ~ {req.start_date})"
        except Exception:
            train_label = f"{req.train_win}{req.train_unit[0]}"
        try:
            test_end_date = _add_period(req.start_date, req.test_win, req.test_unit)
            # 测试窗结束超过回测结束则截断
            if test_end_date > req.end_date:
                test_end_date = req.end_date
            test_label = f"{req.test_win}{req.test_unit[0]} ({req.start_date} ~ {test_end_date})"
        except Exception:
            test_label = f"{req.test_win}{req.test_unit[0]}"

        params_text = {
            "股票池": req.universe,
            "资金": "%.0f万" % ((req.initial_capital or 0) / 10000),
            "模型": req.model,
            "特征": req.feature,
            "TopK": str(req.topk),
            "持仓": f"{req.n_days_hold}天",
            "划分": "滚动" if (req.split_mode or "").lower() == "custom" else "一次性",
            "成交价": req.deal_price,
            "买费": "%.4f" % req.open_cost,
            "卖费": "%.4f" % req.close_cost,
            "滑点": "%.4f" % req.impact_cost,
            "量限制": _fmt_val(req.volume_threshold, 2),
            "涨跌停": _fmt_val(req.limit_threshold, 3),
            "每手": _fmt_val(req.trade_unit),
            # 把训练窗/测试窗放最后一行（最长），避免覆盖其他参数
            "训练窗": train_label,
            "测试窗": test_label,
        }

        # ---------- 主图：曲线(上) + 参数横排(下) ----------
        try:
            fig = plt.figure(figsize=(13, 8))
            # 上曲线：高度 2.2，下参数：高度 1.5，给参数区更多空间；增加 hspace 让参数与曲线分开
            gs = GridSpec(2, 1, height_ratios=[2.2, 1.5], hspace=0.45)

            # 上：曲线
            ax = fig.add_subplot(gs[0])
            ax.plot(dates, values, color="red", linewidth=3.0, label="策略净值")
            if any(b is not None for b in bench):
                bench_vals = [b if b is not None else float("nan") for b in bench]
                ax.plot(dates, bench_vals, color="blue", linewidth=1.5, label="基准")
            ax.axhline(y=1.0, color="gray", linestyle="--", linewidth=0.8)
            ax.set_title("回测净值曲线  %s → %s  (模型: %s, 股票池: %s, 资金: %.0f万元)" % (
                req.start_date, req.end_date, req.model, req.universe,
                (req.initial_capital or 0) / 10000))
            # 不再显示 "日期" x 轴标签
            ax.set_xlabel("")
            ax.set_ylabel("净值")
            ax.legend(loc="upper left")
            if len(dates) > 20:
                step = max(1, len(dates) // 10)
                ax.set_xticks(dates[::step])
            fig.autofmt_xdate(rotation=45)

            # 下：参数横排（网格）
            ax2 = fig.add_subplot(gs[1])
            ax2.axis("off")
            # 把训练窗/测试窗（长字符串）放在主网格的最后两个位置，单独成行不被覆盖
            metrics_list = list(metrics)
            base_items = list(params_text.items())
            train_test_items = [(k, v) for k, v in base_items if k in ("训练窗", "测试窗")]
            other_items = [(k, v) for k, v in base_items if k not in ("训练窗", "测试窗")]

            ncols = 6
            # 第一区域：结果指标 + 主要参数
            main_items = [(k, v, "#0f766e") for k, v in metrics_list] + \
                         [(k, v, "black") for k, v in other_items]
            x_positions = [0.03 + 0.165 * c for c in range(ncols)]
            # 行高收紧（0.18），保证 20 项（4 行）能放下，且训练窗行在主区域之后仍有位置
            row_height = 0.18
            for idx, (k, v, color) in enumerate(main_items):
                r = idx // ncols
                c = idx % ncols
                y = 0.95 - r * row_height
                ax2.text(x_positions[c], y, f"{k}: {v}", ha="left", va="top", fontsize=8,
                         color=color)

            # 第二区域：训练窗/测试窗 单独一行（放在量限制/涨跌停/每手之后）
            n_main_rows = (len(main_items) + ncols - 1) // ncols
            y_train = 0.95 - n_main_rows * row_height - 0.04
            if y_train > 0.0:
                ax2.text(0.03, y_train, f"{train_test_items[0][0]}: {train_test_items[0][1]}",
                         ha="left", va="top", fontsize=7.5, color="black")
                ax2.text(0.55, y_train, f"{train_test_items[1][0]}: {train_test_items[1][1]}",
                         ha="left", va="top", fontsize=7.5, color="black")

            fig.savefig(os.path.join(dir_path, "summary.png"), dpi=100, bbox_inches="tight")
            plt.close(fig)
        except Exception:
            pass

        # ---------- 兼容：单独曲线图 ----------
        try:
            fig, ax = plt.subplots(figsize=(12, 6))
            ax.plot(dates, values, color="red", linewidth=3.0, label="策略净值")
            if any(b is not None for b in bench):
                bench_vals = [b if b is not None else float("nan") for b in bench]
                ax.plot(dates, bench_vals, color="blue", linewidth=1.5, label="基准")
            ax.axhline(y=1.0, color="gray", linestyle="--", linewidth=0.8)
            ax.set_title("回测净值曲线  %s → %s" % (req.start_date, req.end_date))
            ax.set_xlabel("日期")
            ax.set_ylabel("净值")
            ax.legend()
            if len(dates) > 20:
                step = max(1, len(dates) // 12)
                ax.set_xticks(dates[::step])
            fig.autofmt_xdate(rotation=45)
            fig.tight_layout()
            fig.savefig(os.path.join(dir_path, "nav_curve.png"), dpi=100)
            plt.close(fig)
        except Exception:
            pass

        # ---------- 兼容：单独参数图 ----------
        try:
            fig2, ax2 = plt.subplots(figsize=(10, 9))
            ax2.axis("off")
            ax2.text(0.5, 0.96, "回测参数快照", ha="center", va="top", fontsize=16, fontweight="bold")
            y = 0.90
            ax2.text(0.03, y, "回测结果指标：", ha="left", va="top", fontsize=12, fontweight="bold")
            y -= 0.045
            for k, v in metrics:
                ax2.text(0.05, y, f"{k}:  {v}", ha="left", va="top", fontsize=11)
                y -= 0.04
            y -= 0.02
            ax2.text(0.03, y, "回测参数：", ha="left", va="top", fontsize=12, fontweight="bold")
            y -= 0.045
            for k, v in params_text.items():
                ax2.text(0.05, y, f"{k}:  {v}", ha="left", va="top", fontsize=11)
                y -= 0.038
            fig2.tight_layout()
            fig2.savefig(os.path.join(dir_path, "params_snapshot.png"), dpi=100)
            plt.close(fig2)
        except Exception:
            pass
    except Exception:
        pass


def _sanitize_json(o):
    """递归把 NaN/Infinity 等非有限浮点转成 None，避免写入 JSON 后前端/接口序列化失败。"""
    import math
    if isinstance(o, float):
        return None if not math.isfinite(o) else o
    if isinstance(o, dict):
        return {k: _sanitize_json(v) for k, v in o.items()}
    if isinstance(o, (list, tuple)):
        return [_sanitize_json(v) for v in o]
    return o


def _save_result_json(dir_path: str, result: BacktestResult):
    """把回测完整结果（指标/净值/调仓记录）持久化到 artifacts，供历史查看。

    保存前会把 NaN/Infinity 清理为 null，确保 result.json 是标准 JSON，
    /result 接口与前端能正常读取。
    """
    import json
    try:
        data = result.model_dump() if hasattr(result, "model_dump") else result.dict()
        data = _sanitize_json(data)
        with open(os.path.join(dir_path, "result.json"), "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2, default=str)
    except Exception:
        pass


def _save_backtest_params(dir_path: str, req: BacktestRequest):
    """保存回测参数快照（完整可复现参数 + 人工可读 meta）。"""
    import json
    try:
        # 完整参数（前端复现模式直接用）
        params = req.model_dump()
        with open(os.path.join(dir_path, "params.json"), "w", encoding="utf-8") as f:
            json.dump(params, f, ensure_ascii=False, indent=2, default=str)

        # 人工可读参数快照
        meta = {
            "回测目录": os.path.basename(dir_path),
            "股票池": req.universe,
            "起始日期": req.start_date,
            "结束日期": req.end_date,
            "起始资金(元)": req.initial_capital,
            "模型": req.model,
            "特征": req.feature,
            "TopK": req.topk,
            "持仓周期(天)": req.n_days_hold,
            "划分方式": "滚动训练" if (req.split_mode or "").lower() == "custom" else "一次性训练",
            "成交价基准": req.deal_price,
            "买入手续费": req.open_cost,
            "卖出手续费": req.close_cost,
            "滑点": req.impact_cost,
            "最低手续费(元)": req.min_cost,
            "成交量限制": req.volume_threshold,
            "涨跌停限制": req.limit_threshold,
            "每手股数": req.trade_unit,
            "训练窗口": f"{req.train_win} {req.train_unit}",
            "测试窗口": f"{req.test_win} {req.test_unit}",
        }
        with open(os.path.join(dir_path, "meta.json"), "w", encoding="utf-8") as f:
            json.dump(meta, f, ensure_ascii=False, indent=2, default=str)
    except Exception:
        pass


def _add_period(date_str: str, amount: int, unit: str) -> str:
    """在日期上加减一个周期（单位：day/week/month）。返回 %Y-%m-%d 字符串。"""
    from datetime import datetime
    from dateutil.relativedelta import relativedelta
    unit = (unit or "day").lower()
    if unit.startswith("month"):
        delta = relativedelta(months=amount)
    elif unit.startswith("week"):
        delta = relativedelta(weeks=amount)
    else:
        delta = relativedelta(days=amount)
    return (datetime.strptime(date_str, "%Y-%m-%d") + delta).strftime("%Y-%m-%d")


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
