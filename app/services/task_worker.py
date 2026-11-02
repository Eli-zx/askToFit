"""后台任务执行器：消费入库任务与向量 outbox，并在启动时恢复中断工作。"""

from __future__ import annotations

import asyncio
import contextlib
from typing import Any

from sqlalchemy import select

from app.core.logger import get_logger
from app.db.session import get_session_maker
from app.models.async_task import AsyncTask
from app.models.document import Document
from app.models.vector_outbox import VectorOutbox
from app.services import outbox, task_queue
from app.services.ingestion import ingest_document
from config import Config

logger = get_logger(__name__)

_RECOVERY_LIMIT = 200


async def recover_work() -> dict[str, int]:
    """启动恢复：过期租约回收、丢失任务的文档补入队、超时任务收敛。"""
    stats = {"tasks": 0, "outbox": 0, "enqueued": 0, "expired": 0}
    async with get_session_maker()() as db:
        stats["tasks"] = await task_queue.reset_expired_lease(db)
        stats["outbox"] = await outbox.reset_expired_lease(db)
        stats["expired"] = await task_queue.expire_stale_tasks(db)
        await task_queue.cleanup_finished_tasks(db)
        await outbox.cleanup_finished_entries(db)

        active_task_docs = set(
            await db.scalars(
                select(AsyncTask.document_id).where(
                    AsyncTask.status.in_(task_queue.ACTIVE_STATUSES),
                    AsyncTask.document_id.is_not(None),
                )
            )
        )
        pending_entries = list(
            await db.scalars(
                select(VectorOutbox).where(
                    VectorOutbox.status.in_(outbox.ACTIVE_STATUSES),
                    VectorOutbox.op == "replace_doc",
                )
            )
        )
        vector_pending_docs = {
            int((entry.payload or {}).get("doc_id") or 0)
            for entry in pending_entries
        }
        documents = list(
            await db.scalars(
                select(Document)
                .where(
                    Document.status.in_(("pending", "processing")),
                    Document.is_current.is_(True),
                )
                .order_by(Document.id.asc())
                .limit(_RECOVERY_LIMIT)
            )
        )
        for document in documents:
            if (
                document.id in active_task_docs
                or document.id in vector_pending_docs
            ):
                continue
            await task_queue.enqueue_document_task(
                db, document_id=document.id, kb_id=document.kb_id
            )
            stats["enqueued"] += 1
        await db.commit()

    if stats["tasks"] or stats["outbox"] or stats["enqueued"] or stats["expired"]:
        logger.warning(
            "任务恢复完成 tasks=%d outbox=%d enqueued=%d expired=%d"
            "（app/services/task_worker.py:recover_work）",
            stats["tasks"],
            stats["outbox"],
            stats["enqueued"],
            stats["expired"],
        )
    return stats


async def execute_task(task_id: int) -> bool:
    """执行指定任务；供 BackgroundTasks 加速，worker 轮询是兜底。"""
    async with get_session_maker()() as db:
        task = await task_queue.claim_task_by_id(db, task_id)
        if task is None:
            return False
        document_id = task.document_id
        task_type = task.task_type

    try:
        if document_id is not None and task_type in {"ingest", "reindex"}:
            await ingest_document(document_id)
    except Exception as exc:
        logger.exception(
            "入库任务执行失败 task_id=%s doc_id=%s"
            "（app/services/task_worker.py:execute_task）",
            task_id,
            document_id,
        )
        async with get_session_maker()() as db:
            await task_queue.fail_task(db, task_id, exc)
        return False

    async with get_session_maker()() as db:
        await task_queue.succeed_task(db, task_id)
    return True


async def process_document_task_once() -> bool:
    async with get_session_maker()() as db:
        task = await task_queue.claim_next_task(db)
        if task is None:
            return False
        task_id = task.id
        document_id = task.document_id
        task_type = task.task_type

    try:
        if document_id is not None and task_type in {"ingest", "reindex"}:
            await ingest_document(document_id)
    except Exception as exc:
        logger.exception(
            "入库任务执行失败 task_id=%s doc_id=%s"
            "（app/services/task_worker.py:process_document_task_once）",
            task_id,
            document_id,
        )
        async with get_session_maker()() as db:
            await task_queue.fail_task(db, task_id, exc)
        return True

    async with get_session_maker()() as db:
        await task_queue.succeed_task(db, task_id)
    return True


async def process_outbox_once() -> bool:
    async with get_session_maker()() as db:
        entry = await outbox.claim_next_outbox_entry(db)
        if entry is None:
            return False
        await outbox.run_claimed_entry(db, entry)
    return True


class TaskWorker:
    """单个进程内的后台消费者；事件唤醒，空闲时轻量轮询。"""

    def __init__(self) -> None:
        self._wake = asyncio.Event()
        self._task: asyncio.Task[Any] | None = None

    def wake(self) -> None:
        self._wake.set()

    async def start(self) -> None:
        if self._task is not None and not self._task.done():
            return
        self._task = asyncio.create_task(self._run(), name="asktofit-task-worker")

    async def stop(self) -> None:
        task = self._task
        self._task = None
        if task is None or task.done():
            return
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task

    async def _run(self) -> None:
        try:
            await recover_work()
        except Exception:
            logger.exception(
                "任务恢复失败，worker 将继续轮询"
                "（app/services/task_worker.py:TaskWorker._run）"
            )

        poll = max(0.2, float(Config.task_poll_seconds))
        while True:
            try:
                did_work = await process_document_task_once()
                did_work = await process_outbox_once() or did_work
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception(
                    "后台任务轮询失败（app/services/task_worker.py:TaskWorker._run）"
                )
                did_work = False

            if did_work:
                continue
            try:
                await asyncio.wait_for(self._wake.wait(), timeout=poll)
            except asyncio.TimeoutError:
                pass
            self._wake.clear()


task_worker = TaskWorker()
