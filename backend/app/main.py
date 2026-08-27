# -*- coding: utf-8 -*-
"""
FastAPI 应用入口。

启动方式（在 qlib 环境）：
    cd backend
    uvicorn app.main:app --host 0.0.0.0 --port 8000
"""
from __future__ import annotations

import os

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from . import config, __version__
from .logger import get_logger
from .routers import backtest, data, factors

logger = get_logger(__name__)


def create_app() -> FastAPI:
    app = FastAPI(
        title="Qlib 量化回测平台",
        description="基于 Qlib 的量价因子机器学习回测系统，支持 Qlib / rqalpha(h5) 多数据源。",
        version=__version__,
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

    @app.get("/api/version", summary="版本号")
    def version():
        """返回当前版本号（语义化版本）。前端页面展示用。"""
        return {"version": __version__}

    # 参数校验错误：返回友好信息，不暴露堆栈
    @app.exception_handler(RequestValidationError)
    async def validation_exc_handler(request: Request, exc: RequestValidationError):
        logger.warning("参数校验失败 %s %s: %s", request.method, request.url.path, exc.errors())
        return JSONResponse(status_code=422, content={"detail": "请求参数校验失败，请检查输入"})

    # 兜底：未捕获异常记录到服务端日志，前端只收到友好信息（不暴露堆栈）
    @app.exception_handler(Exception)
    async def unhandled_exc_handler(request: Request, exc: Exception):
        import traceback
        logger.error("未捕获异常 %s %s: %s\n%s",
                     request.method, request.url.path, exc, traceback.format_exc())
        return JSONResponse(status_code=500, content={"detail": f"服务内部错误: {exc}"})

    return app


app = create_app()
