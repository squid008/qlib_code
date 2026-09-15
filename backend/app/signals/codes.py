# -*- coding: utf-8 -*-
"""外部 CSV 的**代码 / 日期**规范化（交易信号测试模块的入口关口）。

两条硬规则（用户 2026-09-15 明确）：
  ① **绝不按中文名识别标的** —— 中文名会变（改名/ST 前缀/简称调整），只认代码；
     展示用的中文名一律**剥离丢弃**。
  ② **必须靠市场后缀区分** —— 指数与股票可能同码（`000001` 同时是"平安银行(SZ)"与
     "上证指数(SH)"；`399001` 是深证成指）⇒ 无后缀且歧义的，按"股票优先"处理并**记诊断**，
     让用户能在界面上看到"这些行我不确定"，而不是静默猜。

兼容的后缀写法（同一套市场 4 种表达）：

| 来源 | 写法 | 样例 |
|---|---|---|
| qlib（本项目内部） | 市场前缀 | `SH600000` / `SZ000001` / `BJ430047` |
| 聚宽 / 米筐 | `.XSHG` / `.XSHE` | `600000.XSHG` / `000001.XSHE` |
| Wind / tushare | `.SH` / `.SZ`（`.SS` / `.SZSE` / `.SHSE` 也吃） | `600000.SH` / `000001.SZ` |
| 北交所 | `.BJ` / `.BSE` / `.NEEQ` | `430047.BJ` |

日期：`2016/1/4`、`2016-01-04`、`20160104`、`2016.1.4`、带时分秒、**Excel 序列号**（5 位数）
都能认；交给 pandas 兜底；解析不出来的记诊断并跳过（不猜当天）。
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import List, Optional, Tuple

import pandas as pd

# ---- 市场后缀 → 内部市场码（大写，不含点）----
_SUFFIX_MAP = {
    "XSHG": "SH", "SHSE": "SH", "SS": "SH", "SH": "SH",
    "XSHE": "SZ", "SZSE": "SZ", "SZ": "SZ",
    "BJ": "BJ", "BSE": "BJ", "NEEQ": "BJ",
}
# 前缀写法（qlib）：SH600000 / sz000001 / BJ430047
_PREFIX_RE = re.compile(r"^(SH|SZ|BJ)[._\-]?(\d{6})$", re.I)
# 后缀写法：600000.XSHG / 000001.sh / 430047.BJ
_SUFFIX_RE = re.compile(r"^(\d{6})[._\-]?(XSHG|XSHE|SHSE|SZSE|SS|SH|SZ|BSE|BJ|NEEQ)$", re.I)
# 纯代码：600000
_BARE_RE = re.compile(r"^(\d{6})$")
# 从任意字符串里抽代码（兜底：即使用户把中文名写得花里胡哨）
_ANY_CODE_RE = re.compile(r"(?:(SH|SZ|BJ)[._\-]?(\d{6})|(\d{6})[._\-]?(XSHG|XSHE|SHSE|SZSE|SS|SH|SZ|BSE|BJ|NEEQ)|\b(\d{6})\b)", re.I)

# 上证指数族（SH + 0xxxxx；⚠ 与深市股票码重叠 ⇒ 只用于"确实带 SH 后缀"时的指数识别）
_SH_INDEX_PREFIX = ("000", "950")
# 深证指数族（SZ + 399xxx）
_SZ_INDEX_PREFIX = ("399",)

# 各市场**股票**号段（无后缀时的推断依据）
_STOCK_PREFIX = {
    "SH": ("600", "601", "603", "605", "688", "689", "900"),
    "SZ": ("000", "001", "002", "003", "300", "301", "200"),
    "BJ": ("43", "83", "87", "88", "92", "82"),
}
# 已知的**指数**代码（⚠ 存**裸 6 位码**，与 `_infer_bare(code)` 的入参同构；
# 曾经误写成带市场前缀 ⇒ 无后缀的 `000300` 被当成深市股票、`000001` 的歧义提示也丢了）
_KNOWN_INDEX = {
    "000001", "000009", "000010", "000016", "000300", "000688",
    "000852", "000905", "000906", "000985", "950090",
    "399001", "399005", "399006", "399300", "399905",
}


@dataclass
class CodeInfo:
    """一个标的字段的解析结果。"""
    raw: str = ""
    qlib_code: Optional[str] = None      # 规范化后的 qlib 代码（如 SZ000001），解析失败为 None
    market: Optional[str] = None          # SH / SZ / BJ
    is_index: bool = False                 # 是否指数（信号回测只吃股票 ⇒ 会被忽略）
    name: str = ""                         # 剥离出来的中文名（仅用于回显，不参与识别）
    issues: List[str] = field(default_factory=list)   # 诊断（人类可读）


def strip_name(raw: str) -> Tuple[str, str]:
    """剥离中文名：`新宝股份(002705.XSHE)` → (`002705.XSHE`, `新宝股份`)。

    优先取括号内（半角/全角都吃）；没有括号时按"整串里抽代码"兜底。
    ⚠ 中文名**只作回显**，任何识别逻辑都不许用它。
    """
    s = str(raw or "").strip()
    if not s:
        return "", ""
    m = re.search(r"[（(]([^）)]*)[）)]", s)
    if m:
        inner = m.group(1).strip()
        name = (s[:m.start()] + s[m.end():]).strip(" \t,，;；")
        return (inner or s), name
    return s, ""


def normalize_code(raw: str) -> CodeInfo:
    """把任意写法的标的一列规范化成 qlib 代码（带市场前缀）。"""
    info = CodeInfo(raw=str(raw or ""))
    token, name = strip_name(raw)
    info.name = name
    token = token.strip().strip("'\"").replace(" ", "")
    if not token:
        info.issues.append("标的为空")
        return info

    market = code = None
    m = _PREFIX_RE.match(token)
    if m:                                    # qlib 写法
        market, code = m.group(1).upper(), m.group(2)
    else:
        m = _SUFFIX_RE.match(token)
        if m:                                # 聚宽/米筐/Wind 写法
            code, market = m.group(1), _SUFFIX_MAP[m.group(2).upper()]
        else:
            m = _BARE_RE.match(token)
            if m:                            # 无后缀：推断 + 歧义诊断
                code = m.group(1)
                qlib_guess = _infer_bare(code)
                market = qlib_guess[0]
                if qlib_guess[1]:
                    info.issues.append(qlib_guess[1])
            else:                            # 兜底：从整串里抽
                m2 = _ANY_CODE_RE.search(token)
                if not m2:
                    info.issues.append("未识别到 6 位代码：%s" % token[:24])
                    return info
                if m2.group(1):
                    market, code = m2.group(1).upper(), m2.group(2)
                elif m2.group(3):
                    code, market = m2.group(3), _SUFFIX_MAP[m2.group(4).upper()]
                else:
                    code = m2.group(5)
                    market = _infer_bare(code)[0]
    if not code or not market:
        info.issues.append("代码解析失败：%s" % token[:24])
        return info

    qlib_code = "%s%s" % (market, code)
    info.qlib_code = qlib_code
    info.market = market
    info.is_index = is_index_code(qlib_code)
    return info


def _infer_bare(code: str) -> Tuple[Optional[str], Optional[str]]:
    """无后缀 6 位码 → (市场, 诊断文案或 None)。

    规则：**股票号段优先**；`000001` 这种"指数与深市股票同码"的歧义情形按股票处理并提示
    （信号文件里绝大多数是股票；用户若确实要指指数，加后缀即可）。
    """
    if code in _KNOWN_INDEX and code not in ("000001", "000002", "000003", "000004", "000005"):
        # 不在歧义名单里的已知指数（如 000300 / 399001，没有同码股票）⇒ 直接当指数
        return ("SZ" if code.startswith("39") else "SH"), None
    for mk, pres in _STOCK_PREFIX.items():
        if code.startswith(pres):
            amb = code in _KNOWN_INDEX
            return mk, ("无后缀 %s 有歧义（该码同时是 %s 指数）—— 已按**股票**处理，"
                        "如要指指数请写后缀" % (code, _KNOWN_INDEX_NAMES.get(code, "指数"))) if amb else None
    # 落到这里：既不是常见股票号段也不是已知指数
    if code.startswith(_SH_INDEX_PREFIX) or code.startswith(("000", "950")):
        return "SH", "无后缀 %s 按**上证指数**处理（请确认是否应为深市股票）" % code
    if code.startswith(_SZ_INDEX_PREFIX):
        return "SZ", None
    return None, "无后缀且号段未知：%s（请补后缀，如 600000.XSHG / SZ000001）" % code


_KNOWN_INDEX_NAMES = {
    "000001": "上证指数", "000300": "沪深300", "000905": "中证500",
    "000852": "中证1000", "000906": "中证800", "000985": "中证全指",
    "399001": "深证成指", "399006": "创业板指",
}


def is_index_code(qlib_code: str) -> bool:
    """是否指数（信号回测只支持股票 ⇒ 指数行会被忽略并计数）。

    · `SH0xxxxx`：上证指数族（`SH600000` 这类股票不会被误判）；
    · `SZ399xxx`：深证指数族。
    """
    c = str(qlib_code or "").upper()
    if len(c) < 8:
        return False
    code = c[2:]
    if c.startswith("SH"):
        return code.startswith(_SH_INDEX_PREFIX) or code in ("000001", "000300")
    if c.startswith("SZ"):
        return code.startswith(_SZ_INDEX_PREFIX)
    return False


# --------------------------------------------------------------------------
# 日期
# --------------------------------------------------------------------------
_DATE_FORMATS = (
    "%Y/%m/%d", "%Y-%m-%d", "%Y%m%d", "%Y.%m.%d",
    "%Y/%m/%d %H:%M:%S", "%Y-%m-%d %H:%M:%S", "%Y/%m/%d %H:%M",
    "%Y-%m-%d %H:%M", "%Y/%m/%dT%H:%M:%S",
)
_EXCEL_EPOCH = pd.Timestamp("1899-12-30")


def parse_date(raw) -> Optional[pd.Timestamp]:
    """任意日期写法 → Timestamp（当天 0 点）；无法解析返回 None（调用方记诊断）。"""
    if raw is None:
        return None
    s = str(raw).strip().strip("'\"")
    if not s or s.lower() in ("nan", "none", "nat", "-", "null"):
        return None
    # Excel 序列号（5 位数；1900 日期系统）—— 用户从 Excel 另存 CSV 时常见
    if re.fullmatch(r"\d{5}", s):
        v = int(s)
        if 20000 <= v <= 60000:
            return (_EXCEL_EPOCH + pd.Timedelta(days=v)).normalize()
    for f in _DATE_FORMATS:
        try:
            return pd.Timestamp(pd.to_datetime(s, format=f)).normalize()
        except Exception:
            continue
    try:                                   # pandas 兜底（`2016/1/4` 这类不补零的也吃）
        t = pd.Timestamp(pd.to_datetime(s, errors="raise"))
        return t.normalize()
    except Exception:
        return None


def parse_time(raw) -> Optional[str]:
    """`09:30:00` / `93000` / `2016-01-04 09:30:00` → `"09:30:00"`（只取时分秒）。"""
    s = str(raw or "").strip()
    if not s or s.lower() in ("nan", "none", "-"):
        return None
    m = re.search(r"(\d{1,2}):(\d{2})(?::(\d{2}))?", s)
    if m:
        return "%02d:%02d:%02d" % (int(m.group(1)), int(m.group(2)), int(m.group(3) or 0))
    m = re.search(r"\b(\d{6})\b", s)       # 紧凑写法 93000 / 143000
    if m:
        v = m.group(1)
        return "%s:%s:%s" % (v[:2], v[2:4], v[4:6])
    return None
