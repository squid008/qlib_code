# -*- coding: utf-8 -*-
"""`batch_even`（按批次分片）单元测试（v1.19.76）。

背景（用户 2026-09-16 转述同事的选股文件口径）：
  `持仓周期 W_0..W_4` = 资金分 5 份（各 20%）、按星期几分批；今天选出、下周同一时间比对调仓
  （卖移出的、买新增的、**留下的不动**）；重复行 = **有意加仓**（= 多份权重）；按次日开盘价买卖，
  且**涨跌停/停牌**按"买不进就放弃、卖不出就顺延"处理。

这里用合成面板锁住这些语义（不需要行情数据）。
"""
import numpy as np
import pandas as pd

from app.signals.engine import run_backtest

DATES = pd.bdate_range("2022-01-03", periods=8)


def _panel(px: dict, lu: dict = None, ld: dict = None, susp=None) -> dict:
    """px: {code: [每日价...]}；lu/ld: {code: [每日涨停价/跌停价...]}；susp: [(code, 日序)] 停牌。"""
    codes = sorted(px)
    C = pd.DataFrame({c: list(px[c]) for c in codes}, index=DATES, dtype=float)
    O = C.shift(1).fillna(C)
    CR, OR = C.copy(), O.copy()
    for (c, i) in (susp or []):
        CR.iloc[i, CR.columns.get_loc(c)] = np.nan
        OR.iloc[i, OR.columns.get_loc(c)] = np.nan
    nan = [np.nan] * len(DATES)
    LU = pd.DataFrame({c: list((lu or {}).get(c, nan)) for c in codes}, index=DATES, dtype=float)
    LD = pd.DataFrame({c: list((ld or {}).get(c, nan)) for c in codes}, index=DATES, dtype=float)
    return {"CALENDAR": pd.DataFrame(index=DATES), "CLOSE": C, "OPEN": O,
            "CLOSE_RAW": CR, "OPEN_RAW": OR, "LIMIT_UP": LU, "LIMIT_DOWN": LD}


def _sig(rows) -> pd.DataFrame:
    """rows: [(日序, code, bucket, 份数)]（全部买入信号；顺序即解析器输出）。"""
    return pd.DataFrame([{"date": DATES[d], "code": c, "side": 1, "name": "",
                          "raw_date": "", "raw_code": "", "bucket": b, "weight": w}
                         for d, c, b, w in rows])


def _run(rows, panel, **kw):
    bt = run_backtest(_sig(rows), panel, hold_days=5, fill="t1_open", cost=0.0,
                      capital=1_000_000.0, strict_limit=kw.pop("strict_limit", True),
                      alloc_modes=("batch_even",), **kw)
    return bt


def _trades(bt):
    return bt.trades if len(bt.trades) else pd.DataFrame(columns=["date", "code", "side", "amount"])


def _touching(bt, code):
    t = _trades(bt)
    return t[t["code"] == code] if len(t) else t


def _sells_of(bt, code):
    t = _touching(bt, code)
    return t[t["side"] == "卖"] if len(t) else t


