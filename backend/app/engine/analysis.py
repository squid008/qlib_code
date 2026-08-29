# -*- coding: utf-8 -*-
"""回测分析模块：分层回测 + IC 计算（从 qlib_engine.py 拆分而来）。

包含：预测+label 提取、基准累计收益、分层（5组）、IC/RankIC/ICIR 计算。
均为纯函数/轻依赖（仅 pandas / qlib.data），不依赖回测编排逻辑，便于单测。
"""
import threading
from typing import Optional

import pandas as pd


# 基准累计收益结果的进程内缓存（按 code+start+end），线程安全。
class _BenchCache:
    def __init__(self):
        self._data: dict = {}
        self._lock = threading.Lock()

    def get(self, code: str, start, end):
        key = (code, str(start), str(end))
        with self._lock:
            return self._data.get(key)

    def put(self, code: str, start, end, result):
        key = (code, str(start), str(end))
        with self._lock:
            self._data[key] = result


_BENCH_CACHE = _BenchCache()


def _get_pred_label(model, dataset, instruments, segment: str, label_horizon: int = 2):
    """获取某段（train/test）的预测分 + 未来收益 label，合并成一个 DataFrame。

    label_horizon: 预测周期（天），label = 未来 N 个交易日的收益。
    返回 None 表示该段没有可用的预测或 label。返回 DataFrame columns=['score','label']，
    index 为 MultiIndex=[instrument, datetime]。
    """
    from qlib.data import D

    n = max(1, int(label_horizon or 2))
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
        # label：未来 n 个交易日的收益（用于 IC/分层信号评估）
        label_expr = f"Ref($close, -{n + 1}) / Ref($close, -1) - 1"
        # ret：当日收益（用于"分层持仓周期"算法A：调仓日分组后按日收益累加持有）
        ret_expr = "$close / Ref($close, 1) - 1"
        # 用进程内共享缓存包裹 D.features：相同股票池/表达式/区间 复用，避免重复 I/O+计算
        from .data_cache import SHARED_CACHE

        feat_df = SHARED_CACHE.get_or_load(
            instruments, [label_expr, ret_expr], start, end,
            lambda: D.features(
                instruments, [label_expr, ret_expr],
                start_time=start, end_time=end,
            ),
        )
        if feat_df is None:
            return None
        feat_df = feat_df.copy()  # 只读共享缓存，副本后再改列名/join，避免污染缓存
        feat_df.columns = ["label", "ret"]
        feat_df.index.names = ["instrument", "datetime"]
        merged = pred.join(feat_df, how="inner")
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
        # 基准累计收益结果缓存：相同 code+区间 复用，避免重复计算
        from .data_cache import SHARED_CACHE

        key_code = str(code).lower()
        cached = _BENCH_CACHE.get(key_code, start, end)
        if cached is not None:
            return dict(cached)

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
        _BENCH_CACHE.put(key_code, start, end, result)
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


