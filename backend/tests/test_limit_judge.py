# -*- coding: utf-8 -*-
"""★ T1（v2.8.6，用户 2026-10-16 拍板 ✓）：**涨停判定口径开关**（审计 ② ✓）。

现象（审计原文 ✓）：`signals/engine.py::can_buy` **无条件**先按**成交日收盘涨停**判 ⇒
`t1_open`（次日**开盘**成交）时用了**当日收盘**信息 = 未来信息 ✗。
用户口径：**做成开关、默认老口径** ✓（原话："可能开盘 1 秒拉涨停，其实你吃不到" ⇒ 保守 ✓）；
新口径 = **只拒「开盘一字涨停 / 停牌」** ✓。

语义（本文件逐条钉住 ✓）：
  ① `legacy`（默认 ✓）＝ 收盘涨停也拒 ⇒ 与改动前**逐位一致** ✓；
  ② `open_only` ＝ 不再看收盘 ✗，只看"开盘一字涨停 / 停牌" ✓；
  ③ **`open_only` 只在开盘成交时生效** ✓ —— 收盘成交时引擎**自动退回 legacy** ✓
     （返回的 `judge` 就是生效值 ⇒ 调用方落 `diag` ✓，不会"选了却没生效"还静默 ✗）。
"""
import numpy as np

from app.signals.engine import _limit_ctx

# 一只票、一天；列顺序 = [正常股, 收盘涨停(开盘未一字), 开盘一字涨停, 停牌]
_N = 4
_I_NORMAL, _I_LIMIT_CLOSE, _I_LIMIT_OPEN, _I_SUSP = 0, 1, 2, 3


def _prep():
    """造一份最小 `prep`：真实收盘价 / 开盘价 / 涨跌停价 ✓（只留一个变量 ✓）。"""
    C = np.array([[10.0, 11.0, 11.0, np.nan]])       # 复权收盘（引擎主要看 CR/OR ✓）
    O = np.array([[10.0, 10.0, 11.0, np.nan]])       # 开盘：票1 收盘涨停但开盘未一字 ✓
    CR = np.array([[10.0, 11.0, 11.0, np.nan]])      # 真实收盘
    OR = np.array([[10.0, 10.0, 11.0, np.nan]])      # 真实开盘（票2 = 11.0 ⇒ **开盘一字** ✓）
    LU = np.array([[11.0, 11.0, 11.0, 11.0]])        # 涨停价（票1/票2 都到涨停 ✓）
    LD = np.array([[9.0, 9.0, 9.0, 9.0]])
    return {"C": C, "O": O, "CR": CR, "OR": OR, "LU": LU, "LD": LD}


def _ctx(fill, limit_judge="legacy", strict_limit=True):
    px_kind = "open" if fill == "t1_open" else "close"
    _price, can_buy, can_sell, _PX, _RAW, judge = _limit_ctx(
        _prep(), px_kind, strict_limit, limit_judge)
    return can_buy, can_sell, judge


# ---------------------------- ① 老口径 = 逐位不变 ----------------------------

def test_legacy_open_still_rejects_close_limit_up():
    """★ 默认老口径（开盘成交）：**收盘涨停也拒** ✓（= 改动前行为，逐位一致 ✓）。"""
    can_buy, _cs, judge = _ctx("t1_open", "legacy")
    assert judge == "legacy"
    assert can_buy(0, _I_NORMAL) is None
    assert can_buy(0, _I_LIMIT_CLOSE) == "limit_up"          # ★ 老口径特征 ✓
    assert can_buy(0, _I_SUSP) == "suspended"


def test_legacy_close_fill_unchanged():
    """老口径 + 收盘成交：只看收盘涨停 ✓（与历史一致 ✓）。

    ⚠ 注意：**真一字板的收盘也必然涨停** ✓（O=H=L=C=涨停价 ✓）⇒ 票2 在收盘成交时
      同样返回 `limit_up` ✓（`can_buy` 先判收盘 ⇒ 不会走到 `limit_up_open` 分支 ✓）——
      这是老口径的**既有行为** ✓，本测试把它钉住 ✓（我第一版断言写成 None ✗，被这条测试纠正 ✓）。
    """
    can_buy, _cs, judge = _ctx("t1_close", "legacy")
    assert judge == "legacy"
    assert can_buy(0, _I_LIMIT_CLOSE) == "limit_up"
    assert can_buy(0, _I_LIMIT_OPEN) == "limit_up"           # 一字板 ⇒ 收盘也涨停 ✓（老口径先判 ✓）
    assert can_buy(0, _I_NORMAL) is None


