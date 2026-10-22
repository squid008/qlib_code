# -*- coding: utf-8 -*-
"""把「单因子测试任务状态」映射成 `TopkExportDoc` —— **面板复刻的文案/配色/算法唯一点**。

★ 为什么单独一个模块：
  · `routers/export_xlsx.py` 只负责"取状态 + 挑当前档位"（薄 ✓），而"面板长什么样"
    （文字、分区顺序、配色、指标顺序、分段算法）全在这里 ✓；
  · 用户 2026-10-11 否掉了第一版"数据转储"式导出（"排版太丑"），要求**照面板复刻**
    ⇒ 页面文案此后只有两个消费者（页面 / 这里），改文案时**必须同步改这里** ✓
    （否则就是"页面与文件不一致" ✗ 这种最难发现的问题）。

对照关系（与 `frontend/src/components/TopkCurveModal.tsx` 一一对应）：
  标题栏 → `title/headline`；绩效卡 → `combo_*/bench_*`；图 1 → `nav_*`；
  黄底口径说明 → `note_a`；图 2 → `dec_*`；成本敏感度表 → `sens_*`；每期持仓 → `holdings` ✓。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Sequence, Tuple, cast

from . import spec, xl
from .topk import TopkExportDoc

Pair = Tuple[str, Any, Optional[str]]

# ---------------------------------------------------------------- 与页面一致的视觉常量
TIER_KEYS: Tuple[str, ...] = ("0.0000", "0.0040", "0.0080")
TIER_NAMES: Dict[str, str] = {"0.0000": "无成本", "0.0040": "往返 0.004", "0.0080": "往返 0.008"}
BENCH_NAME = "基准"
# ⚠ 曲线**颜色/线宽/虚线**现在归前端（`TopkCurveModal.tsx` 的 `<Line stroke=...>` ✓）——
#   导出的是**前端截图** ⇒ 配色天然与页面一致 ✓，后端不再维护一份会漂移的配色表 ✗。
SEG_YEARS: Tuple[int, ...] = (2013, 2018, 2021, 2023)   # 页面 `segs` 的起点年集合 ✓
PERF_ORDER: Tuple[Tuple[str, str], ...] = (
    ("annual_return", "年化"), ("max_drawdown", "最大回撤"), ("sharpe", "夏普"),
    ("sortino", "索提诺"), ("calmar", "卡玛"),
)
SENS_HEADERS: Tuple[str, ...] = ("持仓只数", "每期换手", "年化成本（往返 0.004）",
                                 "年化成本（往返 0.008）", "成本吞噬（往返 0.004）", "每期毛收益")


@dataclass
class PanelSource:
    """组装面板所需的一切（路由层从任务状态里取好 ✓）。"""

    row: Dict[str, Any]
    item: Dict[str, Any]                 # 当前选中的曲线档（kind/k/quantile/curves/perf…）
    tc: Dict[str, Any]                   # topk_curves
    qc: Dict[str, Any] = field(default_factory=dict)        # quantile_curves
    sens: Dict[str, Any] = field(default_factory=dict)       # topk_sensitivity
    bench: Optional[Dict[str, Any]] = None                   # 选中的基准（benchmarks.items[]）
    rq: Dict[str, Any] = field(default_factory=dict)         # 原请求参数（_req）
    params: Dict[str, Any] = field(default_factory=dict)     # 契约参数（写隐藏页 ✓）
    basis: str = "compound"              # compound / arithmetic
    tier: str = "0.0040"                 # 绩效成本档
    holdings: List[Dict[str, Any]] = field(default_factory=list)
    # 两张图的**截图**（前端按当前档位渲染的 PNG ⇒ "所见即所得" ✓；缺 ⇒ 文件里会提示并退回数值页 ✓）
    nav_png: Optional[bytes] = None
    dec_png: Optional[bytes] = None
    extra_notes: List[str] = field(default_factory=list)
    subtitle: str = ""


# ---------------------------------------------------------------- 小工具
def _fmt_pct(v: Any, nd: int = 1) -> str:
    """百分比（带正负号，与页面 `signedPct` 一致 ✓）；缺失 ⇒ `-`。"""
    if v is None or not isinstance(v, (int, float)):
        return "-"
    return "%+.{0}f%%".format(nd) % (float(v) * 100)


def _fmt_ratio(v: Any, nd: int = 2) -> str:
    return "-" if v is None or not isinstance(v, (int, float)) else ("%.{0}f".format(nd) % float(v))


def _kpi(perf: Optional[Dict[str, Any]]) -> List[Pair]:
    """绩效卡 5 格（与页面 `PerfCard` 同顺序 ✓；值给**数字** ⇒ Excel 里可排序/可计算 ✓）。"""
    p = perf or {}
    out: List[Pair] = []
    for key, label in PERF_ORDER:
        v = p.get(key)
        out.append((label, (float(v) if isinstance(v, (int, float)) else None),
                    xl.PCT1 if key != "sharpe" and key != "sortino" and key != "calmar" else xl.RATIO))
    return out


def _perf_sub(perf: Optional[Dict[str, Any]], extra: str = "") -> str:
    """绩效卡下的补充行：波动｜日胜率｜样本｜口径（+ 三档年化 ✓）。"""
    p = perf or {}
    bits = ["年化波动 %s" % _fmt_pct(p.get("vol_annual")),
            "日胜率 %s" % _fmt_pct(p.get("win_rate")),
            "样本 %s 个交易日" % (p.get("n_days") if p.get("n_days") is not None else "-"),
            "逐日盯市、rf=0"]
    if extra:
        bits.append(extra)
    return "｜".join(bits)


def _segments(qc: Dict[str, Any]) -> Tuple[List[Tuple[int, float, bool]], int]:
    """「分段（最强−最弱，算累）」——**照抄页面算法**（无成本、算术累加 `cum` ✓）。

    页面（`TopkCurveModal.tsx` L506-552）：起点年 {2013,2018,2021,2023} 去掉早于数据起点的；
    该年起第一个调仓日为起点；`spread = (最强末值−起点值) − (最弱末值−起点值)`、`win = 差 > 0` ✓。
    """
    groups = {g.get("quantile"): g for g in (qc.get("groups") or [])}
    dates = [str(d) for d in (qc.get("dates") or [])]
    gb, gw = groups.get(qc.get("best_quantile")), groups.get(qc.get("worst_quantile"))
    if not dates or not gb or not gw:
        return [], 0
    try:
        y0 = int(str(dates[0])[:4])
    except (TypeError, ValueError):
        y0 = 0
    out: List[Tuple[int, float, bool]] = []
    for y in SEG_YEARS:
        if y < y0:
            continue
        i = next((j for j, d in enumerate(dates) if d >= "%d-01-01" % y), -1)
        if i < 0 or i >= len(dates) - 1:
            continue
        last = len(dates) - 1
        db = float(gb["cum"][last]) - float(gb["cum"][i])
        dw = float(gw["cum"][last]) - float(gw["cum"][i])
        out.append((y, db - dw, db > dw))
    return out, sum(1 for _y, _s, w in out if w)


NOTE_A = (
    "简化估算 · 口径说明（读图后可对照核对）：① 费率是往返（买+卖）合计，按调仓期扣、"
    "只扣实际调仓的股票（首期建仓不计费）；② 买入端已按剔除开关剔除 T/T+1 涨停、T+1 停牌的样本"
    "（与表单剔除开关同口径）；未模拟「跌停卖不出」（无跌停开关），也未计流动性冲击；"
    "③ 曲线 = 每期持有组合的收益（调仓日取信号、T+1 买入、持有到下一个调仓日；调仓期默认 = 预测周期 h）"
    "—— 本次口径：{basis_txt}，以 1 为起点、近似净值但不是逐日盯市净值；"
    "④ 默认档 = 超额收益最强的分位组（逐期等分，只数与固定 K 档略有差异）；"
    "⑤ ⚠ 分红口径差异：上面 5 个指数基准是价格指数、不含分红，而组合与「池内等权」都含分红再投 "
    "⇒ 相对指数的超额里含约 2~3pp/年 的分红差。"
)


# ---------------------------------------------------------------- 主映射
def build_doc(src: PanelSource) -> TopkExportDoc:
    """`PanelSource` → `TopkExportDoc`（**纯映射**，不做取数/不算统计量 ✓）。"""
    row, item, tc, qc, sens = src.row, src.item, src.tc, src.qc, src.sens
    basis_txt = "复利（每期收益滚入本金 = 实盘满仓复投）" if src.basis == "compound" else \
        "算术累加（各期收益直接相加，非实盘净值）"
    rebal = tc.get("rebalance_period")
    horizon = row.get("horizon") or tc.get("horizon")
    n_period = tc.get("n")
    best, worst = qc.get("best_quantile"), qc.get("worst_quantile")
    mono = qc.get("monotonicity")
    kind = str(item.get("kind") or "topk")
    item_label = ("最强分位组 Q%s" % item.get("quantile")) if kind == "decile" else \
        ("固定 K=%s%s" % (item.get("k"), "（默认 10% 档）" if item.get("k") == (sens.get("default_k")) else ""))
    segs, n_win = _segments(qc)
    seg_txt = "".join("%d起 %+.1fpp　" % (y, sp * 100) for y, sp, _w in segs)
    if segs:
        seg_txt += ("（每段都赢 ✓）" if n_win == len(segs) else "（仅 %d/%d 段赢 ✗）" % (n_win, len(segs)))

    # ---------- 标题栏 ----------
    head1 = ("%s　调仓期 %s 交易日（默认 = 预测周期%s）　%s 次调仓" %
             (row.get("name") or "-", rebal if rebal is not None else "-",
              (" h=%s" % horizon) if horizon else "", n_period if n_period is not None else "-"))
    # ⚠ 用户 2026-10-11 要求删掉「（最强/最弱为样本内最优，非样本外）」—— 太长且这句在
    #   面板上本来就是灰色小字补充说明 ⇒ 导出里的摘要行只留"最强 Qx / 最弱 Qy" ✓
    head1 += ("　分位档：最强 Q%s / 最弱 Q%s" % (best, worst)
              if best is not None and worst is not None else "")
    head2 = ("分段（最强−最弱，算累）：%s" % seg_txt if seg_txt else "")
    if mono is not None:
        head2 += ("　十档单调性 ρ=%.2f" % float(mono)
                  + ("（无单调性 ⇒ 别信「最强档」）" if abs(float(mono)) < 0.5 else ""))
    headline = [head1, head2] if head2 else [head1]

    # ---------- 因子与公式 ----------
    formula_pairs: List[Pair] = [
        ("因子名称", row.get("name") or "-", None),
        ("因子 ID", row.get("id") or "-", None),
    ] + spec.formula_cells(row.get("source_formula") or row.get("expression"),
                           row.get("expression"))
    _skip = {spec.P_FACTOR_NAME, spec.P_FACTOR_EXPR, spec.P_KIND, spec.P_APP_VERSION,
             spec.P_GENERATED_AT, spec.P_FORMAT_VERSION}
    param_pairs: List[Pair] = [p for p in spec.param_display_rows(src.params)
                               if not any(p[0] == spec.PARAM_LABELS.get(k) for k in _skip)]
    for label, key, fmt in (("复权方式", "price_adjust", None), ("预热缓冲(交易日)", "warmup_days", "0"),
                            ("分位组数", "quantiles", "0"), ("剔除信号日涨停", "exclude_limit_up_signal", None),
                            ("剔除成交日涨停", "exclude_limit_up_trade", None),
                            ("剔除 T+1 停牌", "exclude_suspended", None),
                            ("剔除创业板", "exclude_stock_gem", None),
                            ("剔除科创板", "exclude_stock_kcb", None),
                            ("价格整分", "price_round", None)):
        if src.rq.get(key) is not None:
            param_pairs.append((label, src.rq[key], fmt))

    # ---------- 绩效卡 ----------
    perf_all = item.get("perf") or {}
    cost_annual = "三档年化：无成本 %s / 0.004 %s / 0.008 %s" % (
        _fmt_pct((perf_all.get("0.0000") or {}).get("annual_return")),
        _fmt_pct((perf_all.get("0.0040") or {}).get("annual_return")),
        _fmt_pct((perf_all.get("0.0080") or {}).get("annual_return")))
    bench_perf = (src.bench or {}).get("perf")

    # ---------- 图 1（净值 = 1 + 累计收益；起点补 1 ✓ —— 与页面 `detailData` 同口径）----------
    cv = (item.get("curves_compound") if src.basis == "compound" else item.get("curves")) \
        or item.get("curves") or {}
    bk = ((src.bench or {}).get("cum_compound") if src.basis == "compound" else
          (src.bench or {}).get("cum")) or (src.bench or {}).get("cum") or []
    dates = [str(d) for d in (tc.get("dates") or [])]
    nav_headers = ["调仓日"]
    nav_series: List[Tuple[str, str]] = []           # (列名, 曲线键) —— 只用来推表头/列数 ✓
    for key in TIER_KEYS:
        if key in cv:
            nav_headers.append(TIER_NAMES[key])
            nav_series.append((TIER_NAMES[key], key))
    has_bench = bool(src.bench) and len(bk) > 0
    if has_bench:
        bname = "%s %s" % (BENCH_NAME, (src.bench or {}).get("name") or (src.bench or {}).get("code") or "")
        nav_headers.append(bname)
    nav_rows: List[List[Any]] = [["起点 = 1"] + [1.0] * (len(nav_headers) - 1)]
    for i, d in enumerate(dates):
        r: List[Any] = [d]
        for key in TIER_KEYS:
            if key in cv:
                v = cv[key][i] if i < len(cv[key]) else None
                r.append(None if v is None else 1.0 + float(v))
        if has_bench:
            v = bk[i] if i < len(bk) else None
            r.append(None if v is None else 1.0 + float(v))
        nav_rows.append(r)

    # ---------- 区间累计（复利：净值比 ✓ —— 与页面 `_pv/_bv/excess` 同口径）----------
    def _last(arr: Sequence[Any]) -> Optional[float]:
        for v in reversed(list(arr)):
            if isinstance(v, (int, float)):
                return float(v)
        return None

    pv, bv = _last(cv.get("0.0000") or []), _last(bk)
    if pv is None or bv is None:
        excess = None
    elif src.basis == "compound":
        excess = None if bv <= -1 else (1 + pv) / (1 + bv) - 1
    else:
        excess = pv - bv
    nav_summary = "区间累计（%s）：组合（无成本）%s｜基准 %s %s｜⇒ 超额%s %s" % (
        "复利" if src.basis == "compound" else "算术", _fmt_pct(pv),
        (src.bench or {}).get("name") or "-", _fmt_pct(bv),
        "（净值比）" if src.basis == "compound" else "", _fmt_pct(excess))
    if int((src.bench or {}).get("n_partial") or 0) > 0:
        nav_summary += "（末期只持有不足 %s 个交易日，两侧同口径用尾部价结算）" % (rebal or "-")
    if bv is None:
        nav_summary += "（基准尾部数据不足，已断线）"

    # ---------- 图 2（累计收益、起点 0；多空走次轴 ✓）----------
    qdates = [str(d) for d in (qc.get("dates") or [])]
    dec_headers = ["调仓日"]
    for g in (qc.get("groups") or []):
        q = int(g.get("quantile") or 0)
        suffix = "（最强）" if q == best else ("（最弱）" if q == worst else "")
        dec_headers.append("Q%d%s" % (q, suffix))
    if qc.get("long_short"):
        dec_headers.append("多空（右轴）")
    bmap = {d: bk[i] for i, d in enumerate(dates) if i < len(bk)}
    has_bench2 = has_bench and bool(bmap)
    if has_bench2:
        dec_headers.append("%s %s" % (BENCH_NAME, (src.bench or {}).get("name") or ""))
    dec_rows: List[List[Any]] = [["起点 = 0"] + [0.0] * (len(dec_headers) - 1)]
    lsv = ((qc.get("long_short") or {}).get("cum_compound") if src.basis == "compound"
           else (qc.get("long_short") or {}).get("cum")) or (qc.get("long_short") or {}).get("cum") or []
    for i, d in enumerate(qdates):
        r = [d]
        for g in (qc.get("groups") or []):
            arr = ((g.get("cum_compound") if src.basis == "compound" else g.get("cum"))
                   or g.get("cum") or [])
            r.append(float(arr[i]) if i < len(arr) and arr[i] is not None else None)
        if qc.get("long_short"):
            r.append(float(lsv[i]) if i < len(lsv) and lsv[i] is not None else None)
        if has_bench2:
            r.append(float(bmap[d]) if bmap.get(d) is not None else None)
        dec_rows.append(r)
    dec_note = ("最强 Q%s / 最弱 Q%s（样本内最优⇒十档不单调时不能当结论）；右轴 = 多空"
                "（最强−最弱，逐期价差%s，起点 0；不可实现，仅作参考）；点图例可显隐曲线" %
                (best, worst, "复利" if src.basis == "compound" else "累加"))

    # ---------- 成本敏感度表 ----------
    dk = sens.get("default_k")
    sens_rows: List[List[Any]] = []
    # ★ v2.11.39：`cast` ✓ —— `sens` 是普通 dict ⇒ `.get("rows")` 取出来是 `object` ✗，
    #   被推成 `list[Any]` 的元素类型后 `r.get(...)` 就报"list 没有 get" ✗（4 条 ✓）。
    # ⚠ 循环变量叫 `_row` 而不是 `r` ✗→✓ —— 本函数前面已经把 `r` 用作 **list** ✓，
    #   同名复用会让 mypy 认为它还是 `list[Any]` ⇒ `r.get(...)` 报"list 没有 get" ✗。
    for _row in cast(List[dict], sens.get("rows") or []):
        _k = _row.get("k")
        _ann, _eaten = _row.get("ann_cost") or {}, _row.get("cost_eaten") or {}
        sens_rows.append([
            ("%s（默认 10%%）" % _k) if (dk is not None and int(_k or 0) == int(dk)) else _k,
            _row.get("avg_turnover"), _ann.get("0.0040"), _ann.get("0.0080"),
            _eaten.get("0.0040"), _row.get("gross_per_period"),
        ])
    sens_note = ("%s　年化按 %s 交易日；持仓只数与明细曲线的固定档一一对应。"
                 % (sens.get("note") or "", sens.get("trading_days") or 252))

    return TopkExportDoc(
        factor_name=str(row.get("name") or ""),
        headline=headline,
        params=src.params,
        formula_pairs=formula_pairs,
        param_pairs=param_pairs,
        basis_note="本次口径：%s　｜　另一口径：%s　　⚠ 两口径不可混比：强策略复利显著高于算术"
                   "（长期可达数十~上百个百分点），弱/无效策略反之。" % (
                       basis_txt,
                       "算术累加（各期收益直接相加）" if src.basis == "compound" else
                       "复利（每期收益滚入本金 = 实盘满仓复投）"),
        cost_tier_label={"0.0000": "无成本", "0.0040": "往返 0.004", "0.0080": "往返 0.008"}.get(src.tier, src.tier),
        combo_title="组合：%s" % item_label,
        combo_kpi=_kpi(perf_all.get(src.tier)),
        combo_sub=_perf_sub(perf_all.get(src.tier), cost_annual),
        bench_title="基准 %s" % ((src.bench or {}).get("name") or "-"),
        bench_kpi=_kpi(bench_perf),
        bench_sub=_perf_sub(bench_perf),
        nav_title="持仓期净值曲线（三档成本 · 同一组合 · 起点 = 1 · %s）" %
                  ("复利" if src.basis == "compound" else "算术累加"),
        nav_switches="切换组合（导出文件中为静态文字，取本次选中的档 ✓）：%s　｜基准：%s" % (
            item_label,
            "　".join(str(b.get("name") or b.get("code") or "")
                      for b in ((tc.get("benchmarks") or {}).get("items") or [])) or "-"),
        nav_note=("每 %s 个交易日调仓一次（调仓日取信号、T+1 买入、持有到下一个调仓日）；" % (rebal or "-")) +
                 ("每日取因子最强端的 1/%s（等分）⇒ 持仓约 %s 只，只数逐期变化" %
                  (qc.get("n_groups") or 10, item.get("k"))
                  if kind == "decile" else "固定持仓 %s 只（按名次取，与分位组口径略有差异）" % item.get("k")) +
                 "；点图例可显隐曲线",
        nav_summary=nav_summary,
        note_a=NOTE_A.format(basis_txt=basis_txt),
        dec_title="十分位累计收益曲线（无成本 · 起点 0）",
        dec_note=dec_note,
        sens_title="成本敏感度：不同持仓只数（换手 / 年化成本 / 吞噬比例）",
        sens_headers=list(SENS_HEADERS),
        sens_rows=sens_rows,
        sens_note=sens_note,
        nav_png=src.nav_png,
        dec_png=src.dec_png,
        nav_headers=nav_headers,
        nav_data=nav_rows,
        dec_headers=dec_headers,
        dec_data=dec_rows,
        holdings=src.holdings,
        notes=src.extra_notes,
        subtitle=src.subtitle,
    )
