# -*- coding: utf-8 -*-
"""`engine/metrics.py` 的三个**提取器**（★ v2.11.83）。

## 为什么挑这三个
后端覆盖 69.60% 只差 **+78 行**就到 70%（跨档 ✓）。这三个函数缺 **72 行**，
而且都**不需要行情数据** —— 只要给 `recorder` / 临时目录喂假对象 ✓：

· `_find_report_fallback`（缺 21）：兜底去 `mlruns/**` 找 `report_normal*.pkl`；
· `_extract_end_position`（缺 32）：**段末持仓跨段传递** —— 它是"滚动训练时下一段能不能
  卖掉上一段遗留持仓"的唯一依据 ✓；
· `_get_segment_end_account`（缺 19）：段末账户总值。

## 它们坏掉的样子（都不报错）
· **丢持仓** ⇒ 下一段从纯现金开始 ⇒ 上一段的旧持仓**凭空消失**（净值曲线看着更"干净"✗，
  而这正是注释里写的"只买不卖/免费清仓"BUG ✓）；
· **把 `cash` 当成一只股票** ⇒ 持仓里多出一个叫 `cash` 的标的 ✗；
· **`price` 缺失时硬填 0** ⇒ 下一段按 0 元卖出 ⇒ 净值暴增 ✗；
· 兜底读到**旧文件** ⇒ 拿上一次回测的报告当这一次的 ✗（所以要按 mtime 取最新 ✓）。
"""
from __future__ import annotations

import os
import pickle
import time

import pandas as pd
import pytest

from app.engine import metrics as M


@pytest.fixture(autouse=True)
def _isolate_mlruns(tmp_path, monkeypatch):
    """把 `WORK_DIR` 指到 tmp —— 否则 `_find_report_fallback` 会去**本机真 mlruns** 找 ✓，
    "应为空"的断言就会随环境红绿（这个坑我在 `test_extract_trades.py` 踩过 ✓）。

    ⚠ 还要**建出这个目录** ✓ —— 我第一版只 `setattr` 了一个字符串 ✗ ⇒
    `os.path.isdir(WORK_DIR)` 是 False ✓（底下那条自检当场红 ✓，抓得对）。
    """
    wd = tmp_path / "workdir"
    # ⚠ `exist_ok=True` ✗→✓ —— 有几条用例自己也会 `mkdir()` 同一个目录 ✓
    #   （我第一版不带它 ⇒ `FileExistsError: WinError 183` 三条当场红 ✓）。
    wd.mkdir(exist_ok=True)
    monkeypatch.setattr("app.config.WORK_DIR", str(wd))


class _Rec:
    def __init__(self, objs: dict | None = None, fail_keys: set | None = None):
        self.objs = objs or {}
        self.fail_keys = fail_keys or set()
        self.calls: list = []

    def load_object(self, key):
        self.calls.append(key)
        if key in self.fail_keys:
            raise RuntimeError("mlflow 读不到")
        return self.objs.get(key)


class _Pos:
    """冒充 qlib 的 Position（`.position` 是 dict ✓）。"""

    def __init__(self, position):
        self.position = position


# ---------------------------------------------------------------- 兜底报告
class TestFindReportFallback:
    def _make(self, root, name, obj, mtime=None):
        d = os.path.join(str(root), "mlruns", "0", "abc", "artifacts", "portfolio_analysis")
        os.makedirs(d, exist_ok=True)
        p = os.path.join(d, name)
        with open(p, "wb") as f:
            pickle.dump(obj, f)
        if mtime is not None:
            os.utime(p, (mtime, mtime))
        return p

    def test_picks_latest_by_mtime(self, tmp_path, monkeypatch):
        """⭐⭐ 必须取 **mtime 最新** 的那份 ✓ —— 取错就是"拿上一次回测的报告当这一次"✗。"""
        wd = tmp_path / "workdir"
        wd.mkdir(exist_ok=True)     # fixture 已建过 ✓（不带 exist_ok 就 WinError 183 ✗）
        monkeypatch.setattr("app.config.WORK_DIR", str(wd))
        self._make(wd, "report_normal_old.pkl", {"which": "old"}, mtime=1_600_000_000)
        self._make(wd, "report_normal_new.pkl", {"which": "new"}, mtime=1_700_000_000)
        got = M._find_report_fallback()
        assert got == {"which": "new"}, got

    def test_none_when_no_files(self, tmp_path, monkeypatch):
        wd = tmp_path / "workdir"
        wd.mkdir(exist_ok=True)     # fixture 已建过 ✓（不带 exist_ok 就 WinError 183 ✗）
        monkeypatch.setattr("app.config.WORK_DIR", str(wd))
        assert M._find_report_fallback() is None

    def test_none_when_pickle_corrupt(self, tmp_path, monkeypatch):
        """坏 pkl ⇒ `None`（不能抛 ✗ —— 它只是兜底路径 ✓）。"""
        wd = tmp_path / "workdir"
        wd.mkdir(exist_ok=True)     # fixture 已建过 ✓
        d = os.path.join(str(wd), "mlruns", "0", "a", "artifacts", "portfolio_analysis")
        os.makedirs(d, exist_ok=True)
        with open(os.path.join(d, "report_normal_x.pkl"), "wb") as f:
            f.write(b"not-a-pickle")
        monkeypatch.setattr("app.config.WORK_DIR", str(wd))
        assert M._find_report_fallback() is None


