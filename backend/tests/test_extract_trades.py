# -*- coding: utf-8 -*-
"""`engine/metrics._extract_trades`（★ v2.11.76）—— 从 qlib Indicator 里提取**逐笔调仓记录**。

## 为什么值得测
它是导出文件里「调仓记录」页的**唯一来源** ✓。坏掉的方式都很安静 ✗：
· **方向搞反**（qlib `OrderDir` 是 SELL=0/BUY=1，这里统一成 1=买入 / -1=卖出 ✓）⇒
  用户看到的"买/卖"全颠倒，而数字一个不差 ✗；
· **日期只有日期没有时间** ✗（`pd.Timestamp(...).strftime` ✓）⇒ 跨段拼接时排序会乱 ✓；
· 某个字段（成本/滑点）取不到就整笔丢掉 ✗ ⇒ 用户以为"那天没交易" ✓；
  ⇒ 正确行为是**字段给 `None`，记录本身保留** ✓（本文件后半段就是这么写的 ✓）。
"""
from __future__ import annotations

import pytest

from app.engine.metrics import _extract_trades


class _Idx:
    """冒充 qlib 的 IndexData（`.index` 是标的列表、`.data` 是数值 ✓）。"""

    def __init__(self, index, data):
        self.index = index
        self.data = data


class _OI:
    """冒充某一天的 OrderIndicator（按名字取各列 ✓）。"""

    def __init__(self, cols):
        self._cols = cols

    def get_index_data(self, name):
        return self._cols.get(name)


class _Indicator:
    def __init__(self, his):
        self.order_indicator_his = his


class _Recorder:
    def __init__(self, obj=None, fail: bool = False):
        self.obj = obj
        self.fail = fail
        self.calls: list = []

    def load_object(self, key):
        self.calls.append(key)
        if self.fail:
            raise RuntimeError("mlflow 读不到")
        return self.obj


def _one_day(**over):
    cols = {
        "trade_dir": _Idx(["SH600000", "SZ000001"], [1.0, 0.0]),
        "amount": _Idx(["SH600000", "SZ000001"], [100.0, 200.0]),
        "trade_price": _Idx(["SH600000", "SZ000001"], [10.5, 8.0]),
        "trade_value": _Idx(["SH600000", "SZ000001"], [1050.0, 1600.0]),
        "trade_cost": _Idx(["SH600000", "SZ000001"], [0.5, 0.8]),
        "deal_amount": _Idx(["SH600000", "SZ000001"], [100.0, 200.0]),
        "ffr": _Idx(["SH600000", "SZ000001"], [0.0, 0.0]),
    }
    cols.update(over)
    return {"2024-01-02": _OI(cols)}


@pytest.fixture(autouse=True)
def _isolate_mlruns(tmp_path, monkeypatch):
    """⭐ 必须隔离本机 `mlruns/` ✗→✓ —— `_extract_trades` 在 recorder 取不到时会
    **回退去 glob 本机 `mlruns/**/indicators_*_obj.pkl`** ✓，我本地正好有真实产物
    ⇒ 3 条"应为空"的断言当场红 ✓（`Left contains 3 more items`）。

    ⇒ 把 `WORK_DIR` 指到 tmp ⇒ 两个 glob 模式（`<BASE>/mlruns` 与 `<BASE>/../mlruns`）
      都落在 tmp 附近、找不到任何文件 ✓ ⇒ 行为**确定** ✓。
    ⚠ 这也说明：**"空结果"这类断言依赖环境**，不隔离就会"本地红、CI 绿"✗。
    """
    monkeypatch.setattr("app.config.WORK_DIR", str(tmp_path / "workdir"))


