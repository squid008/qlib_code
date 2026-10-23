# -*- coding: utf-8 -*-
"""面板级表达式求值器（做法2，第一版：替换单因子测试特征加载）。

背景（2026-09-09 v1.16.8）：qlib 的 D.features 逐股票拆 job + Python 表达式树遍历，
巨型公式（CWH_BREAK_WAIT20_F1 40746 字符 / 4630 节点）全 A 5260 只耗时 ~152s，
边际成本恒定 ~30ms/只、8 核 loky 也吃不满 → 大量时间花在 5260 次 job 调度 /
单只树遍历 / 结果回传。本模块用**一块 MultiIndex(instrument, datetime) 面板** +
pandas 向量化算子（groupby-rolling/shift/逐元素）一次算完全部股票，公共子式按
str 缓存天然只算一次。实测：450 万行 groupby-rolling 0.73s、算术 ~0.01s/op。

语义锚点（2026-09-09 实证，qlib D.features 逐位对齐）：
- 输出行集 = 全日历（含停牌 NaN 行）。
- SR(X, [M]) 语义：停牌日值不算且不占滚动窗口格 → 等价实现为"把 M（默认 X）
  为 NaN 的行值抹成 NaN"，窗口算子对"非 NaN 的有效行"滚动后回填全日历
  （停牌日结果 = NaN）。实证：Mean(SR($close),3) 在 2000-05-08 停牌日 = NaN，
  复牌日窗口只含前两个有效交易日，与此等价。
- 叶子字段从 .day.bin 批量 np.fromfile 读入（首 4 字节 = 该股日历起始 idx，
  后续 float32 每日历日一行、停牌 NaN），不经过 qlib job 调度。

第一版范围：run_single_factor_tests 的特征加载（panel_features 对接），算子集 =
CWH + label/base/tag 实际用到的全部算子。
"""
from __future__ import annotations

import contextlib
import logging
import os
import re
import sys
import time
from collections import OrderedDict
from typing import Any, Dict, Optional, Sequence, Tuple, cast

import numpy as np
import pandas as pd

from ..logger import _log_swallowed  # noqa: E402
from .calc_date import DATE_FIELD, serial_array

# ★ v2.9.56：筹码换手那组已搬到 `chip_turn.py` ✓ —— 这里**显式再导出** ✓，
#   保持 `from ..factors.panel_expr import chip_turn_of` 的老写法继续有效 ✓（**不是** `import *` ✗）。
from .chip_turn import _CHIP_BASE_FIELDS, _chip_turn_proxy, chip_turn_of  # noqa: F401,E402

# ★ v2.9.57：分段/滚动算子已搬到 `seg_ops.py` ✓ —— 这里**显式引回** ✓
#   （本文件自己的求值路径还要用 ✓；**不是** `import *` ✗）。
from .seg_ops import (  # noqa: F401,E402
    _SEG_CACHE,
    _SEG_RC_CACHE,
    _by_group,
    _seg_arrays,
    _seg_grid,
    _seg_roll,
    _seg_roll_vanherk,
    _seg_window_op,
    _segment_bnd,
)

# CWH 这类巨型公式嵌套可达数百~上千层，提高递归上限（模块级，全局一次）
if sys.getrecursionlimit() < 20000:
    sys.setrecursionlimit(20000)

# ===========================================================================
# 1) 日历 / .day.bin 读取
# ===========================================================================

_CAL: Optional[pd.DatetimeIndex] = None
_FEATURE_DIR: Optional[str] = None

# 进程级 .day.bin 读盘 LRU 缓存（v1.17.7 性能优化）：
# profile（CWH_BREAK_WAIT20_F1 300 只 20.5s）显示 np.fromfile 占 10.66s≈52%——
# 同一 (inst, field) 在一次 panel_features 内会被 union_index / 各 evaluator / 字段加载
# 重复全量读多次。缓存以文件 mtime 为失效键（重 dump 后自动失效），按字节上限从最旧
# 淘汰。容量默认 768MB（QLIB_PANEL_BIN_CACHE_MB 可调）。线程不安全但 GIL + 重复读
# 无害（最多并发重复读一次）；子进程 worker 各自独立缓存。
# ★ v2.10.83（2026-10-21 ✓）：**这层缓存搬去 `panel_cache.py`** ✓（拆巨型文件的第一刀 ✓，
#   选它是因为**自包含 + 已有 7 条测试护着** ✓，风险最低 ✓）。
#   ⚠ `import` 放在原位置（而不是文件头 ✓）—— 这样 `git diff` 只看到"一处块被换掉" ✓，
#     审阅时容易确认"没有别的改动" ✓。
from .panel_cache import (  # noqa: E402
    _BIN_CACHE,
    _put_bin_cache,
    bin_cache_bytes,
    clear_bin_entries,
    set_bin_cache_mb,
)


def clear_bin_cache() -> None:
    """清空进程级 .bin 读盘缓存与字段元信息缓存（数据重 dump 后调用；测试用）。"""
    global _MERGED_DIR
    clear_bin_entries()       # ← `.bin` 那层（表 + 字节计数 ✓，见 panel_cache ✓）
    _BIN_META_CACHE.clear()
    _DIR_META_CACHE.clear()   # v1.18.36：目录级元信息快照
    _MERGED_IDX.clear()       # v1.18.42：合并层索引与 memmap 一并失效
    _MERGED_MM.clear()
    _INLINE_CACHE.clear()     # ★ v2.10.64：现算回退的结果也是**数据派生**的 ✓
    _INLINE_WARNED.clear()    #   ⇒ 重 dump 后必须重算 ✓（否则拿旧数据填新面板 ✗）
    _MERGED_DIR = None        # 允许下次重新探测（manifest 出现/消失）


def set_node_cache_mb(mb: int) -> None:
    """动态调整本进程节点缓存上限（MB）。

    节点缓存是 PanelEvaluator 实例级 OrderedDict（记账字节见 _node_bytes），故调小上限
    在实例下一次写入节点时按 LRU 逐出到新上限；worker 场景在求值前（_worker_init）
    设置，无存量节点，等效立即生效。
    """
    global _NODE_CACHE_MAX_BYTES
    _NODE_CACHE_MAX_BYTES = max(1, int(mb)) * 1024 * 1024


def _system_avail_gb() -> float:
    """当前可用内存（GB）；psutil 不可用时返回一个很大的数（不限制）。"""
    try:
        import psutil

        return psutil.virtual_memory().available / (1024 ** 3)
    except Exception as e:
        _log_swallowed("panel_expr.py", e)
        return 999.0


# 节点缓存字节上限（v1.18.1）：单表达式内部公共子式缓存也封顶。超巨型公式
# （如 CUP_POOL 24.6 万字符，单树数千节点 × 每节点 ~MB）即使只驻留一个公式，仍可能
# 把单 worker 撑到数 GB；写满后按 LRU 逐出最旧，普通公式近端命中不受影响。
# v1.18.3：默认改为按可用内存自适应两档（见 _auto_node_cache_mb）。此处模块级初值仍
# 是 512MB —— 未调用 set_node_cache_mb 时的兜底（如单元测试直接 import 本模块）。
_NODE_CACHE_MAX_BYTES = int(os.environ.get("QLIB_SFT_PANEL_NODE_CACHE_MB", "512")) * 1024 * 1024

# 两档划分依据（v1.18.3 实测，脚本 ai_test/bench_node_cache.py / bench_node_cache_parallel.py；
# 全 A 5312 只 × 2024 全年 × 7 worker、CUP_POOL 27.9 万字符）：
#   512MB → 52.4s / worker 峰值合计 5.95GB；1024MB → 46.5s / 9.56GB（-11%）；
#   2048MB → 39.8s / 16.73GB（-24%）——三档输出数值完全一致（校验和恒等）。
# 常规/中型公式（≤5 万字符：CCCMA250 峰值 176MB、CWH 5.3 万字符峰值 241MB）缓存从不
# 触顶、两档完全等价。故只取两档以控内存风险：内存宽裕给 1024MB，否则 512MB；需要
# 2048MB 等更大值可显式设 QLIB_SFT_PANEL_NODE_CACHE_MB（用户自担内存）。
_AUTO_NODE_CACHE_MIN_AVAIL_GB = 10.0   # 可用内存门槛：低于此不升级（低水位 6GB 之上留余量）
_AUTO_NODE_CACHE_MIN_PER_JOB_GB = 2.0  # 每 worker 可用内存门槛：1024MB 仅占其一半预算


def _auto_node_cache_mb(n_jobs: int) -> int:
    """节点缓存上限两档自适应（MB）：内存宽裕 → 1024，否则 512。

    显式 QLIB_SFT_PANEL_NODE_CACHE_MB 优先（可指定任意值，含 2048）。psutil 不可用时
    _system_avail_gb() 返回 999 → 视为宽裕（与 bin cache 同策略）。
    """
    try:
        env_mb = os.environ.get("QLIB_SFT_PANEL_NODE_CACHE_MB")
        if env_mb is not None:
            return max(1, int(env_mb))
        avail_gb = _system_avail_gb()
        n_jobs = max(1, int(n_jobs))
        if (avail_gb >= _AUTO_NODE_CACHE_MIN_AVAIL_GB
                and avail_gb / n_jobs >= _AUTO_NODE_CACHE_MIN_PER_JOB_GB):
            return 1024
    except Exception as e:
        _log_swallowed("panel_expr.py", e)
        pass
    return 512


def _series_mem_bytes(s: pd.Series) -> int:
    """Series 值数组的近似内存字节（用于节点缓存记账）。"""
    try:
        arr = s.array
        return int(arr.nbytes) if hasattr(arr, "nbytes") else int(s.to_numpy(dtype=np.float64).nbytes)
    except Exception as e:
        _log_swallowed("panel_expr.py", e)
        return 0


def set_panel_runtime(calendar=None, feature_dir=None):
    """免 qlib.init 的运行时注入（供并行 worker/外部直接使用）。

    面板求值唯一依赖 qlib 的是全局交易日历 _calendar()。子进程（ProcessPool
    worker）不执行 qlib.init，调用本函数注入 calendar（pd.DatetimeIndex）即可；
    feature_dir 默认从 app.config.QLIB_PROVIDER_URI 解析，无需注入。
    """
    global _CAL, _FEATURE_DIR
    if calendar is not None:
        _CAL = pd.to_datetime(calendar)
    if feature_dir is not None:
        if _FEATURE_DIR and _FEATURE_DIR != feature_dir:
            # 数据目录切换：目录快照/元信息缓存立即失效（避免跨数据源串用）
            _DIR_META_CACHE.clear()
            _BIN_META_CACHE.clear()
            clear_bin_entries()      # ★ v2.10.83：换目录 ⇒ .bin 那层也要清（含字节计数 ✓）
        _FEATURE_DIR = feature_dir


def _calendar() -> pd.DatetimeIndex:
    global _CAL
    if _CAL is None:
        from qlib.data import D

        _CAL = pd.to_datetime(D.calendar())
    return _CAL


def _feature_dir() -> str:
    global _FEATURE_DIR
    if _FEATURE_DIR:
        return _FEATURE_DIR
    try:
        from app.config import QLIB_PROVIDER_URI

        if QLIB_PROVIDER_URI:
            _cand = os.path.join(QLIB_PROVIDER_URI, "features")
            warn_missing(_cand)                 # ★ 目录不存在就告警一次（见 panel_paths ✓）
            return _cand
    except Exception as e:
        _log_swallowed("panel_expr.py", e)
        pass
    return os.path.join(os.path.abspath("."), "..", "data", "cn_data", "features")


def reset_caches() -> None:
    """清空所有**以 provider 目录/日历为前提**的进程级缓存（多数据集切换后必须调 ✓）。

    ★ 为什么必须清（2026-10-09 加"三套数据切换对照"时踩到的道理）：
      `_CAL`/`_FEATURE_DIR` 都是**解析一次就缓存**的 ✓，`.day.bin` 读盘 LRU 与 dir/meta 元数据
      也都在内存里 ✗ ⇒ 切了目录却不清它们，就会**继续读旧数据集** —— 而且**不报错** ✗✗，
      于是"对照实验"会得出"两边一样"的假象 ✗。切数据集必须连缓存一起换 ✓。
    """
    global _CAL, _FEATURE_DIR, _MERGED_DIR
    _CAL = None
    _FEATURE_DIR = None
    clear_bin_entries()          # ★ v2.10.83：`.bin` 层（表 + 字节计数 ✓）
    _BIN_META_CACHE.clear()
    _DIR_META_CACHE.clear()
    _MERGED_DIR = None
    _MERGED_IDX.clear()
    for _mm in list(_MERGED_MM.values()):
        try:
            _mm.close()
        except Exception as e:  # noqa: BLE001
            _log_swallowed("panel_expr.py", e)
            pass
    _MERGED_MM.clear()
    _SEG_CACHE.clear()
    _SEG_RC_CACHE.clear()
    _MISSING_WARNED.clear()
    INST_DIR_CACHE.clear()       # ★ v2.11.47：`features/<inst>` 大小写归一缓存也要清 ✓
    WARNED.clear()               # ★ v2.11.50："目录不存在"的告警去重集合 ✓


# ---------------- 特征合并层（features_merged/，v1.18.42 / T2 §7.7-E #7）----------------
# 背景：`features/<inst>/<field>.day.bin` = 6142 目录 / 321249 文件 / 3.2GB，**单文件仅 1.9~2.3KB**；
# 实测单次 `stat+fromfile` ≈ **205us**（只 stat 就 71us）⇒ 有效 9.5MB/s，比 NVMe 慢 20~50×，
# 且**并发救不了**（thread-4/8/16 均 ~1.8×、同路径重复读也一样慢）⇒ 只能减少 open 次数。
# 合并层把「每股每字段一个小文件」变成「**每字段一个大文件**」：全池面板的读盘次数
# 从 `股票数×字段数` 降到 `字段数`（3213 次 → 7 次，实测 **65×**）。
#
# 布局（与 `features/` **同级**，qlib 原生目录一个字节都不动）：
#     features_merged/<field>.bin      该字段全股票按代码升序拼接（逐股原样字节，
#                                      仍 `<f4`、首元素 = start_idx）
#     features_merged/<field>.idx.json 列式索引 {n, inst[], off[], cnt[], start[]}
#     features_merged/manifest.json    {version, built_at, fields, words, ...}
# 由 `backend/tools/build_merged_features.py` 生成（全量 3.2GB / ~153s / 抽样 3009 对逐字节 0 失败）。
#
# ⚠ 这是**派生数据**：源数据重 dump 后必须重建本层，或设 `QLIB_PANEL_MERGED=0` 停用
#   （`clear_bin_cache()` 也会清掉本层的进程内缓存并在下次重新探测）。
#
# ⚠⚠ **默认关闭**（2026-09-12 实测后定案）：合并层只是在「**纯 I/O**」口径下快（隔离基准
# 51×，0.676s→0.013s），但**真实面板求值里根本没有这么多 I/O 可省** —— 探针实测
# （`ai_test/probe_readfield.py`，csi300 / 3 因子 / 4131 次读）：
#     OFF：`_read_field_bin` 4131 次 / **0.395s（95 µs/次）**，占 wall 7.0%
#     ON ：同一批   4131 次 / **0.437s（106 µs/次）**，占 wall 7.6%   ← **更慢**
# 原因：① 我最初 205 µs/次 的基准**每次读都带 `os.stat`（71 µs）**，而真实代码早已用
# `_dir_meta` TTL 避开 stat；② 热页缓存下「开一个 1.9KB 小文件」比
# 「memmap 切片 + 软件缺页」**更便宜**。⇒ 合并层的收益上限只有 wall 的 ~7%，且被
# memmap 缺页成本吃掉。**冷缓存 / 更大股票池（全 A 5.3 万次读）下请自行复测后再开**。
_MERGED_ENABLED = os.environ.get("QLIB_PANEL_MERGED", "0") != "0"
# ⚠ TTL 不可省（实测教训）：`_merged_index` 若每次都 `os.stat` 那个 71MB 大文件校验 mtime，
# 而本机 stat = **71 µs**、面板求值里本函数会被调上万次 ⇒ 端到端**反而慢 0.7s**
# （与 v1.18.36 给 `_dir_meta` 加 TTL 是同一个坑）。TTL 内零 syscall；`clear_bin_cache()`
# 或重建数据后立刻失效。设 0 表示每次都校验（保守模式）。
_MERGED_TTL_S = float(os.environ.get("QLIB_PANEL_MERGED_TTL", "60"))
_MERGED_DIR: Optional[str] = None
_MERGED_IDX: "dict" = {}      # field -> (bin_mtime_ns, {inst: (off, cnt, start)}, ttl_stamp)
_MERGED_MM: "dict" = {}       # field -> np.memmap


def _merged_dir() -> Optional[str]:
    """合并层目录；未启用/不存在返回 None（只探测一次，命中与否都缓存结论）。"""
    global _MERGED_DIR
    if not _MERGED_ENABLED:
        return None
    if _MERGED_DIR is None:
        d = os.path.join(os.path.dirname(_feature_dir().rstrip("\\/")), "features_merged")
        _MERGED_DIR = d if os.path.isfile(os.path.join(d, "manifest.json")) else ""
    return _MERGED_DIR or None


def _merged_index(field: str):
    """→ (memmap, {inst: (off, cnt, start)})；缺该字段 / 索引与 .bin 不符则 None。

    以 `<field>.bin` 的 mtime 作失效键（重建合并层后自动重载）；额外校验
    `words*4 == 文件字节数`，防索引与数据不同步（截断/写一半）。
    """
    d = _merged_dir()
    if d is None:
        return None
    p = os.path.join(d, field + ".bin")
    hit = _MERGED_IDX.get(field)
    if hit is not None:
        if _MERGED_TTL_S > 0 and (time.monotonic() - hit[2]) < _MERGED_TTL_S:
            return _MERGED_MM[field], hit[1]          # TTL 内**零 syscall**
        try:
            if os.stat(p).st_mtime_ns == hit[0]:      # 过期才校验
                _MERGED_IDX[field] = (hit[0], hit[1], time.monotonic())
                return _MERGED_MM[field], hit[1]
        except OSError:
            return None
    try:
        st = os.stat(p)
    except OSError:
        return None
    try:
        import json as _json

        with open(os.path.join(d, field + ".idx.json"), encoding="utf-8") as fh:
            raw = _json.load(fh)
        if int(raw.get("words", -1)) * 4 != st.st_size:
            return None
        insts, off, cnt, stt = raw["inst"], raw["off"], raw["cnt"], raw["start"]
        m = {insts[i]: (off[i], cnt[i], stt[i]) for i in range(len(insts))}
    except Exception as e:
        _log_swallowed("panel_expr.py", e)
        return None
    _MERGED_IDX[field] = (st.st_mtime_ns, m, time.monotonic())
    _MERGED_MM[field] = np.memmap(p, dtype="<f4", mode="r")
    return _MERGED_MM[field], m


