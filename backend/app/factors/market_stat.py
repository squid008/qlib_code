# -*- coding: utf-8 -*-
"""横向统计物化（通达信 `BLOCKSETNUM` / `INSUM` 语义，2026-10-09）。

## 为什么必须"物化"（而不是在算子里现算）
这两个函数是**跨股票（横向）**统计：
- `BLOCKSETNUM('全部A股')` ⇒ 该板块**当日**成分股数（日截面）；
- `INSUM('全部A股','IS_GOLD_PIT',1,0)` ⇒ 板内所有股票某公式输出的**横向聚合**
  （计算类型：0累加 / 1平均 / 2最大 / 3最小）。

而两条求值链的能力完全不同：
- 单因子测试 / 事件研究 ⇒ 面板求值器（`panel_expr`），它本来就是"日期 × 股票"整块矩阵 ⇒ 横截面天然可做 ✓；
- **回测 / 训练** ⇒ qlib 表达式，**逐股时间序列**，根本看不见"其它股票" ✗✗。

⇒ 唯一能让**四条路都成立**的形态：**先算好，物化成"同日全市场同值"的字段 bin**
（`features/{code}/mkt_*.day.bin`）—— 与筹码 `COST/WINNER` 物化（`chip_store.py`）同一套路 ✓；
之后它是普通字段，面板侧 `field()` 读、qlib 侧当特征读，**两条路逐位一致** ✓。

## 口径（与用户 2026-10-09 确认）
- **板块成分按当日真实成分**取（`instruments/{all,csi300,csi500,csi800,csi1000}.txt` 自带起止日、
  一只股票可有进出池多段）⇒ 历史成分变更**不穿越**（无未来函数）✓；
- **`全部A股` = 全部在市 A 股（含 ST、含停牌，不含退市）** ✓ —— 停牌股**算在分母里**，
  其指标值当日按 0 计（与通达信"成分股数"口径一致 ✓）；`沪深A股` = 同上但**剔除北交所** ✓；
- 峰值/均值等聚合**忽略 NaN**（停牌股在"累加"里算 0，不会把整天的值污染成 NaN ✓）。

## 幂等与可发现性
- `overwrite=False`（默认）时已存在的 bin 跳过；改了口径/被调公式 ⇒ `--overwrite` 全量重写 ✓；
- 物化结束写 `features/_market_meta.json`（口径戳：语义版本 + 规格清单 + 逐字段文件数）✓，
  启动时 `/api/version` 会带出来 ⇒ "换了机器 / pull 了新代码却忘了重物化"能被发现 ✓。
"""
from __future__ import annotations

import json
import os
import re
import time
import unicodedata
import warnings
from typing import Dict, Iterable, List, Optional, Sequence, Tuple

import numpy as np
import pandas as pd

from ..config import QLIB_PROVIDER_URI
from .panel_expr import PanelEvaluator, _calendar, _collect_field_names
from .parser.codegen import (
    BLOCK_KEYS, INSUM_CALC_TYPES, block_key, blocksetnum_field, formula_key, insum_field,
)

# 口径语义版本：改板块口径 / 聚合方式 / 字段命名 ⇒ **必须递增**并重跑物化
MARKET_SEMANTICS = "1.0.0"
MARKET_META_NAME = "_market_meta.json"

# 物化窗口默认值（与筹码一致：2010 起，够长且不至于把内存拉爆）
DEFAULT_START = "2010-01-01"


def features_dir() -> str:
    return str(QLIB_PROVIDER_URI).rstrip("/\\") + "/features"


def market_meta_path() -> str:
    return features_dir() + "/" + MARKET_META_NAME


def all_codes() -> List[str]:
    """参与物化的股票代码 = features 目录下的 `sh/sz/bj + 6 位数字` 目录（小写，与 qlib 一致）。"""
    d = features_dir()
    try:
        names = os.listdir(d)
    except OSError:
        return []
    return sorted(n for n in names
                  if re.match(r"^(sh|sz|bj)\d{6}$", n) and os.path.isdir(os.path.join(d, n)))


