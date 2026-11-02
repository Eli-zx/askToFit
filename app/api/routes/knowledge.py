"""知识库、文档上传解析、分块预览与编辑接口。"""

from __future__ import annotations

import asyncio
import shutil
from pathlib import Path

from fastapi import (
    APIRouter,
    BackgroundTasks,
    Depends,
    File,
    Form,
    HTTPException,
    Query,
    UploadFile,
    status,
)
from sqlalchemy import delete, func, select, true
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_admin, get_current_user
from app.core.logger import get_logger
from app.db.session import get_db
from app.models.chunk import Chunk
from app.models.document import Document
from app.models.knowledge_base import KnowledgeBase
from app.models.parent_chunk import ParentChunk
from app.models.user import User
from app.schemas.knowledge import (
    ChunkListOut,
    ChunkOut,
    ChunkUpdate,
    DocumentOut,
    KnowledgeBaseCreate,
    KnowledgeBaseOut,
    KnowledgeBaseUpdate,
    ParentChunkOut,
)
from app.services.dedup import content_hash
from app.services.mineru_parser import (
    mineru_status,
    mineru_supported,
)
from app.services.outbox import enqueue_outbox, flush_outbox_entry
from app.services.search_index import invalidate_search_caches
from app.services.storage import (
    UnsupportedFileTypeError,
    UploadTooLargeError,
    delete_stored_file,
    save_upload,
)
from app.services.task_queue import enqueue_document_task
from app.services.task_worker import execute_task, task_worker
from config import Config

logger = get_logger(__name__)

router = APIRouter(tags=["知识库"])

VALID_MODES = {"dense", "hybrid"}
VALID_PARSERS = {"auto", "mineru"}


@router.get("/parsers/options", summary="解析器可用性")
async def parser_options(user: User = Depends(get_current_admin)) -> dict:
    """返回上传弹窗需要的解析器状态，MinerU 不可用时给出原因。"""
    status = await asyncio.to_thread(mineru_status)
    return {
        "auto": {
            "available": True,
            "label": "标准解析",
            "description": "内置轻量解析，适合文本型 PDF、Markdown 与常规 Office 文档",
        },
        "mineru": {
            "available": bool(status["available"]),
            "label": "深度解析（MinerU）",
            "description": "复杂表格、扫描件、多栏排版识别更准确，解析耗时更长",
            "image": status["image"],
            "reason": status.get("reason"),
        },
    }


# --------------------------------------------------------------------------- #
# 权限与查询辅助
# --------------------------------------------------------------------------- #
def _kb_scope(user: User):
    """知识库可见范围：管理员全量；普通用户只读公开知识库。"""
    if user.is_admin:
        return true()
    return KnowledgeBase.is_public.is_(True)


async def _get_kb(kb_id: int, db: AsyncSession) -> KnowledgeBase:
    # 管理接口统一由 get_current_admin 鉴权，这里只按主键取数
    kb = await db.get(KnowledgeBase, kb_id)
    if kb is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "知识库不存在")
    return kb


async def _get_document(doc_id: int, db: AsyncSession) -> Document:
    document = await db.get(Document, doc_id)
    if document is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "文档不存在")
    return document


async def _get_chunk(uid: str, db: AsyncSession) -> tuple[Chunk, Document]:
    stmt = (
        select(Chunk, Document)
        .join(Document, Chunk.doc_id == Document.id)
        .where(Chunk.uid == uid)
    )
    row = (await db.execute(stmt)).first()
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "分块不存在")
    return row[0], row[1]


async def _current_document_count(db: AsyncSession, kb_id: int) -> int:
    return int(
        await db.scalar(
            select(func.count())
            .select_from(Document)
            .where(Document.kb_id == kb_id, Document.is_current.is_(True))
        )
        or 0
    )


