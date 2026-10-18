"""对话会话管理与 SSE 流式问答 API 测试。"""

import json

import pytest

from app.services import chat as chat_module
from app.services import retrieval as retrieval_module
from app.services.captcha import CAPTCHA_KEY_PREFIX
from app.services.intent import classify_intent_keyword
from app.services.llm import LLMError


async def auth_headers(client, redis, username: str = "alice") -> dict:
    captcha = await client.get("/api/auth/captcha")
    captcha_id = captcha.json()["captcha_id"]
    code = await redis.get(CAPTCHA_KEY_PREFIX + captcha_id)
    register = await client.post(
        "/api/auth/register",
        json={
            "username": username,
            "password": "secret123",
            "confirm_password": "secret123",
            "captcha_id": captcha_id,
            "captcha_code": code,
        },
    )
    assert register.status_code == 201

    captcha = await client.get("/api/auth/captcha")
    captcha_id = captcha.json()["captcha_id"]
    code = await redis.get(CAPTCHA_KEY_PREFIX + captcha_id)
    login = await client.post(
        "/api/auth/login",
        json={
            "username": username,
            "password": "secret123",
            "captcha_id": captcha_id,
            "captcha_code": code,
        },
    )
    assert login.status_code == 200
    return {"Authorization": f"Bearer {login.json()['access_token']}"}


async def create_kb(
    client, headers, name: str = "售前知识库", *, is_public: bool = False
) -> dict:
    response = await client.post(
        "/api/knowledge_bases",
        headers=headers,
        json={
            "name": name,
            "description": "服装售前知识",
            "embedding_mode": "hybrid",
            "chunk_size": 300,
            "chunk_overlap": 60,
            "is_public": is_public,
        },
    )
    assert response.status_code == 201, response.text
    return response.json()


async def create_conversation(client, headers, kb_id: int, **overrides) -> dict:
    payload = {
        "title": "售前咨询",
        "kb_ids": [kb_id],
        "model_name": "deepseekv4-flash",
        "temperature": 0.3,
        "top_p": 0.8,
        "max_tokens": 512,
        "history_rounds": 5,
    }
    payload.update(overrides)
    response = await client.post(
        "/api/chat/conversations", headers=headers, json=payload
    )
    assert response.status_code == 201, response.text
    return response.json()


def parse_sse(body: str) -> list[tuple[str, dict]]:
    events: list[tuple[str, dict]] = []
    for block in body.strip().split("\n\n"):
        if not block.strip():
            continue
        event = None
        data = None
        for line in block.splitlines():
            if line.startswith("event: "):
                event = line[len("event: ") :]
            elif line.startswith("data: "):
                data = json.loads(line[len("data: ") :])
        if event and data is not None:
            events.append((event, data))
    return events


async def send_and_collect(client, headers, conversation_id: int, content: str) -> list:
    async with client.stream(
        "POST",
        f"/api/chat/conversations/{conversation_id}/messages/stream",
        headers=headers,
        json={"content": content},
    ) as response:
        assert response.status_code == 200, await response.aread()
        body = "".join([chunk async for chunk in response.aiter_text()])
    return parse_sse(body)


@pytest.fixture
def rag_stubs(monkeypatch, db_maker):
    monkeypatch.setattr(chat_module, "get_session_maker", lambda: db_maker)
    queries: list[str] = []

    async def fake_retrieve(query, kb_modes, redis=None, **kwargs):
        queries.append(query)
        return [
            {
                "chunk_uid": "uid-1",
                "doc_id": 1,
                "kb_id": 1,
                "chunk_index": 0,
                "title": "物流说明",
                "section": "运费标准",
                "page": 2,
                "content": "单笔订单实付满 199 元包邮。",
                "score": 0.88,
                "rerank_score": 0.88,
            }
        ]

    async def fake_stream(*args, **kwargs):
        for piece in ("您好，", "满 199 元即可包邮。"):
            yield piece

    async def fake_complete(*args, **kwargs):
        return "换货需要满足什么条件"

    async def fake_enrich(hits):
        return hits

    async def fake_intent(question, redis=None):
        # 测试固定使用关键词分类，避免依赖本地 BERT 模型产物
        return classify_intent_keyword(question)

    async def fake_plan(query, **kwargs):
        # 规划器本身由 test_query_strategy.py 覆盖；API 测试固定走直连计划
        return chat_module.StrategyPlan(
            strategy="direct",
            queries=(query,),
            rerank_query=query,
            source="disabled",
        )

    monkeypatch.setattr(chat_module.query_strategy_service, "plan", fake_plan)
    monkeypatch.setattr(chat_module.retrieval_service, "retrieve", fake_retrieve)
    monkeypatch.setattr(chat_module, "classify_intent_detailed", fake_intent)
    monkeypatch.setattr(retrieval_module, "enrich_hits", fake_enrich)
    monkeypatch.setattr(chat_module.llm_service, "stream_chat", fake_stream)
    monkeypatch.setattr(chat_module.llm_service, "complete", fake_complete)
    return queries


