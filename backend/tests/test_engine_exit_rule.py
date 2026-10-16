# -*- coding: utf-8 -*-
"""★ v2.8.8b（用户 2026-10-16「接着做真算出来」✓）：**事件驱动引擎也真算"含入场价"的离场规则** ✗→✓

背景（同一晚连续两轮 ✓）：0/1 信号的"含离场"回测走 `signals/engine.run_backtest` ✓，
而"含入场价"的离场公式（`ENTRY_PRICE` / `ENTRY_FIRST` ✓）是**回测状态量** ✗ ⇒ 旧实现根本拿不到
离场信号 ⇒ `_mask=None` ⇒ 离场那一支**被静默跳过** ✗ ⇒ 含离场曲线与对照**逐位相同** ✓
（用户原话：「事件研究的离场曲线肯定也没算对」✓ —— 判断完全正确 ✓）。

修法：引擎新增 `exit_rule` 通道 ✓（每天收盘后 → 用「当日价 + **当前入场价向量**」求值 →
挂 `sell_q` → **滞后 lag 天**执行 ✓，与 `period_roll` 逐条同语义 ✓）。

本文件钉住三条：
  ① 传 `exit_rule` ⇒ **真产生离场卖单** ✓（`exit_signals` / `exit_sells` > 0 ✓、`reason="exit_signal"` ✓）；
  ② 不传 ⇒ 行为完全不变 ✓（老路径零影响 ✓）；
  ③ 离场卖单**不前视** ✓（决策日 i ⇒ 成交日 ≥ i + lag ✓）。
⚠ 纯合成数据 ⇒ CI（无数据）也能跑 ✓。
"""
import numpy as np
import pandas as pd

from app.factors.exit_rules import split_entry_rule
from app.signals.engine import run_backtest

CAL = pd.bdate_range("2024-01-01", periods=80)
CODES = ["SH600001", "SH600002"]


def _panel() -> dict:
    n, m = len(CAL), len(CODES)
    px = np.tile(np.array([10.0] * m, dtype=float), (n, 1))
    for i in range(30, n):                       # 第 30 天起每天 −1.2% ⇒ 跌破首次入场价 2% 必然触发 ✓
        px[i] = 10.0 * (0.988 ** (i - 29))
    df = pd.DataFrame(px, index=CAL, columns=CODES)
    return {"CALENDAR": pd.DataFrame(index=CAL), "CLOSE": df, "OPEN": df,
            "CLOSE_RAW": df, "OPEN_RAW": df,
            "LIMIT_UP": df * 1.1, "LIMIT_DOWN": df * 0.9, "_limits_inferred": True}


def _sig() -> pd.DataFrame:
    return pd.DataFrame({"date": [CAL[0]] * len(CODES), "code": CODES, "side": [1] * len(CODES)})


def _rule():
    r = split_entry_rule("Lt($close,Mul($entry_first,0.98))", 0.0)
    assert r is not None, "拆分失败 ✗（`$entry_first` 应被识别 ✓）"
    cl = _panel()["CLOSE"]
    r.bind([cl.to_numpy(dtype=float)] * len(r.pre_exprs))
    return r


def _run(rule=None):
    return run_backtest(_sig(), _panel(), hold_days=60, fill="t1_open", cost=0.004,
                        capital=1e6, strict_limit=True, alloc_modes=("event_even",),
                        exit_rule=rule)


def test_engine_exit_rule_really_sells():
    """★ 传规则 ⇒ 真的卖出 ✓（这正是"真算出来"的判据 ✓）。"""
    out = _run(_rule())
    st = (out.stats or {}).get("event_even") or {}
    assert st.get("exit_rule") is True
    assert st.get("exit_note") == "", "规则求值失败 ✗：%s" % st.get("exit_note")
    assert int(st.get("exit_signals") or 0) > 0, "一次都没触发 ✗"
    assert int(st.get("exit_sells") or 0) > 0, "没真卖出 ✗"
    tr = out.trades
    sells = tr[tr["side"].astype(str) == "卖"] if tr is not None and len(tr) else None
    assert sells is not None and len(sells) > 0
    assert (sells["reason"].astype(str) == "exit_signal").any(), "卖单里没有离场原因 ✗"


def test_engine_without_rule_unchanged():
    """★ 不传规则 ⇒ 行为不变（`exit_rule=False`、零离场计数 ✓）。"""
    out = _run(None)
    st = (out.stats or {}).get("event_even") or {}
    assert st.get("exit_rule") is False
    assert int(st.get("exit_signals") or 0) == 0
    assert int(st.get("exit_sells") or 0) == 0
    assert st.get("exit_note") == ""


def test_exit_not_lookahead():
    """★★ 离场卖单**不得早于** 决策日 + lag ✓（`t1_open` ⇒ lag=1 ✓）。

    ⚠ 这条是本项目的老纪律（v2.4.3 第一版忘了滞后 ⇒ 直接在信号当天卖 ⇒ **前视** ✗，
      被单测当场抓住 ✓）—— 本通道必须同样守住 ✓。
    """
    out = _run(_rule())
    tr = out.trades
    sells = tr[tr["side"].astype(str) == "卖"] if tr is not None and len(tr) else None
    assert sells is not None and len(sells) > 0
    # 价格从第 30 天开始跌 ⇒ 规则最早在第 31 天满足 ⇒ 卖出日必须 ≥ 第 32 天（再 +1 个 lag ✓）
    assert str(sells["date"].min()) >= str(CAL[32].date()), "卖出早于触发 ⇒ 前视 ✗"
