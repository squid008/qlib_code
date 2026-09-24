# -*- coding: utf-8 -*-
"""★ v1.20.67：`topk_ratio` 的分母 = 当日**全池可交易只数**（不是"合成后还剩的只数"✗）。

用户 2026-09-24 实测事故（任务 `7dbe17524d5c` 段 1 ✓）：
    `feature=mixed` + **开闸门** + `topk_ratio=0.01` ⇒ 候选被闸门压到 ~38 只 ✗
    ⇒ 旧口径 `1% × 38 = 0.38` ⇒ 四舍五入 0 ⇒ 兜底 **1 只** ✗✓
    ⇒ 那唯一一只还是**一字连板新股**（2021-01-04 SZ003028 / 2021-02-01 SZ003035 ✓，买不进 ✗）
    ⇒ **全程空仓、净值恒 1** ✗（`seg_result.end_position={"cash":…}` ✓）。
新口径（用户 2026-09-24 明确 ✓）：`比例 × 全池`，再**封顶**到闸门后的可用只数 ✓
    ⇒ `1% × ~5400 ≈ 54` ⇒ 实际买 **~38 只** ✓（= 用户预期 ✓）。
"""
from app.engine.periodic_strategy import PeriodicTopKStrategy


def _mk(topk=50, topk_ratio=None):
    """只测纯函数 ⇒ 用 `__new__` 绕过 `__init__`（不依赖 qlib 的 trade_calendar ✓）。"""
    st = PeriodicTopKStrategy.__new__(PeriodicTopKStrategy)
    st.topk = topk
    st.topk_ratio = topk_ratio
    return st


# ---------------------------- 百分比模式：分母 = 全池 ----------------------------

def test_ratio_denominator_is_full_pool():
    """★★ 用户原案：全池 5400、闸门后可用 38 ⇒ 1% ⇒ **38 只**（旧口径会退化 1 只 ✗）。"""
    st = _mk(topk_ratio=0.01)
    assert st._k_of(5400, 38) == 38        # 1%×5400 = 54 ⇒ 封顶到可用 38 ✓
    assert st._k_of(5400, 5400) == 54      # 没开闸门：54 只 ✓


def test_ratio_keeps_old_numbers_when_no_gate():
    """⚠ 不开闸门时（可用 == 全池 ✓）新旧口径**结果一致** ✓ —— 这批数字来自 v1.20.66 的实测 ✓。"""
    assert _mk(topk_ratio=0.01)._k_of(5000, 5000) == 50
    assert _mk(topk_ratio=0.05)._k_of(3000, 3000) == 150
    assert _mk(topk_ratio=1.0)._k_of(800, 800) == 800


def test_ratio_capped_by_available():
    """⚠ 闸门把候选压得比比例更狠时 ⇒ 买**能买到的那些**（不越界 ✗）。"""
    st = _mk(topk_ratio=0.5)
    assert st._k_of(5400, 300) == 300      # 0.5×5400 = 2700 ⇒ 封顶 300 ✓


def test_ratio_lower_bound_is_one():
    """小池子也不许算成 0 只 ✗（0 只 = 空仓 ✓，不是用户要的"细水长流" ✓）。"""
    assert _mk(topk_ratio=0.01)._k_of(12, 12) == 1           # round(0.12)=0 ⇒ 兜底 1 ✓


def test_ratio_is_clamped():
    """比例脏输入（>1 / <0 ✓）⇒ 夹到 [0,1] ✓（不扩大也不为空仓 ✓）。"""
    assert _mk(topk_ratio=1.5)._k_of(100, 100) == 100
    assert _mk(topk_ratio=-0.2)._k_of(100, 100) == 1


def test_zero_pool_or_zero_available_means_no_trade():
    """全池 0 / 可用 0 ⇒ 0 只（策略据此空仓 ✓，不抛错 ✓）。"""
    st = _mk(topk_ratio=0.01)
    assert st._k_of(0, 0) == 0
    assert st._k_of(5400, 0) == 0


# ---------------------------- 固定只数模式 ----------------------------

def test_fixed_count_still_capped_by_available():
    """固定只数不受本次改动影响 ✓：只数照旧，但**封顶**到可用 ✓（拒尾不补 ⇒ 买 38 只 ✓）。"""
    st = _mk(topk=50)
    assert st._k_of(5400, 380) == 50
    assert st._k_of(5400, 38) == 38        # ★ 拒尾后只剩 38 只 ⇒ 就买 38 只 ✓（用户 2026-09-24 决策 ✓）
    assert st._k_of(5400, 5400) == 50
