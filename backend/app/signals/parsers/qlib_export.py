# -*- coding: utf-8 -*-
"""读回**本工具自己导出的 XLSX**（v2.1.0；用户 2026-10-11 需求 ✓）。

需求原话：「把单因子测试导出的 XLSX 文件拖进去，它能识别出第二页的记录，然后复现出净值曲线。
注意因为咱们自己导出的文件第一页有参数设置，所以拖进去它识别到第一页的参数时，
会**自动把参数设置对齐**。」

★ 设计（**绝不重写解析** ✓）：
  · 读盘完全交给 `app.exports.spec.read_export()`（导出契约的**唯一**读取器 ✓）——
    这里只做"**导出文档 → 本模块统一中间结构**"的适配（列映射/选方案/告警搬运 ✓）；
  · 返回的 `kind` 仍写 **`signal_list`**（每期持仓 = 逐次调仓的**买入信号** ✓）⇒ 下游
    「事件研究 + 回测」那一整条链路**一行都不用改** ✓ —— 少一条分叉就少一处漂移 ✓；
  · `origin="qlib_export"` + `aligned={…}` 供界面区分并"自动对齐参数" ✓。

口径说明（导出文件的第 3 页也写了）：导出的是**每期等权持仓**（不含资金约束/手数取整），
回灌后按真实撮合重算 ⇒ 这条曲线**不会与第 1 页那条逐位相同**（更接近实盘 ✓），
差异主要来自资金、取整、涨停买不到 ✓。
"""
from __future__ import annotations

import io
import zipfile
from typing import Any, Dict, List, Optional, Tuple

import pandas as pd

from ...exports import spec
from ..codes import normalize_code
from .base import ParseResult

_KINDS = (spec.KIND_TOPK, spec.KIND_EVENT_STUDY)


def looks_like_export(raw: Any) -> bool:
    """**轻量**嗅探：是不是本工具导出的工作簿（不整本读 ✗ —— 大文件能到 1MB+ ✓）。

    判据：xlsx 魔数 ✓ + `xl/workbook.xml` 里有隐藏页 `_meta`（工作表名在这一层就看得见 ✓）。
    真正的格式校验交给 `read_export`（会检查 `format_id` / `kind` ✓）。
    """
    b = bytes(raw) if isinstance(raw, (bytes, bytearray)) else None
    if not b or b[:4] != b"PK\x03\x04":
        return False
    try:
        with zipfile.ZipFile(io.BytesIO(b)) as z:
            wb = z.read("xl/workbook.xml").decode("utf-8", "ignore")
    except Exception:                                              # noqa: BLE001
        return False
    return ('name="%s"' % spec.SHEET_META) in wb or spec.SHEET_META in wb


def _aligned_of(doc) -> Dict[str, Any]:
    """导出文件第一页的参数 → 界面可对齐的一套（键名与契约一致 ✓）。"""
    return {
        "kind": doc.kind,
        "origin_label": ("单因子测试 · 持仓曲线" if doc.kind == spec.KIND_TOPK else "事件研究"),
        "factor_name": doc.p(spec.P_FACTOR_NAME, ""),
        "expression": doc.p(spec.P_FACTOR_EXPR, ""),
        "universe": doc.p(spec.P_UNIVERSE, ""),
        "start": doc.p(spec.P_START_DATE, ""),
        "end": doc.p(spec.P_END_DATE, ""),
        "horizon": doc.p(spec.P_HOLD_DAYS),          # ★ 界面的「持仓周期」✓
        # ★ 界面的「成交时点」= **面板净值引擎真正用的**那个（v2.1.4 ✓；见 spec.P_FILL 注释里
        #   两套口径的区别 ✗→✓）。老文件没有这个键 ⇒ 退回 `t1_close`（老文件本来就是那个口径 ✓）
        "fill": doc.p(spec.P_FILL, "t1_close"),
        # ★ v2.1.4：净值引擎的另两个实参（缺了就是"同名不同回测" ✗）
        "strict_limit": doc.p(spec.P_STRICT_LIMIT),  # 严格涨跌停 ✓
        # ★ T1（v2.8.6）：判停口径也要**读回来对齐** ✗→✓（老文件没有这个键 ⇒ 退回 `legacy` ✓，
        #   与老文件当年的口径一致 ✓ 不凭空改变老结果 ✓）—— 否则"导出的 ≠ 复现的" ✗
        "limit_judge": doc.p(spec.P_LIMIT_JUDGE, "legacy"),
        "rebal_band": doc.p(spec.P_REBAL_BAND),      # 再平衡死区 ✓
        # ★★ v2.1.54（用户 2026-10-12："要修。估计是这里有问题" ✓）：**剔除开关 + 复权口径**
        #   —— 它们决定**触发组样本**⇒ 直接决定事件研究的数字 ✓（实测同一份信号 4.84% vs 5.9% ✓）。
        #   旧文件没有这几个键 ✗ ⇒ `doc.p(..., None)` 返回 None ⇒ 回灌侧据此**不剔除** ✓
        #   （与旧文件当年的口径一致 ✓，不凭空改变老结果 ✓）。
        "exclude_limit_up_signal": doc.p(spec.P_EXCLUDE_LIMIT_UP_SIGNAL),
        "exclude_limit_up_trade": doc.p(spec.P_EXCLUDE_LIMIT_UP_TRADE),
        "exclude_suspended": doc.p(spec.P_EXCLUDE_SUSPENDED),
        "exclude_st_t1": doc.p(spec.P_EXCLUDE_ST_T1),
        "exclude_stock_gem": doc.p(spec.P_EXCLUDE_STOCK_GEM),
        "exclude_stock_kcb": doc.p(spec.P_EXCLUDE_STOCK_KCB),
        "price_adjust": doc.p(spec.P_PRICE_ADJUST),
        "capital": doc.p(spec.P_CAPITAL),            # 初始资金 ✓（取整/手数会因它而变 ✓）
        "cost": doc.p(spec.P_COST),                  # ★ 成本 ✓
        "benchmark": doc.p(spec.P_BENCHMARK, ""),    # ★ 基准 ✓
        "max_k": doc.p(spec.P_MAX_K),
        "app_version": doc.app_version,
        "generated_at": doc.generated_at,
    }