def _merged_meta(inst: str, field: str):
    """[零 syscall] → (bin_mtime_ns, start_idx, n_rows)；未收录该 (股票,字段) 返回 None。"""
    r = _merged_index(field)
    if r is None:
        return None
    e = r[1].get(inst)
    if e is None:
        return None
    return (_MERGED_IDX[field][0], int(e[2]), int(e[1]) - 1)


def _merged_values(inst: str, field: str):
    """→ (start_idx, float64 值数组)；未收录返回 None。语义与 `np.fromfile` 路径逐位一致。"""
    r = _merged_index(field)
    if r is None:
        return None
    e = r[1].get(inst)
    if e is None:
        return None
    mm, off, cnt = r[0], e[0], e[1]
    return int(e[2]), np.asarray(mm[off + 1: off + cnt], dtype=np.float64)


from .panel_paths import (  # noqa: E402
    INST_DIR_CACHE,
    WARNED,
    warn_missing,  # noqa: E402
)
from .panel_paths import inst_dir as _inst_dir_impl  # noqa: E402


def _read_field_bin(inst: str, field: str):
    """读 .day.bin → (start_idx, float64 数组)；缺失返回 None。

    命中进程级 LRU 缓存（_BIN_CACHE，mtime 校验）时跳过 np.fromfile；未命中读取
    后写入缓存。语义与原实现完全一致，仅消除同一文件的重复全量读盘。

    v1.18.42（#7）：**优先走合并层**（`features_merged/<field>.bin`，一次 memmap + 切片）
    —— 把「每股每字段一次 open」变成「每字段一次 open」，全池面板读盘次数 3213 → 7
    （实测 65×）；未收录该 (股票,字段) 时回退下面原路径。三条路径产出的 `(start, vals)`
    **逐位相同**。
    """
    key0 = (inst, field)
    if _MERGED_ENABLED:
        m = _merged_meta(inst, field)
        if m is not None:
            mtime = m[0]
            hit = _BIN_CACHE.get(key0)
            if hit is not None and hit[0] == mtime:
                _BIN_CACHE.move_to_end(key0)   # LRU 刷新
                return hit[1], hit[2]
            r = _merged_values(inst, field)
            if r is None:
                return None
            start, vals = r
            _put_bin_cache(key0, mtime, start, vals)
            return start, vals
    p = os.path.join(_inst_dir_impl(inst, _feature_dir()), f"{field}.day.bin")
    try:
        st = os.stat(p)
    except OSError:
        return None
    mtime = st.st_mtime_ns
    key = (inst, field)
    hit = _BIN_CACHE.get(key)
    if hit is not None and hit[0] == mtime:
        _BIN_CACHE.move_to_end(key)  # LRU 刷新
        return hit[1], hit[2]
    arr = np.fromfile(p, dtype="<f4")
    if arr.size < 2:
        return None
    start = int(arr[0])
    vals = arr[1:].astype(np.float64)
    _put_bin_cache(key, mtime, start, vals)
    return start, vals


# 字段文件元信息缓存（v1.18.4 性能）：(mtime_ns, start_idx, n_rows)。
# 用途 = _union_index 只需"数据区间覆盖"，原实现却走 _read_field_bin 把整列读进 BIN_CACHE：
# 全 A 5312 只 × 4 字段 ≈ 1.1GB > 768MB 上限 → 边读边逐出，随后的 load_field_series 又
# 得重读一遍（纯浪费）。改为只 stat 大小推导行数 + 读 4 字节头，读盘/缓存压力归零。
_BIN_META_CACHE: "OrderedDict[Tuple[str, str], Tuple[int, int, int]]" = OrderedDict()
_BIN_META_CACHE_MAX = 131072  # 全 A × 数十字段量级（每项 ~100B）


# 目录级元信息快照（v1.18.36）：一次 scandir 取该股票目录下全部 .bin 的 (mtime_ns, size)，
# 替代「每个字段一次 os.stat」—— 全 A 冷加载实测 os.stat 35643 次 ≈ 3.4s（`_field_bin_meta`
# 每次都 stat 做变更检测）。快照按**目录 mtime** 校验：数据重 dump（删/建目录、增删文件）
# 会更新目录 mtime → 自动失效。LRU 上限 512（每项约几十条记录）。
_DIR_META_CACHE: "OrderedDict[str, Tuple[int, dict, float]]" = OrderedDict()
_DIR_META_MAX = 512
# 快照有效期（秒）：TTL 内直接命中、**不再做任何 syscall**（这才是省 stat 的关键 ——
# 一次 panel 求值里同一 (股票, 字段) 会被 union_index / 各字段加载重复查询多次）。
# 超时才用目录 mtime 校验；数据重 dump 亦可通过 clear_bin_cache() 立即失效。
# QLIB_PANEL_DIR_META_TTL=0 表示每次都校验（保守模式）。
_DIR_META_TTL_S = float(os.environ.get("QLIB_PANEL_DIR_META_TTL", "60"))


# ---------------------------------------------------------------------------
# v1.20.25：**物化字段缺失 ⇒ 显式警告**（用户 2026-09-19 要求，见 `md/deploy.md`）
#
# 背景（最坑的一点，`deploy.md`「物化字段」一节）：`data/` 被 .gitignore 排除 ⇒
# `preclose/preopen/prehigh/prelow/prevwap`（前复权）与 `chip_cost_*` / `chip_win_*`
# 既不在数据包、也不在 git 里，**每台机器必须各生成一次** ✓。缺了**不报错** ✗：
#   · `chip_*` 缺 ⇒ 用 `COST()/WINNER()` 的公式**静默返回全 NaN** ✗（因子静默失效）；
#   · `pre*` 缺 ⇒ `forward`（前复权）静默失效 ⇒ 价格量纲因子被按后复权价排序 ✗
#     （实测 LLT K=20 年化 +9.03% ↔ 米筐 −5.24% ✓）。
# ⇒ 这里做**最低成本的防护**：这两类派生字段若**整列全 NaN**（所有股票 × 所有日期 ✓），
#   几乎必然是"文件缺失"（正常数据不会全军覆没 ✓）⇒ 打一次 WARNING（同字段只警告一次 ✓
#   不刷屏 ✓），并把**修复命令**直接写进消息 ✓。
# ⚠ 刻意**不抛异常**：正常回测/测试不应因缺物化而中断 ✓（且部分场景确实只需别字段 ✓）。
# ---------------------------------------------------------------------------
_LOGGER = logging.getLogger(__name__)
# ★ 2026-10-09 加 `mkt_`：横向统计（`BLOCKSETNUM`/`INSUM`）是**物化**出来的市场级字段，
#   缺了同样"不报错、静默全 NaN"✗（用户会以为"坑数量=0 / 纯度=0"是行情真实状态 ✗）
#   ⇒ 一并纳入"整列全 NaN 就提示 + 给出修复命令"的防护 ✓。
_MATERIALIZED_PREFIXES = ("chip_", "pre", "prevwap", "mkt_")
_MISSING_WARNED: set = set()
# ★ v2.10.64（技术债 P2-#16）：`mkt_*` 现算回退的**结果缓存**与"已提示过"集合 ✓
#   ⚠ 为什么不能只记"这个字段已自愈、下次跳过" ✗：每次调用拿到的是**新的全 NaN 列** ✓
#     ⇒ 跳过 ⇒ 列**永远是 NaN** ✗（把"慢"换成了"错"，比原问题严重得多 ✓）。
#     ⇒ 缓存的是**算出来的序列** ✓：下次仍会回填（正确 ✓），但不用再算（快 ✓）。
_INLINE_CACHE: dict = {}
_INLINE_WARNED: set = set()


def _warn_if_materialized_missing(key: str, s: "pd.Series") -> None:
    """派生/物化字段整列全 NaN ⇒ 提示"物化文件缺失 + 修复命令"（每字段仅一次 ✓）。"""
    if key in _MISSING_WARNED or not key.startswith(_MATERIALIZED_PREFIXES):
        return
    try:
        arr = np.asarray(s.to_numpy(dtype=float, copy=False), dtype=float)
        if arr.size == 0 or np.isfinite(arr).any():
            return                                            # 有值 ⇒ 正常 ✓
    except Exception as e:  # noqa: BLE001
        _log_swallowed("panel_expr.py", e)
        return
    # ★ v1.20.87：`mkt_*` 整列 NaN ⇒ **先试现算回退** ✓（用户 2026-10-09 追问：
    #   「如果 IS_GOLD_PIT 我把物化文件删了，它会**自动回退**不走物化路线计算吗？」⇒ 原来不会 ✗：
    #   `INSUM(...)` 编译产物就是字段引用 `$mkt_insum_...` ⇒ 缺文件 = 整列 NaN，只出声提示 ✗）
    #   现算走 `market_stat.compute_field_inline`：与物化**同一条路**（`all_codes()` 全市场池 +
    #   同一窗口 + 同一个 `_aggregate`/`block_mask` ✓）⇒ **不会**因为"当前面板只有 300 只"
    #   而算出偏小的全A股计数 ✗✓（那正是最危险的静默口径漂移 ✓）。
    #   就地填充 `s`（调用方持有的就是这个 Series ✓）并**不再告警**（已不是失效状态 ✓）；
    #   失败才照旧出声 ✓（慢是必然的：这是"坏了才走"的退路 ✓）。
    if key.startswith("mkt_"):
        try:
            ser = _INLINE_CACHE.get(key)
            if ser is None:
                from .market_stat import compute_field_inline
                ser = compute_field_inline(key)
                if ser is not None and len(ser):
                    _INLINE_CACHE[key] = ser       # ★ 缓存**算出来的序列**（不是"跳过"✗）
            if ser is not None and len(ser):
                _idx = s.index
                _dates = _idx.get_level_values(1) if getattr(_idx, "nlevels", 1) > 1 else _idx
                filled = ser.reindex(pd.DatetimeIndex(_dates)).to_numpy(dtype=float)
                if np.isfinite(filled).any():
                    s.iloc[:] = filled
                    # 提示只出**一次** ✓（每次评估都刷这句 ⇒ 日志没法看 ✗）
                    if _LOGGER is not None and key not in _INLINE_WARNED:
                        _INLINE_WARNED.add(key)
                        _LOGGER.warning(
                            "物化字段 $%s 缺失 ⇒ 已改用**现算回退**（结果正确 ✓ 但明显更慢 ✗）；"
                            "想恢复性能请跑：python backend/tools/materialize_market.py --overwrite ✓",
                            key)
                    return
        except Exception as _e:                                  # noqa: BLE001
            if _LOGGER is not None:
                _LOGGER.warning("$%s 现算回退失败：%r ⇒ 下面照旧给出物化修复提示 ✓", key, _e)
    _MISSING_WARNED.add(key)
    _msg = (
        "物化字段 `$%s` **整列全为 NaN** —— 极可能是【物化文件缺失】✗（缺了不报错、会静默失效 ✓）。\n"
        "  ⇒ 影响：%s\n"
        "  ⇒ 修复（每台机器各做一次，详见 md/deploy.md「物化字段」一节）：\n"
        "       python backend/tools/materialize_chip.py 400   # chip_*（分批）\n"
        "       python backend/tools/build_preclose.py         # pre*（前复权）\n"
        "       python backend/tools/materialize_market.py     # mkt_*（BLOCKSETNUM/INSUM 横向统计）\n"
        "       python backend/tools/verify_materialized.py    # 核对，退出码 0 才算好"
        % (key,
           "`COST()/WINNER()` 等公式静默失效（结果全 NaN）"
           if key.startswith("chip_") else
           "`BLOCKSETNUM()/INSUM()` 静默失效（板内股票数 / 坑数量 / 纯度全 NaN）"
           if key.startswith("mkt_") else
           "`forward`（前复权）静默失效 ⇒ 价格量纲因子会被按**后复权价**排序"))
    if _LOGGER is not None:
        _LOGGER.warning(_msg)
    else:                                                     # pragma: no cover
        import warnings as _w
        _w.warn(_msg, stacklevel=2)


def _dir_meta(inst: str):
    """该股票目录下 {文件名: (mtime_ns, size)}；目录不存在返回 None。

    TTL 内命中 → 零 syscall；过期 → 用目录 mtime 校验后顺延 TTL；
    Windows 上 `DirEntry.stat()` 复用 scandir 已取得的属性，故重建也远快于逐字段 os.stat。
    """
    d = _inst_dir_impl(inst, _feature_dir())
    hit = _DIR_META_CACHE.get(inst)
    now = time.monotonic()
    if hit is not None:
        if _DIR_META_TTL_S > 0 and (now - hit[2]) < _DIR_META_TTL_S:
            _DIR_META_CACHE.move_to_end(inst)
            return hit[1]
        try:
            if os.stat(d).st_mtime_ns == hit[0]:
                _DIR_META_CACHE[inst] = (hit[0], hit[1], now)
                _DIR_META_CACHE.move_to_end(inst)
                return hit[1]
        except OSError:
            return None
    try:
        dm = os.stat(d).st_mtime_ns
    except OSError:
        return None
    info: Dict[str, Tuple[int, int]] = {}
    try:
        with os.scandir(d) as it:
            for ent in it:
                try:
                    stt = ent.stat()
                except OSError as _e:
                    _log_swallowed('panel_expr.py', _e)
                    continue
                info[ent.name] = (stt.st_mtime_ns, stt.st_size)
    except OSError:
        return None
    _DIR_META_CACHE[inst] = (dm, info, now)
    while len(_DIR_META_CACHE) > _DIR_META_MAX:
        _DIR_META_CACHE.popitem(last=False)
    return info


def _field_bin_meta(inst: str, field: str):
    """[只 stat，不读数据] → (mtime_ns, start_idx, n_rows)；缺失/过短返回 None。

    n_rows 与 _read_field_bin 的 len(vals) 严格一致（= 文件字节数 // 4 - 1），
    保证 _union_index 推算的覆盖区间与原实现逐股相同。

    头部 dtype 必须与 _read_field_bin 一致：首 4 字节是**float32** 存的起始日历
    idx（_read_field_bin 用 np.fromfile(dtype='<f4') + int(arr[0])）。若误用 '<i4'
    读，得到的是 float32 位模式（如真实 791 → 0x44480000 = 1145421824），会让
    _union_index 把绝大多数股票判为"无覆盖"而丢行（v1.18.4 实测 144306→64895）。

    v1.18.36 性能：① 若该字段已整列读过（`_BIN_CACHE` 命中，条目即含 mtime/start/值数组），
    直接由缓存给出元信息 —— 免 stat 免开文件；② 否则走**目录快照**（一次 scandir 覆盖该股
    全部字段），不再逐字段 os.stat；③ 头部 4 字节只在快照未命中时读。
    """
    key = (inst, field)
    hit_col = _BIN_CACHE.get(key)
    if hit_col is not None:
        return (hit_col[0], hit_col[1], int(hit_col[2].size))
    if _MERGED_ENABLED:                      # v1.18.42：合并层零 syscall 给元信息
        m = _merged_meta(inst, field)
        if m is not None:
            _BIN_META_CACHE[key] = m
            return m
    info = _dir_meta(inst)
    if not info:
        return None
    ent_dir = info.get(f"{field}.day.bin")
    if ent_dir is None:
        return None
    mtime_ns, size = ent_dir
    n_words = size // 4
    if n_words < 2:  # 与 _read_field_bin 的 arr.size < 2 对齐
        return None
    hit = _BIN_META_CACHE.get(key)
    if hit is not None and hit[0] == mtime_ns:
        _BIN_META_CACHE.move_to_end(key)
        return hit
    p = os.path.join(_inst_dir_impl(inst, _feature_dir()), f"{field}.day.bin")
    try:
        with open(p, "rb") as fh:
            start = int(np.frombuffer(fh.read(4), dtype="<f4")[0])
    except OSError:
        return None
    ent = (mtime_ns, start, int(n_words - 1))
    _BIN_META_CACHE[key] = ent
    while len(_BIN_META_CACHE) > _BIN_META_CACHE_MAX:
        _BIN_META_CACHE.popitem(last=False)
    return ent


def load_field_series(instruments, field: str, start_time, end_time) -> pd.Series:
    """读一字段为 MultiIndex(instrument, datetime) 全日历面板（各股行数=其有效日历）。

    性能（v1.17.7）：原实现每股构造一个 MultiIndex+Series 再 concat（profile：数百~
    数千次 pd.MultiIndex.from_arrays/factorize 占可观测时间）。改为收集 codes/dates/seg
    三组数组后**一次** MultiIndex.from_arrays + Series 构造（顺序与原 concat 后
    sort_index 完全一致）。
    """
    cal = _calendar()
    t0 = pd.Timestamp(start_time)
    t1 = pd.Timestamp(end_time)
    req_lo = int(np.searchsorted(cal, t0, side="left"))
    req_hi = int(np.searchsorted(cal, t1, side="right")) - 1
    codes = []
    dates_parts = []
    seg_parts = []
    for inst in instruments:
        r = _read_field_bin(inst, field)
        if r is None:
            continue
        start_idx, vals = r
        lo = max(start_idx, req_lo)
        hi = min(start_idx + len(vals) - 1, req_hi)
        if hi < lo:
            continue
        seg = vals[lo - start_idx : hi - start_idx + 1]
        dates = cal[lo : hi + 1]
        codes.append(np.repeat(inst, len(dates)))
        dates_parts.append(dates)
        seg_parts.append(seg)
    if not codes:
        return pd.Series(dtype=np.float64)
    index = pd.MultiIndex.from_arrays(
        [np.concatenate(codes), np.concatenate(dates_parts)],
        names=["instrument", "datetime"])
    return pd.Series(np.concatenate(seg_parts), index=index).sort_index()


