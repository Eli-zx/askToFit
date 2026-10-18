"""FastAPI 应用入口。"""

from __future__ import annotations

import asyncio
import time
import uuid
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, Request, Response, status
from fastapi.middleware.cors import CORSMiddleware

from app.api.routes import auth, chat, evaluation, health, knowledge
from app.core.logger import get_logger, setup_logging
from app.core.redis_client import close_redis
from app.db.session import (
    dispose_engine,
    ensure_database,
    ensure_schema_upgrades,
    init_models,
)
from app.services.admin import sync_admin_users
from app.services.llm import llm_service
from app.services.maintenance import cleanup_loop
from app.services.metrics import inc, observe, render_prometheus
from config import Config

setup_logging()
logger = get_logger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("正在启动 %s v%s", Config.app_name, Config.app_version)
    Config.ensure_dirs()
    Config.validate_model_paths()
    try:
        await asyncio.to_thread(ensure_database)
        await init_models()
        await ensure_schema_upgrades()
        await sync_admin_users()
    except Exception:
        logger.exception("数据库初始化失败，请检查 MySQL 服务与配置")
        raise

    stop_event = asyncio.Event()
    maintenance_task = asyncio.create_task(cleanup_loop(stop_event))
    logger.info("服务启动完成")
    try:
        yield
    finally:
        stop_event.set()
        try:
            await asyncio.wait_for(maintenance_task, timeout=10)
        except (asyncio.TimeoutError, asyncio.CancelledError):
            maintenance_task.cancel()
        await llm_service.close()
        await close_redis()
        await dispose_engine()
        logger.info("服务已停止")


app = FastAPI(
    title=Config.app_name,
    version=Config.app_version,
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:5173",
        "http://127.0.0.1:5173",
        "http://localhost:3000",
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.middleware("http")
async def request_logging_middleware(request: Request, call_next):
    request_id = request.headers.get("X-Request-ID") or uuid.uuid4().hex[:12]
    started = time.perf_counter()
    try:
        response = await call_next(request)
    except Exception:
        elapsed = (time.perf_counter() - started) * 1000
        logger.exception(
            "[%s] %s %s -> 500 (%.1fms)", request_id, request.method, request.url.path, elapsed
        )
        raise
    elapsed = (time.perf_counter() - started) * 1000
    response.headers["X-Request-ID"] = request_id
    logger.info(
        "[%s] %s %s -> %d (%.1fms)",
        request_id,
        request.method,
        request.url.path,
        response.status_code,
        elapsed,
    )
    return response


@app.middleware("http")
async def metrics_middleware(request: Request, call_next):
    if not Config.metrics_enabled:
        return await call_next(request)

    started = time.perf_counter()
    status_code = 500
    try:
        response = await call_next(request)
        status_code = response.status_code
        return response
    finally:
        route = request.scope.get("route")
        # 使用路由模板而非原始 URL，避免把会话 ID 变成指标标签
        path = getattr(route, "path", None) or "unmatched"
        if path != f"{Config.api_prefix}/metrics":
            labels = {
                "method": request.method,
                "path": path,
                "status": str(status_code),
            }
            inc("asktofit_http_requests_total", labels)
            observe(
                "asktofit_http_request_duration_seconds",
                time.perf_counter() - started,
                {"method": request.method, "path": path},
            )


app.include_router(health.router, prefix=Config.api_prefix)
app.include_router(auth.router, prefix=Config.api_prefix)
app.include_router(knowledge.router, prefix=Config.api_prefix)
app.include_router(chat.router, prefix=Config.api_prefix)
app.include_router(evaluation.router, prefix=Config.api_prefix)


@app.get(f"{Config.api_prefix}/metrics", include_in_schema=False)
async def metrics() -> Response:
    if not Config.metrics_enabled:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "指标采集已关闭")
    return Response(
        render_prometheus(),
        media_type="text/plain; version=0.0.4; charset=utf-8",
    )


@app.get("/")
async def root() -> dict:
    return {
        "name": Config.app_name,
        "version": Config.app_version,
        "docs": "/docs",
    }
