# -*- coding: utf-8 -*-
"""信号回灌的**口径解析**（★ v2.11.14 从 `routers/signal_test.py::run` 抽出）。

这两块都是"**跟着导出文件走**"的口径决策 ✓ —— 决定回灌能不能逐位复现当时的回测 ✓。

## ① 复权口径决定用哪张价表（三个用户报障的合成 ✓）
| 版本 | 用户报障 | 真因 |
|---|---|---|
| v2.1.72 | 「**不复权**，这里黄金坑我测下来信号测试还是有问题」 | `load_price_panel` **永远**取后复权 `$close` ✗，文件里记的 `price_adjust` **整条链没人消费** ✗ ⇒ 不复权的事件收益/曲线/锚点全不一样 ✓（前/后复权只差整体缩放 ⇒ 看不出来 ✓） |
| v2.1.74 | 审计 #2 | `price_round` 没进文件 ⇒ 本侧硬编码 `.round(2)` ✗ ⇒ 用户关过它，回灌差一个取整 ✓ |
| v2.1.79 | 用户拍板「要与聚宽对齐」 | 只给事件研究用真实价 ✗ ⇒ 回测/净值仍是后复权 ⇒ **同一次回灌两个口径** ✗ |

⇒ 现状：`price_adjust == "none"` ⇒ 用面板**现成的真实价**（`CLOSE_RAW`/`OPEN_RAW` ✓，停牌 NaN ⇒ **ffill 冻结** ✓）
替换 `CLOSE`/`OPEN`，**并且**灌回 `panel`（引擎/净值/持仓都读它 ✓）；
⚠ `CLOSE_RAW`/`OPEN_RAW` **本身不动** ✓（停牌判定与"真实价 vs 涨跌停价"比对仍按它 ✓）；
⚠ `panel` **先浅拷贝** ✓（`load_price_panel` 的返回可能被别处引用 ✓，就地改会外溢 ✓）。
没有真实价列 ⇒ **退回复权价并写 warning** ✓（不许静默按后复权算 ✗ —— 那正是 v2.1.72 那个 bug ✓）。

## ② 剔除开关的优先级
**请求显式给 > 导出文件里记的 > False** ✓：
· 新文件自带开关 ⇒ 自动对齐弹窗口径 ✓（"导出的 = 复现的" ✓）；
· 老文件没有这些键 ⇒ 退回**不剔除** ✓ = 它当年的口径 ✓（不改动历史结果 ✓）。
"""
from __future__ import annotations

from typing import Dict, Tuple

import pandas as pd

#: 需要"请求 > 文件 > False"归并的剔除开关（顺序即 warnings 里的顺序 ✓）
EXCLUDE_KEYS = (
    "exclude_limit_up_signal", "exclude_limit_up_trade", "exclude_suspended",
    "exclude_st_t1", "exclude_stock_gem", "exclude_stock_kcb",
)


def resolve_price_tables(panel, align) -> Tuple[Dict, Dict[str, pd.DataFrame], list, str, bool]:
    """→ `(panel, 事件研究用的价表, 追加的 warnings, price_adjust, price_round)` ✓（见 docstring ①）。

    ⚠ 后两个（`pa` / `pr`）**必须一起返回** ✗→✓ —— 调用方在后面还有 4 处要用它们
    （写进 diag / 日志 ✓）。我第一版只返回前三项 ⇒ `ruff` 的 **F821 当场点名 4 处** ✗
    （与 v2.11.12 的 `per` 是**同一类"搬家漏依赖"** ✓ —— 这已经是第二次 ✓，记进 change_log）。
    """
    al = align or {}
    pa = str(al.get("price_adjust") or "").strip().lower()
    prv = al.get("price_round")
    pr = True if prv is None else bool(prv)          # 老文件没记 ⇒ 按面板默认 True ✓
    px_es: Dict[str, pd.DataFrame] = {}
    warns: list = []
    if pa != "none":
        return panel, px_es, warns, pa, pr

    cr = panel.get("CLOSE_RAW")
    if cr is None or not len(cr):
        warns.append("文件要求「不复权」，但本次取价没有真实价列 ⇒ 退回复权价 ✗"
                     "（事件研究会与面板有差异 ✓）")
        return panel, px_es, warns, pa, pr

    px_es["CLOSE"] = cr.ffill().round(2) if pr else cr.ffill()
    orr = panel.get("OPEN_RAW")
    if orr is not None and len(orr):
        px_es["OPEN"] = orr.ffill().round(2) if pr else orr.ffill()
    # ⚠ 先浅拷贝再灌回（见 docstring ① 的最后两条 ⚠）
    panel = dict(panel)
    for k, v in px_es.items():
        panel[k] = v
    warns.append("已按导出文件的复权口径取价：**不复权**（真实价%s ✓）"
                 "⇒ **因子/事件研究/回测/净值 全链同口径** ✓（与聚宽「不复权」对齐 ✓）"
                 % ("+ 按分取整" if pr else "（未取整，按文件的 price_round=False ✓）"))
    return panel, px_es, warns, pa, pr


def require_frame(x, msg: str):
    """`ParseResult.trades` / `.signals` 都是 `Optional` ✓ ⇒ 统一"判空 + 400" ✓。

    ⚠ 为什么不就地写 `if x is None: raise` ✗：那个路由函数**已经 500 行** ✓，
    每加 3 行就顶破 `test_code_health` 的棘轮上限（501 ✓，**只减不增** ✗）。
    ⇒ 抽成一行调用 ✓（既判空、又不涨函数体 ✓）。
    ⭐ 顺带把"用户看不懂的 `NoneType has no attribute 'min'`"换成**能照做的**中文 ✓。
    """
    if x is None or not len(x):
        from fastapi import HTTPException

        raise HTTPException(status_code=400, detail=msg)
    return x


def build_only_perf_block(res, nav_rows, elapsed: float) -> Dict:
    """模式③（只上传《收益概述》⇒ 只画官方净值 ✓，**无需行情** ✓ 秒出）的响应块 ✓。

    ## 这条路径为什么值得单独成函数
    它是三模式里**唯一不取行情**的 ✓（只把 CSV 里的逐日净值画出来 ✓）⇒
    **能起一个端到端测试** ✓（`POST /api/signal-test/run` 传一份收益概述即可 ✓，
    不用数据集 ✓）。而另外两个模式要真行情 ✗ ⇒ 测不动 ✓。

    ⚠ `bench_nav` **可能整列为空** ✓（用户只上传策略收益 ✓）⇒ 只在 `notna().any()` 时才加基准列 ✓；
    无条件加会让前端画出一条**全 NaN 的基准线** ✓（图例里有、曲线上没有 ✗）。
    """
    pf = res.perf
    nav = pf.set_index("date")[["nav"]].rename(columns={"nav": "nav_official"})
    if pf["bench_nav"].notna().any():
        nav["nav_official_bench"] = pf.set_index("date")["bench_nav"]
    return {
        "official": {"stats": res.stats, "issues": res.issues[:50]},
        "replay": {"stats": {}, "diag": {"mode": "only_perf",
                                         "note": "只有官方净值（未提供成交明细）"},
                   "nav": nav_rows(nav)},
        "elapsed": round(elapsed, 3),
    }


def resolve_exclude_flags(req, align) -> Dict[str, bool]:
    """→ `{开关: bool}` ✓（优先级见模块 docstring ②）。"""
    al = align or {}
    out: Dict[str, bool] = {}
    for k in EXCLUDE_KEYS:
        v = getattr(req, k, None)
        if v is None:
            v = al.get(k)
        out[k] = bool(v)
    return out