def _build_multiindex(cal, seg_insts, seg_lens, seg_cal_lo) -> pd.MultiIndex:
    """免 factorize 构造与 `MultiIndex.from_arrays` **取值完全等价**的 MultiIndex。

    性能（v1.18.31）：`from_arrays` 要对 35 万个 object 字符串做 hash-factorize，
    实测 **21.5ms/次**，而一轮求值至少 3 次（并集 1 次 + 每字段 1 次）= 40~135ms。
    改为直接给 levels+codes：
      · level0 = 各段 instrument 的**首次出现序**（`dict.fromkeys`，300 项）；
        codes0 = `np.repeat(段序号, 段长)` —— 300 次 Python 操作 + O(n) numpy，
        **绝不在 Python 里遍历 35 万个 object**（那样反而更慢，实测 43ms）。
      · level1 = 各段日期的**首次出现序**，用 `pd.factorize(拼接日期)` 复现
        （datetime64 走整数哈希，5~8ms）。
    实测 21.5ms → 8.4ms，且 `equals` / `get_level_values` / `sort_index` / `union` /
    布尔掩码 / `reindex` 全部一致（`ai_test/probe_mi_build.py` 验证；仅内部
    codes/levels 的**表示**不同，取值层面等价）。

    ⚠ level1 必须保持「首次出现序」而不能换成升序：`MultiIndex` 的 codes 比较 /
    lexsort 都依赖 levels 顺序，换成升序会让 `sort_index()` 的结果与原来不同。
    """
    if not seg_insts:
        return pd.MultiIndex.from_arrays([[], []], names=["instrument", "datetime"])
    uniq0 = list(dict.fromkeys(seg_insts))
    pos_of = {v: i for i, v in enumerate(uniq0)}
    codes0 = np.repeat(np.array([pos_of[v] for v in seg_insts], dtype=np.int64), seg_lens)
    dates = np.concatenate([cal[a : a + L] for a, L in zip(seg_cal_lo, seg_lens)])
    codes1, uniq1 = pd.factorize(dates)
    return pd.MultiIndex(
        levels=[pd.Index(uniq0, dtype=object), pd.DatetimeIndex(uniq1)],
        codes=[codes0, codes1], names=["instrument", "datetime"])


def _union_layout(instruments, start_time, end_time, fields=("close",)):
    """一次遍历同时产出 (并集 index, layout, req_lo, req_hi)。

    每只股票在其【参与字段】数据区间并集 ∩ [start,end] 上的全历 index ——
    与 qlib D.features 对齐：不同字段 .day.bin 覆盖范围可能不同（如长期停牌股
    close 止于某日、limit_up/is_st 标签覆盖更长），qlib concat 各列取 union 行集，
    单字段索引无法代表"全历"，必须取全部参与字段的覆盖并集。

    layout = [(inst, row0, row1, cal_lo), ...]：该股占用 index 的 [row0, row1) 行，
    对应交易日历下标 [cal_lo, cal_lo + (row1-row0))。供 `_load_field_on` 把字段值
    **按位铺到 index 上**，免去「每字段一次 `MultiIndex.from_arrays` + `sort_index` +
    `Series.reindex(_full)`」（实测三项合计 ~80ms/字段）。

    性能沿革：v1.17.7 改「收集后一次 from_arrays」（原为逐股 append，O(k²)）；
    v1.18.4 字段覆盖改走 `_field_bin_meta`（只 stat、不读整列数据，全 A 曾达 1.1GB
    反复逐出）；v1.18.31 顺带产出 layout 并改用 `_build_multiindex`。
    """
    cal = _calendar()
    req_lo = int(np.searchsorted(cal, pd.Timestamp(start_time), side="left"))
    req_hi = int(np.searchsorted(cal, pd.Timestamp(end_time), side="right")) - 1
    fields = tuple(dict.fromkeys(fields))
    seg_insts, seg_lens, seg_cal_lo = [], [], []
    layout = []
    pos = 0
    for inst in instruments:
        lo_max, hi_min = None, None
        for fld in fields:
            r = _field_bin_meta(inst, fld)
            if r is None:
                continue
            start_idx, n_rows = r[1], r[2]
            a = max(start_idx, req_lo)
            b = min(start_idx + n_rows - 1, req_hi)
            if b < a:
                continue
            lo_max = a if lo_max is None else min(lo_max, a)
            hi_min = b if hi_min is None else max(hi_min, b)
        if lo_max is None or hi_min < lo_max:
            continue
        n = hi_min - lo_max + 1
        seg_insts.append(inst)
        seg_lens.append(n)
        seg_cal_lo.append(lo_max)
        layout.append((inst, pos, pos + n, lo_max))
        pos += n
    idx = _build_multiindex(cal, seg_insts, seg_lens, seg_cal_lo)
    return idx, layout, req_lo, req_hi


def _union_index(instruments, start_time, end_time, fields=("close",)) -> pd.MultiIndex:
    """`_union_layout` 的薄封装：只要并集 index（保留原签名，供外部/工具调用）。"""
    return _union_layout(instruments, start_time, end_time, fields)[0]


def _load_field_on(idx, layout, req_lo, req_hi, field: str) -> np.ndarray:
    """读一字段的值数组，**直接按位铺到 `idx`（= `_union_layout` 的并集 index）上**。

    等价于 `load_field_series(instruments, field, start, end).reindex(idx)`：
    该字段在某股上的可用区间 `[max(start_idx,req_lo), min(start_idx+n-1,req_hi)]`
    必落在该股的 union 区间 `[cal_lo, cal_lo+len)` 内（union 取的是各字段覆盖的
    min-start / max-end），故按「日历下标差」定位即可；其余位置留 NaN —— 与原
    `reindex` 对缺失标签补 NaN 完全一致。

    性能（v1.18.31）：省掉每字段一次的 `MultiIndex.from_arrays`（21.5ms）、
    `sort_index()`（6.5ms）与 `Series.reindex(_full)`（25ms）。
    """
    n = len(idx)
    out = np.full(n, np.nan, dtype=np.float64)
    for inst, r0, _r1, cal_lo in layout:
        r = _read_field_bin(inst, field)
        if r is None:
            continue
        start_idx, vals = r
        lo = start_idx if start_idx > req_lo else req_lo
        hi = start_idx + len(vals) - 1
        if hi > req_hi:
            hi = req_hi
        if hi < lo:
            continue
        a = r0 + (lo - cal_lo)
        out[a : a + (hi - lo) + 1] = vals[lo - start_idx : hi - start_idx + 1]
    return out


def _trim_index_from(idx: pd.MultiIndex, start_time) -> pd.MultiIndex:
    """把面板 index 裁剪到 datetime ≥ start_time（保持行序；无需裁剪时原样返回）。

    v1.18.4 性能：panel_features 的输出行集 = [start_time, end_time] 的字段覆盖并集，
    与最后一个 evaluator 的 _full = [read_start, end_time] 的并集只差"预热段"
    （read_start ≤ start_time），按 datetime 裁剪即可，免去重算一遍全量 union（全 A
    是数百万行 index 的重复构造 + 数万次 stat）。
    """
    if len(idx) == 0:
        return idx
    dt = idx.get_level_values("datetime")
    mask = np.asarray(dt >= pd.Timestamp(start_time))
    if mask.all():
        return idx
    return idx[mask]


# ===========================================================================
# 2) 轻量 AST
# ===========================================================================


# 结构去重（hash-cons，v1.18.4）：把"结构相同"的子树收敛到同一个 int key，作为
# 节点缓存 key。原实现每次 eval 都 reconstruct(node) 重建子树字符串（CUP_POOL 实测
# 递归 262 万次 / 343M 字符，占单块耗时 15.4%）。但**不能**简单换成节点唯一 id：
# 公式内重复子式极多（实测 CWH 11562 节点 / 仅 150 个唯一子树 = 77×；CUP_POOL
# 49321 / 2141 = 23×），丢掉结构去重会让每个重复子式各自重算（quick 回归
# 15.3s → 342s 的教训）。本方案：parse 时按 (op, 子key...) 做一次 hash-cons，子 key
# 是 int → 构造/哈希/比较都是 O(arity)，整体 O(节点数)；相同结构仍共享同一个 key，
# 故缓存命中率与原实现一致、数值逐位不变。_CANON 是单次解析的局部表，parse_expr
# 入口清空（节点缓存本就按单条表达式清空 → 跨表达式无需共享 key）。
_CANON: Dict[tuple, int] = {}


class Node:
    __slots__ = ("op", "args", "raw", "key")

    def __init__(self, op, args, raw):
        self.op = op
        self.args = args
        self.raw = raw
        parts = [op]
        for a in args:
            parts.append(a.key if isinstance(a, Node) else a)
        self.key = _CANON.setdefault(tuple(parts), len(_CANON))

    def __repr__(self):
        return self.raw


# 中缀二元运算符 → Node op（qlib 表达式允许 A/B、A-B 等写法）
_INFIX = {"+": "Add", "-": "Sub", "*": "Mul", "/": "Div"}
_INFIX_PREC = {"+": 10, "-": 10, "*": 20, "/": 20}

# 词法正则（v1.18.4 性能）：必须用 `match(text, pos)` 而非 `match(text[pos:])`——
# 巨型公式（CUP_POOL 24.6 万字符 / ~10 万 token）下每次切片都是 O(剩余长度) 复制，
# 累计复制量以 GB 计（实测 parse_expr 1.85s/块）。带 pos 匹配为零拷贝，语义等价。
# ★ 2026-10-15（v2.4.0）：**递推组**的两个内部算子（不是给用户写的 ✓，由 `REF(变量,k)` 编译而来 ✓）
_RECUR_OP = "RecurOut"        # RecurOut(j, N, body_0 … body_{N-1}) ⇒ 算整组、取成员 j 的当前值
_RECUR_PREV_OP = "RecurPrev"  # RecurPrev(j, k) ⇒ 组内取成员 j 的 k 根前（只在组内出现 ✓）
_RECUR_CUR_OP = "RecurCur"    # RecurCur(j) ⇒ 组内取成员 j 的**本根**值（同根引用先定义的成员 ✓）

_RE_FIELD = re.compile(r"\$[A-Za-z_][A-Za-z0-9_]*")
_RE_NUM = re.compile(r"-?\d+(?:\.\d+)?(?:[eE][+-]?\d+)?")
_RE_IDENT = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")


def parse_expr(text: str) -> Node:
    """递归下降解析 qlib 表达式：$field / 数字 / Func(args) / 中缀 + - * /。

    自左到右按优先级处理中缀；逗号/右括号作为参数边界。
    """
    _CANON.clear()  # 单次解析的结构去重表（节点 key 分配见 Node.__init__）
    pos = 0
    n = len(text)

    def skip():
        nonlocal pos
        while pos < n and text[pos] in " \t\r\n":
            pos += 1

    def peek():
        skip()
        return text[pos] if pos < n else ""

    def parse_expr_prec(min_prec: int) -> Node:
        """Pratt：先解析一元前缀，再按优先级循环吸收中缀。"""
        nonlocal pos
        left = parse_prefix()
        while True:
            skip()
            if pos >= n:
                break
            ch = text[pos]
            if ch in _INFIX:
                prec = _INFIX_PREC[ch]
                if prec < min_prec:
                    break
                pos += 1  # consume op
                right = parse_expr_prec(prec + 1)
                left = Node(_INFIX[ch], [left, right], ch)
            else:
                break
        return left

    def parse_prefix() -> Node:
        nonlocal pos
        skip()
        c = text[pos]
        # 一元负号（仅当后随数字/字段/函数且上下文需要 → 简化：数字已含负号）
        if c == "$":
            m = _RE_FIELD.match(text, pos)
            # ★ v2.11.31（mypy 收口 ✓，同时也是**真 bug 防线**）：正则没匹配上时
            #   原写法会 `AttributeError: 'NoneType' object has no attribute 'group'` ✗
            #   —— 用户只看到一句与公式毫无关系的错 ✓（正是本项目最忌的"看不懂的崩溃"✓）。
            if m is None:
                raise ValueError("字段名不合法（`$` 后应是字母/数字/下划线）：…%s" % text[pos:pos + 12])
            tok = m.group(0)
            pos = m.end()
            return Node("field", [tok], tok)
        if c.isdigit() or (c == "-" and pos + 1 < n and text[pos + 1].isdigit()):
            m = _RE_NUM.match(text, pos)
            if m is None:                     # 理论上进不来（上面已判 c 是数字/负号+数字）
                raise ValueError("数字字面量不合法：…%s" % text[pos:pos + 12])
            tok = m.group(0)
            pos = m.end()
            return Node("const", [tok], tok)
        # ★★ v2.1.28：**一元负号**（负号后**不是数字**的情形）✗→✓ ——
        #   以前只支持 `-5` 这种字面量 ✗ ⇒ `-Log(x)` / `-$close` / `-(...)` 一律
        #   `无法解析` ✗（2026-10-11 实测：用户库因子原文 `-log(market_cap)` 就撞在这里 ✓，
        #   然后静默退到 qlib 回退路径、再报一个**误导性**的 `[LN] is not registered` ✗）。
        #   ⚠ 生成 **`Sub(0,x)`** 而不是 `Neg(x)`：`Sub` 在面板与 qlib **两边都注册** ✓，
        #     而 qlib 没有 `Neg` ✗（`reconstruct()` 出来的串也可能直接喂给 qlib ✓）。
        #   优先级与数学一致（一元负号紧于 `*`）✓：`-a*b` ⇒ `Sub(0,a) * b` ✓。
        if c == "-":
            pos += 1
            operand = parse_prefix()
            return Node("Sub", [Node("const", ["0"], "0"), operand], "-")
        if c == "(":
            pos += 1
            inner = parse_expr_prec(0)
            skip()
            if pos < n and text[pos] == ")":
                pos += 1
            return inner
        m = _RE_IDENT.match(text, pos)
        if not m:
            raise ValueError(f"无法解析: {text[pos:pos+40]}")
        name = m.group(0)
        pos = m.end()
        skip()
        if pos >= n or text[pos] != "(":
            # 裸标识符（非常量函数/字段）：可能是除法等残留，容错为常量 0
            return Node("const", ["0"], name)
        pos += 1
        # ★★ v2.1.28：**算子名大小写/别名归一** ✗→✓ ——
        #   解析器原来把函数名**原样**当算子名 ✗ ⇒ 裸写小写 `log(x)` 会得到 op=`log`，
        #   而 `_apply` 只注册了 `Log` ✗ ⇒ `panel_expr 不支持算子 log` ⇒ 静默退 qlib ✗
        #   （用户库原文 `-log(market_cap)` 的另一半就撞在这里 ✓）。
        #   ⚠ 只在**确认是函数调用**（后面跟着 `(` ✓）之后归一 ⇒ 裸字段名不受影响 ✓。
        name = _OP_CANON.get(name.lower(), name)
        args = []
        while True:
            skip()
            if pos < n and text[pos] == ")":
                pos += 1
                break
            args.append(parse_expr_prec(0))
            skip()
            if pos < n and text[pos] == ",":
                pos += 1
                continue
            if pos < n and text[pos] == ")":
                pos += 1
                break
            raise ValueError(f"期望 , 或 )：{text[pos:pos+30]}")
        return Node(name, args, name)

    root = parse_expr_prec(0)
    skip()
    if pos != n:
        raise ValueError(f"尾部多余: {text[pos:pos+40]}")
    return root


def reconstruct(node: Node) -> str:
    if node.op in ("field", "const"):
        return node.raw
    if node.op == "Add":
        return f"Add({reconstruct(node.args[0])},{reconstruct(node.args[1])})"
    if node.op == "Sub":
        return f"Sub({reconstruct(node.args[0])},{reconstruct(node.args[1])})"
    if node.op == "Mul":
        return f"Mul({reconstruct(node.args[0])},{reconstruct(node.args[1])})"
    if node.op == "Div":
        return f"Div({reconstruct(node.args[0])},{reconstruct(node.args[1])})"
    return f"{node.op}({','.join(reconstruct(a) for a in node.args)})"


# ===========================================================================
# 3) 求值器
# ===========================================================================

_ROLL_FUNC = {"Mean": "mean", "Max": "max", "Min": "min", "Sum": "sum", "Std": "std",
              "Var": "var",
              # ★ v1.20.83：`Count` 只作**兼容**用 —— codegen 早已不再为 `COUNT(X,N)` 生成它
              #   （改用 `Sum(Gt(Abs(X),0),N)` ✓，见 codegen 里 CONST 分支的长注释 ✓）；
              #   这里补上只为两件事：① 库里**旧缓存**的 expression 不再报
              #   `panel_expr 不支持算子 Count` ✗（2026-10-09 实测：用户选股公式直接卡在这句 ✓）；
              #   ② 语义**对齐 qlib** ✓（qlib `Count` = 窗口内非 NaN 个数 ✗）。
              #   ⚠ 它的口径**不是**通达信 COUNT ✗ ⇒ 谁都不该用它算"条件成立天数" ✓。
              "Count": "count"}
# qlib 的 Max/Min 是滚动窗口极值；通达信 MAX(A,B)/MIN(A,B) 元素取大/小 → Greater/Less
_BIN_ELEM = {
    "Add": "add", "Sub": "sub", "Mul": "mul", "Div": "div",
    "Greater": "max", "Less": "min",
    "Power": "pow", "Pow": "pow",  # POW(X,Y)=X^Y（Power=qlib 内建名；Pow=旧编译别名）
}
_BIN_CMP = {"Gt": "gt", "Ge": "ge", "Lt": "lt", "Le": "le", "Eq": "eq", "Ne": "ne"}
_UNARY = {"Abs": "abs", "Sqrt": "sqrt", "Log": "log", "Neg": "neg", "Sgn": "sign",
          "Exp": "exp"}          # v1.20.34：EXP(X)=e^X（向量化 ⇒ 与 Sqrt/Log 同路径 ✓）

