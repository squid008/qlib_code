# -*- coding: utf-8 -*-
"""任务状态**落盘**（★ P1-C-2，2026-10-18 ✓）。

## 为什么要有它（用户体感最痛的一条 ✗）

原先「单因子测试 / 事件研究」的任务表全在**进程内存**里 ✗（见 `routers/factors_state.py` 顶部约定 ✓）
⇒ **后端一重启，所有结果消失** ✓，前端只能看到「任务不存在或结果已清理」✗。
用户 2026-10-17 一天内遇到 **2 次** ✓（我每次改代码都要重启后端 ✓）——这正是本项目
"明明跑完了却要重跑一遍"的主要来源 ✓。

## 设计（每一条都是踩坑换来的 ✓）

- **一个任务一个文件** ✓：`WORK_DIR/tasks/<kind>/<task_id>.json`
  （不用一个大 JSON ✗ —— 大文件每次全量重写，中断就废 ✓）。
- **原子写** ✓：先写 `<name>.tmp` 再 `os.replace` ✓（Windows 上 `os.replace` 也是原子的 ✓）
  ⇒ 断电/并发时**不会读到半截 JSON** ✗（半截 JSON = 启动时整个库读不出来 ✗）。
- **只落白名单字段** ✓：`status / progress / ts / error / result / _req`。
  ⚠ `_evs`（事件 DataFrame）/ `_hk`（持仓索引）**不落盘** ✓ —— 它们本来是"给净值曲线复用的中间物"✓，
  内存治理时会被 `_trim` 清成 `None` ✓ ⇒ 恢复时**显式置 None** 就与"被 trim 过"完全一致 ✓，
  下游 `.get()` 拿 None 的老路径**一个字都不用改** ✓。
- **单文件超上限就不落** ✓（`_MAX_BYTES = 20MB`）⇒ 宁可不缓存，也不把磁盘写满 ✗。
- **读坏文件 ⇒ 删掉继续** ✓（一个坏文件不许拖垮整个启动 ✗）。
- **重启残留** ✓：进程被杀时任务还停在 `running` ✗ ⇒ 加载时统一改成 `failed` +
  「服务重启，任务已中断」✓（**不能**留着永远 0% 让用户干等 ✗）。
- 落盘失败**只记不抛** ✓（写盘是增强功能，不能反过来让主流程 500 ✗）。

## 不做什么（避免过度设计 ✓）

- 不做 TTL/自动过期 ✓（清理端点已有人工清理 ✓；按数量 `prune` 足够 ✓）；
- 不做多进程共享一致性 ✓（本机单进程部署 ✓；多 worker 时各写各的也不会互相破坏 ✓，只是可能看不到别人的 ✓）；
- 不做 sqlite ✓（一个任务一个文件更简单、可 `cat` 可删 ✓，且无需迁移 ✓）。
"""
from __future__ import annotations

import json
import os
import shutil

from ..config import WORK_DIR

#: 落盘根目录：`workdir/tasks/<kind>/<task_id>.json` ✓
_ROOT = os.path.join(WORK_DIR, "tasks")

#: 只落这些字段 ✓（其余一律丢弃 —— 见文件头说明 ✓）
_WHITELIST = ("status", "progress", "ts", "error", "result", "_req", "factor", "name",
              "created_at", "updated_at")

#: 单文件上限（超了不落 ✓）
_MAX_BYTES = 20 * 1024 * 1024

#: 每个 kind 最多保留多少个文件 ✓（按 mtime 淘汰最旧 ✓）
_DEFAULT_KEEP = 60

#: 上次写入的签名（避免"每次进度都重写一遍"✗ —— 一个任务能更新上百次 ✓）
_SIG: dict = {}


def _dir(kind: str) -> str:
    d = os.path.join(_ROOT, kind)
    os.makedirs(d, exist_ok=True)
    return d


