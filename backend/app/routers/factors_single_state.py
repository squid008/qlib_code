# -*- coding: utf-8 -*-
"""单因子测试任务的**运行态初始结构**（★ v2.11.4 从 `routers/factors_single.py` 抽出）。

## 为什么单独一块
它是"前端读侧"和"后台写侧"之间的**唯一契约** ✓ —— 后端往这些键里写，前端按这些键渲染 ✓。
把它单独写在这儿，等于把契约**显式化** ✓：改键名时两边都得看这一份 ✓，
而不是散在 325 行的路由函数中间 ✓（原来就是这样 ✗，找起来只能全文搜）。

## 为什么要结构化解耦（源码原有注释，一并搬过来 ✓）
多线程都会写 `message` / `progress` ✓ ⇒ 直接抢写会让文案**来回跳** ✓
（用户看到"解析股票池"闪回"已完成" ✗）。所以读侧**合成** message/progress，
写侧只更新分项字段 ✓：

| 键 | 含义 |
|---|---|
| `running_h` / `queued_h` | 正在统计运行 / 排队等并发单元的预测周期 ✓ |
| `per_h_prog` / `per_h_msg` | 各周期统计内部进度 0-100 ✓ / 内部最新进度文案 ✓ |
| `done_n` | 已完成统计的周期数 ✓ |
| `load_prog` / `load_msg` | **共享**特征加载阶段整体进度 0-30 ✓ / 文案 ✓（读侧映射：加载 0-30 → 统计 30-100 ✓） |
| `extra_prog` / `extra_msg` | ⭐ 统计**之后**的附加阶段（见下）✓ |

## ⭐ `extra_*` 为什么必须有（v2.4.2，用户 2026-10-15 截图反馈 ✓）
统计之后还有一段「**每期全仓滚动**（实盘口径，逐点对拍）」✓ ——
它要**先取全池行情面板**再逐档逐日回测（全 A 约 **10~20 秒/档** ✗），
而统计一完成进度就已经是 **100%** ✗ ⇒ 进度条**顶在 100% 干等** ✓
（单周期时 `message` 还是空串 ✗ ⇒ 界面只剩"进度 100%"✓，用户以为卡死 ✓）。
⇒ 统计阶段**封顶 95**、尾巴 **95→99.5** 留给这一阶段，并把文案实时报出来 ✓。
"""
from __future__ import annotations

import time
from typing import List


def new_task_state(task_id: str, horizons: List[int], parallel: bool) -> dict:
    """新建任务运行态（结构见模块 docstring ✓）。"""
    return {
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
        "extra_prog": 0.0,
        "extra_msg": "",
    }
