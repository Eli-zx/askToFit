"""多知识库检索编排测试。"""

import pytest

from app.models.chunk import Chunk
from app.models.document import Document
from app.models.knowledge_base import KnowledgeBase
from app.models.parent_chunk import ParentChunk
from app.services import retrieval as retrieval_module
from app.services.retrieval import RetrievalService, dedupe_hits, enrich_hits
from config import Config


@pytest.fixture(autouse=True)
def _disable_bm25_by_default(monkeypatch) -> None:
    """既有用例只验证向量路径；BM25 用例在用例内显式开启。"""
    monkeypatch.setattr(Config, "bm25_enabled", False)


class FakeVectorStore:
    def __init__(self, results_by_kb: dict[int, list[dict]]) -> None:
        self.results_by_kb = results_by_kb
        self.calls: list[dict] = []

    async def search(self, dense, sparse, kb_ids, mode="hybrid", top_k=None, candidate_k=None):
        self.calls.append(
            {
                "dense": dense,
                "sparse": sparse,
                "kb_ids": kb_ids,
                "mode": mode,
                "top_k": top_k,
            }
        )
        hits: list[dict] = []
        for kb_id in kb_ids:
            hits.extend(self.results_by_kb.get(kb_id, []))
        return hits


class FakeReranker:
    def __init__(self) -> None:
        self.calls: list[dict] = []

    async def rerank(self, query, hits, top_k):
        self.calls.append({"query": query, "hits": hits, "top_k": top_k})
        reranked = []
        for index, hit in enumerate(hits[:top_k]):
            item = dict(hit)
            item["rerank_score"] = 0.9 - index * 0.1
            item["score"] = item["rerank_score"]
            reranked.append(item)
        return reranked


def _hit(uid: str, score: float, content: str = "资料") -> dict:
    return {
        "chunk_uid": uid,
        "doc_id": 1,
        "kb_id": 1,
        "content": content,
        "title": "知识文档",
        "section": "章节",
        "page": 1,
        "score": score,
    }


async def test_retrieve_groups_modes_and_dedupes(monkeypatch) -> None:
    store = FakeVectorStore(
        {
            1: [_hit("uid-1", 0.6), _hit("uid-2", 0.5)],
            2: [_hit("uid-2", 0.8), _hit("uid-3", 0.4)],
        }
    )
    reranker = FakeReranker()

    async def fake_embed_query(text, redis=None):
        return [0.1] * 1024, {1: 0.5}

    monkeypatch.setattr(retrieval_module.embedding_service, "embed_query", fake_embed_query)
    monkeypatch.setattr(retrieval_module, "vector_store", store)
    monkeypatch.setattr(retrieval_module, "reranker_service", reranker)

    service = RetrievalService()
    hits = await service.retrieve(
        "满减怎么算", {"hybrid": [1], "dense": [2]}, final_k=2, recall_k=10
    )

    assert len(hits) == 2
    assert {hit["chunk_uid"] for hit in hits} == {"uid-1", "uid-2"}
    assert len(store.calls) == 2
    hybrid_call = next(call for call in store.calls if call["mode"] == "hybrid")
    dense_call = next(call for call in store.calls if call["mode"] == "dense")
    assert hybrid_call["sparse"] == {1: 0.5}
    assert dense_call["sparse"] is None
    assert hybrid_call["top_k"] == 10

    passed_hits = reranker.calls[0]["hits"]
    uid2 = next(hit for hit in passed_hits if hit["chunk_uid"] == "uid-2")
    # 跨模式分数不可比较，融合后 score 是 RRF 分，原始向量分保留在 vector_score
    assert uid2["vector_score"] == 0.8
    assert uid2["fused_score"] > 0
    assert uid2["retrieval_source"] == "fused"


async def test_retrieve_empty_inputs(monkeypatch) -> None:
    called = {"embed": False}

    async def fake_embed_query(text, redis=None):
        called["embed"] = True
        return [0.1] * 1024, {}

    monkeypatch.setattr(retrieval_module.embedding_service, "embed_query", fake_embed_query)
    service = RetrievalService()

    assert await service.retrieve("", {"hybrid": [1]}) == []
    assert await service.retrieve("问题", {}) == []
    assert called["embed"] is False


