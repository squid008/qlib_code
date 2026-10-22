# -*- coding: utf-8 -*-
"""`single_factor_test` 的**入参校验与归一化**（★ v2.11.3 从 `routers/factors_single.py` 抽出 ✓）。

## 为什么单独一块
这一段（77 行 ✓）**不碰数据、不开线程** ✓，只做三件事：判空 → 范围校验 → 归一化 ✓
⇒ 抽出去以后可以**直接单测每一条 400 文案** ✓（以前只能通过 HTTP 端到端测 ✓，慢且易漏）。

## 这些文案为什么值得单独钉
它们是**用户能看到的唯一线索** ✓。写错的代价不是崩，而是**把人引向错误的方向** ✗：
· 「预测周期需在 1~250 之间」vs「必须为正整数」—— 前者是**范围**、后者是**类型** ✓，
  合成一句的话，用户把 `abc` 改小一点还是过不去 ✓；
· 「分位组数需在 2~20 之间」—— 少于 2 组就没有"分组"可言 ✓；
· 「明细曲线最多点选 12 个 K」—— 12 = 默认档 + 前端 10 个预设 ✓（**曾是 8** ✗：
  前端有 10 个预设 ⇒ 用户勾到第 9 个必报 400 ✓，而计算层其实早为"全选"预留了 12 ✓，
  两处口径不一致 ✓，v1.18.64 才统一 ✓）；
· ST 剔除那条必须**说清怎么修** ✓（`t o o l s/dump_states.py` 同步），
  否则用户只看到"勾了却不能用" ✓，会以为平台坏了 ✗。

## ⚠ 判空必须 `is None`，**不能写 `x or 默认`**
实测事故（v1.18.45 ✓）：`rebalance_period=0` 被 `or 5` 静默当成 **5** 放行 ✓
⇒ **非法参数绕过校验** ✓，而且运行结果"看起来正常"✗。
"""
from __future__ import annotations

from typing import List, NamedTuple, Optional

from fastapi import HTTPException

from ..factors.single_test import _bad_date_arg


class NormalizedReq(NamedTuple):
    """校验通过后的归一化参数（**rest 段只用这几个** ✓）。"""

    horizons: List[int]
    parallel: bool
    n_h: int
    quantiles: int
    rebalance_period: Optional[int]
    topk_list: List[float]


def validate_test_request(req) -> NormalizedReq:
    """校验并归一化单因子测试请求 ✓；不合法 ⇒ `HTTPException(400, 可执行文案)` ✓。"""
    if not req.factors:
        raise HTTPException(status_code=400, detail="请至少勾选一个因子")
    # ★ v2.4.3：离场因子的两条前置校验（用户 2026-10-15 拍板 ✓）—— 纯函数，便于单测 ✓
    # ⚠ **函数内懒导入** ✗→✓：`_apply_exit_validation` 仍在 `factors_single.py` 里
    #   （它依赖 `_exit_list` / `uses_entry` / `split_entry_rule` ✓，搬动面太大 ✗），
    #   而 `factors_single.py` **顶层 import 了本模块** ⇒ 这里**不能**在模块顶层 import 它 ✗
    #   （那会是循环引用 ✓，导入期直接炸 ✓）。调用时 `factors_single` 必然已加载完 ✓。
    from .factors_single import _apply_exit_validation

    _apply_exit_validation(req)
    if not req.start_date or not req.end_date:
        raise HTTPException(status_code=400, detail="请填写测试区间")
    # 日期合法性兜底（前端已校验，此处防 API 直连）：如 2026-06-31 非法，
    # 放行会在 pandas 解析处报成「特征计算失败: day is out of range for month」。
    _bad = _bad_date_arg(req.start_date, req.end_date)
    if _bad:
        raise HTTPException(status_code=400, detail=_bad)

    # 归并预测周期：label_horizons 优先；否则退化为 label_horizon（单周期，历史行为）
    raw = req.label_horizons if req.label_horizons else ([req.label_horizon] if req.label_horizon else [])
    if not raw:
        raise HTTPException(status_code=400, detail="预测周期不能为空")
    horizons: List[int] = []
    for h in raw:
        try:
            hi = int(h)
        except (TypeError, ValueError):
            raise HTTPException(status_code=400, detail=f"预测周期必须为正整数：{h!r}")
        if not (1 <= hi <= 250):
            raise HTTPException(status_code=400, detail=f"预测周期需在 1~250 之间：{hi}")
        if hi not in horizons:
            horizons.append(hi)
    horizons.sort()  # 结果按因子分组、组内周期升序
    parallel = bool(req.parallel) and len(horizons) > 1
    n_h = len(horizons)

    # 分位/曲线参数校验（v1.18.45：由 API 传入，不再硬编码；默认值与历史行为一致）
    try:
        quantiles = int(10 if req.quantiles is None else req.quantiles)
    except (TypeError, ValueError):
        raise HTTPException(status_code=400, detail=f"分位组数必须为整数：{req.quantiles!r}")
    if not (2 <= quantiles <= 20):
        raise HTTPException(status_code=400, detail=f"分位组数需在 2~20 之间：{quantiles}")
    try:
        # None 表示"跟随预测周期 h"（默认口径），原样透传；给了值才校验范围
        rebalance_period = (None if req.rebalance_period is None
                            else int(req.rebalance_period))
    except (TypeError, ValueError):
        raise HTTPException(status_code=400, detail=f"调仓期必须为整数：{req.rebalance_period!r}")
    if rebalance_period is not None and not (1 <= rebalance_period <= 250):
        raise HTTPException(status_code=400, detail=f"调仓期需在 1~250 个交易日之间：{rebalance_period}")
    topk_list: List[float] = []
    if req.topk_list:
        # 上限 12 = 默认档(10% 固定档) + 前端全部 10 个预设，与计算层 `_ks[:12]` 一致 ✓
        if len(req.topk_list) > 12:
            raise HTTPException(
                status_code=400,
                detail=f"明细曲线最多点选 12 个 K（默认档 + 10 个预设）：{len(req.topk_list)}")
        for v in req.topk_list:
            try:
                fv = float(v)
            except (TypeError, ValueError):
                raise HTTPException(status_code=400, detail=f"K 必须为数值：{v!r}")
            if not (fv > 0):
                raise HTTPException(status_code=400, detail=f"K 必须为正数：{fv}")
            topk_list.append(fv)

    # ST 剔除依赖 is_st 标签（tools/dump_states.py 从 E:/rq bundle 同步）：本机无 dump 时明确报错，
    # 避免"勾了但没剔除"的静默错误（涨停/跌停判定无标签时自动回退倒推，不受影响）
    if req.exclude_st_t1:
        from ..engine.limits import field_bin_available
        if not field_bin_available("is_st"):
            raise HTTPException(
                status_code=400,
                detail='勾选了"剔除ST(T+1)"，但本机 Qlib 数据没有 is_st 标签'
                "（需先执行 tools/dump_states.py 从 E:/rq bundle 同步）。"
                "取消勾选即可继续（涨停/跌停判定会自动回退倒推口径）",
            )

    return NormalizedReq(horizons=horizons, parallel=parallel, n_h=n_h,
                         quantiles=quantiles, rebalance_period=rebalance_period,
                         topk_list=topk_list)
