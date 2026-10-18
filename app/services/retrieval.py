"""多知识库检索编排：多查询召回 → RRF 融合 → 重排序 → 父块扩展。

检索层不调用 LLM，也不知道 HyDE/子查询的语义，只接收 ``queries`` 列表并
对每个 ``(mode, query)`` 组合各召回一路，再用 RRF 融合；BM25 作为额外的
词法召回路参与融合，或在向量路全部失败时兜底。
"""

from __future__ import annotations

import asyncio
import time
from typing import Any, Sequence

from sqlalchemy import select

from app.core.logger import get_logger
from app.db.session import get_session_maker
from app.models.chunk import Chunk
from app.models.document import Document
from app.models.parent_chunk import ParentChunk
from app.services.bm25 import bm25_service
from app.services.cache import get_json, retrieval_key, set_json
from app.services.dedup import content_hash, normalize_text, simhash, text_similarity
from app.services.embedding import embedding_service
from app.services.fusion import rrf_fuse
from app.services.metrics import inc, observe
from app.services.reranker import reranker_service
from app.services.vector_store import vector_store
from config import Config

logger = get_logger(__name__)

_MIN_DEDUP_CHARS = 20
_EMPTY_RESULT_CACHE_TTL = 30


def dedupe_hits(hits: list[dict[str, Any]], threshold: float | None = None) -> list[dict[str, Any]]:
    """保留顺序，按父块、归一化正文和 SimHash 去除近重复命中。"""
    if len(hits) <= 1:
        return hits

    threshold = Config.retrieval_dedup_similarity if threshold is None else threshold
    max_distance = max(1, int(round(64 * (1 - threshold))))
    kept: list[dict[str, Any]] = []
    seen_parents: set[str] = set()
    seen_hashes: set[str] = set()
    kept_simhashes: list[tuple[int, dict[str, Any]]] = []

    for hit in hits:
        parent_uid = str(hit.get("parent_uid") or "")
        if parent_uid:
            if parent_uid in seen_parents:
                continue
            seen_parents.add(parent_uid)

        content = str(hit.get("content") or "")
        normalized = normalize_text(content)
        if len(normalized) < _MIN_DEDUP_CHARS:
            kept.append(hit)
            continue

        normalized_hash = content_hash(content)
        if normalized_hash in seen_hashes:
            continue

        current_simhash = simhash(content)
        duplicate = False
        for candidate_simhash, candidate in kept_simhashes:
            if (current_simhash ^ candidate_simhash).bit_count() > max_distance:
                continue
            if (
                text_similarity(
                    content,
                    str(candidate.get("content") or ""),
                    left_simhash=f"{current_simhash:016x}",
                    right_simhash=f"{candidate_simhash:016x}",
                )
                >= threshold
            ):
                duplicate = True
                break
        if duplicate:
            continue

        kept.append(hit)
        seen_hashes.add(normalized_hash)
        kept_simhashes.append((current_simhash, hit))
    return kept


