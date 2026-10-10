# -*- coding: utf-8 -*-
"""多数据集（数据口径）切换服务 —— 用户 2026-10-09：三套数据来回切，前端做对照。

## 三套数据集（都在 `data/` 下 ✓）

| 名字 | 目录 | 口径 | 字段 | 说明 |
|---|---|---|---|---|
| `cn_data` | `data/cn_data` | **米筐/qlib 口径** | 全字段 | 由 `cn_data.rar`（米筐 era 备份）解出 ✓，行情段按 qlib 官方口径重建 ✓ |
| `cn_data2` | `data/cn_data2` | **tushare 口径** | 全字段 | **平台主数据集** ✓：行情 = `raw × tushare adj` ✓，财务/资金流/筹码齐全 ✓ |
| `cn_data3` | `data/cn_data3` | qlib 官方原始 | **仅 10 个行情字段** | 对照用 ✓；**没有** `fin_*`/`mf_*`/`chip_*` ✗ ⇒ UI 必须提示 ✓ |

## ★ 为什么是"全局切换"而不是"每请求带一个数据集"
qlib 的 `D`（DatasetProvider）是**进程级单例**，`qlib.init(provider_uri=...)` 只能生效一次 ✗；
要在运行期换目录，只能重新 `qlib.init`（实测 `C.register()` 会重注册 wrappers ✓ 见
`qlib/config.py:503-522`）并清掉各模块的进程级缓存 ✗ ⇒ 天然是**全局状态** ✓。
本平台单用户、任务串行排队 ⇒ 全局切换够用且最稳 ✓（切换前后端会把正在跑的任务情况回报给 UI ✓）。

## ★ 子进程（joblib worker）怎么跟随
`config.py` 在**导入时**就读 `data/active_dataset.json` ✓ ⇒ `spawn` 出来的 worker 进程
自然落到同一数据集 ✓（只改内存变量是不行的 ✗ —— worker 会重新 import ✓）。
"""
from __future__ import annotations

import json
import os
import threading
import time
from pathlib import Path
from typing import Dict, List, Optional

ROOT = Path(__file__).resolve().parents[3]                 # <repo>/
DATA = ROOT / "data"
ACTIVE_FILE = DATA / "active_dataset.json"
_LOCK = threading.Lock()
_PROBE_CACHE: Dict[str, tuple] = {}                        # name -> (mtime, info)

# 显示名 / 口径标签（前端直接用 ✓）。`expect_convention` = **必须靠目录里的标记文件自证** ✓，
# 不满足就**不显示** ✓（同事自建同名目录、里面不是 tushare 数据时，不能冒充 tushare 口径 ✗）。
REGISTRY: Dict[str, dict] = {
    "cn_data": {
        "dir": "cn_data",
        "label": "米筐/qlib 口径",
        "expect_convention": None,                        # 基线数据集：只要目录在就显示 ✓
        "convention": "qlib 官方（factor 与 tushare adj 只差常数）",
        "note": "由 cn_data.rar 备份解出，行情段按 qlib 口径重建",
    },
    "cn_data2": {
        "dir": "cn_data2",
        "label": "tushare 口径",
        "expect_convention": "tushare",                   # ★ 必须标记为 tushare 才显示 ✓
        "convention": "行情 = raw × tushare adj_factor；财务/资金流/筹码全量",
        "note": "平台主数据集：字段齐全、日历到最新交易日",
    },
    "cn_data3": {
        "dir": "cn_data3",
        "label": "qlib 官方原始",
        "expect_convention": "qlib",                      # ★ 必须标记为 qlib 才显示 ✓
        "convention": "qlib 官方 cn_data 原样（10 个行情字段）",
        "note": "仅行情字段；FINANCE(q)/资金流/筹码等公式在此数据集下取不到数（按 NaN 处理）",
        "only_price": True,
    },
}
_ORDER = ["cn_data2", "cn_data", "cn_data3"]               # 默认优先级：主数据集优先 ✓

# ★ 数据集标记文件（放在 provider 根目录 ✓ 与 qlib 的 calendars/features/instruments 并存 ✓）
#   作用：① **证明口径**（同事自建目录没这个标记 ⇒ 不显示 ✓）；② 记录**字段基准**，
#   用于"同事往 cn_data 里 dump 了新字段"时**只提示不报错** ✓。
MARKER_NAME = ".dataset.json"
KNOWN_CONVENTIONS = {"tushare", "qlib", "ricequant"}


def dataset_dir(name: str) -> Path:
    """数据集目录 ✓（注册表没登记的 `cn_dataN` 也支持 ✓ —— 同事挂自己的数据集用 ✓）。"""
    return DATA / (REGISTRY.get(name, {}).get("dir") or name)


