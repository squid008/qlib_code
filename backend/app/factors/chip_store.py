# -*- coding: utf-8 -*-
"""把筹码派生字段（`$chip_*`）**物化成真正的字段文件**（v1.19.85）。

## 为什么必须物化
`$chip_cost_95` / `$chip_win_close` 原本只在**面板求值器**（`panel_expr.PanelEvaluator.field`）
里"按需计算"。但公式还可能走**另一条路**：

- 单因子测试 / 事件研究 ⇒ 走面板求值器 ✓（能算 ✓）
- **回测 / 训练的特征加载**（qlib `D.features` / `handler.py` 的 feature config）⇒ 走 **qlib** ✗
  ⇒ qlib 把 `$chip_cost_95` 当普通字段去读 `features/<inst>/chip_cost_95.day.bin`，
  文件不存在 ⇒ 返回**长度 0 的序列** ⇒ 报
  `np.greater(series_left, series_right) ... (6337, 0)` / `Can only compare identically-labeled Series objects`
  （用户 2026-09-17 实测：黏合强突破公式在回测里就撞这个）。

⇒ 解决：**一次性算好并落成 `.day.bin`**（格式与其它字段一致：`[起始日历下标(float32), 值…]`，
停牌日为 NaN、按日历连续）⇒ 之后两条路都能像读普通字段一样用它 ✓，且**零额外计算** ✓。

## 口径
- 与面板派生字段**同一份计算**（直接复用它：`PanelEvaluator.field("$chip_…")`）⇒ 两条路数值一致 ✓；
- `read_start`（预热起点）可以早于 `start_time`：筹码是**递推**状态，起点越早越准
  （落盘只覆盖 `[start_time, end_time]`，但状态从 `read_start` 起算）。
- **只在文件不存在时写入**（不覆盖任何既有字段；`chip_*` 是本项目自造名，安全）。
"""
from __future__ import annotations

from typing import Dict, Iterable, List, Optional, Sequence

import numpy as np
import pandas as pd

from .panel_expr import _CHIP_BASE_FIELDS, PanelEvaluator, _calendar, chip_turn_of

# 默认物化清单（覆盖用户公式用到的档位；要更多档位加进这个元组即可）
DEFAULT_FIELDS = ("chip_cost_5", "chip_cost_30", "chip_cost_75", "chip_cost_95",
                  "chip_win_close", "chip_win_high", "chip_win_low")


def _write_bin(path, first_idx: int, vals: np.ndarray) -> None:
    """写一个字段文件：首 4 字节 float32 = 起始日历下标，其后是 float32 值序列。

    与 `panel_expr._read_field_bin` 的约定**逐位对应**（该函数用 `<f4` 读首值再 int() 还原下标）。
    """
    head = np.asarray([float(first_idx)], dtype="<f4")
    body = np.asarray(vals, dtype="<f4")
    with open(path, "wb") as f:
        head.tofile(f)
        body.tofile(f)


