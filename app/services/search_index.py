"""知识库写路径的统一检索缓存失效入口。

Redis 检索缓存与进程内 BM25 索引必须一起失效；集中到一处，避免新增写路径
时只清了 Redis、忘了词法索引，导致已删除分块仍被召回。

Redis 侧不再 scan 删除，而是递增 KB 版本号：检索缓存键包含版本号，
旧键会按各自的 TTL 自然过期，不会因为一次知识变更清空所有用户缓存。
"""

from __future__ import annotations

from app.core.logger import get_logger
from app.core.redis_client import get_redis_client
from app.services.bm25 import bm25_service
from app.services.cache import bump_kb_version

logger = get_logger(__name__)


async def invalidate_search_caches(kb_id: int | None = None) -> None:
    """递增 Redis 版本号并清空对应 BM25 索引。

    ``kb_id`` 为空时递增全局版本并清空全部 BM25 索引。
    """
    version = await bump_kb_version(get_redis_client(), kb_id)
    bm25_service.invalidate([int(kb_id)] if kb_id is not None else None)
    logger.info(
        "检索缓存已失效 kb_id=%s redis_version=%s bm25_scope=%s",
        kb_id,
        version,
        "all" if kb_id is None else kb_id,
    )
