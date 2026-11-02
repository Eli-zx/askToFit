"""向量 outbox：MySQL 与 Milvus 的最终一致同步。

写入顺序固定为“先提交数据库，再同步向量”。Milvus 失败时条目保留在
``vector_outbox`` 中由后台重试；进程重启后继续处理，不会出现“库里有记录
但召回不到”且无人修复的情况。
"""

from __future__ import annotations

from datetime import timedelta
from typing import Any

from sqlalchemy import and_, delete, func, or_, select

from app.core.logger import get_logger
from app.db.session import get_session_maker
from app.models.chunk import Chunk
from app.models.document import Document
from app.models.knowledge_base import KnowledgeBase
from app.models.parent_chunk import ParentChunk
from app.models.vector_outbox import VectorOutbox
from app.services.embedding import embedding_service
from app.services.storage import delete_stored_file
from app.services.task_queue import naive_utcnow
from app.services.vector_store import vector_store
from config import Config

logger = get_logger(__name__)

ACTIVE_STATUSES = ("pending", "running")
TERMINAL_STATUSES = ("done", "failed")


def _backoff_seconds(attempts: int) -> int:
    return min(300, max(2, 2 ** max(0, attempts)))


async def enqueue_outbox(
    db,
    op: str,
    payload: dict[str, Any],
    *,
    max_attempts: int | None = None,
) -> VectorOutbox:
    entry = VectorOutbox(
        op=op,
        payload=payload,
        max_attempts=max_attempts or max(1, Config.vector_outbox_max_attempts),
    )
    db.add(entry)
    await db.flush()
    return entry


async def claim_next_outbox_entry(db, *, now=None) -> VectorOutbox | None:
    current = now or naive_utcnow()
    entry = await db.scalar(
        select(VectorOutbox)
        .where(
            VectorOutbox.available_at <= current,
            or_(
                VectorOutbox.status == "pending",
                and_(
                    VectorOutbox.status == "running",
                    VectorOutbox.lease_until.is_not(None),
                    VectorOutbox.lease_until < current,
                ),
            ),
        )
        .order_by(VectorOutbox.id.asc())
        .limit(1)
        .with_for_update(skip_locked=True)
    )
    if entry is None:
        return None
    entry.status = "running"
    entry.attempts = int(entry.attempts or 0) + 1
    entry.lease_until = current + timedelta(
        seconds=Config.vector_outbox_lease_seconds
    )
    await db.commit()
    return entry


async def _load_vector_rows(db, doc_id: int) -> list[dict[str, Any]] | None:
    """outbox 重试路径：从数据库分块重建向量（模型已预热，正常几秒内完成）。"""
    from app.services.ingestion import build_embedding_text, build_vector_row

    document = await db.get(Document, doc_id)
    if document is None:
        return None
    chunks = list(
        await db.scalars(
            select(Chunk)
            .where(Chunk.doc_id == doc_id)
            .order_by(Chunk.chunk_index.asc())
        )
    )
    if not chunks:
        return []
    texts = [
        build_embedding_text(
            chunk.title or document.original_name, chunk.section, chunk.content
        )
        for chunk in chunks
    ]
    dense, sparse = await embedding_service.embed_documents(texts)
    if len(dense) != len(chunks) or len(sparse) != len(chunks):
        raise RuntimeError("向量编码结果数量与分块数量不一致")
    return [
        build_vector_row(chunk.uid, chunk, dense[index], sparse[index])
        for index, chunk in enumerate(chunks)
    ]


async def _delete_doc_rows(db, doc_id: int) -> None:
    await db.execute(delete(Chunk).where(Chunk.doc_id == doc_id))
    await db.execute(delete(ParentChunk).where(ParentChunk.doc_id == doc_id))


