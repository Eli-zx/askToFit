"""Redis 缓存键、读写与知识库版本化失效策略。"""

from __future__ import annotations

import hashlib
import json
import random
from collections.abc import Iterable, Mapping
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

# 知识库版本号：任一 KB 的内容变化时只递增该 KB 的版本，
# 检索缓存键里带上版本号即可让旧结果自然失效，无需 scan 全库删除。
PREFIX_KB_VERSION = "asktofit:kbver:"
GLOBAL_VERSION_KEY = "asktofit:kbver:global"
KB_VERSION_TTL = 7 * 86400


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
    kb_modes: Mapping[str, Iterable[int]],
    final_k: int,
    recall_k: int,
    *,
    queries: tuple[str, ...] = (),
    strategy: str = "direct",
    bm25_mode: str = "off",
    rrf_k: int | None = None,
    config_fingerprint: dict[str, Any] | None = None,
    kb_versions: Mapping[int, int] | None = None,
    global_version: int = 0,
) -> str:
    normalized_modes = {
        mode: sorted(int(kb_id) for kb_id in kb_ids)
        for mode, kb_ids in sorted(kb_modes.items())
    }
    normalized_versions = {
        int(kb_id): int(version) for kb_id, version in sorted((kb_versions or {}).items())
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
            "kb_versions": normalized_versions,
            "global_version": int(global_version),
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


def kb_version_key(kb_id: int) -> str:
    return f"{PREFIX_KB_VERSION}{int(kb_id)}"


async def get_kb_versions(redis: Any | None, kb_ids: Iterable[int]) -> dict[int, int]:
    """批量读取 KB 版本号；缺失视为 0，Redis 异常时同样按 0 处理。"""
    ids = sorted({int(kb_id) for kb_id in kb_ids})
    if redis is None or not ids:
        return {}
    try:
        values = await redis.mget([kb_version_key(kb_id) for kb_id in ids])
    except Exception:
        logger.warning(
            "读取知识库版本失败 kb_ids=%s（app/services/cache.py:get_kb_versions）",
            ids,
            exc_info=True,
        )
        return {kb_id: 0 for kb_id in ids}
    return {
        kb_id: int(value) if value is not None else 0
        for kb_id, value in zip(ids, values, strict=True)
    }


async def get_global_version(redis: Any | None) -> int:
    if redis is None:
        return 0
    try:
        value = await redis.get(GLOBAL_VERSION_KEY)
        return int(value) if value is not None else 0
    except Exception:
        logger.warning(
            "读取全局版本失败（app/services/cache.py:get_global_version）", exc_info=True
        )
        return 0


async def bump_kb_version(redis: Any | None, kb_id: int | None = None) -> int | None:
    """递增 KB 版本号；kb_id 为空时递增全局版本。

    只递增版本，不做 scan/批量删除，避免一次知识变更清空所有用户缓存。
    """
    if redis is None:
        return None
    key = GLOBAL_VERSION_KEY if kb_id is None else kb_version_key(kb_id)
    try:
        version = int(await redis.incr(key))
        await redis.expire(key, KB_VERSION_TTL)
    except Exception:
        logger.warning(
            "递增知识库版本失败 key=%s（app/services/cache.py:bump_kb_version）",
            key,
            exc_info=True,
        )
        return None
    logger.info("知识库版本已递增 key=%s version=%d", key, version)
    return version


def _jittered_ttl(ttl: int) -> int:
    if Config.cache_ttl_jitter <= 0:
        return ttl
    factor = random.uniform(1.0 - Config.cache_ttl_jitter, 1.0 + Config.cache_ttl_jitter)
    return max(1, int(ttl * factor))


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
            key,
            _jittered_ttl(ttl),
            json.dumps(value, ensure_ascii=False, default=str),
        )
    except Exception:
        logger.warning("写入缓存失败 cache=%s key=%s", cache_name, key, exc_info=True)
