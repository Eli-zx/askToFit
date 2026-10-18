"""密码哈希与 JWT 签发 / 校验。"""

from __future__ import annotations

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
