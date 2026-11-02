"""纯 Python Okapi BM25 词法召回。

不依赖 rank-bm25 / jieba：中文用「单字 + 相邻双字」切分，英文/数字按整词
切分。每个知识库维护一份进程内倒排索引，带 TTL、single-flight 与容量上限；
知识库写入后由 ``search_index.invalidate_search_caches`` 主动失效。

性能要点（来源：app/services/bm25.py:BM25Service.search）：
- 倒排索引只对包含查询词的文档打分，不再扫描整个知识库；
- 打分与 Top-K 选择在 ``asyncio.to_thread`` 中执行，不阻塞事件循环；
- 用 ``heapq.nsmallest`` 维护有界堆，避免对全部命中做全量排序；
- 信号量限制同时进行的查询打分，防止线程池被 BM25 占满。
"""

from __future__ import annotations

import asyncio
import heapq
import math
import re
import time
import unicodedata
import weakref
from collections import OrderedDict
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy import select

from app.core.logger import get_logger
from app.db.session import get_session_maker
from app.models.chunk import Chunk
from app.models.document import Document
from app.services.metrics import inc, observe
from config import Config

logger = get_logger(__name__)

_CJK_RUN = re.compile(r"[㐀-䶿一-鿿]+")
_ASCII_WORD = re.compile(r"[a-z0-9]+")


def tokenize(text: str) -> list[str]:
    """NFKC + 小写；CJK 连续段输出 unigram + bigram，ASCII/数字输出整词。"""
    normalized = unicodedata.normalize("NFKC", text or "").lower()
    tokens: list[str] = []
    for match in _CJK_RUN.finditer(normalized):
        segment = match.group()
        tokens.extend(segment)
        tokens.extend(segment[index : index + 2] for index in range(len(segment) - 1))
    tokens.extend(_ASCII_WORD.findall(normalized))
    return tokens


@dataclass
class _Index:
    rows: list[dict[str, Any]]
    # token -> [(文档下标, 加权词频), ...]
    postings: dict[str, list[tuple[int, float]]]
    lengths: list[float]
    document_freq: dict[str, int]
    avgdl: float
    built_at: float
    chunks: int = 0
    hits: int = field(default=0)


