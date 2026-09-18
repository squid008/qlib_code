# -*- coding: utf-8 -*-
"""特征计算磁盘缓存（精细版：只缓存特征，label 每次现算）。

背景：
- 每次回测（含复用模型权重）都要重新计算特征矩阵（6000 股 × 全区间 × 27 特征），
  这是复用回测依然慢的主要瓶颈。
- 特征计算的结果只取决于：股票池、特征表达式、时间范围、数据版本。
  只要这四样不变，结果就完全一样，可以安全缓存复用。

设计：
- 自定义 CachedQlibDataLoader 继承 qlib 的 QlibDataLoader，
  覆写 load：把 config 里的 "feature" 组结果缓存到磁盘（workdir/feature_cache/），
  "label" 组每次现算（label 只是 Ref(close) 简单表达式，很便宜）。
- 这样改 label_horizon（预测周期）不会让特征缓存失效，命中面更大。
- 缓存 key = md5(版本 + 股票池 + 特征表达式 + 时间范围 + 数据版本号)。
  数据更新后（数据目录 mtime 变化）key 自动变化，不会用到脏缓存。
"""
from __future__ import annotations

import hashlib
import os
import pickle
import re
import time

import pandas as pd

from qlib.data.dataset.loader import QlibDataLoader

try:
    from ..config import WORK_DIR, QLIB_PROVIDER_URI

    _CACHE_DIR = os.path.join(WORK_DIR, "feature_cache")
except Exception:  # pragma: no cover
    _CACHE_DIR = os.path.join(os.path.abspath("."), "feature_cache")


# ---------------------------------------------------------------------------
# 缓存 key
# ---------------------------------------------------------------------------


def _norm_instruments(instruments):
    """把 instruments（str 或 list）规范化为稳定字符串。"""
    if instruments is None:
        return "none"
    if isinstance(instruments, str):
        return instruments
    return ",".join(sorted(str(i) for i in instruments))


def _data_version() -> str:
    """数据版本号：数据目录最后修改时间（数据更新后缓存自动失效）。"""
    try:
        root = QLIB_PROVIDER_URI or ""
        mtime = 0.0
        if root and os.path.isdir(root):
            mtime = os.path.getmtime(root)
            cal = os.path.join(root, "calendars", "day.txt")
            if os.path.exists(cal):
                mtime = max(mtime, os.path.getmtime(cal))
        if mtime:
            return str(mtime)
    except Exception:
        pass
    return "unknown"


# 停牌删行（益盟语义）：哪些叶子字段需要按 $close 掩码删行（自身停牌日可能仍有值）
_SR_FIELDS_BY_CLOSE_MASK = {
    "$open", "$high", "$low", "$volume", "$amount", "$vwap", "$change", "$factor", "$market_cap",
}
_SR_FIELD_RE = re.compile(r"\$([a-z][a-z0-9_]*)")


def _sr_wrap_expr(expr: str) -> str:
    """把表达式里的行情叶子字段包上 SR（停牌删行 → 益盟/聚宽语义）。

    - $close / $mf_*：按字段自身 NaN 删行（close 自身即停牌掩码；资金流字段只覆盖
      2016+，按自身删除不会把其无数据期误当停牌）；
    - 其余行情字段（open/high/low/volume/amount/vwap/change/factor/market_cap）：
      factor/market_cap 停牌日仍可能有值，必须显式按 $close 掩码删行，
      否则与已删行的价格字段组合时会把停牌行"外对齐"回来。
    """
    def _repl(m):
        f = "$" + m.group(1)
        if f == "$close":
            return "SR($close)"
        if f in _SR_FIELDS_BY_CLOSE_MASK:
            return f"SR({f},$close)"
        if f.startswith("$mf_"):
            return f"SR({f})"
        return f  # 未识别的字段保持原样（如 $vwap 等已在掩码集合）
    return _SR_FIELD_RE.sub(_repl, expr)


_CODE_SIG = None


