# -*- coding: utf-8 -*-
"""XLSX 导出接口（因子面板 → 工作簿字节流）。

★ 边界（**这个路由是薄的** ✓）：只做三件事 ——
  ① 从任务状态取出"页面上已经算好的东西"（曲线 / 绩效 / 参数 / 十分位 / 敏感度 / 每期持仓 ✓）；
  ② 按请求里的**当前选择**（档位、曲线口径、基准、成本档）挑出要导的那一档 ✓；
  ③ 交给 `exports.topk_doc.build_doc` 做"面板复刻"映射、`exports.topk` 组装工作簿 ✓。
绝不在这里重算任何统计量 ✗（导出必须**逐位等于**页面上看到的东西 ✓），
也不在这里写文案/排版 ✗（那是 `exports/topk_doc.py`（文案）与 `exports/topk.py`（排版）的事 ✓）。

⚠ 数据来源都靠**任务状态**（内存 dict）：完整结果只保留最近 `_SFT_RESULT_KEEP` 次
  ⇒ 过期任务导出时给**可读提示**（"请重跑一次"）而不是空文件/500 ✓。
"""
from __future__ import annotations

import base64
import time
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import quote

from fastapi import APIRouter, HTTPException
from fastapi.responses import Response
from pydantic import BaseModel

from .. import __version__
from ..exports import spec, xl
from ..exports.event_study import EventStudyExportDoc, build_event_study_workbook, contract_params
from ..exports.event_study import param_pairs_from as es_param_pairs
from ..exports.topk import build_topk_workbook
from ..exports.topk_doc import PanelSource, TIER_KEYS, build_doc
from ..factors.holdings_index import HoldingsIndex
from ..signals.positions import buy_signals_from_trades, positions_from_trades
from .factors import (EventNavRequest, est_export_state, event_study_nav, nav_bt_stash_get,
                      sft_export_state)

router = APIRouter(prefix="/api/factors", tags=["exports"])

XLSX_MIME = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
DEFAULT_TIER = "0.0040"
TIER_LABELS = {"0.0000": "无成本", "0.0040": "往返 0.004", "0.0080": "往返 0.008"}
# 资金方案的中文名（与弹窗里的图例/统计行同措辞 ✓ —— 导出的表要能对上页面）
MODE_LABELS = {"event_even": "事件驱动再平衡（有信号/到期就调平）",
               "cash_even": "现金等分（不主动再平衡）"}


class TopkExportRequest(BaseModel):
    """「导出当前档位的持仓曲线」请求（全部来自前端**当前界面状态** ✓）。"""

    task_id: str = ""                  # 单因子测试任务 id（拿曲线/参数/每期持仓索引 ✓）
    factor_id: str = ""                # 因子 id / 名称 / 表达式（三者任一 ✓）
    horizon: Optional[int] = None      # 预测周期（同一因子多周期时用来定位 ✓）
    item_kind: str = "decile"          # decile（最强分位组）/ topk（固定 K 档）
    item_k: Optional[int] = None       # 固定 K 档的只数（kind=topk 时）
    item_quantile: Optional[int] = None  # 分位档（kind=decile 时，如 10）
    basis: str = "compound"            # 曲线口径：compound（复利，页面默认）/ arithmetic
    benchmark: str = ""                # 基准代码；空 ⇒ 用后端的默认基准（与页面一致 ✓）
    cost_tier: str = ""                # 绩效指标用哪一档；空 ⇒ 默认 0.0040
    # 面板截图（**base64 PNG**，键 `nav` / `decile` ✓）—— 前端按**当前档位**把 recharts 图抓成
    # PNG 传过来 ⇒ 文件里的图与页面像素一致（"所见即所得" ✓）；缺/坏 ⇒ 退回数值页并如实提示 ✓
    shots: Optional[Dict[str, str]] = None


MAX_SHOT_BYTES = 4 * 1024 * 1024        # 单张截图上限（4 MB；正常 25cm 宽的 PNG 约 100~400KB ✓）
_PNG_MAGIC = b"\x89PNG\r\n\x1a\n"


