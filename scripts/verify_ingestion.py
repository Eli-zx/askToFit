"""端到端验证：真实 BGE-M3 + Milvus 入库与检索。

会创建演示账号 demo / demo1234，知识库「服装售前知识库」，
并把 data/ 下的知识文档与多格式样例全部入库。

用法：
    uv run python scripts/verify_ingestion.py           # 跳过已入库文档
    uv run python scripts/verify_ingestion.py --force   # 全部重新入库
"""

import argparse
import asyncio
import shutil
import sys
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except (AttributeError, OSError):
    pass

from sqlalchemy import func, select  # noqa: E402

from app.core.logger import get_logger  # noqa: E402
from app.core.security import hash_password  # noqa: E402
from app.db.session import (  # noqa: E402
    dispose_engine,
    ensure_database,
    get_session_maker,
    init_models,
)
from app.models.document import Document  # noqa: E402
from app.models.knowledge_base import KnowledgeBase  # noqa: E402
from app.models.user import User  # noqa: E402
from app.services.embedding import embedding_service  # noqa: E402
from app.services.ingestion import ingest_document  # noqa: E402
from app.services.storage import delete_stored_file  # noqa: E402
from app.services.vector_store import vector_store  # noqa: E402
from config import Config  # noqa: E402

logger = get_logger(__name__)

DEMO_USERNAME = "demo"
DEMO_PASSWORD = "demo1234"
KB_NAME = "服装售前知识库"
SUPPORTED = {".md", ".markdown", ".pdf", ".pptx", ".ppt", ".png", ".jpg", ".jpeg"}


def collect_files() -> list[Path]:
    files = [
        path
        for path in sorted(Path(Config.data_dir).glob("*.md"))
        if path.suffix.lower() in SUPPORTED
    ]
    samples = Path(Config.data_dir) / "samples"
    if samples.exists():
        files.extend(
            path
            for path in sorted(samples.iterdir())
            if path.suffix.lower() in SUPPORTED
        )
    return files


async def get_or_create_user(db) -> User:
    user = await db.scalar(select(User).where(User.username == DEMO_USERNAME))
    if user is not None:
        return user
    user = User(
        username=DEMO_USERNAME,
        nickname="演示账号",
        password_hash=hash_password(DEMO_PASSWORD),
    )
    db.add(user)
    await db.commit()
    await db.refresh(user)
    logger.info("已创建演示账号 %s / %s", DEMO_USERNAME, DEMO_PASSWORD)
    return user


async def get_or_create_kb(db, user: User) -> KnowledgeBase:
    kb = await db.scalar(
        select(KnowledgeBase).where(
            KnowledgeBase.owner_id == user.id, KnowledgeBase.name == KB_NAME
        )
    )
    if kb is not None:
        return kb
    kb = KnowledgeBase(
        owner_id=user.id,
        name=KB_NAME,
        description="退换货、发票、物流、支付结算、退款与尺码等售前政策",
        embedding_mode="hybrid",
        chunk_size=500,
        chunk_overlap=80,
    )
    db.add(kb)
    await db.commit()
    await db.refresh(kb)
    logger.info("已创建知识库 %s id=%s", KB_NAME, kb.id)
    return kb


async def reset_document(db, document: Document) -> None:
    await vector_store.delete_by_doc(document.id)
    delete_stored_file(document.file_path)
    await db.delete(document)
    await db.commit()


async def ingest_all(force: bool) -> tuple[int, int]:
    files = collect_files()
    if not files:
        raise SystemExit("data 目录下没有可入库的文档")

    async with get_session_maker()() as db:
        user = await get_or_create_user(db)
        kb = await get_or_create_kb(db, user)
        kb_dir = Path(Config.upload_dir) / f"kb_{kb.id}"
        kb_dir.mkdir(parents=True, exist_ok=True)

        completed = 0
        skipped = 0
        for source in files:
            existing = await db.scalar(
                select(Document).where(
                    Document.kb_id == kb.id, Document.original_name == source.name
                )
            )
            if existing is not None and not force and existing.status == "completed":
                skipped += 1
                logger.info("跳过已入库文档 %s", source.name)
                continue

            if existing is not None and force:
                await reset_document(db, existing)
                existing = None

            if existing is not None:
                # 上次失败 / 中断的文档直接复用原文件重新入库
                document = existing
                logger.info("重新处理未完成文档 %s", source.name)
            else:
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
                )
                db.add(document)
                await db.commit()
                await db.refresh(document)

            # 结束当前事务：MySQL 默认 REPEATABLE READ，长事务快照会读到入库前的旧状态
            await db.commit()
            await ingest_document(document.id)
            # ingest_document 使用独立会话，当前会话需刷新才能看到最新状态
            await db.refresh(document)
            if document.status == "completed":
                completed += 1
            else:
                logger.error("文档入库未完成 %s status=%s", source.name, document.status)

        document_total = await db.scalar(
            select(func.count()).select_from(Document).where(Document.kb_id == kb.id)
        )
        kb.document_count = int(document_total or 0)
        total_chunks = int(
            await db.scalar(
                select(func.coalesce(func.sum(Document.chunk_count), 0)).where(
                    Document.kb_id == kb.id
                )
            )
            or 0
        )
        await db.commit()
        logger.info(
            "入库汇总：成功 %d，跳过 %d，文档总数 %d，分块总数 %d",
            completed,
            skipped,
            kb.document_count,
            total_chunks,
        )
        return kb.id, total_chunks


async def retrieval_smoke_test(kb_id: int) -> None:
    queries = ["你们支持货到付款吗？", "满减怎么算？", "开发票要多久？", "尺码偏大还是偏小？"]
    print("\n================ 检索质量抽查 ================")
    for query in queries:
        dense, sparse = await embedding_service.embed_query(query)
        results = await vector_store.search(
            dense, sparse, [kb_id], mode="hybrid", top_k=3
        )
        print(f"\n查询：{query}")
        for rank, hit in enumerate(results, start=1):
            title = hit.get("title") or ""
            section = hit.get("section") or ""
            content = (hit.get("content") or "").replace("\n", " ")
            print(f"  {rank}. score={hit['score']:.4f} [{title} / {section}] {content[:90]}")


async def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--force", action="store_true", help="重新入库全部文档")
    args = parser.parse_args()

    ensure_database()
    await init_models()
    kb_id, total_chunks = await ingest_all(force=args.force)
    await retrieval_smoke_test(kb_id)
    await dispose_engine()
    print(
        f"\n完成：演示账号 {DEMO_USERNAME} / {DEMO_PASSWORD}，"
        f"知识库 id={kb_id}，共 {total_chunks} 个分块。"
    )


if __name__ == "__main__":
    asyncio.run(main())
