# -*- coding: utf-8 -*-
"""因子库目录接口。

对外提供特征集的"因子名 + 表达式 + 分类 + 描述"目录，供前端勾选特征、
悬停查看公式。设计为可扩展：未来维护成千上万因子时，只需在
app/factors/catalog.py 的 FACTOR_PROVIDERS 注册新的 provider，接口无需改动。
"""
import threading
import time
import uuid
from typing import List, Optional

import numpy as np
import pandas as pd
from fastapi import APIRouter, HTTPException
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from ..factors.catalog import get_catalog, FACTOR_PROVIDERS
from ..factors.parser import (
    translate_formula, build_library, LexerError, ParseError, SemanticError, CodeGenError,
)
from ..services.custom_formulas import (
    list_custom_formulas as _list_custom_formulas,
    create_custom_formula as _create_custom_formula,
    update_custom_formula as _update_custom_formula,
    delete_custom_formula as _delete_custom_formula,
    find_live_by_name as _find_live_by_name,          # ★ v1.20.82：保存查重 ✓
    mark_dependents_stale as _mark_dependents_stale,  # ★ v1.20.87：依赖自动失效 ✓
    set_pinned as _set_pinned,                        # ★ v2.1.45：置顶/取消置顶 ✓
)
from .. import config
from ..engine.task_manager import get_task_manager
from ..factors.single_test import FactorTestCancelled, _bad_date_arg, run_single_factor_tests
from ..factors.event_study import run_event_study
from ..factors.holdings_index import HoldingsIndex      # v2.1.0：每期持仓索引（XLSX 导出用 ✓）
# ★ v2.7.0：含入场价（ENTRY_PRICE / ENTRY_FIRST）的离场公式 ⇒ 拆成「预计算列 + 骨架」✓
#   （与入场价无关的部分当普通字段列由面板算 ⇒ 任意字段/算子都能用 ✓，骨架回测逐日代入 ✓）
from ..factors.exit_rules import EntryRule, split_entry_rule, uses_entry

router = APIRouter(prefix="/api/factors", tags=["factors"])


class TranslateRequest(BaseModel):
    formula: str = ""          # 益盟/通达信公式文本（允许整段粘贴，含 := 中间变量，1 条输出线）
    patchable: bool = False    # 是否允许外挂算子占位（默认 False，含外挂算子报错）


class CustomFormulaBody(BaseModel):
    formula: str = ""          # 与 TranslateRequest 一致：用户原文公式
    patchable: bool = False
    allow_duplicate: bool = False   # ★ v1.20.82：明知同名也要新建一条 ⇒ 跳过查重提示 ✓


def _formula_library(exclude_id: str = None) -> dict:
    """公式库（供「公式间调用」，v1.19.87）：已保存公式的 `名 → 原文`。

    ⚠ 编辑某条公式时要**把自己排除**（`exclude_id`）—— 否则它引用同名公式会立刻变成
      "循环引用"（用户只是想改自己的正文）✓。
    """
    items = _list_custom_formulas()
    return build_library([i.get("text", "") for i in items if i.get("id") != exclude_id])


def _compile_formula_or_400(formula: str, patchable: bool = False, exclude_id: str = None):
    """编译公式；成功返回 TranslatedFactor，失败抛 HTTPException(400)。"""
    if not formula or not formula.strip():
        raise HTTPException(status_code=400, detail="公式不能为空")
    try:
        return translate_formula(formula, patchable=patchable, library=_formula_library(exclude_id))
    except (LexerError, ParseError, SemanticError, CodeGenError) as e:
        raise HTTPException(status_code=400, detail=str(e))


def _dup_conflict(mode: str, name: str, dup: dict) -> JSONResponse:
    """★ v1.20.82：**同名公式**的 409 冲突响应（供前端弹"覆盖 / 仍新建 / 取消"✓）。

    ⚠ `detail` 保持**字符串** ✓ —— 前端历史代码是 `String(detail)` 直接显示 ✓，
      塞 dict 会渲染成 `[object Object]` ✗✗；结构化信息因此挂在**同级 `conflict`** ✓
      （新前端读它渲染选择按钮 ✓，旧前端只看到人话提示 ✓ 双向兼容 ✓）。

    `mode`：`create` = 新建撞名（可"仍新建一条" ✓）；`update` = 编辑改成别人的名字
    （几乎都是改错名 ⇒ **只拦不给新建** ✓，否则等于把列表里的重名又造一遍 ✗）。
    """
    who = ("，作者 %s" % dup.get("author")) if dup.get("author") else ""
    if mode == "create":
        hint = ("请点「覆盖它」（把新正文写进那一条）/「仍新建一条」（确实要两条并存）/「取消」，"
                "或换个输出名再存。")
    else:
        hint = "请换个输出名，或先在列表里删掉那条同名公式。"
    return JSONResponse(status_code=409, content={
        "detail": (
            "已存在同名公式「%s」%s：id %s，%s 保存。\n"
            "公式名是平台里唯一可寻址的键（INSUM、公式间调用、列表都只认名字）"
            "⇒ 同名两条并列会让「引用取到哪条」变得不确定。%s"
            % (dup.get("name") or name, who, dup.get("id"),
               dup.get("updated_at") or "时间未知", hint)
        ),
        "conflict": {
            "id": dup.get("id"),
            "name": dup.get("name"),
            "updated_at": dup.get("updated_at"),
            "text": dup.get("text"),
            "author": dup.get("author"),
        },
    })


@router.post("/translate", summary="翻译益盟/通达信公式为 qlib 表达式")
def translate(req: TranslateRequest):
    """把一段益盟/通达信公式翻译成单个输出因子。

    成功：{name, expression, inputs, has_patch, source_formula}
    失败：400 + 中文错误提示（LexerError/ParseError/SemanticError/CodeGenError）
    """
    if not req.formula or not req.formula.strip():
        raise HTTPException(status_code=400, detail="公式不能为空")
    try:
        t = translate_formula(req.formula, patchable=req.patchable, library=_formula_library())
        return {
            "name": t.name,
            "expression": t.expression,
            "inputs": t.inputs,
            "has_patch": t.has_patch,
            "source_formula": t.source_formula,
        }
    except (LexerError, ParseError, SemanticError, CodeGenError) as e:
        raise HTTPException(status_code=400, detail=str(e))


# ---------- 自定义公式持久化（workdir/custom_formulas.json） ----------

@router.get("/custom-formulas", summary="列出已保存的自定义公式")
def list_saved_formulas():
    """返回全部已保存的自定义公式（含原文 text 与编译后的 expression）。"""
    return {"items": _list_custom_formulas()}


def _market_impact_warning(name: str, text: str = "") -> str:
    """★ v1.20.89：保存的公式被**横向统计物化**（`mkt_*`）引用 ⇒ 当场告知"物化值已过期" ✓。

    用户 2026-10-09：把 `IS_GOLD_PIT:轨迹` 改成 `<10` 后问「物化会有影响吗？**我没有看到提示**」
    ⇒ 影响**有** ✓（`mkt_insum_all_is_gold_pit_...` 仍是 `<0` 口径算的 ✓），而提示**没有任何出口** ✗：
      · ② 的指纹比对只在**后端启动时**跑一次 ✗，保存后不重启永远是旧结论 ✗；
      · 前端**根本没读** `chip_meta`/`market_meta` ✗✗（`grep` frontend 0 处引用 ✓）。
    ⇒ 在**保存这条路径上**直接答一句 ✓（最贴近用户动作的位置 ✓），文案与 `/api/version` 同源 ✓。
    返回空串 = 无影响 ✓（前端只看真假 ✓）。
    """
    try:
        from ..factors.market_stat import materialize_impact, missing_materialized_fields
        stale = materialize_impact(name)            # ① 作为**被调公式**被物化引用、指纹已变 ✓
        gap = missing_materialized_fields(text)     # ② 自身 BLOCKSETNUM/INSUM 用法里有新规格 ✗
    except Exception:                                            # noqa: BLE001
        return ""
    parts: list[str] = []
    if stale:
        parts.append("⚠ 这条公式被横向统计物化引用：%s ⇒ 现有文件是旧口径，需要重新物化。"
                     % "、".join(stale))
    if gap:
        # ★ v1.20.91（用户 2026-10-09：「`BLOCKSETNUM('全部A股')` 改成**中证1000**，不提示吗？」✓）：
        #   换板块 ⇒ 字段名也换（`mkt_num_all` → `mkt_num_csi1000` ✓）⇒ 新字段**没有 bin** ✗。
        parts.append("⚠ 这条公式新用到还没物化的横向统计字段：%s ⇒ 需要重新物化"
                     "（不重跑的话：单因子测试/事件研究会现算，结果对但明显更慢；"
                     "qlib 回测取不到该字段，可能直接报错）。" % "、".join(gap))
    return "\n".join(parts)


@router.post("/custom-formulas", summary="编译并保存自定义公式")
def create_saved_formula(req: CustomFormulaBody):
    """编译用户公式并保存到 workdir/custom_formulas.json，返回保存的条目。

    ★ v1.20.82：**保存查重**（用户 2026-10-09：「加个保存查重提示，我记得以前有查重啊」）——
      老查重在前端**只比正文**（`App.tsx`）✗ ⇒ 正文差一个注释/空行就绕过去了 ✗
      （实测：`IS_GOLD_PIT` 被存成两条同名，列表里并排出现 ✗）。
      现在按**输出名**查（后端是唯一权威：名字由编译器定 ✓），命中返回
      **409 + `conflict`** ✓ ⇒ 前端给"覆盖它 / 仍新建一条 / 取消"✓；
      `allow_duplicate=true`（= 用户点了"仍新建一条"✓）即跳过本次提示 ✓。

    ★ v1.20.89：返回体里带 `materialize_warning`（被 `mkt_*` 引用却改了正文 ⇒ 非空 ✓）。
    """
    t = _compile_formula_or_400(req.formula, req.patchable)
    if not req.allow_duplicate:
        dup = _find_live_by_name(t.name)
        if dup is not None:
            return _dup_conflict("create", t.name, dup)
    item = _create_custom_formula(t.name, req.formula.strip(), t.expression)
    # ★ v1.20.87：**自动**把引用了本公式的其它公式标为待重编 ✓（用户 2026-10-09：
    #   「改被调用公式的正文，依赖它的公式必须重存一遍」能不能避免 ⇒ 现在从机制上避免了 ✓）
    _mark_dependents_stale(t.name, exclude_id=item.get("id"))
    # ★ v1.20.89：被物化引用 ⇒ 当场提示"物化值已过期" ✓（用户实测"看不到提示" ✓）
    item["materialize_warning"] = _market_impact_warning(t.name, item.get("text") or req.formula)
    return item


@router.put("/custom-formulas/{formula_id}", summary="编辑自定义公式（重新编译并保存）")
def update_saved_formula(formula_id: str, req: CustomFormulaBody):
    """按 id 修改公式：重新编译后覆盖 text/name/expression。

    ★ v1.20.82：编辑成**别人的名字**同样是造重名（会并列 ✗）⇒ 这里也查重并 409 ✓
      （`exclude_id` 排除自己 ✓ —— 不排除的话"编辑自己"会立刻被判成循环/撞名 ✗）。
      编辑撞名几乎都是名字改错了 ⇒ **只拦、不给"仍新建"** ✓。
    """
    t = _compile_formula_or_400(req.formula, req.patchable, exclude_id=formula_id)
    dup = _find_live_by_name(t.name, exclude_id=formula_id)
    if dup is not None:
        return _dup_conflict("update", t.name, dup)
    item = _update_custom_formula(formula_id, t.name, req.formula.strip(), t.expression)
    if item is None:
        raise HTTPException(status_code=404, detail="公式不存在")
    # ★ v1.20.87：同上 —— 改了正文 ⇒ 引用它的公式（含传递）自动待重编 ✓（避免"回测对、
    #   单因子测试还是旧值" ✓）；⚠ 必须放在 `_update_custom_formula` **之后**（它内部持锁 ✓，
    #   而 `mark_dependents_stale` 也要拿同一把锁 ⇒ 晚一步调用才不会自锁 ✓）。
    _mark_dependents_stale(t.name, exclude_id=formula_id)
    # ★ v1.20.89：编辑被物化引用的公式 ⇒ 同样当场提示（用户 2026-10-09 实测：
    #   `IS_GOLD_PIT:轨迹<10` 改了之后**看不到任何提示** ✓ ⇒ 现在保存返回里就有 ✓）
    item["materialize_warning"] = _market_impact_warning(t.name, item.get("text") or req.formula)
    return item


@router.delete("/custom-formulas/{formula_id}", summary="删除自定义公式")
def delete_saved_formula(formula_id: str):
    if not _delete_custom_formula(formula_id):
        raise HTTPException(status_code=404, detail="公式不存在")
    return {"ok": True}


class PinFormulaBody(BaseModel):
    """置顶开关（v2.1.45 ✓）。"""
    pinned: bool = True


@router.post("/custom-formulas/{formula_id}/pin", summary="置顶 / 取消置顶自定义公式")
def pin_saved_formula(formula_id: str, req: PinFormulaBody):
    """★ v2.1.45（用户 2026-10-12："给公式加置顶/取消置顶按钮，加在编辑左边" ✓）。

    ⚠ 这是**显示偏好**（不动 `name`/`text`/`expression` ✓，也不重编译 ✗）⇒ 不触发
      `mark_dependents_stale`（引用它的公式无需重编 ✓）。
    """
    item = _set_pinned(formula_id, bool(req.pinned))
    if item is None:
        raise HTTPException(status_code=404, detail="公式不存在")
    return item


@router.get("/operators", summary="列出翻译器支持的算子分类")
def list_operators():
    """返回翻译器支持的算子分类清单（供前端公式编辑器提示/灰显）。"""
    from ..factors.parser.codegen import PATCHED_OPS, LEVEL2_OPS, IGNORED_OPS, FUNC_QLIB
    return {
        "supported": sorted(FUNC_QLIB.keys()),           # 直接映射到 qlib 的算子
        "patched_need_impl": sorted(PATCHED_OPS.keys()),  # 有状态算子（M3 待实现外挂）
        "level2_no_data": sorted(LEVEL2_OPS),             # Level2 深度函数（留接口，暂无数据）
        "ignored_plot": sorted(IGNORED_OPS),              # 绘图/颜色（忽略，不生成因子）
    }


@router.get("/datasets", summary="列出所有可用特征集")
def list_datasets():
    """返回可用特征集列表（来自 FACTOR_PROVIDERS 注册表）。"""
    return {
        "datasets": [{"name": reg["dataset"]} for reg in FACTOR_PROVIDERS],
        "default": "Alpha158",
    }


@router.get("/catalog", summary="获取特征集因子目录")
def get_factor_catalog(dataset: str = "Alpha158"):
    """返回某特征集的因子目录：{dataset, total, groups:[{group, fields:[{name,expression,category,description}]}], flat:[...]}"""
    available = {reg["dataset"].lower() for reg in FACTOR_PROVIDERS}
    if dataset.lower() not in ("mixed",) and dataset.lower() not in available and dataset.lower() != "alpha360":
        raise HTTPException(status_code=404, detail=f"未知特征集: {dataset}")
    return get_catalog(dataset)


# ---------- 单因子测试（不训练模型，快速诊断因子预测力） ----------

class SingleFactorTestFactor(BaseModel):
    id: str = ""            # 前端标识（自定义公式 id 或 因子名）
    name: str = ""
    expression: str = ""
    source: str = "custom"  # custom / alpha158 / alpha360
    source_formula: str = ""  # 用户原文公式（custom=保存的原文；目录因子=表达式本身），仅随结果回传用于展示


