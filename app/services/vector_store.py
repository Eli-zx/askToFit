"""Milvus 向量库封装：建库建表、写入、删除、稠密 / 混合检索。"""

from __future__ import annotations

import asyncio
import threading
from typing import Any

from pymilvus import AnnSearchRequest, DataType, MilvusClient, WeightedRanker
from pymilvus import __version__ as pymilvus_version

from app.core.logger import get_logger
from config import Config

logger = get_logger(__name__)


def ensure_hybrid_compat(version: str | None = None) -> None:
    """混合检索依赖 pymilvus 2.4.x 的底层 handler，版本不符时快速失败。"""
    current = version or pymilvus_version
    if not current.startswith("2.4."):
        raise RuntimeError(
            f"pymilvus {current} 与混合检索兼容层不匹配，请安装 2.4.x"
            "（app/services/vector_store.py:ensure_hybrid_compat）"
        )

OUTPUT_FIELDS = [
    "chunk_uid",
    "doc_id",
    "kb_id",
    "chunk_index",
    "content",
    "title",
    "section",
    "page",
]


class MilvusVectorStore:
    def __init__(self) -> None:
        self._client: MilvusClient | None = None
        self._lock = threading.Lock()
        self._uri = f"http://{Config.milvus_host}:{Config.milvus_port}"

    # ------------------------------------------------------------------ #
    # 初始化
    # ------------------------------------------------------------------ #
    def _ensure_database(self) -> None:
        client = MilvusClient(uri=self._uri)
        try:
            databases = client.list_databases()
            if Config.database_name not in databases:
                client.create_database(Config.database_name)
                logger.info("已创建 Milvus 数据库 %s", Config.database_name)
        finally:
            client.close()

    def _create_collection(self, client: MilvusClient) -> None:
        schema = client.create_schema(auto_id=False, enable_dynamic_field=False)
        schema.add_field("chunk_uid", DataType.VARCHAR, max_length=64, is_primary=True)
        schema.add_field("kb_id", DataType.INT64)
        schema.add_field("doc_id", DataType.INT64)
        schema.add_field("chunk_index", DataType.INT64)
        schema.add_field("content", DataType.VARCHAR, max_length=16384)
        schema.add_field("title", DataType.VARCHAR, max_length=512)
        schema.add_field("section", DataType.VARCHAR, max_length=512)
        schema.add_field("page", DataType.INT64)
        schema.add_field("dense_vector", DataType.FLOAT_VECTOR, dim=Config.embedding_dim)
        schema.add_field("sparse_vector", DataType.SPARSE_FLOAT_VECTOR)

        index_params = client.prepare_index_params()
        index_params.add_index(
            field_name="dense_vector",
            index_type="HNSW",
            metric_type="COSINE",
            params={"M": 16, "efConstruction": 200},
        )
        index_params.add_index(
            field_name="sparse_vector",
            index_type="SPARSE_INVERTED_INDEX",
            metric_type="IP",
            params={"drop_ratio_build": 0.2},
        )
        client.create_collection(
            collection_name=Config.collection_name,
            schema=schema,
            index_params=index_params,
            consistency_level="Bounded",
        )
        logger.info("已创建 Milvus Collection %s", Config.collection_name)

    def _ensure_client(self) -> MilvusClient:
        if self._client is not None:
            return self._client
        with self._lock:
            if self._client is None:
                self._ensure_database()
                client = MilvusClient(uri=self._uri, db_name=Config.database_name)
                if not client.has_collection(Config.collection_name):
                    self._create_collection(client)
                client.load_collection(Config.collection_name)
                self._client = client
                logger.info(
                    "Milvus 连接就绪 db=%s collection=%s",
                    Config.database_name,
                    Config.collection_name,
                )
        return self._client

    # ------------------------------------------------------------------ #
    # 写入 / 删除
    # ------------------------------------------------------------------ #
    def _insert_sync(self, rows: list[dict[str, Any]]) -> None:
        if not rows:
            return
        client = self._ensure_client()
        batch = 200
        for start in range(0, len(rows), batch):
            client.insert(Config.collection_name, rows[start : start + batch])
        client.flush(Config.collection_name)

    def _upsert_sync(self, row: dict[str, Any]) -> None:
        client = self._ensure_client()
        client.upsert(Config.collection_name, [row])
        client.flush(Config.collection_name)

    def _delete_sync(self, expression: str) -> None:
        client = self._ensure_client()
        client.delete(Config.collection_name, filter=expression)
        client.flush(Config.collection_name)

    async def insert_chunks(self, rows: list[dict[str, Any]]) -> None:
        await asyncio.to_thread(self._insert_sync, rows)

    async def upsert_chunk(self, row: dict[str, Any]) -> None:
        await asyncio.to_thread(self._upsert_sync, row)

    async def delete_by_doc(self, doc_id: int) -> None:
        await asyncio.to_thread(self._delete_sync, f"doc_id == {int(doc_id)}")

    async def delete_by_kb(self, kb_id: int) -> None:
        await asyncio.to_thread(self._delete_sync, f"kb_id == {int(kb_id)}")

    async def delete_by_uid(self, chunk_uid: str) -> None:
        safe_uid = "".join(ch for ch in chunk_uid if ch.isalnum())
        await asyncio.to_thread(self._delete_sync, f'chunk_uid == "{safe_uid}"')

    # ------------------------------------------------------------------ #
    # 检索
    # ------------------------------------------------------------------ #
    def _search_sync(
        self,
        dense_vector: list[float],
        sparse_vector: dict[int, float] | None,
        kb_ids: list[int],
        mode: str,
        top_k: int,
        candidate_k: int,
    ) -> list[dict[str, Any]]:
        if not kb_ids:
            return []
        client = self._ensure_client()
        expression = f"kb_id in [{','.join(str(int(kb)) for kb in kb_ids)}]"
        dense_param = {
            "metric_type": "COSINE",
            "params": {"ef": max(64, candidate_k)},
        }

        use_hybrid = mode == "hybrid" and bool(sparse_vector)
        if use_hybrid:
            ensure_hybrid_compat()
            requests = [
                AnnSearchRequest(
                    data=[dense_vector],
                    anns_field="dense_vector",
                    param=dense_param,
                    limit=candidate_k,
                    expr=expression,
                ),
                AnnSearchRequest(
                    data=[sparse_vector],
                    anns_field="sparse_vector",
                    param={"metric_type": "IP", "params": {}},
                    limit=candidate_k,
                    expr=expression,
                ),
            ]
            # pymilvus 2.4.x 的 MilvusClient.hybrid_search 在结果迭代处存在兼容性问题，
            # 这里直接调用底层 handler，并用索引访问规避 SequenceIterator 迭代。
            connection = client._get_connection()
            result = connection.hybrid_search(
                Config.collection_name,
                requests,
                WeightedRanker(Config.dense_weight, Config.sparse_weight),
                limit=top_k,
                output_fields=OUTPUT_FIELDS,
            )
            first_hits = result[0] if len(result) else []
            raw_hits = [first_hits[i].to_dict() for i in range(len(first_hits))]
        else:
            results = client.search(
                collection_name=Config.collection_name,
                data=[dense_vector],
                anns_field="dense_vector",
                search_params=dense_param,
                limit=top_k,
                filter=expression,
                output_fields=OUTPUT_FIELDS,
            )
            raw_hits = list(results[0]) if results else []

        hits: list[dict[str, Any]] = []
        for hit in raw_hits:
            # Hit.to_dict() 形如 {"chunk_uid": ..., "distance": ..., "entity": {...}}，
            # 同时兼容输出字段平铺的返回结构。
            raw = dict(hit)
            entity = raw.pop("entity", None)
            data = dict(entity) if isinstance(entity, dict) else {}
            data.update({field: raw.get(field) for field in OUTPUT_FIELDS if field in raw})
            hits.append(
                {
                    **data,
                    "chunk_uid": data.get("chunk_uid") or raw.get("id"),
                    "score": float(raw.get("distance", 0.0)),
                }
            )
        return hits

    async def search(
        self,
        dense_vector: list[float],
        sparse_vector: dict[int, float] | None,
        kb_ids: list[int],
        mode: str = "hybrid",
        top_k: int | None = None,
        candidate_k: int | None = None,
    ) -> list[dict[str, Any]]:
        return await asyncio.to_thread(
            self._search_sync,
            dense_vector,
            sparse_vector,
            kb_ids,
            mode,
            top_k or Config.retrieval_k,
            candidate_k or Config.candidate_k,
        )

    def _ping_sync(self) -> bool:
        client = self._ensure_client()
        return client.has_collection(Config.collection_name)

    async def ping(self) -> bool:
        return await asyncio.to_thread(self._ping_sync)


vector_store = MilvusVectorStore()
