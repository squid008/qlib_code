# -*- coding: utf-8 -*-
"""
回测相关 API 路由。
"""
from __future__ import annotations

from fastapi import APIRouter, HTTPException

from ..engine.task_manager import get_task_manager
from ..models.backtest import BacktestRequest, BacktestTask, TaskIdResponse
from .. import config

router = APIRouter(prefix="/api", tags=["backtest"])


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
    """返回该回测任务训练得到的模型交付物，用于复现/审查。
    若为滚动训练，返回 { "segments": [每段的交付物] }；否则返回单段交付物。
    """
    import json
    import glob
    import os

    base = _find_artifact_dir(task_id)
    if base is None:
        raise HTTPException(status_code=404, detail=f"任务 {task_id} 没有可用的模型交付物")

    # 滚动训练：存在 segment_XX 子目录
    seg_dirs = sorted(glob.glob(os.path.join(base, "segment_*")))
    if seg_dirs:
        segments = []
        for sd in seg_dirs:
            af = os.path.join(sd, "model_artifacts.json")
            if not os.path.exists(af):
                continue
            try:
                with open(af, "r", encoding="utf-8") as f:
                    data = json.load(f)
                mt = os.path.join(sd, "model.txt")
                if os.path.exists(mt) and not data.get("model_file"):
                    with open(mt, "r", encoding="utf-8", errors="ignore") as f:
                        data["model_file"] = f.read()
                segments.append(data)
            except Exception:
                continue
        return {"segments": segments}

    # single 模式：直接读主目录
    artifact_file = os.path.join(base, "model_artifacts.json")
    if not os.path.exists(artifact_file):
        raise HTTPException(status_code=404, detail=f"任务 {task_id} 没有可用的模型交付物")
    try:
        with open(artifact_file, "r", encoding="utf-8") as f:
            data = json.load(f)
        model_txt = os.path.join(base, "model.txt")
        if os.path.exists(model_txt) and not data.get("model_file"):
            with open(model_txt, "r", encoding="utf-8", errors="ignore") as f:
                data["model_file"] = f.read()
        return data
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"读取交付物失败: {e}")


@router.get("/backtest/{task_id}/snapshot", summary="查询任务的产物目录信息（含曲线/参数快照图、参数）")
def get_backtest_snapshot(task_id: str):
    """返回该回测任务的产物目录信息：
    - dir_name: 可读目录名（日期+模型+股票池+年份+ID）
    - params: 完整回测参数（用于复现模式一键填充）
    - meta: 人工可读参数快照
    - images: {nav_curve, params_snapshot} 图片文件相对路径
    - segments: 滚动段目录列表
    """
    import json
    import glob
    import os

    base = _find_artifact_dir(task_id)
    if base is None:
        raise HTTPException(status_code=404, detail=f"任务 {task_id} 没有产物目录")

    info = {
        "task_id": task_id,
        "dir_name": os.path.basename(base),
        "params": None,
        "meta": None,
        "images": {},
        "segments": [os.path.basename(d) for d in sorted(glob.glob(os.path.join(base, "segment_*")))],
    }
    # params.json（完整可复现参数）
    pfile = os.path.join(base, "params.json")
    if os.path.exists(pfile):
        try:
            with open(pfile, "r", encoding="utf-8") as f:
                info["params"] = json.load(f)
        except Exception:
            pass
    # meta.json（人工可读）
    mfile = os.path.join(base, "meta.json")
    if os.path.exists(mfile):
        try:
            with open(mfile, "r", encoding="utf-8") as f:
                info["meta"] = json.load(f)
        except Exception:
            pass
    # 图片
    for name in ["nav_curve.png", "params_snapshot.png"]:
        if os.path.exists(os.path.join(base, name)):
            info["images"][name] = name
    return info


def _find_artifact_dir(task_id: str) -> str:
    """根据 task_id 找到对应的产物目录（新目录名以 *_task_id 结尾；兼容旧版直接用 task_id 命名）。"""
    import glob
    import os
    artifacts_dir = os.path.join(config.WORK_DIR, "artifacts")
    matches = glob.glob(os.path.join(artifacts_dir, "*_" + task_id))
    if matches:
        return matches[0]
    # 兼容旧版：目录名直接就是 task_id
    old = os.path.join(artifacts_dir, task_id)
    return old if os.path.isdir(old) else None


@router.get("/backtest/{task_id}/result", summary="读取持久化的回测完整结果（指标/净值/调仓记录）")
def get_backtest_result(task_id: str):
    """从 artifacts 读取回测完整结果（重启后端后仍可查看历史回测的调仓记录/曲线）。"""
    import json
    import os

    base = _find_artifact_dir(task_id)
    if base is None:
        raise HTTPException(status_code=404, detail=f"任务 {task_id} 没有产物目录")
    rfile = os.path.join(base, "result.json")
    if not os.path.exists(rfile):
        raise HTTPException(status_code=404, detail=f"任务 {task_id} 没有持久化的结果")
    try:
        with open(rfile, "r", encoding="utf-8") as f:
            data = json.load(f)
        # 清理历史文件可能遗留的 NaN/Infinity，避免序列化失败
        from ..engine.qlib_engine import _sanitize_json
        return _sanitize_json(data)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"读取结果失败: {e}")