def _period_roll_pick(hk_state: dict, row: dict, item: dict) -> tuple:
    """解析**某一档**（`item`）的每期名单（v2.1.38 ✓）。返回 `(err, dates, lists)`。

    ⚠ `HoldingsIndex` 的键 = `(kind, k, quantile)`，而**分位档在结果里的 `k` 被改写成了
      "中位持有只数"** ✗（`single_test.py` 组装 item 时 `int(np.median(...))`）⇒ 用 item 的 k
      去查必然 miss ✗（实测就是这条：报"没有该档位的每期持仓"）⇒ 多键**依次尝试** ✓。
    ⚠ 本函数**只解析名单、不取价** ✓ —— 取价是全流程唯一的"慢"来源（全 A 10~20s ✗），
      必须由调用方**一次**取好（= 所有档位的并集 ✓）再喂进来（见 `_attach_period_roll` ✓）。
    """
    from ..factors.holdings_index import HoldingsIndex

    tc = row.get("topk_curves") or {}
    expr = str(row.get("expression") or row.get("name") or "")
    h = row.get("horizon")
    idx = HoldingsIndex.from_state(hk_state)
    cands = [(item.get("kind"), item.get("k"), item.get("quantile")),
             (item.get("kind"), 0, item.get("quantile")),
             ("decile", 0, tc.get("default_quantile")),
             ("decile", 0, None)]
    lists = None
    for _c in cands:
        lists = idx.get(expr, h, _c[0], _c[1], _c[2])
        if lists:
            break
    if not lists:
        # 表达式对不上时（例如结果里是别名）⇒ 按 horizon 找唯一的那个桶 ✓
        for _e, _v in (idx.factors or {}).items():
            if str(h) in (_v or {}):
                for _c in cands:
                    lists = idx.get(_e, h, _c[0], _c[1], _c[2])
                    if lists:
                        expr = _e
                        break
            if lists:
                break
    dates = idx.dates(expr, h)
    if not lists or not dates:
        return ("没有该档位的每期持仓（HoldingsIndex 未记录 ⇒ 请确认已跑连续因子档位）", [], [])
    return (None, dates, lists)


def _attach_period_roll(req, hk_state: dict, row: dict, progress_cb=None,
                        exit_cache: Optional[dict] = None,
                        exit_items: Optional[list] = None) -> dict:
    """给**本行的每个档位**各算一条实盘口径净值（v2.1.38 ✓）。

    ★ 为什么改（用户 2026-10-12："应该是我测试的时候选了哪些档位就算哪些档位" ✓）：
      v2.1.27 只算了 `items[0]`（= 默认档 = 最强分位组 ✗）⇒ 弹窗里切到「固定 K=967」时，
      红线与那行指标**仍是默认档的** ✗（用户实测发现 ✓ —— 因为结果只挂在 row 级 ✓）。
      现在：`row["period_roll_items"] = [与 `items` **同序**的每档结果 ✓]`，
      前端按**当前选中档的下标**取 ✓ ⇒ 切档时曲线与指标一起变 ✓；
      并保留 `row["period_roll"] = period_roll_items[0]` ✓（= 默认档 ✓，兼容旧取法/旧脚本 ✓）。

    ⚠ **行情面板只取一次** ✓（这是关键 ✗→✓）：先解析出所有档位的名单、取**并集**代码去
      `load_price_panel` ✓（10 档若各取一次 = 10~20s × 10 ✗ 不可接受 ✓）；
      而 `run_period_roll` 只按**信号里出现的**标的建矩阵（`period_roll.py::_prepare` ✓
      ⇒ `prep["codes"]` 来自 signals ✓）⇒ 面板里多出来的代码**不增加回测开销** ✓。
    ⚠ 任何失败一律落**各自档位**的 `error` 字段 ✓ —— 它是附加信息，绝不拖垮统计（与 `perf` 同规矩 ✓）。

    ★ v2.4.2（用户 2026-10-15 反馈 ✓）：加 `progress_cb(frac 0~1, msg)` —— 本函数是"统计完成之后
      还要跑十几秒~几分钟"的那一段（先取全池行情面板、再逐档逐日回测 ✗），必须能报进度，
      否则进度条会顶在 100% 干等 ✗（详见 `_sft_render_view` 的 95% 封顶 ✓）。
    """
    from ..signals.pricing import load_price_panel

    def _np(frac: float, msg: str) -> None:
        if progress_cb:
            try:
                progress_cb(frac, msg)
            except Exception:                     # noqa: BLE001
                pass                              # 报进度失败绝不影响回测 ✓

    tc = row.get("topk_curves") or {}
    its = tc.get("items") or []
    if not its:
        row["period_roll"] = {"error": "该行没有持仓曲线档位（topk_curves.items 为空）"}
        row["period_roll_items"] = []
        return row["period_roll"]
    _np(0.02, "每期全仓滚动（实盘口径）：解析各档名单…")
    picks: list = []                              # [(item, (err, dates, lists)), ...]
    codes: set = set()
    for _it in its:
        try:
            _p = _period_roll_pick(hk_state, row, _it)
        except Exception as e:                    # noqa: BLE001
            _p = (repr(e), [], [])
        picks.append((_it, _p))
        if not _p[0]:
            for _lst in (_p[2] or []):
                codes.update(str(c) for c in _lst)
    panel, perr = None, None
    if codes:
        try:
            # ⚠ 这一步是全池取价（全 A 十几秒 ✗）⇒ 文案里点明"较慢"以免被当卡死 ✓
            _np(0.10, "每期全仓滚动（实盘口径）：取全池行情面板（%d 只，约十几秒，请勿刷新）…"
                % len(codes))
            panel = load_price_panel(sorted(codes), str(req.start_date), str(req.end_date),
                                     need_open=True)
        except Exception as e:                    # noqa: BLE001
            perr = repr(e)
    out: list = []
    n_it = max(1, len(picks))
    for _k, (_it, _p) in enumerate(picks, start=1):
        if _p[0] or not panel:
            out.append({"error": _p[0] or perr or "行情面板为空（取价失败）"})
            continue
        _np(0.10 + 0.88 * (_k - 1) / n_it,
            "每期全仓滚动（实盘口径）：第 %d/%d 档回测中…" % (_k, n_it))
        out.append(_panel_period_roll_nav(req, hk_state, row, item=_it, panel=panel))
    row["period_roll_items"] = out
    row["period_roll"] = out[0] if out else {}
    # ★★ v2.4.3（用户 2026-10-15 拍板 ✓）：勾了离场因子 ⇒ **同口径再跑一遍"含离场"**
    #   —— 上面那份就是**必算的对照列**（"不离场"✓，口径与数字**逐位不变** ✓）。
    #   两条同源（同一份名单、同一份价格面板、同一个引擎 ✓）⇒ 差值才解释得清 ✓
    #   （这也是我们自己的老教训：**没有可比参照 ⇒ 结论会看反** ✗）。
    # ★★ v2.5.0（用户 2026-10-15：\"能不能搞成多选啊，要矩阵化\" ✓）：**逐个离场各跑一遍**
    #   ⇒ 一行结果里就有 `1 + N离` 条同源同口径曲线 ✓（第 0 条永远是"不离场"对照 ✓）。
    #   ⚠ 离场列表来自 `req.exit_factors`（路由已规范化 ✓：丢空、去重、同公式拦截 ✓）。
    _exits = list(getattr(req, "exit_factors", None) or [])
    if _exits:
        # 进度：对照那一轮占 0.10→0.55，各离场一起分 0.55→0.98 ✓（总回测数 = (1+N)×档数 ✓）
        _n_pass = 1 + len(_exits)
        _exits_err: dict = {}                     # 离场下标 → 编译/求值失败原因 ✓（落各自档位 ✓）
        _rows_exit: list = []
        for _ei, _ex in enumerate(_exits):
            _ename = str(getattr(_ex, "name", "") or getattr(_ex, "id", "") or "")
            _it0 = ((exit_items or [])[_ei]
                    if (exit_items and _ei < len(exit_items)) else {}) or {}
            _mask = _it0.get("mask")
            # ★ v2.7.0：**含入场价**的离场公式 ⇒ 用单因子侧拆好的「骨架 + 预计算列」✓
            #   （与入场价无关的部分由面板算 ✓ ⇒ 任意字段/算子都能用 ✓；这里只把列对齐到
            #    本轮回测面板 ✓ + 绑定入场价 ✓）。失败 ⇒ 落到档位 error ✓（绝不拖垮统计 ✓）
            _rule = None
            if _it0.get("skel") is not None:
                try:
                    _rule = EntryRule(_it0["skel"], _it0.get("pre_exprs") or [],
                                      float(req.exit_threshold),
                                      _exit_pre_mats(_it0.get("pre_series") or [], panel,
                                                     exit_cache, ei=_ei))
                except Exception as _e:                          # noqa: BLE001
                    _rule = None
                    _exits_err[_ei] = str(_e)
            elif _it0.get("entry_dependent") and _it0.get("error"):
                _exits_err[_ei] = str(_it0["error"])
            out_exit: list = []
            for _k, (_it, _p) in enumerate(picks, start=1):
                if _p[0] or not panel:
                    out_exit.append({"error": _p[0] or perr or "行情面板为空（取价失败）"})
                    continue
                _np(0.55 + 0.43 * ((_ei * n_it + (_k - 1)) / max(1, _n_pass * n_it)),
                    "含离场「%s」：第 %d/%d 档回测中…" % (_ename or "?", _k, n_it))
                if _exits_err.get(_ei):
                    out_exit.append({"error": _exits_err[_ei]})
                    continue
                out_exit.append(_panel_period_roll_nav(
                    req, hk_state, row, item=_it, panel=panel, with_exit=True,
                    exit_mask=_mask, exit_cache=exit_cache,
                    exit_index=_ei, exit_name=_ename, exit_rule=_rule))
            _rows_exit.append({
                "exit": {"index": _ei, "name": _ename,
                         "id": str(getattr(_ex, "id", "") or ""),
                         "expression": str(getattr(_ex, "expression", "") or "")},
                "items": out_exit,
                "first": (out_exit[0] if out_exit else {}),
            })
        row["exit_rolls"] = _rows_exit
        # 兼容 v2.4.3 的**单数**取法 ✓（= 第一个离场 ✓）
        row["exit_roll_items"] = _rows_exit[0]["items"]
        row["exit_roll"] = _rows_exit[0]["first"]
    _np(1.0, "每期全仓滚动完成")
    return row["period_roll"]


def _exit_list(req) -> list:
    """规范化后的**离场列表**（v2.5.0 多选 ✓）：丢空表达式、**按表达式去重** ✓、保序 ✓。"""
    out, seen = [], set()
    for e in list(getattr(req, "exit_factors", None) or []) + \
            ([req.exit_factor] if getattr(req, "exit_factor", None) is not None else []):
        expr = ((e.expression or "").strip() if e is not None else "")
        if not expr or expr in seen:
            continue
        seen.add(expr)
        out.append(e)
    return out


def _apply_exit_validation(req) -> None:
    """离场因子的**前置校验**（v2.4.3 / v2.5.0 多选 ✓）—— 纯函数、**在入队前**跑 ✓。

    ① 空表达式 ⇒ 丢掉 ✓（前端清理选择时常见 ✓）；同一公式选两次 ⇒ **只算一遍** ✓；
    ② **同公式既进又出 ⇒ 直接拦** ✗（用户选的"直接拦"✓）：同日进+出时，按"同日不交易"规则
       会**永远开不了仓**（净值恒 1 ✓）；若照做 ⇒ 买完就卖、被手续费一层层磨 ✗ —— 两种都没意义 ✓；
       ⚠ 多选下要**逐个**查 ✓（v2.4.3 只查了一个 ✗）；
    ③ 勾了离场 ⇒ **自动打开 `period_roll`** ✓（离场只能作用在"真金白银"那条回测上 ✓）；
    ④ 规范化结果**写回 `req.exit_factors`** ✓（下游只看这一个字段 ✓；单数形态归一后置 None ✓）。
    """
    items = _exit_list(req)
    req.exit_factors = items
    req.exit_factor = None
    if not items:
        return
    entries = {(f.expression or "").strip(): f for f in (req.factors or [])}
    # ★ v2.6.0：**入场价字段只能在离场公式里用** ✓ —— 进场因子用到 ⇒ 直接拒（清楚 ✓）：
    #   它是回测状态量（取决于你实际在哪根成交 ✗），当因子算不出任何意义的值 ✗。
    for f in (req.factors or []):
        if uses_entry(str(f.expression or "")):
            raise HTTPException(
                status_code=400,
                detail="「进场因子」里不能使用 ENTRY_PRICE / ENTRY_FIRST（入场价）✗ —— "
                       "它们是**回测状态量**（取决于你实际在哪一根买入 ✓），只有当**离场条件**才有意义 ✓；"
                       "请把它们写在离场公式里（例：`止损: CLOSE < ENTRY_PRICE*0.95;`）✓")
    for e in items:
        expr = (e.expression or "").strip()
        # ★ v2.6.0 / v2.7.0：含入场价的离场公式 ⇒ **入队前就拆一遍做校验** ✓
        #   （不支持的写法立刻 400 ✓，别等回测跑一半才炸 ✗）
        if uses_entry(expr):
            try:
                split_entry_rule(expr, float(getattr(req, "exit_threshold", 0.0) or 0.0))
            except Exception as _e:                               # noqa: BLE001
                raise HTTPException(
                    status_code=400,
                    detail="离场公式「%s」不支持 ✗：%s" % (e.name or e.id or expr[:30], _e))
        if expr in entries:
            raise HTTPException(
                status_code=400,
                detail="「离场因子」与某个「进场因子」是**同一个公式**（%s）✗ ⇒ 会在进场当天就离场："
                       "按『同日进出不交易』规则 ⇒ 永远开不了仓（净值恒 1）；照做 ⇒ 买完就卖、"
                       "被手续费一层层磨掉 ⇒ 两种都没有意义 ✓ 请换一个公式 ✓"
                       % (e.name or e.id or expr[:40]))
    req.period_roll = True


def _exit_matrix(mask, panel: dict, cache: Optional[dict] = None, ei: int = 0):
    """把单因子侧算好的离场布尔 Series 对齐成 **(T×C) 布尔矩阵** ✓（v2.4.3 / v2.5.0 ✓）。

    · 输入 = `run_single_factor_tests(exit_out=…)` 的 `items[ei]["mask"]`（索引 (instrument, datetime) ✓）；
    · 输出与 `panel["CLOSE"]` 的**日历/标的逐位对齐** ✓（`reindex` + `fillna(False)` ✓）——
      这是 `run_period_roll` 能吃的前提 ✓（形状不符它会自己**关掉离场**并记 diag ✓，绝不崩 ✗）；
    · `cache` = 调用方持有的小字典 ✓，键含 `(离场下标, 面板特征)` ✓（多选下每个离场各自一份 ✓；
      `_attach_period_roll` 是**逐行**调用的 ⇒ 不缓存会把 "unstack + reindex" 重复做很多遍 ✗）；
      面板换了（区间/标的变了）⇒ 旧键清掉 ✓（内存安全 ✓）。
    """
    if mask is None or not panel:
        return None
    cl = panel.get("CLOSE")
    if cl is None or not len(cl):
        return None
    pkey = (str(cl.index[0])[:10], str(cl.index[-1])[:10], len(cl.columns))
    key = (int(ei), pkey)
    if cache is not None and key in cache:
        return cache[key]
    try:
        w = mask.unstack(level="instrument")
        w = w.reindex(index=cl.index, columns=list(cl.columns)).fillna(False)
        mat = w.to_numpy(dtype=bool)
    except Exception:                                              # noqa: BLE001
        mat = None
    if cache is not None:
        for k in [k for k in cache if k[1] != pkey]:                # 面板变了 ⇒ 旧的全丢 ✓
            cache.pop(k, None)
        cache[key] = mat
    return mat