def _decode_shot(b64: Any) -> Tuple[Optional[bytes], Optional[str]]:
    """base64（可含 `data:image/png;base64,` 前缀）→ PNG 字节。

    ⚠ **必须校验**：这是唯一"用户侧原样进文件"的输入 ⇒ 只收 PNG 魔数 + 大小上限
      （否则一个坏 base64 就能让导出 500、或把 GB 级垃圾塞进文件 ✗）。失败返回**可读原因** ✓。
    """
    if not isinstance(b64, str) or not b64.strip():
        return None, None
    s = b64.strip()
    if s.startswith("data:"):
        s = s.split(",", 1)[-1]
    try:
        raw = base64.b64decode(s, validate=False)
    except Exception as e:                                            # noqa: BLE001
        return None, "曲线截图 base64 解码失败（%s）⇒ 本次未附图" % type(e).__name__
    if not raw.startswith(_PNG_MAGIC):
        return None, "曲线截图不是 PNG（已忽略）⇒ 本次未附图"
    if len(raw) > MAX_SHOT_BYTES:
        return None, "曲线截图过大（%.1f MB）⇒ 本次未附图" % (len(raw) / 1048576.0)
    return raw, None


class EventStudyExportRequest(BaseModel):
    """「导出事件研究」请求（全部来自前端**当前界面状态** ✓）。"""

    task_id: str = ""
    factor_id: Optional[str] = None       # 因子表达式/id（单因子测试任务里按表达式取触发明细 ✓）
    hold_days: int = 20                   # 净值曲线的持有周期 ⇒ 第 2 页持仓也按它派生 ✓
    cost: float = 0.004
    capital: float = 1e9
    benchmark: str = "SH000300"
    max_k: Optional[int] = None           # 面板的「最长持有」（回显 + 校验 ✓）
    # 面板截图（键 `curve`/`excess`/`nav`/`dist` ✓），与 topk 同一套（前端抓 recharts SVG ✓）
    shots: Optional[Dict[str, str]] = None


def _find_event_study(state: dict, factor_id: Optional[str]) -> Tuple[Optional[dict], Optional[dict]]:
    """在任务状态里找事件研究结果，返回 `(事件研究结果, 它所在的那一行)` ✓。

    ① 独立「事件研究」任务 ⇒ `result` 本身就是结果（有 `curve` ✓，没有"行" ⇒ 返回 None ✓）；
    ② 单因子测试的"秒开"路径 ⇒ 每个因子行里挂着 `event_study`（按 `factor_id` 挑 ✓，
       **并返回该行** —— 因为因子名/表达式挂在行上，而结果里可能没有 ✗，实测导出成空 ✗）；
    都找不到 ⇒ `(None, None)`（调用方给可读提示 ✓，不抛 ✗）。
    """
    res = state.get("result")
    if isinstance(res, dict) and res.get("curve"):
        return res, None
    want = str(factor_id or "")
    for row in ((res or {}).get("items") or []):
        es = row.get("event_study")
        if not es:
            continue
        keys = (str(row.get("id") or ""), str(row.get("name") or ""), str(row.get("expression") or ""))
        if not want or want in keys:
            return es, row
    return None, None


def _safe_name(text: Any, fallback: str = "factor") -> str:
    """文件名安全化（Windows 不允许 `\\/:*?"<>|` ⇒ 一律换成 `_` ✓）。"""
    s = str(text or "").strip() or fallback
    for ch in '\\/:*?"<>|\r\n\t':
        s = s.replace(ch, "_")
    return s[:60]


def _xlsx_response(data: bytes, filename: str) -> Response:
    """统一下载响应：附带 `X-Export-Filename`（前端显示用；CORS 已 expose ✓）。"""
    return Response(
        content=data,
        media_type=XLSX_MIME,
        headers={
            "Content-Disposition": "attachment; filename*=UTF-8''%s" % quote(filename),
            "X-Export-Filename": quote(filename),
        },
    )


def _find_row(items: List[dict], req: TopkExportRequest) -> dict:
    """在任务结果里定位该因子（+ 该预测周期）那一行 ✓。"""
    want = str(req.factor_id or "")

    def _hit(row: dict, strict_h: bool) -> bool:
        keys = (str(row.get("id") or ""), str(row.get("name") or ""), str(row.get("expression") or ""))
        if want and want not in keys:
            return False
        return (not strict_h) or req.horizon is None or int(row.get("horizon") or 0) == int(req.horizon)

    for strict in (True, False):
        for row in items:
            if _hit(row, strict):
                return row
    raise HTTPException(status_code=404,
                        detail="在任务结果里找不到该因子（可能已被新一次测试覆盖）—— 请重跑该因子后再导出")


