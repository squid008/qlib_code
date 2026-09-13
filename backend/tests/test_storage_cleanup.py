# -*- coding: utf-8 -*-
"""存储治理单测：feature_cache 配额+LRU、artifacts 保留数、活跃任务保护。"""
import os
import time

import pytest


def _touch(path, size=500, mtime=None):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "wb") as f:
        f.write(b"x" * size)
    if mtime is not None:
        os.utime(path, (mtime, mtime))


class TestFeatureCacheCleanup:
    def test_removes_oldest_until_under_quota(self, tmp_path, monkeypatch):
        from app.engine import storage_cleanup as sc

        root = tmp_path / "fc"
        now = time.time()
        _touch(root / "a.pkl", 500, now - 300)  # 最旧
        _touch(root / "b.pkl", 500, now - 100)
        _touch(root / "c.pkl", 500, now)
        # 配额设为 1100 字节左右 → 只允许保留 ~2 个：删最旧的 a
        monkeypatch.setattr(sc, "_FEATURE_CACHE_GB", 1100 / (1024 ** 3))
        removed = sc._clean_feature_cache(str(root))
        assert removed == 1
        left = sorted(os.listdir(root))
        assert left == ["b.pkl", "c.pkl"]

    def test_no_remove_under_quota(self, tmp_path, monkeypatch):
        from app.engine import storage_cleanup as sc

        root = tmp_path / "fc2"
        _touch(root / "a.pkl", 500, time.time() - 10)
        monkeypatch.setattr(sc, "_FEATURE_CACHE_GB", 0.1)  # 100MB 配额
        assert sc._clean_feature_cache(str(root)) == 0


class TestArtifactsCleanup:
    def test_keep_recent_and_protect_active(self, tmp_path, monkeypatch):
        from app.engine import storage_cleanup as sc

        root = tmp_path / "art"
        now = time.time()
        # 5 个"旧"任务（很久未写）+ 1 个"活跃"任务（刚刚写）
        for i in range(5):
            _touch(root / f"old_task_{i}" / "seg.json", 100, now - 10 * 86400)
        _touch(root / "active_task" / "partial.json", 100, now)
        monkeypatch.setattr(sc, "_ARTIFACTS_KEEP", 4)  # 想留 4，但最少保护 5
        monkeypatch.setattr(sc, "_ACTIVE_WINDOW_SEC", 86400)

        removed = sc._clean_artifacts(str(root))
        assert removed == 1  # 6 目录 > 最小保留 5 → 只删最旧 1 个
        left = sorted(os.listdir(root))
        assert "active_task" in left
        assert len(left) == 5

    def test_cleanup_storage_smoke(self, tmp_path, monkeypatch):
        """cleanup_storage(force=True) 整链可跑且不抛异常。"""
        from app.engine import storage_cleanup as sc

        wd = tmp_path / "workdir"
        _touch(wd / "feature_cache" / "x.pkl", 100, time.time() - 1000)
        _touch(wd / "artifacts" / "old" / "a.json", 100, time.time() - 10 * 86400)
        monkeypatch.setattr(sc, "_FEATURE_CACHE_GB", 1e-9)
        monkeypatch.setattr(sc, "_ARTIFACTS_KEEP", 0)
        stat = sc.cleanup_storage(str(wd), force=True)
        assert "feature_cache_removed" in stat
        assert "artifacts_removed" in stat
        assert not os.path.exists(wd / "feature_cache" / "x.pkl")
        # artifacts 最少保留 5 个目录（防误删保护），这里只有一个旧目录 → 不删
        assert os.path.exists(wd / "artifacts" / "old")


