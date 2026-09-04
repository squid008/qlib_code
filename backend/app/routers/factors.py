# -*- coding: utf-8 -*-
"""因子库目录接口。

对外提供特征集的"因子名 + 表达式 + 分类 + 描述"目录，供前端勾选特征、
悬停查看公式。设计为可扩展：未来维护成千上万因子时，只需在
app/factors/catalog.py 的 FACTOR_PROVIDERS 注册新的 provider，接口无需改动。
"""
import threading
import time
import uuid

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
from .. import config
from ..engine.task_manager import get_task_manager
from ..factors.single_test import FactorTestCancelled, run_single_factor_test

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
    if dataset.lower() not in ("mixed",) and dataset.lower() not in available and dataset.lower() != "alpha360":
        raise HTTPException(status_code=404, detail=f"未知特征集: {dataset}")
    return get_catalog(dataset)


# ---------- 单因子测试（不训练模型，快速诊断因子预测力） ----------

class SingleFactorTestFactor(BaseModel):
    id: str = ""            # 前端标识（自定义公式 id 或 因子名）
    name: str = ""
    expression: str = ""
    source: str = "custom"  # custom / alpha158 / alpha360


class SingleFactorTestRequest(BaseModel):
    universe: str = "csi300"
    start_date: str = ""
    end_date: str = ""
    label_horizon: int = 2   # 未来 N 日收益作为预测目标
    factors: list[SingleFactorTestFactor] = []
    # 触发组剔除开关（默认全开，保持原行为 + 新增成交日口径）：
    exclude_limit_up_signal: bool = True  # 剔除信号日（T）涨停（选股过滤，无前视）
    exclude_limit_up_trade: bool = True   # 剔除成交日（T+1）涨停（调仓日封板买不到，与回测一致）
    exclude_suspended: bool = True        # 剔除成交日（T+1）停牌/无行情（同样买不到）
    price_adjust: str = "forward"         # 复权方式：none/forward/backward（与回测对齐，默认前复权）
    freeze_suspended_price: bool = False  # 停牌日价格冻结计入未来收益（对齐聚宽口径 B）


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
    """内存治理：已完成任务只保留最近 _SFT_RESULT_KEEP 条的完整 result，更早的置 None。"""
    with _SFT_LOCK:
        finished = sorted(
            (k for k, v in _SFT_TASKS.items() if v.get("status") in ("success", "failed")),
            key=lambda k: _SFT_TASKS[k].get("ts", 0),
            reverse=True,
        )
        for k in finished[_SFT_RESULT_KEEP:]:
            if _SFT_TASKS[k].get("result") is not None:
                _SFT_TASKS[k]["result"] = None


def _sft_get(task_id: str):
    with _SFT_LOCK:
        return _SFT_TASKS.get(task_id)


