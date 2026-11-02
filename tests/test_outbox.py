"""向量 outbox 测试：幂等同步、版本切换与失败重试。"""

import pytest
from sqlalchemy import select

from app.models.chunk import Chunk
from app.models.document import Document
from app.models.knowledge_base import KnowledgeBase
from app.models.user import User
from app.models.vector_outbox import VectorOutbox
from app.services import ingestion, outbox


class FakeVectorStore:
    def __init__(self) -> None:
        self.upserted_rows: list[dict] = []
        self.upserted_chunks: list[dict] = []
        self.deleted_docs: list[int] = []
        self.deleted_kbs: list[int] = []
        self.deleted_uids: list[str] = []
        self.fail = False

    async def upsert_chunks(self, rows):
        if self.fail:
            raise RuntimeError("milvus unavailable")
        self.upserted_rows.extend(rows)

    async def upsert_chunk(self, row):
        if self.fail:
            raise RuntimeError("milvus unavailable")
        self.upserted_chunks.append(row)

    async def delete_by_doc(self, doc_id):
        self.deleted_docs.append(doc_id)

    async def delete_by_kb(self, kb_id):
        self.deleted_kbs.append(kb_id)

    async def delete_by_uid(self, chunk_uid):
        self.deleted_uids.append(chunk_uid)


@pytest.fixture
def fake_store(monkeypatch):
    store = FakeVectorStore()
    monkeypatch.setattr(outbox.vector_store, "upsert_chunks", store.upsert_chunks)
    monkeypatch.setattr(outbox.vector_store, "upsert_chunk", store.upsert_chunk)
    monkeypatch.setattr(outbox.vector_store, "delete_by_doc", store.delete_by_doc)
    monkeypatch.setattr(outbox.vector_store, "delete_by_kb", store.delete_by_kb)
    monkeypatch.setattr(outbox.vector_store, "delete_by_uid", store.delete_by_uid)
    return store


async def _seed(db_maker, *, status: str = "processing") -> tuple[int, int]:
    async with db_maker() as db:
        user = User(username="outbox_user", password_hash="x")
        db.add(user)
        await db.flush()
        kb = KnowledgeBase(owner_id=user.id, name="outbox 库")
        db.add(kb)
        await db.flush()
        document = Document(
            kb_id=kb.id,
            original_name="policy.md",
            stored_name="policy.md",
            file_path="/tmp/outbox-policy.md",
            file_type="md",
            status=status,
            is_current=True,
        )
        db.add(document)
        await db.commit()
        return kb.id, document.id


@pytest.mark.asyncio
async def test_replace_doc_upserts_and_marks_completed(db_maker, fake_store) -> None:
    kb_id, document_id = await _seed(db_maker)
    async with db_maker() as db:
        entry = await outbox.enqueue_outbox(
            db,
            "replace_doc",
            {
                "doc_id": document_id,
                "kb_id": kb_id,
                "action": "created",
                "stale_uids": ["stale-uid"],
            },
        )
        await db.commit()
        claimed = await outbox.claim_next_outbox_entry(db)
        assert claimed is not None
        ok = await outbox.run_claimed_entry(
            db, claimed, vector_rows=[{"chunk_uid": "c1"}]
        )
        await db.commit()

        assert ok is True
        assert fake_store.upserted_rows == [{"chunk_uid": "c1"}]
        assert fake_store.deleted_uids == ["stale-uid"]
        document = await db.get(Document, document_id)
        assert document is not None
        assert document.status == "completed"
        assert document.is_current is True
        assert document.update_action == "created"
        assert entry.status == "done"


@pytest.mark.asyncio
async def test_replace_doc_switches_version_after_vectors_ready(
    db_maker, fake_store
) -> None:
    kb_id, old_id = await _seed(db_maker, status="completed")
    async with db_maker() as db:
        old = await db.get(Document, old_id)
        db.add(
            Chunk(
                uid="old-chunk",
                doc_id=old_id,
                kb_id=kb_id,
                chunk_index=0,
                content="old",
            )
        )
        new = Document(
            kb_id=kb_id,
            original_name="policy-v2.md",
            stored_name="policy-v2.md",
            file_path="/tmp/policy-v2.md",
            file_type="md",
            status="processing",
            is_current=False,
            replaces_doc_id=old_id,
            version=2,
        )
        db.add(new)
        await db.commit()
        new_id = new.id

        entry = await outbox.enqueue_outbox(
            db,
            "replace_doc",
            {
                "doc_id": new_id,
                "kb_id": kb_id,
                "action": "updated",
                "replaces_doc_id": old_id,
            },
        )
        await db.commit()
        claimed = await outbox.claim_next_outbox_entry(db)
        assert claimed is not None
        ok = await outbox.run_claimed_entry(
            db, claimed, vector_rows=[{"chunk_uid": "new-chunk"}]
        )
        await db.commit()

        assert ok is True
        await db.refresh(old)
        assert old.is_current is False
        assert old.update_action == "superseded"
        old_chunks = list(
            await db.scalars(select(Chunk).where(Chunk.doc_id == old_id))
        )
        assert old_chunks == []
        assert old_id in fake_store.deleted_docs
        refreshed = await db.get(Document, new_id)
        assert refreshed is not None
        assert refreshed.status == "completed"
        assert refreshed.is_current is True


@pytest.mark.asyncio
async def test_outbox_failure_marks_document_failed(db_maker, fake_store) -> None:
    fake_store.fail = True
    kb_id, document_id = await _seed(db_maker)
    async with db_maker() as db:
        entry = await outbox.enqueue_outbox(
            db,
            "replace_doc",
            {"doc_id": document_id, "kb_id": kb_id, "action": "created"},
            max_attempts=1,
        )
        await db.commit()
        claimed = await outbox.claim_next_outbox_entry(db)
        assert claimed is not None
        ok = await outbox.run_claimed_entry(
            db, claimed, vector_rows=[{"chunk_uid": "c1"}]
        )

        assert ok is False
        stored = await db.get(VectorOutbox, entry.id)
        assert stored is not None
        assert stored.status == "failed"
        document = await db.get(Document, document_id)
        assert document is not None
        assert document.status == "failed"


@pytest.mark.asyncio
async def test_replace_doc_skips_when_document_deleted(db_maker, fake_store) -> None:
    """文档在同步前被删除时不能再把向量写回 Milvus。"""
    async with db_maker() as db:
        entry = await outbox.enqueue_outbox(
            db,
            "replace_doc",
            {"doc_id": 99999, "kb_id": 1, "action": "created"},
        )
        await db.commit()
        claimed = await outbox.claim_next_outbox_entry(db)
        assert claimed is not None
        ok = await outbox.run_claimed_entry(
            db, claimed, vector_rows=[{"chunk_uid": "ghost"}]
        )

        assert ok is True
        assert fake_store.upserted_rows == []
        assert claimed.status == "done"


def test_stable_uid_is_deterministic() -> None:
    class _Document:
        id = 7
        version = 3

    first = ingestion._stable_uid(_Document(), "c", 1, "abc")
    second = ingestion._stable_uid(_Document(), "c", 1, "abc")
    changed = ingestion._stable_uid(_Document(), "c", 1, "def")

    assert first == second
    assert first != changed
    assert len(first) == 32
