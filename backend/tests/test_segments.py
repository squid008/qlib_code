# -*- coding: utf-8 -*-
"""滚动训练分段生成的单元测试。"""
import pytest

from app.engine.qlib_engine import _gen_rolling_segments
from app.models.backtest import BacktestRequest


def _req(start, end, train_win=12, train_unit="month", test_win=3, test_unit="month", step_win=None, step_unit=None):
    return BacktestRequest(
        universe="csi300", start_date=start, end_date=end,
        train_win=train_win, train_unit=train_unit,
        test_win=test_win, test_unit=test_unit,
        step_win=step_win, step_unit=step_unit,
    )


class TestGenRollingSegments:
    def test_basic_rolling(self):
        """2022-01~2022-06，测试窗3月/步长3月 → 2段：1-4月、4-7月(截断到6月底)。"""
        req = _req("2022-01-01", "2022-06-30", test_win=3, test_unit="month")
        segs = _gen_rolling_segments(req, calendar=None)
        assert len(segs) == 2
        assert segs[0]["seq"] == 1
        # 测试段 = [cursor, cursor + 3月)；第二段截断到 end_date
        assert segs[0]["test"] == ("2022-01-01", "2022-04-01")
        assert segs[1]["seq"] == 2
        assert segs[1]["test"] == ("2022-04-01", "2022-06-30")
        # 训练段在测试开始前
        assert segs[0]["train"][1] < segs[0]["test"][0]

    def test_seq_starts_at_1(self):
        req = _req("2022-01-01", "2022-03-31", test_win=3, test_unit="month")
        segs = _gen_rolling_segments(req, calendar=None)
        assert len(segs) == 1
        assert segs[0]["seq"] == 1  # 段标签从 1 开始，不是 0

    def test_train_window(self):
        """训练窗口 12 月：`train_start = test_start − 12 月` ✓；**段末要 purge** ✓（v2.8.2 审计 #1 ✓）。

        ⚠ 段末**不再**写死"测试起点前一天" ✗（那是未来信息：末批样本的标签会读到测试期价 ✓，
        而 valid 是 `early_stopping` 的打分集 ✓）⇒ 现在退到测试起点前 `h+1` 个**交易日**
        （日历可用时 ✓，否则自然日 ✗）。这里只断言**不变量**，不锁死具体日期 —— 具体值会随
        日历/周末漂 ✓，锁死会把"按交易日退"这件事测成脆的 ✗（本轮就是它先红了 ✓）。
        """
        req = _req("2022-01-01", "2022-03-31", train_win=12, train_unit="month", test_win=3, test_unit="month")
        segs = _gen_rolling_segments(req, calendar=None)
        train_start, train_end = segs[0]["train"]
        assert train_start == "2021-01-01"
        assert train_end < segs[0]["test"][0], "purge：段末必须**严格早于**测试起点 ✓ 实际 %s" % train_end
        assert train_end >= "2021-12-15", "purge 只退 h+1 个交易日，不该退太多 ✓ 实际 %s" % train_end
        assert segs[0]["test"] == ("2022-01-01", "2022-03-31")