# ★★ v2.1.28：**算子名归一表**（大小写不敏感 + 别名）—— 供 `parse_prefix` 用 ✓。
#   为什么必须有（2026-10-11 实测 ✓）：解析器原来把函数名**原样**当算子名 ✗ ⇒ 用户公式原文
#   里的小写 `log(x)`（通达信/益盟写法 ✓）得到 op=`log` ✗，而 `_apply` 只注册了 `Log` ✓
#   ⇒ `panel_expr 不支持算子 log` ⇒ 面板失败 ⇒ 静默退到 qlib 回退路径 ✗
#   ⇒ 最后报一个**误导性**的 `The operator [LN] is not registered` ✗（查了很久才定位 ✓）。
#   ⚠ 语义口径与 `parser/codegen.py` 的 `LOG/LN → Log` **保持一致** ✓（自然对数 ✓）——
#     用户那条 `-log(market_cap)` 的口径就是 ln(市值) ✓，不能在这里改成 10 为底 ✗。
_OP_ALIAS = {"ln": "Log", "log": "Log"}          # 通达信 LN/LOG 都按自然对数（与 codegen 一致 ✓）
_OP_CANON: Dict[str, str] = {}
for _o in (*_ROLL_FUNC, *_BIN_ELEM, *_BIN_CMP, *_UNARY,
           "If", "ROUND", "And", "Or", "Ref", "SR", "Not",
           "BARSLAST", "BARSCOUNT", "BARSSINCEN", "HHVBARS", "LLVBARS",
           "DYN_REF", "DYN_MIN", "DYN_MAX", "DYN_SUM", "DYN_COUNT", "DYN_MEAN",
           "DYN_HHVBARS", "DYN_LLVBARS"):
    _OP_CANON.setdefault(_o.lower(), _o)
_OP_CANON.update(_OP_ALIAS)

# 逐元素算子的 numpy ufunc 直落表（v1.18.29）。
# 本机无 numexpr，pandas 的 Series 算术分派走 operator.* + 重包 Series；当两列同 index
# 且值为 float64 时，numpy ufunc 结果与之**逐位一致**（实测 17 组含 NaN 对拍全 True）。
# 注意：pandas 在内部用 errstate 抑制了 divide-by-zero / invalid 告警，直接调 numpy 会
# 漏出 RuntimeWarning，故下面统一套 np.errstate(all="ignore")（进入开销仅 ~1us）。
_NUM_UFUNC = {
    "add": np.add, "sub": np.subtract, "mul": np.multiply, "div": np.divide,
    "pow": np.power,
    "gt": np.greater, "ge": np.greater_equal, "lt": np.less,
    "le": np.less_equal, "eq": np.equal, "ne": np.not_equal,
}


def _is_num_scalar(x) -> bool:
    """可作为 ufunc 广播标的的数值常量。

    排除 str/bytes（pandas 对字符串走另一套路径）；None/list/ndarray 等一律返回 False
    → 落到原 pandas 兜底分支，保持既有行为。
    """
    return np.isscalar(x) and not isinstance(x, (str, bytes))


def _as_series(x, template: Optional[pd.Series]) -> pd.Series:
    """把标量 / **数组** / Series 归一到 Series（常量按 template index 广播）。

    ★ v2.11.27（技术债 P2-#20 清偿 ✓）：**支持数组入参** ✗→✓ ——
    原来只认「标量 or Series」✗ ⇒ 递推组内部偶尔给出 `ndarray`/`list` 时
    `float(x)` 直接炸 ✗（`TypeError: only length-1 arrays can be converted to Python scalar` ✓），
    而**兜底路径**（`_apply_node` 里两侧都不是 Series ✓）正是为它们准备的 ✓ ——
    结果那条路一进就崩 ✗。

    规则：数组 ⇒ 长度与 `template` 一致就用它的 index ✓（保证后续 `_align` 能按标签对齐 ✓），
    否则退化成 `RangeIndex` ✓（两侧都是数组时 ✓，位置对齐即可 ✓）。
    """
    if isinstance(x, pd.Series):
        return x
    if isinstance(x, (np.ndarray, list, tuple)):
        arr = np.asarray(x, dtype=float)
        if template is not None and len(template) == len(arr):
            return pd.Series(arr, index=template.index)
        return pd.Series(arr)
    if template is not None:
        return pd.Series(float(x), index=template.index)
    raise ValueError("常量不能单独成为结果（缺少 Series 上下文）")


def _align(a: pd.Series, b: pd.Series):
    """两列对齐到并集。字段级面板同 index 时零开销。

    不同行集（字段覆盖范围/上市日不同）：并集（不排序——MultiIndex 混合列类型
    不能整体 sort；pandas 运算按 label 对齐与顺序无关）。

    v1.18.33 性能：全 A 大公式 cProfile 显示 `MultiIndex.equals` 占 6.2s / 6723 次
    （逐元素比较 object level）。两处减法：① `is` 同一对象（同一 evaluator 内部
    index 常为同一对象，如缓存的 `_active_index`）直接返回；② `equals` 要求等长，
    长度不同必不相等，可跳过。
    """
    ia, ib = a.index, b.index
    if ia is ib:
        return a, b
    if len(ia) == len(ib) and ia.equals(ib):
        return a, b
    idx = ia.union(ib)
    # union 可能产生未排序/类型不齐的 index：算术按 label 对齐即可，无需排序
    return a.reindex(idx), b.reindex(idx)






# ★ 2026-10-19 拆分 ✓：原 `PanelEvaluator._apply` → 模块级 `_apply_node`（**算子分派** ✓ 257 行 ✓）。
#   留在本文件：它 15 个自由名是本文件模块级 ✗ ⇒ 搬去新模块会**循环 import** ✗。
def _apply_node(op: str, args, sr: bool, active=None, ev=None) -> Optional[pd.Series]:
    if op == "Ref":
        s, k = args[0], int(args[1])
        if not isinstance(s, pd.Series):
            raise ValueError("Ref 首参不能是常量")
        # active 行集上普通 shift：SR 时 active 已剔停牌 → 语义正确
        return _ref(s, k, sr=False)
    if op in _ROLL_FUNC:
        s, N = args[0], int(args[1])
        if not isinstance(s, pd.Series):
            raise ValueError(f"{op} 首参不能是常量")
        # SR 时 active 已剔停牌 → 普通滚动即可（无需 _valid_rolling 二次剔除）
        return _roll(s, _ROLL_FUNC[op], N, sr=False)
    if op in ("EMA", "EMA_TDX"):
        s, N = args[0], args[1]
        if not isinstance(s, pd.Series):
            raise ValueError(f"{op} 首参不能是常量")
        # EMA 非固定窗口（指数递归），逐股 ewm（sr=True 时 active 已是压缩序列）
        return _ema(s, float(N), tdx=(op == "EMA_TDX"), sr=sr)
    if op == "SMA":
        s, N, M = args[0], args[1], args[2]
        if not isinstance(s, pd.Series):
            raise ValueError("SMA 首参不能是常量")
        return _sma(s, float(N), float(M), sr=sr)
    if op == "BARSCOUNT":
        # 自数据起点累计有效交易日（BARSCOUNT($close)）；面板与 qlib 同从各自
        # read_start 起点累计 → 对齐。逐股内核 _bars_count_seg。
        s = args[0]
        if not isinstance(s, pd.Series):
            raise ValueError("BARSCOUNT 首参不能是常量")
        from . import ops_ext

        return _by_group(s, None, _bars_count_seg,
                         lambda v, w, ss, se: ops_ext.bars_count_seg(v, w, ss, se))
    if op == "BARSSINCE":
        # 距首次成立周期数（不限窗口）。面板与 qlib 共用 ops_ext.barsince_vec。
        s = args[0]
        if not isinstance(s, pd.Series):
            raise ValueError("BARSSINCE 首参不能是常量")
        from . import ops_ext

        return _by_group(s, None, lambda v: ops_ext.barsince_vec(v),
                         lambda v, w, ss, se: ops_ext.barsince_vec_seg(v, w, ss, se))
    if op == "FILTER":
        # 信号抑制：成立输出 1 后 N-1 周期抑制重复。面板与 qlib 共用 ops_ext.filter_vec。
        s, N = args[0], args[1]
        if not isinstance(s, pd.Series):
            raise ValueError("FILTER 首参不能是常量")
        from . import ops_ext

        return _by_group(s, None, lambda v: ops_ext.filter_vec(v, int(N)),
                         lambda v, w, ss, se: ops_ext.filter_vec_seg(v, w, ss, se))
    if op == "TRUNC":
        # 向零截断取整（np.trunc；3.7→3、-3.7→-3），NaN 保持
        s = args[0]
        if not isinstance(s, pd.Series):
            raise ValueError("TRUNC 参数不能是常量")
        return np.trunc(s)
    if op == "BETWEEN":
        # BETWEEN(X,A,B)：X∈[min(A,B), max(A,B)] → 1 否则 0；X NaN 保持 NaN
        x, a, b = args
        tpl = x if isinstance(x, pd.Series) else (a if isinstance(a, pd.Series) else b)
        x = _as_series(x, tpl)
        a = _as_series(a, tpl)
        b = _as_series(b, tpl)
        a, b = _align(a, b)
        lo = a.where(a <= b, b)
        hi = a.where(a >= b, b)
        out = ((x >= lo) & (x <= hi)).astype(np.float64)
        if x.isna().any():
            out = out.mask(x.isna())
        return out
    if op in ("IdxMax", "IdxMin", "Rank", "Slope", "Rsquare", "Resi", "Quantile"):
        # Alpha158 窗口类：逐股复刻 qlib Rolling 语义（min_periods=1），面板与 qlib 同源
        s = args[0]
        if not isinstance(s, pd.Series):
            raise ValueError(f"{op} 首参不能是常量")
        N = int(args[1])
        q = args[2] if op == "Quantile" else None
        return _by_group(s, None, lambda v: _seg_window_op(op, v, N, q))
    if op == "Corr":
        # Alpha158 相关：两序列逐股 rolling corr + std≈0 置 NaN（同 qlib Corr）
        a, b = args[0], args[1]
        N = int(args[2])
        if not isinstance(a, pd.Series) or not isinstance(b, pd.Series):
            raise ValueError("Corr 两参数须为序列")
        a, b = _align(a, b)
        return _corr_pair_panel(a, b, N)
    if op in _BIN_ELEM or op in _BIN_CMP:
        a, b = args
        # ---- numpy 快路（v1.18.29）----
        # 三条等价改动（17 组含 NaN 对拍逐位一致）：
        #  ① 常量操作数**不再经 _as_series 物化成整列**：广播一列常量要写 ~2.8MB
        #     （实测 0.85ms/次；大公式单轮约 950 个常量操作数 ≈ 0.8s），而 ufunc 对
        #     标量广播零成本。v1.18.25 只对 Power 的标量指数做了这一步，此处覆盖
        #     add/sub/mul/div/Greater/Less 与全部比较算子。
        #  ② Greater/Less 的 `a.where(a >= b, b)` 要过两遍 Series 比较 + 掩码写回
        #     （实测 1.65ms → np.where 0.88ms）。
        #  ③ 比较算子走 ufunc，省掉一次 pandas 结果包装。
        # NaN 语义与 pandas 一致：where 对 NaN 比较得 False → 取 b，np.where 同。
        # ⚠ **不能**用 np.maximum/np.minimum —— 它们传播 NaN，而此处语义是
        #   「a 为 NaN 时取 b」。
        a_is_s = isinstance(a, pd.Series)
        b_is_s = isinstance(b, pd.Series)
        if (a_is_s or b_is_s) and (a_is_s or _is_num_scalar(a)) \
                and (b_is_s or _is_num_scalar(b)):
            fn = _BIN_ELEM[op] if op in _BIN_ELEM else _BIN_CMP[op]
            if fn == "pow" and a_is_s and not b_is_s:
                # **「Series ** 标量」保留 pandas 路径**（即 v1.18.25~v1.18.28 的行为）：
                # numpy 对「ndarray ** 标量」启用**快速标量幂**（x**2 → x*x、x**0.5 → sqrt），
                # `np.power(ndarray, 标量)` 则走通用数组内循环，两者在特殊值上不同 ——
                # 实测 `(-inf) ** 0.5`：前者（含 Series/pandas 路径） = nan，后者 = inf。
                # Log(0) 会产出 -inf，故这是真实语义漂移，必须避开。
                # 该路径本身已够快（且不必再物化常量列），v1.18.25 起的快照已覆盖。
                # 注意「标量 ** Series」（常量在左）旧行为本就是数组路径（指数是数组，
                # numpy 不启用标量快速幂），故仍走下面的 numpy 快路。
                return np.power(a, b)
            if a_is_s and b_is_s:
                a2, b2 = _align(a, b)
                idx = a2.index
                av, bv = a2.to_numpy(), b2.to_numpy()
            elif a_is_s:
                # 常量在右：保持标量，交给 ufunc 广播（等价于原 _as_series 的整列广播）
                idx, av, bv = a.index, a.to_numpy(), b
            else:
                idx, av, bv = b.index, a, b.to_numpy()
            with np.errstate(all="ignore"):
                if fn == "max":
                    vals = np.where(av >= bv, av, bv)
                elif fn == "min":
                    vals = np.where(av <= bv, av, bv)
                else:
                    vals = _NUM_UFUNC[fn](av, bv)
            if op in _BIN_CMP:
                vals = vals.astype(np.float64, copy=False)
            return pd.Series(vals, index=idx)
        # ---- pandas 兜底（非常量非 Series 的入参；行为与改动前完全一致）----
        # ★ v2.1.28：**两侧都是标量**（例如 `Sub(0,Log(0))`：用户写 `-log(x)` 而 `x`
        #   不是带 `$` 的字段 ⇒ 被当常量 0 ⇒ `Log(0)` 是标量 ✓）时直接算标量 ✓ ——
        #   否则 `_as_series` 会去取 `template.index` ⇒
        #   `'numpy.float64' object has no attribute 'index'` ✗（实测踩到 ✓，
        #   且报错完全看不出"是字段少写了 $" ✗）。
        # ★ v2.11.27（技术债 P2-#20 清偿 ✓）：**必须是"两侧都是标量"才走这条** ✗→✓ ——
        #   原来只判"两侧都不是 Series" ✗ ⇒ **数组**入参也会进来 ✓，
        #   于是 `Greater(ndarray, ndarray)` 落到下面的内建 `max(_af,_bf)` ⇒
        #   `ValueError: truth value of an array … is ambiguous` ✗（实测 ✓）。
        #   现在数组会落到 pandas 兜底 ✓（`_as_series` 已支持数组 ✓），走 `np.where` 那条 ✓。
        if (not isinstance(a, pd.Series) and not isinstance(b, pd.Series)
                and np.isscalar(a) and np.isscalar(b)):
            # ★ v2.11.31：先 `float()` 再 `np.float64()` ✓ —— 上面已确认 `np.isscalar` ✓，
            #   但类型上是宽联合（`generic|bool|int|float|complex|str|bytes|memoryview` ✓）
            #   ⇒ mypy 不认 ✗。**值不变** ✓（标量转 float 后再转 float64 与直接转等价 ✓）。
            # cast：运行时已由上面的 `np.isscalar` 确认是标量 ✓，这里只是告诉 mypy ✓
            _af, _bf = np.float64(cast(float, a)), np.float64(cast(float, b))
            with np.errstate(all="ignore"):
                if op in _BIN_ELEM:
                    _fn = _BIN_ELEM[op]
                    if _fn == "add":
                        return _af + _bf
                    if _fn == "sub":
                        return _af - _bf
                    if _fn == "mul":
                        return _af * _bf
                    if _fn == "div":
                        return _af / _bf
                    if _fn == "pow":
                        return _af ** _bf
                    if _fn == "max":
                        return max(_af, _bf)
                    if _fn == "min":
                        return min(_af, _bf)
                if op in _BIN_CMP:
                    return np.float64(1.0 if _NUM_UFUNC[_BIN_CMP[op]](_af, _bf) else 0.0)
        # ★ v2.11.27（P2-#20 清偿 ✓）：**两侧都不是 Series** 时 `template` 必须是 `None` ✗→✓ ——
        #   原来写成 `a if Series else b` ✗ ⇒ 两侧都是数组时 `template` 成了**数组** ✓，
        #   而 `_as_series` 会去取 `template.index` ⇒ `AttributeError` / `TypeError` ✗。
        #   ⇒ `None` 表示"没有 Series 上下文" ✓，`_as_series` 会给数组造 `RangeIndex` ✓（位置对齐 ✓）。
        template = (a if isinstance(a, pd.Series)
                    else (b if isinstance(b, pd.Series) else None))
        # 常量右操作数（如 Power(x, 2) 的指数 2）：单独留下标量形态。
        # 若被 _as_series 广播成整列数组，np.power 会走「数组指数」的逐元素通用 pow，
        # 丢掉 numpy/glibc 对**标量指数**的特化（实测 42000 行：0.899ms → 0.059ms，15×）。
        # 仅 b 是标量时 template 必为 a，故 _align 后 b 与 a 同 index，取标量无对齐损失。
        b_scalar = b if (np.isscalar(b) and not isinstance(b, (str, bytes))) else None
        a = _as_series(a, template)
        b = _as_series(b, template)
        a, b = _align(a, b)
        if op in _BIN_ELEM:
            fn = _BIN_ELEM[op]
            if fn in ("add", "sub", "mul", "div"):
                return getattr(a, fn)(b)
            if fn == "pow":
                return np.power(a, b if b_scalar is None else b_scalar)
            if fn == "max":
                return a.where(a >= b, b)
            if fn == "min":
                return a.where(a <= b, b)
        return getattr(a, _BIN_CMP[op])(b).astype(np.float64)
    if op in _UNARY:
        s = args[0]
        if not isinstance(s, pd.Series):
            raise ValueError(f"{op} 参数不能是常量")
        fn = _UNARY[op]
        if fn == "abs":
            return s.abs()
        if fn == "sqrt":
            return np.sqrt(s)
        if fn == "log":
            return np.log(s)
        if fn == "exp":
            # v1.20.34：EXP(X)=e^X —— 整块面板一次 `np.exp`（numpy C 实现 ✓），
            # 与 Sqrt/Log 同一路径 ⇒ **单因子测试不会因它变慢** ✓；
            # 溢出（输入过大 ⇒ inf）由前端/后端既有清洗（CleanInf）处理 ✓。
            with np.errstate(all="ignore"):
                return np.exp(s)
        if fn == "neg":
            return -s
        if fn == "sign":
            return np.sign(s)
    if op == "If":
        cond, a, b = args
        if isinstance(cond, (int, float)) or cond is None:
            cond = _as_series(cond, a if isinstance(a, pd.Series) else b)
        if not isinstance(a, pd.Series):
            a = _as_series(a, cond)
        if not isinstance(b, pd.Series):
            b = _as_series(b, cond)
        a, b = _align(a, b)
        return a.where(cond.astype(bool), b)
    if op == "ROUND":
        # ROUND(x, ndigits)：真实价按分取整（np.round 半进偶；对整分价格与 qlib
        # ops_ext.ROUND 一致——真实价 ≈ 分整数，无 0.5 边界）
        s = args[0]
        nd = int(args[1]) if len(args) > 1 and args[1] is not None else 0
        if not isinstance(s, pd.Series):
            raise ValueError("ROUND 首参不能是常量")
        return np.round(s, nd)
    if op == "And" or op == "Or":
        a, b = args
        tpl = a if isinstance(a, pd.Series) else b
        a = _as_series(a, tpl)
        b = _as_series(b, tpl)
        # numpy 直落：pandas 的 `(a != 0) & (b != 0)` 要过两次 Series 比较（各含一次
        # 包装）+ 一次 Series 级逻辑运算 + astype（实测 3.68ms → 2.33ms）。
        # ⚠ NaN 语义有**两处**不同来源，不能一概而论：
        #   · 原始 NaN 经 fillna(0) → 判 False；
        #   · _align 新引入的缺失行仍是 NaN，而 pandas 里 `NaN != 0` 为 **True**。
        # 故 fillna 必须在 _align **之前**。同 index（面板常态：字段都被 field() 对齐
        # 到同一 _full）时无缺失行，fillna(0) 可安全挪到 numpy 侧，省掉两次 pandas
        # fillna（约 2.9ms/次）；下标不同时严格按「先 fillna 再 align」的原顺序走。
        # （对拍实测：若一概用 numpy 的 `(v != 0) & ~isnan(v)`，会在 union 路径上
        #   产生 525/75 处差异。）
        if a.index is b.index or a.index.equals(b.index):
            idx = a.index
            av = a.to_numpy(dtype=np.float64)
            bv = b.to_numpy(dtype=np.float64)
            av = np.where(np.isnan(av), 0.0, av)
            bv = np.where(np.isnan(bv), 0.0, bv)
        else:
            a, b = _align(a.fillna(0.0), b.fillna(0.0))
            idx = a.index
            av = a.to_numpy(dtype=np.float64)
            bv = b.to_numpy(dtype=np.float64)
        ma = av != 0
        mb = bv != 0
        m = (ma & mb) if op == "And" else (ma | mb)
        return pd.Series(m.astype(np.float64), index=idx)
    if op in ("BARSLAST", "BARSSINCEN", "HHVBARS", "LLVBARS",
              "DYN_REF", "DYN_MIN", "DYN_MAX", "DYN_SUM", "DYN_COUNT", "DYN_MEAN",
              "DYN_HHVBARS", "DYN_LLVBARS"):
        # ⚠ 这里原本写的是 `self` ✗ —— 搬成模块级函数后它不再存在 ✓（ruff F821 当场抓到 ✓）
        #   ⇒ 显式传 `ev`（就是那个 PanelEvaluator 实例 ✓，动态窗口算子需要它取日历/价格 ✓）。
        return _panel_dyn(op, args, ev)
    raise ValueError(f"panel_expr 不支持算子 {op}")


