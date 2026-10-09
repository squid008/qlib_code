# -*- coding: utf-8 -*-
"""★ v1.20.82：保存公式时的**同名查重**（用户 2026-10-09「加个保存查重提示，我记得以前有查重啊」）。

背景：老查重在前端**只比正文完全相同**（`App.tsx` 里 `f.text.trim() === text`）✗ ——
正文差一个注释或一个空行就绕过去了 ✗。实测：`IS_GOLD_PIT`（益盟黄金坑）被存成两条同名，
列表里并排出现、`INSUM('全部A股','IS_GOLD_PIT',…)` 取"合并后首个"⇒ **取到哪条不确定** ✗。

修法：**后端按输出名查**（名字由编译器定 ⇒ 唯一权威 ✓），命中返回 409 + `conflict` ✓；
前端弹"覆盖它 / 仍新建一条 / 取消" ✓；`allow_duplicate=true` = 用户点了"仍新建一条" ✓。

本文件守两件事（都在**临时目录**里跑，绝不碰真实公式库 ✓）：
  ① `custom_formulas.find_live_by_name` 的口径：大小写/空白归一 ✓、跳过 tombstone ✓、
     排除自身 ✓、按名字（不是按正文）✓；
  ② 路由契约：`detail` 必须是**字符串** ✓（前端 `String(detail)` 直接显示 ⇒ 塞 dict 会变
     `[object Object]` ✗✗）、结构化的 `conflict` 挂在**同级** ✓、`allow_duplicate` 能放行 ✓。
"""
from __future__ import annotations

import json

import pytest
from fastapi.responses import JSONResponse

from app.services import custom_formulas as cf


@pytest.fixture()
def lib(tmp_path, monkeypatch):
    """把公式库指到临时目录、并掐掉 git 自动同步 ✓（见 `tests/test_formula_pull_rollback.py` ✓）。"""
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


def _item(fid: str, name: str, text: str, ts: str = "2026-10-09 01:50:10") -> dict:
    return {"id": fid, "name": name, "text": text, "expression": "Close($close)",
            "created_at": ts, "updated_at": ts, "author": "u-me", "author_user": "admin"}


class TestFindLiveByName:
    def test_finds_same_name(self, lib):
        lib([_item("a1", "IS_GOLD_PIT", "坑:CLOSE;")])
        hit = cf.find_live_by_name("IS_GOLD_PIT")
        assert hit is not None and hit["id"] == "a1"

    def test_name_is_the_key_not_text(self, lib):
        """★ 核心：正文不同、**名字相同** ⇒ 也算重名 ✓（这正是老前端查重漏掉的情形 ✗）。"""
        lib([_item("a1", "IS_GOLD_PIT", "坑:CLOSE;{注释}")])
        assert cf.find_live_by_name("IS_GOLD_PIT") is not None

    def test_case_and_space_insensitive(self, lib):
        """`PE` / `pe` / 带空白 ⇒ 同名 ✓（公式语言里变量名大小写不敏感 ✓）。"""
        lib([_item("a1", "PE", "PE:CLOSE;")])
        assert cf.find_live_by_name("pe") is not None
        assert cf.find_live_by_name("  P E  ") is not None

    def test_exclude_self_when_editing(self, lib):
        """编辑自己时**不能**撞自己（`exclude_id`）✓。"""
        lib([_item("a1", "PE", "PE:CLOSE;")])
        assert cf.find_live_by_name("PE", exclude_id="a1") is None
        assert cf.find_live_by_name("PE", exclude_id="other") is not None

    def test_tombstone_is_not_a_duplicate(self, lib):
        """已删（tombstone）的不算重名 ✓ ⇒ 删掉重名那条后，同名的能正常再存 ✓。"""
        lib([_item("a1", "PE", "PE:CLOSE;"),
             {"id": "a1", "name": "PE", "deleted": True, "updated_at": "2026-10-09 02:00:00"}])
        assert cf.find_live_by_name("PE") is None

    def test_tombstone_of_another_id_does_not_hide_live_duplicate(self, lib):
        """⚠ 只屏蔽**自己那个 id** ✓：删了别的同名条目，活体那条照样算重名 ✓。"""
        lib([_item("a1", "PE", "PE:CLOSE;"),
             {"id": "a2", "name": "PE", "deleted": True, "updated_at": "2026-10-09 02:00:00"}])
        hit = cf.find_live_by_name("PE")
        assert hit is not None and hit["id"] == "a1"

    def test_other_install_live_entry_also_counts(self, lib):
        """★ 别的装机文件里的同名条目**同样算重名** ✓ —— 它就在你实际加载/求值的库里 ✓。"""
        lib([])
        other = cf._MY_FILE.replace("me.json", "u-other.json")
        with open(other, "w", encoding="utf-8") as f:
            json.dump([_item("b1", "深跌90", "深跌90:CLOSE;")], f, ensure_ascii=False)
        assert cf.find_live_by_name("深跌90") is not None

    def test_empty_name_never_matches(self, lib):
        lib([_item("a1", "PE", "PE:CLOSE;")])
        assert cf.find_live_by_name("") is None
        assert cf.find_live_by_name("   ") is None


