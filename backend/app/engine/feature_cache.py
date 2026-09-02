# -*- coding: utf-8 -*-
"""特征计算磁盘缓存（精细版：只缓存特征，label 每次现算）。

背景：
- 每次回测（含复用模型权重）都要重新计算特征矩阵（6000 股 × 全区间 × 27 特征），
  这是复用回测依然慢的主要瓶颈。
- 特征计算的结果只取决于：股票池、特征表达式、时间范围、数据版本。
  只要这四样不变，结果就完全一样，可以安全缓存复用。

设计：
- 自定义 CachedQlibDataLoader 继承 qlib 的 QlibDataLoader，
  覆写 load：把 config 里的 "feature" 组结果缓存到磁盘（workdir/feature_cache/），
  "label" 组每次现算（label 只是 Ref(close) 简单表达式，很便宜）。
- 这样改 label_horizon（预测周期）不会让特征缓存失效，命中面更大。
- 缓存 key = md5(版本 + 股票池 + 特征表达式 + 时间范围 + 数据版本号)。
  数据更新后（数据目录 mtime 变化）key 自动变化，不会用到脏缓存。
"""
from __future__ import annotations

import hashlib
import os
import pickle

import pandas as pd

from qlib.data.dataset.loader import QlibDataLoader

try:
    from ..config import WORK_DIR, QLIB_PROVIDER_URI

    _CACHE_DIR = os.path.join(WORK_DIR, "feature_cache")
except Exception:  # pragma: no cover
    _CACHE_DIR = os.path.join(os.path.abspath("."), "feature_cache")


# ---------------------------------------------------------------------------
# 缓存 key
# ---------------------------------------------------------------------------


def _norm_instruments(instruments):
    """把 instruments（str 或 list）规范化为稳定字符串。"""
    if instruments is None:
        return "none"
    if isinstance(instruments, str):
        return instruments
    return ",".join(sorted(str(i) for i in instruments))


def _data_version() -> str:
    """数据版本号：数据目录最后修改时间（数据更新后缓存自动失效）。"""
    try:
        root = QLIB_PROVIDER_URI or ""
        mtime = 0.0
        if root and os.path.isdir(root):
            mtime = os.path.getmtime(root)
            cal = os.path.join(root, "calendars", "day.txt")
            if os.path.exists(cal):
                mtime = max(mtime, os.path.getmtime(cal))
        if mtime:
            return str(mtime)
    except Exception:
        pass
    return "unknown"


def _cache_path(instruments, exprs, names, start_time, end_time) -> str:
    """缓存 key 必须同时含表达式与列名（names）。

    同一批表达式在不同 Handler 下可能映射不同列名（如"混合"模式下 Alpha158/360
    的特征会加 A158_/A360_ 前缀），若 key 只含表达式会导致跨场景命中脏缓存。
    """
    parts = [
        "v1",
        _norm_instruments(instruments),
        "\x01".join(str(e) for e in exprs),
        "\x01".join(str(n) for n in names) if names else "",
        str(start_time),
        str(end_time),
        _data_version(),
    ]
    raw = "|".join(parts)
    return os.path.join(_CACHE_DIR, hashlib.md5(raw.encode("utf-8")).hexdigest()[:24] + ".pkl")


# ---------------------------------------------------------------------------
# 缓存读写（tmp + 原子替换，多任务并发安全）
# ---------------------------------------------------------------------------


def _load_cache(path):
    if not os.path.exists(path):
        return None
    try:
        with open(path, "rb") as f:
            return pickle.load(f)
    except Exception:
        return None


def _save_cache(path, df):
    try:
        os.makedirs(_CACHE_DIR, exist_ok=True)
        tmp = path + ".tmp"
        with open(tmp, "wb") as f:
            pickle.dump(df, f, protocol=4)
        os.replace(tmp, path)
    except Exception:
        pass


# ---------------------------------------------------------------------------
# 自定义 DataLoader
# ---------------------------------------------------------------------------


class CachedQlibDataLoader(QlibDataLoader):
    """带特征磁盘缓存的 QlibDataLoader。

    只缓存 "feature" 组（计算最重的部分）；"label" 组每次现算。
    非 dict config（无分组）时退化为父类行为。
    """

    def load(self, instruments=None, start_time=None, end_time=None):
        if not self.is_group:
            return super().load(instruments, start_time, end_time)

        out = {}
        for grp, (exprs, names) in self.fields.items():
            # feature 与 label 都走缓存，但 key 都包含各自的表达式与列名：
            #  - feature key 不含 label 配置 → 改预测周期(label_horizon)时 feature 仍命中；
            #  - label key 含 label 表达式（含 label_horizon）→ 改动后 label 单独重算（便宜）。
            path = _cache_path(instruments, exprs, names, start_time, end_time)
            df = _load_cache(path)
            if df is None:
                df = self.load_group_df(instruments, exprs, names, start_time, end_time, grp)
                _save_cache(path, df)
            out[grp] = df
        return pd.concat(out, axis=1)
