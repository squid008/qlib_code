# -*- coding: utf-8 -*-
"""
回测产物（artifacts）服务层。

职责：处理与 artifacts 目录相关的文件扫描 / JSON 解析 / 图片定位 / 删除等业务逻辑。
从 routers/backtest.py 抽取而来，保持路由层薄、只负责 HTTP 映射。

所有返回数据为纯 dict/list，由路由层负责转为 HTTPException。
"""
from __future__ import annotations

import glob
import json
import os
import re
import shutil
from typing import Optional

from .. import config
from ..logger import get_logger

logger = get_logger(__name__)


class ArtifactNotFoundError(Exception):
    """产物目录或文件不存在。"""


def artifacts_root() -> str:
    return os.path.join(config.WORK_DIR, "artifacts")


def find_artifact_dir(task_id: str) -> Optional[str]:
    """根据 task_id 找到产物目录（新目录名以 *_task_id 结尾；兼容旧版直接用 task_id 命名）。"""
    dirs = glob.glob(os.path.join(artifacts_root(), "*_" + task_id))
    if dirs:
        return dirs[0]
    old = os.path.join(artifacts_root(), task_id)
    return old if os.path.isdir(old) else None


def load_model_artifacts(task_id: str) -> dict:
    """返回该回测任务训练得到的模型交付物。滚动训练返回 {segments:[...]}；single 返回单段。"""
    base = find_artifact_dir(task_id)
    if base is None:
        raise ArtifactNotFoundError(f"任务 {task_id} 没有可用的模型交付物")

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
            except Exception as e:
                logger.warning("读取段模型交付物失败 %s: %s", sd, e)
                continue
        if not segments:
            raise ArtifactNotFoundError(f"任务 {task_id} 没有可用的模型交付物")
        return {"segments": segments}

    artifact_file = os.path.join(base, "model_artifacts.json")
    if not os.path.exists(artifact_file):
        raise ArtifactNotFoundError(f"任务 {task_id} 没有可用的模型交付物")
    try:
        with open(artifact_file, "r", encoding="utf-8") as f:
            data = json.load(f)
        model_txt = os.path.join(base, "model.txt")
        if os.path.exists(model_txt) and not data.get("model_file"):
            with open(model_txt, "r", encoding="utf-8", errors="ignore") as f:
                data["model_file"] = f.read()
        return data
    except Exception as e:
        logger.error("读取交付物失败 %s: %s", task_id, e)
        raise ArtifactNotFoundError(f"读取交付物失败: {e}")


def load_snapshot(task_id: str) -> dict:
    """返回该回测任务的产物目录信息（含曲线/参数快照图、参数、meta、段目录）。"""
    base = find_artifact_dir(task_id)
    if base is None:
        raise ArtifactNotFoundError(f"任务 {task_id} 没有产物目录")

    info = {
        "task_id": task_id,
        "dir_name": os.path.basename(base),
        "params": None,
        "meta": None,
        "images": {},
        "segments": [os.path.basename(d) for d in sorted(glob.glob(os.path.join(base, "segment_*")))],
    }
    pfile = os.path.join(base, "params.json")
    if os.path.exists(pfile):
        try:
            with open(pfile, "r", encoding="utf-8") as f:
                info["params"] = json.load(f)
        except Exception as e:
            logger.warning("读取 params.json 失败 %s: %s", task_id, e)
    mfile = os.path.join(base, "meta.json")
    if os.path.exists(mfile):
        try:
            with open(mfile, "r", encoding="utf-8") as f:
                info["meta"] = json.load(f)
        except Exception as e:
            logger.warning("读取 meta.json 失败 %s: %s", task_id, e)
    for name in ["nav_curve.png", "params_snapshot.png"]:
        if os.path.exists(os.path.join(base, name)):
            info["images"][name] = name
    return info


