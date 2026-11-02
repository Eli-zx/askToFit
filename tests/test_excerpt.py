"""按查询词选摘录的公共逻辑测试（重排序与出处预览共用）。"""

from app.services.excerpt import (
    build_rerank_passage,
    excerpt_text,
    preview_excerpt,
    query_terms,
)


def _hit(content: str) -> dict:
    return {
        "title": "顾客须知与购物服务指南",
        "section": "九、常见问题",
        "content": content,
    }


LONG_CONTENT = (
    "一、积分规则\n"
    "会员每消费 1 元累计 1 积分，生日月双倍。\n"
    "二、门店服务\n"
    "门店提供免费改衣服务，需提前预约。\n"
    "九、常见问题\n"
    "问：怎么转人工？\n"
    "答：在店铺首页、商品页或订单页点击“在线客服”，"
    "在对话框输入“人工”即可转接人工客服。"
)


def test_query_terms_extracts_cjk_bigrams_and_ascii_words() -> None:
    terms = query_terms("转人工 T恤 1234")

    assert {"转人", "人工"} <= terms
    assert "1234" in terms
    assert "恤" in terms


def test_excerpt_text_keeps_question_and_answer_near_tail() -> None:
    excerpt = excerpt_text(LONG_CONTENT, query_terms("转人工"), 80)

    assert "怎么转人工" in excerpt
    assert "转接人工客服" in excerpt
    assert "免费改衣" not in excerpt
    assert len(excerpt) <= 80


def test_excerpt_text_falls_back_to_head_when_no_terms_match() -> None:
    excerpt = excerpt_text(LONG_CONTENT, query_terms("完全不相关的主题"), 40)

    assert excerpt == LONG_CONTENT[:40]


def test_build_rerank_passage_keeps_header_and_budget() -> None:
    passage = build_rerank_passage("转人工", _hit(LONG_CONTENT), 120)

    assert passage.startswith("顾客须知与购物服务指南\n九、常见问题")
    assert "转接人工客服" in passage
    assert len(passage) <= 120


def test_preview_excerpt_marks_truncated_context() -> None:
    preview = preview_excerpt(LONG_CONTENT, "转人工", 80)

    assert preview.startswith("…")
    assert "转接人工客服" in preview
    assert len(preview) <= 82


def test_preview_excerpt_returns_short_text_untouched() -> None:
    assert preview_excerpt("问：怎么转人工？", "转人工", 400) == "问：怎么转人工？"
    assert preview_excerpt("", "转人工", 400) == ""
