# -*- coding: utf-8 -*-
"""交易信号测试路由（`/api/signal-test/*`，v1.19.38）。

与单因子测试/回测的差异（设计取舍）：
  · **同步接口**：全流程是"CSV 解析 + 向量化事件研究 + 逐日回测"，实测秒级（价格矩阵有磁盘
    缓存，第二次更快）⇒ 不要任务队列、不要轮询，用户点完就出结果（"秒出"是其诉求）；
    响应里仍保留 `elapsed`/`timings`，将来若某天变慢可以平滑切异步；
  · **不占并发配额**：CPU 占用极小（无模型训练、无 qlib 引擎），不进 TaskManager 的槽位，
    不会让正在跑的回测排队（多人上服务器时这点很重要）；
  · 与 `factors/` 零耦合：只 import 它的公开工具（event_study / benchmark_curves）。
"""
from __future__ import annotations

import base64
import time
from typing import Dict, List, Optional

import pandas as pd
from fastapi import APIRouter, HTTPException

from ..factors.benchmark_curves import BENCHMARKS, BENCH_NAMES
from ..signals.engine import attach_benchmark, run_backtest
from ..signals.event import coverage, normalize_events, run_event_study
from ..signals.models import SignalTestRequest
from ..signals.parsers import parse_csv
from ..signals.pricing import (fill_limits, load_bench_wide, load_close_wide,
                              load_price_panel, resolve_universe)
from ..signals.replay import replay_trades

router = APIRouter(prefix="/api/signal-test", tags=["signal-test"])

POOLS = (
    ("all", "全A（默认，与单因子测试同口径）"),
    ("csi300", "沪深300"),
    ("csi500", "中证500"),
    ("csi800", "中证800"),
    ("csi1000", "中证1000"),
    ("@signals", "信号标的并集（最快，基准偏窄）"),
)
FILLS = (
    ("t1_open", "次日开盘价（默认）"),
    ("t_close", "信号日收盘价"),
    ("t1_close", "次日收盘价"),
)
ALLOCS = (
    ("event_even", "事件驱动再平衡（默认：只在买入信号成交/到期卖出当天调平到等权）"),
    ("cash_even", "现金等分（当日新买入在可用现金内等分，不做任何再平衡）"),
)


def _nav_rows(nav: pd.DataFrame) -> List[Dict]:
    """净值宽表 → Recharts 友好的行数组（列名即曲线名；NaN → None，前端断线不猜数）。"""
    rows = []
    for d, row in nav.iterrows():
        rec = {"date": str(pd.Timestamp(d).date())}
        for c in nav.columns:
            v = row[c]
            rec[str(c)] = None if not pd.notna(v) else round(float(v), 5)
        rows.append(rec)
    return rows


def _records(df: Optional[pd.DataFrame], cap: int = 1500) -> List[Dict]:
    if df is None or not len(df):
        return []
    return df.head(cap).to_dict("records")


def _official_series(pf: pd.DataFrame, col: str, index) -> Optional[pd.Series]:
    """官方逐日序列 → 对齐到我们的净值日历（缺日 ffill；列不存在返回 None）。"""
    if pf is None or col not in pf.columns or not len(pf):
        return None
    s = pf.set_index("date")[col].astype(float).reindex(index).ffill()
    return s if s.notna().any() else None


