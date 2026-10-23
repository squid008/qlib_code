# -*- coding: utf-8 -*-
"""**真实数据不变量**（★ v2.11.65）—— 合成数据测不出来的那一类。

## 为什么需要它（这轮血的教训）
v2.11.64 那个"科创板剔除从未生效"是**构造面板**发现的 ✓，但更多问题是**只有真数据才有形状** ✗：
· **量纲**：`LIMIT_UP`（`$limit_up`）是**真实价**量纲 ✓，而 `CLOSE`（后复权）**远大于**真实价 ✗
  ⇒ 拿它比涨停 ⇒ **几乎天天涨停** ⇒ 误剔 23.5%（v2.1.58 真事故 ✓）。
  合成数据是纯随机游走 ✗ ⇒ 这个比例看着"正常" ⇒ **测不出来** ✗✗。
· **代码号段**：合成数据只有 30 只、不含 SH688 ✗ ⇒ 科创板那条判据**永远走不到** ✓。
· **零行**：面板读不到数据时返回**空表而不报错** ✗（P2-#23）⇒ 只有真目录 + 真日历才能验 ✓。

## 数据形状（我第一次写错，记下来 ✗→✓）
`panel_features()` 返回的是 **`pd.DataFrame`** ✓：
· index = **`MultiIndex(instrument, datetime)`**（长表 ✓，不是 date×inst 宽表 ✗）
· **每个字段一列**（列名 = 你给的第二个元素 ✓）
⇒ 要宽表就 `.unstack("instrument")` ✓（`_apply_excludes` 要的就是宽表 ✓）。

## 怎么跑
```bash
cd backend && python -m pytest tests/test_real_data_invariants.py -q -m datareq
```
⚠ 打 `datareq` ⇒ PR CI 不跑 ✓；**合成数据环境自动 skip** ✓（30 只票上这些不变量没意义 ✗）。
"""
from __future__ import annotations

import os

import pandas as pd
import pytest

from app.factors import panel_expr as PE

#: 真实 A 股（主板 + 中小板 + 创业板）：长期存在的票 ✓
INSTS = ["SH600000", "SH600519", "SZ000001", "SH601318", "SZ000002"]
START, END = "2025-01-01", "2025-06-30"


def _feature_dir() -> str:
    return PE._feature_dir()


def _real_data() -> bool:
    """合成数据集只有 30 只 ⇒ 用 500 只当"是不是真行情"的分界 ✓。"""
    d = _feature_dir()
    try:
        return os.path.isdir(d) and len(os.listdir(d)) >= 500
    except OSError:
        return False


pytestmark = [
    pytest.mark.datareq,
    pytest.mark.skipif(
        not _real_data(),
        reason="需要真实行情（合成数据只 30 只 ⇒ 这些不变量对它没意义 ✗）",
    ),
]


@pytest.fixture(scope="module", autouse=True)
def _init_qlib():
    """⚠ 必须先 `ensure_qlib_init()` ✓ —— 否则 `D.calendar` 抛
    `AttributeError: Please run qlib.init() first` ✗（第一次跑 5 条全红 ✓）。

    必须走**项目入口**而不是裸 `qlib.init` ✗：统一入口还会重新注册自定义算子
    （`BARSCOUNT` / `BARSSINCEN`…✓），裸调会清空它们 ✗（`test_chip_field.py` 同款注释 ✓）。
    """
    if not _real_data():
        return
    from app.services.qlib_runtime import ensure_qlib_init

    ensure_qlib_init()


def _panel(fields):
    return PE.panel_features(INSTS, fields, START, END)


def _wide(df: pd.DataFrame, col: str) -> pd.DataFrame:
    """长表（MultiIndex）→ 宽表（index=date, columns=inst）✓ —— `_apply_excludes` 要的形状。"""
    return df[col].unstack("instrument")


class TestPanelNotSilentlyEmpty:
    def test_panel_has_rows(self):
        """⭐⭐ P2-#23 的形态：读不到就**返回空表而不报错** ✗ ⇒ 必须钉住"非空"。"""
        df = _panel([("$close", "close")])
        assert len(df) > 0, "面板读到 0 行 ✗（P2-#23 那种静默空 ✗）—— 查 provider_uri/日历/大小写"
        assert "close" in df.columns, "字段列缺失：%s" % list(df.columns)
        got = sorted(df.index.get_level_values("instrument").unique())
        assert len(got) == len(INSTS), "应有 %d 只，实际 %d：%s" % (len(INSTS), len(got), got)
        assert df["close"].notna().any(), "整列全 NaN ⇒ 没读到 ✓"

    def test_panel_dates_lie_on_calendar(self):
        """面板日期必须来自**交易日历**（含周末 ⇒ 索引拼错了 ✗）。"""
        df = _panel([("$close", "close")])
        idx = pd.DatetimeIndex(df.index.get_level_values("datetime")).unique()
        cal = pd.DatetimeIndex(PE._calendar())
        assert len(idx) > 0
        missing = idx.difference(cal)
        assert len(missing) == 0, "有 %d 个日期不在交易日历里 ✗（例：%s）" % (
            len(missing), list(missing[:3]))


