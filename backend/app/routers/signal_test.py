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
from ..signals.engine import attach_benchmark, nav_rows as _nav_rows, run_backtest
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


# ⚠ `_nav_rows` 已移到 `signals/engine.nav_rows`（v1.19.60）—— 单因子「事件研究」面板也要画
#   净值曲线 ⇒ 序列化只留一份。上面用 import 别名，调用点一行都不用改。


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


def _apply_excludes(ev: "pd.DataFrame", panel: Dict, flags: Dict, warnings: List[str]):
    """按「剔除开关」裁**触发样本**（★ v2.1.54 ✓）—— 判据与单因子测试/事件研究弹窗**完全一致** ✓。

    为什么必须在回灌侧做（用户 2026-10-12 抓出的根因 ✓）：弹窗那条触发组是**剔过**「信号日(T)
    涨停 / 成交日(T+1)涨停 / T+1 停牌 / ST / 创业板 / 科创板」的 ✓，而旧导出文件**一个字都没记** ✗
    ⇒ 回灌只能全算 ⇒ 多进"涨停触发、次日一字板买不进"的样本 ✓（这类样本次日往往冲高 ⇒ 剔掉
    它们会**抬升**均值 ✓）⇒ 实测同一份信号 4.84% vs 5.9% ✓、方向吻合 ✓。

    判据（对齐 `factors/event_study.py` 的 `_exclude` ✓）：
      · T 涨停：`close[T] ≥ LIMIT_UP[T]` ✓
      · T+1 涨停：同式在 T+1 ✓（一字板买不进 ✓）
      · T+1 停牌：`CLOSE_RAW[T+1]` 为 NaN ✓
      · 创业板 / 科创板：代码前缀 `SZ30` / `SH688` ✓
      · ⚠ T+1 ST：需要 IS_ST 字段 —— 本侧面板**没有** ✗ ⇒ 跳过并**如实提示** ✓（不静默 ✗）。
    缺失 `LIMIT_UP`（数据集没有涨跌停字段）⇒ 跳过对应项并提示 ✓（不假装剔了 ✗）。
    """
    import numpy as np

    if ev is None or not len(ev):
        return ev
    cl = panel.get("CLOSE")
    if cl is None or not len(cl) or cl.shape[1] == 0:
        return ev
    codes = ev["code"].astype(str).to_numpy()
    cal = pd.DatetimeIndex(cl.index)
    cols = cl.columns
    pos = cal.get_indexer(pd.DatetimeIndex(pd.to_datetime(ev["date"]).to_numpy()))
    col = cols.get_indexer(pd.Index(codes))
    valid = (pos >= 0) & (col >= 0)
    keep = np.ones(len(ev), dtype=bool)
    n0 = int(len(ev))

    def _arr(df):
        """宽表按「同一日历 + 同一列序」取成 ndarray ✓（缺列即 NaN ✓）。"""
        try:
            return df.reindex(index=cal, columns=cols).to_numpy(dtype=np.float64, copy=False)
        except Exception:                                        # noqa: BLE001
            return None

    p = np.clip(pos, 0, max(len(cal) - 1, 0))
    c = np.clip(col, 0, max(cols.size - 1, 0))
    C = cl.to_numpy(dtype=np.float64, copy=False)
    if flags.get("exclude_stock_gem") or flags.get("exclude_stock_kcb"):
        pre = np.asarray([str(x)[:4] for x in codes], dtype=object)
        if flags.get("exclude_stock_gem"):
            keep &= (pre != "SZ30")
        if flags.get("exclude_stock_kcb"):
            keep &= (pre != "SH688")
    raw = panel.get("CLOSE_RAW")
    if flags.get("exclude_suspended") and raw is not None:
        R = _arr(raw)
        if R is not None:
            q = np.clip(pos + 1, 0, max(len(cal) - 1, 0))
            susp = valid & (pos + 1 < len(cal)) & ~np.isfinite(R[q, c])
            keep &= ~susp
    if flags.get("exclude_limit_up_signal") or flags.get("exclude_limit_up_trade"):
        # ★★ v2.1.58：**改调项目自己的判据 `mark_limit_up`** ✗→✓
        #   （用户 2026-10-12 实测：我这版把 183 → **140** ✗ = 剔掉 23.5% ✗，而弹窗
        #    同一份数据是 **183 → 183**（一条都没剔 ✓）⇒ 说明**我的判据错了** ✗：
        #    我拿「后复权收盘」直接比 `LIMIT_UP` ✗，而 `$limit_up` 是**另一量纲** ✗。
        #    `mark_limit_up` 内部有 `_as_price` 做量纲归一 ✓、优先用交易所标签 ✓、
        #    无标签时回退"昨收×板块幅度四舍五入" ✓ —— 与单因子测试/事件研究**同一函数** ✓）
        try:
            from ..engine.limits import mark_limit_up

            q = np.clip(pos + 1, 0, max(len(cal) - 1, 0))
            meta = pd.DataFrame(index=pd.Index(codes, name="code"))
            # ★★ v2.1.63：**必须用「真实价」列比涨停价** ✗→✓（用户 2026-10-12 实测根因 ✓）
            #   轨迹日志暴露：本次运行 `事件=140` ✗（正常应 183→181 ✓，即只剔 T+1 涨停 2 条 ✓）
            #   —— 43 条被误剔 ✗。原因：`pricing` 自己的文档写着
            #     "判涨停 = **`close_raw` ≥ `limit_up` − 1e-6**"✓ 即 `LIMIT_UP`（`$limit_up`）
            #     是**真实价**量纲 ✗；而 v2.1.54/58 我喂给它的是**后复权** `CLOSE` ✗
            #   ⇒ 后复权价通常**远大于**真实价 ⇒ 几乎天天"涨停" ⇒ 误剔 23.5% ✗✗。
            #   ⇒ 改用 `CLOSE_RAW`（`$close/$factor` = 不复权真实价 ✓，停牌为 NaN ✓）
            #     与 `LIMIT_UP` 同量纲 ✓（与 `single_test._exclude` 的面板内部一致 ✓）。
            craw = panel.get("CLOSE_RAW")
            CRAW = _arr(craw) if craw is not None else None
            if CRAW is None:                      # 没有真实价列 ⇒ **宁可不剔**（不赌量纲 ✓）
                warnings.append("本侧行情取不到「真实价」列 ⇒ 本次**未执行**涨停剔除（避免量纲"
                                "不匹配造成大面积误剔 ✓）；面板/弹窗侧仍会剔 ✓，数字可能略有差异 ✓")
                flags = dict(flags)
                flags["exclude_limit_up_signal"] = False
                flags["exclude_limit_up_trade"] = False
            else:
                meta["CLOSE"] = np.where(valid, CRAW[p, c], np.nan)
            ch = panel.get("CHANGE")
            if ch is not None:
                G = _arr(ch)
                if G is not None:
                    meta["CHANGE"] = np.where(valid, G[p, c], np.nan)
                    meta["T1_CHANGE"] = np.where(valid & (pos + 1 < len(cal)), G[q, c], np.nan)
            lu = panel.get("LIMIT_UP")
            if lu is not None:
                L = _arr(lu)
                if L is not None:
                    meta["LIMIT_UP"] = np.where(valid, L[p, c], np.nan)
                    meta["T1_LIMIT_UP"] = np.where(valid & (pos + 1 < len(cal)), L[q, c], np.nan)
            if "CHANGE" not in meta.columns:
                warnings.append("本侧行情没有涨跌幅字段 ⇒ 涨停剔除只能靠标签列（可能少剔 ✓）")
            if flags.get("exclude_limit_up_signal") and "CLOSE" in meta.columns:
                hit = np.asarray(mark_limit_up(meta, "CLOSE", "CHANGE"), dtype=bool)
                keep &= ~hit
            if flags.get("exclude_limit_up_trade"):
                meta["T1_CLOSE"] = (np.where(valid & (pos + 1 < len(cal)), CRAW[q, c], np.nan)
                                    if CRAW is not None else np.nan)
                hit1 = np.asarray(mark_limit_up(meta, "T1_CLOSE", "T1_CHANGE"), dtype=bool)
                keep &= ~hit1
        except Exception as _e:                                     # noqa: BLE001
            warnings.append("涨停剔除执行失败（%r）⇒ 本次未剔除涨停样本 ✓" % (_e,))
    # ★★ v2.1.76：**「剔除成交日(T+1) ST」现在真的执行** ✗→✓（审计待办 ③ ✓）
    #   判据与面板一致：取 **T+1 当日**的 `$is_st`（`pricing.load_price_panel` 新增 ✓，不做 ffill ✓）
    #   ⇒ `is_st > 0.5` 即剔除 ✓。缺字段（没同步 dump 的机器 ✓）⇒ **保留 warning 明说未执行** ✓（不静默 ✗）。
    if flags.get("exclude_st_t1"):
        stw = panel.get("IS_ST")
        if stw is not None and len(stw):
            S = _arr(stw)
            if S is not None:
                q = np.clip(pos + 1, 0, max(len(cal) - 1, 0))
                hit = valid & (pos + 1 < len(cal)) & np.isfinite(S[q, c]) & (S[q, c] > 0.5)
                keep &= ~hit
                # ★★ v2.1.78：**命中 0 条也要报** ✗→✓ —— 2026-10-13 实测：文件里
                #   `exclude_st_t1=True` ✓，但这个因子恰好没触发到 ST 样本 ⇒ 旧写法
                #   （`if hit.any(): 才 append` ✗）**一声不吭** ✓ ⇒ 我根本分不清
                #   "执行了但 0 命中" 还是 "压根没执行" ✗（自证的坑 ✓）。现在恒报条数 ✓。
                warnings.append("按文件口径剔除「成交日 ST」样本 %d 条 ✓（T+1 当日 is_st ✓）"
                                % int(hit.sum()))
            else:
                warnings.append("「剔除成交日 ST」：IS_ST 宽表构造失败 ⇒ 本次未执行该项 ✓")
        else:
            warnings.append("「剔除成交日 ST」需要 IS_ST 字段，本机行情没有（未同步 dump 标签）"
                            "⇒ 本次未执行该项 ✓")
    out = ev.loc[keep].reset_index(drop=True)
    if int(len(out)) != n0:
        warnings.append("按文件口径剔除触发样本：%d → %d 条（信号日涨停/成交日涨停/成交日停牌/"
                        "创业板/科创板 ✓）" % (n0, int(len(out))))
    return out


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
           # v2.1.0：本工具导出的 XLSX ⇒ 带上"来自导出文件"标记与**第 1 页参数**
           # ⇒ 前端拖进来就能**自动把参数设置对齐**（用户 2026-10-11 需求 ✓）
           "origin": res.origin, "aligned": (res.aligned or None),
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
                  # v2.1.0：导出文件回灌 ⇒ 回显"来自哪个导出"与它第 1 页的参数 ✓（前端已据此对齐 ✓）
                  "origin": res.origin, "aligned": (res.aligned or None),
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
        # v1.19.53：A/B 要按"它的委托时间"取价（09:30 → 开盘）⇒ 必须带**开盘价**
        panel = load_price_panel(codes, start, end, need_open=True)
        timings["prices"] = round(time.perf_counter() - t0, 3)
        if "CLOSE" not in panel:
            raise HTTPException(status_code=400, detail="取不到成交标的的行情数据")
        t0 = time.perf_counter()
        rep = replay_trades(tr, panel["CLOSE"], capital=capital,
                            capital_ref=(caps if req.jq_scale_capital else None),
                            my_cost=req.cost, fee_buy=res.stats.get("fee_rate_buy"),
                            fee_sell=res.stats.get("fee_rate_sell"), end=end,
                            open_=panel.get("OPEN"))
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
                                   fee_sell=res.stats.get("fee_rate_sell"),
                                   open_=panel.get("OPEN"))
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
                # ⚠ v1.20.32：删掉「（下方已用官方《收益概述》校准）」后缀 —— 下方面板已有校准信息 ✓
                #   保留「建议一并上传…」这条**行动指引**（没上传时确实需要提示 ✓）。
                "缺口量级与分红入账相当（成交明细不含现金红利），不影响本次重建的可信度%s。"
                % (("{:,.0f}".format(cap_info["min_cash"])), 100 * abs(cap_info["min_cash"]) / max(capital, 1.0),
                   "{:,.0f}".format(cap_info["cash_end"]),
                   "" if perf_res else "（建议一并上传官方《收益概述》校准）"))
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
            # ⚠ v1.20.32：删掉这条「官方《收益概述》已叠加校准：…重建可信。」提示 ✗
            #   —— 下方**校准面板**（前端 `official.calib`，含官方期末净值/最大回撤/偏差/逐日一致
            #   天数 ✓）已经完整展示同样信息 ✓，再在 warnings 里重复一遍是冗余 ✓。
            #   ⚠ 校准数据本身（`resp["official"]`）**照常返回** ✓，只是不再往 warnings 里塞文字 ✓。
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
    # ★★ v2.1.5：**区间必须跟着导出文件走** ✗→✓ ——
    #   原来这里把区间"推导"出来（最后一个信号 + horizon×1.9+20 天 ⇒ 跑到数据末端 ✗），
    #   而面板那次回测用的是**请求区间**（事件研究文件：2022-01-01~2023-12-31 ✓）⇒
    #   回灌会**多跑一个多月** ✗：实测 事件研究文件止于 2023-12-29（期末 0.8273 ✓），
    #   回灌跑到 2024-02-05 ⇒ 0.7873 ✗ —— 用户 2026-10-11 报的"净值还是不一样"里，
    #   这一条是**主因**（那段多出来的行情是跌的 ✓）。
    #   本工具导出的文件第一页就带着面板的区间（`start_date`/`end_date` ✓）⇒ 有它就照用 ✓，
    #   没有（普通 CSV / 别人的文件）才退回原推导 ✓（行为兼容 ✓）。
    _al = (getattr(res, "aligned", None) or {})
    if res.origin == "qlib_export" and _al.get("start") and _al.get("end"):
        start, end = str(_al["start"])[:10], str(_al["end"])[:10]
    else:
        last = pd.Timestamp(ev["date"].max())
        end = min(pd.Timestamp.today().normalize(),
                  last + pd.Timedelta(days=int(horizon * 1.9) + 20))
        start = str(pd.Timestamp(ev["date"].min()).date())
        end = str(end.date())
    codes = sorted(set(ev["code"]))

    t0 = time.perf_counter()
    panel = load_price_panel(codes, start, end, need_open=(req.fill != "t_close"))
    timings["prices"] = round(time.perf_counter() - t0, 3)
    # ★★ v2.1.72：**按导出文件里的「复权口径」换价表** ✗→✓
    #   （用户 2026-10-12："不复权，这里黄金坑我测下来信号测试还是有问题啊" ✓）
    #   根因（实测代码 ✓）：`signals/pricing.load_price_panel` **永远**取 `$close` = **后复权**
    #   （见其 `FIELDS` ✓），而文件里记的 `price_adjust` 在**信号测试整条链上没人消费** ✗
    #   （`price_adjust` 在 `signals/pricing.py` / `routers/signal_test.py` 里 **0 命中** ✓）⇒
    #     · 前复权 / 后复权：只差整体缩放 ⇒ 比率口径下**数值等价** ✓ ⇒ 这个 bug 一直看不出来 ✓；
    #     · **不复权**：除权跳空 + 按分取整都在里面 ⇒ 事件收益/曲线/锚点**全都不一样** ✗✓
    #       —— 用户这次测的正是「不复权 + 黄金坑启动」✓（信号测试只剩 2 个红锚点 ✗ ✓）。
    #   做法：`none` ⇒ 用面板里**现成的真实价**（`CLOSE_RAW`/`OPEN_RAW` ✓，停牌 = NaN ⇒
    #   ffill 冻结 ✓）替换 `CLOSE`/`OPEN` ✓；`CLOSE_RAW`/`OPEN_RAW` **本身不动** ✗⇒
    #   停牌判定与「真实价 vs 涨跌停价」比对仍按真实价 ✓（不会被复权价污染 ✓）。
    #   ⚠ 取整到分：面板 `price_round` 默认开 ✓ 且「仅不复权生效」✓ ⇒ 这里照做 ✓
    #     （文件暂**未记录**这个开关 ✗ —— 见 change_log 的待办 ✓）。
    # ★★ v2.1.72 / v2.1.73：`_pa == "none"` ⇒ 准备一张**只给事件研究用**的真实价表 ✓
    #   ⚠⚠ v2.1.73 **纠错**：v2.1.72 我直接把 `panel["CLOSE"]/["OPEN"]` 换成了真实价 ✗
    #   ⇒ **回测/净值**也跟着用不复权价 ✗ ⇒ 除权/送转的跳空被当成真实亏损 ✓
    #   ⇒ 用户实测同一份「不复权」文件：**面板净值期末 1.8031 ✓ 而信号测试只剩 0.831** ✗
    #     （"两个净值曲线也差很多" ✓）—— 这是**我 v2.1.72 引入的回归** ✗，不是老问题 ✓。
    #   正解（口径要分两类 ✗→✓）：
    #     · **比率类**（事件研究：价格比/收益 ✓）⇒ 口径必须跟文件走 ✓（不复权 ⇒ 真实价 ✓）
    #       —— 面板那次算的就是真实价 ✓，"导出的 = 复现的"要求这里一致 ✓；
    #     · **金额类**（回测/净值/持仓 ✓）⇒ **必须用后复权价** ✓ —— 不复权价缺分红送转的股份补偿
    #       ⇒ 净值会被系统性打崩 ✗（与是否"跟随文件"无关 ✓：那是**财务上错误**的口径 ✓）。
    #   ⇒ 现在：**只把真实价表交给事件研究** ✓（`_px_es` ✓），`panel` **原封不动** ✓
    #     留给引擎/净值（= 后复权 ✓ ⇒ 与面板净值同口径 ✓）。
    _pa = str((_al or {}).get("price_adjust") or "").strip().lower()
    # ★ v2.1.74：**「按分取整」也跟着文件走** ✗→✓（审计 #2 ✓：`price_round` 过去没进文件 ⇒
    #   本侧只能硬编码 `.round(2)` ✗ ⇒ 用户若关过它，回灌与面板差一个取整 ✓）。
    #   文件没记（v2.1.74 之前的文件 ✓）⇒ 按面板默认 `True` ✓（与当年一致 ✓）。
    _prv = (_al or {}).get("price_round")
    _pr = True if _prv is None else bool(_prv)
    _px_es: Dict[str, "pd.DataFrame"] = {}
    if _pa == "none":
        _cr = panel.get("CLOSE_RAW")
        if _cr is not None and len(_cr):
            _px_es["CLOSE"] = _cr.ffill().round(2) if _pr else _cr.ffill()   # 真实价（按分取整 ✓）
            _orr = panel.get("OPEN_RAW")
            if _orr is not None and len(_orr):
                _px_es["OPEN"] = _orr.ffill().round(2) if _pr else _orr.ffill()
            # ★★ v2.1.79（用户 2026-10-13 拍板 ✓）：**不复权时"实盘/回测"也要用不复权** ✗→✓
            #   —— 他要的是"**与聚宽对齐**"：一个口径走全链（因子/事件研究/回测/净值 ✓）。
            #   ⇒ 把真实价**同时**灌给 `panel`（引擎/净值/持仓都读它 ✓）：
            #     ⚠ **先浅拷贝** ✗→✓（`load_price_panel` 的返回可能被别处引用 ✓，就地改会外溢 ✓）；
            #     ⚠ `CLOSE_RAW`/`OPEN_RAW` **不动** ✓（停牌判定与"真实价 vs 涨跌停价"比对仍按它 ✓）。
            #   ⚠ 这推翻了我 v2.1.73 的"金额类永远后复权"✗ —— 那条是**我按"财务正确"自己定的** ✓，
            #     与用户的**对齐口径**目标冲突 ✓ ⇒ 以用户为准 ✓（他要在聚宽/益盟的"不复权"下逐位对拍 ✓）。
            panel = dict(panel)
            for _k2, _v2 in _px_es.items():
                panel[_k2] = _v2
            warnings.append("已按导出文件的复权口径取价：**不复权**（真实价%s ✓）"
                            "⇒ **因子/事件研究/回测/净值 全链同口径** ✓（与聚宽「不复权」对齐 ✓）"
                            % ("+ 按分取整" if _pr else "（未取整，按文件的 price_round=False ✓）"))
        else:
            warnings.append("文件要求「不复权」，但本次取价没有真实价列 ⇒ 退回复权价 ✗"
                            "（事件研究会与面板有差异 ✓）")
    # ★★ v2.1.54：**按「剔除开关」裁触发样本** ✗→✓（用户 2026-10-12："要修。估计是这里有问题" ✓）
    #   优先级：**请求显式给 > 导出文件里记的 > False** ✓ ——
    #   ① 新文件（v2.1.54+ 导出）自带开关 ⇒ 自动对齐弹窗口径 ✓ ⇒ "导出的 = 复现的" ✓；
    #   ② 老文件没有这几个键（`_al` 取到 None ✓）⇒ 退回**不剔除** ✓ = 它当年的口径 ✓
    #      （实测老文件仍得 4.8419% ✓ 与改动前逐位一致 ✓，不改动历史结果 ✓）。
    _fl: Dict[str, bool] = {}
    for _k in ("exclude_limit_up_signal", "exclude_limit_up_trade", "exclude_suspended",
               "exclude_st_t1", "exclude_stock_gem", "exclude_stock_kcb"):
        _v = getattr(req, _k, None)
        if _v is None:
            _v = _al.get(_k)
        _fl[_k] = bool(_v)
    # ★★ v2.1.64：**导出的信号本来就是「已剔除」的那批 ⇒ 回灌不得再剔一次** ✗→✓
    #   （用户 2026-10-12 的轨迹日志把这条钉死了 ✓）：
    #   · 轨迹显示本次运行 `事件=140`（v2.1.63 后 142）✗，而弹窗/秒开是 **183** ✓；
    #   · 原因：单因子测试写文件用的 `trig_index_out` 是在 `_exclude(...)` **之后**才采集的
    #     （`single_test.py:675` 剔除 → `682` append ✓）⇒ 导出的 183 条**已经剔过** ✓
    #     ⇒ 我在回灌侧再套一次剔除 = **重复剔除** ✗ ⇒ 183→140 ✗（T+1 涨停只该剔 2 条 ✓）；
    #   · 反证：同一个 183 条、**不再剔**地跑，得到
    #     触发组 5.9835% / 基准 1.5250% / 超额 4.4585% / 事件级 5.1263%、2.4209% ✓
    #     —— 与弹窗 **逐位一致** ✓（这才是"导出的 = 复现的" ✓）。
    #   ⇒ 规则：**event_study 导出的文件**（`_signals` 已剔 ✓）⇒ 不再剔除，只**回显**开关 ✓；
    #     其它来源（普通 CSV / 老文件 / 持仓曲线文件 ✓）⇒ 仍按开关剔除 ✓（那些批没剔过 ✓）。
    _is_es_export = str((_al or {}).get("kind") or "") == "event_study"
    if _is_es_export:
        warnings.append("文件来自「事件研究」导出 ⇒ 隐藏页信号**已按面板口径剔除过** ✓ "
                        "⇒ 本次不再重复剔除（文件记录的开关仅作回显 ✓）")
    else:
        ev = _apply_excludes(ev, panel, _fl, warnings)
    if not len(ev):
        raise HTTPException(status_code=400,
                            detail="按文件/请求的剔除口径裁完后没有剩余触发样本（请检查剔除开关）")
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
        # ★★ v2.1.74：**基准池默认跟文件的股票池** ✓（用户 2026-10-12："信号测试里有没有漏掉" ✓）
        #   —— 面板那次事件研究的"未触发组"= **它自己那个 universe** 里的未触发 ✓
        #   ⇒ 回灌若默认 `all`，样本不同 ⇒ 基准/超额/锚点全偏 ✗
        #   （审计确认：`_al["universe"]` 读了却没用 ✗）。
        pool_key = req.pool or str((_al or {}).get("universe") or "").strip() or "all"
        # ★★ v2.1.52：**事件研究跟随成交时点** ✓（用户 2026-10-12 ✓）——
        #   `t1_open` ⇒ 事件研究也用**开盘**价（与面板/回灌的成交价同一口径 ✓）；
        #   ⚠ **触发组与基准池必须同一种价** ✓，否则"超额"两边不同口径 ✗。
        # ★★ v2.1.74：**成交时点兜底到文件里记的那个** ✗→✓（审计 #9 ✓：过去只认 `req.fill`，
        #   而它**有非空默认** ⇒ 前端不发时**无声退回 `t1_open`** ✗ ⇒ 与面板口径分叉 ✓）。
        _fill = str(req.fill or (_al or {}).get("fill") or "t1_open").strip().lower()
        if _fill not in ("t1_open", "t1_close", "t_close"):
            _fill = "t1_open"
        _pk = "OPEN" if _fill == "t1_open" else "CLOSE"
        # ★★ v2.1.59：**事件研究的价格表要延到区间末之后** ✗→✓（用户 2026-10-12，根因实测 ✓）
        #   现象：弹窗 tooltip k=32 触发组日配对 = **5.9835%** ✓（我把「秒开」整条链跑出来，
        #   5.9835/1.5250/4.4585 与弹窗**逐位一致** ✓），而按文件复算只有 **5.3799%** ✗。
        #   真身：**对齐上的事件数不同** —— 秒开 `n_aligned = 183` ✓（它用**未裁剪面板** ✓，
        #   含 `end_date + es_k + 3` 个交易日的尾部，见 `single_test.py` 1887-1889 行 ✓），
        #   而本链路只取到 `end_date` ✗ ⇒ 贴着区间末的 13 个触发（183−170 ✓）**取不到
        #   T+1+k 价**⇒ 被计成"未平仓"丢掉 ✓ ⇒ 样本不同 ⇒ 触发组/基准/超额全偏 ✗
        #   （方向也对：丢的那批是近期表现好的 ⇒ 5.98 掉到 5.38 ✓）。
        #   ⇒ 这里按**同一口径**延尾 ✓；⚠ 只影响**事件研究**（回测仍按文件区间 ✓，见下）。
        _p2 = None                                            # ★ v2.1.59：延尾后的触发面板 ✓
        _end_ext = end
        try:
            from qlib.data import D as _Dq                     # 与 `load_price_panel` 同一日历 ✓
            _calt = pd.to_datetime(_Dq.calendar())
            _posx = int((_calt <= pd.Timestamp(end)).sum())
            _end_ext = str(_calt[min(_posx + int(horizon) + 3, len(_calt) - 1)].date())
        except Exception:                                     # noqa: BLE001
            _end_ext = end
        # ★★ v2.1.73：**事件研究的价表要按文件口径取** ✗→✓ ——
        #   `_px_es` 非空（文件要求「不复权」✓）⇒ 用它（真实价 + 按分取整 ✓，与面板那次一致 ✓）；
        #   否则用面板里的复权价 ✓（前/后复权比率等价 ⇒ 用哪张都一样 ✓）。
        def _es_px(_pan):
            """事件研究用价表：`_px_es` 非空（文件要求**不复权** ✓）⇒ 真实价 ✓；否则复权价 ✓。

            ⚠⚠ v2.1.75：**绝不能用 `or` 兜底 DataFrame** ✗→✓ —— `df or other` 会触发
              `ValueError: The truth value of a DataFrame is ambiguous` ✗（实测 2026-10-12：
              不复权文件回灌直接 **HTTP 500** ✓）。必须 `is not None` 显式判断 ✓。
            """
            if _px_es:
                _t = _px_es.get(_pk)
                return _t if _t is not None else panel.get(_pk)
            return (_pan or {}).get(_pk)

        trig_px = _es_px(panel)
        if _end_ext != end:
            try:
                _p2 = load_price_panel(codes, start, _end_ext,
                                       need_open=(str(req.fill or "t1_open") != "t_close"))
                # ★ v2.1.73：**延尾后的那张表也要同口径** ✗→✓（否则尾部 k 用真实价、
                #   前面用复权价 ⇒ 同一条曲线两种价 ✓✗）
                # ⚠ v2.1.75：取价时同样**不能用 `or`**（`OPEN_RAW or CLOSE_RAW` ✗ ⇒ 真值歧义 ✓）；
                #   并按文件里的 `price_round` 决定是否取整 ✓（`_pr` ✓）。
                _t2 = None
                if _px_es and _p2:
                    _c2 = _p2.get("CLOSE_RAW")
                    _o2 = _p2.get("OPEN_RAW")
                    if _pk == "OPEN" and _o2 is not None and len(_o2):
                        _t2 = _o2.ffill().round(2) if _pr else _o2.ffill()
                    elif _c2 is not None and len(_c2):
                        _t2 = _c2.ffill().round(2) if _pr else _c2.ffill()
                if _t2 is None:
                    _t2 = _es_px(_p2)
                if _t2 is not None and len(_t2):
                    trig_px = _t2                     # 延尾后的触发组价格表（仅事件研究用 ✓）
                    panel_ext = _p2
            except Exception as _e2:                          # noqa: BLE001
                warnings.append("事件研究取价未能延到区间末之后（%r）⇒ 尾部触发可能未对齐 ✓"
                                % (_e2,))
        if trig_px is None or not len(trig_px):          # 面板没带该列（老路径）⇒ 退回收盘 ✓
            trig_px = panel["CLOSE"]
            _pk = "CLOSE"
        pool_close, pool_n = None, 0
        t_pool = time.perf_counter()
        try:
            if pool_key == "@signals":
                # ★ v2.1.59：优先用**延尾后**的那张（同一批标的 ✓）⇒ 与触发组窗口一致 ✓
                # ★ v2.1.75：**同口径取价** ✗→✓ —— 走 `_es_px`（不复权 ⇒ 真实价 ✓）；
                #   否则"触发组真实价 − 基准复权价"= 混合口径 ⇒ **超额**凭空跑偏 ✗。
                pool_close = (_es_px(_p2) if _p2 else None)
                if pool_close is None or not len(pool_close):
                    pool_close = _es_px(panel)
                if pool_close is None or not len(pool_close):
                    pool_close = panel["CLOSE"]
                pool_n = int(pool_close.shape[1])
            else:
                pool_codes = resolve_universe(pool_key, start, end)
                pool_n = len(pool_codes)
                if pool_codes:
                    # v1.19.48：基准池**只要 CLOSE**（`compute_baseline_curves` 只用它）⇒ 走快路径：
                    # 只求 `$close` + 宽表落盘缓存（全A 实测 12s/次 → 首次 ~2s、之后 ~0.3s）。
                    # ★ v2.1.55：**同一条快路径按口径取价** ✓（v2.1.52 曾在这里把开盘口径的池
                    #   置成 None ✗ ⇒ 用户报"基准/超额整块消失 ✗"）——`$open` 与
                    #   `load_price_panel` 的 OPEN 表达式逐字相同 ⇒ 不会混合口径 ✓。
                    # ★ v2.1.72：**不复权时池也必须用真实价** ✗→✓（同一表达式 `$close/$factor` ✓
                    #   = `pricing.FIELDS` 里 CLOSE_RAW 的写法 ✓）——否则"触发组真实价 − 基准复权价"
                    #   又变成混合口径 ✗（**超额**会凭空跑偏 ✓）。
                    pool_close = load_close_wide(
                        pool_codes, start, _end_ext,
                        price=(("$open/$factor" if _pk == "OPEN" else "$close/$factor")
                               if _pa == "none" else
                               ("$open" if _pk == "OPEN" else "$close")))
        except Exception as e:                                  # 池取失败不阻塞主流程
            warnings.append("基准池 %s 取数失败，已退化为「信号标的并集」：%r" % (pool_key, e))
            pool_close = panel["CLOSE"]
        timings["pool"] = round(time.perf_counter() - t_pool, 3)   # 池取数（全A 的耗时大头）
        # v1.19.48：事件研究**内容缓存**（基准池那段每 k 过一遍大矩阵，全A 15~30s 且原来每次重算）
        # ★ v2.1.72：**复权口径也进缓存命名空间** ✗→✓（`signals/event.py` 的指纹只吃
        #   "形状/日期/列 + fill" ✗ ⇒ 复权换了价格但形状相同 ⇒ 会把后复权的结果当不复权的命中返回 ✗✗，
        #   正是本项目最忌讳的"口径漂移 + 看不见" ✓）。
        es = run_event_study(ev, trig_px, pool_close, horizon,
                             cache_ns="%s|%s|%s|%s|%s" % (pool_key, start, end, _fill,
                                                          _pa or "backward"),
                             fill=_fill)
        # ★★ v2.1.62：**把本次运行的关键数字写进后端日志** ✗→✓（用户 2026-10-12：
        #   "这次确实重算了，但还是老问题" ✓ —— 输入完全一致却对不上，必须区分
        #   "**后端算的**" 与 "**前端显示的**" ✓ ⇒ 这里落一行 ✓，谁点一次"开始测试"
        #   我读日志就知道后端到底算了多少 ✓（前端若显示别的 ⇒ 旧包/旧结果 ✓）。
        try:
            _bl = (es.get("baseline") or {})
            _ks = _bl.get("ks") or []
            _ix = {int(k): i for i, k in enumerate(_ks)}
            _line = []
            for _k in (32, 52, 53):
                _i = _ix.get(_k)
                if _i is None:
                    continue
                _cr = es.get("curve") or []
                _line.append(
                    "k=%d 触发组=%s 基准=%s 中位=%s"
                    % (_k, (_bl.get("trigger_pair") or [None])[_i],
                       (_bl.get("baseline") or [None])[_i],
                       (_cr[_i].get("median") if len(_cr) > _i else None)))
            _log = None
            try:
                from ..logger import get_logger
                _log = get_logger("signals")
            except Exception:                                    # noqa: BLE001
                pass
            _msg = ("[事件研究·本次运行] file=%s 事件=%s 池=%s fill=%s 剔除(T/T+1/停)=%s/%s/%s ｜ %s"
                    % (req.filename, es.get("n_events"), pool_key, _fill,
                       _fl.get("exclude_limit_up_signal"), _fl.get("exclude_limit_up_trade"),
                       _fl.get("exclude_suspended"), " ｜ ".join(_line)))
            if _log is not None:
                _log.info(_msg)
            with open("workdir/signal_es_trace.log", "a", encoding="utf-8") as _f:
                _f.write(_msg + "\n")
        except Exception:                                        # noqa: BLE001
            pass
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
        # v1.19.76：信号里带**批次列**（如同事的 `持仓周期`=W_0..W_4）⇒ 追加「按批次分片」方案：
        # 每批固定份额、批内按份数等权、只在名单刷新时动该批的票（见 engine.run_batch_backtest）
        try:
            if "bucket" in sig.columns and int(sig["bucket"].nunique()) > 1:
                specs.append("batch_even")
        except Exception:                          # 批次信息异常不影响其它方案
            pass
        bt = run_backtest(sig, panel, hold_days=horizon, fill=req.fill, cost=req.cost,
                          capital=req.capital, strict_limit=req.strict_limit,
                          rebal_band=req.rebal_band, alloc_modes=tuple(specs))
        # ★★ v2.1.8：自家导出文件（形状 = "**每期一批组合**"）⇒ 再补一档
        #   **`period_roll`（每期全仓滚动）** ✓ —— 那才是面板那条"每期等权换仓、
        #   持有到下一个调仓日"曲线的实盘口径（含资金约束/整手/涨跌停 ✓）。
        #   ⚠ `event_even` 对不上它的原因见 `signals/period_roll.py` 的长注释（重复买入 +
        #     重叠票被当成新买入 ⇒ 多出调仓交易 ✓）；`batch_even` 是"分批固定份额" ✗ 也不对 ✓。
        #   ⚠ 附加信息：失败只记一条 warning，**绝不拖垮主结果** ✓。
        if res.origin == "qlib_export":
            try:
                from ..signals.period_roll import merge_into as _merge_pr
                from ..signals.period_roll import run_period_roll as _run_pr
                bt = _merge_pr(bt, _run_pr(sig, panel, fill=req.fill, cost=req.cost,
                                           capital=req.capital,
                                           strict_limit=req.strict_limit))
            except Exception as _pre:                     # noqa: BLE001
                warnings.append("每期全仓滚动未产出：%r" % (_pre,))
        if not bt.nav.shape[1]:
            raise HTTPException(status_code=400,
                                detail="回测没有产出净值：%s" % bt.diag.get("error", "未知"))
        bench = load_bench_wide([bench_code], start, end)
        nav = attach_benchmark(bt.nav, bench, bench_code)
        timings["backtest"] = round(time.perf_counter() - t0, 3)
        resp["backtest"] = {
            "nav": _nav_rows(nav), "stats": bt.stats, "diag": bt.diag,
            # v1.19.77：明细上限放开到 3 万条（用户要"都显示出来"）；真实总数另给 `rejects_total`
            # ⇒ 前端分页展示 + 可导出 CSV，且能如实说明"共 X 条 / 明细含 Y 条"
            "trades": _records(bt.trades, 20000), "rejects": _records(bt.rejects, 30000),
            "rejects_total": int(bt.diag.get("rejects_total") or len(bt.rejects)),
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