def _exit_pre_mats(pre_series, panel: dict, cache: Optional[dict] = None, ei: int = 0):
    """把"入场价离场规则"的**预计算列**（面板侧算好的 Series ✓）对齐成 **(T×C) 矩阵** ✓。

    ★ v2.7.0（用户 2026-10-15 指出"不能只支持 C/O"✓）：含入场价的离场公式里，**与入场价无关的
      子树**（`MA(C,5)` / `TURNOVERRATE` / `MARKET_CAP` / H/L/V…）由**面板**当普通字段列算出来 ✓
      ⇒ 这里只需按回测面板的日历/标的 **reindex** ✓（`fillna(NaN)` ⇒ 与它比较恒为假 ✓，
      不会凭空触发 ✓）。**没取到 ⇒ 明确报错**，绝不静默算错 ✗（差值会骗人 ✓）。
    """
    if not pre_series or not panel:
        return []
    cl = panel.get("CLOSE")
    if cl is None or not len(cl):
        raise ValueError("行情面板为空（取价失败）")
    pkey = (str(cl.index[0])[:10], str(cl.index[-1])[:10], len(cl.columns))
    key = (int(ei), pkey)
    if cache is not None and key in cache:
        return cache[key]
    out = []
    for s in pre_series:
        try:
            w = s.unstack(level="instrument")
            w = w.reindex(index=cl.index, columns=list(cl.columns))
            out.append(w.to_numpy(dtype=np.float64))
        except Exception as e:                                    # noqa: BLE001
            raise ValueError("离场预计算列对齐失败：%s: %s" % (type(e).__name__, e)) from e
    if cache is not None:
        for k in [k for k in cache if k[1] != pkey]:              # 面板变了 ⇒ 旧的全丢 ✓
            cache.pop(k, None)
        cache[key] = out
    return out


def _panel_period_roll_nav(req, hk_state: dict, row: dict, item: Optional[dict] = None,
                           panel: Optional[dict] = None, with_exit: bool = False,
                           exit_mask=None, exit_cache: Optional[dict] = None,
                           exit_index: int = 0, exit_name: str = "",
                           exit_rule=None) -> dict:
    """面板侧「每期全仓滚动」净值（v2.1.27 ✓，v2.1.38 加 `item`/`panel` ✓）。

    ★ 为什么非要有它（用户 2026-10-11 追问"难道不该逐位一致" ✓）：
      面板那条**持仓曲线**是**组合收益模型**（每期等权、按换手扣成本、复利累乘；
      **无资金约束 / 无手数取整 / 无涨跌停** ✗），而「交易信号测试」拖文件回灌跑的是
      **真金白银引擎** ⇒ 两者的差**不是 bug、也不可能靠补丁消掉** ✗。
      要对拍就必须**两边同口径**：这里直接调**回灌侧同一个** `signals.period_roll.run_period_roll`
      （含资金约束/整手/涨跌停 ✓），喂**同一批每期名单**（= 导出文件 `_signals` 的来源 ✓）
      ⇒ 这两条才该**逐位一致** ✓。

    `item` = 要算**哪一档** ✓（缺省 = `items[0]` = 默认档 ✓）；
    `panel` = 调用方**预先取好**的面板 ✓（缺省 ⇒ 本函数自己取价，= 单档的老口径 ✓）。
    ⚠ 任何异常一律吞进 `error` 字段 ✓ —— 它是**附加信息**，绝不拖垮统计（与 `perf` 同规矩 ✓）。
    """
    try:
        from ..signals.period_roll import run_period_roll
        from ..signals.pricing import load_price_panel

        tc = row.get("topk_curves") or {}
        it0 = item if item is not None else (tc.get("items") or [{}])[0]
        _err, dates, lists = _period_roll_pick(hk_state, row, it0)
        if _err:
            return {"error": _err}
        recs = [{"date": d, "code": c, "bucket": d}      # bucket = 调仓日 ⇒ 每期一批 ✓
                for d, codes in zip(dates, lists) for c in codes]
        # ★★ v2.1.27：**必须按 (日期, 代码) 排序** ✗→✓ —— 回灌侧的解析器就是这么给的
        #   （`parsers/qlib_export.py` 用 `sorted({(date, code)})` ✓）。不排序的话：
        #   现金在"快用完"时**按什么顺序买**就不同 ⇒ 同一批信号会跑出
        #   buys 959 vs 956、min_cash 5.31 vs 3.6 ✗（实测：trades 总数一样、只是买卖拆分不同 ✓）
        #   ⇒ 逐点差 2.6e-3 ✗。排序后两边喂给引擎的表**逐行相同** ✓。
        recs.sort(key=lambda r: (r["date"], r["code"]))
        if not recs:
            return {"error": "每期持仓为空"}
        sig = pd.DataFrame(recs)
        if panel is None:                            # v2.1.38：调用方没预取 ⇒ 自己取（单档口径 ✓）
            panel = load_price_panel(sorted(sig["code"].astype(str).unique().tolist()),
                                     str(req.start_date), str(req.end_date), need_open=True)
        if not panel:
            return {"error": "行情面板为空（取价失败）"}
        # ★★ v2.1.79（用户 2026-10-13 拍板 ✓）：**「每期全仓滚动（实盘口径）」也跟随复权口径** ✗→✓
        #   —— 他要与聚宽对齐 ⇒ **一个口径走全链** ✓；否则"模型曲线走真实价、实盘走复权价" ✗
        #   ⇒ 「实盘 vs 模型」的结论会**随复权口径翻转** ✗（实测：backward 下实盘 −1.31pp ✓、
        #     none 下反而 +1.84pp ✗，用户当场发现 ✓）。
        #   口径来源：**那次任务自己算出来的** 事件研究参数 `row["event_study"]["params"]` ✓
        #   （它就是"面板真正用过的那个" ✓）；⚠ 浅拷贝 ✓（`panel` 由 `_attach_period_roll`
        #   在**多档之间共享** ✓，就地改会串档 ✓）。
        # ⚠⚠ v2.1.80：**优先读那次任务请求自己的 `price_adjust`** ✗→✓ —— 实测
        #   （`ai_test/ab_fuquan.py` ✓）：只读 `row["event_study"]["params"]` 时**拿不到值** ✗
        #   （该字段在装配顺序上**晚于**本函数 ✓）⇒ 换价静默失效 ⇒ 「实盘」在两种复权口径下
        #   **一动不动** ✗（与用户"不复权 → 实盘也要用不复权"的要求正好相反 ✓）。
        #   `req` 就是那次单因子测试请求 ✓（同一对象的 `fill`/`cost`/`capital` 本函数已在用 ✓）。
        _pa_row = str(getattr(req, "price_adjust", None)
                      or ((row.get("event_study") or {}).get("params") or {})
                      .get("price_adjust") or "").strip().lower()
        if _pa_row == "none":
            panel = dict(panel)
            _cr = panel.get("CLOSE_RAW")
            if _cr is not None and len(_cr):
                panel["CLOSE"] = _cr.ffill().round(2)
                _orr = panel.get("OPEN_RAW")
                if _orr is not None and len(_orr):
                    panel["OPEN"] = _orr.ffill().round(2)
        # ★ v2.4.3：`with_exit=True` ⇒ 同一次回测**再带上离场通道** ✓（离场矩阵＝面板侧算好的
        #   布尔信号对齐到本面板 ✓；对不上 ⇒ 引擎自己关掉离场并记 diag ✓，绝不崩 ✗）
        _ex = (_exit_matrix(exit_mask, panel, exit_cache, ei=exit_index)
               if with_exit else None)
        bt = run_period_roll(sig, panel, fill=str(req.fill), cost=float(req.cost),
                             capital=float(req.capital), strict_limit=bool(req.strict_limit),
                             exit_mat=_ex, exit_rule=(exit_rule if with_exit else None))
        nav = getattr(bt, "nav", None)
        if nav is None or not len(nav):
            return {"error": "每期全仓滚动回测无净值"}
        col = str(nav.columns[0])
        stats = dict((getattr(bt, "stats", None) or {}).get(col) or {})
        # ★★ v2.1.41：**截断到"组合曲线的最后一个完整调仓日"** ✗→✓ —— 用户 2026-10-12 拍板：
        #   "实盘口径曲线只出到跟组合时间一样，年化计算也只算到 12-19，这样才是都对齐、有可比性" ✓。
        #   为什么必须截：组合那条日频序列在**最后一个完整持有期末**收口 ✓（未走完的一期
        #   不平铺进样本 ✗），而本函数原本跑满整段（例如到 2026-01-31 ✓）⇒ 一边算到 1-31、
        #   一边算到 12-19 ⇒ **窗口不同、不可比** ✗（用户截图里"实盘年化反而高"正是这么来的 ✗）。
        #   ⚠ 截断后必须**用截断序列重算指标** ✓（引擎的 `stats` 是全段的 ⇒ 不能用 ✗），
        #     并把 `final_nav` / `total_return` 一起校正 ✓，保证"看到的曲线 = 看到的指标" ✓。
        _cd = [str(x)[:10] for x in ((row.get("topk_curves") or {}).get("dates") or [])]
        if _cd:
            _last = _cd[-1]
            try:
                nav = nav.loc[[i for i in nav.index if str(i)[:10] <= _last]]
            except Exception:                                    # noqa: BLE001
                pass
            if not len(nav):
                return {"error": "截断到调仓日（%s）后无净值" % _last}
            try:
                from ..engine.perf_metrics import compute_perf
                _trim = compute_perf(nav[col].pct_change().fillna(0.0).to_numpy())
                if _trim.get("annual_return") is not None:
                    stats["perf"] = _trim
                    stats["final_nav"] = _trim.get("nav_end")
                    stats["total_return"] = _trim.get("total_return")
                    stats["trimmed_to"] = _last          # 自查用：截断到哪个调仓日 ✓
            except Exception as e:                       # noqa: BLE001 —— 附加信息，绝不拖垮
                try:
                    from ..logger import get_logger
                    get_logger("factors").warning("period_roll 截断后重算指标失败: %r", e)
                except Exception:
                    pass
        return {"dates": [str(x)[:10] for x in nav.index],
                # ⚠ 精度 9 位（不是 6 ✓）：对拍脚本要求"逐位一致"，取到 6 位会留下
                #   ≤5e-6 的**纯取整**残差 ✗（实测 max|Δ| = 5e-6 ✓ 就是它）⇒ 提到 9 位 ✓。
                "nav": [round(float(x), 9) for x in nav[col].to_numpy()],
                # ★ v2.4.3 / v2.5.0：同口径的多条曲线必须**一眼可分** ✓（对照 vs 各离场）
                "label": ("每期全仓滚动（实盘口径 · 含离场%s）"
                          % (("「%s」" % exit_name) if exit_name else "")
                          if with_exit else "每期全仓滚动（实盘口径）"),
                "with_exit": bool(with_exit),
                "exit_index": (int(exit_index) if with_exit else None),
                "exit_name": (str(exit_name) if with_exit else ""),
                "stats": {k: v for k, v in stats.items() if k != "perf"},
                "perf": stats.get("perf")}
    except Exception as e:                                   # noqa: BLE001
        try:
            from ..logger import get_logger
            get_logger("factors").warning("period_roll 面板侧计算失败: %r", e)
        except Exception:
            pass
        return {"error": repr(e)}


class SingleFactorTestRequest(BaseModel):
    universe: str = "csi300"
    start_date: str = ""
    end_date: str = ""
    label_horizon: int = 2   # 未来 N 日收益作为预测目标（label_horizons 未传时的默认单周期）
    label_horizons: Optional[List[int]] = None  # 批量预测周期（如 [1,2,3,5,10,15,20] 或 range 展开）；
    #   传了则忽略 label_horizon；结果按"因子分组、组内周期升序"返回，每行带 horizon 字段
    parallel: bool = False   # [已弃用，仅保留兼容] 早期"多周期各占并发单元并行跑"已废弃：现为单执行体
    #   共享一次特征加载后逐周期串行统计（见 run_single_factor_tests），本字段不再生效
    factors: list[SingleFactorTestFactor] = []
    # 触发组剔除开关（默认全开，保持原行为 + 新增成交日口径）：
    exclude_limit_up_signal: bool = True  # 剔除信号日（T）涨停（选股过滤，无前视）
    exclude_limit_up_trade: bool = True   # 剔除成交日（T+1）涨停（调仓日封板买不到，与回测一致）
    exclude_suspended: bool = True        # 剔除成交日（T+1）停牌/无行情（同样买不到）
    exclude_st_t1: bool = False           # 剔除成交日（T+1）处于 ST/*ST/退市整理 的样本（日截面，T+1 当日状态）
    exclude_stock_gem: bool = False       # 剔除创业板（SZ30 段，20% 涨跌幅）
    exclude_stock_kcb: bool = False       # 剔除科创板（SH688，20% 涨跌幅）
    price_adjust: str = "backward"      # v1.19.97：默认改后复权（收益率含分红、无除权跳空 ✓）         # 复权方式：none/forward/backward（与回测对齐，默认前复权）
    freeze_suspended_price: bool = True   # 停牌日价格冻结计入未来收益（对齐聚宽口径 B）
    suspend_remove: bool = True           # 信号停牌行语义：True=SR删行(益盟/回测一致)；False=NaN占位(聚宽口径)
    price_round: bool = True              # 真实价按分取整参与因子计算（仅不复权生效，默认开；与益盟/聚宽对齐）
    warmup_days: Optional[int] = None     # 特征加载额外预热缓冲（交易日，v1.18.6）：None=按
    #   QLIB_SFT_WARMUP_DAYS（默认 250 ≈1 年）；0=关闭。长回看/动态窗口公式（DYN_*/
    #   BARSCOUNT/HHVBARS+Ref 嵌套）扩展天数无法静态推断，预热后区间首日即有收敛值；
    #   出口仍裁剪回 [start_date, ...]，固定窗口公式结果不变。
    # ---- 连续因子「分位 / 持仓期收益曲线」参数（v1.18.45 起由 API 传入，不再硬编码）----
    # ★ v2.1.23（B 主体 ①）：**成交时点** —— 决定"每期买入价 + 期内逐日盯市价"用哪个价 ✓。
    #   `t1_close`（默认 ✓ = 老口径，数字逐位不变 ✓）/ `t1_open`（次日开盘 ✓ 用户选的统一口径 ✓）。
    # ★ v2.1.34（用户 2026-10-12）：**默认改成次日开盘** ✗→✓（与面板净值引擎 `t1_open` 一致 ✓，
    #   也是行业通行做法 ✓）。⚠ 这会让**新任务**的持仓期曲线与旧结果不同 ✓（有意变更 ✓）；
    #   显式传 `t1_close` 仍得老口径 ✓。
    fill: str = "t1_open"
    # ★★ v2.1.27：**每期全仓滚动（实盘口径 · 逐点对拍专用）** —— **默认关** ✗→✓（慢 ✓）
    #   为什么要有它（用户 2026-10-11 定 ✓）：面板那条持仓曲线是**组合收益模型**（每期等权、
    #   按换手扣成本、复利；**无资金约束 / 无手数取整 / 无涨跌停** ✗），而「交易信号测试」
    #   回灌出来的是**真金白银引擎** ⇒ **原理上不可能逐点一致** ✗。
    #   打开本开关 ⇒ 用**与回灌侧同一个** `signals.period_roll.run_period_roll`（含资金约束/
    #   整手/涨跌停 ✓）把"每期名单"按实盘口径跑一遍 ✓ ⇒ 这两条才该**逐位一致** ✓（逐点对拍用 ✓）。
    #   ⚠ 代价：每种档位要多取一次全池行情面板 + 逐日回测（全 A 实测 10~20s 量级 ✗）
    #     ⇒ 故默认关、且不在任何统计口径里（纯附加 ✓，失败只落到 `error` 字段 ✓）。
    period_roll: bool = False
    # ★★ v2.4.3（用户 2026-10-15 拍板 ✓）：**离场因子**（单因子测试的"卖出侧"第一步 ✓）
    #   口径 4 条都是用户定的 ✓：
    #     ① 离场信号 = 该因子值 **> exit_threshold（默认 0）** ✓ —— 二值公式与连续因子**通用** ✓
    #        （不写"只能二值"的限制 ✗），并给**触发率预检**（日均触发占比 > 50% ⇒ 前端黄字 ✓）；
    #     ② **对照列必算** ✓ ⇒ 结果里永远同时有「不离场」（= 现有 `period_roll` ✓）与「含离场」两条 ✓；
    #     ③ 同一公式既进又出 ⇒ **直接拦** ✗（同日进+出：要么恒不交易（净值恒 1）、要么买完就卖
    #        （被手续费磨下去）——两种都没意义 ✓，前后端都拒 ✓）；
    #     ④ 与调仓日的关系：**调仓日以名单为准、离场只在期中生效** ✓（详见 `period_roll` 注释 ✓）；
    #        持币期间的离场信号 = **空转**（忽略但计数 ✓，否则用户会以为信号没生效 ✓）。
    #   ⚠ 勾了离场 ⇒ 路由里**自动打开 `period_roll`** ✓（离场只能作用在"真金白银"那条回测上 ✓）。
    #
    # ★★ v2.5.0（用户 2026-10-15：\"这个离场因子能不能搞成多选啊，要矩阵化\" ✓）：
    #   `exit_factors` = **多选** ✓ ⇒ 结果变成矩阵：**进场 × (1 + 离场数)** 个同口径回测
    #   （`1` = 必算的"不离场"对照列 ✓）⇒ 前端出对比表（含 Δ年化 / Δ回撤 ✓）。
    #   `exit_factor`（单数）= v2.4.3 兼容形态 ✓（传它等于传 `[它]` ✓）。
    #   ⚠ 多个离场**共用一个阈值** ✓（界面上就一个输入框 ✓；要各自阈值以后再加 ✓）。
    exit_factors: List[SingleFactorTestFactor] = []
    exit_factor: Optional[SingleFactorTestFactor] = None
    exit_threshold: float = 0.0
    # 下面三个只在 `period_roll=True` 时被用到（与「交易信号测试」回灌侧同名同义 ✓，
    # 默认值取回灌侧的默认 ⇒ 两边不显式指定时也对得齐 ✓）。
    cost: float = 0.004
    capital: float = 1e9
    strict_limit: bool = True
    quantiles: int = 10                   # 分位组数（默认 10=十分位；5=旧五分位，保留兼容）
    rebalance_period: Optional[int] = None
    #   调仓期（交易日）；**None ⇒ 跟随预测周期 h**（= 「用预测周期调仓换股」，默认口径）；
    #   传值则按固定调仓期（如 5/10/20）覆盖
    topk_list: Optional[List[float]] = None
    #   明细曲线要算的 K 列表：**≤1 视为「日均有效只数的百分比」**（0.1 = 10%），>1 视为只数；
    #   与默认档**同一取整口径**（`int(日均中位只数 × 百分比)`，截断）⇒ 0.1 就是默认档。
    #   None/空 ⇒ 只算默认档（10%）。默认档（= 十分位第 1 组）**恒算且排在 items[0]**。


