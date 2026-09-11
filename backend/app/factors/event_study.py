# -*- coding: utf-8 -*-
"""事件研究：把 0/1 触发信号的**每次触发**对齐到 T=0，统计 T+1 买入后 1..max_k 日的收益分布。

为什么需要它（2026-09-10 CCCMA250 诊断结论）：
  稀疏 0/1 信号的"按日配对检验"会退化——若某天只有 1 只股票触发，当天"触发组截面
  均值"就等于这一只票的收益，日差值序列变成单票噪声（实测 CCCMA250 全 A 每日触发
  中位数 = 1 只，Top20 天贡献了日差净和的 100.1%，剔除后剩余均值 -0.002%）。
  事件研究改以**每个触发事件**为样本单位，能回答真正关心的问题：

    - 赔率：各持有期的均值 / 中位数 / 分位数（p10/p25/p75/p90）
    - 概率：收益 >0 / >10% / >20% / >50% / >100% 的事件占比
    - 上限：T+1 买入后 max_k 日内的最大收益（"最高点卖出"的理想口径）
    - 明细：贡献最大 / 最差的事件（用于逐个人工复核）

口径与单因子测试（`single_test._test_one`）完全一致：
  - 触发 = 因子值 > 0.5（仅支持 0/1 二值信号；连续因子请用 IC/分位）
  - 剔除开关同 _test_one：信号日(T)涨停 / 成交日(T+1)涨停 / T+1 停牌 /
    T+1 ST / 创业板 / 科创板
  - 价格口径 = `adjust_expr("$close")`：forward/backward 用数据原生后复权价、
    none 用真实价（可选按分取整），故收益率口径与 label 一致
  - 停牌按前值冻结（ffill）
"""
from __future__ import annotations

import warnings
from typing import Optional

import numpy as np
import pandas as pd

from ..engine.adjust import adjust_expr, normalize_mode
from ..engine.feature_cache import _sr_wrap_expr
from ..engine.limits import field_bin_available, mark_limit_up


def _q(x: np.ndarray, p: float) -> float:
    """有限值分位数。"""
    x = x[np.isfinite(x)]
    return float(np.percentile(x, p)) if x.size else float("nan")


def _r(v, nd: int = 6):
    """安全 round（NaN/Inf → None）。"""
    try:
        f = float(v)
    except Exception:
        return None
    if not np.isfinite(f):
        return None
    return round(f, nd)


def load_px_wide(codes, start_date: str, end_date: str, price_adjust: str,
                 price_round: bool = True) -> pd.DataFrame:
    """加载指定标的的收盘价并转宽表（index=交易日, columns=标的），停牌按前值冻结。

    价格口径与 label 一致：`adjust_expr("$close")` —— forward/backward 用数据原生
    后复权价、none 用真实价（可选按分取整），保证事件收益与单因子测试口径一致。
    """
    from qlib.data import D

    codes = list(codes)
    if not codes:
        return pd.DataFrame()
    px_expr = adjust_expr("$close", price_adjust, round_prices=price_round)
    df = D.features(codes, [px_expr], start_time=start_date, end_time=end_date, freq="day")
    if df is None or len(df) == 0:
        return pd.DataFrame()
    df.columns = ["PX"]
    return df["PX"].unstack(level=0).sort_index().ffill()


