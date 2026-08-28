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

router = APIRouter(prefix="/api/factors", tags=["factors"])


class TranslateRequest(BaseModel):
    formula: str = ""          # 益盟/通达信公式文本（允许整段粘贴，含 := 中间变量，1 条输出线）
    patchable: bool = False    # 是否允许外挂算子占位（默认 False，含外挂算子报错）


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
