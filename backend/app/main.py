# -*- coding: utf-8 -*-
"""
FastAPI 应用入口。

启动方式（在 qlib 环境）：
    cd backend
    uvicorn app.main:app --host 0.0.0.0 --port 8000
"""
from __future__ import annotations

import os

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from . import config
from .routers import backtest, data, factors


def create_app() -> FastAPI:
    app = FastAPI(
        title="Qlib 量化回测平台",
        description="基于 Qlib 的量价因子机器学习回测系统，支持 Qlib / rqalpha(h5) 多数据源。",
        version="0.1.0",
    )

    # CORS
    app.add_middleware(
        CORSMiddleware,
        allow_origins=config.CORS_ORIGINS,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # 确保工作目录存在
    os.makedirs(config.WORK_DIR, exist_ok=True)

    # 路由
    app.include_router(backtest.router)
    app.include_router(data.router)
    app.include_router(factors.router)

    @app.get("/", summary="服务健康检查")
    def root():
        return {"service": "qlib-backtest-api", "status": "ok"}

    @app.get("/health", summary="健康检查")
    def health():
        return {"status": "ok"}

    return app


app = create_app()