async def test_retrieve_returns_empty_when_no_recall(monkeypatch) -> None:
    store = FakeVectorStore({})
    reranker = FakeReranker()

    async def fake_embed_query(text, redis=None):
        return [0.1] * 1024, {1: 0.5}

    monkeypatch.setattr(retrieval_module.embedding_service, "embed_query", fake_embed_query)
    monkeypatch.setattr(retrieval_module, "vector_store", store)
    monkeypatch.setattr(retrieval_module, "reranker_service", reranker)

    service = RetrievalService()
    assert await service.retrieve("冷门问题", {"hybrid": [1]}) == []
    assert reranker.calls == []


# --------------------------------------------------------------------------- #
# 结果级去重与父块扩展
# --------------------------------------------------------------------------- #
def long_text(paragraphs: int = 10) -> str:
    return "\n".join(
        f"第{i}条：自签收之日起七天内，商品保持完好可申请无理由退换货。"
        f"退换货产生的运费按照责任方承担，质量问题由商家承担。"
        for i in range(1, paragraphs + 1)
    )


def test_dedupe_hits_merges_same_parent() -> None:
    hits = [
        {**_hit("uid-1", 0.9, long_text()), "parent_uid": "p-1"},
        {**_hit("uid-2", 0.8, long_text() + "\n补充说明。"), "parent_uid": "p-1"},
    ]
    kept = dedupe_hits(hits)
    assert [hit["chunk_uid"] for hit in kept] == ["uid-1"]


def test_dedupe_hits_removes_near_duplicate_content() -> None:
    base = long_text()
    near = base.replace("第1条：", "第一条：")
    hits = [
        _hit("uid-1", 0.9, base),
        _hit("uid-2", 0.8, near),
    ]
    kept = dedupe_hits(hits, threshold=0.95)
    assert [hit["chunk_uid"] for hit in kept] == ["uid-1"]


def test_dedupe_hits_keeps_different_content() -> None:
    hits = [
        _hit("uid-1", 0.9, long_text()),
        _hit("uid-2", 0.8, "会员积分可在结算时抵扣现金，每100积分抵扣1元。"),
    ]
    kept = dedupe_hits(hits)
    assert [hit["chunk_uid"] for hit in kept] == ["uid-1", "uid-2"]


def test_dedupe_hits_keeps_short_placeholder_content() -> None:
    hits = [_hit("uid-1", 0.9, "资料"), _hit("uid-2", 0.8, "资料")]
    kept = dedupe_hits(hits)
    assert [hit["chunk_uid"] for hit in kept] == ["uid-1", "uid-2"]


async def _seed_enrichment_rows(db_maker) -> tuple[int, int]:
    async with db_maker() as db:
        kb = KnowledgeBase(owner_id=1, name="检索测试库")
        db.add(kb)
        await db.flush()

        current = Document(
            kb_id=kb.id,
            original_name="当前版本.md",
            stored_name="current.md",
            file_path="/tmp/current.md",
            file_type="md",
            status="completed",
            version=3,
            is_current=True,
        )
        stale = Document(
            kb_id=kb.id,
            original_name="旧版本.md",
            stored_name="stale.md",
            file_path="/tmp/stale.md",
            file_type="md",
            status="completed",
            version=1,
            is_current=False,
        )
        db.add_all([current, stale])
        await db.flush()

        db.add_all(
            [
                ParentChunk(
                    uid="p-1",
                    doc_id=current.id,
                    kb_id=kb.id,
                    parent_index=2,
                    content="父分块完整上下文，包含退换货时效与运费说明。",
                    char_count=20,
                    title="退换货规则",
                    section="售后",
                    page=4,
                ),
                Chunk(
                    uid="c-1",
                    doc_id=current.id,
                    kb_id=kb.id,
                    chunk_index=0,
                    parent_uid="p-1",
                    content="子分块片段：七天无理由退换货。",
                    char_count=16,
                ),
                Chunk(
                    uid="c-stale",
                    doc_id=stale.id,
                    kb_id=kb.id,
                    chunk_index=0,
                    content="旧版本内容。",
                    char_count=7,
                ),
            ]
        )
        await db.commit()
        return current.id, stale.id


