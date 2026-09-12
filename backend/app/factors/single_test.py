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

import re
from collections import OrderedDict
from typing import Dict, List, Optional

import numpy as np
import pandas as pd

from ..engine.limits import mark_limit_up, field_bin_available
from ..engine.adjust import adjust_expr, normalize_mode
from ..engine.feature_cache import _sr_wrap_expr
from .event_study import build_event_stats, compute_baseline_curves


def _inst_codes(s: pd.DataFrame) -> pd.Series:
    """每行样本的 instrument 代码（大写，如 SZ300001/SH688001/BJ430047）；单标的（无 instrument 层）返回空串。

    v1.18.33 性能：原实现逐行 `.astype(str).str.upper()` —— 全 A（760 万行）单次调用实测
    ~4~5s（cProfile：`str.upper` + `object_array._str_map` 合计 832 万次调用）。改为用
    `pd.factorize` 取 unique 标的（约 5000 个）做 str/upper，再用整数 codes 回填，
    逐元素语义与原来完全一致（含 NaN → 'NAN'）。
    """
    if isinstance(s.index, pd.MultiIndex):
        lev = s.index.get_level_values(0)
        codes, uniques = pd.factorize(lev, sort=False)
        ups = np.asarray([str(u).upper() for u in uniques], dtype=object)
        vals = ups[codes] if codes.size else np.empty(0, dtype=object)
        return pd.Series(vals, index=s.index)
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


def _bad_date_arg(start_date, end_date) -> str:
    """校验区间日期是 YYYY-MM-DD 且**真实存在**（如 2026-06-31 非法；6 月只有 30 天）。

    返回可读的中文错误信息，全部合法返回 ""。非法日期若放行，会在后续 pandas 解析处
    报成难懂的「特征计算失败: day is out of range for month: 2026-06-31」（v1.18.24 修复）。
    """
    for lbl, v in (("开始日期", start_date), ("结束日期", end_date)):
        s = str(v or "").strip()
        if not re.match(r"^\d{4}-\d{2}-\d{2}$", s):
            return f"{lbl}格式无效：{v}（需为 YYYY-MM-DD）"
        try:
            pd.Timestamp(s)
        except Exception:
            return f"{lbl}无效：{v}（该日期不存在，请检查年/月/日，如 6 月没有 31 日）"
    return ""


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


def _decile_matrix(dms: dict) -> Optional[pd.DataFrame]:
    """把各分位的逐日截面均值序列对齐成矩阵 `(n_days, n_groups)`（列 = 组号升序）。

    `dms` —— `{组号: 逐日截面均值 Series}`，与 `quintile_ret` 的 `mean_ret` **同源**
    （同一次 `groupby(level="datetime")["LABEL"].mean()`）。
    取**各分组都有值的交易日**（`dropna`）；可用组 < 2 或有效日 < 2 ⇒ 返回 `None`。

    ⚠ 这个矩阵是**分位曲线**与**默认档（最强分位组）曲线**的**唯一来源** —— 两条路径必须
    用同一份对齐结果，否则日轴/基准不一致（此前 `topk_curves` 与 `quantile_curves` 各自
    对齐过一次，靠断言兜）。
    """
    if not dms or len(dms) < 2:
        return None
    mat = pd.DataFrame({int(q): dm for q, dm in dms.items()}).sort_index().dropna()
    # ⚠ 必须显式转 float64：面板 `LABEL` 是 **float32**，`Series.cumsum()` 会在 float32 上
    #   逐步累积（1211 步、量级 ~1e-2 ⇒ 误差可达 1e-4，且与 `topk_curves` 的 float64 路径
    #   **不一致**）。统一转 float64 后两条路径逐位相同（实测自检②当场抓到）。
    mat = mat.astype(np.float64)
    return mat if len(mat) >= 2 else None


def _quantile_curves(mat: pd.DataFrame) -> dict:
    """由**调仓期口径**的分位收益矩阵产出分位累计收益曲线（十分位图数据）。

    `mat` —— 行 = **调仓日**（每 `rebalance_period` 个交易日一个）、列 = 分位组；
             单元格 = 该调仓日（信号日 T，T+1 买入）该组样本的 `LABEL` 均值
             = **持有到下一个调仓日的组合收益**（默认调仓期 = 预测周期 h ⇒ 「按预测周期调仓换股」）。

    · `groups[].cum`  = 该组逐期收益 cumsum（**算术累加**，与 `topk_curves` 无成本档同口径）；
    · `groups[].mean_ret` / `excess` = 各期收益均值 / 相对基准的超额；`baseline_mean` =
      各组均值的平均（等频分组 ⇒ 等于全样本均值，故 `argmax(mean_ret) ≡ argmax(excess)`）；
    · `best_quantile` / `worst_quantile` = 最强 / 最弱分位组（方向由数据定，不硬编码）；
    · **多空 = 最强组 − 最弱组**（⚠ 实盘不可实现，仅作有效性参考）。
      ⚠ 此前误用「第 1 组 − 最后一组」⇒ **负向因子（如负市值对数）的多空曲线朝下、方向反了**，
      用户 2026-09-12 发现并纠正。
    """
    qs = [int(q) for q in mat.columns]
    means = mat.mean()
    base = float(means.mean())          # 等频分组 ⇒ 各组均值之平均 = 全样本均值（同口径基准）
    best, worst = int(means.idxmax()), int(means.idxmin())
    ls = (mat[best] - mat[worst]).cumsum()
    return {
        "n_groups": len(qs),
        "n_periods": int(len(mat)),
        "dates": [pd.Timestamp(d).strftime("%Y-%m-%d") for d in mat.index],
        "baseline_mean": round(base, 6),
        "best_quantile": best,
        "worst_quantile": worst,
        "groups": [{"quantile": q,
                    "mean_ret": round(float(means[q]), 6),
                    "excess": round(float(means[q]) - base, 6),
                    "cum": [round(float(x), 6) for x in mat[q].cumsum()]}
                   for q in qs],
        "long_short": {"quantile": [best, worst],
                       "cum": [round(float(x), 6) for x in ls]},
    }


# 事件研究（v1.18.7）：0/1 稀疏信号顺带计算的最长持有交易日
EVENT_MAX_K = 40


def _event_px_wide(df_full: pd.DataFrame, trig_index, inst_lv: int):
    """由未裁剪面板的 PX 列构造触发标的的价格宽表（index=交易日, columns=标的，停牌 ffill）。

    PX 列口径 = `adjust_expr("$close")`，与 label 的收益口径一致（forward/backward
    用后复权价、none 用真实价，可选按分取整）。
    """
    try:
        px = df_full["PX"]
        codes = trig_index.get_level_values(inst_lv).unique()
        mask = px.index.get_level_values(inst_lv).isin(codes)
        sub = px[mask]
        if len(sub) == 0:
            return None
        return sub.unstack(level=inst_lv).sort_index().ffill()
    except Exception:
        return None


# 全样本 PX 宽表进程级缓存（v1.18.33）：单份约 50MB（≈1300 日 × 5000 只）。同一区间/
# 股票池连续跑多个单因子测试（或同一任务内多个 0/1 因子）时复用，避免每次重建
# （全 A 实测 3.3s/次）。只保留最近一份；区间/规模/首值指纹变化即自然失效。
_FULL_WIDE_CACHE: "OrderedDict[tuple, pd.DataFrame]" = OrderedDict()
_FULL_WIDE_MAX = 1


def clear_full_wide_cache() -> None:
    """清空全样本 PX 宽表缓存（数据更新 / 测试用）。"""
    _FULL_WIDE_CACHE.clear()


