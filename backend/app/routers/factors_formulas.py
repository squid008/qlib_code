# -*- coding: utf-8 -*-
"""**公式库**接口（从 `routers/factors.py` 拆出 ✓ P1-A，2026-10-18）。

## 为什么拆（用户要求"把上帝文件拆开"✓，`factors.py` 曾 2,489 行 ✗）

`factors.py` 一个文件里塞了 **4 组**互不相干的端点（公式库 / 单因子测试 / 事件研究 / 目录 ✓），
是全仓第二长的文件 ✗。本模块搬走其中最**自足**的一组 ✓：

  · **翻译**（`/translate` ✓）
  · **自定义公式** CRUD + 置顶（`/custom-formulas` ✓，含同名查重 409 ✓、依赖失效 ✓、物化提示 ✓）
  · **目录类**（`/operators` `/datasets` `/catalog` ✓）

## ⚠ URL 一个字都没变 ✓

新模块**自带** `APIRouter(prefix="/api/factors")` ✓（与原来同一个 prefix ✓），
`main.py` 只是多 include 一个 router ⇒ **对外路径表完全一致** ✓
（`tests/test_routers_smoke.py` 的"关键路径必须存在"断言就是这条的守门 ✓）。

## ⚠ 依赖方向（别搞反 ✗）

本模块**只依赖** `services.custom_formulas` / `factors.parser` / `factors.catalog` ✓，
**不 import** `factors.py` ✓（所以不会与它循环 ✓）。
"""
import uuid
from typing import Optional

from fastapi import APIRouter, HTTPException
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from ..factors.catalog import FACTOR_PROVIDERS, get_catalog
from ..factors.parser import (
    CodeGenError,
    LexerError,
    ParseError,
    SemanticError,
    build_library,
    translate_formula,
)
from ..services.custom_formulas import (
    create_custom_formula as _create_custom_formula,
)
from ..services.custom_formulas import (
    delete_custom_formula as _delete_custom_formula,
)
from ..services.custom_formulas import (
    find_live_by_name as _find_live_by_name,
)
from ..services.custom_formulas import (
    list_custom_formulas as _list_custom_formulas,
)
from ..services.custom_formulas import (
    mark_dependents_stale as _mark_dependents_stale,
)
from ..services.custom_formulas import (
    set_pinned as _set_pinned,
)
from ..services.custom_formulas import (
    update_custom_formula as _update_custom_formula,
)

router = APIRouter(prefix="/api/factors", tags=["factors"])

class TranslateRequest(BaseModel):
    formula: str = ""          # 益盟/通达信公式文本（允许整段粘贴，含 := 中间变量，1 条输出线）
    patchable: bool = False    # 是否允许外挂算子占位（默认 False，含外挂算子报错）


class CustomFormulaBody(BaseModel):
    formula: str = ""          # 与 TranslateRequest 一致：用户原文公式
    patchable: bool = False
    allow_duplicate: bool = False   # ★ v1.20.82：明知同名也要新建一条 ⇒ 跳过查重提示 ✓


def _formula_library(exclude_id: Optional[str] = None) -> dict:
    """公式库（供「公式间调用」，v1.19.87）：已保存公式的 `名 → 原文`。

    ⚠ 编辑某条公式时要**把自己排除**（`exclude_id`）—— 否则它引用同名公式会立刻变成
      "循环引用"（用户只是想改自己的正文）✓。
    """
    items = _list_custom_formulas()
    return build_library([i.get("text", "") for i in items if i.get("id") != exclude_id])


def _compile_formula_or_400(formula: str, patchable: bool = False, exclude_id: Optional[str] = None):
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
    # ★ v2.11.38：`str(... or "")` ✓ —— 该函数签名要求 `str` ✓，而 `item.get("id")` 是 `Any | None` ✗
    _mark_dependents_stale(t.name, exclude_id=str(item.get("id") or ""))
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
    from ..factors.parser.codegen import FUNC_QLIB, IGNORED_OPS, LEVEL2_OPS, PATCHED_OPS
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
    # ★ v2.11.38：`str(...)` ✓ —— `FACTOR_PROVIDERS` 未标类型 ⇒ 取出来是 `object` ✗，不能 `.lower()` ✓
    available = {str(reg["dataset"]).lower() for reg in FACTOR_PROVIDERS}
    if dataset.lower() not in ("mixed",) and dataset.lower() not in available and dataset.lower() != "alpha360":
        raise HTTPException(status_code=404, detail=f"未知特征集: {dataset}")
    return get_catalog(dataset)