def _align_returns(px_wide: pd.DataFrame, events: pd.DataFrame, max_k: int,
                   cancel_check=None):
    """把事件对齐到「T+1 收盘买入、T+1+k 收盘卖出」，返回 (mat[事件×k], max_ret, min_ret)。

    v1.18.35 性能：原实现是「逐事件 Python 循环 + 内层逐 k 循环」（n_ev × max_k 次标量
    运算，且每个事件还要 `px_wide[c].values` 取一次整列）—— 触发事件上万时单次调用可达
    ~25s（全 A「趋势顶底离开底部」实测）。现改为：
      ① 列位置 / 日期位置一次性解析（`get_indexer`，无 Python 循环）；
      ② 价格宽表一次性转 numpy，逐 k 列向量化切片（40 次 O(n_ev) 运算）。
    数值口径完全不变：T+1 相对 T+1+k（k=1..max_k）、买入价无效或越界的事件整行 NaN。
    """
    cal = px_wide.index
    n_ev = len(events)
    mat = np.full((n_ev, max_k), np.nan)
    max_ret = np.full(n_ev, np.nan)
    min_ret = np.full(n_ev, np.nan)
    if n_ev == 0 or len(cal) == 0 or px_wide.shape[1] == 0:
        return mat, max_ret, min_ret
    code_arr = events["code"].astype(str).values
    dt_arr = pd.to_datetime(events["dt"]).values
    if cancel_check is not None:
        cancel_check()
    # 列位置（缺失标的 → -1）与信号日位置（-1=不在日历上）
    col_pos = px_wide.columns.get_indexer(pd.Index(code_arr))
    p_pos = cal.get_indexer(pd.DatetimeIndex(dt_arr))
    ok = (col_pos >= 0) & (p_pos >= 0) & (p_pos < len(cal))
    if not ok.any():
        return mat, max_ret, min_ret
    ev_idx = np.nonzero(ok)[0]
    cc = col_pos[ev_idx]
    pp = p_pos[ev_idx]
    # 严格落在真实交易日上（与原实现 `cal[p] != dt → skip` 等价），且需存在 T+1
    keep = (cal.take(pp) == pd.DatetimeIndex(dt_arr[ev_idx])) & (pp + 1 < len(cal))
    if not keep.any():
        return mat, max_ret, min_ret
    ev_idx = ev_idx[keep]
    cc = cc[keep]
    pp = pp[keep]
    F = px_wide.to_numpy(dtype=np.float64, copy=False)
    n_row = F.shape[0]
    buy = F[pp + 1, cc]
    good = np.isfinite(buy) & (buy > 0.0)
    block = np.full((ev_idx.size, max_k), np.nan)
    with np.errstate(all="ignore"):
        for j in range(max_k):
            q = pp + 2 + j
            v = np.full(ev_idx.size, np.nan)
            inb = q < n_row
            if inb.any():
                kk = np.nonzero(inb)[0]
                v[kk] = F[q[kk], cc[kk]]
            block[:, j] = v / buy - 1.0
    block[~good] = np.nan
    mat[ev_idx] = block
    # 期内最大/最小收益（原实现：对每个事件的有限值取 max/min；全 NaN → NaN）
    fin_ok = np.isfinite(block).any(axis=1)
    if fin_ok.any():
        sub = block[fin_ok]
        with np.errstate(all="ignore"), warnings.catch_warnings():
            warnings.simplefilter("ignore", RuntimeWarning)
            max_ret[ev_idx[fin_ok]] = np.nanmax(sub, axis=1)
            min_ret[ev_idx[fin_ok]] = np.nanmin(sub, axis=1)
    return mat, max_ret, min_ret