def _stub_router(monkeypatch, dup):
    """把路由层的编译/落盘/查库都换成桩 ✓（本文件只测**冲突契约**，不测编译 ✓）。"""
    from app.routers import factors as fr

    class _T:
        name = "IS_GOLD_PIT"
        expression = "$close"

    monkeypatch.setattr(fr, "_compile_formula_or_400", lambda *a, **k: _T())
    monkeypatch.setattr(fr, "_find_live_by_name", lambda *a, **k: dup)
    created: list = []
    updated: list = []
    monkeypatch.setattr(fr, "_create_custom_formula",
                        lambda *a, **k: created.append(a) or {"id": "new"})
    monkeypatch.setattr(fr, "_update_custom_formula",
                        lambda *a, **k: updated.append(a) or {"id": "upd"})
    return fr, created, updated


DUP = {"id": "a1", "name": "IS_GOLD_PIT", "updated_at": "2026-10-09 01:50:10",
       "text": "坑:CLOSE;", "author": "u-me"}


class TestCreateConflict:
    def test_conflict_is_409_with_string_detail_and_struct(self, monkeypatch):
        """★ 契约：409 + `detail` 为**字符串**（老前端 `String(detail)` 直接显示 ✓）+
        同级 `conflict`（新前端渲染三选按钮 ✓）。"""
        fr, created, _ = _stub_router(monkeypatch, DUP)
        resp = fr.create_saved_formula(fr.CustomFormulaBody(formula="坑:CLOSE;"))
        assert isinstance(resp, JSONResponse) and resp.status_code == 409
        body = json.loads(resp.body)
        assert isinstance(body["detail"], str) and "已存在同名公式" in body["detail"]
        assert "覆盖" in body["detail"] and "仍新建" in body["detail"]
        assert body["conflict"]["id"] == "a1" and body["conflict"]["text"] == "坑:CLOSE;"
        assert created == []                       # 撞名 ⇒ 绝不落盘 ✓

    def test_allow_duplicate_bypasses(self, monkeypatch):
        """用户点了「仍新建一条」⇒ `allow_duplicate=True` ⇒ 直接存 ✓。"""
        fr, created, _ = _stub_router(monkeypatch, DUP)
        resp = fr.create_saved_formula(
            fr.CustomFormulaBody(formula="坑:CLOSE;", allow_duplicate=True))
        assert not isinstance(resp, JSONResponse) and created, "应已调用落盘 ✓"

    def test_no_conflict_saves(self, monkeypatch):
        fr, created, _ = _stub_router(monkeypatch, None)
        fr.create_saved_formula(fr.CustomFormulaBody(formula="坑:CLOSE;"))
        assert created

    def test_default_allow_duplicate_is_false(self):
        """默认必须是 False ✓ —— 忘了传就成了「查重形同虚设」✗。"""
        assert fr_default() is False


def fr_default() -> bool:
    from app.routers import factors as fr
    return fr.CustomFormulaBody(formula="x").allow_duplicate


class TestUpdateConflict:
    def test_rename_into_existing_name_is_blocked(self, monkeypatch):
        """编辑成别人的名字 = 造重名 ⇒ 409 ✓，且**不给**"仍新建"（只拦 ✓）。"""
        fr, _, updated = _stub_router(monkeypatch, DUP)
        resp = fr.update_saved_formula("me1", fr.CustomFormulaBody(formula="坑:CLOSE;"))
        assert isinstance(resp, JSONResponse) and resp.status_code == 409
        body = json.loads(resp.body)
        assert isinstance(body["detail"], str) and "换个输出名" in body["detail"]
        assert updated == []                       # 不覆盖 ✓

    def test_ok_when_no_conflict(self, monkeypatch):
        fr, _, updated = _stub_router(monkeypatch, None)
        fr.update_saved_formula("me1", fr.CustomFormulaBody(formula="坑:CLOSE;"))
        assert updated


if __name__ == "__main__":
    pytest.main([__file__, "-q"])
