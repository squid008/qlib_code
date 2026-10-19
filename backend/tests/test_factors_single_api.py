# -*- coding: utf-8 -*-
"""`routers/factors_single.py` 的**校验/边界路径**（★ 2026-10-19 ✓ 覆盖率补强）。

## 为什么只测"非法的/边界的"请求 ✓

这个路由的 `/single-factor-test` 一旦收到**合法**请求就会**提交异步任务** ✗（要跑面板求值 ✓
几秒到几分钟 ✓）⇒ 单元测试里**绝不能**走那条路 ✗。
但真正容易出错的是**边界** ✓：
  · 任务不存在时 `/progress` / `/cancel` 会不会 500 ✗（前端据此提示"结果已清理"✓）；
  · `/tasks?limit=0` / `limit=-1` 会不会炸 ✗；
  · 空 `factors` / 非法日期 ⇒ 必须 4xx ✓（而不是"接口 200、前端拿到个空结果"✗）。

⇒ 这些路径**不需要任何行情数据** ✓，跑得飞快 ✓，而且正是"用户输入错"的高频场景 ✓。
"""
import pytest
from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)


def test_progress_unknown_task_is_graceful():
    r = client.get("/api/factors/single-factor-test/progress/definitely-not-exist")
    assert r.status_code < 500, "未知任务不许 500 ✗：%s" % r.text[:200]
    body = r.json()
    # 前端靠这个字段决定显示"结果已清理"还是"进度" ✓
    assert any(k in body for k in ("status", "detail", "task_id")), body


def test_cancel_unknown_task_is_graceful():
    r = client.post("/api/factors/single-factor-test/cancel/definitely-not-exist")
    assert r.status_code < 500, "取消未知任务不许 500 ✗：%s" % r.text[:200]


def test_clear_is_idempotent():
    first = client.post("/api/factors/single-factor-test/clear")
    second = client.post("/api/factors/single-factor-test/clear")
    assert first.status_code == 200, first.text[:200]
    assert second.status_code == 200, "重复清理必须幂等 ✓：%s" % second.text[:200]


@pytest.mark.parametrize("limit", [0, -1, 1, 500])
def test_tasks_list_accepts_any_limit(limit):
    """⚠ `limit` 越界（0 / 负数 / 很大）都不许 500 ✓ —— 它是 URL 里的参数，用户随手就改 ✓。"""
    r = client.get("/api/factors/single-factor-test/tasks", params={"limit": limit})
    assert r.status_code == 200, "limit=%s 时 %d：%s" % (limit, r.status_code, r.text[:200])
    body = r.json()
    assert isinstance(body, (list, dict)), body


@pytest.mark.parametrize("payload,tag", [
    ({"universe": "csi300", "start_date": "2024-01-01", "end_date": "2024-03-01", "factors": []},
     "空 factors"),
    ({"universe": "csi300", "start_date": "not-a-date", "end_date": "2024-03-01",
      "factors": [{"id": "f", "name": "f", "expression": "close", "source": "custom"}]},
     "非法开始日期"),
    ({"universe": "csi300", "start_date": "2024-01-01", "end_date": "bad",
      "factors": [{"id": "f", "name": "f", "expression": "close", "source": "custom"}]},
     "非法结束日期"),
])
def test_submit_rejects_bad_payload(payload, tag):
    """非法请求必须 **4xx** ✓ —— 不能"200 但结果为空"✗（那种最难查 ✓）。"""
    r = client.post("/api/factors/single-factor-test", json=payload)
    assert 400 <= r.status_code < 500, (
        "%s 应返回 4xx ✓，实测 %d ✗：%s" % (tag, r.status_code, r.text[:200]))
