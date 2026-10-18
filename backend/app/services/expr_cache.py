# -*- coding: utf-8 -*-
"""公式面板结果**跨任务磁盘缓存**（★ P1-C-1，2026-10-18 ✓）。

## 为什么要有它（用户体感第二痛 ✓）

同一个公式（例如 `CPX`）每跑一次单因子测试，就要把面板**重算一遍** ✗ ——
实测全 A × 4 年 ≈ **4~5 秒**（含递推组 ✓）。而用户真实用法是"改个参数再跑一次" ✓
⇒ 绝大部分时间花在**重复计算同一列**上 ✗。

## 键怎么定（宁可不命中，也不能命中错 ✗）

`(feature_dir, instruments, fields, start, end, warmup_days, 语义版本)` 的 **sha1** ✓
- `instruments` 排序后拼 ✓（顺序不影响结果 ✓）；
- `feature_dir` 带数据集名 ✓（`cn_data2` vs 其它数据集不能串 ✓）；
- `warmup_days` 进键 ✓（它对 EMA/状态类是**真影响结果**的 ✓，2026-10-17 实测过 ✓）；
- **`_SEMANTICS` 进键** ✓：面板求值语义一改（例如递推组规则）⇒ 整体失效 ✓
  ⚠ 改了 `panel_expr` 的结果口径就必须**手动 +1** ✓，否则会读到旧口径 ✗（比不缓存更糟 ✗）。

## 存哪、存什么

`workdir/expr_cache/<sha1>.parquet` ✓ —— parquet 能原样保留 MultiIndex + float32 ✓
（npz 要自己存 index ✗，且列名/MultiIndex 容易错 ✓）。

## 定期清理（用户 2026-10-18 明确要求 ✓「注意定期清理」）

三重边界，**全部可调** ✓：

| 边界 | 默认 | 环境变量 |
|---|---|---|
| 存活天数（TTL） | **7 天** | `QLIB_EXPR_CACHE_TTL_DAYS` |
| 文件个数 | **80** | `QLIB_EXPR_CACHE_MAX_FILES` |
| 总量 | **4 GB** | `QLIB_EXPR_CACHE_MB` |

清理时机：**每次 save 之后**（轻量 `listdir` ✓）+ **进程启动时** ✓ + 超限时强制 ✓。
单条超过 **512 MB** 不落盘 ✓（一次全 A 全历史的大列不值得缓存 ✗）。

## 开关

`QLIB_EXPR_CACHE=0` ⇒ 全关 ✓（排查"两次结果不一致"时，**第一件事就是关它** ✓）。
"""
from __future__ import annotations

import hashlib
import os
import time
from typing import Optional, Sequence, Tuple

import pandas as pd

from ..config import WORK_DIR

#: 语义版本：**改了 `panel_expr` 的计算口径就 +1** ✓（否则会读到旧口径 ✗）
_SEMANTICS = 1

_ROOT = os.path.join(WORK_DIR, "expr_cache")

_TTL_DAYS = float(os.environ.get("QLIB_EXPR_CACHE_TTL_DAYS", "7"))
_MAX_FILES = int(os.environ.get("QLIB_EXPR_CACHE_MAX_FILES", "80"))
_MAX_MB = float(os.environ.get("QLIB_EXPR_CACHE_MB", "4096"))
_MAX_ENTRY_MB = 512.0

#: 命中/未命中计数（诊断用 ✓；不参与清理逻辑 ✓）
_STATS = {"hit": 0, "miss": 0, "save": 0, "skip": 0, "prune": 0}


def enabled() -> bool:
    """开关 ✓（`QLIB_EXPR_CACHE=0` 关闭 ✓）。"""
    return str(os.environ.get("QLIB_EXPR_CACHE", "1")).strip() not in ("0", "false", "False", "off")


def key(feature_dir: str, instruments: Sequence[str], fields: Sequence[Tuple[str, str]],
        start: str, end: str, warmup_days: int = 0) -> str:
    """缓存键 ✓（任一要素不同即 miss ✓）。"""
    insts = sorted({str(x) for x in instruments})
    flds = sorted({(str(e), str(n)) for e, n in fields})
    raw = "\x1f".join([
        "v%d" % _SEMANTICS,
        os.path.abspath(str(feature_dir)),
        str(start), str(end), str(int(warmup_days or 0)),
        "|".join(insts),
        "|".join("%s=%s" % (e, n) for e, n in flds),
    ])
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()


def _path(k: str) -> str:
    return os.path.join(_ROOT, "%s.parquet" % k)


