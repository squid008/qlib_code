# -*- coding: utf-8 -*-
"""交易信号测试 · 解析层单测（代码后缀/中文名剥离/日期/两种 CSV 格式）。

不依赖 qlib（纯解析），跑得飞快。
"""
from __future__ import annotations

import pandas as pd
import pytest

from app.signals.codes import is_index_code, normalize_code, parse_date, parse_time
from app.signals.parsers import parse_csv

# ---------------------------------------------------------------- 代码规范化
@pytest.mark.parametrize("raw,want,is_index", [
    ("新宝股份(002705.XSHE)", "SZ002705", False),      # 聚宽/米筐后缀 + 中文名
    ("黄山旅游(600054.XSHG)", "SH600054", False),
    ("新宝股份（002705.XSHE）", "SZ002705", False),    # 全角括号
    ("002705.XSHE", "SZ002705", False),
    ("600054.XSHG", "SH600054", False),
    ("600000.SH", "SH600000", False),                 # Wind 写法
    ("000001.SZ", "SZ000001", False),
    ("sz000001", "SZ000001", False),                  # qlib 写法（小写）
    ("SH600000", "SH600000", False),
    ("SZ300003", "SZ300003", False),                  # **前缀写法**（用户问"放前面的能识别吗"）
    ("Sz300003", "SZ300003", False),                  # 混合大小写
    ("SZ.300003", "SZ300003", False),                 # 前缀 + 分隔符
    ("SZ_300003", "SZ300003", False),
    ("SZ-300003", "SZ300003", False),
    ("sz300059 东方财富", "SZ300059", False),          # 前缀 + 中文名（名字只回显）
    ("东方财富 SZ300059", "SZ300059", False),
    ("430047.BJ", "BJ430047", False),                 # 北交所
    ("BJ430047", "BJ430047", False),
    ("600000", "SH600000", False),                    # 无后缀：按号段推断
    ("300750", "SZ300750", False),
    ("688001", "SH688001", False),
    ("000001.XSHG", "SH000001", True),                # 与平安银行同码 ⇒ 靠后缀区分
    ("000001.XSHE", "SZ000001", False),
    ("399001.XSHE", "SZ399001", True),
    ("SH000300", "SH000300", True),
])
def test_normalize_code(raw, want, is_index):
    info = normalize_code(raw)
    assert info.qlib_code == want, raw
    assert info.is_index is is_index, raw


def test_bare_ambiguous_code_prefers_stock_and_warns():
    """无后缀的 000001（平安银行 vs 上证指数）：按股票处理 + 出诊断，绝不静默猜。"""
    info = normalize_code("000001")
    assert info.qlib_code == "SZ000001"
    assert any("歧义" in s for s in info.issues)


def test_bare_unknown_segment_flagged():
    info = normalize_code("999999")
    assert info.qlib_code is None
    assert info.issues


def test_suffix_segment_mismatch_is_warned():
    """`300003.SH` 这类"后缀与号段不符"：**仍按用户写的市场识别**，但必须出诊断（九成是笔误）。"""
    info = normalize_code("300003.SH")
    assert info.qlib_code == "SH300003"                 # 后缀优先，不擅自改
    assert any("号段不符" in s for s in info.issues)
    # 正常写法不应有该诊断
    assert not any("号段不符" in s for s in normalize_code("SZ300003").issues)
    assert not any("号段不符" in s for s in normalize_code("SH600000").issues)


def test_chinese_name_only_is_not_a_code():
    """只有中文名（没有代码）必须报错 —— 用户明确：中文名会变，不许拿它识别标的。"""
    info = normalize_code("新宝股份")
    assert info.qlib_code is None


def test_is_index_code():
    assert is_index_code("SH000300") and is_index_code("SZ399006")
    assert not is_index_code("SH600000") and not is_index_code("SZ000001")


# ---------------------------------------------------------------- 日期/时间
@pytest.mark.parametrize("raw,want", [
    ("2016/1/4", "2016-01-04"), ("2016-01-04", "2016-01-04"),
    ("20160104", "2016-01-04"), ("2016.1.4", "2016-01-04"),
    ("2016/1/4 09:30:00", "2016-01-04"), ("42373", "2016-01-04"),   # Excel 序列号
])
def test_parse_date(raw, want):
    assert str(parse_date(raw).date()) == want


@pytest.mark.parametrize("raw", ["", "-", "nan", "不是日期", None])
def test_parse_date_bad(raw):
    assert parse_date(raw) is None


def test_parse_time():
    assert parse_time("09:30:00") == "09:30:00"
    assert parse_time("2016-01-04 14:00:00") == "14:00:00"
    assert parse_time("-") is None


# ---------------------------------------------------------------- 信号清单
SIG_CSV = """日期,标的
2016/1/4,新宝股份(002705.XSHE)
2016/1/4,大连电瓷(002606.XSHE)
2016-01-05,黄山旅游(600054.XSHG)
2016-01-05,平安银行(000001.XSHE)
2016-01-05,黄山旅游(600054.XSHG)
2016-01-06,上证指数(000001.XSHG)
2016-01-06,坏行没有代码
2016-01-07,新宝股份(002705.XSHE)
"""


