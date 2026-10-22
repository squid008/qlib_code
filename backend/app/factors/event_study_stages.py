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

import pandas as pd

from ..engine.limits import mark_limit_up  # noqa: E402
from ..logger import _log_swallowed  # noqa: E402
# ⚠ 必须从 **`engine/limits.py`**（真正出处 ✓）取 `mark_limit_up`，
#   而**不能** `from .event_study import mark_limit_up` ✗ ——
#   后者会造成 **`event_study` ↔ `event_study_stages` 循环引用**
#   （`event_study` 顶层 import 本模块 ✓）⇒ 导入期直接炸 ✗。


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


def _build_triggers(df, *, exclude_limit_up_signal: bool, exclude_limit_up_trade: bool,
                    exclude_suspended: bool, exclude_st_t1: bool = False,
                    exclude_stock_gem: bool = False, exclude_stock_kcb: bool = False):
    """从因子面板提 0/1 触发样本并剔除 ⇒ `(trig, ev, n_raw, inst_pos, dt_pos, 错误文案)` ✓。

    剔除口径**与 `single_test._test_one._exclude` 完全一致** ✓
    （信号日涨停 / 成交日涨停 / T+1 停牌 / T+1 ST / 创业板 / 科创板 ✓）——
    两条链口径不一致的话，"同一因子在单因子测试与事件研究里样本数不同"✓，
    而这种差异**没有任何地方会报错** ✗。

    ⚠ 两个"空样本"必须给出**不同的**文案 ✓（源码原样保留）：
    · 剔除前就空 ⇒ 因子在区间内根本没触发（**可能是表达式写错** ✓）；
    · 剔除后空 ⇒ 触发了但全被涨停/停牌/ST/板块过滤掉（**是过滤太严** ✓）。
    合成一句话的话，用户会照着错的方向排查 ✗。
    """
    from .single_test import _inst_codes       # 懒导入：避免 `single_test` ↔ 本模块的循环引用 ✓

    sub = df[df["F0"].notna()]
    trig = sub[sub["F0"] > 0.5]
    if len(trig) == 0:
        return None, None, 0, 0, 0, "该因子在区间内没有触发样本（非 0/1 信号或条件从未满足）"

    def _exclude(g):
        if len(g) == 0:
            return g
        if exclude_limit_up_signal and "CLOSE" in df.columns and "CHANGE" in df.columns:
            g = g[~mark_limit_up(df.loc[g.index], "CLOSE", "CHANGE")]
        if exclude_limit_up_trade and "T1_CLOSE" in df.columns and "T1_CHANGE" in df.columns:
            g = g[~mark_limit_up(df.loc[g.index], "T1_CLOSE", "T1_CHANGE")]
        if exclude_suspended and "T1_CLOSE" in df.columns:
            g = g[~df.loc[g.index, "T1_CLOSE"].isna()]
        if (exclude_st_t1 or exclude_stock_gem or exclude_stock_kcb) and len(g):
            codes = _inst_codes(df.loc[g.index])
            keep = pd.Series(True, index=g.index)
            if exclude_st_t1 and "T1_IS_ST" in df.columns:
                keep &= ~(df.loc[g.index, "T1_IS_ST"] > 0.5)
            if exclude_stock_gem:
                keep &= ~codes.str.startswith("SZ30")
            if exclude_stock_kcb:
                keep &= ~codes.str.startswith("SH688")
            g = g[keep]
        return g

    n_raw = int(len(trig))
    trig = _exclude(trig)
    if len(trig) == 0:
        return None, None, n_raw, 0, 0, "触发样本在剔除后为空（全部落在涨停/停牌/ST/板块过滤内）"

    inst_pos = trig.index.names.index("instrument")
    dt_pos = trig.index.names.index("datetime")
    ev = pd.DataFrame({
        "code": trig.index.get_level_values(inst_pos).astype(str),
        "dt": pd.to_datetime(trig.index.get_level_values(dt_pos)),
    }).reset_index(drop=True)
    return trig, ev, n_raw, inst_pos, dt_pos, None


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
