"""RAG 评估运行与逐题结果，供管理员看板读取。"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import (
    BigInteger,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    JSON,
    String,
    Text,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class EvalRun(Base):
    __tablename__ = "eval_runs"

    id: Mapped[int] = mapped_column(
        BigInteger().with_variant(Integer, "sqlite"),
        primary_key=True,
        autoincrement=True,
    )
    owner_id: Mapped[int | None] = mapped_column(
        BigInteger().with_variant(Integer, "sqlite"),
        ForeignKey("users.id", ondelete="SET NULL"),
        index=True,
        nullable=True,
    )
    dataset_name: Mapped[str] = mapped_column(String(255), nullable=False)
    dataset_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    kb_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    kb_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    strategy: Mapped[str] = mapped_column(String(32), default="auto", nullable=False)
    top_k: Mapped[int] = mapped_column(Integer, default=10, nullable=False)
    judge_model: Mapped[str | None] = mapped_column(String(64), nullable=True)
    ragas_version: Mapped[str | None] = mapped_column(String(32), nullable=True)
    sample_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    answerable_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    retrieval_metrics: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    generation_metrics: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    config_snapshot: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    status: Mapped[str] = mapped_column(
        String(16), default="success", nullable=False, index=True
    )
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    started_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), nullable=False
    )


class EvalCase(Base):
    __tablename__ = "eval_cases"

    id: Mapped[int] = mapped_column(
        BigInteger().with_variant(Integer, "sqlite"),
        primary_key=True,
        autoincrement=True,
    )
    run_id: Mapped[int] = mapped_column(
        BigInteger().with_variant(Integer, "sqlite"),
        ForeignKey("eval_runs.id", ondelete="CASCADE"),
        index=True,
        nullable=False,
    )
    case_id: Mapped[str] = mapped_column(String(64), nullable=False)
    question: Mapped[str] = mapped_column(Text, nullable=False)
    ground_truth: Mapped[str | None] = mapped_column(Text, nullable=True)
    answer: Mapped[str | None] = mapped_column(Text, nullable=True)
    intent: Mapped[str | None] = mapped_column(String(32), nullable=True)
    tags: Mapped[list[str] | None] = mapped_column(JSON, nullable=True)
    expected_doc_names: Mapped[list[str] | None] = mapped_column(JSON, nullable=True)
    expected_sections: Mapped[list[str] | None] = mapped_column(JSON, nullable=True)
    retrieved: Mapped[list[dict[str, Any]] | None] = mapped_column(JSON, nullable=True)
    doc_metrics: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    section_metrics: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    generation_metrics: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    keyword_coverage: Mapped[float | None] = mapped_column(Float, nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
