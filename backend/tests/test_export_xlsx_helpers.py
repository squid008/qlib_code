# -*- coding: utf-8 -*-
"""`routers/export_xlsx.py` 的**纯 helper**（★ v2.11.78）—— 找行/找档位/截图校验。

## 为什么这批值钱
它们是三条导出（topk / 离场曲线 / 事件研究）**共用的定位逻辑** ✓。错的方式都很安静 ✗：
· `_decode_shot`：唯一"**用户侧原样进文件**"的输入 ⇒ 不校验就可能是 GB 级垃圾或非 PNG ✓；
· `_find_row` / `_find_item`：**找错行** ⇒ 导出的是**另一个因子/另一档**的结果 ✗
  （文件看着完全正常，数字也是真的，只是**不是你要的那一份** ✗ —— 最难发现的一类）；
· `_safe_name`：Windows 不允许 `\\/:*?"<>|` ⇒ 不换就是**下载直接失败** ✓；
· `_pick_benchmark`：给了代码却静默回落到默认基准 ⇒ 曲线对不上页面 ✗。
"""
from __future__ import annotations

import base64
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from app.routers import export_xlsx as X

#: 合法 1×1 PNG（用它的真魔数走通"是 PNG"那条分支 ✓）
PNG = bytes.fromhex(
    "89504e470d0a1a0a0000000d49484452000000010000000108060000001f15c4"
    "890000000a49444154789c6300010000050001 0d0a2db40000000049454e44ae426082".replace(" ", "")
)


def _b64(raw: bytes) -> str:
    return base64.b64encode(raw).decode("ascii")


class TestDecodeShot:
    def test_png_ok(self):
        raw, err = X._decode_shot(_b64(PNG))
        assert raw == PNG and err is None

    def test_data_url_prefix_stripped(self):
        raw, err = X._decode_shot("data:image/png;base64," + _b64(PNG))
        assert raw == PNG and err is None, "前端抓图给的是 dataURL ✓（前缀必须吃掉 ✓）"

    def test_empty_gives_none_without_error(self):
        """没截图是**正常情况**（浏览器抓图可能失败 ✓）⇒ 不该报错，只返回 None ✓。"""
        assert X._decode_shot(None) == (None, None)
        assert X._decode_shot("") == (None, None)
        assert X._decode_shot(123) == (None, None)

    def test_non_png_rejected_with_reason(self):
        """⭐⭐ 只收 **PNG 魔数** ✓ —— 否则一个坏 base64 就能让 openpyxl 写图时 500 ✗。"""
        raw, err = X._decode_shot(_b64(b"not a png at all"))
        assert raw is None
        assert err and "PNG" in err, err

    def test_bad_base64_rejected_with_reason(self):
        raw, err = X._decode_shot("!!!not-base64!!!")
        assert raw is None
        assert err and "解码失败" in err, err

    def test_oversize_rejected(self, monkeypatch):
        """⭐ 大小上限 ✓ —— 否则一次导出能把几百 MB 塞进 xlsx ✗。

        ⚠ 上限要设得比样例**还小** ✗→✓ —— 我第一版写 `100`，而那个 1×1 PNG 只有 ~70 字节
        ⇒ 根本没触发上限 ✓（断言红，但错的是测试 ✓）。
        """
        monkeypatch.setattr(X, "MAX_SHOT_BYTES", 10)
        raw, err = X._decode_shot(_b64(PNG))
        assert raw is None
        assert err and "过大" in err, err

    def test_reason_is_user_readable(self):
        """失败原因要能直接摆在用户面前 ✓（含"本次未附图"这类结论 ✓）。"""
        _, err = X._decode_shot(_b64(b"junk"))
        assert "未附图" in err