def _full_wide_key(df_full: pd.DataFrame, inst_lv: int):
    """宽表缓存键：形状 + 区间端点 + 首值指纹（同一面板必然一致，数据变动即失效）。"""
    try:
        idx = df_full.index
        dt_lv = idx.names.index("datetime") if "datetime" in idx.names else -1
        dt = idx.get_level_values(dt_lv)
        n_inst = len(idx.levels[inst_lv]) if isinstance(idx, pd.MultiIndex) else 0
        px = df_full["PX"]
        sample = float(px.iloc[0]) if len(px) else float("nan")
        return (len(idx), n_inst, str(dt[0]), str(dt[-1]), inst_lv, sample)
    except Exception:
        return None


def _event_px_from_full(full_wide: pd.DataFrame, trig_index, inst_lv: int):
    """由**全样本宽表**按触发标的取列子集，替代 `_event_px_wide` 的 mask+unstack。

    v1.18.35 性能：同一因子的触发股宽表与全样本宽表内容一致（unstack/ffill 均逐列独立），
    从已构造好的全样本宽表取列即可（列选择 ~0.05s），省掉 `px[mask]` 布尔索引 + 二次
    unstack + sort_index + ffill（全 A「趋势顶底」实测 3.86s）。数值等价：列集合相同、
    列顺序同为字典序（两者都 sort_index 过），下游 `_align_returns` 按列名取值。
    """
    try:
        if full_wide is None or len(full_wide) == 0:
            return None
        codes = trig_index.get_level_values(inst_lv).unique()
        want = pd.Index([str(c).upper() for c in codes])
        cols = full_wide.columns.intersection(want)
        if len(cols) == 0:
            return None
        return full_wide[cols]
    except Exception:
        return None