class TestUnitScale:
    def test_close_raw_and_limit_up_are_same_scale(self):
        """⭐⭐⭐ **量纲不变量**（v2.1.58 真事故的根因）。

        `$limit_up` 是**真实价**量纲 ✓；后复权 `$close` 通常**远大于**它 ✗。
        拿 `$close` 比涨停 ⇒ 绝大多数样本都"涨停" ⇒ 误剔 23.5% ✓。
        这里在真数据上量两个比例：**真实价必须远小于 1，后复权必须明显更高** ✓。
        """
        df = _panel([("$close", "close"), ("$close/$factor", "craw"), ("$limit_up", "lu")])
        if "lu" not in df.columns:
            pytest.skip("该数据集没有 $limit_up 字段 ⇒ 此条不适用 ✓")
        sub = df[["close", "craw", "lu"]].dropna()
        if not len(sub):
            pytest.skip("三列无交集（可能缺 $factor）⇒ 此条不适用 ✓")

        raw_hit = float((sub["craw"] >= sub["lu"] - 1e-6).mean())
        close_hit = float((sub["close"] >= sub["lu"] - 1e-6).mean())
        assert raw_hit < 0.15, (
            "用**真实价**比涨停的比例 %.1f%% 偏高 ✗ —— 量纲/字段口径可能变了 ✓" % (raw_hit * 100))
        assert close_hit > raw_hit * 3, (
            "⚠ 这是**反证**：后复权价应**明显更容易**超过 limit_up ✓（raw=%.3f / close=%.3f）"
            "—— 两者接近说明 `$factor` 或 `$limit_up` 已同量纲 ⇒ 那段注释与判据要一起复核 ✓"
            % (raw_hit, close_hit))


class TestExclusionOnRealData:
    def _codes_with_prefix(self, prefix: str, n: int = 3):
        d = _feature_dir()
        got = sorted(x for x in os.listdir(d) if x.startswith(prefix))
        return [c.upper() for c in got[:n]]

    def test_kcb_exclusion_really_removes_sh688(self):
        """⭐ v2.11.64 那个 bug 的**真数据版**：以前 `[:4]` 比 `"SH688"` ⇒ 一条都剔不掉 ✗。"""
        from app.routers.signal_test import _apply_excludes

        kcb = self._codes_with_prefix("sh688")
        if len(kcb) < 2:
            pytest.skip("本数据集没有科创板样本 ⇒ 此条不适用 ✓")
        df = _panel([("$close", "close")])
        close = _wide(df, "close")
        cal = pd.DatetimeIndex(close.index)
        if not len(cal):
            pytest.skip("面板空 ⇒ 先修 P2-#23 ✓")

        ev = pd.DataFrame({"code": kcb + ["SH600000"], "date": [cal[-1]] * (len(kcb) + 1)})
        out = _apply_excludes(ev, {"CLOSE": close}, {"exclude_stock_kcb": True}, [])
        assert "SH600000" in set(out["code"]), "主板票被误剔了 ✗"
        assert not any(c.startswith("SH688") for c in out["code"]), (
            "科创板**没被剔除** ✗ —— v2.11.64 那个 `[:4]` bug 回来了？输入 %s" % (list(ev["code"]),))

    def test_limit_up_exclusion_is_not_overzealous(self):
        """⭐⭐ 真数据上**剔除比例必须合理**（v2.1.58 的 23.5% 误剔就是这条能抓的）。"""
        from app.routers.signal_test import _apply_excludes

        df = _panel([("$close", "close"), ("$close/$factor", "craw"),
                     ("$limit_up", "lu"), ("$change", "chg")])
        close = _wide(df, "close")
        if not len(close):
            pytest.skip("面板空 ⇒ 先修 P2-#23 ✓")
        if "lu" not in df.columns:
            pytest.skip("该数据集没有 $limit_up ⇒ 此条不适用 ✓")

        panel = {"CLOSE": close, "CLOSE_RAW": _wide(df, "craw"),
                 "LIMIT_UP": _wide(df, "lu"), "CHANGE": _wide(df, "chg")}
        cal = pd.DatetimeIndex(close.index)
        codes = list(close.columns)[:5]
        ev = pd.DataFrame({"code": [c for c in codes for _ in cal],
                           "date": [d for _c in codes for d in cal]})
        w: list[str] = []
        out = _apply_excludes(ev, panel, {"exclude_limit_up_signal": True}, w)
        dropped = 1.0 - len(out) / max(len(ev), 1)
        assert dropped < 0.30, (
            "「剔除信号日涨停」剔掉了 **%.1f%%** 的样本 ✗（合理应远小于 10%%）✓\n"
            "  这正是 v2.1.58 的形态：拿后复权价比 limit_up ⇒ 大面积误剔 ✗\n  warnings=%s"
            % (dropped * 100, w))
        assert any("剔除" in x for x in w), "剔了就必须报数 ✓（不许静默）: %s" % w
