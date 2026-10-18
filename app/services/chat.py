"""RAG 问答编排：意图路由、查询改写、检索、提示词与流式回答。"""

from __future__ import annotations

import asyncio
import time
from collections.abc import AsyncIterator
from typing import Any

from app.core.logger import get_logger
from app.db.session import get_session_maker
from app.models.conversation import ChatMessage, Conversation
from app.models.knowledge_gap import KnowledgeGap
from app.services.cache import answer_key, get_json, set_json
from app.services.intent import classify_intent_detailed
from app.services.llm import LLMError, llm_service
from app.services.metrics import inc
from app.services.rag import (
    build_llm_messages,
    build_rewrite_messages,
    build_sources,
    clean_rewritten,
    has_follow_up_cue,
    should_rewrite,
    trim_history,
)
from app.services.query_strategy import StrategyPlan, query_strategy_service
from app.services.retrieval import retrieval_service
from config import Config

logger = get_logger(__name__)

Event = tuple[str, dict[str, Any]]


def _filter_qualified(hits: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """按显式得分模式过滤：重排序分、向量分或失败降级策略。

    - score_mode=rerank：按 rerank_score 阈值过滤；
    - rerank_failed：严格模式丢弃，显式 vector 模式保留；
    - 未启用重排序：保留向量召回结果。
    """
    if not hits:
        return []
    if any(hit.get("rerank_failed") for hit in hits):
        if Config.rerank_failure_mode == "vector":
            logger.warning(
                "重排序失败，按 rerank_failure_mode=vector 使用向量得分"
            )
            return hits
        logger.warning("重排序失败，按 rerank_failure_mode=drop 丢弃本轮资料")
        return []

    reranked = [hit for hit in hits if "rerank_score" in hit]
    if reranked:
        threshold = Config.rerank_score_threshold
        return [
            hit
            for hit in reranked
            if float(hit.get("rerank_score", 0.0)) >= threshold
        ]
    return hits


async def rewrite_query(
    model_name: str, question: str, history: list[dict[str, str]]
) -> str:
    response = await llm_service.complete(
        model_name,
        build_rewrite_messages(question, history),
        temperature=0.0,
        top_p=0.8,
        max_tokens=160,
    )
    rewritten = clean_rewritten(response, question)
    if rewritten.strip() == question.strip():
        # 模型未完成改写时，用上一轮完整问题补全上下文，避免指代查询召回为空；
        # 跳过同样是追问的历史问题，防止把多个指代串在一起。
        user_questions = [
            message["content"].strip()
            for message in history
            if message.get("role") == "user" and message.get("content", "").strip()
        ]
        context_question = next(
            (item for item in reversed(user_questions) if not has_follow_up_cue(item)),
            user_questions[-1] if user_questions else "",
        )
        if context_question:
            rewritten = f"{context_question} {question.strip()}"[:500]
    return rewritten


async def save_assistant_message(
    conversation_id: int,
    content: str,
    sources: list[dict[str, Any]],
    intent: str,
    rewritten_query: str,
    latency_ms: int,
) -> int:
    async with get_session_maker()() as db:
        message = ChatMessage(
            conversation_id=conversation_id,
            role="assistant",
            content=content,
            sources=sources,
            intent=intent,
            rewritten_query=rewritten_query,
            latency_ms=latency_ms,
        )
        db.add(message)
        conversation = await db.get(Conversation, conversation_id)
        if conversation is not None:
            conversation.message_count += 1
            conversation.last_message = content[:200]
        await db.commit()
        await db.refresh(message)
        return message.id


async def record_knowledge_gap(
    user_id: int,
    conversation_id: int,
    question: str,
    intent: str,
    kb_ids: list[int],
    *,
    intent_confidence: float | None = None,
    intent_source: str | None = None,
    best_score: float | None = None,
    vector_score: float | None = None,
    rerank_score: float | None = None,
    retrieval_mode: str | None = None,
) -> None:
    async with get_session_maker()() as db:
        db.add(
            KnowledgeGap(
                user_id=user_id,
                conversation_id=conversation_id,
                question=question,
                intent=intent,
                intent_confidence=intent_confidence,
                intent_source=intent_source,
                kb_ids=list(kb_ids),
                best_score=best_score,
                vector_score=vector_score,
                rerank_score=rerank_score,
                retrieval_mode=retrieval_mode,
            )
        )
        await db.commit()
    inc("asktofit_knowledge_gaps_total", {"intent": intent})
    logger.info(
        "记录知识缺口 intent=%s mode=%s best=%s vector=%s rerank=%s question=%s",
        intent,
        retrieval_mode,
        best_score,
        vector_score,
        rerank_score,
        question[:80],
    )


async def _persist_assistant(
    conversation_id: int,
    content: str,
    sources: list[dict[str, Any]],
    intent: str,
    rewritten_query: str,
    latency_ms: int,
) -> int | None:
    if not content:
        return None
    try:
        return await save_assistant_message(
            conversation_id, content, sources, intent, rewritten_query, latency_ms
        )
    except Exception:
        logger.exception("保存助手消息失败 conversation_id=%s", conversation_id)
        return None


def _score_summary(hits: list[dict[str, Any]]) -> dict[str, Any]:
    """拆分量纲：重排序分优先作为 best_score，同时保留向量分。"""
    if not hits:
        return {
            "best_score": None,
            "vector_score": None,
            "rerank_score": None,
            "retrieval_mode": "none",
        }

    # 只汇总显式 vector_score：融合后的 score 是 RRF 分，量纲不同不可混用
    vector_values = [
        float(hit["vector_score"])
        for hit in hits
        if hit.get("vector_score") is not None
    ]
    rerank_values = [
        float(hit["rerank_score"]) for hit in hits if "rerank_score" in hit
    ]
    vector_score = max(vector_values) if vector_values else None
    rerank_score = max(rerank_values) if rerank_values else None
    if rerank_score is not None:
        return {
            "best_score": rerank_score,
            "vector_score": vector_score,
            "rerank_score": rerank_score,
            "retrieval_mode": "rerank",
        }
    return {
        "best_score": vector_score,
        "vector_score": vector_score,
        "rerank_score": None,
        "retrieval_mode": "vector",
    }


async def _record_gap_safe(
    user_id: int,
    conversation_id: int,
    question: str,
    intent: str,
    kb_ids: list[int],
    hits: list[dict[str, Any]],
    *,
    intent_confidence: float | None = None,
    intent_source: str | None = None,
) -> None:
    try:
        await record_knowledge_gap(
            user_id,
            conversation_id,
            question,
            intent,
            kb_ids,
            intent_confidence=intent_confidence,
            intent_source=intent_source,
            **_score_summary(hits),
        )
    except Exception:
        logger.exception("记录知识缺口失败 conversation_id=%s", conversation_id)


async def stream_reply(
    *,
    conversation_id: int,
    user_id: int,
    question: str,
    kb_ids: list[int],
    kb_modes: dict[str, list[int]],
    model_name: str,
    temperature: float,
    top_p: float,
    max_tokens: int,
    history_rounds: int,
    history: list[dict[str, str]],
    redis: Any | None = None,
) -> AsyncIterator[Event]:
    """生成 SSE 事件：status / meta / delta / done / error。"""
    started = time.perf_counter()
    intent_result = await classify_intent_detailed(question, redis=redis)
    intent = intent_result.intent
    inc("asktofit_intent_total", {"intent": intent, "source": intent_result.source})
    yield "status", {
        "stage": "retrieving",
        "intent": intent,
        "intent_confidence": round(intent_result.confidence, 4),
        "intent_source": intent_result.source,
    }

    trimmed_history = trim_history(history, history_rounds)
    rewritten_query = question

    if intent != "greeting" and should_rewrite(
        question, trimmed_history, intent=intent
    ):
        try:
            rewritten_query = await rewrite_query(
                model_name, question, trimmed_history
            )
            logger.info(
                "查询改写 original=%s rewritten=%s", question[:60], rewritten_query[:80]
            )
        except LLMError as exc:
            logger.warning("查询改写失败，使用原问题：%s", exc)

    hits: list[dict[str, Any]] = []
    strategy_meta = {
        "retrieval_strategy": "direct",
        "strategy_source": "disabled",
        "retrieval_query_count": 0,
    }
    if intent != "greeting" and kb_modes:
        plan = StrategyPlan(
            strategy="direct",
            queries=(rewritten_query,),
            rerank_query=rewritten_query,
            source="disabled",
        )
        try:
            plan = await query_strategy_service.plan(
                rewritten_query,
                intent=intent,
                model_name=model_name,
                redis=redis,
                has_knowledge=bool(kb_modes),
            )
        except Exception:
            logger.exception(
                "检索策略规划失败，回退直接检索 conversation_id=%s", conversation_id
            )
        strategy_meta = {
            "retrieval_strategy": plan.strategy,
            "strategy_source": plan.source,
            "retrieval_query_count": len(plan.queries),
        }
        logger.debug(
            "检索策略 strategy=%s source=%s queries=%s",
            plan.strategy,
            plan.source,
            [item[:60] for item in plan.queries],
        )
        try:
            hits = await retrieval_service.retrieve(
                rewritten_query,
                kb_modes,
                redis=redis,
                strategy=plan.strategy,
                queries=plan.queries,
                rerank_query=plan.rerank_query,
            )
            hits = await retrieval_service.enrich_hits(hits)
        except Exception:
            logger.exception("知识库检索失败 conversation_id=%s", conversation_id)
            yield "error", {"message": "知识库检索失败，请稍后重试"}
            return

    qualified = _filter_qualified(hits)
    if not qualified and intent != "greeting":
        await _record_gap_safe(
            user_id,
            conversation_id,
            question,
            intent,
            kb_ids,
            hits,
            intent_confidence=intent_result.confidence,
            intent_source=intent_result.source,
        )

    sources = build_sources(qualified)
    yield "meta", {
        "conversation_id": conversation_id,
        "intent": intent,
        "intent_confidence": round(intent_result.confidence, 4),
        "intent_source": intent_result.source,
        "rewritten_query": rewritten_query,
        "sources": sources,
        **strategy_meta,
    }

    messages = build_llm_messages(question, qualified, trimmed_history, intent)
    cache_key = answer_key(
        messages=messages,
        model_name=model_name,
        temperature=temperature,
        top_p=top_p,
        max_tokens=max_tokens,
    )
    cached_answer = await get_json(redis, cache_key, "answer")
    if isinstance(cached_answer, str) and cached_answer.strip():
        content = cached_answer.strip()
        for start in range(0, len(content), 32):
            yield "delta", {"content": content[start : start + 32]}
        latency_ms = int(round((time.perf_counter() - started) * 1000))
        assistant_message_id = await _persist_assistant(
            conversation_id,
            content,
            sources,
            intent,
            rewritten_query,
            latency_ms,
        )
        logger.info(
            "回答命中缓存 conversation_id=%s intent=%s sources=%d latency=%dms",
            conversation_id,
            intent,
            len(sources),
            latency_ms,
        )
        yield "done", {
            "conversation_id": conversation_id,
            "assistant_message_id": assistant_message_id,
            "content": content,
            "sources": sources,
            "intent": intent,
            "intent_confidence": round(intent_result.confidence, 4),
            "intent_source": intent_result.source,
            "rewritten_query": rewritten_query,
            "latency_ms": latency_ms,
            "cache_hit": True,
        }
        return

    parts: list[str] = []
    try:
        async for delta in llm_service.stream_chat(
            model_name,
            messages,
            temperature=temperature,
            top_p=top_p,
            max_tokens=max_tokens,
        ):
            parts.append(delta)
            yield "delta", {"content": delta}
    except asyncio.CancelledError:
        content = "".join(parts).strip()
        await _persist_assistant(
            conversation_id,
            content,
            sources,
            intent,
            rewritten_query,
            int(round((time.perf_counter() - started) * 1000)),
        )
        raise
    except LLMError as exc:
        content = "".join(parts).strip()
        await _persist_assistant(
            conversation_id,
            content,
            sources,
            intent,
            rewritten_query,
            int(round((time.perf_counter() - started) * 1000)),
        )
        yield "error", {"message": str(exc)}
        return
    except Exception:
        logger.exception("流式回答异常 conversation_id=%s", conversation_id)
        yield "error", {"message": "回答生成失败，请稍后重试"}
        return

    content = "".join(parts).strip()
    if not content:
        yield "error", {"message": "模型未返回有效内容，请重试"}
        return

    await set_json(
        redis, cache_key, content, Config.answer_cache_ttl, "answer"
    )
    latency_ms = int(round((time.perf_counter() - started) * 1000))
    assistant_message_id = await _persist_assistant(
        conversation_id,
        content,
        sources,
        intent,
        rewritten_query,
        latency_ms,
    )
    logger.info(
        "回答完成 conversation_id=%s intent=%s sources=%d latency=%dms",
        conversation_id,
        intent,
        len(sources),
        latency_ms,
    )
    yield "done", {
        "conversation_id": conversation_id,
        "assistant_message_id": assistant_message_id,
        "content": content,
        "sources": sources,
        "intent": intent,
        "intent_confidence": round(intent_result.confidence, 4),
        "intent_source": intent_result.source,
        "rewritten_query": rewritten_query,
        "latency_ms": latency_ms,
        "cache_hit": False,
    }