def _item_identity(item: dict) -> Tuple[str, int, Optional[int]]:
    """曲线的**档位三元组** `(kind, k, quantile)` —— 找它与取每期持仓都用它 ✓（键同源 ✓）。"""
    kind = str(item.get("kind") or "topk")
    if kind == "decile":
        return kind, 0, (int(item["quantile"]) if item.get("quantile") is not None else None)
    return kind, int(item.get("k") or 0), None


def _find_item(tc: dict, req: TopkExportRequest) -> dict:
    """在 `topk_curves.items` 里定位前端选中的那一档 ✓。"""
    want_kind = str(req.item_kind or "decile")
    for item in (tc.get("items") or []):
        kind, k, quantile = _item_identity(item)
        if kind != want_kind:
            continue
        if want_kind == "topk":
            if req.item_k is not None and int(req.item_k) != k:
                continue
        elif req.item_quantile is not None and int(req.item_quantile) != int(quantile or 0):
            continue
        return item
    raise HTTPException(status_code=400,
                        detail="页面上这个档位没有曲线数据（可能没勾选该 K）—— 请重新选择档位后再导出")


def _pick_benchmark(tc: dict, want: str) -> Optional[dict]:
    """挑基准：给了代码就按代码，没给 ⇒ 用**后端的默认基准**（与页面默认一致 ✓）。"""
    items = (tc.get("benchmarks") or {}).get("items") or []
    code = str(want or (tc.get("benchmarks") or {}).get("default") or "")
    for b in items:
        if str(b.get("code") or "") == code:
            return b
    return None


def _contract_params(row: dict, tc: dict, item: dict, rq: dict, bench: Optional[dict],
                     basis: str, tier: str) -> Dict[str, Any]:
    """导出契约参数（写进隐藏页 ⇒ 回灌侧据此**自动对齐设置** ✓）。"""
    kind, ik, iq = _item_identity(item)
    return {
        spec.P_KIND: spec.KIND_TOPK,
        spec.P_APP_VERSION: __version__,
        spec.P_GENERATED_AT: time.strftime("%Y-%m-%d %H:%M:%S"),
        spec.P_FACTOR_NAME: str(row.get("name") or ""),
        spec.P_FACTOR_EXPR: str(row.get("expression") or ""),
        spec.P_UNIVERSE: str(rq.get("universe") or ""),
        spec.P_START_DATE: str(rq.get("start_date") or ""),
        spec.P_END_DATE: str(rq.get("end_date") or ""),
        spec.P_LABEL_HORIZON: row.get("horizon"),
        spec.P_REBALANCE_PERIOD: tc.get("rebalance_period"),
        # ★ 回灌要用「持仓周期」与「成本」：持仓周期 = 调仓期（每 `_REBAL` 个交易日换股 ✓），
        #   成本 = 绩效用的那一档（曲线本身三档都在 ✓）
        spec.P_HOLD_DAYS: tc.get("rebalance_period") or row.get("horizon"),
        # ★★ v2.1.23（B ②）：成交时点**跟着面板那次测试走** ✓ —— 取任务请求里的 `fill` ✓
        #   （默认 `t1_close` = 老口径 ✓；用户选"次日开盘"⇒ 这里就是 `t1_open` ✓）。
        #   ⚠ 绝不能写死 ✗：文件里必须是"面板**真正用的那个**" ✓，
        #     否则回灌"看起来一样、结果不一样" ✗（这正是 2026-10-11 那串问题的总根源 ✓）。
        spec.P_FILL: str((rq.get("fill") or "t1_close")),
        # ★★ v2.1.76：**持仓曲线导出也把口径写进 `_meta`** ✗→✓（审计待办 ② ✓）——
        #   此前这一路（`/factors/export/topk-xlsx` ✓）**一个口径键都没写** ✗（只在第 1 页人读块 ✓）
        #   ⇒ 拿它回灌只能按页面默认跑 ⇒ "导出的 ≠ 复现的" ✓。取值来源与事件研究导出**同一批** ✓
        #   （`rq` = 那次任务请求 ✓ ⇒ 面板真正用过的参数 ✓）。
        spec.P_PRICE_ADJUST: str(rq.get("price_adjust") or "backward"),
        spec.P_PRICE_ROUND: bool(rq.get("price_round", True)),
        spec.P_SUSPEND_REMOVE: bool(rq.get("suspend_remove", True)),
        spec.P_EXCLUDE_LIMIT_UP_SIGNAL: bool(rq.get("exclude_limit_up_signal", True)),
        spec.P_EXCLUDE_LIMIT_UP_TRADE: bool(rq.get("exclude_limit_up_trade", True)),
        spec.P_EXCLUDE_SUSPENDED: bool(rq.get("exclude_suspended", True)),
        spec.P_EXCLUDE_ST_T1: bool(rq.get("exclude_st_t1", False)),
        spec.P_EXCLUDE_STOCK_GEM: bool(rq.get("exclude_stock_gem", False)),
        spec.P_EXCLUDE_STOCK_KCB: bool(rq.get("exclude_stock_kcb", False)),
        spec.P_COST: float(tier),
        # ★★ v2.1.27：**净值引擎的另三个实参也必须一起搬走** ✗→✓ ——
        #   v2.1.4 只给**事件研究导出**补了这三个键 ✗，持仓曲线这条路径漏了 ✗ ⇒ 实测
        #   （`ai_test/check_period_roll_panel.py`）文件里全是 `null` ⇒ 回灌退回**自己的默认**
        #   （`strict_limit` 尤其致命：null ⇒ False ✗）⇒ 同一批信号跑出
        #   buys 956 vs 面板 959、末值 1.2747 vs 1.2728 ✗（差 0.2%）✓。
        #   ⇒ 补上后两边"逐点一致" ✓（"导出 = 把面板那次回测原样搬走"对**两条导出路径**都成立 ✓）。
        spec.P_STRICT_LIMIT: bool(rq.get("strict_limit", True)),
        spec.P_REBAL_BAND: float(rq.get("rebal_band") or 0.0),
        spec.P_CAPITAL: float(rq.get("capital") or 1e9),
        spec.P_COST_TIER: tier,
        spec.P_CURVE_COST_TIERS: [TIER_LABELS.get(k, k) for k in TIER_KEYS
                                  if k in ((item.get("curves_compound") or item.get("curves") or {}))],
        spec.P_BENCHMARK: str((bench or {}).get("code") or ""),
        spec.P_BASIS: basis,
        spec.P_QUANTILE: (int(iq) if iq is not None else None),
        spec.P_TOPK: (int(ik) if kind == "topk" else None),
    }


