"""Redis 异步客户端。"""

from __future__ import annotations

from collections.abc import AsyncGenerator

import redis.asyncio as aioredis

from config import Config

_client: aioredis.Redis | None = None


def get_redis_client() -> aioredis.Redis:
    global _client
    if _client is None:
        _client = aioredis.Redis(
            host=Config.redis_host,
            port=Config.redis_port,
            password=Config.redis_password,
            db=Config.db,
            decode_responses=True,
            socket_connect_timeout=5,
            socket_timeout=5,
        )
    return _client


async def get_redis() -> AsyncGenerator[aioredis.Redis, None]:
    """FastAPI 依赖：返回共享连接池客户端。"""
    yield get_redis_client()


async def close_redis() -> None:
    global _client
    if _client is not None:
        await _client.aclose()
        _client = None