class PanelEvaluator:
    # ★ v2.11.31：动态挂上的缓存要在**类上**声明类型 ✓ ——
    #   否则 mypy 报 `Need type annotation for "_recur_cache"` ✓（它是 `setattr` 出来的 ✓）。
    _recur_cache: "OrderedDict[tuple, Any]"
    """在一整块面板上求值；节点缓存消除公共子式重复计算。

    SR 上下文模型（对齐 qlib 2026-09-09 实证）：SR 语义 = 剔停牌(NaN)行后在
    "有效行压缩序列"上计算窗口/移位/算术，结果 scatter 回全日历（停牌日 NaN）。
    由于 _sr_wrap_expr 会把因子表达式的所有叶子字段包 SR，含 SR 的整棵表达式
    都在压缩序列上求值：eval 用 active（当前行集合）驱动——
      sr=True  → active = 各股 close 非 NaN 的有效行（剔停牌）；
      sr=False → active = 全日历（label/base/tag 等，停牌日保留前值语义）。
    压缩序列上 shift/rolling 天然按剔除后的连续行序，与 qlib 一致。
    """

    def __init__(self, instruments, start_time, end_time, union_fields=("close",),
                 read_start=None):
        self.instruments = list(instruments)
        self.start_time = start_time
        self.end_time = end_time
        # 读盘起点：默认=逻辑起点；传入 read_start（更早）时字段读取/滚动从前移起点
        # 开始（预热窗口），输出仍由 panel_features reindex 回 [start_time, end_time]。
        self.read_start = read_start or start_time
        self._union_fields = tuple(dict.fromkeys(union_fields))
        # v1.18.31：一次拿到 (并集 index, 各股行区间 layout, 请求日历区间) —— 字段加载
        # 由此改为「按位铺值」（`_load_field_on`），免掉每字段一次的 MultiIndex 构造 +
        # sort_index + reindex(_full)（实测 ~80ms/字段）
        (self._full, self._layout,
         self._req_lo, self._req_hi) = _union_layout(
            instruments, self.read_start, end_time, self._union_fields)
        self._active_cache: Dict[bool, pd.MultiIndex] = {}
        # v1.18.38：active 相对 _full 的**位置数组**（仅 sr=True 且确有剔除时有值）——
        # 供 `eval` 的 field 节点用「位置 gather」替代「标签 reindex」（见 eval 处注释）
        self._active_pos: Dict[bool, Optional[np.ndarray]] = {}
        self._field_cache: Dict[str, pd.Series] = {}
        self._node_cache: "OrderedDict[Tuple[object, bool], pd.Series]" = OrderedDict()
        self._node_bytes = 0  # 节点缓存已记账字节（配合 LRU 上限）

    def _active_index(self, sr: bool) -> pd.MultiIndex:
        """当前行集合：sr=True → close 有效行压缩序列；sr=False → 全日历。"""
        key = bool(sr)
        if key in self._active_cache:
            return self._active_cache[key]
        if not sr:
            self._active_cache[key] = self._full
            self._active_pos[key] = None
            return self._full
        close_full = self.field("$close")  # 全历（自身非 NaN=有效）
        nmask = close_full.notna()
        ma = nmask.to_numpy()
        if ma.all():
            # 全部有效：active 与 _full **取值相同** → 直接复用同一对象（下游只按值使用），
            # 好处是 `eval` 里可用 `is` 短路、且 `_seg_arrays` 缓存也能命中同一份
            self._active_cache[key] = self._full
            self._active_pos[key] = None
            return self._full
        active = close_full[nmask].index
        self._active_cache[key] = active
        self._active_pos[key] = np.flatnonzero(ma)
        return active

    # ---- 字段 ----
    def field(self, name: str) -> pd.Series:
        # 表达式里叶子是 $close 形态，bin 文件名是 close（去 $ 前缀）
        key = name.lstrip("$")
        if key == DATE_FIELD:
            # ★ 2026-10-15：`DATE`（公式字段）由面板 index **直接合成** ✓（零 IO ✓）。
            #   口径/换算与物化 bin（`tools/dump_date.py`）**同源**（`factors/calc_date.py` ✓）
            #   ⇒ 面板路径与 qlib 路径逐位一致 ✓（tests/test_date_field.py 守着 ✓）。
            s = self._field_cache.get(key)
            if not isinstance(s, pd.Series):
                _full = getattr(self, "_full", None)
                if _full is None:
                    raise ValueError("DATE 字段需要面板已就绪（缺少 _full）")
                try:
                    dt = _full.get_level_values("datetime")
                except (KeyError, AttributeError) as e:
                    raise ValueError("DATE 字段要求面板 index 含 datetime 层级：%r" % (e,))
                s = pd.Series(serial_array(dt), index=_full)
                self._field_cache[key] = s
            return s
        s = self._field_cache.get(key)
        if s is None and key.startswith("chip_"):
            # ★ v1.20.17：**物化 bin 优先**（用户要求「COST(36) 这种没物化的就走计算」✓）
            s = self._chip_or_bin(key)
            self._field_cache[key] = s
            # ⚠ v1.20.25：缺物化文件时**不报错、静默全 NaN** ✗ ⇒ 主动警告一次 ✓
            _warn_if_materialized_missing(key, s)
            return s
        if not isinstance(s, pd.Series):
            # v1.18.31：直接按位铺到 _full（等价于原 `load_field_series(...).reindex(_full)`，
            # 见 _load_field_on）。所有字段仍统一对齐到全池全日历基准（缺失股/缺失行补 NaN），
            # 保证跨字段 Series 天然同 index（_align 恒等路径，避免 union 类型问题）。
            vals = _load_field_on(self._full, self._layout,
                                  self._req_lo, self._req_hi, key)
            s = pd.Series(vals, index=self._full)
            self._field_cache[key] = s
            # ⚠ v1.20.25：`pre*`（前复权）缺物化时同样**静默失效** ⇒ 一并警告 ✓
            _warn_if_materialized_missing(key, s)
        return s

    def _chip_or_bin(self, key: str) -> pd.Series:
        """`$chip_*` 取数（v1.20.17）：**已物化的直读 bin** ✓ / **未物化的现算** ✓。

        为什么必须分开（用户 2026-09-18 提问"cost 函数那么慢？bin 都物化了"）：
          · 筹码是**状态递推**字段（128 箱 × 全池 × 全历史 ⇒ `chip_dist` 自述"8 亿次元素
            运算 ⇒ 几十秒"✗），而 close/high/low 是**逐点函数**（O(1) ✓）；
          · `chip_store.materialize`（v1.19.85 ✓）已把 7 个常用字段落成 `.day.bin`
            （`chip_cost_{5,30,75,95}` / `chip_win_{close,high,low}` ✓ 实测各 6075 份 ✓）
            ⇒ 这些**直读即可**，开销与 `$close` 同量级（实测各 1.0 ms ✓）；
          · 但此前 `field()` 对**所有** `$chip_*` 一律走 `_chip_field` **现算** ✗ ⇒ 物化白做了 ✗。
        判据用**静态清单**（`chip_store.DEFAULT_FIELDS` ✓）**零 IO** ✓；清单外的档位
        （如 `COST(36)` ⇒ `$chip_cost_36` ✓）**按用户要求退回现算** ✓（结果同样正确 ✓，
        只是慢 ⇒ 想快就把该档位加进 `chip_store.DEFAULT_FIELDS` 再物化一次 ✓）。
        """
        # ★ v2.11.31：用 **list** 承接 ✓ —— 原来 `_MAT` 直接是 import 来的 tuple ✓，
        #   而 except 分支赋 `()` 时 mypy 报 "Name already defined by an import / 类型不兼容" ✗。
        _MAT: list[str]
        try:
            from .chip_store import DEFAULT_FIELDS as _M0
            _MAT = list(_M0)
        except Exception as e:  # noqa: BLE001
            _log_swallowed("panel_expr.py", e)
            _MAT = []
        if key in _MAT:
            # ⚠ 轻量构造（单测里 `PanelEvaluator.__new__` 不走 `__init__` ✓）没有 `_full`
            #   ⇒ 视为"面板未就绪"，退回现算 ✓（生产路径恒有 ✓，行为不变 ✓）。
            _full = getattr(self, "_full", None)
            if _full is not None:
                vals = _load_field_on(_full, self._layout,
                                      self._req_lo, self._req_hi, key)
                return pd.Series(vals, index=_full)
        return self._chip_field(key)

    def _chip_field(self, key: str) -> pd.Series:
        """筹码分布**派生字段**（v1.19.83）：`chip_cost_<q>`（= `COST(q)`）/ `chip_win_<close|high|low>`
        （= `WINNER(现价/最高/最低)`）。

        **为什么做成派生字段、而不是普通算子（设计约束，勿轻易改）**：
          `panel_expr` 与 qlib 的算子都按**逐股**调用（`_by_group` / qlib 的 per-instrument 求值）
          ⇒ 把筹码递推挂成算子就等于逐股 Python 递推 ⇒ 全 A 实测约 **4~8 分钟 / 每个分位** ✗✗
          （单股 2500 天 × 128 箱，纯 Python 循环）；而 `field()` 能拿到**整块面板** ⇒
          在这里按「日期 × 股票」矩阵一次递推（`chip_dist.chip_run`）= **几秒** ✓✓，
          且按字段名缓存 ⇒ 公式里出现多次也只算一次。

        名称约定（codegen 生成，两者必须一致）：`$chip_cost_95`、`$chip_win_close`。
        口径见 `chip_dist` 模块文档：后复权价 + `$turn`（单位自适应）+ 三角分布注入。
        """
        from .chip_dist import chip_run

        body = key[len("chip_"):]
        head, _, tail = body.partition("_")
        c = self.field("$close")
        h = self.field("$high")
        l = self.field("$low")
        # ---- 换手率（v1.19.92：抽成公共函数 `chip_turn_of`，**物化侧复用同一口径** ✓）----
        #   量纲/自校准/真 turn 优先的说明随函数一起搬走，见 `chip_turn_of` 的 docstring ✓
        t = chip_turn_of(self)
        names = list(c.index.names)
        li = names.index("instrument") if "instrument" in names else 0
        ld = 1 - li
        # 面板 → (日期 × 股票) 矩阵：筹码递推必须按日推进，且一次算全池（向量化）
        try:
            C = c.unstack(level=li)
            H = h.unstack(level=li)
            L = l.unstack(level=li)
            T = t.unstack(level=li)
        except Exception as e:                        # noqa: BLE001
            raise ValueError("筹码分布字段要求面板 index 为 (instrument, datetime) 两级：%r" % (e,))
        cn, hn, ln, tn = (x.to_numpy(dtype=float) for x in (C, H, L, T))
        if head == "cost":
            try:
                q = float(tail)
            except ValueError:
                raise ValueError("无法解析筹码成本分位：%s（应为 $chip_cost_95 形态）" % key) from None
            vals = chip_run(cn, hn, ln, tn, qs=(q,))["cost_%g" % q]
        elif head == "win":
            if tail not in ("close", "high", "low"):
                raise ValueError("WINNER 只支持 现价/最高价/最低价，收到：%s" % key)
            px = {"close": cn, "high": hn, "low": ln}[tail]
            vals = chip_run(cn, hn, ln, tn, prices=px)["winner"]
        else:
            raise ValueError("未知的筹码派生字段：%s" % key)
        wide = pd.DataFrame(vals, index=C.index, columns=C.columns)
        st = wide.stack()                              # (日期, 股票) → 标签对齐回面板顺序
        st.index = st.index.set_names([names[ld], names[li]])
        return st.reorder_levels(names).reindex(c.index)

    # ---- 递推组（v2.4.0，用户 2026-10-15 定稿 ✓；内核在 factors/recursion.py ✓）----
    def _recur_out(self, node, sr: bool, active) -> pd.Series:
        """`RecurOut(j, N, body_0…body_{N-1})` ⇒ 算整组、返回成员 j 的当前值 ✓。

        ⚠ 缓存键必须**只含整组定义**（不含 j ✗）：同一条公式里 `RecurOut(0,…)` 与
        `RecurOut(1,…)` 是同一个组 ⇒ 不这样就会把整组算两遍（实测趋势线那种 3 成员要 ×3 ✗）。
        """
        j = int(node.args[0].args[0])
        n = int(node.args[1].args[0])
        members = node.args[2:]
        # ★ v2.11.31：注解写成 `... | None` ✓ —— 这样 `if cache is None` 之后
        #   mypy 能收窄成 `OrderedDict[...]` ✓，`cache[key] = got` 才合法 ✓
        #   （只写 `OrderedDict[...]` 会在赋值处报"Any|None 不兼容"✗）。
        cache: "OrderedDict[tuple, Any] | None" = getattr(self, "_recur_cache", None)
        if cache is None:
            cache = self._recur_cache = OrderedDict()
        key = (bool(sr), n, tuple(m.key for m in members))
        got = cache.get(key)
        if got is None:
            pre: list = []
            skels = [_recur_split(self, m, sr, pre) for m in members]
            got = self._recur_group(skels, n, pre, sr, active)
            cache[key] = got
            while len(cache) > 3:                      # 组结果很大（(日期×股票) 矩阵）⇒ 只留最近几组 ✓
                cache.popitem(last=False)
        return got[j]

    def _recur_group(self, skels, n_members: int, pre: list, sr: bool, active):
        """按"日期 × 股票"矩阵一次 O(N) 推进整组，返回每个成员的 Series（面板顺序 ✓）。

        做法与筹码派生字段同构（`_chip_field` ✓）：面板是 (股票, 日期) 两级 index，递归必须
        **按日推进**且一次算全池 ⇒ `unstack` 成 (日期 × 股票) 后逐根推进 ✓。
        ⚠ 停牌/未上市的行（`$close` 为 NaN）⇒ 状态**冻结**、输出 NaN ✓（= 益盟"无停牌行"口径 ✓）。
        """
        from .recursion import run_group

        idx = pre[0].index if pre else active
        names = list(idx.names)
        li = names.index("instrument") if "instrument" in names else 0
        ld = 1 - li
        close = self.field("$close")
        ref = close.unstack(level=li)                  # (日期 × 股票)：日期轴/股票轴基准 ✓
        dates, codes = ref.index, ref.columns
        pre_mats = []
        for s in pre:
            w = s.unstack(level=li)
            if not (w.index.equals(dates) and w.columns.equals(codes)):
                w = w.reindex(index=dates, columns=codes)   # 停牌日/缺股 ⇒ NaN ✓（该行随后被冻结 ✓）
            pre_mats.append(w.to_numpy(dtype=np.float64))
        valid = ~np.isnan(ref.to_numpy(dtype=np.float64))
        vals = run_group(skels, n_members, pre_mats, valid)
        mi = pd.MultiIndex.from_product([dates, codes], names=[names[ld], names[li]])
        out = []
        for m in vals:
            s = pd.Series(m.ravel(), index=mi).reorder_levels(names).reindex(idx)
            out.append(s)
        return out

    # ---- 节点 ----
    def eval(self, node: Node, sr: bool = False) -> pd.Series:
        """在 active 行集上求值一个节点。

        sr=True（表达式含 SR）：active = 各股 close 有效行（剔停牌），所有窗口/
        移位/算术在压缩序列上进行（与 qlib SR 删行语义一致，无 NaN 停牌行干扰）；
        sr=False（label/base/tag）：active = 全日历，保留停牌 NaN 行（普通 shift
        在停牌日给前值，与 qlib 非 SR 字段一致）。
        """
        # v1.18.4 性能：key 用 parse 时预计算的"结构规范 id"（O(1) 属性读取），取代
        # reconstruct(node)（每次 eval 递归重建子树字符串）。结构相同的子树共享同
        # 一个 key → 缓存命中率与原实现一致，数值逐位不变。
        key = (node.key, sr)
        hit = self._node_cache.get(key)
        if hit is not None:
            self._node_cache.move_to_end(key)  # LRU 刷新（命中过的节点更晚被逐出）
            return hit

        active = self._active_index(sr)
        op = node.op
        if op == "field":
            f = self.field(node.args[0])
            # v1.18.38 性能（本轮特征加载段最大单项，全 A 3 因子口径实测 151 次 / 73.5s ≈ 19%）：
            # `field()` 自 v1.18.31 起**已按 _full 对齐**，所以这里的 reindex 有两种情形：
            #   · sr=False（label/base/tag）：active **就是** _full 同一个对象 ⇒ reindex 是
            #     **恒等操作**，却仍要跑一次全表 `MultiIndex.get_indexer`（81 万行 ≈33ms/次）
            #     ⇒ 用 `is` 同对象短路（做法与 `_align` 一致）。
            #   · sr=True：active 是 close 有效行压缩序列，确需取子集；但它是 `_full` 的
            #     **布尔子集**（同序），故「按位置 gather」与「按标签 reindex」取值等价
            #     （float64 逐位相同、index 同一对象），而 gather 只要 ~2ms（省 ~30ms/次）。
            if active is self._full:
                out = f
            else:
                pos = self._active_pos.get(sr)
                if pos is None:
                    out = f.reindex(active)
                else:
                    out = pd.Series(f.to_numpy(dtype=np.float64)[pos], index=active)
        elif op == "const":
            return float(node.args[0])
        elif op == "SR":
            # SR 表达式：子表达式已在 sr=True 的压缩行集上求值，透传即可
            # （active 已按 close 有效行剔除停牌，无需再 where 抹 NaN）。
            inner = self.eval(node.args[0], sr=True)
            if inner is None:
                raise ValueError("SR 内层不能是常量")
            out = inner
        elif op == _RECUR_OP:
            # ★★ v2.4.0：递推组（用户 2026-10-15 定稿 ✓）—— **必须在通用参数求值之前**处理 ✗：
            #   组内成员体里含 `RecurPrev(j,k)`，它单独求值没有意义（"上一根"要整组一起推 ✓）
            #   ⇒ 这里直接接手整组：按 (日期×股票) 矩阵一次 O(N) 推进，取成员 j ✓。
            return self._recur_out(node, sr, active)
        elif op in (_RECUR_PREV_OP, _RECUR_CUR_OP):
            raise ValueError(
                "%s 只能出现在递推组内部 ✗（它由 `REF(变量,k)` / 组内同根引用自动编译而来）—— "
                "请在公式里写 `REF(变量,1)`（k≥1 的常量）✓" % op)
        else:
            args = [self.eval(a, sr=sr) for a in node.args]
            out = self._apply(op, args, sr=sr, active=active)
            # ⚠ v2.10.69（技术债 #17 ② ✗→✓）：**只缓存 Series** ✓ ——
            #   常量子树（`MAX(1,2)` ✓）的结果是 `float` ✓，而 `_series_mem_bytes(out)`
            #   会去读 `out.index` ✗ ⇒ `AttributeError: 'float' object has no attribute 'index'`
            #   ⇒ 整条公式崩 ✓（`$close*(1+2)` 正常、`MAX(1,2)+$close` 崩 ✗ —— 只因标量在左边 ✓）。
            #   ⇒ 标量不缓存（本来就便宜 ✓），也不计字节 ✓。
            if isinstance(out, pd.Series):
                self._node_cache[key] = out
                self._node_bytes += _series_mem_bytes(out)
            # LRU 逐出：记账字节超上限时从最旧开始淘汰，封顶单 worker 节点缓存
            while self._node_bytes > _NODE_CACHE_MAX_BYTES and self._node_cache:
                _k, _v = self._node_cache.popitem(last=False)
                self._node_bytes -= _series_mem_bytes(_v)
        return out

    def _apply(self, op: str, args, sr: bool, active=None):
        """委托到模块级 `_apply_node` ✓（★ 2026-10-19 拆分 ✓；测试有 3 处直接调它 ✓）。"""
        return _apply_node(op, args, sr, active, ev=self)

    def eval_expr(self, expr: str) -> pd.Series:
        # 内存（v1.18.1）：节点缓存按"单个表达式"为界清理——多公式共用同一 evaluator
        # 时，若中间节点缓存跨公式累积（N 公式 × 全树节点同时驻留），每块峰值随勾选
        # 公式数线性放大（×12 worker 后家里小内存会爆）。同一表达式内部的公共子式
        # 缓存收益全部保留；跨表达式的完全重复子式极罕见（字段读取走 _field_cache /
        # 读盘 LRU，不受影响）。清空对数值零影响，只改缓存生命周期。
        self._node_cache.clear()
        self._node_bytes = 0
        # ★ v2.4.0：递推组缓存同生命周期（整组结果是 (日期×股票) 量级的内存 ⇒ 不能跨公式累积 ✗）
        if getattr(self, "_recur_cache", None):
            self._recur_cache.clear()
        _SEG_CACHE.clear()          # 段边界缓存与节点缓存同生命周期（v1.18.22）
        _SEG_RC_CACHE.clear()       # 段→矩阵布局缓存（v1.18.30，同上）
        node = parse_expr(expr)
        # SR 语义：single_test 在 suspend_remove=True 时用 _sr_wrap_expr 把因子表达式
        # 的所有叶子字段包上 SR(...)。含 SR 的表达式整棵在"close 有效行压缩序列"上
        # 求值（剔除停牌行后滚动/移位/算术，与 qlib 删行语义一致），出口把结果
        # scatter 回全日历（停牌日 NaN——实证 Mean(SR($close),3) 停牌日=NaN）。
        # label/base/tag 等不含 SR 的字段组表达式在全日历上普通求值（停牌日保留
        # 前值，实证 Ref($close,1) 停牌日=前收 0.977708 非 NaN）。
        sr = "SR(" in expr
        s = self.eval(node, sr=sr)
        if sr and s is not None and isinstance(s, pd.Series) and not s.empty:
            # 压缩序列 → 回填全日历（有效行位置保持原值，停牌日 NaN）
            full = self._full
            if len(s) != len(full):
                s = s.reindex(full)
        return s


