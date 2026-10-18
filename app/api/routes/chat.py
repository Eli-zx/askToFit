"""对话会话管理、多轮消息与 SSE 流式 RAG 问答接口。"""

from __future__ import annotations

import json
from collections import Counter
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta

import redis.asyncio as aioredis
from fastapi import APIRouter, Depends, HTTPException, Query, status
from fastapi.responses import StreamingResponse
from sqlalchemy import delete, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_user
from app.core.logger import get_logger
from app.core.rate_limit import enforce_rate_limit
from app.core.redis_client import get_redis
from app.db.session import get_db
from app.models.conversation import ChatMessage, Conversation
from app.models.knowledge_base import KnowledgeBase
from app.models.knowledge_gap import KnowledgeGap
from app.models.user import User
from app.schemas.chat import (
    ChatOptionsOut,
    ChatSendRequest,
    ConversationCreate,
    ConversationOut,
    ConversationUpdate,
    KnowledgeGapOut,
    KnowledgeGapStatItem,
    KnowledgeGapStats,
    KnowledgeGapUpdate,
    MessageListOut,
    MessageOut,
    ModelOption,
)
from app.services.chat import stream_reply
from app.services.intent import intent_label
from app.services.llm import llm_service
from config import AVAILABLE_MODELS, Config

logger = get_logger(__name__)

router = APIRouter(prefix="/chat", tags=["智能问答"])

DEFAULT_TITLE = "新对话"


def _now() -> datetime:
    """数据库统一存 naive UTC，避免不同驱动对时区的处理差异。"""
    return datetime.now(UTC).replace(tzinfo=None)


async def _get_conversation(
    conversation_id: int, user: User, db: AsyncSession
) -> Conversation:
    conversation = await db.scalar(
        select(Conversation).where(
            Conversation.id == conversation_id, Conversation.user_id == user.id
        )
    )
    if conversation is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "会话不存在")
    return conversation


async def _validate_kb_ids(
    kb_ids: list[int], user: User, db: AsyncSession
) -> list[KnowledgeBase]:
    unique_ids = list(dict.fromkeys(int(kb_id) for kb_id in kb_ids))
    if not unique_ids:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "请至少选择一个知识库")
    rows = list(
        await db.scalars(
            select(KnowledgeBase).where(
                KnowledgeBase.id.in_(unique_ids),
                or_(
                    KnowledgeBase.owner_id == user.id,
                    KnowledgeBase.is_public.is_(True),
                ),
            )
        )
    )
    mapping = {kb.id: kb for kb in rows}
    missing = [kb_id for kb_id in unique_ids if kb_id not in mapping]
    if missing:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            f"知识库不存在或无权访问：{','.join(map(str, missing))}",
        )
    return [mapping[kb_id] for kb_id in unique_ids]


def _sse(event: str, data: dict) -> str:
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"


# --------------------------------------------------------------------------- #
# 选项与会话管理
# --------------------------------------------------------------------------- #
@router.get("/options", response_model=ChatOptionsOut, summary="可选的模型与参数")
async def get_chat_options() -> ChatOptionsOut:
    models = [
        ModelOption(
            name=name,
            label=meta["label"],
            provider=meta["provider"],
            available=llm_service.is_available(name),
            default=meta.get("default", False),
        )
        for name, meta in AVAILABLE_MODELS.items()
    ]
    return ChatOptionsOut(
        models=models,
        defaults={
            "model_name": Config.model,
            "temperature": Config.temperature_default,
            "top_p": Config.top_p_default,
            "max_tokens": Config.max_tokens_default,
            "history_rounds": Config.history_rounds_default,
        },
        limits={
            "temperature": [0.0, 2.0],
            "top_p": [0.0, 1.0],
            "max_tokens": [32, Config.max_tokens_limit],
            "history_rounds": [0, Config.history_rounds_max],
            "kb_count": 10,
        },
    )


@router.post(
    "/conversations",
    response_model=ConversationOut,
    status_code=status.HTTP_201_CREATED,
    summary="创建对话会话",
)
async def create_conversation(
    payload: ConversationCreate,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
) -> Conversation:
    await _validate_kb_ids(payload.kb_ids, user, db)
    title = (payload.title or "").strip() or DEFAULT_TITLE
    conversation = Conversation(
        user_id=user.id,
        title=title[:120],
        kb_ids=payload.kb_ids,
        model_name=payload.model_name,
        temperature=payload.temperature,
        top_p=payload.top_p,
        max_tokens=payload.max_tokens,
        history_rounds=payload.history_rounds,
    )
    db.add(conversation)
    await db.commit()
    await db.refresh(conversation)
    logger.info(
        "创建会话 conversation_id=%s user=%s kbs=%s model=%s",
        conversation.id,
        user.username,
        conversation.kb_ids,
        conversation.model_name,
    )
    return conversation


