"""评估看板 API：仅管理员可读，数据由评估 CLI 写入。"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_admin
from app.db.session import get_db
from app.models.eval_run import EvalRun
from app.schemas.evaluation import (
    EvalCaseListOut,
    EvalCaseOut,
    EvalRunListOut,
    EvalRunOut,
    EvalOverviewOut,
    EvalTrendListOut,
    EvalTrendPoint,
)
from app.services import evaluation_store

router = APIRouter(
    prefix="/evaluations",
    tags=["评估"],
    dependencies=[Depends(get_current_admin)],
)

_TREND_KEYS = {
    "doc_hit_at_k": ("doc", "hit_at_k"),
    "doc_recall_at_k": ("doc", "recall_at_k"),
    "section_hit_at_k": ("section", "hit_at_k"),
    "section_recall_at_k": ("section", "recall_at_k"),
}


def _not_found(run_id: int) -> HTTPException:
    return HTTPException(
        status.HTTP_404_NOT_FOUND, f"评估运行不存在：{run_id}"
    )


def _metric(
    run: EvalRun | None, level: str, metric: str
) -> float | None:
    if run is None:
        return None
    value = ((run.retrieval_metrics or {}).get(level) or {}).get(metric)
    try:
        return float(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def _delta(
    latest: EvalRun | None, previous: EvalRun | None
) -> dict[str, Any] | None:
    if latest is None or previous is None:
        return None
    retrieval: dict[str, dict[str, float | None]] = {}
    for level in ("doc", "section"):
        retrieval[level] = {}
        for metric in ("hit_at_k", "recall_at_k", "mrr", "ndcg_at_k"):
            current = _metric(latest, level, metric)
            before = _metric(previous, level, metric)
            retrieval[level][metric] = (
                None
                if current is None or before is None
                else round(current - before, 6)
            )
    generation: dict[str, float | None] = {}
    for name in sorted(
        set((latest.generation_metrics or {}))
        | set((previous.generation_metrics or {}))
    ):
        current = (latest.generation_metrics or {}).get(name)
        before = (previous.generation_metrics or {}).get(name)
        try:
            generation[name] = (
                None
                if current is None or before is None
                else round(float(current) - float(before), 6)
            )
        except (TypeError, ValueError):
            generation[name] = None
    return {"retrieval": retrieval, "generation": generation}


@router.get("/overview", response_model=EvalOverviewOut, summary="评估概览")
async def evaluation_overview(
    db: AsyncSession = Depends(get_db),
) -> EvalOverviewOut:
    overview = await evaluation_store.get_overview(db)
    latest = overview["latest"]
    previous = overview["previous"]
    return EvalOverviewOut(
        latest=EvalRunOut.model_validate(latest) if latest else None,
        previous=EvalRunOut.model_validate(previous) if previous else None,
        total_runs=overview["total_runs"],
        delta=_delta(latest, previous),
    )


@router.get("/trends", response_model=EvalTrendListOut, summary="评估趋势")
async def evaluation_trends(
    limit: int = Query(30, ge=1, le=100),
    db: AsyncSession = Depends(get_db),
) -> EvalTrendListOut:
    rows = await evaluation_store.list_eval_trends(db, limit)
    items: list[EvalTrendPoint] = []
    for run in rows:
        values = {
            key: _metric(run, level, metric)
            for key, (level, metric) in _TREND_KEYS.items()
        }
        generation = {
            name: (
                float(value) if value is not None else None
            )
            for name, value in (run.generation_metrics or {}).items()
        }
        items.append(
            EvalTrendPoint(
                id=run.id,
                created_at=run.created_at,
                dataset_name=run.dataset_name,
                strategy=run.strategy,
                top_k=run.top_k,
                generation=generation,
                **values,
            )
        )
    return EvalTrendListOut(items=items)


@router.get("/runs", response_model=EvalRunListOut, summary="评估运行列表")
async def evaluation_runs(
    limit: int = Query(20, ge=1, le=100),
    offset: int = Query(0, ge=0),
    strategy: str | None = Query(None),
    status_filter: str | None = Query(None, alias="status"),
    db: AsyncSession = Depends(get_db),
) -> EvalRunListOut:
    total, rows = await evaluation_store.list_eval_runs(
        db, limit, offset, strategy=strategy or None, status=status_filter or None
    )
    return EvalRunListOut(
        total=total,
        items=[EvalRunOut.model_validate(row) for row in rows],
    )


@router.get(
    "/runs/{run_id}", response_model=EvalRunOut, summary="评估运行详情"
)
async def evaluation_run_detail(
    run_id: int,
    db: AsyncSession = Depends(get_db),
) -> EvalRunOut:
    run = await evaluation_store.get_eval_run(db, run_id)
    if run is None:
        raise _not_found(run_id)
    return EvalRunOut.model_validate(run)


@router.get(
    "/runs/{run_id}/cases",
    response_model=EvalCaseListOut,
    summary="评估逐题结果",
)
async def evaluation_run_cases(
    run_id: int,
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    failed_only: bool = Query(False),
    db: AsyncSession = Depends(get_db),
) -> EvalCaseListOut:
    run = await evaluation_store.get_eval_run(db, run_id)
    if run is None:
        raise _not_found(run_id)
    total, rows = await evaluation_store.get_eval_cases(
        db, run_id, limit, offset, failed_only=failed_only
    )
    return EvalCaseListOut(
        total=total,
        items=[EvalCaseOut.model_validate(row) for row in rows],
    )


@router.delete(
    "/runs/{run_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="删除评估运行",
)
async def evaluation_run_delete(
    run_id: int,
    db: AsyncSession = Depends(get_db),
) -> None:
    if not await evaluation_store.delete_eval_run(db, run_id):
        raise _not_found(run_id)
