# -*- coding: utf-8 -*-
"""公式面板结果缓存单测（★ P1-C-1，2026-10-18 ✓）。

守的六件事（每条都对应一个"会静默出错"的风险 ✓）：
  ① 存进去读回来**逐位相同** ✓；
  ② **键必须敏感** ✓（表达式/区间/预热天数/数据集任一变化都要 miss ✗ —— 否则拿旧结果当真 ✗）；
  ③ **坏文件 ⇒ None + 删掉** ✓（不许抛异常污染主流程 ✗）；
  ④ **三重清理**（TTL / 个数 / 总量 ✓ —— 用户明确要求"注意定期清理"✓）；
  ⑤ 单条超限不落 ✓；
  ⑥ **接线生效**：第二次调用真的走缓存 ✓（不然模块写得再好也没用 ✗）。
"""
import os
import time

import pandas as pd
import pytest

from app.services import expr_cache as C


@pytest.fixture(autouse=True)
def _isolated(monkeypatch, tmp_path):
    monkeypatch.setattr(C, "_ROOT", str(tmp_path / "expr_cache"))
    C._STATS.update({k: 0 for k in C._STATS})
    yield


def _df(n=3):
    idx = pd.MultiIndex.from_product(
        [pd.date_range("2022-01-03", periods=n, freq="D"), ["SH600000", "SZ000001"]],
        names=["datetime", "instrument"])
    return pd.DataFrame({"x": range(len(idx)), "y": [i * 0.5 for i in range(len(idx))]}, index=idx)


def _key(**kw):
    base = dict(feature_dir=r"d:\data\cn_data2\features", instruments=["SH600000", "SZ000001"],
                fields=[("$close/Ref($close,1)-1", "f1")], start="2022-01-03",
                end="2022-03-01", warmup_days=0)
    base.update(kw)
    return C.key(base["feature_dir"], base["instruments"], base["fields"],
                 base["start"], base["end"], base["warmup_days"])


def test_roundtrip_identical():
    df = _df(4)
    k = _key()
    assert C.save(k, df) is True
    got = C.load(k)
    assert got is not None
    pd.testing.assert_frame_equal(got, df)


def test_key_is_sensitive_to_every_input():
    k0 = _key()
    assert _key(fields=[("$close", "f1")]) != k0
    assert _key(start="2021-01-01") != k0
    assert _key(end="2022-04-01") != k0
    assert _key(warmup_days=20) != k0
    assert _key(feature_dir=r"d:\data\cn_data\features") != k0
    assert _key(instruments=["SH600000"]) != k0
    # 集合顺序不影响 ✓（排序后拼键 ✓）
    assert _key(instruments=["SZ000001", "SH600000"]) == k0


def test_disabled_by_env(monkeypatch):
    monkeypatch.setenv("QLIB_EXPR_CACHE", "0")
    assert C.enabled() is False
    monkeypatch.setenv("QLIB_EXPR_CACHE", "1")
    assert C.enabled() is True


def test_corrupt_file_returns_none_and_is_removed():
    k = _key()
    os.makedirs(C._ROOT, exist_ok=True)
    p = os.path.join(C._ROOT, k + ".parquet")
    open(p, "wb").write(b"not a parquet")
    assert C.load(k) is None
    assert not os.path.exists(p)          # 坏文件已清 ✓


def test_ttl_prune():
    k1, k2 = _key(fields=[("a", "f1")]), _key(fields=[("b", "f1")])
    C.save(k1, _df())
    C.save(k2, _df())
    old = time.time() - 30 * 86400        # 30 天前 ✓（默认 TTL 7 天 ✓）
    os.utime(os.path.join(C._ROOT, k1 + ".parquet"), (old, old))
    assert C.prune() >= 1
    assert C.load(k1) is None and C.load(k2) is not None


def test_max_files_prune():
    """按**个数**淘汰最旧 ✓。

    ⚠ 这里必须 `ttl_days=0` 关掉 TTL ✓ —— 我第一版把 mtime 设成 1970（为了排序 ✗）
      ⇒ `save()` 内部那次 prune 把它们当**过期**删掉了 ✗ ⇒ 只剩 1 个 ✓（实测抓出来 ✓）。
      ⇒ 做法：**全部存完**再统一设 mtime ✓，并且只验证"个数"这一条边界 ✓。
    """
    keys = []
    for i in range(5):
        k = _key(fields=[("e%d" % i, "f")])
        C.save(k, _df())
        keys.append(k)
    for i, k in enumerate(keys):
        os.utime(os.path.join(C._ROOT, k + ".parquet"), (1000 + i, 1000 + i))
    assert C.prune(ttl_days=0, max_files=2) == 3
    left = sorted(n[:-8] for n in os.listdir(C._ROOT) if n.endswith(".parquet"))
    assert left == sorted(keys[-2:])


def test_max_total_size_prune():
    for i in range(3):
        C.save(_key(fields=[("s%d" % i, "f")]), _df(50))
    # 把总量上限压到极小 ⇒ 至少删到一个 ✓
    assert C.prune(max_mb=0.0001) >= 1


def test_oversize_entry_skipped(monkeypatch):
    monkeypatch.setattr(C, "_MAX_ENTRY_MB", 0.0001)
    assert C.save(_key(), _df(50)) is False
    assert C.load(_key()) is None


def test_panel_features_uses_cache(monkeypatch, tmp_path):
    """★ 接线验证 ✓：**第二次调用不再重算** ✓（否则缓存等于没接 ✗）。"""
    from app.factors import panel_expr as P

    calls = {"n": 0}

    def _fake(instruments, fields, start_time, end_time, cancel_cb=None, warmup_days=0):
        calls["n"] += 1
        return _df(2)

    monkeypatch.setattr(C, "_ROOT", str(tmp_path / "c"))
    monkeypatch.setattr(P, "_panel_features_uncached", _fake)
    monkeypatch.setattr(P, "_feature_dir", lambda: r"d:\data\cn_data2\features")

    args = (["SH600000"], [("$close", "f1")], "2022-01-03", "2022-02-01")
    a = P.panel_features(*args)
    b = P.panel_features(*args)
    assert calls["n"] == 1               # 第二次命中 ✓
    pd.testing.assert_frame_equal(a, b)

    # 换个字段 ⇒ 必须重算 ✓
    P.panel_features(["SH600000"], [("$open", "f1")], "2022-01-03", "2022-02-01")
    assert calls["n"] == 2
