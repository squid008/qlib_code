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
from ..factors.single_test import FactorTestCancelled, _bad_date_arg, run_single_factor_tests
from ..factors.event_study import run_event_study

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
    source_formula: str = ""  # 用户原文公式（custom=保存的原文；目录因子=表达式本身），仅随结果回传用于展示


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
    price_adjust: str = "forward"         # 复权方式：none/forward/backward（与回测对齐，默认前复权）
    freeze_suspended_price: bool = True   # 停牌日价格冻结计入未来收益（对齐聚宽口径 B）
    suspend_remove: bool = True           # 信号停牌行语义：True=SR删行(益盟/回测一致)；False=NaN占位(聚宽口径)
    price_round: bool = True              # 真实价按分取整参与因子计算（仅不复权生效，默认开；与益盟/聚宽对齐）
    warmup_days: Optional[int] = None     # 特征加载额外预热缓冲（交易日，v1.18.6）：None=按
    #   QLIB_SFT_WARMUP_DAYS（默认 250 ≈1 年）；0=关闭。长回看/动态窗口公式（DYN_*/
    #   BARSCOUNT/HHVBARS+Ref 嵌套）扩展天数无法静态推断，预热后区间首日即有收敛值；
    #   出口仍裁剪回 [start_date, ...]，固定窗口公式结果不变。
    # ---- 连续因子「分位 / 持仓期收益曲线」参数（v1.18.45 起由 API 传入，不再硬编码）----
    quantiles: int = 10                   # 分位组数（默认 10=十分位；5=旧五分位，保留兼容）
    rebalance_period: int = 5             # TopK 持仓期曲线的调仓期（交易日）
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
        rebalance_period = int(5 if req.rebalance_period is None else req.rebalance_period)
    except (TypeError, ValueError):
        raise HTTPException(status_code=400, detail=f"调仓期必须为整数：{req.rebalance_period!r}")
    if not (1 <= rebalance_period <= 250):
        raise HTTPException(status_code=400, detail=f"调仓期需在 1~250 个交易日之间：{rebalance_period}")
    topk_list: List[float] = []
    if req.topk_list:
        if len(req.topk_list) > 8:
            raise HTTPException(status_code=400, detail=f"明细曲线最多点选 8 个 K：{len(req.topk_list)}")
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

                res = run_single_factor_tests(
                    label_horizons=horizons,
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
                )
                with lock:
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
            n_factor = len(req.factors)
            for fi in range(n_factor):
                for h in horizons:
                    rows = per_h.get(h)
                    if rows is None or fi >= len(rows):
                        continue
                    row = dict(rows[fi])
                    row["horizon"] = h
                    items.append(row)
            state.update(
                status="success",
                progress=100.0,
                message=f"完成（{n_h} 个预测周期）" if n_h > 1 else "完成",
                result={"items": items, "total": len(items)},
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
    price_adjust: str = "forward"
    price_round: bool = True
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
                state.update(status="success", progress=100.0, message="事件研究完成",
                             result=_json_safe(res), ts=time.time())
        except FactorTestCancelled:
            state.update(status="cancelled", progress=100.0, message="已取消", ts=time.time())
        except Exception as e:  # noqa: BLE001
            state.update(status="failed", progress=100.0, message=f"事件研究失败: {e}",
                         error=str(e), ts=time.time())
        finally:
            _est_trim()

    threading.Thread(target=_run, daemon=True).start()
    return {"task_id": task_id}


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