@router.post("/export/topk-xlsx", summary="导出单因子测试「持仓期收益曲线」（XLSX）")
def export_topk_xlsx(req: TopkExportRequest) -> Response:
    """把**页面上当前选中的档位/基准**导成 XLSX（第 1 页复刻面板 ✓）：

    第 1 页 = 标题摘要 + 因子与参数（含公式名/公式）+ 曲线口径 + 组合·基准绩效卡
             + 持仓期净值曲线（原生图）+ 黄色口径说明 + 十分位累计收益曲线 + 成本敏感度表 ✓；
    第 2 页 = 每期持仓明细（可拖回「交易信号测试」复现净值曲线 ✓）；
    第 3 页 = 两张图的数据源；第 4 页 = 说明；隐藏页 `_meta` = 机器可读参数 ✓。
    """
    state = sft_export_state(req.task_id) if req.task_id else None
    if state is None:
        raise HTTPException(status_code=404,
                            detail="任务不存在或已过期（结果只保留最近几次）—— 请重跑一次单因子测试再导出")
    result = state.get("result")
    if not result:
        raise HTTPException(status_code=404,
                            detail="该任务的完整结果已被释放（内存治理只保留最近几次）—— 请重跑一次再导出")
    row = _find_row(result.get("items") or [], req)
    tc = row.get("topk_curves") or {}
    if not tc:
        raise HTTPException(status_code=400,
                            detail=str(row.get("topk_curves_error") or
                                       "该因子没有持仓曲线（0/1 二值信号无此曲线，或曲线计算失败）"))
    item = _find_item(tc, req)
    kind, ik, iq = _item_identity(item)
    basis = "compound" if str(req.basis or "").lower().startswith("comp") else "arithmetic"
    tier = str(req.cost_tier or "") if str(req.cost_tier or "") in TIER_KEYS else DEFAULT_TIER
    bench = _pick_benchmark(tc, req.benchmark)

    # ---- 每期持仓（测试时顺手存的索引；零重算 ✓）----------------------------------
    expr = str(row.get("expression") or "")
    hk = HoldingsIndex.from_state(state.get("_hk"))
    per_period = hk.get(expr, row.get("horizon"), kind, ik, iq)
    dates = [str(x) for x in (tc.get("dates") or [])]
    if not dates:
        raise HTTPException(status_code=400, detail="该档位曲线为空（调仓次数不足或数据缺失）")
    hold_rows: List[Dict[str, Any]] = []
    for day, codes in zip(dates, per_period or []):
        n = len(codes or [])
        for seq, code in enumerate(codes or [], start=1):
            hold_rows.append({"rebalance_date": day, "seq": seq, "code": code,
                              "weight": (1.0 / n if n else None), "n_held": n})

    rq = state.get("_req") or {}
    params = _contract_params(row, tc, item, rq, bench, basis, tier)
    notes = [
        "· 曲线 = 「每期等权（1 ÷ 当期只数）持有到下一个调仓日」的**理论累计收益**："
        "不含资金约束、手数取整、涨停买不到等撮合细节 ⇒ 回灌到「交易信号测试」按真实规则重算后"
        "**不会逐位相同**（回灌曲线更接近实盘 ✓，差异主要来自资金/取整/涨停 ✓）。",
        "· 每期持仓取自本次测试算出的名次（**名次升序** ✓）；「序号」= 该只当期的名次序 ✓。",
    ]
    if per_period is None:
        notes.append("· ⚠ 本档位的持仓明细不在任务索引里（任务可能来自旧版本或超出保留上限）"
                     "⇒ 本次只有参数与曲线，没有第 2 页持仓记录 ✗")
    if hk.truncated:
        notes.append("· ⚠ 本次测试的因子/档位较多，持仓索引**只保留了一部分** ⇒ 建议单独重跑该因子再导出 ✓")

    # 面板截图（前端按当前档位渲染的 PNG ⇒ 文件里的图与页面像素一致 ✓）
    shots = req.shots or {}
    nav_png, err1 = _decode_shot(shots.get("nav"))
    dec_png, err2 = _decode_shot(shots.get("decile"))
    for err in (err1, err2):
        if err:
            notes.append("· ⚠ %s。" % err)

    doc = build_doc(PanelSource(
        row=row, item=item, tc=tc, qc=row.get("quantile_curves") or {},
        sens=row.get("topk_sensitivity") or {}, bench=bench, rq=rq, params=params,
        basis=basis, tier=tier, holdings=hold_rows, extra_notes=notes,
        nav_png=nav_png, dec_png=dec_png,
        subtitle="因子公式：%s ｜ 导出时间 %s ｜ 工具版本 v%s ｜ 格式 %s v%s ｜ %s"
                 % (expr or "-", params[spec.P_GENERATED_AT], __version__,
                    spec.FORMAT_ID, spec.FORMAT_VERSION, spec.dataset_line()),
    ))
    filename = "单因子测试_持仓曲线_%s_%s.xlsx" % (_safe_name(row.get("name")), time.strftime("%Y%m%d_%H%M"))
    try:
        data = build_topk_workbook(doc)
    except Exception as e:                                            # noqa: BLE001
        raise HTTPException(status_code=500, detail="生成 XLSX 失败：%s: %s" % (type(e).__name__, e))
    return _xlsx_response(data, filename)


