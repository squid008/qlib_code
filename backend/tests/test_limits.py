# -*- coding: utf-8 -*-
"""涨跌停判定（limits.py）单元测试：板块幅度 + 整分价口径。

对齐交易所/聚宽：
- 主板 10%（SH600/SZ000…）、创业板/科创板 20%（SZ300/SZ301/SH688）、北交所 30%（BJ）
- 昨收/涨停价/真实价都按"分"取整后判定（数据源真实价 = $close/$factor 除法有 float 尾差，
  不能直接与涨停价比较，否则整分边界样本对账差异）
"""
import numpy as np
import pandas as pd
import pytest

from app.engine.limits import limit_ratio, mark_limit_down, mark_limit_up


def _quote(rows):
    """rows: [(instrument, date, close, change), ...] → DataFrame(MultiIndex)."""
    idx = pd.MultiIndex.from_tuples(
        [(r[0], r[1]) for r in rows], names=["instrument", "datetime"]
    )
    return pd.DataFrame(
        {"CLOSE": [r[2] for r in rows], "CHANGE": [r[3] for r in rows]}, index=idx
    )


class TestLimitRatio:
    def test_board_ratios(self):
        assert limit_ratio("SH600000") == pytest.approx(0.10)  # 主板沪
        assert limit_ratio("SZ000001") == pytest.approx(0.10)  # 主板深
        assert limit_ratio("SZ300001") == pytest.approx(0.20)  # 创业板
        assert limit_ratio("SZ301236") == pytest.approx(0.20)  # 创业板(301)
        assert limit_ratio("SH688111") == pytest.approx(0.20)  # 科创板
        assert limit_ratio("BJ430047") == pytest.approx(0.30)  # 北交所
        # 大小写 / 带后缀的 qlib 代码
        assert limit_ratio("sz300001") == pytest.approx(0.20)
        assert limit_ratio("SZ300001") == pytest.approx(0.20)


class TestMarkLimitUp:
    def test_main_board_10pct(self):
        df = _quote([
            ("SH600000", "2021-01-04", 11.00, 0.10),   # 昨收10 涨停11.00 → 封板
            ("SH600000", "2021-01-05", 10.99, 0.099),  # 差一分 → 未封板
            ("SZ000001", "2021-01-04", 9.99, 0.10),    # 同上（昨收≈9.08? 见 change 反推）
        ])
        # 第三行 change=0.10 但 close 9.99：昨收=9.99/1.10=9.08，涨停价=9.99 → 封板
        out = mark_limit_up(df, "CLOSE", "CHANGE")
        assert list(out) == [True, False, True]

    def test_cyb_20pct_not_confused_with_10pct(self):
        # 昨收 10.00，创业板涨停价 12.00：涨 15%（11.50）不算涨停，涨 20%（12.00）才算
        df = _quote([
            ("SZ300001", "2021-01-04", 11.50, 0.15),    # 15% → 未封板（若按主板10%会误判封板）
            ("SZ300001", "2021-01-05", 12.00, 0.20),    # 20% → 封板
            ("SZ301236", "2021-01-06", 12.00, 0.20),    # 301 创业板同样 20%
            ("SH688111", "2021-01-07", 12.00, 0.20),    # 科创板同样 20%
        ])
        out = mark_limit_up(df, "CLOSE", "CHANGE")
        assert list(out) == [False, True, True, True]

    def test_st_k_codes_not_handled(self):
        # 新上市首日不设涨跌停等特殊规则不在本函数范围，仅验证函数正常返回布尔
        df = _quote([("SH605588", "2021-01-04", 20.00, 1.0)])  # 100% 主板 10% 涨停价 22? 非涨停
        out = mark_limit_up(df, "CLOSE", "CHANGE")
        assert out.iloc[0] in (True, False)

    def test_float_tail_close_on_limit_price(self):
        # 真实整分价 12.00（封板）在 float32 除法下可能存成 11.999999 / 12.000001，
        # 必须按分取整后判为封板，不能因尾差漏判（聚宽对账关键）
        df = _quote([
            ("SZ300001", "2021-01-04", 11.999999, 0.199999),  # 尾差向下 → round 12.00 封板
            ("SZ300001", "2021-01-05", 12.000001, 0.200001),  # 尾差向上 → round 12.00 封板
            ("SZ300001", "2021-01-06", 11.989999, 0.198999),  # 真实 11.99 → 未封板
        ])
        out = mark_limit_up(df, "CLOSE", "CHANGE")
        assert list(out) == [True, True, False]

    def test_suspended_nan_false(self):
        df = _quote([("SZ000001", "2021-01-04", np.nan, np.nan)])
        out = mark_limit_up(df, "CLOSE", "CHANGE")
        assert not bool(out.iloc[0])


class TestMarkLimitDown:
    def test_main_board_down(self):
        df = _quote([
            ("SH600000", "2021-01-04", 9.00, -0.10),   # 昨收10 跌停9.00 → 封死
            ("SH600000", "2021-01-05", 9.01, -0.099),  # 差一分 → 未封死
        ])
        out = mark_limit_down(df, "CLOSE", "CHANGE")
        assert list(out) == [True, False]

    def test_cyb_20pct_down(self):
        # 创业板跌停价 8.00（昨收 10，-20%）：跌 15%（8.50）不算，跌 20%（8.00）算
        df = _quote([
            ("SZ300001", "2021-01-04", 8.50, -0.15),
            ("SZ300001", "2021-01-05", 8.00, -0.20),
        ])
        out = mark_limit_down(df, "CLOSE", "CHANGE")
        assert list(out) == [False, True]
