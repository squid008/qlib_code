# -*- coding: utf-8 -*-
"""
回测相关 API 路由。

业务逻辑已抽到 services/artifacts_service.py，本层只负责：
- 参数校验
- 调用 service 层
- 将 service 异常映射为 HTTP 状态码
"""
from __future__ import annotations

import os

from fastapi import APIRouter, HTTPException

from ..engine.task_manager import get_task_manager
from ..models.backtest import BacktestRequest, BacktestTask, TaskIdResponse
from .. import config
from ..services import artifacts_service

router = APIRouter(prefix="/api", tags=["backtest"])


def _map_artifact_error(e: artifacts_service.ArtifactNotFoundError):
    return HTTPException(status_code=404, detail=str(e))


@router.get("/backtest/capacity", summary="查询并发回测能力与资源摘要（前端超限提示）")
def get_backtest_capacity():
    """返回当前机器可支持的并发回测上限、运行/排队数、硬件资源摘要。

    前端提交回测前可调用，若 available == 0 则提示"已达并发上限，请等待"。
    """
    manager = get_task_manager(config.WORK_DIR)
    return manager.concurrency_info()


@router.post("/backtest", response_model=TaskIdResponse, summary="提交回测任务")
def create_backtest(req: BacktestRequest):
    manager = get_task_manager(config.WORK_DIR)
    task_id = manager.submit(req)
    return TaskIdResponse(task_id=task_id)


@router.post("/backtest/{task_id}/resume", response_model=TaskIdResponse, summary="断点续跑（未完成的滚动回测）")
def resume_backtest(task_id: str):
    """从源任务的断点继续滚动回测。

    读源任务 params.json 构造续跑请求，复用源 artifacts 目录
    （_run_rolling 会检测已完成的段并跳过），提交一个新任务继续跑未完成的部分。
    """
    try:
        snap = artifacts_service.load_snapshot(task_id)
    except artifacts_service.ArtifactNotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e))
    params = snap.get("params")
    if not params:
        raise HTTPException(status_code=404, detail=f"任务 {task_id} 没有参数快照，无法续跑")
    try:
        # 老任务（v1.5.0 之前 / 参数里无复权方式）的特征是用【数据原生 $close（后复权价）】
        # 训练的。2026-09-02 修正复权语义后（见 engine/adjust.py docstring）：
        #   - none     = 真实价 $close/$factor（东财"不复权"口径）
        #   - backward = $close 原生后复权价（= 老任务特征口径）
        #   - forward  = 前复权价（比率特征与 backward 等价）
        # 因此老任务续跑统一映射为 backward，保证与旧模型权重特征一致。
        if "price_adjust" not in params or not params.get("price_adjust"):
            params = {**params, "price_adjust": "backward"}
        req = BacktestRequest(**params)
    except Exception as e:
        raise HTTPException(status_code=422, detail=f"源任务参数解析失败，无法续跑: {e}")
    # 续跑：复用源 artifacts 目录（段结果缓存负责跳过已完成段），未完成段正常训练
    req.resume_task_id = task_id
    req.load_model_task_id = None
    manager = get_task_manager(config.WORK_DIR)
    new_task_id = manager.submit(req)
    # 续测任务沿用源任务的可读名（目录名），避免任务状态区显示裸 task_id
    # （续测复用源目录，按新 task_id 查 find_artifact_dir 找不到，因此直接沿用）
    try:
        adir = artifacts_service.find_artifact_dir(task_id)
        if adir:
            manager.set_display_name(new_task_id, os.path.basename(adir))
    except Exception:
        pass
    return TaskIdResponse(task_id=new_task_id)


@router.get("/backtest/{task_id}", response_model=BacktestTask, summary="查询回测任务状态")
def get_backtest(task_id: str):
    manager = get_task_manager(config.WORK_DIR)
    task = manager.get(task_id)
    if task is None:
        raise HTTPException(status_code=404, detail=f"任务 {task_id} 不存在")
    # 附加可读名称（来自 artifacts 目录名），方便前端区分任务（如取消时识别）
    if not task.display_name:
        try:
            adir = artifacts_service.find_artifact_dir(task_id)
            if adir:
                task.display_name = os.path.basename(adir)
        except Exception:
            pass
    # 滚动训练运行中：附加中途 partial_result（已跑段的净值/分层/IC），前端可实时查看
    if task.status in ("running", "cancelling"):
        try:
            adir = artifacts_service.find_artifact_dir(task_id)
            if not adir:
                # 续测任务复用源目录（目录名后缀是源 task_id），按 resume_task_id 找源目录读 partial
                try:
                    req = manager.get_req(task_id)
                    if req and getattr(req, "resume_task_id", None):
                        adir = artifacts_service.find_artifact_dir(req.resume_task_id)
                except Exception:
                    adir = None
            if adir:
                import json
                ppath = os.path.join(adir, "partial_result.json")
                if os.path.exists(ppath):
                    with open(ppath, "r", encoding="utf-8") as f:
                        task.partial_result = json.load(f)
        except Exception:
            pass
    return task


