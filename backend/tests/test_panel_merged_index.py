# -*- coding: utf-8 -*-
"""合并层索引 `_merged_index` / `_merged_meta` / `_merged_values`（★ v2.10.93，P1-#2 第十三刀）。

## 合并层是什么（默认**关闭** ✓ `QLIB_PANEL_MERGED=0`）
把「每股每字段一个 `.day.bin`」预先拼成「**每字段一个大 `.bin`** + `.idx.json` 索引」✓
—— 全池面板读盘次数 **3213 → 7**（实测 65× ✓）。源码注释还记着相反的一面：
**热页缓存下它反而更慢**（0.395s → 0.437s ✓）⇒ 默认关闭是**有实测依据**的 ✓，
不是"没做完" ✗。

## 为什么这层必须测（虽然默认关）
它一旦被开启（大股票池/冷缓存 ✓），**索引错位 = 所有因子数据整体错位** ✓，
而且错得"很像对的"（每段长度接近、起始值合理 ✓）。三个具体风险：
1. **`words*4 != 文件字节数`** ⇒ 索引与数据不同步（截断/写一半 ✓）⇒ 必须**判无效** ✓
   否则会按旧索引切出**半条**序列 ✓；
2. **`cnt` 含头部那 1 个 word** ⇒ 行数必须是 **`cnt-1`** ✗（写 `cnt` 会多读一行 ✓）；
3. **TTL 内零 syscall** 是性能关键 ✓（每次 `os.stat` 一个 71MB 文件 = 71µs × 上万次 ✗）。
"""
from __future__ import annotations

import json

import numpy as np
import pytest

from app.factors import panel_expr as P


@pytest.fixture()
def merged(tmp_path, monkeypatch):
    """把 `_feature_dir()` 指到 `tmp/features` ⇒ 合并层就是 `tmp/features_merged` ✓。"""
    feat = tmp_path / "features"
    feat.mkdir()
    monkeypatch.setattr(P, "_feature_dir", lambda: str(feat))
    monkeypatch.setattr(P, "_MERGED_ENABLED", True)
    monkeypatch.setattr(P, "_MERGED_TTL_S", 0.0)      # 默认每次都校验 mtime（保守 ✓）
    P._MERGED_DIR = None
    P._MERGED_IDX.clear()
    P._MERGED_MM.clear()
    yield tmp_path / "features_merged"
    P._MERGED_DIR = None
    P._MERGED_IDX.clear()
    P._MERGED_MM.clear()


def build_field(merged_dir, field: str, blocks: dict):
    """把 {inst: (start, [values...])} 拼成 `<field>.bin` + `<field>.idx.json` ✓。"""
    merged_dir.mkdir(parents=True, exist_ok=True)
    words, insts, off, cnt, stt = [], [], [], [], []
    for inst, (start, values) in blocks.items():
        off.append(len(words))
        words.append(float(start))
        words.extend(values)
        cnt.append(1 + len(values))          # ⚠ 含头部 ✓
        stt.append(int(start))
        insts.append(inst)
    # ⚠⚠ **必须造 `manifest.json`** ✗→✓ —— `_merged_dir()` 用"该文件是否存在"判断
    #   "合并层在不在" ✓（内容不看 ✓，只做存在性探测 ✓）。
    #   我第一版漏了它 ⇒ `_merged_dir()` 恒 None ⇒ 3 条用例全挂在 None 上 ✓。
    (merged_dir / "manifest.json").write_text("{}", encoding="utf-8")
    arr = np.asarray(words, dtype="<f4")
    arr.tofile(merged_dir / f"{field}.bin")
    (merged_dir / f"{field}.idx.json").write_text(json.dumps({
        "words": int(arr.size), "inst": insts, "off": off, "cnt": cnt, "start": stt,
    }), encoding="utf-8")
    return arr.size
    # 注意：`.idx.json` 里的 `words` 必须等于 **bin 的 float32 个数** ✓
    # （校验式是 `words * 4 == 文件字节数` ✓ —— 用字节数填会永远判无效 ✗）


def test_merged_dir_none_when_disabled(monkeypatch):
    """默认关闭 ⇒ `_merged_dir()` 直接 None ✓（**连目录都不探测** ✓ —— 否则每次求值多一次 syscall ✗）。"""
    monkeypatch.setattr(P, "_MERGED_ENABLED", False)
    assert P._merged_dir() is None


def test_merged_dir_none_without_manifest(merged):
    """启用但**没有 manifest.json** ⇒ None ✓（合并层"存在"的唯一标志就是它 ✓）。"""
    assert P._merged_dir() is None


def test_merged_index_loads_and_slices(merged):
    """⭐⭐ 正常路径：索引读出来 ⇒ `_merged_values` 的值必须与**单个 `.bin` 逐位一致** ✓
    （这是三条路径唯一的契约 ✓ —— 差一位就是整列错 ✗）。"""
    build_field(merged, "close", {"A": (10, [1.5, 2.5, 3.5]), "B": (0, [7.5])})
    assert P._merged_meta("A", "close")[1:] == (10, 3), "start=10, 行数=cnt-1=3 ✓"
    start, vals = P._merged_values("A", "close")
    assert start == 10
    np.testing.assert_array_equal(vals, np.array([1.5, 2.5, 3.5], dtype=np.float64))
    assert vals.dtype == np.float64, "对外必须是 float64 ✓（内部存 float32 ✓）"
    # B 段一块也不能串到 A 上 ✓
    assert P._merged_values("B", "close") == (0, np.array([7.5]))