async def test_enrich_hits_filters_stale_and_expands_parent(db_maker, monkeypatch) -> None:
    current_id, stale_id = await _seed_enrichment_rows(db_maker)
    monkeypatch.setattr(retrieval_module, "get_session_maker", lambda: db_maker)

    hits = [
        {
            **_hit("c-1", 0.9, "子分块片段：七天无理由退换货。"),
            "doc_id": current_id,
        },
        {
            **_hit("c-stale", 0.8, "旧版本内容。"),
            "doc_id": stale_id,
        },
    ]
    enriched = await enrich_hits(hits)

    assert len(enriched) == 1
    item = enriched[0]
    assert item["chunk_uid"] == "c-1"
    assert item["doc_version"] == 3
    assert item["parent_uid"] == "p-1"
    assert item["content"] == "父分块完整上下文，包含退换货时效与运费说明。"
    assert item["child_content"] == "子分块片段：七天无理由退换货。"
    assert item["title"] == "退换货规则"
    assert item["section"] == "售后"
    assert item["page"] == 4


async def test_enrich_hits_returns_empty_without_current_documents(
    db_maker, monkeypatch
) -> None:
    _, stale_id = await _seed_enrichment_rows(db_maker)
    monkeypatch.setattr(retrieval_module, "get_session_maker", lambda: db_maker)

    hits = [{**_hit("c-stale", 0.8, "旧版本内容。"), "doc_id": stale_id}]
    assert await enrich_hits(hits) == []


async def test_enrich_hits_keeps_original_when_db_fails(monkeypatch) -> None:
    def broken_maker():
        raise RuntimeError("db unavailable")

    monkeypatch.setattr(retrieval_module, "get_session_maker", broken_maker)
    hits = [_hit("c-1", 0.9, "子分块片段")]

    enriched = await enrich_hits(hits)
    assert enriched == hits


# --------------------------------------------------------------------------- #
# 多查询 / RRF / BM25 接入
# --------------------------------------------------------------------------- #
class QueryAwareStore:
    def __init__(self, by_dense: dict[float, list[dict]], failing_mode: str | None = None):
        self.by_dense = by_dense
        self.failing_mode = failing_mode
        self.calls: list[dict] = []

    async def search(self, dense, sparse, kb_ids, mode="hybrid", top_k=None, candidate_k=None):
        self.calls.append({"dense": dense, "mode": mode, "kb_ids": kb_ids})
        if self.failing_mode == mode:
            raise RuntimeError(f"{mode} unavailable")
        return list(self.by_dense.get(dense[0], []))


class FakeBM25Service:
    def __init__(self, hits: list[dict] | None = None, error: Exception | None = None) -> None:
        self.hits = hits or []
        self.error = error
        self.calls: list[dict] = []

    async def search(self, query, kb_ids, top_k=None):
        self.calls.append({"query": query, "kb_ids": kb_ids, "top_k": top_k})
        if self.error is not None:
            raise self.error
        return list(self.hits)


def _bm25_hit(uid: str, *, score: float = 8.0, coverage: float = 1.0) -> dict:
    return {
        **_hit(uid, score),
        "bm25_score": score,
        "bm25_coverage": coverage,
        "vector_score": 0.0,
        "retrieval_source": "bm25",
    }


