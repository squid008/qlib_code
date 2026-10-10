# -*- coding: utf-8 -*-
"""数据集切换接口（用户 2026-10-09：三套数据来回切、前端做对照 ✓）。

- `GET  /api/datasets`        ⇒ 可选项 + 当前生效 + 每套的体检（日历范围/股票数/字段数/缺哪些字段 ✓）
- `POST /api/datasets/active` ⇒ 切换（写盘 ✓ → 重新 init qlib ✓ → 清进程级缓存 ✓）
"""
from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from ..logger import get_logger
from ..services import datasets

logger = get_logger(__name__)
router = APIRouter(prefix="/api/datasets", tags=["datasets"])


class SwitchRequest(BaseModel):
    name: str = Field(..., description="数据集名：cn_data / cn_data2 / cn_data3")
    allow_while_running: bool = Field(
        False, description="有任务在跑时是否强制切换（默认拒绝 ✓）")


@router.get("", summary="列出可用数据集与当前生效项")
def list_datasets(refresh: bool = False):
    """`?refresh=1` ⇒ **绕过体检缓存**立即重算 ✓（后台作业补完 fin_*/chip_* 后想立刻看到 ✓）。"""
    try:
        return datasets.status(refresh=refresh)
    except Exception as e:                                                # noqa: BLE001
        logger.exception("列出数据集失败")
        raise HTTPException(status_code=500, detail="列出数据集失败：%s" % e) from e


@router.post("/active", summary="切换当前数据集（全局生效 ✓）")
def switch_dataset(req: SwitchRequest):
    try:
        info = datasets.activate(req.name, allow_while_running=req.allow_while_running)
    except FileNotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e)) from e
    except RuntimeError as e:                                             # 有任务在跑 ⇒ 让 UI 确认 ✓
        raise HTTPException(status_code=409, detail=str(e)) from e
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    except Exception as e:                                                # noqa: BLE001
        logger.exception("切换数据集失败")
        raise HTTPException(status_code=500, detail="切换数据集失败：%s" % e) from e
    logger.info("数据集已切换到 %s（清理缓存 %s）", info.get("name"), info.get("cleared"))
    return {"active": info.get("name"), "info": info}
