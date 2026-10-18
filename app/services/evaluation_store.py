"""评估结果的 MySQL 持久化：CLI 写入、管理员 API 读取。"""

from __future__ import annotations

from typing import Any

from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.eval_run import EvalCase, EvalRun

_RUN_FIELDS = {
    "dataset_name",
    "dataset_sha256",
    "kb_id",
    "kb_name",
    "strategy",
    "top_k",
    "judge_model",
    "ragas_version",
    "sample_count",
    "answerable_count",
    "retrieval_metrics",
    "generation_metrics",
    "config_snapshot",
    "status",
    "error",
    "started_at",
    "finished_at",
}

_CASE_FIELDS = {
    "case_id",
    "question",
    "ground_truth",
    "answer",
    "intent",
    "tags",
    "expected_doc_names",
    "expected_sections",
    "retrieved",
    "doc_metrics",
    "section_metrics",
    "generation_metrics",
    "keyword_coverage",
    "error",
}


async def save_eval_run(
    db: AsyncSession,
    run: dict[str, Any],
    cases: list[dict[str, Any]],
    owner_id: int | None,
) -> int:
    """run 与 cases 单事务写入；任一步失败整体回滚。"""
    row = EvalRun(owner_id=owner_id)
    for field in _RUN_FIELDS:
        if field in run:
            setattr(row, field, run[field])
    db.add(row)
    await db.flush()
    run_id = int(row.id)

    db.add_all(
        [
            EvalCase(
                run_id=run_id,
                **{key: case.get(key) for key in _CASE_FIELDS},
            )
            for case in cases
        ]
    )
    await db.commit()
    return run_id


async def list_eval_runs(
    db: AsyncSession,
    limit: int = 20,
    offset: int = 0,
    *,
    strategy: str | None = None,
    status: str | None = None,
) -> tuple[int, list[EvalRun]]:
    conditions = []
    if strategy:
        conditions.append(EvalRun.strategy == strategy)
    if status:
        conditions.append(EvalRun.status == status)

    total = int(
        await db.scalar(select(func.count()).select_from(EvalRun).where(*conditions))
        or 0
    )
    rows = list(
        await db.scalars(
            select(EvalRun)
            .where(*conditions)
            .order_by(EvalRun.id.desc())
            .limit(max(1, limit))
            .offset(max(0, offset))
        )
    )
    return total, rows


async def get_eval_run(db: AsyncSession, run_id: int) -> EvalRun | None:
    return await db.get(EvalRun, int(run_id))


async def get_eval_cases(
    db: AsyncSession,
    run_id: int,
    limit: int = 50,
    offset: int = 0,
    *,
    failed_only: bool = False,
) -> tuple[int, list[EvalCase]]:
    conditions = [EvalCase.run_id == int(run_id)]
    if failed_only:
        conditions.append(EvalCase.error.is_not(None))
    total = int(
        await db.scalar(
            select(func.count()).select_from(EvalCase).where(*conditions)
        )
        or 0
    )
    rows = list(
        await db.scalars(
            select(EvalCase)
            .where(*conditions)
            .order_by(EvalCase.id.asc())
            .limit(max(1, limit))
            .offset(max(0, offset))
        )
    )
    return total, rows


async def delete_eval_run(db: AsyncSession, run_id: int) -> bool:
    """显式删除明细再删主表，SQLite 未开启外键时同样可靠。"""
    run = await get_eval_run(db, run_id)
    if run is None:
        return False
    await db.execute(delete(EvalCase).where(EvalCase.run_id == int(run_id)))
    await db.delete(run)
    await db.commit()
    return True


async def get_overview(db: AsyncSession) -> dict[str, Any]:
    """最近一次成功运行、上一次成功运行与总运行数。"""
    latest = await db.scalar(
        select(EvalRun)
        .where(EvalRun.status == "success")
        .order_by(EvalRun.id.desc())
        .limit(1)
    )
    previous = None
    if latest is not None:
        previous = await db.scalar(
            select(EvalRun)
            .where(EvalRun.status == "success", EvalRun.id < latest.id)
            .order_by(EvalRun.id.desc())
            .limit(1)
        )
    total = int(await db.scalar(select(func.count()).select_from(EvalRun)) or 0)
    return {"latest": latest, "previous": previous, "total_runs": total}


async def list_eval_trends(db: AsyncSession, limit: int = 30) -> list[EvalRun]:
    rows = list(
        await db.scalars(
            select(EvalRun)
            .where(EvalRun.status == "success")
            .order_by(EvalRun.id.desc())
            .limit(max(1, limit))
        )
    )
    return list(reversed(rows))
