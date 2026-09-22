# -*- coding: utf-8 -*-
"""诊断：为什么按 `trades` 净额算出的持仓（59 只）比 `end_position`（34 只）多 25 只 ✗。

只需看 segment_1：把那 25 只"多出来"的股票的**逐笔成交**打出来 ✓
⇒ 若它们**只有买单、没有卖单** ⇒ 说明 `trades` 里的卖出口径与我假设的不同 ✓
  （例如卖出记在别的字段 / `direction` 语义不同 / 卖单在**其它段** ✓）。
"""
import collections
import json
import os

D = (r"d:\quant\qlib_code\backend\workdir\artifacts"
     r"\20260922-000538_LightGBM_all_2021_2021_fd76879e00f0")


def main():
    s = json.load(open(os.path.join(D, "segment_1", "seg_result.json"), encoding="utf-8"))
    tr = s["trades"]
    print("  segment_1: trades=%d  nav点=%d  date_range=%s"
          % (len(tr), len(s["nav"]), s["date_range"]))
    print("  成交日期分布 =", dict(collections.Counter(t["date"] for t in tr)))
    print("  direction 分布 =", dict(collections.Counter(t["direction"] for t in tr)))
    print("  ffr 分布 =", dict(collections.Counter(round(float(t.get("ffr") or 0), 2) for t in tr)))

    net = collections.Counter()
    filled = collections.Counter()
    for t in tr:
        ffr = float(t.get("ffr") or 0.0)
        if ffr <= 0 or t.get("deal_price") is None:
            continue
        net[t["instrument"]] += float(t["direction"]) * float(t["amount"]) * ffr
        filled[t["instrument"]] += 1
    nz = {k: v for k, v in net.items() if abs(v) > 1e-6}
    ep = {k: (v["amount"] if isinstance(v, dict) else v)
          for k, v in (s.get("end_position") or {}).items()}
    print("")
    print("  按 trades 净额 ⇒ 持仓 %d 只 ; end_position ⇒ %d 只" % (len(nz), len(ep)))
    extra = [k for k in nz if k not in ep]
    miss = [k for k in ep if k not in nz]
    print("  多出来的 = %d 只：%s" % (len(extra), extra[:10]))
    print("  缺的     = %d 只：%s" % (len(miss), miss[:10]))
    print("")
    print("  [样例 A] 多出来的前 3 只逐笔（date, direction, amount, ffr）：")
    for k in extra[:3]:
        rows = [(t["date"], t["direction"], round(float(t["amount"]), 1),
                 round(float(t.get("ffr") or 0), 2)) for t in tr if t["instrument"] == k]
        print("    %-10s 净=%.1f  笔数=%d  %s" % (k, nz[k], len(rows), rows[:8]))
    print("")
    print("  [样例 B] end_position 里有的前 3 只逐笔：")
    for k in list(ep)[:3]:
        rows = [(t["date"], t["direction"], round(float(t["amount"]), 1),
                 round(float(t.get("ffr") or 0), 2)) for t in tr if t["instrument"] == k]
        print("    %-10s 记录持仓=%.1f 我算=%.1f  笔数=%d  %s"
              % (k, float(ep[k]), nz.get(k, 0.0), len(rows), rows[:8]))
    print("")
    print("  ★ 判读：若多出来的那些只有买单、没有卖单 ⇒ trades 的卖出不在本段 ✗")
    print("          若两边 amount 口径不同（如 amount 已是成交后股数）⇒ 需改公式 ✓")


if __name__ == "__main__":
    main()
