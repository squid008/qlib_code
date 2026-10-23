# -*- coding: utf-8 -*-
"""**P2-#23 根因回归**（★ v2.11.47）：`features/<inst>` 的目录解析必须**大小写归一** ✓。

## 为什么需要这个文件
`panel_features` 在 **Linux CI 上返回 0 行**、Windows 本地正常 ✗ —— 根因是
`_union_layout → _field_bin_meta → _dir_meta` 一路用**调用方传进来的 instrument 原样**
拼路径 ✗，而 bin 目录是全小写 ✓：

· **Windows / NTFS 大小写不敏感** ⇒ 大写路径照样命中小写目录 ⇒ **本地永远看不出来** ✗✗
· **Linux 大小写敏感** ⇒ 找不到 ⇒ `layout=[]` ⇒ 面板 **0 行** ✗
  （而 qlib 自己的 `D.features` 会 `lower()` ⇒ 129 行 ✓，于是只看得到"行数不一致"这种怪现象 ✗）

⚠ 注意：本文件**不需要真数据、不需要 `qlib.init`** ✓（只测目录解析 ✓）⇒ 能在默认测试集里跑 ✓。
"""
import os

import pandas as pd
import pytest

from app.factors import panel_expr as PE
from app.factors import panel_paths as PP


@pytest.fixture()
def lower_only_provider(tmp_path, monkeypatch):
    """造一个**只有小写目录**的 provider（模拟 qlib bin 的命名约定 ✓）。"""
    feat = tmp_path / "features" / "sh600000"
    feat.mkdir(parents=True)
    (feat / "close.day.bin").write_bytes(b"\x00\x00\x00\x00" * 8)   # 形式即可 ✓
    monkeypatch.setattr(PE, "_feature_dir", lambda: str(tmp_path / "features"))
    PP.INST_DIR_CACHE.clear()
    yield tmp_path
    PP.INST_DIR_CACHE.clear()


@pytest.mark.parametrize("inst", ["SH600000", "sh600000", "Sh600000"])
def test_inst_dir_normalizes_case(lower_only_provider, inst):
    """⭐⭐ 传大写/混合大小写 ⇒ 解析出的目录**必须是小写那个** ✓。

    ⚠ 这条**在 Windows 上也会红**（老代码会原样返回 `features/SH600000` ✓）——
    这正是它能当回归测试的原因 ✓（否则本地永远绿 ✗）。
    """
    got = PE._inst_dir_impl(inst, PE._feature_dir())
    assert os.path.basename(got) == "sh600000", (
        "解析出的目录名应**归一到小写** ✓，实际 %r ✗\n"
        "  ⇒ 老代码在 Windows 上靠文件系统不敏感蒙对 ✓，到 Linux 就 0 行 ✗（P2-#23）"
        % os.path.basename(got))
    assert os.path.isdir(got)


def test_dir_meta_finds_lowercase_dir_when_given_upper(lower_only_provider):
    """⭐ 真正的症状路径：`_dir_meta("SH600000")` 必须**找得到**小写目录 ✓（否则 layout 空 ⇒ 0 行 ✗）。"""
    info = PE._dir_meta("SH600000")
    assert info, "老代码在 Linux 上这里返回 None ⇒ `_union_layout` 直接 layout=[] ⇒ 面板 0 行 ✗"
    assert "close.day.bin" in info


def test_reset_caches_clears_inst_dir_cache(lower_only_provider):
    """⭐ `reset_caches()` 必须把新缓存也清掉 ✓（否则切数据集后仍用旧目录 ✗）。"""
    PE._inst_dir_impl("SH600000", PE._feature_dir())
    assert PP.INST_DIR_CACHE, "应已缓存 ✓"
    PE.reset_caches()
    assert not PP.INST_DIR_CACHE, "reset_caches 后应清空 ✓"
