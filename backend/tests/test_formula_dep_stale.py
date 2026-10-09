# -*- coding: utf-8 -*-
"""★ v1.20.87：改**被调用公式**的正文 ⇒ 引用它的公式**自动**待重编（用户 2026-10-09 追问「能不能避免」✓）。

背景（用户原文）：「以后改'被调用公式'的正文，依赖它的公式必须重存一遍 —— 因为回测按原文重编 ✓，
但单因子测试/事件研究用的是缓存 expression ✗ ⇒ 会出现'回测是对的、单因子测试还是旧值'这种最误导的现象。
**那能不能避免呢？**」

能 ✓：保存/编辑公式时把**引用它的公式**（含**传递**依赖 ✓）的 `codegen` 戳清空 ✓ ⇒ 下次读列表时被
`refresh_stale_expressions()` 自动按新正文重编 ✓（失败只保留旧串、不会 500 ✓）。

本文件守四件事（都在临时目录里跑 ✓ 不碰真实公式库 ✓）：
  ① 直接依赖会被标脏 + 重编成新正文 ✓；
  ② **传递**依赖（A→B→C 改 C ⇒ B、A 都要脏 ✓）；
  ③ 整词匹配：`CPX` 不能把 `CPX2` 标脏、`深跌10` 不能把 `深跌100` 标脏 ✓✗；
  ④ 不误伤自己、也不动"已经是待重编"的条目（幂等 ✓）。
"""
from __future__ import annotations

import json

import pytest

from app.factors.parser.codegen import CODEGEN_SEMANTICS
from app.services import custom_formulas as cf


@pytest.fixture()
def lib(tmp_path, monkeypatch):
    d = tmp_path / "formulas"
    monkeypatch.setattr(cf, "_FORMULAS_DIR", str(d))
    monkeypatch.setattr(cf, "_MY_FILE", str(d / "me.json"))
    monkeypatch.setattr(cf, "_CUSTOM_FORMULAS_PATH", str(tmp_path / "legacy.json"))
    monkeypatch.setattr(cf, "_USER", "admin")
    monkeypatch.setattr(cf._formula_git_sync, "schedule", lambda *a, **k: None)
    monkeypatch.setattr(cf, "_migrated", True, raising=False)
    d.mkdir(parents=True, exist_ok=True)

    def write(items):
        (d / "me.json").write_text(json.dumps(items, ensure_ascii=False), encoding="utf-8")

    return write


def _item(fid, name, text, expr="Close($close)"):
    return {"id": fid, "name": name, "text": text, "expression": expr,
            "codegen": CODEGEN_SEMANTICS, "created_at": "2026-10-09 00:00:00",
            "updated_at": "2026-10-09 00:00:00", "author": "u-me", "author_user": "admin"}


def _codes(name_or_id):
    return {it["name"]: it.get("codegen") for it in cf.load_merged()}


class TestMarkDependents:
    def test_direct_dependency_marked(self, lib):
        lib([_item("b1", "CPX", "CPX:MA(CLOSE,5);"),
             _item("a1", "基础", "基础:CPX>0 AND C>MA(C,5);")])
        changed = cf.mark_dependents_stale("CPX", exclude_id="b1")
        assert changed == ["基础"]
        assert _codes("")["基础"] == ""                     # 戳被清空 ⇒ 待重编 ✓
        assert _codes("")["CPX"] == CODEGEN_SEMANTICS        # 被改的那条不受影响 ✓

    def test_transitive_dependency_marked(self, lib):
        """A→B→C：改 C ⇒ B、A 都要被标脏 ✓（只标一层的话 A 会永远停在旧正文 ✗）。"""
        lib([_item("c1", "C", "C:MA(CLOSE,5);"),
             _item("b1", "B", "B:C>0;"),
             _item("a1", "A", "A:B>0;")])
        changed = set(cf.mark_dependents_stale("C", exclude_id="c1"))
        assert changed == {"B", "A"}
        assert _codes("")["A"] == "" and _codes("")["B"] == ""

    def test_whole_word_only(self, lib):
        """整词匹配：`CPX` 不该把 `CPX2` 标脏、`深跌10` 不该把 `深跌100` 标脏 ✓✗。"""
        lib([_item("b1", "CPX", "CPX:MA(CLOSE,5);"),
             _item("x1", "CPX2", "CPX2:MA(CLOSE,10);"),
             _item("s1", "深跌10", "深跌10:MA(CLOSE,10);"),
             _item("s2", "深跌100", "深跌100:MA(CLOSE,100);")])
        assert cf.mark_dependents_stale("CPX", exclude_id="b1") == []
        assert cf.mark_dependents_stale("深跌10", exclude_id="s1") == []
        assert all(v == CODEGEN_SEMANTICS for v in _codes("").values())

    def test_excludes_self_and_is_idempotent(self, lib):
        """输出名出现在自己正文里时，**不能**把自己标脏 ✗；重复调用不产生新变化 ✓。"""
        lib([_item("b1", "CPX", "CPX:MA(CLOSE,5);"),
             _item("a1", "基础", "基础:CPX>0;")])
        assert cf.mark_dependents_stale("CPX", exclude_id="b1") == ["基础"]
        assert cf.mark_dependents_stale("CPX", exclude_id="b1") == []      # 幂等 ✓

    def test_tombstone_ignored(self, lib):
        """墓碑（已删）不参与标记 ✓。

        ⚠ 两种情形要分开看（第一版用例想当然写错 ✗，实测才看清 ✓）：
          · 该 id **只有墓碑**（活体行已不在文件里 ✓）⇒ 不会被标 ✓；
          · 文件里仍留着**同 id 的旧活体行** + 更新时间的墓碑（合并视图里被屏蔽 ✓）——
            此时旧的活体行**会被标脏**，但它已经被隐藏、标了也没任何影响 ✓（无害 ✓）。
        """
        lib([_item("b1", "CPX", "CPX:MA(CLOSE,5);"),
             {"id": "a9", "name": "已删公式", "deleted": True,
              "updated_at": "2026-10-09 01:00:00", "text": "已删公式:CPX>0;"}])
        assert cf.mark_dependents_stale("CPX", exclude_id="b1") == []


class TestRefreshRebuildsFromNewBody:
    def test_dependent_gets_new_body_after_marking(self, lib):
        """★ 端到端：标脏 + `refresh_stale_expressions` ⇒ 依赖方缓存变成**新正文**的展开 ✓。"""
        lib([_item("b1", "CPX", "CPX:MA(CLOSE,5);", expr="Mean($close,5)"),
             _item("a1", "基础", "基础:CPX>0;", expr="Gt(Mean($close,4),0)")])   # ← 旧正文展开 ✗
        cf.mark_dependents_stale("CPX", exclude_id="b1")
        items = cf.load_merged()
        assert cf.refresh_stale_expressions(items) is True
        got = {it["name"]: it["expression"] for it in items}
        assert got["基础"] == "Gt(Mean($close,5),0)", "依赖方必须按**新**正文重编 ✓"


if __name__ == "__main__":
    pytest.main([__file__, "-q"])