class BM25Service:
    """按知识库构建内存倒排索引并执行 BM25 打分。"""

    def __init__(self) -> None:
        # OrderedDict 用作 LRU：最近使用的索引移到末尾，超容量时从头部淘汰
        self._indexes: "OrderedDict[int, _Index]" = OrderedDict()
        self._locks: dict[int, asyncio.Lock] = {}
        self._semaphores: "weakref.WeakKeyDictionary[asyncio.AbstractEventLoop, asyncio.Semaphore]" = (
            weakref.WeakKeyDictionary()
        )

    # ------------------------------------------------------------------ #
    # 索引
    # ------------------------------------------------------------------ #
    async def _load_rows(self, kb_id: int) -> list[dict[str, Any]]:
        limit = max(1, int(Config.bm25_max_corpus_chunks))
        async with get_session_maker()() as db:
            result = await db.execute(
                select(
                    Chunk.uid,
                    Chunk.doc_id,
                    Chunk.kb_id,
                    Chunk.chunk_index,
                    Chunk.parent_uid,
                    Chunk.content,
                    Chunk.title,
                    Chunk.section,
                    Chunk.page,
                )
                .join(Document, Chunk.doc_id == Document.id)
                .where(Chunk.kb_id == int(kb_id), Document.is_current.is_(True))
                .order_by(Chunk.id.asc())
                .limit(limit)
            )
            return [
                {
                    "chunk_uid": str(row[0]),
                    "doc_id": int(row[1]),
                    "kb_id": int(row[2]),
                    "chunk_index": int(row[3]),
                    "parent_uid": row[4],
                    "content": row[5] or "",
                    "title": row[6] or "",
                    "section": row[7] or "",
                    "page": row[8],
                }
                for row in result
            ]

    @staticmethod
    def _build_index(rows: list[dict[str, Any]]) -> _Index:
        postings: dict[str, list[tuple[int, float]]] = {}
        lengths: list[float] = []
        document_freq: dict[str, int] = {}
        for doc_index, row in enumerate(rows):
            frequencies: dict[str, float] = {}
            fields = (
                (row.get("content"), float(Config.bm25_content_weight)),
                (row.get("title"), float(Config.bm25_title_weight)),
                (row.get("section"), float(Config.bm25_section_weight)),
            )
            for text, weight in fields:
                if not text or weight <= 0:
                    continue
                for token in tokenize(str(text)):
                    frequencies[token] = frequencies.get(token, 0.0) + weight
            lengths.append(sum(frequencies.values()))
            for token, frequency in frequencies.items():
                postings.setdefault(token, []).append((doc_index, frequency))
                document_freq[token] = document_freq.get(token, 0) + 1

        avgdl = (sum(lengths) / len(lengths)) if lengths else 0.0
        return _Index(
            rows=rows,
            postings=postings,
            lengths=lengths,
            document_freq=document_freq,
            avgdl=avgdl,
            built_at=time.monotonic(),
            chunks=len(rows),
        )

    def _evict_if_needed(self, protected_kb_id: int | None = None) -> None:
        max_kbs = max(1, int(Config.bm25_max_indexed_kbs))
        max_chunks = max(1, int(Config.bm25_max_total_chunks))
        total_chunks = sum(index.chunks for index in self._indexes.values())
        while self._indexes and (
            len(self._indexes) > max_kbs or total_chunks > max_chunks
        ):
            victim = next(
                (
                    kb_id
                    for kb_id in self._indexes
                    if kb_id != protected_kb_id
                ),
                None,
            )
            if victim is None:
                break
            evicted = self._indexes.pop(victim)
            total_chunks -= evicted.chunks
            self._locks.pop(victim, None)
            logger.info(
                "BM25 索引容量淘汰 kb_id=%s chunks=%d indexed_kbs=%d",
                victim,
                evicted.chunks,
                len(self._indexes),
            )

    async def _ensure_index(self, kb_id: int) -> _Index:
        ttl = max(0, int(Config.bm25_index_ttl_seconds))
        current = self._indexes.get(kb_id)
        if current is not None and time.monotonic() - current.built_at <= ttl:
            self._indexes.move_to_end(kb_id)
            return current

        lock = self._locks.setdefault(kb_id, asyncio.Lock())
        async with lock:
            current = self._indexes.get(kb_id)
            if current is not None and time.monotonic() - current.built_at <= ttl:
                self._indexes.move_to_end(kb_id)
                return current
            started = time.perf_counter()
            rows = await self._load_rows(kb_id)
            index = await asyncio.to_thread(self._build_index, rows)
            self._indexes[kb_id] = index
            self._indexes.move_to_end(kb_id)
            self._evict_if_needed(protected_kb_id=kb_id)
            elapsed = time.perf_counter() - started
            observe("asktofit_bm25_index_build_seconds", elapsed)
            logger.info(
                "BM25 索引构建完成 kb_id=%s chunks=%d elapsed=%.3fs",
                kb_id,
                len(rows),
                elapsed,
            )
            return index

    # ------------------------------------------------------------------ #
    # 检索
    # ------------------------------------------------------------------ #
    @staticmethod
    def _score_index(
        query_terms: list[str], index: _Index, limit: int
    ) -> list[dict[str, Any]]:
        """倒排打分并用有界堆选出 Top-K（分数降序、UID 升序）。"""
        total = len(index.rows)
        if not query_terms or total == 0 or index.avgdl <= 0:
            return []

        k1 = float(Config.bm25_k1)
        b = float(Config.bm25_b)
        scores: dict[int, float] = {}
        matched: dict[int, int] = {}
        for term in query_terms:
            term_postings = index.postings.get(term)
            if not term_postings:
                continue
            df = index.document_freq.get(term, 0)
            if df <= 0:
                continue
            idf = math.log(1.0 + (total - df + 0.5) / (df + 0.5))
            for doc_index, frequency in term_postings:
                length = index.lengths[doc_index]
                denominator = frequency + k1 * (
                    1.0 - b + b * length / index.avgdl
                )
                scores[doc_index] = (
                    scores.get(doc_index, 0.0)
                    + idf * frequency * (k1 + 1.0) / denominator
                )
                matched[doc_index] = matched.get(doc_index, 0) + 1

        if not scores:
            return []

        term_count = len(query_terms)
        top = heapq.nsmallest(
            limit,
            scores.items(),
            key=lambda item: (
                -item[1],
                str(index.rows[item[0]]["chunk_uid"]),
            ),
        )
        results: list[dict[str, Any]] = []
        for doc_index, score in top:
            hit = dict(index.rows[doc_index])
            hit["score"] = float(score)
            hit["bm25_score"] = float(score)
            hit["bm25_coverage"] = matched.get(doc_index, 0) / term_count
            hit["vector_score"] = 0.0
            hit["retrieval_source"] = "bm25"
            results.append(hit)
        return results

    def _query_semaphore(self) -> asyncio.Semaphore:
        loop = asyncio.get_running_loop()
        semaphore = self._semaphores.get(loop)
        if semaphore is None:
            semaphore = asyncio.Semaphore(
                max(1, int(Config.bm25_max_concurrent_searches))
            )
            self._semaphores[loop] = semaphore
        return semaphore

    async def search(
        self, query: str, kb_ids: list[int], top_k: int | None = None
    ) -> list[dict[str, Any]]:
        """返回与向量命中字段兼容的 BM25 结果，按分数降序、UID 升序。"""
        text = (query or "").strip()
        ids = sorted({int(kb_id) for kb_id in kb_ids})
        if not text or not ids:
            return []

        # 查询 token 去重，避免重复词重复计分
        query_terms = list(dict.fromkeys(tokenize(text)))
        if not query_terms:
            return []
        limit = max(1, int(top_k or Config.bm25_top_k))

        try:
            indexes = await asyncio.gather(
                *(self._ensure_index(kb_id) for kb_id in ids)
            )
        except Exception:
            inc("asktofit_bm25_searches_total", {"outcome": "error"})
            logger.exception(
                "BM25 索引构建失败 kb_ids=%s（app/services/bm25.py:BM25Service.search）",
                ids,
            )
            raise

        async with self._query_semaphore():
            scored_lists = await asyncio.gather(
                *(
                    asyncio.to_thread(self._score_index, query_terms, index, limit)
                    for index in indexes
                )
            )

        merged = [hit for hits in scored_lists for hit in hits]
        merged.sort(
            key=lambda item: (-float(item["bm25_score"]), str(item["chunk_uid"]))
        )
        results = merged[:limit]
        inc("asktofit_bm25_searches_total", {"outcome": "hit" if results else "empty"})
        if results:
            inc("asktofit_bm25_hits_total", value=float(len(results)))
        return results

    def invalidate(self, kb_ids: list[int] | None = None) -> None:
        if kb_ids is None:
            self._indexes.clear()
            self._locks.clear()
            return
        for kb_id in kb_ids:
            self._indexes.pop(int(kb_id), None)
            self._locks.pop(int(kb_id), None)


bm25_service = BM25Service()