def _holding_rows(doc) -> Tuple[List[Tuple[str, str]], str]:
    """导出文档 → `[(调仓日, 代码), …]`（**去重**：同日同票只发一次买入信号 ✓）。

    事件研究导出会按资金方案分列 ⇒ 只取**一个**方案（`event_even` 优先 ✓ —— 它才是"有信号就
    调仓"那条，与交易信号测试的默认方案同名同义 ✓）；取了哪个方案由返回值第二个元素告知 ✓。
    """
    rows: List[Tuple[str, str]] = []
    modes = sorted({str(h.get("mode")) for h in doc.holdings if h.get("mode")})
    picked = "event_even" if "event_even" in modes else (modes[0] if modes else "")
    for h in doc.holdings:
        if picked and str(h.get("mode")) != picked:
            continue
        day, code = str(h.get("rebalance_date") or ""), str(h.get("code") or "")
        if day and code:
            rows.append((day, code))
    return sorted(set(rows)), picked


def _approx_buys(doc, mode: str = "") -> List[Tuple[str, str]]:
    """老文件兜底：从"每期持仓快照"推买入点（**近似** ✓，调用方必须提示用户 ✓）。

    · `topk`（持仓曲线）：模型本身就是"每期整体换到新组合、持有到下一个调仓日" ✓
      ⇒ **每期的持仓都算该期买入** ✓（跨期重复出现是正常的再买入 ✓）；
    · `event_study`（事件研究）：真实行为是"触发日买、k 日后卖" ⇒ 取**相邻期差集**（新增=买入 ✓）
      —— 仍测不到"同日调平/卖出又买回" ✗（故为近似 ✓）。
    """
    rows = [h for h in doc.holdings
            if (not mode or str(h.get("mode") or "") == str(mode))
            and h.get("rebalance_date") and h.get("code")]
    if doc.kind == spec.KIND_TOPK:
        return sorted({(str(h.get("rebalance_date")), str(h.get("code"))) for h in rows})
    by_date: Dict[str, set] = {}
    for h in rows:
        by_date.setdefault(str(h.get("rebalance_date")), set()).add(str(h.get("code")))
    out: List[Tuple[str, str]] = []
    prev: set = set()
    for day in sorted(by_date):
        cur = by_date[day]
        out.extend((day, code) for code in sorted(cur - prev))
        prev = cur
    return out