def build_event_stats(px_wide: pd.DataFrame, events: pd.DataFrame, max_k: int,
                      n_raw: int = 0, cancel_check=None) -> dict:
    """由「价格宽表 + 事件表」计算事件研究结果。

    口径：T 为信号日，T+1 收盘买入，T+1+k 收盘卖出（k = 1..max_k）。
    events：DataFrame，需含 `code` / `dt` 两列（dt 为 Timestamp）。
    返回 curve / prob / upside / top_events / worst_events 等（不含 factor/params）。
    """
    max_k = max(1, int(max_k or 40))
    ks = list(range(1, max_k + 1))
    n_ev = len(events)
    code_arr = events["code"].astype(str).values
    dt_arr = pd.to_datetime(events["dt"]).values
    mat, max_ret, min_ret = _align_returns(px_wide, events, max_k, cancel_check)

    curve = []
    for j, k in enumerate(ks):
        x = mat[:, j]
        x = x[np.isfinite(x)]
        if x.size == 0:
            continue
        curve.append({
            "k": k,
            "n": int(x.size),
            "mean": _r(x.mean()), "median": _r(np.median(x)),
            "win": _r(float((x > 0).mean())),
            "p10": _r(_q(x, 10)), "p25": _r(_q(x, 25)),
            "p75": _r(_q(x, 75)), "p90": _r(_q(x, 90)),
            "max": _r(x.max()), "min": _r(x.min()),
        })

    prob = []
    for j, k in enumerate(ks):
        x = mat[:, j]
        x = x[np.isfinite(x)]
        if x.size == 0:
            continue
        prob.append({
            "k": k,
            "gt0": _r(float((x > 0).mean())),
            "gt10": _r(float((x > 0.10).mean())),
            "gt20": _r(float((x > 0.20).mean())),
            "gt50": _r(float((x > 0.50).mean())),
            "gt100": _r(float((x > 1.00).mean())),
        })

    mr = max_ret[np.isfinite(max_ret)]
    mn = min_ret[np.isfinite(min_ret)]
    upside = {
        "mean": _r(mr.mean()) if mr.size else None,
        "median": _r(np.median(mr)) if mr.size else None,
        "p75": _r(_q(mr, 75)) if mr.size else None,
        "p90": _r(_q(mr, 90)) if mr.size else None,
        "p99": _r(_q(mr, 99)) if mr.size else None,
        "max": _r(mr.max()) if mr.size else None,
        "reach20": _r(float((mr > 0.20).mean())) if mr.size else None,
        "reach50": _r(float((mr > 0.50).mean())) if mr.size else None,
        "reach100": _r(float((mr > 1.00).mean())) if mr.size else None,
        "dn_mean": _r(mn.mean()) if mn.size else None,
        "dn_median": _r(np.median(mn)) if mn.size else None,
        "dn_min": _r(mn.min()) if mn.size else None,
    }

    # 逐 k 的榜单（top_by_k / worst_by_k）：
    #   × 旧实现固定用 `mat[:, -1]`（= max_k 期）排序，导致「表头写"持有 k 日"、数值却是
    #     max_k 期」的错配，且会漏掉「未持满 max_k 期、但在更短的 k 上完全有效」的事件
    #     （典型场景：评估区间末端贴近数据末尾，末尾触发的后几期取不到价格）。
    #   √ 改为每个 k 各出一份榜单；前端按当前「最长持有」取对应那份。
    # 兼容：仍保留顶层 top_events / worst_events（= max_k 期口径），不破坏既有调用方。
    codes_str = [str(c) for c in code_arr]
    dts_str = [str(pd.Timestamp(d).date()) for d in dt_arr]
    lead = np.fmax.accumulate(mat, axis=1)      # 期内最高（到 j 期为止的最大值，忽略 NaN）
    top_by_k: dict = {}
    worst_by_k: dict = {}
    for j, k in enumerate(ks):
        x = mat[:, j]
        ok = np.where(np.isfinite(x))[0]
        if ok.size == 0:
            continue
        o = ok[np.argsort(-x[ok])]
        top_by_k[str(k)] = [{
            "code": codes_str[i],
            "dt": dts_str[i],
            "ret": _r(x[i]),
            "max_ret": _r(lead[i, j]),
        } for i in o[:20]]
        o2 = ok[np.argsort(x[ok])]
        worst_by_k[str(k)] = [{
            "code": codes_str[i],
            "dt": dts_str[i],
            "ret": _r(x[i]),
        } for i in o2[:10]]

    last = mat[:, -1]
    order = np.argsort(-np.nan_to_num(last, nan=-9e9))
    top_events = []
    for idx in order[:20]:
        if not np.isfinite(last[idx]):
            continue
        top_events.append({
            "code": codes_str[idx],
            "dt": dts_str[idx],
            "ret": _r(last[idx]),
            "max_ret": _r(max_ret[idx]),
        })
    worst_events = []
    for idx in order[-10:][::-1]:
        if not np.isfinite(last[idx]):
            continue
        worst_events.append({
            "code": codes_str[idx],
            "dt": dts_str[idx],
            "ret": _r(last[idx]),
        })

    # ---- 数据不足的事件清单（避免"静默丢弃"）----
    # 场景：用户把 end_date 设到接近数据尾部（或数据本身没更新到该日）时，靠近末尾的
    # 触发在 T+1..T+1+max_k 上拿不到全部收盘价 —— 也就是"到截止日还没平仓"。
    # 这类事件不会进入 curve 的对应 k（该 k 的 n 会偏小），也不会出现在 top/worst 里，
    # 界面上完全看不出来，容易被误读为"全部触发都统计了 / 样本凭空变少"。
    # 故单独统计：
    #   n_unaligned —— 连买入价都拿不到（完全无法对齐）
    #   n_short     —— 能买入但有效期数 < max_k（典型的"未平仓"）
    valid_cnt = np.isfinite(mat).sum(axis=1)
    n_unaligned = int((valid_cnt == 0).sum())
    short_idx = np.where((valid_cnt > 0) & (valid_cnt < max_k))[0]
    short_idx = short_idx[np.argsort(valid_cnt[short_idx])]      # 缺得最多的排前面
    short_events = [{
        "code": str(code_arr[i]),
        "dt": str(pd.Timestamp(dt_arr[i]).date()),
        "n_valid_k": int(valid_cnt[i]),
    } for i in short_idx[:200]]

    return {
        "n_events": int(n_ev),
        "n_aligned": int(np.isfinite(last).sum()),
        "n_raw": int(n_raw or n_ev),
        "max_k": int(max_k),
        # 数据不足（未平仓 / 完全无法对齐）的统计与明细，供界面提示
        "n_unaligned": n_unaligned,
        "n_short": int(short_idx.size),
        "short_events": short_events,
        "ks": ks,
        "curve": curve,
        "prob": prob,
        "upside": upside,
        # 逐 k 榜单（前端按当前「最长持有」取对应那份，键为字符串化的 k）
        "top_by_k": top_by_k,
        "worst_by_k": worst_by_k,
        # 兼容保留：max_k 期口径的榜单
        "top_events": top_events,
        "worst_events": worst_events,
    }


