"""异步数据库引擎与会话管理。"""

from __future__ import annotations

from collections.abc import AsyncGenerator

from sqlalchemy import inspect, text
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from app.db.base import Base
from app.core.logger import get_logger
from config import Config

logger = get_logger(__name__)

_engine: AsyncEngine | None = None
_session_maker: async_sessionmaker[AsyncSession] | None = None


def get_engine() -> AsyncEngine:
    global _engine
    if _engine is None:
        _engine = create_async_engine(
            Config.mysql_url(async_driver=True),
            pool_pre_ping=True,
            pool_size=10,
            max_overflow=20,
            pool_recycle=3600,
            echo=False,
        )
    return _engine


def get_session_maker() -> async_sessionmaker[AsyncSession]:
    global _session_maker
    if _session_maker is None:
        _session_maker = async_sessionmaker(
            bind=get_engine(),
            class_=AsyncSession,
            expire_on_commit=False,
            autoflush=False,
        )
    return _session_maker


async def get_db() -> AsyncGenerator[AsyncSession, None]:
    """FastAPI 依赖：每个请求一个会话，异常回滚。"""
    async with get_session_maker()() as session:
        try:
            yield session
        except Exception:
            await session.rollback()
            raise


def ensure_database() -> None:
    """若目标库不存在则创建（启动时调用，使用同步驱动）。"""
    import pymysql

    conn = pymysql.connect(
        host=Config.host,
        port=Config.port,
        user=Config.user,
        password=Config.password,
        charset="utf8mb4",
        autocommit=True,
    )
    try:
        with conn.cursor() as cursor:
            cursor.execute(
                f"CREATE DATABASE IF NOT EXISTS `{Config.database}` "
                "CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci"
            )
        logger.info("MySQL 数据库 %s 已就绪", Config.database)
    finally:
        conn.close()


async def init_models() -> None:
    """创建所有 ORM 表（开发期使用，生产建议迁移工具）。"""
    # 确保模型已注册到 Base.metadata
    import app.models  # noqa: F401

    async with get_engine().begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    logger.info("MySQL 数据表初始化完成")


async def ensure_schema_upgrades() -> None:
    """轻量兼容升级：create_all 不会给存量表补列，这里幂等补齐。"""

    def _add_columns(sync_conn, table: str, columns: dict[str, str]) -> set[str]:
        existing = {
            column["name"] for column in inspect(sync_conn).get_columns(table)
        }
        for name, definition in columns.items():
            if name in existing:
                continue
            sync_conn.execute(
                text(f"ALTER TABLE {table} ADD COLUMN {name} {definition}")
            )
            existing.add(name)
            logger.info("已为 %s 补充 %s 字段", table, name)
        return existing

    def _add_index(sync_conn, table: str, name: str, columns: str) -> None:
        existing = {
            index["name"] for index in inspect(sync_conn).get_indexes(table)
        }
        if name in existing:
            return
        sync_conn.execute(
            text(f"CREATE INDEX {name} ON {table} ({columns})")
        )
        logger.info("已创建索引 %s", name)

    def _upgrade(sync_conn) -> None:
        inspector = inspect(sync_conn)
        tables = set(inspector.get_table_names())
        if "knowledge_bases" in tables:
            columns = {column["name"] for column in inspector.get_columns("knowledge_bases")}
            if "is_public" not in columns:
                sync_conn.execute(
                    text(
                        "ALTER TABLE knowledge_bases "
                        "ADD COLUMN is_public BOOLEAN NOT NULL DEFAULT 0"
                    )
                )
                logger.info("已为 knowledge_bases 补充 is_public 字段")
            _add_index(
                sync_conn,
                "knowledge_bases",
                "ix_knowledge_bases_is_public",
                "is_public",
            )

        if "documents" in tables:
            _add_columns(
                sync_conn,
                "documents",
                {
                    "content_hash": "VARCHAR(64) NULL",
                    "content_simhash": "VARCHAR(16) NULL",
                    "content_length": "INT NOT NULL DEFAULT 0",
                    "version": "INT NOT NULL DEFAULT 1",
                    "is_current": "BOOLEAN NOT NULL DEFAULT 1",
                    "replaces_doc_id": "BIGINT NULL",
                    "update_action": "VARCHAR(24) NOT NULL DEFAULT 'created'",
                    "similar_doc_id": "BIGINT NULL",
                    "similar_doc_name": "VARCHAR(255) NULL",
                    "similarity": "DOUBLE NULL",
                    "force_new": "BOOLEAN NOT NULL DEFAULT 0",
                    "parser": "VARCHAR(16) NOT NULL DEFAULT 'auto'",
                },
            )
            _add_index(
                sync_conn, "documents", "ix_documents_is_current", "is_current"
            )
            _add_index(
                sync_conn,
                "documents",
                "ix_documents_kb_current",
                "kb_id, is_current",
            )
            _add_index(
                sync_conn,
                "documents",
                "ix_documents_content_hash",
                "content_hash",
            )

        if "chunks" in tables:
            _add_columns(
                sync_conn,
                "chunks",
                {
                    "parent_uid": "VARCHAR(64) NULL",
                    "content_hash": "VARCHAR(64) NULL",
                },
            )
            _add_index(
                sync_conn, "chunks", "ix_chunks_parent_uid", "parent_uid"
            )
            _add_index(
                sync_conn, "chunks", "ix_chunks_content_hash", "content_hash"
            )

        if "knowledge_gaps" in tables:
            _add_columns(
                sync_conn,
                "knowledge_gaps",
                {
                    "intent_confidence": "DOUBLE NULL",
                    "intent_source": "VARCHAR(16) NULL",
                    "vector_score": "DOUBLE NULL",
                    "rerank_score": "DOUBLE NULL",
                    "retrieval_mode": "VARCHAR(16) NULL",
                    "status": "VARCHAR(16) NOT NULL DEFAULT 'open'",
                    "note": "TEXT NULL",
                    "resolved_at": "DATETIME NULL",
                },
            )
            _add_index(
                sync_conn, "knowledge_gaps", "ix_knowledge_gaps_status", "status"
            )

        if "users" in tables:
            _add_columns(
                sync_conn,
                "users",
                {"is_admin": "BOOLEAN NOT NULL DEFAULT 0"},
            )

        if "conversations" in tables:
            _add_columns(
                sync_conn,
                "conversations",
                {"archived_at": "DATETIME NULL"},
            )
            _add_index(
                sync_conn,
                "conversations",
                "ix_conversations_archived_at",
                "archived_at",
            )

    async with get_engine().begin() as conn:
        await conn.run_sync(_upgrade)


async def dispose_engine() -> None:
    global _engine, _session_maker
    if _engine is not None:
        await _engine.dispose()
        _engine = None
        _session_maker = None