def _full_px_wide(df_full: pd.DataFrame, inst_lv: int):
    """全样本 PX 宽表（index=交易日, columns=标的）—— 事件研究「基准（未触发组）」曲线用。

    v1.18.33：加进程级缓存（_FULL_WIDE_CACHE）。**返回值仅供只读使用**，调用方不得原地修改。
    """
    key = _full_wide_key(df_full, inst_lv)
    if key is not None:
        hit = _FULL_WIDE_CACHE.get(key)
        if hit is not None:
            _FULL_WIDE_CACHE.move_to_end(key)
            return hit
    try:
        w = df_full["PX"].unstack(level=inst_lv).sort_index().ffill()
    except Exception:
        return None
    if key is not None and w is not None:
        _FULL_WIDE_CACHE[key] = w
        while len(_FULL_WIDE_CACHE) > _FULL_WIDE_MAX:
            _FULL_WIDE_CACHE.popitem(last=False)
    return w


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
    trig_index_out: Optional[list] = None,  # 出参：回传触发样本索引（供事件研究复用，可空）
    quantiles: int = 10,               # 连续因子分位组数（默认 10=十分位；5=旧五分位，保留兼容）
    rebalance_period: Optional[int] = None,  # 调仓期（交易日）；None ⇒ **跟随预测周期 horizon**
    #                                   （= 「用预测周期调仓换股」的默认口径，v1.18.45 起）
    topk_list: Optional[list] = None,  # 明细曲线要算的 K：≤1 视为「日均只数的百分比」（0.1=10%），
    #                                   >1 视为只数；None ⇒ 只算默认档（10%）。默认档恒算且排在最前
    horizon: Optional[int] = None,     # 该次统计的预测周期 h（用于「调仓期默认 = h」；可空）
    bench_close: Optional[pd.DataFrame] = None,  # 指数收盘宽表（可切换基准；可空=不画基准）
    bench_default: Optional[str] = None,         # 默认基准代码（按股票池映射，见 benchmark_curves）
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
        # 逐日截面均值：信号组/非信号组各自"配对日"（两组同日都有样本）日均未来收益，
        # 与 daily_diff 同集合自洽（daily_diff = daily_trig_mean - daily_not_mean）。
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

    # v1.18.34 性能：位置索引化。原实现在 _exclude / 分位段里对百万行做 `df.loc[g.index]`
    # （MultiIndex 对齐）——全 A 单因子 `_test_one`（20s）cProfile 实测：`Index._get_indexer`
    # 2.7s + `ndarray.take` 2.5s + `_take_nd_ndarray` 1.2s + `MultiIndex.equals` 3.1s。
    # 现改为：先用布尔掩码定位 df 行位置，后续一律 `Index.take(pos)` / numpy 花式索引
    # （位置操作，无 hash 查找/对齐），并把所需行情与标签列预取为 numpy 视图。
    _NR = {
        c: df[c].to_numpy(copy=False)
        for c in (col, "LABEL", "CLOSE", "CHANGE", "T1_CLOSE", "T1_CHANGE",
                  "T1_IS_ST", "LIMIT_UP", "T1_LIMIT_UP", "IS_ST")
        if c in df.columns
    }
    _sub_pos = np.nonzero((~pd.isna(_NR[col])) & (~pd.isna(_NR["LABEL"])))[0]

    def _frame(pos, cols):
        """按 df 行位置取列构造小 DataFrame（index 与等价的 df.loc[...] 一致）。"""
        return pd.DataFrame({c: _NR[c][pos] for c in cols}, index=df.index.take(pos))

    sub = _frame(_sub_pos, (col, "LABEL"))
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

    # IC / RankIC / ICIR（连续因子专用，v1.18.35）
    # 0/1 信号只有 0 与 1 两档取值：Pearson IC 退化为"点二列相关"，RankIC 更谈不上
    # 排序（秩只有两档）、ICIR 亦无解释意义；且 0/1 信号的结论本就以事件研究
    # （中位数 / 绝对收益胜率 / 日配对超额）为准。故跳过计算 —— 全 A 约省 4s/因子，
    # 代价是 0/1 不再参与「方向矛盾」判定（该分支依赖 IC/ICIR）。
    if not result["is_binary"]:
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
    _sv = _NR[col][_sub_pos]
    if result["is_binary"]:
        _m_trig = _sv > 0.5
        _m_not = _sv <= 0.5
        result["grouping"] = "binary"
    else:
        q_hi = float(sub[col].quantile(0.8))
        q_lo = float(sub[col].quantile(0.2))
        _m_trig = _sv >= q_hi
        _m_not = _sv <= q_lo
        result["grouping"] = "quantile"
    # 组样本以「df 行位置」表示，后续剔除/构造都在位置层面进行（无索引对齐）
    trig_pos = _sub_pos[np.nonzero(_m_trig)[0]]
    not_pos = _sub_pos[np.nonzero(_m_not)[0]]
    # 剔除（信号组与非信号组应用相同开关，保证两组样本口径一致，diff 才公平）：
    #   1) 信号日（T）涨停：选股过滤（涨停追高风险，信号日收盘后已知，无前视）
    #   2) 成交日（T+1）涨停：真实撮合约束（调仓日封板买不到），与回测 BoardAwareExchange 口径一致
    #   3) 成交日（T+1）停牌/无行情：同样买不到

    def _exclude(pos: np.ndarray):
        """对一组样本（df 行位置）应用剔除开关。

        返回 (pos, T涨停数, T+1涨停数, 停牌数, ST/板块剔除数)。
        涨停判定优先交易所标签列（df 含 LIMIT_UP/T1_LIMIT_UP 时 mark_limit_up 自动走标签，
        覆盖 ST 5% / 退市整理 10% / 创业科创 20%）；ST/板块剔除只用 T+1 当日已发布状态（日截面，无未来函数）。
        实现上按位置切片（`_frame` / numpy 掩码），避免原 `df.loc[g.index]` 的 MultiIndex 对齐。
        """
        lu_t = lu_t1 = susp = extra = 0
        if pos.size:
            if exclude_limit_up_signal and "CLOSE" in df.columns and "CHANGE" in df.columns:
                mask = mark_limit_up(_frame(pos, ("CLOSE", "CHANGE", "LIMIT_UP")),
                                     "CLOSE", "CHANGE").to_numpy()
                lu_t = int(mask.sum())
                pos = pos[~mask]
            if exclude_limit_up_trade and "T1_CLOSE" in df.columns and "T1_CHANGE" in df.columns:
                mask = mark_limit_up(_frame(pos, ("T1_CLOSE", "T1_CHANGE", "LIMIT_UP", "T1_LIMIT_UP")),
                                     "T1_CLOSE", "T1_CHANGE").to_numpy()
                lu_t1 = int(mask.sum())
                pos = pos[~mask]
            if exclude_suspended and "T1_CLOSE" in df.columns:
                mask = pd.isna(_NR["T1_CLOSE"][pos])
                susp = int(mask.sum())
                pos = pos[~mask]
            # ST/板块剔除：日截面（T+1 当日状态/所属板块），无未来函数
            if (exclude_st_t1 or exclude_stock_gem or exclude_stock_kcb) and pos.size:
                keep = np.ones(pos.size, dtype=bool)
                if exclude_st_t1 and "T1_IS_ST" in df.columns:
                    keep &= ~(_NR["T1_IS_ST"][pos] > 0.5)
                if exclude_stock_gem or exclude_stock_kcb:
                    codes = _inst_codes(pd.DataFrame(index=df.index.take(pos)))
                    if exclude_stock_gem:
                        keep &= ~codes.str.startswith("SZ30").to_numpy()
                    if exclude_stock_kcb:
                        keep &= ~codes.str.startswith("SH688").to_numpy()
                extra = int((~keep).sum())
                pos = pos[keep]
        return pos, lu_t, lu_t1, susp, extra

    trig_pos, t_lu_t, t_lu_t1, t_susp, t_extra = _exclude(trig_pos)
    not_pos, n_lu_t, n_lu_t1, n_susp, n_extra = _exclude(not_pos)
    trig = _frame(trig_pos, (col, "LABEL"))
    not_trig = _frame(not_pos, (col, "LABEL"))
    # 触发样本索引出参（可选）：供调用方顺带做事件研究（见 run_single_factor_tests）
    if trig_index_out is not None:
        try:
            trig_index_out.append(trig.index)
        except Exception:
            trig_index_out.append(None)
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
            daily = (daily_trig - daily_not).dropna()
            # 诊断落盘（默认关闭）：设环境变量 QLIB_SFT_DUMP_DAILY=<目录> 时，把该因子的
            # 日差值序列（每日 触发组截面均值 − 未触发组截面均值）写成 CSV，供"贡献度"
            # 分析（判断日差均值是否被少数交易日撑起）。只在诊断时开启，不影响任何统计量。
            try:
                import os as _os_dump
                _dump_dir = _os_dump.environ.get("QLIB_SFT_DUMP_DAILY", "").strip()
                if _dump_dir:
                    _os_dump.makedirs(_dump_dir, exist_ok=True)
                    _safe = "".join(c if (c.isalnum() or c in "._-") else "_" for c in str(name))[:60]
                    pd.DataFrame({
                        "daily_diff": daily,
                        "daily_trig": daily_trig.reindex(daily.index),
                        "daily_not": daily_not.reindex(daily.index),
                    }).rename_axis("date").to_csv(
                        _os_dump.path.join(_dump_dir, "daily_%s.csv" % _safe),
                        encoding="utf-8-sig")
                    # 触发样本明细（剔除后，通常只有几百~几千行）：用于定位极端值来源
                    try:
                        trig.reset_index().to_csv(
                            _os_dump.path.join(_dump_dir, "trig_%s.csv" % _safe),
                            encoding="utf-8-sig")
                    except Exception:
                        pass
            except Exception:
                pass
            # 口径 = 配对日（科学口径）：触发/非触发组的日截面均值都只在"当天两组都有
            # 样本"的配对日上计算。若按各自独立全集（daily_trig 只统计有触发的日、
            # daily_not 统计≈全部交易日），分母不同、两值直接相减无意义——这正是用户
            # 最初困惑"触发 0.717% − 非触发 0.037% ≠ 配对差 0.221%"的来源：0.037% 是
            # 被大量无信号平淡日稀释的全期非触发均值，而触发信号集中发生在非触发组
            # 当天也普涨的日子（同日非触发实际 ~0.5%）。统一到配对日后三者自洽且公平：
            #   daily_diff == daily_trig_mean - daily_not_mean（同一日期集合的线性平均）。
            # 注：早期对账脚本（jq notebook）的 daily_not 用全部交易日 → 数值更大
            # （如趋势顶底 0.9594% vs 配对日 0.871%），那是分母含无信号日的口径，作为
            # "触发 vs 非触发 同日净超额"的参照并不严谨；本处采用更科学的配对口径。
            _pair = daily.index
            if len(_pair) > 0:
                result["daily_trig_mean"] = round(float(daily_trig.reindex(_pair).mean()), 6)
                result["daily_not_mean"] = round(float(daily_not.reindex(_pair).mean()), 6)
            else:
                result["daily_trig_mean"] = round(float(daily_trig.mean()), 6)
                result["daily_not_mean"] = round(float(daily_not.mean()), 6)
            # 配对日 ≥1：日差与配对日数即真实值（n=1 时日差=该单日两组日截面均值差，
            # 仍自洽）；t/胜率/HAC/自相关等统计推断仍要求 ≥2（单配对日无统计意义，留空）。
            if len(daily) >= 1:
                x = daily.to_numpy(dtype=float)
                result["daily_diff"] = round(float(x.mean()), 6)
                result["daily_n"] = int(len(x))
            if len(daily) >= 2:
                from scipy.stats import ttest_1samp

                x = daily.to_numpy(dtype=float)
                t_stat, _ = ttest_1samp(x, 0)
                result["daily_t"] = round(float(t_stat), 4)
                result["daily_win"] = round(float((x > 0).mean()), 4)
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
            meta = _frame(_sub_pos, meta_cols)
            excl = pd.Series(False, index=meta.index)
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
            quint = sub.iloc[np.nonzero(~excl.to_numpy())[0]]
            dt_pos = quint.index.names.index("datetime")
            tmp = quint[[col, "LABEL"]].copy()
            # 组内名次**只算一次**（v1.18.45）：原实现把 `rank(method="first")` 放在 `_q` 的
            # lambda 里，下方 TopK 曲线又独立算一遍 ⇒ 同一次 226 万行组内排名算两遍（实测
            # item_s +1.6s）。现在显式算一次 `_rk`，`_q` 与 TopK 共用（数值完全不变：
            # `qcut` 的输入仍是同一列的名次）。
            _qn = max(2, min(int(10 if quantiles is None else quantiles), 20))
            # ↑ 分位组数（API 参数，默认 10=十分位；**不要写 `quantiles or 10`**，0 会被静默吞掉）
            tmp["_rk"] = tmp.groupby(level=dt_pos)[col].rank(method="first")
            tmp["_q"] = tmp.groupby(level=dt_pos)["_rk"].transform(
                lambda x: pd.qcut(x, _qn, labels=False, duplicates="drop") + 1
            )
            tmp = tmp.dropna(subset=["_q"])
            groups, _q_dms = [], {}
            for q, g in tmp.groupby("_q"):
                # 日截面：每天组内样本先取均值 → 对所有参与交易日再平均
                dm = g.groupby(level=dt_pos)["LABEL"].mean()
                _q_dms[int(q)] = dm          # 曲线与 `mean_ret` **同源**（同一次 groupby）
                groups.append({
                    "quantile": int(q),
                    "count": int(len(g)),                        # 该组剔除后样本总数（观测数）
                    "n_days": int(len(dm)),                      # 参与交易日数
                    "mean_ret": round(float(dm.mean()), 6),      # 日截面平均收益
                })
            _dec_mat = _decile_matrix(_q_dms) if len(groups) >= 2 else None
            # 调仓期（v1.18.45）：默认跟随**预测周期 h**（= 「用预测周期调仓换股」），
            # API 可用 `rebalance_period` 覆盖；调仓日 = 日轴上每 `_REBAL` 个交易日取一个。
            _REBAL = max(1, int(rebalance_period if rebalance_period is not None else (horizon or 5)))
            _reb_pos = list(range(0, len(_dec_mat), _REBAL)) if _dec_mat is not None else []
            _dec_p = _dec_mat.iloc[_reb_pos] if _dec_mat is not None else None
            if len(groups) >= 2:
                result["quintile_ret"] = groups
                # 分位累计收益曲线（十分位图数据）：**调仓期口径**（每期 = 持有到下一个调仓日）
                if _dec_p is not None and len(_dec_p) >= 2:
                    result["quantile_curves"] = _quantile_curves(_dec_p)
            # ---- 连续因子：持仓期收益曲线（含三档成本）----------------------------
            # 纯算法在 `engine/cost_curves.py`（跨路径共用，**勿在此重写**）；本段只做
            # 「取现成序列 + 调纯函数 + 组装」：
            #   · `tmp` 已有分位 `_q` 与名次 `_rk` ⇒ 名次现成、无需二次排序；
            #   · **默认档 = 超额收益最强的分位组**（`quantile_curves.best_quantile`，可能是
            #     Q10/Q9/Q2…，方向由数据定、**不硬编码**）—— 逐日等分、只数逐日变化，
            #     换手分母用「当期在手只数」；
            #   · 固定 K 档（TopK 明细，含 K 切换用）按**同一方向**取：强端在大名次一侧时
            #     用镜像名次 `_rkd`（不再重算一遍 226 万行 rank）。
            # ⚠ **简化估算：未考虑涨跌停、停牌、流动性冲击**（前端须注明）；首期建仓不计费。
            try:
                from app.engine.cost_curves import _topgroup_cost_curves

                inst_pos = tmp.index.names.index("instrument")
                # 日均有效只数（10% 固定档 + K 敏感度表的两档百分比共用，只算一次）
                _med = float(tmp.groupby(level=dt_pos).size().median())
                k10 = max(1, int(_med * 0.1))
                # 方向：最强分位组落在高分位一侧 ⇒ 取「名次大」的一侧（镜像名次 `_rkd`）
                _qbest = (result.get("quantile_curves") or {}).get("best_quantile")
                _nq = len(groups) if groups else _qn
                _side = 1 if (_qbest is not None and 2 * int(_qbest) > int(_nq)) else -1
                if _side > 0 and "_rkd" not in tmp.columns:
                    # 同日名次唯一 ⇒ `_rkd = n_day - _rk + 1` 与「降序 rank」等价，且**零重算**
                    tmp["_rkd"] = tmp.groupby(level=dt_pos)["_rk"].transform("max") - tmp["_rk"] + 1.0
                _rkcol = "_rkd" if _side > 0 else "_rk"
                top = tmp[tmp[_rkcol] <= k10]
                dm10 = top.groupby(level=dt_pos)["LABEL"].mean().dropna()
                # 共享日轴优先取分位对齐矩阵（默认档与分位曲线同轴）；退化时才用固定档的
                _dts = list(_dec_mat.index) if _dec_mat is not None else list(dm10.index)
                if len(_dts) >= 2:
                    _idx_dts = pd.Index(_dts)
                    # 调仓日（**逐期**口径）：每 `_REBAL` 个交易日一个 ⇒ 每期收益 = 持有到下一
                    # 个调仓日（默认调仓期 = 预测周期 h，即「用预测周期调仓换股」）
                    _reb_pos = list(range(0, len(_dts), _REBAL))
                    _reb_dates = [_dts[_i] for _i in _reb_pos]
                    _reb_str = [pd.Timestamp(d).strftime("%Y-%m-%d") for d in _reb_dates]
                    _items, _cc_fixed_default = [], None

                    def _append_item(_kind, _k, _ret, _sub, _n_hold=None, _quantile=None):
                        """调纯函数 + 追加一条明细（三个自检都在这里）。

                        `_ret`  —— **逐调仓期**组合收益（长度 = 调仓次数）
                        `_sub`  —— 用于取每期名单的样本（分位组 / 固定 K 档）
                        `_n_hold` —— 换手分母；`None` ⇒ 分位组（只数逐期变化）用**当期在手只数**
                        """
                        # 「调仓日 → 当日名单」**一次 groupby** 取出（原先在调仓日循环里逐日做
                        # `index.get_level_values(...) == _dt` 全表扫描：242 次 × 22.5 万行
                        # ≈ 5400 万次比较，实测 item_s +1.6s）。
                        _sets = {_d: set(_g.index.get_level_values(inst_pos))
                                 for _d, _g in _sub.groupby(level=dt_pos, sort=True)}
                        hold_seq = [_sets.get(_d) for _d in _reb_dates]
                        if _n_hold is None:
                            _n_hold = np.asarray([len(_s) if _s else 0 for _s in hold_seq],
                                                 dtype=np.float64)
                        cc = _topgroup_cost_curves(_ret, hold_seq, _n_hold)
                        # 自检①：无成本档必须与 cumsum 逐位相同（＝本段没改坏任何数值）
                        if not np.array_equal(cc["curves"]["0.0000"], np.cumsum(_ret)):
                            raise AssertionError("无成本档应等于 cumsum(逐期收益)（%s）" % _kind)
                        if _kind == "decile":
                            # 自检②：默认档（分位组）的无成本档必须与 `quantile_curves` 里
                            # 该分位组的曲线一致 —— 两条独立组装路径互相钉住。
                            # ⚠ 用 atol=1e-9 而非逐位：本路径是 `np.cumsum`，分位曲线是
                            #   `Series.cumsum`，同一组同一序列下可能差 1 ULP；1e-9 对
                            #   累加量级（~1e-13 误差）有 1e4 倍余量，但结构性错误（串组/错轴）
                            #   差异在 1e-2 量级，照样能抓到。
                            _refc = _dec_p[int(_quantile)].cumsum().to_numpy(dtype=np.float64)
                            _gotc = np.asarray(cc["curves"]["0.0000"], dtype=np.float64)
                            _dmax = (float(np.max(np.abs(_gotc - _refc)))
                                     if _gotc.shape == _refc.shape else float("nan"))
                            if not (_gotc.shape == _refc.shape and _dmax <= 1e-9):
                                raise AssertionError(
                                    "默认档曲线应等于分位曲线（Q%s）：max|Δ|=%s（len %d vs %d）"
                                    % (_quantile, _dmax, _gotc.shape[0], _refc.shape[0]))
                        item = {
                            "kind": _kind,
                            "k": (int(np.median(_n_hold[_n_hold > 0]))
                                  if _kind == "decile" and np.any(_n_hold > 0) else int(_k)),
                            "turnover": [round(float(x), 6) for x in cc["turnover"]],
                            "curves": {kk: [round(float(x), 6) for x in vv]
                                       for kk, vv in cc["curves"].items()},
                        }
                        if _quantile is not None:
                            item["quantile"] = int(_quantile)
                        _items.append(item)
                        return cc

                    # ① 默认档 = 最强分位组（每期等权；换手分母 = 当期在手只数）
                    if _dec_p is not None and _qbest is not None:
                        _append_item("decile", 0,
                                     _dec_p[int(_qbest)].to_numpy(dtype=np.float64),
                                     tmp[tmp["_q"] == _qbest], None, _quantile=int(_qbest))

                    # ② 固定 K 档（TopK；与 K 敏感度表同一口径 ⇒ 自检③）
                    # K 清单：默认含 10% 档；**`< 1` 视为「日均只数的百分比」**（0.1 ⇒ 10%、
                    # 0.2 ⇒ 20%），**`≥ 1` 视为只数**（1 ⇒ 1 只）。
                    # ⚠ 分界必须是 1（不是"≤1 当百分比"）：否则 **K=1 会被算成"100% 只数"**
                    #   （≈ 全池），而 2/3 这种小 K 也无法表达 —— 前端 K 预设含 1/2/3，实测暴露。
                    #   100% 只数没有实际用途，故把 1 让给"1 只"。
                    # ⚠ 取整口径必须与 `k10` 一致（**截断 `int()`**，不用 `round`）—— 否则
                    #   0.1 会算出 36 与默认档 35 并存（API 层实测抓到）。
                    _ks = [k10]
                    for _x in (topk_list or []):
                        try:
                            _v = float(_x)
                        except (TypeError, ValueError):
                            continue
                        if not (_v > 0):
                            continue
                        _k = max(1, int(_v)) if _v >= 1 else max(1, int(_med * _v))
                        if _k not in _ks:
                            _ks.append(_k)
                    # 上限 12：默认档 + 前端预设 10 个（1/2/3/5/10/20/50/100/10%/20%）也要能全选
                    for _k in _ks[:12]:
                        _sub = top if _k == k10 else tmp[tmp[_rkcol] <= _k]
                        if _k == k10:
                            _dm = dm10
                        else:
                            _dm = _sub.groupby(level=dt_pos)["LABEL"].mean().dropna()
                            if len(_dm) < 2:
                                continue
                        if not _dm.index.equals(_idx_dts):
                            _dm = _dm.reindex(_dts).fillna(0.0)   # 缺失日按 0（= 空仓不计费）
                        # 只取**调仓期**那几天的收益（每期持有到下一个调仓日）
                        _cc = _append_item("topk", _k,
                                           _dm.to_numpy(dtype=np.float64)[_reb_pos], _sub, _k)
                        if _k == k10:
                            _cc_fixed_default = _cc
                    result["topk_curves"] = {
                        "rebalance_period": _REBAL,
                        "horizon": (int(horizon) if horizon else None),
                        "n_days": int(len(_dts)),        # 日轴长度（交易日）
                        "n": int(len(_reb_dates)),       # **调仓次数**（= 曲线点数 / 期数）
                        "dates": _reb_str,               # 调仓日（每期一个点）
                        "side": ("high" if _side > 0 else "low"),
                        "default_quantile": (int(_qbest) if _qbest is not None else None),
                        "items": _items,   # items[0] = 默认档（最强分位组，kind="decile"）
                    }
                    # ---- 基准（可切换）：与组合曲线**同轴同口径** ----------------------
                    # 每期 = 指数在同一调仓日的 T+1 → T+h+1 收益（与 `LABEL` 同口径）、各期算术累加；
                    # 多候选一次算出 ⇒ 前端**纯切换、零重算**（定稿口径）。失败不阻塞主流程。
                    # ⚠ 持有期必须用 h（不是调仓期）：调仓期一旦被改成 ≠h，各期收益本身就重叠，
                    #   基准仍按 h 算才与组合同口径。
                    if bench_close is not None and horizon:
                        try:
                            from app.factors.benchmark_curves import period_return_cums

                            result["topk_curves"]["benchmarks"] = {
                                "default": (bench_default or None),
                                "items": [{"code": c, **v} for c, v in period_return_cums(
                                    bench_close, _reb_dates, int(horizon)).items()],
                            }
                        except Exception as _e3:      # 与 `event_study_error` 同一处理风格
                            result["topk_curves"]["benchmark_error"] = "%r @ %s" % (
                                _e3, __import__("traceback").format_exc().strip().splitlines()[-1])
                    # ---- K 敏感度汇总表（换手 / 三档年化 / 成本吞噬比例）--------------
                    # 与固定 K 档**同一方向、同一份名次、同一批调仓日** ⇒ 同一组合的两个视图
                    # （默认档是最强分位组，逐日等分、只数逐日变化，与固定 K 略有差异）；
                    # 适配+组装在独立小模块 `factors/topk_sensitivity.py`，
                    # 算法在 `engine/cost_curves.py`（**勿在此重写**）。
                    # 自检③：默认 K 那一行的逐期换手必须与固定 K 档明细**逐位相同**。
                    try:
                        from app.factors.topk_sensitivity import build_k_sensitivity

                        result["topk_sensitivity"] = build_k_sensitivity(
                            tmp, dt_pos, inst_pos, _dts, _REBAL,
                            median_daily=_med, default_k=k10, rk_col=_rkcol,
                            verify_turnover=_cc_fixed_default["turnover"])
                    except Exception as _e2:      # 与 `event_study_error` 同一处理风格
                        result["topk_sensitivity_error"] = "%r @ %s" % (
                            _e2, __import__("traceback").format_exc().strip().splitlines()[-1])
            except Exception as _e:        # 与 `event_study_error` 同一处理风格
                result["topk_curves_error"] = "%r @ %s" % (
                    _e, __import__("traceback").format_exc().strip().splitlines()[-1])
        except FactorTestCancelled:
            raise
        except Exception:
            result["quintile_ret"] = None
    return result


