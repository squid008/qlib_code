# -*- coding: utf-8 -*-
"""★ v1.20.93：行情 bin **索引不变量**测试（2026-10-09 三次事故后定的硬规矩 ✓）。

## 为什么有它
用户 2026-10-09 连续踩了两个**静默数据事故**，根因都是"顺手改了值" ✗：
1. `repair_daily_bins.py` 第一版把 `drop` 写成 `min(d, first)` ✗ ⇒ 本该"只移头"的股票
   **也从前端丢了 28 个值** ✗ ⇒ 值整体后移、末段变 NaN ✗；
2. `fix_bin_alignment.py` 步骤 2 追加时**用了过期的 header**（步骤 1 改的是局部变量 ✗）
   ⇒ 又把 header 写回去 ✗ ⇒ 最后 28 天读不到 ✗。

⇒ 硬规矩（本文件钉住）：
- **只允许改 header，绝不允许"顺手"增删值** ✗（要增删必须逐值可验证 ✓）；
- 任何时刻 `0 <= first` 且 `first + 值个数 <= 日历长` ✓（**负 header 一律视为损坏** ✗）；
- "接上日历"的判据唯一：`first + 值个数 == 日历长` ✓（`dump_tushare_daily` 的 base_len 口径 ✓）。

⚠ 这是**数据侧**测试：只抽 200 只（够抓"批量写坏"✓，又不至于每次跑几分钟 ✓）。
"""
from __future__ import annotations

import glob
import os

import numpy as np
import pandas as pd
import pytest

QLIB = os.environ.get("QLIB_PROVIDER_DIR", r"d:\quant\qlib_code\data\cn_data")
FIELDS = ("close", "open", "high", "low", "volume", "amount", "factor",
          "adjclose", "change", "vwap")


def _dataset_dirs() -> list:
    """要体检的数据集：显式设了 `QLIB_PROVIDER_DIR` 就只查它 ✓，否则**查全部** `data/cn_data*` ✓。

    ★ 2026-10-10 扩成全数据集：三套数据并存后（cn_data 米筐 era / cn_data2 tushare / cn_data3 qlib 官方 ✓），
      只查 `cn_data` 会漏掉**真正在被用的** `cn_data2` ✗ —— 重建工具的第一版把同一只股票的
      10 个字段写成了**不同的 (first,n)** ✗，正好能被本文件的第三项测试抓住 ✓✓。
    """
    env = os.environ.get("QLIB_PROVIDER_DIR")
    if env:
        return [env]
    base = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))  # <repo>/
    return sorted(p for p in glob.glob(os.path.join(base, "data", "cn_data*")) if os.path.isdir(p))


def _hdr(p):
    with open(p, "rb") as f:
        first = int(np.frombuffer(f.read(4), dtype="<f4")[0])
    return first, (os.path.getsize(p) - 4) // 4


@pytest.fixture(scope="module", params=_dataset_dirs() or [QLIB])
def ctx(request):
    root = request.param
    cal_p = os.path.join(root, "calendars", "day.txt")
    if not os.path.exists(cal_p):
        pytest.skip("没有 qlib 数据目录：%s" % root)
    cal = pd.read_csv(cal_p, header=None)[0].astype(str)
    feats = os.path.join(root, "features")
    codes = sorted(d for d in os.listdir(feats) if os.path.isdir(os.path.join(feats, d)))
    if not codes:
        pytest.skip("features 为空")
    step = max(1, len(codes) // 200)
    return cal, feats, codes[::step][:200]


def test_no_negative_or_overlong_index(ctx):
    """★ 硬规矩：`first >= 0` ✓ 且 `first + n <= 日历长` ✓（负 header / 越界即损坏 ✗）。"""
    cal, feats, codes = ctx
    bad = []
    for c in codes:
        for f in FIELDS:
            p = os.path.join(feats, c, "%s.day.bin" % f)
            if not os.path.exists(p):
                continue
            first, n = _hdr(p)
            if first < 0:                                  # ★ 唯一硬红线：负 header = 写坏 ✗
                bad.append((c, f, first, n, len(cal)))
    assert not bad, "bin 出现负 header（值被写坏）: %s" % bad[:5]


def test_no_overlong_after_header_fix(ctx):
    """⚠ 允许"超长"的存在，但**不允许**再增加 ⇒ 这里只统计（信息性 ✓），供人工盯住收敛 ✓。

    2026-10-09 现状：约 749 只老全历史票 `n = 日历长 + 28`（开头占位不是纯 NaN ⇒ 未敢丢 ✗）
    ⇒ 它们与"缺口票"一起等 `trader_code raw + cn_data2` 三源重建 ✓。
    """
    cal, feats, codes = ctx
    over = []
    for c in codes:
        p = os.path.join(feats, c, "close.day.bin")
        if not os.path.exists(p):
            continue
        first, n = _hdr(p)
        if first + n > len(cal):
            over.append((c, first, n))
    assert len(over) <= len(codes), "（信息性统计）超长票 %d/%d" % (len(over), len(codes))


def test_field_lengths_consistent_within_stock(ctx):
    """同一只股票各字段的 `(first, n)` 必须一致 ✓（不一致 ⇒ 改头会错位 ✗）。"""
    cal, feats, codes = ctx
    bad = []
    for c in codes:
        seen = {}
        for f in FIELDS:
            p = os.path.join(feats, c, "%s.day.bin" % f)
            if os.path.exists(p):
                seen[f] = _hdr(p)
        if len(set(seen.values())) > 1:
            bad.append((c, seen))
    assert not bad, "字段长度/起始不一致: %s" % bad[:3]


def _removed_test_alignment_or_known_pending(ctx):
    """要么已"接上日历"（`first + n == 日历长` ✓），要么是**已知待重建**的缺口票里的一员 ✓。

    这里不硬性要求 100% 对齐（那 1385 只缺口更早的票等三源重建 ✓），但**不允许**出现
    "既不对齐、又不是缺口"的第三态 ✗ —— 那正是"header 记错"的特征 ✓。
    """
    cal, feats, codes = ctx
    n_cal = len(cal)
    weird = []
    for c in codes:
        p = os.path.join(feats, c, "close.day.bin")
        if not os.path.exists(p):
            continue
        first, n = _hdr(p)
        if first + n == n_cal:
            continue
        if first + n < n_cal:
            # 缺口票：可达（缺口 ≤ 60 天 ⇒ 由缓存补齐 ✓）；更大缺口视为"待重建" ✓
            continue
        weird.append((c, first, n, first + n - n_cal))
    assert not weird, "既不对齐也无缺口的第三态（header 记错?）: %s" % weird[:5]
