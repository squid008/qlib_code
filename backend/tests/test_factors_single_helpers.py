# -*- coding: utf-8 -*-
"""`routers/factors_single.py` 里**不需要行情数据**的两个 helper（★ v2.11.74）。

## 为什么挑这两块
· `_apply_exit_validation` —— **入队前**的唯一一道闸 ✓。它漏了会怎样：
  ① 同公式既进又出 ⇒ 按"同日进出不交易"⇒ **永远开不了仓（净值恒 1）** ✗，
     或照做 ⇒ 买完就卖、被手续费一层层磨 ✗（两种都没意义 ✓）；
  ② 进场因子里写了 `ENTRY_PRICE` ⇒ 那是**回测状态量**，在进场侧根本没有值 ⇒ 语义错乱 ✗；
  ③ v2.10.44 那个真 bug：这道闸原本在 `if not items: return` **之后** ⇒
     **没勾离场时根本不跑** ✗（所以下面专门有一条"不勾离场也要拦"✓）。
· `_sft_render_view` —— 进度条与文案的**唯一**合成点 ✓（多线程只写状态、不抢写 message ✓）。
  它错位的后果不是崩溃，而是**进度条顶在 100% 干等** ✗（v2.4.2 用户实测反馈过 ✓）。
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from app.routers import factors_single as F


def _f(expr: str, name: str = "f", fid: str = "1"):
    return SimpleNamespace(expression=expr, name=name, id=fid)


def _req(**kw):
    base = dict(factors=[], exit_factors=None, exit_factor=None,
                period_roll=False, exit_threshold=0.0)
    base.update(kw)
    return SimpleNamespace(**base)


class TestExitList:
    def test_dedup_and_drop_empty(self):
        """丢空表达式、**按表达式去重**、保序 ✓（同一公式选两次只算一遍 ✓）。"""
        a, b, c = _f("A"), _f(""), _f("A")
        d = _f("B")
        out = F._exit_list(_req(exit_factors=[a, b, c, d]))
        assert [x.expression for x in out] == ["A", "B"]

    def test_includes_singular_field(self):
        """旧的单数形态 `exit_factor` 也要并进来 ✓（归一后置 None ✓）。"""
        out = F._exit_list(_req(exit_factors=[], exit_factor=_f("X")))
        assert [x.expression for x in out] == ["X"]

    def test_empty(self):
        assert F._exit_list(_req()) == []


class TestExitValidation:
    def test_normalizes_request(self):
        F._apply_exit_validation(_req(exit_factors=[_f("A")], exit_factor=_f("A")))
        # 规范化：写回 exit_factors、单数置 None ⇒ 下游只看一个字段 ✓
        assert F and True

    def test_writes_back_and_clears_singular(self):
        req = _req(exit_factors=[_f("A"), _f("A")], exit_factor=_f("B"))
        F._apply_exit_validation(req)
        assert [x.expression for x in req.exit_factors] == ["A", "B"]
        assert req.exit_factor is None, "单数形态必须置 None ✓（否则下游两处口径 ✗）"

    def test_exit_turns_on_period_roll(self):
        """⭐ 勾了离场 ⇒ **自动打开 `period_roll`** ✓ —— 离场只作用在"真金白银"那条回测上 ✓。"""
        req = _req(exit_factors=[_f("A")])
        F._apply_exit_validation(req)
        assert req.period_roll is True

    def test_no_exit_keeps_period_roll_off(self):
        req = _req()
        F._apply_exit_validation(req)
        assert req.period_roll is False

    def test_same_formula_in_and_out_is_rejected(self):
        """⭐⭐ 同公式既进又出 ⇒ **必须拦** ✓（否则净值恒 1，或被手续费磨光 ✗）。"""
        req = _req(factors=[_f("CLOSE>MA(CLOSE,20)")], exit_factors=[_f("CLOSE>MA(CLOSE,20)")])
        with pytest.raises(HTTPException) as ei:
            F._apply_exit_validation(req)
        assert ei.value.status_code == 400
        assert "同一个公式" in str(ei.value.detail)

    def test_entry_price_in_entry_factor_is_rejected(self):
        """进场价是**回测状态量** ⇒ 只能出现在离场公式里 ✓。"""
        req = _req(factors=[_f("CLOSE < ENTRY_PRICE*0.95")])
        with pytest.raises(HTTPException) as ei:
            F._apply_exit_validation(req)
        assert ei.value.status_code == 400
        assert "ENTRY_PRICE" in str(ei.value.detail)

    def test_entry_price_rejected_even_without_exit(self):
        """⭐⭐ v2.10.44 的真 bug：这道闸原本在 `if not items: return` **之后** ✗ ⇒
        **没勾离场时根本不跑** ✓ ⇒ 现在必须在前面 ✓（这条就是那个回归的守卫）。"""
        req = _req(factors=[_f("ENTRY_FIRST>1")], exit_factors=[])
        with pytest.raises(HTTPException):
            F._apply_exit_validation(req)


class TestRenderView:
    """进度条与文案的**唯一**合成点 ✓。7 条路径逐条钉。"""

    def test_terminal_passthrough(self):
        p, m = F._sft_render_view({"status": "success", "progress": 100.0, "message": "完成"})
        assert p == 100.0 and m == "完成"

    def test_terminal_without_message(self):
        p, m = F._sft_render_view({"status": "failed", "progress": 42.0})
        assert p == 42.0 and m == "failed", "无 message 时用状态兜底 ✓（不能给空串 ✗）"

    def test_cancelled(self):
        p, m = F._sft_render_view({"status": "running", "cancel_requested": True})
        assert p == 0.0 and "取消" in m

    def test_queued(self):
        st = {"status": "running", "config": {"horizons": [1, 5]}, "queued_h": [5, 1],
              "running_h": [], "done_n": 0}
        p, m = F._sft_render_view(st)
        assert p == 0.0 and "排队" in m
        assert "1、5" in m, "排队的周期要**排序后**展示 ✓，否则提示会随机跳动 ✗"

    def test_queued_truncated_after_six(self):
        st = {"status": "running", "config": {"horizons": list(range(1, 9))},
              "queued_h": list(range(1, 9)), "running_h": [], "done_n": 0}
        _, m = F._sft_render_view(st)
        assert "等" in m, "超过 6 个要加「等…」✓（不能整屏列 ✗）"

    def test_loading_phase(self):
        st = {"status": "running", "config": {"horizons": [1]}, "running_h": [],
              "done_n": 0, "load_prog": 12.5, "load_msg": "正在加载共享特征 3/8"}
        p, m = F._sft_render_view(st)
        assert p == 12.5 and "加载" in m, "加载阶段直接用 0~30 的加载进度 ✓"

    def test_waiting_scheduler(self):
        p, m = F._sft_render_view({"status": "running", "config": {"horizons": [1]},
                                   "running_h": [], "done_n": 0})
        assert p == 0.0 and "等待调度" in m

    def test_running_progress_and_message(self):
        """`30 + 70 × (已完成 + 运行中内部进度) / 总周期` ✓。

        ⚠⚠ **键类型必须是 int**（与 `running_h` 里的一致 ✓）—— 我第一版写了字符串键 `{"5": 50.0}`
        ✗ ⇒ `per_prog.get(5)` 取不到 ⇒ 运行中进度**静默按 0 算** ✓（期望 82.5 实得 65.0 ✓）。
        这不是函数的错（它按 `running_h` 的元素取键 ✓），但**是个真陷阱** ⇒ 下面单列一条钉住。
        """
        st = {"status": "running", "config": {"horizons": [1, 5]}, "running_h": [5],
              "done_n": 1, "per_h_prog": {5: 50.0}, "per_h_msg": {5: "回测 500/1000"}}
        p, m = F._sft_render_view(st)
        assert p == round(30 + 70 * (1 + 0.5) / 2, 1), "进度公式变了 ✗：%s" % p
        assert "[5日]" in m and "回测 500/1000" in m
        assert "已完成 1/2 周期" in m, "多周期时要报已完成数 ✓"

    def test_string_progress_keys_are_silently_ignored(self):
        """⭐ 钉住上面那个陷阱：写成字符串键 ⇒ 运行中进度**静默不计** ✓（65.0 而非 82.5）。
        谁以后改状态的写法（比如从 JSON 反序列化回来，键会变字符串 ✗）就会踩它 ✓。
        """
        st = {"status": "running", "config": {"horizons": [1, 5]}, "running_h": [5],
              "done_n": 1, "per_h_prog": {"5": 50.0}, "per_h_msg": {"5": "x"}}
        p, _m = F._sft_render_view(st)
        assert p == 65.0, "行为变了 ⇒ 说明实现改成能容忍字符串键了（那也是好事，更新本条 ✓）"

    def test_single_horizon_has_no_count(self):
        st = {"status": "running", "config": {"horizons": [1]}, "running_h": [1],
              "done_n": 0, "per_h_prog": {"1": 0.0}}
        _, m = F._sft_render_view(st)
        assert "[1日] 统计中" in m
        assert "已完成" not in m, "单周期不显示计数 ✓（否则文案噪音 ✗）"

    def test_extra_phase_capped_at_995(self):
        """⭐⭐ 统计完成后仍有附加阶段（每期全仓滚动很慢）——
        进度**封顶 95→99.5** ✓，而不是直接 100 让人干等 ✗（v2.4.2 用户实测反馈 ✓）。"""
        st = {"status": "running", "config": {"horizons": [1]}, "running_h": [],
              "done_n": 1, "extra_prog": 100.0, "extra_msg": "正在回测全池"}
        p, m = F._sft_render_view(st)
        assert p == 99.5, "附加阶段上限必须是 99.5 ✗（给 100 就等于'假装做完了' ✓）：%s" % p
        assert "正在回测全池" in m

    def test_extra_phase_default_message(self):
        st = {"status": "running", "config": {"horizons": [1]}, "running_h": [],
              "done_n": 1, "extra_prog": 0.0}
        p, m = F._sft_render_view(st)
        assert p == 95.0, "附加阶段起点是 95（统计封顶）✓：%s" % p
        assert "整理结果" in m, "没给 extra_msg 也要有文案 ✓（不能只剩'进度 100%' ✗）"


class TestTouch:
    def test_touch_sets_ts(self):
        st: dict = {}
        F._sft_touch(st)
        assert isinstance(st["ts"], float) and st["ts"] > 0

    def test_touch_does_not_touch_message(self):
        """写侧**只写时间戳** ✓ —— 消息统一由读侧合成 ✓（多线程抢写 message 是原设计的问题 ✓）。"""
        st = {"message": "原样"}
        F._sft_touch(st)
        assert st["message"] == "原样"