async def test_multiple_queries_are_fused_with_rrf(monkeypatch) -> None:
    monkeypatch.setattr(Config, "rrf_k", 60)
    store = QueryAwareStore(
        {
            0.1: [_hit("A", 0.9), _hit("B", 0.8)],
            0.2: [_hit("B", 0.7), _hit("C", 0.6)],
        }
    )
    reranker = FakeReranker()

    async def fake_embed_query(text, redis=None):
        return [0.1 if text == "原问题" else 0.2], {}

    monkeypatch.setattr(retrieval_module.embedding_service, "embed_query", fake_embed_query)
    monkeypatch.setattr(retrieval_module, "vector_store", store)
    monkeypatch.setattr(retrieval_module, "reranker_service", reranker)

    hits = await RetrievalService().retrieve(
        "原问题",
        {"dense": [1]},
        final_k=3,
        queries=("原问题", "子问题"),
        strategy="subquery",
        rerank_query="原问题",
    )

    assert len(store.calls) == 2
    assert [hit["chunk_uid"] for hit in hits] == ["B", "A", "C"]
    assert reranker.calls[0]["query"] == "原问题"
    assert all("fused_score" in hit for hit in hits)


async def test_single_mode_failure_keeps_other_modes(monkeypatch) -> None:
    store = QueryAwareStore({0.1: [_hit("A", 0.9)]}, failing_mode="dense")
    reranker = FakeReranker()

    async def fake_embed_query(text, redis=None):
        return [0.1], {}

    monkeypatch.setattr(retrieval_module.embedding_service, "embed_query", fake_embed_query)
    monkeypatch.setattr(retrieval_module, "vector_store", store)
    monkeypatch.setattr(retrieval_module, "reranker_service", reranker)

    hits = await RetrievalService().retrieve(
        "包邮", {"dense": [1], "hybrid": [1]}, final_k=3
    )

    assert [hit["chunk_uid"] for hit in hits] == ["A"]


async def test_bm25_fallback_when_vector_is_empty(monkeypatch) -> None:
    monkeypatch.setattr(Config, "bm25_enabled", True)
    monkeypatch.setattr(Config, "bm25_mode", "fallback")
    monkeypatch.setattr(Config, "rerank_enabled", False)
    monkeypatch.setattr(Config, "bm25_min_query_coverage", 0.4)
    monkeypatch.setattr(retrieval_module, "bm25_service", FakeBM25Service([_bm25_hit("B-1")]))
    monkeypatch.setattr(retrieval_module, "vector_store", FakeVectorStore({}))
    monkeypatch.setattr(retrieval_module, "reranker_service", FakeReranker())

    async def fake_embed_query(text, redis=None):
        return [0.1], {}

    monkeypatch.setattr(retrieval_module.embedding_service, "embed_query", fake_embed_query)

    hits = await RetrievalService().retrieve("包邮吗", {"hybrid": [1]})

    assert [hit["chunk_uid"] for hit in hits] == ["B-1"]
    assert hits[0]["vector_score"] == 0.0


async def test_bm25_fallback_when_embedding_fails(monkeypatch) -> None:
    monkeypatch.setattr(Config, "bm25_enabled", True)
    monkeypatch.setattr(Config, "bm25_mode", "fallback")
    monkeypatch.setattr(Config, "rerank_enabled", False)
    monkeypatch.setattr(Config, "bm25_min_query_coverage", 0.4)
    bm25 = FakeBM25Service([_bm25_hit("B-2", coverage=0.8)])
    monkeypatch.setattr(retrieval_module, "bm25_service", bm25)
    monkeypatch.setattr(retrieval_module, "reranker_service", FakeReranker())

    async def broken_embed_query(text, redis=None):
        raise RuntimeError("embedding down")

    monkeypatch.setattr(retrieval_module.embedding_service, "embed_query", broken_embed_query)

    hits = await RetrievalService().retrieve("包邮吗", {"hybrid": [1]})

    assert [hit["chunk_uid"] for hit in hits] == ["B-2"]
    assert bm25.calls[0]["query"] == "包邮吗"


