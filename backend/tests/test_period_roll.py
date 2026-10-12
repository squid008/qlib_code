# -*- coding: utf-8 -*-
"""`period_roll`（每期全仓滚动）语义守门单测（离线自造面板 ✓ 不依赖 qlib/真实数据 ✓）。

重点守两条（都是用户 2026-10-11 亲自指出来的 ✓）：

· **顺延的卖单必须在票"回到名单"后作废** ✓ —— 用户原话：
  "遇到跌停顺延，但是下次名单有它了，就不应该继续卖了呀。这跟 `event_even` 应该是一个逻辑才对" ✓
  （v2.1.13 修的是 `drop` 集合**累加**的 bug ✗）。
· **只带单批次时不该出现 `batch_even` 语义** ✓（每批固定 1/N ✗）—— 那条由 `parsers` 侧保证 ✓，
  这里只测 `period_roll` 本身 ✓。
"""
import numpy as np
import pandas as pd

from app.signals.period_roll import run_period_roll

CODES = ["S00", "S01"]
CAL = pd.bdate_range("2024-01-01", periods=9)


def _panel(limit_down_days: dict) -> dict:
    """造一个离线面板：价格恒定 10 元；`limit_down_days` 指定"某票在某几天跌停"（卖不出 ✓）。"""
    close = pd.DataFrame(10.0, index=CAL, columns=CODES)
    open_ = close.copy()
    lu = close * 1.1
    ld = close * 0.9
    for code, days in limit_down_days.items():
        for d in days:
            ld.loc[CAL[d], code] = close.loc[CAL[d], code]      # 收盘价 = 跌停价 ⇒ can_sell 挡掉 ✓
    return {"CALENDAR": pd.DataFrame(index=CAL), "CLOSE": close, "OPEN": open_,
            "CLOSE_RAW": close.copy(), "OPEN_RAW": open_.copy(),
            "LIMIT_UP": lu, "LIMIT_DOWN": ld, "_limits_inferred": True}


def _signals(pairs) -> pd.DataFrame:
    days = [CAL[d] for d, _c in pairs]
    codes = [c for _d, c in pairs]
    return pd.DataFrame({"date": pd.to_datetime(days), "code": codes, "side": 1})


def test_deferred_sell_is_cancelled_when_name_returns_to_list():
    """★ 核心：A 在第 2 期被剔除但**跌停卖不出**；第 3 期它**回到名单** ⇒ 不该再卖 ✓。"""
    # 期次：day0 = 第一期(A,B)、day4 = 第二期(只 B)、day6 = 第三期(A,B 回归 ✓)
    sig = _signals([(0, "S00"), (0, "S01"), (4, "S01"), (6, "S00"), (6, "S01")])
    # ⚠⚠ 关键：A **只在第二期跌停**（卖不出 ⇒ 顺延 ✓），到第三期**恢复可卖** ✓ ——
    #   否则"旧实现想卖也卖不掉"⇒ 断言照样过 ⇒ **测不出那个 bug** ✗
    #   （我第一版就是这么写的，被自己抓出来 ✓）。现在：旧实现会在第 3 期把它卖掉 ✗，
    #   新实现因为"它回到名单"而**不该卖** ✓ ⇒ 这条断言才真正卡住 bug ✓。
    panel = _panel({"S00": [4, 5]})
    out = run_period_roll(sig, panel, fill="t1_open", cost=0.004, capital=1e7,
                          strict_limit=True)
    tr = out.trades
    # ⚠ 断言的是"**不得因为移出名单而被卖**"（`roll_out` ✓），**不是**"完全不能有卖单" ✗ ——
    #   等权重建时的 `roll_trim`（把超配的份额调回目标 ✓）是正常行为 ✓（我第一版断言过严，
    #   被单测自己教育了一次 ✓）。
    sell_out_a = tr[(tr["code"] == "S00") & (tr["reason"] == "roll_out")]
    assert len(sell_out_a) == 0, "A 回到名单 ⇒ 不该再按「移出名单」卖 ✗\n%s" % sell_out_a
    # 顺延确实发生过（否则这条测试没意义 ✓）：应留下"跌停卖不出"的记录 ✓
    assert (out.rejects["reason"] == "limit_down").any(), out.rejects
    st = out.stats["period_roll"]
    assert st["rolls"] == 3, st          # 三个调仓日 ✓
    assert st["final_nav"] is not None


def test_pure_rotation_sells_dropped_name_when_sellable():
    """对照：能卖的时候，被剔除的票**必须卖掉** ✓（别把上面的修复做成"永远不卖" ✗）。"""
    sig = _signals([(0, "S00"), (0, "S01"), (4, "S01"), (6, "S00"), (6, "S01")])
    out = run_period_roll(sig, _panel({}), fill="t1_open", cost=0.004, capital=1e7,
                          strict_limit=True)
    tr = out.trades
    sell_out_a = tr[(tr["code"] == "S00") & (tr["reason"] == "roll_out")]
    assert len(sell_out_a) == 1, "A 被剔除且能卖 ⇒ 应卖出 1 次 ✓\n%s" % tr
