# -*- coding: utf-8 -*-
"""
rqalpha 数据源（基于本地 E:\\rq bundle h5 数据文件）。

数据结构（实测探测 E:\\rq）：
  - bundle/stocks.h5        股票日线，key=代码(如 000001.XSHE)，
                            字段 [datetime(i8,YYYYMMDDHHMMSS), open, close, high, low,
                                  prev_close, limit_up, limit_down, volume, total_turnover]
  - bundle/indexes.h5       指数日线，key=代码(如 000001.XSHG)，字段同上（无涨跌停）
  - bundle/dividends.h5     分红送转：book_closure_date/announcement_date/
                            dividend_cash_before_tax/ex_dividend_date/payable_date/round_lot
  - bundle/trading_dates.npy 交易日历，int32 数组，元素为 YYYYMMDD
  - bundle/h5/equities/*.h5 分钟线，单股一文件：data(分钟结构化数组)+index(每日起始行号)
  - finance/pit/*.h5        财报，单股一文件：info_date/quarter/if_adjusted/fields(394字段)
  - constituents/index/*.h5 指数成分，一指数一文件：change_dates/components/weights
  - constituents/etf/       ETF 相关

读取策略：按需读取（只打开指定股票/指数/日期的数据），不加载全量，避免 150G 数据卡死。

代码格式约定：
  - 内部/文件：rqalpha bundle 大写（如 000001.XSHE / 000001.XSHG）
  - 对外统一：base.py 契约，小写（如 sz000001 / sh600000）
"""
from __future__ import annotations

from datetime import date, datetime
from pathlib import Path
from typing import Dict, List, Optional

import numpy as np

from .base import (
    DataSource,
    DailyBar,
    FinancialData,
    IndustryData,
    IndexConstituent,
    MinuteBar,
    DataNotAvailableError,
)