def compute_baseline_curves(px_wide_trig: pd.DataFrame, px_wide_full: pd.DataFrame,
                            events: pd.DataFrame, max_k: int,
                            cancel_check=None) -> dict:
    """计算「基准（未触发组）」与「超额」曲线（仅供展示）。

    **两套口径，不可混用**：
      ① 均值口径（日配对）：对每个触发日 T，取【T 当天未触发的股票】在 T+1..T+1+k 的
         等权平均收益 → 再对所有配对日求平均；触发组同法（先按日截面均值再对日平均）。
         输出 trigger_pair / baseline / excess。
      ② 中位数口径（事件级）：把配对日上的**全部样本**（日 × 标的）汇成一份取中位数，
         触发组取全部触发事件的 k 期收益中位数。输出 trigger_median / baseline_median /
         excess_median。
    之所以两套：均值会被少数极端事件（连板/妖股）主导，中位数刻画「典型一次触发」；
    两者差距本身就是判断信号是否「彩票型」的关键证据。数值均为原始小数；前端按 % 展示。
    """
    max_k = max(1, int(max_k or 40))
    ks = list(range(1, max_k + 1))
    ev_code = events["code"].astype(str).values
    ev_dt = pd.to_datetime(events["dt"]).values

    # ---- 触发组：事件矩阵 → 按触发日聚合 → 配对日平均 ----
    mat, _, _ = _align_returns(px_wide_trig, events, max_k, cancel_check)
    tdf = pd.DataFrame(mat, columns=ks)
    tdf["_d"] = ev_dt
    by_day = tdf.groupby("_d")[ks].mean()
    days = by_day.index
    trig_pair = by_day.mean(axis=0)

    # ---- 基准：全样本宽表 → 每日「未触发组」等权均值（剔除当日触发股） ----
    # v1.18.33 性能：原实现对每个 k 做 full.shift(-(k+1)) / entry / where(~flag) /
    # reindex(days) / to_numpy（40 次全表操作，全 A 实测 8.8s/因子）。改为**一次性转
    # numpy**，逐 k 只在「配对日」行上取子矩阵做除法/截面均值/中位数（配对日 ~几百行，
    # 无全表 shift 与整表 reindex 拷贝）。数值口径不变：ret[t] = F[t+1+k]/F[t+1] − 1，
    # 剔除当日触发股后「先按日截面均值、再对配对日平均」/ 中位数取配对日全部样本。
    full = px_wide_full
    base_arr = np.full(len(ks), np.nan, dtype=float)
    base_med_arr = np.full(len(ks), np.nan, dtype=float)
    if full is not None and len(full) and len(days):
        F = full.to_numpy(dtype=np.float64, copy=False)
        n_row = F.shape[0]
        n_col = F.shape[1]
        # 配对日 → 宽表行位置；不在宽表内的日期跳过（等价于原 reindex(days).dropna()）
        rows = full.index.get_indexer(pd.DatetimeIndex(days))
        rows = rows[rows >= 0]
        if rows.size:
            # 当日触发股标记：只建「配对日 × 标的」小布尔矩阵（原实现是全表 DataFrame flag）
            cols = full.columns.get_indexer(pd.Index(ev_code))
            ev_rows = full.index.get_indexer(pd.DatetimeIndex(ev_dt))
            row_of = {int(r): i for i, r in enumerate(rows)}
            flag = np.zeros((rows.size, n_col), dtype=bool)
            for r, c in zip(ev_rows, cols):
                i = row_of.get(int(r))
                if i is not None and c >= 0:
                    flag[i, c] = True
            entry_rows = rows + 1  # T+1（entry 行）
            for j, k in enumerate(ks):
                if (j % 10 == 0) and (cancel_check is not None):
                    cancel_check()
                tgt = entry_rows + k  # T+1+k
                ok = (tgt < n_row) & (entry_rows < n_row)
                if not ok.any():
                    continue
                # v1.18.35 性能：只对**有效配对日**（该日存在 T+1+k 价）构造子矩阵 ——
                # 原实现对全部配对日分配 (n_pair_days × n_inst) 大矩阵（全 A 约
                # 1172×5418 = 635 万），大 k 时绝大多数行整行无效仍参与 nanmean/median。
                r_idx = np.nonzero(ok)[0]
                with np.errstate(all="ignore"):
                    num = F[tgt[r_idx]] / F[entry_rows[r_idx]] - 1.0
                num[flag[r_idx]] = np.nan  # 剔除当日触发股（保持 NaN）
                # 均值口径：先按日取截面均值，再对配对日平均
                # （某配对日可能全部为 NaN——如该日所有个股都缺 T+1+k 价：nanmean 会发
                #  RuntimeWarning「Mean of empty slice」，此处显式抑制，结果仍为 NaN）
                with np.errstate(all="ignore"), warnings.catch_warnings():
                    warnings.simplefilter("ignore", RuntimeWarning)
                    daily = np.nanmean(num, axis=1)
                    if np.isfinite(daily).any():
                        base_arr[j] = float(np.nanmean(daily))
                    # 中位数口径：配对日 × 未触发股的全部样本汇成一份取中位数
                    # （不能"先算每日横截面中位数、再对日子平均"——与触发组事件级中位数不对称）
                    vals = num[np.isfinite(num)]
                    if vals.size:
                        base_med_arr[j] = float(np.median(vals))
    trig_arr = np.asarray([float(trig_pair.get(k, np.nan)) for k in ks], dtype=float)

    # 触发组「事件级中位数」：全部触发事件在 k 期收益的中位数（与 curve[].median 同口径）
    mat_arr = np.asarray(mat, dtype=float)
    with np.errstate(all="ignore"):
        trig_med_arr = (np.nanmedian(mat_arr, axis=0) if mat_arr.size
                        else np.full(len(ks), np.nan))

    return {
        "ks": ks,
        # ── 均值口径（日配对）──
        "trigger_pair": [_r(v, 6) for v in trig_arr],
        "baseline": [_r(v, 6) for v in base_arr],
        "excess": [_r(v, 6) for v in (trig_arr - base_arr)],
        # ── 中位数口径（事件级；与均值口径不可混用）──
        "trigger_median": [_r(v, 6) for v in trig_med_arr],
        "baseline_median": [_r(v, 6) for v in base_med_arr],
        "excess_median": [_r(v, 6) for v in (trig_med_arr - base_med_arr)],
        "n_pair_days": int(len(days)),
    }


