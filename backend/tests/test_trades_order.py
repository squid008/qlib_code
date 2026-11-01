# -*- coding: utf-8 -*-
"""成交明细**行序确定性**（技术债 P2-#21 的回归钉子）。

## 为什么单独钉这两条
v2.11.18 的跨平台指纹发现：**同一策略、同一份数据，Windows 与 ubuntu 的成交明细行序不同** ✗
（NAV 逐位相同、行序无关哈希也相同 ⇒ 内容一样、只是顺序 ✗）。
影响是**用户可见**的：导出的 XLSX「成交明细」页在不同机器上行序不同 ⇒
"逐位对比两份导出文件"会失败 ✗（对账/回归都受影响 ✓）。

v2.11.21 的修法：`_trades_frame` 末尾 `sort_values(by=全部列, kind="stable")` ✓。

⚠ 这里钉的是**行为**，不是实现细节：只要"两种追加顺序 ⇒ 输出完全相同"成立 ✓，
将来换排序实现也不会误报 ✓。

> 2026-11-01 备注：复查技术债时我曾把它误判为"部分清偿"（只看过期的状态行、没读代码 ✗），
> 实测后才改回 ✓ —— 这两条测试就是防止再次误判 ✓。
"""
from __future__ import annotations

import numpy as np

from app.signals.engine import _TRADE_KEYS, _trades_frame

DATES = np.asarray(["2024-01-02", "2024-01-03", "2024-01-04"])
CODES = ["SH600000", "SZ000001"]
HALF = 0.002


def _rows():
    """4 笔成交（元组形态）：买/卖、不同日、不同标的、不同金额 ✓。"""
    return [
        (0, 0, 1, 100, 10.0, "r1", "cash_even"),
        (1, 1, -1, 100, 11.0, "r2", "cash_even"),
        (2, 0, 1, 200, 10.5, "r3", "cash_even"),
        (0, 1, 1, 100, 20.0, "r4", "cash_even"),
    ]


class TestOrderIsDeterministic:
    def test_reversed_insertion_gives_identical_frame(self):
        """⭐⭐ **追加顺序反过来 ⇒ 输出完全相同**（行序 + 数值 + dtypes）。"""
        a = _trades_frame(_rows(), DATES, CODES, HALF)
        b = _trades_frame(list(reversed(_rows())), DATES, CODES, HALF)
        assert a.equals(b), "行序依赖追加顺序 ⇒ 跨平台会不同 ✗"

    def test_sorted_by_full_row(self):
        """⭐ 排序键是**全部列** ⇒ 结果本身有序（date 升序、同日内按 code ✓）。"""
        f = _trades_frame(_rows(), DATES, CODES, HALF)
        keys = list(zip(*[f[k].tolist() for k in _TRADE_KEYS]))
        assert keys == sorted(keys), "输出没有按全列排序 ✗"

    def test_duplicate_rows_have_no_effect(self):
        """完全相同的两笔成交 ⇒ 顺序互换无意义（行本身一样 ✓），且总行数正确 ✓。"""
        rows = _rows()
        f1 = _trades_frame(rows, DATES, CODES, HALF)
        f2 = _trades_frame(rows + [rows[0]], DATES, CODES, HALF)
        assert len(f1) == 4
        assert len(f2) == 5

    def test_empty_stays_0x0(self):
        """空输入保持 `pd.DataFrame()`（0×0 ✓ —— 不是 0×9 ✗）。"""
        f = _trades_frame([], DATES, CODES, HALF)
        assert f.shape == (0, 0)

    def test_batch_row_survives_and_no_crash(self):
        """`("__ROW__", dict)`（`batch_even` 的已格式化行 ✓）要被并进同一张表且**不抛** ✓。

        ⚠ 这里**不断言排序结果** ✗ —— 这种混装（`shares` 里既有 int 又有占位值）
        比大小没有确定语义 ✓；只钉"不丢行、不抛" ✓。
        ⚠ 我第一版写成 `assert ... or True` ✗ —— 真空断言（永真），已删 ✓。
        """
        rows = _rows()[:2] + [("__ROW__", {k: "X" for k in _TRADE_KEYS})]
        f = _trades_frame(rows, DATES, CODES, HALF)
        assert len(f) == 3, "`__ROW__` 那行被丢了 ✗"
        assert "X" in set(f["code"]), f["code"].tolist()
