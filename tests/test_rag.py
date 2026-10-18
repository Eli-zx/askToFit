"""RAG 提示词、上下文与历史裁剪测试。"""

from app.services.rag import (
    build_llm_messages,
    build_rewrite_messages,
    build_sources,
    clean_rewritten,
    format_context,
    should_rewrite,
    trim_history,
)
from config import Config


def _hit(index: int, content: str, score: float = 0.8) -> dict:
    return {
        "chunk_uid": f"uid-{index}",
        "doc_id": index,
        "kb_id": 1,
        "title": f"文档{index}",
        "section": f"章节{index}",
        "page": index,
        "content": content,
        "score": score,
        "rerank_score": score,
    }


def test_format_context_contains_metadata_and_truncates() -> None:
    hits = [_hit(1, "满 199 元包邮。" * 50), _hit(2, "电子发票三个工作日开具。")]
    context = format_context(hits, max_chars=180)

    assert "[资料1]" in context
    assert "标题：文档1" in context
    assert "章节：章节1" in context
    assert "页码：1" in context
    assert len(context) <= 220
    assert "电子发票" not in context or "[资料2]" not in context


def test_format_context_empty() -> None:
    assert "未检索到可靠资料" in format_context([])


def test_build_sources_truncates_content_and_rounds_score() -> None:
    sources = build_sources([_hit(1, "很长的资料内容" * 100, score=0.87654)])

    assert len(sources) == 1
    assert sources[0]["score"] == 0.8765
    assert len(sources[0]["content"]) <= 401
    assert sources[0]["content"].endswith("…")


def test_trim_history_respects_rounds_and_orders() -> None:
    history = [
        {"role": "user", "content": "第一轮问题"},
        {"role": "assistant", "content": "第一轮回答"},
        {"role": "user", "content": "第二轮问题"},
        {"role": "assistant", "content": "第二轮回答"},
        {"role": "user", "content": "第三轮问题"},
        {"role": "assistant", "content": "第三轮回答"},
    ]

    trimmed = trim_history(history, rounds=2)
    assert [item["content"] for item in trimmed] == [
        "第二轮问题",
        "第二轮回答",
        "第三轮问题",
        "第三轮回答",
    ]

    assert trim_history(history, rounds=0) == []


def test_trim_history_drops_orphan_assistant_and_limits_chars() -> None:
    history = [
        {"role": "assistant", "content": "孤立回答"},
        {"role": "user", "content": "问题一" * 100},
        {"role": "assistant", "content": "回答一" * 100},
        {"role": "user", "content": "最新问题"},
        {"role": "assistant", "content": "最新回答"},
    ]

    trimmed = trim_history(history, rounds=3, max_chars=60)
    assert trimmed[0]["role"] == "user"
    assert sum(len(item["content"]) for item in trimmed) <= 60
    assert trimmed[-1]["content"] == "最新回答"


def test_build_llm_messages_orders_and_injects_policy() -> None:
    history = [{"role": "user", "content": "之前的问题"}]
    messages = build_llm_messages(
        "退货要多久？", [_hit(1, "七天无理由退货。")], history, "return_exchange"
    )

    assert messages[0]["role"] == "system"
    assert "退换货" in messages[0]["content"]
    assert "七天无理由退货" in messages[0]["content"]
    assert "只读资料" in messages[0]["content"]
    assert messages[1] == {"role": "user", "content": "之前的问题"}
    assert messages[-1] == {"role": "user", "content": "退货要多久？"}


def test_build_llm_messages_without_context() -> None:
    messages = build_llm_messages("你好", [], [], "greeting")
    assert "未检索到可靠资料" in messages[0]["content"]
    assert "问候" in messages[0]["content"]


def test_should_rewrite_only_for_follow_up(monkeypatch) -> None:
    history = [{"role": "user", "content": "退货政策"}]
    monkeypatch.setattr(Config, "query_rewrite_enabled", True)

    assert should_rewrite("那换货呢？", history)
    assert should_rewrite("这个可以退吗", history)
    assert should_rewrite("短问题", history)
    assert not should_rewrite("开发票要多久？", history)
    assert not should_rewrite("请问电子发票一般在审核通过后多久可以开具？", history)
    assert not should_rewrite("那换货呢？", [])

    monkeypatch.setattr(Config, "query_rewrite_enabled", False)
    assert not should_rewrite("那换货呢？", history)


def test_rewrite_messages_include_dialogue_and_example() -> None:
    messages = build_rewrite_messages(
        "那发票呢？", [{"role": "user", "content": "退货多久到账？"}]
    )
    assert "退货多久到账" in messages[0]["content"]
    assert "那发票呢" in messages[0]["content"]
    assert "只输出改写后的查询" in messages[0]["content"]
    assert "偏远地区是否包邮" in messages[0]["content"]


def test_clean_rewritten() -> None:
    assert clean_rewritten("「七天无理由退货的条件」", "原问题") == "七天无理由退货的条件"
    assert clean_rewritten("  第一行  \n第二行", "原问题") == "第一行"
    assert clean_rewritten("", "原问题") == "原问题"
    assert clean_rewritten("   ", "原问题") == "原问题"