@router.get("/backtest/{task_id}/artifacts", summary="查询任务的模型交付物（训练公式/权重/超参数/特征）")
def get_backtest_artifacts(task_id: str):
    """返回该回测任务训练得到的模型交付物，用于复现/审查。"""
    try:
        return artifacts_service.load_model_artifacts(task_id)
    except artifacts_service.ArtifactNotFoundError as e:
        raise _map_artifact_error(e)


@router.get("/backtest/{task_id}/snapshot", summary="查询任务的产物目录信息（含曲线/参数快照图、参数）")
def get_backtest_snapshot(task_id: str):
    """返回该回测任务的产物目录信息（曲线/参数快照图、参数、meta、段目录）。"""
    try:
        return artifacts_service.load_snapshot(task_id)
    except artifacts_service.ArtifactNotFoundError as e:
        raise _map_artifact_error(e)


@router.get("/backtest/{task_id}/result", summary="读取持久化的回测完整结果（指标/净值/调仓记录）")
def get_backtest_result(task_id: str):
    """从 artifacts 读取回测完整结果（重启后端后仍可查看历史回测的调仓记录/曲线）。"""
    try:
        return artifacts_service.load_result(task_id)
    except artifacts_service.ArtifactNotFoundError as e:
        raise _map_artifact_error(e)


@router.get("/backtest/{task_id}/partial", summary="读取滚动回测已跑段的部分结果（partial_result.json）")
def get_backtest_partial(task_id: str):
    """读取滚动回测的 partial_result.json（已跑段的净值/分层/IC 汇总）。

    任务未完成（取消/失败/中断/后端重启）但已跑过若干段时，磁盘上仍有该文件，
    前端据此展示已跑段的结果曲线（而不是只显示"无回测记录"）。
    """
    import json
    try:
        adir = artifacts_service.find_artifact_dir(task_id)
    except artifacts_service.ArtifactNotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e))
    if not adir:
        raise HTTPException(status_code=404, detail=f"任务 {task_id} 没有产物目录")
    ppath = os.path.join(adir, "partial_result.json")
    if not os.path.exists(ppath):
        raise HTTPException(status_code=404, detail=f"任务 {task_id} 没有已跑段的部分结果（partial_result.json）")
    try:
        with open(ppath, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"读取部分结果失败: {e}")


@router.get("/backtest/{task_id}/image/{name}", summary="获取产物图片（曲线/参数快照）")
def get_backtest_image(task_id: str, name: str):
    """返回产物目录中的图片文件（如 nav_curve.png / params_snapshot.png）。"""
    from fastapi.responses import FileResponse
    try:
        fpath = artifacts_service.resolve_image_path(task_id, name)
    except artifacts_service.ArtifactNotFoundError as e:
        raise _map_artifact_error(e)
    return FileResponse(fpath, media_type="image/png")


@router.get("/backtests", response_model=dict, summary="列出所有回测任务")
def list_backtests():
    manager = get_task_manager(config.WORK_DIR)
    return manager.list()


@router.get("/backtests/history", summary="从 artifacts 目录扫描所有回测产物（跨重启/跨版本）")
def list_backtests_history():
    """扫描 artifacts 目录下所有回测产物，作为历史回测列表返回。"""
    return artifacts_service.scan_history()


@router.post("/backtest/{task_id}/cancel", summary="停止回测任务")
def cancel_backtest(task_id: str):
    manager = get_task_manager(config.WORK_DIR)
    ok = manager.cancel(task_id)
    if not ok:
        raise HTTPException(status_code=400, detail=f"任务 {task_id} 无法停止（不存在或已结束）")
    return {"status": "cancelling", "task_id": task_id}


@router.delete("/backtest/{task_id}", summary="删除某个回测的产物目录")
def delete_backtest(task_id: str):
    """删除 artifacts 下对应的回测产物目录（含参数/结果/图片/模型等）。

    运行中的任务禁止删除（避免删除正在使用的产物目录导致回测异常）。
    """
    manager = get_task_manager(config.WORK_DIR)
    task = manager.get(task_id)
    if task is not None and task.status in ("running", "pending", "cancelling"):
        raise HTTPException(
            status_code=409,
            detail=f"任务 {task_id} 正在运行（{task.status}），请等待其完成、失败或取消后再删除",
        )
    try:
        dir_name = artifacts_service.delete_artifacts(task_id)
    except artifacts_service.ArtifactNotFoundError as e:
        raise _map_artifact_error(e)
    return {"status": "deleted", "task_id": task_id, "dir": dir_name}
