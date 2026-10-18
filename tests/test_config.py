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
