# -*- coding: utf-8 -*-
"""数据集切换的**路径跟随**守卫（2026-10-10 加，用户实测踩到"切换没生效"后补）。

背景（代价很大的一次事故 ✗）：
- 机器上有个**用户级**环境变量 `QLIB_PROVIDER_URI=data\\cn_data`，而 `app/config.py` 旧逻辑
  是"**环境变量优先**" ✗ ⇒ 界面里切到 `cn_data2` 后，新起的进程/子进程又回到 `cn_data`
  ⇒ 切换"看着生效、实际没生效"，用户的"两个数据源对照"因此得出**结果一模一样**的假象 ✗✗
  （实测：单因子测试面板链读的始终是 cn_data ✓）。
- 另一类同源坑：模块里 `from ..config import QLIB_PROVIDER_URI` 是 **import 期快照** ✗
  ⇒ 切数据集后该模块仍指向旧目录（`feature_cache` 就曾如此 ✗）。

本测试钉住两件事：
1. **环境变量不能盖过落盘的"当前数据集"**（除非显式 `QLIB_PROVIDER_URI_FORCE=1`）——
   用子进程实跑 `app.config` 验证（import 期行为只能这么测 ✓）；没有 `data/active_dataset.json`
   的机器（CI）自动跳过 ✓；
2. `panel_expr._feature_dir()` 与 `feature_cache.data_version_stamp()` 必须**动态跟随**
   `app.config.QLIB_PROVIDER_URI`（改了就跟着变 ✓，不许有 import 期快照 ✗）。
"""
import json
import os
import subprocess
import sys

import pytest

_BACKEND = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
_ROOT = os.path.abspath(os.path.join(_BACKEND, ".."))
_ACTIVE = os.path.join(_ROOT, "data", "active_dataset.json")


def _active_dir():
    try:
        with open(_ACTIVE, encoding="utf-8-sig") as fh:
            d = (json.load(fh) or {}).get("dir")
        return d if (d and os.path.isdir(d)) else None
    except Exception:                                    # noqa: BLE001
        return None


def test_env_var_does_not_override_active_dataset():
    """★ 环境变量与 active_dataset.json 冲突时，**以文件为准** ✓（除非 *_FORCE=1 ✓）。"""
    want = _active_dir()
    if not want:
        pytest.skip("本机没有 data/active_dataset.json（CI 无数据 ✓）⇒ 该优先级无从验证")
    env = dict(os.environ)
    env["QLIB_PROVIDER_URI"] = os.path.join(_ROOT, "data", "__bogus_env_dir__")
    env.pop("QLIB_PROVIDER_URI_FORCE", None)
    out = subprocess.run([sys.executable, "-c",
                          "import app.config as c; print(c.QLIB_PROVIDER_URI);"
                          " print(c.QLIB_PROVIDER_URI_SOURCE)"],
                         cwd=_BACKEND, env=env, capture_output=True, timeout=120,
                         encoding="utf-8", errors="replace")
    lines = [x.strip() for x in (out.stdout or "").splitlines() if x.strip()]
    assert lines, "子进程没有输出：%s" % (out.stderr or "")
    assert os.path.normcase(os.path.normpath(lines[0])) == os.path.normcase(os.path.normpath(want)), (
        "环境变量盖过了 active_dataset.json ⇒ 界面切数据集会‘看似生效、实际没生效’ ✗\n"
        "  实际生效=%s\n  应为=%s" % (lines[0], want))
    assert lines[-1].startswith("active_dataset"), "来源应为 active_dataset.json：%s" % lines

    # FORCE=1 时环境变量优先（容器/CI 显式指定数据根的场景 ✓）
    env["QLIB_PROVIDER_URI_FORCE"] = "1"
    out2 = subprocess.run([sys.executable, "-c", "import app.config as c; print(c.QLIB_PROVIDER_URI)"],
                          cwd=_BACKEND, env=env, capture_output=True, timeout=120,
                          encoding="utf-8", errors="replace")
    assert "__bogus_env_dir__" in (out2.stdout or ""), "FORCE=1 时环境变量应当优先：%s" % out2.stdout


def test_panel_feature_dir_follows_config(monkeypatch):
    """★ 面板的取数字段目录必须**动态**跟随 config（切数据集后立刻换目录 ✓）。"""
    import app.config as C
    from app.factors import panel_expr

    fake = os.path.join(_ROOT, "data", "__fake_ds__")
    monkeypatch.setattr(C, "QLIB_PROVIDER_URI", fake)
    panel_expr.reset_caches()
    assert os.path.normcase(panel_expr._feature_dir()) == os.path.normcase(fake + os.sep + "features")
    # 再换一个 ⇒ 仍要跟着变（不许只在首次解析时取值 ✗）
    fake2 = os.path.join(_ROOT, "data", "__fake_ds2__")
    monkeypatch.setattr(C, "QLIB_PROVIDER_URI", fake2)
    assert os.path.normcase(panel_expr._feature_dir()) == os.path.normcase(fake2 + os.sep + "features")
    panel_expr.reset_caches()


def test_feature_cache_paths_follow_config(monkeypatch, tmp_path):
    """★ `feature_cache` 的数据集路径/戳文件也必须动态跟随（旧的 import 期快照会算错缓存键 ✗）。"""
    import app.config as C
    from app.engine import feature_cache as fc

    ds_a, ds_b = tmp_path / "ds_a", tmp_path / "ds_b"
    for d, mark in ((ds_a, "AAA"), (ds_b, "BBB")):
        d.mkdir()
        (d / ".data_version").write_text("12345\t%s" % mark, encoding="utf-8")
    monkeypatch.setattr(C, "QLIB_PROVIDER_URI", str(ds_a))
    assert os.path.normcase(fc.data_version_stamp()) == os.path.normcase(
        os.path.join(str(ds_a), ".data_version"))
    v_a = fc._data_version()
    monkeypatch.setattr(C, "QLIB_PROVIDER_URI", str(ds_b))
    v_b = fc._data_version()
    assert "AAA" in v_a and "BBB" not in v_a, "数据版本号没跟着换数据集 ⇒ 缓存键会串数据 ✗：%s" % v_a
    assert "BBB" in v_b, "换数据集后数据版本号应当来自新目录 ✗：%s" % v_b