def run_event_study(
    universe: str,
    start_date: str,
    end_date: str,
    factor: dict,
    max_k: int = 40,
    exclude_limit_up_signal: bool = True,
    exclude_limit_up_trade: bool = True,
    exclude_suspended: bool = True,
    exclude_st_t1: bool = False,
    exclude_stock_gem: bool = False,
    exclude_stock_kcb: bool = False,
    price_adjust: str = "forward",
    price_round: bool = True,
    suspend_remove: bool = True,
    freeze_suspended_price: bool = True,
    warmup_days: Optional[int] = None,
    progress_cb=None,
    cancelled=None,
) -> dict:
    """执行事件研究，返回 {error} 或完整结果字典（见文件末尾结构说明）。

    progress_cb(h, p, msg)：h=None 表示整体阶段（0-100）。
    cancelled()：返回 True 时在检查点抛出 FactorTestCancelled。
    """
    import os

    from qlib.data import D

    from .single_test import (
        FactorTestCancelled,
        _ensure_qlib_init,
        _inst_codes,
        _load_feature_panel,
        _resolve_instruments,
    )

    def _check():
        if cancelled is not None and cancelled():
            raise FactorTestCancelled()

    def _prog(p, msg):
        if progress_cb:
            progress_cb(None, p, msg)

    _ensure_qlib_init()
    pa = normalize_mode(price_adjust)
    max_k = max(1, int(max_k or 40))
    expr = (factor or {}).get("expression") or ""
    if not expr:
        return {"error": "因子表达式为空"}

    # ---------- 0. 预热缓冲（与单因子测试同语义：None → env → 默认 250） ----------
    if warmup_days is None:
        try:
            warmup_days = int(os.environ.get("QLIB_SFT_WARMUP_DAYS", "250"))
        except Exception:
            warmup_days = 250
    warmup_days = max(0, int(warmup_days or 0))

    # ---------- 1. 股票池 ----------
    _prog(2.0, "解析股票池成分股...")
    try:
        instruments = _resolve_instruments(universe, start_date)
    except Exception as e:  # noqa: BLE001
        return {"error": "股票池解析失败: %s" % e}
    if not instruments:
        return {"error": "股票池为空（无成分股）"}
    _check()

    # ---------- 2. 交易日历边界 ----------
    #   load_end（判定用）：只需前瞻到 T+1 → end + 3 个交易日
    #   ev_end （取价用）：需覆盖 T+1+max_k → end + max_k + 3 个交易日
    cal = pd.to_datetime(D.calendar())
    pos_end = int((cal <= pd.Timestamp(end_date)).sum())
    load_end = str(cal[min(pos_end + 3, len(cal) - 1)].date())
    ev_end = str(cal[min(pos_end + max_k + 3, len(cal) - 1)].date())

    # ---------- 3. 加载因子 + T+1 行情（触发判定与剔除，与 _test_one 同字段） ----------
    if suspend_remove:
        f0_expr = _sr_wrap_expr(adjust_expr(expr, pa, round_prices=price_round))
    else:
        f0_expr = adjust_expr(expr, pa, round_prices=price_round)

    base_fields = ["$close/$factor", "$change", "Ref($close/$factor, -1)", "Ref($change, -1)"]
    base_names = ["CLOSE", "CHANGE", "T1_CLOSE", "T1_CHANGE"]
    tag_fields: list = []
    tag_names: list = []
    if field_bin_available("limit_up") and field_bin_available("limit_down"):
        tag_fields += ["$limit_up", "$limit_down", "Ref($limit_up, -1)", "Ref($limit_down, -1)"]
        tag_names += ["LIMIT_UP", "LIMIT_DOWN", "T1_LIMIT_UP", "T1_LIMIT_DOWN"]
    if field_bin_available("is_st"):
        tag_fields += ["$is_st", "Ref($is_st, -1)"]
        tag_names += ["IS_ST", "T1_IS_ST"]

    fields = [f0_expr] + base_fields + tag_fields
    all_cols = ["F0"] + base_names + tag_names

    _prog(4.0, "计算特征数据（%d 只）..." % len(instruments))
    df = _load_feature_panel(
        instruments, fields, all_cols, start_date, load_end,
        freeze_suspended_price=freeze_suspended_price, end_date=end_date,
        cancelled=cancelled, progress_cb=progress_cb, factors=[factor],
        warmup_days=warmup_days,
    )
    if df is None:
        return {"error": "特征计算失败（面板不支持该算子或数据异常，无法事件研究）"}
    df = df.copy()
    df.columns = all_cols
    _check()

    # ---------- 4. 触发样本 + 剔除（口径同 _test_one._exclude） ----------
    _prog(35.0, "提取触发样本...")
    sub = df[df["F0"].notna()]
    trig = sub[sub["F0"] > 0.5]
    if len(trig) == 0:
        return {"error": "该因子在区间内没有触发样本（非 0/1 信号或条件从未满足）"}

    def _exclude(g: pd.DataFrame) -> pd.DataFrame:
        if len(g) == 0:
            return g
        if exclude_limit_up_signal and "CLOSE" in df.columns and "CHANGE" in df.columns:
            g = g[~mark_limit_up(df.loc[g.index], "CLOSE", "CHANGE")]
        if exclude_limit_up_trade and "T1_CLOSE" in df.columns and "T1_CHANGE" in df.columns:
            g = g[~mark_limit_up(df.loc[g.index], "T1_CLOSE", "T1_CHANGE")]
        if exclude_suspended and "T1_CLOSE" in df.columns:
            g = g[~df.loc[g.index, "T1_CLOSE"].isna()]
        if (exclude_st_t1 or exclude_stock_gem or exclude_stock_kcb) and len(g):
            codes = _inst_codes(df.loc[g.index])
            keep = pd.Series(True, index=g.index)
            if exclude_st_t1 and "T1_IS_ST" in df.columns:
                keep &= ~(df.loc[g.index, "T1_IS_ST"] > 0.5)
            if exclude_stock_gem:
                keep &= ~codes.str.startswith("SZ30")
            if exclude_stock_kcb:
                keep &= ~codes.str.startswith("SH688")
            g = g[keep]
        return g

    n_raw = int(len(trig))
    trig = _exclude(trig)
    if len(trig) == 0:
        return {"error": "触发样本在剔除后为空（全部落在涨停/停牌/ST/板块过滤内）"}

    inst_pos = trig.index.names.index("instrument")
    dt_pos = trig.index.names.index("datetime")
    ev = pd.DataFrame({
        "code": trig.index.get_level_values(inst_pos).astype(str),
        "dt": pd.to_datetime(trig.index.get_level_values(dt_pos)),
    }).reset_index(drop=True)

    # ---------- 5. 加载触发标的收盘价（仅涉及标的，数据量小） ----------
    codes = sorted(ev["code"].unique().tolist())
    _prog(45.0, "加载 %d 只触发标的的后续行情（至 %s）..." % (len(codes), ev_end))
    piv = load_px_wide(codes, start_date, ev_end, pa, price_round)
    if piv is None or len(piv) == 0:
        return {"error": "触发标的行情加载失败"}
    _check()

    # ---------- 6. 对齐 + 统计（T+1 收盘买入，T+1+k 收盘卖出） ----------
    _prog(60.0, "对齐 %d 个事件..." % len(ev))
    stats = build_event_stats(piv, ev, max_k, n_raw=n_raw, cancel_check=_check)
    if stats.get("n_aligned", 0) == 0:
        _ns = int(stats.get("n_short") or 0)
        _na = int(stats.get("n_unaligned") or 0)
        if _ns and not _na:
            return {"error": "全部 %d 个触发在 %d 期上均未平仓（信号日距数据末尾不足 %d 个交易日）"
                             "——请把结束日期提前 %d 个交易日以上，或延后数据" % (_ns, max_k, max_k, max_k)}
        return {"error": "触发事件无法对齐到买入价（行情缺失：无法对齐 %d 个 / 未平仓 %d 个）" % (_na, _ns)}

    # ---------- 7. 基准（未触发组）与超额曲线（日配对口径，仅供展示） ----------
    # 口径与单因子测试「顺带计算」时完全一致（全样本宽表 → 每日剔除当日触发股后取等权均值）。
    # 此步原先只在 single_test.py 里做，导致弹窗「重新计算」（走本独立接口）会丢掉基准/超额，
    # 与表格行的结果（带 baseline）表现不一致 —— 本次补齐。
    baseline = None
    try:
        _check()
        _prog(88.0, "计算基准（全样本收盘价宽表，%d 只）..." % len(instruments))
        full_codes = sorted({str(c).upper() for c in instruments})
        full_piv = load_px_wide(full_codes, start_date, ev_end, pa, price_round)
        if full_piv is not None and len(full_piv):
            baseline = compute_baseline_curves(piv, full_piv, ev, max_k, cancel_check=_check)
    except FactorTestCancelled:
        raise
    except Exception as _ble:      # 基准失败只影响展示，不拖垮事件研究主结果
        baseline = {"error": repr(_ble)}

    _prog(100.0, "完成")
    return {
        "factor": {
            "id": (factor or {}).get("id") or "",
            "name": (factor or {}).get("name") or "",
            "expression": expr,
            "source_formula": (factor or {}).get("source_formula") or "",
        },
        "params": {
            "universe": universe, "start_date": start_date, "end_date": end_date,
            "max_k": max_k, "price_adjust": pa,
        },
        **stats,
        # 基准（未触发组）/超额曲线；None 或 {"error": ...} 时前端自动隐藏对应图
        "baseline": baseline,
    }
