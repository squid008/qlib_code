# -*- coding: utf-8 -*-
"""单因子测试：不训练模型，对单个因子做快速诊断。

针对稀疏 0/1 信号（自定义公式）与连续因子（Alpha158/360）做统一筛查：
  1. coverage        因子值非空比例
  2. nonzero_ratio   非零比例
  3. is_binary       是否为 0/1 二值信号
  4. trigger         触发组未来 N 日收益统计：样本数/均值/中位数（0/1 信号：>0.5；连续因子：前 20% 高分位）
  5. not_trigger     未触发组（0/1：<=0.5；连续因子：后 20% 低分位）同口径
  6. diff            触发均值 - 未触发均值
  7. p_value         Mann-Whitney U 检验 p 值（两组均有样本时计算）
  8. ic/rank_ic/icir/rank_icir   信息系数（连续因子主要指标）

所有因子一次 D.features 加载（多表达式并行计算），逐个统计。
"""
from __future__ import annotations

from typing import Dict, List, Optional

import numpy as np
import pandas as pd

from ..engine.limits import mark_limit_up
from ..engine.adjust import adjust_expr, normalize_mode


def _acf(x: np.ndarray, k: int) -> float:
    """lag-k 自相关系数（样本）。"""
    n = len(x)
    if n <= k:
        return 0.0
    xc = x - x.mean()
    denom = float(np.sum(xc ** 2))
    if denom <= 0:
        return 0.0
    return float(np.sum(xc[: n - k] * xc[k:]) / denom)


def _hac_t(x: np.ndarray, maxlags: int = None) -> Optional[float]:
    """Newey-West HAC 稳健 t 统计量（修正自相关 + 异方差）。

    maxlags 默认按 Newey-West 建议：int(4 * (n/100)^(2/9))。
    方差 = γ0 + 2·Σ(1 - j/(L+1))·γj；t = mean / sqrt(修正方差/n)。
    序列方差被极端自相关压成非正时钳到极小值。
    """
    n = len(x)
    if n < 3:
        return None
    if maxlags is None:
        maxlags = int(4 * (n / 100.0) ** (2 / 9.0))
        maxlags = max(1, min(maxlags, n - 2))
    m = float(x.mean())
    xc = x - m
    gam = np.array([np.sum(xc[: n - k] * xc[k:]) / n for k in range(maxlags + 1)])
    var = gam[0] + 2.0 * np.sum((1 - np.arange(1, maxlags + 1) / (maxlags + 1)) * gam[1:])
    se = np.sqrt(max(var, 1e-18) / n)
    return float(m / se)


class FactorTestCancelled(Exception):
    """任务被用户取消（由 progress_cb 抛出）。供路由层捕获后标记 cancelled 状态。"""


def _dump_sft_error(exc: Exception) -> None:
    """特征计算失败时把完整诊断信息写入 workdir/sft_error.log（定位算子注册问题用）。"""
    try:
        import os
        import traceback
        from datetime import datetime

        import qlib
        import qlib.data.ops as _qlib_ops
        from qlib.data.ops import Operators
        from qlib.config import C as _C

        backend_dir = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
        log_path = os.path.join(backend_dir, "workdir", "sft_error.log")
        with open(log_path, "a", encoding="utf-8") as f:
            f.write("=" * 60 + "\n")
            f.write("time: %s  pid: %s\n" % (datetime.now().isoformat(), os.getpid()))
            f.write("exc: %r\n" % exc)
            f.write("qlib module: %s\n" % getattr(qlib, "__file__", "?"))
            f.write("ops module: %s\n" % getattr(_qlib_ops, "__file__", "?"))
            f.write("C.registered: %s  C.custom_ops: %s\n" % (
                getattr(_C, "registered", None),
                [type(o).__name__ for o in (getattr(_C, "custom_ops", None) or [])],
            ))
            f.write("Operators in this process: %s\n" % sorted(Operators._ops.keys()))
            f.write(traceback.format_exc())
            f.write("\n")
    except Exception:
        pass


def _ensure_qlib_init() -> None:
    """复用回测引擎的全局 qlib.init（线程安全，进程内只初始化一次）。"""
    from ..engine.qlib_engine import _ensure_qlib_init as _engine_init
    from ..engine.utils import _default_qlib_uri

    _engine_init(_default_qlib_uri())


