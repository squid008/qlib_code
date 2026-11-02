# -*- coding: utf-8 -*-
r"""任务状态**只有一个内存源**（★ v2.15.2）。

## 为什么加这条
第三方评审把"任务状态"列为 **4 套并存**：
`engine/task_manager.py`、`routers/factors_state.py`、
`routers/factors_single_state.py`、`services/task_store.py`。

我逐项实测后**不成立** ✓ —— 它们是**四层不同职责**，不是四份复制：

| 模块 | 真实职责 | 有状态吗 |
|---|---|---|
| `services/task_store.py` | **落盘层**（save/load_all/delete/prune/clear + 按 status/progress/ts 去重 + 坏文件自愈）| 无内存态 ✓ |
| `routers/factors_state.py` | **内存态**（`_SFT_TASKS` / `_EST_TASKS` + 7 个锁 + 上限裁剪 + 锁外落盘）| ✅ 唯一的内存源 |
| `routers/factors_single_state.py` | **纯结构工厂**（56 行，只有 `new_task_state()` 返回 dict）| ❌ 无状态/无锁/无 IO |
| `engine/task_manager.py` | **并发闸门**（信号量 + loky 池回收 + 取消异常）| ❌ 不是状态存储 |

关键证据：`factors_single._SFT_TASKS is factors_state._SFT_TASKS` ⇒ **True** ✓
（单因子路由用的是**同一个 dict**，不是副本 ✓）。

## 这条测试钉住什么
1. 内存源**只有** `factors_state.py` 一处（`app/` 下模块级任务 dict 白名单）✓；
2. 两个路由模块必须引用**同一个对象**（不许 `dict(...)` 拷贝 ✗）；
3. 落盘只经 `task_store`（`factors_state` 里 sft/est 写入都调 `save` ✓，且启动 `load_all` + `prune` ✓）；
4. `factors_single_state` **不许**引入 `task_store` 或文件 IO（否则它就变成第 5 套 ✗）。

> 结论写在这里，是为了让下一个人**不必重新数一遍**，也避免"看着像重复就合并"把
> 分层拆坏 ✗（合并 `task_store` 进 `factors_state` = 把落盘和内存态耦在一起，
> 重启恢复/去重/自愈三件事会一起变难 ✓）。
"""
from __future__ import annotations

import io
import os
import re

BACKEND = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
APP = os.path.join(BACKEND, "app")

#: 允许出现"模块级任务 dict"的**唯一**位置（新增必须同时改文档 + 这里 ✓）
ALLOWED_STATE_FILES = {"app/routers/factors_state.py"}


def _read(rel: str) -> str:
    return io.open(os.path.join(BACKEND, rel.replace("/", os.sep)),
                   encoding="utf-8", errors="ignore").read()


class TestSingleMemorySource:
    def test_routers_share_the_same_dict_object(self):
        """⭐⭐ 两个路由模块引用**同一个** dict/锁对象（不是拷贝 ✗）。"""
        from app.routers import factors_single as FS
        from app.routers import factors_state as FST

        assert FS._SFT_TASKS is FST._SFT_TASKS, "单因子路由拿了副本 ✗（会变成第二份内存态）"
        assert FS._SFT_LOCK is FST._SFT_LOCK, "锁也不是同一个 ✗（会丢互斥）"

    def test_only_one_module_owns_task_dicts(self):
        """⭐⭐ `app/` 下模块级任务 dict **只许**出现在 `factors_state.py` ✓。"""
        pat = re.compile(
            r"^(_[A-Z0-9_]*(?:TASKS|TASK_STATE|STATE_[A-Z0-9_]*))\s*(?::[^=]+)?=\s*(\{\}|dict\(\)|OrderedDict\(\))",
            re.M,
        )
        found = {}
        for dp, _dn, fs in os.walk(APP):
            if "__pycache__" in dp:
                continue
            for f in sorted(fs):
                if not f.endswith(".py"):
                    continue
                p = os.path.join(dp, f)
                rel = os.path.relpath(p, BACKEND).replace("\\", "/")
                s = io.open(p, encoding="utf-8", errors="ignore").read()
                for m in pat.finditer(s):
                    found.setdefault(rel, []).append(m.group(1))
        extra = {k: v for k, v in found.items() if k not in ALLOWED_STATE_FILES}
        assert not extra, "这些文件也持有模块级任务状态 ✗：%s" % extra
        assert "app/routers/factors_state.py" in found, "factors_state 里没找到任务 dict ✗"

    def test_persistence_goes_through_task_store(self):
        """⭐ sft/est 写入都必须走 `task_store.save`（否则重启丢任务 ✓）。"""
        s = _read("app/routers/factors_state.py")
        for kind in ("sft", "est"):
            assert 'task_store.save("%s"' % kind in s, "`%s` 没有落盘 ✗" % kind
            assert 'task_store.load_all("%s")' % kind in s, "`%s` 启动恢复缺失 ✗" % kind
            assert 'task_store.prune("%s"' % kind in s, "`%s` 没有磁盘上限 ✗" % kind

    def test_factory_module_stays_stateless(self):
        """⭐⭐ `factors_single_state` 必须保持**纯工厂**（不许接 task_store / 文件 IO）✓。"""
        s = _read("app/routers/factors_single_state.py")
        code = "\n".join(ln.split("#")[0] for ln in s.splitlines())
        assert "task_store" not in code, "工厂模块接了 task_store ✗（会变成第 5 套）"
        assert "open(" not in code, "工厂模块做了文件 IO ✗"
        assert "Lock(" not in code, "工厂模块加锁了 ✗（它不该有状态）"

    def test_concurrency_gate_is_not_a_store(self):
        """⭐ `task_manager` 只管并发，不许它持有任务状态 dict ✓（职责边界）。"""
        s = _read("app/engine/task_manager.py")
        code = "\n".join(ln.split("#")[0] for ln in s.splitlines())
        assert not re.search(r"^_?[A-Z0-9_]*(TASKS|TASK_STATE)\s*(?::[^=]+)?=", code, re.M), \
            "task_manager 里出现了任务状态 dict ✗（它应该是并发闸门）"

    def test_task_store_api_is_complete(self):
        """⭐ 落盘层的五个 API 都在（应用/恢复/清理都靠它们 ✓）。"""
        from app.services import task_store as TS

        for api in ("save", "load_all", "delete", "prune", "clear"):
            assert hasattr(TS, api), "task_store 缺 %s ✗" % api
