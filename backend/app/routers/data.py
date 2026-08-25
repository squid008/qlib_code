# -*- coding: utf-8 -*-
"""
数据查询 API 路由。

通过数据源抽象层对外提供统一的数据访问接口。
前端 / 因子研究 / 回测引擎都通过这些接口获取数据，而不关心底层数据源。
"""
from __future__ import annotations

from typing import List, Optional

from fastapi import APIRouter, HTTPException, Query

from ..datasource.factory import get_data_source, list_data_sources
from ..datasource.base import (
    DataSourceError,
    DataNotAvailableError,
    DailyBar,
    MinuteBar,
    FinancialData,
    IndustryData,
    IndexConstituent,
)

router = APIRouter(prefix="/api", tags=["data"])


@router.get("/data-sources", summary="列出可用数据源及其能力")
def data_sources():
    return list_data_sources()


@router.get("/data/daily-bars", response_model=List[DailyBar], summary="获取日线数据")
def daily_bars(
    instrument: str,
    start_date: str,
    end_date: str,
    adjust: str = "none",
    source: str = "qlib",
):
    try:
        return get_data_source(source).get_daily_bars(instrument, start_date, end_date, adjust)
    except DataNotAvailableError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except DataSourceError as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/data/minute-bars", response_model=List[MinuteBar], summary="获取分钟数据")
def minute_bars(
    instrument: str,
    start_dt: str,
    end_dt: str,
    freq: str = "1min",
    adjust: str = "none",
    source: str = "rqalpha",
):
    try:
        return get_data_source(source).get_minute_bars(instrument, start_dt, end_dt, freq, adjust)
    except DataNotAvailableError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except DataSourceError as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/data/financial", response_model=List[FinancialData], summary="获取财报数据")
def financial(
    instrument: str,
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
    source: str = "rqalpha",
):
    try:
        return get_data_source(source).get_financial_data(instrument, start_date, end_date)
    except DataNotAvailableError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except DataSourceError as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/data/industry", response_model=List[IndustryData], summary="获取行业分类")
def industry(instrument: str, source: str = "rqalpha"):
    try:
        return get_data_source(source).get_industry(instrument)
    except DataNotAvailableError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except DataSourceError as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/data/index-constituents", response_model=List[IndexConstituent], summary="获取指数成分股")
def index_constituents(index_code: str, source: str = "rqalpha"):
    try:
        return get_data_source(source).get_index_constituents(index_code)
    except DataNotAvailableError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except DataSourceError as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/data/instruments", response_model=List[str], summary="列出证券代码")
def instruments(market: str = "all", source: str = "qlib"):
    try:
        return get_data_source(source).list_instruments(market)
    except DataSourceError as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/data/calendar", response_model=List[str], summary="获取交易日历")
def calendar(start_date: str, end_date: str, source: str = "qlib"):
    try:
        cal = get_data_source(source).get_calendar(start_date, end_date)
        return [d.isoformat() for d in cal]
    except DataSourceError as e:
        raise HTTPException(status_code=500, detail=str(e))
