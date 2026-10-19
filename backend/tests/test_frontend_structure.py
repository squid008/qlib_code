# -*- coding: utf-8 -*-
"""前端**结构断言**（★ P1-B 安全网，2026-10-18 ✓）。

## 为什么用"读源码断言"而不是 DOM 测试 ✗→✓

本仓库**前端零测试** ✗，也没装 vitest/jsdom ✗。而这两天最贵的返工全在**前端结构**上：

| 事故 | 表现 | 本文件对应的断言 ✓ |
|---|---|---|
| 2.8.9 表格布局 | 改 6 轮都"没变" | `tableLayout: fixed` + `width: 100%` 必须在 ✓ ｜ `min-w-[` 必须不在 ✓ |
| 2.8.9 列宽溢出 | 右边三列被截、出横向滚动条 | `colgroup` 列宽**合计**必须在 55~80rem 区间 ✓ |
| 2.9.1 参照线口径 | 参照线只持 1 天 ⇒ 假 alpha | `navSeries` 的 `bench_equal` 文案必须含「同持有期」✓ |
| 2.9.3 持仓上限 | 参数没入口 | 弹窗必须有「最大持仓数」+ 请求里有 `max_positions` ✓ |
| 2.9.4 按钮位置 | 按钮被黄色警告挤到第二行 | 警告（`basis-full`）必须在「重新计算」**之后** ✓ |

⚠ 这是**文本/结构**断言 ✓ —— 它挡不住"样式算错"，但能挡住**上面全部 5 类**（都是"代码结构被改坏"✓）。
真正的 DOM 测试（vitest + jsdom）是后续任务 ✓（要 npm 装依赖 ✓）。
"""
import os
import re

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
COMP = os.path.join(ROOT, "frontend", "src", "components")
PANEL = os.path.join(COMP, "SingleFactorTestPanel.tsx")
MODAL = os.path.join(COMP, "EventStudyModal.tsx")
NAVSERIES = os.path.join(COMP, "navSeries.ts")
APP = os.path.join(ROOT, "frontend", "src", "App.tsx")


def _read(p):
    assert os.path.isfile(p), "文件不存在：%s" % p
    with open(p, encoding="utf-8") as f:
        return f.read()


def _strip_comments(src):
    """剥掉 `/* … */` 与 `{/* … */}` 注释 ✓（注释里的示例代码不该被断言命中 ✗）。"""
    return re.sub(r"\{?/\*.*?\*/\}?", "", src, flags=re.S)


def _colgroup_block(src):
    i = src.find("<colgroup>")
    assert i > 0, "结果表必须有 `<colgroup>` ✓（列宽唯一来源 ✓）"
    j = src.find("</colgroup>", i)
    assert j > i
    return src[i:j]


# ---------- ① 表格布局（2.8.9 的 6 轮返工）----------

def test_results_table_uses_fixed_layout_with_full_width():
    """`table-layout: fixed` **且** `width: 100%` —— 缺一个就会"改了看不见" ✗。"""
    src = _read(PANEL)
    assert "tableLayout: 'fixed'" in src, "必须内联 tableLayout: 'fixed' ✓（别用 Tailwind 类 ✗）"
    assert "width: '100%'" in src, "fixed 必须配 width:100%，否则表被拉伸、列按比例放大 ✗"
    assert "minWidth" in src, "必须有 minWidth（窄屏兜底 ✓）"


def test_results_table_has_no_min_w_on_cells():
    """**结果表**里不许有 `min-w-[…]` ✗（硬下限会盖过 colgroup ⇒ 表格被顶大 ✗）。

    ⚠ 只查**这张表** ✓ —— 文件里别的区块（参数区/进度条 ✓）出现 `min-w-` 是正常的 ✓
      （首版我查了整文件 ⇒ 误报 2 处 `min-w-[5rem]` ✗，实测抓出来的 ✓）。
    """
    src = _read(PANEL)
    # ⚠ 别用 `find("tableLayout…")` 定位 ✗ —— 它**第一次出现是在注释里** ✓
    #   （我上一版就这么写的 ⇒ `rfind("<table")` 返回 -1 ⇒ 断言 -1>0 失败 ✓ 实测抓到 ✓）。
    # 正确做法：**逐张表**看"紧跟其后的 400 字里有没有 tableLayout" ✓ ⇒ 找到那张结果表 ✓。
    # ⚠ 必须**先剥注释** ✗ —— 否则会命中"注释里写的 `min-w-[5rem]`"✓
    #   （我上一版就误报了 2 处 ⇒ 实测 L1865 / L2175 都在注释里 ✓）。
    src = _strip_comments(src)
    i = -1
    pos = src.find("<table")
    while pos >= 0:
        if "tableLayout" in src[pos:pos + 400]:
            i = pos
            break
        pos = src.find("<table", pos + 1)
    assert i >= 0, "找不到结果表（<table … tableLayout …>）"
    j = src.find("</table>", i)
    assert j > i, "找不到结果表的 </table>"
    hits = re.findall(r"min-w-\[[0-9.]+rem\]", src[i:j])
    assert not hits, "结果表里不许再出现 %s ✗（列宽只由 colgroup 决定 ✓）" % hits[:3]


