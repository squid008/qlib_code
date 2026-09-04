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