# ---------- 单因子测试异步任务：POST 提交返回 task_id，GET 轮询进度/结果 ----------
# 进度存储为进程内内存 dict（本地单用户工具，无需持久化）；任务完成后保留最近 _SFT_MAX_TASKS 条。
_SFT_MAX_TASKS = 50
# 内存治理：任务列表最多保留 _SFT_MAX_TASKS 条元信息；但完整 result（items 含全部因子的
# 大 dict）只保留最近 _SFT_RESULT_KEEP 个已完成任务，更早的 result 置 None 释放长驻内存
# （此前每条成功任务都完整驻留，多测几次单因子后 result 总量可占数百 MB，导致进程内存持续高位）。
_SFT_RESULT_KEEP = 5
_SFT_TASKS: dict[str, dict] = {}
_SFT_LOCK = threading.Lock()


def _sft_store(task_id: str, state: dict) -> None:
    with _SFT_LOCK:
        _SFT_TASKS[task_id] = state
        if len(_SFT_TASKS) > _SFT_MAX_TASKS:
            # 只清理已结束任务里最旧的，保留运行中的
            finished = [k for k, v in _SFT_TASKS.items() if v.get("status") in ("success", "failed")]
            for k in sorted(finished, key=lambda k: _SFT_TASKS[k].get("ts", 0))[: len(_SFT_TASKS) - _SFT_MAX_TASKS]:
                _SFT_TASKS.pop(k, None)


def _sft_trim_results() -> None:
    """内存治理：已完成任务只保留最近 _SFT_RESULT_KEEP 条的完整 result，更早的置 None。

    ⚠ v1.19.61：**同时释放 `_evs`**（0/1 因子的触发明细）—— 它是给"净值曲线"复用的事件表，
      一个任务可能有几万行；只清 `result` 不清它，跑几十次后内存白占（而净值端点里那条
      "按表达式在最近任务里找回事件"的兜底只扫最近几个任务，清掉更早的正好）。
    """
    with _SFT_LOCK:
        finished = sorted(
            (k for k, v in _SFT_TASKS.items() if v.get("status") in ("success", "failed")),
            key=lambda k: _SFT_TASKS[k].get("ts", 0),
            reverse=True,
        )
        for k in finished[_SFT_RESULT_KEEP:]:
            if _SFT_TASKS[k].get("result") is not None:
                _SFT_TASKS[k]["result"] = None
            if _SFT_TASKS[k].get("_evs") is not None:
                _SFT_TASKS[k]["_evs"] = None
            if _SFT_TASKS[k].get("_hk") is not None:      # v2.1.0：每期持仓索引同策略释放 ✓
                _SFT_TASKS[k]["_hk"] = None


def _sft_get(task_id: str):
    with _SFT_LOCK:
        return _SFT_TASKS.get(task_id)


def est_export_state(task_id: str) -> Optional[dict]:
    """**导出模块读取独立「事件研究」任务状态的唯一入口**（与 `sft_export_state` 对称 ✓）。"""
    return _est_get(task_id)


def sft_export_state(task_id: str) -> Optional[dict]:
    """**导出模块读取单因子测试任务状态的唯一入口**（结果 / `_hk` 持仓索引 / `_req` 参数 ✓）。

    ⚠ 为什么走函数、而不是让导出路由直接摸 `_SFT_TASKS`：那张表是**本模块的内部结构**
      （键名会变、还带锁 ✓）⇒ 集中一处暴露，将来调整结构只动这里 ✓。
    """
    return _sft_get(task_id)


def _json_safe(o):
    """递归把非有限 float（NaN/±Inf）替换为 None，保证 JSON 序列化不 500。

    单因子统计在极端样本下（如 60 日收益、触发组样本过小、0/0 比值、HAC 方差钳制等）
    可能天然产生 NaN/Inf——这些字段应传给前端展示为 null，而不是让整个任务
    progress/result 接口抛 "Out of range float values are not JSON compliant"（表现为任务卡死）。
    """
    if isinstance(o, float):
        if o != o or o in (float("inf"), float("-inf")):
            return None
        return o
    if isinstance(o, dict):
        return {k: _json_safe(v) for k, v in o.items()}
    if isinstance(o, (list, tuple)):
        return [_json_safe(v) for v in o]
    return o


def _sft_render_view(state: dict) -> tuple:
    """读侧统一渲染 (progress, message)：消息只在这里合成，杜绝多线程抢写 message。

    进度模型（共享特征加载 → 逐周期统计，保证单调不回跳）：
      - 排队等待并发单元：0
      - 共享加载/解析：0-30（load_prog）
      - 周期统计：30 + 70×(已完成周期数 + 当前周期内部进度/100) / 总周期数
      message 按状态分层（排队中 → 共享加载中 → 正在统计哪些周期及其内部进度）。
    - 终态：直接使用存储的终态 message。
    """
    status = state.get("status")
    if status in ("success", "failed", "cancelled"):
        return float(state.get("progress", 100.0)), state.get("message", status)
    with _SFT_LOCK:
        horizons = list((state.get("config") or {}).get("horizons") or [])
        running = sorted(state.get("running_h") or [])
        queued = sorted(state.get("queued_h") or [])
        done_n = int(state.get("done_n") or 0)
        per_prog = dict(state.get("per_h_prog") or {})
        per_msg = dict(state.get("per_h_msg") or {})
        cancelled = bool(state.get("cancel_requested"))
        load_prog = float(state.get("load_prog") or 0.0)
        load_msg = state.get("load_msg") or ""
        ts = state.get("ts", 0.0)
    n_h = max(1, len(horizons))

    if cancelled:
        return 0.0, "正在取消..."  # 终态由 worker 主线程统一收尾写 "已取消"

    # 排队中（尚未获得并发单元）
    if queued and not running and done_n == 0:
        qs = "、".join(str(x) for x in queued[:6])
        tail = "等" if len(queued) > 6 else ""
        return 0.0, f"排队等待并发单元（预测周期 {qs} 日{tail}，与回测/训练共用配额）..."

    # 共享加载/解析阶段（尚无周期进入统计）：直接用加载进度 0-30
    if not running and done_n == 0:
        if load_msg:
            return round(load_prog, 1), load_msg
        return 0.0, "等待调度..."

    # 统计阶段：30 + 70 × 完成占比（已完成周期计 100%，运行中按内部进度）
    running_prog = sum(float(per_prog.get(h, 0.0)) for h in running) / 100.0
    done_frac = (done_n + running_prog) / n_h
    overall = round(30 + 70 * done_frac, 1)

    # ★ v2.4.2（用户 2026-10-15 反馈 ✓）：**统计全部完成后**仍有附加阶段
    #   （「每期全仓滚动」取全池行情 + 逐档逐日回测，全 A 十秒~分钟级 ✗）——
    #   原来这里直接给 100% ✗ ⇒ 进度条顶在 100% 干等；
    #   单周期时 `parts` 为空 ⇒ `msg` 也是空串 ✗ ⇒ 界面只剩"进度 100%"✗。
    #   现在：统计封顶 95 ✓、尾巴 95→99.5 交给 `extra_prog` ✓、文案用 `extra_msg` ✓。
    if done_n >= n_h and not running:
        _ep = float(state.get("extra_prog") or 0.0)
        _overall = min(99.5, 95.0 + 4.5 * max(0.0, min(100.0, _ep)) / 100.0)
        _msg = str(state.get("extra_msg") or "").strip() or "正在整理结果..."
        return round(_overall, 1), _msg

    parts = []
    for h in running:
        inner = per_msg.get(h, "")
        parts.append(f"[{h}日] {inner}" if inner else f"[{h}日] 统计中")
    msg = "；".join(parts)
    if n_h > 1:
        # 单执行体串行统计：running 恒为正在统计的那 1 个周期（parts 已带 [h日]），
        # 不再显示旧并发语义的"运行 k/n"（多周期并行取消后该数字恒 1，误导）；
        # 改为进度维度的"已完成 k/n 周期"，与 overall 进度条口径一致。
        msg += f"（已完成 {done_n}/{n_h} 周期"
        if queued:
            msg += f"，{len(queued)} 个排队"
        msg += "）"
    return overall, msg


def _sft_touch(state: dict) -> None:
    """写侧标记时间戳（供 tasks 列表按活动排序；不写 message——消息统一读侧渲染）。"""
    with _SFT_LOCK:
        state["ts"] = time.time()


