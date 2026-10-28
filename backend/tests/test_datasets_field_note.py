# -*- coding: utf-8 -*-
"""数据集「与基准字段比对」的口径（★ v2.11.99）。

## 为什么单独钉这三条
用户看到的这条提示曾**在每个数据集上固定出现** ✗：

```
与基准字段不一致（仅提示，不影响使用）：多 1 个（如 date）、缺 0 个
```

真因：qlib 落盘时**每只票都写 `date.day.bin`** ✓，而 `.dataset.json` 的 `fields_baseline`
是"**因子字段**清单"、**从不登记 `date`** ✓ ⇒ `date` 被算成"多出来的字段" ✗。
实测三个数据集全中（baseline 111/121/10 个，**都没有 date**）✓。

⇒ 修法：`date` 属于**结构/索引字段**，比对时**两边都剔掉** ✓。
但**真的多/真的缺仍然要报** ✓（这是这条提示存在的意义：同事往目录里 dump 新字段时看得见 ✓）。
"""
from __future__ import annotations

import json

import pytest

from app.services import datasets as DS


def _mk_dataset(root, name, baseline, extra=(), missing_from_disk=()):
    """造一个最小数据集目录 ✓：`.dataset.json` + `calendars/day.txt` + 一只票的 bin 文件。"""
    d = root / name
    (d / "calendars").mkdir(parents=True)
    (d / "calendars" / "day.txt").write_text("2024-01-02\n2024-01-03\n2024-01-04\n", encoding="utf-8")
    (d / ".dataset.json").write_text(
        json.dumps({"convention": "tushare", "fields_baseline": list(baseline)}), encoding="utf-8")
    feats = d / "features" / "SH600000"
    feats.mkdir(parents=True)
    fields = [f for f in ("close", "open", "volume", "date") if f not in missing_from_disk]
    fields += list(extra)
    for f in fields:
        (feats / (f + ".day.bin")).write_bytes(b"")
    return d


@pytest.fixture(autouse=True)
def _isolate(tmp_path, monkeypatch):
    """把 `datasets.DATA` 指到 tmp ✓（否则 probe 会去读**真数据目录** ⇒ 随环境红绿 ✗）。"""
    monkeypatch.setattr(DS, "DATA", tmp_path)
    DS._PROBE_CACHE.clear()
    yield
    DS._PROBE_CACHE.clear()


class TestStructFieldExcluded:
    def test_date_is_not_reported_as_extra(self, tmp_path):
        """⭐⭐ 每只票都有 `date.day.bin`，而 baseline 不登记它 ⇒ **必须算一致** ✓。"""
        _mk_dataset(tmp_path, "cn_data2", ["close", "open", "volume"])
        info = DS.probe("cn_data2", use_cache=False)
        assert info["fields_extra_count"] == 0, info.get("fields_extra")
        assert info["fields_missing_count"] == 0, info.get("fields_missing")
        assert info["field_note"] == "字段与基准一致（3 个）", info["field_note"]

    def test_baseline_listing_date_is_not_reported_as_missing(self, tmp_path):
        """反向也一样：baseline 若登记了 `date`、盘上没有 ⇒ **不该报缺 `date`** ✓。"""
        _mk_dataset(tmp_path, "cn_data2", ["close", "open", "volume", "date"],
                    missing_from_disk=("date",))
        info = DS.probe("cn_data2", use_cache=False)
        assert info["fields_missing_count"] == 0, info.get("fields_missing")
        assert info["field_note"] == "字段与基准一致（3 个）", info["field_note"]


class TestRealDiffStillReported:
    def test_real_extra_field_reported(self, tmp_path):
        """⭐ 真的多一个字段 ⇒ 必须报出来（这正是这条提示的用途 ✓）。"""
        _mk_dataset(tmp_path, "cn_data2", ["close", "open", "volume"], extra=("newf",))
        info = DS.probe("cn_data2", use_cache=False)
        assert info["fields_extra_count"] == 1
        assert "newf" in info["field_note"], info["field_note"]
        assert "date" not in info["field_note"], "结构字段不该出现在提示里 ✗：%s" % info["field_note"]

    def test_real_missing_field_reported(self, tmp_path):
        _mk_dataset(tmp_path, "cn_data2", ["close", "open", "volume", "fin_x"],
                    missing_from_disk=("volume",))
        info = DS.probe("cn_data2", use_cache=False)
        assert info["fields_missing_count"] == 2, info.get("fields_missing")
        assert "fin_x" in info["field_note"], info["field_note"]

    def test_baseline_count_excludes_struct(self, tmp_path):
        """⭐ 文案里的"N 个"要按**剔掉结构字段后**的基准数 ✓（否则和"一致"自相矛盾 ✗）。"""
        _mk_dataset(tmp_path, "cn_data2", ["close", "open", "volume", "date"])
        info = DS.probe("cn_data2", use_cache=False)
        assert info["baseline_count"] == 3, info["baseline_count"]
