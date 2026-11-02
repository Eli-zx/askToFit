"""配置实例化与环境变量解析测试。"""

from pathlib import Path

import pytest

from config import BASE_DIR, Settings


def test_env_overrides_are_applied_at_instantiation(monkeypatch) -> None:
    monkeypatch.setenv("RETRIEVAL_K", "7")
    monkeypatch.setenv("INTENT_ENABLED", "false")
    monkeypatch.setenv("RERANK_FAILURE_MODE", "vector")
    monkeypatch.setenv("INTENT_MODEL_PATH", "models/custom-intent")

    settings = Settings()

    assert settings.retrieval_k == 7
    assert settings.intent_enabled is False
    assert settings.rerank_failure_mode == "vector"
    assert Path(settings.intent_model_path) == BASE_DIR / "models" / "custom-intent"


def test_invalid_env_value_fails_fast(monkeypatch) -> None:
    monkeypatch.setenv("RETRIEVAL_K", "not-a-number")
    with pytest.raises(ValueError, match="RETRIEVAL_K"):
        Settings()


def test_invalid_failure_mode_is_rejected(monkeypatch) -> None:
    monkeypatch.setenv("RERANK_FAILURE_MODE", "silent")
    with pytest.raises(ValueError, match="RERANK_FAILURE_MODE"):
        Settings()


def test_relative_model_paths_resolve_under_project(monkeypatch) -> None:
    monkeypatch.setenv("BGE_M3_PATH", "models/bge-m3")
    monkeypatch.setenv("BGE_RERANKER_PATH", "models/bge-reranker-v2-m3")

    settings = Settings()

    assert settings.bge_m3 == str(BASE_DIR / "models" / "bge-m3")
    assert settings.bge_reranker == str(BASE_DIR / "models" / "bge-reranker-v2-m3")


def test_strategy_and_bm25_env_are_parsed(monkeypatch) -> None:
    monkeypatch.setenv("STRATEGY_ENABLED", "false")
    monkeypatch.setenv("STRATEGY_MAX_SUBQUERIES", "5")
    monkeypatch.setenv("STRATEGY_HYDE_MAX_CHARS", "180")
    monkeypatch.setenv("BM25_MODE", "fallback")
    monkeypatch.setenv("BM25_K1", "2.0")
    monkeypatch.setenv("RRF_K", "42")
    monkeypatch.setenv("RETRIEVAL_DEGRADED_CACHE_TTL", "15")

    settings = Settings()

    assert settings.strategy_enabled is False
    assert settings.strategy_max_subqueries == 5
    assert settings.strategy_hyde_max_chars == 180
    assert settings.bm25_mode == "fallback"
    assert settings.bm25_k1 == 2.0
    assert settings.rrf_k == 42
    assert settings.retrieval_degraded_cache_ttl == 15


def test_rerank_tuning_env_is_parsed(monkeypatch) -> None:
    monkeypatch.setenv("RERANK_CANDIDATE_K", "6")
    monkeypatch.setenv("RERANK_PASSAGE_CHARS", "240")
    monkeypatch.setenv("RERANK_MAX_LENGTH", "192")
    monkeypatch.setenv("RERANK_TIMEOUT_SECONDS", "4.5")
    monkeypatch.setenv("RERANK_BATCH_SIZE", "2")

    settings = Settings()

    assert settings.rerank_candidate_k == 6
    assert settings.rerank_passage_chars == 240
    assert settings.rerank_max_length == 192
    assert settings.rerank_timeout_seconds == 4.5
    assert settings.rerank_batch_size == 2


def test_concurrency_and_capacity_env_is_parsed(monkeypatch) -> None:
    monkeypatch.setenv("PASSWORD_HASH_CONCURRENCY", "3")
    monkeypatch.setenv("LLM_QUEUE_TIMEOUT_SECONDS", "2.5")
    monkeypatch.setenv("RERANK_MAX_CONCURRENT", "2")
    monkeypatch.setenv("BM25_MAX_CONCURRENT_SEARCHES", "4")
    monkeypatch.setenv("BM25_MAX_TOTAL_CHUNKS", "8000")
    monkeypatch.setenv("BM25_MAX_INDEXED_KBS", "5")
    monkeypatch.setenv("RETRIEVAL_SINGLEFLIGHT", "false")
    monkeypatch.setenv("CACHE_TTL_JITTER", "0.25")

    settings = Settings()

    assert settings.password_hash_concurrency == 3
    assert settings.llm_queue_timeout_seconds == 2.5
    assert settings.rerank_max_concurrent == 2
    assert settings.bm25_max_concurrent_searches == 4
    assert settings.bm25_max_total_chunks == 8000
    assert settings.bm25_max_indexed_kbs == 5
    assert settings.retrieval_singleflight is False
    assert settings.cache_ttl_jitter == 0.25


def test_demo_capacity_and_admin_channel_env_is_parsed(monkeypatch) -> None:
    monkeypatch.setenv("DB_POOL_SIZE", "20")
    monkeypatch.setenv("DB_MAX_OVERFLOW", "40")
    monkeypatch.setenv("DB_POOL_TIMEOUT", "8")
    monkeypatch.setenv("RATE_LIMIT_REGISTER_REQUESTS", "500")
    monkeypatch.setenv("MAX_CONCURRENT_LLM", "16")
    monkeypatch.setenv("LLM_ADMIN_SLOTS", "4")
    monkeypatch.setenv("LLM_ADMIN_QUEUE_TIMEOUT_SECONDS", "30")
    monkeypatch.setenv(
        "CORS_ORIGINS",
        "https://demo.example.com, http://localhost:4173,http://localhost:4173",
    )

    settings = Settings()

    assert settings.db_pool_size == 20
    assert settings.db_max_overflow == 40
    assert settings.db_pool_timeout == 8.0
    assert settings.rate_limit_register_requests == 500
    assert settings.max_concurrent_llm == 16
    assert settings.llm_admin_slots == 4
    assert settings.llm_admin_queue_timeout_seconds == 30.0
    assert settings.cors_origins == (
        "https://demo.example.com",
        "http://localhost:4173",
    )