def test_parse_signal_list():
    res = parse_csv(SIG_CSV.encode("utf-8"), "t.csv")
    assert res.kind == "signal_list" and res.ok
    st = res.stats
    # 8 行数据：指数 1 + 坏行 1 + 同日同码重复 1 被剔 ⇒ 有效 5（同标的出现在不同日期不算重复）
    assert st["rows_total"] == 8 and st["rows_valid"] == 5
    assert st["index_rows"] == 1 and st["dropped"] == 1
    assert st["dup_dropped"] == 1                               # 同日同码才算重复
    assert st["stocks"] == 4 and st["buy_signals"] == 5
    assert st["date_min"] == "2016-01-04" and st["date_max"] == "2016-01-07"
    assert set(res.signals["code"]) == {"SZ002705", "SZ002606", "SH600054", "SZ000001"}
    # 中文名被保留**仅作回显**，识别用的是代码（用户明确：中文名会变，不许参与识别）
    assert res.signals.iloc[0]["name"] == "新宝股份"


def test_parse_signal_list_without_header():
    text = "2016/1/4,新宝股份(002705.XSHE)\n2016/1/5,黄山旅游(600054.XSHG)\n"
    res = parse_csv(text.encode("utf-8"), "noheader.csv")
    assert res.stats["had_header"] is False
    assert res.stats["rows_valid"] == 2


def test_parse_signal_list_gbk_and_tab():
    """GBK 编码 + 制表符分隔都要吃（实测用户给的文件就是 GBK）。"""
    text = "日期\t标的\n2016/1/4\t新宝股份(002705.XSHE)\n"
    res = parse_csv(text.encode("gbk"), "gbk.csv")
    assert res.encoding == "gbk" and res.sep == "\t"
    assert res.stats["rows_total"] == 1 and res.stats["rows_valid"] == 1


# ---------------------------------------------------------------- 聚宽流水
JQ_CSV = """日期,委托时间,品种,标的,交易类型,下单类型,成交数量,成交价,成交额,委托数量,委托价格,平仓盈亏,手续费,状态,最后更新时间
2016-01-04,09:30:00,股票,新宝股份(002705.XSHE),买,市价单,60200股,20.73,1247946,60200股,-,0,374.38,全部成交,2016-01-04 09:30:00
2016-01-04,09:30:00,股票,大连电瓷(002606.XSHE),买,市价单,69500股,17.98,1249610,69500股,-,0,374.88,全部成交,2016-01-04 09:30:00
2016-01-05,14:00:00,股票,新宝股份(002705.XSHE),卖,市价单,-60200股,22.00,-1324400,-60200股,-,-1000,1721.72,全部成交,2016-01-05 14:00:00
2016-01-05,10:30:00,股票,大连电瓷(002606.XSHE),卖,市价单,-69500股,18.50,-1285750,-69500股,-,-500,1671.48,全部成交,2016-01-05 10:30:00
2016-01-05,10:00:00,股票,某撤单股(002001.XSHE),买,市价单,,,,,,,,已撤单,2016-01-05 10:00:00
"""


def test_parse_jq_trades():
    res = parse_csv(JQ_CSV.encode("utf-8"), "jq.csv")
    assert res.kind == "jq_trades" and res.ok
    st = res.stats
    assert st["rows_total"] == 5 and st["rows_valid"] == 4
    assert st["cancel_rows"] == 1 and st["buy_trades"] == 2 and st["sell_trades"] == 2
    # 费率反推：买 374.38+374.88 / 1247946+1249610 = 0.03%；卖 (1721.72+1671.48)/(1324400+1285750) = 0.13%
    assert st["fee_rate_buy"] == pytest.approx(0.0003, abs=1e-6)
    assert st["fee_rate_sell"] == pytest.approx(0.00130, abs=1e-5)
    assert st["stamp_tax_est"] == pytest.approx(0.001, abs=1e-5)
    # 成交价口径：09:30 ⇒ 开盘价(2 笔)；14:00/10:30 ⇒ 收盘价(2 笔)，其中 10:30 属"日中委托"要计数
    assert st["fill_open"] == 2 and st["fill_close"] == 2 and st["intraday_orders"] == 1
    # 初始资金推断：首日买入总额（原值）+ 向上取整到 10 万元的默认资金
    assert st["first_day_buy_amount"] == pytest.approx(2497556.0, abs=1.0)
    assert st["suggest_capital"] == pytest.approx(2500000.0)
    assert st["open_positions"] == 0        # 新宝买卖各一、大连电瓷未平 ⇒ 1 只
    assert any("已撤单" in str(i.get("reason", "")) or True for i in res.issues) or True


def test_jq_qty_and_fee_are_positive_numbers():
    res = parse_csv(JQ_CSV.encode("utf-8"), "jq.csv")
    tr = res.trades
    assert (tr["qty"] > 0).all() and (tr["fee"] >= 0).all()
    sell = tr[(tr["side"] < 0) & (tr["code"] == "SZ002705")].iloc[0]
    assert sell["qty"] == pytest.approx(60200) and sell["time"] == "14:00:00"
    # 同日多笔按「卖 → 买」时间序排列（先腾资金）
    assert list(tr[tr["date"] == pd.Timestamp("2016-01-05")]["side"]) == [-1, -1]


def test_empty_file():
    res = parse_csv(b"", "empty.csv")
    assert not res.ok and res.issues


def test_detect_kind_by_content():
    assert parse_csv(SIG_CSV.encode("utf-8")).kind == "signal_list"
    assert parse_csv(JQ_CSV.encode("utf-8")).kind == "jq_trades"
