#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
@Author  : Vincent
@Time    : 2026/7/9 21:39
@File    : config.py
@Function : 全局配置。所有可变参数均在实例化时读取环境变量，支持 .env 覆盖。
"""

from __future__ import annotations

import os
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent

# .env 不覆盖进程中已存在的同名环境变量，便于 CI / 容器注入
load_dotenv(BASE_DIR / ".env", override=False)


def _env_path(name: str, default: Path) -> Path:
    value = os.getenv(name)
    if value is None or not value.strip():
        return default
    path = Path(value.strip())
    return path if path.is_absolute() else BASE_DIR / path


def _split_csv(value: str) -> tuple[str, ...]:
    """逗号分隔配置：去空白、去空项、保序去重。"""
    seen: set[str] = set()
    items: list[str] = []
    for raw in value.split(","):
        item = raw.strip()
        if item and item not in seen:
            seen.add(item)
            items.append(item)
    return tuple(items)


@dataclass
class Settings:
    """运行期配置。

    字段默认值只描述“无环境变量时的行为”，真正的环境变量读取发生在
    ``__post_init__``，因此测试可以直接构造 ``Settings()`` 并在 monkeypatch
    环境变量后验证解析结果，而不会被模块导入时机锁死。
    """

    # ===== 应用 =====
    app_name: str = "AskToFit 售前智能导购"
    app_version: str = "0.2.0"
    api_prefix: str = "/api"
    debug: bool = False

    # ===== 路径 =====
    base_dir: Path = BASE_DIR
    data_dir: Path = BASE_DIR / "data"
    upload_dir: Path = BASE_DIR / "uploads"
    log_dir: Path = BASE_DIR / "logs"
    models_dir: Path = BASE_DIR / "models"

    # ===== 本地向量模型（默认相对项目 models/ 目录） =====
    bge_m3: str = str(BASE_DIR / "models" / "bge-m3")
    bge_reranker: str = str(BASE_DIR / "models" / "bge-reranker-v2-m3")
    embedding_device: str = "cpu"
    embedding_dim: int = 1024
    embedding_batch_size: int = 16
    validate_models_on_startup: bool = True

    # ===== MySQL =====
    host: str = "localhost"
    port: int = 3306
    user: str = "root"
    password: str = "123456"
    database: str = "asktofit"

    # ===== Redis =====
    redis_host: str = "localhost"
    redis_port: int = 6379
    redis_password: str = "1234"
    db: int = 0

    # ===== Milvus =====
    milvus_host: str = "localhost"
    milvus_port: int = 19530
    database_name: str = "asktofit"
    collection_name: str = "fitrag"

    # ===== 大模型 =====
    model: str = "deepseekv4-flash"
    deepseek_api_key: str = ""
    deepseek_base_url: str = "https://api.deepseek.com/v1"
    dashscope_api_key: str = ""
    dashscope_base_url: str = "https://dashscope.aliyuncs.com/compatible-mode/v1"

    # ===== 安全 =====
    jwt_secret: str = "asktofit-dev-secret-change-me-at-least-32-bytes"
    jwt_algorithm: str = "HS256"
    access_token_expire_minutes: int = 1440
    captcha_ttl_seconds: int = 300
    captcha_length: int = 4
    bcrypt_rounds: int = 12

    # ===== 上传 =====
    max_upload_mb: int = 50
    allowed_extensions: tuple[str, ...] = (
        "pdf",
        "ppt",
        "pptx",
        "md",
        "markdown",
        "doc",
        "docx",
        "png",
        "jpg",
        "jpeg",
    )

    # ===== 文档切块 =====
    chunk_size: int = 500
    chunk_overlap: int = 80
    min_chunk_size: int = 80
    parent_chunk_size: int = 1200

    # ===== 文档判重与更新 =====
    doc_dedup_similarity: float = 0.90
    doc_dedup_simhash_max_distance: int = 10
    chunk_near_dup_similarity: float = 0.97
    retrieval_dedup_similarity: float = 0.95

    # ===== MinerU 深度解析（Docker） =====
    mineru_image: str = "asktofit-mineru:cpu"
    mineru_timeout_seconds: int = 900
    mineru_max_concurrency: int = 1
    mineru_memory: str = "8g"

    # ===== 检索 =====
    retrieval_k: int = 5
    candidate_k: int = 20
    dense_weight: float = 0.7
    sparse_weight: float = 0.3
    rrf_k: int = 60

    # ===== 多策略检索 =====
    strategy_enabled: bool = True
    strategy_selector_llm_enabled: bool = False
    strategy_selector_model: str = ""
    strategy_timeout_seconds: float = 8.0
    strategy_max_subqueries: int = 3
    strategy_max_query_chars: int = 200
    strategy_hyde_max_chars: int = 240
    strategy_cache_ttl: int = 3600

    # ===== BM25 词法召回 =====
    bm25_enabled: bool = True
    bm25_mode: str = "fuse"
    bm25_top_k: int = 20
    bm25_rrf_weight: float = 0.5
    bm25_index_ttl_seconds: int = 120
    bm25_k1: float = 1.5
    bm25_b: float = 0.75
    bm25_title_weight: float = 3.0
    bm25_section_weight: float = 2.0
    bm25_content_weight: float = 1.0
    bm25_max_corpus_chunks: int = 50000
    bm25_min_query_coverage: float = 0.4

    # ===== 意图识别（BERT） =====
    intent_enabled: bool = True
    intent_model_path: str = str(BASE_DIR / "models" / "intent-bert")
    intent_device: str = "cpu"
    intent_max_length: int = 64
    intent_confidence_threshold: float = 0.55
    intent_margin_threshold: float = 0.12
    intent_cache_ttl: int = 86400

    # ===== 重排序 / RAG =====
    rerank_enabled: bool = True
    rerank_candidate_k: int = 20
    rerank_score_threshold: float = 0.25
    # 重排序失败时的显式策略：drop=丢弃候选并记录缺口；vector=明示降级为向量排序
    rerank_failure_mode: str = "drop"
    rerank_max_consecutive_failures: int = 3
    query_rewrite_enabled: bool = True
    max_context_chars: int = 6000
    max_history_chars: int = 3000

    # ===== 对话 =====
    history_rounds_default: int = 5
    history_rounds_max: int = 20
    temperature_default: float = 0.3
    top_p_default: float = 0.8
    max_tokens_default: int = 1024
    max_tokens_limit: int = 4096
    conversation_retention_days: int = 90
    cleanup_interval_seconds: int = 86400

    # ===== 缓存 =====
    cache_enabled: bool = True
    answer_cache_ttl: int = 300
    retrieval_cache_ttl: int = 180
    retrieval_degraded_cache_ttl: int = 30
    embedding_cache_ttl: int = 86400

    # ===== 权限 =====
    admin_usernames: tuple[str, ...] = ()

    # ===== 可观测 / 限流 =====
    metrics_enabled: bool = True
    rate_limit_enabled: bool = True
    rate_limit_requests: int = 60
    rate_limit_window_seconds: int = 60
    rate_limit_login_requests: int = 10

    # ===== 大模型容错 =====
    llm_timeout_seconds: float = 60.0
    llm_max_retries: int = 3
    llm_retry_backoff_seconds: float = 0.5
    llm_circuit_failure_threshold: int = 5
    llm_circuit_reset_seconds: int = 30

    # ===== 并发 / 背压 =====
    max_concurrent_llm: int = 8
    max_concurrent_embedding: int = 2
    upload_task_ttl: int = 86400

    # ===== 日志 =====
    log_level: str = "INFO"
    log_file: str = "app.log"
    log_max_bytes: int = 10 * 1024 * 1024
    log_backup_count: int = 5

    # 环境变量映射在实例化时统一应用，避免 dataclass 类体只求值一次
    _env_spec: dict[str, tuple[str, Callable[[str], Any]]] = field(
        default_factory=lambda: {
            "debug": ("DEBUG", lambda v: v.lower() in {"1", "true", "yes", "on"}),
            "bge_m3": ("BGE_M3_PATH", str),
            "bge_reranker": ("BGE_RERANKER_PATH", str),
            "embedding_device": ("EMBEDDING_DEVICE", str),
            "embedding_batch_size": ("EMBEDDING_BATCH_SIZE", int),
            "validate_models_on_startup": ("VALIDATE_MODELS_ON_STARTUP", lambda v: v.lower() in {"1", "true", "yes", "on"}),
            "host": ("MYSQL_HOST", str),
            "port": ("MYSQL_PORT", int),
            "user": ("MYSQL_USER", str),
            "password": ("MYSQL_PASSWORD", str),
            "database": ("MYSQL_DATABASE", str),
            "redis_host": ("REDIS_HOST", str),
            "redis_port": ("REDIS_PORT", int),
            "redis_password": ("REDIS_PASSWORD", str),
            "db": ("REDIS_DB", int),
            "milvus_host": ("MILVUS_HOST", str),
            "milvus_port": ("MILVUS_PORT", int),
            "database_name": ("MILVUS_DATABASE", str),
            "collection_name": ("MILVUS_COLLECTION", str),
            "model": ("DEFAULT_LLM_MODEL", str),
            "deepseek_api_key": ("DEEPSEEK_API_KEY", str),
            "deepseek_base_url": ("DEEPSEEK_BASE_URL", str),
            "dashscope_api_key": ("DASHSCOPE_API_KEY", str),
            "dashscope_base_url": ("DASHSCOPE_BASE_URL", str),
            "jwt_secret": ("JWT_SECRET", str),
            "jwt_algorithm": ("JWT_ALGORITHM", str),
            "access_token_expire_minutes": ("ACCESS_TOKEN_EXPIRE_MINUTES", int),
            "captcha_ttl_seconds": ("CAPTCHA_TTL_SECONDS", int),
            "bcrypt_rounds": ("BCRYPT_ROUNDS", int),
            "max_upload_mb": ("MAX_UPLOAD_MB", int),
            "chunk_size": ("CHUNK_SIZE", int),
            "chunk_overlap": ("CHUNK_OVERLAP", int),
            "min_chunk_size": ("MIN_CHUNK_SIZE", int),
            "parent_chunk_size": ("PARENT_CHUNK_SIZE", int),
            "doc_dedup_similarity": ("DOC_DEDUP_SIMILARITY", float),
            "doc_dedup_simhash_max_distance": ("DOC_DEDUP_SIMHASH_MAX_DISTANCE", int),
            "chunk_near_dup_similarity": ("CHUNK_NEAR_DUP_SIMILARITY", float),
            "retrieval_dedup_similarity": ("RETRIEVAL_DEDUP_SIMILARITY", float),
            "mineru_image": ("MINERU_IMAGE", str),
            "mineru_timeout_seconds": ("MINERU_TIMEOUT_SECONDS", int),
            "mineru_max_concurrency": ("MINERU_MAX_CONCURRENCY", int),
            "mineru_memory": ("MINERU_MEMORY", str),
            "retrieval_k": ("RETRIEVAL_K", int),
            "candidate_k": ("CANDIDATE_K", int),
            "dense_weight": ("DENSE_WEIGHT", float),
            "sparse_weight": ("SPARSE_WEIGHT", float),
            "rrf_k": ("RRF_K", int),
            "strategy_enabled": ("STRATEGY_ENABLED", lambda v: v.lower() in {"1", "true", "yes", "on"}),
            "strategy_selector_llm_enabled": ("STRATEGY_SELECTOR_LLM_ENABLED", lambda v: v.lower() in {"1", "true", "yes", "on"}),
            "strategy_selector_model": ("STRATEGY_SELECTOR_MODEL", str),
            "strategy_timeout_seconds": ("STRATEGY_TIMEOUT_SECONDS", float),
            "strategy_max_subqueries": ("STRATEGY_MAX_SUBQUERIES", int),
            "strategy_max_query_chars": ("STRATEGY_MAX_QUERY_CHARS", int),
            "strategy_hyde_max_chars": ("STRATEGY_HYDE_MAX_CHARS", int),
            "strategy_cache_ttl": ("STRATEGY_CACHE_TTL", int),
            "bm25_enabled": ("BM25_ENABLED", lambda v: v.lower() in {"1", "true", "yes", "on"}),
            "bm25_mode": ("BM25_MODE", str),
            "bm25_top_k": ("BM25_TOP_K", int),
            "bm25_rrf_weight": ("BM25_RRF_WEIGHT", float),
            "bm25_index_ttl_seconds": ("BM25_INDEX_TTL_SECONDS", int),
            "bm25_k1": ("BM25_K1", float),
            "bm25_b": ("BM25_B", float),
            "bm25_title_weight": ("BM25_TITLE_WEIGHT", float),
            "bm25_section_weight": ("BM25_SECTION_WEIGHT", float),
            "bm25_content_weight": ("BM25_CONTENT_WEIGHT", float),
            "bm25_max_corpus_chunks": ("BM25_MAX_CORPUS_CHUNKS", int),
            "bm25_min_query_coverage": ("BM25_MIN_QUERY_COVERAGE", float),
            "intent_enabled": ("INTENT_ENABLED", lambda v: v.lower() in {"1", "true", "yes", "on"}),
            "intent_model_path": ("INTENT_MODEL_PATH", str),
            "intent_device": ("INTENT_DEVICE", str),
            "intent_max_length": ("INTENT_MAX_LENGTH", int),
            "intent_confidence_threshold": ("INTENT_CONFIDENCE_THRESHOLD", float),
            "intent_margin_threshold": ("INTENT_MARGIN_THRESHOLD", float),
            "intent_cache_ttl": ("INTENT_CACHE_TTL", int),
            "rerank_enabled": ("RERANK_ENABLED", lambda v: v.lower() in {"1", "true", "yes", "on"}),
            "rerank_candidate_k": ("RERANK_CANDIDATE_K", int),
            "rerank_score_threshold": ("RERANK_SCORE_THRESHOLD", float),
            "rerank_failure_mode": ("RERANK_FAILURE_MODE", str),
            "rerank_max_consecutive_failures": ("RERANK_MAX_CONSECUTIVE_FAILURES", int),
            "query_rewrite_enabled": ("QUERY_REWRITE_ENABLED", lambda v: v.lower() in {"1", "true", "yes", "on"}),
            "max_context_chars": ("MAX_CONTEXT_CHARS", int),
            "max_history_chars": ("MAX_HISTORY_CHARS", int),
            "history_rounds_default": ("HISTORY_ROUNDS_DEFAULT", int),
            "history_rounds_max": ("HISTORY_ROUNDS_MAX", int),
            "temperature_default": ("TEMPERATURE_DEFAULT", float),
            "top_p_default": ("TOP_P_DEFAULT", float),
            "max_tokens_default": ("MAX_TOKENS_DEFAULT", int),
            "max_tokens_limit": ("MAX_TOKENS_LIMIT", int),
            "conversation_retention_days": ("CONVERSATION_RETENTION_DAYS", int),
            "cleanup_interval_seconds": ("CLEANUP_INTERVAL_SECONDS", int),
            "cache_enabled": ("CACHE_ENABLED", lambda v: v.lower() in {"1", "true", "yes", "on"}),
            "answer_cache_ttl": ("ANSWER_CACHE_TTL", int),
            "retrieval_cache_ttl": ("RETRIEVAL_CACHE_TTL", int),
            "retrieval_degraded_cache_ttl": ("RETRIEVAL_DEGRADED_CACHE_TTL", int),
            "embedding_cache_ttl": ("EMBEDDING_CACHE_TTL", int),
            "admin_usernames": ("ADMIN_USERNAMES", _split_csv),
            "metrics_enabled": ("METRICS_ENABLED", lambda v: v.lower() in {"1", "true", "yes", "on"}),
            "rate_limit_enabled": ("RATE_LIMIT_ENABLED", lambda v: v.lower() in {"1", "true", "yes", "on"}),
            "rate_limit_requests": ("RATE_LIMIT_REQUESTS", int),
            "rate_limit_window_seconds": ("RATE_LIMIT_WINDOW_SECONDS", int),
            "rate_limit_login_requests": ("RATE_LIMIT_LOGIN_REQUESTS", int),
            "llm_timeout_seconds": ("LLM_TIMEOUT_SECONDS", float),
            "llm_max_retries": ("LLM_MAX_RETRIES", int),
            "llm_retry_backoff_seconds": ("LLM_RETRY_BACKOFF_SECONDS", float),
            "llm_circuit_failure_threshold": ("LLM_CIRCUIT_FAILURE_THRESHOLD", int),
            "llm_circuit_reset_seconds": ("LLM_CIRCUIT_RESET_SECONDS", int),
            "max_concurrent_llm": ("MAX_CONCURRENT_LLM", int),
            "max_concurrent_embedding": ("MAX_CONCURRENT_EMBEDDING", int),
            "upload_task_ttl": ("UPLOAD_TASK_TTL", int),
            "log_level": ("LOG_LEVEL", str),
            "log_file": ("LOG_FILE", str),
            "log_max_bytes": ("LOG_MAX_BYTES", int),
            "log_backup_count": ("LOG_BACKUP_COUNT", int),
        }
    )

    def __post_init__(self) -> None:
        # 路径类环境变量需要相对项目根目录解析
        upload_dir = _env_path("UPLOAD_DIR", Path("uploads"))
        log_dir = _env_path("LOG_DIR", Path("logs"))
        data_dir = _env_path("DATA_DIR", Path("data"))
        self.upload_dir = upload_dir if upload_dir.is_absolute() else BASE_DIR / upload_dir
        self.log_dir = log_dir if log_dir.is_absolute() else BASE_DIR / log_dir
        self.data_dir = data_dir if data_dir.is_absolute() else BASE_DIR / data_dir

        for attribute, (env_name, converter) in self._env_spec.items():
            raw = os.getenv(env_name)
            if raw is None or not raw.strip():
                continue
            try:
                value = converter(raw)
            except (TypeError, ValueError) as exc:
                raise ValueError(
                    f"环境变量 {env_name} 解析失败（字段 {attribute}）：{raw!r}"
                ) from exc
            setattr(self, attribute, value)

        # 模型路径支持相对项目根目录
        self.bge_m3 = str(self._resolve_path(self.bge_m3))
        self.bge_reranker = str(self._resolve_path(self.bge_reranker))
        self.intent_model_path = str(self._resolve_path(self.intent_model_path))

        self._validate()

    @staticmethod
    def _resolve_path(value: str) -> Path:
        path = Path(value)
        return path if path.is_absolute() else BASE_DIR / path

    def _validate(self) -> None:
        if self.rerank_failure_mode not in {"drop", "vector"}:
            raise ValueError(
                "RERANK_FAILURE_MODE 只能是 drop 或 vector，"
                f"当前为 {self.rerank_failure_mode!r}"
            )
        if not 0.0 <= self.rerank_score_threshold <= 1.0:
            raise ValueError("RERANK_SCORE_THRESHOLD 必须在 0~1 之间")
        if not 0.0 <= self.intent_confidence_threshold <= 1.0:
            raise ValueError("INTENT_CONFIDENCE_THRESHOLD 必须在 0~1 之间")
        if self.bm25_mode not in {"off", "fallback", "fuse"}:
            raise ValueError(
                "BM25_MODE 只能是 off、fallback 或 fuse，"
                f"当前为 {self.bm25_mode!r}"
            )
        if not 0.0 <= self.bm25_b <= 1.0:
            raise ValueError("BM25_B 必须在 0~1 之间")
        if not 0.0 <= self.bm25_min_query_coverage <= 1.0:
            raise ValueError("BM25_MIN_QUERY_COVERAGE 必须在 0~1 之间")
        if self.bm25_k1 <= 0 or self.bm25_top_k <= 0:
            raise ValueError("BM25_K1 与 BM25_TOP_K 必须为正数")
        if self.bm25_rrf_weight < 0 or self.bm25_index_ttl_seconds < 0:
            raise ValueError("BM25_RRF_WEIGHT 与 BM25_INDEX_TTL_SECONDS 不能为负")
        if self.rrf_k <= 0:
            raise ValueError("RRF_K 必须为正整数")
        if self.strategy_timeout_seconds <= 0 or self.strategy_max_subqueries <= 0:
            raise ValueError("STRATEGY_TIMEOUT_SECONDS 与 STRATEGY_MAX_SUBQUERIES 必须为正数")
        if self.retrieval_degraded_cache_ttl < 0:
            raise ValueError("RETRIEVAL_DEGRADED_CACHE_TTL 不能为负")
        if self.llm_max_retries < 1:
            raise ValueError("LLM_MAX_RETRIES 至少为 1")
        if self.rate_limit_requests < 1 or self.rate_limit_window_seconds < 1:
            raise ValueError("限流参数必须为正整数")
        self.log_level = self.log_level.upper()

    def validate_model_paths(self) -> None:
        """启动时校验模型目录，缺失时立即失败而不是等首个请求。"""
        if not self.validate_models_on_startup:
            return
        required = [("BGE_M3_PATH", Path(self.bge_m3))]
        if self.rerank_enabled:
            required.append(("BGE_RERANKER_PATH", Path(self.bge_reranker)))
        if self.intent_enabled:
            required.append(("INTENT_MODEL_PATH", Path(self.intent_model_path)))
        missing = [
            f"{name}={path}（来源：config.py:Settings.validate_model_paths）"
            for name, path in required
            if not path.exists()
        ]
        if missing:
            raise RuntimeError(
                "本地模型目录缺失，请检查环境变量或 models/ 目录：" + "；".join(missing)
            )

    def mysql_url(self, database: str | None = None, async_driver: bool = True) -> str:
        driver = "mysql+aiomysql" if async_driver else "mysql+pymysql"
        return (
            f"{driver}://{self.user}:{self.password}@{self.host}:{self.port}/"
            f"{database or self.database}?charset=utf8mb4"
        )

    def ensure_dirs(self) -> None:
        for path in (self.data_dir, self.upload_dir, self.log_dir):
            Path(path).mkdir(parents=True, exist_ok=True)


# 模块级单例：在导入时实例化，但读取环境变量的逻辑集中在 __post_init__，
# 测试可构造 Settings() 验证，无需重新加载模块。
Config = Settings()


# 可用模型注册表：provider 决定使用哪套 base_url / api_key；
# api_model 用于把前端展示标识映射到服务商实际接受的模型名。
AVAILABLE_MODELS: dict[str, dict] = {
    "deepseekv4-flash": {
        "provider": "deepseek",
        "api_model": "deepseek-flash",
        "label": "DeepSeek V4 Flash",
        "default": True,
    },
    "deepseek-v4-pro": {
        "provider": "deepseek",
        "api_model": "deepseek-v4-pro",
        "label": "DeepSeek V4 Pro",
        "default": False,
    },
    "qwen-plus": {
        "provider": "dashscope",
        "api_model": "qwen-plus",
        "label": "通义千问 Plus",
        "default": False,
    },
    "qwen-max": {
        "provider": "dashscope",
        "api_model": "qwen-max",
        "label": "通义千问 Max",
        "default": False,
    },
}


def get_model_settings(model_name: str) -> tuple[str, str]:
    """返回 (base_url, api_key)，未知模型回退到 DeepSeek。"""
    meta = AVAILABLE_MODELS.get(model_name)
    if meta and meta["provider"] == "dashscope":
        return Config.dashscope_base_url, Config.dashscope_api_key
    return Config.deepseek_base_url, Config.deepseek_api_key


def get_api_model(model_name: str) -> str:
    """返回服务商实际接受的模型名；未配置映射时原样返回。"""
    meta = AVAILABLE_MODELS.get(model_name)
    if not meta:
        return model_name
    return meta.get("api_model") or model_name