@router.post("/single-factor-test", summary="单因子测试（不训练模型，异步提交）")
def single_factor_test(req: SingleFactorTestRequest):
    """提交单因子测试任务，后台线程逐个因子快速诊断，返回 task_id。

    批量预测周期：
      - label_horizons=[1,2,5,...] 一次测多个持有周期（结果为"因子分组、组内周期升序"，
        每行带 horizon 字段）；单周期（默认 label_horizon=2）行为与历史完全一致。
      - parallel：已弃用不再生效（单执行体共享一次特征加载，逐周期串行统计，见实现）。
    完成后通过 GET /factors/single-factor-test/progress/{task_id} 轮询进度并获取结果。
    """
    if not req.factors:
        raise HTTPException(status_code=400, detail="请至少勾选一个因子")
    # ★ v2.4.3：离场因子的两条前置校验（用户 2026-10-15 拍板 ✓）—— 抽成纯函数便于单测 ✓
    _apply_exit_validation(req)
    if not req.start_date or not req.end_date:
        raise HTTPException(status_code=400, detail="请填写测试区间")
    # 日期合法性兜底（前端已校验，此处防 API 直连）：如 2026-06-31 非法，
    # 放行会在 pandas 解析处报成「特征计算失败: day is out of range for month」。
    _bad = _bad_date_arg(req.start_date, req.end_date)
    if _bad:
        raise HTTPException(status_code=400, detail=_bad)

    # 归并预测周期：label_horizons 优先；否则退化为 label_horizon（单周期，历史行为）
    raw = req.label_horizons if req.label_horizons else ([req.label_horizon] if req.label_horizon else [])
    if not raw:
        raise HTTPException(status_code=400, detail="预测周期不能为空")
    horizons: list[int] = []
    for h in raw:
        try:
            hi = int(h)
        except (TypeError, ValueError):
            raise HTTPException(status_code=400, detail=f"预测周期必须为正整数：{h!r}")
        if not (1 <= hi <= 250):
            raise HTTPException(status_code=400, detail=f"预测周期需在 1~250 之间：{hi}")
        if hi not in horizons:
            horizons.append(hi)
    horizons.sort()  # 结果按因子分组、组内周期升序
    parallel = bool(req.parallel) and len(horizons) > 1
    n_h = len(horizons)

    # 分位/曲线参数校验（v1.18.45：由 API 传入，不再硬编码；默认值与历史行为一致）
    # ⚠ 判空必须用 `is None`，**不能写 `x or 默认`** —— 那样 `0` 会被静默当成默认值，
    #   非法参数便绕过校验（实测 API 层抓到：rebalance_period=0 被当成 5 放行）。
    try:
        quantiles = int(10 if req.quantiles is None else req.quantiles)
    except (TypeError, ValueError):
        raise HTTPException(status_code=400, detail=f"分位组数必须为整数：{req.quantiles!r}")
    if not (2 <= quantiles <= 20):
        raise HTTPException(status_code=400, detail=f"分位组数需在 2~20 之间：{quantiles}")
    try:
        # None 表示"跟随预测周期 h"（默认口径），原样透传；给了值才校验范围
        rebalance_period = (None if req.rebalance_period is None
                            else int(req.rebalance_period))
    except (TypeError, ValueError):
        raise HTTPException(status_code=400, detail=f"调仓期必须为整数：{req.rebalance_period!r}")
    if rebalance_period is not None and not (1 <= rebalance_period <= 250):
        raise HTTPException(status_code=400, detail=f"调仓期需在 1~250 个交易日之间：{rebalance_period}")
    topk_list: List[float] = []
    if req.topk_list:
        # 上限 12 = 默认档(10% 固定档) + 前端全部 10 个预设，与计算层 `_ks[:12]`
        # （factors/single_test.py）保持一致。**原为 8**，而前端预设本就有 10 个
        # ⇒ 用户勾到第 9 个必报 400（"最多点选 8 个"），且计算层其实早就为"全选"预留了 12 的
        # 容量（其注释即写明"默认档 + 前端预设 10 个也要能全选"）—— 两处口径不一致，v1.18.64 统一。
        if len(req.topk_list) > 12:
            raise HTTPException(
                status_code=400,
                detail=f"明细曲线最多点选 12 个 K（默认档 + 10 个预设）：{len(req.topk_list)}")
        for v in req.topk_list:
            try:
                fv = float(v)
            except (TypeError, ValueError):
                raise HTTPException(status_code=400, detail=f"K 必须为数值：{v!r}")
            if not (fv > 0):
                raise HTTPException(status_code=400, detail=f"K 必须为正数：{fv}")
            topk_list.append(fv)

    # ST 剔除依赖 is_st 标签（tools/dump_states.py 从 E:/rq bundle 同步）：本机无 dump 时明确报错，
    # 避免"勾了但没剔除"的静默错误（涨停/跌停判定无标签时自动回退倒推，不受影响）
    if req.exclude_st_t1:
        from ..engine.limits import field_bin_available
        if not field_bin_available("is_st"):
            raise HTTPException(
                status_code=400,
                detail='勾选了"剔除ST(T+1)"，但本机 Qlib 数据没有 is_st 标签'
                "（需先执行 tools/dump_states.py 从 E:/rq bundle 同步）。"
                "取消勾选即可继续（涨停/跌停判定会自动回退倒推口径）",
            )

    task_id = uuid.uuid4().hex[:12]
    # 结构化运行态（读侧渲染 message/progress，杜绝多线程抢写 message 造成文案来回跳）：
    #   running_h / queued_h：正在统计运行 / 排队等并发单元的预测周期
    #   per_h_prog / per_h_msg：各周期统计内部进度 0-100 / 内部最新进度文案
    #   done_n：已完成统计的周期数
    #   load_prog / load_msg：共享特征加载阶段整体进度 0-30 / 文案（读侧映射：加载 0-30 → 统计 30-100）
    state: dict = {
        "task_id": task_id,
        "status": "running",
        "progress": 0.0,
        "message": "已提交",
        "result": None,
        "error": None,
        "cancel_requested": False,
        "ts": time.time(),
        "config": {"horizons": horizons, "parallel": parallel},
        "running_h": [],
        "queued_h": [],
        "done_n": 0,
        "per_h_prog": {},
        "per_h_msg": {},
        "load_prog": 0.0,
        "load_msg": "",
        # ★ v2.4.2（用户 2026-10-15 截图反馈 ✓）：统计**之后**还有一段附加阶段 ——
        #   「每期全仓滚动（实盘口径，逐点对拍）」要**先取全池行情面板**再逐档逐日回测
        #   （全 A 约 10~20 秒/档 ✗），而统计一完成进度就已经是 100% ✗ ⇒ 进度条顶在 100%
        #   干等（单周期时 `message` 还是空串 ✗ ⇒ 界面只剩"进度 100%"✗）。
        #   ⇒ 统计阶段封顶 **95%**、尾巴 95→99.5 留给这一阶段，并把文案实时报出来 ✓。
        "extra_prog": 0.0,
        "extra_msg": "",
    }
    _sft_store(task_id, state)

    def _set_cancel(state: dict) -> None:
        """标记任务已取消（仅当仍在运行中；避免覆盖 failed/success）。"""
        if state.get("status") == "running":
            state.update(status="cancelled", progress=100.0, message="已取消", ts=time.time())

    def _run() -> None:
        manager = get_task_manager(config.WORK_DIR)
        # 进入排队状态（计入并发统计的 queued）
        manager.register_external_queued(task_id)
        # 并行 worker 前先确保 qlib 只初始化一次（引擎 init 自带锁；先 init 完再开线程）
        try:
            from ..factors.single_test import _ensure_qlib_init
            _ensure_qlib_init()
        except Exception as e:
            state.update(status="failed", progress=100.0, message=f"单因子测试失败: {e}", error=str(e), ts=time.time())
            manager.unregister_external_queued(task_id)
            _sft_trim_results()
            return

        lock = threading.Lock()
        per_h: dict[int, list] = {}       # 周期 -> 该周期全部因子的结果
        fatal_all: str = ""               # 致命错误（共享执行体整体异常）
        done_set: set = set()             # 已完成的预测周期
        # ★ v2.4.3：离场因子的**跨作用域箱**（`_run_shared` 是内层函数 ⇒ 它里头的局部变量
        #   外层看不见 ✗）。⚠ 这里必须有：装配段（`t.join()` 之后）要用 mask/stats/cache ✓ ——
        #   第一版把它们定义在 `_run_shared` 里 ⇒ **CI 的 ruff F821 当场点名** ✗
        #   （若不修，运行时会 NameError ⇒ **每个**单因子测试的装配段都会炸 ✗）。
        #   ★ v2.5.0：多选 ⇒ 箱里放 `items`（每个离场一份：name/stats/effect/mask ✓）与
        #   `masks`（对齐用 ✓，**不进 HTTP 响应** ✗ —— pandas Series 不序列化 ✓）。
        _exit_box: dict = {"mask": None, "stats": None, "cache": {}, "err": "",
                           "items": [], "masks": []}

        def _run_shared() -> None:
            """多周期共享一次特征加载的单执行体（根治"并行多周期各自重复加载"）。

            占 1 个并发槽（而不是每周期各占一个）：特征加载只做一次，逐周期统计在
            内存 DataFrame 上进行（不再触发 qlib D.features）。排队/取消语义与原来一致：
            拿不到槽则阻塞排队，cancel_requested 时立即退出。
            """
            nonlocal fatal_all
            # 排队中：所有周期登记为 queued（读侧渲染"排队等待并发单元"）
            with lock:
                state["queued_h"] = list(horizons)
            got = manager.external_wait_slot(
                task_id, cancel_check=lambda: bool(state.get("cancel_requested"))
            )
            if not got:
                with lock:
                    state["queued_h"] = []
                return  # 排队期间被取消：终态由主线程统一收尾
            with lock:
                state["queued_h"] = []
            manager.unregister_external_queued(task_id)
            try:
                def _cb(h, p: float, m: str) -> None:
                    # h 维度进度：结构化解耦，读侧合成整体 progress/message。
                    # h=None → 共享加载/解析阶段整体进度（0-30）；h=int → 该周期统计内部进度。
                    with lock:
                        p = float(p)
                        if h is None:
                            state["load_prog"] = p
                            state["load_msg"] = m
                        elif p >= 100.0 and h not in done_set:
                            done_set.add(h)
                            state["done_n"] = len(done_set)
                            state["running_h"] = [x for x in state["running_h"] if x != h]
                            state["per_h_prog"].pop(h, None)
                            state["per_h_msg"].pop(h, None)
                        else:
                            if h not in state["running_h"]:
                                state["running_h"] = sorted(state["running_h"] + [h])
                            state["per_h_prog"][h] = p
                            state["per_h_msg"][h] = m
                    if state.get("cancel_requested"):
                        raise FactorTestCancelled()

                _evs: dict = {}          # 出参：0/1 因子的触发事件（键=因子表达式）
                # v2.1.0：出参 —— **每期持仓索引**（导出 XLSX 的「持仓明细」页用 ✓）。
                # 持仓在测试进程内算完即丢 ✗ ⇒ 顺手存一份（`has()` 为假时任务状态里不落键 ✓）
                _hks = HoldingsIndex()
                # ★ v2.4.3：离场因子的出参（布尔信号 + 触发率预检 ✓）—— 结果写进**外层**的
                #   `_exit_box` ✓（本函数是嵌套函数，写自己局部变量外层看不见 ✗）
                _exit_out: dict = {}
                res = run_single_factor_tests(
                    label_horizons=horizons,
                    fill=req.fill,      # v2.1.23（B ①）：成交时点（t1_close 默认 / t1_open ✓）
                    universe=req.universe,
                    start_date=req.start_date,
                    end_date=req.end_date,
                    factors=[f.model_dump() for f in req.factors],
                    progress_cb=_cb,
                    cancelled=lambda: bool(state.get("cancel_requested")),
                    exclude_limit_up_signal=req.exclude_limit_up_signal,
                    exclude_limit_up_trade=req.exclude_limit_up_trade,
                    exclude_suspended=req.exclude_suspended,
                    exclude_st_t1=req.exclude_st_t1,
                    exclude_stock_gem=req.exclude_stock_gem,
                    exclude_stock_kcb=req.exclude_stock_kcb,
                    price_adjust=req.price_adjust,
                    freeze_suspended_price=req.freeze_suspended_price,
                    suspend_remove=req.suspend_remove,
                    price_round=req.price_round,
                    warmup_days=req.warmup_days,
                    quantiles=quantiles,
                    rebalance_period=rebalance_period,
                    topk_list=topk_list,
                    events_out=_evs,     # v1.19.60：0/1 因子的触发事件（键=表达式）⇒ 供净值曲线复用
                    holdings_out=_hks,   # v2.1.0：每期持仓索引 ⇒ 供 XLSX 导出复用
                    # ★ v2.4.3 / v2.5.0：离场因子（用户 2026-10-15 ✓，**多选** ✓）——
                    #   每个只多求值一个 `EXIT_i` 列 ✓（不动既有列序/数字 ✓），
                    #   出参给布尔信号 + 触发率预检 + **离场事件研究** ✓
                    exit_factors=[e.model_dump() for e in (req.exit_factors or [])],
                    exit_threshold=float(req.exit_threshold),
                    exit_out=_exit_out,
                )
                # ★ v2.4.3 / v2.5.0：离场信号（布尔 Series ✓）+ 触发率预检 + 事件研究
                #   ⚠ 写进外层 `_exit_box`（见它的注释 ✓ —— 内层函数的局部变量外层看不见 ✗）
                _exit_box["items"] = list(_exit_out.get("items") or [])
                _exit_box["masks"] = [it.get("mask") for it in _exit_box["items"]]
                _exit_box["mask"] = _exit_out.get("mask")
                _exit_box["stats"] = (_exit_out.get("stats")
                                      or ({"error": _exit_out["error"]}
                                          if _exit_out.get("error") else None))
                if _exit_out.get("error"):
                    state["_exit_error"] = _exit_out["error"]
                with lock:
                    if _evs:
                        # ⚠ 不进 HTTP 响应（几万行会撑大结果）：存任务状态，供 /factors/event-study/nav 复用
                        state["_evs"] = dict(_evs)
                    # ⚠ v2.1.0：`_req` 由"仅在有事件时存"改为**无条件存** —— 导出（XLSX）要用它
                    #   回显参数（池子/区间/成本口径…），而**连续因子没有触发事件** ⇒ 原实现下
                    #   持仓曲线导不出来（参数全空 ✗）。它只是个几百字节的 dict ✓。
                    state["_req"] = req.model_dump()
                    if _hks.has():
                        # 同 `_evs`：**不进 HTTP 响应**（几千~几万个代码会白白撑大每次轮询 ✗）
                        state["_hk"] = _hks.as_state()
                    for h, rows in (res or {}).items():
                        per_h[h] = rows
                        if h not in done_set:
                            done_set.add(h)
                    state["done_n"] = len(done_set)
                    state["running_h"] = []
            except FactorTestCancelled:
                pass  # 终态由主线程统一收尾
            except Exception as e:
                import traceback
                from ..logger import get_logger
                get_logger("factors").error("单因子共享执行失败: %s\n%s", e, traceback.format_exc())
                with lock:
                    fatal_all = str(e)
            finally:
                manager.release_slot(task_id)

        # 单执行体占 1 槽完成全部预测周期（共享一次特征加载）。
        # parallel 仅保留前端语义（不再每周期各占一并发单元），加载共享后无需多槽。
        t = threading.Thread(target=_run_shared, daemon=True)
        t.start()
        t.join()

        with lock:
            # 取消优先（排队中或运行中被取消都汇聚到这里统一收尾，保证只写一次终态）
            if state.get("cancel_requested") and state.get("status") == "running":
                _set_cancel(state)
            if state.get("status") == "cancelled":
                manager.unregister_external_queued(task_id)
                _sft_trim_results()
                return
            if fatal_all:
                state.update(status="failed", progress=100.0, message=f"单因子测试失败: {fatal_all}",
                             error=fatal_all, ts=time.time())
                manager.unregister_external_queued(task_id)
                _sft_trim_results()
                return
            # 结果按"因子分组、组内周期升序"组织：对每个因子，遍历所有周期取同一下标的行
            items: list[dict] = []
            # ★ v2.1.27：每期持仓索引（导出/逐点对拍复用 ✓；没落键 ⇒ 空 dict ✓）
            hk_state = state.get("_hk") or {}
            n_factor = len(req.factors)
            # ★★ v2.4.2（用户 2026-10-15 截图反馈 ✓）：**统计之后**这一整段（整理结果 +
            #   「每期全仓滚动」取全池行情 + 逐档逐日回测）原来完全不报进度 ✗ ⇒ 进度条顶在
            #   100% 干等、单周期时连文案都是空的 ✗。现在按"组合格"计数上报 ✓（统计封顶 95% ✓）。
            _cells = sum(1 for _fi in range(n_factor) for _h in horizons
                         if (per_h.get(_h) is not None and _fi < len(per_h[_h])))
            _cell = 0
            state["extra_prog"] = 0.0
            state["extra_msg"] = (
                "整理结果：正在计算「每期全仓滚动（实盘口径%s）」…"
                % (" · 对照 1 条 + 离场 %d 条" % len(req.exit_factors or [])
                   if req.exit_factors else "")
                if req.period_roll else "整理结果…")
            for fi in range(n_factor):
                for h in horizons:
                    rows = per_h.get(h)
                    if rows is None or fi >= len(rows):
                        continue
                    row = dict(rows[fi])
                    row["horizon"] = h
                    _cell += 1
                    # ★ v2.1.27：**每期全仓滚动**（默认关 ✓，慢 ✓）—— 逐点对拍专用 ✓
                    # ★ v2.1.38：**用户选了哪些档就都算哪些** ✓ —— 结果落
                    #   `row["period_roll_items"]`（与 `topk_curves.items` **同序** ✓），
                    #   前端按当前选中档的下标取 ✓（面板取一次价喂所有档 ✓）。
                    if req.period_roll:
                        _base = 100.0 * (_cell - 1) / max(1, _cells)
                        _span = 100.0 / max(1, _cells)

                        def _pc(frac, msg, _b=_base, _s=_span, _c=_cell, _n=_cells):
                            # ⚠ 默认参数绑定当轮的值 ✓（闭包晚绑定会把所有轮次指向最后一轮 ✗）
                            state["extra_prog"] = _b + _s * max(0.0, min(1.0, float(frac)))
                            state["extra_msg"] = "整理结果（%d/%d）：%s" % (_c, _n, msg)

                        row["period_roll"] = _attach_period_roll(
                            req, hk_state, row, progress_cb=_pc,
                            exit_cache=_exit_box["cache"], exit_items=_exit_box["items"])
                    else:
                        state["extra_prog"] = 100.0 * _cell / max(1, _cells)
                    items.append(row)
            state.update(
                status="success",
                progress=100.0,
                message=f"完成（{n_h} 个预测周期）" if n_h > 1 else "完成",
                result={"items": items, "total": len(items),
                        # ★ v2.4.3：离场因子回显（含**触发率预检** ✓ —— 用户定的 >50% 前端黄字 ✓）
                        # ★ v2.4.3 / v2.5.0：离场回显 —— `exit_factors` 是**列表**（多选 ✓），
                        #   每项带触发率预检 `stats` 与**离场事件研究** `effect` ✓；
                        #   `exit_stats`/`exit_factor` 保留为**第一项**（v2.4.3 前端兼容 ✓）。
                        #   ⚠ `mask`（pandas Series）**绝不进响应** ✗（不序列化 ✓）。
                        "exit_stats": _exit_box["stats"],
                        "exit_factor": ({"name": _exit_box["items"][0].get("name"),
                                         "id": _exit_box["items"][0].get("id")}
                                        if _exit_box["items"] else None),
                        "exit_factors": [{"index": it.get("index"),
                                          "id": it.get("id"), "name": it.get("name"),
                                          "source": it.get("source"),
                                          "expression": it.get("expression"),
                                          "stats": it.get("stats"),
                                          "effect": it.get("effect"),
                                          "error": it.get("error")}
                                         for it in _exit_box["items"]],
                        "exit_threshold": float(req.exit_threshold)},
                ts=time.time(),
            )
        manager.unregister_external_queued(task_id)
        # 内存治理：任务结束立即释放超龄任务的完整结果
        _sft_trim_results()

    threading.Thread(target=_run, daemon=True).start()
    return {"task_id": task_id}