async def _refresh_kb_count(db, kb_id: int) -> None:
    await db.flush()
    total = await db.scalar(
        select(func.count())
        .select_from(Document)
        .where(Document.kb_id == kb_id, Document.is_current.is_(True))
    )
    kb = await db.get(KnowledgeBase, kb_id)
    if kb is not None:
        kb.document_count = int(total or 0)


async def apply_entry(
    db,
    entry: VectorOutbox,
    *,
    vector_rows: list[dict[str, Any]] | None = None,
) -> list[VectorOutbox]:
    """执行一条 outbox；返回需要随后执行的派生条目（如旧版本文档清理）。"""
    payload = dict(entry.payload or {})
    followups: list[VectorOutbox] = []

    if entry.op == "replace_doc":
        doc_id = int(payload["doc_id"])
        # 文档或所属知识库可能在同步前已被删除；用新查询绕过身份映射缓存，
        # 避免把已删除文档的向量重新写回 Milvus。
        still_exists = await db.scalar(
            select(Document.id).where(Document.id == doc_id)
        )
        if still_exists is None:
            logger.warning(
                "文档已删除，跳过向量同步 doc_id=%s outbox_id=%s"
                "（app/services/outbox.py:apply_entry）",
                doc_id,
                entry.id,
            )
            return followups
        rows = (
            vector_rows
            if vector_rows is not None
            else await _load_vector_rows(db, doc_id)
        )
        if rows is None:
            logger.warning(
                "文档已不存在，跳过向量同步 doc_id=%s outbox_id=%s"
                "（app/services/outbox.py:apply_entry）",
                doc_id,
                entry.id,
            )
            return followups
        if rows:
            await vector_store.upsert_chunks(rows)
        # 重建索引时删除本次已不存在的旧分块向量，避免残留命中
        for stale_uid in payload.get("stale_uids") or ():
            await vector_store.delete_by_uid(str(stale_uid))

        document = await db.get(Document, doc_id)
        if document is not None:
            document.status = "completed"
            document.error_message = None
            document.is_current = True
            document.update_action = str(payload.get("action") or "created")
            replacing_id = payload.get("replaces_doc_id")
            if replacing_id:
                old = await db.get(Document, int(replacing_id))
                if old is not None:
                    old.is_current = False
                    old.update_action = "superseded"
                    await _delete_doc_rows(db, old.id)
                    delete_stored_file(old.file_path)
                    followups.append(
                        await enqueue_outbox(
                            db, "delete_doc", {"doc_id": int(replacing_id)}
                        )
                    )
            await _refresh_kb_count(db, document.kb_id)
        return followups

    if entry.op == "upsert_chunk":
        from app.services.ingestion import build_embedding_text, build_vector_row

        uid = str(payload["chunk_uid"])
        chunk = await db.scalar(select(Chunk).where(Chunk.uid == uid))
        if chunk is None:
            logger.warning(
                "分块已不存在，跳过向量更新 chunk_uid=%s"
                "（app/services/outbox.py:apply_entry）",
                uid,
            )
            return followups
        text = build_embedding_text(chunk.title, chunk.section, chunk.content)
        dense, sparse = await embedding_service.embed_documents([text])
        await vector_store.upsert_chunk(
            build_vector_row(chunk.uid, chunk, dense[0], sparse[0])
        )
        return followups

    if entry.op == "delete_doc":
        await vector_store.delete_by_doc(int(payload["doc_id"]))
        return followups

    if entry.op == "delete_kb":
        await vector_store.delete_by_kb(int(payload["kb_id"]))
        return followups

    if entry.op == "delete_chunk":
        await vector_store.delete_by_uid(str(payload["chunk_uid"]))
        return followups

    raise ValueError(f"未知的向量 outbox 操作：{entry.op}")


