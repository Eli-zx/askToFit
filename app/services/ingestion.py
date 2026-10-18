"""文档入库流水线：解析 → 指纹判重 → 父子切块 → 向量化 → MySQL + Milvus。

更新策略：
- 归一化正文完全一致：标记 unchanged，不新增重复文档；
- 同名或不同文件名但正文相似度达到阈值：作为同一逻辑文档的新版本；
- 新版本入库成功后才切换 current，旧版本在失败时保持可检索。
"""

from __future__ import annotations

import asyncio
import time
import uuid
from pathlib import Path
from typing import Any

from sqlalchemy import delete, func, select

from app.core.logger import get_logger
from app.db.session import get_session_maker
from app.models.chunk import Chunk
from app.models.document import Document
from app.models.knowledge_base import KnowledgeBase
from app.models.parent_chunk import ParentChunk
from app.services.dedup import (
    Fingerprint,
    content_hash,
    fingerprint_text,
    simhash_distance,
    simhash_similarity,
    text_similarity,
)
from app.services.embedding import embedding_service
from app.services.mineru_parser import parse_with_mineru
from app.services.parsers import (
    DocumentParseError,
    ParsedDocument,
    parse_document,
)
from app.services.search_index import invalidate_search_caches
from app.services.storage import delete_stored_file
from app.services.text_splitter import ParentChunkData, TextChunk, TextSplitter
from app.services.vector_store import vector_store
from config import Config

logger = get_logger(__name__)

_semaphore = asyncio.Semaphore(max(1, Config.max_concurrent_embedding))


def build_embedding_text(title: str | None, section: str | None, content: str) -> str:
    """标题与章节参与编码，提升上下文完整性与检索命中率。"""
    parts = [part.strip() for part in (title, section, content) if part and part.strip()]
    return "\n".join(parts)


def _truncate(value: str | None, limit: int) -> str | None:
    if not value:
        return None
    return value[:limit]


def document_text(parsed: ParsedDocument) -> str:
    """用于文档级判重的正文，不包含文件名等易变信息。"""
    return "\n".join(block.text for block in parsed.blocks if block.text.strip())


def build_vector_row(
    chunk_uid: str,
    chunk: Chunk | Any,
    dense_vector: list[float],
    sparse_vector: dict[int, float],
) -> dict[str, Any]:
    return {
        "chunk_uid": chunk_uid,
        "kb_id": int(chunk.kb_id),
        "doc_id": int(chunk.doc_id),
        "chunk_index": int(chunk.chunk_index),
        "content": chunk.content,
        "title": _truncate(chunk.title, 500) or "",
        "section": _truncate(chunk.section, 500) or "",
        "page": int(chunk.page or 0),
        "dense_vector": dense_vector,
        "sparse_vector": sparse_vector,
    }


async def _embed_chunks(
    chunks: list[TextChunk], parsed: ParsedDocument
) -> tuple[list[list[float]], list[dict[int, float]]]:
    dense_vectors: list[list[float]] = []
    sparse_vectors: list[dict[int, float]] = []
    batch_size = max(1, Config.embedding_batch_size)
    for start in range(0, len(chunks), batch_size):
        batch = chunks[start : start + batch_size]
        texts = [
            build_embedding_text(chunk.title or parsed.title, chunk.section, chunk.content)
            for chunk in batch
        ]
        dense, sparse = await embedding_service.embed_documents(texts, batch_size=batch_size)
        if len(dense) != len(batch) or len(sparse) != len(batch):
            raise RuntimeError("向量编码结果数量与切块数量不一致")
        dense_vectors.extend(dense)
        sparse_vectors.extend(sparse)
        logger.debug("向量编码进度 %d/%d", len(dense_vectors), len(chunks))
    return dense_vectors, sparse_vectors


async def _refresh_kb_count(db, kb_id: int) -> None:
    # 会话关闭了 autoflush，必须显式落盘 is_current 变更再统计
    await db.flush()
    total = await db.scalar(
        select(func.count())
        .select_from(Document)
        .where(Document.kb_id == kb_id, Document.is_current.is_(True))
    )
    kb = await db.get(KnowledgeBase, kb_id)
    if kb is not None:
        kb.document_count = int(total or 0)


async def _delete_rows(db, doc_id: int) -> None:
    await db.execute(delete(Chunk).where(Chunk.doc_id == doc_id))
    await db.execute(delete(ParentChunk).where(ParentChunk.doc_id == doc_id))