# ===========================================================================
# 窗口 / 移位 / 动态算子 的"面板 + SR"实现
# ===========================================================================


def _ref(s: pd.Series, k: int, sr: bool) -> pd.Series:
    """Ref(X, k)：k>0 过去、k<0 未来。sr=True 时按有效行（非 NaN）shift。

    性能（v1.18.30）：原 `s.groupby(level=0, group_keys=False).shift(k)` 实测 354794 行 /
    300 段要 **22.9ms**，而"逐段纯 numpy 搬值"只要 **1.6ms（14×）** —— shift 不出算术、
    只搬值，两者**逐位相同**（实测对拍通过）。大头同在 groupby（MultiIndex level 取组键）。
    """
    idx = s.index
    if len(s) == 0:
        return pd.Series(np.nan, index=idx, dtype=np.float64)
    if sr:
        valid = s[s.notna()]
        if valid.empty:
            return pd.Series(np.nan, index=s.index, dtype=np.float64)
        core = _shift_seg(valid.to_numpy(dtype=np.float64), valid.index, k)
        out = pd.Series(np.nan, index=s.index, dtype=np.float64)
        out.loc[valid.index] = core
        return out
    return pd.Series(_shift_seg(s.to_numpy(dtype=np.float64), idx, k), index=idx)


def _roll(s: pd.Series, func: str, N: int, sr: bool) -> pd.Series:
    """滚动算子；sr=True 剔除 NaN 行滚动后回填全日历（停牌日 NaN）。

    性能（v1.18.30）：原 `s.groupby(level=0, sort=False).rolling(N, min_periods=1)` 实测
    354794 行 / 300 段 **55~66ms/次**，其中 **81% 是 groupby 本身**（取 MultiIndex level=0
    作组键要物化 35 万个 object 字符串：`groupby(level=0).size()` 就要 18.5ms，而全表
    `pd.Series(arr).rolling(40).mean()` 只需 11ms）。改走 `_seg_roll`（段作列矩阵 + 一次
    DataFrame.rolling）→ **22~29ms/次（2.1~2.6×）**，且逐位相同。
    """
    idx = s.index
    if len(s) == 0:
        return pd.Series(np.nan, index=idx, dtype=np.float64)
    if sr:
        valid = s[s.notna()]
        if valid.empty:
            return pd.Series(np.nan, index=s.index, dtype=np.float64)
        core = _seg_roll(valid.to_numpy(dtype=np.float64), N, func, valid.index)
        out = pd.Series(np.nan, index=s.index, dtype=np.float64)
        out.loc[valid.index] = core
        return out
    return pd.Series(_seg_roll(s.to_numpy(dtype=np.float64), N, func, idx), index=idx)


def _ema_apply_series(x: pd.Series, N, tdx: bool) -> pd.Series:
    """单股 EWM（对齐 qlib ops.EMA / ops_ext.EMA_TDX 的逐股实现）。

    tdx=False（qlib 内建 EMA）：N==0 → expanding 指数加权；0<N<1 → ewm(alpha=N,
      min_periods=1)；N>=1 → ewm(span=N, min_periods=1)。均 adjust=True（pandas 默认）。
    tdx=True（EMA_TDX 通达信递归式）：N==0 → expanding 均值；0<N<1 → ewm(alpha=N,
      adjust=False)；N>=1 → ewm(alpha=2/(N+1), adjust=False)。
    """
    n = float(N)
    if tdx:
        if n == 0:
            return x.expanding(min_periods=1).mean()
        if 0 < n < 1:
            return x.ewm(alpha=n, min_periods=1, adjust=False).mean()
        return x.ewm(alpha=2.0 / (n + 1), min_periods=1, adjust=False).mean()
    # qlib 内建 EMA
    if n == 0:
        arr = x.to_numpy(dtype=np.float64)
        out = np.full(len(arr), np.nan, dtype=np.float64)
        for i in range(len(arr)):
            win = arr[: i + 1]
            valid = win[~np.isnan(win)]
            if valid.size == 0:
                continue
            a = 1 - 2 / (1 + len(valid))
            w = a ** np.arange(len(valid))[::-1]
            out[i] = np.nansum(w * valid) / w.sum()
        return pd.Series(out, index=x.index)
    if 0 < n < 1:
        return x.ewm(alpha=n, min_periods=1).mean()
    return x.ewm(span=n, min_periods=1).mean()


def _sma_apply_series(x: pd.Series, N, M) -> pd.Series:
    """单股通达信 SMA：Y_t = (M·X_t + (N−M)·Y_{t−1}) / N → ewm(alpha=M/N, adjust=False)。

    与 ops_ext.SMA 逐位一致：ewm(alpha=M/N, adjust=False, min_periods=1)，起点敏感
    （从序列首值起递归）。N/M 为常量（codegen 已校验）。M 越接近 N 越贴近原值。
    """
    n = max(1, int(N))
    m = max(0, min(int(M), n))
    if n == 0 or m == 0:
        return x * np.nan
    return x.ewm(alpha=m / n, adjust=False, min_periods=1).mean()


def _ema(s: pd.Series, N, tdx: bool, sr: bool) -> pd.Series:
    """EMA(X, N) / EMA_TDX(X, N) 面板实现。

    语义与逐股一致（EMA 是每股独立指数加权）：
    - sr=False：s 是全日历（含停牌 NaN 行）。qlib 内建 EMA 直接用 ewm（pandas 遇 NaN
      保持 NaN 传播）——与 qlib 在含 NaN 全日历上逐股 ewm 一致。这里按组逐段调用
      _ema_apply_series（各股自己的连续段；段内含 NaN 由 ewm 处理，与 qlib 相同）。
    - sr=True：active 已是 close 有效行压缩序列（无停牌 NaN），逐段 EWM 后
      scatter 回全日历由 eval_expr 出口统一处理（此处直接按组返回即可）。
    """
    return _by_group(s, None, lambda v: _ema_apply_series(pd.Series(v), N, tdx).to_numpy(dtype=np.float64))


def _sma(s: pd.Series, N, M, sr: bool) -> pd.Series:
    """SMA(X, N, M) 面板实现（逐股 ewm alpha=M/N adjust=False；语义同 _ema 的分组处理）。"""
    return _by_group(s, None, lambda v: _sma_apply_series(pd.Series(v), N, M).to_numpy(dtype=np.float64))


def _panel_dyn(op: str, args, ev: PanelEvaluator) -> pd.Series:
    """BARSLAST/DYN_* 面板实现：逐组调用 ops_ext 的纯向量函数（组数~5000，内部 numpy）。"""
    from . import ops_ext

    if op == "BARSLAST":
        s = args[0]
        if not isinstance(s, pd.Series):
            raise ValueError("BARSLAST 参数不能是常量")
        return _by_group(s, None, lambda v: ops_ext.barslast_vec(v),
                         lambda v, w, ss, se: ops_ext.barslast_vec_seg(v, w, ss, se))
    if op == "BARSSINCEN":
        s, N = args[0], int(args[1])
        if not isinstance(s, pd.Series):
            raise ValueError("BARSSINCEN 首参不能是常量")
        return _by_group(s, None, lambda v: ops_ext.barsincen_vec(v, N),
                         lambda v, w, ss, se: ops_ext.barsincen_vec_seg(v, N, ss, se))
    if op in ("HHVBARS", "LLVBARS"):
        s, N = args[0], int(args[1])
        if not isinstance(s, pd.Series):
            raise ValueError(f"{op} 首参不能是常量")
        is_max = op == "HHVBARS"
        _seg = ops_ext.hhvbars_seg if is_max else ops_ext.llvbars_seg
        return _by_group(s, None,
                         lambda v: ops_ext.HHVBARS._bars(v, N, is_max=is_max),
                         lambda v, w, ss, se, _f=_seg: _f(v, N, ss, se))
    s, ns = args
    if not isinstance(s, pd.Series) or not isinstance(ns, pd.Series):
        raise ValueError(f"{op} 参数须为序列")
    kind = {"DYN_REF": "ref", "DYN_MIN": "min", "DYN_MAX": "max",
            "DYN_SUM": "sum", "DYN_COUNT": "count", "DYN_MEAN": "mean",
            "DYN_HHVBARS": "hhvbars", "DYN_LLVBARS": "llvbars"}[op]
    return _by_group(s, ns, lambda v, w: _dyn_kernel(kind, v, w, ops_ext),
                     lambda v, w, ss, se: _dyn_kernel_seg(kind, v, w, ss, se, ops_ext))


def _dyn_kernel(kind, vals, nvals, ops_ext):
    if kind == "ref":
        return ops_ext.dyn_ref_vec(vals, nvals)
    if kind == "min":
        return ops_ext._dyn_rmq_vec(vals, nvals, np.fmin)
    if kind == "max":
        return ops_ext._dyn_rmq_vec(vals, nvals, np.fmax)
    if kind == "sum":
        return ops_ext.dyn_window_sum_vec(vals, nvals)
    if kind == "count":
        return ops_ext.dyn_window_count_vec(vals, nvals)
    if kind == "mean":                      # ★ v1.20.55：MA/MEAN 的变量周期 ✓
        return ops_ext.dyn_window_mean_vec(vals, nvals)
    if kind == "hhvbars":
        return ops_ext.dyn_bars_vec(vals, nvals, True)
    if kind == "llvbars":
        return ops_ext.dyn_bars_vec(vals, nvals, False)
    raise ValueError(kind)


def _dyn_kernel_seg(kind, vals, nvals, seg_start, seg_end, ops_ext):
    """DYN_* 段感知内核分派（一次处理全部段；与 `_dyn_kernel` 逐位一致，见 v1.18.4）。

    sum/count 返回 None 表示"无段感知实现"，由 `_by_group` 回退逐段循环：它们内部是
    `cumsum` 前缀和相减，全局前缀和的浮点累加顺序与逐段前缀和不同 → 结果差 1 ULP
    （实测 dyn_sum 0.9 vs 0.9000000000000004），无法位一致；而这两个内核本身很轻
    （~8 次 numpy 调用），放弃段感知的收益损失可接受。
    """
    if kind == "ref":
        return ops_ext.dyn_ref_vec_seg(vals, nvals, seg_start, seg_end)
    if kind == "min":
        return ops_ext._dyn_rmq_vec_seg(vals, nvals, np.fmin, seg_start)
    if kind == "max":
        return ops_ext._dyn_rmq_vec_seg(vals, nvals, np.fmax, seg_start)
    if kind in ("sum", "count", "mean"):
        # ★ v1.20.55：mean 同 sum/count —— 内部都是 cumsum 前缀和相减 ⇒ 全局前缀和的浮点累加顺序
        #   与逐段前缀和不同（差 1 ULP ✗）⇒ 返回 None 让 `_by_group` 回退逐段循环 ✓（与既有取舍一致 ✓）
        return None
    if kind == "hhvbars":
        return ops_ext.dyn_bars_vec_seg(vals, nvals, True, seg_start)
    if kind == "llvbars":
        return ops_ext.dyn_bars_vec_seg(vals, nvals, False, seg_start)
    raise ValueError(kind)


def _bars_count_seg(v: np.ndarray) -> np.ndarray:
    """BARSCOUNT 单段内核：自数据起点累计非 NaN 交易日数（与 qlib notna().cumsum() 一致）。"""
    v = np.asarray(v, dtype=float)
    return np.cumsum(~np.isnan(v)).astype(np.float64)


# ---- Alpha158 窗口算子（逐股复刻 qlib Rolling 语义：rolling(N, min_periods=1)，
# 直接复用同一 pandas / qlib _libs 内核，保证逐位一致）----







def _shift_seg(arr: np.ndarray, idx, k: int) -> np.ndarray:
    """段内移位（= `groupby(level=0).shift(k)`，k>0 取过去、k<0 取未来）。

    shift 只是搬值、**不含任何算术** → 与 pandas 的 groupby-shift **逐位相同**（实测对拍
    通过），但 22.9ms → 1.6ms（14×），因为省掉了 groupby 的组键物化。
    """
    n = arr.shape[0]
    out = np.full(n, np.nan, dtype=np.float64)
    if n == 0:
        return out
    k = int(k)
    if k == 0:
        out[:] = arr
        return out
    _bnd, ss, se = _seg_arrays(idx, n)          # se 为「不含」端点
    i = np.arange(n, dtype=np.int64)
    j = i - k
    # 仅当源位置仍落在**同一段**内才搬（跨段即补 NaN，与 shift 逐段语义一致）
    ok = (j >= ss) & (j < se)
    out[i[ok]] = arr[j[ok]]
    return out












