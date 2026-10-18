"""知识库写路径的统一检索缓存失效入口。

Redis 检索/回答缓存与进程内 BM25 索引必须一起失效；集中到一处，避免新增
写路径时只清了 Redis、忘了词法索引，导致已删除分块仍被召回。
"""

from __future__ import annotations

from app.core.logger import get_logger
from app.core.redis_client import get_redis_client
from app.services.bm25 import bm25_service
from app.services.cache import invalidate_prefixes

logger = get_logger(__name__)


async def invalidate_search_caches(kb_id: int | None = None) -> None:
    """失效 Redis 检索/回答缓存；``kb_id`` 为空时清空全部 BM25 索引。"""
    deleted = await invalidate_prefixes(get_redis_client())
    bm25_service.invalidate([int(kb_id)] if kb_id is not None else None)
    logger.info(
        "检索缓存已失效 kb_id=%s redis_keys=%d bm25_scope=%s",
        kb_id,
        deleted,
        "all" if kb_id is None else kb_id,
    )