def _compute_layers(pred_label, N: int = 5, benchmark_ret: Optional[dict] = None,
                    rebalance_period: int = 1):
    """按预测分横截面均分 N 组，返回每组累计收益曲线数据。

    rebalance_period（分层持仓周期，算法A）：
      - 1（默认）：每日重排分组（原行为，因子诊断用）
      - >1：只在调仓日（每 rebalance_period 个交易日）按 score 分组，中间持仓不动
        （组内股票按当日收益累加），下一调仓日重新分组。用于评估"与实盘调仓周期
        一致"的可落地收益。
    benchmark_ret: {date_str: cum_ret}，可选的基准累计收益。
    返回 list[{date, Group1..GroupN, long_short, long_average, benchmark?}]；失败返回 None。
    """
    if pred_label is None or len(pred_label) == 0:
        return None
    df = pred_label.copy()
    df = df.dropna(subset=["score"])
    if df.empty:
        return None
    try:
        dates = df.index.get_level_values("datetime").unique().sort_values()
        # 每日收益列（算法A需要逐日收益累加）；每日重排模式不依赖它
        has_ret = "ret" in df.columns

        if rebalance_period <= 1 or not has_ret:
            # ---- 每日重排（原逻辑）：每日横截面按 score 分 N 组，取各组当日 label 均值 ----
            d = df.sort_values("score", ascending=False)
            t_df = pd.DataFrame({
                "Group%d" % (i + 1): d.groupby(level="datetime", group_keys=False)["label"].apply(
                    lambda x: x[len(x) // N * i: len(x) // N * (i + 1)].mean()  # noqa: B023
                )
                for i in range(N)
            })
            t_df["long_short"] = t_df["Group1"] - t_df["Group%d" % N]
            t_df["long_average"] = t_df["Group1"] - d.groupby(level="datetime", group_keys=False)["label"].mean()
            t_df = t_df.dropna(how="all")
            cum = t_df.cumsum()
            rows = cum.iterrows()
        else:
            # ---- 算法A：调仓日分组，持有到下一调仓日 ----
            period = max(2, int(rebalance_period))
            # 调仓日：从第一天起每 period 个交易日取一个
            rebal_dates = set(dates[i] for i in range(0, len(dates), period))
            # 每个交易日各组的收益
            daily_groups = {}
            current_groups = None  # 当前持仓期各组的股票列表
            for t in dates:
                if t in rebal_dates:
                    # 调仓日：按当天 score 重新分组
                    sub = df.xs(t, level="datetime")
                    sub = sub.sort_values("score", ascending=False)
                    current_groups = [
                        sub.iloc[len(sub) // N * i: len(sub) // N * (i + 1)].index.tolist()
                        for i in range(N)
                    ]
                # 当前日各组收益 = 持仓股票当日 ret 的等权平均（持仓不动）
                g_ret = []
                if current_groups is not None:
                    day_sub = df.xs(t, level="datetime")
                    for g in range(N):
                        stocks = current_groups[g]
                        if stocks:
                            vals = day_sub["ret"].reindex(stocks).dropna()
                            g_ret.append(float(vals.mean()) if len(vals) else None)
                        else:
                            g_ret.append(None)
                daily_groups[t] = g_ret
            # 汇总为 DataFrame 并累计
            recs = {}
            for t, g_ret in daily_groups.items():
                recs[t] = {"Group%d" % (i + 1): g_ret[i] for i in range(N)}
            t_df = pd.DataFrame.from_dict(recs, orient="index")
            t_df = t_df.dropna(how="all")
            t_df["long_short"] = t_df["Group1"] - t_df["Group%d" % N]
            t_df["long_average"] = t_df["Group1"] - df.groupby(level="datetime", group_keys=False)["label"].mean()
            cum = t_df.cumsum()
            rows = cum.iterrows()
    except Exception:
        return None
    points = []
    for dt, row in rows:
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


def _compute_analysis(model, dataset, instruments, seg_label: str, benchmark: Optional[str] = None,
                      label_horizon: int = 2, rebalance_period: int = 1):
    """计算某段的分析数据：分层回测（test 段）+ 训练/测试集 IC 曲线。

    benchmark: 基准指数代码（如 SH000300），用于在分层图上叠加基准累计收益线。
    label_horizon: 预测周期（天），分层/IC 用未来 N 日收益，与训练 label 口径一致。
    rebalance_period: 分层持仓周期（天，算法A）。1=每日重排；>1=调仓日分组持有到下一调仓日。
    返回 dict:
      { "layers": {segment, groups}, "ic_train": {...}, "ic_test": {...},
        "test_pl": DataFrame|None, "train_pl": DataFrame|None }
    test_pl / train_pl 分别是测试段/训练段的预测+label（调用方可复用于汇总合成，
    避免重复 predict）。失败项为 None。
    """
    test_pl = _get_pred_label(model, dataset, instruments, "test", label_horizon=label_horizon)
    train_pl = _get_pred_label(model, dataset, instruments, "train", label_horizon=label_horizon)

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
        layers = _compute_layers(test_pl, benchmark_ret=benchmark_ret, rebalance_period=rebalance_period)
        if layers:
            layers = {"segment": seg_label, "groups": layers, "benchmark": benchmark}

    ic_train = _compute_ic(train_pl)
    ic_test = _compute_ic(test_pl)

    return {
        "layers": layers,
        "ic_train": ic_train,
        "ic_test": ic_test,
        "test_pl": test_pl,
        "train_pl": train_pl,
    }
