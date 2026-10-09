# -*- coding: utf-8 -*-
"""★ v1.20.87：② 物化"内容过期"能被发现 ／ ③ `mkt_*` 缺失的**现算回退**（用户 2026-10-09）。

用户的三个追问：
  ① 「以后改被调用公式的正文，依赖它的公式必须重存一遍 —— **那能不能避免呢？**」（已做：v1.20.87
     的 `mark_dependents_stale` ✓，见 `test_formula_dep_stale.py` ✓）
  ② 「我改了公式，涉及的**物化文件**也要变，**能不能提示呢？**」
  ③ 「如果 IS_GOLD_PIT 我把**物化文件删了**，它会**自动回退**不走物化路线计算吗？」

②③ 的关键事实（本文件钉住）：
  · 物化字段名里的 6 位十六进制是**公式名**的哈希（`..._is_gold_pit_7758f0_...` ✓）⇒ 改正文后
    **文件名不变、内容却是旧口径** ✗✗ ⇒ "整列全 NaN 才告警"那道防线**看不见它** ✗；
    ⇒ 物化时把被调公式的**编译后 expression 指纹**写进 meta ✓，比对 ⇒ 能喊出"该重跑物化" ✓；
  · 指纹用**编译结果**而不是原文 ✓ ⇒ 改注释/空行**不误报** ✓（只有语义真变才报 ✓）；
  · ③ 的退路必须与物化**逐位同口径** ⇒ 用 `all_codes()` 全市场池（**不是**当前面板的池 ✗，
    否则"全A股坑数量"会按 300 只算 ⇒ 静默偏小 ✗✗）。
"""
from __future__ import annotations

import json
import os

import pytest

from app.factors import market_stat as ms


@pytest.fixture()
def meta(tmp_path, monkeypatch):
    """把口径戳指向临时文件（不碰真实 `_market_meta.json` ✓）。"""
    p = tmp_path / "_market_meta.json"
    p.write_text("{}", encoding="utf-8")
    monkeypatch.setattr(ms, "market_meta_path", lambda: str(p))
    return p


def _specs():
    return [ms.Spec("全部A股", "IS_GOLD_PIT", 1, 0)]


class TestFingerprint:
    def test_fingerprint_uses_compiled_expression(self, monkeypatch):
        """指纹 = 编译结果（⚠ 编译表达式一变，指纹就必须变 ✓）。"""
        monkeypatch.setattr(ms, "formula_expression", lambda n: "Lt(Less(40,Mul($low,1)),0)")
        fp1 = ms.formula_fingerprints(_specs())
        monkeypatch.setattr(ms, "formula_expression", lambda n: "Gt($close,0)")
        fp2 = ms.formula_fingerprints(_specs())
        assert set(fp1) == {"IS_GOLD_PIT"} and len(fp1["IS_GOLD_PIT"]) == 12
        assert fp1["IS_GOLD_PIT"] != fp2["IS_GOLD_PIT"]

    def test_missing_formula_is_skipped(self, monkeypatch):
        def boom(_n):
            raise ValueError("库里没有这个公式")

        monkeypatch.setattr(ms, "formula_expression", boom)
        assert ms.formula_fingerprints(_specs()) == {}

    def test_no_specs_no_fingerprints(self):
        assert ms.formula_fingerprints([]) == {}


class TestStaleDetection:
    def test_stale_detected_after_body_change(self, meta, monkeypatch):
        """★ 核心：物化后改了正文 ⇒ `stale_formula_names()` 必须报出这个公式 ✓。"""
        monkeypatch.setattr(ms, "formula_expression", lambda n: "旧正文编译结果")
        json.dump({"formula_fingerprints": ms.formula_fingerprints(_specs())},
                  meta.open("w", encoding="utf-8"), ensure_ascii=False)
        assert ms.stale_formula_names() == []                 # 没改 ⇒ 一致 ✓

        monkeypatch.setattr(ms, "formula_expression", lambda n: "新正文编译结果")
        assert ms.stale_formula_names() == ["IS_GOLD_PIT"]     # 改了 ⇒ 报出 ✓

    def test_old_meta_without_fingerprint_does_not_alarm(self, meta):
        """老物化（meta 里还没指纹 ✓）⇒ **不误报** ✓（此时由既有口径戳提示兜底 ✓）。"""
        json.dump({"semantics": ms.MARKET_SEMANTICS},
                  meta.open("w", encoding="utf-8"), ensure_ascii=False)
        assert ms.stale_formula_names() == []

    def test_missing_meta_returns_empty(self, tmp_path, monkeypatch):
        monkeypatch.setattr(ms, "market_meta_path",
                            lambda: str(tmp_path / "nope" / "_market_meta.json"))
        assert ms.stale_formula_names() == []


class TestInlineFallback:
    def test_unknown_field_returns_none(self, meta, monkeypatch):
        """meta 里没登记、公式库里也推不出 ⇒ 返回 None ✓（让既有告警照旧出声 ✓）。"""
        monkeypatch.setattr(ms, "discover_specs", lambda *a, **k: [])
        ms._INLINE_CACHE.clear()
        assert ms.compute_field_inline("mkt_insum_unknown_ffffff_1_0") is None

    def test_blocksetnum_is_cheap_path(self, meta, monkeypatch):
        """`BLOCKSETNUM` 不需要求值被调公式 ⇒ 只走掩码求和（便宜 ✓）—— 用假日历/假池验证 ✓。"""
        field = ms.blocksetnum_field("全部A股").lstrip("$")
        json.dump({"start_time": "2025-01-01", "specs": [
            {"kind": "BLOCKSETNUM", "block": "全部A股", "formula": None,
             "out_index": 1, "calc_type": -1, "field": field}]},
            meta.open("w", encoding="utf-8"), ensure_ascii=False)
        import pandas as pd
        monkeypatch.setattr(ms, "_calendar", lambda: pd.date_range("2025-01-02", periods=3, freq="B"))
        monkeypatch.setattr(ms, "all_codes", lambda: ["sh600000", "sz000001"])
        monkeypatch.setattr(ms, "block_mask", lambda key, codes, cal: __import__("numpy").ones(
            (len(cal), len(codes)), dtype=bool))
        ms._INLINE_CACHE.clear()
        got = ms.compute_field_inline(field)
        assert got is not None and list(got.to_numpy()) == [2.0, 2.0, 2.0]
        assert ms.compute_field_inline(field) is got            # 命中缓存 ⇒ 不重算 ✓
        ms._INLINE_CACHE.clear()


if __name__ == "__main__":
    pytest.main([__file__, "-q"])
