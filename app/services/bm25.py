"""纯 Python Okapi BM25 词法召回。

不依赖 rank-bm25 / jieba：中文用「单字 + 相邻双字」切分，英文/数字按整词
切分。每个知识库维护一份进程内索引，带 TTL 与 single-flight，知识库写入
后由 ``search_index.invalidate_search_caches`` 主动失效。
"""

from __future__ import annotations

import asyncio
import math
import re
import time
import unicodedata
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
    term_freqs: list[dict[str, float]]
    lengths: list[float]
    document_freq: dict[str, int]
    avgdl: float
    built_at: float
    hits: int = field(default=0)


class BM25Service:
    """按知识库构建内存索引并执行 BM25 打分。"""

    def __init__(self) -> None:
        self._indexes: dict[int, _Index] = {}
        self._locks: dict[int, asyncio.Lock] = {}

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
        term_freqs: list[dict[str, float]] = []
        lengths: list[float] = []
        document_freq: dict[str, int] = {}
        for row in rows:
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
            term_freqs.append(frequencies)
            lengths.append(sum(frequencies.values()))
            for token in frequencies:
                document_freq[token] = document_freq.get(token, 0) + 1

        avgdl = (sum(lengths) / len(lengths)) if lengths else 0.0
        return _Index(
            rows=rows,
            term_freqs=term_freqs,
            lengths=lengths,
            document_freq=document_freq,
            avgdl=avgdl,
            built_at=time.monotonic(),
        )

    async def _ensure_index(self, kb_id: int) -> _Index:
        ttl = max(0, int(Config.bm25_index_ttl_seconds))
        current = self._indexes.get(kb_id)
        if current is not None and time.monotonic() - current.built_at <= ttl:
            return current

        lock = self._locks.setdefault(kb_id, asyncio.Lock())
        async with lock:
            current = self._indexes.get(kb_id)
            if current is not None and time.monotonic() - current.built_at <= ttl:
                return current
            started = time.perf_counter()
            rows = await self._load_rows(kb_id)
            index = await asyncio.to_thread(self._build_index, rows)
            self._indexes[kb_id] = index
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
    def _score_document(
        query_terms: list[str],
        frequencies: dict[str, float],
        length: float,
        index: _Index,
    ) -> float:
        if not query_terms or index.avgdl <= 0:
            return 0.0
        total = len(index.rows)
        k1 = float(Config.bm25_k1)
        b = float(Config.bm25_b)
        score = 0.0
        for term in query_terms:
            freq = frequencies.get(term, 0.0)
            if freq <= 0:
                continue
            df = index.document_freq.get(term, 0)
            if df <= 0:
                continue
            idf = math.log(1.0 + (total - df + 0.5) / (df + 0.5))
            denominator = freq + k1 * (1.0 - b + b * length / index.avgdl)
            score += idf * freq * (k1 + 1.0) / denominator
        return score

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
            indexes = await asyncio.gather(*(self._ensure_index(kb_id) for kb_id in ids))
        except Exception:
            inc("asktofit_bm25_searches_total", {"outcome": "error"})
            logger.exception(
                "BM25 索引构建失败 kb_ids=%s（app/services/bm25.py:BM25Service.search）",
                ids,
            )
            raise

        scored: list[dict[str, Any]] = []
        for index in indexes:
            for row, frequencies, length in zip(
                index.rows, index.term_freqs, index.lengths, strict=True
            ):
                score = self._score_document(query_terms, frequencies, length, index)
                if score <= 0:
                    continue
                matched = sum(
                    1 for term in query_terms if frequencies.get(term, 0.0) > 0
                )
                hit = dict(row)
                hit["score"] = float(score)
                hit["bm25_score"] = float(score)
                hit["bm25_coverage"] = matched / len(query_terms)
                hit["vector_score"] = 0.0
                hit["retrieval_source"] = "bm25"
                scored.append(hit)

        scored.sort(key=lambda item: (-float(item["bm25_score"]), str(item["chunk_uid"])))
        results = scored[:limit]
        inc("asktofit_bm25_searches_total", {"outcome": "hit" if results else "empty"})
        if results:
            inc("asktofit_bm25_hits_total", value=float(len(results)))
        return results

    def invalidate(self, kb_ids: list[int] | None = None) -> None:
        if kb_ids is None:
            self._indexes.clear()
            return
        for kb_id in kb_ids:
            self._indexes.pop(int(kb_id), None)


bm25_service = BM25Service()
