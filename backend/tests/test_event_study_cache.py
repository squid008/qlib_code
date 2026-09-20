# -*- coding: utf-8 -*-
"""`factors/event_study_cache.py` 的缓存指纹/落盘（纯逻辑，不碰 qlib 数据）。

要点（v1.20.36）：事件研究的耗时缓存必须满足
  · **同参数必命中**（否则用户"重算一次还是慢"）；
  · **任一影响结果的参数变化必失效**（否则会出现"参数不同却拿到旧结果"的静默口径错误）；
  · 缓存文件损坏/结构不对 ⇒ 当作未命中（**缓存绝不能挡住主流程**）。
"""
import os

from app.factors import event_study_cache as esc


def _params(**over):
    base = {
        "universe": "all", "start_date": "2016-01-01", "end_date": "2024-12-31",
        "expr": "Gt($close,Ref($close,1))", "max_k": 40,
        "exclude_limit_up_signal": True, "exclude_limit_up_trade": True,
        "exclude_suspended": True, "exclude_st_t1": False,
        "exclude_stock_gem": False, "exclude_stock_kcb": False,
        "price_adjust": "backward", "price_round": True, "suspend_remove": True,
        "freeze_suspended_price": True, "warmup_days": 250,
    }
    base.update(over)
    return base


class TestFingerprint:
    def test_same_params_same_fingerprint(self):
        assert esc.fingerprint(_params()) == esc.fingerprint(_params())

    def test_each_result_affecting_param_changes_fingerprint(self):
        """逐个参数改动都必须换指纹（漏一个 = 静默拿到旧结果）。"""
        base = esc.fingerprint(_params())
        for key, val in (("max_k", 41), ("universe", "csi300"), ("expr", "Gt($close,0)"),
                         ("start_date", "2017-01-01"), ("end_date", "2023-12-31"),
                         ("exclude_st_t1", True), ("exclude_stock_gem", True),
                         ("exclude_limit_up_signal", False), ("exclude_limit_up_trade", False),
                         ("exclude_suspended", False), ("exclude_stock_kcb", True),
                         ("price_adjust", "none"), ("price_round", False),
                         ("suspend_remove", False), ("freeze_suspended_price", False),
                         ("warmup_days", 0)):
            assert esc.fingerprint(_params(**{key: val})) != base, key

    def test_version_in_fingerprint(self, monkeypatch):
        """口径版本号变了，旧缓存必须失效（防"新算法返回旧结果"）。"""
        p = _params()
        a = esc.fingerprint(p)
        monkeypatch.setattr(esc, "CACHE_VERSION", esc.CACHE_VERSION + "x")
        assert esc.fingerprint(p) != a


class TestStore:
    def test_roundtrip_and_structure_guard(self, tmp_path, monkeypatch):
        monkeypatch.setattr(esc, "cache_dir", lambda: str(tmp_path))
        fp = esc.path_for(_params())
        assert esc.load(fp) is None, "未写入时应视为未命中"
        assert esc.save(fp, {"curve": [1, 2, 3], "ok": True}) is True
        got = esc.load(fp)
        assert got == {"curve": [1, 2, 3], "ok": True}

    def test_bad_file_is_a_miss(self, tmp_path, monkeypatch):
        """损坏/结构不对 ⇒ 当未命中（不能抛异常挡住主流程）。"""
        monkeypatch.setattr(esc, "cache_dir", lambda: str(tmp_path))
        fp = esc.path_for(_params())
        with open(fp, "wb") as f:
            f.write(b"not a pickle at all")
        assert esc.load(fp) is None
        esc.save(fp, {"no_curve_key": 1})           # 结构不对（缺 curve）⇒ 也算未命中
        assert esc.load(fp) is None
        assert os.path.exists(fp)