# ---------------------------------------------------------------------------
# 板块成分
# ---------------------------------------------------------------------------
def read_instrument_spans(key: str, qlib_dir: Optional[str] = None) -> Dict[str, List[Tuple[str, str]]]:
    """读 `instruments/<key>.txt`：`CODE 起始日 [结束日]`（制表符/空格分隔，可多段）。

    返回 `{qlib小写代码: [(起, 止), ...]}`（`止` 为空串表示至今）。
    """
    root = (qlib_dir or str(QLIB_PROVIDER_URI)).rstrip("/\\")
    path = os.path.join(root, "instruments", key + ".txt")
    out: Dict[str, List[Tuple[str, str]]] = {}
    if not os.path.exists(path):
        raise FileNotFoundError("找不到股票池文件：%s（可用池见 data/cn_data/instruments/）" % path)
    with open(path, "r", encoding="utf-8", errors="replace") as f:
        for line in f:
            parts = line.strip().split()
            if len(parts) < 2:
                continue
            code = parts[0].strip().lower()
            start = parts[1].strip()
            end = parts[2].strip() if len(parts) > 2 else ""
            out.setdefault(code, []).append((start, end))
    return out


def block_mask(key: str, codes: Sequence[str], cal: pd.DatetimeIndex) -> np.ndarray:
    """板块逐日成分掩码：(日数 × 股票数) 的 bool 矩阵。

    `key='hsa'`（沪深A股）＝ `all` 里剔除北交所（`bj*`）—— 现算，不需要额外的池文件 ✓。
    """
    n_d, n_c = len(cal), len(codes)
    mask = np.zeros((n_d, n_c), dtype=bool)
    if key == "hsa":
        base = block_mask("all", codes, cal)
        keep = np.array([not str(c).startswith("bj") for c in codes], dtype=bool)
        return base & keep[None, :]
    spans = read_instrument_spans(key)
    cal_vals = cal.values.astype("datetime64[D]")
    for j, code in enumerate(codes):
        for (s, e) in spans.get(str(code).lower(), []):
            try:
                lo = int(np.searchsorted(cal_vals, np.datetime64(s, "D"), side="left"))
                hi = (n_d - 1) if not e else int(np.searchsorted(
                    cal_vals, np.datetime64(e, "D"), side="right")) - 1
            except Exception:
                continue
            if hi >= lo:
                mask[lo:hi + 1, j] = True
    return mask


# ---------------------------------------------------------------------------
# 规格（spec）：物化什么
# ---------------------------------------------------------------------------
class Spec:
    """一条物化规格：`block + (可选) 被调公式 + 计算类型`。"""

    def __init__(self, block: str, formula: Optional[str] = None,
                 out_index: int = 1, calc_type: int = 0):
        self.block_name = block
        self.block_key = block_key(block)
        self.formula = formula
        self.out_index = int(out_index)
        self.calc_type = int(calc_type) if formula else -1

    @property
    def field(self) -> str:
        if self.formula is None:
            return blocksetnum_field(self.block_name).lstrip("$")
        return insum_field(self.block_name, self.formula, self.out_index,
                           self.calc_type).lstrip("$")

    @property
    def kind(self) -> str:
        return "BLOCKSETNUM" if self.formula is None else "INSUM"

    def to_dict(self) -> Dict:
        return {"kind": self.kind, "block": self.block_name, "block_key": self.block_key,
                "formula": self.formula, "out_index": self.out_index,
                "calc_type": self.calc_type, "field": self.field}

    def __repr__(self) -> str:
        if self.formula is None:
            return "BLOCKSETNUM(%r)" % self.block_name
        return "INSUM(%r,%r,%d,%d)" % (self.block_name, self.formula, self.out_index, self.calc_type)


_STR = r"""['"]([^'"]+)['"]"""
_RE_BLOCK = re.compile(r"BLOCKSETNUM\s*\(\s*%s\s*\)" % _STR, re.I)
_RE_INSUM = re.compile(
    r"INSUM\s*\(\s*%s\s*,\s*%s\s*,\s*([0-9]+)\s*,\s*([0-9]+)\s*\)" % (_STR, _STR), re.I)


def specs_from_text(text: str) -> List[Spec]:
    """从公式原文里抠出需要物化的规格（文本级扫描：物化器不能依赖"先编译成功" ✓）。"""
    src = unicodedata.normalize("NFKC", text or "")
    out: List[Spec] = []
    for m in _RE_INSUM.finditer(src):
        try:
            out.append(Spec(m.group(1), m.group(2), int(m.group(3)), int(m.group(4))))
        except Exception:
            continue
    known = {s.field for s in out}
    for m in _RE_BLOCK.finditer(src):
        try:
            s = Spec(m.group(1))
        except Exception:
            continue
        if s.field not in known:
            out.append(s)
    return out


