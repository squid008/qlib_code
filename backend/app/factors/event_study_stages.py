# -*- coding: utf-8 -*-
"""`run_event_study` 的前置阶段（★ v2.11.1 从 `event_study.py` 抽出 ✓，技术债 P1-#3）。

## 为什么抽这两块
`run_event_study` 原本 **326 行**（编排：预热 → 缓存 → 股票池 → 日历 → 取数 → 触发 → 对齐 → 基准），
已在代码体检棘轮里登记为"待拆、只能变小" ✓。这里抽走**入口处的两个独立阶段** ✓：

1. **预热缓冲解析** ✓ —— `None ⇒ 环境变量 ⇒ 默认 250`，与单因子测试同语义 ✓；
2. **内容寻址缓存查表** ✓ —— 指纹 + 落盘读取 + 命中时的返回体装配 ✓。

两块都**不碰面板/行情** ✓，只依赖环境变量与缓存模块 ✓ ⇒ 抽出去零耦合 ✓。

## ⚠⚠ 拆分时必须同步的一件事（很容易漏 ✗）
`event_study_cache.fingerprint()` 里有一段 **算法源码指纹** ✓
（`code_fingerprint("app/factors/event_study.py", "app/factors/event_study_cache.py")` ✓，
v2.1.61 加的：改算法 ⇒ 指纹变 ⇒ 旧缓存自动失效 ✓）。

⇒ **任何从 `event_study.py` 拆出去的新模块，都必须加进那份 `code_fingerprint(...)` 列表** ✗→✓，
否则"改了新模块 ⇒ 缓存**不**失效" ⇒ 新算法**看起来没生效** ✓
（正是这个项目最忌讳的"口径漂移 + 看不见" ✓）。
本模块已在 v2.11.1 同步加入 ✓。
"""
from __future__ import annotations

import os

from ..logger import _log_swallowed  # noqa: E402


def _resolve_warmup(warmup_days) -> int:
    """预热缓冲（交易日）：`None` ⇒ 读 `QLIB_SFT_WARMUP_DAYS` ⇒ 默认 **250** ✓。

    与单因子测试（`single_test`）**同一套语义** ✓ —— 两边不一致会导致
    "同一因子在单因子测试与事件研究里拿到**不同的历史长度**" ✓
    ⇒ 触发样本数不同 ✓，但**没人看得出来** ✗。
    """
    if warmup_days is None:
        try:
            warmup_days = int(os.environ.get("QLIB_SFT_WARMUP_DAYS", "250"))
        except Exception as e:
            _log_swallowed("event_study.py", e)
            warmup_days = 250
    return max(0, int(warmup_days or 0))


def _cache_lookup(params: dict, *, use_cache: bool, cache_mod, prog, timings):
    """内容寻址缓存查表 ⇒ `(指纹, 命中结果或 None)` ✓。

    · `use_cache` 关 ⇒ `(None, None)`（不读写盘 ✓）；
    · 读写缓存**出错一律当"没命中"** ✓（`load` 自己也是这个原则 ✓）——
      缓存是加速器 ✓，它坏了不该让整次事件研究失败 ✗；
    · **命中** ⇒ 装配返回体 ✓：`cached=True` + `timings(cached=True)` ✓ ——
      前端靠这两个字段判断"能秒回就自动跟随周期" ✓，漏了就只能退回手工按钮 ✗。
    """
    if not use_cache:
        return None, None
    try:
        fp = cache_mod.path_for(params)
        hit = cache_mod.load(fp)
    except Exception as e:
        _log_swallowed("event_study.py", e)
        return None, None
    if hit is None:
        return fp, None
    prog(100.0, "缓存命中（同参数上次已算过，直接复用）")
    out = dict(hit)
    out["cached"] = True
    out["timings"] = timings(True)
    return fp, out
