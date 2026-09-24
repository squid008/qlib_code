# -*- coding: utf-8 -*-
"""★ v1.20.65：公式库**按人分文件 + 读取时合并**的单测（用户 2026-09-24 的诉求 ✓）。

动机：原先所有人共用 `workdir/custom_formulas.json`（且纳入 git ✓）⇒ 两个人各自保存过公式后，
`git pull` 时 **JSON 必然冲突** ✗（`.gitattributes` 不存在、逐行合并对 JSON 无效 ✗）⇒ 要么丢东西 ✗。
⇒ 改为 `formulas/<user>.json`（每人一份 ✓）⇒ git 只新增文件 ⇒ **天然无冲突** ✓✓。
"""
from __future__ import annotations

import json
import os

import pytest

from app.services import custom_formulas as cf


def _write(path: str, items: list) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(items, f, ensure_ascii=False)


@pytest.fixture(autouse=True)
def _isolate(tmp_path, monkeypatch):
    """把三个路径都指到临时目录 ✓（绝不碰真实公式库 ✓）。"""
    d = tmp_path / "formulas"
    monkeypatch.setattr(cf, "_FORMULAS_DIR", str(d))
    monkeypatch.setattr(cf, "_MY_FILE", str(d / "me.json"))
    monkeypatch.setattr(cf, "_CUSTOM_FORMULAS_PATH", str(tmp_path / "legacy.json"))
    return d


def _item(fid: str, name: str, text: str = "OUT:CLOSE;", ts: str = "2026-01-01 00:00:00") -> dict:
    return {"id": fid, "name": name, "text": text, "expression": "Close($close)",
            "codegen": cf.CODEGEN_SEMANTICS, "updated_at": ts}


def test_merge_reads_all_user_files(_isolate):
    """★ 合并读取：我和同事的文件**都能看到** ✓。"""
    _write(str(_isolate / "me.json"), [_item("a1", "我的公式")])
    _write(str(_isolate / "bob.json"), [_item("b1", "同事的公式")])
    names = sorted(x["name"] for x in cf.load_merged())
    assert names == ["同事的公式", "我的公式"]


def test_my_file_wins_on_same_id(_isolate):
    """★ 同 id 冲突 ⇒ **我的文件胜** ✓（谁也覆盖不了我 ✓）。"""
    _write(str(_isolate / "me.json"), [_item("x1", "我改过的", ts="2026-01-01 00:00:00")])
    _write(str(_isolate / "bob.json"),
           [_item("x1", "同事改的", ts="2026-09-01 00:00:00")])       # 时间更新也压不过我 ✓
    got = cf.load_merged()
    assert len(got) == 1 and got[0]["name"] == "我改过的"


def test_other_users_newer_wins(_isolate):
    """★ 别人之间（同优先级）⇒ `updated_at` **较新**者胜 ✓（不靠文件 mtime ✗）。"""
    _write(str(_isolate / "bob.json"), [_item("y1", "bob旧", ts="2026-01-01 00:00:00")])
    _write(str(_isolate / "carol.json"), [_item("y1", "carol新", ts="2026-05-01 00:00:00")])
    got = cf.load_merged()
    assert len(got) == 1 and got[0]["name"] == "carol新"


def test_tombstone_hides_other_users_entry(_isolate):
    """★ 删除**别人的**条目 ⇒ 自己文件写 tombstone（删除标记）⇒ 合并时被隐藏 ✓（不动别人文件 ✓）。"""
    _write(str(_isolate / "bob.json"), [_item("z1", "同事的")])
    _write(str(_isolate / "me.json"), [{"id": "z1", "deleted": True, "updated_at": "2026-09-24 00:00:00"}])
    assert cf.load_merged() == []
    # ⚠ 同事的文件必须**原样不动** ✓（否则又制造冲突 ✗）
    with open(str(_isolate / "bob.json"), encoding="utf-8") as f:
        assert json.load(f)[0]["name"] == "同事的"


def test_create_writes_only_my_file(_isolate):
    """新建 ⇒ **只写自己的文件** ✓；别人的文件不动 ✓。"""
    _write(str(_isolate / "bob.json"), [_item("b1", "同事的")])
    item = cf.create_custom_formula("新公式", "OUT:CLOSE;", "Close($close)")
    assert item["author"]                                   # 标了作者 ✓
    mine = json.load(open(str(_isolate / "me.json"), encoding="utf-8"))
    assert [x["name"] for x in mine] == ["新公式"]
    bob = json.load(open(str(_isolate / "bob.json"), encoding="utf-8"))
    assert [x["name"] for x in bob] == ["同事的"]
    assert sorted(x["name"] for x in cf.list_custom_formulas()) == ["同事的", "新公式"]


def test_update_other_users_entry_writes_override(_isolate):
    """改**别人的**条目 ⇒ 自己的文件里存一份**覆盖副本** ✓（不去动别人的文件 ✓）。"""
    _write(str(_isolate / "bob.json"), [_item("b1", "同事的")])
    got = cf.update_custom_formula("b1", "同事的(我改了)", "OUT:CLOSE;", "Close($close)")
    assert got and got["name"] == "同事的(我改了)"
    bob = json.load(open(str(_isolate / "bob.json"), encoding="utf-8"))
    assert bob[0]["name"] == "同事的"                        # 同事那份没被改 ✓
    merged = cf.load_merged()
    assert len(merged) == 1 and merged[0]["name"] == "同事的(我改了)"


def test_delete_own_entry_removes_it(_isolate):
    """删自己的条目 ⇒ 直接移除 ✓（不写多余 tombstone ✓）。"""
    cf.create_custom_formula("我的", "OUT:CLOSE;", "Close($close)")
    fid = json.load(open(str(_isolate / "me.json"), encoding="utf-8"))[0]["id"]
    assert cf.delete_custom_formula(fid) is True
    assert json.load(open(str(_isolate / "me.json"), encoding="utf-8")) == []
    assert cf.load_merged() == []


def test_legacy_file_is_read_but_never_written(_isolate, tmp_path):
    """★ 老的单文件（`custom_formulas.json` ✓）**只读** ✓ ⇒ 平滑迁移、老公式不丢 ✓。"""
    _write(str(tmp_path / "legacy.json"), [_item("L1", "老公式")])
    assert [x["name"] for x in cf.load_merged()] == ["老公式"]
    cf.create_custom_formula("新的", "OUT:CLOSE;", "Close($close)")
    legacy = json.load(open(str(tmp_path / "legacy.json"), encoding="utf-8"))
    assert [x["name"] for x in legacy] == ["老公式"]          # 老文件没被改写 ✓


def test_my_newer_overrides_legacy(_isolate, tmp_path):
    """自己文件与老文件同 id ⇒ **自己胜** ✓（优先级 0 < 2 ✓）。"""
    _write(str(tmp_path / "legacy.json"), [_item("k1", "老版本")])
    _write(str(_isolate / "me.json"), [_item("k1", "我的新版")])
    got = cf.load_merged()
    assert len(got) == 1 and got[0]["name"] == "我的新版"
