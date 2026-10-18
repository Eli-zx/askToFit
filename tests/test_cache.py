"""Redis 缓存键与失效策略测试。"""

from app.services.cache import (
    PREFIX_ANSWER,
    PREFIX_EMBEDDING,
    PREFIX_RETRIEVAL,
    answer_key,
    embedding_key,
    get_json,
    invalidate_prefixes,
    retrieval_key,
    retrieval_config_fingerprint,
    set_json,
)
from config import Config


def test_keys_are_stable_and_sensitive_to_inputs() -> None:
    assert embedding_key("包邮吗") == embedding_key("包邮吗")
    assert embedding_key("包邮吗") != embedding_key("退换吗")

    first = retrieval_key("包邮吗", {"hybrid": [2, 1]}, 5, 20)
    reordered = retrieval_key("包邮吗", {"hybrid": [1, 2]}, 5, 20)
    more_results = retrieval_key("包邮吗", {"hybrid": [1, 2]}, 8, 20)
    assert first == reordered
    assert first != more_results

    base = {
        "messages": [{"role": "user", "content": "你好"}],
        "model_name": "deepseekv4-flash",
        "temperature": 0.3,
        "top_p": 0.8,
        "max_tokens": 512,
    }
    assert answer_key(**base) == answer_key(**base)
    assert answer_key(**base) != answer_key(**{**base, "max_tokens": 1024})


def test_retrieval_key_sensitive_to_queries_strategy_and_bm25() -> None:
    base = retrieval_key(
        "原问题",
        {"hybrid": [1]},
        5,
        20,
        queries=("原问题",),
        strategy="direct",
        bm25_mode="off",
    )
    more_queries = retrieval_key(
        "原问题",
        {"hybrid": [1]},
        5,
        20,
        queries=("原问题", "子问题"),
        strategy="direct",
        bm25_mode="off",
    )
    strategy = retrieval_key(
        "原问题",
        {"hybrid": [1]},
        5,
        20,
        queries=("原问题",),
        strategy="hyde",
        bm25_mode="off",
    )
    bm25 = retrieval_key(
        "原问题",
        {"hybrid": [1]},
        5,
        20,
        queries=("原问题",),
        strategy="direct",
        bm25_mode="fuse",
    )
    assert len({base, more_queries, strategy, bm25}) == 4


def test_retrieval_key_sensitive_to_config_fingerprint(monkeypatch) -> None:
    first = retrieval_key("问题", {"hybrid": [1]}, 5, 20)
    monkeypatch.setattr(Config, "rerank_score_threshold", 0.123)
    second = retrieval_key("问题", {"hybrid": [1]}, 5, 20)
    assert first != second
    assert retrieval_config_fingerprint()["rerank_score_threshold"] == 0.123


async def test_get_and_set_json_roundtrip(fake_redis) -> None:
    await set_json(fake_redis, "asktofit:answer:test", {"text": "包邮"}, 60, "answer")
    assert await get_json(fake_redis, "asktofit:answer:test", "answer") == {
        "text": "包邮"
    }
    assert await get_json(fake_redis, "asktofit:answer:missing", "answer") is None


async def test_invalidate_prefixes_keeps_unrelated_caches(fake_redis) -> None:
    await fake_redis.set(PREFIX_RETRIEVAL + "a", "1")
    await fake_redis.set(PREFIX_ANSWER + "b", "2")
    await fake_redis.set(PREFIX_EMBEDDING + "c", "3")

    deleted = await invalidate_prefixes(fake_redis, (PREFIX_RETRIEVAL, PREFIX_ANSWER))

    assert deleted == 2
    assert await fake_redis.get(PREFIX_RETRIEVAL + "a") is None
    assert await fake_redis.get(PREFIX_ANSWER + "b") is None
    assert await fake_redis.get(PREFIX_EMBEDDING + "c") == "3"