@router.get("/backtest/{task_id}/image/{name}", summary="获取产物图片（曲线/参数快照）")
def get_backtest_image(task_id: str, name: str):
    """返回产物目录中的图片文件（如 nav_curve.png / params_snapshot.png）。"""
    import os
    from fastapi.responses import FileResponse

    base = _find_artifact_dir(task_id)
    if base is None:
        raise HTTPException(status_code=404, detail=f"任务 {task_id} 没有产物目录")
    # 防目录穿越
    safe = os.path.basename(name)
    fpath = os.path.join(base, safe)
    if not os.path.exists(fpath):
        raise HTTPException(status_code=404, detail=f"图片 {safe} 不存在")
    return FileResponse(fpath, media_type="image/png")


@router.get("/backtests", response_model=dict, summary="列出所有回测任务")
def list_backtests():
    manager = get_task_manager(config.WORK_DIR)
    return manager.list()


@router.get("/backtests/history", summary="从 artifacts 目录扫描所有回测产物（跨重启/跨版本）")
def list_backtests_history():
    """扫描 backend/workdir/artifacts/ 下所有子目录，作为历史回测列表返回。

    用途：与 /api/backtests（内存任务）互补。后者只包含本进程内存中跑过的任务，
    重启后端或换版本后清空；本接口直接从磁盘目录扫描，能看到旧版本/历史回测/复制目录。

    每条记录：
      - task_id: 优先从 params.json 中读（最权威），否则从目录名末尾解析
      - dir_name: 目录名（人类可读，含日期/模型/股票池/年份/ID）
      - has_params: 是否有 params.json（决定"复用参数/复用回测"是否可用）
      - has_result: 是否有 result.json（决定"查看"按钮是否可用）
      - has_meta: 是否有 meta.json
      - images: 存在的图片文件
      - segments: 滚动段子目录列表
      - meta: 摘要（股票池/资金/起止日期等，从目录名/meta.json 提取）
    """
    import os
    import json
    import glob
    import re

    artifacts_dir = os.path.join(config.WORK_DIR, "artifacts")
    if not os.path.isdir(artifacts_dir):
        return {"items": []}

    items = []
    # 目录命名规则：{ts}_{model}_{universe}_{start_y}_{end_y}_{task_id}（最后一段是 task_id）
    # 但用户可能复制/改名，所以优先读 params.json 里的 task_id 字段
    for name in sorted(os.listdir(artifacts_dir), reverse=True):
        full = os.path.join(artifacts_dir, name)
        if not os.path.isdir(full):
            continue

        # 决定 task_id：优先从 params.json 读；否则用目录名最后一段
        params_file = os.path.join(full, "params.json")
        result_file = os.path.join(full, "result.json")
        meta_file = os.path.join(full, "meta.json")

        task_id = None
        params = None
        if os.path.exists(params_file):
            try:
                with open(params_file, "r", encoding="utf-8") as f:
                    params = json.load(f)
                # params 字典里没显式 task_id；可从 meta.json 读，或目录名末尾
            except Exception:
                params = None
        if os.path.exists(meta_file):
            try:
                with open(meta_file, "r", encoding="utf-8") as f:
                    meta = json.load(f)
                # meta 里通常没有 task_id，跳过
            except Exception:
                meta = None
        else:
            meta = None

        # 从目录名末尾解析 task_id（命名规则最后一段）
        parts = name.rsplit("_", 1)
        if len(parts) == 2 and re.match(r"^[0-9a-f]{12,}$", parts[1]):
            task_id = parts[1]
        else:
            # 目录名不规范（如用户复制改名），用目录名本身作为标识
            task_id = name

        # 图片与段目录
        images = {}
        for img in ("nav_curve.png", "params_snapshot.png", "summary.png"):
            if os.path.exists(os.path.join(full, img)):
                images[img] = img
        segments = sorted(
            os.path.basename(d) for d in glob.glob(os.path.join(full, "segment_*"))
        )

        # 从目录名解析股票池/资金/起止（用于表格展示）
        # 命名：{ts}_{model}_{universe}_{start_y}_{end_y}_{task_id}
        meta_summary = {}
        segs = name.split("_")
        # 段数：{ts(2段日期+时间)} {model} {universe} {start_y} {end_y} {task_id} → 至少 6 段
        if len(segs) >= 6:
            meta_summary = {
                "model": segs[1],
                "universe": segs[2],
                "start_year": segs[3],
                "end_year": segs[4],
            }
        elif meta and isinstance(meta, dict):
            meta_summary = {
                "model": meta.get("模型"),
                "universe": meta.get("股票池"),
                "start_year": (meta.get("起始日期") or "")[:4],
                "end_year": (meta.get("结束日期") or "")[:4],
            }

        items.append({
            "task_id": task_id,
            "dir_name": name,
            "has_params": os.path.exists(params_file),
            "has_result": os.path.exists(result_file),
            "has_meta": os.path.exists(meta_file),
            "images": images,
            "segments": segments,
            "meta_summary": meta_summary,
        })

    return {"items": items}


@router.post("/backtest/{task_id}/cancel", summary="停止回测任务")
def cancel_backtest(task_id: str):
    manager = get_task_manager(config.WORK_DIR)
    ok = manager.cancel(task_id)
    if not ok:
        raise HTTPException(status_code=400, detail=f"任务 {task_id} 无法停止（不存在或已结束）")
    return {"status": "cancelling", "task_id": task_id}