def discover_specs(extra_texts: Optional[Iterable[str]] = None) -> List[Spec]:
    """扫描**已保存的公式库**（+ 额外的公式原文）→ 去重后的规格清单。

    ⇒ 用户改完公式只要重跑一次物化脚本，不必手工登记"要算什么" ✓。
    """
    texts: List[str] = []
    try:
        from ..services.custom_formulas import list_custom_formulas
        texts += [(it.get("text") or "") for it in list_custom_formulas()]
    except Exception:
        pass
    texts += [t for t in (extra_texts or []) if t]
    seen: Dict[str, Spec] = {}
    for t in texts:
        for s in specs_from_text(t):
            seen.setdefault(s.field, s)
    return list(seen.values())


# ---------------------------------------------------------------------------
# 被调公式 → qlib 表达式
# ---------------------------------------------------------------------------
def formula_expression(name: str) -> str:
    """按**公式名（输出名）**从公式库取 qlib 表达式（库里存着编译好的 `expression` ✓）。"""
    from ..services.custom_formulas import list_custom_formulas
    items = list_custom_formulas()
    want = (name or "").strip().upper()
    for it in items:
        if str(it.get("name") or "").strip().upper() == want:
            expr = it.get("expression") or ""
            if not expr.strip():
                raise ValueError("公式 %r 的 expression 为空 ⇒ 请先在编辑器里重新保存一次" % name)
            return expr
    avail = "、".join(sorted({str(it.get("name") or "") for it in items if it.get("name")}))
    raise ValueError("公式库里没有名为 %r 的公式（INSUM 的第 2 个参数必须是**已保存公式**）\n"
                     "⇒ 现有公式：%s" % (name, avail or "（空）"))


# ---------------------------------------------------------------------------
# ★ v1.20.87：被调公式的**内容指纹**（"文件在、内容旧"也能被发现 ✓）
# ---------------------------------------------------------------------------
def formula_fingerprints(specs: Optional[Sequence[Spec]] = None) -> Dict[str, str]:
    """`{被调公式名: md5(编译后 expression)[:12]}` ✓。

    为什么需要（用户 2026-10-09 追问「我改了公式，涉及的物化文件也要变，**能不能提示呢**」✓）：
      物化字段名里那 6 位十六进制是**公式名**的哈希（`..._is_gold_pit_7758f0_...` ✓ 与正文无关 ✓）
      ⇒ 改了正文后**文件名不变、内容却是旧口径** ✗✗ —— 这是最难发现的一类错：
      现有防线只有"**整列全 NaN** 才告警"（`panel_expr._warn_if_materialized_missing` ✓），
      而"旧值有数、看着还挺合理"它**完全看不见** ✗（2026-10-09 实测：改了 `IS_GOLD_PIT`
      正文，`mkt_insum_...` 仍是 02:06 的旧算式 ✓）。⇒ 物化时把指纹写进 `_market_meta.json` ✓，
      启动 / `/api/version` 时比对 ⇒ 立刻能指出"该重跑物化了" ✓。

    ⚠ 指纹取**编译后的 expression**（不是原文 ✓）：改注释/空行/缩进**不会**误报 ✓，
      只有**语义真的变了**（生成结果不同）才报 ✓。
    """
    import hashlib                                   # noqa: PLC0415  局部导入：本函数极少调用 ✓
    out: Dict[str, str] = {}
    try:
        todo = list(specs if specs is not None else discover_specs())
    except Exception:                                # noqa: BLE001
        return out
    for name in sorted({sp.formula for sp in todo if sp.formula}):
        try:
            expr = formula_expression(name)
        except Exception:                            # noqa: BLE001  公式没了 ⇒ 由别的检查报 ✓
            continue
        out[str(name)] = hashlib.md5(expr.encode("utf-8")).hexdigest()[:12]
    return out


def stale_formula_names() -> List[str]:
    """`_market_meta.json` 里存的指纹 vs **当前**公式库 ⇒ **正文变过**的被调公式名 ✓（空 = 一致 ✓）。

    读不到 meta / meta 里没有指纹（老物化，本次之前跑的 ✓）⇒ 返回空 ✓（不误报 ✓，此时
    `/api/version` 仍会给出"口径戳缺失/需重跑"的既有提示 ✓）。
    """
    import json                                      # noqa: PLC0415
    try:
        with open(market_meta_path(), "r", encoding="utf-8") as f:
            stored = (json.load(f) or {}).get("formula_fingerprints") or {}
    except Exception:                                # noqa: BLE001
        return []
    if not stored:
        return []
    now = formula_fingerprints()
    return sorted(n for n, h in stored.items() if now.get(n) not in (None, h))


