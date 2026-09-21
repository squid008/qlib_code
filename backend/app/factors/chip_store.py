# -*- coding: utf-8 -*-
"""把筹码派生字段（`$chip_*`）**物化成真正的字段文件**（v1.19.85）。

## 为什么必须物化
`$chip_cost_95` / `$chip_win_close` 原本只在**面板求值器**（`panel_expr.PanelEvaluator.field`）
里"按需计算"。但公式还可能走**另一条路**：

- 单因子测试 / 事件研究 ⇒ 走面板求值器 ✓（能算 ✓）
- **回测 / 训练的特征加载**（qlib `D.features` / `handler.py` 的 feature config）⇒ 走 **qlib** ✗
  ⇒ qlib 把 `$chip_cost_95` 当普通字段去读 `features/<inst>/chip_cost_95.day.bin`，
  文件不存在 ⇒ 返回**长度 0 的序列** ⇒ 报
  `np.greater(series_left, series_right) ... (6337, 0)` / `Can only compare identically-labeled Series objects`
  （用户 2026-09-17 实测：黏合强突破公式在回测里就撞这个）。

⇒ 解决：**一次性算好并落成 `.day.bin`**（格式与其它字段一致：`[起始日历下标(float32), 值…]`，
停牌日为 NaN、按日历连续）⇒ 之后两条路都能像读普通字段一样用它 ✓，且**零额外计算** ✓。

## 口径
- 与面板派生字段**同一份计算**（直接复用它：`PanelEvaluator.field("$chip_…")`）⇒ 两条路数值一致 ✓；
- `read_start`（预热起点）可以早于 `start_time`：筹码是**递推**状态，起点越早越准
  （落盘只覆盖 `[start_time, end_time]`，但状态从 `read_start` 起算）。
- **默认只在文件不存在时写入** ✓；`overwrite=True`（`materialize_chip.py --overwrite`）⇒
  **全池重算** ✓（★ v1.20.44 起支持 —— 修了筹码口径后**必须**这样重跑一次 ✗，
  否则"没有 `chip_cost_95` 才处理"的过滤会让它**什么都不做** ✗，慎漏 ✓）。
- ★★ v1.20.45 **物化口径语义戳**（`_chip_meta.json` + `CHIP_SEMANTICS` ✓）：筹码口径
  （换手率单位 / 衰减 / 网格 / 预热 ✗）**一改就必须重算 bin** ✗，而"拿旧口径物化的 bin"
  是**静默错误** ✗（数值看着合理，只是分布塌缩/偏移 ✗ —— 2026-09-21 那次"换手率放大 100 倍"
  就是这种 ✗，且**四档依然单调** ✗，常规检查查不出 ✓）。⇒ 物化时写戳 ✓、加载/启动时比对 ✓
  ⇒ 一旦不一致就**响亮报错** ✓（见 `chip_meta_state()`，`/api/version` 与
  `tools/verify_materialized.py` 都会报 ✓）。
"""
from __future__ import annotations

import json
import os
import time
from typing import Dict, Iterable, List, Optional, Sequence

import numpy as np
import pandas as pd

from .panel_expr import _CHIP_BASE_FIELDS, PanelEvaluator, _calendar, chip_turn_of

# 默认物化清单（覆盖用户公式用到的档位；要更多档位加进这个元组即可）
DEFAULT_FIELDS = ("chip_cost_5", "chip_cost_30", "chip_cost_75", "chip_cost_95",
                  "chip_win_close", "chip_win_high", "chip_win_low")

# ★★ v1.20.45：**物化口径语义版本** —— 只要"筹码怎么算"变了就必须**递增** ✗ 并重跑物化 ✓。
#   语义清单（改任一项都要递增 + 重物化 ✗）：
#     · 换手率单位/来源（`chip_turn_of` 的 `/100`、手/股校准 ✓）
#     · `chip_run` 的网格 / 衰减 / 峰值注入 / `turn_unit` 判据 ✓
#     · 预热窗口 `read_start` ✓
#   历史：`1.20.44` = 换手率单位修正（`turn` 百分数 `/100` ✓ + auto 判据 `max>1` ✓）
#         + 反推路径逐股手/股校准 ✓。**此前的任何物化都视为过期** ✗（没有戳 = 过期 ✓）。
CHIP_SEMANTICS = "1.20.44"
CHIP_META_NAME = "_chip_meta.json"