class TestBatchSplit:
    def test_each_batch_gets_equal_share_and_kept_names_untouched(self):
        """两批各 50%；A 批刷新时只动移出/新增，**留下的不动**（X 全程无成交，B 批不被牵连）。"""
        px = {c: [10.0 + 0.01 * i for i in range(len(DATES))] for c in ("X", "Y", "Z", "W")}
        panel = _panel(px)
        rows = [(0, "X", "A", 1), (0, "Y", "A", 1), (0, "Z", "B", 1),
                (3, "X", "A", 1), (3, "W", "A", 1)]          # A 批第 4 天刷新：Y 出、W 进
        bt = _run(rows, panel)

        # 刷新在第 4 天（day3）⇒ 成交在第 5 天（day4，次日开盘）；X 只有最初那一次买入，
        # 之后**不再被触碰**（他的口径：留下的不动）
        x = _touching(bt, "X")
        assert len(x) == 1 and x.iloc[0]["side"] == "买", "留下的票不该被重复买卖"
        assert list(x["date"]) == [str(DATES[1].date())]
        assert set(_touching(bt, "Y")["side"]) == {"买", "卖"}      # Y 被移出 ⇒ 卖掉
        assert set(_touching(bt, "W")["side"]) == {"买"}            # W 新增 ⇒ 买入
        assert len(_touching(bt, "Z")) == 1, "B 批不该被 A 批的刷新牵连"

        d = bt.diag["batch"]
        assert d["n_batches"] == 2 and abs(d["share_each"] - 0.5) < 1e-9
        assert sorted(d["batches"]) == ["A", "B"]

    def test_batch_cash_not_shared(self):
        """批间资金互不挪用：把 A 批的名单扩大一倍，B 批的期末现金**完全不变**。"""
        px = {c: [10.0] * len(DATES) for c in ("X", "Y", "Z")}
        panel = _panel(px)
        small = _run([(0, "X", "A", 1), (0, "Z", "B", 1)], panel)
        big = _run([(0, "X", "A", 1), (0, "Y", "A", 1), (0, "Z", "B", 1)], panel)
        assert (small.diag["batch"]["cash_end"]["B"]
                == big.diag["batch"]["cash_end"]["B"]), "A 批多买一只不该动到 B 批的钱"
        assert (small.diag["batch"]["holdings_end"]["A"]
                < big.diag["batch"]["holdings_end"]["A"]), "A 批自己的持仓只数应随名单变化"

    def test_weight_is_units(self):
        """份数 = 权重：同一批里 X 出现 2 次（有意加仓）⇒ X 买入金额≈Y 的 2 倍。"""
        px = {c: [10.0] * len(DATES) for c in ("X", "Y")}
        bt = _run([(0, "X", "A", 2), (0, "Y", "A", 1)], _panel(px))
        amt = {r["code"]: r["amount"] for _, r in _trades(bt)[_trades(bt)["side"] == "买"].iterrows()}
        assert amt["X"] > amt["Y"] * 1.8, "2 份的票买入金额应≈1 份的 2 倍（整手会有小差）"


class TestLimitsRemainStrict:
    def test_limit_up_buy_gives_up(self, ):
        """涨停（收盘封板）买不进 ⇒ 放弃该笔，且不动别的批。"""
        px = {c: [10.0] * len(DATES) for c in ("X", "W")}
        lu = {"W": [10.0] * len(DATES)}                     # W 在成交日收盘=涨停价 ⇒ 买不进
        bt = _run([(0, "X", "A", 1), (0, "W", "B", 1)], _panel(px, lu=lu))
        assert len(_touching(bt, "W")) == 0
        rj = bt.rejects
        assert len(rj) and (rj["reason"] == "limit_up").any()
        assert bt.diag["batch"]["cash_end"]["B"] == 500_000.0, "被拒的批现金留着（不跨批挪用）"

    def test_limit_down_sell_defers_then_sells(self):
        """跌停卖不出 ⇒ 顺延到下一个能卖的日子（这里第 5 天跌停、第 6 天卖出）。"""
        px = {c: [10.0] * len(DATES) for c in ("X", "Y")}
        ld = {"Y": [np.nan] * len(DATES)}
        ld["Y"][5] = 10.0                                   # 第 6 天（成交日）跌停封死
        rows = [(0, "X", "A", 1), (0, "Y", "A", 1), (4, "X", "A", 1)]   # 第 5 天刷新 ⇒ Y 出
        bt = _run(rows, _panel(px, ld=ld))
        sells = _sells_of(bt, "Y")
        assert len(sells) == 1
        assert sells.iloc[0]["date"] == str(DATES[6].date()), "应顺延到跌停打开的次日"
        assert (bt.rejects["reason"] == "limit_down").any()

    def test_suspended_buy_rejected_and_sell_deferred(self):
        """停牌：买入日停牌 ⇒ 放弃（suspended）；卖出日停牌 ⇒ 顺延。"""
        px = {c: [10.0] * len(DATES) for c in ("X", "Y", "W")}
        panel = _panel(px, susp=[("W", 1), ("Y", 5)])       # 第 2 天 W 停牌；第 6 天 Y 停牌
        rows = [(0, "X", "A", 1), (0, "Y", "A", 1), (0, "W", "B", 1), (4, "X", "A", 1)]
        bt = _run(rows, panel)
        assert len(_touching(bt, "W")) == 0
        assert (bt.rejects["reason"] == "suspended").any()
        y_sells = _sells_of(bt, "Y")
        assert len(y_sells) == 1 and y_sells.iloc[0]["date"] == str(DATES[6].date())

    def test_strict_limit_off_allows_trading(self):
        """关掉严格口径 ⇒ 涨停也买得进（对照）。"""
        px = {c: [10.0] * len(DATES) for c in ("W",)}
        lu = {"W": [10.0] * len(DATES)}
        bt = _run([(0, "W", "A", 1)], _panel(px, lu=lu), strict_limit=False)
        assert len(_touching(bt, "W")) == 1
