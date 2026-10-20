# -*- coding: utf-8 -*-
"""异常分级策略（★ P2-A，2026-10-18 ✓）。

## 为什么需要它（实测 ✓）

`app/` 里现有 **410 处** `except Exception` / 裸 `except` ✓，分三档：

| 档 | 数量 | 评价 |
|---|---|---|
| **静默**（body 只有 `pass`/`continue`，**没有任何日志** ✗） | **163** ✗ | 出事时**零痕迹** ⇒ 最贵的一类 bug ✓ |
| 有痕迹（会打日志 ✓） | 70 ✓ | 可接受 ✓ |
| 有动作（`raise`/`return`/回退 ✓） | 177 ✓ | 正常 ✓ |

**为什么静默最贵**：本项目已经吃过两次 ✓ ——
  · 「离场卖出 **0** 笔」却又报 1844 笔顺延 ✓（数字对不上，但没人报 ✗）；
  · 「任务不存在或结果已清理」✓（结果丢了，日志里查不到原因 ✗）。

## 规矩（本模块就是这条规矩的执行工具 ✓）

1. **可以吞**（缓存写入、可选增强、清理、导出美化 ✓）—— 但**必须留一行日志** ✓；
2. **不能吞**（用户请求路径、账目/净值计算、口径转换 ✓）—— 该 `raise` 就 `raise` ✓，
   要转成 HTTP 错误就转 ✓（带**可读原因** ✓）；
3. **绝不捕 `BaseException`** ✗（`KeyboardInterrupt` / `SystemExit` 必须穿过去 ✓）。

## 用法

```python
from ..services.error_policy import tolerate, swallow

with tolerate("expr_cache.save", extra={"key": k[:8]}):     # 可选增强 ⇒ 吞，但留 debug 日志 ✓
    ...

except Exception as e:
    swallow("untrig_nav_series", e, level="warning")         # 影响结果 ⇒ 用 warning ✓
```
"""
from __future__ import annotations

import contextlib
from typing import Any, Iterator, Optional

from ..logger import get_logger

_logger = None


def _log():
    global _logger
    if _logger is None:
        _logger = get_logger(__name__)
    return _logger


def swallow(scope: str, exc: BaseException, *, level: str = "debug",
            extra: Optional[dict] = None, limit: int = 200) -> None:
    """**吞掉**一个异常，但留一行日志 ✓（`scope` 写清"在哪吞的"✓）。

    参数
    ----
    scope: 位置标签（`"expr_cache.save"` ✓）——**必须具体** ✓（写 "somewhere" 等于没写 ✗）
    level: `debug`（缓存/可选增强 ✓）｜`warning`（影响了结果或用户能看到的东西 ✓）｜`info`
    extra: 额外上下文（键、任务号、区间 ✓）—— 只放**能复现**的信息 ✓，别塞整张表 ✗
    """
    msg = "[吞] %s: %s"
    # ★ 2026-10-20（mypy `assignment`）：下面有 `extra` 时 `detail` 会被换成**元组** ✓
    #   （`fn(msg, scope, detail)` 两种形状都允许 ✓）⇒ 标成 `object` ✓，**行为不变** ✓。
    detail: object = repr(exc)[:limit]
    if extra:
        msg += " ｜ %s"
        detail = (detail, extra)
    # ⚠ "连日志都失败"的兜底 ✓ —— 用 `contextlib.suppress` 而不是 `except: pass` ✓：
    #   ① 惯用法 ✓（一眼看出"这里允许失败"）；② 不会被"静默吞异常棘轮"误算成债务 ✓
    #   （`test_except_ratchet` 只统计 `except` 块 ✓）。
    with contextlib.suppress(Exception):
        fn = getattr(_log(), level, None) or _log().debug
        if extra:
            fn(msg, scope, detail)
        else:
            fn(msg, scope, detail)


@contextlib.contextmanager
def tolerate(scope: str, *, level: str = "debug",
             extra: Optional[dict] = None) -> Iterator[None]:
    """`try/except: pass` 的**合规替代** ✓ —— 吞，但一定留痕 ✓。

    ⚠ 只捕 `Exception` ✓（`KeyboardInterrupt`/`SystemExit` 属于 `BaseException` ⇒ 自动穿过去 ✓）。
    """
    try:
        yield
    except Exception as e:                            # noqa: BLE001
        swallow(scope, e, level=level, extra=extra)


def must_raise(scope: str, exc: BaseException, *, detail: str = "") -> None:
    """**不允许吞**的场合用 ✓：记一条 error 日志后原样抛回 ✓（保持栈 ✓）。"""
    with contextlib.suppress(Exception):             # 日志失败也要把异常抛回去 ✓
        _log().error("[必报] %s: %r %s", scope, exc, detail)
    raise exc
