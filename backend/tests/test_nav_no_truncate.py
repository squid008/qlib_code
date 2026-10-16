# -*- coding: utf-8 -*-
"""★ T2（审计 ⑫ / v2.8.7，用户 2026-10-16 已拍 ✓）：**净值曲线不许截尾** ✗。

审计现象：长区间回测出现「**总收益 100% 但曲线终值只有 1.80、最后 500 天不见**」✗ ——
根因是两处硬截断（而**指标是用完整序列算的** ✓ ⇒ 指标与曲线自相矛盾 ✗）：
  · `metrics._aggregate_from_nav`：`result.nav = all_nav[:2000]` ✗（滚动路径，10 年 ≈ 2500 天）；
  · `metrics._extract_result`：`result.nav = nav[:800]` ✗（一次性训练路径，10 年丢 1700 天）。
修法：两处都**返回全程** ✓（逐日 10 年 ≈ 2500 点、百 KB 级 ✓）；绘图性能交给**前端抽样** ✓。
"""
import numpy as np
import pandas as pd

from app.engine.metrics import _aggregate_from_nav, _extract_result


class _FakeRecorder:
    """只实现 `load_object`（方式一命中即可 ⇒ `par` 不会被用到 ✓）。"""

    def __init__(self, df):
        self._df = df

    def load_object(self, key):                       # noqa: ARG002
        return self._df


# ---------------------------- ① 滚动路径：`[:2000]` ----------------------------

def test_aggregate_keeps_more_than_2000_points():
    """★ 2500 个点（≈10 年）必须**一个不少** ✓ —— 旧实现会砍到 2000 ✗。"""
    pts = [{"date": "d%04d" % i, "value": 1.0 + i * 0.0004} for i in range(2500)]
    res = _aggregate_from_nav(pts, [])
    assert len(res.nav) == 2500, "曲线被截尾 ✗（指标用全程、曲线缺尾 ⇒ 自相矛盾 ✗）"
    # ★ 曲线与指标必须**同源**：终值 - 1 就是总收益 ✓
    assert abs(res.total_return - (res.nav[-1]["value"] - 1.0)) < 1e-9


def test_aggregate_short_series_untouched():
    """短序列（< 2000）本来就该原样 ✓（改动不能影响它 ✓）。"""
    pts = [{"date": "d%02d" % i, "value": 1.0 + i * 0.001} for i in range(30)]
    res = _aggregate_from_nav(pts, [])
    assert len(res.nav) == 30


# ---------------------------- ② 一次性训练路径：`[:800]` ----------------------------

def _report(n: int) -> pd.DataFrame:
    idx = pd.bdate_range("2016-01-04", periods=n)
    acct = 1e6 * (1.0 + 0.0004) ** np.arange(n)        # 稳定上涨 ⇒ 净值单调 ✓
    # ⚠ `bench` 是**收益序列**（`(1+bench).cumprod()` ✓），给 1.0 会 2^n **溢出** ✗ ⇒ 给 0 ✓
    return pd.DataFrame({"account": acct, "return": np.zeros(n), "bench": np.zeros(n)}, index=idx)


def test_extract_result_keeps_more_than_800_points():
    """★ 1200 天必须全在 ✓ —— 旧实现只留前 800 天（后 400 天消失 ✗）。"""
    res = _extract_result(None, _FakeRecorder(_report(1200)), initial_account=1e6)
    assert len(res.nav) == 1200, "曲线被截尾 ✗（`nav[:800]` 的残留）"


def test_extract_result_metrics_match_full_series():
    """★ 指标与曲线**同源**：曲线末值 - 1 == 总收益 ✓（这正是审计里对不上的那件事 ✓）。

    ⚠ 容差取 `1e-5`：`result.nav` 里的值**四舍五入到 6 位小数**（`round(..., 6)` ✓），
      而 `total_return` 用未取整值 ⇒ 两者天然差 ~1e-7 ✓（我第一版容差写 `1e-9` ✗，被测试纠正 ✓）。
    """
    res = _extract_result(None, _FakeRecorder(_report(1000)), initial_account=1e6)
    assert len(res.nav) == 1000
    assert abs(res.total_return - (res.nav[-1]["value"] - 1.0)) < 1e-5
    # ⚠ 多年期里**年化必然小于总收益** ✓（我第一版写成 `>` ✗，被这条测试纠正 ✓）
    assert res.annualized_return is not None and 0 < res.annualized_return < res.total_return
