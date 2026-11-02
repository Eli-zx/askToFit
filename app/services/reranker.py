"""BGE-reranker-v2-m3 重排序服务（两阶段检索的第二阶段）。

输出同时保留 `vector_score` 与 `rerank_score`，并用 `score_mode` /
`rerank_failed` 明确标记本轮得分来源，避免上层把两种量纲混用。
"""

from __future__ import annotations

import asyncio
import threading
import time
import weakref
from typing import Any

from app.core.logger import get_logger
from app.services.excerpt import build_rerank_passage
from app.services.metrics import inc
from app.services.priority import PRIORITY_NORMAL, normalize_priority
from config import Config

logger = get_logger(__name__)


class RerankerService:
    """懒加载重排序模型；失败时按显式策略降级并保留原始得分。"""

    def __init__(self) -> None:
        self._model: Any = None
        self._lock = threading.Lock()
        self._consecutive_failures = 0
        self._disabled = False
        # 普通 / 管理员各自独立的推理槽位：管理员不会排在大批普通请求之后
        self._semaphores: dict[
            str,
            "weakref.WeakKeyDictionary[asyncio.AbstractEventLoop, asyncio.Semaphore]",
        ] = {
            "normal": weakref.WeakKeyDictionary(),
            "admin": weakref.WeakKeyDictionary(),
        }

    def _semaphore(self, priority: str = PRIORITY_NORMAL) -> asyncio.Semaphore:
        """限制同时进行的重排序推理，避免 CPU 线程被单次请求占满。"""
        normalized = normalize_priority(priority)
        loop = asyncio.get_running_loop()
        per_loop = self._semaphores[normalized]
        semaphore = per_loop.get(loop)
        if semaphore is None:
            # 两个通道各留一份推理容量；CPU 只有一份算力时由系统调度
            semaphore = asyncio.Semaphore(max(1, Config.rerank_max_concurrent))
            per_loop[loop] = semaphore
        return semaphore

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
            batch_size=Config.rerank_batch_size,
            max_length=Config.rerank_max_length,
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

    def _timeout_fallback(
        self,
        hits: list[dict[str, Any]],
        top_k: int,
        stage: str,
        timeout: float,
        candidates: int,
    ) -> list[dict[str, Any]]:
        """超时按向量顺序降级；不触发熔断，也不标 rerank_failed。

        `rerank_failed` 会在默认 drop 策略下丢弃全部候选；超时属于主动降级，
        应保留 RRF 排序继续回答，只用 `rerank_timeout` 标记并缩短缓存 TTL。
        """
        inc("asktofit_rerank_timeouts_total", {"stage": stage})
        logger.warning(
            "重排序超时 stage=%s timeout=%.1fs candidates=%d，按向量顺序降级"
            "（app/services/reranker.py:RerankerService.rerank）",
            stage,
            timeout,
            candidates,
        )
        fallback = self._vector_only(hits[:top_k])
        for item in fallback:
            item["rerank_timeout"] = True
        return fallback

    @staticmethod
    def _release_when_done(
        future: "asyncio.Future[Any]", semaphore: asyncio.Semaphore
    ) -> None:
        """超时后线程无法取消：等它真正结束再释放信号量，避免请求堆积。"""

        def _done(done: "asyncio.Future[Any]") -> None:
            try:
                done.exception()
            except BaseException:
                pass
            finally:
                semaphore.release()

        future.add_done_callback(_done)

    async def rerank(
        self,
        query: str,
        hits: list[dict[str, Any]],
        top_k: int,
        priority: str = PRIORITY_NORMAL,
    ) -> list[dict[str, Any]]:
        if not hits:
            return []
        if not Config.rerank_enabled:
            return self._vector_only(hits[:top_k])
        if self._disabled:
            logger.warning("重排序已熔断，本轮按 rerank_failure_mode 处理")
            return self._vector_only(hits[:top_k], failed=True)

        # 模型加载不占用推理超时预算：冷启动加载可能需要数秒到数十秒，
        # 若与 RERANK_TIMEOUT_SECONDS 共用，会把正常加载误判成推理超时并降级。
        if self._model is None:
            try:
                await asyncio.to_thread(self._ensure_model)
            except Exception:
                self._record_failure()
                logger.exception(
                    "重排序模型加载失败，按 rerank_failure_mode=%s 显式降级"
                    "（app/services/reranker.py:RerankerService.rerank）",
                    Config.rerank_failure_mode,
                )
                return self._vector_only(hits[:top_k], failed=True)

        candidates = hits[: Config.rerank_candidate_k]
        # 只需判断相关性，用标题+章节+正文前段即可；截断可显著降低 CPU 耗时，
        # 最终回答仍使用未截断的父块内容。
        passages = [
            build_rerank_passage(query, hit, Config.rerank_passage_chars)
            for hit in candidates
        ]
        started = time.perf_counter()
        timeout = float(Config.rerank_timeout_seconds)
        deadline = started + timeout if timeout > 0 else None
        semaphore = self._semaphore(priority)

        # 排队时间也计入预算：宿主机被抢占时先降级，比继续等待更有用。
        if deadline is not None:
            remaining = deadline - time.perf_counter()
            if remaining <= 0:
                return self._timeout_fallback(
                    hits, top_k, "queue", timeout, len(candidates)
                )
            try:
                await asyncio.wait_for(semaphore.acquire(), timeout=remaining)
            except asyncio.TimeoutError:
                return self._timeout_fallback(
                    hits, top_k, "queue", timeout, len(candidates)
                )
        else:
            await semaphore.acquire()

        loop = asyncio.get_running_loop()
        future = loop.run_in_executor(None, self._score_sync, query, passages)
        try:
            if deadline is not None:
                remaining = max(0.0, deadline - time.perf_counter())
                scores = await asyncio.wait_for(
                    asyncio.shield(future), timeout=remaining
                )
            else:
                scores = await future
        except asyncio.TimeoutError:
            # 线程无法取消：等它跑完再释放信号量，避免下一次请求继续堆积。
            self._release_when_done(future, semaphore)
            return self._timeout_fallback(
                hits, top_k, "inference", timeout, len(candidates)
            )
        except asyncio.CancelledError:
            self._release_when_done(future, semaphore)
            raise
        except Exception:
            semaphore.release()
            self._record_failure()
            logger.exception(
                "重排序模型调用失败，按 rerank_failure_mode=%s 显式降级"
                "（app/services/reranker.py:RerankerService.rerank）",
                Config.rerank_failure_mode,
            )
            # 不在这里丢弃候选：上层需要原始命中记录知识缺口得分
            return self._vector_only(hits[:top_k], failed=True)
        else:
            semaphore.release()

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
        logger.info(
            "重排序完成 candidates=%d elapsed=%.2fs（app/services/reranker.py:RerankerService.rerank）",
            len(candidates),
            time.perf_counter() - started,
        )
        return reranked[:top_k]

    async def warmup(self) -> None:
        """加载模型并执行一次短推理，避免首个请求承担加载与冷启动耗时。"""
        await asyncio.to_thread(
            self._score_sync, "模型预热", ["这是一段用于预热重排序模型的短文本。"]
        )


reranker_service = RerankerService()