class TestArtifactsSizeQuota:
    """v1.19.12：artifacts 配额由「固定 40 个」改为**容量优先 + 先精简后删 + 写日志**。

    背景：用户报「回测产物从 3 页变 2 页、早期产物丢失」——旧实现固定删到只剩 40 个且**完全静默**。
    """

    def test_under_quota_keeps_everything(self, tmp_path, monkeypatch):
        """容量未超配额 ⇒ 既不精简也不删除（旧实现会因"个数>40"直接删）。"""
        from app.engine import storage_cleanup as sc

        root = tmp_path / "art3"
        for i in range(6):
            _touch(root / f"t{i}" / "result.json", 100, time.time() - (i + 1) * 86400)
        monkeypatch.setattr(sc, "_ARTIFACTS_GB", 30.0)
        monkeypatch.setattr(sc, "_ARTIFACTS_KEEP", 300)
        monkeypatch.setattr(sc, "_ACTIVE_WINDOW_SEC", 86400)
        st = sc._clean_artifacts(str(root), detail=True)
        assert st["removed"] == 0 and st["slimmed"] == 0
        assert len(os.listdir(root)) == 6

    def test_over_quota_slims_first_and_keeps_records(self, tmp_path, monkeypatch):
        """超容量 ⇒ **先精简**：删 `segment_*/` 与大型中间产物，**保留 result.json 等轻量记录**，目录不删。"""
        from app.engine import storage_cleanup as sc

        root = tmp_path / "art4"
        d = root / "old_task"
        _touch(d / "result.json", 100, time.time() - 10 * 86400)
        _touch(d / "segment_1" / "pred.pkl", 5000, time.time() - 10 * 86400)
        _touch(d / "big_model.pkl", 5000, time.time() - 10 * 86400)
        # 配额 3000 字节：精简后（~160B）已低于配额 ⇒ 不应整删
        monkeypatch.setattr(sc, "_ARTIFACTS_GB", 3000 / (1024 ** 3))
        monkeypatch.setattr(sc, "_ARTIFACTS_KEEP", 300)
        # 精简门槛调到 1KB（默认为 5MB）：让测试里的 5KB 假 pkl 也算"大体积中间产物"
        monkeypatch.setattr(sc, "_ARTIFACTS_SLIM_MB", 1024 / (1024 ** 2))
        monkeypatch.setattr(sc, "_ACTIVE_WINDOW_SEC", 86400)
        st = sc._clean_artifacts(str(root), detail=True)
        assert st["slimmed"] == 1 and st["removed"] == 0
        assert os.path.isdir(d)                       # 目录还在（历史列表条目不消失）
        assert os.path.exists(d / "result.json")      # 参数/结果/曲线保留
        assert not os.path.exists(d / "segment_1")    # 大体积中间产物已回收
        assert not os.path.exists(d / "big_model.pkl")
        assert os.path.exists(d / ".slimmed")         # 留精简标记

    def test_over_quota_deletes_when_slim_not_enough(self, tmp_path, monkeypatch):
        """精简后仍超配额 ⇒ 才整目录删除。"""
        from app.engine import storage_cleanup as sc

        root = tmp_path / "art5"
        d = root / "old_task"
        _touch(d / "result.json", 100, time.time() - 10 * 86400)
        monkeypatch.setattr(sc, "_ARTIFACTS_GB", 1e-9)   # 配额 ~1 字节，精简也救不回来
        monkeypatch.setattr(sc, "_ARTIFACTS_KEEP", 300)
        monkeypatch.setattr(sc, "_ACTIVE_WINDOW_SEC", 86400)
        st = sc._clean_artifacts(str(root), detail=True)
        assert st["removed"] == 1
        assert not os.path.exists(d)

    def test_active_dir_never_touched(self, tmp_path, monkeypatch):
        """近 1 天有写入的目录（活跃）绝不精简/删除，即使超配额。"""
        from app.engine import storage_cleanup as sc

        root = tmp_path / "art6"
        d = root / "running_task"
        _touch(d / "segment_1" / "pred.pkl", 5000, time.time())
        monkeypatch.setattr(sc, "_ARTIFACTS_GB", 1e-9)
        monkeypatch.setattr(sc, "_ACTIVE_WINDOW_SEC", 86400)
        st = sc._clean_artifacts(str(root), detail=True)
        assert st["removed"] == 0 and st["slimmed"] == 0
        assert os.path.exists(d / "segment_1" / "pred.pkl")
