# -*- coding: utf-8 -*-
"""★ T4（审计 ⑩ / v2.8.7）：`/api/data/daily-bars` 的 `adjust` 必须**真的生效** ✗→✓。

审计现象：`qlib_source.get_daily_bars` 里 `if adjust in ("qfq","hfq"): fields.append("$factor")` ✗
—— **只多取了个因子字段**，价格原样返回 ⇒ `none`/`qfq`/`hfq` 三条**价格完全相同** ✗✓，
而路由 `routers/data.py` 照原样透传 ⇒ 用的人以为换算过 ✓（误导 ✓）。

修法（与 `engine/adjust.py` 同一套口径 ✓）：
  · `none` ⇒ 真实价 = 原生价 / `$factor` ✓；
  · `hfq`  ⇒ 后复权 = 原生价 ✓（两者必须**不同** ✓ —— 这条断言就是"参数不再被吃"的判据 ✓）；
  · `qfq`  ⇒ **明确报错** ✗（引擎侧"除以每股最新因子"还没落地 ⇒ 硬凑会与回测口径分叉 ✗）。
⚠ 需要真数据 ⇒ 打 `datareq`（CI 无数据 ⇒ 必须打标 ✓）。
"""
import pytest

from app.datasource.base import DataNotAvailableError
from app.datasource.qlib_source import QlibDataSource

_INST = "SH600000"
_S, _E = "2021-01-04", "2021-01-15"


@pytest.mark.datareq
def test_none_and_hfq_really_differ():
    """★★ 核心判据：`none`（真实价）与 `hfq`（后复权）**必须不同** ✗（旧实现三条一样 ✓）。"""
    ds = QlibDataSource()
    a = ds.get_daily_bars(_INST, _S, _E, "none")
    b = ds.get_daily_bars(_INST, _S, _E, "hfq")
    assert a and b and len(a) == len(b)
    x, y = a[0], b[0]
    assert x.factor == y.factor > 0
    # 换算关系：后复权 = 真实价 × 当日因子 ✓（同一份数据内部自洽 ✓）
    assert abs(y.close - x.close * x.factor) < 1e-6
    # 因子不等于 1 的票 ⇒ 两种口径的价**必须**不同 ✓
    if abs(x.factor - 1.0) > 1e-9:
        assert abs(y.close - x.close) > 1e-9, "adjust 又被吃了 ✗（两口径返回同样的价）"


@pytest.mark.datareq
@pytest.mark.synthdata
def test_qfq_raises_loudly():
    """★ 前复权没落地 ⇒ **报错** ✓（不许静默返回"看起来像前复权"的价 ✗）。"""
    ds = QlibDataSource()
    with pytest.raises(DataNotAvailableError) as ei:
        ds.get_daily_bars(_INST, _S, _E, "qfq")
    assert "前复权" in str(ei.value)


@pytest.mark.datareq
@pytest.mark.synthdata
def test_unknown_adjust_rejected():
    """脏输入 ⇒ 明确拒绝 ✓（而不是当 none 静默跑 ✓）。"""
    ds = QlibDataSource()
    with pytest.raises(DataNotAvailableError):
        ds.get_daily_bars(_INST, _S, _E, "whatever")


@pytest.mark.datareq
def test_volume_not_scaled():
    """⚠ 量与额不随复权变化 ✓（别顺手把它们也除了因子 ✗）。"""
    ds = QlibDataSource()
    a = ds.get_daily_bars(_INST, _S, _E, "none")
    b = ds.get_daily_bars(_INST, _S, _E, "hfq")
    assert abs(a[0].volume - b[0].volume) < 1e-6
    assert abs(a[0].amount - b[0].amount) < 1e-6