def _resolve_instruments(universe: str, start_date: str) -> List[str]:
    """按股票池解析成分股（大写 qlib 代码），排除北交所与指数代码。"""
    from qlib.data import D

    inst_scope = D.instruments(market=universe)
    instruments = [
        str(i).upper()
        for i in D.list_instruments(inst_scope, start_time=start_date, as_list=True)
    ]
    filtered = []
    for i in instruments:
        c = str(i)
        if c.startswith("BJ"):
            continue
        if c.startswith(("SH000", "SH88", "SH89", "SZ39")):
            continue
        filtered.append(i)
    return filtered


def _stat_group(g: pd.DataFrame, limit_up_excluded: int = 0) -> Optional[dict]:
    """触发/未触发组的收益统计。"""
    if g is None or len(g) == 0:
        return None
    return {
        "count": int(len(g)),
        "mean_ret": round(float(g["LABEL"].mean()), 6),
        "median_ret": round(float(g["LABEL"].median()), 6),
        "limit_up_excluded": int(limit_up_excluded),
    }


def _compute_ic_stats(pl: pd.DataFrame) -> Optional[dict]:
    """对 (score, label) 面板数据计算 IC/RankIC/ICIR。pl index 为 [instrument, datetime]。"""
    from ..engine.analysis import _compute_ic

    res = _compute_ic(pl)
    if not res:
        return None
    return {
        "ic": res.get("mean_ic"),          # 与前端字段名对齐（此前误用 mean_ic 导致前端 IC 恒为 null）
        "rank_ic": res.get("mean_rank_ic"),
        "icir": res.get("icir"),
        "rank_icir": res.get("rank_icir"),
    }