def marker_path(name: str) -> Path:
    return dataset_dir(name) / MARKER_NAME


def read_marker(name: str) -> dict:
    """读数据集标记（**一律 `utf-8-sig`** ✓ 容忍 BOM）；没有/坏了 ⇒ {} ✓（不抛异常 ✗）。"""
    p = marker_path(name)
    if not p.exists():
        return {}
    try:
        return json.loads(p.read_text(encoding="utf-8-sig")) or {}
    except Exception:                                                     # noqa: BLE001
        return {}


def declared_convention(name: str) -> Optional[str]:
    """目录**自报**的口径（标记文件里的 `convention` ✓）；没标记/没写 ⇒ None ✓。"""
    c = read_marker(name).get("convention")
    return str(c).strip() if c else None


def _accepted(name: str) -> bool:
    """该数据集是否允许出现在切换列表里 ✓（**认不出来的一律不显示** ✗）。

    规则（用户 2026-10-10 定 ✓）：
    · `cn_data` = 基线，只要目录在就显示 ✓（同事往里面 dump 字段也照常显示 ✓，只给提示 ✓）；
    · `cn_data2` **必须**标记 `convention=tushare` ✓、`cn_data3` **必须**标记 `qlib` ✓
      —— 同事自己建的同名目录若没标记 ⇒ **不显示** ✓（免得把非 tushare 的数据当 tushare 用 ✗）；
    · 其它 `cn_dataN`：标记里的口径属于已知集合 ⇒ 显示 ✓（方便同事挂自己的数据集 ✓）。
    """
    if not (dataset_dir(name) / "calendars" / "day.txt").exists():
        return False
    expect = (REGISTRY.get(name) or {}).get("expect_convention")
    declared = declared_convention(name)
    if expect:
        return declared == expect
    if name == "cn_data":
        return True
    return declared in KNOWN_CONVENTIONS


def discover_names() -> List[str]:
    """候选名 = 注册表里的名字 + `data/` 下任何 `*_data`/`cn_data*` 目录 ✓（按 `_ORDER` 排序 ✓）。"""
    names = list(REGISTRY.keys())
    if DATA.is_dir():
        for d in sorted(DATA.iterdir()):
            if d.is_dir() and d.name not in names and (d.name.startswith("cn_data")
                                                      or d.name.endswith("_data")):
                names.append(d.name)
    order = {n: i for i, n in enumerate(_ORDER)}
    return sorted(names, key=lambda n: (order.get(n, 99), n))


def available() -> List[str]:
    """磁盘上真实存在**且口径可自证**的数据集 ✓（保持 `_ORDER` 的展示顺序 ✓）。"""
    return [n for n in discover_names() if _accepted(n)]


def active_name() -> str:
    """当前生效的数据集名：优先落盘记录 ✓ → 再按优先级挑一个存在的 ✓ → 兜底 cn_data ✓。

    ⚠ 读盘一律用 **`utf-8-sig`**：PowerShell 的 `Set-Content -Encoding UTF8` 会写 **BOM** ✗，
      用 `utf-8` 读会抛 `JSONDecodeError: Unexpected UTF-8 BOM` ⇒ 被 `except` 吞掉后**静默回落**
      到别的数据集 ✗✗（2026-10-09 实测踩到 ✓）。这里的 `except` 是"文件坏了也别让服务起不来" ✓，
      但**不能**让它变成"悄悄换数据集" ✗ ⇒ 容错要往"读得进去"的方向做，而不是"猜一个" ✗。
    """
    try:
        if ACTIVE_FILE.exists():
            name = (json.loads(ACTIVE_FILE.read_text(encoding="utf-8-sig")) or {}).get("name")
            if name and name in available():      # 只认"存在且口径可自证"的数据集 ✓
                return name
    except Exception:                                                     # noqa: BLE001
        pass
    av = available()
    return av[0] if av else "cn_data"


def active_dir() -> str:
    return str(dataset_dir(active_name()))


_PROBE_TTL = 20.0        # 秒；缓存最长寿命（配合 ?refresh=1 可立即刷新 ✓）