def test_merged_index_rejects_word_count_mismatch(merged):
    """⭐⭐ **`words*4 != 文件字节数` ⇒ 判无效** ✓ —— 防"索引与数据不同步"
    （截断 / 写一半 ✓）。不校验就会按旧索引切出**半条**序列 ✓（长度不够时不报错 ✗）。"""
    build_field(merged, "close", {"A": (0, [1.0, 2.0])})
    idx = merged / "close.idx.json"
    raw = json.loads(idx.read_text(encoding="utf-8"))
    raw["words"] += 1                        # 故意与实际文件不符
    idx.write_text(json.dumps(raw), encoding="utf-8")
    assert P._merged_index("close") is None
    assert P._merged_meta("A", "close") is None


def test_merged_index_bad_index_json_is_none(merged):
    """索引 JSON 坏掉（写一半/不是 JSON）⇒ **None 且不抛** ✓（合并层是可选加速 ✓，
    它坏了应该**回退**，不该让整块面板崩 ✗）。"""
    build_field(merged, "close", {"A": (0, [1.0])})
    (merged / "close.idx.json").write_text("{ not json", encoding="utf-8")
    assert P._merged_index("close") is None


def test_merged_index_missing_field_bin_is_none(merged):
    """只有索引、没有 `.bin` ⇒ None ✓（不许按索引去切一个不存在的文件 ✗）。"""
    build_field(merged, "close", {"A": (0, [1.0])})
    (merged / "volume.idx.json").write_text(json.dumps({"words": 2, "inst": ["A"], "off": [0], "cnt": [2]}), encoding="utf-8")
    assert P._merged_index("volume") is None


def test_merged_meta_unlisted_instrument_is_none(merged):
    """未收录的股票 ⇒ meta/values 都 **None** ✓（让调用方回退到单文件路径 ✓，
    不许"返回一段别人的数据" ✗）。"""
    build_field(merged, "close", {"A": (0, [1.0, 2.0])})
    assert P._merged_meta("ZZZ", "close") is None
    assert P._merged_values("ZZZ", "close") is None


def test_merged_index_ttl_skips_stat(merged, monkeypatch):
    """⭐⭐ **TTL 内零 syscall** ✓ —— 这是性能关键（每次 `os.stat` 71MB 文件 = 71µs ✓，
    面板求值里要调上万次 ⇒ 端到端慢 0.7s ✗）。改用 TTL 后**连 stat 都不做** ✓。"""
    build_field(merged, "close", {"A": (0, [1.0])})
    monkeypatch.setattr(P, "_MERGED_TTL_S", 999.0)
    assert P._merged_index("close") is not None
    calls = {"n": 0}
    real_stat = P.os.stat

    def _counting_stat(*a, **kw):
        calls["n"] += 1
        return real_stat(*a, **kw)

    monkeypatch.setattr(P.os, "stat", _counting_stat)
    P._merged_index("close")           # TTL 内 ⇒ 不该碰 os.stat
    assert calls["n"] == 0, "TTL 内必须零 syscall ✓"


def test_merged_index_reloads_when_bin_mtime_changes(merged):
    """⭐⭐ 重建合并层后（mtime 变）⇒ **必须重新加载** ✓ —— 否则一直拿旧索引切新文件 ✓
    ⇒ 每段错位 ✓（这是"数据重 dump 后结果没变"的经典原因 ✗）。"""
    build_field(merged, "close", {"A": (0, [1.0])})
    assert P._merged_meta("A", "close")[1:] == (0, 1)

    # ⚠⚠ Windows 上有两个坑（我都踩了 ✓）：
    #   ① `os.utime(ns=…)` 对这个文件抛 `OSError 22` ✗；
    #   ② **memmap 还开着** ⇒ 重写 `.bin` 也抛 `OSError 22` ✗（文件被映射 ⇒ 无法截断 ✓）。
    #   ⇒ 先**释放 memmap**（模拟"进程里那层视图没了" ✓），但**保留 `_MERGED_IDX` 索引缓存** ✓
    #     —— 这样才真的在验"**mtime 变了 ⇒ 必须重建**"✓（若连索引一起清掉，
    #     那就变成"冷启动加载"✓，测不到失效逻辑 ✗）。
    for _mm in list(P._MERGED_MM.values()):
        try:
            _mm._mmap.close()          # noqa: SLF001 —— 测试里显式释放 ✓
        except Exception:
            pass
    P._MERGED_MM.clear()
    assert "close" in P._MERGED_IDX, "索引缓存必须还在 ✓（否则这条就不是在验失效 ✓）"

    import time as _time

    _time.sleep(0.02)
    build_field(merged, "close", {"A": (5, [9.0, 8.0])})   # 重建：start 与长度都变了
    assert P._merged_meta("A", "close")[1:] == (5, 2), "mtime 变了必须重载 ✓"
