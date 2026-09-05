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

from ..engine.limits import mark_limit_up, field_bin_available
from ..engine.adjust import adjust_expr, normalize_mode
from ..engine.feature_cache import _sr_wrap_expr


def _inst_codes(s: pd.DataFrame) -> pd.Series:
    """每行样本的 instrument 代码（大写，如 SZ300001/SH688001/BJ430047）；单标的（无 instrument 层）返回空串。"""
    if isinstance(s.index, pd.MultiIndex):
        return s.index.get_level_values(0).astype(str).str.upper()
    return pd.Series([""] * len(s), index=s.index)


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


def _stat_group(g: pd.DataFrame, lu_t: int = 0, lu_t1: int = 0, susp: int = 0, extra: int = 0) -> Optional[dict]:
    """触发/未触发组的收益统计（含剔除明细，供前端逐项展示）。"""
    if g is None or len(g) == 0:
        return None
    return {
        "count": int(len(g)),
        "mean_ret": round(float(g["LABEL"].mean()), 6),
        "median_ret": round(float(g["LABEL"].median()), 6),
        "limit_up_excluded": int(lu_t + lu_t1 + susp),  # 总剔除数（信号日涨停 + 成交日涨停 + 停牌）
        "limit_up_excluded_t": int(lu_t),               # 信号日（T）涨停剔除数
        "limit_up_excluded_t1": int(lu_t1),             # 成交日（T+1）涨停剔除数
        "suspended_excluded": int(susp),                # 成交日停牌/无行情剔除数
        "extra_excluded": int(extra),                   # ST(T+1)/创业板/科创板剔除数（勾选时）
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
    exclude_st_t1: bool = False,       # 剔除成交日 T+1 处于 ST/*ST/退市整理 的样本（日截面）
    exclude_stock_gem: bool = False,   # 剔除创业板
    exclude_stock_kcb: bool = False,   # 剔除科创板
    cancelled=None,  # 取消检查回调：返回 True 表示用户已取消，在重计算步骤间调用（可空）
) -> dict:
    """测试单个因子列（df 含 col 与 LABEL 两列）。

    df 还需含 CLOSE/CHANGE（信号日 T 行情）与 T1_CLOSE/T1_CHANGE（成交日 T+1 行情，Ref 取未来一天）。
    剔除开关（信号组与非信号组应用相同规则，保证两组样本口径一致，diff 公平）：
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
        "source_formula": factor.get("source_formula") or "",  # 用户原文（仅展示用，随结果回传）
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
        "limit_up_excluded": 0,       # 信号组总剔除数（信号日涨停 + 成交日涨停 + 停牌）
        "limit_up_excluded_t": 0,     # 信号组信号日（T）涨停剔除数
        "limit_up_excluded_t1": 0,    # 信号组成交日（T+1）涨停剔除数
        "suspended_excluded": 0,      # 信号组成交日停牌/无行情剔除数
        "not_limit_up_excluded": 0,   # 非信号组总剔除数（与信号组同口径）
        "not_limit_up_excluded_t": 0,  # 非信号组信号日（T）涨停剔除数
        "not_limit_up_excluded_t1": 0,  # 非信号组成交日（T+1）涨停剔除数
        "not_suspended_excluded": 0,  # 非信号组成交日停牌/无行情剔除数
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
    # 剔除（信号组与非信号组应用相同开关，保证两组样本口径一致，diff 才公平）：
    #   1) 信号日（T）涨停：选股过滤（涨停追高风险，信号日收盘后已知，无前视）
    #   2) 成交日（T+1）涨停：真实撮合约束（调仓日封板买不到），与回测 BoardAwareExchange 口径一致
    #   3) 成交日（T+1）停牌/无行情：同样买不到

    def _exclude(g: pd.DataFrame):
        """对一组样本应用剔除开关，返回 (组, T涨停数, T+1涨停数, 停牌数, ST/板块剔除数)。

        涨停判定优先交易所标签列（df 含 LIMIT_UP/T1_LIMIT_UP 时 mark_limit_up 自动走标签，
        覆盖 ST 5% / 退市整理 10% / 创业科创 20%）；ST/板块剔除只用 T+1 当日已发布状态（日截面，无未来函数）。
        """
        lu_t = lu_t1 = susp = extra = 0
        if len(g) > 0:
            if exclude_limit_up_signal and "CLOSE" in df.columns and "CHANGE" in df.columns:
                mask = mark_limit_up(df.loc[g.index], "CLOSE", "CHANGE")
                lu_t = int(mask.sum())
                g = g[~mask]
            if exclude_limit_up_trade and "T1_CLOSE" in df.columns and "T1_CHANGE" in df.columns:
                mask = mark_limit_up(df.loc[g.index], "T1_CLOSE", "T1_CHANGE")
                lu_t1 = int(mask.sum())
                g = g[~mask]
            if exclude_suspended and "T1_CLOSE" in df.columns:
                mask = df.loc[g.index, "T1_CLOSE"].isna()
                susp = int(mask.sum())
                g = g[~mask]
            # ST/板块剔除：日截面（T+1 当日状态/所属板块），无未来函数
            if (exclude_st_t1 or exclude_stock_gem or exclude_stock_kcb) and len(g):
                codes = _inst_codes(df.loc[g.index])
                keep = pd.Series(True, index=g.index)
                if exclude_st_t1 and "T1_IS_ST" in df.columns:
                    keep &= ~(df.loc[g.index, "T1_IS_ST"] > 0.5)
                if exclude_stock_gem:
                    keep &= ~codes.str.startswith("SZ30")
                if exclude_stock_kcb:
                    keep &= ~codes.str.startswith("SH688")
                extra = int((~keep).sum())
                g = g[keep]
        return g, lu_t, lu_t1, susp, extra

    trig, t_lu_t, t_lu_t1, t_susp, t_extra = _exclude(trig)
    not_trig, n_lu_t, n_lu_t1, n_susp, n_extra = _exclude(not_trig)
    result["limit_up_excluded"] = t_lu_t + t_lu_t1 + t_susp
    result["limit_up_excluded_t"] = t_lu_t
    result["limit_up_excluded_t1"] = t_lu_t1
    result["suspended_excluded"] = t_susp
    result["not_limit_up_excluded"] = n_lu_t + n_lu_t1 + n_susp
    result["not_limit_up_excluded_t"] = n_lu_t
    result["not_limit_up_excluded_t1"] = n_lu_t1
    result["not_suspended_excluded"] = n_susp
    result["trigger"] = _stat_group(trig, t_lu_t, t_lu_t1, t_susp, t_extra)
    result["not_trigger"] = _stat_group(not_trig, n_lu_t, n_lu_t1, n_susp, n_extra)
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

    # 5 组分位收益（连续因子）：按每日横截面均分 5 组，每组收益为【日截面口径】
    # （每天先取组内样本均值，再对所有参与交易日取平均）——与 0/1 双柱、日配对检验
    # 一致，避免少数日子的样本集中主导组均值。用于识别 U 型/倒 U 型等非线性关系
    # （单调关系 IC/RankIC 已足够，U 型/倒 U 型两端收益相近、diff≈0，必须看全部分组形态）。
    # 注意：分位样本同样应用剔除开关（真实价涨停/停牌，与触发/非触发组同口径，
    # 见上方 _exclude 语义），否则分位收益含"买不到"样本，与触发收益口径不一致。
    if not result["is_binary"] and len(sub) >= 100:
        try:
            if cancelled is not None and cancelled():
                raise FactorTestCancelled()
            meta_cols = [c for c in ("CLOSE", "CHANGE", "T1_CLOSE", "T1_CHANGE",
                                     "LIMIT_UP", "T1_LIMIT_UP", "IS_ST", "T1_IS_ST")
                         if c in df.columns]
            meta = df.loc[sub.index, meta_cols]
            excl = pd.Series(False, index=sub.index)
            if exclude_limit_up_signal:
                excl = excl | mark_limit_up(meta, "CLOSE", "CHANGE")
            if exclude_limit_up_trade:
                excl = excl | mark_limit_up(meta, "T1_CLOSE", "T1_CHANGE")
            if exclude_suspended:
                excl = excl | meta["T1_CLOSE"].isna()
            # ST/板块剔除（与触发/非触发组同口径，见 _exclude）；无 is_st 标签时自动跳过
            if exclude_st_t1 and "T1_IS_ST" in meta.columns:
                excl = excl | (meta["T1_IS_ST"] > 0.5)
            if exclude_stock_gem or exclude_stock_kcb:
                codes = _inst_codes(meta)
                if exclude_stock_gem:
                    excl = excl | codes.str.startswith("SZ30")
                if exclude_stock_kcb:
                    excl = excl | codes.str.startswith("SH688")
            quint = sub.loc[~excl]
            dt_pos = quint.index.names.index("datetime")
            tmp = quint[[col, "LABEL"]].copy()
            tmp["_q"] = tmp.groupby(level=dt_pos)[col].transform(
                lambda x: pd.qcut(x.rank(method="first"), 5, labels=False, duplicates="drop") + 1
            )
            tmp = tmp.dropna(subset=["_q"])
            groups = []
            for q, g in tmp.groupby("_q"):
                # 日截面：每天组内样本先取均值 → 对所有参与交易日再平均
                dm = g.groupby(level=dt_pos)["LABEL"].mean()
                groups.append({
                    "quantile": int(q),
                    "count": int(len(g)),                        # 该组剔除后样本总数（观测数）
                    "n_days": int(len(dm)),                      # 参与交易日数
                    "mean_ret": round(float(dm.mean()), 6),      # 日截面平均收益
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
    freeze_suspended_price: bool = True,   # 未来收益对停牌日采用"停牌前收盘价冻结"，把停牌/退市
    #   崩盘的真实亏损计入（对齐聚宽/通达信因子诊断口径；默认 True=冻结，见下）
    suspend_remove: bool = True,           # 信号计算的停牌行语义：True=SR删行（益盟/通达信
    #   "无停牌行"，与回测特征一致，默认）；False=停牌日保留 NaN 占位（qlib 官方/聚宽 notebook
    #   口径）。只影响因子信号，不影响 label 与涨停/停牌元数据。
    exclude_st_t1: bool = False,           # 剔除成交日（T+1）处于 ST/*ST/退市整理 的样本（日截面，
    #   用 T+1 当日状态判定可买，无未来函数；源 bundle st_stock_days）
    exclude_stock_gem: bool = False,       # 剔除创业板（SZ30 段，20% 涨跌幅）
    exclude_stock_kcb: bool = False,       # 剔除科创板（SH688，20% 涨跌幅）
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
    # 复权：数据 $close 原生为后复权价。none→真实价($close/$factor)；forward/backward→
    # 原生后复权价（比率类收益前/后复权等价，见 engine/adjust.py docstring）。
    pa = normalize_mode(price_adjust)
    label_expr = adjust_expr(f"Ref($close, -{n + 1})/Ref($close, -1) - 1", pa)

    # freeze_suspended_price（口径 B，对齐聚宽）：未来收益需在"停牌日价格冻结（前收）"的行情上
    # 做 per-stock shift，因此行情要加载到 end_date 之后 (n+1) 个交易日（供 shift 取未来行）。
    load_end = end_date
    if freeze_suspended_price:
        try:
            from qlib.data import D as _D
            cal = _D.calendar()
            cal_ts = pd.to_datetime(cal)
            pos = int((cal_ts <= pd.Timestamp(end_date)).sum())
            load_end = str(cal_ts[min(pos + n + 3, len(cal_ts) - 1)].date())
        except Exception:
            load_end = end_date

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
    # 复权（2026-09-02 修正，见 engine/adjust.py docstring）：
    #   数据 $close 原生为【后复权价】(=真实价×$factor)。因子表达式与 label 按复权模式
    #   计算（none→真实价 close/$factor；forward/backward→原生后复权价 close，比率等价）。
    #   而【涨停/停牌判定字段（CLOSE/CHANGE/T1_CLOSE/T1_CHANGE）恒用真实价
    #   ($close/$factor 与 $change，均东财"不复权"口径且同源)】——涨停价由交易所按真实
    #   价格确定，与复权无关；$change 反推真实昨收即可精确定涨停，除权日不受 factor 跳变影响。
    # 因子表达式是否包 SR（停牌删行→益盟语义）由 suspend_remove 开关控制：
    #   True=SR删行（默认，复牌初期因子连续，信号判断与益盟/回测特征一致）；
    #   False=停牌日保留 NaN 占位（qlib 官方原生 / 同事聚宽 notebook 口径，对账用）。
    # label / 涨停停牌判定元数据（CLOSE/CHANGE/T1_*）不包——它们必须保留停牌 NaN 行
    # （T1 为 NaN = 成交日停牌，是剔除/涨停判断的依据，删行会破坏该逻辑）。
    if suspend_remove:
        adj_exprs = [_sr_wrap_expr(adjust_expr(e, pa)) for e in ordered_exprs]
    else:
        adj_exprs = [adjust_expr(e, pa) for e in ordered_exprs]
    # 交易所标签字段（涨跌停价 / ST）：本机数据有 dump 才加载（dump_states.py）；
    # 缺失时自动降级不崩——涨停/跌停判定回退"昨收×板块幅度"倒推，ST 剔除开关因无列自动失效。
    base_fields = ["$close/$factor", "$change", "Ref($close/$factor, -1)", "Ref($change, -1)"]
    base_names = ["LABEL", "CLOSE", "CHANGE", "T1_CLOSE", "T1_CHANGE"]
    tag_fields: List[str] = []
    tag_names: List[str] = []
    if field_bin_available("limit_up") and field_bin_available("limit_down"):
        tag_fields += ["$limit_up", "$limit_down", "Ref($limit_up, -1)", "Ref($limit_down, -1)"]
        tag_names += ["LIMIT_UP", "LIMIT_DOWN", "T1_LIMIT_UP", "T1_LIMIT_DOWN"]
    if field_bin_available("is_st"):
        tag_fields += ["$is_st", "Ref($is_st, -1)"]
        tag_names += ["IS_ST", "T1_IS_ST"]
    fields = tuple(adj_exprs) + (label_expr,) + tuple(base_fields) + tuple(tag_fields)
    col_names = col_names + base_names + tag_names
    batch_size = 2
    frames = []
    try:
        for k in range(0, len(fields), batch_size):
            if progress_cb:
                done = min(k + batch_size, len(fields))
                progress_cb(12 + 23 * (done / len(fields)), f"加载特征数据 {done}/{len(fields)}...")
            part = D.features(instruments, fields[k:k + batch_size], start_time=start_date, end_time=load_end)
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

    if freeze_suspended_price:
        # 口径 B（对齐聚宽）：未来收益 label 对停牌/退市尾段采用"收盘价冻结"——
        # 1) 窗口内停牌：CLOSE 按股票 ffill（停牌日=前收冻结），再 per-stock shift；
        # 2) 退市/长期停牌到数据末（未来不足 (n+1) 个交易日）：以该股"最后收盘价"冻结
        #    作为退出价结算（对齐聚宽用退市整理末价计真实亏损，而非因 Ref 落空被剔除）。
        # 原 qlib Ref label 有值的样本保持原值（正常口径不受影响）。
        try:
            inst_lv = df.index.names.index("instrument")
            dt_lv = df.index.names.index("datetime")
            cf = df.groupby(level=inst_lv)["CLOSE"].ffill()
            # 该股最后收盘价（每行广播到组末；601258 退市尾段=退市整理末价）
            last_c = cf.groupby(level=inst_lv).transform("last")
            exit_px = cf.groupby(level=inst_lv).shift(-(n + 1))
            exit_px = exit_px.where(exit_px.notna(), last_c)  # 未来不足 → 末日价冻结
            entry_px = cf.groupby(level=inst_lv).shift(-1)
            label_ff = exit_px / entry_px - 1
            df["LABEL"] = df["LABEL"].where(df["LABEL"].notna(), label_ff)
            sig_end = pd.Timestamp(end_date)
            df = df[df.index.get_level_values(dt_lv) <= sig_end]
        except Exception as e:
            _dump_sft_error(e)
            return [{**_test_one(pd.DataFrame(), f, ""), "error": f"冻结价 label 计算失败: {e}"} for f in factors]

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
                    exclude_st_t1=exclude_st_t1,
                    exclude_stock_gem=exclude_stock_gem,
                    exclude_stock_kcb=exclude_stock_kcb,
                    cancelled=cancelled,
                )
            )
    if progress_cb:
        progress_cb(100, "测试完成")
    return results
