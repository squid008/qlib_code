# -*- coding: utf-8 -*-
"""多数据集（口径）切换的守卫测试（用户 2026-10-09：三套数据来回切对照 ✓）。

为什么值得钉（都是"静默出错"的类型 ✗）：
1. `active_dataset.json` 落盘 + `config.py` 导入时读回 ⇒ **joblib worker 子进程**才能跟随 ✓，
   只改内存变量的话，回测 worker 会悄悄读回旧数据集 ✗✗。
2. 切换必须**清进程级缓存**（panel_expr 的 `_CAL/_FEATURE_DIR/*_CACHE` ✓）—— 不清就是
   "切了目录但继续读旧 bin"，而且不报错 ✗ ⇒ 对照实验全废 ✗。
3. 有回测在跑时默认**拒绝**切换 ✓（否则跑到一半换数据 ✗）。
"""
from __future__ import annotations

import json
import threading

import pytest

from app.services import datasets as D


# 各数据集**默认**应有的口径标记（`convention="auto"` 时按名字取 ✓；显式给 None ⇒ 故意不写标记 ✓）
DEFAULT_CONV = {"cn_data": "ricequant", "cn_data2": "tushare", "cn_data3": "qlib"}


def _make_dataset(root, name, days, fields=("close", "factor"), codes=("sz000001",),
                  convention="auto", baseline=None):
    """造一个最小 provider：calendars/day.txt + features/<code>/<field>.day.bin（+ 标记文件 ✓）。"""
    base = root / name
    (base / "calendars").mkdir(parents=True, exist_ok=True)
    (base / "calendars" / "day.txt").write_text("\n".join(days) + "\n", encoding="utf-8")
    for code in codes:
        d = base / "features" / code
        d.mkdir(parents=True, exist_ok=True)
        for f in fields:
            (d / ("%s.day.bin" % f)).write_bytes(b"\x00" * 8)
    conv = DEFAULT_CONV.get(name) if convention == "auto" else convention
    if conv is not None:
        marker = {"name": name, "convention": conv, "label": "%s 口径" % conv}
        if baseline is not None:
            marker["fields_baseline"] = list(baseline)
        (base / D.MARKER_NAME).write_text(json.dumps(marker, ensure_ascii=False), encoding="utf-8")
    return base


@pytest.fixture()
def fake_data(tmp_path, monkeypatch):
    """把 DATA/ACTIVE_FILE 指到临时目录，避免碰真实数据 ✓。

    ⚠ 还必须 monkeypatch `config.QLIB_PROVIDER_URI` ✓：`activate()` 会**直接写这个模块全局**（生产上
      就是这样让同进程的读者立刻生效的 ✓），但测试若不还原，跑完本文件后它仍指向**已删除的 tmp 目录**
      ✗✗ ⇒ 后面的 `test_panel_expr.py` / `test_signal_pool_wide.py` 全崩 ✗（2026-10-10 实测：
      单跑能过、全量 9 项挂 ✗）。monkeypatch 会在 teardown 还原**原值** ⇒ 无论测试中间被写成什么 ✓。
    """
    from app import config
    data = tmp_path / "data"
    data.mkdir()
    monkeypatch.setattr(D, "DATA", data)
    monkeypatch.setattr(D, "ACTIVE_FILE", data / "active_dataset.json")
    monkeypatch.setattr(config, "QLIB_PROVIDER_URI", config.QLIB_PROVIDER_URI)
    yield data
    # teardown 时顺手把可能被写脏的全局复位 ✓（monkeypatch 也会再兜一层 ✓）
    try:
        import importlib
        importlib.reload(config)
    except Exception:                                                     # noqa: BLE001
        pass


def test_registry_covers_three_conventions():
    """三套口径都要登记：米筐/qlib、tushare（主）、qlib 官方原始（**仅行情** ✓）。"""
    assert set(D.REGISTRY) == {"cn_data", "cn_data2", "cn_data3"}
    assert D.REGISTRY["cn_data3"].get("only_price") is True      # UI 要提示"仅行情字段" ✓
    assert D._ORDER[0] == "cn_data2"                             # 默认主数据集 = tushare 口径 ✓


def test_available_and_active_fallback(fake_data, monkeypatch):
    """只有存在的数据集才算可选 ✓；没有落盘记录时按优先级挑 ✓（cn_data2 > cn_data > cn_data3）。"""
    _make_dataset(fake_data, "cn_data3", ["2026-09-04"])
    monkeypatch.setattr(D, "_PROBE_CACHE", {})
    assert D.available() == ["cn_data3"]
    assert D.active_name() == "cn_data3"                          # 只有这一套 ⇒ 就是它 ✓
    _make_dataset(fake_data, "cn_data", ["2026-08-21"])
    assert D.available() == ["cn_data", "cn_data3"]
    assert D.active_name() == "cn_data"                           # 优先级高于 cn_data3 ✓


