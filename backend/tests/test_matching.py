# -*- coding: utf-8 -*-
"""撮合原子（`signals/matching.py`）的**独立单测** ★ P0-B，2026-10-18 ✓。

## 为什么这组测试值钱

这些函数是"**账**"的全部依据 ✓ —— 费用算错 = 绩效虚高 ✗、手数算错 = 买了买不起的量 ✗、
科创板的 200 股整手错 = 直接下错单 ✗。抽出模块之前，它们埋在 600 行的 `engine` 状态机里 ✗，
**没法单测**（只能跑整个回测看结果 ✓）；现在可以一函数一断言 ✓。

⚠ 断言的期望值都是**手算**的 ✓（不是"拿实现算一遍再断言"✗ —— 那等于没测 ✓）。
"""
import pytest

from app.signals import matching as M


@pytest.mark.parametrize("code,expect", [
    ("SH688001", 200),      # 科创板（沪）
    ("688001.SH", 200),     # 带后缀形态
    ("688001", 200),        # 纯代码
    ("689009", 200),        # 689 也算科创板 ✓
    ("SH600000", 100),      # 主板
    ("000001", 100),        # 深市主板
    ("SZ300750", 100),      # 创业板仍 100 ✓
])
def test_lot_of(code, expect):
    """整手股数：**科创板 688/689 = 200**，其余 100 ✓（三种代码形态都要认 ✓）。"""
    assert M.lot_of(code) == expect


def test_lot_of_custom_default():
    """`lot_default` 可覆盖 ✓（港股/其它市场将来能复用 ✓）。"""
    assert M.lot_of("SH600000", lot_default=500) == 500


@pytest.mark.parametrize("amount,half,expect", [
    (1000.0, 0.002, 5.0),        # 2 元 < 最低 5 元 ⇒ 取 5 ✓
    (10000.0, 0.002, 20.0),      # 20 元 > 5 元 ⇒ 按比例 ✓
    (2500.0, 0.002, 5.0),        # exactly 5 元 ⇒ 取 5 ✓（`fee > MIN_FEE` 才按比例 ✓）
    (1000.0, 0.0, 0.0),          # 无成本档 ⇒ 恒 0 ✓（不能被最低 5 元污染 ✗）
    (1000.0, -0.5, 0.0),         # 负数费率也当无成本 ✓（防御 ✓）
])
def test_fee_of(amount, half, expect):
    """手续费 = `max(成交额 × half, 最低 5 元)` ✓；`half<=0` ⇒ **恒 0** ✓。"""
    assert M.fee_of(amount, half) == pytest.approx(expect)


@pytest.mark.parametrize("budget,px,lot,half,cash,expect", [
    # 预算 1000、价 10 ⇒ 想买 100 股（1000 元），但 +5 元佣金 > 1000 ⇒ 缩到 0 ✓
    (1000.0, 10.0, 100, 0.002, 1000.0, 0),
    # 现金 2000：想看"按预算 2000 买 200 股"会不会被 5 元顶破 ⇒ 退到 100 股 ✓
    (2000.0, 10.0, 100, 0.002, 2000.0, 100),
    # 现金充足 ⇒ 按预算买满 ✓
    (2000.0, 10.0, 100, 0.002, 5000.0, 200),
    # 非正价 / NaN ⇒ 不买 ✓
    (1000.0, 0.0, 100, 0.002, 5000.0, 0),
    (1000.0, float("nan"), 100, 0.002, 5000.0, 0),
    # 非正手数 ⇒ 不买 ✓
    (1000.0, 10.0, 0, 0.002, 5000.0, 0),
])
def test_fit_lots_never_overspends(budget, px, lot, half, cash, expect):
    """`fit_lots`：**绝不透支** ✓ —— 用真实费用回退到手数为止 ✓（审计 #9 的回归守门 ✓）。"""
    q = M.fit_lots(budget, px, lot, half, cash)
    assert q == expect
    if q > 0:                                     # 而且必须真的买得起 ✓
        assert q * px + M.fee_of(q * px, half) <= cash + 1e-9


def test_fit_real_lots_degenerates_when_same_price():
    """不复权（`px_raw == px_adj`）⇒ **逐位退化成老行为** ✓（老数字一位都不许动 ✓）。"""
    q_adj, n = M.fit_real_lots(10.0, 10.0, 100, 0.002, 5000.0, budget=2000.0)
    assert n == 200
    assert q_adj == pytest.approx(200.0)


def test_fit_real_lots_uses_raw_price_for_whole_lots():
    """**按真实价求整手、再折回复权单位** ✓（审计 #8 的核心 ✓）。

    现金 1010、真实价 10、复权价 20 ⇒ 能买 **100 真实股**（1000+5 ≤ 1010 ✓）⇒
    复权单位 = 100×10/20 = **50** ✓（老代码按复权价算 = 买 0 ✗，这就是修的那个 bug ✓）。
    """
    q_adj, n = M.fit_real_lots(10.0, 20.0, 100, 0.002, 1010.0)
    assert n == 100
    assert q_adj == pytest.approx(50.0)


def test_fit_real_lots_buys_nothing_when_cannot_afford_one_lot():
    """连一手都买不起 ⇒ **返回 0，不硬凑** ✓（现金 685 / 价 10 ⇒ 一手要 1005 ✗）。"""
    q_adj, n = M.fit_real_lots(10.0, 6.0, 100, 0.002, 685.0)
    assert (q_adj, n) == (0.0, 0)


def test_fill_tables_cover_all_supported_modes():
    """成交时点表：三种模式都在 ✓（`t_close` / `t1_open` / `t1_close` ✓）。"""
    assert set(M.FILL_LAG) == {"t_close", "t1_open", "t1_close"}
    assert set(M.FILL_PRICE) == {"t_close", "t1_open", "t1_close"}
    assert M.FILL_PRICE["t1_open"] == "open"
    assert M.FILL_PRICE["t_close"] == "close"


def test_reject_text_has_no_raw_codes():
    """拒单文案表：关键码都要有中文 ✓（界面显示裸码是"未收录"的意思 ✓，别让它悄悄发生 ✗）。"""
    for code in ("limit_up", "limit_up_open", "limit_down", "limit_down_open",
                 "suspended", "no_cash", "no_price", "max_positions"):
        assert code in M.REJECT_TEXT, "缺文案：%s" % code
