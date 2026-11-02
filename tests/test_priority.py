"""管理员 / 普通双通道优先级测试。"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from app.services import llm as llm_module
from app.services.llm import LLMError, LLMService
from app.services.metrics import render_prometheus
from app.services.priority import (
    PRIORITY_ADMIN,
    PRIORITY_NORMAL,
    PriorityGate,
    normalize_priority,
)


def _client_with(content: str = "回答"):
    async def fake_create(**kwargs):
        return SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content=content))]
        )

    return SimpleNamespace(
        chat=SimpleNamespace(completions=SimpleNamespace(create=fake_create))
    )


def test_normalize_priority_accepts_bool_and_string() -> None:
    assert normalize_priority(True) == PRIORITY_ADMIN
    assert normalize_priority("admin") == PRIORITY_ADMIN
    assert normalize_priority(False) == PRIORITY_NORMAL
    assert normalize_priority(None) == PRIORITY_NORMAL
    assert normalize_priority("normal") == PRIORITY_NORMAL


async def test_admin_channel_is_independent_from_saturated_normal_channel() -> None:
    gate = PriorityGate(normal_slots=1, admin_slots=1)

    await gate.acquire(PRIORITY_NORMAL)
    try:
        with pytest.raises(TimeoutError):
            await gate.acquire(PRIORITY_NORMAL, timeout=0.05)

        # 普通通道已满，管理员仍能立即拿到槽位
        assert await gate.acquire(PRIORITY_ADMIN, timeout=0.05) == PRIORITY_ADMIN
        snapshot = gate.snapshot()
        assert snapshot["normal_inflight"] == 1
        assert snapshot["admin_inflight"] == 1
    finally:
        gate.release(PRIORITY_ADMIN)
        gate.release(PRIORITY_NORMAL)

    snapshot = gate.snapshot()
    assert snapshot["normal_inflight"] == 0
    assert snapshot["admin_inflight"] == 0
    assert snapshot["normal_available"] == 1
    assert snapshot["admin_available"] == 1


async def test_queue_timeout_is_recorded_and_does_not_leak_slot() -> None:
    gate = PriorityGate(normal_slots=1, admin_slots=1)
    await gate.acquire(PRIORITY_NORMAL)

    with pytest.raises(TimeoutError):
        await gate.acquire(PRIORITY_NORMAL, timeout=0.05)
    assert gate.snapshot()["normal_available"] == 0

    gate.release(PRIORITY_NORMAL)
    assert gate.snapshot()["normal_available"] == 1
    assert "asktofit_llm_queue_timeout_total" in render_prometheus()


async def test_llm_admin_priority_bypasses_saturated_normal_channel(monkeypatch) -> None:
    monkeypatch.setattr(llm_module.Config, "max_concurrent_llm", 1)
    monkeypatch.setattr(llm_module.Config, "llm_admin_slots", 1)
    monkeypatch.setattr(llm_module.Config, "llm_queue_timeout_seconds", 0.05)
    monkeypatch.setattr(llm_module.Config, "llm_admin_queue_timeout_seconds", 1.0)
    service = LLMService()
    monkeypatch.setattr(service, "_get_client", lambda model: _client_with())

    await service._gate.normal.acquire()
    try:
        with pytest.raises(LLMError, match="当前咨询较多"):
            await service.complete(
                "deepseekv4-flash", [{"role": "user", "content": "hi"}]
            )
        answer = await service.complete(
            "deepseekv4-flash",
            [{"role": "user", "content": "hi"}],
            priority="admin",
        )
        assert answer == "回答"
    finally:
        service._gate.normal.release()


async def test_admin_priority_bypasses_circuit_breaker(monkeypatch) -> None:
    monkeypatch.setattr(llm_module.Config, "max_concurrent_llm", 1)
    monkeypatch.setattr(llm_module.Config, "llm_admin_slots", 1)
    monkeypatch.setattr(llm_module.Config, "llm_circuit_failure_threshold", 1)
    service = LLMService()
    monkeypatch.setattr(service, "_get_client", lambda model: _client_with())

    service._record_failure("deepseekv4-flash")
    with pytest.raises(LLMError, match="暂时不可用"):
        await service.complete(
            "deepseekv4-flash", [{"role": "user", "content": "hi"}]
        )

    # 管理员不受普通流量触发的熔断影响
    assert (
        await service.complete(
            "deepseekv4-flash",
            [{"role": "user", "content": "hi"}],
            priority="admin",
        )
        == "回答"
    )