async def _refresh_parent_chunk(db: AsyncSession, parent_uid: str | None) -> None:
    """子分块编辑或删除后，同步父分块正文与指纹。"""
    if not parent_uid:
        return
    parent = await db.scalar(select(ParentChunk).where(ParentChunk.uid == parent_uid))
    if parent is None:
        return
    contents = list(
        await db.scalars(
            select(Chunk.content)
            .where(Chunk.parent_uid == parent_uid)
            .order_by(Chunk.chunk_index.asc())
        )
    )
    if not contents:
        await db.execute(delete(ParentChunk).where(ParentChunk.uid == parent_uid))
        return
    parent.content = "\n".join(contents)
    parent.content_hash = content_hash(parent.content)
    parent.char_count = len(parent.content)


async def _parent_map(
    db: AsyncSession, chunks: list[Chunk]
) -> dict[str, ParentChunk]:
    uids = {chunk.parent_uid for chunk in chunks if chunk.parent_uid}
    if not uids:
        return {}
    rows = await db.scalars(select(ParentChunk).where(ParentChunk.uid.in_(uids)))
    return {row.uid: row for row in rows}


def _chunk_out(chunk: Chunk, parents: dict[str, ParentChunk]) -> ChunkOut:
    item = ChunkOut.model_validate(chunk)
    parent = parents.get(chunk.parent_uid or "")
    if parent is not None:
        item.parent_index = parent.parent_index
        item.parent_content = parent.content
    return item


# --------------------------------------------------------------------------- #
# 知识库
# --------------------------------------------------------------------------- #
@router.post(
    "/knowledge_bases",
    response_model=KnowledgeBaseOut,
    status_code=status.HTTP_201_CREATED,
    summary="创建知识库",
)
async def create_knowledge_base(
    payload: KnowledgeBaseCreate,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_admin),
) -> KnowledgeBase:
    exists = await db.scalar(
        select(KnowledgeBase.id).where(
            KnowledgeBase.owner_id == user.id, KnowledgeBase.name == payload.name
        )
    )
    if exists is not None:
        raise HTTPException(status.HTTP_409_CONFLICT, "同名知识库已存在")

    kb = KnowledgeBase(
        owner_id=user.id,
        name=payload.name.strip(),
        description=payload.description,
        embedding_mode=payload.embedding_mode,
        chunk_size=payload.chunk_size,
        chunk_overlap=payload.chunk_overlap,
        is_public=payload.is_public,
    )
    db.add(kb)
    try:
        await db.commit()
    except IntegrityError:
        await db.rollback()
        raise HTTPException(status.HTTP_409_CONFLICT, "同名知识库已存在") from None
    await db.refresh(kb)
    logger.info("创建知识库 kb_id=%s owner=%s name=%s", kb.id, user.username, kb.name)
    return kb


@router.get(
    "/knowledge_bases", response_model=list[KnowledgeBaseOut], summary="知识库列表"
)
async def list_knowledge_bases(
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
) -> list[KnowledgeBase]:
    result = await db.scalars(
        select(KnowledgeBase)
        .where(_kb_scope(user))
        .order_by(KnowledgeBase.updated_at.desc(), KnowledgeBase.id.desc())
    )
    return list(result)


@router.get(
    "/knowledge_bases/{kb_id}", response_model=KnowledgeBaseOut, summary="知识库详情"
)
async def get_knowledge_base(
    kb_id: int,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_admin),
) -> KnowledgeBase:
    return await _get_kb(kb_id, db)


@router.patch(
    "/knowledge_bases/{kb_id}", response_model=KnowledgeBaseOut, summary="更新知识库"
)
async def update_knowledge_base(
    kb_id: int,
    payload: KnowledgeBaseUpdate,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_admin),
) -> KnowledgeBase:
    kb = await _get_kb(kb_id, db)
    if payload.name is not None and payload.name.strip() != kb.name:
        # 管理员改别人的库时，重名判断要对齐库的创建者，而不是管理员自己
        duplicate = await db.scalar(
            select(KnowledgeBase.id).where(
                KnowledgeBase.owner_id == kb.owner_id,
                KnowledgeBase.name == payload.name.strip(),
                KnowledgeBase.id != kb.id,
            )
        )
        if duplicate is not None:
            raise HTTPException(status.HTTP_409_CONFLICT, "同名知识库已存在")
        kb.name = payload.name.strip()
    if payload.description is not None:
        kb.description = payload.description
    if payload.is_public is not None:
        kb.is_public = payload.is_public
    try:
        await db.commit()
    except IntegrityError:
        await db.rollback()
        raise HTTPException(status.HTTP_409_CONFLICT, "同名知识库已存在") from None
    await db.refresh(kb)
    return kb