def parse_export(raw: Any, filename: str = "") -> Optional[ParseResult]:
    """读回导出文件 → 统一的 `ParseResult`（失败/不是我们的文件 ⇒ None ✓，由调用方走原管线 ✓）。"""
    doc = spec.read_export(bytes(raw) if isinstance(raw, (bytes, bytearray)) else raw)
    if doc.kind not in _KINDS:
        return None
    # 只要"选了哪个资金方案"；真正的买入点优先取隐藏页（见下 ✓），取不到才用持仓页近似 ✓
    _all_pairs, picked_mode = _holding_rows(doc)
    pairs: List[Tuple[str, str]] = _all_pairs
    res = ParseResult(kind="signal_list", encoding="excel")
    res.origin = "qlib_export"
    res.aligned = _aligned_of(doc)
    res.headers = [zh for _k, zh in spec.HOLDING_COLS.get(doc.kind, ())]
    for w in doc.warnings:
        res.add_issue(0, "", w)

    # ★★ v2.1.3：优先用**隐藏页里的真实买入信号** ✓；老文件没有 ⇒ 退回"快照差集"并**如实提示** ✓
    #   （差集只是近似：同一只票"卖出又买回"、或同日调平仓位都不会被记成新买入 ✗；
    #     用户 2026-10-11 正是拿老文件回灌发现结果对不上 ✓）
    if doc.signals:
        # 新文件（v2.1.3+）：隐藏页给的就是**面板用的那批信号**（触发日/代码 ✓）
        # ⚠ v2.1.4：**不按资金方案过滤** ✗ —— 面板的两种方案跑的是**同一批信号**（区别只在
        #   "怎么分配资金" ✓），按方案过滤会丢掉另一方案的信号 ⇒ 又对不上 ✗。
        pairs = sorted({(str(s.get("date")), str(s.get("code"))) for s in doc.signals
                        if s.get("date") and s.get("code")})
        res.stats["signal_source"] = "file"                     # 来自隐藏页 ✓（准确 ✓）
    else:
        # 老文件（v2.1.3 之前）：只能从"每期持仓快照"近似推断买入点 ✗ ⇒ 必须如实提示 ✓
        pairs = _approx_buys(doc, picked_mode)
        res.stats["signal_source"] = "approx_diff"
        res.add_issue(0, "", "文件里没有「买入信号」隐藏页（v2.1.3 之前导出的旧文件）⇒ 已按"
                             "「持仓快照的相邻期差集」**近似**推断买入点（同日调平、卖出又买回"
                             "测不到 ✗）⇒ **建议重新导出一次**再回灌 ✓")
    if not pairs:
        res.add_issue(0, "", "导出文件的「%s」页里没有可用的持仓记录" % spec.SHEET_HOLDINGS)
        res.stats.update({"rows_total": 0, "rows_valid": 0, "periods": 0})
        return res

    # ★★ v2.1.7：**不要把"每期一批"合成 `bucket`** ✗ —— v2.1.6 我这么干过，是错的 ✗
    #   （用户 2026-10-11 指出："那个按批次分片，咱们的回测里都是一批次的，不存在一周 5 个批次，
    #   所以那个按批次分片的结果也应该等于事件驱动再平衡才对啊" ✓ 实测印证了：
    #   引擎的 `batch_even` = **每批固定 1/N 份额** ✗ ⇒ 新批只占 1/N、旧批到期卖出后变成
    #   **不再投入的现金** ✗ ⇒ 复利大打折扣（图5：`batch_even` +95.38% vs `event_even` +42.2%，
    #   差 53pp ✗ 明显不是同一个东西 ✓）。批次的意义是"**一周 5 个并发批次**"那种场景 ✗，
    #   我们导出的每期组合是**单批次、批间不重叠** ⇒ 语义上就该走 `event_even` ✓。
    #   ⇒ 这里一律**不带 bucket**（`batch_even` 也就不会被启用 ✓）。
    #   ⚠ 遗留：`event_even` 本身"**任何成交日都全仓调平**"也会多收手续费（实测 7.9% ✗）——
    #     那是引擎语义问题，属于"每期全仓滚动"该由引擎新增方案解决 ✓（见开发记录 ✓）。
    buckets: List[str] = []

    codes = []
    for _day, code in pairs:
        try:
            info = normalize_code(code)
            codes.append(info.qlib_code if info is not None and info.qlib_code else code)
        except Exception:                                          # noqa: BLE001
            codes.append(code)                                     # 归一失败就原样用（不因一只票炸整份 ✓）
    sig = pd.DataFrame({
        "date": pd.to_datetime([d for d, _c in pairs]),
        "code": codes,
        "side": 1,                                   # 每期持仓 = 买入信号（卖出由到期/调仓自动产生 ✓）
        "name": codes,
        "raw_date": [d for d, _c in pairs],
        "raw_code": [c for _d, c in pairs],
        "bucket": None,                            # v2.1.7：不带批次 ⇒ 不启用 batch_even ✓
        "weight": 1.0,
    })
    res.signals = sig
    res.stats.update({
        "rows_total": int(len(doc.holdings)),
        "rows_valid": int(len(sig)),
        "buy_signals": int(len(sig)),
        "sell_signals": 0,
        "stocks": int(sig["code"].nunique()),
        "periods": int(doc.n_periods()),
        "from_export": True,
        "export_kind": doc.kind,
        "export_mode": picked_mode,
        "filename": filename,
    })
    modes = sorted({str(h.get("mode")) for h in doc.holdings if h.get("mode")})
    if len(modes) > 1:
        res.stats["modes_in_file"] = modes
        res.add_issue(0, "", "文件含 %d 个资金方案（%s），本次只用「%s」的持仓 ✓"
                      % (len(modes), "、".join(modes), picked_mode))
    return res