def chip_meta_path() -> str:
    from ..config import QLIB_PROVIDER_URI
    return str(QLIB_PROVIDER_URI).rstrip("/\\") + "/features/" + CHIP_META_NAME


def write_chip_meta(payload: Dict) -> None:
    """写物化口径戳（物化结束时调用 ✓）。失败不抛（只影响"可发现性" ✗，不影响数据 ✓）。"""
    try:
        p = chip_meta_path()
        payload = dict(payload or {})
        payload["chip_semantics"] = CHIP_SEMANTICS
        payload["written_at"] = time.strftime("%Y-%m-%d %H:%M:%S")
        os.makedirs(os.path.dirname(p), exist_ok=True)
        with open(p, "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False, indent=2, sort_keys=True)
    except Exception:                                     # noqa: BLE001
        pass


def chip_meta_state() -> Dict:
    """`chip_*` 物化戳是否与当前代码口径一致 ✓ —— **给"换机器 / 拉代码"用** ✓。

    返回 `{ok, state, message, ...}`：
      · `state="ok"`      ⇒ 物化口径与代码一致 ✓；
      · `state="missing"` ⇒ **没有戳** ✗ ⇒ 要么没物化过、要么戳是 v1.20.45 之前的老版本 ✓
        ⇒ **必须跑** `python backend/tools/materialize_chip.py 400 --overwrite` ✗；
      · `state="stale"`   ⇒ 戳在、但口径版本不同 ✗ ⇒ 同样必须重物化 ✓。
    ⚠ 为什么值得这么麻烦：旧口径的 bin **不会报错、也查不出异常** ✗（分布塌缩但单调 ✓）
      ⇒ 只能靠"戳"来发现 ✓（另有 `/verify_materialized.py` 的**展开比体检**做经验兜底 ✓）。
    """
    import numpy as _np
    info: Dict = {"ok": False, "state": "missing", "expected": CHIP_SEMANTICS,
                  "path": "", "message": ""}
    try:
        from ..config import QLIB_PROVIDER_URI
        fdir = str(QLIB_PROVIDER_URI).rstrip("/\\") + "/features"
        info["path"] = fdir + "/" + CHIP_META_NAME
        n_chip = 0
        try:                                              # 覆盖度（顺带报出来 ✓）
            for n in os.listdir(fdir):
                if os.path.exists(os.path.join(fdir, n, "chip_cost_95.day.bin")):
                    n_chip += 1
        except Exception:                                 # noqa: BLE001
            pass
        info["n_chip_cost_95"] = n_chip
        p = chip_meta_path()
        if not os.path.exists(p):
            info["message"] = (
                "⚠ chip_* 物化戳缺失（%s）⇒ **无法确认筹码是用当前口径物化的** ✗。"
                "若是 pull 了新代码/换了机器，必须重物化一次："
                "`python backend/tools/materialize_chip.py 400 --overwrite`，"
                "再跑 `python backend/tools/verify_materialized.py` 核对 ✓。"
                % (CHIP_META_NAME,))
            return info
        with open(p, "r", encoding="utf-8") as f:
            meta = json.load(f)
        info["meta"] = meta
        got = str(meta.get("chip_semantics", "") or "")
        if got != CHIP_SEMANTICS:
            info["state"] = "stale"
            info["message"] = (
                "⚠ chip_* 物化口径过期 ✗：bin 是 `%s` 物化的，当前代码口径是 `%s` "
                "⇒ 筹码数值与公式口径**不一致**（静默偏差 ✗，不报错 ✓）"
                "⇒ 必须重跑：`python backend/tools/materialize_chip.py 400 --overwrite` ✓。"
                % (got or "<空>", CHIP_SEMANTICS))
            return info
        info["ok"] = True
        info["state"] = "ok"
        info["message"] = ("chip_* 物化口径与代码一致 ✓（%s；覆盖 %d 只）"
                           % (CHIP_SEMANTICS, n_chip))
        return info
    except Exception as e:                                # noqa: BLE001
        info["message"] = "chip_* 物化戳检查失败：%r" % (e,)
        return info


def _write_bin(path, first_idx: int, vals: np.ndarray) -> None:
    """写一个字段文件：首 4 字节 float32 = 起始日历下标，其后是 float32 值序列。

    与 `panel_expr._read_field_bin` 的约定**逐位对应**（该函数用 `<f4` 读首值再 int() 还原下标）。
    """
    head = np.asarray([float(first_idx)], dtype="<f4")
    body = np.asarray(vals, dtype="<f4")
    with open(path, "wb") as f:
        head.tofile(f)
        body.tofile(f)


def materialize(codes: Sequence[str], start_time: str, end_time: str,
                fields: Iterable[str] = DEFAULT_FIELDS,
                read_start: Optional[str] = None,
                overwrite: bool = False, progress_cb=None) -> Dict[str, int]:
    """把筹码字段落成 `.day.bin`（返回 `{字段: 写入股票数}`）。

    `read_start`：预热起点（建议比 `start_time` 早 ≥1 年，让筹码递推先"跑起来"）。
    `overwrite=False`：已存在的文件跳过（安全默认）。
    """
    from ..config import QLIB_PROVIDER_URI
    fdir = str(QLIB_PROVIDER_URI).rstrip("/\\") + "/features"
    want: List[str] = [f for f in fields]
    ev = PanelEvaluator(list(codes), start_time, end_time,
                        union_fields=_CHIP_BASE_FIELDS, read_start=read_start or start_time)
    cal = _calendar()
    out: Dict[str, int] = {}

    # ---- 一次递推产出全部字段（v1.19.85 性能）----
    #   原实现按字段逐个 `ev.field("$chip_…")` ⇒ 每个字段各自重跑一遍**全池递推**
    #   （实测 7 个字段 ≈ 40 分钟 ✗）。这里直接调内核：多分位一次出（`qs=`）、
    #   多价位一次出（`prices=[…]`）⇒ 2 次递推搞定 7 个字段 ✓。
    # ⚠ 记得留住**面板**（两级 index）的名字：`unstack` 之后只剩一级，写盘前要把
    #   名字换回 (instrument, datetime) ⇒ 早期误用 `c.index.names[1]` 直接 IndexError（已修）
    _panel_close = ev.field("$close")
    _pnames = list(_panel_close.index.names)
    c = _panel_close.unstack(level=0)
    h = ev.field("$high").unstack(level=0)
    l = ev.field("$low").unstack(level=0)
    t = chip_turn_of(ev).unstack(level=0)        # ★ v1.19.92：与面板**同一口径**（见下）
    from .chip_dist import chip_run
    qs = [float(n[len("chip_cost_"):]) for n in want if n.startswith("chip_cost_")]
    wins = [n[len("chip_win_"):] for n in want if n.startswith("chip_win_")]
    series_map: Dict[str, "pd.Series"] = {}
    if qs:
        res = chip_run(c.to_numpy(float), h.to_numpy(float), l.to_numpy(float),
                       t.to_numpy(float), qs=tuple(qs))
        for q in qs:
            series_map["chip_cost_%g" % q] = pd.DataFrame(
                res["cost_%g" % q], index=c.index, columns=c.columns).stack()
    if wins:
        pmat = [{"close": c, "high": h, "low": l}[w].to_numpy(float) for w in wins]
        res = chip_run(c.to_numpy(float), h.to_numpy(float), l.to_numpy(float),
                       t.to_numpy(float), prices=pmat)
        for i, w in enumerate(wins):
            key = "winner" if len(pmat) == 1 else "winner_%d" % i
            series_map["chip_win_%s" % w] = pd.DataFrame(
                res[key], index=c.index, columns=c.columns).stack()

    for name in want:
        # ⚠⚠ v1.19.92 **不要在这里批量删旧文件**：本机有 bulk-delete 安全闸 ——
        #   单次删除 ≥500 个会被拦下并**直接中断整个进程**（实测 2026-09-17 22:08 那次就死在这 ✗，
        #   stderr 里只有一行 `[safe-delete][SAFE_DELETE_BULK_CONFIRM_REQUIRED] {"count":500,…}`，
        #   而 stdout 停在启动三行 ⇒ 看起来"无声消失"，极易误判成被重启脚本杀了 ✗）。
        #   ⇒ 改为**"每只股票都写字（含全 NaN）"**：旧文件被自然覆盖 ✓，且全市场都有该字段、
        #     日期轴与 `$close` 一致 ⇒ 多股票加载不再缺字段 ✓✓（见下方 `if not len(sub)` 的注释）。
        st = series_map.get(name)
        if st is None:
            out[name] = 0
            continue
        # 面板 index 是 (instrument, datetime)；stack 出来是 (日期, 股票) ⇒ 换回面板顺序
        st.index = st.index.set_names([_pnames[1], _pnames[0]])
        # ⚠⚠ `stack()` 会**丢掉全 NaN 行** ⇒ 直接用会写出"日期不连续"的 bin（读取端按
        #   "起始下标 + 行号" 还原 ⇒ 整体错位；实测表现为 `Gt($close,$chip_cost_95)`
        #   报 "Can only compare identically-labeled Series objects"，v1.19.85 抓到）
        #   ⇒ 必须 reindex 回**面板的完整 index**（每只股票全日历、缺失为 NaN）✓
        series = st.reorder_levels(_pnames).reindex(_panel_close.index)
        n_ok = 0
        for inst in codes:
            try:
                sub = series.xs(inst, level="instrument")
            except KeyError:
                continue
            if not len(sub):
                continue
            # ★ v1.19.92：**不再跳过"全 NaN"的股票** —— 跳过会让它留着**旧文件**（旧日期轴 ✗），
            #   以及"这只股票没有该字段"⇒ 多股票一起加载时崩（`identically-labeled` ✗）。
            #   这里一律写出（全 NaN 也写 ✓），日期轴与 `$close` 完全一致 ⇒ qlib 侧永远能对齐 ✓。
            path = "%s/%s/%s.day.bin" % (fdir, inst.lower(), name)
            import os
            if os.path.exists(path) and not overwrite:
                continue
            os.makedirs(os.path.dirname(path), exist_ok=True)
            # ★★ v1.19.91：写盘的**日期轴必须与 `$close` 逐位一致**（起点 offset + 行数）。
            #   为什么（用户 2026-09-17 报：黏合强突破 / 过顶0 / 过顶 单因子测试
            #   「Can only compare identically-labeled Series objects … Gt($close,$chip_cost_95)」）：
            #   面板的 index 是 `[start_time, end_time]`（我们物化时 start=2010-01-01），
            #   而 **`$close` 的 bin 从"每只股票自己的上市起点"开始** ⇒ 两个字段的日期轴不同 ✗。
            #   · 只加载**一只**股票时看不出来（qlib 用该股自己的区间，两边被对齐到同一轴 ✓）；
            #   · **多只一起加载**时 qlib 按"多股票联合日历"对齐 ⇒ 标签不一致 ⇒ 直接报错 ✗✗
            #     （这就是"单只 OK、批量崩"的原因 ✓）。
            #   ⇒ 直接读 `$close` 的 header（首 4 字节 = 起始日历下标）与行数，把筹码值 reindex 到
            #     **同一条日期轴**上（超出面板窗口的段落为 NaN ✓）。
            close_path = "%s/%s/close.day.bin" % (fdir, inst.lower())
            if os.path.exists(close_path):
                with open(close_path, "rb") as fh:
                    first = int(np.frombuffer(fh.read(4), dtype="<f4")[0])
                n_ref = max(0, (os.path.getsize(close_path) - 4) // 4)
                sub = sub.reindex(cal[first:first + n_ref])
            else:                                     # 极端情况：没有 close 就退回旧口径
                first = int(cal.searchsorted(sub.index[0]))
            _write_bin(path, first, sub.to_numpy(dtype=float))
            n_ok += 1
        out[name] = n_ok
        if progress_cb:
            progress_cb("物化 %s：%d 只" % (name, n_ok))
    # ★ v1.20.45：写**物化口径戳** ✓ —— 让"换机器 / pull 新代码后没重物化"能被**自动发现** ✗
    #   （旧口径 bin 是静默偏差 ✗：不报错、数值看着合理、连"四档单调"都成立 ✗）
    write_chip_meta({
        "overwrite": bool(overwrite),
        "n_codes": len(list(codes)),
        "fields": list(want),
        "counts": dict(out),
        "turn_meta": dict(getattr(ev, "_chip_turn_meta", {}) or {}),
    })
    return out