def test_eval_min_meaningful_samples_env_is_parsed(monkeypatch) -> None:
    monkeypatch.setenv("EVAL_MIN_MEANINGFUL_SAMPLES", "25")

    settings = Settings()

    assert settings.eval_min_meaningful_samples == 25


def test_model_warmup_interval_env_is_parsed(monkeypatch) -> None:
    monkeypatch.setenv("MODEL_WARMUP_INTERVAL_SECONDS", "120")
    monkeypatch.setenv("STRATEGY_TIMEOUT_SECONDS", "3.5")

    settings = Settings()

    assert settings.model_warmup_interval_seconds == 120.0
    assert settings.strategy_timeout_seconds == 3.5


def test_admin_usernames_split_deduplicate_and_trim(monkeypatch) -> None:
    monkeypatch.setenv("ADMIN_USERNAMES", " Alice, bob ,bob,, ")

    settings = Settings()

    assert settings.admin_usernames == ("Alice", "bob")


@pytest.mark.parametrize(
    ("name", "value", "field"),
    [
        ("BM25_MODE", "hybrid", "BM25_MODE"),
        ("BM25_B", "1.5", "BM25_B"),
        ("BM25_MIN_QUERY_COVERAGE", "-0.1", "BM25_MIN_QUERY_COVERAGE"),
        ("STRATEGY_MAX_SUBQUERIES", "0", "STRATEGY_MAX_SUBQUERIES"),
        ("RRF_K", "0", "RRF_K"),
        ("RETRIEVAL_DEGRADED_CACHE_TTL", "-1", "RETRIEVAL_DEGRADED_CACHE_TTL"),
        ("MODEL_WARMUP_INTERVAL_SECONDS", "-1", "MODEL_WARMUP_INTERVAL_SECONDS"),
        ("RERANK_CANDIDATE_K", "0", "RERANK_CANDIDATE_K"),
        ("RERANK_PASSAGE_CHARS", "10", "RERANK_PASSAGE_CHARS"),
        ("RERANK_MAX_LENGTH", "16", "RERANK_MAX_LENGTH"),
        ("RERANK_TIMEOUT_SECONDS", "-1", "RERANK_TIMEOUT_SECONDS"),
        ("RERANK_BATCH_SIZE", "0", "RERANK_BATCH_SIZE"),
        ("RERANK_MAX_CONCURRENT", "0", "RERANK_MAX_CONCURRENT"),
        ("BM25_MAX_CONCURRENT_SEARCHES", "0", "BM25_MAX_CONCURRENT_SEARCHES"),
        ("BM25_MAX_TOTAL_CHUNKS", "0", "BM25_MAX_TOTAL_CHUNKS"),
        ("BM25_MAX_INDEXED_KBS", "0", "BM25_MAX_INDEXED_KBS"),
        ("CACHE_TTL_JITTER", "1.0", "CACHE_TTL_JITTER"),
        ("PASSWORD_HASH_CONCURRENCY", "0", "PASSWORD_HASH_CONCURRENCY"),
        ("LLM_QUEUE_TIMEOUT_SECONDS", "-1", "LLM_QUEUE_TIMEOUT_SECONDS"),
        ("LLM_ADMIN_QUEUE_TIMEOUT_SECONDS", "-1", "LLM_ADMIN_QUEUE_TIMEOUT_SECONDS"),
        ("LLM_ADMIN_SLOTS", "0", "LLM_ADMIN_SLOTS"),
        ("MAX_CONCURRENT_LLM", "0", "MAX_CONCURRENT_LLM"),
        ("DB_POOL_SIZE", "0", "DB_POOL_SIZE"),
        ("DB_MAX_OVERFLOW", "-1", "DB_MAX_OVERFLOW"),
        ("DB_POOL_TIMEOUT", "0", "DB_POOL_TIMEOUT"),
        ("RATE_LIMIT_REGISTER_REQUESTS", "0", "RATE_LIMIT_REGISTER_REQUESTS"),
        ("EVAL_MIN_MEANINGFUL_SAMPLES", "0", "EVAL_MIN_MEANINGFUL_SAMPLES"),
    ],
)
def test_invalid_new_settings_are_rejected(monkeypatch, name, value, field) -> None:
    monkeypatch.setenv(name, value)
    with pytest.raises(ValueError, match=field):
        Settings()


def test_validate_model_paths_reports_missing(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("VALIDATE_MODELS_ON_STARTUP", "true")
    monkeypatch.setenv("BGE_M3_PATH", str(tmp_path / "missing-bge"))
    monkeypatch.setenv("BGE_RERANKER_PATH", str(tmp_path / "missing-reranker"))
    monkeypatch.setenv("INTENT_MODEL_PATH", str(tmp_path / "missing-intent"))

    settings = Settings()
    with pytest.raises(RuntimeError) as exc_info:
        settings.validate_model_paths()

    message = str(exc_info.value)
    assert "missing-bge" in message
    assert "config.py:Settings.validate_model_paths" in message