def _pair_seg_corr(va: np.ndarray, vb: np.ndarray, N: int) -> np.ndarray:
    """单股段相关：rolling/expanding corr(min_periods=1) + qlib Corr 的 std≈0 置 NaN。"""
    sa = pd.Series(np.asarray(va, dtype=np.float64))
    sb = pd.Series(np.asarray(vb, dtype=np.float64))
    if N <= 0:
        res = sa.expanding(min_periods=1).corr(sb)
        std_a = sa.expanding(min_periods=1).std().to_numpy(dtype=np.float64)
        std_b = sb.expanding(min_periods=1).std().to_numpy(dtype=np.float64)
    else:
        res = sa.rolling(int(N), min_periods=1).corr(sb)
        std_a = sa.rolling(int(N), min_periods=1).std().to_numpy(dtype=np.float64)
        std_b = sb.rolling(int(N), min_periods=1).std().to_numpy(dtype=np.float64)
    out = res.to_numpy(dtype=np.float64)
    out[np.isclose(std_a, 0, atol=2e-05) | np.isclose(std_b, 0, atol=2e-05)] = np.nan
    return out


def _corr_pair_panel(a: pd.Series, b: pd.Series, N: int) -> pd.Series:
    """Corr(X, Y, N) 面板实现：按 instrument 分段逐段算（两序列须同 index 对齐）。"""
    if len(a) == 0:
        return a
    idx = a.index
    arr_a = a.to_numpy(dtype=np.float64)
    arr_b = b.to_numpy(dtype=np.float64)
    n = len(a)
    bnd = _segment_bnd(idx)
    out = np.empty(n, dtype=np.float64)
    for gi in range(len(bnd) - 1):
        s = bnd[gi]
        e = bnd[gi + 1]
        out[s:e] = _pair_seg_corr(arr_a[s:e], arr_b[s:e], N)
    return pd.Series(out, index=idx)


def _node_has_recur(node) -> bool:
    """该子树里有没有组内引用（`RecurPrev` / `RecurCur`）⇒ 不能预计算、必须留在骨架里 ✓。"""
    if getattr(node, "op", None) in (_RECUR_PREV_OP, _RECUR_CUR_OP):
        return True
    return any(_node_has_recur(a) for a in node.args if isinstance(a, Node))


def _recur_split(ev: "PanelEvaluator", node, sr: bool, pre: list):
    """把成员表达式拆成"**骨架 + 预计算子树**"（v2.4.0 ✓，格式见 `factors/recursion.py`）。

    为什么不能整棵逐根算 ✗：成员体里大多数子树（字段 / MA / 比较…）与"上一根的自己"无关
    ⇒ 用现成的面板算子**整条一次算完**（`("a", k)` 叶子 ✓），逐根循环里只留少量标量骨架 ✓
    —— 否则全 A 6000 股 × 6000 根 × 每次重算 MA 会慢到不可用 ✗（与筹码派生字段同一个道理 ✓）。
    """
    if node.op == _RECUR_PREV_OP:
        return ("p", int(node.args[0].args[0]), int(node.args[1].args[0]))
    if node.op == _RECUR_CUR_OP:
        return ("m", int(node.args[0].args[0]))
    if node.op == "const":
        return ("c", float(node.args[0]))
    if not _node_has_recur(node):
        s = ev.eval(node, sr=sr)
        if not isinstance(s, pd.Series):
            return ("c", float(s))
        pre.append(s)
        return ("a", len(pre) - 1)
    return (node.op, [_recur_split(ev, a, sr, pre) for a in node.args])




def _warm_days(exprs) -> int:
    """窗口预热天数：表达式用到的最大前向历史。

    复刻 qlib 语义——Rolling(N)/Ref(±k) 需读 start 之前 N/k 个交易日数据使
    区间首日窗口完整；SR（停牌删行）另需 LOOKBACK_DAYS(250) 前扩覆盖停牌段。
    保守取 max(所有窗口常量, SR lookback) + 30 天余量。
    """
    max_k = 0
    for expr in exprs:
        e = str(expr)
        # 固定窗口算子 + Ref/EMA 的第二参（窗口/前移天数）；HHVBARS/LLVBARS 亦固定窗口；
        # Alpha158 窗口类（IdxMax/IdxMin/Rank/Slope/Rsquare/Resi 第二参、Quantile 第二参、
        # Corr 第三参）同样前移 N-1。
        for m in re.finditer(
            r"(?:Ref|Mean|Max|Min|Sum|Std|Var|Abs|Sqrt|EMA|EMA_TDX|HHVBARS|LLVBARS|"
            r"IdxMax|IdxMin|Rank|Slope|Rsquare|Resi|Quantile)\([^,]+,\s*(-?\d+)"
            r"|Corr\([^,]+,[^,]+,\s*(-?\d+)",
            e,
        ):
            try:
                for g in m.groups():
                    if g is not None:
                        max_k = max(max_k, abs(int(g)))
            except ValueError as _e:
                _log_swallowed('panel_expr.py', _e)
                pass
        if "SR(" in e:
            max_k = max(max_k, 250)
    return max_k + 30


# 需"精确冷启动起点"的递归算子：qlib 逐字段按 get_extended_window_size 精确前移
# N-1 天起算（EMA 是序列起点敏感的指数递归，读多了反而不对齐——固定窗口算子多读
# 无害，EMA 必须恰好 N-1）。这里只把这些算子的窗口参数也纳入 warm，供逐字段分组。
_EXT_SENSITIVE_OPS = ("EMA", "EMA_TDX")


def _tree_ext_days(node) -> int:
    """递归计算表达式的 qlib extended window（左侧交易日数）。

    复刻 qlib Expression.get_extended_window_size 的递归语义：
      - Rolling 类（Mean/Max/Min/Sum/Std/Var...）与 EMA/EMA_TDX：N>=1 → 子ext + (N-1)；
        N==0 → 子ext；0<N<1 → 子ext + log(1e-6)/log(1-N)（与 qlib 同式）。
      - Ref(x, N)：qlib 对左侧扩展 max(child_ext + N, child_ext)，即正 N（取过去）才加 N。
      - 其余（二元/If/And/Or/一元/逻辑）：两侧子 ext 取 max。
    叶子/常量 → 0。

    为什么必须递归整棵树：EMA 是"序列起点敏感"的指数递归，面板若只按最外层 EMA 的
    N-1 前移 read_start，而输入链里还嵌了 Max/Min/Ref 等固定窗口算子，qlib 会让它们
    也从各自 extended 起点起算（嵌套窗口沿树累加，如 EMA(Max($h,34),4) 总 extended
    = 33+3=36），面板起点晚 33 天 → EMA 整条序列永久偏移（实测同一日值差可达 0.37，
    0/1 阈值比较即翻面）。此递归把整棵树需要的最大前移量算准。
    """
    op = node.op
    if op in ("field", "const"):
        return 0
    args = node.args or []
    if op == "Corr":
        # qlib PairRolling：扩展 = max(左右子 ext) + N-1
        base = max(_tree_ext_days(a) for a in (args[0], args[1]) if hasattr(a, "op"))
        try:
            n = float(args[2].raw) if len(args) > 2 and getattr(args[2], "op", None) == "const" else 0.0
        except Exception as e:
            _log_swallowed("panel_expr.py", e)
            n = 0.0
        if n >= 1:
            return base + (int(n) - 1)
        return base
    if op in _ROLL_FUNC or op in ("EMA", "EMA_TDX", "HHVBARS", "LLVBARS",
                                  "IdxMax", "IdxMin", "Rank", "Slope", "Rsquare", "Resi", "Quantile"):
        base = _tree_ext_days(args[0]) if args else 0
        try:
            n = float(args[1].raw) if len(args) > 1 and getattr(args[1], "op", None) == "const" else 0.0
        except Exception as e:
            _log_swallowed("panel_expr.py", e)
            n = 0.0
        if n >= 1:
            return base + (int(n) - 1)
        if n == 0:
            return base
        # 0<N<1（qlib: alpha 型，需 ~log(1e-6)/log(1-N) 天收敛）
        import math as _math
        return base + max(0, int(_math.log(1e-6) / _math.log(1 - n)) - 1)
    if op == "Ref":
        base = _tree_ext_days(args[0]) if args else 0
        try:
            k = int(args[1].raw) if args[1].op == "const" else 0
        except Exception as e:
            _log_swallowed("panel_expr.py", e)
            k = 0
        return base + max(k, 0)  # 正 k=取过去 k 天前 → 左侧多读 k 天
    # 其余组合/一元：两侧取 max
    best = 0
    for a in args:
        if hasattr(a, "op"):
            best = max(best, _tree_ext_days(a))
    return best


def _expr_ext_days(expr: str) -> int:
    """计算单个表达式为精确复刻 qlib 需前移的交易日数（整棵树 extended，见 _tree_ext_days）。

    含嵌套固定窗口的输入链会正确累加（如 EMA(Max($h,34),4)=33+3=36，而非只认最外层 3）；
    不含敏感/窗口算子返回 0（统一 warm 宽余量对固定窗口无害）。
    """
    try:
        node = parse_expr(str(expr))
        return _tree_ext_days(node)
    except Exception as e:
        _log_swallowed("panel_expr.py", e)
        # 解析失败退化为旧的顶层 EMA 正则（保守兜底）
        import math as _math
        need = 0
        for m in re.finditer(r"(?:EMA|EMA_TDX)\([^,]+,\s*([\d.]+)", str(expr)):
            try:
                n = float(m.group(1))
            except ValueError as _e:
                _log_swallowed('panel_expr.py', _e)
                continue
            if n >= 1:
                need = max(need, int(n) - 1)
            elif n > 0:
                # ⚠ v2.10.67（2026-10-21 ✗→✓ 写测试才发现）：这里原来**不减 1** ✗，
                #   而主路径（`_tree_ext_days` 的 `0<N<1` 分支）是 `int(...) - 1` ✓
                #   ⇒ 同一个表达式"能解析"和"解析失败走兜底"会差 **1 天** ✓
                #   ⇒ 少读一天 ⇒ EMA/滚动类序列**整体偏移** ✓（不报错 ✗）。
                #   两条路的语义必须一致 ✓（`-1` 的理由同主路径：窗口要覆盖 N 个点的跨度 ✓）。
                need = max(need, max(0, int(_math.log(1e-6) / _math.log(1 - n)) - 1))
        # ⚠⚠ v2.10.67（2026-10-21 ✗→✓ 实测抓到）：`return need` 原来**缩在 `for` 循环体里** ✗
        #   ⇒ 循环**一次都没进入**时（表达式解析失败、且里面没有 EMA）函数返回 **None** ✓
        #   —— 而签名是 `-> int` ✗ ⇒ 调用方 `start - ext` 会 TypeError ✓，
        #   或某处 `or 0` 悄悄兜住 ⇒ 预热 0 天 ⇒ 序列偏移 ✓（两种都很糟 ✓）。
        return need


def _shift_calendar_start(start_time: str, days: int) -> str:
    """把 start_time 前移 days 个交易日（不够则取数据首日）。"""
    cal = _calendar()
    t = pd.Timestamp(start_time)
    pos = int(np.searchsorted(cal, t, side="left"))
    prev = max(0, pos - days)
    return str(cal[prev].date())


def _panel_features_uncached(instruments: Sequence[str], fields: Sequence[Tuple[str, str]],
                             start_time: str, end_time: str, cancel_cb=None,
                             warmup_days: int = 0) -> pd.DataFrame:
    """**真正的计算本体** ✓（对外请用 `panel_features` ✓ —— 它外面包了跨任务缓存 ✓）。

    替代 D.features：一次面板求值 (expr, name) 列表 → MultiIndex × 列 DataFrame。

    与 D.features 输出对齐：行 = 各股在【全部参与字段覆盖并集】∩[start,end] 上的
    日历年（含停牌 NaN），列名 = name。读盘自动前移预热窗口（见 _warm_days），
    最终输出仅覆盖 [start_time, end_time]。
    fields: [(表达式文本, 列名), ...]。空表达式跳过。
    cancel_cb: 可选取消检查回调（主线程每求值一个表达式前调用一次）。约定：应取消时
    回调直接抛出异常中止求值（默认 None 不检查）。注意粒度 = 表达式级，单条巨型表达式
    内部仍不可中断。
    warmup_days: 额外预热缓冲（交易日，v1.18.6）。>0 时在【各字段自身可推断的扩展量】
    之外再多前移 warmup_days 天起算（普通组 warm+wu、起点敏感组 ext+wu），使
    DYN_*/BARSCOUNT/HHVBARS+Ref 嵌套等"扩展天数无法静态推断"的状态类/动态窗口算子
    在 start_time 当天就有收敛后的值。固定窗口算子（Mean/Max/Ref…）多读无副作用
    （结果与 warmup_days=0 逐位相同）；EMA/状态类算子的序列起点会提前（值更接近
    长历史真值，但不再复刻 qlib 在 start_time 的冷启动）→ 默认 0，由单因子诊断按需开启。
    输出仍只覆盖 [start_time, end_time]（预热段在出口裁剪，行集不受影响）。
    """
    union_fields = _collect_field_names(fields)
    warm = _warm_days([e for e, _ in fields])
    _wu = max(0, int(warmup_days or 0))  # 额外预热缓冲（交易日）
    # 分组求值：qlib 逐字段按各自 get_extended_window_size 精确前移历史起点——
    # EMA/EMA_TDX 是序列起点敏感的指数递归，qlib 只前移 N-1 天冷启动，读多了反而不
    # 对齐（实测 EMA(5) 前移 4 天才与 qlib 全对、前移 25/56 天都偏）；固定窗口算子
    # （Mean/Max/Min/Ref 等）多读无害，统一用 warm 即可。因此把字段按各自"精确
    # 扩展天数"分桶，每桶一个 PanelEvaluator（桶内共享读盘缓存），输出拼回。
    # v1.17.8：起点敏感还包括"自起点状态类"算子（BARSCOUNT/BARSSINCE/FILTER）——
    # 它们的值取决于起点前的历史（累计/首成立/信号抑制），若被统一 warm 组多读，会与
    # qlib（自身扩展≈子树、多为 0）系统性偏移（实测 csi300 对拍差 32/37/1）→ 也走
    # 精确起点组（ext 允许为 0 = 逻辑起点，不强制最小扩展）。
    _STATE_START_OPS = ("BARSCOUNT(", "BARSSINCE(", "FILTER(")
    # ★ v2.11.31：显式注解 ✓（空 dict 不注解 ⇒ mypy 推成 `dict[Never, Never]` ✗，
    #   后面 `d[e] = ...` 会报 "Invalid index type" ✓）。
    sensitive_fields: dict = {}
    normal_fields: dict = {}
    for expr, name in fields:
        e = str(expr).strip()
        if not e or e.lower() in ("", "none", "nan"):
            continue
        if "SR(" in e:
            # 含 SR 的字段：read_start 由 SR 前移主导（lookback 250），EMA/SMA 在其中
            # 从更早收敛点起算，与 qlib 一致（实测 SR(EMA) 全对）→ 归普通组。
            normal_fields.setdefault(warm, []).append((e, name))
        elif re.search(r"\b(?:EMA|EMA_TDX|SMA)\(", e):
            ext = _expr_ext_days(e)
            # EMA/EMA_TDX 是 Rolling 起点敏感（扩展>=N-1>=1）；SMA 的 qlib 扩展 =
            # 子特征透传（可为 0，如 SMA($close,5,1) 扩展为 0）→ 不强制 >=1。
            if ext < 1 and not re.search(r"\bSMA\(", e):
                ext = max(1, ext)
            # 该字段的精确起点 = 统一 warm 与"精确 N-1"的交集？不：qlib 只前移
            # N-1，故此处 read_start 前移量直接取 ext（比 warm 更晚），让 EMA
            # 恰好从 qlib 的冷启动点起算。
            sensitive_fields.setdefault(int(ext), []).append((e, name))
        elif any(_t in e for _t in _STATE_START_OPS):
            # 起点敏感状态算子：qlib 扩展 = 自身子树（可为 0），不强制最小前移
            ext = _expr_ext_days(e)
            sensitive_fields.setdefault(int(ext), []).append((e, name))
        else:
            # 固定窗口/普通算子：多读无害，统一 warm 组
            normal_fields.setdefault(warm, []).append((e, name))

    cols = {}
    last_ev = None
    # 普通组：统一 read_start（保持原语义，含 SR/固定窗口）+ 额外预热缓冲 wu
    if normal_fields:
        rs = _shift_calendar_start(start_time, warm + _wu)
        ev = PanelEvaluator(instruments, start_time, end_time, union_fields,
                            read_start=rs)
        last_ev = ev
        for e, name in normal_fields[warm]:
            if cancel_cb is not None:
                cancel_cb()  # 取消检查点：表达式级（单条巨型表达式内不可中断）
            cols[name] = ev.eval_expr(e)
    # 敏感组：每桶按各自精确扩展量建独立 evaluator（桶共享 read_start/读盘缓存）
    # 同样叠加额外预热缓冲 wu（EMA 等在更早起点收敛，见 warmup_days 说明）
    for ext, items in sensitive_fields.items():
        rs = _shift_calendar_start(start_time, ext + _wu)
        ev = PanelEvaluator(instruments, start_time, end_time, union_fields,
                            read_start=rs)
        last_ev = ev
        for e, name in items:
            if cancel_cb is not None:
                cancel_cb()
            cols[name] = ev.eval_expr(e)
    # 输出 = 逻辑区间 [start_time, end_time] 的并集（裁剪预热段）
    # v1.18.4 性能：复用最后一个 evaluator 的 _full（[read_start, end] 的字段覆盖并集）
    # 按时序裁剪，免去重算一遍全量 union。等价性：read_start ≤ start_time，
    # 每股覆盖 [max(lo, rs), hi] ∩ [start, end] = [max(lo, start), hi]（hi 已截到 end）。
    if last_ev is not None:
        full_out = _trim_index_from(last_ev._full, start_time)
    else:
        full_out = _union_index(instruments, start_time, end_time, union_fields)
    if not cols:
        return pd.DataFrame(index=full_out)
    df = pd.DataFrame(cols)
    # v1.18.31：两处全量 MultiIndex 运算按需免算（35 万行实测分别为 21ms / 21ms / 5ms）
    #  · 列 index 已正好等于目标行集（无预热前移时：read_start == start_time 且只有一个
    #    evaluator）⇒ union + 两次 reindex + 时间戳掩码全是恒等操作，直接返回；
    #  · 否则若列 index 正好等于最后一个 evaluator 的 _full，则 df.index ⊇ full_out
    #    （full_out 由它裁剪而来）⇒ union 必等于 df.index，跳过这次 union。
    if df.index is full_out or df.index.equals(full_out):
        return _cast_output_f32(df)
    if last_ev is None or not (df.index is last_ev._full or df.index.equals(last_ev._full)):
        # 不同 read_start 的列 index 覆盖范围不同 → 统一 reindex 到各列并集再裁剪
        df = df.reindex(df.index.union(full_out))
    df = df[df.index.get_level_values("datetime") >= pd.Timestamp(start_time)]
    df = df.reindex(full_out)
    return _cast_output_f32(df)