def materialize(codes: Sequence[str], start_time: str, end_time: str,
                fields: Iterable[str] = DEFAULT_FIELDS,
                read_start: Optional[str] = None,
                overwrite: bool = False, progress_cb=None) -> Dict[str, int]:
    """把筹码字段落成 `.day.bin`（返回 `{字段: 写入股票数}`）。

    `read_start`：预热起点（建议比 `start_time` 早 ≥1 年，让筹码递推先"跑起来"）。
    `overwrite=False`：已存在的文件跳过（安全默认）。
    """
    from ..config import QLIB_PROVIDER_URI
    fdir = str(QLIB_PROVIDER_URI).rstrip("/\\") + "/features"
    want: List[str] = [f for f in fields]
    ev = PanelEvaluator(list(codes), start_time, end_time,
                        union_fields=_CHIP_BASE_FIELDS, read_start=read_start or start_time)
    cal = _calendar()
    out: Dict[str, int] = {}

    # ---- 一次递推产出全部字段（v1.19.85 性能）----
    #   原实现按字段逐个 `ev.field("$chip_…")` ⇒ 每个字段各自重跑一遍**全池递推**
    #   （实测 7 个字段 ≈ 40 分钟 ✗）。这里直接调内核：多分位一次出（`qs=`）、
    #   多价位一次出（`prices=[…]`）⇒ 2 次递推搞定 7 个字段 ✓。
    # ⚠ 记得留住**面板**（两级 index）的名字：`unstack` 之后只剩一级，写盘前要把
    #   名字换回 (instrument, datetime) ⇒ 早期误用 `c.index.names[1]` 直接 IndexError（已修）
    _panel_close = ev.field("$close")
    _pnames = list(_panel_close.index.names)
    c = _panel_close.unstack(level=0)
    h = ev.field("$high").unstack(level=0)
    l = ev.field("$low").unstack(level=0)
    t = chip_turn_of(ev).unstack(level=0)        # ★ v1.19.92：与面板**同一口径**（见下）
    from .chip_dist import chip_run
    qs = [float(n[len("chip_cost_"):]) for n in want if n.startswith("chip_cost_")]
    wins = [n[len("chip_win_"):] for n in want if n.startswith("chip_win_")]
    series_map: Dict[str, "pd.Series"] = {}
    if qs:
        res = chip_run(c.to_numpy(float), h.to_numpy(float), l.to_numpy(float),
                       t.to_numpy(float), qs=tuple(qs))
        for q in qs:
            series_map["chip_cost_%g" % q] = pd.DataFrame(
                res["cost_%g" % q], index=c.index, columns=c.columns).stack()
    if wins:
        pmat = [{"close": c, "high": h, "low": l}[w].to_numpy(float) for w in wins]
        res = chip_run(c.to_numpy(float), h.to_numpy(float), l.to_numpy(float),
                       t.to_numpy(float), prices=pmat)
        for i, w in enumerate(wins):
            key = "winner" if len(pmat) == 1 else "winner_%d" % i
            series_map["chip_win_%s" % w] = pd.DataFrame(
                res[key], index=c.index, columns=c.columns).stack()

    for name in want:
        # ⚠⚠ v1.19.92 **不要在这里批量删旧文件**：本机有 bulk-delete 安全闸 ——
        #   单次删除 ≥500 个会被拦下并**直接中断整个进程**（实测 2026-09-17 22:08 那次就死在这 ✗，
        #   stderr 里只有一行 `[safe-delete][SAFE_DELETE_BULK_CONFIRM_REQUIRED] {"count":500,…}`，
        #   而 stdout 停在启动三行 ⇒ 看起来"无声消失"，极易误判成被重启脚本杀了 ✗）。
        #   ⇒ 改为**"每只股票都写字（含全 NaN）"**：旧文件被自然覆盖 ✓，且全市场都有该字段、
        #     日期轴与 `$close` 一致 ⇒ 多股票加载不再缺字段 ✓✓（见下方 `if not len(sub)` 的注释）。
        st = series_map.get(name)
        if st is None:
            out[name] = 0
            continue
        # 面板 index 是 (instrument, datetime)；stack 出来是 (日期, 股票) ⇒ 换回面板顺序
        st.index = st.index.set_names([_pnames[1], _pnames[0]])
        # ⚠⚠ `stack()` 会**丢掉全 NaN 行** ⇒ 直接用会写出"日期不连续"的 bin（读取端按
        #   "起始下标 + 行号" 还原 ⇒ 整体错位；实测表现为 `Gt($close,$chip_cost_95)`
        #   报 "Can only compare identically-labeled Series objects"，v1.19.85 抓到）
        #   ⇒ 必须 reindex 回**面板的完整 index**（每只股票全日历、缺失为 NaN）✓
        series = st.reorder_levels(_pnames).reindex(_panel_close.index)
        n_ok = 0
        for inst in codes:
            try:
                sub = series.xs(inst, level="instrument")
            except KeyError:
                continue
            if not len(sub):
                continue
            # ★ v1.19.92：**不再跳过"全 NaN"的股票** —— 跳过会让它留着**旧文件**（旧日期轴 ✗），
            #   以及"这只股票没有该字段"⇒ 多股票一起加载时崩（`identically-labeled` ✗）。
            #   这里一律写出（全 NaN 也写 ✓），日期轴与 `$close` 完全一致 ⇒ qlib 侧永远能对齐 ✓。
            path = "%s/%s/%s.day.bin" % (fdir, inst.lower(), name)
            import os
            if os.path.exists(path) and not overwrite:
                continue
            os.makedirs(os.path.dirname(path), exist_ok=True)
            # ★★ v1.19.91：写盘的**日期轴必须与 `$close` 逐位一致**（起点 offset + 行数）。
            #   为什么（用户 2026-09-17 报：黏合强突破 / 过顶0 / 过顶 单因子测试
            #   「Can only compare identically-labeled Series objects … Gt($close,$chip_cost_95)」）：
            #   面板的 index 是 `[start_time, end_time]`（我们物化时 start=2010-01-01），
            #   而 **`$close` 的 bin 从"每只股票自己的上市起点"开始** ⇒ 两个字段的日期轴不同 ✗。
            #   · 只加载**一只**股票时看不出来（qlib 用该股自己的区间，两边被对齐到同一轴 ✓）；
            #   · **多只一起加载**时 qlib 按"多股票联合日历"对齐 ⇒ 标签不一致 ⇒ 直接报错 ✗✗
            #     （这就是"单只 OK、批量崩"的原因 ✓）。
            #   ⇒ 直接读 `$close` 的 header（首 4 字节 = 起始日历下标）与行数，把筹码值 reindex 到
            #     **同一条日期轴**上（超出面板窗口的段落为 NaN ✓）。
            close_path = "%s/%s/close.day.bin" % (fdir, inst.lower())
            if os.path.exists(close_path):
                with open(close_path, "rb") as fh:
                    first = int(np.frombuffer(fh.read(4), dtype="<f4")[0])
                n_ref = max(0, (os.path.getsize(close_path) - 4) // 4)
                sub = sub.reindex(cal[first:first + n_ref])
            else:                                     # 极端情况：没有 close 就退回旧口径
                first = int(cal.searchsorted(sub.index[0]))
            _write_bin(path, first, sub.to_numpy(dtype=float))
            n_ok += 1
        out[name] = n_ok
        if progress_cb:
            progress_cb("物化 %s：%d 只" % (name, n_ok))
    return out
