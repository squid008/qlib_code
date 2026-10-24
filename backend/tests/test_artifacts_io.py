# -*- coding: utf-8 -*-
"""`app/engine/artifacts.py` 的**写盘层**（★ v2.11.72）—— 缺口最大的纯逻辑块。

## 为什么先啃它（覆盖率口径）
v2.11.71 实测：后端 **67.74%**，到 70% 还差 **+435 行** ✓。
缺行最多的是 `single_test`(470) / `qlib_engine`(469) / `panel_expr`(422) —— 但它们**几乎全要行情数据** ✗
（PR CI 里没有数据，覆盖率就是在**无数据**那条 job 里量的 ✓）。
而 `artifacts.py` 缺 **288 行 (33.6%)** 且**纯文件/JSON** ✓ ⇒ tmp_path 就能测 ✓ 性价比最高 ✓。

## 这些函数坏掉的后果（为什么值得测）
· `_sanitize_json`：NaN/Inf 漏进 JSON ⇒ `result.json` **不是标准 JSON** ⇒ 前端 `JSON.parse` 直接炸 ✗
  （用户看到的是"结果打不开"，而不是"某个指标是 NaN" ✗）；
· `_assign_backtest_seq`：序号重复 ⇒ 历史列表里两条同名/互相覆盖 ✗；
· `_save_compose_attr`：**按段累积**——写坏一次就把前面的段全丢了 ✗（而它是"旁路记录"，失败不报错 ✗）；
· `_save_backtest_params`/`_save_train_signature`：参数快照错了 ⇒ **"复现同一结果"这件事失效** ✗。
"""
from __future__ import annotations

import json
import math
import os

import pytest

from app.engine import artifacts as A
from app.models.backtest import BacktestRequest


class _StubResult:
    """冒充 `BacktestResult`：只要有 `.dict()` 就够 `_save_result_json` 用 ✓。"""

    def dict(self):
        return {
            "total_return": 0.12,
            "metrics": {"sharpe": float("nan"), "max_dd": float("-inf"), "ir": 1.5},
            "nav": [{"date": "2024-01-02", "value": 1.0}, {"date": "2024-01-03", "value": float("inf")}],
        }


class TestSanitizeJson:
    def test_nan_and_inf_become_none(self):
        """⭐⭐ NaN/Inf 必须变 `None` —— 否则 `json.dump` 写出**非标准 JSON** ✗（前端 `JSON.parse` 炸）。"""
        out = A._sanitize_json({"a": float("nan"), "b": float("inf"), "c": float("-inf"), "d": 1.25})
        assert out == {"a": None, "b": None, "c": None, "d": 1.25}
        # 反证：不清洗会怎样 —— `json.dumps` 默认允许 NaN ⇒ 产物就不是标准 JSON 了 ✓
        assert "NaN" in json.dumps({"a": float("nan")})

    def test_nested_and_tuple(self):
        out = A._sanitize_json({"x": [1.0, float("nan")], "y": (float("inf"), "s")})
        assert out == {"x": [1.0, None], "y": [None, "s"]}, "嵌套 list/tuple 都要递归 ✓"

    def test_non_floats_pass_through(self):
        for v in (None, True, 3, "s", b"b"):
            assert A._sanitize_json(v) is v or A._sanitize_json(v) == v


class TestSaveResultJson:
    def test_nan_becomes_null_and_file_is_valid_json(self, tmp_path):
        A._save_result_json(str(tmp_path), _StubResult())
        p = tmp_path / "result.json"
        assert p.is_file()
        txt = p.read_text(encoding="utf-8")
        assert "NaN" not in txt and "Infinity" not in txt, "清洗没生效 ✗：%s" % txt[:120]
        data = json.loads(txt)
        assert data["metrics"]["sharpe"] is None
        assert data["nav"][1]["value"] is None
        assert data["metrics"]["ir"] == 1.5, "有限值不该被改 ✓"

    def test_bad_result_does_not_raise(self, tmp_path):
        """旁路记录：**写失败也不能让回测挂掉** ✓（只记 warning）。"""

        class _Bad:
            def dict(self):
                raise RuntimeError("boom")

        A._save_result_json(str(tmp_path), _Bad())  # 不应抛 ✓


class TestAssignSeq:
    def test_first_is_one(self, tmp_path):
        d = tmp_path / "art" / "task-a"
        d.mkdir(parents=True)
        A._assign_backtest_seq(str(d))
        assert json.loads((d / "seq.json").read_text(encoding="utf-8"))["seq"] == 1

    def test_increments_across_dirs(self, tmp_path):
        root = tmp_path / "art"
        for i, name in enumerate(["a", "b", "c"], start=1):
            d = root / name
            d.mkdir(parents=True)
            A._assign_backtest_seq(str(d))
            got = json.loads((d / "seq.json").read_text(encoding="utf-8"))["seq"]
            assert got == i, "第 %d 个目录应得序号 %d，实际 %s" % (i, i, got)

    def test_idempotent(self, tmp_path):
        """⭐ 已分配过就**不再改** ✓ —— 否则同一个回测每调一次序号都变 ✗（历史列表会乱跳）。"""
        d = tmp_path / "art" / "a"
        d.mkdir(parents=True)
        A._assign_backtest_seq(str(d))
        first = json.loads((d / "seq.json").read_text(encoding="utf-8"))["seq"]
        A._assign_backtest_seq(str(d))
        assert json.loads((d / "seq.json").read_text(encoding="utf-8"))["seq"] == first

    def test_corrupt_seq_is_tolerated(self, tmp_path):
        """坏的 `seq.json` 不该把新回测也带崩 ✓（扫描时单条解析失败只记日志 ✓）。"""
        root = tmp_path / "art"
        bad = root / "bad"
        bad.mkdir(parents=True)
        (bad / "seq.json").write_text("{不是 json", encoding="utf-8")
        d = root / "new"
        d.mkdir(parents=True)
        A._assign_backtest_seq(str(d))
        assert json.loads((d / "seq.json").read_text(encoding="utf-8"))["seq"] == 1