def panel_features(instruments: Sequence[str], fields: Sequence[Tuple[str, str]],
                   start_time: str, end_time: str, cancel_cb=None,
                   warmup_days: int = 0) -> pd.DataFrame:
    """面板求值（**带跨任务磁盘缓存** ✓ ★ P1-C-1，2026-10-18 ✓）。

    ## 为什么包这一层（而不是改本体 ✗）

    `_panel_features_uncached` 里有 **3 条 return**（提前返回 / 免算再返回 / 裁剪后返回 ✓），
    直接在里面插缓存会逼着改三处控制流 ✗ ⇒ 风险大且不必要 ✓。
    包一层之后：**计算本体一个字没改** ✓，缓存只是"查 → 算 → 存" ✓。

    ## 关键约定

    · **缓存绝不改变结果** ✓：`load` 失败/坏文件/写盘失败 ⇒ **一律退回直算** ✓；
    · **键含 warmup_days 与语义版本** ✓（它俩都真影响结果 ✗ ⇒ 漏一个就会读到错口径 ✗）；
    · 开关 `QLIB_EXPR_CACHE=0` ✓（排查"两次结果不一致"时先关它 ✓）；
    · 清理见 `services/expr_cache.py`（TTL 7 天 / 80 个 / 4GB ✓ 每次写完自动清 ✓）。
    """
    # ⚠ 惰性 import ✓：`services.expr_cache` 只依赖 `config` ✓，但放函数里可以彻底
    #   避免"factors ↔ services 循环引用"的隐患 ✗（本项目踩过循环 import 的坑 ✓）。
    from ..services import expr_cache

    ck = None
    if expr_cache.enabled():
        try:
            ck = expr_cache.key(_feature_dir(), instruments, fields,
                                start_time, end_time, int(warmup_days or 0))
            hit = expr_cache.load(ck)
            if hit is not None:
                return hit
        except Exception as e:  # noqa: BLE001 —— 缓存不许影响正确性 ✗
            _log_swallowed("panel_expr.py", e)
            ck = None
    out = _panel_features_uncached(instruments, fields, start_time, end_time,
                                   cancel_cb=cancel_cb, warmup_days=warmup_days)
    if ck:
        try:
            expr_cache.save(ck, out)
        except Exception as e:  # noqa: BLE001
            _log_swallowed("panel_expr.py", e)
            pass
    return out


def _cast_output_f32(df: pd.DataFrame) -> pd.DataFrame:
    """把输出列统一 cast 到 float32——对齐 qlib D.features 的返回 dtype。

    背景（v1.17.5 后全 A 复现定位）：qlib D.features 所有字段最终输出整列为
    float32（内部 float64 求值、出口 cast，见 qlib data.py dtype=np.float32）；
    而面板此前全程 float64 输出。两者对 CLOSE/CHANGE 等列存在 ~4~8e-6 尾差
    （float32 ulp 级），本不影响因子值，但会传导到涨停/停牌剔除判定
    （mark_limit_up 用 close >= limit_up - 1e-6 的 1e-6 容差与面板尾差同量级）
    → 全 A 端到端触发集出现零星差异（实测 15 只各多/少 1 个触发、daily 差
    ~0.002pp）。内部求值保持 float64（更精确、EMA 递归收敛一致），仅出口
    cast float32 与 qlib 对齐后逐列 0 差（实测 base/tag 全部 cast 后 0 差）。
    """
    return df.astype(np.float32)




def _collect_field_names(fields) -> tuple:
    """从 (expr, name) 列表收集全部 $field 引用（供 union index 对齐 qlib 行集）。

    ⚠ 筹码字段是**计算字段**（不在数据里）⇒ 必须把它依赖的基础字段一并纳入 union，
      否则这些字段的行集/覆盖区间不对齐，会算出莫名 NaN（只在"该股有行情但字段没被 union 覆盖"时暴露）。
    """
    out = []
    seen = set()
    for expr, _ in fields:
        for m in re.finditer(r"\$([A-Za-z_][A-Za-z0-9_]*)", str(expr)):
            f = m.group(1)
            if f.startswith("chip_"):
                for b in _CHIP_BASE_FIELDS:
                    if b not in seen:
                        seen.add(b)
                        out.append(b)
                continue                      # 计算字段本身不是 bin 文件，不进 union
            if f not in seen:
                seen.add(f)
                out.append(f)
    if not out:
        out = ["close"]
    return tuple(out)


# ===========================================================================
# 4) 并行版（v1.16.9：全 A 大池。面板求值跨股票独立 → 按股票切块多进程并行）
# ===========================================================================

def _worker_init(calendar, feature_dir, bin_cache_mb=None, node_cache_mb=None):
    """子进程初始化：注入日历与数据目录（免 qlib.init）；可选收紧读盘/节点缓存上限。"""
    set_panel_runtime(calendar=calendar, feature_dir=feature_dir)
    if bin_cache_mb is not None:
        set_bin_cache_mb(bin_cache_mb)
    if node_cache_mb is not None:
        set_node_cache_mb(node_cache_mb)


def _panel_features_chunk(payload):
    """子进程执行体：对一份股票子集跑 panel_features。

    payload: (insts, fields, start, end, warmup_days)；兼容旧 4 元组（warmup=0）。
    """
    if len(payload) >= 5:
        insts, fields, start, end, wu = payload[:5]
    else:
        insts, fields, start, end = payload
        wu = 0
    return panel_features(insts, fields, start, end, warmup_days=wu)


@contextlib.contextmanager
def _nowin_spawn():
    """Windows 下让 multiprocessing spawn 的 python 子进程不弹黑色控制台窗口。

    根因：后端若以无控制台方式运行（如被 DETACHED_PROCESS 启动），spawn 的
    python.exe（console 子系统）子进程会被 Windows 分配【各自新的控制台窗口】
    → 用户看到弹出多个黑色命令行窗口。解法：把 spawn 的可执行文件临时切成
    同目录 pythonw.exe（GUI 子系统、天然无控制台），池用完即还原。

    仅影响 multiprocessing.spawn._python_exe（ProcessPoolExecutor 走这里）；
    qlib loky 用自己的 _python_exe（joblib externals loky.backend.spawn），互不影响。
    非 Windows 平台为空操作。
    """
    if os.name != "nt":
        yield
        return
    import sys as _sys

    old = None
    pythonw = None
    try:
        pythonw = os.path.join(os.path.dirname(_sys.executable), "pythonw.exe")
        if os.path.exists(pythonw):
            import multiprocessing.spawn as _sp

            old = _sp._python_exe
            _sp.set_executable(pythonw)
        yield
    finally:
        if old is not None and pythonw is not None:
            import multiprocessing.spawn as _sp

            _sp._python_exe = old


def _panel_executor(max_workers, initializer, initargs):
    """构造 ProcessPoolExecutor（Windows 下在 _nowin_spawn 上下文内创建/使用，免弹窗）。"""
    import concurrent.futures as cf

    return cf.ProcessPoolExecutor(max_workers=max_workers, initializer=initializer, initargs=initargs)


def _force_terminate_executor(ex):
    """取消兜底：强杀进程池所有 worker（含在跑块），立即归还内存。

    ProcessPoolExecutor.shutdown(wait=False, cancel_futures=True) 只能让主进程不再等待
    ——已派发给 worker 的块无法中途取消，worker 会跑完当前块才退出。全 A 大公式单块可跑
    数分钟，仅 shutdown 时取消后 worker 仍 100% CPU + 峰值内存驻留。这里直接 terminate
    全部 worker 进程（executor 私有 API `_processes`，CPython 3.7+ 稳定存在；terminate
    后 OS 立即回收 worker 内存）。仅供取消/异常路径使用：executor 随后不再 submit/收块。
    """
    try:
        procs = getattr(ex, "_processes", None)
        if procs:
            for p in list(procs.values()):
                try:
                    if p.is_alive():
                        p.terminate()
                except Exception as e:
                    _log_swallowed("panel_expr.py", e)
                    pass
    except Exception as e:
        _log_swallowed("panel_expr.py", e)
        pass


def panel_features_parallel(instruments: Sequence[str], fields: Sequence[Tuple[str, str]],
                            start_time: str, end_time: str,
                            n_jobs: Optional[int] = None,
                            progress_cb=None,
                            progress_lo: float = 6.0, progress_hi: float = 30.0,
                            cancel_cb=None,
                            warmup_days: int = 0) -> pd.DataFrame:
    """并行面板求值（替代 panel_features 用于大池）。

    panel_features 的计算对每只股票独立（rolling/shift 按 instrument 分组、字段各读各的
    bin），因此把 instruments 切成 n_jobs 块后每块在独立进程求值，concat 结果等价于
    单块整体求值——但每进程只持有 1/n_jobs 的面板，内存与墙钟都大幅下降（全 A 实测
    单进程 ~300s / 8.6GB；12 核并行预期 ~20-40s / 单进程内存 1/12）。

    worker 进程通过 _worker_init 注入 calendar（免 qlib.init，calendar 与
    D.calendar() 完全一致：6455 行 2000-01-04 ~ 2026-08-21）。
    Windows 子进程用 pythonw.exe spawn，不弹黑色命令行窗口（_panel_executor）。

    progress_cb: 可选回调 progress_cb(pct, msg)。每完成一块报一次，pct 从
    progress_lo 线性推进到 progress_hi（默认 6→30，对应 single_test 的加载阶段
    0-30 进度）。回调只在主进程收集结果时触发，不进入子进程计算路径，不拖慢求值。
    cancel_cb: 可选取消检查回调（主进程每收一块前调用一次）。约定：应取消时回调直接
    抛异常中止收集；已提交给进程池的块无法中途终止，但剩余未收结果不再等待
    （最坏多等一块运行时间）。worker 内 panel_features 不传（无法感知主进程标志）。
    warmup_days: 额外预热缓冲（交易日），随切块 payload 透传给每个 worker 的
    panel_features（语义见 panel_features.warmup_days；默认 0 = 关闭）。

    缓存档位（v1.18.3）：节点缓存上限按可用内存自适应两档（_auto_node_cache_mb，
    宽裕 1024MB / 否则 512MB，QLIB_SFT_PANEL_NODE_CACHE_MB 显式覆盖）；读盘缓存按
    可用内存均分（见下）。两者先作用于本进程，再随 initargs 注入 worker。
    """
    import concurrent.futures as cf
    import math

    n = len(instruments)
    if n == 0:
        return pd.DataFrame()
    if n_jobs is None:
        # CPU 维度：每块 ≥300 只、≤12 worker
        jobs_cpu = min(12, max(1, math.ceil(n / 300)))
        # 内存维度（v1.18.1）：worker 峰值 ≈ 块内(公式节点 + 字段 + bin LRU) 的倍数，
        # 小内存机器开满 12 worker 会爆（多公式 × 并行 × 缓存驻留）。按可用内存 ÷
        # 单 worker 预算估算上限；QLIB_SFT_PANEL_MEM_PER_JOB_GB 可调（默认 3GB），
        # QLIB_SFT_PANEL_JOBS 显式指定则完全覆盖。
        avail_gb = _system_avail_gb()
        per_gb = float(os.environ.get("QLIB_SFT_PANEL_MEM_PER_JOB_GB", "3"))
        jobs_mem = max(1, int(avail_gb / per_gb))
        # 低水位加固（v1.18.1）：可用内存已紧张（默认 <6GB）再收敛到 ≤ avail/2 个 worker
        low = float(os.environ.get("QLIB_SFT_PANEL_LOW_MEM_GB", "6"))
        if avail_gb < low:
            jobs_mem = min(jobs_mem, max(1, int(avail_gb / 2)))
        n_jobs = max(1, min(jobs_cpu, jobs_mem, n))
    n_jobs = max(1, min(n_jobs, n))
    # 节点缓存上限（v1.18.3）：按可用内存自适应两档（见 _auto_node_cache_mb），显式
    # QLIB_SFT_PANEL_NODE_CACHE_MB 优先。先在本进程设置（单进程路径同样生效），再把
    # 同一档位显式注入 worker（比依赖 spawn 继承 env 更可靠、可测）。
    node_cache_mb = _auto_node_cache_mb(n_jobs)
    set_node_cache_mb(node_cache_mb)
    if n_jobs == 1 or n <= 1000:
        # 小池/单块：直接单进程（避免进程池固定开销）；同样支持取消检查点
        return panel_features(instruments, fields, start_time, end_time, cancel_cb=cancel_cb,
                              warmup_days=warmup_days)

    cal = _calendar()
    fdir = _feature_dir()

    # 切块（v1.20.1，2026-09-18 改）：**块数 = n_jobs × 4**，不再等于 n_jobs ✗ ——
    #   进度只在"每完成一块"时上报 ✓，块数 = 核数时每块要跑**几分钟**（全 A 5418 只、
    #   过顶那种重公式 ⇒ 用户看到进度条**长时间停在 6%** ✗，体感"卡死了" ✓）。
    #   块数 ×4 后：上报次数 ×4（进度肉眼可见地走 ✓）、**总耗时基本不变**（进程池按块调度，
    #   块数 > worker 数时负载反而更均衡 ✓），且单块内存更小 ✓。
    #   ⚠ 下限保护 `_MIN_PER_CHUNK`：块太碎会放大"主↔子进程"的数据传输与建池开销 ✓。
    #   ⚠⚠ worker 数**仍必须是 n_jobs**（见下方 `_panel_executor(n_jobs, ...)` ✗ 不能用
    #     `len(chunks)` —— 否则 worker 数 ×4 ⇒ 内存直接爆 ✓）。
    _MIN_PER_CHUNK = 80
    n_chunk_target = max(n_jobs, min(n_jobs * 4, max(1, n // _MIN_PER_CHUNK)))
    chunk_size = math.ceil(n / n_chunk_target)
    chunks = [instruments[i:i + chunk_size] for i in range(0, n, chunk_size)]
    n_chunk = len(chunks)
    _wu = max(0, int(warmup_days or 0))
    payloads = [(c, fields, start_time, end_time, _wu) for c in chunks]

    # 每 worker bin 读盘缓存预算（v1.18.1 加固）：显式 QLIB_PANEL_BIN_CACHE_MB 优先；
    # 否则按可用内存均分（全部 worker 盘缓存合计 ≈ 可用内存 40%），避免
    # "12 worker × 768MB" 再叠数 GB。
    bin_cache_mb = None
    try:
        env_mb = os.environ.get("QLIB_PANEL_BIN_CACHE_MB")
        if env_mb is not None:
            bin_cache_mb = int(env_mb)
        else:
            _avail_mb = _system_avail_gb() * 1024.0
            bin_cache_mb = max(64, min(768, int(_avail_mb * 0.4 / n_jobs)))
    except Exception as e:
        _log_swallowed("panel_expr.py", e)
        pass

    parts: dict = {}
    done = 0
    with _nowin_spawn():
        # ⚠ worker 数 = `n_jobs`（**不是** `len(chunks)` ✗ —— 块数已 ×4，若按块数建池
        #   会把并发 worker 也 ×4 ⇒ 内存爆 ✓）；多出的块由进程池**排队**执行 ✓。
        cm = _panel_executor(n_jobs, _worker_init,
                             (cal, fdir, bin_cache_mb, node_cache_mb))
        ex = cm.__enter__()
        try:
            futs = {ex.submit(_panel_features_chunk, p): i for i, p in enumerate(payloads)}
            pending = set(futs)
            # 轮询式收块：每 0.25s 查一次取消标志 + 收已完成块。相对 as_completed 阻塞
            # 等待，取消无需等"下一块完成"才被感知——用户点取消后 ≤0.25s 即响应。
            while pending:
                if cancel_cb is not None:
                    cancel_cb()  # 命中即抛，由调用方转为 FactorTestCancelled
                done_set, pending = cf.wait(pending, timeout=0.25, return_when=cf.FIRST_COMPLETED)
                for fut in done_set:
                    i = futs[fut]
                    r = fut.result()
                    if r is not None and len(r):
                        parts[i] = r
                    done += 1
                    if progress_cb:
                        # 线性映射 lo→hi；msg 显示已完块数（真实计算进度，非字节/耗时估算）
                        pct = progress_lo + (progress_hi - progress_lo) * (done / n_chunk)
                        progress_cb(pct, f"计算特征数据（面板并行，{done}/{n_chunk} 块完成）...")
        except BaseException:
            # 取消（异常路径）：强杀全部 worker 立即释放内存，不等在跑块。
            # 仅 shutdown(wait=False, cancel_futures=True) 只能停主进程收块——已派发的
            # 块无法取消，worker 会跑完当前块才退出（全 A 大公式单块可跑数分钟，取消后
            # worker 仍 100% CPU + 峰值内存驻留，全 A 多公式场景实测取消 7 分钟内存不减、
            # 接近 OOM）。terminate 后 OS 立即回收 worker 内存；executor 随即不再使用
            # （调用方已中止），安全。
            _force_terminate_executor(ex)
            ex.shutdown(wait=False, cancel_futures=True)
            raise
        else:
            # 正常路径：等待全部块完成（等价原 with 语义）
            cm.__exit__(None, None, None)
    if not parts:
        return pd.DataFrame()
    # 每块 panel_features 已自行裁剪到该块股票的 union index（字段覆盖 ∩ [start,end]），
    # 跨块股票不重叠 → concat + sort 即完整（无需全局 reindex）。
    ordered = [parts[i] for i in range(n_chunk) if i in parts]
    return pd.concat(ordered).sort_index()