def _test_one(
    df: pd.DataFrame,
    factor: dict,
    col: str,
    exclude_limit_up_signal: bool = True,
    exclude_limit_up_trade: bool = True,
    exclude_suspended: bool = True,
    cancelled=None,  # 取消检查回调：返回 True 表示用户已取消，在重计算步骤间调用（可空）
) -> dict:
    """测试单个因子列（df 含 col 与 LABEL 两列）。

    df 还需含 CLOSE/CHANGE（信号日 T 行情）与 T1_CLOSE/T1_CHANGE（成交日 T+1 行情，Ref 取未来一天）。
    触发组剔除（均可开关）：
      - exclude_limit_up_signal  信号日（T）涨停：选股过滤语义（涨停后追高风险），无前视
      - exclude_limit_up_trade   成交日（T+1）涨停：真实撮合约束（封板买不到），与回测一致
      - exclude_suspended        成交日（T+1）停牌/无行情：同样买不到
    """
    name = factor.get("name") or factor.get("id") or col
    result: dict = {
        "id": factor.get("id") or name,
        "name": name,
        "source": factor.get("source") or "custom",
        "expression": factor.get("expression") or "",
        "coverage": None,
        "nonzero_ratio": None,
        "is_binary": False,
        "grouping": None,
        "quintile_ret": None,
        "trigger": None,
        "not_trigger": None,
        "diff": None,
        "p_value": None,
        "daily_diff": None,
        "daily_t": None,
        "daily_win": None,
        "daily_n": 0,
        # 逐日截面均值：信号组/非信号组各自逐日(触发组均值/未触发组均值)的日均未来收益
        # （用于 0/1 信号的"信号组 vs 非信号组"日截面双柱展示，区别于整体观测加权 mean_ret）
        "daily_trig_mean": None,
        "daily_not_mean": None,
        "ic": None,
        "rank_ic": None,
        "icir": None,
        "rank_icir": None,
        "n_obs": 0,
        "limit_up_excluded": 0,       # 总剔除数（信号日涨停 + 成交日涨停 + 停牌）
        "limit_up_excluded_t": 0,     # 信号日（T）涨停剔除数
        "limit_up_excluded_t1": 0,    # 成交日（T+1）涨停剔除数
        "suspended_excluded": 0,      # 成交日停牌/无行情剔除数
        "error": None,
    }
    if df is None or len(df) == 0:
        result["error"] = "无数据（股票池或日期区间无行情）"
        return result

    fv = df[col]
    result["coverage"] = round(float(fv.notna().mean()), 4)

    sub = df[[col, "LABEL"]].dropna()
    result["n_obs"] = int(len(sub))
    if len(sub) == 0:
        result["error"] = "无有效配对样本（因子或未来收益为空）"
        return result

    vals = sub[col]
    result["nonzero_ratio"] = round(float((vals > 1e-9).mean()), 4)
    try:
        uniq = vals.round(6).unique()
        result["is_binary"] = len(uniq) <= 2 and set(map(float, uniq)).issubset({0.0, 1.0})
    except Exception:
        result["is_binary"] = False

    # IC / RankIC / ICIR
    try:
        if cancelled is not None and cancelled():
            raise FactorTestCancelled()
        pl = sub.rename(columns={col: "score", "LABEL": "label"})
        icr = _compute_ic_stats(pl)
        if icr:
            result.update(icr)
    except FactorTestCancelled:
        raise
    except Exception:
        pass

    # 触发 vs 未触发：0/1 稀疏信号按 >0.5 分组；连续因子按分位数分组
    # （触发 = 前 20% 高分位，未触发 = 后 20% 低分位）。否则连续因子几乎全部落入
    # 触发组，触发/未触发统计失去意义。
    if result["is_binary"]:
        trig = sub[sub[col] > 0.5]
        not_trig = sub[sub[col] <= 0.5]
        result["grouping"] = "binary"
    else:
        q_hi = float(sub[col].quantile(0.8))
        q_lo = float(sub[col].quantile(0.2))
        trig = sub[sub[col] >= q_hi]
        not_trig = sub[sub[col] <= q_lo]
        result["grouping"] = "quantile"
    # 触发组剔除（三层独立统计，均可开关）：
    #   1) 信号日（T）涨停：选股过滤（涨停追高风险，信号日收盘后已知，无前视）
    #   2) 成交日（T+1）涨停：真实撮合约束（调仓日封板买不到），与回测 BoardAwareExchange 口径一致
    #   3) 成交日（T+1）停牌/无行情：同样买不到
    lu_t = lu_t1 = susp = 0
    if len(trig) > 0:
        if exclude_limit_up_signal and "CLOSE" in df.columns and "CHANGE" in df.columns:
            mask = mark_limit_up(df.loc[trig.index], "CLOSE", "CHANGE")
            lu_t = int(mask.sum())
            trig = trig[~mask]
        if exclude_limit_up_trade and "T1_CLOSE" in df.columns and "T1_CHANGE" in df.columns:
            mask = mark_limit_up(df.loc[trig.index], "T1_CLOSE", "T1_CHANGE")
            lu_t1 = int(mask.sum())
            trig = trig[~mask]
        if exclude_suspended and "T1_CLOSE" in df.columns:
            mask = df.loc[trig.index, "T1_CLOSE"].isna()
            susp = int(mask.sum())
            trig = trig[~mask]
    lu_excluded = lu_t + lu_t1 + susp
    result["limit_up_excluded"] = lu_excluded
    result["limit_up_excluded_t"] = lu_t
    result["limit_up_excluded_t1"] = lu_t1
    result["suspended_excluded"] = susp
    result["trigger"] = _stat_group(trig, lu_excluded)
    result["not_trigger"] = _stat_group(not_trig)
    if len(trig) > 0 and len(not_trig) > 0:
        result["diff"] = round(float(trig["LABEL"].mean() - not_trig["LABEL"].mean()), 6)
        if len(trig) >= 5 and len(not_trig) >= 5:
            try:
                if cancelled is not None and cancelled():
                    raise FactorTestCancelled()
                from scipy.stats import mannwhitneyu

                _, p = mannwhitneyu(trig["LABEL"], not_trig["LABEL"], alternative="two-sided")
                result["p_value"] = float(p)
            except FactorTestCancelled:
                raise
            except Exception:
                result["p_value"] = None
        # 按日配对检验：逐日 触发均值-未触发均值 作为日差值序列做单样本 t 检验。
        # 原始 MWU p 值在百万级样本下必然趋近 0，信息量低；日差值序列（约交易日数个点）
        # 规避横截面收益相关导致的假高显著性，且能给出业务上有意义的胜率。
        try:
            if cancelled is not None and cancelled():
                raise FactorTestCancelled()
            dt_pos = sub.index.names.index("datetime")
            daily_trig = trig.groupby(level=dt_pos)["LABEL"].mean()
            daily_not = not_trig.groupby(level=dt_pos)["LABEL"].mean()
            result["daily_trig_mean"] = round(float(daily_trig.mean()), 6)
            result["daily_not_mean"] = round(float(daily_not.mean()), 6)
            daily = (daily_trig - daily_not).dropna()
            if len(daily) >= 2:
                from scipy.stats import ttest_1samp

                x = daily.to_numpy(dtype=float)
                t_stat, _ = ttest_1samp(x, 0)
                result["daily_diff"] = round(float(x.mean()), 6)
                result["daily_t"] = round(float(t_stat), 4)
                result["daily_win"] = round(float((x > 0).mean()), 4)
                result["daily_n"] = int(len(x))
                # Newey-West HAC 稳健 t（主显示用，修正自相关/异方差）
                try:
                    th = _hac_t(x)
                    if th is not None:
                        result["daily_t_hac"] = round(th, 4)
                except Exception:
                    pass
                # 自相关（lag-1/lag-5）与方差稳定性（后半/前半方差比）
                try:
                    result["daily_acf1"] = round(_acf(x, 1), 4)
                    result["daily_acf5"] = round(_acf(x, 5), 4)
                    half = len(x) // 2
                    v0 = float(x[:half].var())
                    v1 = float(x[half:].var())
                    result["daily_var_ratio"] = round((v1 / v0) if v0 > 1e-12 else float("nan"), 2)
                except Exception:
                    pass
        except FactorTestCancelled:
            raise
        except Exception:
            pass

    # 5 组分位收益（连续因子）：按每日横截面均分 5 组，汇总每组平均未来收益。
    # 用于识别 U 型/倒 U 型等非线性关系（单调关系 IC/RankIC 已足够，U 型/倒 U 型两端
    # 收益相近、diff≈0，必须看全部分组形态）。
    if not result["is_binary"] and len(sub) >= 100:
        try:
            if cancelled is not None and cancelled():
                raise FactorTestCancelled()
            dt_pos = sub.index.names.index("datetime")
            tmp = sub[[col, "LABEL"]].copy()
            tmp["_q"] = tmp.groupby(level=dt_pos)[col].transform(
                lambda x: pd.qcut(x.rank(method="first"), 5, labels=False, duplicates="drop") + 1
            )
            tmp = tmp.dropna(subset=["_q"])
            groups = []
            for q, g in tmp.groupby("_q"):
                groups.append({
                    "quantile": int(q),
                    "count": int(len(g)),
                    "mean_ret": round(float(g["LABEL"].mean()), 6),
                })
            if len(groups) >= 2:
                result["quintile_ret"] = groups
        except FactorTestCancelled:
            raise
        except Exception:
            result["quintile_ret"] = None
    return result


