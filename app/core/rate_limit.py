"""基于 Redis ZSet 的滑动窗口限流。"""

from __future__ import annotations

import time
import uuid
from typing import Any

from fastapi import HTTPException, status

from app.core.logger import get_logger
from app.services.metrics import inc
from config import Config

logger = get_logger(__name__)


async def enforce_rate_limit(
    redis: Any | None,
    *,
    scope: str,
    identity: str,
    limit: int | None = None,
    window_seconds: int | None = None,
) -> None:
    """超过窗口内请求数时抛出 429；Redis 异常时放行并记录告警。"""
    if not Config.rate_limit_enabled or redis is None:
        return

    limit = limit or Config.rate_limit_requests
    window = window_seconds or Config.rate_limit_window_seconds
    key = f"asktofit:ratelimit:{scope}:{identity}"
    now = time.time()
    member = f"{now:.6f}:{uuid.uuid4().hex[:8]}"

    try:
        pipeline = redis.pipeline()
        pipeline.zremrangebyscore(key, 0, now - window)
        pipeline.zadd(key, {member: now})
        pipeline.zcard(key)
        pipeline.expire(key, window)
        results = await pipeline.execute()
        count = int(results[2])
    except Exception:
        logger.warning(
            "限流检查失败，按放行处理 scope=%s identity=%s"
            "（app/core/rate_limit.py:enforce_rate_limit）",
            scope,
            identity,
            exc_info=True,
        )
        return

    if count > limit:
        inc("asktofit_rate_limited_total", {"scope": scope})
        logger.warning(
            "触发限流 scope=%s identity=%s count=%d limit=%d",
            scope,
            identity,
            count,
            limit,
        )
        raise HTTPException(
            status.HTTP_429_TOO_MANY_REQUESTS,
            "请求过于频繁，请稍后再试",
            headers={"Retry-After": str(window)},
        )
