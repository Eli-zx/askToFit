"""后台维护任务：归档长期不活跃的会话、清理过期数据。"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta

from sqlalchemy import update

from app.core.logger import get_logger
from app.db.session import get_session_maker
from app.models.conversation import Conversation
from config import Config

logger = get_logger(__name__)


def _naive_utcnow() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


async def archive_stale_conversations(days: int | None = None) -> int:
    """将超过保留期仍未活跃的会话标记为归档，返回归档数量。

    归档只影响会话列表的默认展示，消息本身保留，用户仍可取消归档。
    """
    retention_days = Config.conversation_retention_days if days is None else days
    if retention_days <= 0:
        return 0

    now = _naive_utcnow()
    cutoff = now - timedelta(days=retention_days)
    async with get_session_maker()() as session:
        result = await session.execute(
            update(Conversation)
            .where(
                Conversation.archived_at.is_(None),
                Conversation.updated_at < cutoff,
            )
            .values(archived_at=now)
        )
        await session.commit()
        archived = int(result.rowcount or 0)

    if archived:
        logger.info(
            "会话归档完成 archived=%d retention_days=%d"
            "（app/services/maintenance.py:archive_stale_conversations）",
            archived,
            retention_days,
        )
    return archived


async def cleanup_loop(stop_event: asyncio.Event) -> None:
    """按配置间隔循环执行维护任务，直到应用关闭。"""
    interval = max(60, Config.cleanup_interval_seconds)
    while not stop_event.is_set():
        try:
            await archive_stale_conversations()
        except Exception:
            logger.error(
                "后台维护任务失败（app/services/maintenance.py:cleanup_loop）",
                exc_info=True,
            )
        try:
            await asyncio.wait_for(stop_event.wait(), timeout=interval)
        except asyncio.TimeoutError:
            continue