def run_single_factor_test(
    universe: str,
    start_date: str,
    end_date: str,
    label_horizon: int = 2,
    factors: List[dict] = None,
    progress_cb=None,
    cancelled=None,  # 取消检查回调：返回 True 表示用户已取消（在因子间与 _test_one 内检查）
    exclude_limit_up_signal: bool = True,  # 剔除信号日（T）涨停样本
    exclude_limit_up_trade: bool = True,   # 剔除成交日（T+1）涨停样本
    exclude_suspended: bool = True,        # 剔除成交日（T+1）停牌/无行情样本
    price_adjust: str = "none",            # 复权方式：none/forward/backward（与回测对齐）
) -> List[dict]:
    """执行单因子测试，返回每个因子的测试结果列表。

    factors: [{id, name, expression, source}]。expression 为 qlib 表达式。
    label: 未来 label_horizon 个交易日的收益（与回测 label 口径一致）。
    progress_cb: 可选回调 progress_cb(percent: float, message: str)，0-100。
        阶段：0-10 解析股票池 → 10-35 分批计算特征（批间可取消）→ 35-95 逐个因子统计 → 100 完成。
        取消：调用方通过 progress_cb 抛异常可中断任务（特征加载按批检查）。
    cancelled: 可选，返回 True 表示用户已请求取消；在因子间、以及 _test_one 内部的
        重计算步骤（IC/检验/分位）间检查，使取消响应更快（尤其单个大因子）。
    exclude_limit_up_signal/trade、exclude_suspended：触发组剔除开关，
        分别对应 信号日(T)涨停 / 成交日(T+1)涨停 / 成交日停牌 三层剔除（详见 _test_one）。
    """
    if not factors:
        return []
    _ensure_qlib_init()

    if progress_cb:
        progress_cb(5, "解析股票池成分股...")

    n = max(1, int(label_horizon or 2))
    # label：信号日 t 的分数在 t+1（成交日 T）收盘买入，持有 n 个交易日到 t+n+1（T+n）收盘卖出。
    # （与回测 shift=1 一致：回测用 T-1 信号、T 收盘成交；分子/分母各后移一天，label 不混入
    #  信号日当日收益，单因子测试与回测口径完全对齐，无前视。）
    # 复权：price_adjust != none 时价格字段按复权价计算（比率类收益前/后复权等价）。
    pa = normalize_mode(price_adjust)
    label_expr = adjust_expr(f"Ref($close, -{n + 1})/Ref($close, -1) - 1", pa)

    # 因子列表中的表达式按序编号；重复表达式只加载一次（去重），统计时映射回原列
    ordered_exprs: List[str] = []
    col_names: List[str] = []
    col_map: List[str] = []  # 每个因子对应的去重后列名
    seen = {}
    for f in factors:
        e = f.get("expression", "")
        if not e:
            col_map.append(None)
            continue
        if e not in seen:
            seen[e] = len(ordered_exprs)
            ordered_exprs.append(e)
            col_names.append(f"F{len(ordered_exprs) - 1}")
        col_map.append(col_names[seen[e]])

    if not ordered_exprs:
        return [{**_test_one(pd.DataFrame(), f, ""), "error": "因子表达式为空"} for f in factors]

    from qlib.data import D

    try:
        instruments = _resolve_instruments(universe, start_date)
    except Exception as e:
        return [{**_test_one(pd.DataFrame(), f, ""), "error": f"股票池解析失败: {e}"} for f in factors]

    if not instruments:
        return [{**_test_one(pd.DataFrame(), f, ""), "error": "股票池为空（无成分股）"} for f in factors]

    if progress_cb:
        progress_cb(12, f"股票池 {len(instruments)} 只，计算特征数据...")

    # 加载全部因子（去重）+ label + 涨停/停牌判断所需基础字段。
    # 列名用唯一编号 F0..Fn / LABEL / CLOSE / CHANGE（信号日 T）/ T1_CLOSE / T1_CHANGE（成交日 T+1）。
    # T1 行情用 Ref 取未来一天（与 label 买入价 Ref($close,-1) 同源），成交日停牌时 T1_CLOSE 为 NaN。
    # 分小批加载（每批 2 个表达式），批间回调进度并检查"取消"。
    # 注意：D.features 单批内部不可中断，批越小取消响应越快（此前 5 个一批时，
    # 大股票池全区间的一批可能算很久，导致"取消半天没反应"）。
    # 复权：因子表达式与价格字段（$close 及其未来一日）按复权价加载；$change 为涨跌幅不受复权影响
    adj_exprs = [adjust_expr(e, pa) for e in ordered_exprs]
    fields = adj_exprs + [label_expr, adjust_expr("$close", pa), "$change",
                          adjust_expr("Ref($close, -1)", pa), "Ref($change, -1)"]
    col_names = col_names + ["LABEL", "CLOSE", "CHANGE", "T1_CLOSE", "T1_CHANGE"]
    batch_size = 2
    frames = []
    try:
        for k in range(0, len(fields), batch_size):
            if progress_cb:
                done = min(k + batch_size, len(fields))
                progress_cb(12 + 23 * (done / len(fields)), f"加载特征数据 {done}/{len(fields)}...")
            part = D.features(instruments, fields[k:k + batch_size], start_time=start_date, end_time=end_date)
            frames.append(part)
    except FactorTestCancelled:
        raise  # 用户取消：向上传递，由路由层标记 cancelled，不得吞掉
    except Exception as e:
        _dump_sft_error(e)
        return [{**_test_one(pd.DataFrame(), f, ""), "error": f"特征计算失败: {e}"} for f in factors]

    if not frames or all(f is None or len(f) == 0 for f in frames):
        return [{**_test_one(pd.DataFrame(), f, ""), "error": "特征计算无数据"} for f in factors]

    raw = frames[0] if len(frames) == 1 else pd.concat(frames, axis=1)
    df = raw.copy()
    df.columns = col_names

    if progress_cb:
        progress_cb(35, "特征数据就绪，逐个因子统计...")

    results = []
    total = len(factors)
    for i, (f, col_name) in enumerate(zip(factors, col_map), start=1):
        if progress_cb:
            progress_cb(35 + 60 * (i - 1) / total, f"测试因子 {i}/{total}: {f.get('name') or f.get('id')}")
        if cancelled is not None and cancelled():
            raise FactorTestCancelled()
        if col_name is None:
            results.append({**_test_one(pd.DataFrame(), f, ""), "error": "因子表达式为空"})
        else:
            results.append(
                _test_one(
                    df,
                    f,
                    col_name,
                    exclude_limit_up_signal=exclude_limit_up_signal,
                    exclude_limit_up_trade=exclude_limit_up_trade,
                    exclude_suspended=exclude_suspended,
                    cancelled=cancelled,
                )
            )
    if progress_cb:
        progress_cb(100, "测试完成")
    return results
