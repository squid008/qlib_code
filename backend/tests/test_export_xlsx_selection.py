# -*- coding: utf-8 -*-
"""`routers/export_xlsx.py` 的**选行/选档/选基准/写契约**（★ v2.10.49，2026-10-21 ✓）。

已有测试覆盖了 `_decode_shot` / `_safe_name` / `_find_event_study` ✓（2.9.x ✓）；
这一批补的是**还没测过的那半**：**导出的到底是哪一行、哪一档、哪个基准，以及文件里记了什么参数** ✓。

## 为什么这半更值钱

导出文件的使命是「**把面板那次回测原样搬走**」✓ ——
选错行 ⇒ 导出的是**另一个因子** ✓；契约参数写错 ⇒ 回灌**看起来一样、结果不一样** ✓
（项目历史上"buys 956 vs 面板 959、末值 1.2747 vs 1.2728"就是这么来的 ✓）。
"""
from __future__ import annotations

import pytest
from fastapi import HTTPException

from app.exports import spec
from app.routers.export_xlsx import (
    TopkExportRequest,      # ⚠ 它就定义在本文件内（L48）✓，不在 models 里 ✗（我第一版猜错了 ✓）
    _contract_params,
    _find_item,
    _find_row,
    _item_identity,
    _pick_benchmark,
)


def _rq(**kw) -> TopkExportRequest:
    base = {"factor_id": "f1"}
    base.update(kw)
    return TopkExportRequest(**base)


ROWS = [
    {"id": "f1", "name": "因子一", "expression": "CLOSE>10", "horizon": 1},
    {"id": "f1", "name": "因子一", "expression": "CLOSE>10", "horizon": 5},
    {"id": "f2", "name": "因子二", "expression": "$open", "horizon": 5},
]


# ------------------------------------------------------------------- _find_row
@pytest.mark.parametrize("key", ["f1", "因子一", "CLOSE>10"])
def test_find_row_matches_id_name_or_expression(key):
    """三个键都当标识用 ✓（前端可能只带其中一个 ✓）。"""
    got = _find_row(ROWS, _rq(factor_id=key, horizon=1))
    assert got["horizon"] == 1


def test_find_row_prefers_exact_horizon_then_falls_back():
    """⭐⭐ **先按周期严格匹配** ✓；对不上再回退（不严格）✓ ——
    否则用户改了"预测周期"却没重跑时，导出会**直接 404** ✓。"""
    assert _find_row(ROWS, _rq(factor_id="f1", horizon=5))["horizon"] == 5
    assert _find_row(ROWS, _rq(factor_id="f1", horizon=7))["horizon"] == 1, "回退取第一个命中 ✓"
    assert _find_row(ROWS, _rq(factor_id="f1", horizon=None))["horizon"] == 1


def test_find_row_404_with_actionable_message():
    """找不到 ⇒ **404 + 人话** ✓（告诉用户"可能被新一次测试覆盖、请重跑"✓）。"""
    with pytest.raises(HTTPException) as ei:
        _find_row(ROWS, _rq(factor_id="nope"))
    assert ei.value.status_code == 404
    assert "重跑" in str(ei.value.detail)


# -------------------------------------------------------------- _item_identity
def test_item_identity_decile_k_is_zeroed():
    """⭐⭐ 分位档的 `k` **必须归 0** ✓ —— 结果里的分位档 `k` 被写成了"中位持有只数" ✗
    拿它当键会**必然 miss** ✓（取每期持仓用的就是这个三元组 ✓）。"""
    assert _item_identity({"kind": "decile", "k": 37, "quantile": 3}) == ("decile", 0, 3)
    assert _item_identity({"kind": "decile", "quantile": None}) == ("decile", 0, None)


def test_item_identity_topk_keeps_k_and_drops_quantile():
    assert _item_identity({"kind": "topk", "k": 50, "quantile": 9}) == ("topk", 50, None)
    assert _item_identity({}) == ("topk", 0, None), "缺 kind ⇒ 默认 topk ✓"


# ------------------------------------------------------------------ _find_item
def _tc(items) -> dict:
    return {"items": items}


def test_find_item_defaults_to_decile():
    items = [{"kind": "decile", "quantile": 1}, {"kind": "topk", "k": 10}]
    assert _find_item(_tc(items), _rq()).get("quantile") == 1


def test_find_item_topk_by_k_and_without_k():
    items = [{"kind": "topk", "k": 10}, {"kind": "topk", "k": 50}]
    assert _find_item(_tc(items), _rq(item_kind="topk", item_k=50))["k"] == 50
    assert _find_item(_tc(items), _rq(item_kind="topk"))["k"] == 10, "不给 k ⇒ 第一个 ✓"