@router.get("/single-factor-test/progress/{task_id}", summary="查询单因子测试任务进度")
def single_factor_test_progress(task_id: str):
    """轮询单因子测试任务：status running/success/failed/cancelled，progress 0-100，success 时附带 result。

    progress/message 由 _sft_render_view 读侧统一合成（多 worker 不写 message）。
    """
    state = _sft_get(task_id)
    if state is None:
        raise HTTPException(status_code=404, detail="任务不存在或已过期")
    progress, message = _sft_render_view(state)
    result = state.get("result")
    # 防御：统计结果可能含 NaN/Inf（极端样本），清洗为 null 避免整个接口 500
    result = _json_safe(result) if result is not None else None
    return {
        "task_id": task_id,
        "status": state["status"],
        "progress": progress,
        "message": message,
        "result": result,
        "error": state.get("error"),
    }


@router.post("/single-factor-test/cancel/{task_id}", summary="取消单因子测试任务")
def single_factor_test_cancel(task_id: str):
    """请求取消正在运行的单因子测试任务：设置取消标记，并唤醒排队等待配额的任务线程
    （否则 worker 阻塞等槽时无法及时感知取消），后台线程在下一进度点/下次唤醒终止。

    返回 {ok, message}；任务已结束（success/failed/cancelled）时 ok=False 且不改变状态。
    """
    state = _sft_get(task_id)
    if state is None:
        raise HTTPException(status_code=404, detail="任务不存在或已过期")
    if state.get("status") != "running":
        return {"ok": False, "message": f"任务已{state.get('message', '结束')}，无需取消"}
    state["cancel_requested"] = True
    # 唤醒阻塞在 external_wait_slot 的排队 worker（无槽可释放时也能立即响应取消）
    try:
        manager = get_task_manager(config.WORK_DIR)
        manager.wake_external_waiters()
    except Exception:
        pass
    return {"ok": True, "message": "已请求取消，正在终止..."}


@router.post("/single-factor-test/clear", summary="清理已结束的单因子测试任务与结果（释放内存）")
def single_factor_test_clear():
    """删除全部已结束（success/failed/cancelled）任务的记录并释放其 result 内存。

    运行中 / 排队中 / 已请求取消但未结束的任务保留（前端仍轮询其进度）；之后新任务照常记录。
    供前端"清理结果（释放内存）"按钮调用——展示保留到用户主动清理，不随收起丢失。
    """
    with _SFT_LOCK:
        done_keys = [
            k for k, v in _SFT_TASKS.items()
            if v.get("status") in ("success", "failed", "cancelled")
        ]
        for k in done_keys:
            _SFT_TASKS.pop(k, None)
    return {"ok": True, "cleared": len(done_keys)}


@router.get("/single-factor-test/tasks", summary="列出最近的单因子测试任务")
def single_factor_test_tasks(limit: int = 20):
    """列出最近提交的单因子测试任务（含 running，按时间倒序）。

    用于前端刷新页面后恢复"未取消完成"的任务：找到 running 任务即可重新轮询进度/继续取消。
    """
    with _SFT_LOCK:
        tasks = sorted(_SFT_TASKS.values(), key=lambda v: v.get("ts", 0), reverse=True)
    out = []
    for t in tasks[: max(1, min(int(limit or 20), 100))]:
        progress, message = _sft_render_view(t)
        out.append(
            {
                "task_id": t["task_id"],
                "status": t["status"],
                "progress": progress,
                "message": message,
                "ts": t.get("ts", 0),
                "cancel_requested": t.get("cancel_requested", False),
            }
        )
    return {"tasks": out}


# ---------- 事件研究（0/1 稀疏信号：触发事件对齐 T=0 的收益分布） ----------
# 动机：稀疏信号的"按日配对检验"在触发日样本=1 时退化为单票收益序列，均值/显著性
# 不可信（实测 CCCMA250 全 A 每日触发中位数 1 只，Top20 天贡献日差净和 100.1%）。
# 事件研究以"每次触发"为样本单位，给出真正的概率/赔率画像。


class EventStudyRequest(BaseModel):
    universe: str = "csi300"
    start_date: str = ""
    end_date: str = ""
    factor: SingleFactorTestFactor = SingleFactorTestFactor()
    max_k: int = 40                  # 最长持有交易日（1~120）
    exclude_limit_up_signal: bool = True
    exclude_limit_up_trade: bool = True
    exclude_suspended: bool = True
    exclude_st_t1: bool = False
    exclude_stock_gem: bool = False
    exclude_stock_kcb: bool = False
    price_adjust: str = "backward"      # v1.19.97：默认改后复权（收益率含分红、无除权跳空 ✓）
    price_round: bool = True
    # ★★ v2.1.52：**成交时点**（用户 2026-10-12："事件研究要跟随『成交时点』" ✓）——
    #   与单因子测试面板同名字段/同取值 ✓；默认 `t1_open`（面板默认，v2.1.34 起 ✓）。
    fill: str = "t1_open"
    suspend_remove: bool = True
    freeze_suspended_price: bool = True
    warmup_days: Optional[int] = None


_EST_TASKS: dict = {}
_EST_LOCK = threading.Lock()
_EST_RESULT_KEEP = 3   # 内存治理：只保留最近 3 个完成任务的结果


def _est_get(task_id: str):
    with _EST_LOCK:
        return _EST_TASKS.get(task_id)


def _est_store(task_id: str, state: dict) -> None:
    with _EST_LOCK:
        _EST_TASKS[task_id] = state


def _est_trim() -> None:
    with _EST_LOCK:
        finished = sorted(
            (k for k, v in _EST_TASKS.items() if v.get("status") in ("success", "failed")),
            key=lambda k: _EST_TASKS[k].get("ts", 0),
            reverse=True,
        )
        for k in finished[_EST_RESULT_KEEP:]:
            if _EST_TASKS[k].get("result") is not None:
                _EST_TASKS[k]["result"] = None


@router.post("/event-study", summary="事件研究（0/1 信号触发后收益分布，异步提交）")
def event_study(req: EventStudyRequest):
    """提交事件研究任务，返回 task_id；随后轮询 /factors/event-study/progress/{task_id}。

    仅适用于 0/1 二值信号（触发 = 因子值 > 0.5）。口径与单因子测试完全一致
    （见 factors/event_study.py 模块 docstring）。
    """
    if not req.start_date or not req.end_date:
        raise HTTPException(status_code=400, detail="请填写测试区间")
    _bad = _bad_date_arg(req.start_date, req.end_date)
    if _bad:
        raise HTTPException(status_code=400, detail=_bad)
    if not (req.factor and req.factor.expression):
        raise HTTPException(status_code=400, detail="请提供因子表达式")
    max_k = int(req.max_k or 40)
    if not (1 <= max_k <= 120):
        raise HTTPException(status_code=400, detail="最长持有期 max_k 需在 1~120 之间")
    if req.exclude_st_t1:
        from ..engine.limits import field_bin_available
        if not field_bin_available("is_st"):
            raise HTTPException(
                status_code=400,
                detail='勾选了"剔除ST(T+1)"，但本机 Qlib 数据没有 is_st 标签，请取消勾选',
            )

    task_id = uuid.uuid4().hex[:12]
    state: dict = {
        "task_id": task_id,
        "status": "running",
        "progress": 0.0,
        "message": "已提交",
        "result": None,
        "error": None,
        "cancel_requested": False,
        "ts": time.time(),
    }
    _est_store(task_id, state)

    def _run() -> None:
        manager = get_task_manager(config.WORK_DIR)
        manager.register_external_queued(task_id)
        try:
            from ..factors.single_test import _ensure_qlib_init
            _ensure_qlib_init()
        except Exception as e:  # noqa: BLE001
            state.update(status="failed", progress=100.0, message=f"事件研究失败: {e}", error=str(e), ts=time.time())
            manager.unregister_external_queued(task_id)
            _est_trim()
            return

        got = manager.external_wait_slot(
            task_id, cancel_check=lambda: bool(state.get("cancel_requested"))
        )
        manager.unregister_external_queued(task_id)
        if not got:
            return   # 排队期间被取消（终态在 cancel 侧已标记，或由收尾标记）

        def _cb(h, p: float, m: str) -> None:
            state["progress"] = float(p)
            state["message"] = m
            if state.get("cancel_requested"):
                raise FactorTestCancelled()

        try:
            res = run_event_study(
                universe=req.universe,
                start_date=req.start_date,
                end_date=req.end_date,
                factor=req.factor.model_dump(),
                max_k=max_k,
                exclude_limit_up_signal=req.exclude_limit_up_signal,
                exclude_limit_up_trade=req.exclude_limit_up_trade,
                exclude_suspended=req.exclude_suspended,
                exclude_st_t1=req.exclude_st_t1,
                exclude_stock_gem=req.exclude_stock_gem,
                exclude_stock_kcb=req.exclude_stock_kcb,
                price_adjust=req.price_adjust,
                price_round=req.price_round,
                # ★ v2.1.52：口径跟着面板的「成交时点」✓（t1_open ⇒ 开盘买/卖 ✓）
                fill=req.fill,
                suspend_remove=req.suspend_remove,
                freeze_suspended_price=req.freeze_suspended_price,
                warmup_days=req.warmup_days,
                progress_cb=_cb,
                cancelled=lambda: bool(state.get("cancel_requested")),
            )
            if res.get("error"):
                state.update(status="failed", progress=100.0, message=res["error"],
                             error=res["error"], ts=time.time())
            else:
                # ⚠ v1.19.60：触发事件（`_ev`）**不进 HTTP 响应**（几万行会把结果撑大、前端也不用），
                #   存进任务状态供「净值曲线」端点复用（`/factors/event-study/nav`）；
                #   同时留一份请求参数（取价需要区间）。
                state["_ev"] = res.pop("_ev", None)
                state["_req"] = req.model_dump()
                # v1.20.36：把耗时明细/缓存命中透出给前端（`/event-study/progress` 会带上）
                state["timings"] = res.get("timings")
                state["cached"] = res.get("cached")
                state.update(status="success", progress=100.0, message="事件研究完成",
                             result=_json_safe(res), ts=time.time())
        except FactorTestCancelled:
            state.update(status="cancelled", progress=100.0, message="已取消", ts=time.time())
        except Exception as e:  # noqa: BLE001
            state.update(status="failed", progress=100.0, message=f"事件研究失败: {e}",
                         error=str(e), ts=time.time())
        finally:
            # ⚠⚠ v1.20.35 修复（用户 2026-09-20 报「事件研究卡死」）：
            #   `external_wait_slot` 拿到的并发配额**必须成对归还**（同 `factors.py` 单因子路径
            #   的 `release_slot`）。此前这里只调 `_est_trim()`、**漏了归还** ⇒ 每做一次事件研究
            #   就永久泄漏 1 个槽；泄漏数攒到并发上限（本机 `max_concurrent=3`）之后，
            #   **后续所有事件研究 / 单因子测试都会永远排队**（message 停在「已提交」、CPU 0、
            #   磁盘 0 —— 表现完全像"卡死"，且取消也未必能唤醒）。
            #   诊断特征（很好认）：`GET /api/backtest/capacity` 的 `running` **大于实际在跑的任务数**
            #   （`running=3 / queued=1` 但没有任何任务在跑）⇒ 就是配额泄漏。
            #   注：走到这里必然已持有配额（`if not got: return` 在 try 之前）⇒ 归还恒成对。
            manager.release_slot(task_id)
            _est_trim()

    threading.Thread(target=_run, daemon=True).start()
    return {"task_id": task_id}


# ★★ 2026-10-14（用户：「加一条**未触发组等权**参照线，我看看到底咋样」✓）
#   为什么必须有它 ✗→✓：旧图里只有「沪深300」灰虚线 + 两条策略曲线 ✗，而**事件研究的真实基准
#   其实是「未触发组等权」**（超额曲线用的就是它 ✓）⇒ 同一张图上**没有可比参照** ✓，
#   实测用户把灰线当成"全 A 等权" ✗（沪深300 0.957 vs 池内等权 1.4125 ✓ 差 48% ✗ ⇒ 结论会看反 ✓）。
#   本函数产出一条**与超额曲线同源**的净值曲线 ✓：每日把「全池 − 当日触发股」等权持有一天的收益累积 ✓
#   （收盘对收盘 ✓、无成本 ✓ —— 与策略曲线的口径差异在曲线名里注明 ✓）。
#   缓存 ✓：key = (universe, 起止, 复权, 整分, 价字段, 事件数) ✓，容量 4 ✓（同一任务反复改 k 只算一次 ✓）。
_UNTRIG_CACHE: dict = {}
_UNTRIG_LOCK = threading.Lock()
_UNTRIG_CAP = 4


def _untrig_nav_series(state: dict, ev, start: str, end: str) -> List[tuple]:
    """未触发组等权净值序列 ✓（`[(日期, 净值), ...]`；起点 1.0 ✓）。"""
    rq = (state or {}).get("_req") or {}
    universe = str(rq.get("universe") or "all")
    pa = str(rq.get("price_adjust") or "backward")
    price_round = bool(rq.get("price_round", True))
    price_field = "$open" if str(rq.get("fill") or "").startswith("t1_open") else "$close"
    key = (universe, str(start), str(end), pa, price_round, price_field, int(len(ev)))
    with _UNTRIG_LOCK:
        got = _UNTRIG_CACHE.get(key)
    if got is not None:
        return got
    # ⚠ `_resolve_instruments` 的**家在 `single_test`** ✓（`event_study` 只是把它 import 进来 ✓
    #   ⇒ 单测要 monkeypatch 时打 `app.factors.event_study._resolve_instruments` ✓ 也有效 ✓）
    from ..factors.event_study import load_px_wide
    from ..factors.single_test import _resolve_instruments

    full_codes = sorted({str(c).upper() for c in _resolve_instruments(universe, start)})
    piv = load_px_wide(full_codes, str(start), str(end), pa, price_round, price=price_field)
    if piv is None or not len(piv) or len(piv) < 3:
        return []
    arr = piv.to_numpy(dtype=np.float64, copy=False)
    arr = np.where(np.isfinite(arr) & (arr > 0.0), arr, np.nan)
    with np.errstate(all="ignore"):
        ret = arr[1:] / arr[:-1] - 1.0
    valid = np.isfinite(ret)
    # ---- 剔除「前一日触发」的标的（与超额曲线同一约定：触发日 T ⇒ 从 T+1 起不在这条线上 ✓）----
    dates = list(piv.index)
    pos = {str(d)[:10]: i for i, d in enumerate(dates)}
    col = {str(c).upper(): j for j, c in enumerate(piv.columns)}
    _r: List[int] = []
    _c: List[int] = []
    for d, c in zip(list(ev["dt"]), list(ev["code"])):
        i = pos.get(str(d)[:10])
        j = col.get(str(c).upper())
        if i is None or j is None:
            continue
        if i + 1 < len(dates):                     # 信号日 T ⇒ 剔除 T+1 那一格收益 ✓
            _r.append(i)                           # ret 的第 i 行 = (T → T+1) ✓
            _c.append(j)
    if _r:
        valid[np.asarray(_r, dtype=np.intp), np.asarray(_c, dtype=np.intp)] = False
    with np.errstate(all="ignore"):
        means = np.nanmean(np.where(valid, ret, np.nan), axis=1)
    nav = np.cumprod(np.where(np.isfinite(means), 1.0 + means, 1.0))
    nav = np.concatenate([[1.0], nav])             # ret 比 dates 少一行 ✓ ⇒ 首日补 1.0 ✓
    out = [(str(d)[:10], float(v)) for d, v in zip(dates, nav)]
    with _UNTRIG_LOCK:
        while len(_UNTRIG_CACHE) >= _UNTRIG_CAP:
            _UNTRIG_CACHE.pop(next(iter(_UNTRIG_CACHE)))
        _UNTRIG_CACHE[key] = out
    return out


