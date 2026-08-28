# -*- coding: utf-8 -*-
"""因子库目录接口。

对外提供特征集的"因子名 + 表达式 + 分类 + 描述"目录，供前端勾选特征、
悬停查看公式。设计为可扩展：未来维护成千上万因子时，只需在
app/factors/catalog.py 的 FACTOR_PROVIDERS 注册新的 provider，接口无需改动。
"""
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from ..factors.catalog import get_catalog, FACTOR_PROVIDERS
from ..factors.parser import (
    translate_formula, LexerError, ParseError, SemanticError, CodeGenError,
)
from ..services.custom_formulas import (
    list_custom_formulas as _list_custom_formulas,
    create_custom_formula as _create_custom_formula,
    update_custom_formula as _update_custom_formula,
    delete_custom_formula as _delete_custom_formula,
)

router = APIRouter(prefix="/api/factors", tags=["factors"])


class TranslateRequest(BaseModel):
    formula: str = ""          # 益盟/通达信公式文本（允许整段粘贴，含 := 中间变量，1 条输出线）
    patchable: bool = False    # 是否允许外挂算子占位（默认 False，含外挂算子报错）


class CustomFormulaBody(BaseModel):
    formula: str = ""          # 与 TranslateRequest 一致：用户原文公式
    patchable: bool = False


def _compile_formula_or_400(formula: str, patchable: bool = False):
    """编译公式；成功返回 TranslatedFactor，失败抛 HTTPException(400)。"""
    if not formula or not formula.strip():
        raise HTTPException(status_code=400, detail="公式不能为空")
    try:
        return translate_formula(formula, patchable=patchable)
    except (LexerError, ParseError, SemanticError, CodeGenError) as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.post("/translate", summary="翻译益盟/通达信公式为 qlib 表达式")
def translate(req: TranslateRequest):
    """把一段益盟/通达信公式翻译成单个输出因子。

    成功：{name, expression, inputs, has_patch, source_formula}
    失败：400 + 中文错误提示（LexerError/ParseError/SemanticError/CodeGenError）
    """
    if not req.formula or not req.formula.strip():
        raise HTTPException(status_code=400, detail="公式不能为空")
    try:
        t = translate_formula(req.formula, patchable=req.patchable)
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


@router.post("/custom-formulas", summary="编译并保存自定义公式")
def create_saved_formula(req: CustomFormulaBody):
    """编译用户公式并保存到 workdir/custom_formulas.json，返回保存的条目。"""
    t = _compile_formula_or_400(req.formula, req.patchable)
    return _create_custom_formula(t.name, req.formula.strip(), t.expression)


@router.put("/custom-formulas/{formula_id}", summary="编辑自定义公式（重新编译并保存）")
def update_saved_formula(formula_id: str, req: CustomFormulaBody):
    """按 id 修改公式：重新编译后覆盖 text/name/expression。"""
    t = _compile_formula_or_400(req.formula, req.patchable)
    item = _update_custom_formula(formula_id, t.name, req.formula.strip(), t.expression)
    if item is None:
        raise HTTPException(status_code=404, detail="公式不存在")
    return item


@router.delete("/custom-formulas/{formula_id}", summary="删除自定义公式")
def delete_saved_formula(formula_id: str):
    if not _delete_custom_formula(formula_id):
        raise HTTPException(status_code=404, detail="公式不存在")
    return {"ok": True}


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
    if dataset.lower() not in available and dataset.lower() != "alpha360":
        raise HTTPException(status_code=404, detail=f"未知特征集: {dataset}")
    return get_catalog(dataset)