def load(k: str) -> Optional[pd.DataFrame]:
    """读缓存 ✓（**任何异常都返回 None** ✓ —— 坏文件直接删掉 ✓）。"""
    p = _path(k)
    if not os.path.isfile(p):
        _STATS["miss"] += 1
        return None
    try:
        df = pd.read_parquet(p)
        _STATS["hit"] += 1
        try:
            os.utime(p, None)                 # 命中即"续命" ✓（TTL 按最近使用 ✓ 不是写入时间 ✓）
        except Exception:                     # noqa: BLE001
            pass
        return df
    except Exception:                          # noqa: BLE001
        try:
            os.remove(p)
        except Exception:                      # noqa: BLE001
            pass
        _STATS["miss"] += 1
        return None


def save(k: str, df: pd.DataFrame) -> bool:
    """写缓存 ✓（原子 ✓、超限跳过 ✓、写完顺手清理 ✓）。"""
    try:
        if df is None or not isinstance(df, pd.DataFrame) or df.empty:
            _STATS["skip"] += 1
            return False
        os.makedirs(_ROOT, exist_ok=True)
        p = _path(k)
        tmp = p + ".tmp"
        df.to_parquet(tmp, compression="snappy")
        mb = os.path.getsize(tmp) / (1024.0 * 1024.0)
        if mb > _MAX_ENTRY_MB:
            os.remove(tmp)                     # 单条太大 ⇒ 不落 ✓
            _STATS["skip"] += 1
            return False
        os.replace(tmp, p)                     # 原子 ✓
        _STATS["save"] += 1
        prune()                                # ★ 每次写完就清理 ✓（用户要求 ✓）
        return True
    except Exception:                          # noqa: BLE001
        _STATS["skip"] += 1
        return False


def prune(ttl_days: float = None, max_files: int = None, max_mb: float = None) -> int:
    """按 **TTL / 个数 / 总量** 清理 ✓，返回删掉的文件数 ✓。

    ⚠ 全部异常吞掉 ✓（清理失败绝不能影响主流程 ✗）。
    """
    ttl = float(_TTL_DAYS if ttl_days is None else ttl_days)
    keep_n = int(_MAX_FILES if max_files is None else max_files)
    keep_mb = float(_MAX_MB if max_mb is None else max_mb)
    n = 0
    try:
        if not os.path.isdir(_ROOT):
            return 0
        rows = []
        for name in os.listdir(_ROOT):
            if not name.endswith(".parquet"):
                continue
            p = os.path.join(_ROOT, name)
            try:
                st = os.stat(p)
            except Exception:                  # noqa: BLE001
                continue
            rows.append((st.st_mtime, st.st_size, p))
        now = time.time()
        # ① TTL ✓
        keep = []
        for mt, size, p in rows:
            if ttl > 0 and (now - mt) > ttl * 86400.0:
                try:
                    os.remove(p)
                    n += 1
                    continue
                except Exception:              # noqa: BLE001
                    pass
            keep.append((mt, size, p))
        # ② 个数 ✓（按最近使用保留最新的 N 个 ✓）
        keep.sort(reverse=True)
        for mt, size, p in keep[keep_n:]:
            try:
                os.remove(p)
                n += 1
            except Exception:                  # noqa: BLE001
                pass
        keep = keep[:keep_n]
        # ③ 总量 ✓（从最旧的删起 ✓）
        total = sum(s for _, s, _ in keep) / (1024.0 * 1024.0)
        if total > keep_mb:
            for mt, size, p in sorted(keep):
                if total <= keep_mb:
                    break
                try:
                    os.remove(p)
                    n += 1
                    total -= size / (1024.0 * 1024.0)
                except Exception:              # noqa: BLE001
                    pass
        if n:
            _STATS["prune"] += n
    except Exception:                          # noqa: BLE001
        pass
    return n


def stats() -> dict:
    """诊断快照 ✓（给日志/排查用 ✓）。"""
    files, mb = 0, 0.0
    try:
        if os.path.isdir(_ROOT):
            for name in os.listdir(_ROOT):
                if name.endswith(".parquet"):
                    files += 1
                    mb += os.path.getsize(os.path.join(_ROOT, name)) / (1024.0 * 1024.0)
    except Exception:                          # noqa: BLE001
        pass
    out = dict(_STATS)
    out.update({"files": files, "mb": round(mb, 2), "enabled": enabled(),
                "ttl_days": _TTL_DAYS, "max_files": _MAX_FILES, "max_mb": _MAX_MB})
    return out


def clear() -> int:
    """清空（测试/维护用 ✓）。"""
    n = 0
    try:
        if os.path.isdir(_ROOT):
            for name in os.listdir(_ROOT):
                if name.endswith(".parquet") or name.endswith(".tmp"):
                    try:
                        os.remove(os.path.join(_ROOT, name))
                        n += 1
                    except Exception:          # noqa: BLE001
                        pass
    except Exception:                          # noqa: BLE001
        pass
    return n


try:                                           # 进程启动时清理一次 ✓（用户要求"定期清理" ✓）
    prune()
except Exception:                              # noqa: BLE001
    pass