def _load_feature_panel(instruments, fields, all_cols, start_date, load_end,
                        freeze_suspended_price, end_date, cancelled, progress_cb, factors,
                        warmup_days=0):
    """面板级特征加载（panel_expr 求值器，替代 qlib D.features）。

    成功返回与 D.features 输出结构一致的 DataFrame（MultiIndex 全历 × all_cols）；
    失败（含 panel 不支持的算子 / 数据异常）返回 None，由调用方回退 qlib。
    进度：面板求值一次性完成，进度回调给 5（解析）→ 30（就绪）；取消在求值期间
    无法中断（面板单批计算），接受此局限（求值远快于 qlib，等待显著缩短）。
    warmup_days: 额外预热缓冲（交易日，v1.18.6），透传给 panel_features/
    panel_features_parallel（语义见 panel_expr.panel_features；0=关闭）。
    """
    import os

    # 面板求值器开关（默认开；QLIB_SFT_PANEL=0 强制回退 qlib）
    if os.environ.get("QLIB_SFT_PANEL", "1") == "0":
        return None
    # 含 EMA/EMA_TDX/SMA 的字段默认【走面板】（v1.17.6 放开，根因已定位修复）。
    # 定位结论：面板求值本身与 qlib 逐位一致（小池 0 差、嵌套窗口 _tree_ext_days
    # 递归 + float64 求值），全 A 端到端的零星触发差异（25444 vs 25427、~15 处）
    # 来自【面板输出 float64 vs qlib D.features 输出 float32】→ CLOSE/CHANGE 等列
    # ~4-8e-6 尾差传导到涨停/停牌剔除判定（mark_limit_up 的 close>=limit_up-1e-6
    # 容差与尾差同量级）→ 边界触发翻面。修复：面板输出出口统一 cast float32
    # （panel_expr._cast_output_f32，与 qlib 返回 dtype 对齐），实测逐列 0 差。
    # 逃生口：QLIB_SFT_PANEL_EMA=1 可强制含 EMA/EMA_TDX/SMA 字段回退 qlib。
    if os.environ.get("QLIB_SFT_PANEL_EMA", "0") != "0":
        try:
            for _f in fields:
                _e = _f if isinstance(_f, str) else _f[0]
                if ("EMA(" in _e) or ("EMA_TDX(" in _e) or ("SMA(" in _e):
                    return None
        except Exception:
            pass
    # 取消探针：cancelled() 为 True 时抛 FactorTestCancelled 上传（由路由层标记 cancelled）。
    # 面板求值器每表达式/每块调用一次；注意必须先于 except Exception 捕获，否则被吞成回退 qlib。
    def _cancel_probe():
        if cancelled is not None and cancelled():
            raise FactorTestCancelled()

    # 池子规模阈值：中小池走单进程面板；超大池（全 A）走并行面板（v1.16.9）。
    # 可用 QLIB_SFT_PANEL_MAX 覆盖（如 ="0" 等价关面板，="999999" 强制全走面板）。
    _max_stocks = int(os.environ.get("QLIB_SFT_PANEL_MAX", "1000"))
    _force_single = _max_stocks == 0  # 兼容旧值：0 = 强制全部单进程面板
    if _force_single or len(instruments) <= max(_max_stocks, 1):
        try:
            from .panel_expr import panel_features

            if progress_cb:
                progress_cb(None, 6.0, f"计算特征数据（面板 {len(instruments)} 只）...")
            pdf = panel_features(instruments, list(zip(fields, all_cols)),
                                 start_date, load_end, cancel_cb=_cancel_probe,
                                 warmup_days=warmup_days)
        except FactorTestCancelled:
            raise
        except Exception as e:
            _dump_sft_error(e)
            return None
        if pdf is None or len(pdf) == 0:
            return None
        pdf = pdf.copy()
        pdf.columns = all_cols
        # 对齐列顺序（panel_features 顺序与 fields/all_cols 一致，此处兜底）
        pdf = pdf[list(all_cols)]
        if progress_cb:
            progress_cb(None, 30.0, "特征数据就绪")
        return pdf

    # 超大池（> _max_stocks）：并行面板（按股票切块多进程）；失败回退 qlib
    try:
        from .panel_expr import panel_features_parallel

        n_jobs = int(os.environ.get("QLIB_SFT_PANEL_JOBS", "0")) or None
        if progress_cb:
            progress_cb(None, 6.0, f"计算特征数据（面板并行 {len(instruments)} 只，切块求值中）...")
        # 并行求值按块回报进度：progress_cb(h=None, pct, msg) 驱动加载阶段 6→30 平滑推进；
        # cancel_cb 每收一块前检查，命中抛 FactorTestCancelled（最坏多等一块）
        pdf = panel_features_parallel(instruments, list(zip(fields, all_cols)),
                                      start_date, load_end, n_jobs=n_jobs,
                                      progress_cb=lambda p, m: progress_cb(None, p, m) if progress_cb else None,
                                      cancel_cb=_cancel_probe,
                                      warmup_days=warmup_days)
        if pdf is None or len(pdf) == 0:
            return None
        pdf = pdf.copy()
        pdf.columns = all_cols
        # 对齐列顺序（panel_features 顺序与 fields/all_cols 一致，此处兜底）
        pdf = pdf[list(all_cols)]
        if progress_cb:
            progress_cb(None, 30.0, "特征数据就绪")
        return pdf
    except FactorTestCancelled:
        raise
    except Exception as e:
        _dump_sft_error(e)
        return None


