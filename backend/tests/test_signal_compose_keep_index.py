# -*- coding: utf-8 -*-
"""★ v1.20.68：**信号合成不许删行** ✗ —— 被拒的置 `-inf` 即可 ✓。

用户 2026-09-24 要求"查到底"的 K 事故（任务 `c4eaaf7ff738` 段1 净值断崖 ✗）：

    `topk_ratio` 的分母应是当日**全池**可交易只数（~4139 ✓），
    但 `compose_final_signal` 旧实现 `frame = frame.loc[frame["score"] != -np.inf]` **删行** ✗
    ⇒ 策略拿到的 `pred_score` 只剩"候选∩存活" **~38 行** ✗
    ⇒ `1% × 38 = 0.38` ⇒ 四舍五入 0 ⇒ 兜底 **1 只** ✗✓
    ⇒ 那一笔 ≈ 8.55M = 当日成交量的 5~10 倍 ⇒ qlib **二次冲击成本**爆炸
      （买 2.64% / 卖 10.07% ✓ 与成交记录逐位吻合 ✓）⇒ 段1 **−20%** ✗
      （按信号表复算"选中那批票"本身 ≈ **+0.2%** ✓ ⇒ 断崖不是股票跌出来的 ✓）。

修法：**保留全索引**、被拒的置 `-inf` ✓（策略 v1.20.66 起已显式丢弃非有限分 ✓ ⇒ 行为等价 ✓）。
本文件锁住：① 合成后行数**不许变少** ✓；② 被拒的确实变 `-inf` ✓；
③ 用"全池"当分母时 K = 可用只数（**不是 1** ✗）。
"""
import numpy as np
import pandas as pd

from app.engine import signal_compose as sc
from app.engine.periodic_strategy import PeriodicTopKStrategy

_N_DAYS = 2
_N_INST = 4000


def _idx():
    dates = pd.date_range("2021-01-04", periods=_N_DAYS, freq="B")
    insts = ["SH%06d" % (600000 + i) for i in range(_N_INST)]
    return pd.MultiIndex.from_product([dates, insts], names=["datetime", "instrument"])


class _FakeModel:
    """只提供 `predict`：返回全池打分（越大越好 ✓）。"""

    def __init__(self, score):
        self._score = score

    def predict(self, dataset, segment="test"):
        return self._score


class _FakeReq:
    meta_gate = True
    topk = 50
    meta_gate_opts = {}
    hard_filters = {}
    trigger_overlay_opts = None


def _mk_strategy(topk_ratio=0.01, topk=50):
    st = PeriodicTopKStrategy.__new__(PeriodicTopKStrategy)
    st.topk = topk
    st.topk_ratio = topk_ratio
    return st


def test_gate_compose_keeps_all_rows(monkeypatch):
    """★★ 开闸门后：**行数不变** ✓，被拒的变 `-inf` ✓ —— 分母才拿得到"全池" ✓。"""
    idx = _idx()
    score = pd.Series(np.arange(len(idx), dtype=float), index=idx)
    z = pd.Series(np.random.RandomState(0).rand(len(idx)), index=idx)

    monkeypatch.setattr(sc, "train_gate", lambda *a, **k: (object(), {"reject_ratio": 0.25, "n": 10, "valid_auc": 0.6}))
    monkeypatch.setattr(sc, "gate_z", lambda *a, **k: z)

    frame, _info = sc.compose_final_signal(None, _FakeModel(score), _FakeReq(), seg_label="段1")

    assert len(frame) == len(idx), "★ 不许删行 ✗（删了 ⇒ 分母退化成候选数 ⇒ K 变 1 ✗）"
    finite = frame["score"].replace([np.inf, -np.inf], np.nan).dropna()
    assert 0 < len(finite) < 100, "被拒的必须变 -inf 而不是留着 ✓，数量应为候选∩存活量级"


def test_hard_filter_compose_keeps_all_rows(monkeypatch):
    """硬规则同理：被剔的置 `-inf` ✓，**行数也不许少** ✓。"""
    idx = _idx()
    score = pd.Series(np.arange(len(idx), dtype=float), index=idx)
    bad = score.index[::2]                       # 一半被规则剔掉 ✓

    def _fake_filters(s, req):
        out = s.copy().astype(float)
        out.loc[bad] = -np.inf
        return out

    monkeypatch.setattr(sc, "apply_hard_filters", _fake_filters)
    req = _FakeReq()
    req.meta_gate = False                        # 只测硬规则分支 ✓
    req.hard_filters = {"min_price": 3.0}

    frame, _info = sc.compose_final_signal(None, _FakeModel(score), req, seg_label="段1")

    assert len(frame) == len(idx)
    assert int((frame["score"] == -np.inf).sum()) == len(bad)


def test_k_uses_full_pool_after_compose(monkeypatch):
    """★★ 口径闭环：**全池 4000 做分母** ⇒ K = 可用只数（~38 ✓）；旧口径（候选做分母）⇒ 1 只 ✗。"""
    idx = _idx()
    score = pd.Series(np.arange(len(idx), dtype=float), index=idx)
    z = pd.Series(np.random.RandomState(1).rand(len(idx)), index=idx)
    monkeypatch.setattr(sc, "train_gate", lambda *a, **k: (object(), {"reject_ratio": 0.25, "n": 10, "valid_auc": 0.6}))
    monkeypatch.setattr(sc, "gate_z", lambda *a, **k: z)

    frame, _info = sc.compose_final_signal(None, _FakeModel(score), _FakeReq(), seg_label="段1")
    finite = frame["score"].replace([np.inf, -np.inf], np.nan).dropna()
    st = _mk_strategy(topk_ratio=0.01)

    n_pool, n_avail = len(frame), len(finite)
    assert st._k_of(n_pool, n_avail) == n_avail, "★ 1%% × 全池 > 可用 ⇒ 买可用只数（不越界 ✓）"
    # ⚠ 负向对照：**旧行为**（删行后拿候选数当分母 ✗）⇒ 退化成 1 只 ✗ —— 这就是用户那次的事故 ✓
    assert st._k_of(n_avail, n_avail) == 1
