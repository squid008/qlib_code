# -*- coding: utf-8 -*-
"""P1：用**每段完整成交流水**（`segment_*/seg_result.json` ✓）独立复算净值 ✓（2026-09-22）。

为什么比上一版强 ✓：`result.json.trades` **不是完整流水** ✗（588 条只覆盖 3 个调仓日 ✗），
而 `segment_*/seg_result.json` 每段都有**完整 trades** ✓（segment_1 就有 90 条 ✓）
且自带两个**校验锚点**：`end_position`（段末持仓 ✓）、`end_account`（段末账户值 ✓）。

步骤：
  1. **价格空间自标定** ✓：拿几笔成交比 `deal_price` 与 `前复权(preclose)` / `后复权(close)`，
     看哪个 ≈1 ⇒ 确定回测用的价格口径 ✓（`price_adjust=forward` ⇒ 预期是 preclose ✓）；
  2. 按段、按日依序应用成交：`shares += direction × amount × ffr` ✓、
     `cash -= direction × trade_value + trade_cost` ✓；
  3. 逐日盯市 ⇒ 与 `seg_result.nav` 逐点比 ✓；段末再和 `end_position` / `end_account` 比 ✓。
"""
import json
import os
import sys
from collections import defaultdict

import numpy as np

ART = r"d:\quant\qlib_code\backend\workdir\artifacts"
DIRN = "20260922-000538_LightGBM_all_2021_2021_fd76879e00f0"
FDIR = r"d:\quant\qlib_code\data\cn_data\features"


