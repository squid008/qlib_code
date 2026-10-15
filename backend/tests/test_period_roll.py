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


# ===========================================================================
# ★ v2.4.3：**期中离场通道**（用户 2026-10-15 拍板 ✓）—— 四条语义各一条测试 ✓
#   ① 非调仓日触发离场 ⇒ 卖出（T+1 按 `fill` 执行 ✓）
#   ② 卖不掉（跌停）⇒ **顺延逐日重试** ✓（与主引擎同规矩 ✓）
#   ③ 调仓日若它**仍在名单** ⇒ 离场被**覆盖**（= 用户的"同日进+出 ⇒ 不交易"✓）
#   ④ 持币期间触发 ⇒ **空转**（忽略但计数 ✓）
# ===========================================================================
CODES3 = ["S00", "S01", "S02"]


def _exit_mat(pairs, codes=CODES) -> "np.ndarray":
    """(T×C) 布尔离场矩阵 ✓。

    ⚠ 列必须与 `run_period_roll` 内部认的 `codes` **完全一致** ✗ —— 它是
      `sorted(set(信号里的 code) & set(面板列))` ✓（**只有信号里出现过的票** ✓，
      面板里多出来的列不算 ✗）⇒ 矩阵造错会命中"形状不符 ⇒ 关掉离场"的兜底 ✓，
      测试会变成"看起来没生效"✗（第一版就是这么踩的 ✓）。
    """
    m = np.zeros((len(CAL), len(codes)), dtype=bool)
    for d, c in pairs:
        m[d, list(codes).index(c)] = True
    return m


def test_exit_signal_sells_between_rebalances():
    """★ 核心①：**非调仓日**触发离场 ⇒ 卖出，且 reason = `exit_signal` ✓（T 日信号 T+1 执行 ✓）。"""
    sig = _signals([(0, "S00"), (0, "S01"), (8, "S00"), (8, "S01")])   # 只在期初/期末调仓 ✓
    out = run_period_roll(sig, _panel({}), fill="t1_open", cost=0.0, capital=1e7,
                          strict_limit=True, exit_mat=_exit_mat([(3, "S00")]))
    hit = out.trades[(out.trades["code"] == "S00") & (out.trades["reason"] == "exit_signal")]
    assert len(hit) == 1, out.trades
    assert str(hit.iloc[0]["date"])[:10] == str(CAL[4].date()), "T 日信号 ⇒ T+1 成交 ✓\n%s" % hit
    st = out.stats["period_roll"]
    assert st["exit_enabled"] is True and st["exit_signals"] == 1 and st["exit_sells"] == 1, st
    # 对照（不传矩阵）⇒ **不该**有离场卖单 ✓（两条曲线才有可比性 ✓）
    base = run_period_roll(sig, _panel({}), fill="t1_open", cost=0.0, capital=1e7,
                           strict_limit=True)
    assert not (base.trades["reason"] == "exit_signal").any(), base.trades
    assert base.stats["period_roll"]["exit_enabled"] is False


def test_exit_signal_deferred_when_cannot_sell():
    """★ ②：执行日跌停卖不掉 ⇒ **顺延逐日重试** ✓，并能统计顺延天数 ✓。"""
    sig = _signals([(0, "S00"), (0, "S01"), (8, "S00"), (8, "S01")])
    panel = _panel({})
    for d in (3, 4):                                   # 执行日 3、4 跌停（收盘 = 跌停价 ⇒ 卖不出 ✓）
        panel["LIMIT_DOWN"].loc[CAL[d], "S00"] = panel["CLOSE"].loc[CAL[d], "S00"]
    out = run_period_roll(sig, panel, fill="t1_open", cost=0.0, capital=1e7,
                          strict_limit=True, exit_mat=_exit_mat([(2, "S00")]))
    hit = out.trades[(out.trades["code"] == "S00") & (out.trades["reason"] == "exit_signal")]
    assert len(hit) == 1, out.trades
    assert str(hit.iloc[0]["date"])[:10] == str(CAL[5].date()), "顺延到能卖的那天 ✓\n%s" % hit
    assert out.stats["period_roll"]["exit_deferrals"] == 2, out.stats["period_roll"]


def test_exit_on_rebalance_day_is_overridden_by_list():
    """★ ③（用户那条"同日进+出 ⇒ 不交易"）：**调仓日**它仍在名单 ⇒ 不离场 ✓（计数 ✓）。

    ⚠ 调仓日 = **执行日** = `信号日 + lag`（`fill='t1_open'` ⇒ lag=1 ✓）：信号日 CAL[4] 的
      执行日是 CAL[5] ✓ ⇒ 离场矩阵要打在**信号日** CAL[4] 上（引擎内部再 `-lag` ✓）——
      打在 CAL[5] 上会变成"执行日 CAL[6]"⇒ 测不到覆盖 ✗（两种错法我都踩过 ✓）。
    """
    sig = _signals([(0, "S00"), (0, "S01"), (4, "S00"), (4, "S01"), (8, "S00"), (8, "S01")])
    out = run_period_roll(sig, _panel({}), fill="t1_open", cost=0.0, capital=1e7,
                          strict_limit=True, exit_mat=_exit_mat([(4, "S00")]))
    assert not (out.trades["reason"] == "exit_signal").any(), out.trades
    st = out.stats["period_roll"]
    assert st["exit_overridden"] >= 1, st


def test_exit_signal_while_no_position_is_counted_but_ignored():
    """★ ④：持币期间触发 ⇒ **空转**（忽略 ✓ 但计数 ✓，绝不凭空开仓/报错 ✓）。

    构造：S00 在自己的离场信号下于 CAL[4] 被卖掉 ✓ ⇒ 后来的离场信号是"持币期间"⇒ 空转 ✓。
    """
    sig = _signals([(0, "S00"), (0, "S01"), (8, "S00"), (8, "S01")])
    out = run_period_roll(sig, _panel({}), fill="t1_open", cost=0.0, capital=1e7,
                          strict_limit=True, exit_mat=_exit_mat([(3, "S00"), (5, "S00")]))
    st = out.stats["period_roll"]
    assert st["exit_signals"] == 1, "只有持仓中那次算 exit_signals ✓：%s" % st
    assert st["exit_ignored"] == 1, "CAL[5] 那次是持币期间 ⇒ 空转 ✓：%s" % st
    assert st["exit_sells"] == 1, st


def test_exit_matrix_shape_mismatch_disables_exit_without_crashing():
    """★ 兜底：离场矩阵形状不符 ⇒ **关掉离场**并记 diag ✓（附加信息绝不拖垮回测 ✗）。"""
    sig = _signals([(0, "S00"), (0, "S01"), (8, "S00"), (8, "S01")])
    out = run_period_roll(sig, _panel({}), fill="t1_open", cost=0.0, capital=1e7,
                          strict_limit=True, exit_mat=np.zeros((3, 3), dtype=bool))
    st = out.stats["period_roll"]
    assert st["exit_enabled"] is False and st["exit_signals"] == 0, st
    assert out.diag.get("exit_note"), out.diag
    assert st["final_nav"] is not None, "回测本身必须照常跑完 ✓"