async def _delete_vectors_by_uids(uids: list[str]) -> None:
    for uid in uids:
        try:
            await vector_store.delete_by_uid(uid)
        except Exception:
            logger.exception("清理分块向量失败 uid=%s", uid)


async def _parse_stored_document(document: Document) -> ParsedDocument:
    """按文档记录的解析器解析，保证重建与判重口径一致。"""
    title = Path(document.original_name).stem
    if (document.parser or "auto") == "mineru":
        logger.info("使用 MinerU 深度解析 doc_id=%s", document.id)
        return await asyncio.to_thread(
            parse_with_mineru,
            document.file_path,
            document.file_type,
            title,
        )
    return await asyncio.to_thread(
        parse_document,
        document.file_path,
        document.file_type,
        title,
    )


async def _load_candidate_fingerprint(
    db, document: Document, *, need_text: bool = False
) -> tuple[Fingerprint | None, str | None]:
    """读取已有文档的归一化指纹；缺失时按需解析并回填。"""
    if document.content_hash and document.content_simhash and not need_text:
        return (
            Fingerprint(
                content_hash=document.content_hash,
                simhash=document.content_simhash,
                length=int(document.content_length or 0),
            ),
            None,
        )

    if not document.file_path or not Path(document.file_path).exists():
        return None, None

    try:
        parsed = await _parse_stored_document(document)
        text = document_text(parsed)
        fingerprint = fingerprint_text(text)
        document.content_hash = fingerprint.content_hash
        document.content_simhash = fingerprint.simhash
        document.content_length = fingerprint.length
        return fingerprint, text
    except Exception:
        logger.exception(
            "已有文档指纹回填失败 doc_id=%s file=%s",
            document.id,
            document.original_name,
        )
        return None, None


async def _decide_action(
    db,
    document: Document,
    fingerprint: Fingerprint,
    text: str,
) -> tuple[Document | None, float, str]:
    """返回 (被替换文档, 相似度, created/update/unchanged)。"""
    if document.force_new:
        return None, 0.0, "created"

    candidates = list(
        await db.scalars(
            select(Document)
            .where(
                Document.kb_id == document.kb_id,
                Document.id != document.id,
                Document.is_current.is_(True),
                Document.status == "completed",
            )
            .order_by(Document.updated_at.desc(), Document.id.desc())
        )
    )
    if not candidates:
        return None, 0.0, "created"

    same_name = next(
        (item for item in candidates if item.original_name == document.original_name),
        None,
    )
    if same_name is not None:
        candidate_fp, candidate_text = await _load_candidate_fingerprint(
            db, same_name, need_text=True
        )
        if candidate_fp is None:
            return same_name, 0.0, "update"
        if candidate_fp.content_hash == fingerprint.content_hash:
            return same_name, 1.0, "unchanged"
        similarity = text_similarity(
            text,
            candidate_text or "",
            left_simhash=fingerprint.simhash,
            right_simhash=candidate_fp.simhash,
        )
        return same_name, similarity, "update"

    best: tuple[Document | None, float] = (None, 0.0)
    threshold = Config.doc_dedup_similarity
    for candidate in candidates:
        candidate_fp, candidate_text = await _load_candidate_fingerprint(db, candidate)
        if candidate_fp is None:
            continue
        # 文件名不同但归一化正文一致（只改标点/格式）同样视为未变化
        if candidate_fp.content_hash == fingerprint.content_hash:
            return candidate, 1.0, "unchanged"
        shorter, longer = sorted((candidate_fp.length, fingerprint.length))
        if longer and shorter / longer < 0.35:
            continue
        distance = simhash_distance(candidate_fp.simhash, fingerprint.simhash)
        if distance > Config.doc_dedup_simhash_max_distance:
            continue
        if candidate_text is None:
            candidate_fp, candidate_text = await _load_candidate_fingerprint(
                db, candidate, need_text=True
            )
        if candidate_text is None:
            similarity = simhash_similarity(
                candidate_fp.simhash, fingerprint.simhash
            )
        else:
            similarity = text_similarity(
                text,
                candidate_text,
                left_simhash=fingerprint.simhash,
                right_simhash=candidate_fp.simhash,
            )
        if similarity > best[1]:
            best = (candidate, similarity)

    if best[0] is not None and best[1] >= threshold:
        return best[0], best[1], "update"
    return None, best[1], "created"