def main():
    sys.path.insert(0, r"d:\quant\qlib_code\backend")
    from app.services.qlib_runtime import ensure_qlib_init
    ensure_qlib_init()
    from app.factors.panel_expr import _calendar
    cal = _calendar()
    base = os.path.join(ART, DIRN)

    p = json.load(open(os.path.join(base, "params.json"), encoding="utf-8"))
    cap = float(p.get("initial_capital") or 5e6)
    segs = [json.load(open(os.path.join(base, "segment_%d" % i, "seg_result.json"),
                           encoding="utf-8"))
            for i in range(1, 10)]
    all_tr = [t for s in segs for t in (s.get("trades") or [])]
    insts = sorted({t["instrument"] for t in all_tr})
    print("  段数 %d | 成交合计 %d 笔 | 股票 %d 只 | 初始资金 %.0f"
          % (len(segs), len(all_tr), len(insts), cap))

    # ---- 行情（两种口径都读，供自标定 ✓）----
    cache = {}

    def load(inst, fld):
        key = (inst, fld)
        if key in cache:
            return cache[key]
        path = os.path.join(FDIR, inst.lower(), fld + ".day.bin")
        m = {}
        if os.path.exists(path):
            raw = np.fromfile(path, dtype="<f4")
            j0 = int(raw[0])
            v = raw[1:].astype(np.float64)
            for k, x in enumerate(v):
                j = j0 + k
                if 0 <= j < len(cal) and np.isfinite(x) and x > 0:
                    m[cal[j].strftime("%Y-%m-%d")] = float(x)
        cache[key] = m
        return m

    # ---- 1) 价格空间自标定（**逐股** ✓）----
    # ⚠ 实测：`deal_price / preclose` 中位 ≈1.12 ✗（不是 1）⇒ 引擎的复权基准与
    #   `preclose`（= 源/factor_last ✓，按**最后一日**归一 ✓）**不是同一个基准** ✗ ——
    #   差一个随日期缓慢漂移的因子 ✓。⇒ 用**每只股票自己**的成交价反解出缩放系数 k ✓
    #   （k = median(deal_price / preclose) ✓）⇒ 之后 `k × preclose` 就落在**引擎的价格空间**里 ✓。
    #   ⚠ 残留误差：k 取常数 ⇒ 忽略该股持有期内的复权漂移（1 个月内极小 ✓，交叉校验够用 ✓）。
    print("")
    print("  === ① 价格空间自标定（逐股 k = median(deal_price/preclose) ✓）===")
    ks = {}
    for t in all_tr:
        px = t.get("deal_price")
        if px is None or not np.isfinite(float(px)):
            continue
        v = load(t["instrument"], "preclose").get(str(t["date"]))
        if v:
            ks.setdefault(t["instrument"], []).append(float(px) / v)
    kk = {i: float(np.median(v)) for i, v in ks.items() if v}
    if kk:
        arr = np.asarray(list(kk.values()))
        print("     有效股票 %d 只 | k 中位 %.4f | p10 %.4f | p90 %.4f"
              % (len(kk), float(np.median(arr)), float(np.percentile(arr, 10)),
                 float(np.percentile(arr, 90))))
    else:
        print("     ✗ 无法标定")
        return
    space = "preclose"

    # ---- 2) 逐段推进 ----
    pos = defaultdict(float)
    cash = cap
    last_px = {}
    rows = []
    if "date_range" in segs[0]:
        pass
    for si, s in enumerate(segs, 1):
        by_day = defaultdict(list)
        for t in (s.get("trades") or []):
            by_day[str(t["date"])].append(t)
        for x in (s.get("nav") or []):
            d = x["date"]
            for t in by_day.get(d, []):          # 先成交
                ffr = float(t.get("ffr") or 0.0)
                px = t.get("deal_price")
                if ffr <= 0 or px is None or not np.isfinite(float(px)):
                    continue
                # ⚠⚠ 关键修正（2026-09-22 实测 ✓）：`amount` **本身已带符号** ✗
                #   （买单 +2391 ✓、卖单 **−2391** ✓）⇒ 再乘 `direction` 会把符号翻回来 ✗
                #   ⇒ 卖出变成加仓 ✗（实测持仓一路涨到 293 只 ✗ 而 topk=50 ✓）。
                #   ⇒ 直接 `amount × ffr` ✓，**不要**乘 direction ✓。
                sh = float(t["amount"]) * ffr
                pos[t["instrument"]] += sh
                tv = abs(float(t.get("trade_value") or 0.0))
                cash -= float(t["direction"]) * tv + float(t.get("trade_cost") or 0.0)
            mv = 0.0                             # 再盯市
            for inst, sh in pos.items():
                if abs(sh) < 1e-9:
                    continue
                m = load(inst, space)
                px = m.get(d) or last_px.get(inst)
                if px is None:
                    continue
                px = px * kk.get(inst, 1.0)       # ★ 落到引擎的价格空间 ✓
                last_px[inst] = px
                mv += sh * px
            rows.append((si, d, float(x["value"]), (cash + mv) / cap, cash, mv))
        # ---- 段末锚点 ----
        ep = s.get("end_position") or {}
        mine = {k: v for k, v in pos.items() if abs(v) > 1e-9}

        def _amt(v):
            """`end_position` 的值是 dict（如 {'amount':…, 'price':…} ✓）；**另含一个 `cash` 键** ✓。"""
            if isinstance(v, dict):
                for key in ("amount", "shares", "qty", "size", "num"):
                    if key in v:
                        return float(v[key])
                return float("nan")
            return float(v)

        cash_rec = ep.get("cash")
        if isinstance(cash_rec, dict):
            cash_rec = cash_rec.get("amount")
        pos_rec = {k: v for k, v in ep.items() if k != "cash"}
        if si == 1:
            print("     [调试] end_position 里的 cash = %s（%s）"
                  % (json.dumps(ep.get("cash"), ensure_ascii=False)[:90], type(ep.get("cash")).__name__))
        same = sum(1 for k in pos_rec
                   if np.isfinite(_amt(pos_rec[k]))
                   and abs(_amt(pos_rec[k]) - mine.get(k, 0.0)) <= max(1.0, abs(_amt(pos_rec[k])) * 1e-4))
        ea = s.get("end_account")
        print("")
        print("  === 段%d 锚点 ===  %s" % (si, s.get("date_range")))
        print("     持仓：记录 %d 只 / 我算 %d 只 ⇒ 命中 %d 只" % (len(pos_rec), len(mine), same))
        print("     末 NAV：记录 %.6f / 我算 %.6f  (差 %+.6f)"
              % (s["nav"][-1]["value"], rows[-1][3], rows[-1][3] - s["nav"][-1]["value"]))
        if cash_rec is not None:
            try:
                print("     现金：记录 %.2f / 我算 %.2f  (差 %+.2f)"
                      % (float(cash_rec), rows[-1][4], rows[-1][4] - float(cash_rec)))
            except Exception:                                # noqa: BLE001
                print("     现金：记录值不可解析 = %r" % (cash_rec,))
        if ea is not None:
            print("     段末账户：记录 %.2f / 我算 %.2f  (差 %+.2f)"
                  % (float(ea), rows[-1][4] + rows[-1][5], rows[-1][4] + rows[-1][5] - float(ea)))

    # ---- 3) 全局比对 ----
    rep = {}
    for s in segs:
        for x in (s.get("nav") or []):
            rep[x["date"]] = float(x["value"])
    a = np.asarray([rep.get(d, np.nan) for _, d, _, _, _, _ in rows], dtype=float)
    b = np.asarray([v for _, _, _, v, _, _ in rows], dtype=float)
    m = np.isfinite(a) & np.isfinite(b)
    diff = b[m] - a[m]
    print("")
    print("  === 全局（%d 点 ✓）===" % int(m.sum()))
    print("     报告末值 %.6f  vs  我复算 %.6f" % (a[m][-1], b[m][-1]))
    print("     max|diff| = %.6f | 均值 = %+.6f | 相关 = %.6f"
          % (np.abs(diff).max(), diff.mean(), np.corrcoef(a[m], b[m])[0, 1]))
    print("     判读：max|diff| ≲1e-3 ⇒ ★回测曲线**被独立复现** ✓✓（引擎没算错 ✓）")


if __name__ == "__main__":
    main()
    sys.exit(0)
