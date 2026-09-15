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

import time
from typing import Dict, List, Optional

import pandas as pd
from fastapi import APIRouter, HTTPException

from ..factors.benchmark_curves import BENCHMARKS, BENCH_NAMES
from ..signals.engine import attach_benchmark, run_backtest
from ..signals.event import coverage, normalize_events, run_event_study
from ..signals.models import SignalTestRequest
from ..signals.parsers import parse_csv
from ..signals.pricing import (fill_limits, load_bench_wide, load_price_panel,
                              resolve_universe)
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


def _raw_of(req: SignalTestRequest):
    """请求体 → 原始字节/文本（base64 优先：GBK 文件必须由后端嗅探解码，不要在前端猜）。"""
    if req.content_b64:
        import base64

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

    # ================= 模式②：聚宽成交明细 =================
    if res.kind == "jq_trades":
        tr = res.trades
        start, end = str(tr["date"].min().date()), str(tr["date"].max().date())
        codes = sorted(set(tr["code"]))
        caps = res.stats.get("suggest_capital") or 0.0
        capital = float(req.jq_capital or caps or 1_000_000.0)
        t0 = time.perf_counter()
        panel = load_price_panel(codes, start, end, need_open=False)
        timings["prices"] = round(time.perf_counter() - t0, 3)
        if "CLOSE" not in panel:
            raise HTTPException(status_code=400, detail="取不到成交标的的行情数据")
        t0 = time.perf_counter()
        rep = replay_trades(tr, panel["CLOSE"], capital=capital,
                            capital_ref=(caps if req.jq_scale_capital else None),
                            my_cost=req.cost, fee_buy=res.stats.get("fee_rate_buy"),
                            fee_sell=res.stats.get("fee_rate_sell"))
        timings["replay"] = round(time.perf_counter() - t0, 3)
        if rep.get("nav") is None:
            raise HTTPException(status_code=400,
                                detail="净值重建失败：%s" % (rep.get("diag", {}).get("error", "未知")))
        t0 = time.perf_counter()
        bench = load_bench_wide([bench_code], start, end)
        nav = attach_benchmark(rep["nav"], bench, bench_code)
        timings["benchmark"] = round(time.perf_counter() - t0, 3)
        mincap = res.stats.get("min_capital") or 0.0
        neg = (rep.get("diag") or {}).get("negative_cash_bars") or {}
        if (neg.get("exact") or 0) > 0:
            warnings.append("初始资金 %.0f 小于「现金非负」下界 %.0f（C 曲线有 %d 根 K 线现金为负）"
                            "⇒ C 等效于加了杠杆、收益被高估；请把「聚宽初始资金」填成 ≥ %.0f"
                            "（或它策略的真实资金）" % (capital, mincap, neg["exact"], mincap))
        elif abs(capital - caps) / max(caps, 1.0) > 0.05:
            warnings.append("初始资金与「首日买入总额」相差 %.0f%%（A/B 按资金等比缩放股数，"
                            "C 按它原始股数记账；要公平比较请填它的真实资金）"
                            % (100 * abs(capital - caps) / max(caps, 1.0)))
        resp.update({
            "capital": {"used": capital, "suggest": caps, "min_viable": mincap,
                        "first_day_buy_amount": res.stats.get("first_day_buy_amount"),
                        "negative_cash_bars": neg},
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
        try:
            if pool_key == "@signals":
                pool_close = panel["CLOSE"]
                pool_n = int(panel["CLOSE"].shape[1])
            else:
                pool_codes = resolve_universe(pool_key, start, end)
                pool_n = len(pool_codes)
                if pool_codes:
                    pp = load_price_panel(pool_codes, start, end, need_open=False)
                    pool_close = pp.get("CLOSE")
        except Exception as e:                                  # 池取失败不阻塞主流程
            warnings.append("基准池 %s 取数失败，已退化为「信号标的并集」：%r" % (pool_key, e))
            pool_close = panel["CLOSE"]
        es = run_event_study(ev, panel["CLOSE"], pool_close, horizon)
        es["coverage"] = coverage(ev, panel["CLOSE"])
        es["pool"] = {"key": pool_key, "n_codes": int(pool_n),
                      "name": dict(POOLS).get(pool_key, pool_key)}
        timings["event"] = round(time.perf_counter() - t0, 3)
        resp["event"] = es

    # ---- 回测（两种资金方案一起给）----
    if not req.signals_only:
        t0 = time.perf_counter()
        bt = run_backtest(sig, panel, hold_days=horizon, fill=req.fill, cost=req.cost,
                          capital=req.capital, strict_limit=req.strict_limit,
                          rebal_band=req.rebal_band)
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