class TestComposeAttr:
    def test_none_dir_is_safe(self):
        A._save_compose_attr(None, "段1", {"gate": "x"})   # 不应抛 ✓

    def test_accumulates_segments(self, tmp_path):
        """⭐ 按段**累积** ✓ —— 滚动回测每段写一次，前面写的不能丢 ✗。"""
        d = str(tmp_path)
        A._save_compose_attr(d, "段1", {"gate": "g1"})
        A._save_compose_attr(d, "段2", {"gate": "g2"})
        data = json.loads((tmp_path / "compose.json").read_text(encoding="utf-8"))
        assert set(data["segments"]) == {"段1", "段2"}
        assert data["segments"]["段1"]["gate"] == "g1", "第二段把第一段覆盖了 ✗"
        assert data["updated_at"], "应有时间戳 ✓"

    def test_default_segment_name(self, tmp_path):
        A._save_compose_attr(str(tmp_path), None, {"a": 1})
        data = json.loads((tmp_path / "compose.json").read_text(encoding="utf-8"))
        assert "段1" in data["segments"], "段名为空应落到「段1」✓"

    def test_corrupt_file_is_rebuilt(self, tmp_path):
        """损坏的旧文件 ⇒ **重建** ✓（不能因为历史坏了就永久写不进 ✓）。"""
        (tmp_path / "compose.json").write_text("{{{坏", encoding="utf-8")
        A._save_compose_attr(str(tmp_path), "段1", {"a": 1})
        data = json.loads((tmp_path / "compose.json").read_text(encoding="utf-8"))
        assert data["segments"]["段1"] == {"a": 1}

    def test_nan_in_info_is_sanitized(self, tmp_path):
        A._save_compose_attr(str(tmp_path), "段1", {"w": float("nan")})
        txt = (tmp_path / "compose.json").read_text(encoding="utf-8")
        assert "NaN" not in txt, "compose.json 也得清洗 ✓"


class TestParamsAndSignature:
    def test_backtest_params_written(self, tmp_path):
        A._save_backtest_params(str(tmp_path), BacktestRequest())
        files = [p.name for p in tmp_path.iterdir()]
        assert files, "一个文件都没写 ✗"
        for name in files:
            json.loads((tmp_path / name).read_text(encoding="utf-8"))  # 必须都是合法 JSON ✓

    def test_train_signature_written(self, tmp_path):
        A._save_train_signature(str(tmp_path), BacktestRequest())
        assert any(tmp_path.iterdir()), "训练签名没落盘 ✗"

    def test_train_semantics_helpers(self):
        """`_current_train_semantics()` 与 `_train_semantics_info(req)` 是"同口径"的两个出口 ✓。"""
        cur = A._current_train_semantics()
        assert isinstance(cur, str) and cur, "口径戳不能是空串 ✓（空串会被当成'无戳'⇒缓存无效 ✗）"
        info = A._train_semantics_info(BacktestRequest())
        assert isinstance(info, dict) and info, info


@pytest.mark.parametrize("task_id", ["不存在-1", "不存在-2"])
def test_find_artifact_base_missing(task_id, monkeypatch, tmp_path):
    """找不到产物目录 ⇒ 返回 `None`（调用方据此判断"没有可复用模型"✓，不能抛 ✗）。

    ⚠ 我第一版去 monkeypatch `get_artifact_dir` ✗ ⇒ 红 ✓：它**不用那个**，
    而是 `from ..config import WORK_DIR` + glob `artifacts/*_<task_id>` ✓（读实现才发现 ✓）。
    """
    monkeypatch.setattr("app.config.WORK_DIR", str(tmp_path))
    (tmp_path / "artifacts").mkdir()
    assert A._find_artifact_base(task_id) is None


def test_find_artifact_base_finds_prefixed_dir(tmp_path, monkeypatch):
    """`artifacts/<日期>_<task_id>` 这种**带前缀**的目录也要认出来 ✓（真实命名就是它 ✓）。"""
    monkeypatch.setattr("app.config.WORK_DIR", str(tmp_path))
    d = tmp_path / "artifacts" / "20260101_093015_t1"
    d.mkdir(parents=True)
    got = A._find_artifact_base("t1")
    assert got and os.path.isdir(got)
    assert got.endswith("20260101_093015_t1"), got


def test_find_artifact_base_exact_name(tmp_path, monkeypatch):
    """也接受**不带前缀**的 `<task_id>` ✓（glob 的第二条回退 ✓）。"""
    monkeypatch.setattr("app.config.WORK_DIR", str(tmp_path))
    (tmp_path / "artifacts" / "t2").mkdir(parents=True)
    assert A._find_artifact_base("t2")