# ---------------------------------------------------------------- 段末持仓
class TestExtractEndPosition:
    def _rec(self, position, fail_keys=None):
        return _Rec({"portfolio_analysis/positions_normal_1day.pkl":
                     {pd.Timestamp("2024-01-05"): _Pos(position)}}, fail_keys)

    def test_filters_meta_keys_and_keeps_stocks(self):
        """⭐⭐ `cash` / `now_account_value` / `now_weight` **不是股票** ✓
        —— 当股票塞进去 ⇒ 下一段会"卖出 cash" ✗。"""
        rec = self._rec({
            "SH600000": {"amount": 100, "price": 10.5},
            "SZ000001": {"amount": 200},
            "cash": 12345.0,
            "now_account_value": 99999.0,
            "now_weight": 1.0,
        })
        acct = M._extract_end_position(rec)
        assert set(acct) == {"SH600000", "SZ000001", "cash"}
        assert acct["SH600000"] == {"amount": 100.0, "price": 10.5}
        assert acct["cash"] == 12345.0, "cash 要**单独**作为金额 ✓（不是标的 ✓）"

    def test_missing_price_does_not_invent_zero(self):
        """⭐ `price` 缺失 ⇒ 该条**不带** price ✓ —— 填 0 会让下一段按 0 元卖出 ⇒ 净值暴增 ✗。"""
        acct = M._extract_end_position(self._rec({"SH600000": {"amount": 100}}))
        assert "price" not in acct["SH600000"]

    def test_zero_amount_skipped(self):
        acct = M._extract_end_position(self._rec({"SH600000": {"amount": 0}, "cash": 5.0}))
        assert "SH600000" not in acct

    def test_none_when_no_positions(self):
        assert M._extract_end_position(_Rec({})) is None

    def test_none_when_position_not_dict(self):
        assert M._extract_end_position(self._rec(None)) is None

    def test_tolerates_loader_errors(self):
        """第一个 key 抛 ⇒ 试第二个 ✓；全抛 ⇒ `None` ✓（不向上抛 ✗）。"""
        rec = _Rec({}, fail_keys={"portfolio_analysis/positions_normal_1day.pkl",
                                  "positions_normal_1day.pkl"})
        assert M._extract_end_position(rec) is None
        assert len(rec.calls) >= 2, "应该**逐个** try 两个 key ✓：%s" % rec.calls

    def test_empty_position_dict_gives_only_cash(self):
        acct = M._extract_end_position(self._rec({"cash": 0.0}))
        assert acct == {"cash": 0.0}


# ---------------------------------------------------------------- 段末账户总值
class TestSegmentEndAccount:
    def _rec(self, obj):
        return _Rec({"portfolio_analysis/report_normal_1day.pkl": obj})

    def test_last_non_nan(self):
        df = pd.DataFrame({"account": [100.0, 110.0, float("nan")]})
        assert M._get_segment_end_account(None, self._rec(df)) == 110.0

    def test_none_without_column(self):
        assert M._get_segment_end_account(None, self._rec(pd.DataFrame({"x": [1.0]}))) is None

    def test_none_when_report_missing(self):
        assert M._get_segment_end_account(None, _Rec({})) is None

    def test_none_when_all_nan(self):
        df = pd.DataFrame({"account": [float("nan")]})
        assert M._get_segment_end_account(None, self._rec(df)) is None


def test_isolate_fixture_actually_redirects(tmp_path):
    """⚠ 钉住上面那个 fixture 的前提：`WORK_DIR` **真的**被改到 tmp ✓
    （否则那三条"应为空"会去读本机真 mlruns ⇒ 本地红 / CI 绿 ✗ —— 我在
      `test_extract_trades.py` 就踩过这个坑 ✓）。

    ⚠⚠ 我第一版这里写成 `assert "tmp" in TEMP or True` ✗ —— `or True` 让它**永真** ✓，
    等于什么都没断言 ✓（"真空测试"比没测更糟：它会让人以为这一块有守卫 ✗）。
    """
    import app.config as cfg

    wd = str(cfg.WORK_DIR)
    # ⚠ 别断言 `"tmp" in wd.lower()` ✗ —— Windows 的临时目录叫 `Temp`，
    #   小写后是 `temp`，里面**没有** `tmp` 这三个字母 ✓（我第一版就这么红的 ✗）。
    assert wd.endswith("workdir"), wd
    assert os.path.isdir(wd), wd
    assert not os.path.isdir(os.path.join(wd, "mlruns")), "fixture 没生效 ⇒ 会去读本机 mlruns ✗"