def stale_message(names: Sequence[str]) -> str:
    """过期提示的中文长文案（`/api/version` 与**保存时**共用 ✓，只此一份 ✓）。"""
    return ("被横向统计物化引用的公式已改：%s ⇒ 现有 `mkt_*` 文件是**旧口径**，需要重新物化："
            "`python backend/tools/materialize_market.py --overwrite` ✓" % "、".join(names))


def materialize_impact(name: str) -> List[str]:
    """★ v1.20.89：这条公式**被物化引用、且编译结果与物化时不一致** ⇒ 返回那些 `mkt_*` 字段
    （空 = **无需提示** ✓ —— 包括"压根没被引用"与"改回原样、指纹已吻合"两种情形 ✓）。

    为什么要有它（用户 2026-10-09：「我把 `IS_GOLD_PIT:轨迹` 改成了 `<10`，物化会有影响吗？
    **我没有看到提示**」✓）：② 的检查原来只在**后端启动时**跑一次 ✗、而且前端**根本没读**
    `chip_meta`/`market_meta` ✗✗（`grep` frontend 0 处引用 ✓）⇒ 保存公式后**没有任何提示出口** ✗。
    ⇒ 保存路径直接问一句"你改的这条有没有被物化引用" ✓，有就当场告诉用户 ✓。
    """
    key = (name or "").strip().upper()
    if not key:
        return []
    try:
        specs = list(discover_specs())          # 扫公式库现推（meta 缺失也能答 ✓）
    except Exception:                            # noqa: BLE001
        return []
    # ★ v1.20.89：**必须做指纹比对**（用户 2026-10-09：「我改回原样就不要报这个提示了
    #   （刚才我改 0 也提示了）」✓）—— 只判"被引用"是**误报** ✗：改回原样后正文与物化时
    #   一致 ⇒ 文件其实是对的 ⇒ 不该打扰 ✓。只有"**跟物化时不一样**"才提示 ✓：
    #   ① 正文真的改了（口径变了 ✓）② 库里 expression 被新版编译器重编过（如 codegen 1.20.83
    #   的 COUNT 修正 ✓）—— 两种都该重物化 ✓。
    #   ⚠ meta 里没有该公式的指纹（老物化 ✓）⇒ **不提示** ✗（无从判断 ⇒ 交给
    #     `/api/version` 的口径戳兜底 ✓）。
    # ⚠ 必须先按**公式名**筛出规格 ✗（否则会把 `mkt_num_all` 这种"成分股数"也算进影响面 ✗
    #   —— 它 `formula=None`、跟任何公式都无关 ✓；2026-10-09 实测第一版就漏了这一步 ✗）
    specs = [sp for sp in specs if (sp.formula or "").strip().upper() == key]
    if not specs:
        return []
    stored = {str(k).strip().upper(): v for k, v in _stored_fingerprints().items()}
    old = stored.get(key)
    if not old:
        return []
    import hashlib                                   # noqa: PLC0415
    try:
        now = hashlib.md5(formula_expression(specs[0].formula).encode("utf-8")).hexdigest()[:12]
    except Exception:                                # noqa: BLE001
        return []
    if now == old:                                   # ★ 改回原样 ⇒ 指纹吻合 ⇒ **不提示** ✓
        return []
    return sorted({sp.field for sp in specs})


def materialized_fields() -> set:
    """`_market_meta.json` 里**已物化**的字段名集合（读不到 / 没物化过 ⇒ 空集 ✓）。"""
    import json                                      # noqa: PLC0415
    try:
        with open(market_meta_path(), "r", encoding="utf-8") as f:
            meta = json.load(f) or {}
    except Exception:                                # noqa: BLE001
        return set()
    return {str(d.get("field") or "") for d in (meta.get("specs") or []) if d.get("field")}


