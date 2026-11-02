"""Redis 缓存键与版本化失效策略测试。"""

from app.services.cache import (
    GLOBAL_VERSION_KEY,
    PREFIX_KB_VERSION,
    PREFIX_RETRIEVAL,
    answer_key,
    bump_kb_version,
    embedding_key,
    get_global_version,
    get_json,
    get_kb_versions,
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


def test_retrieval_key_sensitive_to_kb_versions() -> None:
    base = retrieval_key("问题", {"hybrid": [1]}, 5, 20, kb_versions={1: 1})
    assert base == retrieval_key("问题", {"hybrid": [1]}, 5, 20, kb_versions={1: 1})
    assert base != retrieval_key("问题", {"hybrid": [1]}, 5, 20, kb_versions={1: 2})
    assert base != retrieval_key(
        "问题", {"hybrid": [1]}, 5, 20, kb_versions={1: 1}, global_version=1
    )


async def test_get_kb_versions_defaults_to_zero(fake_redis) -> None:
    assert await get_kb_versions(fake_redis, [1, 2]) == {1: 0, 2: 0}
    assert await get_global_version(fake_redis) == 0


async def test_bump_kb_version_only_invalidates_target_kb(fake_redis) -> None:
    await fake_redis.set(PREFIX_RETRIEVAL + "a", "1")

    assert await bump_kb_version(fake_redis, 1) == 1
    assert await bump_kb_version(fake_redis, 1) == 2
    assert await bump_kb_version(fake_redis, 2) == 1
    assert await get_kb_versions(fake_redis, [1, 2, 3]) == {1: 2, 2: 1, 3: 0}

    key = f"{PREFIX_KB_VERSION}1"
    assert await fake_redis.ttl(key) > 0
    # 版本化失效不再扫描/删除已有缓存键
    assert await fake_redis.get(PREFIX_RETRIEVAL + "a") == "1"
    assert await get_global_version(fake_redis) == 0


async def test_bump_global_version_when_kb_id_missing(fake_redis) -> None:
    assert await bump_kb_version(fake_redis, None) == 1
    assert await get_global_version(fake_redis) == 1
    assert await fake_redis.get(GLOBAL_VERSION_KEY) == "1"
