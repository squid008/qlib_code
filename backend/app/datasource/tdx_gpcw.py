# -*- coding: utf-8 -*-
"""通达信财务数据包（`gpcw<报告期>.zip`）读取器（2026-10-09）。

## 文件格式（实测 `gpcw20260630.zip`：13,077,504 = 20 + 5572×11 + 5572×2336，逐字节对上 ✓）
```
头 20 字节  : struct '<1hI1H3L'
              [0] int16  版本(1)
              [1] uint32 报告期(如 20260630)
              [2] uint16 股票数 N
              [3] uint32 索引项长度(11)
              [4] uint32 单条记录长度(2336 = 584 × float32)
              [5] uint32 保留(0)
索引表      : N × 11 字节 '<6s1c1L' —— 6B 代码(ASCII) + 1B 标志 + 4B 该股数据块**偏移**
数据区      : N × 2336 字节 = 584 个 float32（同一报告期，各股字段顺序相同）
```
字段顺序与中文名：社区已知表（`mootdx/financial/columns.py`，581 个名字）。**本项目只取用到的下标**
（下面 `IDX_*`，每一个都用已知值核对过：茅台 2026q1 EPS 21.76 / 加权ROE 10.57 / 归母净利 272.4 亿 /
营收 539.1 亿 / 毛利率 89.76% 全部逐位吻合 ✓）—— 这样既不依赖第三方库，也不怕那张表将来变 ✓。

⚠ 已知缺限（务必记住）：gpcw **没有公告日**、也**没有逐次修正快照**（每次下载都是"按最新口径回填"）
⇒ 不能做到严格 PIT（见 `tools/dump_finance_tdx.py` 顶部的"可用日推定"说明）✗✓。
"""
from __future__ import annotations

import glob
import os
import re
import struct
import zipfile
from typing import Dict, Iterator, List, Optional, Tuple

import numpy as np

HEADER = "<1hI1H3L"
INDEX_ITEM = "<6s1c1L"

# ---- 用到的字段下标（= mootdx/financial/columns 里的顺序 - 1；每个都核对过 ✓）----
IDX_EPS = 0              # 基本每股收益（元）
IDX_BPS = 3              # 每股净资产（元）
IDX_TOTAL_ASSETS = 39    # 资产总计（元）
IDX_SHARES = 237         # 总股本（股）
IDX_EQUITY_PARENT = 270  # 归属于母公司股东权益（元）
IDX_REVENUE = 229        # 营业收入（元）
IDX_COST = 74            # 营业成本（元）
IDX_OP_PROFIT = 230      # 营业利润（元）
IDX_NET_PROFIT = 94      # 净利润（元，含少数股东）
IDX_NP_PARENT = 231      # 归属于母公司所有者的净利润（元）
IDX_GROSS_MARGIN = 201   # 销售毛利率（%）（通达信自己算好的，与益盟同口径 ✓）
IDX_ROE_WEIGHTED = 280   # 加权净资产收益率（%）（与米筐 return_on_equity_weighted_average 逐位一致 ✓）
IDX_NP_TTM = 275         # 近一年净利润（元）—— PE(TTM) 用
# ★ 2026-10-09：自由现金流用（⚠ 这两个是**年内累计**，与营收/净利的"单季"口径**不同** ✗✓
#   实测：300750 2026q2 的 106 == 米筐同报告期 `cash_flow_from_operating_activities`（比值 1.0000 ✓））
IDX_OCF = 106            # 经营活动产生的现金流量净额（元，年内累计）
IDX_CAPEX = 113          # 购建固定资产、无形资产和其他长期资产支付的现金（元，年内累计）

WANT = {
    "eps": IDX_EPS,
    "bps": IDX_BPS,
    "total_assets": IDX_TOTAL_ASSETS,
    "shares": IDX_SHARES,
    "equity_parent": IDX_EQUITY_PARENT,
    "revenue": IDX_REVENUE,
    "cost": IDX_COST,
    "op_profit": IDX_OP_PROFIT,
    "net_profit": IDX_NET_PROFIT,
    "np_parent": IDX_NP_PARENT,
    "gross_margin": IDX_GROSS_MARGIN,
    "roe": IDX_ROE_WEIGHTED,
    "np_ttm": IDX_NP_TTM,
    "ocf": IDX_OCF,          # 经营现金流净额（年内累计）
    "capex": IDX_CAPEX,      # 资本开支（年内累计）
}

DEFAULT_CW_DIR = os.environ.get("TDX_CW_DIR", r"D:\new_tdx\vipdoc\cw")

_PACK_RE = re.compile(r"gpcw(\d{8})\.zip$", re.I)


def list_packs(cw_dir: str = DEFAULT_CW_DIR) -> List[Tuple[int, str]]:
    """列出全部财务包 `[(报告期 int, 路径)]`，按报告期升序。"""
    out: List[Tuple[int, str]] = []
    for p in glob.glob(os.path.join(cw_dir, "gpcw*.zip")):
        m = _PACK_RE.search(os.path.basename(p))
        if not m:
            continue
        out.append((int(m.group(1)), p))
    out.sort()
    return out


def _read_bytes(path: str) -> bytes:
    if path.lower().endswith(".zip"):
        with zipfile.ZipFile(path) as z:
            names = [n for n in z.namelist() if n.lower().endswith(".dat")]
            if not names:
                raise ValueError("压缩包里没有 .dat：%s" % path)
            return z.read(names[0])
    with open(path, "rb") as f:
        return f.read()


def read_pack(path: str, fields: Optional[Dict[str, int]] = None) -> Tuple[int, Dict[str, np.ndarray]]:
    """读一个报告期包 → `(报告期 int, {字段名: 各股数值数组})`。

    ⚠ 只取 `WANT` 里那几个下标（584 个字段全读会白吃内存 ✗：一个包 5572×584×4B ≈ 13MB，
      148 个包就是 1.9GB ✓）。
    """
    fields = WANT if fields is None else fields
    raw = _read_bytes(path)
    hs = struct.calcsize(HEADER)
    _ver, report_date, n, _idx_len, rec_len, _res = struct.unpack(HEADER, raw[:hs])
    nf = rec_len // 4
    item = struct.calcsize(INDEX_ITEM)
    codes: List[str] = []
    out: Dict[str, List[float]] = {k: [] for k in fields}
    for i in range(n):
        off = hs + i * item
        code, _flag, foa = struct.unpack(INDEX_ITEM, raw[off:off + item])
        if foa + rec_len > len(raw):
            continue
        arr = np.frombuffer(raw, dtype="<f4", count=min(nf, max(fields.values()) + 1), offset=foa)
        codes.append(code.decode("ascii", "ignore"))
        for k, idx in fields.items():
            out[k].append(float(arr[idx]) if idx < arr.size else np.nan)
    res = {k: np.asarray(v, dtype=np.float64) for k, v in out.items()}
    res["__codes__"] = np.asarray(codes, dtype=object)      # type: ignore[assignment]
    return int(report_date), res


def quarter_of(report_date: int) -> str:
    """20260630 → '2026q2'。"""
    y, md = divmod(int(report_date), 10000)
    q = {3: 1, 6: 2, 9: 3, 12: 4}.get(md // 100, 4)
    return "%dq%d" % (y, q)


def iter_packs(cw_dir: str = DEFAULT_CW_DIR) -> Iterator[Tuple[int, Dict[str, np.ndarray]]]:
    for report_date, path in list_packs(cw_dir):
        yield read_pack(path)
