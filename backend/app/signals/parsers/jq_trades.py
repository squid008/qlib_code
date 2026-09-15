# -*- coding: utf-8 -*-
"""模式②「聚宽成交明细」解析（15 列）。

实测样例（GBK，1678 行）：
    日期,委托时间,品种,标的,交易类型,下单类型,成交数量,成交价,成交额,委托数量,委托价格,平仓盈亏,手续费,状态,最后更新时间
    2016-01-04,09:30:00,股票,新宝股份(002705.XSHE),买,市价单,60200股,20.73,1247946,60200股,-,0,374.38,全部成交,2016-01-04 09:30:00

要点：
  · `品种` 是"股票"这类**类别**，不是代码 ⇒ 代码永远取 `标的`（同样剥中文名）；
  · `成交数量` 卖出行是**负数**且带"股"字 ⇒ 用数值解析 + `交易类型` 定方向（两者都留，取一致的那个）；
  · **撤单/部撤**：`状态` 含"撤"且成交为空/0 的行直接剔除（留痕）；部成部撤保留**实际成交量**；
  · **费率反推**（用户要的"自动算佣金/印花税"）：分别用 买入 / 卖出 的 `手续费 ÷ 成交额`；
  · **成交价口径**：`委托时间` ≤ 09:35 ⇒ 开盘价；≥ 13:00 ⇒ 收盘价；其余（日中委托）按**收盘价**近似
    并计数提示（日频做不到分钟级精确，用户已认可）；
  · 首日买入总额 + "现金非负"约束推出的**最小可行初始资金**，供界面提示。
"""
from __future__ import annotations

import pandas as pd

from ..codes import normalize_code, parse_date, parse_time
from .base import (AMOUNT_ALIASES, CODE_ALIASES, DATE_ALIASES, FEE_ALIASES, PRICE_ALIASES,
                   QTY_ALIASES, STATUS_ALIASES, SIDE_ALIASES, TIME_ALIASES, ParseResult,
                   decode_bytes, num, pick_col, read_table, side_of)


def parse_jq_trades(raw, filename: str = "") -> ParseResult:
    text, enc = decode_bytes(raw)
    res = ParseResult(kind="jq_trades", encoding=enc)
    df, sep, had_header = read_table(text)
    res.sep = sep
    res.headers = [str(c) for c in df.columns]

    date_col = pick_col(df, DATE_ALIASES)
    time_col = pick_col(df, TIME_ALIASES)
    code_col = pick_col(df, CODE_ALIASES)
    side_col = pick_col(df, SIDE_ALIASES)
    qty_col = pick_col(df, QTY_ALIASES)
    px_col = pick_col(df, PRICE_ALIASES)
    amt_col = pick_col(df, AMOUNT_ALIASES)
    fee_col = pick_col(df, FEE_ALIASES)
    st_col = pick_col(df, STATUS_ALIASES)
    missing = [n for n, c in (("日期", date_col), ("标的", code_col), ("成交价", px_col))
               if c is None]
    if missing:
        res.add_issue(0, " | ".join(res.headers[:10]), "缺少必要列：%s" % "、".join(missing))
        res.stats.update(rows_total=int(len(df)), rows_valid=0, dropped=1)
        return res

    rows, n_drop, n_cancel, n_mid = [], 0, 0, 0
    for i, r in enumerate(df.itertuples(index=False), start=(2 if had_header else 1)):
        rec = dict(zip(df.columns, r))
        raw_date, raw_code = rec.get(date_col), rec.get(code_col)
        status = str(rec.get(st_col) or "").strip()
        qty = num(rec.get(qty_col)) if qty_col else None
        amt = num(rec.get(amt_col)) if amt_col else None
        if "撤" in status and not qty and not amt:
            n_cancel += 1                                # 撤单（无成交）
            continue
        dt = parse_date(raw_date)
        if dt is None:
            n_drop += 1
            res.add_issue(i, "%s | %s" % (raw_date, raw_code), "日期无法识别")
            continue
        info = normalize_code(raw_code)
        if info.qlib_code is None:
            n_drop += 1
            res.add_issue(i, "%s | %s" % (raw_date, raw_code), (info.issues or ["标的无法识别"])[0])
            continue
        if info.is_index:
            n_drop += 1
            res.add_issue(i, "%s | %s" % (raw_date, raw_code), "指数行，已忽略")
            continue
        px = num(rec.get(px_col))
        if px is None or px <= 0:
            n_drop += 1
            res.add_issue(i, "%s | %s" % (raw_date, raw_code), "成交价缺失")
            continue
        sd = side_of(rec.get(side_col)) if side_col else None
        if sd is None:
            sd = 1 if (qty or 0) > 0 else (-1 if (qty or 0) < 0 else None)
        if sd is None:
            n_drop += 1
            res.add_issue(i, "%s | %s" % (raw_date, raw_code), "买卖方向无法识别")
            continue
        q = abs(qty) if qty else (abs(amt) / px if amt else None)
        if not q:
            n_drop += 1
            res.add_issue(i, "%s | %s" % (raw_date, raw_code), "成交数量缺失")
            continue
        tm = parse_time(rec.get(time_col)) if time_col else None
        # 委托时间 → 成交价口径（日频近似）
        if tm and tm <= "09:35:00":
            fill = "open"
        elif tm and tm >= "13:00:00":
            fill = "close"
        else:
            fill = "close"
            if tm:
                n_mid += 1
        rows.append({
            "date": dt, "time": tm or "", "code": info.qlib_code, "side": int(sd),
            "qty": float(q), "price": float(px),
            "amount": float(abs(amt)) if amt else float(q * px),
            "fee": float(abs(num(rec.get(fee_col)) or 0.0)) if fee_col else 0.0,
            "status": status, "fill": fill,
            "name": info.name, "raw_date": str(raw_date), "raw_code": str(raw_code),
        })

    tr = pd.DataFrame(rows)
    if len(tr):
        tr = tr.sort_values(["date", "time", "side"], ascending=[True, True, False]) \
               .reset_index(drop=True)
    res.trades = tr
    res.stats.update(_trade_stats(tr, n_drop, n_cancel, n_mid, int(len(df))))
    return res


