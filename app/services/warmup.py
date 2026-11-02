"""本地模型预热与空闲保活。

启动时不仅加载权重，还各跑一次极短推理；服务空闲较久后同样重复一次，
避免首个请求承担模型换入、线程池与 CPU 频率恢复带来的冷启动开销。
"""

from __future__ import annotations

import asyncio
import time

from app.core.logger import get_logger
from config import Config

logger = get_logger(__name__)


async def warmup_models() -> None:
    """依次预热意图、向量与重排序模型；单个失败不阻断其余模型。"""
    from app.services.embedding import embedding_service
    from app.services.intent_bert import intent_bert_classifier
    from app.services.reranker import reranker_service

    targets = (
        ("intent-bert", intent_bert_classifier.warmup),
        ("bge-m3", embedding_service.warmup),
        ("bge-reranker", reranker_service.warmup),
    )
    for name, warmup in targets:
        started = time.perf_counter()
        try:
            await warmup()
            logger.info(
                "模型预热完成 name=%s elapsed=%.2fs（app/services/warmup.py:warmup_models）",
                name,
                time.perf_counter() - started,
            )
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception(
                "模型预热失败 name=%s，将在首次使用时重试"
                "（app/services/warmup.py:warmup_models）",
                name,
            )


async def keep_warm_loop(stop_event: asyncio.Event) -> None:
    """按配置间隔重复预热，直到应用关闭；间隔为 0 时直接退出。"""
    interval = float(Config.model_warmup_interval_seconds)
    if interval <= 0:
        logger.info("模型保活已关闭（MODEL_WARMUP_INTERVAL_SECONDS=0）")
        return

    while not stop_event.is_set():
        try:
            await asyncio.wait_for(stop_event.wait(), timeout=interval)
            return
        except asyncio.TimeoutError:
            pass

        started = time.perf_counter()
        try:
            await warmup_models()
            logger.info(
                "模型保活完成 interval=%.0fs elapsed=%.2fs"
                "（app/services/warmup.py:keep_warm_loop）",
                interval,
                time.perf_counter() - started,
            )
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("模型保活失败（app/services/warmup.py:keep_warm_loop）")