def load_result(task_id: str) -> dict:
    """读取持久化的回测完整结果（指标/净值/调仓记录），并清理 NaN/Infinity。"""
    base = find_artifact_dir(task_id)
    if base is None:
        raise ArtifactNotFoundError(f"任务 {task_id} 没有产物目录")
    rfile = os.path.join(base, "result.json")
    if not os.path.exists(rfile):
        raise ArtifactNotFoundError(f"任务 {task_id} 没有持久化的结果")
    try:
        with open(rfile, "r", encoding="utf-8") as f:
            data = json.load(f)
        from ..engine.qlib_engine import _sanitize_json
        return _sanitize_json(data)
    except Exception as e:
        logger.error("读取结果失败 %s: %s", task_id, e)
        raise ArtifactNotFoundError(f"读取结果失败: {e}")


def resolve_image_path(task_id: str, name: str) -> Optional[str]:
    """定位产物图片文件绝对路径（防目录穿越）。不存在返回 None。"""
    base = find_artifact_dir(task_id)
    if base is None:
        raise ArtifactNotFoundError(f"任务 {task_id} 没有产物目录")
    safe = os.path.basename(name)
    fpath = os.path.join(base, safe)
    if not os.path.exists(fpath):
        raise ArtifactNotFoundError(f"图片 {safe} 不存在")
    return fpath


def scan_history() -> dict:
    """扫描 artifacts 目录下所有回测产物，作为历史回测列表返回（跨重启/跨版本）。"""
    root = artifacts_root()
    if not os.path.isdir(root):
        return {"items": []}

    items = []
    for name in sorted(os.listdir(root), reverse=True):
        full = os.path.join(root, name)
        if not os.path.isdir(full):
            continue

        params_file = os.path.join(full, "params.json")
        result_file = os.path.join(full, "result.json")
        meta_file = os.path.join(full, "meta.json")

        params = None
        if os.path.exists(params_file):
            try:
                with open(params_file, "r", encoding="utf-8") as f:
                    params = json.load(f)
            except Exception as e:
                logger.warning("扫描历史时解析 params.json 失败 %s: %s", name, e)
                params = None
        meta = None
        if os.path.exists(meta_file):
            try:
                with open(meta_file, "r", encoding="utf-8") as f:
                    meta = json.load(f)
            except Exception as e:
                logger.warning("扫描历史时解析 meta.json 失败 %s: %s", name, e)
                meta = None

        parts = name.rsplit("_", 1)
        if len(parts) == 2 and re.match(r"^[0-9a-f]{12,}$", parts[1]):
            task_id = parts[1]
        else:
            task_id = name

        images = {}
        for img in ("nav_curve.png", "params_snapshot.png", "summary.png"):
            if os.path.exists(os.path.join(full, img)):
                images[img] = img
        segments = sorted(os.path.basename(d) for d in glob.glob(os.path.join(full, "segment_*")))

        meta_summary = {}
        segs = name.split("_")
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

        has_artifacts = os.path.exists(os.path.join(full, "model_artifacts.json")) or any(
            os.path.exists(os.path.join(full, sd, "model_artifacts.json"))
            for sd in segments
        )

        items.append({
            "task_id": task_id,
            "dir_name": name,
            "has_params": os.path.exists(params_file),
            "has_result": os.path.exists(result_file),
            "has_meta": os.path.exists(meta_file),
            "has_artifacts": has_artifacts,
            "images": images,
            "segments": segments,
            "meta_summary": meta_summary,
        })
    return {"items": items}


def delete_artifacts(task_id: str) -> Optional[str]:
    """删除某个回测的产物目录。返回被删除的目录名；目录不存在抛 ArtifactNotFoundError。"""
    base = find_artifact_dir(task_id)
    if base is None or not os.path.isdir(base):
        raise ArtifactNotFoundError(f"任务 {task_id} 产物目录不存在")
    # 防误删：只允许删除 artifacts 目录下的子目录
    artifacts_root_path = os.path.abspath(artifacts_root())
    target = os.path.abspath(base)
    if os.path.dirname(target) != artifacts_root_path:
        raise ArtifactNotFoundError("拒绝删除非产物目录")
    shutil.rmtree(target, ignore_errors=True)
    return os.path.basename(base)