def _trade_stats(tr: pd.DataFrame, n_drop: int, n_cancel: int, n_mid: int, n_total: int) -> dict:
    """流水统计：笔数、费率反推、初始资金推断、成交价口径分布。"""
    st = {
        "rows_total": n_total, "rows_valid": int(len(tr)), "dropped": int(n_drop),
        "cancel_rows": int(n_cancel), "intraday_orders": int(n_mid),
    }
    if not len(tr):
        return st
    buy, sell = tr[tr["side"] > 0], tr[tr["side"] < 0]
    st.update({
        "buy_trades": int(len(buy)), "sell_trades": int(len(sell)),
        "stocks": int(tr["code"].nunique()),
        "date_min": str(tr["date"].min().date()), "date_max": str(tr["date"].max().date()),
        "fill_open": int((tr["fill"] == "open").sum()),
        "fill_close": int((tr["fill"] == "close").sum()),
        "turnover": round(float(tr["amount"].sum()), 2),
        "fee_total": round(float(tr["fee"].sum()), 2),
    })
    # ---- 费率反推（用户要的"从手续费列自动算佣金/印花税"）----
    def _rate(sub):
        a = float(sub["amount"].sum())
        return round(float(sub["fee"].sum()) / a, 8) if a > 0 else None
    st["fee_rate_buy"] = _rate(buy)
    st["fee_rate_sell"] = _rate(sell)
    if st["fee_rate_buy"] is not None and st["fee_rate_sell"] is not None:
        # 卖出费率 − 买入费率 ≈ 印花税（滑点并进佣金，用户已定：简单点）
        st["stamp_tax_est"] = round(max(0.0, st["fee_rate_sell"] - st["fee_rate_buy"]), 8)
    # ---- 初始资金推断 ----
    d0 = tr["date"].min()
    first_buys = buy[buy["date"] == d0]
    st["first_day"] = str(d0.date())
    st["first_day_buy_amount"] = round(float(first_buys["amount"].sum()), 2)
    st["first_day_buy_trades"] = int(len(first_buys))
    st["suggest_capital"] = st["first_day_buy_amount"]          # 默认值：首日买入总额
    # 现金非负约束：cash(t) = C0 + Σ(卖出净额 − 买入总额) ≥ 0 ⇒ C0 ≥ −min(cum)
    flow = (tr["amount"] * tr["side"] * -1.0) - tr["fee"]       # 买 ⇒ −(额+费)；卖 ⇒ +额−费
    cum = flow.cumsum()
    st["min_capital"] = round(float(max(0.0, -cum.min())), 2)
    st["cum_flow_end"] = round(float(cum.iloc[-1]), 2)
    st["open_positions"] = _open_positions(tr)
    return st


def _open_positions(tr: pd.DataFrame) -> int:
    """期末仍有持仓的标的数（买多于卖）——流水里常见（策略没平完）。"""
    n = 0
    for _, g in tr.groupby("code"):
        if float(g["qty"].where(g["side"] > 0, 0).sum() - g["qty"].where(g["side"] < 0, 0).sum()) > 1e-6:
            n += 1
    return int(n)
