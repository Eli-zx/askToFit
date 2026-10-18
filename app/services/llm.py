"""大模型调用：DeepSeek / DashScope 双通道，支持重试、退避与熔断。"""

from __future__ import annotations

import asyncio
import random
import time
from collections.abc import AsyncIterator
from dataclasses import dataclass

from app.core.logger import get_logger
from app.services.metrics import inc
from config import AVAILABLE_MODELS, Config, get_api_model, get_model_settings

logger = get_logger(__name__)


class LLMError(Exception):
    """大模型调用失败（配置缺失、网络错误、服务端异常等）。"""


@dataclass
class _CircuitState:
    failures: int = 0
    opened_at: float | None = None


class LLMService:
    def __init__(self) -> None:
        self._clients: dict[str, object] = {}
        self._semaphore = asyncio.Semaphore(max(1, Config.max_concurrent_llm))
        self._circuits: dict[str, _CircuitState] = {}

    @staticmethod
    def is_available(model_name: str) -> bool:
        _, api_key = get_model_settings(model_name)
        return bool(api_key)

    def _get_client(self, model_name: str):
        from openai import AsyncOpenAI

        base_url, api_key = get_model_settings(model_name)
        if not api_key:
            provider = AVAILABLE_MODELS.get(model_name, {}).get("provider", "deepseek")
            env_name = "DASHSCOPE_API_KEY" if provider == "dashscope" else "DEEPSEEK_API_KEY"
            raise LLMError(f"模型 {model_name} 未配置 API Key，请在环境变量中设置 {env_name}")

        cache_key = f"{base_url}|{api_key[-6:]}"
        client = self._clients.get(cache_key)
        if client is None:
            client = AsyncOpenAI(
                api_key=api_key,
                base_url=base_url,
                timeout=Config.llm_timeout_seconds,
                # 重试由本服务统一控制，避免 SDK 与业务层重复放大请求
                max_retries=0,
            )
            self._clients[cache_key] = client
        return client

    # ------------------------------------------------------------------ #
    # 熔断与重试
    # ------------------------------------------------------------------ #
    def _circuit(self, model_name: str) -> _CircuitState:
        return self._circuits.setdefault(model_name, _CircuitState())

    def _ensure_circuit_closed(self, model_name: str) -> None:
        state = self._circuit(model_name)
        if state.opened_at is None:
            return
        if time.monotonic() - state.opened_at >= Config.llm_circuit_reset_seconds:
            logger.warning("大模型熔断冷却结束，进入半开状态 model=%s", model_name)
            state.opened_at = None
            state.failures = 0
            return
        raise LLMError("大模型服务暂时不可用，请稍后重试")

    def _record_success(self, model_name: str) -> None:
        state = self._circuit(model_name)
        state.failures = 0
        state.opened_at = None

    def _record_failure(self, model_name: str) -> None:
        state = self._circuit(model_name)
        state.failures += 1
        if (
            state.failures >= max(1, Config.llm_circuit_failure_threshold)
            and state.opened_at is None
        ):
            state.opened_at = time.monotonic()
            inc("asktofit_llm_circuit_open_total", {"model": model_name})
            logger.error(
                "大模型连续失败 %d 次，熔断 %d 秒 model=%s"
                "（app/services/llm.py:_record_failure）",
                state.failures,
                Config.llm_circuit_reset_seconds,
                model_name,
            )

    @staticmethod
    def _is_retryable(exc: Exception) -> bool:
        from openai import APIConnectionError, APIStatusError, APITimeoutError, RateLimitError

        if isinstance(exc, (APITimeoutError, APIConnectionError, RateLimitError)):
            return True
        if isinstance(exc, APIStatusError):
            return exc.status_code == 429 or exc.status_code >= 500
        return False

    @staticmethod
    async def _backoff(attempt: int) -> None:
        base = max(0.05, Config.llm_retry_backoff_seconds)
        delay = min(8.0, base * (2 ** (attempt - 1)))
        await asyncio.sleep(delay + random.uniform(0, 0.2))

    def _error_message(self, exc: Exception) -> str:
        from openai import APITimeoutError

        if isinstance(exc, APITimeoutError):
            return "大模型响应超时，请稍后重试"
        return f"大模型服务异常：{exc}"

    # ------------------------------------------------------------------ #
    # 调用
    # ------------------------------------------------------------------ #
    async def stream_chat(
        self,
        model_name: str,
        messages: list[dict[str, str]],
        temperature: float,
        top_p: float,
        max_tokens: int,
    ) -> AsyncIterator[str]:
        client = self._get_client(model_name)
        api_model = get_api_model(model_name)
        attempts = max(1, Config.llm_max_retries)
        emitted = False

        for attempt in range(1, attempts + 1):
            self._ensure_circuit_closed(model_name)
            try:
                async with self._semaphore:
                    stream = await client.chat.completions.create(
                        model=api_model,
                        messages=messages,  # type: ignore[arg-type]
                        temperature=temperature,
                        top_p=top_p,
                        max_tokens=max_tokens,
                        stream=True,
                    )
                    async for chunk in stream:
                        if not chunk.choices:
                            continue
                        delta = chunk.choices[0].delta
                        content = getattr(delta, "content", None)
                        if content:
                            emitted = True
                            yield content
                self._record_success(model_name)
                inc("asktofit_llm_requests_total", {"model": model_name, "status": "success"})
                return
            except Exception as exc:
                retryable = self._is_retryable(exc)
                if emitted:
                    # 已经输出部分内容，重试会造成重复文本，只能终止本轮
                    self._record_failure(model_name)
                    inc("asktofit_llm_requests_total", {"model": model_name, "status": "error"})
                    logger.error(
                        "大模型流式输出中断 model=%s attempt=%d",
                        model_name,
                        attempt,
                        exc_info=True,
                    )
                    raise LLMError("回答生成中断，请重试") from exc
                if retryable and attempt < attempts:
                    inc("asktofit_llm_requests_total", {"model": model_name, "status": "retry"})
                    logger.warning(
                        "大模型流式调用失败，准备重试 model=%s attempt=%d/%d",
                        model_name,
                        attempt,
                        attempts,
                        exc_info=True,
                    )
                    await self._backoff(attempt)
                    continue
                self._record_failure(model_name)
                inc("asktofit_llm_requests_total", {"model": model_name, "status": "error"})
                logger.error(
                    "大模型流式调用失败 model=%s attempt=%d",
                    model_name,
                    attempt,
                    exc_info=True,
                )
                raise LLMError(self._error_message(exc)) from exc

    async def complete(
        self,
        model_name: str,
        messages: list[dict[str, str]],
        temperature: float = 0.0,
        top_p: float = 0.8,
        max_tokens: int = 256,
        timeout: float | None = None,
    ) -> str:
        client = self._get_client(model_name)
        api_model = get_api_model(model_name)
        attempts = max(1, Config.llm_max_retries)

        for attempt in range(1, attempts + 1):
            self._ensure_circuit_closed(model_name)
            try:
                async with self._semaphore:
                    response = await client.chat.completions.create(
                        model=api_model,
                        messages=messages,  # type: ignore[arg-type]
                        temperature=temperature,
                        top_p=top_p,
                        max_tokens=max_tokens,
                        stream=False,
                        **({"timeout": timeout} if timeout is not None else {}),
                    )
                self._record_success(model_name)
                inc("asktofit_llm_requests_total", {"model": model_name, "status": "success"})
                if not response.choices:
                    return ""
                return response.choices[0].message.content or ""
            except Exception as exc:
                if self._is_retryable(exc) and attempt < attempts:
                    inc("asktofit_llm_requests_total", {"model": model_name, "status": "retry"})
                    logger.warning(
                        "大模型调用失败，准备重试 model=%s attempt=%d/%d",
                        model_name,
                        attempt,
                        attempts,
                        exc_info=True,
                    )
                    await self._backoff(attempt)
                    continue
                self._record_failure(model_name)
                inc("asktofit_llm_requests_total", {"model": model_name, "status": "error"})
                logger.error(
                    "大模型调用失败 model=%s attempt=%d",
                    model_name,
                    attempt,
                    exc_info=True,
                )
                raise LLMError(self._error_message(exc)) from exc
        return ""

    def reset_circuit(self, model_name: str | None = None) -> None:
        """测试或运维手动恢复熔断。"""
        if model_name is None:
            self._circuits.clear()
            return
        self._circuits.pop(model_name, None)

    async def close(self) -> None:
        for client in self._clients.values():
            try:
                await client.close()  # type: ignore[attr-defined]
            except Exception:
                logger.warning("关闭大模型客户端失败", exc_info=True)
        self._clients.clear()
        self._circuits.clear()


llm_service = LLMService()