async def _mark_failed(
    document_id: int, error: Exception, *, preserve_current: bool = False
) -> None:
    maker = get_session_maker()
    async with maker() as db:
        document = await db.get(Document, document_id)
        if document is None:
            return
        document.status = "failed"
        document.error_message = str(error)[:1000]
        if preserve_current:
            # 重建索引失败：保留旧分块与向量，继续支撑检索
            document.is_current = True
            document.update_action = "reindex_failed"
        else:
            document.chunk_count = 0
            await _delete_rows(db, document_id)
            document.update_action = (
                "update_failed" if document.replaces_doc_id else "create_failed"
            )
            if document.replaces_doc_id:
                document.is_current = False
        await _refresh_kb_count(db, document.kb_id)
        await db.commit()
    if not preserve_current:
        try:
            await vector_store.delete_by_doc(document_id)
        except Exception:
            logger.exception("清理失败文档的向量数据时出错 doc_id=%s", document_id)


def _build_parent_records(
    document: Document, parents: list[ParentChunkData]
) -> tuple[list[ParentChunk], dict[int, str]]:
    records: list[ParentChunk] = []
    uid_by_index: dict[int, str] = {}
    for parent in parents:
        uid = uuid.uuid4().hex
        uid_by_index[parent.index] = uid
        records.append(
            ParentChunk(
                uid=uid,
                doc_id=document.id,
                kb_id=document.kb_id,
                parent_index=parent.index,
                content=parent.content,
                content_hash=parent.content_hash or content_hash(parent.content),
                title=_truncate(parent.title or document.original_name, 250),
                section=_truncate(parent.section, 250),
                page=parent.page,
                char_count=len(parent.content),
            )
        )
    return records, uid_by_index


def _build_child_records(
    document: Document,
    chunks: list[TextChunk],
    parent_uid_by_index: dict[int, str],
) -> list[Chunk]:
    records: list[Chunk] = []
    for index, chunk in enumerate(chunks):
        records.append(
            Chunk(
                uid=uuid.uuid4().hex,
                doc_id=document.id,
                kb_id=document.kb_id,
                chunk_index=index,
                parent_uid=parent_uid_by_index.get(chunk.parent_index),
                content=chunk.content,
                content_hash=chunk.content_hash or content_hash(chunk.content),
                title=_truncate(chunk.title or document.original_name, 250),
                section=_truncate(chunk.section, 250),
                page=chunk.page,
                char_count=len(chunk.content),
            )
        )
    return records