class RQAlphaDataSource(DataSource):
    """基于 rqalpha bundle h5 文件的数据源（日线/分钟/财报/指数成分）。"""

    name = "rqalpha"

    # bundle 目录结构（相对 bundle 根的路径）
    _STOCK_DAILY = "stocks.h5"      # 股票日线
    _INDEX_DAILY = "indexes.h5"     # 指数日线
    _DIVIDENDS = "dividends.h5"     # 分红
    _TRADING_DATES = "trading_dates.npy"
    _EQUITY_MINUTE_DIR = "h5/equities"      # 分钟线（单股一文件）
    _FINANCE_PIT_DIR = "finance/pit"        # 财报（单股一文件）
    _CONSTITUENTS_DIR = "constituents/index"  # 指数成分

    def __init__(self, bundle_path: Optional[str] = None, finance_dir: Optional[str] = None,
                 constituents_dir: Optional[str] = None):
        self.bundle_path = Path(bundle_path) if bundle_path else Path.home() / ".rqalpha" / "bundle"
        # 财报/成分目录允许单独指定（默认在 bundle 相邻的 finance/constituents）
        root = self.bundle_path.parent
        self.finance_dir = Path(finance_dir) if finance_dir else root / "finance" / "pit"
        self.constituents_dir = Path(constituents_dir) if constituents_dir else root / "constituents" / "index"
        self._h5_files: Dict[str, object] = {}  # 文件路径 -> h5 句柄缓存

    @property
    def capabilities(self):
        return {
            "daily": True,
            "minute": True,
            "financial": True,
            "industry": False,           # bundle 无独立行业分类文件（行业在财报/第三方）
            "index_constituent": True,
        }

    # ------------------------------------------------------------------
    # 内部：h5 句柄管理（惰性打开 + 缓存，用完不主动关以复用；进程级单例）
    # ------------------------------------------------------------------

    def _open_h5(self, path: Path):
        """惰性打开 h5 文件并缓存句柄。"""
        import h5py
        p = str(path)
        if p not in self._h5_files:
            if not path.exists():
                raise DataNotAvailableError(f"数据文件不存在: {path}")
            self._h5_files[p] = h5py.File(p, "r")
        return self._h5_files[p]

    # ------------------------------------------------------------------
    # 代码格式转换
    # ------------------------------------------------------------------

    @staticmethod
    def _to_file_code(instrument: str) -> str:
        """对外小写(sz000001) -> bundle 文件代码(000001.XSHE)。兼容已大写输入。"""
        code = instrument.strip().upper()
        digits = "".join(ch for ch in code if ch.isdigit())
        if code.startswith("SZ"):
            return f"{digits}.XSHE"
        if code.startswith("SH"):
            return f"{digits}.XSHG"
        if code.startswith("BJ"):
            return f"{digits}.XSHG"
        # 已是 bundle 格式（000001.XSHE / 600000.XSHG）
        if "." in code:
            return code
        # 纯数字：深市默认 XSHE，沪市(6/5开头) XSHG
        if digits.startswith(("6", "5", "9")):
            return f"{digits}.XSHG"
        return f"{digits}.XSHE"

    @staticmethod
    def _from_file_code(code: str) -> str:
        """bundle 文件代码(000001.XSHE) -> 对外统一小写(sz000001)。"""
        digits = "".join(ch for ch in code if ch.isdigit())
        upper = code.upper()
        if upper.endswith("XSHG"):
            return f"sh{digits}"
        if upper.endswith("XSHE"):
            return f"sz{digits}"
        if upper.endswith("INDX"):
            return f"idx{digits}"
        return f"sz{digits}"

    @staticmethod
    def _parse_datetime(v) -> datetime:
        """解析 bundle datetime(int YYYYMMDDHHMMSS 或 YYYYMMDD) 为 datetime。"""
        s = str(int(v))
        s = s.ljust(14, "0")  # 补齐到 YYYYMMDDHHMMSS
        try:
            return datetime(int(s[0:4]), int(s[4:6]), int(s[6:8]),
                            int(s[8:10]) if len(s) > 8 else 0,
                            int(s[10:12]) if len(s) > 10 else 0)
        except Exception:
            raise DataNotAvailableError(f"无法解析日期: {v}")

    def _filter_by_date(self, arr, start_dt: datetime, end_dt: datetime):
        """按 datetime 字段过滤结构化数组，返回 (indices, list[DailyBar])。"""
        dts = arr["datetime"]
        # bundle datetime 是 YYYYMMDDHHMMSS，日期区间比较用 YYYYMMDD
        start_n = int(start_dt.strftime("%Y%m%d") + "000000")
        end_n = int(end_dt.strftime("%Y%m%d") + "235959")
        mask = (dts >= start_n) & (dts <= end_n)
        idxs = np.where(mask)[0]
        return idxs

    # ------------------------------------------------------------------
    # 日线
    # ------------------------------------------------------------------

    def get_daily_bars(self, instrument: str, start_date: str, end_date: str,
                       adjust: str = "none") -> List[DailyBar]:
        """读取 stocks.h5 / indexes.h5 的日线。instrument 支持小写(sz000001)或大写(000001.XSHE)。"""
        code = self._to_file_code(instrument)
        start_dt = datetime.strptime(start_date[:10], "%Y-%m-%d")
        end_dt = datetime.strptime(end_date[:10], "%Y-%m-%d")

        # 先尝试股票日线；若代码看起来像指数（INDX/0开头指数）则读指数
        bars = []
        try:
            f = self._open_h5(self.bundle_path / self._STOCK_DAILY)
            if code in f:
                arr = f[code]
                idxs = self._filter_by_date(arr, start_dt, end_dt)
                for i in idxs:
                    row = arr[i]
                    bars.append(DailyBar(
                        instrument=instrument,
                        datetime=self._parse_datetime(row["datetime"]),
                        open=float(row["open"]),
                        high=float(row["high"]),
                        low=float(row["low"]),
                        close=float(row["close"]),
                        volume=float(row["volume"]),
                        amount=float(row["total_turnover"]),
                        factor=1.0,
                    ))
                return bars
        except DataNotAvailableError:
            raise
        except Exception:
            pass

        # 指数日线（指数代码可能用 XSHG 或 XSHE 后缀，需尝试多种形式）
        digits = "".join(ch for ch in code if ch.isdigit())
        index_candidates = [
            f"{digits}.XSHG",
            f"{digits}.XSHE",
            f"{digits}.INDX",
        ]
        try:
            f = self._open_h5(self.bundle_path / self._INDEX_DAILY)
            for ic in index_candidates:
                if ic in f:
                    arr = f[ic]
                    idxs = self._filter_by_date(arr, start_dt, end_dt)
                    for i in idxs:
                        row = arr[i]
                        bars.append(DailyBar(
                            instrument=instrument,
                            datetime=self._parse_datetime(row["datetime"]),
                            open=float(row["open"]),
                            high=float(row["high"]),
                            low=float(row["low"]),
                            close=float(row["close"]),
                            volume=float(row["volume"]),
                            amount=float(row["total_turnover"]),
                            factor=1.0,
                        ))
                    return bars
        except Exception:
            pass

        raise DataNotAvailableError(f"数据源中没有 {instrument} 的日线数据")

    # ------------------------------------------------------------------
    # 分钟线
    # ------------------------------------------------------------------

    def get_minute_bars(self, instrument: str, start_dt: str, end_dt: str,
                        freq: str = "1min", adjust: str = "none") -> List[MinuteBar]:
        """读取 bundle/h5/equities/{code}.h5 的分钟线。

        freq 仅支持 1min（原始数据为 1 分钟）；更高周期需后续聚合。
        """
        if freq not in ("1min", "1m"):
            raise DataNotAvailableError("分钟线当前仅支持 1min 频率（bundle 原始数据为 1 分钟）")
        code = self._to_file_code(instrument)
        path = self.bundle_path / self._EQUITY_MINUTE_DIR / f"{code}.h5"
        f = self._open_h5(path)
        data = f["data"]
        idx = f["index"]  # [date(int YYYYMMDD), line_no]

        # 定位日期区间对应的行范围
        start_y = int(start_dt[:10].replace("-", ""))
        end_y = int(end_dt[:10].replace("-", ""))
        dates = idx["date"]
        start_pos = int(np.searchsorted(dates, start_y, side="left"))
        end_pos = int(np.searchsorted(dates, end_y, side="right"))
        if start_pos >= end_pos:
            raise DataNotAvailableError(f"数据源中没有 {instrument} 在 {start_dt}~{end_dt} 的分钟数据")

        line_start = int(idx["line_no"][start_pos])
        # 取到 end_pos 之前最后一个日期的 line_no 起点之后的所有行
        line_end = int(data.shape[0])
        if end_pos < len(dates):
            line_end = int(idx["line_no"][end_pos])
        sub = data[line_start:line_end]

        bars = []
        for row in sub:
            bars.append(MinuteBar(
                instrument=instrument,
                datetime=self._parse_datetime(row["datetime"]),
                open=float(row["open"]),
                high=float(row["high"]),
                low=float(row["low"]),
                close=float(row["close"]),
                volume=float(row["volume"]),
                amount=float(row["total_turnover"]) if "total_turnover" in data.dtype.names else 0.0,
                factor=1.0,
                freq="1min",
            ))
        return bars

    # ------------------------------------------------------------------
    # 财报
    # ------------------------------------------------------------------

    def get_financial_data(self, instrument: str, start_date: Optional[str] = None,
                           end_date: Optional[str] = None) -> List[FinancialData]:
        """读取 finance/pit/{code}.h5 的财报数据。"""
        code = self._to_file_code(instrument)
        path = self.finance_dir / f"{code}.h5"
        f = self._open_h5(path)

        info_date = f["info_date"]  # bytes b'YYYY-MM-DD'
        quarter = f["quarter"]       # bytes b'YYYYqN'
        fields = f["fields"]

        n = info_date.shape[0]
        # 通用字段映射：尽量从 fields 里取常见字段；缺失则 None
        def _fval(field_key):
            if field_key in fields:
                arr = fields[field_key]
                if arr.shape[0] != n:
                    return None
                return arr
            return None

        rev = _fval("revenue")
        net = _fval("net_profit")
        total_assets = _fval("total_assets")
        total_liab = _fval("total_liabilities")
        equity = _fval("equity")
        eps = _fval("basic_earnings_per_share")
        roe = _fval("roe")

        result = []
        for i in range(n):
            qdate_str = info_date[i].decode() if isinstance(info_date[i], bytes) else str(info_date[i])
            report_str = quarter[i].decode() if isinstance(quarter[i], bytes) else str(quarter[i])
            # 报告期转换为 date：'1990q4' -> 1990-12-31
            try:
                y = int(report_str[:4])
                q = report_str[5]
                month = {"1": "03", "2": "06", "3": "09", "4": "12"}.get(q, "12")
                report_date = date(y, int(month), 31)
            except Exception:
                report_date = None
            try:
                ann_date = datetime.strptime(qdate_str[:10], "%Y-%m-%d").date()
            except Exception:
                ann_date = None
            if start_date and ann_date and ann_date < datetime.strptime(start_date[:10], "%Y-%m-%d").date():
                continue
            if end_date and ann_date and ann_date > datetime.strptime(end_date[:10], "%Y-%m-%d").date():
                continue
            result.append(FinancialData(
                instrument=instrument,
                report_date=report_date or date(1900, 1, 1),
                announce_date=ann_date,
                revenue=float(rev[i]) if rev is not None else None,
                net_profit=float(net[i]) if net is not None else None,
                total_assets=float(total_assets[i]) if total_assets is not None else None,
                total_liabilities=float(total_liab[i]) if total_liab is not None else None,
                equity=float(equity[i]) if equity is not None else None,
                eps=float(eps[i]) if eps is not None else None,
                roe=float(roe[i]) if roe is not None else None,
            ))
        return result

    # ------------------------------------------------------------------
    # 行业
    # ------------------------------------------------------------------

    def get_industry(self, instrument: str) -> List[IndustryData]:
        raise DataNotAvailableError("rqalpha bundle 无独立行业分类文件，行业数据暂不可用")

    # ------------------------------------------------------------------
    # 指数成分
    # ------------------------------------------------------------------

    def get_index_constituents(self, index_code: str) -> List[IndexConstituent]:
        """读取 constituents/index/{code}.h5 的指数成分及权重。index_code 如 sz000300 / 000300.XSHG。

        该文件按"成分调整日"存储历史全量成分（weights 可达数百万行），
        这里默认返回**最新一个调整日**的成分+权重（最常用场景），避免全量加载。
        """
        # 尝试多种代码形式（000300.XSHG / 000300.XSHE / 000300.INDX）
        digits = "".join(ch for ch in index_code if ch.isdigit())
        code = self._to_file_code(index_code)
        candidates = [code, f"{digits}.XSHG", f"{digits}.XSHE", f"{digits}.INDX"]
        f = None
        for c in candidates:
            p = self.constituents_dir / f"{c}.h5"
            if p.exists():
                f = self._open_h5(p)
                break
        if f is None:
            raise DataNotAvailableError(f"数据源中没有指数 {index_code} 的成分数据")

        # weights: 三个并行数组 date/order_book_id/weight（一次性读入内存，向量化过滤）
        w = f["weights"]
        dates = np.asarray(w["date"])
        ids = np.asarray(w["order_book_id"])
        weights = np.asarray(w["weight"])

        # 取最新一个生效日期（bytes b'YYYY-MM-DD'）
        last_date_raw = dates[-1]
        last_date_str = last_date_raw.decode() if isinstance(last_date_raw, bytes) else str(last_date_raw)[:10]

        # 向量化：只保留与最新日期相同的行
        if isinstance(dates[0], bytes):
            mask = dates == dates[-1]
        else:
            mask = np.array([str(d)[:10] == last_date_str for d in dates], dtype=bool)
        sel_ids = ids[mask]
        sel_weights = weights[mask]
        eff_date = datetime.strptime(last_date_str, "%Y-%m-%d").date()

        result = []
        for i in range(sel_ids.shape[0]):
            raw = sel_ids[i]
            result.append(IndexConstituent(
                instrument=self._from_file_code(raw.decode() if isinstance(raw, bytes) else str(raw)),
                index_code=index_code,
                weight=float(sel_weights[i]),
                effective_date=eff_date,
            ))
        return result

    # ------------------------------------------------------------------
    # 工具方法
    # ------------------------------------------------------------------

    def list_instruments(self, market: str = "all") -> List[str]:
        """列出 stocks.h5 中所有股票代码（对外小写格式）。"""
        f = self._open_h5(self.bundle_path / self._STOCK_DAILY)
        codes = []
        for k in f.keys():
            lower = self._from_file_code(k)
            if market in ("all", "stock"):
                codes.append(lower)
            elif market == "index":
                continue
        return codes

    def get_calendar(self, start_date: str, end_date: str) -> List[date]:
        """读取 trading_dates.npy 交易日历。"""
        path = self.bundle_path / self._TRADING_DATES
        if not path.exists():
            raise DataNotAvailableError(f"交易日历文件不存在: {path}")
        dates = np.load(str(path))  # int32 YYYYMMDD
        start_n = int(start_date[:10].replace("-", ""))
        end_n = int(end_date[:10].replace("-", ""))
        result = []
        for v in dates:
            if start_n <= int(v) <= end_n:
                s = str(int(v))
                result.append(date(int(s[0:4]), int(s[4:6]), int(s[6:8])))
        return result
