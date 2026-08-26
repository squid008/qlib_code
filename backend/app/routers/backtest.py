# -*- coding: utf-8 -*-
"""
回测相关 API 路由。

业务逻辑已抽到 services/artifacts_service.py，本层只负责：
- 参数校验
- 调用 service 层
- 将 service 异常映射为 HTTP 状态码
"""
from __future__ import annotations

from fastapi import APIRouter, HTTPException

from ..engine.task_manager import get_task_manager
from ..models.backtest import BacktestRequest, BacktestTask, TaskIdResponse
from .. import config
from ..services import artifacts_service

router = APIRouter(prefix="/api", tags=["backtest"])


def _map_artifact_error(e: artifacts_service.ArtifactNotFoundError):
    return HTTPException(status_code=404, detail=str(e))


@router.post("/backtest", response_model=TaskIdResponse, summary="提交回测任务")
def create_backtest(req: BacktestRequest):
    manager = get_task_manager(config.WORK_DIR)
    task_id = manager.submit(req)
    return TaskIdResponse(task_id=task_id)


@router.get("/backtest/{task_id}", response_model=BacktestTask, summary="查询回测任务状态")
def get_backtest(task_id: str):
    manager = get_task_manager(config.WORK_DIR)
    task = manager.get(task_id)
    if task is None:
        raise HTTPException(status_code=404, detail=f"任务 {task_id} 不存在")
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
    """删除 artifacts 下对应的回测产物目录（含参数/结果/图片/模型等）。"""
    try:
        dir_name = artifacts_service.delete_artifacts(task_id)
    except artifacts_service.ArtifactNotFoundError as e:
        raise _map_artifact_error(e)
    return {"status": "deleted", "task_id": task_id, "dir": dir_name}
