# -*- coding: utf-8 -*-
"""进程内数据共享缓存的单元测试（阶段一）。"""
import pandas as pd

from app.engine.data_cache import DataCache, SHARED_CACHE


def _make_df(instruments, dates):
    import numpy as np
    index = pd.MultiIndex.from_product(
        [[str(i).lower() for i in instruments], [str(d) for d in dates]],
        names=["instrument", "datetime"],
    )
    return pd.DataFrame({"$close": np.arange(len(index), dtype=float)}, index=index)


class TestDataCache:
    def setup_method(self):
        self.cache = DataCache()

    def test_get_or_load_caches_and_reuses(self):
        calls = {"n": 0}

        def loader():
            calls["n"] += 1
            return _make_df(["SH600000"], ["2024-01-01", "2024-01-02"])

        df1 = self.cache.get_or_load(["SH600000"], ["$close"], "2024-01-01", "2024-01-02", loader)
        df2 = self.cache.get_or_load(["sh600000"], ["$close"], "2024-01-01", "2024-01-02", loader)
        # 第二次命中缓存，loader 不再调用
        assert calls["n"] == 1
        assert df1 is not None and df2 is not None
        # 命中时返回同一份（共享引用）
        assert df1 is df2

    def test_different_key_reloads(self):
        calls = {"n": 0}

        def loader():
            calls["n"] += 1
            return _make_df(["SH600000"], ["2024-01-01"])

        self.cache.get_or_load(["SH600000"], ["$close"], "2024-01-01", "2024-01-02", loader)
        self.cache.get_or_load(["SH600000"], ["$open"], "2024-01-01", "2024-01-02", loader)  # 不同字段
        assert calls["n"] == 2

    def test_instruments_order_normalized(self):
        """股票顺序不同但集合相同应命中同一缓存键。"""
        calls = {"n": 0}

        def loader():
            calls["n"] += 1
            return _make_df(["SH600000", "SZ000001"], ["2024-01-01"])

        self.cache.get_or_load(["SH600000", "SZ000001"], ["$close"], "2024-01-01", "2024-01-01", loader)
        self.cache.get_or_load(["SZ000001", "SH600000"], ["$close"], "2024-01-01", "2024-01-01", loader)
        assert calls["n"] == 1

    def test_stats(self):
        self.cache.get_or_load(["SH600000"], ["$close"], "2024-01-01", "2024-01-01",
                               lambda: _make_df(["SH600000"], ["2024-01-01"]))
        s = self.cache.stats()
        assert s["entries"] == 1
        assert s["total_refs"] >= 1

    def test_clear(self):
        self.cache.get_or_load(["SH600000"], ["$close"], "2024-01-01", "2024-01-01",
                               lambda: _make_df(["SH600000"], ["2024-01-01"]))
        self.cache.clear()
        assert self.cache.stats()["entries"] == 0

    def test_shared_singleton(self):
        # SHARED_CACHE 是全局单例
        assert SHARED_CACHE is not None
