"""BGE-reranker-v2-m3 重排序服务（两阶段检索的第二阶段）。

输出同时保留 `vector_score` 与 `rerank_score`，并用 `score_mode` /
`rerank_failed` 明确标记本轮得分来源，避免上层把两种量纲混用。
"""

from __future__ import annotations

import asyncio
import threading
from typing import Any

from app.core.logger import get_logger
from app.services.ingestion import build_embedding_text
from app.services.metrics import inc
from config import Config

logger = get_logger(__name__)


class RerankerService:
    """懒加载重排序模型；失败时按显式策略降级并保留原始得分。"""

    def __init__(self) -> None:
        self._model: Any = None
        self._lock = threading.Lock()
        self._consecutive_failures = 0
        self._disabled = False

    def _ensure_model(self) -> Any:
        if self._model is not None:
            return self._model
        with self._lock:
            if self._model is None:
                from FlagEmbedding import FlagReranker

                logger.info("正在加载 BGE Reranker 模型 path=%s", Config.bge_reranker)
                self._model = FlagReranker(
                    Config.bge_reranker,
                    use_fp16=Config.embedding_device.startswith("cuda"),
                    device=Config.embedding_device,
                )
                logger.info("BGE Reranker 加载完成")
        return self._model

    def _score_sync(self, query: str, passages: list[str]) -> list[float]:
        model = self._ensure_model()
        scores = model.compute_score(
            [[query, passage] for passage in passages],
            batch_size=8,
            max_length=1024,
            normalize=True,
        )
        if isinstance(scores, (int, float)):
            return [float(scores)]
        return [float(score) for score in scores]

    @staticmethod
    def _vector_only(hits: list[dict[str, Any]], *, failed: bool = False) -> list[dict[str, Any]]:
        annotated: list[dict[str, Any]] = []
        for hit in hits:
            item = dict(hit)
            item["vector_score"] = float(item.get("vector_score", item.get("score", 0.0)))
            item["score_mode"] = "vector"
            if failed:
                item["rerank_failed"] = True
            annotated.append(item)
        return annotated

    def _record_failure(self) -> None:
        self._consecutive_failures += 1
        inc("asktofit_rerank_failures_total")
        if self._consecutive_failures >= max(1, Config.rerank_max_consecutive_failures):
            if not self._disabled:
                logger.error(
                    "重排序连续失败 %d 次，已熔断为不可用"
                    "（app/services/reranker.py:_record_failure）",
                    self._consecutive_failures,
                )
            self._disabled = True

    async def rerank(
        self, query: str, hits: list[dict[str, Any]], top_k: int
    ) -> list[dict[str, Any]]:
        if not hits:
            return []
        if not Config.rerank_enabled:
            return self._vector_only(hits[:top_k])
        if self._disabled:
            logger.warning("重排序已熔断，本轮按 rerank_failure_mode 处理")
            return self._vector_only(hits[:top_k], failed=True)

        candidates = hits[: Config.rerank_candidate_k]
        passages = [
            build_embedding_text(
                hit.get("title"), hit.get("section"), hit.get("content") or ""
            )[:2000]
            for hit in candidates
        ]
        try:
            scores = await asyncio.to_thread(self._score_sync, query, passages)
        except Exception:
            self._record_failure()
            logger.exception(
                "重排序模型调用失败，按 rerank_failure_mode=%s 显式降级",
                Config.rerank_failure_mode,
            )
            # 不在这里丢弃候选：上层需要原始命中记录知识缺口得分
            return self._vector_only(hits[:top_k], failed=True)

        if len(scores) != len(candidates):
            self._record_failure()
            logger.warning(
                "重排序得分数量异常 scores=%d candidates=%d，标记本轮重排序失败",
                len(scores),
                len(candidates),
            )
            return self._vector_only(hits[:top_k], failed=True)

        self._consecutive_failures = 0
        reranked: list[dict[str, Any]] = []
        for hit, score in zip(candidates, scores, strict=True):
            item = dict(hit)
            item["vector_score"] = float(
                item.get("vector_score", item.get("score", 0.0))
            )
            item["rerank_score"] = float(score)
            item["score"] = float(score)
            item["score_mode"] = "rerank"
            reranked.append(item)
        reranked.sort(key=lambda item: item["score"], reverse=True)
        return reranked[:top_k]


reranker_service = RerankerService()
