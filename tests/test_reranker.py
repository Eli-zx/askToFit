"""重排序失败语义测试：不得静默丢弃候选或混淆得分量纲。"""

import asyncio
import threading
import time

import pytest

from app.services import reranker as reranker_module
from app.services.reranker import RerankerService


@pytest.fixture(autouse=True)
def _skip_model_load(monkeypatch):
    """单测不加载真实模型；需要验证加载行为的用例自行覆盖。"""

    def fake_ensure(self):
        if self._model is None:
            self._model = object()
        return self._model

    monkeypatch.setattr(RerankerService, "_ensure_model", fake_ensure)


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


async def test_rerank_caps_candidates_and_truncates_passages(monkeypatch) -> None:
    monkeypatch.setattr(reranker_module.Config, "rerank_candidate_k", 2)
    monkeypatch.setattr(reranker_module.Config, "rerank_passage_chars", 60)
    service = RerankerService()
    captured: dict[str, list[str]] = {}

    def fake_score(query, passages):
        captured["passages"] = passages
        return [0.5 for _ in passages]

    service._score_sync = fake_score  # type: ignore[method-assign]
    hits = [
        {
            "chunk_uid": f"uid-{index}",
            "content": "长" * 200,
            "title": "知识",
            "section": None,
            "score": 1.0 - index * 0.1,
        }
        for index in range(5)
    ]

    await service.rerank("问题", hits, top_k=2)

    assert len(captured["passages"]) == 2
    assert all(len(passage) <= 60 for passage in captured["passages"])


async def test_rerank_excerpt_keeps_relevant_tail_fact(monkeypatch) -> None:
    monkeypatch.setattr(reranker_module.Config, "rerank_candidate_k", 1)
    monkeypatch.setattr(reranker_module.Config, "rerank_passage_chars", 80)
    service = RerankerService()
    captured: dict[str, list[str]] = {}

    def fake_score(query, passages):
        captured["passages"] = passages
        return [0.9 for _ in passages]

    service._score_sync = fake_score  # type: ignore[method-assign]
    filler = "- 与客服问题无关的说明内容，用于占满分块前半段。\n" * 6
    hits = [
        {
            "chunk_uid": "uid-1",
            "content": filler + "- 问：客服几点在线？答：每日 9:00-22:00。" + filler,
            "title": "顾客须知",
            "section": "常见问题",
            "score": 0.9,
        }
    ]

    await service.rerank("客服几点在线？", hits, top_k=1)

    passage = captured["passages"][0]
    assert len(passage) <= 80
    assert "9:00-22:00" in passage


async def test_rerank_concurrency_is_bounded(monkeypatch) -> None:
    monkeypatch.setattr(reranker_module.Config, "rerank_max_concurrent", 1)
    service = RerankerService()
    lock = threading.Lock()
    active = {"now": 0, "max": 0}

    def slow_score(query, passages):
        with lock:
            active["now"] += 1
            active["max"] = max(active["max"], active["now"])
        time.sleep(0.05)
        with lock:
            active["now"] -= 1
        return [0.5 for _ in passages]

    service._score_sync = slow_score  # type: ignore[method-assign]

    results = await asyncio.gather(
        *(service.rerank("问题", _hits(), top_k=2) for _ in range(3))
    )

    assert all(len(result) == 2 for result in results)
    assert active["max"] == 1


async def test_rerank_disabled_uses_vector_scores(monkeypatch) -> None:
    monkeypatch.setattr(reranker_module.Config, "rerank_enabled", False)
    service = RerankerService()
    result = await service.rerank("问题", _hits(), top_k=3)

    assert [hit["chunk_uid"] for hit in result] == ["uid-0", "uid-1", "uid-2"]
    assert all(hit["score_mode"] == "vector" for hit in result)
    assert all("rerank_failed" not in hit for hit in result)


async def test_rerank_timeout_falls_back_without_waiting(monkeypatch) -> None:
    monkeypatch.setattr(reranker_module.Config, "rerank_timeout_seconds", 0.05)
    monkeypatch.setattr(reranker_module.Config, "rerank_max_concurrent", 1)
    service = RerankerService()

    def slow_score(query, passages):
        time.sleep(0.4)
        return [0.9 for _ in passages]

    service._score_sync = slow_score  # type: ignore[method-assign]

    started = time.perf_counter()
    result = await service.rerank("问题", _hits(), top_k=2)
    elapsed = time.perf_counter() - started

    assert elapsed < 0.25
    assert [hit["chunk_uid"] for hit in result] == ["uid-0", "uid-1"]
    # 超时是主动降级：不能标 rerank_failed，否则 drop 策略会丢弃全部候选
    assert all("rerank_failed" not in hit for hit in result)
    assert all(hit["rerank_timeout"] is True for hit in result)
    assert all(hit["score_mode"] == "vector" for hit in result)

    # 线程结束后信号量必须释放，否则后续请求会被永久卡住
    await asyncio.sleep(0.5)
    assert service._semaphore().locked() is False


