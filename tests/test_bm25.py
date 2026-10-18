"""BM25 分词、索引生命周期与打分测试。"""

from __future__ import annotations

import asyncio

import pytest

from app.models.chunk import Chunk
from app.models.document import Document
from app.models.knowledge_base import KnowledgeBase
from app.services import bm25 as bm25_module
from app.services.bm25 import BM25Service, tokenize


def _row(uid: str, content: str, *, kb_id: int = 1, title: str = "", section: str = "") -> dict:
    return {
        "chunk_uid": uid,
        "doc_id": 1,
        "kb_id": kb_id,
        "chunk_index": 0,
        "parent_uid": None,
        "content": content,
        "title": title,
        "section": section,
        "page": None,
    }


# --------------------------------------------------------------------------- #
# 分词
# --------------------------------------------------------------------------- #
def test_tokenize_cjk_unigram_and_bigram() -> None:
    tokens = tokenize("七天无理由")
    assert "七" in tokens
    assert "天" in tokens
    assert "七天" in tokens
    assert "天无" in tokens
    assert "由" in tokens


def test_tokenize_normalizes_width_case_and_punctuation() -> None:
    tokens = tokenize("ＡＢＣ-123，包邮！")
    assert "abc" in tokens
    assert "123" in tokens
    assert "包" in tokens
    assert "包邮" in tokens
    assert "，" not in tokens


def test_tokenize_empty_and_symbol_only() -> None:
    assert tokenize("") == []
    assert tokenize("，。！？--") == []


# --------------------------------------------------------------------------- #
# 打分
# --------------------------------------------------------------------------- #
async def test_title_match_outranks_content_match(monkeypatch) -> None:
    monkeypatch.setattr(bm25_module.Config, "bm25_index_ttl_seconds", 120)
    service = BM25Service()

    async def fake_load(kb_id: int) -> list[dict]:
        return [
            _row("content", "包邮"),
            _row("title", "", title="包邮"),
        ]

    monkeypatch.setattr(service, "_load_rows", fake_load)
    hits = await service.search("包邮", [1], top_k=5)

    assert [hit["chunk_uid"] for hit in hits] == ["title", "content"]
    assert hits[0]["retrieval_source"] == "bm25"
    assert hits[0]["vector_score"] == 0.0


async def test_rare_term_scores_higher_than_common_term(monkeypatch) -> None:
    service = BM25Service()

    async def fake_load(kb_id: int) -> list[dict]:
        return [
            _row("a", "稀有 常见"),
            _row("b", "常见"),
            _row("c", "常见"),
        ]

    monkeypatch.setattr(service, "_load_rows", fake_load)
    rare = await service.search("稀有", [1], top_k=1)
    common = await service.search("常见", [1], top_k=1)

    assert rare[0]["bm25_score"] > common[0]["bm25_score"]


async def test_search_without_overlap_returns_empty(monkeypatch) -> None:
    service = BM25Service()

    async def fake_load(kb_id: int) -> list[dict]:
        return [_row("a", "退换货规则")]

    monkeypatch.setattr(service, "_load_rows", fake_load)
    assert await service.search("发票", [1]) == []


async def test_empty_corpus_and_empty_query(monkeypatch) -> None:
    service = BM25Service()

    async def fake_load(kb_id: int) -> list[dict]:
        return []

    monkeypatch.setattr(service, "_load_rows", fake_load)
    assert await service.search("包邮", [1]) == []
    assert await service.search("", [1]) == []
    assert await service.search("包邮", []) == []


async def test_multiple_kbs_are_scored_and_isolated(monkeypatch) -> None:
    service = BM25Service()

    async def fake_load(kb_id: int) -> list[dict]:
        return [_row(f"kb-{kb_id}", "包邮" if kb_id == 1 else "退换货", kb_id=kb_id)]

    monkeypatch.setattr(service, "_load_rows", fake_load)
    hits = await service.search("包邮", [1, 2], top_k=10)

    assert [hit["chunk_uid"] for hit in hits] == ["kb-1"]
    service.invalidate([1])
    assert 1 not in service._indexes
    assert 2 in service._indexes