@router.get(
    "/conversations", response_model=list[ConversationOut], summary="会话列表"
)
async def list_conversations(
    include_archived: bool = Query(False, description="是否包含已归档会话"),
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
) -> list[Conversation]:
    stmt = select(Conversation).where(Conversation.user_id == user.id)
    if not include_archived:
        stmt = stmt.where(Conversation.archived_at.is_(None))
    rows = await db.scalars(
        stmt.order_by(Conversation.updated_at.desc(), Conversation.id.desc())
    )
    return list(rows)


@router.get(
    "/conversations/{conversation_id}",
    response_model=ConversationOut,
    summary="会话详情",
)
async def get_conversation(
    conversation_id: int,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
) -> Conversation:
    return await _get_conversation(conversation_id, user, db)


@router.patch(
    "/conversations/{conversation_id}",
    response_model=ConversationOut,
    summary="重命名或更新会话配置",
)
async def update_conversation(
    conversation_id: int,
    payload: ConversationUpdate,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
) -> Conversation:
    conversation = await _get_conversation(conversation_id, user, db)
    if payload.kb_ids is not None:
        await _validate_kb_ids(payload.kb_ids, user, db)
        conversation.kb_ids = payload.kb_ids

    fields = ("title", "model_name", "temperature", "top_p", "max_tokens", "history_rounds")
    for field in fields:
        value = getattr(payload, field)
        if value is not None:
            if field == "title":
                value = value.strip()
            setattr(conversation, field, value)

    await db.commit()
    await db.refresh(conversation)
    return conversation


@router.post(
    "/conversations/{conversation_id}/archive",
    response_model=ConversationOut,
    summary="归档会话",
)
async def archive_conversation(
    conversation_id: int,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
) -> Conversation:
    conversation = await _get_conversation(conversation_id, user, db)
    if conversation.archived_at is None:
        conversation.archived_at = _now()
        await db.commit()
        await db.refresh(conversation)
        logger.info("归档会话 conversation_id=%s", conversation.id)
    return conversation


@router.delete(
    "/conversations/{conversation_id}/archive",
    response_model=ConversationOut,
    summary="取消归档",
)
async def unarchive_conversation(
    conversation_id: int,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
) -> Conversation:
    conversation = await _get_conversation(conversation_id, user, db)
    if conversation.archived_at is not None:
        conversation.archived_at = None
        await db.commit()
        await db.refresh(conversation)
        logger.info("取消归档 conversation_id=%s", conversation.id)
    return conversation


@router.delete(
    "/conversations/{conversation_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="删除会话及其消息",
)
async def delete_conversation(
    conversation_id: int,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
) -> None:
    conversation = await _get_conversation(conversation_id, user, db)
    await db.execute(
        delete(ChatMessage).where(ChatMessage.conversation_id == conversation.id)
    )
    await db.execute(delete(Conversation).where(Conversation.id == conversation.id))
    await db.commit()
    logger.info("删除会话 conversation_id=%s", conversation_id)


@router.get(
    "/conversations/{conversation_id}/messages",
    response_model=MessageListOut,
    summary="会话消息记录",
)
async def list_messages(
    conversation_id: int,
    limit: int = Query(50, ge=1, le=200),
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
) -> MessageListOut:
    conversation = await _get_conversation(conversation_id, user, db)
    total = (
        await db.scalar(
            select(func.count())
            .select_from(ChatMessage)
            .where(ChatMessage.conversation_id == conversation.id)
        )
        or 0
    )
    rows = list(
        await db.scalars(
            select(ChatMessage)
            .where(ChatMessage.conversation_id == conversation.id)
            .order_by(ChatMessage.id.desc())
            .limit(limit)
        )
    )
    rows.reverse()
    return MessageListOut(
        total=total, items=[MessageOut.model_validate(row) for row in rows]
    )


