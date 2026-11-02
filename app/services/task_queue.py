"""持久化入库任务队列：入队、租约领取、重试与恢复。

任务落在 MySQL 的 ``async_tasks`` 表，服务重启不会丢。领取使用
``SELECT ... FOR UPDATE SKIP LOCKED``（MySQL 8），多 worker 不会重复消费。
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import and_, delete, or_, select

from app.core.logger import get_logger
from app.models.async_task import AsyncTask
from config import Config

logger = get_logger(__name__)

ACTIVE_STATUSES = ("pending", "running")
TERMINAL_STATUSES = ("succeeded", "failed")


def naive_utcnow() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


def _backoff_seconds(attempts: int) -> int:
    return min(300, max(2, 2 ** max(0, attempts)))


async def enqueue_document_task(
    db,
    *,
    document_id: int,
    kb_id: int,
    task_type: str = "ingest",
    payload: dict[str, Any] | None = None,
) -> AsyncTask:
    """同一文档同类型只保留一个未完成任务，避免重复解析。"""
    existing = await db.scalar(
        select(AsyncTask).where(
            AsyncTask.document_id == document_id,
            AsyncTask.task_type == task_type,
            AsyncTask.status.in_(ACTIVE_STATUSES),
        )
    )
    if existing is not None:
        return existing

    task = AsyncTask(
        task_type=task_type,
        document_id=document_id,
        kb_id=kb_id,
        payload=payload,
        max_attempts=max(1, Config.task_max_attempts),
    )
    db.add(task)
    await db.flush()
    return task


async def claim_next_task(db, *, now: datetime | None = None) -> AsyncTask | None:
    """领取一个可执行任务；租约过期的 running 任务也会被重新领取。"""
    current = now or naive_utcnow()
    stmt = (
        select(AsyncTask)
        .where(
            AsyncTask.available_at <= current,
            or_(
                AsyncTask.status == "pending",
                and_(
                    AsyncTask.status == "running",
                    AsyncTask.lease_until.is_not(None),
                    AsyncTask.lease_until < current,
                ),
            ),
        )
        .order_by(AsyncTask.id.asc())
        .limit(1)
        .with_for_update(skip_locked=True)
    )
    task = await db.scalar(stmt)
    if task is None:
        return None

    task.status = "running"
    task.attempts = int(task.attempts or 0) + 1
    task.lease_until = current + timedelta(seconds=Config.task_lease_seconds)
    task.error_message = None
    await db.commit()
    return task


async def claim_task_by_id(db, task_id: int) -> AsyncTask | None:
    """按 ID 抢占任务（BackgroundTasks 加速路径与 worker 竞争时只有一方成功）。"""
    current = naive_utcnow()
    task = await db.scalar(
        select(AsyncTask)
        .where(
            AsyncTask.id == task_id,
            or_(
                AsyncTask.status == "pending",
                and_(
                    AsyncTask.status == "running",
                    AsyncTask.lease_until.is_not(None),
                    AsyncTask.lease_until < current,
                ),
            ),
        )
        .with_for_update(skip_locked=True)
    )
    if task is None:
        return None
    task.status = "running"
    task.attempts = int(task.attempts or 0) + 1
    task.lease_until = current + timedelta(seconds=Config.task_lease_seconds)
    task.error_message = None
    await db.commit()
    return task


async def succeed_task(db, task_id: int) -> None:
    task = await db.get(AsyncTask, task_id)
    if task is None:
        return
    task.status = "succeeded"
    task.lease_until = None
    task.error_message = None
    task.finished_at = naive_utcnow()
    await db.commit()


async def fail_task(db, task_id: int, error: BaseException) -> bool:
    """记录失败；未达上限时退避重试，返回是否还会重试。"""
    task = await db.get(AsyncTask, task_id)
    if task is None:
        return False

    now = naive_utcnow()
    task.lease_until = None
    task.error_message = str(error)[:1000]
    if int(task.attempts or 0) < int(task.max_attempts or 1):
        task.status = "pending"
        task.available_at = now + timedelta(
            seconds=_backoff_seconds(int(task.attempts or 0))
        )
        await db.commit()
        return True

    task.status = "failed"
    task.finished_at = now
    if task.document_id is not None:
        from app.models.document import Document

        document = await db.get(Document, task.document_id)
        if document is not None and document.status != "completed":
            document.status = "failed"
            document.error_message = str(error)[:1000]
    await db.commit()
    return False


async def reset_expired_lease(db, *, now: datetime | None = None) -> int:
    """把租约过期仍在 running 的任务放回 pending，供启动/轮询恢复。"""
    current = now or naive_utcnow()
    tasks = list(
        await db.scalars(
            select(AsyncTask).where(
                AsyncTask.status == "running",
                AsyncTask.lease_until.is_not(None),
                AsyncTask.lease_until < current,
            )
        )
    )
    for task in tasks:
        task.status = "pending"
        task.lease_until = None
        task.available_at = current
    if tasks:
        await db.commit()
    return len(tasks)


async def expire_stale_tasks(db, *, ttl_seconds: int | None = None) -> int:
    """超时仍未完成的任务标记失败，避免文档永久停在 pending/processing。"""
    ttl = Config.upload_task_ttl if ttl_seconds is None else ttl_seconds
    cutoff = naive_utcnow() - timedelta(seconds=max(60, ttl))
    tasks = list(
        await db.scalars(
            select(AsyncTask).where(
                AsyncTask.status.in_(ACTIVE_STATUSES),
                AsyncTask.created_at < cutoff,
            )
        )
    )
    for task in tasks:
        task.status = "failed"
        task.finished_at = naive_utcnow()
        task.error_message = "任务超过保留时间仍未完成"
        task.lease_until = None
        if task.document_id is not None:
            from app.models.document import Document

            document = await db.get(Document, task.document_id)
            if document is not None and document.status != "completed":
                document.status = "failed"
                document.error_message = "解析任务超时，请重新上传或重建索引"
    if tasks:
        await db.commit()
        logger.warning(
            "已过期入库任务 task_count=%d ttl=%ds"
            "（app/services/task_queue.py:expire_stale_tasks）",
            len(tasks),
            ttl,
        )
    return len(tasks)


async def cleanup_finished_tasks(db, *, ttl_seconds: int | None = None) -> int:
    ttl = Config.upload_task_ttl if ttl_seconds is None else ttl_seconds
    cutoff = naive_utcnow() - timedelta(seconds=max(60, ttl))
    result = await db.execute(
        delete(AsyncTask).where(
            AsyncTask.status.in_(TERMINAL_STATUSES),
            AsyncTask.finished_at.is_not(None),
            AsyncTask.finished_at < cutoff,
        )
    )
    await db.commit()
    return int(result.rowcount or 0)
