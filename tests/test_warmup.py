"""模型预热与保活循环测试。"""

from __future__ import annotations

import asyncio

from app.services import warmup as warmup_module
from app.services.embedding import embedding_service
from app.services.intent_bert import intent_bert_classifier
from app.services.reranker import reranker_service
from app.services.warmup import keep_warm_loop, warmup_models
from config import Config


async def test_warmup_models_runs_every_model_and_survives_failure(monkeypatch) -> None:
    calls: list[str] = []

    async def intent_ok() -> None:
        calls.append("intent-bert")

    async def embedding_broken() -> None:
        calls.append("bge-m3")
        raise RuntimeError("模型不可用")

    async def reranker_ok() -> None:
        calls.append("bge-reranker")

    monkeypatch.setattr(intent_bert_classifier, "warmup", intent_ok)
    monkeypatch.setattr(embedding_service, "warmup", embedding_broken)
    monkeypatch.setattr(reranker_service, "warmup", reranker_ok)

    await warmup_models()

    assert calls == ["intent-bert", "bge-m3", "bge-reranker"]


async def test_keep_warm_loop_exits_when_interval_is_zero(monkeypatch) -> None:
    monkeypatch.setattr(Config, "model_warmup_interval_seconds", 0)
    stop_event = asyncio.Event()

    await asyncio.wait_for(keep_warm_loop(stop_event), timeout=1.0)


async def test_keep_warm_loop_repeats_until_stopped(monkeypatch) -> None:
    monkeypatch.setattr(Config, "model_warmup_interval_seconds", 0.01)
    calls: list[int] = []

    async def fake_warmup() -> None:
        calls.append(1)

    monkeypatch.setattr(warmup_module, "warmup_models", fake_warmup)

    stop_event = asyncio.Event()
    task = asyncio.create_task(keep_warm_loop(stop_event))
    await asyncio.sleep(0.05)
    stop_event.set()
    await asyncio.wait_for(task, timeout=1.0)

    assert len(calls) >= 1
    stopped_at = len(calls)
    await asyncio.sleep(0.03)
    assert len(calls) == stopped_at