# --------------------------------------------------------------------------- #
# 流式问答
# --------------------------------------------------------------------------- #
@router.post(
    "/conversations/{conversation_id}/messages/stream",
    summary="发送问题并流式生成回答（SSE）",
)
async def send_message_stream(
    conversation_id: int,
    payload: ChatSendRequest,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
    redis: aioredis.Redis = Depends(get_redis),
):
    await enforce_rate_limit(redis, scope="chat", identity=str(user.id))
    conversation = await _get_conversation(conversation_id, user, db)
    kbs = await _validate_kb_ids(conversation.kb_ids, user, db)
    question = payload.content.strip()
    if not question:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "问题内容不能为空")

    kb_modes: dict[str, list[int]] = {}
    for kb in kbs:
        kb_modes.setdefault(kb.embedding_mode, []).append(kb.id)

    history: list[dict[str, str]] = []
    rounds = max(0, conversation.history_rounds)
    if rounds > 0:
        rows = list(
            await db.scalars(
                select(ChatMessage)
                .where(ChatMessage.conversation_id == conversation.id)
                .order_by(ChatMessage.id.desc())
                .limit(rounds * 2)
            )
        )
        rows.reverse()
        history = [
            {"role": row.role, "content": row.content}
            for row in rows
            if row.role in ("user", "assistant")
        ]

    is_first_message = conversation.message_count == 0
    user_message = ChatMessage(
        conversation_id=conversation.id, role="user", content=question
    )
    db.add(user_message)
    conversation.message_count += 1
    conversation.last_message = question[:200]
    if is_first_message and conversation.title in (DEFAULT_TITLE, "", None):
        conversation.title = question[:30]
    await db.commit()
    await db.refresh(user_message)

    config_snapshot = {
        "model_name": conversation.model_name,
        "temperature": conversation.temperature,
        "top_p": conversation.top_p,
        "max_tokens": conversation.max_tokens,
        "history_rounds": rounds,
    }
    logger.info(
        "收到提问 conversation_id=%s user=%s question=%s",
        conversation.id,
        user.username,
        question[:80],
    )

    async def event_stream() -> AsyncIterator[str]:
        try:
            async for event, data in stream_reply(
                conversation_id=conversation.id,
                user_id=user.id,
                question=question,
                kb_ids=list(conversation.kb_ids),
                kb_modes=kb_modes,
                history=history,
                redis=redis,
                **config_snapshot,
            ):
                yield _sse(event, data)
        except Exception:
            logger.exception("SSE 问答流异常 conversation_id=%s", conversation_id)
            yield _sse("error", {"message": "回答生成失败，请稍后重试"})

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream; charset=utf-8",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


# --------------------------------------------------------------------------- #
# 知识缺口
# --------------------------------------------------------------------------- #
@router.get(
    "/knowledge-gaps",
    response_model=list[KnowledgeGapOut],
    summary="知识缺口记录",
)
async def list_knowledge_gaps(
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    status_filter: str = Query("all", alias="status", pattern="^(all|open|resolved|ignored)$"),
    intent: str | None = Query(None, max_length=32),
    q: str | None = Query(None, max_length=100),
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
) -> list[KnowledgeGap]:
    stmt = select(KnowledgeGap).where(KnowledgeGap.user_id == user.id)
    if status_filter != "all":
        stmt = stmt.where(KnowledgeGap.status == status_filter)
    if intent:
        stmt = stmt.where(KnowledgeGap.intent == intent)
    if q and q.strip():
        stmt = stmt.where(KnowledgeGap.question.like(f"%{q.strip()}%"))
    rows = await db.scalars(
        stmt.order_by(KnowledgeGap.id.desc()).offset(offset).limit(limit)
    )
    return list(rows)


@router.get(
    "/knowledge-gaps/stats",
    response_model=KnowledgeGapStats,
    summary="知识缺口统计",
)
async def knowledge_gap_stats(
    days: int = Query(14, ge=1, le=90),
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
) -> KnowledgeGapStats:
    rows = list(
        await db.scalars(
            select(KnowledgeGap)
            .where(KnowledgeGap.user_id == user.id)
            .order_by(KnowledgeGap.id.desc())
        )
    )
    status_counter = Counter(gap.status or "open" for gap in rows)
    intent_counter = Counter(gap.intent or "other" for gap in rows)
    question_counter = Counter(gap.question.strip() for gap in rows if gap.question.strip())

    start_date = (_now().date() - timedelta(days=days - 1)).isoformat()
    date_counter: Counter[str] = Counter()
    for gap in rows:
        day = gap.created_at.date().isoformat() if gap.created_at else ""
        if day >= start_date:
            date_counter[day] += 1

    return KnowledgeGapStats(
        total=len(rows),
        open=status_counter.get("open", 0),
        resolved=status_counter.get("resolved", 0),
        ignored=status_counter.get("ignored", 0),
        by_intent=[
            KnowledgeGapStatItem(key=key, label=intent_label(key), count=count)
            for key, count in intent_counter.most_common()
        ],
        by_date=[
            KnowledgeGapStatItem(key=day, label=day, count=count)
            for day, count in sorted(date_counter.items())
        ],
        top_questions=[
            KnowledgeGapStatItem(key=text, label=text, count=count)
            for text, count in question_counter.most_common(10)
        ],
    )


@router.patch(
    "/knowledge-gaps/{gap_id}",
    response_model=KnowledgeGapOut,
    summary="更新知识缺口状态或备注",
)
async def update_knowledge_gap(
    gap_id: int,
    payload: KnowledgeGapUpdate,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
) -> KnowledgeGap:
    gap = await db.scalar(
        select(KnowledgeGap).where(
            KnowledgeGap.id == gap_id, KnowledgeGap.user_id == user.id
        )
    )
    if gap is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "知识缺口不存在")

    if payload.status is not None:
        gap.status = payload.status
        gap.resolved_at = _now() if payload.status == "resolved" else None
    if payload.note is not None:
        gap.note = payload.note.strip() or None

    await db.commit()
    await db.refresh(gap)
    logger.info(
        "更新知识缺口 gap_id=%s status=%s user=%s",
        gap.id,
        gap.status,
        user.username,
    )
    return gap