class TestHappyPath:
    def test_two_rows(self):
        out = _extract_trades(None, _Recorder(_Indicator(_one_day())))
        assert len(out) == 2
        r0, r1 = out
        assert r0["date"] == "2024-01-02" and r0["instrument"] == "SH600000"
        assert r1["instrument"] == "SZ000001"

    def test_direction_mapping(self):
        """⭐⭐ qlib 的 `OrderDir` 是 SELL=0 / BUY=1；这里统一成 **1=买入 / -1=卖出** ✓。
        搞反了不会报错，只会让用户看到"买/卖"全颠倒而数字一个不差 ✗。"""
        out = _extract_trades(None, _Recorder(_Indicator(_one_day())))
        assert out[0]["direction"] == 1, "1.0 ⇒ 买入 ✓"
        assert out[1]["direction"] == -1, "0.0 ⇒ 卖出（不是买入 ✗）"

    def test_numeric_fields(self):
        out = _extract_trades(None, _Recorder(_Indicator(_one_day())))
        assert out[0]["amount"] == 100.0
        assert out[0]["deal_price"] == 10.5
        assert out[0]["trade_cost"] == 0.5

    def test_date_is_truncated_to_day(self):
        his = {"2024-01-02 15:00:00": _OI({k: v for k, v in
                                          _one_day()["2024-01-02"]._cols.items()})}
        out = _extract_trades(None, _Recorder(_Indicator(his)))
        assert out[0]["date"] == "2024-01-02", "带时分秒会破坏排序 ✓：%s" % out[0]["date"]


class TestMissingPieces:
    def test_empty_his_returns_empty(self):
        assert _extract_trades(None, _Recorder(_Indicator({}))) == []

    def test_no_indicator_returns_empty(self):
        """recorder 一个都取不到 ⇒ 空列表（不能抛 ✗ —— 调用方只是"这次没记录"）。"""
        assert _extract_trades(None, _Recorder(None)) == []

    def test_recorder_failures_are_tolerated(self):
        assert _extract_trades(None, _Recorder(fail=True)) == []

    def test_missing_trade_dir_skips_that_day(self):
        his = {"2024-01-02": _OI({"trade_dir": None})}
        assert _extract_trades(None, _Recorder(_Indicator(his))) == []

    def test_empty_index_skips_that_day(self):
        his = {"2024-01-02": _OI({"trade_dir": _Idx([], [])})}
        assert _extract_trades(None, _Recorder(_Indicator(his))) == []

    def test_missing_optional_field_gives_none_not_drop(self):
        """⭐⭐ 某个字段取不到 ⇒ 该字段 `None`，但**记录要保留** ✓ ——
        整笔丢掉会让用户以为"那天没交易" ✗。"""
        out = _extract_trades(None, _Recorder(_Indicator(_one_day(trade_cost=None, ffr=None))))
        assert len(out) == 2, "记录被整笔丢了 ✗"
        assert out[0]["trade_cost"] is None
        assert out[0]["ffr"] is None
        assert out[0]["deal_price"] == 10.5, "其它字段不受影响 ✓"

    def test_short_column_does_not_raise(self):
        """列比标的少 ⇒ 越界的那格给 `None` ✓（不能抛 ✗ —— 一段坏数据会毁掉整份导出）。"""
        out = _extract_trades(None, _Recorder(_Indicator(_one_day(
            trade_price=_Idx(["SH600000"], [10.5])))))
        assert len(out) == 2
        assert out[0]["deal_price"] == 10.5
        assert out[1]["deal_price"] is None, "越界应给 None ✓"

    def test_bad_direction_value_is_none(self):
        """方向值不是数字 ⇒ `None`（不是猜 0/-1 ✗ —— 猜错就是"买卖颠倒"）。"""
        out = _extract_trades(None, _Recorder(_Indicator(_one_day(
            trade_dir=_Idx(["SH600000", "SZ000001"], ["坏值", 1.0])))))
        assert out[0]["direction"] is None
        assert out[1]["direction"] == 1


@pytest.mark.parametrize("keys", [0])
def test_tries_multiple_object_keys(keys):
    """先在几个固定 key 上找 indicators 对象 ✓（不同 qlib 版本路径不同 ✓）。"""
    rec = _Recorder(None)
    _extract_trades(None, rec)
    assert len(rec.calls) >= 2, "应逐个 key 尝试 ✓：%s" % rec.calls
