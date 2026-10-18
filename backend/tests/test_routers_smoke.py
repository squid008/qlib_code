# -*- coding: utf-8 -*-
"""路由层冒烟测试（★ P0-B′，2026-10-18 ✓）。

## 为什么要有它（覆盖率基线暴露的最大空洞 ✓）

2026-10-18 首次测覆盖率 = **53.54%**，而 **7 个文件是 0%** ✗，其中：

```
routers/signal_test.py  0%（418 语句）← 全仓最大空洞
routers/backtest.py     0%（158）
main.py                 0%（72）
routers/data.py         0%（62）
signals/models.py       0%（42）
engine/cost_curves.py   0%（41）
routers/datasets.py     0%（36）
```

⇒ 这些路由**一行测试都没有** ✗ ⇒ 改 URL、改参数名、改错误码，全都没人拦 ✓
（而前端完全依赖这些契约 ✗）。本文件只做**冒烟级**断言 ✓：

  · **路由存在**（`app.openapi()` 里必须有这些路径 ✓）—— 防止"重构拆 router 把 URL 弄丢"✗；
  · **能返回**（200 ✓）且**结构对**（关键字段在 ✓）；
  · **坏输入要 4xx 而不是 500** ✓（这条最值钱：500 等于"我们崩了"✗）；
  · **不碰需要行情数据的路径** ✓（那些标 `datareq`，CI 无数据必跳过 ✗ ⇒ 等于没测）。

⚠ 刻意**不测**：`/signal-test/run`、`/factors/single-factor-test` 等**重计算**端点 ✗
  （跑一次要几十秒且依赖数据 ⇒ 属于集成测试 ✓，不放在冒烟里 ✓）。
"""
import pytest
from fastapi.testclient import TestClient

from app.main import app

#: 这些路径**必须存在** ✓（拆 router / 改前缀时最先被打破 ✗）
REQUIRED_PATHS = (
    "/api/version",
    "/api/backtest/capacity",
    "/api/backtest/{task_id}",
    "/api/data-sources",
    "/api/datasets",
    "/api/factors/custom-formulas",
    "/api/factors/operators",
    "/api/factors/translate",
    "/api/factors/datasets",
)


@pytest.fixture(scope="module")
def client():
    with TestClient(app) as c:
        yield c


def test_openapi_has_required_paths():
    """**路由契约**：关键路径一条都不许少 ✓（前端全靠它们 ✓）。"""
    paths = set(app.openapi().get("paths") or {})
    missing = [p for p in REQUIRED_PATHS if p not in paths]
    assert not missing, "这些路径不见了 ✗：%s" % missing


def test_version_matches_backend_module(client):
    """`/api/version` 与 `app.__version__` 一致 ✓（顶栏标黄就是靠它判前后端 ✓）。"""
    from app import __version__ as v

    r = client.get("/api/version")
    assert r.status_code == 200
    assert r.json().get("version") == v


def test_custom_formulas_returns_items(client):
    """公式库列表：200 + `items` 是列表 ✓（不校验条数 —— 那是用户数据 ✓）。"""
    r = client.get("/api/factors/custom-formulas")
    assert r.status_code == 200
    body = r.json()
    assert isinstance(body.get("items"), list)


def test_operators_returns_catalog(client):
    """算子目录：200 且非空 ✓（前端"算子帮助"用它 ✓）。"""
    r = client.get("/api/factors/operators")
    assert r.status_code == 200
    assert r.json()


def test_factor_datasets_list(client):
    """特征集列表：200 ✓。"""
    r = client.get("/api/factors/datasets")
    assert r.status_code == 200
    assert r.json() is not None


def test_datasets_status(client):
    """数据集（口径）状态：200 ✓（切换数据集的前置查询 ✓）。"""
    r = client.get("/api/datasets")
    assert r.status_code == 200
    assert r.json() is not None


def test_data_sources_list(client):
    """数据源能力表：200 ✓。"""
    r = client.get("/api/data-sources")
    assert r.status_code == 200
    assert r.json() is not None


def test_backtest_capacity(client):
    """并发回测能力：200 且含 `running` 之类字段 ✓。"""
    r = client.get("/api/backtest/capacity")
    assert r.status_code == 200
    assert isinstance(r.json(), dict)


@pytest.mark.parametrize("path", ["/api/backtest/no-such-task",
                                  "/api/backtest/no-such-task/artifacts"])
def test_unknown_task_is_404_not_500(client, path):
    """**未知任务必须 404 而不是 500** ✓ —— 前端据此提示"结果已清理"✓（不是"系统崩了"✗）。"""
    r = client.get(path)
    assert r.status_code == 404, "应为 404，实际 %s：%s" % (r.status_code, r.text[:120])


def test_translate_ok(client):
    """公式翻译：合法公式 → 200，且带回 `name` / `expression` ✓（前端保存公式必用 ✓）。"""
    r = client.post("/api/factors/translate", json={"formula": "MY_MA:MA(C,5);"})
    assert r.status_code == 200, r.text[:200]
    body = r.json()
    assert body.get("name") == "MY_MA"
    assert "$close" in (body.get("expression") or "")


@pytest.mark.parametrize("formula", ["", "   ", "A:=;", "A:MA(C,5", "A:UNKNOWN_OP(C);"])
def test_translate_rejects_bad_formula(client, formula):
    """**坏公式必须是 400（可读错误），不能 500** ✓。

    这类断言最能防"用户粘贴半截公式 ⇒ 后端 500 ⇒ 前端只看到 Network Error"✗。
    """
    r = client.post("/api/factors/translate", json={"formula": formula})
    assert r.status_code == 400, "公式 %r 应 400，实际 %s" % (formula, r.status_code)
    assert r.json().get("detail"), "400 必须带可读原因 ✓"


@pytest.mark.parametrize("path", ["/api/data/daily-bars", "/api/data/minute-bars"])
def test_data_bars_bad_args_are_4xx(client, path):
    """行情端点缺参数 ⇒ 4xx ✓（不允许 500 ✗）。"""
    r = client.get(path)
    assert 400 <= r.status_code < 500, "%s 应 4xx，实际 %s" % (path, r.status_code)


def test_signal_test_options(client):
    """交易信号测试的可选项：200 ✓。"""
    r = client.get("/api/signal-test/options")
    assert r.status_code == 200
    assert r.json() is not None