async def ingest_document(document_id: int) -> None:
    """后台任务入口。使用独立数据库会话，异常不向调用方抛出。"""
    async with _semaphore:
        started = time.perf_counter()
        maker = get_session_maker()
        async with maker() as db:
            document = await db.get(Document, document_id)
            if document is None:
                logger.warning("待入库文档不存在 doc_id=%s", document_id)
                return

            previous_current = bool(document.is_current)
            previous_chunk_count = int(document.chunk_count or 0)
            previous_action = document.update_action
            old_chunk_uids = list(
                await db.scalars(select(Chunk.uid).where(Chunk.doc_id == document.id))
            )
            reindexing = previous_current and previous_chunk_count > 0

            document.status = "processing"
            document.error_message = None
            await db.commit()
            logger.info(
                "开始解析文档 doc_id=%s kb_id=%s file=%s type=%s reindex=%s",
                document.id,
                document.kb_id,
                document.original_name,
                document.file_type,
                reindexing,
            )

            inserted_vector_uids: list[str] = []
            try:
                parsed = await _parse_stored_document(document)
                text = document_text(parsed)
                fingerprint = fingerprint_text(text)
                document.content_hash = fingerprint.content_hash
                document.content_simhash = fingerprint.simhash
                document.content_length = fingerprint.length

                candidate, similarity, action = await _decide_action(
                    db, document, fingerprint, text
                )

                if action == "unchanged" and candidate is not None:
                    document.status = "skipped"
                    document.update_action = "unchanged"
                    document.is_current = False
                    document.chunk_count = 0
                    document.similar_doc_id = candidate.id
                    document.similar_doc_name = candidate.original_name
                    document.similarity = 1.0
                    await _delete_rows(db, document.id)
                    await _refresh_kb_count(db, document.kb_id)
                    await db.commit()
                    delete_stored_file(document.file_path)
                    await invalidate_search_caches(document.kb_id)
                    logger.info(
                        "文档内容未变化，已跳过 doc_id=%s similar_to=%s",
                        document.id,
                        candidate.id,
                    )
                    return

                replacing = candidate if action == "update" else None
                if replacing is not None:
                    document.replaces_doc_id = replacing.id
                    document.version = int(replacing.version or 1) + 1
                    document.similar_doc_id = replacing.id
                    document.similar_doc_name = replacing.original_name
                    document.similarity = similarity
                    document.is_current = False
                    document.update_action = "updating"
                    logger.info(
                        "检测到文档更新 doc_id=%s replaces=%s similarity=%.4f version=%d",
                        document.id,
                        replacing.id,
                        similarity,
                        document.version,
                    )
                else:
                    document.is_current = True
                    document.update_action = (
                        previous_action if reindexing else "pending"
                    )
                await db.commit()

                splitter = TextSplitter(
                    chunk_size=document.chunk_size,
                    chunk_overlap=document.chunk_overlap,
                    min_size=Config.min_chunk_size,
                    parent_size=Config.parent_chunk_size,
                    dedup_similarity=Config.chunk_near_dup_similarity,
                )
                parents, chunks = await asyncio.to_thread(
                    splitter.split_with_parents, parsed.blocks
                )
                if not chunks:
                    raise DocumentParseError("切块结果为空，请检查文档内容")

                logger.info(
                    "解析完成 doc_id=%s title=%s chars=%d parents=%d chunks=%d",
                    document.id,
                    parsed.title,
                    parsed.text_length,
                    len(parents),
                    len(chunks),
                )

                dense_vectors, sparse_vectors = await _embed_chunks(chunks, parsed)

                parent_records, parent_uid_by_index = _build_parent_records(
                    document, parents
                )
                records = _build_child_records(document, chunks, parent_uid_by_index)
                vector_rows = [
                    build_vector_row(
                        record.uid,
                        record,
                        dense_vectors[index],
                        sparse_vectors[index],
                    )
                    for index, record in enumerate(records)
                ]

                # 先写入新向量；DB 事务成功后才切换 current 并删除旧版本数据
                inserted_vector_uids = [record.uid for record in records]
                await vector_store.insert_chunks(vector_rows)

                # 同一文档重建索引：旧分块在事务内整体替换
                await _delete_rows(db, document.id)
                db.add_all(parent_records)
                db.add_all(records)
                document.chunk_count = len(records)
                document.status = "completed"
                document.error_message = None
                document.is_current = True
                if replacing is not None:
                    document.update_action = "updated"
                    replacing.is_current = False
                    replacing.update_action = "superseded"
                    await _delete_rows(db, replacing.id)
                elif reindexing:
                    document.update_action = "reindexed"
                else:
                    document.update_action = "created"

                await _refresh_kb_count(db, document.kb_id)
                await db.commit()
                # 知识内容变化后旧检索/回答缓存必须失效
                await invalidate_search_caches(document.kb_id)

                if replacing is not None:
                    try:
                        await vector_store.delete_by_doc(replacing.id)
                    except Exception:
                        logger.exception(
                            "删除旧版本文档向量失败 doc_id=%s", replacing.id
                        )
                    delete_stored_file(replacing.file_path)
                if reindexing and old_chunk_uids:
                    await _delete_vectors_by_uids(old_chunk_uids)

                elapsed = time.perf_counter() - started
                logger.info(
                    "文档入库完成 doc_id=%s action=%s chunks=%d parents=%d elapsed=%.2fs",
                    document.id,
                    document.update_action,
                    len(records),
                    len(parents),
                    elapsed,
                )
            except Exception as exc:  # noqa: BLE001
                logger.exception("文档入库失败 doc_id=%s", document_id)
                await db.rollback()
                if inserted_vector_uids:
                    await _delete_vectors_by_uids(inserted_vector_uids)
                await _mark_failed(
                    document_id, exc, preserve_current=reindexing
                )


async def refresh_chunk_vector(chunk: Chunk) -> None:
    """分块内容编辑后重新编码并写入 Milvus。"""
    text = build_embedding_text(chunk.title, chunk.section, chunk.content)
    dense, sparse = await embedding_service.embed_documents([text])
    row = build_vector_row(chunk.uid, chunk, dense[0], sparse[0])
    await vector_store.upsert_chunk(row)
    logger.info("分块向量已更新 chunk_uid=%s", chunk.uid)