def _code_version() -> str:
    """**影响面板结果的代码**指纹（源码 mtime + size 汇总；模块级只算一次 ✓）。

    ⚠ 2026-09-18 血泪（v1.19.97）：key 里原本只有"数据版本"✓ 没有"代码版本"✗ ⇒
    改了 `adjust_expr`（前复权替换价格字段）与列对齐逻辑后，**旧缓存仍被复用** ✗ ⇒
    拿到"列错位"的坏面板（`F0` 列里装的是 `T1_IS_ST` ✗）⇒ 收益复利滚到 **2e31** ✗
    （手工清 4 GB 缓存才恢复 ✓）。⇒ 把这几处源码指纹拼进 key：**任何改动 ⇒ key 变 ⇒
    自动失效** ✓（比手工维护版本号可靠 ✓ 不会忘 ✓；单次 stat，开销可忽略 ✓）。
    """
    global _CODE_SIG
    if _CODE_SIG is None:
        _here = os.path.dirname(os.path.abspath(__file__))        # app/engine
        _fac = os.path.join(os.path.dirname(_here), "factors")    # app/factors
        _cands = [os.path.join(_here, "feature_cache.py"),
                  os.path.join(_here, "adjust.py"),
                  os.path.join(_here, "limits.py"),
                  os.path.join(_fac, "ops_ext.py"),
                  os.path.join(_fac, "panel_expr.py"),
                  os.path.join(_fac, "single_test.py")]
        _sig = []
        for _p in _cands:
            try:
                _st = os.stat(_p)
                _sig.append("%s:%d-%d" % (os.path.basename(_p),
                                          int(_st.st_mtime), int(_st.st_size)))
            except Exception:                                     # noqa: BLE001
                pass
        _CODE_SIG = ";".join(_sig) or "na"
    return _CODE_SIG


def _cache_path(instruments, exprs, names, start_time, end_time, extra="") -> str:
    """缓存 key 必须同时含表达式与列名（names）。

    同一批表达式在不同 Handler 下可能映射不同列名（如"混合"模式下 Alpha158/360
    的特征会加 A158_/A360_ 前缀），若 key 只含表达式会导致跨场景命中脏缓存。

    `extra`（v1.18.69）：额外的**影响结果的参数**指纹。单因子测试的面板求值器除了
    表达式/区间，还受 `warmup_days` / `freeze_suspended_price` / 信号截断日等影响
    —— 这些不放进 key 就会**跨参数命中脏缓存**，故调用方必须把它们拼进来。

    `_code_version()`（v1.19.97）：**代码指纹** ⇒ 改了 evaluate/adjust/ops/列对齐
    之类的代码，旧缓存自动失效 ✓（覆盖**所有**调用方：单因子面板 + 回测/训练 Handler ✓）。
    """
    parts = [
        "v1",
        _norm_instruments(instruments),
        "\x01".join(str(e) for e in exprs),
        "\x01".join(str(n) for n in names) if names else "",
        str(start_time),
        str(end_time),
        _data_version(),
        _code_version(),
        str(extra or ""),
    ]
    raw = "|".join(parts)
    return os.path.join(_CACHE_DIR, hashlib.md5(raw.encode("utf-8")).hexdigest()[:24] + ".pkl")


# ---------------------------------------------------------------------------
# 缓存读写（tmp + 原子替换，多任务并发安全）
# ---------------------------------------------------------------------------


def _load_cache(path):
    if not os.path.exists(path):
        return None
    try:
        with open(path, "rb") as f:
            return pickle.load(f)
    except Exception:
        return None


def _save_cache(path, df):
    try:
        os.makedirs(_CACHE_DIR, exist_ok=True)
        tmp = path + ".tmp"
        with open(tmp, "wb") as f:
            pickle.dump(df, f, protocol=4)
        os.replace(tmp, path)
    except Exception:
        pass
    _prune_cache()                    # v1.18.69：写入后就地治理（上限 / 过期 / LRU）


# ---------------------------------------------------------------------------
# 缓存容量治理（v1.18.69）：大小上限 + 过期清理 + LRU
# ---------------------------------------------------------------------------
# 单因子测试接入缓存后，单份面板动辄数百 MB（全A 5 年 ≈ 590 万行），反复调参会迅速
# 撑爆磁盘（此前只有回测路径写缓存、量小，故缺治理）。策略：
#   ① 过期：mtime 早于 `max_age_days` 的文件直接删；
#   ② LRU：仍超 `max_bytes` 或 `max_files` 时，按 mtime 从旧到新继续删。
# 环境变量可覆盖：QLIB_CACHE_MAX_MB（默认 4096）/ QLIB_CACHE_MAX_FILES（200）/
# QLIB_CACHE_MAX_AGE_DAYS（30）。⚠ 治理失败绝不影响主流程。
_CACHE_MAX_BYTES = int(os.environ.get("QLIB_CACHE_MAX_MB", "4096") or 0) * 1024 * 1024
_CACHE_MAX_FILES = int(os.environ.get("QLIB_CACHE_MAX_FILES", "200") or 0)
_CACHE_MAX_AGE_DAYS = float(os.environ.get("QLIB_CACHE_MAX_AGE_DAYS", "30") or 0)


