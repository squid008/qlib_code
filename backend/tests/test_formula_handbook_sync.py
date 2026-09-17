# -*- coding: utf-8 -*-
"""手册同步守卫：后端支持的函数，必须在**前端公式手册**里有条目（v1.19.86）。

用户 2026-09-17：「插入函数-公式手册里是不是要把新增的函数加进去，比如 COST/WINNER」——
查实 `COST`/`WINNER`（v1.19.83 加的筹码函数）**只在后端**、手册里没有 ✗
⇒ 用户搜不到它们的用法（这类"后端加了、手册忘了"的漂移，靠人记是靠不住的）⇒ 用测试守住 ✓。

规则：
- `BUILTIN_FUNCS` 里的函数 ⇒ 要么在前端手册里有条目，要么**显式**登记在下面的 `KNOWN_GAP`
  （"先加接口、后补文档"的情况留个明账 ⇒ 谁看到都能接着补，而不是静默漏掉）；
- 反过来，手册里有的名字若**不在** `BUILTIN_FUNCS` 里，也应能解释（本测试只校验函数一侧，
  字段名如 CLOSE / VWAP 不在 `BUILTIN_FUNCS` 里，属正常）。
"""
import os
import re

import pytest

from app.factors.parser.semantic import BUILTIN_FUNCS

_HANDBOOK = os.path.abspath(
    os.path.join(os.path.dirname(__file__), "..", "..", "frontend", "src", "formulaHandbook.ts")
)

# 后端有、手册**暂不**写条目的函数（补好手册后从这里删掉 ⇒ 测试会提醒你）
KNOWN_GAP = {
    # Level2：只有语法、**没有数据**（真实成交队列未接入）⇒ 写进手册会误导用户以为能用
    "BIGORDER", "ORDER", "ORDERAMT", "ORDERNUM", "ORDERNWP", "ORDERVOL",
    "TRANSACTNUM", "TRANSACTVOL", "ALLASKVOL", "ALLBIDVOL",
    # 已有函数的**别名/同义写法**（手册只在主名条目里说明别名，避免重复条目）
    "SIGN",      # SGN 的别名（SGN 条目里已写「别名 SIGN」）
    "POWER",     # POW 的别名
    "LN",        # LOG 的别名（LOG 条目里已写「别名 LN」）
    "ALL", "ANY", "LAST", "RANGE", "LONGCROSS",
    # ⚠⚠ **白名单里有名字、但 codegen 根本没映射 ⇒ 现在写进公式只会报「不支持的函数」**
    #   （2026-09-17 核实：这 12 个在 `semantic.BUILTIN_FUNCS` 里出现，但 `codegen.FUNC_QLIB`
    #     及 codegen 的任何分支里都没有 ⇒ 是"承诺了却没实现"✗）
    #   ⇒ 手册**故意不写**它们（写了就是骗用户）；实测/补实现见 md/开发记录.md §9.27。
    "AVEDEV", "DEVSQ", "STDP", "VARP",     # 统计类
    "MEMA", "DMA", "FORCAST", "HHVALL", "LLVALL",   # 通达信均线/回归/全历史
    "FLOOR", "CEILING", "EXP",             # 取整/指数
}


def _handbook_names() -> set:
    if not os.path.exists(_HANDBOOK):
        pytest.skip("前端手册文件不存在（CI 只跑后端时可以跳过）")
    with open(_HANDBOOK, "r", encoding="utf-8") as f:
        ts = f.read()
    return set(re.findall(r"name: '([A-Za-z0-9_]+)'", ts))


def test_handbook_file_is_findable():
    assert os.path.exists(_HANDBOOK), "手册文件路径变了 ⇒ 本测试要同步改"


def test_every_backend_func_is_documented_or_declared():
    """后端每个函数都要"要么写进手册、要么登记为已知缺口"。"""
    names = _handbook_names()
    miss = sorted(BUILTIN_FUNCS - names - KNOWN_GAP)
    assert not miss, (
        "这些函数在后端可用、但前端公式手册里没有条目，也没登记到 KNOWN_GAP：\n  "
        + "、".join(miss)
        + "\n⇒ 请在 frontend/src/formulaHandbook.ts 补条目（用户靠它查用法）；"
        + "确属「暂无数据 / 别名」的，就加到本文件的 KNOWN_GAP 并写明理由。"
    )


def test_known_gap_is_not_stale():
    """`KNOWN_GAP` 里不该留着**已经补好手册**的名字（否则守卫会越来越松）。"""
    names = _handbook_names()
    stale = sorted(n for n in KNOWN_GAP if n in names)
    assert not stale, f"这些已在手册里 ⇒ 请从 KNOWN_GAP 删掉：{'、'.join(stale)}"


def test_chip_funcs_are_documented():
    """★ 用户 2026-09-17 点名的两个（筹码函数）必须有手册条目。"""
    names = _handbook_names()
    assert {"COST", "WINNER"} <= names, "COST / WINNER 必须写进手册（用户点名要的）"
