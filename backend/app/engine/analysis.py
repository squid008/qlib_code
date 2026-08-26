# -*- coding: utf-8 -*-
"""回测分析模块：分层回测 + IC 计算（从 qlib_engine.py 拆分而来）。

包含：预测+label 提取、基准累计收益、分层（5组）、IC/RankIC/ICIR 计算。
均为纯函数/轻依赖（仅 pandas / qlib.data），不依赖回测编排逻辑，便于单测。
"""
from typing import Optional

import pandas as pd


def _get_pred_label(model, dataset, instruments, segment: str):
    """获取某段（train/test）的预测分 + 未来收益 label，合并成一个 DataFrame。

    返回 None 表示该段没有可用的预测或 label。返回 DataFrame columns=['score','label']，
    index 为 MultiIndex=[instrument, datetime]。
    """
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
    from qlib.data import D

    def _calc(code):
        df = D.features([code], ["$close"], start_time=start, end_time=end)
        if df is None or len(df) == 0:
            return None
        close = df["$close"]
        s = close.droplevel("instrument").sort_index()
        ret = s.pct_change().fillna(0.0)
        cum = ret.cumsum()
        result = {}
        for dt, v in cum.items():
            result[pd.Timestamp(dt).strftime("%Y-%m-%d")] = round(float(v), 6)
        return result

    try:
        result = _calc(benchmark)
        if result:
            return result
        # Fallback: 原始 benchmark 在该时间段无数据时，回退到 SH000300（沪深300）
        if benchmark and benchmark != "SH000300":
            result = _calc("SH000300")
            if result:
                return result
        return {}
    except Exception:
        return {}


def _compute_layers(pred_label, N: int = 5, benchmark_ret: Optional[dict] = None):
    """按预测分每日横截面均分 N 组，返回每组累计收益曲线数据（复刻 qlib _group_return，但返回数据）。

    benchmark_ret: {date_str: cum_ret}，可选的基准累计收益，用于在分层图上叠加基准线。
    返回 list[{date, Group1..GroupN, long_short, long_average, benchmark?}]；失败返回 None。
    """
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
