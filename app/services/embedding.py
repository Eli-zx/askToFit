"""BGE-M3 稠密 + 稀疏向量编码服务。"""

from __future__ import annotations

import asyncio
import threading
from typing import Any

from app.core.logger import get_logger
from app.services.cache import embedding_key, get_json, set_json
from config import Config

logger = get_logger(__name__)


class EmbeddingService:
    """懒加载 BGE-M3；编码串行执行，避免模型并发争用。"""

    def __init__(self) -> None:
        self._model: Any = None
        self._lock = threading.Lock()
        self._semaphore = asyncio.Semaphore(max(1, Config.max_concurrent_embedding))

    def _ensure_model(self) -> Any:
        if self._model is not None:
            return self._model
        with self._lock:
            if self._model is None:
                from FlagEmbedding import BGEM3FlagModel

                logger.info("正在加载 BGE-M3 模型 path=%s", Config.bge_m3)
                self._model = BGEM3FlagModel(
                    Config.bge_m3,
                    use_fp16=Config.embedding_device.startswith("cuda"),
                    device=Config.embedding_device,
                )
                logger.info("BGE-M3 加载完成")
        return self._model

    @staticmethod
    def _convert_sparse(weights: dict) -> dict[int, float]:
        return {int(token): float(weight) for token, weight in weights.items() if float(weight) > 0}

    def _encode_sync(
        self, texts: list[str], batch_size: int
    ) -> tuple[list[list[float]], list[dict[int, float]]]:
        model = self._ensure_model()
        with self._lock:
            output = model.encode(
                texts,
                batch_size=batch_size,
                max_length=8192,
                return_dense=True,
                return_sparse=True,
                return_colbert_vecs=False,
            )
        dense = output["dense_vecs"]
        sparse = [self._convert_sparse(weights) for weights in output["lexical_weights"]]
        dense_lists = [
            vector.tolist() if hasattr(vector, "tolist") else [float(v) for v in vector]
            for vector in dense
        ]
        for vector in dense_lists:
            if len(vector) != Config.embedding_dim:
                raise RuntimeError(
                    f"向量维度异常：期望 {Config.embedding_dim}，实际 {len(vector)}"
                )
        return dense_lists, sparse

    async def embed_documents(
        self, texts: list[str], batch_size: int | None = None
    ) -> tuple[list[list[float]], list[dict[int, float]]]:
        if not texts:
            return [], []
        size = batch_size or Config.embedding_batch_size
        async with self._semaphore:
            return await asyncio.to_thread(self._encode_sync, texts, size)

    async def embed_query(
        self, text: str, redis: Any | None = None
    ) -> tuple[list[float], dict[int, float]]:
        key = embedding_key(text)
        cached = await get_json(redis, key, "embedding")
        if isinstance(cached, dict) and cached.get("dense") is not None:
            try:
                dense = [float(value) for value in cached["dense"]]
                sparse = {
                    int(token): float(weight)
                    for token, weight in (cached.get("sparse") or {}).items()
                }
                return dense, sparse
            except (TypeError, ValueError):
                logger.warning("查询向量缓存格式异常，重新编码 key=%s", key)

        dense, sparse = await self.embed_documents([text])
        await set_json(
            redis,
            key,
            {"dense": dense[0], "sparse": sparse[0]},
            Config.embedding_cache_ttl,
            "embedding",
        )
        return dense[0], sparse[0]


embedding_service = EmbeddingService()