async def _mark_failure(db, entry_id: int, error: BaseException) -> bool:
    """记录失败并决定重试；返回是否还会重试。"""
    entry = await db.get(VectorOutbox, entry_id)
    if entry is None:
        return False

    now = naive_utcnow()
    entry.lease_until = None
    entry.last_error = str(error)[:1000]
    if int(entry.attempts or 0) < int(entry.max_attempts or 1):
        entry.status = "pending"
        entry.available_at = now + timedelta(
            seconds=_backoff_seconds(int(entry.attempts or 0))
        )
        await db.commit()
        return True

    entry.status = "failed"
    entry.finished_at = now
    payload = dict(entry.payload or {})
    if entry.op == "replace_doc" and payload.get("doc_id"):
        document = await db.get(Document, int(payload["doc_id"]))
        if document is not None and document.status != "completed":
            document.status = "failed"
            document.error_message = str(error)[:1000]
    await db.commit()
    return False


async def run_claimed_entry(
    db,
    entry: VectorOutbox,
    *,
    vector_rows: list[dict[str, Any]] | None = None,
) -> bool:
    """执行一条已处于 running 的 outbox；成功返回 True，失败自动重试/落败。"""
    entry_id = entry.id
    op = entry.op
    try:
        followups = await apply_entry(db, entry, vector_rows=vector_rows)
        entry.status = "done"
        entry.lease_until = None
        entry.last_error = None
        entry.finished_at = naive_utcnow()
        await db.commit()
    except Exception as exc:
        await db.rollback()
        logger.exception(
            "向量同步失败 op=%s outbox_id=%s（app/services/outbox.py:run_claimed_entry）",
            op,
            entry_id,
        )
        await _mark_failure(db, entry_id, exc)
        return False

    for followup in followups:
        await process_entry_in_session(db, followup)
    return True


async def process_entry_in_session(
    db,
    entry: VectorOutbox,
    *,
    vector_rows: list[dict[str, Any]] | None = None,
) -> bool:
    """在当前会话领取并执行一条 outbox（加速路径使用）。"""
    now = naive_utcnow()
    entry.status = "running"
    entry.attempts = int(entry.attempts or 0) + 1
    entry.lease_until = now + timedelta(
        seconds=Config.vector_outbox_lease_seconds
    )
    await db.commit()
    return await run_claimed_entry(db, entry, vector_rows=vector_rows)


async def flush_outbox_entry(
    entry_id: int,
    *,
    vector_rows: list[dict[str, Any]] | None = None,
) -> bool:
    """按 ID 立即尝试同步（请求后的加速路径），失败留给后台 worker。"""
    async with get_session_maker()() as db:
        entry = await db.get(VectorOutbox, entry_id)
        if entry is None or entry.status not in ACTIVE_STATUSES:
            return False
        if (
            entry.status == "running"
            and entry.lease_until is not None
            and entry.lease_until > naive_utcnow()
        ):
            return False
        if entry.available_at > naive_utcnow():
            return False
        return await process_entry_in_session(
            db, entry, vector_rows=vector_rows
        )


async def reset_expired_lease(db, *, now=None) -> int:
    current = now or naive_utcnow()
    entries = list(
        await db.scalars(
            select(VectorOutbox).where(
                VectorOutbox.status == "running",
                VectorOutbox.lease_until.is_not(None),
                VectorOutbox.lease_until < current,
            )
        )
    )
    for entry in entries:
        entry.status = "pending"
        entry.lease_until = None
        entry.available_at = current
    if entries:
        await db.commit()
    return len(entries)


async def cleanup_finished_entries(db, *, ttl_seconds: int | None = None) -> int:
    ttl = Config.upload_task_ttl if ttl_seconds is None else ttl_seconds
    cutoff = naive_utcnow() - timedelta(seconds=max(60, ttl))
    result = await db.execute(
        delete(VectorOutbox).where(
            VectorOutbox.status.in_(TERMINAL_STATUSES),
            VectorOutbox.finished_at.is_not(None),
            VectorOutbox.finished_at < cutoff,
        )
    )
    await db.commit()
    return int(result.rowcount or 0)