class TestSafeName:
    @pytest.mark.parametrize("bad", ["a/b", "a\\b", "a:b", "a*b", 'a"b', "a<b", "a>b", "a|b", "a\nb"])
    def test_illegal_chars_replaced(self, bad):
        """⭐⭐ Windows 不允许 `\\/:*?"<>|` ✓ —— 不换就是**下载直接失败** ✗。"""
        out = X._safe_name(bad)
        assert not any(ch in out for ch in '\\/:*?"<>|\r\n\t'), out

    def test_empty_falls_back(self):
        assert X._safe_name("") == "factor"
        assert X._safe_name(None) == "factor"
        assert X._safe_name(None, "topk") == "topk"

    def test_truncated(self):
        assert len(X._safe_name("字" * 200)) == 60, "文件名要截断 ✓（否则路径超长 ✗）"

    def test_keeps_chinese(self):
        assert X._safe_name("单因子测试结果") == "单因子测试结果"


class TestResponse:
    def test_headers(self):
        r = X._xlsx_response(b"PK..", "结果.xlsx")
        assert r.media_type == X.XLSX_MIME
        assert "attachment" in r.headers["content-disposition"]
        assert "filename*=UTF-8''" in r.headers["content-disposition"], "中文名必须 URL 编码 ✓"
        assert r.headers["x-export-filename"], "前端靠它显示文件名 ✓"

    def test_filename_is_url_quoted(self):
        r = X._xlsx_response(b"PK..", "单因子 结果.xlsx")
        raw = r.headers["content-disposition"]
        assert "单因子" not in raw, "未编码的中文会破坏 header ✗：%s" % raw


class _Req:
    def __init__(self, **kw):
        self.factor_id = kw.get("factor_id")
        self.horizon = kw.get("horizon")
        self.item_kind = kw.get("item_kind")
        self.item_k = kw.get("item_k")
        self.item_quantile = kw.get("item_quantile")


class TestFindRow:
    ITEMS = [
        {"id": "1", "name": "mom", "expression": "Mean($close,20)", "horizon": 5, "v": "A"},
        {"id": "1", "name": "mom", "expression": "Mean($close,20)", "horizon": 20, "v": "B"},
        {"id": "2", "name": "vol", "expression": "Std($close,20)", "horizon": 5, "v": "C"},
    ]

    def test_matches_factor_and_horizon(self):
        """⭐⭐ 必须**同时**对上因子与周期 ✓ —— 只对上因子会导出错周期的结果 ✗。"""
        row = X._find_row(self.ITEMS, _Req(factor_id="1", horizon=20))
        assert row["v"] == "B"

    def test_falls_back_to_ignoring_horizon(self):
        row = X._find_row(self.ITEMS, _Req(factor_id="2", horizon=99))
        assert row["v"] == "C", "该因子的这个周期不存在时，退一步给同因子的行 ✓"

    def test_by_expression(self):
        row = X._find_row(self.ITEMS, _Req(factor_id="Std($close,20)", horizon=5))
        assert row["v"] == "C"

    def test_empty_factor_id_takes_first(self):
        row = X._find_row(self.ITEMS, _Req(factor_id=None, horizon=5))
        assert row["v"] == "A"

    def test_not_found_raises_404(self):
        with pytest.raises(HTTPException) as ei:
            X._find_row(self.ITEMS, _Req(factor_id="不存在"))
        assert ei.value.status_code == 404
        assert "找不到该因子" in str(ei.value.detail)


class TestItemIdentity:
    def test_topk(self):
        assert X._item_identity({"kind": "topk", "k": 50}) == ("topk", 50, None)

    def test_decile(self):
        assert X._item_identity({"kind": "decile", "quantile": 3}) == ("decile", 0, 3)

    def test_default_kind_is_topk(self):
        assert X._item_identity({})[0] == "topk"


class TestFindItem:
    TC = {"items": [{"kind": "decile", "quantile": 1}, {"kind": "decile", "quantile": 5},
                    {"kind": "topk", "k": 10}]}

    def test_pick_by_kind_and_k(self):
        got = X._find_item(self.TC, _Req(item_kind="topk", item_k=10))
        assert got["k"] == 10

    def test_pick_by_quantile(self):
        got = X._find_item(self.TC, _Req(item_kind="decile", item_quantile=5))
        assert got["quantile"] == 5

    def test_missing_raises_400(self):
        with pytest.raises(HTTPException) as ei:
            X._find_item(self.TC, _Req(item_kind="topk", item_k=999))
        assert ei.value.status_code == 400
        assert "档位" in str(ei.value.detail)