def test_find_item_400_explains_missing_k():
    with pytest.raises(HTTPException) as ei:
        _find_item(_tc([{"kind": "topk", "k": 10}]), _rq(item_kind="topk", item_k=99))
    assert ei.value.status_code == 400
    assert "档位" in str(ei.value.detail)


# ------------------------------------------------------------- _pick_benchmark
def test_pick_benchmark_by_code_or_default():
    tc = {"benchmarks": {"default": "SH000300",
                         "items": [{"code": "SH000300", "nav": [1]}, {"code": "SH000905", "nav": [2]}]}}
    assert _pick_benchmark(tc, "")["code"] == "SH000300", "不给 ⇒ 用后端默认 ✓（与页面默认一致 ✓）"
    assert _pick_benchmark(tc, "SH000905")["code"] == "SH000905"
    assert _pick_benchmark(tc, "NOPE") is None
    assert _pick_benchmark({}, "X") is None


# ----------------------------------------------------------- _contract_params
def _row(**kw) -> dict:
    base = {"name": "因子一", "expression": "CLOSE>10", "horizon": 5}
    base.update(kw)
    return base


def test_contract_fill_follows_task_request_never_hardcoded():
    """⭐⭐⭐ `P_FILL` **必须取那次任务请求里的 `fill`** ✓ ——
    写死会把「次日开盘」导成「次日收盘」✓ ⇒ 回灌"看起来一样、结果不一样"✓
    （项目里 2026-10-11 那串问题的总根源 ✓）。"""
    p = _contract_params(_row(), {}, {"kind": "decile", "quantile": 1}, {"fill": "t1_open"},
                         None, "compound", "0.0040")
    assert p[spec.P_FILL] == "t1_open"
    p2 = _contract_params(_row(), {}, {"kind": "decile", "quantile": 1}, {},
                          None, "compound", "0.0040")
    assert p2[spec.P_FILL] == "t1_close", "缺省必须是老口径 t1_close ✓"


def test_contract_strict_limit_is_bool_not_null():
    """⭐⭐ `P_STRICT_LIMIT` 必须是**真 bool** ✓ ——
    写成 `null` ⇒ 回灌侧读成 `False` ✗ ⇒ 涨跌停约束**静默关掉** ✓
    ⇒ 实测同一批信号 buys 956 vs 面板 959 ✓。"""
    p = _contract_params(_row(), {}, {"kind": "decile", "quantile": 1}, {}, None, "compound", "0.0040")
    assert p[spec.P_STRICT_LIMIT] is True
    assert p[spec.P_PRICE_ROUND] is True and p[spec.P_SUSPEND_REMOVE] is True


def test_contract_hold_days_prefers_rebalance_period():
    p = _contract_params(_row(horizon=5), {"rebalance_period": 10},
                         {"kind": "decile", "quantile": 1}, {}, None, "compound", "0.0040")
    assert p[spec.P_HOLD_DAYS] == 10, "持仓周期 = 调仓期 ✓"
    p2 = _contract_params(_row(horizon=5), {"rebalance_period": None},
                          {"kind": "decile", "quantile": 1}, {}, None, "compound", "0.0040")
    assert p2[spec.P_HOLD_DAYS] == 5, "没调仓期 ⇒ 退预测周期 ✓"


def test_contract_cost_tiers_only_lists_present_curves():
    """⭐ 只列**真有曲线**的成本档 ✓（列了却没有 ⇒ 回灌找空档 ✗）。"""
    item = {"kind": "decile", "quantile": 1,
            "curves_compound": {"0.0000": [0, 1], "0.0040": [0, 0.9]}}
    p = _contract_params(_row(), {}, item, {}, None, "compound", "0.0040")
    assert len(p[spec.P_CURVE_COST_TIERS]) == 2


def test_contract_topk_and_quantile_are_mutually_exclusive():
    """分位档只写 `P_QUANTILE` ✓、固定 K 档只写 `P_TOPK` ✓（两个都写 ⇒ 回灌不知道听谁的 ✗）。"""
    p_q = _contract_params(_row(), {}, {"kind": "decile", "quantile": 3}, {},
                           None, "compound", "0.0040")
    assert p_q[spec.P_QUANTILE] == 3 and p_q[spec.P_TOPK] is None
    p_k = _contract_params(_row(), {}, {"kind": "topk", "k": 50}, {},
                           None, "compound", "0.0040")
    assert p_k[spec.P_TOPK] == 50 and p_k[spec.P_QUANTILE] is None
