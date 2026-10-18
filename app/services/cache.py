"""Redis 缓存键、读写与知识库变更后的失效策略。"""

from __future__ import annotations

import hashlib
import json
from typing import Any

from app.core.logger import get_logger
from app.services.metrics import inc
from config import Config

logger = get_logger(__name__)

PREFIX_INTENT = "asktofit:intent:"
PREFIX_EMBEDDING = "asktofit:embedding:"
PREFIX_RETRIEVAL = "asktofit:retrieval:"
PREFIX_ANSWER = "asktofit:answer:"
PREFIX_STRATEGY = "asktofit:strategy:"

ALL_PREFIXES = (
    PREFIX_INTENT,
    PREFIX_EMBEDDING,
    PREFIX_RETRIEVAL,
    PREFIX_ANSWER,
    PREFIX_STRATEGY,
)


def _digest(payload: Any) -> str:
    text = json.dumps(payload, ensure_ascii=False, sort_keys=True, default=str)
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def embedding_key(text: str) -> str:
    return PREFIX_EMBEDDING + _digest({"text": text, "model": Config.bge_m3})


def retrieval_config_fingerprint() -> dict[str, Any]:
    """影响检索结果的配置快照。

    缓存键必须随影响排序/过滤行为的配置变化，避免调参后仍命中旧结果。
    """
    return {
        "dense_weight": Config.dense_weight,
        "sparse_weight": Config.sparse_weight,
        "rerank_enabled": Config.rerank_enabled,
        "rerank_candidate_k": Config.rerank_candidate_k,
        "rerank_score_threshold": Config.rerank_score_threshold,
        "retrieval_dedup_similarity": Config.retrieval_dedup_similarity,
        "bm25_enabled": Config.bm25_enabled,
        "bm25_mode": Config.bm25_mode,
        "bm25_rrf_weight": Config.bm25_rrf_weight,
        "bm25_top_k": Config.bm25_top_k,
        "rrf_k": Config.rrf_k,
    }


def retrieval_key(
    query: str,
    kb_modes: dict[str, list[int]],
    final_k: int,
    recall_k: int,
    *,
    queries: tuple[str, ...] = (),
    strategy: str = "direct",
    bm25_mode: str = "off",
    rrf_k: int | None = None,
    config_fingerprint: dict[str, Any] | None = None,
) -> str:
    normalized_modes = {
        mode: sorted(int(kb_id) for kb_id in kb_ids)
        for mode, kb_ids in sorted(kb_modes.items())
    }
    return PREFIX_RETRIEVAL + _digest(
        {
            "query": query,
            "queries": list(queries),
            "strategy": strategy,
            "kb_modes": normalized_modes,
            "final_k": final_k,
            "recall_k": recall_k,
            "bm25_mode": bm25_mode,
            "rrf_k": Config.rrf_k if rrf_k is None else rrf_k,
            "config": config_fingerprint or retrieval_config_fingerprint(),
        }
    )


def answer_key(
    *,
    messages: list[dict[str, str]],
    model_name: str,
    temperature: float,
    top_p: float,
    max_tokens: int,
) -> str:
    return PREFIX_ANSWER + _digest(
        {
            "messages": messages,
            "model": model_name,
            "temperature": temperature,
            "top_p": top_p,
            "max_tokens": max_tokens,
        }
    )


async def get_json(redis: Any | None, key: str, cache_name: str) -> Any | None:
    if redis is None or not Config.cache_enabled:
        return None
    try:
        raw = await redis.get(key)
        if raw is None:
            return None
        inc("asktofit_cache_hits_total", {"cache": cache_name})
        return json.loads(raw)
    except Exception:
        logger.warning("读取缓存失败 cache=%s key=%s", cache_name, key, exc_info=True)
        return None


async def set_json(
    redis: Any | None, key: str, value: Any, ttl: int, cache_name: str
) -> None:
    if redis is None or not Config.cache_enabled or ttl <= 0:
        return
    try:
        await redis.setex(
            key, ttl, json.dumps(value, ensure_ascii=False, default=str)
        )
    except Exception:
        logger.warning("写入缓存失败 cache=%s key=%s", cache_name, key, exc_info=True)


async def invalidate_prefixes(
    redis: Any | None, prefixes: tuple[str, ...] = (PREFIX_RETRIEVAL, PREFIX_ANSWER)
) -> int:
    """按前缀删除缓存；用于文档入库、更新或删除后避免陈旧结果。"""
    if redis is None:
        return 0
    deleted = 0
    try:
        for prefix in prefixes:
            batch: list[str] = []
            async for key in redis.scan_iter(match=f"{prefix}*", count=200):
                batch.append(key)
                if len(batch) >= 200:
                    deleted += int(await redis.delete(*batch))
                    batch.clear()
            if batch:
                deleted += int(await redis.delete(*batch))
    except Exception:
        logger.warning(
            "缓存失效失败 prefixes=%s（app/services/cache.py:invalidate_prefixes）",
            prefixes,
            exc_info=True,
        )
        return deleted
    logger.info("已失效知识库相关缓存 count=%d prefixes=%s", deleted, prefixes)
    return deleted