@router.post("/single-factor-test", summary="单因子测试（不训练模型，异步提交）")
def single_factor_test(req: SingleFactorTestRequest):
    """提交单因子测试任务，后台线程逐个因子快速诊断，返回 task_id。

    完成后通过 GET /factors/single-factor-test/progress/{task_id} 轮询进度并获取结果。
    """
    if not req.factors:
        raise HTTPException(status_code=400, detail="请至少勾选一个因子")
    if not req.start_date or not req.end_date:
        raise HTTPException(status_code=400, detail="请填写测试区间")

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
    _sft_store(task_id, state)

    def _run() -> None:
        def _on_progress(p: float, m: str) -> None:
            # 用户已点取消：抛异常终止任务（progress_cb 在每批特征/每个因子计算间隙被调用）
            if state.get("cancel_requested"):
                raise FactorTestCancelled()
            if state.get("status") == "running":
                state.update(progress=float(p), message=m, ts=time.time())

        # 与回测共用并发配额：排队等待（每秒检查一次取消，排队中可随时取消）
        manager = get_task_manager(config.WORK_DIR)
        # 进入排队状态（计入并发统计的 queued）
        manager.register_external_queued(task_id)
        acquired = False
        try:
            while not manager.try_acquire_slot(task_id):
                if state.get("cancel_requested"):
                    state.update(status="cancelled", progress=100.0, message="已取消", ts=time.time())
                    return
                state.update(message="排队等待执行（共用回测并发配额）...", ts=time.time())
                time.sleep(1)
            acquired = True
            manager.unregister_external_queued(task_id)

            if state.get("cancel_requested"):
                state.update(status="cancelled", progress=100.0, message="已取消", ts=time.time())
                return
            state.update(message="开始执行...", ts=time.time())
            try:
                items = run_single_factor_test(
                    universe=req.universe,
                    start_date=req.start_date,
                    end_date=req.end_date,
                    label_horizon=req.label_horizon,
                    factors=[f.model_dump() for f in req.factors],
                    progress_cb=_on_progress,
                    cancelled=lambda: bool(state.get("cancel_requested")),
                    exclude_limit_up_signal=req.exclude_limit_up_signal,
                    exclude_limit_up_trade=req.exclude_limit_up_trade,
                    exclude_suspended=req.exclude_suspended,
                    price_adjust=req.price_adjust,
                    freeze_suspended_price=req.freeze_suspended_price,
                )
                state.update(
                    status="success",
                    progress=100.0,
                    message="完成",
                    result={"items": items, "total": len(items)},
                    ts=time.time(),
                )
            except FactorTestCancelled:
                state.update(status="cancelled", progress=100.0, message="已取消", ts=time.time())
            except Exception as e:
                state.update(status="failed", progress=100.0, message=f"单因子测试失败: {e}", error=str(e), ts=time.time())
        finally:
            manager.unregister_external_queued(task_id)
            if acquired:
                manager.release_slot(task_id)
            # 内存治理：任务结束立即释放超龄任务的完整结果
            _sft_trim_results()

    threading.Thread(target=_run, daemon=True).start()
    return {"task_id": task_id}


@router.get("/single-factor-test/progress/{task_id}", summary="查询单因子测试任务进度")
def single_factor_test_progress(task_id: str):
    """轮询单因子测试任务：status running/success/failed/cancelled，progress 0-100，success 时附带 result。"""
    state = _sft_get(task_id)
    if state is None:
        raise HTTPException(status_code=404, detail="任务不存在或已过期")
    return {
        "task_id": task_id,
        "status": state["status"],
        "progress": state["progress"],
        "message": state["message"],
        "result": state.get("result"),
        "error": state.get("error"),
    }


@router.post("/single-factor-test/cancel/{task_id}", summary="取消单因子测试任务")
def single_factor_test_cancel(task_id: str):
    """请求取消正在运行的单因子测试任务：设置取消标记，后台线程在下一个进度点终止。

    返回 {ok, message}；任务已结束（success/failed/cancelled）时 ok=False 且不改变状态。
    """
    state = _sft_get(task_id)
    if state is None:
        raise HTTPException(status_code=404, detail="任务不存在或已过期")
    if state.get("status") != "running":
        return {"ok": False, "message": f"任务已{state.get('message', '结束')}，无需取消"}
    state["cancel_requested"] = True
    return {"ok": True, "message": "已请求取消，正在终止..."}


@router.get("/single-factor-test/tasks", summary="列出最近的单因子测试任务")
def single_factor_test_tasks(limit: int = 20):
    """列出最近提交的单因子测试任务（含 running，按时间倒序）。

    用于前端刷新页面后恢复"未取消完成"的任务：找到 running 任务即可重新轮询进度/继续取消。
    """
    with _SFT_LOCK:
        tasks = sorted(_SFT_TASKS.values(), key=lambda v: v.get("ts", 0), reverse=True)
    return {
        "tasks": [
            {
                "task_id": t["task_id"],
                "status": t["status"],
                "progress": t["progress"],
                "message": t["message"],
                "ts": t.get("ts", 0),
                "cancel_requested": t.get("cancel_requested", False),
            }
            for t in tasks[: max(1, min(int(limit or 20), 100))]
        ]
    }
