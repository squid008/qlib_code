# -*- coding: utf-8 -*-
"""**统一的哈希入口**（★ v2.15.1，第三方评审「缓存键至少 4 份」的收敛）。

## 为什么要有它
`app/` 里原本有 **11 处** `hashlib.md5(...)`，按语义分三类：
| 语义 | 例子 | 截断 |
|---|---|---|
| 参数/内容指纹 | `artifacts.params_md5`、`market_stat` 公式指纹 | 16 / 12 |
| 缓存键 | `feature_cache`、`event_study_cache`、`event`、`pricing` | 24 / 32 / 32 / 32 |
| 符号短键 | `codegen.formula_key` | 6 |

它们**不是同一份代码的复制**，但共享三条硬要求：
1. **稳定**（同输入跨进程/跨机器同输出 ✓ —— 否则缓存永远不命中 ✗）；
2. **非加密用途**（必须带 `usedforsecurity=False` ✓，否则 FIPS 环境直接抛 ✗）；
3. **字节序列固定**（改动即让几十 GB 缓存**静默失效** ✗）。

⇒ 收敛到这里，任何人改"算法/编码/截断"只需改一处 ✓，也避免"某处漏了
`usedforsecurity=False`"这种只在特定机器上炸的坑 ✓。

## ⚠ 铁律：**字节级不变**
`content_hash(*parts, n=)` 与旧的 `md5("".join(parts)).hexdigest()[:n]` **逐位相同** ✓；
`new_hasher()` 与旧的手写 `hashlib.md5(usedforsecurity=False)` **完全等价** ✓。
改这两条等于**让 1.6 GB 特征缓存 + 620 MB 表达式缓存全部失效** ✓（技术债里记过这类事故）。
"""
from __future__ import annotations

import hashlib


def new_hasher():
    """新建一个**非加密用途**的 md5 累积器 ✓（等价于 `hashlib.md5(usedforsecurity=False)`）。"""
    return hashlib.md5(usedforsecurity=False)


def content_hash(*parts, n: int = 16) -> str:
    """把若干段按**给定顺序**拼起来算 md5，返回前 `n` 位 ✓（等价于 `"".join(parts)` 后算）。

    ⚠ 顺序即语义 ✓ —— 调用方别乱调换（会变成另一个缓存键 ✗）。
    """
    h = new_hasher()
    for p in parts:
        h.update(p.encode("utf-8") if isinstance(p, str) else bytes(p))
    return h.hexdigest()[:n]
