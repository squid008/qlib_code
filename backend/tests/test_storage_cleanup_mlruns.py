# -*- coding: utf-8 -*-
"""`mlruns` 配额治理单测（★ v2.9.23，2026-10-18 ✓）。

守四件事（都对应用户明确要求 ✓）：
  ① **受保护期内不动** ✓（用户指定"保护最近 **3 天**" ✓）；
  ② 超配额时**按最旧优先**删、删到不超为止 ✓；
  ③ **只删 `<exp>/<run>/` 两层目录** ✓（`meta.yaml` / 实验级文件一个不碰 ✗）；
  ④ 未超配额 ⇒ **一个都不删** ✓（"空间够就别动"✓，这是 v1.19.12 立下的规矩 ✓）。
"""
import os
import time

from app.engine import storage_cleanup as S


def _mk_run(root, exp, run, size_kb=4, age_days=0.0):
    d = os.path.join(root, exp, run, "artifacts")
    os.makedirs(d, exist_ok=True)
    p = os.path.join(d, "pred.pkl")
    with open(p, "wb") as f:
        f.write(b"x" * (size_kb * 1024))
    ts = time.time() - age_days * 86400.0
    os.utime(p, (ts, ts))
    os.utime(os.path.join(root, exp, run), (ts, ts))
    return os.path.join(root, exp, run)


def test_protected_runs_are_never_removed(tmp_path, monkeypatch):
    """最近 3 天内写过的 run ⇒ **一个不删** ✓（即使超配额 ✓）。"""
    root = str(tmp_path / "mlruns")
    _mk_run(root, "1", "r_new", 4, age_days=0.5)          # 12 小时前 ✓
    _mk_run(root, "1", "r_edge", 4, age_days=2.9)         # 2.9 天 ✓ 仍保护
    monkeypatch.setattr(S, "_MLRUNS_GB", 0.000001)        # 配额极小 ⇒ 想删 ✓
    st = S._clean_mlruns(root, detail=True)
    assert st["removed"] == 0, "受保护的 run 被删了 ✗"
    assert st["runs"] == 2


def test_old_runs_removed_until_under_quota(tmp_path, monkeypatch):
    """超配额 ⇒ 从最旧删起、删到不超为止 ✓。"""
    root = str(tmp_path / "mlruns")
    # ⚠ 用 512KB 而不是 8KB ✓ —— `freed_mb` 保留 1 位小数 ✓，8KB 会被舍成 0.0 ✗
    #   ⇒ 我第一版断言 `freed_mb > 0` 直接失败 ✓（实测抓出来的 ✓）。
    old1 = _mk_run(root, "1", "r_old1", 512, age_days=30)
    _mk_run(root, "1", "r_old2", 512, age_days=20)
    new = _mk_run(root, "1", "r_new", 512, age_days=0.2)
    monkeypatch.setattr(S, "_MLRUNS_GB", 0.0012)          # ≈ 1.26MB ⇒ 3×0.5MB 装不下 ✓
    st = S._clean_mlruns(root, detail=True)
    assert st["removed"] >= 1
    assert not os.path.isdir(old1), "最旧的没被删 ✗"
    assert os.path.isdir(new), "受保护的新 run 被删了 ✗"
    assert st["freed_mb"] > 0, "释放量应 > 0（用例体积足够大 ⇒ 不会被舍成 0.0 ✓）"


def test_only_two_level_run_dirs_touched(tmp_path, monkeypatch):
    """`meta.yaml` / `.trash` / 实验级文件**不许碰** ✓（只处理 `<exp>/<run>/` ✓）。"""
    root = str(tmp_path / "mlruns")
    os.makedirs(os.path.join(root, "1"), exist_ok=True)
    meta = os.path.join(root, "1", "meta.yaml")
    open(meta, "w", encoding="utf-8").write("ok")
    trash = os.path.join(root, ".trash")
    os.makedirs(trash, exist_ok=True)
    keep = os.path.join(trash, "old.pkl")
    open(keep, "wb").write(b"x" * 4096)
    _mk_run(root, "1", "r_x", 4, age_days=40)
    monkeypatch.setattr(S, "_MLRUNS_GB", 0.000001)
    S._clean_mlruns(root)
    assert os.path.isfile(meta), "meta.yaml 被删了 ✗"
    assert os.path.isfile(keep), ".trash 里的东西被删了 ✗"


def test_under_quota_deletes_nothing(tmp_path, monkeypatch):
    """没超配额 ⇒ **一个都不删** ✓（这是"空间够就别动"的硬规矩 ✓）。"""
    root = str(tmp_path / "mlruns")
    for i in range(3):
        _mk_run(root, "1", "r%d" % i, 4, age_days=100)
    monkeypatch.setattr(S, "_MLRUNS_GB", 10.0)            # 配额 10GB ✓ 远超实际 ✓
    st = S._clean_mlruns(root, detail=True)
    assert st["removed"] == 0
    assert st["runs"] == 3


def test_missing_root_is_noop(tmp_path):
    st = S._clean_mlruns(str(tmp_path / "nope"), detail=True)
    assert st["removed"] == 0 and st["runs"] == 0
