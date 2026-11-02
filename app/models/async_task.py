"""后台任务表：文档入库/重建索引的可持久化队列。"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from sqlalchemy import (
    BigInteger,
    DateTime,
    Index,
    Integer,
    JSON,
    String,
    Text,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


def _utcnow() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


class AsyncTask(Base):
    """持久化任务，服务重启后仍可继续执行。

    ``status``：pending → running → succeeded / failed；``lease_until`` 用于
    识别因进程崩溃而停留在 running 的任务。
    """

    __tablename__ = "async_tasks"
    __table_args__ = (
        Index("ix_async_tasks_status_available", "status", "available_at"),
    )

    id: Mapped[int] = mapped_column(
        BigInteger().with_variant(Integer, "sqlite"),
        primary_key=True,
        autoincrement=True,
    )
    task_type: Mapped[str] = mapped_column(
        String(32), default="ingest", index=True, nullable=False
    )
    status: Mapped[str] = mapped_column(
        String(16), default="pending", index=True, nullable=False
    )
    document_id: Mapped[int | None] = mapped_column(
        BigInteger().with_variant(Integer, "sqlite"), index=True, nullable=True
    )
    kb_id: Mapped[int | None] = mapped_column(
        BigInteger().with_variant(Integer, "sqlite"), index=True, nullable=True
    )
    payload: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    attempts: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    max_attempts: Mapped[int] = mapped_column(Integer, default=3, nullable=False)
    available_at: Mapped[datetime] = mapped_column(
        DateTime, default=_utcnow, nullable=False
    )
    lease_until: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, default=_utcnow, nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=_utcnow, onupdate=_utcnow, nullable=False
    )
    finished_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