class EventNavRequest(BaseModel):
    """「事件研究」面板里再画一条净值曲线（两种资金方案 + 基准 + 未触发组等权）。"""
    task_id: str
    hold_days: int = 20
    cost: float = 0.004
    capital: float = 1e9
    benchmark: str = "SH000300"
    # ⚠ 两种来源：① 独立事件研究任务（`/event-study`）⇒ 用 `_ev`；
    #   ② 单因子测试的"秒开"路径（弹窗直接用表格里的 event_study 结果，没有独立任务）
    #      ⇒ 任务状态里存的是**按表达式分组**的 `_evs`，用因子表达式取。
    #   `task_id` 允许为空（v1.19.61）：前端拿不到任务 id 时，仅凭 `factor_id` 也能在**最近的任务**里
    #   找回触发事件（用户 2026-09-15 报「净值曲线暂不可用：需要任务上下文」）。
    task_id: str = ""
    factor_id: Optional[str] = None
    # ★★ v2.1.65：**成交时点**（用户 2026-10-12："事件研究的净值曲线是跟随面板参数次日收盘/开盘走的
    #   还是写死了次日收盘价？我怎么测下来两个数一模一样" ✓ —— 观察**完全正确** ✓，实测根因 ✓）：
    #   本端点原来把 `fill` **写死 `t1_open`** ✗（见 `run_backtest(...)` 调用 ✓，只是当年为对拍
    #   硬编码的 ✗）⇒ 面板选"次日收盘"也照样开盘成交 ⇒ 两种设置**数字必然一模一样** ✗✗。
    #   现在跟随面板/请求 ✓。
    # ★★ v2.1.71：**默认从 `"t1_open"` 改成 `None`** ✗→✓ —— 否则"跟随面板那次口径"的兜底
    #   **永远不触发** ✗：pydantic 会把"没传"填成 `"t1_open"` ✗ ⇒ 下面
    #   `req.fill or rq["fill"]` 的左边恒为真 ✓。**实测证据**（`ai_test/check_export_fill2.log` ✓）：
    #   任务按"次日收盘"提交 ✓，而净值请求**故意不传 `fill`** ⇒ 拿到的仍是 `t1_open` 那条
    #   （期末 0.9981 vs 收盘那条 0.9999 ✗）⇒ 导出/净值一起写错口径 ✗（用户 2026-10-12 报的 ✓）。
    #   ⇒ 语义：`None` = **跟随那次任务（面板）的口径** ✓；显式传三个取值仍照用 ✓（可切换 ✓）。
    fill: Optional[str] = None


# ---------------------------------------------------------------------------
# 净值曲线的「价格面板 + 涨跌停标注」缓存（v1.19.93）
#
# 为什么（用户 2026-09-17：过顶**触发 14.88 万次** ⇒ 净值曲线长挂"计算中"✗）：
#   `/event-study/nav` 每次请求都要 `load_price_panel(事件涉及的所有股票, 起止)` +
#   `fill_limits(...)` —— 对"事件覆盖几千只股票 × 十几年"的公式，这两步**与 k 完全无关**
#   （只有回测那部分随 k 变 ✓），却每改一次 k 就重算一遍 ✗ ⇒ 拖动持仓周期像卡死 ✗。
#   ⇒ 按 (codes, start, end, strict) **单条缓存**（只留最近 1 条：一块 5000 列 × 4000 行的
#     面板已是数百 MB 量级，多留会吃内存 ✗）。
#   ⚠ 这是"治本（C）"的第 1 步；第 2 步（把每日估值/持仓市值向量化）改动在
#     `signals/engine.run_backtest` 内，必须带**新旧对拍**（成交/被拒/净值逐位一致）才敢上 ✓。
# ---------------------------------------------------------------------------
_NAV_PANEL_CACHE: dict = {}
_NAV_PANEL_LOCK = threading.Lock()

# ---------------------------------------------------------------------------
# 回测结果缓存（v1.20.0；用户 2026-09-18：「事件研究里净值曲线（两种方案 + 基准）计算很慢」）
#
# 病根：`run_backtest` **与 k 强相关** ⇒ 改一次持仓周期就整条重跑 ✗，且它内部是
#   **逐日 Python 循环 × 两种资金方案 × 遍历全部持仓** ⇒ 过顶（14.88 万触发）下单次数十秒 ✓。
#   而"同一 k 重复点开"（前端缓存失效后重算 / 切换基准 / 关掉弹窗再开）尤其浪费 ✗。
# 做法：按 (codes 指纹 + 区间 + **事件指纹** + k + cost + capital + modes + 口径) 缓存 `BtResult` ✓。
#   结果对象很小（净值/成交/统计 ✓）⇒ 容量 4 的 LRU 足够覆盖"来回切 2~3 个 k/成本" ✓。
#   ⚠ key **必须含事件指纹**（同一批 codes 但触发集合不同 ⇒ 结果不同 ✗）。
#   ⚠ `BtResult` 只读复用（调用方不改 ✓）⇒ 直接返回同一对象 ✓ 省一次深拷贝 ✓。
# ---------------------------------------------------------------------------
_NAV_BT_CACHE: dict = {}
_NAV_BT_LOCK = threading.Lock()
_NAV_BT_CAP = 4

# ---------------------------------------------------------------------------
# 「净值曲线那次回测」的**导出旁路**（v2.1.0；用户 2026-10-11 要"事件研究导出也要每期持仓"✓）
#
# 为什么需要它：每期持仓只能从 `bt.trades` 派生（回测产物 ✓），而 `bt` 对象**不进 HTTP 响应**
#   （几万笔成交会撑爆 JSON ✗）⇒ 导出侧没法直接拿到 ✗。重新算一遍又要重跑回测（白烧 CPU ✗）、
#   复制一份"取事件 + 取价面板 + 回测"的逻辑又是两处漂移 ✗（最坏的一种）。
# ⇒ 这里让 `/event-study/nav` 把**刚算好的** `bt` 顺手按"调用方可复算的键"登记一份 ✓，
#   导出端点先调同一个端点（命中缓存 ⇒ 秒回 ✓）再按同一个键取 `bt` ✓ —— 两侧**同一份实现** ✓。
#   ⚠ 键里必须含 task/factor/k/cost/capital（不同参数是不同回测 ✗）；容量 4、FIFO ✓。
# ---------------------------------------------------------------------------
_NAV_BT_STASH: dict = {}
_NAV_BT_STASH_CAP = 4


def _nav_stash_key(task_id: str, factor_id: Optional[str], hold_days: int,
                   cost: float, capital: float) -> tuple:
    return (str(task_id or ""), str(factor_id or ""), int(hold_days), float(cost), float(capital))


def nav_bt_stash_get(task_id: str, factor_id: Optional[str], hold_days: int,
                     cost: float, capital: float):
    """导出用：取回 `/event-study/nav` **刚算过的那次**结果（含 `bt` / `signals` / 输入参数 ✓）。

    ★ v2.1.4：登记的东西从"只有 `bt`"扩成**"`bt` + 输入"** ✗→✓ —— 因为导出（XLSX）的意义
      是"把面板这次回测**原样搬走**"：信号（触发日/代码 ✓）、`fill`、`strict_limit`、
      `rebal_band`、`capital`、`cost`、`hold_days` 全都要跟着走 ✓，否则回灌方"看起来一样、
      结果不一样" ✗（用户 2026-10-11 报的就是这个：面板 `t1_open` + 触发日信号 + 死区 0，
      而文件里被我写成了 `t1_close` + **成交日**信号 + 界面死区 ⇒ 四个输入全不同 ✗）。

    返回 `{"bt":…, "signals":DataFrame, "fill":…, "strict_limit":…, "rebal_band":…, …}`；没有 ⇒ None ✓。
    ⚠ 调用方应**先调用一次** `event_study_nav(...)`（命中缓存 ⇒ 秒回 ✓）再取 ⇒ 保证有值 ✓。
    """
    with _NAV_BT_LOCK:
        return _NAV_BT_STASH.get(_nav_stash_key(task_id, factor_id, hold_days, cost, capital))

# ---------------------------------------------------------------------------
# 取消源（v1.20.5；用户 2026-09-18：「先把取消源接通」✓）
#
# 需求：用户在净值曲线上反复改持仓周期（60 → 6 → …）时，**旧的那次回测应当立刻停** ✗
#   —— 否则每改一次就多堆一个"逐日循环 × 两资金方案"的任务在后台空烧 CPU ✓。
# 做法：按 **回测指纹**（= `_nav_bt_key`，含事件指纹 + k + 成本 ✓）维护一个**单调递增序号** ✓。
#   每次请求开始时把自己的序号记下来 ✓，`run_backtest` 主循环每 20 个交易日回查一次
#   （`cancel_cb`，见 `signals/engine.py` ✓）⇒ **序号被后来者顶掉 ⇒ 抛 `_NavCancelled`** ✓
#   ⇒ 立即中止（不再继续算完 ✓）。响应 409 ⚠ 前端此时早已 abort/换了 key ⇒ 通常看不到它 ✓。
# ⚠ 用**回测指纹**而不是任务 id：改 k 时 `_nav_bt_key` 里 k 不同 ⇒ **不会互相取消** ✗
#   ⇒ 所以这里额外**只按事件指纹分组**（见 `_nav_req_key` ✓），同一批事件下"最新那次 k 才算数" ✓。
# ---------------------------------------------------------------------------
_NAV_REQ_SEQ: dict = {}
_NAV_REQ_LOCK = threading.Lock()
_NAV_REQ_KEEP = 32


class _NavCancelled(Exception):
    """同事件指纹下已有更新的请求 ⇒ 本次在途回测立即中止（由 `cancel_cb` 抛出）✓。"""


def _nav_req_key(codes, ev, start, end):
    """取消分组键：**只含事件与区间**（不含 k/cost ✓ ⇒ 同批事件下"最新的 k 才算数" ✓）。"""
    return (len(codes), hash(tuple(codes)), str(start), str(end),
            len(ev), str(ev["dt"].iloc[0]), str(ev["dt"].iloc[-1]))


def _nav_req_begin(key) -> int:
    with _NAV_REQ_LOCK:
        _NAV_REQ_SEQ[key] = _NAV_REQ_SEQ.get(key, 0) + 1
        while len(_NAV_REQ_SEQ) > _NAV_REQ_KEEP:      # 防字典无限增长 ✓
            _NAV_REQ_SEQ.pop(next(iter(_NAV_REQ_SEQ)))
        return _NAV_REQ_SEQ[key]


def _nav_req_current(key) -> int:
    with _NAV_REQ_LOCK:
        return _NAV_REQ_SEQ.get(key, 0)


# ⚠ v1.20.15（用户 2026-09-18：「关弹窗即取消」✓）：前端**卸载弹窗**时会调
#   `POST /event-study/nav/cancel` ✓ ⇒ 这里记下 task_id ✓；在途回测的 `cancel_cb`
#   每次检查命中即抛 ⇒ **关掉弹窗后后端立刻停算** ✓（此前是"自己跑完为止、没人收结果"✗，
#   大公式一次白烧几十秒 ✓）。
_NAV_CANCEL: set = set()
_NAV_CANCEL_MAX = 64


def _nav_bt_key(codes, ev, start, end, k, cost, capital, modes, fill: str = "t1_open"):
    # ★ v2.1.65：**`fill` 必须进回测缓存键** ✗→✓ —— 否则面板从"次日开盘"切到"次日收盘"时
    #   会**命中同一份缓存** ✗ ⇒ 净值不动 ⇒ 用户看到"两个数一模一样" ✗✓（就是这个现象 ✓）。
    return (len(codes), hash(tuple(codes)), str(start), str(end),
            len(ev), str(ev["dt"].iloc[0]), str(ev["dt"].iloc[-1]),
            int(k), float(cost), float(capital), tuple(modes), str(fill))


def _nav_cache_key(codes, start, end, strict: bool = True):
    return (len(codes), hash(tuple(codes)), str(start), str(end), bool(strict))


def _nav_panel_cached(codes, start, end, *, strict: bool = True):
    """取「价格面板 + 涨跌停标注」，按 (codes, 区间, strict) 缓存（见上方说明，v1.19.93）。"""
    from ..signals.pricing import fill_limits, load_price_panel

    key = _nav_cache_key(codes, start, end, strict)
    with _NAV_PANEL_LOCK:
        hit = _NAV_PANEL_CACHE.get(key)
    if hit is not None:
        return hit, True
    # ⚠ 必须与端点原调用一致：`need_open=True`（回测要开盘价 ✓）
    panel = load_price_panel(codes, start, end, need_open=True)
    if panel is None or "CLOSE" not in panel:
        return panel, False
    panel = fill_limits(panel, strict=strict)
    with _NAV_PANEL_LOCK:
        # ⚠ v1.20.0：原为 `clear()` **只留 1 条** ✗ ⇒ 换个因子/池（或先点 K=5 再点 K=20 触发
        #   不同 codes 集合）就整段重算（全A 面板 ≈12s ✓）⇒ 改为**容量 2 的 LRU**：一块
        #   5000×4000 的宽表面板是数百 MB 量级 ✓，容 2 条即可覆盖"来回切两个池/因子"的常见操作 ✓。
        while len(_NAV_PANEL_CACHE) >= 2:
            _NAV_PANEL_CACHE.pop(next(iter(_NAV_PANEL_CACHE)))
        _NAV_PANEL_CACHE[key] = panel
    return panel, False


class EventNavCancelRequest(BaseModel):
    """关弹窗取消（v1.20.15）：前端只要把 task_id 报上来即可 ✓。"""
    task_id: str = ""


@router.post("/event-study/nav/cancel", summary="取消净值曲线计算（关弹窗时调用）")
def event_study_nav_cancel(req: EventNavCancelRequest):
    """把该 task 标记为"已放弃" ✓ ⇒ 在途回测的 `cancel_cb` 下一次检查即抛 ⇒ 立即停算 ✓。

    ⚠ 幂等 ✓、永远返回 ok ✓（前端卸载时调用，不该因任何原因报错 ✓）。
    ⚠ 集合上限 64（FIFO 清理 ✓）—— 只存 task_id，量极小 ✓。
    """
    tid = str(req.task_id or "")
    if tid:
        _NAV_CANCEL.add(tid)
        while len(_NAV_CANCEL) > _NAV_CANCEL_MAX:
            _NAV_CANCEL.pop()
    return {"ok": True}