def missing_materialized_fields(text: str) -> List[str]:
    """这段公式原文里用到、但 meta 里**还没物化**的 `mkt_*` 字段（空 = 没有缺口 ✓）。

    为什么要有它（用户 2026-10-09：「我把 `总股票数:=BLOCKSETNUM('全部A股')` 改成**中证1000**，
    **不提示重新物化吗**？」✓）：① 的指纹判据只覆盖"**被** `INSUM(...)` 引用的公式" ✗，
    而这次改的是**用法本身**（公式自己**含有** BLOCKSETNUM/INSUM ✓）⇒ 判据覆盖不到 ✗✗。
    改动后果：板块换了 ⇒ `block_key` 不同 ⇒ **字段名也换了**（`mkt_num_all` → `mkt_num_csi1000` ✗）
    ⇒ 新字段**没有 bin** ✗ ⇒ 单因子测试/事件研究靠 ③ 的现算兜住（BLOCKSETNUM 很便宜 ✓，
    INSUM 慢但结果一致 ✓），而 **qlib 回测链没有现算退路** ✗ ⇒ 很可能直接报"取不到该 feature" ✗
    ⇒ 必须提示 ✓。
    ⚠ meta 里一条规格都没有（从没物化 / 很老的物化 ✓）⇒ **不提示** ✗（无从判断，别误报 ✓）。
    """
    want = {s.field for s in specs_from_text(text or "")}
    known = materialized_fields()
    if not known:
        return []
    return sorted(want - known)


def _stored_fingerprints() -> Dict[str, str]:
    """读 `_market_meta.json` 里存的被调公式指纹（读不到 ⇒ 空 dict ✓，调用方据此**不提示** ✓）。"""
    import json                                      # noqa: PLC0415
    try:
        with open(market_meta_path(), "r", encoding="utf-8") as f:
            return (json.load(f) or {}).get("formula_fingerprints") or {}
    except Exception:                                # noqa: BLE001
        return {}


def market_meta_state_live() -> Dict:
    """`market_meta_state()` + **实时**指纹比对（每次调用都算 ✓，供 `/api/version` 用 ✓）。

    ⚠ 启动时抓的那份是**快照** ✗ ⇒ 保存公式后不重启就永远是旧结论 ✗（用户实测"看不到提示"✓）。
    """
    st = market_meta_state()
    try:
        stale = stale_formula_names()
        if stale:
            st.update({"ok": False, "state": "stale_formula",
                       "stale_formulas": stale, "message": stale_message(stale)})
    except Exception:                            # noqa: BLE001
        pass
    return st


# ---------------------------------------------------------------------------
# ★ v1.20.87：`mkt_*` 物化文件缺失 ⇒ **现算回退**（用户 2026-10-09 要求 ✓）
# ---------------------------------------------------------------------------
_INLINE_CACHE: Dict[str, Optional[pd.Series]] = {}