async def test_create_list_and_options(client, fake_redis) -> None:
    headers = await auth_headers(client, fake_redis)
    kb = await create_kb(client, headers)

    options = await client.get("/api/chat/options", headers=headers)
    assert options.status_code == 200
    data = options.json()
    names = {model["name"] for model in data["models"]}
    assert "deepseekv4-flash" in names
    assert data["defaults"]["history_rounds"] == 5
    assert data["limits"]["max_tokens"][1] >= 1024

    conversation = await create_conversation(client, headers, kb["id"])
    assert conversation["title"] == "售前咨询"
    assert conversation["kb_ids"] == [kb["id"]]
    assert conversation["message_count"] == 0

    listing = await client.get("/api/chat/conversations", headers=headers)
    assert listing.status_code == 200
    assert [item["id"] for item in listing.json()] == [conversation["id"]]

    invalid_model = await client.post(
        "/api/chat/conversations",
        headers=headers,
        json={"kb_ids": [kb["id"]], "model_name": "not-a-model"},
    )
    assert invalid_model.status_code == 422

    empty_kbs = await client.post(
        "/api/chat/conversations",
        headers=headers,
        json={"kb_ids": [], "model_name": "deepseekv4-flash"},
    )
    assert empty_kbs.status_code == 422


async def test_conversation_requires_owned_kb(client, fake_redis) -> None:
    alice = await auth_headers(client, fake_redis, "alice")
    bob = await auth_headers(client, fake_redis, "bob")
    kb = await create_kb(client, alice)

    response = await client.post(
        "/api/chat/conversations",
        headers=bob,
        json={"kb_ids": [kb["id"]], "model_name": "deepseekv4-flash"},
    )
    assert response.status_code == 400
    assert "无权访问" in response.json()["detail"]


async def test_public_kb_can_be_used_by_other_user(client, fake_redis) -> None:
    alice = await auth_headers(client, fake_redis, "alice")
    bob = await auth_headers(client, fake_redis, "bob")
    kb = await create_kb(client, alice, "公共售前知识库", is_public=True)

    conversation = await create_conversation(client, bob, kb["id"])
    assert conversation["kb_ids"] == [kb["id"]]

    updated = await client.patch(
        f"/api/chat/conversations/{conversation['id']}",
        headers=bob,
        json={"kb_ids": [kb["id"]], "title": "使用公共知识库"},
    )
    assert updated.status_code == 200
    assert updated.json()["kb_ids"] == [kb["id"]]


async def test_private_kb_removed_from_public_scope(client, fake_redis) -> None:
    alice = await auth_headers(client, fake_redis, "alice")
    bob = await auth_headers(client, fake_redis, "bob")
    kb = await create_kb(client, alice, "临时公共库", is_public=True)
    conversation = await create_conversation(client, bob, kb["id"])

    await client.patch(
        f"/api/knowledge_bases/{kb['id']}",
        headers=alice,
        json={"is_public": False},
    )

    response = await client.post(
        "/api/chat/conversations",
        headers=bob,
        json={"kb_ids": [kb["id"]], "model_name": "deepseekv4-flash"},
    )
    assert response.status_code == 400

    update = await client.patch(
        f"/api/chat/conversations/{conversation['id']}",
        headers=bob,
        json={"kb_ids": [kb["id"]]},
    )
    assert update.status_code == 400