async def enrich_hits(hits: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """过滤非 current / 已删除分块，并用父分块正文替换子分块正文。"""
    if not hits:
        return []

    doc_ids = {
        int(hit["doc_id"])
        for hit in hits
        if hit.get("doc_id") is not None
    }
    chunk_uids = {
        str(hit.get("chunk_uid"))
        for hit in hits
        if hit.get("chunk_uid")
    }
    if not doc_ids or not chunk_uids:
        return []

    try:
        async with get_session_maker()() as db:
            doc_rows = await db.execute(
                select(Document.id, Document.is_current, Document.version).where(
                    Document.id.in_(doc_ids)
                )
            )
            current_versions = {
                int(row[0]): int(row[2] or 1)
                for row in doc_rows
                if bool(row[1])
            }
            if not current_versions:
                return []

            chunk_rows = await db.execute(
                select(Chunk.uid, Chunk.parent_uid).where(Chunk.uid.in_(chunk_uids))
            )
            parent_by_chunk = {
                str(row[0]): row[1] for row in chunk_rows if row[0] is not None
            }
            parent_uids = {
                str(parent_uid)
                for parent_uid in parent_by_chunk.values()
                if parent_uid
            }
            parent_map: dict[str, ParentChunk] = {}
            if parent_uids:
                parent_rows = await db.scalars(
                    select(ParentChunk).where(ParentChunk.uid.in_(parent_uids))
                )
                parent_map = {parent.uid: parent for parent in parent_rows}
    except Exception:
        logger.exception("检索结果父块扩展失败，返回子分块原文")
        return hits

    enriched: list[dict[str, Any]] = []
    for hit in hits:
        doc_id = int(hit.get("doc_id") or 0)
        if doc_id not in current_versions:
            continue
        chunk_uid = str(hit.get("chunk_uid") or "")
        # BM25 索引有 TTL，可能仍持有已删除分块；DB 可达时必须丢弃
        if chunk_uid and chunk_uid not in parent_by_chunk:
            continue
        item = dict(hit)
        item["doc_version"] = current_versions[doc_id]
        item["child_content"] = hit.get("content")
        parent_uid = parent_by_chunk.get(chunk_uid)
        if parent_uid:
            parent = parent_map.get(str(parent_uid))
            if parent is not None:
                item["parent_uid"] = parent.uid
                item["content"] = parent.content
                item["title"] = parent.title or item.get("title")
                item["section"] = parent.section or item.get("section")
                if parent.page is not None:
                    item["page"] = parent.page
        enriched.append(item)

    return dedupe_hits(enriched)


def _normalize_queries(query: str, queries: Sequence[str] | None) -> tuple[str, ...]:
    """去空、去重并保证原问题排第一，避免重复召回同一路。"""
    ordered: list[str] = [query]
    for raw in queries or ():
        text = str(raw or "").strip()
        if text and text not in ordered:
            ordered.append(text)
    return tuple(ordered)


def _effective_bm25_mode() -> str:
    if not Config.bm25_enabled:
        return "off"
    mode = str(Config.bm25_mode or "off").lower()
    return mode if mode in {"off", "fallback", "fuse"} else "off"


def _bm25_only(hit: dict[str, Any]) -> bool:
    if hit.get("bm25_score") is None:
        return False
    return float(hit.get("vector_score") or 0.0) <= 0.0


def _filter_bm25_coverage(hits: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """重排序关闭时，BM25-only 命中按查询词覆盖度过滤。"""
    if Config.rerank_enabled:
        return hits
    threshold = float(Config.bm25_min_query_coverage)
    return [
        hit
        for hit in hits
        if not _bm25_only(hit)
        or float(hit.get("bm25_coverage") or 0.0) >= threshold
    ]


class RetrievalService:
    """按知识库的检索模式分组召回，再统一重排序。"""

    async def retrieve(
        self,
        query: str,
        kb_modes: dict[str, list[int]],
        final_k: int | None = None,
        recall_k: int | None = None,
        redis: Any | None = None,
        *,
        strategy: str = "direct",
        queries: Sequence[str] | None = None,
        rerank_query: str | None = None,
    ) -> list[dict[str, Any]]:
        """kb_modes 形如 {"hybrid": [1, 2], "dense": [3]}。

        ``queries`` 是策略层生成的额外召回查询；``rerank_query`` 始终用原始
        改写后问题，避免 HyDE 假设答案或简化问题丢掉金额、时效等约束。
        """
        query = (query or "").strip()
        if not query or not kb_modes:
            return []

        started = time.perf_counter()
        strategy_name = str(strategy or "direct")
        final_limit = final_k or Config.retrieval_k
        recall_limit = recall_k or Config.candidate_k
        query_texts = _normalize_queries(query, queries)
        bm25_mode = _effective_bm25_mode()

        cache_key = retrieval_key(
            query,
            kb_modes,
            final_limit,
            recall_limit,
            queries=query_texts,
            strategy=strategy_name,
            bm25_mode=bm25_mode,
        )
        cached = await get_json(redis, cache_key, "retrieval")
        if isinstance(cached, list):
            logger.info("检索命中缓存 query=%s hits=%d", query[:50], len(cached))
            return cached

        kb_ids = sorted(
            {int(kb_id) for ids in kb_modes.values() for kb_id in ids}
        )
        embedding_error = False
        embeddings: dict[str, tuple[Any, Any]] = {}
        for text in query_texts:
            try:
                embeddings[text] = await embedding_service.embed_query(
                    text, redis=redis
                )
            except asyncio.CancelledError:
                raise
            except Exception:
                embedding_error = True
                logger.exception(
                    "查询向量化失败 query=%s（app/services/retrieval.py:RetrievalService.retrieve）",
                    text[:60],
                )

        vector_calls: list[tuple[str, list[int], str]] = []
        for text in query_texts:
            if text not in embeddings:
                continue
            for mode, ids in kb_modes.items():
                normalized_ids = [int(kb_id) for kb_id in ids]
                if normalized_ids:
                    vector_calls.append((mode, normalized_ids, text))

        vector_exception = False
        vector_lists: list[list[dict[str, Any]]] = []
        if vector_calls:
            results = await asyncio.gather(
                *(
                    vector_store.search(
                        embeddings[text][0],
                        embeddings[text][1] if mode == "hybrid" else None,
                        ids,
                        mode=mode,
                        top_k=recall_limit,
                    )
                    for mode, ids, text in vector_calls
                ),
                return_exceptions=True,
            )
            for (mode, ids, text), result in zip(
                vector_calls, results, strict=True
            ):
                if isinstance(result, asyncio.CancelledError):
                    raise result
                if isinstance(result, BaseException):
                    vector_exception = True
                    logger.error(
                        "向量检索失败 mode=%s query=%s kb_ids=%s"
                        "（app/services/retrieval.py:RetrievalService.retrieve）",
                        mode,
                        text[:60],
                        ids,
                        exc_info=(type(result), result, result.__traceback__),
                    )
                    continue
                vector_lists.append(list(result))

        bm25_hits: list[dict[str, Any]] = []
        bm25_error: BaseException | None = None
        if bm25_mode != "off" and kb_ids:
            # 额外查询只走向量；词法召回始终使用原始改写后问题
            try:
                bm25_hits = await bm25_service.search(
                    query, kb_ids, Config.bm25_top_k
                )
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                bm25_error = exc
                logger.exception(
                    "BM25 检索失败 kb_ids=%s（app/services/retrieval.py:RetrievalService.retrieve）",
                    kb_ids,
                )

        if bm25_error is not None and not any(vector_lists) and not embedding_error:
            raise bm25_error

        routes: list[tuple[list[dict[str, Any]], float]] = []
        non_empty_vectors = [hits for hits in vector_lists if hits]
        if bm25_mode == "fuse":
            routes.extend((hits, 1.0) for hits in non_empty_vectors)
            if bm25_hits:
                routes.append((bm25_hits, float(Config.bm25_rrf_weight)))
        elif bm25_mode == "fallback":
            if non_empty_vectors:
                routes.extend((hits, 1.0) for hits in non_empty_vectors)
            elif bm25_hits:
                reason = (
                    "embedding_error"
                    if embedding_error
                    else "vector_error"
                    if vector_exception
                    else "empty"
                )
                inc("asktofit_bm25_fallback_total", {"reason": reason})
                routes.append((bm25_hits, 1.0))
        else:
            routes.extend((hits, 1.0) for hits in non_empty_vectors)

        if not routes:
            if bm25_error is not None and (
                embedding_error or vector_exception
            ):
                raise bm25_error
            # 异常造成的空结果不缓存，避免一次抖动压制整个 TTL
            if not embedding_error and not vector_exception:
                await set_json(
                    redis,
                    cache_key,
                    [],
                    _EMPTY_RESULT_CACHE_TTL,
                    "retrieval",
                )
            observe(
                "asktofit_retrieval_duration_seconds",
                time.perf_counter() - started,
                {"strategy": strategy_name},
            )
            return []

        candidates = rrf_fuse(
            [hits for hits, _ in routes],
            weights=[weight for _, weight in routes],
        )
        candidates = _filter_bm25_coverage(candidates)
        if not candidates:
            if not embedding_error and not vector_exception and bm25_error is None:
                await set_json(
                    redis,
                    cache_key,
                    [],
                    _EMPTY_RESULT_CACHE_TTL,
                    "retrieval",
                )
            observe(
                "asktofit_retrieval_duration_seconds",
                time.perf_counter() - started,
                {"strategy": strategy_name},
            )
            return []

        logger.info(
            "检索召回 query=%s strategy=%s queries=%d modes=%s bm25=%s candidates=%d",
            query[:50],
            strategy_name,
            len(query_texts),
            {mode: len(ids) for mode, ids in kb_modes.items()},
            bm25_mode,
            len(candidates),
        )
        inc(
            "asktofit_retrieval_candidates_total",
            {"strategy": strategy_name},
            value=float(len(candidates)),
        )
        reranked = await reranker_service.rerank(
            rerank_query or query,
            candidates[: Config.rerank_candidate_k],
            final_limit,
        )
        results = dedupe_hits(reranked)
        degraded = any(hit.get("rerank_failed") for hit in results)
        if degraded:
            logger.warning(
                "本轮重排序失败，按配置 rerank_failure_mode=%s 交给上层处理",
                Config.rerank_failure_mode,
            )
        ttl = (
            Config.retrieval_degraded_cache_ttl
            if degraded
            else Config.retrieval_cache_ttl
        )
        await set_json(redis, cache_key, results, ttl, "retrieval")
        observe(
            "asktofit_retrieval_duration_seconds",
            time.perf_counter() - started,
            {"strategy": strategy_name},
        )
        return results

    async def enrich_hits(self, hits: list[dict[str, Any]]) -> list[dict[str, Any]]:
        return await enrich_hits(hits)


retrieval_service = RetrievalService()