class TestPickBenchmark:
    TC = {"benchmarks": {"default": "SH000300",
                         "items": [{"code": "SH000300", "name": "沪深300"},
                                   {"code": "SH000905", "name": "中证500"}]}}

    def test_by_code(self):
        assert X._pick_benchmark(self.TC, "SH000905")["name"] == "中证500"

    def test_default_when_empty(self):
        assert X._pick_benchmark(self.TC, "")["code"] == "SH000300"

    def test_unknown_code_returns_none(self):
        """⭐ 不认识的代码 ⇒ `None`（调用方据此提示 ✓）—— **不许**静默回落到默认 ✗
        （否则用户以为看的是中证500，其实是沪深300 ✗）。"""
        assert X._pick_benchmark(self.TC, "SH999999") is None


class TestExitRunHeadline:
    """离场页顶上的摘要行 ✓（与弹窗那张对比表同口径 ✓）。"""

    def test_full(self):
        st = {"perf": {"annual_return": 0.123, "max_drawdown": 0.087},
              "final_nav": 1.2345,
              "exit_sells": 12, "exit_deferrals": 3, "exit_overridden": 1, "exit_ignored": 4}
        lines = X._exit_run_headline(st, "离场A 年化")
        assert len(lines) == 2
        assert "离场A 年化 12.3%" in lines[0], lines[0]
        assert "最大回撤 8.7%" in lines[0]
        assert "期末净值 1.2345" in lines[0]
        assert "离场卖出 12" in lines[1] and "持币空转 4" in lines[1]

    def test_zero_counts_still_shown(self):
        """⭐⭐ `0` 必须**显示**出来 ✓ —— 用 `if st.get(k)` 判空会把 0 当"没有"✗，
        而"离场卖出 0"和"没统计"对用户是两件完全不同的事 ✓。"""
        st = {"exit_sells": 0, "exit_deferrals": 0}
        lines = X._exit_run_headline(st, "年化")
        assert len(lines) == 1
        assert "离场卖出 0" in lines[0] and "顺延 0" in lines[0]

    def test_empty_state_returns_empty_list(self):
        assert X._exit_run_headline({}, "年化") == []
        assert X._exit_run_headline(None, "年化") == []

    def test_non_numeric_fields_skipped(self):
        """字符串/None 的指标 ⇒ 跳过 ✓（不能抛 ✗ —— 它只是摘要文案 ✓）。"""
        st = {"perf": {"annual_return": "n/a", "max_drawdown": None}, "final_nav": "x"}
        assert X._exit_run_headline(st, "年化") == []

    def test_prefix_only_used_for_annual(self):
        st = {"perf": {"annual_return": 0.1}}
        assert X._exit_run_headline(st, "对照")[0] == "对照 10.0%"


class TestFindEventStudy:
    def test_standalone_result(self):
        state = {"result": {"curve": [1, 2], "factor": "f"}}
        es, row = X._find_event_study(state, None)
        assert es["curve"] == [1, 2] and row is None, "独立任务没有'行' ⇒ None ✓"

    def test_from_items_returns_row_too(self):
        """⭐⭐ 必须连**那一行**一起返回 ✓ —— 因子名/表达式挂在行上，结果里没有 ✗
        （v2.1.3 实测：只返回结果 ⇒ 导出里「因子名称」是空的 ✗）。"""
        state = {"result": {"items": [
            {"id": "1", "name": "mom", "event_study": {"curve": [1]}},
            {"id": "2", "name": "vol", "event_study": {"curve": [2]}}]}}
        es, row = X._find_event_study(state, "2")
        assert es["curve"] == [2]
        assert row["name"] == "vol", "行必须一起返回 ✗"

    def test_empty_factor_id_takes_first_with_es(self):
        state = {"result": {"items": [{"id": "1"}, {"id": "2", "event_study": {"curve": [2]}}]}}
        es, row = X._find_event_study(state, "")
        assert es["curve"] == [2] and row["id"] == "2"

    def test_missing_returns_pair_of_none(self):
        assert X._find_event_study({}, "x") == (None, None)
        assert X._find_event_study({"result": {"items": []}}, "x") == (None, None)
