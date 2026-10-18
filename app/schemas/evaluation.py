"""评估看板 API 的响应模型。"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class EvalRunOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    owner_id: int | None = None
    dataset_name: str
    dataset_sha256: str
    kb_id: int | None = None
    kb_name: str | None = None
    strategy: str
    top_k: int
    judge_model: str | None = None
    ragas_version: str | None = None
    sample_count: int
    answerable_count: int
    retrieval_metrics: dict[str, Any] | None = None
    generation_metrics: dict[str, Any] | None = None
    config_snapshot: dict[str, Any] | None = None
    status: str
    error: str | None = None
    started_at: datetime | None = None
    finished_at: datetime | None = None
    created_at: datetime


class EvalRunListOut(BaseModel):
    total: int
    items: list[EvalRunOut]


class EvalCaseOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    run_id: int
    case_id: str
    question: str
    ground_truth: str | None = None
    answer: str | None = None
    intent: str | None = None
    tags: list[str] | None = None
    expected_doc_names: list[str] | None = None
    expected_sections: list[str] | None = None
    retrieved: list[dict[str, Any]] | None = None
    doc_metrics: dict[str, Any] | None = None
    section_metrics: dict[str, Any] | None = None
    generation_metrics: dict[str, Any] | None = None
    keyword_coverage: float | None = None
    error: str | None = None


class EvalCaseListOut(BaseModel):
    total: int
    items: list[EvalCaseOut]


class EvalOverviewOut(BaseModel):
    latest: EvalRunOut | None = None
    previous: EvalRunOut | None = None
    total_runs: int = 0
    delta: dict[str, Any] | None = None


class EvalTrendPoint(BaseModel):
    id: int
    created_at: datetime
    dataset_name: str
    strategy: str
    top_k: int
    doc_hit_at_k: float | None = None
    doc_recall_at_k: float | None = None
    section_hit_at_k: float | None = None
    section_recall_at_k: float | None = None
    generation: dict[str, float | None] = Field(default_factory=dict)


class EvalTrendListOut(BaseModel):
    items: list[EvalTrendPoint]
