"""密码哈希与 JWT 签发 / 校验。"""

from __future__ import annotations

import asyncio
import weakref
from datetime import datetime, timedelta, timezone

import bcrypt
import jwt

from config import Config


def hash_password(password: str) -> str:
    """bcrypt 哈希；bcrypt 仅处理前 72 字节，入参已由 schema 限制。"""
    return bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt(rounds=Config.bcrypt_rounds)).decode(
        "utf-8"
    )


def verify_password(password: str, password_hash: str) -> bool:
    try:
        return bcrypt.checkpw(password.encode("utf-8"), password_hash.encode("utf-8"))
    except (ValueError, TypeError):
        return False


# bcrypt 是 CPU 密集操作（cost=12 单次约 190ms），直接在协程里调用会卡住
# 整个事件循环。这里用 asyncio.to_thread 下沉到线程池，并用信号量限制并发，
# 避免大量登录请求同时占满线程池（来源：app/core/security.py:hash_password_async）。
_semaphores: "weakref.WeakKeyDictionary[asyncio.AbstractEventLoop, asyncio.Semaphore]" = (
    weakref.WeakKeyDictionary()
)


def _password_semaphore() -> asyncio.Semaphore:
    loop = asyncio.get_running_loop()
    semaphore = _semaphores.get(loop)
    if semaphore is None:
        semaphore = asyncio.Semaphore(max(1, Config.password_hash_concurrency))
        _semaphores[loop] = semaphore
    return semaphore


async def hash_password_async(password: str) -> str:
    async with _password_semaphore():
        return await asyncio.to_thread(hash_password, password)


async def verify_password_async(password: str, password_hash: str) -> bool:
    async with _password_semaphore():
        return await asyncio.to_thread(verify_password, password, password_hash)


_DUMMY_PASSWORD = "asktofit-dummy-password"
_dummy_hash: str | None = None


def _dummy_hash_sync() -> str:
    global _dummy_hash
    if _dummy_hash is None:
        _dummy_hash = hash_password(_DUMMY_PASSWORD)
    return _dummy_hash


async def verify_password_or_dummy(password: str, password_hash: str | None) -> bool:
    """账号不存在时也执行一次等价耗时的校验，避免用户名枚举。"""
    if password_hash is None:
        await verify_password_async(password, await asyncio.to_thread(_dummy_hash_sync))
        return False
    return await verify_password_async(password, password_hash)


def create_access_token(user_id: int, username: str) -> tuple[str, int]:
    """返回 (token, 有效期秒数)。"""
    expires_delta = timedelta(minutes=Config.access_token_expire_minutes)
    now = datetime.now(timezone.utc)
    payload = {
        "sub": str(user_id),
        "username": username,
        "iat": now,
        "exp": now + expires_delta,
    }
    token = jwt.encode(payload, Config.jwt_secret, algorithm=Config.jwt_algorithm)
    return token, int(expires_delta.total_seconds())


def decode_access_token(token: str) -> dict:
    """解析 JWT；无效或过期时抛出 jwt.PyJWTError。"""
    return jwt.decode(token, Config.jwt_secret, algorithms=[Config.jwt_algorithm])