def _prune_cache(max_bytes=None, max_age_days=None, max_files=None):
    """按「过期 + LRU」清理缓存目录，返回 `(删除文件数, 剩余字节数)`。

    只处理 `*.pkl` 与 `*.tmp`（原子替换的中间产物）。任何异常都被吞掉 —— 缓存治理
    失败不能影响主流程。
    """
    try:
        if not os.path.isdir(_CACHE_DIR):
            return (0, 0)
        mb = _CACHE_MAX_BYTES if max_bytes is None else int(max_bytes)
        mf = _CACHE_MAX_FILES if max_files is None else int(max_files)
        ma = _CACHE_MAX_AGE_DAYS if max_age_days is None else float(max_age_days)
        items = []
        for fn in os.listdir(_CACHE_DIR):
            if not (fn.endswith(".pkl") or fn.endswith(".tmp")):
                continue
            p = os.path.join(_CACHE_DIR, fn)
            try:
                st = os.stat(p)
            except OSError:
                continue
            items.append((st.st_mtime, st.st_size, p))
        items.sort()                       # 旧的在前
        removed = 0
        if ma and ma > 0:                  # ① 过期
            cutoff = time.time() - ma * 86400.0
            for mtime, _size, p in items:
                if mtime < cutoff:
                    try:
                        os.remove(p)
                        removed += 1
                    except OSError:
                        pass
        alive = [(m, s, p) for (m, s, p) in items if os.path.exists(p)]
        total = sum(s for _, s, _ in alive)
        while alive and ((mb and total > mb) or (mf and len(alive) > mf)):
            _, size, p = alive.pop(0)      # ② LRU：最旧优先
            try:
                os.remove(p)
                removed += 1
                total -= size
            except OSError:
                pass
        return (removed, total)
    except Exception:
        return (0, 0)


# ---------------------------------------------------------------------------
# 自定义 DataLoader
# ---------------------------------------------------------------------------


class CachedQlibDataLoader(QlibDataLoader):
    """带特征磁盘缓存的 QlibDataLoader。

    只缓存 "feature" 组（计算最重的部分）；"label" 组每次现算。
    非 dict config（无分组）时退化为父类行为。
    strip_suspended=True（默认）：对 feature 组表达式统一包 SR（停牌删行），
    使训练特征按益盟/聚宽"无停牌行"的连续交易日语义计算；label 不包。
    """

    def __init__(
        self,
        config,
        filter_pipe=None,
        swap_level=True,
        freq="day",
        inst_processors=None,
        universe=None,
        *,
        strip_suspended: bool = True,
    ):
        self._strip_suspended = bool(strip_suspended)
        # v1.18.51：股票池名（用于「按当日真实成分」过滤样本，修股票池未来函数）
        self._pool_universe = universe
        super().__init__(
            config,
            filter_pipe=filter_pipe,
            swap_level=swap_level,
            freq=freq,
            inst_processors=inst_processors,
        )

    def _apply_pool_filter(self, df):
        """按「当日是否属于股票池」过滤样本（v1.18.51，修「股票池未来函数」）。

        背景：`qlib_engine` 原先把 `D.list_instruments(market=..., start_time=...)`（**未传
        end_time**）展开成**全期并集**交给 handler ⇒ 训练样本、预测与持仓都可能落在"彼时尚未
        纳入该池"的股票上（指数纳入标准偏好规模/流动性/涨幅好的标的 ⇒ 系统性高估收益）。

        这里按**当日真实成分**过滤。过滤放在**缓存之后**：同一份原始数据缓存可服务不同池，
        不影响缓存 key 语义。`universe` 为 None 或 "all"（全 A 无成分概念，上市前天然无数据）
        时不处理；成分文件缺失/解析异常时也退化旧口径（不使回测失败）。
        """
        u = self._pool_universe
        if not u or u == "all" or df is None or len(df) == 0:
            return df
        try:
            from .inst_mask import _daily_member_mask

            mk = _daily_member_mask(u, df.index)
        except Exception:      # 过滤失败不致命：退化旧口径
            return df
        if mk is None or bool(mk.all()):
            return df
        return df[mk]

    def load(self, instruments=None, start_time=None, end_time=None):
        if not self.is_group:
            return super().load(instruments, start_time, end_time)

        out = {}
        for grp, (exprs, names) in self.fields.items():
            if self._strip_suspended and grp == "feature":
                # 停牌删行（益盟语义）：包装后的表达式参与缓存 key → 语义变更自动刷新缓存
                exprs = [_sr_wrap_expr(e) for e in exprs]
            # feature 与 label 都走缓存，但 key 都包含各自的表达式与列名：
            #  - feature key 不含 label 配置 → 改预测周期(label_horizon)时 feature 仍命中；
            #  - label key 含 label 表达式（含 label_horizon）→ 改动后 label 单独重算（便宜）。
            path = _cache_path(instruments, exprs, names, start_time, end_time)
            df = _load_cache(path)
            if df is None:
                df = self.load_group_df(instruments, exprs, names, start_time, end_time, grp)
                _save_cache(path, df)
            out[grp] = df
        return self._apply_pool_filter(pd.concat(out, axis=1))