@router.post("/export/event-study-xlsx", summary="导出事件研究（XLSX）")
def export_event_study_xlsx(req: EventStudyExportRequest) -> Response:
    """把**当前弹窗看到的东西**导成 XLSX：

    第 1 页 = 参数与公式 + 触发事件统计 + 面板各区块**截图**（主曲线/超额/净值/分布 ✓）+ 口径说明；
    第 2 页 = **每次调仓的持仓明细**（由回测成交明细派生，两种资金方案分列 ✓）；
    第 3 页 = 说明；隐藏页 `_meta` = 机器可读参数 ✓。

    ⚠ 持仓走的**就是** `/event-study/nav` 那条计算路径（命中缓存 ⇒ 秒回 ✓）——
      不另写一份回测逻辑 ✗（两处实现必然漂移，而"导出的持仓与页面曲线不是同一批股票"是最坏的情况 ✗）。
    """
    tid = str(req.task_id or "")
    state = (sft_export_state(tid) if tid else None) or (est_export_state(tid) if tid else None)
    if state is None:
        raise HTTPException(status_code=404,
                            detail="任务不存在或已过期（结果只保留最近几次）—— 请重跑一次事件研究再导出")
    es, es_row = _find_event_study(state, req.factor_id)
    if es is None:
        raise HTTPException(status_code=404,
                            detail="任务里找不到这次事件研究的结果 —— 请在「事件研究」弹窗里看到结果后再导出")
    k = max(1, min(int(req.hold_days or 20), 250))
    cost, capital = float(req.cost), float(req.capital)
    bench_code = str(req.benchmark or "SH000300")
    # ① 同一条净值路径（内含缓存 ⇒ 秒回 ✓）—— 它会顺手把这次的 bt 登记给导出用 ✓
    nav_res = event_study_nav(EventNavRequest(task_id=tid, factor_id=req.factor_id, hold_days=k,
                                             cost=cost, capital=capital, benchmark=bench_code))
    # ★ v2.1.4：取"面板这次回测的 `bt` **和它的输入**"（同一份登记 ✓）
    stash = nav_bt_stash_get(tid, req.factor_id, k, cost, capital) or {}
    bt = stash.get("bt")
    positions = positions_from_trades(bt.trades) if bt is not None else []
    # ★★ v2.1.4：`_signals` = **面板用的那批信号**（触发日/代码 ✓），不是成交明细里的"买" ✗ ——
    #   成交日已经是"信号日 + 1 个交易日"了，回灌再按 `fill` 成交一次 ⇒ **整体右移一天** ✗
    #   （用户 2026-10-11 追问"难道不该逐位一致"，这就是当天被揪出来的第二处输入错 ✓）。
    sig_df = stash.get("signals")
    buys: List[Dict[str, Any]] = []
    if sig_df is not None:
        for _d, _c in zip(sig_df["date"], sig_df["code"]):
            buys.append({"date": str(_d)[:10], "code": str(_c), "mode": ""})
    if not buys and bt is not None:
        # 老路径兜底（理论上不会走到：`event_study_nav` 一定登记了 signals ✓）
        buys = buy_signals_from_trades(bt.trades)

    shots = req.shots or {}
    png: Dict[str, Optional[bytes]] = {}
    shot_errs: List[str] = []
    for key in ("kpi", "curve", "excess", "nav", "dist"):
        png[key], err = _decode_shot(shots.get(key))
        if err:
            shot_errs.append(err)

    # ★ v2.1.3：因子名/表达式优先取结果里的 `factor`，取不到就取**它所在的那一行** ✓
    #   （实测：独立事件研究任务的 `result` 里没有 `factor` ⇒ 导出成空 ✗，用户文件里
    #    「因子名称 = -」就是这么来的 ✓）
    fac = es.get("factor") or {}
    fac_row = es_row or {}
    fac_name = str(fac.get("name") or fac_row.get("name") or "")
    fac_expr = str(fac.get("expression") or fac_row.get("expression") or "")
    # ★★ v2.1.69：口径归一（**只算一次、两处共用** ✓）—— 既写进隐藏页 `_meta` + 第 1 页参数块 ✓，
    #   也用来把「说明」里那句**硬编码**的"T+1 收盘买入"改成**当时的真实口径** ✗→✓
    #   （用户 2026-10-12："你看曲线截图都写了次日收盘，但你参数填的却是次日开盘" ✓ ——
    #    正文那句是从这里写死的 ✗，与参数页的口径各说各话 ✓）。
    #   三级兜底：**事件研究自己那份 `es.params.fill`** ✓ > 弹窗净值那条链的登记 `stash` ✓ > 默认 ✓。
    #   为什么以 `es` 为准：本文件第 1 页的曲线/超额/分布截图与 KPI **全部来自 `es`** ✓
    #   ⇒ 参数必须与它们一致 ✓；`stash["fill"]` 来自弹窗的净值链 ✗（老前端不发 `fill` ⇒
    #   退回默认 `t1_open` ✗）⇒ 会导出"参数=开盘 ✗ / 曲线=收盘 ✓"的自相矛盾文件 ✓。
    _fill_final = str((es.get("params") or {}).get("fill") or stash.get("fill") or "t1_open")
    _fill_lab = {"t1_open": "T+1 开盘买入", "t1_close": "T+1 收盘买入",
                 "t_close": "信号日收盘买入"}.get(_fill_final, "T+1 收盘买入")
    params = contract_params(
        factor_name=fac_name,
        factor_expression=fac_expr,
        universe=str((es.get("params") or {}).get("universe") or ""),
        start=str((es.get("params") or {}).get("start_date") or ""),
        end=str((es.get("params") or {}).get("end_date") or ""),
        max_k=(es.get("params") or {}).get("max_k"),
        hold_days=k, cost=cost, capital=capital, benchmark=bench_code,
        # ★ v2.1.4：把**面板这次回测真正用的输入**原样写进文件（回灌据此对齐 ⇒ 才有"逐位一致" ✓）
        fill=_fill_final,
        strict_limit=bool(stash.get("strict_limit", True)),
        # ★ T1（v2.8.6）：判停口径也搬进文件 ✓（缺它 ⇒ 回灌只能按页面默认跑 ⇒ "导出的 ≠ 复现的" ✗）
        limit_judge=str(stash.get("limit_judge") or "legacy"),
        rebal_band=float(stash.get("rebal_band", 0.0)),
        app_version=__version__, generated_at=time.strftime("%Y-%m-%d %H:%M:%S"),
    )
    # ★★ v2.1.54：**剔除开关 + 复权口径必须进文件** ✗→✓（用户 2026-10-12："要修。估计是这里
    #   有问题" ✓）。为什么要（实测根因 ✓）：事件研究的**触发组样本**是剔除过「信号日涨停 /
    #   成交日涨停 / 成交日停牌 / ST / 创业板 / 科创板」的 ✓，而旧文件**一个字都没记** ✗
    #   ⇒ 回灌侧只能按**页面默认**跑 ⇒ 多算进"涨停触发、次日一字板买不进"的样本 ✗
    #   ⇒ 同一份信号两个数（实测 4.84% vs 5.9% ✓，且方向吻合：剔掉涨停 ⇒ 均值**抬升** ✓）。
    _esp = dict(es.get("params") or {})
    for _k in (spec.P_EXCLUDE_LIMIT_UP_SIGNAL, spec.P_EXCLUDE_LIMIT_UP_TRADE,
               spec.P_EXCLUDE_SUSPENDED, spec.P_EXCLUDE_ST_T1,
               spec.P_EXCLUDE_STOCK_GEM, spec.P_EXCLUDE_STOCK_KCB, spec.P_PRICE_ADJUST,
               # ★ v2.1.74：把"影响计算"的其余面板参数也搬进文件 ✓（用户 2026-10-12：
               #   "导出的时候有没有漏掉参数" ✓）——过去它们只在第 1 页人读块 ✗ ⇒ 回灌丢口径 ✓。
               spec.P_PRICE_ROUND, spec.P_SUSPEND_REMOVE, spec.P_FREEZE_SUSPENDED,
               spec.P_WARMUP_DAYS):
        if _k in _esp and _esp[_k] is not None:
            params[_k] = _esp[_k]
    point = next((c for c in (es.get("curve") or []) if int(c.get("k") or 0) == k), None)
    kpi: List[Tuple[str, Any, Optional[str]]] = [
        ("触发次数", es.get("n_events"), "0"),
        ("纳入统计", es.get("n_aligned"), "0"),
        ("未对齐", es.get("n_unaligned"), "0"),
        ("最长持有", (es.get("params") or {}).get("max_k"), "0"),
        ("持有 %d 日均值" % k, (point or {}).get("mean"), xl.PCT1),
        ("持有 %d 日中位" % k, (point or {}).get("median"), xl.PCT1),
    ]
    notes = [
        "· 触发样本 = 每个交易日触发的标的；持有期收益按「信号日取信号、T+1 买入、持有 k 个交易日」计 ✓"
        "（与净值曲线同一口径 ✓）。",
        "· 两种资金方案：`event_even` = 只在**买入成交或到期卖出**当天把全部持仓再平衡到等权；"
        "`cash_even` = 新买入在**可用现金**内等分、其余时间不再平衡 ✓。",
        "· ⚠ 未模拟「跌停卖不出」，也未计流动性冲击；成本按往返合计、按调仓日扣 ✓。",
        "· ⚠ 成交明细上限 2 万笔（防内存）⇒ 触发次数极多时，第 2 页持仓可能缺尾部 ✓。",
        # ★ v2.1.54：把**口径**写明白 ✓ —— 这几项决定"触发组到底算哪些样本"，直接改变数字 ✓
        "· 触发组口径（文件里的「剔除信号日涨停 / 剔除成交日涨停 / 剔除成交日停牌 / 剔除成交日ST / "
        "剔除创业板 / 剔除科创板 / 复权口径」✓）：回灌请按**同开关**执行 ✓ —— "
        "旧文件没记这几项 ✗，回灌只能按页面默认 ⇒ 与弹窗**必然对不上** ✗（实测差 1 个百分点 ✓）。",
    ]
    if shot_errs:
        notes.append("· ⚠ %s。" % "；".join(shot_errs))
    if bt is None:
        notes.append("· ⚠ 本次没取到回测成交明细 ⇒ 第 2 页没有持仓记录（请先在弹窗里看一下净值曲线再导出 ✓）")

    doc = EventStudyExportDoc(
        factor_name=fac_name,
        headline="%s · %s~%s · 最长持有 %s 日 · 触发 %s 次（纳入 %s）"
                 % (params[spec.P_UNIVERSE] or "-", params[spec.P_START_DATE] or "-",
                    params[spec.P_END_DATE] or "-", params[spec.P_MAX_K],
                    es.get("n_events"), es.get("n_aligned")),
        subtitle="导出时间 %s ｜ 工具版本 v%s ｜ 格式 %s v%s ｜ 净值持有周期 %d 日 · 成本 %.4f ｜ %s"
                 % (params[spec.P_GENERATED_AT], __version__, spec.FORMAT_ID,
                    spec.FORMAT_VERSION, k, cost, spec.dataset_line()),
        params=params,
        formula_pairs=[("因子名称", fac_name or "-", None),
                       ("因子 ID", str(fac.get("id") or fac_row.get("id") or "-"), None)]
                      + spec.formula_cells(fac.get("source_formula") or fac_expr, fac_expr),
        param_pairs=es_param_pairs(params, skip=(spec.P_FACTOR_NAME, spec.P_FACTOR_EXPR)),
        buy_signals=buys,
        kpi_title="触发事件统计",
        kpi=kpi,
        kpi_png=png.get("kpi"),          # 有面板截图就优先用它（与页面完全一致 ✓）
        sections=[
            # ★ v2.1.69：**口径动态化** ✗→✓（原来写死"T+1 收盘买入" ✗ ⇒ 面板选"次日开盘"时
            #   文件里仍写"收盘"⇒ 与参数页自相矛盾 ✓，用户 2026-10-12 抓出来 ✓）
            # ⚠⚠ v2.1.70：**结尾那个"单位 %"必须写成 `%%`** ✗→✓ —— 老代码把它拼在
            #   `"...单位 %"` 里**没有做格式化**（是纯字面量 ✓）；我 v2.1.69 改成 `%` 格式化后，
            #   那个裸 `%` 被当成转换符 ⇒ `ValueError: incomplete format` ✗ ⇒ 用户点导出
            #   直接吃「服务内部错误: incomplete format」✓（用户 2026-10-12 实测 ✓）。
            ("持有期收益曲线", "%s，持有 k 个交易日；单位 %%" % _fill_lab, png.get("curve")),
            ("超额曲线（触发组 − 基准·未触发组）", "单位 %", png.get("excess")),
            ("净值曲线（两种资金方案 + 基准）",
             "持有周期 %d 交易日 · 成本（往返）%.4f · 初始资金 %s" % (k, cost, _money(capital)),
             png.get("nav")),
            ("持有 %d 日收益分布" % k, "每次触发一个样本；纵轴 = 该区间事件占比", png.get("dist")),
        ],
        # v2.1.1：净值曲线的**收益/回撤统计**（面板上那是 hover tooltip ⇒ 导出必须给成表 ✓）
        nav_stats_headers=["资金方案", "期末净值", "累计收益", "年化", "最大回撤", "夏普", "卡玛", "成交笔数"],
        nav_stats_rows=[
            [MODE_LABELS.get(str(mode), str(mode)),
             (st or {}).get("final_nav"), (st or {}).get("total_return"),
             ((st or {}).get("perf") or {}).get("annual_return"),
             ((st or {}).get("perf") or {}).get("max_drawdown"),
             ((st or {}).get("perf") or {}).get("sharpe"),
             ((st or {}).get("perf") or {}).get("calmar"),
             (st or {}).get("trades")]
            for mode, st in (nav_res.get("stats") or {}).items()
        ],
        notes=notes,
        holdings=positions,
        holdings_note=("· 第 2 页持仓由成交明细派生（买 − 卖 ✓），共 %d 行 / 方案 %s ✓"
                       % (len(positions), "、".join(sorted({str(p.get("mode")) for p in positions})) or "-")
                       if positions else ""),
    )
    filename = "事件研究_%s_%s.xlsx" % (_safe_name(params[spec.P_FACTOR_NAME]),
                                     time.strftime("%Y%m%d_%H%M"))
    try:
        data = build_event_study_workbook(doc)
    except Exception as e:                                            # noqa: BLE001
        raise HTTPException(status_code=500, detail="生成 XLSX 失败：%s: %s" % (type(e).__name__, e))
    return _xlsx_response(data, filename)


def _money(v: Any) -> str:
    try:
        return "{:,.0f}".format(float(v))
    except (TypeError, ValueError):
        return "-"
