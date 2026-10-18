"""Milvus 检索结果结构兼容性测试。"""

from typing import Any

import pytest

from app.services.vector_store import MilvusVectorStore, ensure_hybrid_compat


class _FakeHit:
    def __init__(self, pk: str, distance: float, fields: dict[str, Any]) -> None:
        self._pk = pk
        self._distance = distance
        self._fields = fields

    def to_dict(self) -> dict[str, Any]:
        return {self._pk: self._pk, "distance": self._distance, "entity": self._fields}


class _FakeHits(list):
    pass


class _FakeConnection:
    def __init__(self) -> None:
        self.called_with: dict[str, Any] = {}

    def hybrid_search(self, collection_name, reqs, ranker, **kwargs):
        self.called_with = {
            "collection_name": collection_name,
            "reqs": reqs,
            "ranker": ranker,
            **kwargs,
        }
        return [
            _FakeHits(
                [
                    _FakeHit(
                        "uid-1",
                        0.87,
                        {
                            "chunk_uid": "uid-1",
                            "doc_id": 1,
                            "kb_id": 2,
                            "chunk_index": 0,
                            "content": "满 199 元包邮",
                            "title": "物流说明",
                            "section": "运费标准",
                            "page": 2,
                        },
                    )
                ]
            )
        ]


class _FakeClient:
    def __init__(self) -> None:
        self.connection = _FakeConnection()

    def _get_connection(self) -> _FakeConnection:
        return self.connection

    def search(self, **kwargs):
        return [
            [
                {
                    "chunk_uid": "uid-2",
                    "content": "电子发票三个工作日开具",
                    "title": "发票说明",
                    "distance": 0.75,
                    "id": "uid-2",
                }
            ]
        ]


def _make_store() -> tuple[MilvusVectorStore, _FakeClient]:
    store = MilvusVectorStore()
    client = _FakeClient()
    store._client = client  # type: ignore[assignment]
    return store, client


def test_hybrid_search_parses_nested_hit_entity() -> None:
    store, client = _make_store()

    hits = store._search_sync(
        dense_vector=[0.1] * 1024,
        sparse_vector={1: 0.5},
        kb_ids=[2],
        mode="hybrid",
        top_k=3,
        candidate_k=10,
    )

    assert len(hits) == 1
    hit = hits[0]
    assert hit["chunk_uid"] == "uid-1"
    assert hit["content"] == "满 199 元包邮"
    assert hit["section"] == "运费标准"
    assert hit["page"] == 2
    assert hit["score"] == 0.87
    assert client.connection.called_with["collection_name"] == "fitrag"


def test_dense_search_parses_flat_hit() -> None:
    store, _ = _make_store()

    hits = store._search_sync(
        dense_vector=[0.1] * 1024,
        sparse_vector=None,
        kb_ids=[2],
        mode="dense",
        top_k=3,
        candidate_k=10,
    )

    assert len(hits) == 1
    assert hits[0]["chunk_uid"] == "uid-2"
    assert hits[0]["content"] == "电子发票三个工作日开具"
    assert hits[0]["score"] == 0.75


def test_hybrid_compat_accepts_supported_version() -> None:
    ensure_hybrid_compat("2.4.15")


def test_hybrid_compat_rejects_other_versions() -> None:
    with pytest.raises(RuntimeError) as excinfo:
        ensure_hybrid_compat("2.5.0")

    message = str(excinfo.value)
    assert "2.5.0" in message
    assert "vector_store.py:ensure_hybrid_compat" in message


def test_search_without_knowledge_bases_returns_empty() -> None:
    store, _ = _make_store()

    hits = store._search_sync(
        dense_vector=[0.1] * 1024,
        sparse_vector={1: 0.5},
        kb_ids=[],
        mode="hybrid",
        top_k=3,
        candidate_k=10,
    )

    assert hits == []