def test_probe_reports_calendar_codes_fields(fake_data, monkeypatch):
    """体检要给前端够用的信息：日历范围、股票数、字段数、缺哪些类字段 ✓。"""
    _make_dataset(fake_data, "cn_data2", ["2026-10-08", "2026-10-09"],
                  fields=("close", "factor", "fin_pe_ttm", "chip_cost_5"), codes=("sz000001", "sh600000"))
    monkeypatch.setattr(D, "_PROBE_CACHE", {})
    info = D.probe("cn_data2")
    assert info["exists"] and info["calendar_days"] == 2
    assert info["calendar_first"] == "2026-10-08" and info["calendar_last"] == "2026-10-09"
    assert info["codes"] == 2 and info["fields"] == 4
    assert info["has_fin"] and info["has_chip"] and not info["has_mf"]


def test_activate_writes_file_and_reinitializes(fake_data, monkeypatch):
    """切换三件事必须都做：① 落盘 ✓ ② 让 qlib 重新 init ✓ ③ 清缓存 ✓。"""
    _make_dataset(fake_data, "cn_data2", ["2026-10-09"])
    _make_dataset(fake_data, "cn_data3", ["2026-09-04"])
    monkeypatch.setattr(D, "_PROBE_CACHE", {})
    called = {}
    monkeypatch.setattr("app.services.qlib_runtime.reset_qlib_init",
                        lambda uri=None: called.setdefault("init", uri))
    monkeypatch.setattr(D, "clear_caches", lambda: called.setdefault("cleared", True) or {"x": True})

    info = D.activate("cn_data3")
    assert info["name"] == "cn_data3"
    assert called.get("cleared") is True
    assert called.get("init") == str(fake_data / "cn_data3")      # 新 provider 传进 qlib ✓
    saved = json.loads((fake_data / "active_dataset.json").read_text(encoding="utf-8"))
    assert saved["name"] == "cn_data3"                            # 落盘 ⇒ 子进程能跟随 ✓
    from app import config
    assert config.QLIB_PROVIDER_URI == str(fake_data / "cn_data3")
    assert D.active_name() == "cn_data3"


def test_activate_rejects_unknown_and_missing(fake_data, monkeypatch):
    """未知名字 / 目录不存在 ⇒ 明确报错 ✓（绝不"静默不切" ✗）。"""
    monkeypatch.setattr(D, "_PROBE_CACHE", {})
    with pytest.raises(ValueError):
        D.activate("cn_data9")
    with pytest.raises(FileNotFoundError):
        D.activate("cn_data2")                                    # 目录还没造 ✓


def test_activate_refuses_while_tasks_running(fake_data, monkeypatch):
    """有任务在跑 ⇒ 默认拒绝 ✓；显式 force 才允许 ✓（避免"跑到一半换数据" ✗）。"""
    _make_dataset(fake_data, "cn_data2", ["2026-10-09"])
    monkeypatch.setattr(D, "_PROBE_CACHE", {})
    monkeypatch.setattr(D, "_running_tasks", lambda: "回测 2 个")
    monkeypatch.setattr("app.services.qlib_runtime.reset_qlib_init", lambda uri=None: None)
    monkeypatch.setattr(D, "clear_caches", lambda: {})
    with pytest.raises(RuntimeError) as ei:
        D.activate("cn_data2")
    assert "运行" in str(ei.value)
    assert D.activate("cn_data2", allow_while_running=True)["name"] == "cn_data2"


def test_active_name_tolerates_utf8_bom(fake_data, monkeypatch):
    """★ 读盘必须容忍 BOM（实测踩到的静默坑 ✗）。

    PowerShell 的 `Set-Content -Encoding UTF8` 会写 **BOM** ✗；用 `encoding="utf-8"` 读会抛
    `JSONDecodeError: Unexpected UTF-8 BOM` ⇒ 被 `except` 吞掉后**静默回落**到另一个数据集 ✗✗
    —— 用户以为切到了 A、实际读的是 B，而且**不报错** ✗。修法：一律 `utf-8-sig` ✓。
    """
    _make_dataset(fake_data, "cn_data2", ["2026-10-09"])
    _make_dataset(fake_data, "cn_data3", ["2026-09-04"])
    (fake_data / "active_dataset.json").write_bytes(
        b"\xef\xbb\xbf" + json.dumps({"name": "cn_data3"}).encode("utf-8"))
    monkeypatch.setattr(D, "_PROBE_CACHE", {})
    assert D.active_name() == "cn_data3"


