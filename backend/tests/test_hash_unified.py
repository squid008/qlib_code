# -*- coding: utf-8 -*-
"""哈希**统一入口**（★ v2.15.1，第三方评审「缓存键至少 4 份」的收敛）。

## 为什么加这条
`app/` 里原有 **9 处**真实代码调用 `hashlib.md5(...)`（另有 2 处是注释里提到）：
参数指纹（`artifacts.params_md5` / `market_stat` 公式指纹）、
缓存键（`feature_cache` / `event_study_cache` / `event` / `pricing`）、
符号短键（`codegen.formula_key`）。

它们共享三条硬要求，却各写各的 ⇒ 任何一处漏了 `usedforsecurity=False`、
或换了编码/截断，都会**只在一部分缓存上静默失效** ✗（最难查的那类问题）。
⇒ 已收敛到 `app/utils/hashutil.py`，本测试保证**不再长回去** ✓。

## 判据
1. `app/` 下 `hashlib.md5` **只许**出现在 `app/utils/hashutil.py` ✓；
2. `content_hash` 与"手写 md5 + 截断"**逐位等价**（含空串、多段、中文、截断长度）✓；
3. `new_hasher()` 的累积用法与 `content_hash(*parts)` **等价** ✓
   （因为 `event_study_cache` / `event` / `pricing` 仍是**累积式**写法 ✓）。
"""
from __future__ import annotations

import hashlib
import io
import os
import re

from app.utils.hashutil import content_hash, new_hasher

BACKEND = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
APP = os.path.join(BACKEND, "app")


def _md5(s: str) -> str:
    return hashlib.md5(s.encode("utf-8"), usedforsecurity=False).hexdigest()


class TestSingleEntryPoint:
    def test_md5_only_inside_hashutil(self):
        """⭐⭐ `app/` 下除了 `utils/hashutil.py`，**不许**再出现 `hashlib.md5` ✓。"""
        bad = []
        for dp, _dn, fs in os.walk(APP):
            if "__pycache__" in dp:
                continue
            for f in fs:
                if not f.endswith(".py"):
                    continue
                p = os.path.join(dp, f)
                rel = os.path.relpath(p, BACKEND).replace("\\", "/")
                if rel == "app/utils/hashutil.py":
                    continue
                src = io.open(p, encoding="utf-8", errors="ignore").read()
                # 只看代码行（去掉注释，避免"注释里提到 md5"被误判 ✓）
                for i, ln in enumerate(src.splitlines(), 1):
                    code = ln.split("#")[0]
                    if "hashlib.md5" in code:
                        bad.append("%s:%d" % (rel, i))
        assert not bad, "这些地方绕过了统一入口 ✗：%s" % bad[:8]


class TestByteIdentical:
    """⭐⭐ **逐位等价** —— 改这里等于让几十 GB 缓存静默失效 ✗。"""

    def test_known_values(self):
        assert _md5("abc") == "900150983cd24fb0d6963f7d28e17f72"
        assert content_hash("abc", n=32) == _md5("abc")
        assert content_hash("abc", n=16) == _md5("abc")[:16]
        assert content_hash("abc", n=6) == _md5("abc")[:6]

    def test_single_part_matches_raw_md5(self):
        for s in ("", "a", "中文表达式", "SH600000|2020-01-01|2020-12-31", "x" * 500):
            assert content_hash(s, n=32) == _md5(s), s

    def test_multi_part_equals_join(self):
        """`content_hash(a, b, c)` ≡ `md5(a+b+c)` ✓（累积 update 就是拼接 ✓）。"""
        a, b, c = "|fill=t1_open", "|src=deadbeef", "|dv=20260101"
        assert content_hash(a, b, c, n=32) == _md5(a + b + c)

    def test_hasher_streaming_equals_content_hash(self):
        h = new_hasher()
        for seg in ("|a=1", "|b=2", "|src=xyz"):
            h.update(seg.encode("utf-8"))
        assert h.hexdigest()[:32] == content_hash("|a=1", "|b=2", "|src=xyz", n=32)

    def test_truncation_lengths_used_in_repo(self):
        """⭐ 仓库里实际用到的截断长度（16/24/32/12/6）都支持且互不相同 ✓。"""
        s = "sample"
        full = _md5(s)
        for n in (6, 12, 16, 24, 32):
            assert content_hash(s, n=n) == full[:n]

    def test_no_direct_hashlib_in_engine_factors_signals(self):
        """⭐ 抽查三个高危目录：一处 `hashlib.md5(` 都不许有 ✓（防"新代码又绕过去"）。"""
        for sub in ("engine", "factors", "signals"):
            d = os.path.join(APP, sub)
            for dp, _dn, fs in os.walk(d):
                if "__pycache__" in dp:
                    continue
                for f in fs:
                    if not f.endswith(".py"):
                        continue
                    src = io.open(os.path.join(dp, f), encoding="utf-8", errors="ignore").read()
                    for ln in src.splitlines():
                        assert "hashlib.md5" not in ln.split("#")[0], \
                            "%s/%s 里还有直接 md5 ✗" % (sub, f)