@router.delete(
    "/knowledge_bases/{kb_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="删除知识库",
)
async def delete_knowledge_base(
    kb_id: int,
    background_tasks: BackgroundTasks,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_admin),
) -> None:
    kb = await _get_kb(kb_id, db)
    documents = list(
        await db.scalars(
            select(Document).where(Document.kb_id == kb.id).with_for_update()
        )
    )
    await db.execute(delete(Chunk).where(Chunk.kb_id == kb.id))
    await db.execute(delete(ParentChunk).where(ParentChunk.kb_id == kb.id))
    await db.execute(delete(Document).where(Document.kb_id == kb.id))
    await db.execute(delete(KnowledgeBase).where(KnowledgeBase.id == kb.id))
    # 先提交数据库，向量删除交给 outbox，Milvus 短暂不可用也不会留下“删不掉”的库
    entry = await enqueue_outbox(db, "delete_kb", {"kb_id": kb.id})
    await db.commit()
    await invalidate_search_caches(kb.id)
    background_tasks.add_task(flush_outbox_entry, entry.id)
    task_worker.wake()

    for document in documents:
        delete_stored_file(document.file_path)
    directory = Path(Config.upload_dir) / f"kb_{kb.id}"
    root = Path(Config.upload_dir).resolve()
    if directory.resolve() != root and root in directory.resolve().parents:
        shutil.rmtree(directory, ignore_errors=True)

    logger.info("知识库已删除 kb_id=%s name=%s", kb.id, kb.name)


# --------------------------------------------------------------------------- #
# 文档
# --------------------------------------------------------------------------- #
@router.post(
    "/knowledge_bases/{kb_id}/documents",
    response_model=DocumentOut,
    status_code=status.HTTP_202_ACCEPTED,
    summary="上传文档并异步解析入库",
)
async def upload_document(
    kb_id: int,
    background_tasks: BackgroundTasks,
    file: UploadFile = File(...),
    chunk_size: int | None = Form(None),
    chunk_overlap: int | None = Form(None),
    embedding_mode: str | None = Form(None),
    force_new: bool = Form(False),
    parser: str = Form("auto"),
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_admin),
) -> Document:
    kb = await _get_kb(kb_id, db)

    size = chunk_size if chunk_size is not None else kb.chunk_size
    overlap = chunk_overlap if chunk_overlap is not None else kb.chunk_overlap
    mode = (embedding_mode or kb.embedding_mode).lower()
    if not 100 <= size <= 2000:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "切块大小需在 100-2000 之间")
    if not 0 <= overlap < size:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "重叠大小需大于等于 0 且小于切块大小")
    if mode not in VALID_MODES:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "检索模式仅支持 dense / hybrid")

    selected_parser = (parser or "auto").strip().lower()
    if selected_parser not in VALID_PARSERS:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST, "解析器仅支持 auto / mineru"
        )
    if selected_parser == "mineru":
        extension = Path(file.filename or "").suffix.lower().lstrip(".")
        if not mineru_supported(extension):
            raise HTTPException(
                status.HTTP_400_BAD_REQUEST,
                "MinerU 暂不支持该格式，请使用标准解析或转换为 PDF",
            )
        status_info = await asyncio.to_thread(mineru_status)
        if not status_info["available"]:
            raise HTTPException(
                status.HTTP_400_BAD_REQUEST,
                status_info.get("reason") or "MinerU 未就绪",
            )

    try:
        saved = await save_upload(file, kb.id)
    except UnsupportedFileTypeError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc)) from None
    except UploadTooLargeError as exc:
        raise HTTPException(status.HTTP_413_CONTENT_TOO_LARGE, str(exc)) from None
    except ValueError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc)) from None

    document = Document(
        kb_id=kb.id,
        original_name=saved.original_name,
        stored_name=saved.stored_name,
        file_path=str(saved.path),
        file_type=saved.file_type,
        file_size=saved.file_size,
        status="pending",
        chunk_size=size,
        chunk_overlap=overlap,
        embedding_mode=mode,
        parser=selected_parser,
        version=1,
        is_current=True,
        update_action="pending",
        force_new=force_new,
    )
    db.add(document)
    try:
        await db.flush()
        task = await enqueue_document_task(db, document_id=document.id, kb_id=kb.id)
        await db.commit()
    except Exception:
        await db.rollback()
        delete_stored_file(saved.path)
        raise

    await db.refresh(document)
    # 任务已持久化；BackgroundTasks 只负责尽快执行，重启后由 worker 恢复
    background_tasks.add_task(execute_task, task.id)
    task_worker.wake()
    logger.info(
        "文档已上传 doc_id=%s kb_id=%s file=%s size=%d task_id=%s",
        document.id,
        kb.id,
        saved.original_name,
        saved.file_size,
        task.id,
    )
    return document