def compute_field_inline(field: str) -> Optional[pd.Series]:
    """按规格**现算**一个 `mkt_*` 字段 ⇒ 逐日市场级 Series（失败返回 None ✓）。

    用户追问：「如果 `IS_GOLD_PIT` 我把物化文件删了，它会**自动回退**不走物化路线计算吗？」
    ⇒ **原来不会** ✗：`INSUM(...)` 的编译产物就是**字段引用** `$mkt_insum_...` ✓ ⇒ 缺文件=整列 NaN，
      只会在面板打一条"整列全 NaN ⇒ 请重跑 materialize_market.py"的告警 ✓（回测链则直接报
      取不到该 feature ✗）。⇒ 现在补上退路 ✓。

    ⚠ 口径必须与物化**逐位一致** ⇒ 走**同一条路**：
      · 股票池 = `all_codes()`（不是当前面板的池 ✗ —— 面板可能只有 300 只，
        照它算出来的"全A股坑数量"必然偏小 ✗✗，这正是最危险的静默口径漂移 ✓）；
      · 窗口 = meta 里记录的那段（读不到就用 `DEFAULT_START` ✓）；
      · 被调公式 = `formula_expression(name)`（库里编译好的 ✓）；
      · 聚合 = **同一个** `_aggregate` ✓、掩码 = **同一个** `block_mask` ✓。
    代价：要建一个全市场 PanelEvaluator（慢 ✓ 请当"坏了才走的退路" ✓）；结果按字段缓存 ✓
    （一次会话只算一遍 ✓）。
    """
    if field in _INLINE_CACHE:
        return _INLINE_CACHE[field]
    _INLINE_CACHE[field] = None                      # 占位：算失败也不反复重算 ✓
    import json                                      # noqa: PLC0415
    spec: Optional[Spec] = None
    start = DEFAULT_START
    try:
        with open(market_meta_path(), "r", encoding="utf-8") as f:
            meta = json.load(f) or {}
        for d in (meta.get("specs") or []):
            if str(d.get("field") or "") == field:
                spec = Spec(str(d.get("block") or ""), d.get("formula"),
                            int(d.get("out_index") or 1), int(d.get("calc_type") or 0))
                break
        start = str(meta.get("start_time") or start)
    except Exception:                                # noqa: BLE001
        spec = None
    if spec is None:                                 # meta 缺失 ⇒ 退一步：扫公式库现推规格 ✓
        for sp in discover_specs():
            if sp.field == field:
                spec = sp
                break
    if spec is None:
        return None
    try:
        from ..services.qlib_runtime import ensure_qlib_init
        ensure_qlib_init()
        codes = all_codes()
        cal = _calendar()
        cal = cal[cal >= pd.Timestamp(start)]
        if spec.formula is None:                     # BLOCKSETNUM：每日成分股数 ✓（便宜 ✓）
            values = block_mask(spec.block_key, codes, cal).sum(axis=1).astype(float)
        else:
            expr = formula_expression(spec.formula)
            fields = _collect_field_names([(expr, "x")])
            ev = PanelEvaluator(codes, cal[0].strftime("%Y-%m-%d"), cal[-1].strftime("%Y-%m-%d"),
                                union_fields=fields, read_start=cal[0].strftime("%Y-%m-%d"))
            s = ev.eval_expr(expr).unstack(level=0).reindex(index=cal, columns=codes)
            values = _aggregate(s.to_numpy(dtype=float), block_mask(spec.block_key, codes, cal),
                                spec.calc_type)
        out = pd.Series(np.asarray(values, dtype=float), index=cal)
        _INLINE_CACHE[field] = out
        return out
    except Exception:                                # noqa: BLE001  退路失败 ⇒ 让告警照旧出声 ✓
        return None


# ---------------------------------------------------------------------------
# 写盘
# ---------------------------------------------------------------------------
def _write_bin(path: str, first_idx: int, values: np.ndarray) -> None:
    """首 4 字节 float32 = 起始日历下标，其后 float32 值（与 `panel_expr._read_field_bin` 约定一致）。"""
    head = np.asarray([float(first_idx)], dtype="<f4")
    body = np.asarray(values, dtype="<f4")
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "wb") as f:
        head.tofile(f)
        body.tofile(f)