def run_single_factor_tests(
    label_horizons,
    universe: str,
    start_date: str,
    end_date: str,
    factors: List[dict] = None,
    progress_cb=None,
    cancelled=None,  # 取消检查回调：返回 True 表示用户已取消
    exclude_limit_up_signal: bool = True,
    exclude_limit_up_trade: bool = True,
    exclude_suspended: bool = True,
    price_adjust: str = "none",
    freeze_suspended_price: bool = True,
    suspend_remove: bool = True,
    exclude_st_t1: bool = False,
    exclude_stock_gem: bool = False,
    exclude_stock_kcb: bool = False,
    price_round: bool = True,
    warmup_days: Optional[int] = None,
    quantiles: int = 10,
    rebalance_period: Optional[int] = None,
    topk_list: Optional[list] = None,
) -> Dict[int, list]:
    """多预测周期单因子测试：所有周期【共享一次特征加载】，再逐周期分别统计。

    背景：此前并行多周期时每个周期各自调一次 run_single_factor_test → 各自做一遍
    D.features 全量加载（实测每次调用有 ~16s 与字段/股票数基本无关的固定开销），
    8 个周期=8 份固定开销 + 相同因子表达式算 8 遍，大公式下"加载半天"。

    本实现把 F0..Fn（因子去重）+ 每个周期的 LABEL_{h} 列 + base/tag 一次合并进
    同一个 D.features 调用（固定开销只付 1 次），随后逐周期在共享面板上取对应
    label 列做 _test_one 统计（统计阶段对内存中 DataFrame 操作，不触发 qlib 加载）。

    进度：progress_cb 约定为 progress_cb(h, p, msg)。
      - h=None：整体阶段（解析股票池/共享特征加载），p 为整体 0-100，供整体进度条直接使用；
      - h=int：该预测周期统计阶段内部 0-100。
    返回 {label_horizon: [因子结果]}。

    warmup_days（v1.18.6）：特征加载的额外预热缓冲（交易日）。None（默认）取环境变量
    QLIB_SFT_WARMUP_DAYS（默认 250，≈1 年）；0 = 关闭（回到 v1.18.5 口径，与 qlib
    D.features 冷启动逐位对齐）。>0 时面板多前移 warmup_days 天起算、qlib 回退路径亦
    从该点起加载并在出口裁回 [start_date, ...]，使"扩展天数无法静态推断"的长回看/
    动态窗口公式（DYN_*/BARSCOUNT/HHVBARS+Ref 嵌套）在区间首日即有收敛后的因子值。
    """
    horizons = sorted({max(1, int(h or 2)) for h in (label_horizons or [])})
    if not horizons or not factors:
        return {h: [] for h in horizons}
    # 区间日期合法性前置校验：非法日期（如 2026-06-31）放行后会在 pandas 解析处
    # 报成「特征计算失败: day is out of range for month」，掩盖真正的原因。
    _bad = _bad_date_arg(start_date, end_date)
    if _bad:
        err = [{**_test_one(pd.DataFrame(), f, ""), "error": _bad} for f in factors]
        return {h: err for h in horizons}
    # 阶段计时（v1.18.43）：只在 3 个阶段边界各打 3 次 perf_counter（每个因子约 0.4µs，
    # 实测无性能影响）。结果挂在每个因子结果的 `timing` 上，前端可直接显示
    # 「初始化 / 特征加载 / 该因子统计」三段耗时 —— 便于一眼看出"从哪里慢"。
    import time as _tmod

    _ph_t0 = _tmod.perf_counter()
    _ensure_qlib_init()
    _ph_init = _tmod.perf_counter() - _ph_t0
    _ph_feat = 0.0

    if progress_cb:
        progress_cb(None, 2.0, "解析股票池成分股...")

    pa = normalize_mode(price_adjust)
    # 各周期 label 表达式列（LABEL_{h}），一次 D.features 全部算出
    label_exprs = {
        h: adjust_expr(f"Ref($close, -{h + 1})/Ref($close, -1) - 1", pa) for h in horizons
    }
    # 尾部加载长度：label 需 n_max+1 个交易日；冻结价 label 与事件研究另需尾部延展。
    # **统计区间不受影响**：默认 freeze_suspended_price=True 时面板会在统计前裁回
    # [start_date, end_date]。
    # 事件研究（0/1 信号顺带算）的期数 = **用户填的最大周期**（v1.18.37 起）：
    #   × 旧行为 `max(EVENT_MAX_K=40, max(horizons))`：填 20 天也会白算到 40 期 ——
    #     基线曲线是逐 k 的大矩阵运算，k 越大越贵，20 期白算是近一半浪费。
    #   √ 现行为 `max(horizons)`：填 20 就算 1..20。弹窗「最长持有」默认 = es_k（已算期数），
    #     想看更长期数点「重算至 N 期」即可（独立接口按需重算，不拖累本任务）。
    # 判定口径不受影响：0/1 判定取的是**该行「周期」对应的 k**（前端 esPointOf 用 r.horizon），
    # 而 es_k ≥ 每个 horizon 恒成立，故判定点始终存在。
    es_k = max(1, int(max(horizons)))

    load_end = end_date
    if freeze_suspended_price:
        n_max = max(horizons)
        try:
            from qlib.data import D as _D
            cal = _D.calendar()
            cal_ts = pd.to_datetime(cal)
            pos = int((cal_ts <= pd.Timestamp(end_date)).sum())
            n_need = max(n_max, es_k)
            load_end = str(cal_ts[min(pos + n_need + 3, len(cal_ts) - 1)].date())
        except Exception:
            # 日历不可用时退回 end_date 原样加载（其合法性已在函数入口校验过）
            load_end = end_date

    # 预热缓冲（v1.18.6）：多前移 warmup_days 个交易日加载，仅供状态类/动态窗口算子
    # 收敛（启动值），出口仍裁剪回 [start_date, ...]，不改变评估区间。
    # 动机（2026-09-10 CCCMA250 不复权对账定位）：DYN_*/BARSCOUNT/HHVBARS+Ref 嵌套的
    # 真实扩展天数无法从表达式静态推断（_tree_ext_days 只能算固定窗口），面板原先只
    # 前移"可推断量" → 区间首日因子 NaN/未收敛（实测 002414 2021-01-04 因子 NaN，
    # start 提前到 2019-07-01 后 = 1，同池触发 627→628）。默认取
    # QLIB_SFT_WARMUP_DAYS（默认 250 交易日 ≈ 1 年）；传 0 关闭（回到 v1.18.5 口径）。
    import os as _os
    if warmup_days is None:
        try:
            warmup_days = int(_os.environ.get("QLIB_SFT_WARMUP_DAYS", "250"))
        except Exception:
            warmup_days = 250
    warmup_days = max(0, int(warmup_days or 0))
    load_start = start_date
    if warmup_days > 0:
        try:
            from qlib.data import D as _D
            _cal_ts = pd.to_datetime(_D.calendar())
            _pos = int((_cal_ts < pd.Timestamp(start_date)).sum())
            load_start = str(_cal_ts[max(0, _pos - warmup_days)].date())
        except Exception:
            load_start = start_date

    # 基准（可切换）指数行情：**一次取全候选** ⇒ 前端纯切换、零重算（定稿口径）。
    # 区间与特征面板一致（load_start..load_end，已含尾部 h+1 个交易日的延展）⇒ 末期基准也有值；
    # 5 个指数一次取数只需几毫秒。失败/无数据 ⇒ 不画基准线，**不阻塞**主流程。
    bench_close, bench_default = None, None
    try:
        from app.factors.benchmark_curves import BENCH_CODES, default_benchmark, load_bench_close

        bench_default = default_benchmark(universe)
        bench_close = load_bench_close(BENCH_CODES, load_start, load_end, cancelled=cancelled)
    except Exception:
        bench_close, bench_default = None, None

    # 因子去重编号
    ordered_exprs: List[str] = []
    factor_cols: List[str] = []
    col_map: List[str] = []
    seen = {}
    for f in factors:
        e = f.get("expression", "")
        if not e:
            col_map.append(None)
            continue
        if e not in seen:
            seen[e] = len(ordered_exprs)
            ordered_exprs.append(e)
            factor_cols.append(f"F{len(ordered_exprs) - 1}")
        col_map.append(factor_cols[seen[e]])

    if not ordered_exprs:
        err = [{**_test_one(pd.DataFrame(), f, ""), "error": "因子表达式为空"} for f in factors]
        return {h: err for h in horizons}

    from qlib.data import D

    try:
        instruments = _resolve_instruments(universe, start_date)
    except Exception as e:
        err = [{**_test_one(pd.DataFrame(), f, ""), "error": f"股票池解析失败: {e}"} for f in factors]
        return {h: err for h in horizons}
    if not instruments:
        err = [{**_test_one(pd.DataFrame(), f, ""), "error": "股票池为空（无成分股）"} for f in factors]
        return {h: err for h in horizons}

    if progress_cb:
        progress_cb(None, 4.0, f"股票池 {len(instruments)} 只，计算特征数据...")

    # 因子 + 各周期 label + 基础字段（真实价行情） + 涨跌停/ST 标签，全部一次加载
    if suspend_remove:
        adj_exprs = [_sr_wrap_expr(adjust_expr(e, pa, round_prices=price_round)) for e in ordered_exprs]
    else:
        adj_exprs = [adjust_expr(e, pa, round_prices=price_round) for e in ordered_exprs]
    base_fields = ["$close/$factor", "$change", "Ref($close/$factor, -1)", "Ref($change, -1)"]
    base_names = ["CLOSE", "CHANGE", "T1_CLOSE", "T1_CHANGE"]
    # 事件研究取价用（复权口径与 label 一致）：forward/backward=后复权价、none=真实价
    px_field = adjust_expr("$close", pa, round_prices=price_round)
    tag_fields: List[str] = []
    tag_names: List[str] = []
    if field_bin_available("limit_up") and field_bin_available("limit_down"):
        tag_fields += ["$limit_up", "$limit_down", "Ref($limit_up, -1)", "Ref($limit_down, -1)"]
        tag_names += ["LIMIT_UP", "LIMIT_DOWN", "T1_LIMIT_UP", "T1_LIMIT_DOWN"]
    if field_bin_available("is_st"):
        tag_fields += ["$is_st", "Ref($is_st, -1)"]
        tag_names += ["IS_ST", "T1_IS_ST"]
    label_cols = {h: f"LABEL_{h}" for h in horizons}
    fields = tuple(adj_exprs) + tuple(label_exprs.values()) + tuple(base_fields) + (px_field,) + tuple(tag_fields)
    all_cols = factor_cols + list(label_cols.values()) + base_names + ["PX"] + tag_names

    _ph_t0 = _tmod.perf_counter()
    df = _load_feature_panel(
        instruments, fields, all_cols, start_date, load_end,
        freeze_suspended_price=freeze_suspended_price, end_date=end_date,
        cancelled=cancelled, progress_cb=progress_cb, factors=factors,
        warmup_days=warmup_days,
    )
    _ph_feat = _tmod.perf_counter() - _ph_t0
    if df is None:
        # 面板加载失败（不支持的算子/数据异常）→ 回退 qlib D.features
        frames = []
        try:
            batch_size = max(1, min(len(fields), 32))
            for k in range(0, len(fields), batch_size):
                # qlib 回退路径取消点：每批 D.features 前检查（qlib 内部 job 不可中断）
                if cancelled is not None and cancelled():
                    raise FactorTestCancelled()
                if progress_cb:
                    done = min(k + batch_size, len(fields))
                    progress_cb(None, 5 + 25 * (done / len(fields)), f"加载特征数据 {done}/{len(fields)}...")
                part = D.features(instruments, list(fields[k:k + batch_size]),
                                  start_time=load_start, end_time=load_end)
                frames.append(part)
        except FactorTestCancelled:
            raise
        except Exception as e:
            _dump_sft_error(e)
            err = [{**_test_one(pd.DataFrame(), f, ""), "error": f"特征计算失败: {e}"} for f in factors]
            return {h: err for h in horizons}

        if not frames or all(f is None or len(f) == 0 for f in frames):
            err = [{**_test_one(pd.DataFrame(), f, ""), "error": "特征计算无数据"} for f in factors]
            return {h: err for h in horizons}

        raw = frames[0] if len(frames) == 1 else pd.concat(frames, axis=1)
        df = raw.copy()
        df.columns = all_cols
        # 预热缓冲（v1.18.6）：qlib 回退路径从 load_start 起加载 → 此处出口裁剪回评估区间
        # [start_date, ...]。面板路径出口已裁剪，本步对两条路径幂等生效，保证预热行绝不
        # 进入统计（失败必须报错而非静默保留未裁剪面板，否则样本数会被污染）。
        try:
            _dt_lv0 = df.index.names.index("datetime")
            df = df[df.index.get_level_values(_dt_lv0) >= pd.Timestamp(start_date)]
        except Exception as e:
            _dump_sft_error(e)
            err = [{**_test_one(pd.DataFrame(), f, ""), "error": f"预热区间裁剪失败: {e}"} for f in factors]
            return {h: err for h in horizons}
        if len(df) == 0:
            err = [{**_test_one(pd.DataFrame(), f, ""), "error": "预热裁剪后无样本"} for f in factors]
            return {h: err for h in horizons}

    # 未裁剪面板（含尾部 n_need+3 个交易日）：事件研究需要 T+1+k 的未来价格。
    # 默认口径下 df 会在下方裁回 [.., end_date] 用于统计；df_full 仅供事件研究取价。
    df_full = df

    # 冻结价 label 兜底：CLOSE ffill 一次，各周期按各自 h 做 shift 修正 label 列
    if freeze_suspended_price:
        try:
            inst_lv = df.index.names.index("instrument")
            dt_lv = df.index.names.index("datetime")
            cf = df.groupby(level=inst_lv)["CLOSE"].ffill()
            last_c = cf.groupby(level=inst_lv).transform("last")
            for h in horizons:
                exit_px = cf.groupby(level=inst_lv).shift(-(h + 1))
                exit_px = exit_px.where(exit_px.notna(), last_c)
                entry_px = cf.groupby(level=inst_lv).shift(-1)
                label_ff = exit_px / entry_px - 1
                df[label_cols[h]] = df[label_cols[h]].where(df[label_cols[h]].notna(), label_ff)
            sig_end = pd.Timestamp(end_date)
            df = df[df.index.get_level_values(dt_lv) <= sig_end]
        except Exception as e:
            _dump_sft_error(e)
            err = [{**_test_one(pd.DataFrame(), f, ""), "error": f"冻结价 label 计算失败: {e}"} for f in factors]
            return {h: err for h in horizons}

    out: Dict[int, list] = {}
    es_inst = df_full.index.names.index("instrument")
    es_dt = df_full.index.names.index("datetime")
    _es_px_cache: Dict[str, object] = {}      # col_name -> 触发股价格宽表（同因子跨周期复用）
    _es_res_cache: Dict[str, dict] = {}       # col_name -> 事件研究结果（同因子跨周期复用，见下）
    _full_wide_box: Dict[str, object] = {}    # 全样本价格宽表（基准曲线用，跨因子复用一次）
    for h in horizons:
        lc = label_cols[h]
        if lc not in df.columns:
            out[h] = [{**_test_one(pd.DataFrame(), f, ""), "error": "label 列缺失"} for f in factors]
            continue
        if progress_cb:
            progress_cb(h, 0.0, f"统计 {h} 日周期...")
        # 只保留该周期需要的列，避免每周期持有全量面板
        need = [c for c in (factor_cols + base_names + tag_names + [lc]) if c in df.columns]
        sub = df[need].copy()
        sub.rename(columns={lc: "LABEL"}, inplace=True)
        results = []
        total = len(factors)
        for i, (f, col_name) in enumerate(zip(factors, col_map), start=1):
            if progress_cb:
                progress_cb(h, 100.0 * ((i - 1) / total), f"测试因子 {i}/{total}: {f.get('name') or f.get('id')}")
            if cancelled is not None and cancelled():
                raise FactorTestCancelled()
            if col_name is None:
                results.append({**_test_one(pd.DataFrame(), f, ""), "error": "因子表达式为空"})
                continue
            trig_out: list = []
            _ph_t0 = _tmod.perf_counter()
            r = _test_one(
                sub,
                f,
                col_name,
                exclude_limit_up_signal=exclude_limit_up_signal,
                exclude_limit_up_trade=exclude_limit_up_trade,
                exclude_suspended=exclude_suspended,
                exclude_st_t1=exclude_st_t1,
                exclude_stock_gem=exclude_stock_gem,
                exclude_stock_kcb=exclude_stock_kcb,
                cancelled=cancelled,
                trig_index_out=trig_out,
                quantiles=quantiles,
                rebalance_period=rebalance_period,
                topk_list=topk_list,
                horizon=h,          # 调仓期默认跟随预测周期（「用预测周期调仓换股」）
                bench_close=bench_close,        # 可切换基准（同口径，见 benchmark_curves）
                bench_default=bench_default,
            )
            # 事件研究（0/1 信号顺带计算，v1.18.7）：稀疏信号按日配对会退化成单票
            # 收益序列（触发日只有 1 只票），须以「每次触发」为样本单位才有意义。
            # 复用本面板的 PX 列，无需二次加载（相对独立事件研究任务省掉一次全量加载）。
            if r.get("is_binary") and trig_out and trig_out[0] is not None and len(trig_out[0]):
                try:
                    if cancelled is not None and cancelled():
                        raise FactorTestCancelled()
                    # v1.18.35：先构造（或复用）**全样本宽表**，触发股宽表直接取列子集
                    # （等价且免去 mask + 二次 unstack：全 A「趋势顶底」实测 3.86s → ~0.05s）
                    if "w" not in _full_wide_box:
                        _full_wide_box["w"] = _full_px_wide(df_full, es_inst)
                    _px = _es_px_cache.get(col_name)
                    if _px is None:
                        _px = _event_px_from_full(_full_wide_box.get("w"), trig_out[0], es_inst)
                        if _px is None:  # 兜底：全样本宽表不可用时回退原路径
                            _px = _event_px_wide(df_full, trig_out[0], es_inst)
                        _es_px_cache[col_name] = _px
                    if _px is not None and len(_px):
                        # v1.18.37 跨周期复用：事件研究只取决于「触发事件集合 + es_k」，
                        # 二者都与预测周期 h 无关（触发由因子自身决定、es_k 为所有周期的上界），
                        # 因此多周期下逐周期重算完全等价、纯属浪费（全 A 实测单次
                        # build_event_stats + compute_baseline_curves ≈ 11.2s，N 个周期白烧
                        # (N-1)×11.2s）。首周期算完存缓存，其余周期浅拷贝复用（多周期结果
                        # 逐位相同，实测 MD5 指纹一致）。
                        _cached_es = _es_res_cache.get(col_name)
                        if _cached_es is not None:
                            r["event_study"] = dict(_cached_es)
                        else:
                            _ev = pd.DataFrame({
                                "code": trig_out[0].get_level_values(es_inst).astype(str).values,
                                "dt": pd.to_datetime(trig_out[0].get_level_values(es_dt)).values,
                            })
                            _es = build_event_stats(_px, _ev, es_k)
                            # 补参数信息：前端头部展示用，并据此判断「最长持有」是否需要重算
                            _es["params"] = {
                                "universe": universe,
                                "start_date": start_date,
                                "end_date": end_date,
                                "max_k": es_k,
                                "price_adjust": pa,
                            }
                            r["event_study"] = _es   # 先挂主结果，保证基准失败不影响事件研究
                            # 基准（未触发组）+ 超额（日配对口径）：仅展示，不参与判定。
                            # 全样本宽表一次构造、跨因子复用（约 1300×5000，50MB 量级）。
                            try:
                                _fw = _full_wide_box.get("w")  # 已在上方构造（w 不存在时为 None）
                                if _fw is not None and len(_fw):
                                    _es["baseline"] = compute_baseline_curves(
                                        _px, _fw, _ev, es_k)
                            except Exception as _bl_e:   # 基准失败只影响展示，不拖垮事件研究
                                _es["baseline_error"] = repr(_bl_e)
                            _es_res_cache[col_name] = _es
                except FactorTestCancelled:
                    raise
                except Exception as _es_e:       # 诊断：暴露事件研究失败原因（前端忽略该字段）
                    import traceback as _tb
                    r["event_study_error"] = "%r @ %s" % (
                        _es_e, _tb.format_exc().strip().splitlines()[-1])
            _ph_item = _tmod.perf_counter() - _ph_t0
            r["timing"] = {
                "init_s": round(_ph_init, 3),       # 一次性 qlib 初始化（仅首个任务有值）
                "feature_s": round(_ph_feat, 3),    # 面板/特征加载（全池共享，各因子相同）
                "item_s": round(_ph_item, 3),       # 本因子：统计 + 事件研究
                "total_s": round(_ph_init + _ph_feat + _ph_item, 3),
            }
            results.append(r)
        out[h] = results
        if progress_cb:
            progress_cb(h, 100.0, f"{h} 日周期完成")
    return out
