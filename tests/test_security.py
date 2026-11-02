"""密码哈希异步包装测试：确认 bcrypt 不再阻塞事件循环。"""

from __future__ import annotations

import asyncio
import contextlib

from app.core.security import (
    hash_password,
    hash_password_async,
    verify_password,
    verify_password_async,
    verify_password_or_dummy,
)
from config import Config


async def test_async_hash_and_verify_roundtrip(monkeypatch) -> None:
    monkeypatch.setattr(Config, "bcrypt_rounds", 4)

    password_hash = await hash_password_async("secret123")

    assert password_hash.startswith("$2")
    assert await verify_password_async("secret123", password_hash) is True
    assert await verify_password_async("wrong-pass", password_hash) is False


async def test_sync_wrappers_remain_compatible(monkeypatch) -> None:
    monkeypatch.setattr(Config, "bcrypt_rounds", 4)

    password_hash = hash_password("secret123")

    assert verify_password("secret123", password_hash) is True
    assert verify_password("wrong-pass", password_hash) is False
    assert verify_password("secret123", "not-a-bcrypt-hash") is False


async def test_async_verify_does_not_block_event_loop(monkeypatch) -> None:
    monkeypatch.setattr(Config, "bcrypt_rounds", 10)
    password_hash = await hash_password_async("secret123")

    ticks = 0

    async def ticker() -> None:
        nonlocal ticks
        while True:
            await asyncio.sleep(0.005)
            ticks += 1

    task = asyncio.create_task(ticker())
    try:
        assert await verify_password_async("secret123", password_hash) is True
    finally:
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task

    # 若 bcrypt 在事件循环里同步执行，ticker 在整段校验期间一次都跑不到
    assert ticks >= 3


async def test_unknown_user_still_runs_dummy_verification(monkeypatch) -> None:
    monkeypatch.setattr(Config, "bcrypt_rounds", 4)

    assert await verify_password_or_dummy("any-password", None) is False
    assert await verify_password_or_dummy("any-password", "not-a-bcrypt-hash") is False
