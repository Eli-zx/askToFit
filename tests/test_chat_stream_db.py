"""SSE 流式问答在返回前释放数据库连接，并区分管理员优先级。"""

from __future__ import annotations

import pytest

from app.api.routes import chat as chat_routes
from app.models.conversation import Conversation
from app.models.knowledge_base import KnowledgeBase
from app.models.user import User
from app.schemas.chat import ChatSendRequest


async def _seed(db_maker, *, is_admin: bool) -> tuple[User, Conversation]:
    async with db_maker() as db:
        user = User(
            username="admin" if is_admin else "normal",
            password_hash="x",
            is_admin=is_admin,
        )
        db.add(user)
        await db.flush()
        kb = KnowledgeBase(
            owner_id=user.id,
            name="服装知识库",
            embedding_mode="hybrid",
            is_public=True,
        )
        db.add(kb)
        await db.flush()
        conversation = Conversation(
            user_id=user.id,
            title="新对话",
            kb_ids=[kb.id],
        )
        db.add(conversation)
        await db.commit()
        await db.refresh(user)
        await db.refresh(conversation)
        return user, conversation


@pytest.fixture
def patched_stream(monkeypatch):
    calls: dict[str, object] = {}

    async def fake_stream_reply(**kwargs):
        calls.update(kwargs)
        yield "done", {
            "conversation_id": kwargs["conversation_id"],
            "content": "ok",
            "sources": [],
        }

    monkeypatch.setattr(chat_routes, "stream_reply", fake_stream_reply)
    return calls


async def test_stream_closes_request_session_before_response(
    db_maker, fake_redis, monkeypatch, patched_stream
) -> None:
    hits: list[str] = []

    async def fake_rate_limit(*args, **kwargs):
        hits.append(kwargs.get("scope", ""))

    monkeypatch.setattr(chat_routes, "enforce_rate_limit", fake_rate_limit)
    user, conversation = await _seed(db_maker, is_admin=False)

    async with db_maker() as db:
        response = await chat_routes.send_message_stream(
            conversation.id,
            ChatSendRequest(content="这件衬衫怎么洗？"),
            db=db,
            user=user,
            redis=fake_redis,
        )

        # 返回 StreamingResponse 之前连接已经归还，流式生成器不再持有事务
        assert db.in_transaction() is False
        # 依赖清理阶段会再次 close，必须幂等
        await db.close()

        chunks = [chunk async for chunk in response.body_iterator]
        body = "".join(
            chunk.decode("utf-8") if isinstance(chunk, bytes) else chunk
            for chunk in chunks
        )

    assert "event: done" in body
    assert hits == ["chat"]
    assert patched_stream["priority"] == "normal"
    assert patched_stream["conversation_id"] == conversation.id


async def test_admin_stream_skips_chat_rate_limit(
    db_maker, fake_redis, monkeypatch, patched_stream
) -> None:
    calls: list[str] = []

    async def fake_rate_limit(*args, **kwargs):
        calls.append(kwargs.get("scope", ""))

    monkeypatch.setattr(chat_routes, "enforce_rate_limit", fake_rate_limit)
    user, conversation = await _seed(db_maker, is_admin=True)

    async with db_maker() as db:
        response = await chat_routes.send_message_stream(
            conversation.id,
            ChatSendRequest(content="这件衬衫怎么洗？"),
            db=db,
            user=user,
            redis=fake_redis,
        )
        [chunk async for chunk in response.body_iterator]

    assert calls == []
    assert patched_stream["priority"] == "admin"
