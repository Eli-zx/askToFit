"""健康检查接口。"""

from __future__ import annotations

from fastapi import APIRouter

from config import Config

router = APIRouter(tags=["系统"])


@router.get("/health")
async def health() -> dict:
    return {
        "status": "ok",
        "app": Config.app_name,
        "version": Config.app_version,
    }