async def test_update_and_delete_conversation(client, fake_redis) -> None:
    headers = await auth_headers(client, fake_redis)
    kb = await create_kb(client, headers)
    conversation = await create_conversation(client, headers, kb["id"])

    updated = await client.patch(
        f"/api/chat/conversations/{conversation['id']}",
        headers=headers,
        json={"title": "发票与物流", "temperature": 0.7, "history_rounds": 3},
    )
    assert updated.status_code == 200
    payload = updated.json()
    assert payload["title"] == "发票与物流"
    assert payload["temperature"] == 0.7
    assert payload["history_rounds"] == 3

    deleted = await client.delete(
        f"/api/chat/conversations/{conversation['id']}", headers=headers
    )
    assert deleted.status_code == 204
    missing = await client.get(
        f"/api/chat/conversations/{conversation['id']}", headers=headers
    )
    assert missing.status_code == 404


async def test_stream_answer_persists_messages_and_sources(
    client, fake_redis, rag_stubs
) -> None:
    headers = await auth_headers(client, fake_redis)
    kb = await create_kb(client, headers)
    conversation = await create_conversation(client, headers, kb["id"], title=None)

    events = await send_and_collect(
        client, headers, conversation["id"], "满多少包邮？"
    )
    names = [name for name, _ in events]
    assert names[0] == "status"
    assert "meta" in names
    assert "delta" in names
    assert names[-1] == "done"

    meta = next(data for name, data in events if name == "meta")
    assert meta["sources"][0]["title"] == "物流说明"
    assert meta["intent"] == "logistics"
    assert meta["retrieval_strategy"] == "direct"
    assert meta["strategy_source"] == "disabled"
    assert meta["retrieval_query_count"] == 1

    done = events[-1][1]
    assert done["content"] == "您好，满 199 元即可包邮。"
    assert done["assistant_message_id"] is not None

    messages = await client.get(
        f"/api/chat/conversations/{conversation['id']}/messages", headers=headers
    )
    assert messages.status_code == 200
    payload = messages.json()
    assert payload["total"] == 2
    assert [item["role"] for item in payload["items"]] == ["user", "assistant"]
    assert payload["items"][1]["sources"][0]["chunk_uid"] == "uid-1"

    detail = await client.get(
        f"/api/chat/conversations/{conversation['id']}", headers=headers
    )
    assert detail.json()["title"] == "满多少包邮？"
    assert detail.json()["message_count"] == 2


async def test_follow_up_question_is_rewritten(
    client, fake_redis, rag_stubs, monkeypatch
) -> None:
    headers = await auth_headers(client, fake_redis)
    kb = await create_kb(client, headers)
    conversation = await create_conversation(client, headers, kb["id"])

    await send_and_collect(client, headers, conversation["id"], "满多少包邮？")
    events = await send_and_collect(client, headers, conversation["id"], "那换货呢？")

    meta = next(data for name, data in events if name == "meta")
    assert meta["rewritten_query"] == "换货需要满足什么条件"
    assert rag_stubs[-1] == "换货需要满足什么条件"


async def test_greeting_skips_retrieval(
    client, fake_redis, rag_stubs, monkeypatch
) -> None:
    headers = await auth_headers(client, fake_redis)
    kb = await create_kb(client, headers)
    conversation = await create_conversation(client, headers, kb["id"])

    async def forbidden_retrieve(*args, **kwargs):
        raise AssertionError("问候语不应触发检索")

    async def forbidden_plan(*args, **kwargs):
        raise AssertionError("问候语不应触发策略规划")

    monkeypatch.setattr(
        chat_module.retrieval_service, "retrieve", forbidden_retrieve
    )
    monkeypatch.setattr(
        chat_module.query_strategy_service, "plan", forbidden_plan
    )
    events = await send_and_collect(client, headers, conversation["id"], "你好")

    meta = next(data for name, data in events if name == "meta")
    assert meta["intent"] == "greeting"
    assert meta["sources"] == []
    assert meta["retrieval_strategy"] == "direct"
    assert meta["retrieval_query_count"] == 0
    assert events[-1][0] == "done"


async def test_low_confidence_hits_are_filtered_and_gap_recorded(
    client, fake_redis, rag_stubs, monkeypatch
) -> None:
    headers = await auth_headers(client, fake_redis)
    kb = await create_kb(client, headers)
    conversation = await create_conversation(client, headers, kb["id"])

    async def low_score_retrieve(query, kb_modes, redis=None, **kwargs):
        return [
            {
                "chunk_uid": "uid-low",
                "doc_id": 1,
                "kb_id": kb["id"],
                "title": "无关文档",
                "section": None,
                "page": None,
                "content": "无关内容",
                "score": 0.12,
                "rerank_score": 0.12,
            }
        ]

    monkeypatch.setattr(chat_module.retrieval_service, "retrieve", low_score_retrieve)
    events = await send_and_collect(
        client, headers, conversation["id"], "支持以旧换新吗？"
    )
    meta = next(data for name, data in events if name == "meta")
    assert meta["sources"] == []

    gaps = await client.get("/api/chat/knowledge-gaps", headers=headers)
    assert gaps.status_code == 200
    assert gaps.json()[0]["question"] == "支持以旧换新吗？"


