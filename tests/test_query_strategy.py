"""多策略检索规划：规则优先级、生成回退、缓存与 HyDE 隔离。"""

from __future__ import annotations

import pytest

from app.services import query_strategy as qs
from app.services.query_strategy import QueryStrategyService, _rule_strategy
from config import Config

_MODEL = "deepseekv4-flash"


def test_rule_priority_subquery_over_backtrack() -> None:
    query = "如果尺码不合适怎么办，另外运费和发票分别怎么处理？"
    assert _rule_strategy(query) == "subquery"


def test_rule_backtrack_for_conditional_long_question() -> None:
    query = "如果收到货后发现尺码不合适，这种情况还能不能申请换货呢"
    assert _rule_strategy(query) == "backtrack"


def test_rule_hyde_for_abstract_question() -> None:
    assert _rule_strategy("我该如何选择适合自己的尺码") == "hyde"


def test_short_entity_and_amount_questions_stay_direct() -> None:
    assert _rule_strategy("满99包邮吗") == "direct"
    assert _rule_strategy("退货多少钱") == "direct"
    assert _rule_strategy("M码胸围") == "direct"


def test_multiple_question_marks_trigger_subquery() -> None:
    assert _rule_strategy("包邮吗？多久到？") == "subquery"


async def test_disabled_strategy_returns_direct_without_llm(monkeypatch) -> None:
    monkeypatch.setattr(Config, "strategy_enabled", False)
    called = {"count": 0}

    async def fake_complete(*args, **kwargs):
        called["count"] += 1
        return "不应调用"

    monkeypatch.setattr(qs.llm_service, "complete", fake_complete)
    plan = await QueryStrategyService().plan(
        "如果尺码不合适怎么办，这种情况能换吗", intent="size", model_name=_MODEL
    )

    assert plan.strategy == "direct"
    assert plan.source == "disabled"
    assert plan.queries == ("如果尺码不合适怎么办，这种情况能换吗",)
    assert called["count"] == 0


async def test_greeting_skips_strategy(monkeypatch) -> None:
    monkeypatch.setattr(Config, "strategy_enabled", True)
    plan = await QueryStrategyService().plan(
        "你好", intent="greeting", model_name=_MODEL
    )
    assert plan.strategy == "direct"
    assert plan.source == "disabled"


async def test_subqueries_are_cleaned_deduped_and_limited(monkeypatch) -> None:
    monkeypatch.setattr(Config, "strategy_max_subqueries", 2)
    monkeypatch.setattr(Config, "strategy_max_query_chars", 50)
    raw = "\n".join(
        [
            "1. 满多少包邮？",
            "2. “满多少包邮？”",
            "- 偏远地区运费怎么算？",
            "* 发票怎么开？",
        ]
    )

    async def fake_complete(*args, **kwargs):
        return raw

    monkeypatch.setattr(qs.llm_service, "complete", fake_complete)
    plan = await QueryStrategyService().plan(
        "满多少包邮？还有偏远地区运费和发票怎么处理？",
        intent="logistics",
        model_name=_MODEL,
    )

    assert plan.strategy == "subquery"
    assert plan.queries == (
        "满多少包邮？还有偏远地区运费和发票怎么处理？",
        "满多少包邮？",
        "偏远地区运费怎么算？",
    )
    assert plan.rerank_query == plan.queries[0]


async def test_subquery_generation_failure_falls_back(monkeypatch) -> None:
    async def broken_complete(*args, **kwargs):
        raise TimeoutError("llm timeout")

    monkeypatch.setattr(qs.llm_service, "complete", broken_complete)
    plan = await QueryStrategyService().plan(
        "包邮和发票分别怎么处理？", intent="payment", model_name=_MODEL
    )

    assert plan.strategy == "direct"
    assert plan.source == "fallback"
    assert plan.queries == ("包邮和发票分别怎么处理？",)


async def test_backtrack_invalid_output_falls_back(monkeypatch) -> None:
    query = "如果收到货后发现尺码不合适，这种情况还能不能申请换货呢"

    async def fake_complete(*args, **kwargs):
        return query

    monkeypatch.setattr(qs.llm_service, "complete", fake_complete)
    plan = await QueryStrategyService().plan(
        query, intent="after_sale", model_name=_MODEL
    )

    assert plan.strategy == "direct"
    assert plan.source == "fallback"


async def test_hyde_queries_are_separate_from_rerank_query(monkeypatch) -> None:
    async def fake_complete(*args, **kwargs):
        return "建议根据身高体重和版型选择尺码，必要时咨询客服。"

    monkeypatch.setattr(qs.llm_service, "complete", fake_complete)
    query = "我该如何选择适合自己的尺码"
    plan = await QueryStrategyService().plan(query, intent="size", model_name=_MODEL)

    assert plan.strategy == "hyde"
    assert plan.queries[0] == query
    assert plan.queries[1].startswith("建议根据身高体重")
    # 重排序始终使用原问题，假设答案不进入提示词
    assert plan.rerank_query == query


async def test_hyde_empty_output_falls_back(monkeypatch) -> None:
    async def fake_complete(*args, **kwargs):
        return ""

    monkeypatch.setattr(qs.llm_service, "complete", fake_complete)
    plan = await QueryStrategyService().plan(
        "我该如何选择适合自己的尺码", intent="size", model_name=_MODEL
    )
    assert plan.strategy == "direct"
    assert plan.source == "fallback"


async def test_llm_selector_can_upgrade_direct_rule(monkeypatch) -> None:
    monkeypatch.setattr(Config, "strategy_selector_llm_enabled", True)

    async def fake_select(query, model_name):
        return "hyde"

    async def fake_hyde(query, model_name):
        return "这是一段用于检索的假设答案文本。"

    monkeypatch.setattr(qs, "select_strategy", fake_select)
    monkeypatch.setattr(qs, "generate_hyde", fake_hyde)

    plan = await QueryStrategyService().plan(
        "请问你们家这件春季新款宽松版型棉麻衬衫的尺码表在哪里可以查看呢吗",
        intent="size",
        model_name=_MODEL,
    )

    assert plan.strategy == "hyde"
    assert plan.source == "llm"


async def test_plan_is_cached_in_redis(fake_redis, monkeypatch) -> None:
    calls = {"count": 0}

    async def fake_complete(*args, **kwargs):
        calls["count"] += 1
        return "第一行\n第二行"

    monkeypatch.setattr(qs.llm_service, "complete", fake_complete)
    service = QueryStrategyService()
    query = "包邮和发票分别怎么处理？"

    first = await service.plan(
        query, intent="payment", model_name=_MODEL, redis=fake_redis
    )
    second = await service.plan(
        query, intent="payment", model_name=_MODEL, redis=fake_redis
    )

    assert first.strategy == "subquery"
    assert second == first
    assert calls["count"] == 1


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("direct", "direct"),
        ("HyDE", "hyde"),
        ("子查询", "subquery"),
        ("回溯。", "backtrack"),
        ("unknown", None),
    ],
)
async def test_selector_aliases_and_whitelist(monkeypatch, raw, expected) -> None:
    async def fake_complete(*args, **kwargs):
        return raw

    monkeypatch.setattr(qs.llm_service, "complete", fake_complete)
    assert await qs.select_strategy("问题", _MODEL) == expected
