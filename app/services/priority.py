"""LLM 并发槽位的普通 / 管理员双通道。

普通用户共享 ``max_concurrent_llm`` 个槽位；管理员使用独立的
``llm_admin_slots`` 个槽位。两条通道互不排队，因此普通流量打满时，
管理员仍然可以立刻拿到生成槽位。
"""

from __future__ import annotations

import asyncio

from app.core.logger import get_logger
from app.services.metrics import inc, set_gauge
from config import Config

logger = get_logger(__name__)

PRIORITY_ADMIN = "admin"
PRIORITY_NORMAL = "normal"


def normalize_priority(priority: str | bool | None) -> str:
    """兼容布尔写法：True 视为管理员，其余一律按普通用户处理。"""
    if priority is True or priority == PRIORITY_ADMIN:
        return PRIORITY_ADMIN
    return PRIORITY_NORMAL


class PriorityGate:
    """按优先级分配 LLM 槽位。"""

    def __init__(
        self,
        normal_slots: int | None = None,
        admin_slots: int | None = None,
    ) -> None:
        normal = normal_slots if normal_slots is not None else Config.max_concurrent_llm
        admin = admin_slots if admin_slots is not None else Config.llm_admin_slots
        self.normal = asyncio.Semaphore(max(1, int(normal)))
        self.admin = asyncio.Semaphore(max(1, int(admin)))
        self._inflight: dict[str, int] = {PRIORITY_NORMAL: 0, PRIORITY_ADMIN: 0}
        for priority in (PRIORITY_NORMAL, PRIORITY_ADMIN):
            set_gauge("asktofit_llm_inflight", 0, {"priority": priority})

    def _semaphore(self, priority: str) -> asyncio.Semaphore:
        return self.admin if normalize_priority(priority) == PRIORITY_ADMIN else self.normal

    async def acquire(self, priority: str = PRIORITY_NORMAL, timeout: float | None = None) -> str:
        """获取槽位；``timeout=None`` 表示一直等待。

        超时抛出内置 ``TimeoutError``，由调用方转换成业务错误。
        """
        normalized = normalize_priority(priority)
        semaphore = self._semaphore(normalized)
        try:
            if timeout is None:
                await semaphore.acquire()
            else:
                await asyncio.wait_for(semaphore.acquire(), timeout=timeout)
        except TimeoutError:
            inc(
                "asktofit_llm_queue_timeout_total",
                {"priority": normalized},
            )
            logger.warning(
                "大模型槽位排队超时 priority=%s 等待=%.1fs",
                normalized,
                timeout or 0.0,
            )
            raise

        self._inflight[normalized] += 1
        set_gauge(
            "asktofit_llm_inflight",
            self._inflight[normalized],
            {"priority": normalized},
        )
        return normalized

    def release(self, priority: str = PRIORITY_NORMAL) -> None:
        normalized = normalize_priority(priority)
        self._semaphore(normalized).release()
        self._inflight[normalized] = max(0, self._inflight[normalized] - 1)
        set_gauge(
            "asktofit_llm_inflight",
            self._inflight[normalized],
            {"priority": normalized},
        )

    def snapshot(self) -> dict[str, int]:
        """运维/日志用：当前占用与剩余容量。"""
        return {
            "normal_inflight": self._inflight[PRIORITY_NORMAL],
            "normal_available": getattr(self.normal, "_value", 0),
            "admin_inflight": self._inflight[PRIORITY_ADMIN],
            "admin_available": getattr(self.admin, "_value", 0),
        }