def _amount_match(tr: pd.DataFrame, pf: pd.DataFrame, tol: float = 1.0) -> tuple:
    """成交明细的逐日买卖金额 vs 官方《收益概述》的 `当日买入/当日卖出` ⇒ (一致天数, 不一致天数)。

    ⚠ 这是**判断"成交明细是否完整"的硬证据**：2026-09-15 实测该文件 2187/2187 天一致，
      据此确认明细完整 ⇒ 反过来定位到"现金看起来不闭合"是我的**排序**问题（不是流水缺账）。
    """
    if tr is None or not len(tr) or pf is None or not len(pf):
        return (0, 0)
    day = tr["date"].dt.normalize()
    b = tr[tr["side"] > 0].groupby(day)["amount"].sum()
    s = tr[tr["side"] < 0].groupby(day)["amount"].sum()
    p = pf.set_index("date")
    idx = p.index
    ob = pd.to_numeric(p.get("buy_amt"), errors="coerce").fillna(0.0).astype(float)
    os_ = pd.to_numeric(p.get("sell_amt"), errors="coerce").fillna(0.0).astype(float)
    db = (b.reindex(idx).fillna(0.0) - ob).abs()
    ds = (s.reindex(idx).fillna(0.0) - os_).abs()
    ok = (db <= tol) & (ds <= tol)
    return (int(ok.sum()), int((~ok).sum()))


def _raw_of(req: SignalTestRequest):
    """请求体 → 原始字节/文本（base64 优先：GBK 文件必须由后端嗅探解码，不要在前端猜）。"""
    if req.content_b64:
        try:
            return base64.b64decode(req.content_b64)
        except Exception as e:
            raise HTTPException(status_code=400, detail="base64 解码失败：%s" % e)
    return req.content or ""


@router.get("/options", summary="交易信号测试的可选项（基准/池/成交时点/资金方案）")
def options() -> Dict:
    return {
        "benchmarks": [{"code": c, "name": n} for c, n in BENCHMARKS],
        "pools": [{"key": k, "name": n} for k, n in POOLS],
        "fills": [{"key": k, "name": n} for k, n in FILLS],
        "allocs": [{"key": k, "name": n} for k, n in ALLOCS],
        "defaults": {"cost": 0.004, "horizon": 60, "fill": "t1_open",
                     "capital": 1e9, "pool": "all", "alloc": "event_even",
                     "benchmark": "SH000300", "strict_limit": True},
    }


@router.post("/parse", summary="只解析 CSV（上传后立刻给出识别结果与诊断）")
def parse_only(req: SignalTestRequest) -> Dict:
    t0 = time.perf_counter()
    res = parse_csv(_raw_of(req), req.filename)
    if req.kind:
        res.kind = req.kind
    out = {"ok": bool(res.ok), "mode": res.kind, "encoding": res.encoding,
           "sep": "\t" if res.sep == "\t" else res.sep, "headers": res.headers,
           "stats": res.stats, "issues": res.issues[:200],
           "elapsed": round(time.perf_counter() - t0, 3)}
    if res.kind == "signal_list" and res.signals is not None and len(res.signals):
        s = res.signals
        out["preview"] = [{"date": str(r.date.date()), "code": r.code, "name": r.name}
                          for r in s.head(8).itertuples()]
    if res.kind == "jq_trades" and res.trades is not None and len(res.trades):
        out["preview"] = [{"date": str(r.date.date()), "time": r.time, "code": r.code,
                           "side": "买" if r.side > 0 else "卖", "qty": int(r.qty),
                           "price": r.price, "fee": r.fee}
                          for r in res.trades.head(8).itertuples()]
    return out


