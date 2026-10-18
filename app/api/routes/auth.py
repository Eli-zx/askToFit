"""注册、登录、验证码与当前用户接口。"""

from __future__ import annotations

import redis.asyncio as aioredis
from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_user
from app.core.logger import get_logger
from app.core.rate_limit import enforce_rate_limit
from app.core.redis_client import get_redis
from app.core.security import create_access_token, hash_password, verify_password
from config import Config
from app.db.session import get_db
from app.models.user import User
from app.schemas.auth import (
    CaptchaResponse,
    LoginRequest,
    RegisterRequest,
    TokenResponse,
    UserOut,
)
from app.services.captcha import create_captcha, verify_captcha

logger = get_logger(__name__)

router = APIRouter(prefix="/auth", tags=["认证"])


@router.get("/captcha", response_model=CaptchaResponse, summary="获取图形验证码")
async def get_captcha(redis: aioredis.Redis = Depends(get_redis)) -> dict:
    return await create_captcha(redis)


@router.post(
    "/register",
    response_model=UserOut,
    status_code=status.HTTP_201_CREATED,
    summary="用户注册",
)
async def register(
    payload: RegisterRequest,
    request: Request,
    db: AsyncSession = Depends(get_db),
    redis: aioredis.Redis = Depends(get_redis),
) -> User:
    await enforce_rate_limit(
        redis,
        scope="register",
        identity=request.client.host if request.client else "unknown",
        limit=Config.rate_limit_login_requests,
    )
    if not await verify_captcha(redis, payload.captcha_id, payload.captcha_code):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "验证码错误或已过期")

    exists = await db.scalar(select(User.id).where(User.username == payload.username))
    if exists is not None:
        raise HTTPException(status.HTTP_409_CONFLICT, "该账号已被注册")

    user = User(
        username=payload.username,
        nickname=payload.username,
        password_hash=hash_password(payload.password),
    )
    db.add(user)
    try:
        await db.commit()
    except IntegrityError:
        await db.rollback()
        raise HTTPException(status.HTTP_409_CONFLICT, "该账号已被注册") from None

    await db.refresh(user)
    logger.info("用户注册成功 user_id=%s username=%s", user.id, user.username)
    return user


@router.post("/login", response_model=TokenResponse, summary="用户登录")
async def login(
    payload: LoginRequest,
    request: Request,
    db: AsyncSession = Depends(get_db),
    redis: aioredis.Redis = Depends(get_redis),
) -> TokenResponse:
    client_host = request.client.host if request.client else "unknown"
    await enforce_rate_limit(
        redis,
        scope="login",
        identity=f"{client_host}:{payload.username}",
        limit=Config.rate_limit_login_requests,
    )
    if not await verify_captcha(redis, payload.captcha_id, payload.captcha_code):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "验证码错误或已过期")

    user = await db.scalar(select(User).where(User.username == payload.username))
    if user is None or not verify_password(payload.password, user.password_hash):
        logger.warning("登录失败 username=%s", payload.username)
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "账号或密码错误")

    if not user.is_active:
        logger.warning("禁用账号尝试登录 username=%s", payload.username)
        raise HTTPException(status.HTTP_403_FORBIDDEN, "账号已被禁用，请联系管理员")

    token, expires_in = create_access_token(user.id, user.username)
    logger.info("用户登录成功 user_id=%s username=%s", user.id, user.username)
    return TokenResponse(
        access_token=token,
        expires_in=expires_in,
        user=UserOut.model_validate(user),
    )


@router.get("/me", response_model=UserOut, summary="获取当前用户信息")
async def read_me(current_user: User = Depends(get_current_user)) -> User:
    return current_user