async def test_llm_error_keeps_user_message(
    client, fake_redis, rag_stubs, monkeypatch
) -> None:
    headers = await auth_headers(client, fake_redis)
    kb = await create_kb(client, headers)
    conversation = await create_conversation(client, headers, kb["id"])

    async def failing_stream(*args, **kwargs):
        raise LLMError("模型暂不可用")
        yield ""

    monkeypatch.setattr(chat_module.llm_service, "stream_chat", failing_stream)
    events = await send_and_collect(
        client, headers, conversation["id"], "开发票需要多久？"
    )
    assert events[-1][0] == "error"
    assert "模型暂不可用" in events[-1][1]["message"]

    messages = await client.get(
        f"/api/chat/conversations/{conversation['id']}/messages", headers=headers
    )
    assert messages.json()["total"] == 1
    assert messages.json()["items"][0]["role"] == "user"


async def test_conversation_isolation(client, fake_redis) -> None:
    alice = await auth_headers(client, fake_redis, "alice")
    bob = await auth_headers(client, fake_redis, "bob")
    kb = await create_kb(client, alice)
    conversation = await create_conversation(client, alice, kb["id"])

    response = await client.get(
        f"/api/chat/conversations/{conversation['id']}", headers=bob
    )
    assert response.status_code == 404

    messages = await client.get(
        f"/api/chat/conversations/{conversation['id']}/messages", headers=bob
    )
    assert messages.status_code == 404


# --------------------------------------------------------------------------- #
# 会话归档
# --------------------------------------------------------------------------- #
async def test_archive_and_restore_conversation(client, fake_redis) -> None:
    headers = await auth_headers(client, fake_redis)
    kb = await create_kb(client, headers)
    conversation = await create_conversation(client, headers, kb["id"])
    archived = await create_conversation(
        client, headers, kb["id"], title="待归档会话"
    )

    response = await client.post(
        f"/api/chat/conversations/{archived['id']}/archive", headers=headers
    )
    assert response.status_code == 200
    assert response.json()["archived_at"] is not None

    default_list = await client.get("/api/chat/conversations", headers=headers)
    assert [item["id"] for item in default_list.json()] == [conversation["id"]]

    full_list = await client.get(
        "/api/chat/conversations?include_archived=true", headers=headers
    )
    assert {item["id"] for item in full_list.json()} == {
        conversation["id"],
        archived["id"],
    }

    restored = await client.delete(
        f"/api/chat/conversations/{archived['id']}/archive", headers=headers
    )
    assert restored.status_code == 200
    assert restored.json()["archived_at"] is None