def _broadcast_write(field: str, full_cal: pd.DatetimeIndex, win_cal: pd.DatetimeIndex,
                     values: np.ndarray, codes: Sequence[str], overwrite: bool) -> int:
    """把"每日一个值"的市场级序列写进 **每只股票** 的同名字段（日期轴对齐该股 `close` ✓）。

    ⚠⚠ 两个日历**不能混**（2026-10-09 第一版就踩了、而且是**静默错** ✗）：
      · `close.day.bin` 的 header（起始下标）是相对 **完整日历**（`_calendar()`，2000 起）的 ✓；
      · 而我们的物化窗口可能只是完整日历的一段（默认 2010 起 ⇒ 4040 天 vs 完整 6455 天）✓。
      ⇒ 必须先把窗口序列铺到**完整日历**上（窗口外为 NaN），再按 `first:first+n_ref` 切片 ✓。
      第一版直接拿**完整日历的下标**去切**窗口日历** ⇒ ① 2016 年后上市的股票整段空 ⇒ 直接跳过 ✗
      （3011 / 6141 只，看着"跑完了"其实漏了一半 ✗）；② 写得出来的那些**日期轴整体错位** ✗。

    日期轴必须与 `close.day.bin` 逐位一致 —— 否则多股一起加载时 qlib 会报
    `Can only compare identically-labeled Series objects`（筹码物化踩过，见 chip_store.py:229）。
    """
    fdir = features_dir()
    series = pd.Series(np.asarray(values, dtype=float), index=win_cal).reindex(full_cal)
    n_ok = cnt_skip = 0
    for code in codes:
        close_path = "%s/%s/close.day.bin" % (fdir, code)
        if not os.path.exists(close_path):
            continue
        with open(close_path, "rb") as fh:
            first = int(np.frombuffer(fh.read(4), dtype="<f4")[0])
        n_ref = max(0, (os.path.getsize(close_path) - 4) // 4)
        sub = series.iloc[first:first + n_ref]
        if not len(sub):
            cnt_skip += 1
            continue
        path = "%s/%s/%s.day.bin" % (fdir, code, field)
        if os.path.exists(path) and not overwrite:
            continue
        _write_bin(path, first, sub.to_numpy(dtype=float))
        n_ok += 1
    if cnt_skip:
        # 走到了这里说明"股票有 close 却取不到窗口"⇒ 日历口径又错位了 ✗（不许静默）
        raise RuntimeError("有 %d 只股票写不出（close 与日历对不上）⇒ 物化中止，请检查日历口径"
                           % cnt_skip)
    return n_ok


def _aggregate(mat: np.ndarray, mask: np.ndarray, calc_type: int) -> np.ndarray:
    """(日 × 股) 矩阵 + 逐日成分掩码 → 逐日一个值。

    口径：成分股里 **NaN（停牌/无数据）在"累加/平均"里按 0 计**（分母仍是成分股数 ✓），
    "最大/最小"则**忽略 NaN**（当天全 NaN ⇒ NaN ✓）。
    """
    m = np.asarray(mask, dtype=bool)
    if m.shape != mat.shape:
        raise ValueError("板块掩码与面板形状不一致：%s vs %s" % (m.shape, mat.shape))
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", RuntimeWarning)
        if calc_type == 0:                     # 累加
            return np.nansum(np.where(m, np.nan_to_num(mat, nan=0.0), 0.0), axis=1)
        if calc_type == 1:                     # 平均（分母 = 当日成分股数）
            s = np.nansum(np.where(m, np.nan_to_num(mat, nan=0.0), 0.0), axis=1)
            cnt = m.sum(axis=1)
            return np.where(cnt > 0, s / np.maximum(cnt, 1), np.nan)
        vals = np.where(m, mat, np.nan)
        if calc_type == 2:                     # 最大
            return np.nanmax(np.where(np.isnan(vals).all(axis=1)[:, None], np.nan, vals), axis=1)
        if calc_type == 3:                     # 最小
            return np.nanmin(np.where(np.isnan(vals).all(axis=1)[:, None], np.nan, vals), axis=1)
    raise ValueError("不支持的 INSUM 计算类型：%s（支持 %s）"
                     % (calc_type, list(INSUM_CALC_TYPES)))


# ---------------------------------------------------------------------------
# 主入口
# ---------------------------------------------------------------------------
def materialize(specs: Optional[Sequence[Spec]] = None,
                start_time: str = DEFAULT_START,
                end_time: Optional[str] = None,
                overwrite: bool = False,
                progress_cb=None) -> Dict[str, int]:
    """按规格物化（返回 `{字段: 写入股票数}`）。`specs=None` ⇒ 自动扫描公式库 ✓。"""
    def _log(msg: str) -> None:
        if progress_cb:
            progress_cb(msg)

    specs = list(specs if specs is not None else discover_specs())
    if not specs:
        _log("没有发现任何 BLOCKSETNUM/INSUM 用法 ⇒ 无需物化")
        return {}

    # ⚠ 必须先走**统一入口**初始化 qlib：`_calendar()` 依赖 `D.calendar()`，
    #   裸调 `qlib.init` 会清空 custom_ops（见 services/qlib_runtime.ensure_qlib_init 的说明）✓
    from ..services.qlib_runtime import ensure_qlib_init
    ensure_qlib_init()

    full_cal = _calendar()                      # 完整日历（close.day.bin 的 header 以它为准 ✓）
    cal = full_cal
    if end_time:
        cal = cal[cal <= pd.Timestamp(end_time)]
    cal = cal[cal >= pd.Timestamp(start_time)]
    if len(cal) == 0:
        raise ValueError("物化窗口为空（start=%s end=%s）" % (start_time, end_time))
    codes = all_codes()
    _log("物化窗口 %s ~ %s（%d 个交易日 / 完整日历 %d 天），股票 %d 只，规格 %d 条"
         % (cal[0].strftime("%Y-%m-%d"), cal[-1].strftime("%Y-%m-%d"), len(cal), len(full_cal),
            len(codes), len(specs)))

    masks: Dict[str, np.ndarray] = {}
    out: Dict[str, int] = {}

    def _mask(key: str) -> np.ndarray:
        if key not in masks:
            masks[key] = block_mask(key, codes, cal)
        return masks[key]

    # 同一个被调公式只求值一次（多个 calc/板块共用）
    panels: Dict[str, np.ndarray] = {}
    for i, sp in enumerate(specs, 1):
        try:
            mask = _mask(sp.block_key)
            if sp.formula is None:
                values = mask.sum(axis=1).astype(float)
                _log("[%d/%d] %r → %s（每日成分股数，均值 %.0f）"
                     % (i, len(specs), sp, sp.field, float(np.nanmean(values))))
            else:
                if sp.formula not in panels:
                    expr = formula_expression(sp.formula)
                    fields = _collect_field_names([(expr, "x")])
                    _log("[%d/%d] 求值被调公式 %s：%s" % (i, len(specs), sp.formula, expr))
                    ev = PanelEvaluator(codes, cal[0].strftime("%Y-%m-%d"),
                                        cal[-1].strftime("%Y-%m-%d"),
                                        union_fields=fields,
                                        read_start=cal[0].strftime("%Y-%m-%d"))
                    s = ev.eval_expr(expr)
                    df = s.unstack(level=0).reindex(index=cal, columns=codes)
                    panels[sp.formula] = df.to_numpy(dtype=float)
                values = _aggregate(panels[sp.formula], mask, sp.calc_type)
                _log("[%d/%d] %s（%s，%s）"
                     % (i, len(specs), sp, INSUM_CALC_TYPES.get(sp.calc_type, "?"),
                        "值域 %.3g ~ %.3g" % (float(np.nanmin(values)), float(np.nanmax(values)))))
            n = _broadcast_write(sp.field, full_cal, cal, values, codes, overwrite)
            out[sp.field] = n
            _log("     写盘 %d 只 → %s" % (n, sp.field))
        except Exception as e:                                     # noqa: BLE001
            out[sp.field] = 0
            _log("     [警告] %r 物化失败：%s: %s" % (sp, type(e).__name__, e))

    write_market_meta({
        "semantics": MARKET_SEMANTICS,
        "start_time": cal[0].strftime("%Y-%m-%d"),
        "end_time": cal[-1].strftime("%Y-%m-%d"),
        "overwrite": bool(overwrite),
        "n_codes": len(codes),
        "specs": [sp.to_dict() for sp in specs],
        "counts": dict(out),
        # ★ v1.20.87：被调公式的**内容指纹** ✓ ⇒ 以后改了 `IS_GOLD_PIT` 之类被物化引用的公式，
        #   启动 / `/api/version` 一比就能喊"该重跑物化了" ✓（见 `stale_formula_names` ✓）
        "formula_fingerprints": formula_fingerprints(specs),
    })
    return out


# ---------------------------------------------------------------------------
# 口径戳（照 chip_store 的套路）
# ---------------------------------------------------------------------------
def write_market_meta(payload: Dict) -> None:
    try:
        p = market_meta_path()
        data = dict(payload or {})
        data["market_semantics"] = MARKET_SEMANTICS
        data["written_at"] = time.strftime("%Y-%m-%d %H:%M:%S")
        os.makedirs(os.path.dirname(p), exist_ok=True)
        with open(p, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
    except Exception:
        pass


def market_meta_state() -> Dict:
    """物化口径戳状态（启动日志 / `/api/version` 用；**不抛异常**）。"""
    info: Dict = {"ok": True, "state": "ok", "expected": MARKET_SEMANTICS, "path": market_meta_path()}
    try:
        p = market_meta_path()
        if not os.path.exists(p):
            info.update({"ok": False, "state": "missing",
                         "message": "尚未物化横向统计字段（要用 BLOCKSETNUM/INSUM 就先跑 "
                                    "tools/materialize_market.py）"})
            return info
        with open(p, "r", encoding="utf-8") as f:
            meta = json.load(f)
        info["meta"] = meta
        got = meta.get("market_semantics")
        if got != MARKET_SEMANTICS:
            info.update({"ok": False, "state": "stale",
                         "message": "横向统计物化口径不一致：bin 是 %s、代码要 %s ⇒ 必须重跑 "
                                    "tools/materialize_market.py --overwrite" % (got, MARKET_SEMANTICS)})
        else:
            info["message"] = "横向统计字段物化于 %s（%d 条规格 ✓）" % (
                meta.get("written_at"), len(meta.get("specs", [])))
    except Exception as e:                                          # noqa: BLE001
        info.update({"ok": False, "state": "error", "message": "横向统计口径戳检查失败：%r" % (e,)})
    return info
