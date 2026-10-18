"""Redis 滑动窗口限流测试。"""

import pytest
from fastapi import HTTPException

from app.core.rate_limit import enforce_rate_limit


async def test_rate_limit_blocks_after_limit(fake_redis) -> None:
    for _ in range(2):
        await enforce_rate_limit(
            fake_redis, scope="chat", identity="1", limit=2, window_seconds=60
        )

    with pytest.raises(HTTPException) as exc_info:
        await enforce_rate_limit(
            fake_redis, scope="chat", identity="1", limit=2, window_seconds=60
        )

    assert exc_info.value.status_code == 429
    assert exc_info.value.headers["Retry-After"] == "60"


async def test_rate_limit_isolated_by_scope_and_identity(fake_redis) -> None:
    await enforce_rate_limit(
        fake_redis, scope="chat", identity="1", limit=1, window_seconds=60
    )
    await enforce_rate_limit(
        fake_redis, scope="chat", identity="2", limit=1, window_seconds=60
    )
    await enforce_rate_limit(
        fake_redis, scope="login", identity="1", limit=1, window_seconds=60
    )


async def test_rate_limit_allows_when_redis_fails() -> None:
    class BrokenRedis:
        def pipeline(self):
            raise RuntimeError("redis down")

    # Redis 故障不应阻断业务请求
    await enforce_rate_limit(
        BrokenRedis(), scope="chat", identity="1", limit=1, window_seconds=60
    )
