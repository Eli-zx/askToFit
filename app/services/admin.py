"""管理员账号引导：启动期把 ADMIN_USERNAMES 中的账号幂等置为管理员。"""

from __future__ import annotations

from sqlalchemy import update

from app.core.logger import get_logger
from app.db.session import get_session_maker
from app.models.user import User
from config import Config

logger = get_logger(__name__)


async def sync_admin_users() -> int:
    """返回本次新提升为管理员的账号数量；已存在且已授权的账号不重复处理。"""
    usernames = tuple(
        dict.fromkeys(
            name.strip().lower() for name in Config.admin_usernames if name.strip()
        )
    )
    if not usernames:
        return 0

    async with get_session_maker()() as db:
        result = await db.execute(
            update(User)
            .where(User.username.in_(usernames), User.is_admin.is_(False))
            .values(is_admin=True)
        )
        await db.commit()
        promoted = int(result.rowcount or 0)

    logger.info(
        "管理员引导完成 configured=%d promoted=%d",
        len(usernames),
        promoted,
    )
    return promoted
