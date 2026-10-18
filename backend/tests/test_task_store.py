# -*- coding: utf-8 -*-
"""任务状态落盘的单测（★ P1-C-2，2026-10-18 ✓）。

守的是什么（每条对应一个真实风险 ✓）：
  ① 存进去能读回来 ✓（否则"重启不丢"是空话 ✗）；
  ② **不落中间物** ✓（`_evs` / `_hk` 可能是几万行 DataFrame ✗ ⇒ 必须落成 None ✓）；
  ③ **原子写** ✓（写入过程被中断 ⇒ 不许留下半截 JSON ✗ ⇒ 见 `test_tmp_file_is_used_then_replaced` ✓）；
  ④ **坏文件不许拖垮启动** ✓（删掉继续 ✓）；
  ⑤ **重启残留的 running 必须改成 failed** ✓（否则用户对着 0% 干等 ✗）；
  ⑥ 超上限不落 ✓ ｜ ⑦ prune 按数量淘汰 ✓。
"""
import json
import os

import pytest

from app.services import task_store as S


@pytest.fixture(autouse=True)
def _isolated(monkeypatch, tmp_path):
    """每个用例一个独立目录 ✓（绝不碰真实 `workdir/tasks` ✗）。"""
    monkeypatch.setattr(S, "_ROOT", str(tmp_path / "tasks"))
    S._SIG.clear()
    yield


def test_roundtrip_keeps_whitelisted_fields():
    st = {"id": "t1", "status": "success", "progress": 100, "ts": 123,
          "result": {"ic": 0.03}, "_req": {"symbol": "BP"}, "error": None}
    assert S.save("sft", "t1", st) is True
    got = S.load_all("sft")
    assert list(got) == ["t1"]
    assert got["t1"]["status"] == "success"
    assert got["t1"]["result"] == {"ic": 0.03}
    assert got["t1"]["_req"] == {"symbol": "BP"}


def test_dataframe_and_holdings_are_not_persisted():
    """`_evs` / `_hk` 必须落成 None ✓ —— 它们可能是几万行 ✗，且内存治理时本来就会被清 ✓。"""
    st = {"status": "success", "progress": 100, "ts": 1,
          "_evs": object(), "_hk": object(), "result": {"x": 1}}
    S.save("sft", "t2", st, force=True)
    raw = json.load(open(os.path.join(S._ROOT, "sft", "t2.json"), encoding="utf-8"))
    assert raw["_evs"] is None
    assert raw["_hk"] is None
    assert raw["result"] == {"x": 1}


def test_same_status_is_not_rewritten():
    """状态没变 ⇒ 不重复写 ✓（一个任务进度能更新上百次 ✗，不能每次都落盘 ✗）。"""
    st = {"status": "running", "progress": 10, "ts": 1}
    assert S.save("sft", "t3", st) is True
    assert S.save("sft", "t3", st) is False          # 第二次 ⇒ 跳过 ✓
    st2 = dict(st, progress=20)
    assert S.save("sft", "t3", st2) is True           # 进度变了 ⇒ 写 ✓


def test_corrupt_file_is_removed_and_does_not_break_load():
    """坏 JSON ⇒ 删掉继续 ✓（一个坏文件不许拖垮启动 ✗）。"""
    d = S._dir("sft")
    bad = os.path.join(d, "bad.json")
    open(bad, "w", encoding="utf-8").write("{ this is not json")
    S.save("sft", "good", {"status": "success", "progress": 100, "ts": 1}, force=True)
    got = S.load_all("sft")
    assert "good" in got
    assert not os.path.exists(bad)                    # 已清理 ✓
    assert "bad" not in got


def test_stale_running_becomes_failed():
    """★ 重启残留 ✓：`running` 必须变 `failed` 且带可读原因 ✓（不能让用户对着 0% 干等 ✗）。"""
    S.save("sft", "r1", {"status": "running", "progress": 40, "ts": 1}, force=True)
    S.save("sft", "q1", {"status": "queued", "progress": 0, "ts": 2}, force=True)
    got = S.load_all("sft")
    assert got["r1"]["status"] == "failed"
    assert "重启" in got["r1"]["error"]
    assert got["q1"]["status"] == "failed"
    assert got["r1"]["progress"] == 40               # 保留原进度 ✓（便于判断断在哪 ✓）


def test_oversize_payload_is_skipped(monkeypatch):
    """超过上限 ⇒ 不落 ✓（宁可不缓存，也不把磁盘写满 ✗）。"""
    monkeypatch.setattr(S, "_MAX_BYTES", 200)
    st = {"status": "success", "progress": 100, "ts": 1, "result": {"blob": "x" * 1000}}
    assert S.save("sft", "big", st, force=True) is False
    assert S.load_all("sft") == {}


def test_prune_keeps_newest_n():
    for i in range(5):
        S.save("sft", "p%d" % i, {"status": "success", "progress": 100, "ts": i}, force=True)
        os.utime(os.path.join(S._ROOT, "sft", "p%d.json" % i), (1000 + i, 1000 + i))
    assert S.prune("sft", 2) == 3
    assert sorted(S.load_all("sft")) == ["p3", "p4"]


def test_task_id_is_sanitized():
    """task_id 不许穿越目录 ✓（`../` 之类必须被剥掉 ✓）。"""
    S.save("sft", "../../evil", {"status": "success", "progress": 100, "ts": 1}, force=True)
    files = os.listdir(S._dir("sft"))
    assert files == ["evil.json"]                     # 只留安全字符 ✓
    assert not os.path.exists(os.path.join(S._ROOT, "evil.json"))
