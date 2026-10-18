"""重排序失败语义测试：不得静默丢弃候选或混淆得分量纲。"""

from app.services import reranker as reranker_module
from app.services.reranker import RerankerService


def _hits(count: int = 3) -> list[dict]:
    return [
        {
            "chunk_uid": f"uid-{index}",
            "content": f"第 {index} 条资料",
            "title": "知识",
            "section": None,
            "score": 0.9 - index * 0.1,
        }
        for index in range(count)
    ]


async def test_rerank_success_keeps_both_scores() -> None:
    service = RerankerService()

    def fake_score(query, passages):
        return [0.2, 0.95, 0.5]

    service._score_sync = fake_score  # type: ignore[method-assign]
    result = await service.rerank("问题", _hits(), top_k=2)

    assert [hit["chunk_uid"] for hit in result] == ["uid-1", "uid-2"]
    assert result[0]["score_mode"] == "rerank"
    assert result[0]["rerank_score"] == 0.95
    assert result[0]["vector_score"] == 0.8


async def test_rerank_success_preserves_fused_vector_score() -> None:
    service = RerankerService()
    service._score_sync = lambda query, passages: [0.9]  # type: ignore[method-assign]

    hits = [
        {
            "chunk_uid": "uid-1",
            "content": "资料",
            "title": "知识",
            "section": None,
            "score": 0.03,
            "fused_score": 0.03,
            "vector_score": 0.8,
            "bm25_score": 12.0,
        }
    ]
    result = await service.rerank("问题", hits, top_k=1)

    # RRF 分不能被写成 vector_score
    assert result[0]["vector_score"] == 0.8
    assert result[0]["fused_score"] == 0.03
    assert result[0]["rerank_score"] == 0.9


async def test_rerank_disabled_preserves_fused_vector_score(monkeypatch) -> None:
    monkeypatch.setattr(reranker_module.Config, "rerank_enabled", False)
    service = RerankerService()
    hits = [
        {
            "chunk_uid": "uid-1",
            "content": "资料",
            "title": "知识",
            "section": None,
            "score": 0.03,
            "vector_score": 0.7,
            "bm25_score": 9.0,
        }
    ]
    result = await service.rerank("问题", hits, top_k=1)
    assert result[0]["vector_score"] == 0.7


async def test_rerank_failure_returns_vector_hits_with_flag(monkeypatch) -> None:
    service = RerankerService()

    def broken_score(query, passages):
        raise RuntimeError("model crash")

    service._score_sync = broken_score  # type: ignore[method-assign]
    result = await service.rerank("问题", _hits(), top_k=2)

    assert [hit["chunk_uid"] for hit in result] == ["uid-0", "uid-1"]
    assert all(hit["rerank_failed"] is True for hit in result)
    assert all(hit["score_mode"] == "vector" for hit in result)
    assert result[0]["vector_score"] == 0.9


async def test_rerank_circuit_opens_after_consecutive_failures(monkeypatch) -> None:
    monkeypatch.setattr(reranker_module.Config, "rerank_max_consecutive_failures", 2)
    service = RerankerService()

    def broken_score(query, passages):
        raise RuntimeError("model crash")

    service._score_sync = broken_score  # type: ignore[method-assign]
    await service.rerank("问题", _hits(), top_k=2)
    assert service._disabled is False
    await service.rerank("问题", _hits(), top_k=2)
    assert service._disabled is True

    calls = {"count": 0}

    def should_not_run(query, passages):
        calls["count"] += 1
        return []

    service._score_sync = should_not_run  # type: ignore[method-assign]
    result = await service.rerank("问题", _hits(), top_k=2)
    assert calls["count"] == 0
    assert all(hit.get("rerank_failed") for hit in result)


async def test_rerank_disabled_uses_vector_scores(monkeypatch) -> None:
    monkeypatch.setattr(reranker_module.Config, "rerank_enabled", False)
    service = RerankerService()
    result = await service.rerank("问题", _hits(), top_k=3)

    assert [hit["chunk_uid"] for hit in result] == ["uid-0", "uid-1", "uid-2"]
    assert all(hit["score_mode"] == "vector" for hit in result)
    assert all("rerank_failed" not in hit for hit in result)