def _path(kind: str, task_id: str) -> str:
    # 防路径穿越 ✗：task_id 只允许文件名安全字符 ✓（后端生成的 uuid 一定安全 ✓，但不赌 ✓）
    safe = "".join(ch for ch in str(task_id) if ch.isalnum() or ch in "-_")[:80]
    return os.path.join(_dir(kind), "%s.json" % safe)


def _pick(state: dict) -> dict:
    out = {}
    for k in _WHITELIST:
        if k in state:
            out[k] = state[k]
    # ⚠ 不落的中间物：显式写 None ✓（与"被 trim 过"一致 ✓，下游无需改动 ✓）
    out["_evs"] = None
    out["_hk"] = None
    return out


def save(kind: str, task_id: str, state: dict, *, force: bool = False) -> bool:
    """落盘一个任务 ✓（返回是否真的写了 ✓）。任何异常都**吞掉** ✓（写盘失败不拖垮主流程 ✗）。"""
    try:
        sig = (state.get("status"), state.get("progress"), state.get("ts"))
        key = (kind, str(task_id))
        if not force and _SIG.get(key) == sig and os.path.isfile(_path(kind, task_id)):
            return False                      # 状态没变 ⇒ 不重复写 ✓
        payload = json.dumps(_pick(state), ensure_ascii=False, default=str)
        if len(payload.encode("utf-8")) > _MAX_BYTES:
            return False                      # 太大 ⇒ 不落 ✓（宁可不缓存）
        p = _path(kind, task_id)
        tmp = p + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            f.write(payload)
        os.replace(tmp, p)                    # 原子 ✓
        _SIG[key] = sig
        return True
    except Exception:                                            # noqa: BLE001
        return False


def load_all(kind: str) -> dict:
    """加载某类任务 ✓（坏文件直接删掉 ✓，`running` 残留改成 `failed` ✓）。"""
    out: dict = {}
    try:
        d = _dir(kind)
    except Exception:                                            # noqa: BLE001
        return out
    for name in os.listdir(d):
        if not name.endswith(".json"):
            continue
        p = os.path.join(d, name)
        try:
            with open(p, encoding="utf-8") as f:
                st = json.load(f)
            if not isinstance(st, dict):
                raise ValueError("not a dict")
        except Exception:                                        # noqa: BLE001
            try:
                os.remove(p)                                     # 坏文件不许拖垮启动 ✓
            except Exception:                                    # noqa: BLE001
                pass
            continue
        # 重启残留 ⇒ 明确失败 ✓（不能让用户对着 0% 干等 ✗）
        if st.get("status") in ("queued", "running"):
            st["status"] = "failed"
            st["error"] = st.get("error") or "服务重启，任务已中断（结果未生成）"
            st["progress"] = int(st.get("progress") or 0)
        out[str(st.get("id") or name[:-5])] = st
    return out


def delete(kind: str, task_id: str) -> None:
    try:
        os.remove(_path(kind, task_id))
    except Exception:                                            # noqa: BLE001
        pass
    _SIG.pop((kind, str(task_id)), None)


def prune(kind: str, keep: int = _DEFAULT_KEEP) -> int:
    """只保留最近 `keep` 个文件 ✓（按 mtime ✓）。返回删掉几个 ✓。"""
    try:
        d = _dir(kind)
        files = [(os.path.getmtime(os.path.join(d, n)), n) for n in os.listdir(d)
                 if n.endswith(".json")]
    except Exception:                                            # noqa: BLE001
        return 0
    files.sort(reverse=True)
    n = 0
    for _, name in files[max(0, int(keep)):]:
        try:
            os.remove(os.path.join(d, name))
            n += 1
        except Exception:                                        # noqa: BLE001
            pass
    return n


def clear(kind: str) -> int:
    """清空某类任务（**仅测试/维护用** ✓）。返回删掉几个 ✓。"""
    try:
        d = _dir(kind)
        shutil.rmtree(d, ignore_errors=True)
        _SIG.clear()
        return 1
    except Exception:                                            # noqa: BLE001
        return 0
