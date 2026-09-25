# -*- coding: utf-8 -*-
"""★ v1.20.73：`pull` 的「撤销未推送的删除」必须**连被删公式的本体一起还回来** ✓
+ 「**同名绝不覆盖**」的锁 ✓。

【为什么加这组测试 —— 2026-09-25 实测事故】
  用户本机独有的公式 `深跌2`，在界面上删掉后（**没 push**）跑 `formula_sync.py pull`：
    · pull 按约定撤销了那条"未推送的删标记" ✓，脚本还打印了"被删公式恢复显示 ✓" ✗✗；
    · **但公式其实没回来** ✗ —— 因为 App 的删除是「**把活体条目整条替换成 tombstone**」
      （`delete_custom_formula` ✓）⇒ body 早就没了 ✗，只删 tombstone 恢复不出东西 ✗。
    · 症状：本机合并可见比远端视角**少 1 条**（丢的正是那条本机独有公式 ✗）。
  ⇒ 修法：回滚时从**已推送的副本**（`origin/main:<本装机文件>`）把同 id 的活体条目捞回来 ✓
    （删除没推送过 ⇒ 那边仍是活体 ✓，这正是"没 push 就不算数"在 git 层的体现 ✓）。

【用户 2026-09-25 定稿的第二条语义（本文件同样上锁）】
  「我修改了一堆公式但忘记 push，结果 pull 一下把我改过的公式全覆盖了」**绝不允许** ✗ ——
  合并/回滚的**身份是 `id`**，`name` **从不参与裁决** ⇒ 同名不同 id 的两条**并列共存** ✓
  （观察到的现象：改过的 `深跌3` 与 git 里的 `深跌3` 同时存在、**没有被覆盖** ✓ —— 这就是要的 ✓）。
"""
from __future__ import annotations

import json
import os

import pytest

from app.services import custom_formulas as cf


def _write(path: str, items: list) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(items, f, ensure_ascii=False, indent=2)


def _read(path: str) -> list:
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


@pytest.fixture(autouse=True)
def _isolate(tmp_path, monkeypatch):
    """把公式路径指到临时目录、并把 git 自动同步打成空操作 ✓（绝不碰真实公式库/仓库 ✓）。"""
    d = tmp_path / "formulas"
    monkeypatch.setattr(cf, "_FORMULAS_DIR", str(d))
    monkeypatch.setattr(cf, "_MY_FILE", str(d / "me.json"))
    monkeypatch.setattr(cf, "_CUSTOM_FORMULAS_PATH", str(tmp_path / "legacy.json"))
    monkeypatch.setattr(cf, "_USER", "admin")
    monkeypatch.setattr(cf._formula_git_sync, "schedule", lambda *a, **k: None)
    monkeypatch.delenv("QUANT_FORMULA_USER", raising=False)
    return d


def _item(fid: str, name: str, text: str = "OUT:CLOSE;", ts: str = "2026-01-01 00:00:00") -> dict:
    return {"id": fid, "name": name, "text": text, "expression": "Close($close)",
            "codegen": cf.CODEGEN_SEMANTICS, "updated_at": ts}


def _tomb(fid: str, ts: str = "2026-09-25 08:15:25") -> dict:
    return {"id": fid, "deleted": True, "updated_at": ts,
            "author": "u-1dc0ce59", "author_user": "admin"}


# ---------------------------------------------------------------------------
# ① 回滚：未推送的删除 ⇒ 连 body 一起还回来（事故现场）
# ---------------------------------------------------------------------------
def test_rollback_restores_body_from_pushed_copy(_isolate):
    """本机独有公式「删了没 push」⇒ pull 撤销删除后，**正文必须回来** ✓（原实现只删 tombstone ✗）。"""
    deep2 = _item("586d0f987b97", "深跌2", text="OUT:MA(CLOSE,2);", ts="2026-09-25 09:29:20")
    mine = [_item("keep1", "别的公式", ts="2026-09-20 10:00:00"), _tomb("586d0f987b97")]
    keep, restored = cf.rollback_unpushed_deletes(mine, [deep2], ["586d0f987b97"])

    assert [x["name"] for x in restored] == ["深跌2"]                  # 还原了哪条 ✓
    assert restored[0]["text"] == "OUT:MA(CLOSE,2);"                   # **本体**（不只是 id ✓）
    assert not any(x.get("deleted") for x in keep)                     # tombstone 已撤销 ✓
    assert {x["id"] for x in keep} == {"keep1", "586d0f987b97"}        # 别的条目一条不动 ✓
    # 按 updated_at 归位（09-20 在前、09-25 在后 ✓）⇒ 文件顺序稳定、diff 小 ✓
    assert [x["id"] for x in keep] == ["keep1", "586d0f987b97"]


