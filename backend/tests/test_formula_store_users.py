# -*- coding: utf-8 -*-
"""★ v1.20.69：公式库「**每装机一个文件** + 只增不减（时间戳裁决）」的单测。

为什么又改一次（用户在 2026-09-24 追问"两台机器都叫 admin 不就又覆盖了吗？是不是该只增不减、
不用管是不是同一个人？" —— **完全正确** ✓）：v1.20.65 的「按人分文件 + 身份优先合并」有两个洞
  ① 隔离**依赖"两端用户名不同"**这个不可靠假设 ✗ ⇒ 都叫 `admin`/`Administrator`（或同账号在两台
     登录）时两个后端写**同一个文件** ⇒ `git pull` 又回到"整文件 JSON 冲突" ✗；
  ② 旧合并规则「自己恒胜」（连 `updated_at` 都不看 ✗）+ `list_custom_formulas` 会把**别人的条目
     抄进自己文件** ✗ ⇒ 只要 `CODEGEN_SEMANTICS` 升一次，就能把对方**全部条目**抄成"我的高优先
     副本" ⇒ 此后对方再改这些公式，我这边**永远看不到** ✗✗。
⇒ v1.20.69：写入键 = **装机 ID**（与用户名无关 ✓）、裁决 = **`updated_at`**（不看是谁 ✓）、
  删除 = **带戳 tombstone**（只屏蔽不物理删 ⇒ "只增不减"，之后被编辑还能复活 ✓）、
  别人的条目**只读、不回写** ✓（掐掉遮蔽源 ✓）。
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


def _read(path: str) -> list:
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


@pytest.fixture(autouse=True)
def _isolate(tmp_path, monkeypatch):
    """把三个路径都指到临时目录 ✓（绝不碰真实公式库 ✓）。"""
    d = tmp_path / "formulas"
    monkeypatch.setattr(cf, "_FORMULAS_DIR", str(d))
    monkeypatch.setattr(cf, "_MY_FILE", str(d / "me.json"))
    monkeypatch.setattr(cf, "_CUSTOM_FORMULAS_PATH", str(tmp_path / "legacy.json"))
    monkeypatch.setattr(cf, "_USER", "admin")
    monkeypatch.delenv("QUANT_FORMULA_USER", raising=False)
    return d


def _item(fid: str, name: str, text: str = "OUT:CLOSE;", ts: str = "2026-01-01 00:00:00",
          codegen: str = None) -> dict:
    return {"id": fid, "name": name, "text": text, "expression": "Close($close)",
            "codegen": cf.CODEGEN_SEMANTICS if codegen is None else codegen, "updated_at": ts}


# ---------------------------------------------------------------------------
# ① 合并：所有装机文件都能看到 ✓
# ---------------------------------------------------------------------------
def test_merge_reads_all_install_files(_isolate):
    _write(str(_isolate / "me.json"), [_item("a1", "我的公式")])
    _write(str(_isolate / "u-1234abcd.json"), [_item("b1", "同事的公式")])
    names = sorted(x["name"] for x in cf.load_merged())
    assert names == ["同事的公式", "我的公式"]


def test_newer_updated_at_wins_regardless_of_owner(_isolate):
    """★ 裁决只看 `updated_at` ⇒ **较新的赢，不管它在谁的文件里** ✓（不引入"身份" ✓）。

    ⚠ 这与 v1.20.65 的旧断言（`test_my_file_wins_on_same_id`：我恒胜）**正好相反** ✓ ——
      旧规则正是"抄副本永久遮蔽对方更新"的根源 ✗（见模块头 ②）。
    """
    _write(str(_isolate / "me.json"), [_item("x1", "我改过的", ts="2026-01-01 00:00:00")])
    _write(str(_isolate / "u-9999.json"),
           [_item("x1", "同事后来改的", ts="2026-09-01 00:00:00")])
    got = cf.load_merged()
    assert len(got) == 1 and got[0]["name"] == "同事后来改的"


def test_tie_timestamp_prefers_install_file_over_legacy(_isolate, tmp_path):
    """时间戳**相同时**：普通装机文件（prio 0）> 老单文件（prio 1）✓。"""
    _write(str(tmp_path / "legacy.json"), [_item("k1", "老版本", ts="2026-05-05 00:00:00")])
    _write(str(_isolate / "me.json"), [_item("k1", "我的同戳版", ts="2026-05-05 00:00:00")])
    got = cf.load_merged()
    assert len(got) == 1 and got[0]["name"] == "我的同戳版"


# ---------------------------------------------------------------------------
# ② 删除 = 带戳 tombstone：只屏蔽、不物理删、可被更新的编辑"复活" ✓
# ---------------------------------------------------------------------------
def test_tombstone_hides_other_users_entry(_isolate):
    """删**别人的**条目 ⇒ 自己文件写 tombstone ⇒ 合并时隐藏 ✓（**不动别人的文件** ✓）。"""
    _write(str(_isolate / "u-7777.json"), [_item("z1", "同事的")])
    _write(str(_isolate / "me.json"),
           [{"id": "z1", "deleted": True, "updated_at": "2026-09-24 00:00:00"}])
    assert cf.load_merged() == []
    assert _read(str(_isolate / "u-7777.json"))[0]["name"] == "同事的"      # 原样不动 ✓


def test_tombstone_also_blocks_other_installs_copy(_isolate):
    """★ 我删掉一条后，**别人若还持有同 id 的旧副本**也必须被压住 ✓（"只增不减" ✓）。

    ⚠ 旧实现「自己的条目直接从文件里移除」在这种情形下会让对方那份**重新浮现** ✗ ——
      这正是 v1.20.69 改成"一律写带戳 tombstone"的原因 ✓。
    """
    cf.create_custom_formula("我的", "OUT:CLOSE;", "Close($close)")
    fid = _read(str(_isolate / "me.json"))[0]["id"]
    # 别的装机持有同 id 的旧副本（时间戳比我删除动作早 ✓）
    _write(str(_isolate / "u-5555.json"), [_item(fid, "同事手里那份", ts="2026-01-01 00:00:00")])
    assert cf.delete_custom_formula(fid) is True
    assert cf.load_merged() == []                                          # 被 tombstone 压住 ✓
    items = _read(str(_isolate / "me.json"))
    assert any(x.get("deleted") for x in items)                            # 写了带戳 tombstone ✓


def test_newer_edit_resurrects_deleted_entry(_isolate):
    """★ 删除之后，别人**再编辑**（`updated_at` 更新）⇒ 条目**复活** ✓（谁最后改谁赢 ✓）。"""
    _write(str(_isolate / "me.json"),
           [{"id": "r1", "deleted": True, "updated_at": "2026-09-01 00:00:00"}])
    _write(str(_isolate / "u-2222.json"),
           [_item("r1", "同事后来救回来了", ts="2026-09-02 00:00:00")])
    got = cf.load_merged()
    assert len(got) == 1 and got[0]["name"] == "同事后来救回来了"


# ---------------------------------------------------------------------------
# ③ 写操作只动自己的文件 ✓
# ---------------------------------------------------------------------------
def test_create_writes_only_my_file(_isolate):
    _write(str(_isolate / "u-1111.json"), [_item("b1", "同事的")])
    item = cf.create_custom_formula("新公式", "OUT:CLOSE;", "Close($close)")
    assert item["author"] and item["author_user"] == "admin"               # 装机 ID + 用户名 ✓
    assert [x["name"] for x in _read(str(_isolate / "me.json"))] == ["新公式"]
    assert [x["name"] for x in _read(str(_isolate / "u-1111.json"))] == ["同事的"]
    assert sorted(x["name"] for x in cf.list_custom_formulas()) == ["同事的", "新公式"]


def test_update_other_users_entry_writes_override(_isolate):
    """改**别人的**条目 ⇒ 自己文件里存同 id 覆盖副本（时间戳前进 ⇒ 胜出 ✓）⇒ 不动别人的文件 ✓。"""
    _write(str(_isolate / "u-1111.json"), [_item("b1", "同事的", ts="2026-01-01 00:00:00")])
    got = cf.update_custom_formula("b1", "同事的(我改了)", "OUT:CLOSE;", "Close($close)")
    assert got and got["name"] == "同事的(我改了)"
    assert _read(str(_isolate / "u-1111.json"))[0]["name"] == "同事的"      # 同事那份没动 ✓
    merged = cf.load_merged()
    assert len(merged) == 1 and merged[0]["name"] == "同事的(我改了)"


def test_legacy_file_is_read_but_never_written(_isolate, tmp_path):
    """老单文件 `custom_formulas.json` **只读** ✓ ⇒ 平滑迁移、老公式不丢 ✓。"""
    _write(str(tmp_path / "legacy.json"), [_item("L1", "老公式")])
    assert [x["name"] for x in cf.load_merged()] == ["老公式"]
    cf.create_custom_formula("新的", "OUT:CLOSE;", "Close($close)")
    assert [x["name"] for x in _read(str(tmp_path / "legacy.json"))] == ["老公式"]


# ---------------------------------------------------------------------------
# ④ ★ 关键回归：`list` 重编**绝不把别人的条目抄进自己文件**（遮蔽源 ✓）
# ---------------------------------------------------------------------------
def test_list_does_not_copy_others_entries_on_recompile(_isolate):
    """★ 陈旧条目重编时：**只回写自己文件里已有的** ⇒ 别人的条目只返回、不落盘 ✓。

    ⚠ 旧实现 `mine.append(it)` 会把别人的全部条目抄进自己文件 ✗ ⇒ 一次 codegen 升级
      即产生"高时间戳副本" ⇒ **永久遮蔽**对方后续更新 ✗✗（本用例就是钉住它 ✓）。
    """
    stale = "0.0.0-old-codegen"                                            # 故意过期 ⇒ 触发重编 ✓
    _write(str(_isolate / "u-3333.json"), [_item("o1", "同事的陈旧条目", codegen=stale)])
    _write(str(_isolate / "me.json"), [_item("m1", "我的陈旧条目", codegen=stale)])

    items = cf.list_custom_formulas()                                       # GET 会顺带重编 ✓
    assert sorted(x["name"] for x in items) == ["同事的陈旧条目", "我的陈旧条目"]
    assert all(x["codegen"] == cf.CODEGEN_SEMANTICS for x in items)         # 返回值已刷新 ✓

    mine = _read(str(_isolate / "me.json"))
    assert [x["name"] for x in mine] == ["我的陈旧条目"]                    # ✗ 没抄别人的 ✓
    theirs = _read(str(_isolate / "u-3333.json"))
    assert [x["codegen"] for x in theirs] == [stale]                        # 别人的文件没被改 ✓


# ---------------------------------------------------------------------------
# ⑤ 装机 ID：与用户名无关、稳定、可显式覆盖 ✓
# ---------------------------------------------------------------------------
def test_install_id_stable_and_independent_of_username(_isolate, monkeypatch):
    """★ 装机 ID 落盘、**与 `USERNAME` 无关** ✓ —— 这正是"两台都叫 admin 也不冲突"的保证 ✓。"""
    first = cf._install_id()
    assert first.startswith("u-") and len(first) > 3
    p = cf._install_id_path()
    assert os.path.isfile(p) and open(p, encoding="utf-8").read().strip() == first
    monkeypatch.setenv("USERNAME", "另一个完全不同的用户名")
    assert cf._install_id() == first                                       # 不受用户名影响 ✓


def test_install_id_env_override(_isolate, monkeypatch):
    monkeypatch.setenv("QUANT_FORMULA_USER", "home")
    assert cf._install_id() == "home"


def test_migrate_v12065_username_file_to_install_file(_isolate):
    """★ 迁移：v1.20.65 的「按人文件」`formulas/<USERNAME>.json` ⇒ **改名**成本装机文件 ✓。

    （⚠ 只搬"用户名匹配"的那份 ✓；别人的文件**绝不搬** ✗ —— 靠读时合并天然可见 ✓）
    """
    _write(str(_isolate / "admin.json"), [_item("old1", "我 09-24 之前的公式")])
    _write(str(_isolate / "u-4444.json"), [_item("o2", "别人的")])
    assert not os.path.exists(str(_isolate / "me.json"))                    # 装机文件还不存在 ✓
    names = sorted(x["name"] for x in cf.load_merged())
    assert names == ["别人的", "我 09-24 之前的公式"]                       # 都看得到 ✓
    assert os.path.isfile(str(_isolate / "me.json"))                        # 已改名到装机文件 ✓
    assert not os.path.exists(str(_isolate / "admin.json"))                 # 旧名字不再残留 ✓
    assert [x["name"] for x in _read(str(_isolate / "me.json"))] == ["我 09-24 之前的公式"]
    assert [x["name"] for x in _read(str(_isolate / "u-4444.json"))] == ["别人的"]   # 别人没被动 ✓
