# -*- coding: utf-8 -*-
"""`.day.bin` 的**目录元信息与读盘**（★ v2.10.72，2026-10-21 ✓ —— 技术债 #2 的第五刀）。

## 为什么这块最该测（它是所有因子数据的**唯一入口**）

`features/<inst>/<field>.day.bin` 的格式是：**首 4 字节 = float32 的起始日历下标**，
其后是 float32 数据 ✓。读错一个字节 ⇒ **整条因子序列错位** ✓，而它
**不报错、不崩、图照样画** ✗ —— 只是数值整体偏了 ✓。

⚠ 源码注释里就记着一次真实事故 ✓：
> 头部若误用 `'<i4'` 读，得到的是 float32 的**位模式**
> （真实 `791` → `0x44480000` = **1145421824**）⇒ `_union_index` 把绝大多数股票判为
> 「无覆盖」⇒ 行数从 **144306 掉到 64895** ✓。

⇒ 这组测试**专门钉住"按 float32 解析"** ✓，以及三条容易错的行为：
① 过短文件（`< 2 words`）必须当**缺失**（`)`✓ 而不是读出一堆垃圾 ✓）；
② **mtime 变 ⇒ 必须重读**（否则改了数据还用旧值 ✗ —— 正是「数据戳」想防的事 ✓）；
③ 目录元信息有 **TTL 缓存** ✓，但**目录变了必须重建** ✓。
"""
from __future__ import annotations

import os

import numpy as np
import pandas as pd  # noqa: F401  （导入以保证与其它 panel 测试同一运行环境 ✓）
import pytest

from app.factors import panel_expr as P


@pytest.fixture()
def feat(tmp_path, monkeypatch):
    """把 `_feature_dir()` 指到临时目录，并清空三处缓存 ✓。"""
    monkeypatch.setattr(P, "_feature_dir", lambda: str(tmp_path))
    monkeypatch.setattr(P, "_MERGED_ENABLED", False)      # 合并层关掉 ✓（默认就是关 ✓）
    P.clear_bin_cache()
    P._BIN_META_CACHE.clear()
    P._DIR_META_CACHE.clear()
    yield tmp_path
    P.clear_bin_cache()
    P._BIN_META_CACHE.clear()
    P._DIR_META_CACHE.clear()


def write_bin(root, inst: str, field: str, start: int, values, mtime_ns: int | None = None):
    """按真实格式写一个 `.day.bin`（头部 float32 起始下标 + 数据）✓。"""
    d = root / inst
    d.mkdir(exist_ok=True)
    p = d / f"{field}.day.bin"
    arr = np.concatenate([[float(start)], np.asarray(values, dtype=np.float32)])
    p.write_bytes(arr.astype("<f4").tobytes())
    if mtime_ns is not None:
        os.utime(p, ns=(mtime_ns, mtime_ns))
    return p


# ---------------------------------------------------------------- _dir_meta
def test_dir_meta_missing_dir_returns_none(feat):
    """目录不存在 ⇒ `None` ✓（不许抛 ✓ —— 同事机器上某个票没落盘是常态 ✓）。"""
    assert P._dir_meta("NO_SUCH_INST") is None


def test_dir_meta_lists_files_with_size(feat):
    """目录元信息要给出 **{文件名: (mtime_ns, size)}** ✓ —— size 用来推 `n_rows` ✓。"""
    write_bin(feat, "SH600000", "close", 0, [1.0, 2.0, 3.0])
    info = P._dir_meta("SH600000")
    assert info is not None and "close.day.bin" in info
    mtime_ns, size = info["close.day.bin"]
    assert size == 4 * 4, "1 个头部 + 3 个数据 = 4 个 float32 = 16 字节 ✓"
    assert mtime_ns > 0


def test_dir_meta_ttl_cache_returns_same_object(feat, monkeypatch):
    """⭐ TTL 内命中 ⇒ **返回同一个 dict 对象** ✓（零 syscall ✓ —— 每次重建会给全 A×N 字段放大开销 ✗）。"""
    write_bin(feat, "SH600000", "close", 0, [1.0])
    monkeypatch.setattr(P, "_DIR_META_TTL_S", 999.0)
    first = P._dir_meta("SH600000")
    second = P._dir_meta("SH600000")
    assert first is second, "TTL 内必须复用缓存对象 ✓"


def test_dir_meta_rebuilds_when_directory_mtime_changes(feat, monkeypatch):
    """⭐⭐ 目录 **mtime 变了** ⇒ 必须重建 ✓（TTL 只防"频繁 stat" ✓，不能防"数据变了还看旧的" ✗）。

    ⚠ 实测要点 ✓：**新增文件后 Windows 上目录 mtime 不一定立刻变** ✗ ⇒ 我第一版直接
    "加个文件再读"会拿到旧快照 ✓（不是 bug，是平台的 mtime 精度/更新时机 ✓）。
    ⇒ 这里**显式 `os.utime` 改目录 mtime** ✓，钉的才是代码**真正**依赖的那条信号 ✓。
    """
    write_bin(feat, "SH600000", "close", 0, [1.0])
    monkeypatch.setattr(P, "_DIR_META_TTL_S", 0.0)        # 关掉 TTL，强制走 mtime 校验 ✓
    assert "close.day.bin" in P._dir_meta("SH600000")
    write_bin(feat, "SH600000", "volume", 0, [2.0])
    os.utime(feat / "SH600000", None)                     # 显式更新目录 mtime ✓
    info = P._dir_meta("SH600000")
    assert "volume.day.bin" in info, "目录 mtime 变后必须重建 ✓（否则永远读不到新字段 ✗）"


