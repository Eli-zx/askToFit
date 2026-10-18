"""把业务文档导入公共知识库并执行真实入库与检索抽查。

默认把 data/business_docs/ 下的 9 份文档导入到 eli 账号名下的公共知识库
「服装售前知识库（2026 春夏）」，其他登录账号（如 demo）可直接检索。

用法：
    uv run python scripts/import_business_docs.py
    uv run python scripts/import_business_docs.py --username eli --force
    uv run python scripts/import_business_docs.py --skip-smoke
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import shutil
import sys
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except (AttributeError, OSError):
    pass

from sqlalchemy import delete, func, select  # noqa: E402

from app.core.logger import get_logger  # noqa: E402
from app.db.session import (  # noqa: E402
    dispose_engine,
    ensure_database,
    ensure_schema_upgrades,
    get_session_maker,
    init_models,
)
from app.models.chunk import Chunk  # noqa: E402
from app.models.document import Document  # noqa: E402
from app.models.knowledge_base import KnowledgeBase  # noqa: E402
from app.models.parent_chunk import ParentChunk  # noqa: E402
from app.models.user import User  # noqa: E402
from app.services.embedding import embedding_service  # noqa: E402
from app.services.ingestion import ingest_document  # noqa: E402
from app.services.storage import delete_stored_file  # noqa: E402
from app.services.vector_store import vector_store  # noqa: E402
from config import Config  # noqa: E402

logger = get_logger(__name__)

DEFAULT_USERNAME = "eli"
DEFAULT_KB_NAME = "服装售前知识库（2026 春夏）"
DEFAULT_DOC_DIR = Path(Config.data_dir) / "business_docs"
SUPPORTED = {
    ".md",
    ".markdown",
    ".pdf",
    ".pptx",
    ".ppt",
    ".docx",
    ".doc",
    ".png",
    ".jpg",
    ".jpeg",
}
SMOKE_QUERIES = [
    "真丝衬衫洗过还能退吗？",
    "定制西装支持七天无理由吗？",
    "增值税专用发票税率是多少，多久能开出来？",
    "新疆发货要几天，运费怎么收？",
    "会员日折扣和跨店满减能叠加吗？",
    "618 预售定金付了尾款后能退吗？",
]


def collect_files(doc_dir: Path) -> list[Path]:
    if not doc_dir.exists():
        raise SystemExit(f"文档目录不存在：{doc_dir}，请先运行 build_business_docs.py")
    files = [
        path
        for path in sorted(doc_dir.iterdir())
        if path.is_file() and path.suffix.lower() in SUPPORTED and not path.name.startswith("~$")
    ]
    if not files:
        raise SystemExit(f"目录中没有可导入的文档：{doc_dir}")
    return files


async def resolve_user(db, username: str) -> User:
    user = await db.scalar(select(User).where(User.username == username))
    if user is None:
        raise SystemExit(f"账号 {username} 不存在，请先登录一次或注册该账号")
    return user


async def get_or_create_kb(db, user: User, name: str) -> KnowledgeBase:
    kb = await db.scalar(
        select(KnowledgeBase).where(
            KnowledgeBase.owner_id == user.id, KnowledgeBase.name == name
        )
    )
    if kb is None:
        kb = KnowledgeBase(
            owner_id=user.id,
            name=name,
            description="退换货、尺码、面料、发票、物流、会员优惠与 618 大促等售前业务文档",
            embedding_mode="hybrid",
            chunk_size=500,
            chunk_overlap=80,
            is_public=True,
        )
        db.add(kb)
        await db.commit()
        await db.refresh(kb)
        logger.info("已创建公共知识库 %s id=%s owner=%s", name, kb.id, user.username)
        return kb

    changed = False
    if not kb.is_public:
        kb.is_public = True
        changed = True
    if changed:
        await db.commit()
        await db.refresh(kb)
        logger.info("知识库已设为公共 kb_id=%s", kb.id)
    return kb


def _file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _same_file(source: Path, document: Document) -> bool:
    stored = Path(document.file_path)
    if not stored.exists() or stored.stat().st_size != source.stat().st_size:
        return False
    return _file_sha256(source) == _file_sha256(stored)


async def reset_document(db, document: Document) -> None:
    await vector_store.delete_by_doc(document.id)
    await db.execute(delete(Chunk).where(Chunk.doc_id == document.id))
    await db.execute(delete(ParentChunk).where(ParentChunk.doc_id == document.id))
    delete_stored_file(document.file_path)
    await db.delete(document)
    await db.commit()


async def import_all(
    doc_dir: Path, username: str, kb_name: str, force: bool
) -> tuple[KnowledgeBase, list[tuple[str, str, int]]]:
    files = collect_files(doc_dir)

    async with get_session_maker()() as db:
        user = await resolve_user(db, username)
        kb = await get_or_create_kb(db, user, kb_name)
        kb_dir = Path(Config.upload_dir) / f"kb_{kb.id}"
        kb_dir.mkdir(parents=True, exist_ok=True)

        results: list[tuple[str, str, int]] = []
        for source in files:
            existing = await db.scalar(
                select(Document)
                .where(
                    Document.kb_id == kb.id,
                    Document.original_name == source.name,
                    Document.is_current.is_(True),
                )
                .order_by(Document.id.desc())
            )
            if existing is not None and existing.status == "completed" and not force:
                if _same_file(source, existing):
                    logger.info(
                        "跳过未变化文档 %s（v%d，%d 分块）",
                        source.name,
                        existing.version,
                        existing.chunk_count,
                    )
                    results.append((source.name, "skipped", existing.chunk_count))
                    continue
                logger.info("检测到文件变化，%s 将作为新版本入库", source.name)

            if existing is not None and (force or existing.status != "completed"):
                await reset_document(db, existing)
                existing = None

            # 文件已变化但旧版本仍可检索：创建新记录，由入库流程成功后切换
            suffix = source.suffix.lower()
            target = kb_dir / f"{uuid.uuid4().hex}{suffix}"
            shutil.copy2(source, target)
            document = Document(
                kb_id=kb.id,
                original_name=source.name,
                stored_name=target.name,
                file_path=str(target),
                file_type=suffix.lstrip("."),
                file_size=target.stat().st_size,
                status="pending",
                chunk_size=kb.chunk_size,
                chunk_overlap=kb.chunk_overlap,
                embedding_mode=kb.embedding_mode,
                version=1,
                is_current=True,
                update_action="pending",
            )
            db.add(document)
            await db.commit()
            await db.refresh(document)

            # 结束长事务，避免 MySQL REPEATABLE READ 读到入库前的旧快照
            await db.commit()
            logger.info("开始入库 %s（type=%s, %.1f KB）", source.name, document.file_type, document.file_size / 1024)
            await ingest_document(document.id)
            await db.refresh(document)
            if document.status == "completed":
                logger.info("入库成功 %s：%d 个分块", source.name, document.chunk_count)
            else:
                logger.error(
                    "入库失败 %s：status=%s error=%s",
                    source.name,
                    document.status,
                    document.error_message,
                )
            results.append((source.name, document.status, document.chunk_count))

        document_total = await db.scalar(
            select(func.count())
            .select_from(Document)
            .where(Document.kb_id == kb.id, Document.is_current.is_(True))
        )
        kb.document_count = int(document_total or 0)
        total_chunks = int(
            await db.scalar(
                select(func.coalesce(func.sum(Document.chunk_count), 0)).where(
                    Document.kb_id == kb.id, Document.is_current.is_(True)
                )
            )
            or 0
        )
        await db.commit()
        await db.refresh(kb)
        logger.info(
            "导入汇总：kb_id=%s 文档 %d 篇，分块 %d 个",
            kb.id,
            kb.document_count,
            total_chunks,
        )
        return kb, results


async def retrieval_smoke_test(kb_id: int) -> None:
    print("\n================ 业务问题检索抽查 ================")
    for query in SMOKE_QUERIES:
        dense, sparse = await embedding_service.embed_query(query)
        hits = await vector_store.search(
            dense, sparse, [kb_id], mode="hybrid", top_k=3
        )
        print(f"\n查询：{query}")
        for rank, hit in enumerate(hits, start=1):
            title = hit.get("title") or ""
            section = hit.get("section") or ""
            content = (hit.get("content") or "").replace("\n", " ")
            print(
                f"  {rank}. score={hit['score']:.4f} [{title} / {section}] "
                f"{content[:100]}"
            )


async def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--username", default=DEFAULT_USERNAME, help="知识库所属账号")
    parser.add_argument("--kb-name", default=DEFAULT_KB_NAME, help="公共知识库名称")
    parser.add_argument("--doc-dir", default=str(DEFAULT_DOC_DIR), help="业务文档目录")
    parser.add_argument("--force", action="store_true", help="删除并重新入库全部文档")
    parser.add_argument("--skip-smoke", action="store_true", help="跳过检索抽查")
    args = parser.parse_args()

    ensure_database()
    await init_models()
    await ensure_schema_upgrades()

    kb, results = await import_all(
        Path(args.doc_dir), args.username, args.kb_name, args.force
    )
    failed = [item for item in results if item[1] not in {"completed", "skipped"}]
    if failed:
        for name, status, _ in failed:
            logger.error("未完成：%s status=%s", name, status)
        await dispose_engine()
        raise SystemExit(1)

    if not args.skip_smoke:
        await retrieval_smoke_test(kb.id)

    await dispose_engine()
    print(
        f"\n完成：公共知识库「{kb.name}」id={kb.id}，"
        f"owner={args.username}，文档 {kb.document_count} 篇。"
    )


if __name__ == "__main__":
    asyncio.run(main())