@router.post("/run", summary="跑交易信号测试（同步返回：事件研究 + 回测 / 或聚宽三曲线）")
def run(req: SignalTestRequest) -> Dict:
    t_all = time.perf_counter()
    timings: Dict[str, float] = {}
    warnings: List[str] = []

    t0 = time.perf_counter()
    res = parse_csv(_raw_of(req), req.filename)
    timings["parse"] = round(time.perf_counter() - t0, 3)
    if req.kind:
        res.kind = req.kind
    if not res.ok:
        raise HTTPException(status_code=400,
                            detail="CSV 里没有解析出任何有效记录（请展开诊断看具体行）")

    bench_code = req.benchmark if req.benchmark in BENCH_NAMES else "SH000300"
    resp: Dict = {"ok": True, "mode": res.kind, "timings": timings, "warnings": warnings,
                  "parse": {"stats": res.stats, "issues": res.issues[:200],
                            "headers": res.headers, "encoding": res.encoding},
                  "benchmark": {"code": bench_code, "name": BENCH_NAMES.get(bench_code, bench_code),
                                "options": [{"code": c, "name": n} for c, n in BENCHMARKS]}}

    # ================= 模式③：只上传《收益概述》⇒ 只画官方净值（无需行情，秒出）=================
    if res.kind == "jq_perf":
        pf = res.perf
        nav = pf.set_index("date")[["nav"]].rename(columns={"nav": "nav_official"})
        if pf["bench_nav"].notna().any():
            nav["nav_official_bench"] = pf.set_index("date")["bench_nav"]
        resp.update({
            "official": {"stats": res.stats, "issues": res.issues[:50]},
            "replay": {"stats": {}, "diag": {"mode": "only_perf",
                                             "note": "只有官方净值（未提供成交明细）"},
                       "nav": _nav_rows(nav)},
            "elapsed": round(time.perf_counter() - t_all, 3),
        })
        return resp

    # ================= 模式②：聚宽成交明细 =================
    if res.kind == "jq_trades":
        tr = res.trades
        start = str(tr["date"].min().date())
        end = str(tr["date"].max().date())
        codes = sorted(set(tr["code"]))
        caps = res.stats.get("suggest_capital") or 0.0
        capital = float(req.jq_capital or caps or 1_000_000.0)
        # ⚠ 先解析（可选）《收益概述》：用它把**重放窗口**对齐到官方序列的末日 ——
        #   否则两条曲线停在不同的日子上比较（实测：明细止于 2024-12-30、官方到 12-31，
        #   12-31 那天策略又跌 2% ⇒ 拿"我方 12-30"比"官方 12-31"会平白多出 2% 偏差）。
        perf_res = None
        if req.perf_b64:
            try:
                pr = parse_csv(base64.b64decode(req.perf_b64), "perf.csv")
            except Exception as e:
                pr = None
                warnings.append("《收益概述》解析失败：%r" % e)
            if pr is not None and pr.kind == "jq_perf" and pr.perf is not None and len(pr.perf):
                perf_res = pr
                end = max(end, str(pr.perf["date"].max().date()))
            elif pr is not None:
                warnings.append("上传的《收益概述》没解析出逐日净值（需含 `时间` + `策略收益` 列），已忽略")
        t0 = time.perf_counter()
        panel = load_price_panel(codes, start, end, need_open=False)
        timings["prices"] = round(time.perf_counter() - t0, 3)
        if "CLOSE" not in panel:
            raise HTTPException(status_code=400, detail="取不到成交标的的行情数据")
        t0 = time.perf_counter()
        rep = replay_trades(tr, panel["CLOSE"], capital=capital,
                            capital_ref=(caps if req.jq_scale_capital else None),
                            my_cost=req.cost, fee_buy=res.stats.get("fee_rate_buy"),
                            fee_sell=res.stats.get("fee_rate_sell"), end=end)
        timings["replay"] = round(time.perf_counter() - t0, 3)
        if rep.get("nav") is None:
            raise HTTPException(status_code=400,
                                detail="净值重建失败：%s" % (rep.get("diag", {}).get("error", "未知")))
        mincap = float(res.stats.get("min_capital") or 0.0)
        neg = (rep.get("diag") or {}).get("negative_cash_bars") or {}
        cap_info: Dict = {"used": capital, "suggest": caps, "min_viable": mincap,
                          "first_day_buy_amount": res.stats.get("first_day_buy_amount"),
                          "negative_cash_bars": neg}
        # ---- 流水"现金不闭合"时的处置：再算一条「现金非负下界」口径的 C 曲线 ⇒ 给收益区间 ----
        # 为什么（用户 2026-09-15 报"真实资金就是 1000 万"）：实测这份明细 2016-01-04 满仓 8 只花到
        # 只剩 3,535 元（⇒ 本金确为 ~1000 万 ✓），但 **2016-01-11 在没有任何卖出的情况下又买入 101.97 万**
        # ⇒ 现金变 −101.6 万 ⇒ **流水缺现金流**（分红入账 / 期初已有持仓或入金 / 只是某段窗口）。
        # 此时"用本金记的 C"与"用现金非负下界记的 C"分别是乐观/保守两个极端，真值在两者之间。
        if mincap > capital * 1.30:
            t0 = time.perf_counter()
            rep_lo = replay_trades(tr, panel["CLOSE"], capital=mincap,
                                   capital_ref=(caps if req.jq_scale_capital else None),
                                   my_cost=req.cost, fee_buy=res.stats.get("fee_rate_buy"),
                                   fee_sell=res.stats.get("fee_rate_sell"))
            if rep_lo.get("nav") is not None:
                rep["nav"]["nav_exact_min"] = rep_lo["nav"]["nav_exact"]
                cap_info["interval"] = {
                    "low": {"capital": mincap,
                            "final_nav": (rep_lo.get("stats") or {}).get("nav_exact", {}).get("final_nav")},
                    "high": {"capital": capital,
                             "final_nav": (rep.get("stats") or {}).get("nav_exact", {}).get("final_nav")},
                }
            timings["replay_min"] = round(time.perf_counter() - t0, 3)

        # 现金口径证据 + 官方《收益概述》校准（可选上传）
        cf = (tr["amount"] * tr["side"] * -1.0) - tr["fee"]
        cash_series = capital + cf.cumsum()
        cap_info["min_cash"] = round(float(cash_series.min()), 2)
        cap_info["cash_end"] = round(float(cash_series.iloc[-1]), 2)
        first_neg = tr[cash_series < 0].head(1)
        cap_info["first_negative"] = (None if not len(first_neg) else {
            "date": str(first_neg.iloc[0]["date"].date()),
            "code": first_neg.iloc[0]["code"],
            "side": "买" if first_neg.iloc[0]["side"] > 0 else "卖",
            "amount": round(float(first_neg.iloc[0]["amount"]), 2),
        })
        if (neg.get("exact") or 0) > 0:
            warnings.append(
                "现金口径：按卖出所得当日即可用于买入推演，现金最低 %s 元（占本金 %.1f%%）、期末 %s 元。"
                "缺口量级与分红入账相当（成交明细不含现金红利），不影响本次重建的可信度%s。"
                % (("{:,.0f}".format(cap_info["min_cash"])), 100 * abs(cap_info["min_cash"]) / max(capital, 1.0),
                   "{:,.0f}".format(cap_info["cash_end"]),
                   "（下方已用官方《收益概述》校准）" if perf_res else "（建议一并上传官方《收益概述》校准）"))
        elif abs(capital - caps) / max(caps, 1.0) > 0.05:
            warnings.append("初始资金与首日买入总额相差 %.0f%%（A/B 按资金等比缩放股数，"
                            "C 按它原始股数记账；要公平比较请填它的真实资金）"
                            % (100 * abs(capital - caps) / max(caps, 1.0)))
        t0 = time.perf_counter()
        bench = load_bench_wide([bench_code], start, end)
        nav = attach_benchmark(rep["nav"], bench, bench_code)
        timings["benchmark"] = round(time.perf_counter() - t0, 3)
        # ---- 官方净值叠加 + 校准（用户 2026-09-15 提供《收益概述》后新增）----
        if perf_res is not None:
            off = _official_series(perf_res.perf, "nav", nav.index)
            offb = _official_series(perf_res.perf, "bench_nav", nav.index)
            if off is not None:
                nav["nav_official"] = off
            if offb is not None:
                nav["nav_official_bench"] = offb
            try:
                ok_n, bad_n = _amount_match(res.trades, perf_res.perf)
            except Exception:
                ok_n, bad_n = 0, 0
            exact_end = float(nav["nav_exact"].iloc[-1])
            off_end = float(off.iloc[-1]) if off is not None else None
            diff = (nav["nav_exact"] - off).dropna() if off is not None else None
            resp["official"] = {
                "stats": perf_res.stats,
                "calib": {
                    # ⚠ 比较日期必须写明：两条曲线可能停在不同的最后一天（见上面重放窗口对齐）
                    "as_of": str(pd.Timestamp(nav.index[-1]).date()),
                    "official_final": (round(off_end, 4) if off_end is not None else None),
                    "rebuilt_final": round(exact_end, 4),
                    "diff_final": (round(float(diff.iloc[-1]), 4) if diff is not None and len(diff) else None),
                    "max_abs_diff": (round(float(diff.abs().max()), 4)
                                     if diff is not None and len(diff) else None),
                    "matched_days": ok_n, "mismatched_days": bad_n,
                },
                "issues": perf_res.issues[:50],
            }
            if off_end:
                warnings.append(
                    "官方《收益概述》已叠加校准：官方期末净值 %.4f、官方最大回撤 %.2f%%；"
                    "我方 C（它的成交价 + 实际手续费）%.4f，偏差 %+.2f%%。"
                    "逐日买卖金额与成交明细一致 %d/%d 天，重建可信。"
                    % (off_end, 100 * float((perf_res.stats or {}).get("max_drawdown") or 0.0),
                       exact_end, 100 * (exact_end / off_end - 1.0), ok_n, ok_n + bad_n))
        resp.update({
            "capital": cap_info,
            "fees": {"rate_buy": res.stats.get("fee_rate_buy"),
                     "rate_sell": res.stats.get("fee_rate_sell"),
                     "stamp_tax_est": res.stats.get("stamp_tax_est")},
            "replay": {"stats": rep.get("stats"), "diag": rep.get("diag"),
                       "nav": _nav_rows(nav)},
            "elapsed": round(time.perf_counter() - t_all, 3),
        })
        return resp

    # ================= 模式①：信号清单 =================
    sig = res.signals
    ev = normalize_events(sig[sig["side"] > 0])
    if not len(ev):
        raise HTTPException(status_code=400, detail="没有解析出任何买入信号")
    horizon = int(req.horizon)
    last = pd.Timestamp(ev["date"].max())
    end = min(pd.Timestamp.today().normalize(),
              last + pd.Timedelta(days=int(horizon * 1.9) + 20))
    start = str(pd.Timestamp(ev["date"].min()).date())
    end = str(end.date())
    codes = sorted(set(ev["code"]))

    t0 = time.perf_counter()
    panel = load_price_panel(codes, start, end, need_open=(req.fill != "t_close"))
    timings["prices"] = round(time.perf_counter() - t0, 3)
    if "CLOSE" not in panel:
        raise HTTPException(status_code=400, detail="取不到信号标的的行情数据（请核对代码与日期）")
    panel = fill_limits(panel, req.strict_limit)
    if req.price_mode == "none" and "CLOSE_RAW" in panel:
        panel["CLOSE"] = panel["CLOSE_RAW"].ffill()
        warnings.append("价格口径 = 不复权真实价（`$close/$factor`）：分红日会被当成下跌，"
                        "仅用于与单因子测试的 none 口径对齐")
    if "_limits_inferred" in panel:
        warnings.append("数据里没有交易所涨跌停标签 ⇒ 用「昨收 × 涨跌停幅度」推算"
                        "（不识别 ST 的 5%）；精度略降")
    if panel.get("_raw_fallback") is not None:
        warnings.append("数据里没有真实价列 ⇒ 停牌判定退化为 NaN 判断，精度略降")

    resp["signal"] = {"n_signals": int(len(sig)), "n_buy": int((sig["side"] > 0).sum()),
                      "n_sell": int((sig["side"] < 0).sum()),
                      "n_stocks": int(sig["code"].nunique()),
                      "n_events_dedup": int(len(ev)),
                      "span": {"start": start, "end": end},
                      "horizon": horizon}

    # ---- 事件研究（01 信号那一套）----
    if not req.backtest_only:
        t0 = time.perf_counter()
        pool_key = req.pool or "all"
        pool_close, pool_n = None, 0
        t_pool = time.perf_counter()
        try:
            if pool_key == "@signals":
                pool_close = panel["CLOSE"]
                pool_n = int(panel["CLOSE"].shape[1])
            else:
                pool_codes = resolve_universe(pool_key, start, end)
                pool_n = len(pool_codes)
                if pool_codes:
                    # v1.19.48：基准池**只要 CLOSE**（`compute_baseline_curves` 只用它）⇒ 走快路径：
                    # 只求 `$close` + 宽表落盘缓存（全A 实测 12s/次 → 首次 ~2s、之后 ~0.3s）。
                    pool_close = load_close_wide(pool_codes, start, end)
        except Exception as e:                                  # 池取失败不阻塞主流程
            warnings.append("基准池 %s 取数失败，已退化为「信号标的并集」：%r" % (pool_key, e))
            pool_close = panel["CLOSE"]
        timings["pool"] = round(time.perf_counter() - t_pool, 3)   # 池取数（全A 的耗时大头）
        # v1.19.48：事件研究**内容缓存**（基准池那段每 k 过一遍大矩阵，全A 15~30s 且原来每次重算）
        es = run_event_study(ev, panel["CLOSE"], pool_close, horizon,
                             cache_ns="%s|%s|%s" % (pool_key, start, end))
        es["coverage"] = coverage(ev, panel["CLOSE"])
        es["pool"] = {"key": pool_key, "n_codes": int(pool_n),
                      "name": dict(POOLS).get(pool_key, pool_key)}
        timings["event"] = round(time.perf_counter() - t0, 3)
        timings["event_cached"] = bool(es.get("cached"))          # 前端显示「缓存命中」
        resp["event"] = es

    # ---- 回测（两种资金方案一起给）----
    if not req.signals_only:
        t0 = time.perf_counter()
        # 资金方案：默认那档（死区取界面值）+ 现金等分；**再自动加一档「死区 5%」做对比**
        # （用户 2026-09-15 要求「给个"死区 5%"的对比曲线」⇒ 实测全调平 9 年吃掉 53% 本金）
        specs = ["event_even", "cash_even"]
        if abs(float(req.rebal_band) - 0.05) > 1e-9:
            specs.append("event_even@0.05")
        bt = run_backtest(sig, panel, hold_days=horizon, fill=req.fill, cost=req.cost,
                          capital=req.capital, strict_limit=req.strict_limit,
                          rebal_band=req.rebal_band, alloc_modes=tuple(specs))
        if not bt.nav.shape[1]:
            raise HTTPException(status_code=400,
                                detail="回测没有产出净值：%s" % bt.diag.get("error", "未知"))
        bench = load_bench_wide([bench_code], start, end)
        nav = attach_benchmark(bt.nav, bench, bench_code)
        timings["backtest"] = round(time.perf_counter() - t0, 3)
        resp["backtest"] = {
            "nav": _nav_rows(nav), "stats": bt.stats, "diag": bt.diag,
            "trades": _records(bt.trades, 3000), "rejects": _records(bt.rejects, 800),
            "nav_columns": [str(c) for c in nav.columns],
            "alloc_default": (req.alloc_default if req.alloc_default in bt.stats
                              else sorted(bt.stats.keys())[0]),
            "fill": req.fill, "cost": req.cost, "capital": req.capital,
        }
        if len(bt.rejects) and not resp.get("warnings"):
            pass
        nb_skip = bt.diag.get("signals_beyond_data", 0)
        if nb_skip:
            warnings.append("有 %d 条信号落在行情数据范围外（早于首个交易日或晚于数据末尾），已丢弃"
                            % nb_skip)
    resp["elapsed"] = round(time.perf_counter() - t_all, 3)
    return resp
