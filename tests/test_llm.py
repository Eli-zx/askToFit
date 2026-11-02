"""大模型并发槽位与排队超时测试。"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from app.services import llm as llm_module
from app.services.llm import LLMError, LLMService


def _client_with(create):
    return SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create)))


async def test_queue_timeout_raises_friendly_error(monkeypatch) -> None:
    monkeypatch.setattr(llm_module.Config, "max_concurrent_llm", 1)
    monkeypatch.setattr(llm_module.Config, "llm_queue_timeout_seconds", 0.05)
    service = LLMService()
    monkeypatch.setattr(service, "_get_client", lambda model: _client_with(None))

    await service._semaphore.acquire()
    try:
        with pytest.raises(LLMError, match="当前咨询较多"):
            await service.complete("deepseekv4-flash", [{"role": "user", "content": "hi"}])
    finally:
        service._semaphore.release()


async def test_slot_is_released_after_success(monkeypatch) -> None:
    monkeypatch.setattr(llm_module.Config, "max_concurrent_llm", 1)

    async def fake_create(**kwargs):
        return SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content="回答"))]
        )

    service = LLMService()
    monkeypatch.setattr(service, "_get_client", lambda model: _client_with(fake_create))

    assert await service.complete("deepseekv4-flash", [{"role": "user", "content": "hi"}]) == "回答"
    assert service._semaphore._value == 1


async def test_slot_is_released_after_failure(monkeypatch) -> None:
    monkeypatch.setattr(llm_module.Config, "max_concurrent_llm", 1)
    monkeypatch.setattr(llm_module.Config, "llm_max_retries", 1)

    async def broken_create(**kwargs):
        raise RuntimeError("upstream down")

    service = LLMService()
    monkeypatch.setattr(service, "_get_client", lambda model: _client_with(broken_create))

    with pytest.raises(LLMError):
        await service.complete("deepseekv4-flash", [{"role": "user", "content": "hi"}])
    assert service._semaphore._value == 1