# ---------------------------------------------------------------- _field_bin_meta
def test_field_meta_missing_returns_none(feat):
    write_bin(feat, "SH600000", "close", 0, [1.0])
    assert P._field_bin_meta("SH600000", "volume") is None, "字段不存在 ⇒ None ✓"
    assert P._field_bin_meta("NO_SUCH", "close") is None, "股票不存在 ⇒ None ✓"


def test_field_meta_too_short_is_treated_as_missing(feat):
    """⭐⭐ 只有头部（或连头部都不全）⇒ 当**缺失** ✓。

    若判成"有数据但 0 行"✗ ⇒ 下游会算出一条**全 NaN 但看起来存在**的序列 ✓，
    比直接判缺失更难排查 ✗。
    """
    d = feat / "SH600000"
    d.mkdir()
    (d / "close.day.bin").write_bytes(b"\x00\x00\x00\x00")      # 只有 4 字节 = 1 word ✗
    assert P._field_bin_meta("SH600000", "close") is None


def test_field_meta_parses_header_as_float32(feat):
    """⭐⭐⭐ **头部必须是 float32** ✓ —— 这条直接钉住那次真实事故 ✓。

    真实起始下标 `791` 用 `'<i4'` 会读成 **1145421824** ✓（float32 的位模式 ✓）
    ⇒ 覆盖区间算错 ⇒ 行数从 144306 掉到 64895 ✓。
    """
    write_bin(feat, "SH600000", "close", 791, [1.0, 2.0])
    got = P._field_bin_meta("SH600000", "close")
    assert got is not None
    _mtime, start, n = got
    assert start == 791, "起始下标必须按 float32 解析 ✓（当成 int32 会得到 1145421824 ✗）"
    assert start != 1145421824
    assert n == 2, "n_rows = words - 1（减去头部 ✓）"


def test_field_meta_rereads_when_mtime_changes(feat):
    """⭐⭐ **文件 mtime 变就必须重读头部** ✓ —— 否则重 dump 数据后还在用旧的起始下标 ✗
    （整条序列错位 ✓，且没有任何提示 ✗）。

    ⚠⚠ 这里有个**必须写下来的语义**（实测才发现 ✓）：
      `_field_bin_meta` 先问 `_dir_meta`（**TTL 缓存的目录快照** ✓）要 mtime ✓ ——
      而**原地重写同一个文件时，目录 mtime 往往不变** ✗ ⇒ **TTL 内读到的是旧 mtime**
      ⇒ 元信息缓存命中 ⇒ **仍然返回旧起始下标** ✓。
      ⇒ 生产上靠"**重 dump 后清缓存 / 落 `.data_version` 戳**"兜底 ✓ ——
        `clear_bin_cache()` **三处全清** ✓（`_BIN_CACHE` / `_BIN_META_CACHE` / `_DIR_META_CACHE` ✓，
        ⚠ 我一度以为它漏了目录快照 ✗，核对源码后确认**没有漏** ✓ —— 差点写出一条假债 ✓）。
      ⇒ 本用例显式清掉目录快照（等价于调 `clear_bin_cache()` 的那一步 ✓），
        钉的是"**mtime 一变就必须重读**"这条契约本身 ✓。
    """
    p = write_bin(feat, "SH600000", "close", 5, [1.0], mtime_ns=1_600_000_000_000_000_000)
    assert P._field_bin_meta("SH600000", "close")[1] == 5
    # 重写同一文件、起始下标改成 9 ✓ + 显式改 mtime ✓
    p.write_bytes(np.array([9.0, 1.0, 2.0], dtype="<f4").tobytes())
    os.utime(p, ns=(1_700_000_000_000_000_000, 1_700_000_000_000_000_000))
    P._DIR_META_CACHE.clear()          # ← 见上面的 ⚠：目录快照不会自己失效 ✓
    assert P._field_bin_meta("SH600000", "close")[1] == 9, "mtime 变后必须重读 ✓"


# ---------------------------------------------------------------- _read_field_bin
def test_read_field_bin_returns_start_and_values(feat):
    """`_read_field_bin` ⇒ `(start_idx, float 数组)` ✓，且**不含头部** ✓。"""
    write_bin(feat, "SH600000", "close", 3, [10.0, 20.0, 30.0])
    got = P._read_field_bin("SH600000", "close")
    assert got is not None
    start, vals = got
    assert start == 3
    assert list(np.asarray(vals, dtype=float)) == [10.0, 20.0, 30.0], "头部不能被当成数据 ✓"


def test_read_field_bin_caches_by_mtime(feat):
    """⭐ 同一 `(inst, field)` 再读 ⇒ 走缓存，**返回同一个数组对象** ✓
    （这是 65× 提速的来源 ✓；按 mtime 失效 ✓）。"""
    write_bin(feat, "SH600000", "close", 0, [1.0, 2.0])
    a = P._read_field_bin("SH600000", "close")
    b = P._read_field_bin("SH600000", "close")
    assert a[1] is b[1], "命中缓存应复用同一数组 ✓"


def test_read_field_bin_missing_or_short_returns_none(feat):
    """缺失 / 过短 ⇒ `None` ✓（调用方据此走"整列 NaN + 物化提示"✓，不能抛 ✓）。"""
    assert P._read_field_bin("SH600000", "close") is None
    d = feat / "SH600000"
    d.mkdir()
    (d / "close.day.bin").write_bytes(b"\x00\x00\x00\x00")
    assert P._read_field_bin("SH600000", "close") is None
