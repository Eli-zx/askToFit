"""持久化任务队列测试：去重、租约、重试与启动恢复。"""

from datetime import timedelta

import pytest
from sqlalchemy import select

from app.models.async_task import AsyncTask
from app.models.document import Document
from app.models.knowledge_base import KnowledgeBase
from app.models.user import User
from app.services import task_queue, task_worker
from app.services.task_queue import naive_utcnow
from config import Config


async def _seed_document(db_maker, *, status: str = "pending") -> tuple[int, int]:
    async with db_maker() as db:
        user = User(username="queue_user", password_hash="x")
        db.add(user)
        await db.flush()
        kb = KnowledgeBase(owner_id=user.id, name="任务队列库")
        db.add(kb)
        await db.flush()
        document = Document(
            kb_id=kb.id,
            original_name="policy.md",
            stored_name="policy.md",
            file_path="/tmp/policy.md",
            file_type="md",
            status=status,
            is_current=True,
        )
        db.add(document)
        await db.commit()
        return kb.id, document.id


@pytest.mark.asyncio
async def test_enqueue_document_task_is_deduplicated(db_maker) -> None:
    kb_id, document_id = await _seed_document(db_maker)
    async with db_maker() as db:
        first = await task_queue.enqueue_document_task(
            db, document_id=document_id, kb_id=kb_id
        )
        second = await task_queue.enqueue_document_task(
            db, document_id=document_id, kb_id=kb_id
        )
        await db.commit()

        assert first.id == second.id
        rows = list(
            await db.scalars(
                select(AsyncTask).where(AsyncTask.document_id == document_id)
            )
        )
        assert len(rows) == 1


@pytest.mark.asyncio
async def test_claim_task_sets_running_and_attempts(db_maker) -> None:
    kb_id, document_id = await _seed_document(db_maker)
    async with db_maker() as db:
        task = await task_queue.enqueue_document_task(
            db, document_id=document_id, kb_id=kb_id
        )
        await db.commit()

        claimed = await task_queue.claim_next_task(db)

        assert claimed is not None
        assert claimed.id == task.id
        assert claimed.status == "running"
        assert claimed.attempts == 1
        assert claimed.lease_until is not None


@pytest.mark.asyncio
async def test_expired_running_task_is_reclaimed(db_maker) -> None:
    kb_id, document_id = await _seed_document(db_maker)
    async with db_maker() as db:
        task = await task_queue.enqueue_document_task(
            db, document_id=document_id, kb_id=kb_id
        )
        task.status = "running"
        task.attempts = 1
        task.lease_until = naive_utcnow() - timedelta(seconds=1)
        await db.commit()

        claimed = await task_queue.claim_next_task(db)

        assert claimed is not None
        assert claimed.id == task.id
        assert claimed.attempts == 2


@pytest.mark.asyncio
async def test_fail_task_retries_then_marks_document_failed(db_maker) -> None:
    kb_id, document_id = await _seed_document(db_maker)
    async with db_maker() as db:
        task = AsyncTask(
            task_type="ingest",
            document_id=document_id,
            kb_id=kb_id,
            status="running",
            attempts=1,
            max_attempts=2,
        )
        db.add(task)
        await db.commit()

        will_retry = await task_queue.fail_task(db, task.id, RuntimeError("boom"))
        assert will_retry is True
        await db.refresh(task)
        assert task.status == "pending"

        task.status = "running"
        task.attempts = 2
        await db.commit()
        will_retry = await task_queue.fail_task(db, task.id, RuntimeError("boom"))
        assert will_retry is False
        await db.refresh(task)
        assert task.status == "failed"

        document = await db.get(Document, document_id)
        assert document is not None
        assert document.status == "failed"


@pytest.mark.asyncio
async def test_recover_enqueues_processing_document_without_task(
    db_maker, monkeypatch
) -> None:
    _, document_id = await _seed_document(db_maker, status="processing")
    monkeypatch.setattr(task_worker, "get_session_maker", lambda: db_maker)
    monkeypatch.setattr(Config, "upload_task_ttl", 86400)

    stats = await task_worker.recover_work()

    assert stats["enqueued"] == 1
    async with db_maker() as db:
        task = await db.scalar(
            select(AsyncTask).where(AsyncTask.document_id == document_id)
        )
        assert task is not None
        assert task.status == "pending"
