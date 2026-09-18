# -*- coding: utf-8 -*-
"""股票池「逐日成分」掩码 —— 供单因子测试与回测**共用**的纯函数。

⚠ **本模块必须保持"零副作用"**：不得 import 任何会触发 `qlib.init()` 的模块
（尤其不能 import `app.factors.single_test` —— 它在模块级就执行 `_engine_init(...)`，
而 qlib 回测的数据加载走**多进程**，子进程一 import 就会重新 init、覆盖全局 C/D 状态，
表现为多个 PID 同时抛 `RuntimeError`。这是 v1.18.50 首次接入回测时踩的坑，
见 `qlib_engine.py::_ensure_qlib_init` 的既有约定「全局只 init 一次」）。
依赖仅限 numpy / pandas / 本包内的路径工具（且路径工具也延迟 import）。
"""
from typing import Dict, List, Optional, Tuple

import numpy as np
import pandas as pd


def _inst_spans_path(universe: str) -> Optional[str]:
    """qlib instruments 区间文件路径（`<provider_uri>/instruments/<universe>.txt`）。"""
    import os

    from .utils import _default_qlib_uri

    try:
        uri = _default_qlib_uri()
    except Exception:
        return None
    if not uri:
        return None
    p = os.path.join(str(uri), "instruments", "%s.txt" % universe)
    return p if os.path.isfile(p) else None


def _daily_member_mask(universe: str, index: pd.MultiIndex) -> Optional[np.ndarray]:
    """**逐日成分掩码**（v1.18.50 修「股票池未来函数」）：与 `index` 逐行对齐的 bool 数组。

    背景：`D.list_instruments(scope, start_time=...)`（**不传 end_time**）的语义是
    「start 之后**曾属于**该池的全部股票」= 全期并集 —— 实测 csi300 为 459 只，而真实成分
    每天 300 只，2021 年样本里混入 **106 只彼时尚未纳入**的股票 ⇒ 幸存者偏差、系统性高估。

    本函数直接解析 instruments **区间文件**（比逐日调 qlib API 快 ~8.5×：0.2s vs 1.76s），
    用「日 × 股」布尔矩阵按区间 slice 填充，再以 `Index.get_indexer` 向量化取每行掩码。

    返回 `None` 表示**无从判断**（池名无对应文件，如自定义池）→ 调用方应保持旧行为（不筛）。
    `all`（全 A）没有"成分"概念，调用方应跳过（上市前天然无数据，本就不构成偏差）。
    """
    path = _inst_spans_path(universe)
    if path is None:
        return None
    try:
        inst_lv = index.names.index("instrument")
        dt_lv = index.names.index("datetime")
    except ValueError:
        return None

    inst_raw = np.asarray(index.get_level_values(inst_lv))
    dt_raw = np.asarray(index.get_level_values(dt_lv))
    if inst_raw.size == 0:
        return None
    # ⚠⚠ v1.20.18（2026-09-18 profile 定位）：原实现对**每一行**都做
    #   `pd.Timestamp(x).to_datetime64()`（`dt_raw` 可达几十万行）⇒ 实测 **0.91 s** ✗，
    #   是"过顶公式 7.6 s"里最大的单项之一。而面板 index 的 datetime 级别**本来就是
    #   DatetimeIndex** ⇒ 直接 `pd.DatetimeIndex(dt_raw)` 一次转换 + `.unique()` 排序即可 ✓
    #   （语义逐位等价：原来是对每行做同一变换后去重排序 ✓）。
    dt_index = pd.DatetimeIndex(dt_raw)
    uniq_days = dt_index.unique().sort_values()
    days = uniq_days.values.astype("datetime64[ns]")
    # ⚠ 原来的 `days` 走 setcomp + `pd.Timestamp(x)` 逐行转换（0.91 s ✗）；
    #   `insts` 同理但量小（几十万行 → 几百只 ✓）。这里都用 **numpy/Index 向量化** ✓。
    insts = np.asarray(sorted({str(x).upper() for x in inst_raw}))
    inst_pos = {c: i for i, c in enumerate(insts)}

    # 解析区间：code -> [(start, end), ...]（同一代码可有多段：进出池多次）
    with open(path, "r", encoding="utf-8", errors="ignore") as fh:
        spans: Dict[str, List[Tuple[np.datetime64, np.datetime64]]] = {}
        for line in fh:
            p = line.split()
            if len(p) < 3:
                continue
            c = p[0].upper()
            if c not in inst_pos:
                continue
            try:
                s = np.datetime64(pd.Timestamp(p[1][:10]).to_datetime64())
                e = np.datetime64(pd.Timestamp(p[2][:10]).to_datetime64())
            except Exception:
                continue
            spans.setdefault(c, []).append((s, e))

    mat = np.zeros((days.size, insts.size), dtype=bool)
    for c, segs in spans.items():
        j = inst_pos[c]
        for s, e in segs:
            a = int(np.searchsorted(days, s, side="left"))
            b = int(np.searchsorted(days, e, side="right"))
            if b > a:
                mat[a:b, j] = True

    # 向量化映射（v1.18.50）：`Index.get_indexer` 是 C 实现 —— 原先用 Python 生成器逐行
    # 构造「行/列位置」（面板通常 60 万行 × 2 次）实测要 3~5s，是"过滤后反而更慢"的真因。
    d_idx = pd.DatetimeIndex(days)
    c_idx = pd.Index(insts)
    dt_vals = pd.DatetimeIndex(index.get_level_values(dt_lv)).values.astype("datetime64[ns]")
    r = d_idx.get_indexer(dt_vals)
    cc = c_idx.get_indexer(pd.Index(index.get_level_values(inst_lv)).astype(str).str.upper())
    ok = (r >= 0) & (cc >= 0)
    out = np.zeros(r.size, dtype=bool)
    if ok.any():
        out[ok] = mat[r[ok], cc[ok]]
    return out
