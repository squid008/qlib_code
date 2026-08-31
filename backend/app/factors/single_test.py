# -*- coding: utf-8 -*-
"""单因子测试：不训练模型，对单个因子做快速诊断。

针对稀疏 0/1 信号（自定义公式）与连续因子（Alpha158/360）做统一筛查：
  1. coverage        因子值非空比例
  2. nonzero_ratio   非零比例
  3. is_binary       是否为 0/1 二值信号
  4. trigger         触发组（>0.5）未来 N 日收益统计：样本数/均值/中位数
  5. not_trigger     未触发组（<=0.5）同口径
  6. diff            触发均值 - 未触发均值
  7. p_value         Mann-Whitney U 检验 p 值（二值信号）
  8. ic/rank_ic/icir/rank_icir   信息系数（连续因子主要指标）

所有因子一次 D.features 加载（多表达式并行计算），逐个统计。
"""
from __future__ import annotations

from typing import Dict, List, Optional

import numpy as np
import pandas as pd


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


def _limit_ratio(code: str) -> float:
    """按板块返回涨停幅度：主板 10%、创业板/科创板 20%、北交所 30%。"""
    c = str(code).upper()
    if c.startswith(("SH688", "SZ300", "SZ301")):
        return 0.20
    if c.startswith("BJ"):
        return 0.30
    return 0.10


def _mark_limit_up(s: pd.DataFrame, close_col: str = "CLOSE", change_col: str = "CHANGE") -> pd.Series:
    """返回布尔 Series：收盘价是否封住涨停板。

    涨停价 = round(昨收 * (1 + 板块涨停幅度), 2)，昨收由 close/(1+change) 反推。
    """
    if s is None or len(s) == 0:
        return pd.Series(dtype=bool)
    codes = s.index.get_level_values(0).astype(str)
    ratios = pd.Series([_limit_ratio(c) for c in codes], index=s.index)
    prev = s[close_col] / (1 + s[change_col])
    limit_price = np.floor(prev * (1 + ratios) * 100 + 0.5) / 100
    return (s[close_col] >= limit_price - 1e-6).fillna(False)


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


def _test_one(df: pd.DataFrame, factor: dict, col: str) -> dict:
    """测试单个因子列（df 含 col 与 LABEL 两列）。"""
    name = factor.get("name") or factor.get("id") or col
    result: dict = {
        "id": factor.get("id") or name,
        "name": name,
        "source": factor.get("source") or "custom",
        "expression": factor.get("expression") or "",
        "coverage": None,
        "nonzero_ratio": None,
        "is_binary": False,
        "trigger": None,
        "not_trigger": None,
        "diff": None,
        "p_value": None,
        "ic": None,
        "rank_ic": None,
        "icir": None,
        "rank_icir": None,
        "n_obs": 0,
        "limit_up_excluded": 0,
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
        pl = sub.rename(columns={col: "score", "LABEL": "label"})
        icr = _compute_ic_stats(pl)
        if icr:
            result.update(icr)
    except Exception:
        pass

    # 触发 vs 未触发
    trig = sub[sub[col] > 0.5]
    not_trig = sub[sub[col] <= 0.5]
    # 信号当日涨停的样本剔除（涨停买不到，收益不真实）
    lu_excluded = 0
    if len(trig) > 0 and "CLOSE" in df.columns and "CHANGE" in df.columns:
        trig_mask = _mark_limit_up(df.loc[trig.index])
        lu_excluded = int(trig_mask.sum())
        trig = trig[~trig_mask]
    result["limit_up_excluded"] = lu_excluded
    result["trigger"] = _stat_group(trig, lu_excluded)
    result["not_trigger"] = _stat_group(not_trig)
    if len(trig) > 0 and len(not_trig) > 0:
        result["diff"] = round(float(trig["LABEL"].mean() - not_trig["LABEL"].mean()), 6)
        if result["is_binary"] and len(trig) >= 5 and len(not_trig) >= 5:
            try:
                from scipy.stats import mannwhitneyu

                _, p = mannwhitneyu(trig["LABEL"], not_trig["LABEL"], alternative="two-sided")
                result["p_value"] = float(p)
            except Exception:
                result["p_value"] = None
    return result


def run_single_factor_test(
    universe: str,
    start_date: str,
    end_date: str,
    label_horizon: int = 2,
    factors: List[dict] = None,
    progress_cb=None,
) -> List[dict]:
    """执行单因子测试，返回每个因子的测试结果列表。

    factors: [{id, name, expression, source}]。expression 为 qlib 表达式。
    label: 未来 label_horizon 个交易日的收益（与回测 label 口径一致）。
    progress_cb: 可选回调 progress_cb(percent: float, message: str)，0-100。
        阶段：0-10 解析股票池 → 10-35 分批计算特征（批间可取消）→ 35-95 逐个因子统计 → 100 完成。
        取消：调用方通过 progress_cb 抛异常可中断任务（特征加载按批检查）。
    """
    if not factors:
        return []
    _ensure_qlib_init()

    if progress_cb:
        progress_cb(5, "解析股票池成分股...")

    n = max(1, int(label_horizon or 2))
    label_expr = f"Ref($close, -{n + 1})/Ref($close, -1) - 1"

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

    # 加载全部因子（去重）+ label + 涨停判断所需基础字段。列名用唯一编号 F0..Fn / LABEL / CLOSE / CHANGE
    # 分小批加载（每批 5 个表达式），批间回调进度，从而在特征计算阶段也能响应"取消"。
    fields = ordered_exprs + [label_expr, "$close", "$change"]
    col_names = col_names + ["LABEL", "CLOSE", "CHANGE"]
    batch_size = 5
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
        if col_name is None:
            results.append({**_test_one(pd.DataFrame(), f, ""), "error": "因子表达式为空"})
        else:
            results.append(_test_one(df, f, col_name))
    if progress_cb:
        progress_cb(100, "测试完成")
    return results
