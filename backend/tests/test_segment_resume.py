# -*- coding: utf-8 -*-
"""**断点续跑**的分段落盘/读取（★ v2.11.66）。

## 为什么值得测（且不需要任何行情数据 ✓）
`qlib_engine` 覆盖率只有 **32.9%** ✓，但它大半要真 qlib + 训练 ✗。
而 `_save_segment_result` / `_load_segment_result` 是**断点续跑的核心** ✓：
· 跑到一半取消/断电 ⇒ 下次从已完成段续 ✓；
· 存歪了（字段漏、编码错、口径没戳）⇒ **续跑结果和一次跑完不一致** ✗ ——
  而这种不一致**不报错** ✓，只是数字悄悄变了 ✗（最讨厌的那类 ✓）。

⇒ 钉四条：**往返一致 / 缺段返回 None / 每段独立文件 / 口径不符视作无效** ✓。
最后一条是 v1.20.48 的真事故：2026-09-21 夜"前几段旧筹码 + 后几段新筹码 ⇒ 净值虚高"✗。
"""
from __future__ import annotations

import pandas as pd
import pytest

from app.engine import qlib_engine as QE


def _nav(n: int = 5) -> list:
    idx = pd.bdate_range("2024-01-01", periods=n)
    return [(str(d.date()), 1.0 + 0.01 * i) for i, d in enumerate(idx)]


def _trades() -> pd.DataFrame:
    return pd.DataFrame({
        "date": ["2024-01-02", "2024-01-03"],
        "code": ["SH600000", "SH600000"],
        "side": ["buy", "sell"],
        "qty": [1000, 1000],
        "price": [10.5, 10.8],
    })


def _save(art: str, seg: int = 1, nav=None, total_return: float = 0.0123) -> None:
    """按**真实签名**（11 个参数 ✓）存一段 —— 我第一次只传 6 个 ⇒ TypeError ✗。"""
    QE._save_segment_result(
        art, seg, nav if nav is not None else _nav(), _trades(),
        total_return, 1_000_123.45, 0.004, {}, "2024-01-01", "2024-01-31",
    )


def test_roundtrip(tmp_path):
    """⭐⭐ 存 → 读：段号 / 日期区间 / 净值点 / 总收益 都要原样回来 ✓。"""
    art = str(tmp_path / "art")
    _save(art, 1)

    got = QE._load_segment_result(art, 1)
    assert got is not None, "刚存的分段读不回来 ✗（续跑会从头再跑一遍 ✓）"
    data, test_pl, train_pl = got
    assert data["seg_no"] == 1
    assert data["date_range"] == ["2024-01-01", "2024-01-31"]
    assert [p[0] for p in data["nav"]] == [p[0] for p in _nav()], "净值点的日期序列变了 ✗"
    assert [float(p[1]) for p in data["nav"]] == pytest.approx([p[1] for p in _nav()])
    assert float(data["total_return"]) == pytest.approx(0.0123)
    assert test_pl is None and train_pl is None, "没存 pkl ⇒ 应为 None ✓"


def test_missing_segment_returns_none(tmp_path):
    """没存过的段 ⇒ `None`（调用方据此判断"这次要真跑"✓ —— 抛错会让续跑整体失败 ✗）。"""
    art = str(tmp_path / "empty")
    assert QE._load_segment_result(art, 7) is None


def test_each_segment_has_its_own_file(tmp_path):
    """每段一个文件 ✓ —— 撞名会导致"续跑用错段"✗。"""
    p1 = QE._seg_result_path(str(tmp_path), 1)
    p2 = QE._seg_result_path(str(tmp_path), 2)
    assert p1 != p2
    assert "segment_1" in p1.replace("\\", "/")
    assert "segment_2" in p2.replace("\\", "/")


def test_save_overwrites(tmp_path):
    """同一段重存必须覆盖 ✓（不能残留上一轮 ⇒ 否则续跑读到旧值 ✗）。"""
    art = str(tmp_path / "art2")
    _save(art, 3, nav=_nav(2), total_return=0.01)
    _save(art, 3, nav=_nav(4), total_return=0.02)
    got = QE._load_segment_result(art, 3)
    assert got is not None
    data = got[0]
    assert len(data["nav"]) == 4, "重存没覆盖 ✗（读到的是旧的 %d 个点）" % len(data["nav"])
    assert float(data["total_return"]) == pytest.approx(0.02)


def test_semantics_mismatch_invalidates_cache(tmp_path, monkeypatch):
    """⭐⭐ v1.20.48 的真事故：**口径不符的段缓存必须视作无效** ✓。

    不加这条校验的后果（2026-09-21 夜实测）：前几段用旧筹码口径、后几段用新的 ✓
    ⇒ 拼出来的净值**虚高且不可信** ✗，而且**一声不吭** ✓。
    """
    art = str(tmp_path / "art3")
    _save(art, 1)
    assert QE._load_segment_result(art, 1) is not None, "前提：同口径时能读到 ✓"

    monkeypatch.setattr(QE, "TRAIN_SEMANTICS", "another-semantics-v9")
    assert QE._load_segment_result(art, 1) is None, (
        "训练口径变了却仍然读到了旧段 ✗ —— 续跑会拼出两套口径的净值 ✓"
    )


def test_no_art_dir_is_safe():
    """`art_dir` 为空 ⇒ 存也静默返回、读也返回 None ✓（不能抛 ✗ —— 它被当"可选能力"用 ✓）。"""
    assert QE._load_segment_result("", 1) is None
    QE._save_segment_result("", 1, _nav(), _trades(), 0.0, 0.0, 0.0, {}, "a", "b")
