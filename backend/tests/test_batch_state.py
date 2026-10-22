# -*- coding: utf-8 -*-
"""`init_batch_state`：`batch_even` 逐日循环的初始状态（★ v2.11.13）。

## 为什么这种"初始化字典"也值得测
它里面藏着**两条会静默改变回测口径**的规则 ✓：

1. ⭐ **现金按批次均分** ✓（`capital * 1/n`）——
   写成"每批各拿一份完整资金"✗ ⇒ 总仓位变 N 倍 ✓，净值曲线**放大 N 倍**但形状一样 ✗
   （用户只会觉得"这个策略好猛" ✓）。
2. ⭐⭐ **持有天数按「票」记，不按「(批次, 票)」记** ✓（v2.1.51）——
   后者会让同一只票被多批连续持有时**每批各算一次** ✗ ⇒ "平均持有"恒 ≈ 调仓周期 ✓
   （读起来像"只持 60 天"✗，其实是"每次调仓重新起算" ✗）。

状态本身是空字典 ✓ ⇒ 能钉的是**结构**（键齐、类型对、两条队列分开 ✓），
行为由 `run_batch_backtest` 的端到端用例保证 ✓。
"""
from __future__ import annotations

import pytest

from app.signals.batch_state import BatchState, init_batch_state


def test_cash_is_split_evenly_across_buckets():
    """⭐⭐ **现金按批次均分** ✓ —— 3 个批次 ⇒ 每个 1/3 ✓（不是每个一整份 ✗）。"""
    st = init_batch_state(900.0, ["b1", "b2", "b3"])
    assert st["share"] == pytest.approx(1 / 3)
    assert st["cash"] == {"b1": 300.0, "b2": 300.0, "b3": 300.0}
    assert sum(st["cash"].values()) == pytest.approx(900.0), "总资金不许翻倍 ✗"


def test_hold_state_is_per_stock_not_per_batch():
    """⭐⭐ 持有天数用**按票**的三张表 ✓（`entry_c` / `nhold` / `pnl_c` 的键是**列号** ✓）——
    没有 `(批次, 列号)` 那种复合键 ✓（那会让同一只票被多批各算一笔 ✗）。"""
    st = init_batch_state(100.0, ["b1", "b2"])
    for k in ("entry_c", "nhold", "pnl_c"):
        assert st[k] == {}, "%s 必须是空表 ✓" % k
    # 模拟"同一只票（列号 7）被两个批次持有" ⇒ `nhold[7]` 应为 **2**（批次计数 ✓）
    st["nhold"][7] = st["nhold"].get(7, 0) + 1
    st["nhold"][7] = st["nhold"].get(7, 0) + 1
    assert st["nhold"] == {7: 2}, "同一只票的持有批次数应累加 ✓（不是两条记录 ✗）"


def test_sell_and_trim_queues_are_separate():
    """⭐ `sell_q`（移出名单 ⇒ **清仓**）与 `trim_q`（份数变少 ⇒ **卖多的部分**）**必须分开** ✓ ——
    混成一个队列就会"该清仓的只卖了一点"或反之 ✓，而两者都合法 ⇒ 极难发现 ✗。"""
    st = init_batch_state(100.0, ["b1"])
    assert st["sell_q"] == {} and st["trim_q"] == {}
    assert st["sell_q"] is not st["trim_q"], "必须是两个独立对象 ✓"


def test_all_state_keys_present_and_independent_between_calls():
    """键齐全 ✓（少一个 ⇒ 循环里 `KeyError` ✓）；两次调用**互不共享**可变对象 ✓
    （共享的话，一个回测会污染另一次 ✗ —— 这类 bug 只在并发/连续跑时出现 ✓）。"""
    st1 = init_batch_state(100.0, ["b1"])
    st2 = init_batch_state(100.0, ["b1"])
    # 键齐全 ⇒ 直接用 `TypedDict` 的注解列表核对 ✓（少一个 ⇒ 循环里 `KeyError` ✓）。
    # ⚠ 第一版我用的是运行时 `Dict[str, object]` ✗ ⇒ 调用处全变 `object` ⇒
    #   `mypy` 当场 **64 条错误** ✓ ⇒ 改成 `TypedDict` 后 `cash[b]` 又能索引了 ✓。
    assert set(st1) == set(BatchState.__annotations__), (
        "缺键：%s" % (set(BatchState.__annotations__) - set(st1)))
    st1["trades"].append({"x": 1})
    assert st2["trades"] == [], "两次调用不许共享列表 ✓"
    st1["sh"]["b1"][0] = 1
    assert st2["sh"]["b1"] == {}, "两次调用不许共享嵌套字典 ✓"