async def test_embedding_and_bm25_failures_raise(monkeypatch) -> None:
    monkeypatch.setattr(Config, "bm25_enabled", True)
    monkeypatch.setattr(Config, "bm25_mode", "fallback")
    monkeypatch.setattr(
        retrieval_module,
        "bm25_service",
        FakeBM25Service(error=RuntimeError("bm25 down")),
    )

    async def broken_embed_query(text, redis=None):
        raise RuntimeError("embedding down")

    monkeypatch.setattr(retrieval_module.embedding_service, "embed_query", broken_embed_query)

    with pytest.raises(RuntimeError, match="bm25 down"):
        await RetrievalService().retrieve("包邮吗", {"hybrid": [1]})


async def test_bm25_low_coverage_filtered_when_rerank_disabled(monkeypatch) -> None:
    monkeypatch.setattr(Config, "bm25_enabled", True)
    monkeypatch.setattr(Config, "bm25_mode", "fuse")
    monkeypatch.setattr(Config, "rerank_enabled", False)
    monkeypatch.setattr(Config, "bm25_min_query_coverage", 0.6)
    monkeypatch.setattr(
        retrieval_module,
        "bm25_service",
        FakeBM25Service([_bm25_hit("low", coverage=0.2), _bm25_hit("high", coverage=0.9)]),
    )
    monkeypatch.setattr(retrieval_module, "vector_store", FakeVectorStore({}))
    monkeypatch.setattr(retrieval_module, "reranker_service", FakeReranker())

    async def fake_embed_query(text, redis=None):
        return [0.1], {}

    monkeypatch.setattr(retrieval_module.embedding_service, "embed_query", fake_embed_query)

    hits = await RetrievalService().retrieve("包邮吗", {"hybrid": [1]})
    assert [hit["chunk_uid"] for hit in hits] == ["high"]


async def test_degraded_results_use_short_ttl(monkeypatch) -> None:
    monkeypatch.setattr(Config, "retrieval_degraded_cache_ttl", 30)
    captured: dict[str, int] = {}

    async def fake_set_json(redis, key, value, ttl, cache_name):
        captured["ttl"] = ttl

    class DegradedReranker(FakeReranker):
        async def rerank(self, query, hits, top_k):
            return [{**hit, "rerank_failed": True} for hit in hits[:top_k]]

    monkeypatch.setattr(retrieval_module, "set_json", fake_set_json)
    monkeypatch.setattr(retrieval_module, "vector_store", FakeVectorStore({1: [_hit("A", 0.9)]}))
    monkeypatch.setattr(retrieval_module, "reranker_service", DegradedReranker())

    async def fake_embed_query(text, redis=None):
        return [0.1], {}

    monkeypatch.setattr(retrieval_module.embedding_service, "embed_query", fake_embed_query)

    await RetrievalService().retrieve("包邮", {"hybrid": [1]})
    assert captured["ttl"] == 30


async def test_empty_results_cache_for_short_ttl(monkeypatch) -> None:
    captured: dict[str, int] = {}

    async def fake_set_json(redis, key, value, ttl, cache_name):
        captured["ttl"] = ttl

    monkeypatch.setattr(retrieval_module, "set_json", fake_set_json)
    monkeypatch.setattr(retrieval_module, "vector_store", FakeVectorStore({}))

    async def fake_embed_query(text, redis=None):
        return [0.1], {}

    monkeypatch.setattr(retrieval_module.embedding_service, "embed_query", fake_embed_query)

    await RetrievalService().retrieve("冷门", {"hybrid": [1]})
    assert captured["ttl"] == 30


async def test_exception_results_are_not_cached(monkeypatch) -> None:
    calls = {"count": 0}

    async def fake_set_json(*args, **kwargs):
        calls["count"] += 1

    class BrokenStore:
        async def search(self, *args, **kwargs):
            raise RuntimeError("milvus down")

    monkeypatch.setattr(retrieval_module, "set_json", fake_set_json)
    monkeypatch.setattr(retrieval_module, "vector_store", BrokenStore())

    async def fake_embed_query(text, redis=None):
        return [0.1], {}

    monkeypatch.setattr(retrieval_module.embedding_service, "embed_query", fake_embed_query)

    assert await RetrievalService().retrieve("包邮", {"hybrid": [1]}) == []
    assert calls["count"] == 0