def test_unmarked_or_mismatched_dir_is_hidden(fake_data, monkeypatch):
    """★ 口径必须靠**目录里的标记文件自证** ✓（用户 2026-10-10 要求）。

    场景：同事自己建了 `cn_data2`，里面放的是**他自己的数据**（不是 tushare ✗）。
    这时绝不能把它当"tushare 口径"端出来 ✗ ⇒ 没标记 / 标记不匹配 ⇒ **不显示** ✓。
    """
    monkeypatch.setattr(D, "_PROBE_CACHE", {})
    # ① 完全没标记
    _make_dataset(fake_data, "cn_data2", ["2026-10-09"], convention=None)
    assert D.available() == []                                    # cn_data2 不显示、cn_data 也还没造 ✓
    # ② 标记写的是别的口径
    _make_dataset(fake_data, "cn_data2", ["2026-10-09"], convention="ricequant")
    assert "cn_data2" not in D.available()
    # ③ 标记写对了 ⇒ 才显示 ✓
    _make_dataset(fake_data, "cn_data2", ["2026-10-09"], convention="tushare")
    assert D.available() == ["cn_data2"]


def test_cn_data_shows_without_marker_and_extra_dataset_needs_known_convention(fake_data, monkeypatch):
    """`cn_data` 是基线：只要有目录就显示 ✓（同事往里 dump 字段也照常 ✓）；
    其它 `cn_dataN` 则需要**已知口径**的标记才显示 ✓（方便同事挂自己的数据集 ✓）。"""
    monkeypatch.setattr(D, "_PROBE_CACHE", {})
    _make_dataset(fake_data, "cn_data", ["2026-08-21"], convention=None)
    _make_dataset(fake_data, "cn_data4", ["2026-10-09"], convention="unknown-vendor")
    assert D.available() == ["cn_data"]                           # cn_data4 口径不认识 ⇒ 不显示 ✓
    _make_dataset(fake_data, "cn_data4", ["2026-10-09"], convention="tushare")
    assert D.available() == ["cn_data", "cn_data4"]               # 认了 ⇒ 显示（顺序按 _ORDER：注册的在前 ✓）


def test_field_baseline_mismatch_is_only_a_hint(fake_data, monkeypatch):
    """★ 字段与基准不一致 ⇒ **只提示、不报错** ✓（用户明确要求：同事随意 dump 字段 ✓）。"""
    _make_dataset(fake_data, "cn_data", ["2026-08-21"],
                  fields=("close", "factor", "fin_pe_ttm", "their_own_field"),
                  baseline=("close", "factor"))                   # 基准只有 2 个 ⇒ 多出 2 个 ✓
    monkeypatch.setattr(D, "_PROBE_CACHE", {})
    info = D.probe("cn_data")                                     # 绝不抛异常 ✓
    assert info["baseline_count"] == 2
    assert info["fields_extra_count"] == 2
    assert info["fields_missing_count"] == 0
    assert "不一致" in info["field_note"] and "仅提示" in info["field_note"]
    # 基准一致时给"一致"提示 ✓
    _make_dataset(fake_data, "cn_data", ["2026-08-21"], fields=("close", "factor"),
                  baseline=("close", "factor"))
    monkeypatch.setattr(D, "_PROBE_CACHE", {})
    assert "一致" in D.probe("cn_data")["field_note"]


def test_marker_label_overrides_registry_and_bom_tolerated(fake_data, monkeypatch):
    """标记里的 `label` 覆盖注册表默认值 ✓（前端**收起时只显示这个短标签** ✓）；带 BOM 也要能读 ✓。"""
    import os as _os
    base = _make_dataset(fake_data, "cn_data2", ["2026-10-09"], convention="tushare")
    (base / D.MARKER_NAME).write_bytes(
        b"\xef\xbb\xbf" + json.dumps({"convention": "tushare", "label": "我自己的标签"},
                                     ensure_ascii=False).encode("utf-8"))
    monkeypatch.setattr(D, "_PROBE_CACHE", {})
    assert D.probe("cn_data2")["label"] == "我自己的标签"
    assert _os.path.exists(str(base / D.MARKER_NAME))


def test_clear_caches_touches_panel_expr():
    """真清一次 `panel_expr` 的进程级缓存（`_CAL/_FEATURE_DIR` 必须被复位 ✓）。"""
    from app.factors import panel_expr
    panel_expr._CAL = "sentinel"                                  # type: ignore[assignment]
    panel_expr._FEATURE_DIR = "sentinel"                          # type: ignore[assignment]
    done = D.clear_caches()
    assert done.get("panel_expr") is True
    assert panel_expr._CAL is None and panel_expr._FEATURE_DIR is None