def _mtime(name: str) -> float:
    """缓存键：日历文件的 mtime ✓（**注意它不足以侦测"新增字段"** ✗）。

    ⚠ 2026-10-10 踩到：后台作业补 `fin_*` / 重算 `chip_*` 时，改的是 `features/<code>/` 这些
      **子目录**的 mtime ✗，而这里看的是 `calendars/day.txt` ⇒ 缓存**永不失效** ⇒ 补完米筐财务后
      接口仍报"74 字段 / fin=False" ✗✗（界面与实际数据长期不符 ✗）。故再加 TTL + `?refresh=1` ✓。
    """
    try:
        d = dataset_dir(name)
        t = (d / "calendars" / "day.txt").stat().st_mtime
        try:
            # 顺带把 `features/` 目录本身的 mtime 并进去（新增/删除股票目录时能立刻察觉 ✓）
            t = max(t, (d / "features").stat().st_mtime)
        except Exception:                                                 # noqa: BLE001
            pass
        return t
    except Exception:                                                     # noqa: BLE001
        return 0.0


def probe(name: str, use_cache: bool = True) -> dict:
    """数据集体检信息（前端展示 / 切换前确认用 ✓）：日历范围、股票数、字段数、缺哪些类字段 ✓。"""
    mt = _mtime(name)
    if use_cache and name in _PROBE_CACHE:
        ts, mt0, info0 = _PROBE_CACHE[name]
        if mt0 == mt and (time.time() - ts) < _PROBE_TTL:
            return dict(info0)
    d = dataset_dir(name)
    reg = REGISTRY.get(name) or {}
    mk = read_marker(name)                                 # 标记文件可覆盖显示名/说明 ✓
    info: dict = {
        "name": name,
        "dir": str(d),
        "exists": (d / "calendars" / "day.txt").exists(),
        "label": str(mk.get("label") or reg.get("label") or name),        # ★ 前端**收起时只显示这个短标签** ✓
        "convention": str(mk.get("convention") or reg.get("convention") or ""),
        "declared_convention": declared_convention(name) or "",           # 目录自报口径（标记 ✓）
        "note": str(mk.get("note") or reg.get("note") or ""),
        "only_price": bool(reg.get("only_price")),
    }
    cal_days = codes = fields = 0
    first = last = ""
    has_fin = has_mf = has_chip = False
    if info["exists"]:
        try:
            cal = (d / "calendars" / "day.txt").read_text(encoding="utf-8").split()
            cal_days, first, last = len(cal), (cal[0] if cal else ""), (cal[-1] if cal else "")
        except Exception:                                                 # noqa: BLE001
            pass
        feats = d / "features"
        if feats.is_dir():
            # ⚠ 字段数要**跨全表均匀抽样后取并集** ✓：字段集**逐股不同**（新股/退市/北交所少很多 ✗），
            #   只数开头那几只 bj 票会把 93 字段的数据集报成 "22~35 字段" ✗（2026-10-09 两次踩到并修 ✓）。
            subs = [s for s in sorted(feats.iterdir()) if s.is_dir()]
            codes = len(subs)
            union: set = set()
            best = 0
            step = max(1, len(subs) // 30)
            for sub in subs[::step][:30]:
                # ⚠ 必须**去掉 `.day.bin` 后缀** ✓：标记文件里的 `fields_baseline` 存的是"字段名"
                #   （如 `close` ✓），带后缀去比差集会把每个字段都算成"多出" ✗（2026-10-10 实测 ✓）。
                names = {p.name.replace(".day.bin", "") for p in sub.glob("*.bin")}
                union |= names
                best = max(best, len(names))
            fields = max(best, len(union))
            has_fin = any(n.startswith("fin_") for n in union)
            has_mf = any(n.startswith("mf_") for n in union)
            has_chip = any(n.startswith("chip_") for n in union)
    info.update({"calendar_days": cal_days, "calendar_first": first, "calendar_last": last,
                 "codes": codes, "fields": fields,
                 "has_fin": has_fin, "has_mf": has_mf, "has_chip": has_chip})
    # ★ 字段基准对比 ⇒ **只提示、不报错** ✓（用户要求：同事往 cn_data 里 dump 一堆字段，随便他们 ✓，
    #   只要提示"与基准不一致"即可 ✓）。基准来自该目录自己的标记文件 `fields_baseline` ✓。
    base = mk.get("fields_baseline")
    if isinstance(base, list) and base:
        base_set = {str(x) for x in base}
        extra = sorted(union - base_set) if info["exists"] else []
        miss = sorted(base_set - union) if info["exists"] else []
        info["fields_extra"] = extra[:60]
        info["fields_extra_count"] = len(extra)
        info["fields_missing"] = miss[:60]
        info["fields_missing_count"] = len(miss)
        info["baseline_count"] = len(base_set)
        if extra or miss:
            info["field_note"] = ("与基准字段不一致（仅提示，不影响使用）：多 %d 个%s、缺 %d 个%s"
                                  % (len(extra), ("（如 " + "、".join(extra[:3]) + "）") if extra else "",
                                     len(miss), ("（如 " + "、".join(miss[:3]) + "）") if miss else ""))
        else:
            info["field_note"] = "字段与基准一致（%d 个）" % len(base_set)
    _PROBE_CACHE[name] = (time.time(), mt, dict(info))
    return info


def status(refresh: bool = False) -> dict:
    """给前端的整体状态：可选项 + 当前项 + 体检 ✓（`refresh=True` ⇒ 体检绕过缓存 ✓）。"""
    av = available()
    cur = active_name()
    return {"active": cur, "datasets": [probe(n, use_cache=not refresh) for n in av],
            "switchable": len(av) > 1}


def clear_caches() -> Dict[str, bool]:
    """清掉各模块的**进程级缓存**（它们会把 provider 目录/日历快照住 ✗ ⇒ 切换后必须清 ✓）。"""
    done: Dict[str, bool] = {}
    try:
        from ..factors import panel_expr
        panel_expr.reset_caches()
        done["panel_expr"] = True
    except Exception:                                                     # noqa: BLE001
        done["panel_expr"] = False
    try:
        from ..engine import limits as _limits
        _limits._tag_field_cache.clear()
        done["limits"] = True
    except Exception:                                                     # noqa: BLE001
        done["limits"] = False
    try:                                                                  # 特征面板缓存按目录 mtime 失效 ✓
        from ..engine import feature_cache
        if hasattr(feature_cache, "invalidate_all"):
            feature_cache.invalidate_all()
        done["feature_cache"] = True
    except Exception:                                                     # noqa: BLE001
        done["feature_cache"] = False
    try:                                                                  # 事件研究缓存按内容寻址 ✓
        from ..factors import event_study_cache
        if hasattr(event_study_cache, "clear"):
            event_study_cache.clear()
        done["event_study_cache"] = True
    except Exception:                                                     # noqa: BLE001
        done["event_study_cache"] = False
    return done


def activate(name: str, allow_while_running: bool = False) -> dict:
    """切到某个数据集：落盘 ✓ → 改 `config.QLIB_PROVIDER_URI` ✓ → 重新 init qlib ✓ → 清缓存 ✓。

    ⚠ 切换是**全局**的：正在跑的回测/单因子任务会读到新数据 ✗ ⇒ 默认先探测任务占用 ✓，
      `allow_while_running=False` 时若检测到在跑就**拒绝**（由 UI 让用户确认后重试 ✓）。
    """
    if name not in REGISTRY:
        raise ValueError("未知数据集：%s（可选 %s）" % (name, ", ".join(REGISTRY)))
    if name not in available():
        raise FileNotFoundError("数据集目录不存在或缺日历：%s" % dataset_dir(name))
    running = _running_tasks()
    if running and not allow_while_running:
        raise RuntimeError("有正在运行的任务（%s）⇒ 现在切换会让它们读到新数据；"
                           "请等它跑完，或确认后用 allow_while_running=true 强制切换" % running)
    with _LOCK:
        target = str(dataset_dir(name))
        ACTIVE_FILE.parent.mkdir(parents=True, exist_ok=True)
        tmp = ACTIVE_FILE.with_suffix(".json.tmp")
        tmp.write_text(json.dumps({"name": name, "dir": target,
                                   "updated": time.strftime("%Y-%m-%d %H:%M:%S")},
                                  ensure_ascii=False, indent=2), encoding="utf-8")
        os.replace(tmp, ACTIVE_FILE)                                      # 原子 ✓
        from .. import config
        config.QLIB_PROVIDER_URI = target                                 # ① 所有直接读它的模块自动跟随 ✓
        os.environ["QLIB_PROVIDER_URI"] = target                          # ② 兼容按 env 读的脚本 ✓
        from .qlib_runtime import reset_qlib_init
        reset_qlib_init(target)                                           # ③ 重注册 qlib providers ✓
        cleared = clear_caches()                                          # ④ 清进程级缓存 ✓
    out = probe(name, use_cache=False)
    out["cleared"] = cleared
    return out


def _running_tasks() -> str:
    """在跑的任务描述（"回测 3 个" / "" ✓）—— 探不到就当没有 ✓（不阻塞切换 ✓）。"""
    parts = []
    try:
        from ..engine import task_registry as _tr                     # type: ignore
        n = int(getattr(_tr, "running_count", lambda: 0)() or 0)
        if n:
            parts.append("回测 %d 个" % n)
    except Exception:                                                     # noqa: BLE001
        pass
    try:
        from ..factors import single_test as _st                      # type: ignore
        n = int(getattr(_st, "running_count", lambda: 0)() or 0)
        if n:
            parts.append("单因子 %d 个" % n)
    except Exception:                                                     # noqa: BLE001
        pass
    return "、".join(parts)