@router.get(
    "/knowledge_bases/{kb_id}/documents",
    response_model=list[DocumentOut],
    summary="知识库文档列表",
)
async def list_documents(
    kb_id: int,
    status_filter: str | None = Query(None, alias="status"),
    include_history: bool = Query(False),
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_admin),
) -> list[Document]:
    kb = await _get_kb(kb_id, db)
    stmt = select(Document).where(Document.kb_id == kb.id)
    if not include_history:
        stmt = stmt.where(Document.is_current.is_(True))
    if status_filter:
        stmt = stmt.where(Document.status == status_filter)
    stmt = stmt.order_by(Document.created_at.desc(), Document.id.desc())
    return list(await db.scalars(stmt))


@router.get("/documents/{doc_id}", response_model=DocumentOut, summary="文档详情")
async def get_document(
    doc_id: int,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_admin),
) -> Document:
    return await _get_document(doc_id, db)


@router.post(
    "/documents/{doc_id}/reindex",
    response_model=DocumentOut,
    status_code=status.HTTP_202_ACCEPTED,
    summary="重新解析入库",
)
async def reindex_document(
    doc_id: int,
    background_tasks: BackgroundTasks,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_admin),
) -> Document:
    document = await _get_document(doc_id, db)
    if not document.is_current:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "历史版本或已跳过文档不能重新入库")
    if document.status == "processing":
        raise HTTPException(status.HTTP_409_CONFLICT, "文档正在处理中，请稍后再试")
    if not Path(document.file_path).exists():
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "原始文件已丢失，请重新上传")

    document.status = "pending"
    document.error_message = None
    await db.flush()
    task = await enqueue_document_task(
        db, document_id=document.id, kb_id=document.kb_id
    )
    await db.commit()
    await db.refresh(document)
    background_tasks.add_task(execute_task, task.id)
    task_worker.wake()
    logger.info("文档重新入库 doc_id=%s task_id=%s", document.id, task.id)
    return document


@router.delete(
    "/documents/{doc_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="删除文档",
)
async def delete_document(
    doc_id: int,
    background_tasks: BackgroundTasks,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_admin),
) -> None:
    document = await _get_document(doc_id, db)
    # 与入库任务串行化，避免删除与正在提交的分块互相覆盖
    await db.execute(
        select(Document.id).where(Document.id == document.id).with_for_update()
    )

    kb_id = document.kb_id
    await db.execute(delete(Chunk).where(Chunk.doc_id == document.id))
    await db.execute(delete(ParentChunk).where(ParentChunk.doc_id == document.id))
    await db.execute(delete(Document).where(Document.id == document.id))
    await db.flush()
    kb = await db.get(KnowledgeBase, kb_id)
    if kb is not None:
        kb.document_count = await _current_document_count(db, kb_id)
    entry = await enqueue_outbox(db, "delete_doc", {"doc_id": document.id})
    await db.commit()
    await invalidate_search_caches(kb_id)
    background_tasks.add_task(flush_outbox_entry, entry.id)
    task_worker.wake()
    delete_stored_file(document.file_path)
    logger.info("文档已删除 doc_id=%s file=%s", document.id, document.original_name)


