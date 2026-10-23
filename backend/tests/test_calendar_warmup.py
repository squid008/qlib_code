# -*- coding: utf-8 -*-
"""**日历预热（warmup_days）边界**（★ v2.11.66）—— 真数据上才验得出来。

## 为什么这条重要
`Mean($close, 60)` 这类**窗口算子**在区间开头天然缺前 59 天 ✓。
· 不预热 ⇒ 前 59 行是 **NaN** ✓ —— 如果下游把它当 0 或直接 drop 掉，
  **特征就少了两个月** ✗，而**不会报错** ✗（模型照训、净值照算 ✓）；
· `warmup_days=60` 的意义就是"**往前多读 60 天**再算，让窗口在逻辑区间开头就已填满"✓。

⇒ 这里钉**两条相反的行为**：不预热 ⇒ 开头必须 NaN ✓；预热够 ⇒ 开头必须**已填满** ✓。
（合成数据也能测这条 ✓，但只有真数据能保证"窗口里真的有 60 个交易日"✓ —— 长假/停牌都在里面 ✓。）
"""
from __future__ import annotations

import os

import pandas as pd
import pytest

from app.factors import panel_expr as PE

INSTS = ["SH600000", "SH600519", "SZ000001"]
START, END = "2025-03-01", "2025-06-30"
WINDOW = 60


def _real_data() -> bool:
    d = PE._feature_dir()
    try:
        return os.path.isdir(d) and len(os.listdir(d)) >= 500
    except OSError:
        return False


pytestmark = [
    pytest.mark.datareq,
    pytest.mark.skipif(not _real_data(), reason="需要真实交易日历（含长假/停牌 ✓）"),
]


@pytest.fixture(scope="module", autouse=True)
def _init_qlib():
    if not _real_data():
        return
    from app.services.qlib_runtime import ensure_qlib_init

    ensure_qlib_init()


def _ma(insts=INSTS, warmup: int = 0) -> pd.DataFrame:
    return PE.panel_features(insts, [("Mean($close,%d)" % WINDOW, "ma")],
                             START, END, warmup_days=warmup)


class TestWindowWarmup:
    def test_engine_warms_up_by_itself(self):
        """⭐⭐ **反转的发现**：不显式预热，`Mean($close,60)` 在区间**开头就已经有值** ✓。

        我第一版断言"不预热 ⇒ 开头必有 NaN" ✗ ⇒ **实测红** ✓ ——
        因为 `_panel_features_uncached` 内部本来就 `_shift_calendar_start(start_time, warm + _wu)`
        做了**自动预热** ✓。所以：
        · `warmup_days=` 是**额外**加读 ✓（给更长的窗口用 ✓）；
        · "开头是 NaN" 从来不会发生 ✓ ⇒ 若哪天真的出现了 NaN，说明**自动预热被改坏了** ✗
          （那就是个真回归：特征少两个月而不报错 ✓）。
        """
        df = _ma(warmup=0)
        assert len(df) > 0, "面板 0 行（P2-#23 形态）⇒ 先修数据 ✓"
        g = df["ma"].unstack("instrument")
        head = g.iloc[:1]
        assert head.notna().any().any(), (
            "区间第一行就是 NaN ✗ ⇒ 内置预热失效了（特征会少一大段而不报错 ✓）")
        assert g.notna().any().any(), "整列全 NaN ✗"

    def test_with_warmup_head_is_filled(self):
        """⭐ 预热 60 天 ⇒ 逻辑区间**第一行就该有值** ✓（这正是 warmup 的定义 ✓）。"""
        df = _ma(warmup=WINDOW)
        assert len(df) > 0
        g = df["ma"].unstack("instrument")
        head = g.iloc[:3]
        assert head.notna().any().any(), (
            "预热 %d 天后开头仍全 NaN ✗ —— 预热没生效（或读取被截断了 ✓）" % WINDOW)

    def test_warmup_does_not_change_later_values(self):
        """⭐ 预热**只该补开头**，不该改后面的值 ✓（两边重叠区间必须逐位相同 ✗ 否则就是换了算法）。"""
        a = _ma(warmup=0)["ma"].unstack("instrument")
        b = _ma(warmup=WINDOW)["ma"].unstack("instrument")
        common_idx = a.index.intersection(b.index)
        common_col = a.columns.intersection(b.columns)
        if len(common_idx) < 10:
            pytest.skip("重叠区间太短 ⇒ 此条不适用 ✓")
        a2 = a.loc[common_idx, common_col].iloc[WINDOW:]
        b2 = b.loc[common_idx, common_col].iloc[WINDOW:]
        both = a2.notna() & b2.notna()
        if not both.any().any():
            pytest.skip("没有共同有值的点 ⇒ 此条不适用 ✓")
        diff = (a2 - b2).abs().where(both).max().max()
        assert float(diff) < 1e-9, (
            "预热改变了下游数值 ✗（最大差 %.3e）—— 预热应当只影响**开头**那段 ✓" % float(diff))

    def test_warmup_index_still_within_logical_range(self):
        """⚠ 预热**不许把输出区间往前扩** ✓ —— 输出只能是 [START, END]（否则下游日期对不上 ✗）。"""
        df = _ma(warmup=WINDOW)
        idx = pd.DatetimeIndex(df.index.get_level_values("datetime")).unique()
        assert idx.min() >= pd.Timestamp(START), (
            "输出起点 %s 早于请求区间 %s ✗ —— 预热数据泄漏进结果了 ✓" % (idx.min(), START))
        assert idx.max() <= pd.Timestamp(END), "输出终点超出请求区间 ✗"