def test_rollback_leaves_pushed_deletes_and_local_edits_alone(_isolate):
    """⚠ 已推送的删除**绝不**回滚 ✓；本机新增/编辑**一律保留** ✓。"""
    mine = [
        _item("edited1", "我改过的", text="OUT:CLOSE;", ts="2026-09-25 09:53:03"),   # 本机编辑 ✓
        _item("new1", "我新建的", ts="2026-09-25 09:55:00"),                          # 本机新建 ✓
        _tomb("pushed_del", ts="2026-09-24 12:00:00"),                               # 已推送的删除 ✓
        _tomb("unpushed_del", ts="2026-09-25 09:52:39"),                             # 未推送的删除 ✓
    ]
    body = _item("unpushed_del", "被误删的", text="OUT:HIGH;", ts="2026-09-25 09:00:00")
    keep, restored = cf.rollback_unpushed_deletes(mine, [body], ["unpushed_del"])

    assert [x["id"] for x in restored] == ["unpushed_del"]
    ids = {x["id"] for x in keep}
    assert ids == {"edited1", "new1", "pushed_del", "unpushed_del"}      # 只多回被撤销的那条 ✓
    assert any(x.get("deleted") and x["id"] == "pushed_del" for x in keep)   # 已推送删除保留 ✓


def test_rollback_without_pushed_body_only_drops_tombstone(_isolate):
    """已推送副本里**没有**活体条目（本机建的、从没 push 过）⇒ 只撤销 tombstone，不凭空造条目 ✓。"""
    mine = [_tomb("never_pushed"), _item("keep1", "别的")]
    keep, restored = cf.rollback_unpushed_deletes(mine, [], ["never_pushed"])
    assert restored == []
    assert [x["id"] for x in keep] == ["keep1"]


def test_rollback_noop_when_nothing_to_rollback(_isolate):
    mine = [_item("a1", "甲"), _tomb("pushed_del")]
    keep, restored = cf.rollback_unpushed_deletes(mine, [], [])
    assert restored == [] and keep == mine


def test_delete_then_rollback_roundtrip(_isolate):
    """端到端复现事故 + 验证修法：`delete_custom_formula` 会把 body 抹掉 ✗ ⇒ 用已推送副本还原 ✓。"""
    live = _item("d1", "深跌3", text="OUT:LLV(CLOSE,3);", ts="2026-09-25 09:47:07")
    _write(str(_isolate / "me.json"), [live])
    pushed = list(_read(str(_isolate / "me.json")))          # 删除**之前**的已推送副本 ✓

    assert cf.delete_custom_formula("d1") is True            # App 删除 ⇒ 活体被替换成 tombstone ✗
    loc = _read(str(_isolate / "me.json"))
    assert not any(x.get("name") == "深跌3" for x in loc)     # ⇒ 这就是"只删 tombstone 恢复不了"的成因 ✓

    keep, restored = cf.rollback_unpushed_deletes(loc, pushed, ["d1"])
    assert [x["name"] for x in restored] == ["深跌3"]
    back = [x for x in keep if x["id"] == "d1"]
    assert back and back[0].get("text") == "OUT:LLV(CLOSE,3);"   # **本体**回来了 ✓


# ---------------------------------------------------------------------------
# ② 用户定稿：「同名公式**绝不**互相覆盖」（身份 = id，name 从不参与裁决 ✓）
# ---------------------------------------------------------------------------
def test_same_name_different_ids_coexist(_isolate):
    """我改过的 `深跌3`（新 id、未推送）与 git 里的 `深跌3`（旧 id）**并列共存、谁都不覆盖谁** ✓。"""
    _write(str(_isolate / "me.json"),
           [_item("43fc64eb9a77", "深跌3", text="OUT:CLOSE-OPEN;", ts="2026-09-25 09:53:03")])
    _write(str(_isolate / "u-others.json"),
           [_item("6ba47d63d40a", "深跌3", text="OUT:CLOSE;", ts="2026-09-25 09:47:07")])

    got = cf.load_merged()
    assert sorted(x["id"] for x in got) == ["43fc64eb9a77", "6ba47d63d40a"]   # 两条都在 ✓
    new = [x for x in got if x["id"] == "43fc64eb9a77"][0]
    assert new["text"] == "OUT:CLOSE-OPEN;"          # ★ 我的改动**没被**同名条目覆盖 ✓


def test_same_name_old_ts_is_not_overwritten_by_newer_peer(_isolate):
    """即使对方那份 `updated_at` **更新**，它也不该顶掉我的同名条目（同名 ≠ 同一条 ✓）。"""
    _write(str(_isolate / "me.json"),
           [_item("mine1", "深跌3", text="OUT:MY-VERSION;", ts="2026-01-01 00:00:00")])
    _write(str(_isolate / "u-others.json"),
           [_item("peer1", "深跌3", text="OUT:PEER-VERSION;", ts="2026-09-01 00:00:00")])

    got = cf.load_merged()
    assert len(got) == 2                                        # 同名两条都在 ✓
    assert {x["text"] for x in got} == {"OUT:MY-VERSION;", "OUT:PEER-VERSION;"}
    assert [x for x in got if x["id"] == "mine1"][0]["text"] == "OUT:MY-VERSION;"   # 我的那份在位 ✓