def test_colgroup_width_sum_is_sane():
    """`colgroup` 列宽**合计**必须落在 55~80rem ✓（太小挤爆、太大出横向滚动条 ✗）。"""
    blk = _colgroup_block(_read(PANEL))
    widths = [float(x) for x in re.findall(r"width:\s*'([0-9.]+)rem'", blk)]
    assert len(widths) >= 18, "列数偏少（%d）⇒ 可能漏了列 ✓" % len(widths)
    total = sum(widths)
    assert 55.0 <= total <= 80.0, "列宽合计 %.2frem 越界（55~80 ✓）⇒ 布局会坏 ✗" % total


# ---------- ② 参照线口径（2.9.1）----------

def test_bench_equal_label_mentions_same_holding_period():
    """未触发组参照线必须写明「同持有期」✓ —— 否则又会被读成"策略有 alpha"✗。"""
    src = _read(NAVSERIES)
    assert "bench_equal" in src
    i = src.find("bench_equal")
    seg = src[i:i + 400]
    assert "同持有期" in seg, "bench_equal 的文案必须含「同持有期」✓（2.9.1 的教训 ✓）"


# ---------- ③ 最大同时持仓数（2.9.3）----------

def test_modal_has_max_positions_control():
    """弹窗必须真有「最大持仓数」输入框 ✓，且请求里带 `max_positions` ✓。"""
    src = _read(MODAL)
    assert "最大持仓数" in src, "缺输入框 ⇒ 用户找不到参数 ✗（2.9.3 实测 ✓）"
    assert "max_positions: navMaxPos" in src, "请求没带上参数 ⇒ 后端按不限算 ✗"
    assert "（空=不限）" in src, "必须写明空 = 不限 ✓（否则用户不敢留空 ✓）"


def test_modal_shows_dilution_warning():
    """信号稀释警告必须接 `nav_diag.diluted` ✓（防"把 beta 当 alpha"✗）。"""
    src = _read(MODAL)
    assert "nav_diag?.diluted" in src
    assert "nav_diag?.bench_equal_note" in src


# ---------- ④ 按钮位置（2.9.4）----------

def test_recalc_button_precedes_dilution_warning():
    """「重新计算」必须排在警告**之前** ✓ —— 警告带 `basis-full` 会把按钮挤到第二行 ✗。"""
    src = _read(MODAL)
    i_btn = src.find("重新计算")
    i_warn = src.find("basis-full")
    assert i_btn > 0 and i_warn > 0
    assert i_btn < i_warn, "按钮在警告之后 ⇒ 又被挤下去了 ✗（2.9.4 的教训 ✓）"


# ---------- ⑤ 防"上帝文件"回潮（ratchet ✓）----------

@pytest.mark.parametrize("path,cap", [
    (PANEL, 2150),
    # ★ v2.9.41/42/43（2026-10-19 ✓）：拆出 `DayExclusions` + `HardRuleGate` + `MetaGatePanel`
    #   后 App **2,048 → 1,949** ✓ ⇒ 上限 2100 → 2040 → 2020 → **1960** ✓
    #   ⚠ 棘轮只许变小 ✗ —— 下刀拆完记得一起调 ✓
    # ★ v2.9.44：再拆 `TradingBasics` ⇒ App **1,949 → 1,923** ✓ 上限 1960 → 1930 ✓
    # ★ v2.9.45：再拆 `CostSettings` ⇒ App **1,923 → 1,893** ✓ 上限 1930 → 1900 ✓
    # ★ v2.9.46：再拆 `ExecutionLimits`（交易设置三组齐了 ✓）⇒ App **1,893 → 1,864** ✓ 上限 1875 ✓
    # ★ v2.9.47：再拆 `TrainTestSplit`（滚动训练 ✓）⇒ App **1,864 → 1,796** ✓ 上限 1805 ✓
    # ★ v2.9.48：任务卡片动作抽成 `useTaskActions` hook（**逻辑**而非 JSX ✓）
    #   ⇒ App **1,796 → 1,734** ✓ 上限 1745 ✓
    # ★ v2.9.49：结果区展示态推导抽成纯函数 `resolveResultView` ✓
    #   ⇒ App **1,734 → 1,718** ✓ 上限 → **1730** ✓
    (APP, 1730),
    (MODAL, 1600),
])
def test_component_size_ratchet(path, cap):
    """组件行数**不许再涨** ✓（当前值即上限 ✓；拆的时候把 cap 一起调小 ✓）。

    ⚠ 这是"只许变好"的棘轮 ✓：拆分后记得把这里改小 ✓，否则下一个人会以为可以随便加 ✗。
    """
    n = len(_read(path).splitlines())
    assert n <= cap, "%s 已 %d 行（上限 %d ✗）⇒ 该拆了 ✓" % (
        os.path.basename(path), n, cap)