# --------------------------------------------------------------------------- #
# 分块
# --------------------------------------------------------------------------- #
@router.get(
    "/documents/{doc_id}/chunks",
    response_model=ChunkListOut,
    summary="文档分块预览",
)
async def list_chunks(
    doc_id: int,
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    keyword: str | None = Query(None, max_length=100),
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_admin),
) -> ChunkListOut:
    document = await _get_document(doc_id, db)
    filters = [Chunk.doc_id == document.id]
    if keyword and keyword.strip():
        filters.append(Chunk.content.contains(keyword.strip()))

    total = await db.scalar(select(func.count()).select_from(Chunk).where(*filters)) or 0
    rows = list(
        await db.scalars(
            select(Chunk)
            .where(*filters)
            .order_by(Chunk.chunk_index.asc())
            .offset((page - 1) * page_size)
            .limit(page_size)
        )
    )
    parents = await _parent_map(db, rows)
    return ChunkListOut(
        total=total,
        page=page,
        page_size=page_size,
        items=[_chunk_out(row, parents) for row in rows],
    )


@router.get(
    "/documents/{doc_id}/parents",
    response_model=list[ParentChunkOut],
    summary="文档父分块列表",
)
async def list_parent_chunks(
    doc_id: int,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_admin),
) -> list[ParentChunk]:
    document = await _get_document(doc_id, db)
    rows = await db.scalars(
        select(ParentChunk)
        .where(ParentChunk.doc_id == document.id)
        .order_by(ParentChunk.parent_index.asc())
    )
    return list(rows)


@router.get("/chunks/{uid}", response_model=ChunkOut, summary="分块详情")
async def get_chunk(
    uid: str,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_admin),
) -> ChunkOut:
    chunk, _ = await _get_chunk(uid, db)
    parents = await _parent_map(db, [chunk])
    return _chunk_out(chunk, parents)


@router.patch("/chunks/{uid}", response_model=ChunkOut, summary="编辑分块并同步向量")
async def update_chunk(
    uid: str,
    payload: ChunkUpdate,
    background_tasks: BackgroundTasks,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_admin),
) -> Chunk:
    chunk, _ = await _get_chunk(uid, db)
    content = payload.content.strip()
    if not content:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "分块内容不能为空")

    chunk.content = content
    chunk.char_count = len(content)
    chunk.content_hash = content_hash(content)
    try:
        await db.flush()
        await _refresh_parent_chunk(db, chunk.parent_uid)
        # 数据库先落盘，向量由 outbox 异步重编码；失败可重试
        entry = await enqueue_outbox(
            db, "upsert_chunk", {"chunk_uid": chunk.uid, "kb_id": chunk.kb_id}
        )
        await db.commit()
        await invalidate_search_caches(chunk.kb_id)
    except Exception:
        await db.rollback()
        logger.exception(
            "分块更新失败 uid=%s（app/api/routes/knowledge.py:update_chunk）", uid
        )
        raise HTTPException(status.HTTP_500_INTERNAL_SERVER_ERROR, "分块更新失败，请稍后重试") from None

    background_tasks.add_task(flush_outbox_entry, entry.id)
    task_worker.wake()
    await db.refresh(chunk)
    return chunk


@router.delete(
    "/chunks/{uid}", status_code=status.HTTP_204_NO_CONTENT, summary="删除分块"
)
async def delete_chunk(
    uid: str,
    background_tasks: BackgroundTasks,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_admin),
) -> None:
    chunk, document = await _get_chunk(uid, db)
    parent_uid = chunk.parent_uid
    await db.execute(delete(Chunk).where(Chunk.id == chunk.id))
    await db.flush()
    await _refresh_parent_chunk(db, parent_uid)
    document.chunk_count = max(0, document.chunk_count - 1)
    entry = await enqueue_outbox(
        db, "delete_chunk", {"chunk_uid": chunk.uid, "kb_id": document.kb_id}
    )
    await db.commit()
    await invalidate_search_caches(document.kb_id)
    background_tasks.add_task(flush_outbox_entry, entry.id)
    task_worker.wake()
    logger.info("分块已删除 uid=%s doc_id=%s", chunk.uid, document.id)
