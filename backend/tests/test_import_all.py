# -*- coding: utf-8 -*-
"""**全量导入守门**（★ v2.9.52，2026-10-19 ✓ —— 2.9.51 的 CI 红就是它该拦住的）。

## 为什么需要它

2.9.51 把 `_log_swallowed` **追加在文件末尾** ✗，而那一批处理的 `except OSError:` 有几十处
位于**模块级 try**（导入时就执行 ✓）——
本地 Windows 上那些分支**没触发** ✓（文件都在 ✓），CI 的 Linux 上触发了 ✓
（`FileNotFoundError: workdir/.install_id` ✓）⇒ 走进 except ⇒ `NameError: _log_swallowed` ✗
⇒ **13 个测试文件在收集阶段就 ERROR** ✓，而我本地全绿 ✗。

⇒ 这个用例把"**每个模块都 import 一次**"变成红灯 ✓ —— 导入期的问题**本地就能抓到** ✓，
不用等 CI（那次等了 6 分钟才发现 ✗）。

⚠ 它不是"重复跑 pytest"：普通用例只会 import **被测到的**模块 ✓；
这里覆盖 `app` 下**全部 82 个**模块 ✓（含从没被任何用例碰过的 ✓）。
"""
import importlib
import pkgutil

import pytest

import app


def _all_modules():
    return sorted(m.name for m in pkgutil.walk_packages(app.__path__, "app."))


def test_there_are_reasonably_many_modules():
    """自检 ✓ —— 如果这个数突然掉到个位数，说明 `walk_packages` 没扫到东西 ✗（假绿）。"""
    names = _all_modules()
    assert len(names) > 50, "只扫到 %d 个模块 ⇒ 扫描本身失效了 ✗" % len(names)


@pytest.mark.parametrize("mod", _all_modules())
def test_module_imports_cleanly(mod):
    """逐个 import ✓ —— **导入期**的 `NameError` / 循环 import / 语法错都会在这里红 ✓。"""
    try:
        importlib.import_module(mod)
    except Exception as e:                                        # noqa: BLE001
        pytest.fail("%s 导入失败 ✗：%r" % (mod, e))