# ---------------------------- ② 新口径 = 只拒一字板 ----------------------------

def test_open_only_lets_close_limit_up_through():
    """★★ 新口径（用户要的 ✓）：票1「收盘涨停、开盘没一字」⇒ **可以买** ✓（老口径会拒 ✗）。"""
    can_buy, can_sell, judge = _ctx("t1_open", "open_only")
    assert judge == "open_only"
    assert can_buy(0, _I_LIMIT_CLOSE) is None                # ★ 核心差异：放行 ✓
    # ⚠ 但"开盘一字涨停"仍要拒 ✓（= "只拒一字板" ✓）
    assert can_buy(0, _I_LIMIT_OPEN) == "limit_up_open"
    assert can_buy(0, _I_SUSP) == "suspended"
    assert can_buy(0, _I_NORMAL) is None


def test_open_only_still_blocks_one_word_board():
    """★ 一字板买不进 ✓ —— 这正是用户保留新口径时唯一要拦的情形 ✓。"""
    can_buy, _cs, _j = _ctx("t1_open", "open_only")
    assert can_buy(0, _I_LIMIT_OPEN) == "limit_up_open"


def test_strict_limit_false_only_checks_suspension():
    """`strict_limit=False` ⇒ 两种口径都只按停牌判 ✓（老行为不变 ✓）。"""
    for lj in ("legacy", "open_only"):
        can_buy, _cs, _j = _ctx("t1_open", lj, strict_limit=False)
        assert can_buy(0, _I_LIMIT_CLOSE) is None
        assert can_buy(0, _I_LIMIT_OPEN) is None
        assert can_buy(0, _I_SUSP) == "suspended"


# ---------------------------- ③ 守卫：非开盘成交不许静默分叉 ----------------------------

def test_open_only_falls_back_to_legacy_on_close_fill():
    """★★ 收盘成交 + 选了新口径 ⇒ **自动退回 legacy** ✓，且返回的 `judge` 就是生效值 ✓ ——
    否则会变成"完全不判涨跌停" ✗（用户看不出、又和导出文件对不上 ✗）。"""
    can_buy, _cs, judge = _ctx("t1_close", "open_only")
    assert judge == "legacy", "非开盘成交必须退回老口径（并把生效值交出去 ✓）"
    assert can_buy(0, _I_LIMIT_CLOSE) == "limit_up"


def test_unknown_value_is_treated_as_legacy():
    """脏输入（拼错/老前端）⇒ 当 legacy ✓，绝不"静默变新口径" ✗。"""
    can_buy, _cs, judge = _ctx("t1_open", "what?")
    assert judge == "legacy"
    assert can_buy(0, _I_LIMIT_CLOSE) == "limit_up"


def test_sell_side_mirrors_buy_side():
    """卖出侧对称 ✓：老口径看"收盘跌停"，新口径只拒"开盘一字跌停" ✓。"""
    c = np.array([[9.0, 9.0, 9.0, np.nan]])                  # 收盘跌停（开盘未一字）
    o = np.array([[10.0, 10.0, 9.0, np.nan]])
    prep = _prep()
    prep["C"], prep["O"], prep["CR"], prep["OR"] = c, o, c, o
    _p, _b, can_sell_legacy, _px, _raw, j1 = _limit_ctx(prep, "open", True, "legacy")
    _p, _b, can_sell_open, _px, _raw, j2 = _limit_ctx(prep, "open", True, "open_only")
    assert j1 == "legacy" and j2 == "open_only"
    assert can_sell_legacy(0, 0) == "limit_down"             # 老口径：收盘跌停卖不掉 ✓
    assert can_sell_open(0, 0) is None                       # 新口径：放行 ✓
    assert can_sell_open(0, 2) == "limit_down_open"          # 一字跌停仍卖不掉 ✓