@router.post("/event-study/nav", summary="事件研究的净值曲线（两种资金方案 + 基准）")
def event_study_nav(req: EventNavRequest):
    """复用事件研究任务里**已经算好的触发事件**与磁盘缓存的价格面板，只花"一次回测"的钱。

    ⚠ 为什么单开端点、不塞进事件研究任务：净值要**跟随持仓周期 k 变动** ⇒ 每次改 k 都得重跑一遍
      回测（实测 0.34~0.59s/次，见 `ai_test/bench_event_nav.py`），不能一次算死。
      触发事件与价格面板都复用现成的（事件存在任务状态、面板走 `feature_cache`）⇒ 单次请求 ~0.5~1s。
    """
    # 事件研究任务与单因子测试任务**共用这个 id 查询**（两类状态字典都认一下）
    state = _est_get(req.task_id) or _sft_get(req.task_id)
    if state is None and req.task_id:
        raise HTTPException(status_code=404, detail="任务不存在或未完成（结果可能已被清理）")
    rq = (state or {}).get("_req") or {}
    ev = None
    if req.factor_id:
        ev = ((state or {}).get("_evs") or {}).get(req.factor_id)
    if ev is None and state is not None:
        ev = state.get("_ev")
    if ev is None and req.factor_id:
        # 兜底（v1.19.61）：任务 id 失效/没传时，按**因子表达式**在最近的任务里找触发明细。
        # 只扫最近 _SFT_RESULT_KEEP 个"有事件"的任务，命中即用（单因子测试常连跑多次，同因子很容易找到）。
        for st in sorted(_SFT_TASKS.values(), key=lambda v: v.get("ts", 0), reverse=True):
            found = (st.get("_evs") or {}).get(req.factor_id)
            if found is not None and len(found):
                ev, state = found, st
                rq = st.get("_req") or rq
                break
        if ev is None:
            for st in sorted(_EST_TASKS.values(), key=lambda v: v.get("ts", 0), reverse=True):
                found = st.get("_ev")
                if found is not None and len(found):
                    ev, state = found, st
                    rq = st.get("_req") or rq
                    break
    if ev is None or not len(ev):
        raise HTTPException(status_code=400,
                            detail="找不到这次信号的触发明细（任务已清理或因子没触发过）—— "
                                   "点「重新计算」重跑一次事件研究即可看净值曲线")
    start, end = rq.get("start_date"), rq.get("end_date")
    k = max(1, min(int(req.hold_days or 20), 250))
    codes = sorted(ev["code"].astype(str).unique().tolist())
    # 单向依赖：factors → signals（signals 侧不反向依赖 factors，无环）
    from ..signals.engine import attach_benchmark, nav_rows, run_backtest
    from ..signals.pricing import load_bench_wide

    timings: dict = {}
    try:
        t0 = time.perf_counter()
        # ★ v1.19.93：取价面板 + 涨跌停标注**与 k 无关** ⇒ 走进程内缓存
        #   （过顶触发 14.88 万次、事件覆盖几千只股票时，这一步原来每改一次 k 就重算一遍 ✗）
        panel, _hit = _nav_panel_cached(codes, start, end, strict=True)
        timings["prices"] = round(time.perf_counter() - t0, 3)
        timings["prices_cached"] = bool(_hit)
        if panel is None or "CLOSE" not in panel:
            raise HTTPException(status_code=400, detail="取不到触发标的的行情数据")
        # ★★ v2.1.79（用户 2026-10-13 拍板 ✓）：**不复权时净值曲线也用不复权** ✗→✓
        #   为什么改（用户的规矩 ✓）：他要"**与聚宽对齐**" ⇒ **一个口径走全链** ✓
        #   （因子/事件研究/回测/净值/回灌 全都不复权 ✓），而不是我 v2.1.73 自己定的
        #   "金额类永远后复权" ✗ —— 那条虽然是"财务正确" ✓，但**破坏了对拍** ✗：
        #   实测（`ai_test/ab_fuquan.py` ✓，只改复权口径 ✓）：
        #     组合卡 Q10 年化 backward 24.18% → none 21.03%（−3.15pp ✗ 真实价的除权跳空 ✓）
        #     实盘口径    backward 22.87% → none 22.87%（0.00pp ✓ 因为当时它被写死后复权 ✗）
        #   ⇒ 两边基准不同 ⇒ "实盘 vs 模型"的结论会**随复权口径翻转** ✗（用户当场发现 ✓）。
        #   ⚠ **必须浅拷贝** ✓：`_nav_panel_cached` 返回的是**进程内缓存对象** ✗，
        #     就地改 `panel["CLOSE"]` 会污染后续所有请求 ✓✓（比 bug 本身更糟 ✗）。
        #   ⚠ `CLOSE_RAW`/`OPEN_RAW` **不动** ✓（停牌/涨跌停判定仍按真实价 ✓）。
        if str(rq.get("price_adjust") or "").strip().lower() == "none":
            panel = dict(panel)
            _cr = panel.get("CLOSE_RAW")
            if _cr is not None and len(_cr):
                panel["CLOSE"] = _cr.ffill().round(2)
                _orr = panel.get("OPEN_RAW")
                if _orr is not None and len(_orr):
                    panel["OPEN"] = _orr.ffill().round(2)
        t0 = time.perf_counter()
        _modes = ("event_even", "cash_even")
        # ★★ v2.1.66：**口径归一必须放在最外层** ✗→✓（用户 2026-10-12 实测报错：
        #   `UnboundLocalError: local variable '_fill_nav' referenced before assignment` ✗）
        #   —— v2.1.65 我把它定义在「回测**缓存未命中**」的 else 分支里 ✗，而下面
        #   「登记给导出用」那步**命中缓存时也会用它** ⇒ 第二次同口径请求（命中缓存 ✓）必炸 ✗。
        # ★★ v2.1.69：**兜底改成"跟随那次任务的请求"** ✗→✓（用户 2026-10-12 实测：
        #   面板选「次日收盘」✓，导出的 XLSX 参数页却写「次日开盘」✗）。
        #   根因：本函数要**三方**都同意口径 —— ① 本请求 `req.fill`（v2.1.65 起前端才发 ✗，
        #   老 bundle 不发 ⇒ `None` ✗）② 那次任务自己的 `rq["fill"]`（= 面板真正用的 ✓）
        #   ③ 默认 `t1_open` ✓。旧代码只认 ① ✗ ⇒ 老前端 ⇒ 退成 `t1_open` ✗ ⇒
        #   净值/导出登记都写错 ✗（而**事件研究本身**用的是 `rq["fill"]` = 次日收盘 ✓
        #   ⇒ 文件里"参数=开盘 ✗ / 曲线=收盘 ✓"自相矛盾 ✓，回灌必然对不上 ✓）。
        #   ⇒ 现在按 ① > ② > ③ 取 ✓（任何一条链传了口径都认 ✓，绝不会无声退回默认 ✗）。
        _fill_nav = str(req.fill or rq.get("fill") or "t1_open").strip().lower()
        if _fill_nav not in ("t1_open", "t1_close", "t_close"):
            _fill_nav = "t1_open"
        _btk = _nav_bt_key(codes, ev, start, end, k, req.cost, req.capital, _modes, _fill_nav)
        # 取消源（v1.20.5）：登记本次请求序号；同事件指纹来了更新的请求 ⇒ 本次立即自停 ✓
        _seq = _nav_req_begin(_nav_req_key(codes, ev, start, end))

        _tid_key = str(req.task_id or "")
        # ⚠⚠ 必须**清除旧的取消标记** ✗ —— 否则用户"关掉弹窗、再重新打开同一个公式"时
        #   会命中上次留下的标记 ⇒ **一开就被取消** ✓（v1.20.15 自查发现 ✓）。
        if _tid_key:
            _NAV_CANCEL.discard(_tid_key)

        def _cancel_cb():
            # ① 前端关了弹窗（v1.20.15 ✓）② 同事件指纹来了更新的请求（v1.20.5 ✓）⇒ 立即自停 ✓
            if _tid_key and _tid_key in _NAV_CANCEL:
                raise _NavCancelled()
            if _nav_req_current(_nav_req_key(codes, ev, start, end)) != _seq:
                raise _NavCancelled()
        # ⚠ 信号表要在**缓存判断之前**建好 ✗→✓：v2.1.4 起它还要进"给导出用的登记"（stash ✓），
        #   若只在"缓存未命中"分支里建 ⇒ 命中缓存时 `events` 未绑定 ⇒ UnboundLocalError ✗
        #   （实测被 ai_test/check_replay_identical.py 抓出来：HTTP 400 ✓）。建表本身是 O(事件数) ✓
        #   的浅拷贝级开销（几万行也就几毫秒 ✓），放在这里不亏 ✓。
        events = pd.DataFrame({"date": pd.to_datetime(ev["dt"]),
                               "code": ev["code"].astype(str), "side": 1})
        with _NAV_BT_LOCK:
            bt = _NAV_BT_CACHE.get(_btk)
        if bt is not None:
            timings["backtest_cached"] = True
        else:
            timings["backtest_cached"] = False
            # ★ v2.1.65：**fill 跟随请求** ✗→✓（原先写死 `"t1_open"` ✗ ⇒ 面板切"次日收盘"无效果 ✗）
            #   ⚠ `_fill_nav` 由外层统一算好（v2.1.66 修：别在分支里定义 ✗，命中缓存时下面还要用 ✓）
            bt = run_backtest(events, panel, hold_days=k, fill=_fill_nav, cost=float(req.cost),
                              capital=float(req.capital), strict_limit=True,
                              alloc_modes=_modes, cancel_cb=_cancel_cb)
            with _NAV_BT_LOCK:
                while len(_NAV_BT_CACHE) >= _NAV_BT_CAP:
                    _NAV_BT_CACHE.pop(next(iter(_NAV_BT_CACHE)))
                _NAV_BT_CACHE[_btk] = bt
        timings["backtest"] = round(time.perf_counter() - t0, 3)
        if bt.nav is None:
            raise HTTPException(status_code=400, detail="回测没有产出净值：%s" % bt.diag.get("error"))
        # v2.1.0：登记给导出用（**不进响应** ✗）—— 导出侧据此从 `bt.trades` 派生每期持仓 ✓
        _stash_key = _nav_stash_key(req.task_id, req.factor_id, k, float(req.cost), float(req.capital))
        with _NAV_BT_LOCK:
            _NAV_BT_STASH.pop(_stash_key, None)
            # ★ v2.1.4：连同**这次回测的输入**一起登记（导出要原样搬走 ⇒ 回灌才能逐位一致 ✓）
            #   `signals` 是**信号日**（触发日 ✓）而不是成交日 —— 引擎会自己按 `fill` 成交 ✓
            _NAV_BT_STASH[_stash_key] = {
                "bt": bt, "signals": events,
                # ★ v2.1.65：登记**本次真实用的**口径（原来写死 `"t1_open"` ✗ ⇒ 导出文件跟着写错 ✗）
                "fill": _fill_nav, "strict_limit": True, "rebal_band": 0.0,
                "hold_days": int(k), "cost": float(req.cost), "capital": float(req.capital),
            }
            while len(_NAV_BT_STASH) > _NAV_BT_STASH_CAP:
                _NAV_BT_STASH.pop(next(iter(_NAV_BT_STASH)))
        t0 = time.perf_counter()
        bench = load_bench_wide([req.benchmark], start, end)
        nav = attach_benchmark(bt.nav, bench, req.benchmark)
        timings["benchmark"] = round(time.perf_counter() - t0, 3)
        stats = dict(bt.stats or {})
        # ★★ 2026-10-14（用户："加一条未触发组等权参照线" ✓）：
        #   为什么需要它 ✗→✓：面板上原来只有「沪深300」（灰虚线 ✓）与两条策略曲线 ✓ ——
        #   而**"事件研究"里的基准其实是「未触发组等权」**（超额曲线用的就是它 ✓），
        #   两者在同一张图上**没有可比参照** ⇒ 用户实测时把灰线当成了"全 A 等权" ✗
        #   （实测沪深300 = 0.957，而池内等权 = 1.4125 ✓ 差 48% ✗ ⇒ 结论会看反 ✓）。
        #   ∴ 把「未触发组等权」也画成一条曲线 ✓（同日剔除当日触发股 ✓、等权 ✓、无成本 ✓），
        #     与超额曲线**同源** ✓ ⇒ "事件到底跑没跑赢它自己的基准"一眼可见 ✓。
        try:
            _un = _untrig_nav_series(state, ev, start, end)
            if _un is not None and len(_un):
                _s = pd.Series({pd.Timestamp(d): float(v) for d, v in _un})
                nav = nav.copy()
                nav["bench_equal"] = _s.reindex(nav.index).ffill()
                try:
                    from ..engine.perf_metrics import compute_perf      # 与上面净值统计同一个 ✓
                    _ret = nav["bench_equal"].pct_change().fillna(0.0).to_numpy()
                    stats["bench_equal"] = compute_perf(_ret)
                except Exception:                                  # noqa: BLE001
                    pass
        except Exception as _ue:                                  # noqa: BLE001
            # 参照线失败只影响展示（与基准失败同一策略 ✓）⇒ 不拖垮净值曲线 ✓
            timings["untrig_error"] = repr(_ue)[:160]
        return {"hold_days": k, "nav": nav_rows(nav), "stats": stats, "diag": bt.diag,
                "nav_columns": [str(c) for c in nav.columns],
                "alloc_default": "event_even", "timings": timings}
    except _NavCancelled:
        # 被"更新的那次 k"顶掉 ⇒ 如实告知（前端此时通常已切到新的 key ✓，一般看不到这条 ✓）
        raise HTTPException(status_code=409, detail="已被更新的一次计算取代（旧请求自动取消）")
    except HTTPException:
        raise
    except Exception as e:                                      # noqa: BLE001
        # 这类失败基本都是数据侧问题（标的/区间/字段缺失）⇒ 给**可读原因**而不是裸 500
        raise HTTPException(status_code=400, detail="净值计算失败：%s: %s" % (type(e).__name__, e))


@router.get("/event-study/progress/{task_id}", summary="查询事件研究任务进度")
def event_study_progress(task_id: str):
    state = _est_get(task_id)
    if state is None:
        raise HTTPException(status_code=404, detail="任务不存在或已过期")
    return {
        "task_id": task_id,
        "status": state["status"],
        "progress": float(state.get("progress", 0.0)),
        "message": state.get("message", ""),
        "result": state.get("result"),
        "error": state.get("error"),
        # v1.20.36：耗时明细 + 缓存命中标记（前端据此自适应"自动跟随 / 手工按钮"，不显示耗时数字也用它做判定）
        "timings": state.get("timings"),
        "cached": state.get("cached"),
    }


@router.post("/event-study/cancel/{task_id}", summary="取消事件研究任务")
def event_study_cancel(task_id: str):
    state = _est_get(task_id)
    if state is None:
        raise HTTPException(status_code=404, detail="任务不存在或已过期")
    if state.get("status") != "running":
        return {"ok": False, "message": "任务已结束，无需取消"}
    state["cancel_requested"] = True
    try:
        manager = get_task_manager(config.WORK_DIR)
        manager.wake_external_waiters()
    except Exception:
        pass
    return {"ok": True, "message": "已请求取消，正在终止..."}


@router.post("/event-study/clear", summary="清理已结束的事件研究任务与结果")
def event_study_clear():
    with _EST_LOCK:
        done_keys = [k for k, v in _EST_TASKS.items()
                     if v.get("status") in ("success", "failed", "cancelled")]
        for k in done_keys:
            _EST_TASKS.pop(k, None)
    return {"ok": True, "cleared": len(done_keys)}