# --------------------------------------------------------------------------- #
# 知识缺口运营
# --------------------------------------------------------------------------- #
async def test_knowledge_gap_filters_stats_and_status(
    client, fake_redis, rag_stubs, monkeypatch
) -> None:
    headers = await auth_headers(client, fake_redis)
    kb = await create_kb(client, headers)
    conversation = await create_conversation(client, headers, kb["id"])

    async def low_score_retrieve(query, kb_modes, redis=None, **kwargs):
        return [
            {
                "chunk_uid": "uid-low",
                "doc_id": 1,
                "kb_id": kb["id"],
                "title": "无关文档",
                "section": None,
                "page": None,
                "content": "无关内容",
                "score": 0.12,
                "vector_score": 0.12,
                "rerank_score": 0.12,
                "score_mode": "rerank",
            }
        ]

    monkeypatch.setattr(chat_module.retrieval_service, "retrieve", low_score_retrieve)
    await send_and_collect(client, headers, conversation["id"], "支持以旧换新吗？")

    opened = await client.get("/api/chat/knowledge-gaps?status=open", headers=headers)
    assert opened.status_code == 200
    gap = opened.json()[0]
    assert gap["status"] == "open"
    assert gap["intent"] == "other"
    assert gap["intent_confidence"] is not None
    assert gap["retrieval_mode"] == "rerank"
    assert gap["vector_score"] == 0.12

    resolved_filter = await client.get(
        "/api/chat/knowledge-gaps?status=resolved", headers=headers
    )
    assert resolved_filter.json() == []

    searched = await client.get(
        "/api/chat/knowledge-gaps?q=以旧换新", headers=headers
    )
    assert len(searched.json()) == 1

    updated = await client.patch(
        f"/api/chat/knowledge-gaps/{gap['id']}",
        headers=headers,
        json={"status": "resolved", "note": "已补充售后政策文档"},
    )
    assert updated.status_code == 200
    assert updated.json()["status"] == "resolved"
    assert updated.json()["note"] == "已补充售后政策文档"
    assert updated.json()["resolved_at"] is not None

    stats = await client.get("/api/chat/knowledge-gaps/stats", headers=headers)
    assert stats.status_code == 200
    payload = stats.json()
    assert payload["total"] == 1
    assert payload["resolved"] == 1
    assert payload["open"] == 0
    assert payload["top_questions"][0]["key"] == "支持以旧换新吗？"

    missing = await client.patch(
        "/api/chat/knowledge-gaps/9999", headers=headers, json={"status": "ignored"}
    )
    assert missing.status_code == 404


# --------------------------------------------------------------------------- #
# 回答缓存
# --------------------------------------------------------------------------- #
async def test_repeated_question_replays_cached_answer(
    client, fake_redis, rag_stubs, monkeypatch
) -> None:
    headers = await auth_headers(client, fake_redis)
    kb = await create_kb(client, headers)
    first = await create_conversation(client, headers, kb["id"])
    second = await create_conversation(client, headers, kb["id"])

    stream_calls = {"count": 0}

    async def counting_stream(*args, **kwargs):
        stream_calls["count"] += 1
        yield "您好，"
        yield "满 199 元即可包邮。"

    monkeypatch.setattr(chat_module.llm_service, "stream_chat", counting_stream)

    first_events = await send_and_collect(
        client, headers, first["id"], "满多少包邮？"
    )
    assert first_events[-1][1]["cache_hit"] is False

    second_events = await send_and_collect(
        client, headers, second["id"], "满多少包邮？"
    )
    assert second_events[-1][1]["cache_hit"] is True
    assert second_events[-1][1]["content"] == "您好，满 199 元即可包邮。"
    assert stream_calls["count"] == 1
    assert len(rag_stubs) == 2  # 两次请求均在缓存回答前完成检索


# --------------------------------------------------------------------------- #
# 检索策略接入
# --------------------------------------------------------------------------- #
async def test_plan_failure_falls_back_to_direct_retrieval(
    client, fake_redis, rag_stubs, monkeypatch
) -> None:
    headers = await auth_headers(client, fake_redis)
    kb = await create_kb(client, headers)
    conversation = await create_conversation(client, headers, kb["id"])

    async def broken_plan(*args, **kwargs):
        raise RuntimeError("planner down")

    captured: dict = {}

    async def capture_retrieve(query, kb_modes, redis=None, **kwargs):
        captured.update(kwargs)
        captured["query"] = query
        return list(await _sample_hits())

    monkeypatch.setattr(chat_module.query_strategy_service, "plan", broken_plan)
    monkeypatch.setattr(
        chat_module.retrieval_service, "retrieve", capture_retrieve
    )

    events = await send_and_collect(
        client, headers, conversation["id"], "满多少包邮？"
    )
    meta = next(data for name, data in events if name == "meta")

    assert meta["retrieval_strategy"] == "direct"
    assert meta["strategy_source"] == "disabled"
    assert meta["retrieval_query_count"] == 1
    assert captured["strategy"] == "direct"
    assert captured["queries"] == ("满多少包邮？",)
    assert captured["rerank_query"] == "满多少包邮？"
    assert events[-1][0] == "done"


async def _sample_hits() -> list[dict]:
    return [
        {
            "chunk_uid": "uid-1",
            "doc_id": 1,
            "kb_id": 1,
            "chunk_index": 0,
            "title": "物流说明",
            "section": "运费标准",
            "page": 2,
            "content": "单笔订单实付满 199 元包邮。",
            "score": 0.88,
            "rerank_score": 0.88,
        }
    ]
