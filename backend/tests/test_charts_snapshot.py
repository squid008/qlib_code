# -*- coding: utf-8 -*-
"""`_save_curve_snapshot` 4 条（★ v2.9.89，2026-10-19 ✓ —— `engine/charts.py`，全仓覆盖最低的模块 ✓）。

## 为什么这个"画图函数"值得测

它整个函数体包在 `try/except Exception` 里 ✓，**出错只会留一行日志** ✗ ——
也就是说：图没画出来时，**接口照常返回 200、结果照常展示，只是产物目录里少几张图** ✗。
而前端/历史面板正是按文件名取图的（`summary.png` / `nav_curve.png` / `params_snapshot.png` ✓）
⇒ 少一张 = 用户看到**碎图** ✗，且没有任何报错可查 ✓。

⇒ 所以这里只钉**最硬的事实**：**该出图时三个文件真的在、且是合法 PNG** ✓
（不是"看着像成功"✗ —— 用 PNG 魔数 `\\x89PNG` 验 ✓）。

⚠ 用 `SimpleNamespace` 假冒 `BacktestRequest` ✓（它只读 19 个标量字段 ✓，duck typing 足够 ✓）；
真正建 `BacktestRequest` 会因必填字段演化而碎 ✗。
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from app.engine.charts import _save_curve_snapshot

PNG_MAGIC = b"\x89PNG\r\n\x1a\n"


def _req(**over):
    base = dict(
        feature="Alpha158", model="lgb", universe="csi300", topk=50, n_days_hold=10,
        start_date="2023-01-01", end_date="2023-06-30", split_mode="single",
        train_win=1, train_unit="year", test_win=1, test_unit="month",
        open_cost=0.0013, close_cost=0.0013, impact_cost=0.0005, min_cost=5.0,
        trade_unit=100, limit_threshold=0.1, volume_threshold=None,
        initial_capital=1000000.0, deal_price="close",
    )
    base.update(over)
    return SimpleNamespace(**base)


def _nav(n=6, with_bench=True):
    out = []
    for i in range(n):
        p = {"date": "2023-01-%02d" % (i + 2), "value": 1.0 + 0.01 * i}
        if with_bench:
            p["benchmark"] = 1.0 + 0.005 * i
        out.append(p)
    return out


def _result(nav=None, **over):
    r = SimpleNamespace(
        nav=nav if nav is not None else _nav(),
        annualized_return=0.1234, annualized_excess_return=0.05,
        sharpe=1.23, max_drawdown=-0.0876, win_rate=0.55,
        total_return=0.2345, benchmark_return=0.1234,
    )
    for k, v in over.items():
        setattr(r, k, v)
    return r


@pytest.fixture()
def _agg(monkeypatch):
    """强制无界面后端 ✓（CI 上没有显示器 ✗）。"""
    import matplotlib

    monkeypatch.setattr(matplotlib, "use", lambda *a, **k: None)
    matplotlib.use("Agg")


def test_no_dir_writes_nothing(tmp_path, _agg):
    _save_curve_snapshot("", _req(), _result())
    assert list(tmp_path.iterdir()) == []


def test_empty_nav_writes_nothing(tmp_path, _agg):
    _save_curve_snapshot(str(tmp_path), _req(), _result(nav=[]))
    assert list(tmp_path.iterdir()) == []


def test_three_pngs_written_and_valid(tmp_path, _agg):
    """⭐ 该出图时必须**三个文件都在** ✓ 且是合法 PNG ✓（少一张 = 前端碎图 ✗）。"""
    _save_curve_snapshot(str(tmp_path), _req(), _result())
    names = {p.name for p in tmp_path.iterdir()}
    for expect in ("summary.png", "nav_curve.png", "params_snapshot.png"):
        assert expect in names, "缺少 %s ✗（函数把异常吞了，只能靠这里发现 ✓）" % expect
        data = (tmp_path / expect).read_bytes()
        assert data.startswith(PNG_MAGIC), "%s 不是合法 PNG ✗" % expect
        assert len(data) > 1000, "%s 太小（可能只画了空白）✗" % expect


def test_robust_to_missing_benchmark_and_repeat_call(tmp_path, _agg):
    """没有基准曲线 / 连画两次 ⇒ 都不许炸 ✓（第二次是覆盖写入 ✓）。"""
    _save_curve_snapshot(str(tmp_path), _req(), _result(nav=_nav(with_bench=False)))
    _save_curve_snapshot(str(tmp_path), _req(), _result(nav=_nav(with_bench=False)))
    assert (tmp_path / "summary.png").exists()