# --------------------------------------------------------------------------- #
# 索引生命周期
# --------------------------------------------------------------------------- #
async def test_index_ttl_rebuilds_after_invalidate(monkeypatch) -> None:
    monkeypatch.setattr(bm25_module.Config, "bm25_index_ttl_seconds", 120)
    service = BM25Service()
    calls = {"count": 0}

    async def fake_load(kb_id: int) -> list[dict]:
        calls["count"] += 1
        return [_row("a", "包邮")]

    monkeypatch.setattr(service, "_load_rows", fake_load)
    await service.search("包邮", [1])
    await service.search("包邮", [1])
    assert calls["count"] == 1

    service.invalidate([1])
    await service.search("包邮", [1])
    assert calls["count"] == 2


async def test_concurrent_first_search_builds_index_once(monkeypatch) -> None:
    service = BM25Service()
    calls = {"count": 0}

    async def fake_load(kb_id: int) -> list[dict]:
        calls["count"] += 1
        await asyncio.sleep(0.02)
        return [_row("a", "包邮")]

    monkeypatch.setattr(service, "_load_rows", fake_load)
    await asyncio.gather(
        service.search("包邮", [1]),
        service.search("包邮", [1]),
        service.search("包邮", [1]),
    )
    assert calls["count"] == 1


async def test_index_expires_by_ttl(monkeypatch) -> None:
    monkeypatch.setattr(bm25_module.Config, "bm25_index_ttl_seconds", 0)
    service = BM25Service()
    calls = {"count": 0}

    async def fake_load(kb_id: int) -> list[dict]:
        calls["count"] += 1
        return [_row("a", "包邮")]

    monkeypatch.setattr(service, "_load_rows", fake_load)
    await service.search("包邮", [1])
    await service.search("包邮", [1])
    assert calls["count"] == 2


# --------------------------------------------------------------------------- #
# 数据库过滤
# --------------------------------------------------------------------------- #
async def test_load_rows_excludes_non_current_documents(db_maker, monkeypatch) -> None:
    async with db_maker() as db:
        kb = KnowledgeBase(owner_id=1, name="BM25 测试库")
        db.add(kb)
        await db.flush()

        current = Document(
            kb_id=kb.id,
            original_name="当前.md",
            stored_name="current.md",
            file_path="/tmp/current.md",
            file_type="md",
            status="completed",
            is_current=True,
        )
        stale = Document(
            kb_id=kb.id,
            original_name="旧版.md",
            stored_name="stale.md",
            file_path="/tmp/stale.md",
            file_type="md",
            status="completed",
            is_current=False,
        )
        db.add_all([current, stale])
        await db.flush()
        db.add_all(
            [
                Chunk(
                    uid="c-current",
                    doc_id=current.id,
                    kb_id=kb.id,
                    chunk_index=0,
                    content="当前文档内容",
                    char_count=7,
                ),
                Chunk(
                    uid="c-stale",
                    doc_id=stale.id,
                    kb_id=kb.id,
                    chunk_index=0,
                    content="旧文档内容",
                    char_count=5,
                ),
            ]
        )
        await db.commit()
        kb_id = kb.id

    monkeypatch.setattr(bm25_module, "get_session_maker", lambda: db_maker)
    service = BM25Service()
    rows = await service._load_rows(kb_id)

    assert [row["chunk_uid"] for row in rows] == ["c-current"]


async def test_coverage_reports_query_term_ratio(monkeypatch) -> None:
    service = BM25Service()

    async def fake_load(kb_id: int) -> list[dict]:
        return [_row("a", "包邮")]

    monkeypatch.setattr(service, "_load_rows", fake_load)
    hits = await service.search("包邮政策", [1], top_k=1)

    assert hits
    assert 0.0 < hits[0]["bm25_coverage"] < 1.0


@pytest.mark.parametrize("query", ["七天无理由", "退货", "发票"])
async def test_search_is_deterministic(monkeypatch, query: str) -> None:
    service = BM25Service()

    async def fake_load(kb_id: int) -> list[dict]:
        return [_row("b", query), _row("a", query)]

    monkeypatch.setattr(service, "_load_rows", fake_load)
    first = await service.search(query, [1], top_k=5)
    second = await service.search(query, [1], top_k=5)
    assert [hit["chunk_uid"] for hit in first] == [hit["chunk_uid"] for hit in second]
