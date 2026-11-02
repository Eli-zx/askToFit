"""问答编排中的管理员优先级透传测试。"""

from __future__ import annotations

from types import SimpleNamespace

from app.services import chat as chat_module


async def test_rewrite_query_forwards_priority(monkeypatch) -> None:
    captured: dict[str, object] = {}

    async def fake_complete(*args, **kwargs):
        captured.update(kwargs)
        return "完整问题"

    monkeypatch.setattr(chat_module.llm_service, "complete", fake_complete)

    await chat_module.rewrite_query(
        "deepseekv4-flash",
        "那这件呢？",
        [{"role": "user", "content": "衬衫怎么洗？"}],
        "admin",
    )

    assert captured["priority"] == "admin"


async def test_stream_reply_forwards_admin_priority_to_every_stage(monkeypatch) -> None:
    calls: dict[str, object] = {}

    async def fake_intent(question, redis=None):
        return SimpleNamespace(intent="size", confidence=0.93, source="test")

    async def fake_rewrite(model_name, question, history, priority="normal"):
        calls["rewrite"] = priority
        return "衬衫尺码怎么选"

    async def fake_plan(query, *, priority="normal", **kwargs):
        calls["plan"] = priority
        return chat_module.StrategyPlan(
            strategy="direct",
            queries=(query,),
            rerank_query=query,
            source="test",
        )

    async def fake_retrieve(query, kb_modes, **kwargs):
        calls["retrieve"] = kwargs.get("priority")
        return [
            {
                "chunk_uid": "uid-1",
                "doc_id": 1,
                "kb_id": 1,
                "chunk_index": 0,
                "title": "尺码说明",
                "content": "胸围 100cm 建议选择 L 码。",
                "score": 0.9,
                "rerank_score": 0.9,
            }
        ]

    async def fake_enrich(hits):
        return hits

    async def fake_stream_chat(*args, **kwargs):
        calls["stream"] = kwargs.get("priority")
        yield "建议选择 L 码。"

    async def fake_get_json(*args, **kwargs):
        return None

    async def fake_set_json(*args, **kwargs):
        return None

    async def fake_persist(*args, **kwargs):
        return 1

    monkeypatch.setattr(chat_module, "classify_intent_detailed", fake_intent)
    monkeypatch.setattr(chat_module, "should_rewrite", lambda *args, **kwargs: True)
    monkeypatch.setattr(chat_module, "rewrite_query", fake_rewrite)
    monkeypatch.setattr(chat_module.query_strategy_service, "plan", fake_plan)
    monkeypatch.setattr(chat_module.retrieval_service, "retrieve", fake_retrieve)
    monkeypatch.setattr(chat_module.retrieval_service, "enrich_hits", fake_enrich)
    monkeypatch.setattr(chat_module.llm_service, "stream_chat", fake_stream_chat)
    monkeypatch.setattr(chat_module, "get_json", fake_get_json)
    monkeypatch.setattr(chat_module, "set_json", fake_set_json)
    monkeypatch.setattr(chat_module, "_persist_assistant", fake_persist)
    monkeypatch.setattr(chat_module, "build_sources", lambda hits, **kwargs: [])

    events = [
        event
        async for event in chat_module.stream_reply(
            conversation_id=1,
            user_id=1,
            question="我 100cm 胸围穿什么码？",
            kb_ids=[1],
            kb_modes={"hybrid": [1]},
            model_name="deepseekv4-flash",
            temperature=0.3,
            top_p=0.8,
            max_tokens=1024,
            history_rounds=5,
            history=[],
            redis=None,
            priority="admin",
        )
    ]

    assert calls == {
        "rewrite": "admin",
        "plan": "admin",
        "retrieve": "admin",
        "stream": "admin",
    }
    assert events[-1][0] == "done"
    assert events[-1][1]["content"] == "建议选择 L 码。"