async def test_rerank_queue_wait_counts_toward_timeout(monkeypatch) -> None:
    monkeypatch.setattr(reranker_module.Config, "rerank_timeout_seconds", 0.05)
    monkeypatch.setattr(reranker_module.Config, "rerank_max_concurrent", 1)
    service = RerankerService()

    def slow_score(query, passages):
        time.sleep(0.4)
        return [0.9 for _ in passages]

    service._score_sync = slow_score  # type: ignore[method-assign]

    first = asyncio.create_task(service.rerank("问题", _hits(), top_k=2))
    await asyncio.sleep(0.01)

    started = time.perf_counter()
    second = await service.rerank("问题", _hits(), top_k=2)
    elapsed = time.perf_counter() - started

    assert elapsed < 0.2
    assert all(hit["rerank_timeout"] is True for hit in second)

    await first
    await asyncio.sleep(0.5)
    assert service._semaphore().locked() is False


async def test_rerank_timeout_does_not_open_circuit(monkeypatch) -> None:
    monkeypatch.setattr(reranker_module.Config, "rerank_timeout_seconds", 0.02)
    monkeypatch.setattr(reranker_module.Config, "rerank_max_consecutive_failures", 2)
    service = RerankerService()

    def slow_score(query, passages):
        time.sleep(0.1)
        return [0.5 for _ in passages]

    service._score_sync = slow_score  # type: ignore[method-assign]

    for _ in range(3):
        result = await service.rerank("问题", _hits(), top_k=2)
        assert all(hit["rerank_timeout"] is True for hit in result)
        await asyncio.sleep(0.15)

    assert service._disabled is False


async def test_rerank_timeout_zero_waits_for_completion(monkeypatch) -> None:
    monkeypatch.setattr(reranker_module.Config, "rerank_timeout_seconds", 0.0)
    service = RerankerService()

    def slow_score(query, passages):
        time.sleep(0.08)
        return [0.5 for _ in passages]

    service._score_sync = slow_score  # type: ignore[method-assign]

    result = await service.rerank("问题", _hits(), top_k=2)

    assert all(hit["score_mode"] == "rerank" for hit in result)
    assert all("rerank_failed" not in hit for hit in result)


async def test_rerank_model_load_does_not_consume_inference_timeout(monkeypatch) -> None:
    """冷启动加载慢于推理超时时，仍应完成推理而不是降级为向量顺序。"""
    monkeypatch.setattr(reranker_module.Config, "rerank_timeout_seconds", 0.05)
    service = RerankerService()

    def slow_load():
        time.sleep(0.2)
        service._model = object()
        return service._model

    service._ensure_model = slow_load  # type: ignore[method-assign]
    service._score_sync = lambda query, passages: [0.9 for _ in passages]  # type: ignore[method-assign]

    result = await service.rerank("问题", _hits(), top_k=2)

    assert all(hit["score_mode"] == "rerank" for hit in result)
    assert all(hit.get("rerank_score") == 0.9 for hit in result)
    assert all("rerank_timeout" not in hit for hit in result)


async def test_rerank_model_load_failure_degrades_with_flag(monkeypatch) -> None:
    """加载失败按显式失败降级，交给上层按 rerank_failure_mode 处理。"""
    monkeypatch.setattr(reranker_module.Config, "rerank_timeout_seconds", 0.05)
    monkeypatch.setattr(reranker_module.Config, "rerank_max_consecutive_failures", 10)
    service = RerankerService()
    calls = {"count": 0}

    def broken_load():
        raise RuntimeError("model load crash")

    def should_not_run(query, passages):
        calls["count"] += 1
        return [0.9 for _ in passages]

    service._ensure_model = broken_load  # type: ignore[method-assign]
    service._score_sync = should_not_run  # type: ignore[method-assign]

    result = await service.rerank("问题", _hits(), top_k=2)

    assert calls["count"] == 0
    assert all(hit["rerank_failed"] is True for hit in result)
    assert all(hit["score_mode"] == "vector" for hit in result)
    assert service._consecutive_failures == 1


async def test_admin_rerank_channel_is_independent(monkeypatch) -> None:
    """普通重排序槽位被占满时，管理员仍能完成本轮重排序。"""
    service = RerankerService()
    service._model = object()
    service._score_sync = lambda query, passages: [0.9 for _ in passages]  # type: ignore[method-assign]

    normal = service._semaphore("normal")
    await normal.acquire()
    try:
        result = await asyncio.wait_for(
            service.rerank("问题", _hits(), top_k=1, priority="admin"),
            timeout=1.0,
        )
    finally:
        normal.release()

    assert len(result) == 1
    assert result[0]["rerank_score"] == 0.9
