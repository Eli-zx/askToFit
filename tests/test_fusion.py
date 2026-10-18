"""RRF 融合测试：手算名次分、去重、确定性排序与字段契约。"""

from app.services.fusion import rrf_fuse


def _hit(uid: str, score: float, **extra) -> dict:
    return {"chunk_uid": uid, "score": score, "content": uid, **extra}


def test_rrf_scores_are_computed_from_ranks() -> None:
    first = [_hit("A", 0.9), _hit("B", 0.8), _hit("C", 0.7)]
    second = [_hit("B", 0.95), _hit("D", 0.5)]

    fused = rrf_fuse([first, second], k=60)
    scores = {hit["chunk_uid"]: hit["score"] for hit in fused}

    assert [hit["chunk_uid"] for hit in fused] == ["B", "A", "D", "C"]
    assert scores["A"] == 1 / 61
    assert scores["B"] == 1 / 62 + 1 / 61
    assert scores["C"] == 1 / 63
    assert scores["D"] == 1 / 62


def test_rrf_weights_apply_per_route() -> None:
    first = [_hit("A", 0.9)]
    second = [_hit("B", 0.9)]
    fused = rrf_fuse([first, second], k=60, weights=[1.0, 0.5])
    scores = {hit["chunk_uid"]: hit["score"] for hit in fused}
    assert scores["A"] == 1 / 61
    assert scores["B"] == 0.5 / 61


def test_rrf_deduplicates_same_uid_in_one_list() -> None:
    hits = [_hit("A", 0.5), _hit("A", 0.9), _hit("B", 0.7)]
    fused = rrf_fuse([hits], k=60)
    assert [hit["chunk_uid"] for hit in fused] == ["A", "B"]
    assert fused[0]["score"] == 0.9


def test_rrf_tie_break_is_deterministic_by_uid() -> None:
    fused = rrf_fuse([[_hit("B", 0.9)], [_hit("A", 0.9)]], k=60)
    assert [hit["chunk_uid"] for hit in fused] == ["A", "B"]


def test_rrf_single_list_preserves_original_scores() -> None:
    originals = [_hit("A", 0.42), _hit("B", 0.31)]
    fused = rrf_fuse([originals], k=60)
    assert [hit["score"] for hit in fused] == [0.42, 0.31]
    assert fused[0] is not originals[0]


def test_rrf_keeps_vector_score_and_marks_bm25_only() -> None:
    vector = [_hit("A", 0.9, vector_score=0.8, retrieval_source="vector")]
    bm25 = [
        {
            "chunk_uid": "A",
            "score": 12.0,
            "bm25_score": 12.0,
            "bm25_coverage": 0.75,
            "vector_score": 0.0,
            "retrieval_source": "bm25",
        },
        {
            "chunk_uid": "B",
            "score": 8.0,
            "bm25_score": 8.0,
            "bm25_coverage": 0.5,
            "vector_score": 0.0,
            "retrieval_source": "bm25",
        },
    ]
    fused = {hit["chunk_uid"]: hit for hit in rrf_fuse([vector, bm25], k=60)}

    assert fused["A"]["vector_score"] == 0.8
    assert fused["A"]["bm25_score"] == 12.0
    assert fused["A"]["retrieval_source"] == "fused"
    assert set(fused["A"]["retrieval_sources"]) == {"vector", "bm25"}
    assert fused["B"]["vector_score"] == 0.0
    assert fused["B"]["bm25_score"] == 8.0
    assert fused["B"]["retrieval_sources"] == ["bm25"]


def test_rrf_empty_inputs() -> None:
    assert rrf_fuse([]) == []
    assert rrf_fuse([[], []]) == []


def test_rrf_rejects_mismatched_weights() -> None:
    try:
        rrf_fuse([[_hit("A", 1.0)], [_hit("B", 1.0)]], weights=[1.0])
    except ValueError as exc:
        assert "权重" in str(exc)
    else:  # pragma: no cover - 明确失败路径
        raise AssertionError("权重数量不一致时应抛 ValueError")
