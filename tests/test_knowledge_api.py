"""知识库 / 文档入库 / 分块管理 API 测试。

外部依赖（BGE-M3、Milvus）使用轻量替身，确保测试快速且可重复；
真实 Milvus + BGE-M3 的端到端验证由集成测试脚本完成。
"""

from pathlib import Path

import pytest

from app.api.routes import knowledge as knowledge_module
from app.services import ingestion
from app.services.captcha import CAPTCHA_KEY_PREFIX
from app.services.parsers import ParsedBlock, ParsedDocument
from config import Config


class FakeVectorStore:
    def __init__(self) -> None:
        self.inserted: list[dict] = []
        self.upserted: list[dict] = []
        self.deleted_docs: list[int] = []
        self.deleted_kbs: list[int] = []
        self.deleted_uids: list[str] = []

    async def insert_chunks(self, rows):
        self.inserted.extend(rows)

    async def upsert_chunk(self, row):
        self.upserted.append(row)

    async def delete_by_doc(self, doc_id):
        self.deleted_docs.append(doc_id)

    async def delete_by_kb(self, kb_id):
        self.deleted_kbs.append(kb_id)

    async def delete_by_uid(self, chunk_uid):
        self.deleted_uids.append(chunk_uid)

    async def search(self, *args, **kwargs):
        return []


@pytest.fixture
def fake_store(monkeypatch):
    store = FakeVectorStore()

    async def fake_embed(texts, batch_size=None):
        return (
            [[0.01] * Config.embedding_dim for _ in texts],
            [{1: 0.5} for _ in texts],
        )

    monkeypatch.setattr(ingestion.embedding_service, "embed_documents", fake_embed)
    monkeypatch.setattr(ingestion.vector_store, "insert_chunks", store.insert_chunks)
    monkeypatch.setattr(ingestion.vector_store, "upsert_chunk", store.upsert_chunk)
    monkeypatch.setattr(ingestion.vector_store, "delete_by_doc", store.delete_by_doc)
    monkeypatch.setattr(ingestion.vector_store, "delete_by_kb", store.delete_by_kb)
    monkeypatch.setattr(ingestion.vector_store, "delete_by_uid", store.delete_by_uid)
    monkeypatch.setattr(ingestion.vector_store, "search", store.search)
    return store


@pytest.fixture(autouse=True)
def isolated_uploads(monkeypatch, tmp_path):
    upload_dir = tmp_path / "uploads"
    monkeypatch.setattr(Config, "upload_dir", upload_dir)
    monkeypatch.setattr(Config, "max_upload_mb", 5)
    return upload_dir


@pytest.fixture
def use_test_db(monkeypatch, db_maker):
    monkeypatch.setattr(ingestion, "get_session_maker", lambda: db_maker)


async def auth_headers(client, redis, username: str = "alice") -> dict:
    captcha = await client.get("/api/auth/captcha")
    captcha_id = captcha.json()["captcha_id"]
    code = await redis.get(CAPTCHA_KEY_PREFIX + captcha_id)
    register = await client.post(
        "/api/auth/register",
        json={
            "username": username,
            "password": "secret123",
            "confirm_password": "secret123",
            "captcha_id": captcha_id,
            "captcha_code": code,
        },
    )
    assert register.status_code == 201

    captcha = await client.get("/api/auth/captcha")
    captcha_id = captcha.json()["captcha_id"]
    code = await redis.get(CAPTCHA_KEY_PREFIX + captcha_id)
    login = await client.post(
        "/api/auth/login",
        json={
            "username": username,
            "password": "secret123",
            "captcha_id": captcha_id,
            "captcha_code": code,
        },
    )
    assert login.status_code == 200
    return {"Authorization": f"Bearer {login.json()['access_token']}"}


async def create_kb(
    client, headers, name: str = "售后政策库", *, is_public: bool = False
) -> dict:
    resp = await client.post(
        "/api/knowledge_bases",
        headers=headers,
        json={
            "name": name,
            "description": "服装售前售后政策",
            "embedding_mode": "hybrid",
            "chunk_size": 300,
            "chunk_overlap": 60,
            "is_public": is_public,
        },
    )
    assert resp.status_code == 201, resp.text
    return resp.json()


def long_markdown() -> bytes:
    paragraphs = []
    for index in range(1, 13):
        paragraphs.append(
            f"第{index}条：自签收之日起七天内，商品保持完好可申请无理由退换货。"
            f"退换货产生的运费按照责任方承担，质量问题由商家承担。"
        )
    return ("# 退换货规则\n\n" + "\n\n".join(paragraphs)).encode("utf-8")


async def upload_markdown(client, headers, kb_id: int, filename: str = "退换货规则.md"):
    return await client.post(
        f"/api/knowledge_bases/{kb_id}/documents",
        headers=headers,
        files={"file": (filename, long_markdown(), "text/markdown")},
        data={"chunk_size": "300", "chunk_overlap": "60", "embedding_mode": "hybrid"},
    )


async def test_upload_and_ingest_markdown(
    client, fake_redis, db_maker, use_test_db, fake_store
) -> None:
    headers = await auth_headers(client, fake_redis)
    kb = await create_kb(client, headers)

    resp = await upload_markdown(client, headers, kb["id"])
    assert resp.status_code == 202, resp.text
    document_id = resp.json()["id"]

    detail = await client.get(f"/api/documents/{document_id}", headers=headers)
    assert detail.status_code == 200
    data = detail.json()
    assert data["status"] == "completed"
    assert data["error_message"] is None
    assert data["chunk_count"] > 1
    assert data["embedding_mode"] == "hybrid"
    assert data["parser"] == "auto"

    chunks = await client.get(f"/api/documents/{document_id}/chunks", headers=headers)
    assert chunks.status_code == 200
    payload = chunks.json()
    assert payload["total"] == data["chunk_count"]
    assert any("七天内" in item["content"] for item in payload["items"])
    assert all(item["char_count"] == len(item["content"]) for item in payload["items"])
    assert len(fake_store.inserted) == data["chunk_count"]
    assert all(row["dense_vector"] for row in fake_store.inserted)
    assert all(row["sparse_vector"] for row in fake_store.inserted)


async def test_upload_unsupported_file_type(
    client, fake_redis, db_maker, use_test_db, fake_store
) -> None:
    headers = await auth_headers(client, fake_redis)
    kb = await create_kb(client, headers)
    resp = await client.post(
        f"/api/knowledge_bases/{kb['id']}/documents",
        headers=headers,
        files={"file": ("notes.txt", b"hello", "text/plain")},
    )
    assert resp.status_code == 400


async def test_upload_too_large(
    client, fake_redis, db_maker, use_test_db, fake_store, monkeypatch
) -> None:
    headers = await auth_headers(client, fake_redis)
    kb = await create_kb(client, headers)
    monkeypatch.setattr(Config, "max_upload_mb", 0)
    resp = await upload_markdown(client, headers, kb["id"])
    assert resp.status_code == 413


async def test_invalid_chunk_params(
    client, fake_redis, db_maker, use_test_db, fake_store
) -> None:
    headers = await auth_headers(client, fake_redis)
    kb = await create_kb(client, headers)
    resp = await client.post(
        f"/api/knowledge_bases/{kb['id']}/documents",
        headers=headers,
        files={"file": ("policy.md", b"# policy\n\ncontent", "text/markdown")},
        data={"chunk_size": "50", "chunk_overlap": "10"},
    )
    assert resp.status_code == 400


async def test_knowledge_base_isolation(
    client, fake_redis, db_maker, use_test_db, fake_store
) -> None:
    alice = await auth_headers(client, fake_redis, "alice")
    kb = await create_kb(client, alice)
    bob = await auth_headers(client, fake_redis, "bob")

    resp = await client.get(f"/api/knowledge_bases/{kb['id']}", headers=bob)
    assert resp.status_code == 404

    listing = await client.get("/api/knowledge_bases", headers=bob)
    assert listing.status_code == 200
    assert all(item["id"] != kb["id"] for item in listing.json())


async def test_public_kb_readable_but_not_writable_by_others(
    client, fake_redis, db_maker, use_test_db, fake_store
) -> None:
    alice = await auth_headers(client, fake_redis, "alice")
    kb = await create_kb(client, alice, "服装售前知识库", is_public=True)
    upload = await upload_markdown(client, alice, kb["id"])
    assert upload.status_code == 202, upload.text
    document_id = upload.json()["id"]
    chunks = await client.get(
        f"/api/documents/{document_id}/chunks", headers=alice
    )
    chunk_uid = chunks.json()["items"][0]["uid"]

    bob = await auth_headers(client, fake_redis, "bob")

    listing = await client.get("/api/knowledge_bases", headers=bob)
    assert listing.status_code == 200
    public_item = next(item for item in listing.json() if item["id"] == kb["id"])
    assert public_item["is_public"] is True
    assert public_item["owner_id"] == kb["owner_id"]

    detail = await client.get(f"/api/knowledge_bases/{kb['id']}", headers=bob)
    assert detail.status_code == 200
    assert detail.json()["is_public"] is True

    documents = await client.get(
        f"/api/knowledge_bases/{kb['id']}/documents", headers=bob
    )
    assert documents.status_code == 200
    assert [item["id"] for item in documents.json()] == [document_id]

    document_detail = await client.get(f"/api/documents/{document_id}", headers=bob)
    assert document_detail.status_code == 200

    chunk_list = await client.get(
        f"/api/documents/{document_id}/chunks", headers=bob
    )
    assert chunk_list.status_code == 200
    assert chunk_list.json()["total"] > 0

    chunk_detail = await client.get(f"/api/chunks/{chunk_uid}", headers=bob)
    assert chunk_detail.status_code == 200

    assert (
        await client.patch(
            f"/api/knowledge_bases/{kb['id']}",
            headers=bob,
            json={"description": "越权修改"},
        )
    ).status_code == 404
    assert (
        await client.delete(f"/api/knowledge_bases/{kb['id']}", headers=bob)
    ).status_code == 404
    assert (await upload_markdown(client, bob, kb["id"])).status_code == 404
    assert (
        await client.post(f"/api/documents/{document_id}/reindex", headers=bob)
    ).status_code == 404
    assert (
        await client.delete(f"/api/documents/{document_id}", headers=bob)
    ).status_code == 404
    assert (
        await client.patch(
            f"/api/chunks/{chunk_uid}",
            headers=bob,
            json={"content": "越权修改分块"},
        )
    ).status_code == 404
    assert (
        await client.delete(f"/api/chunks/{chunk_uid}", headers=bob)
    ).status_code == 404


async def test_owner_can_publish_and_unpublish_kb(
    client, fake_redis, db_maker, use_test_db, fake_store
) -> None:
    alice = await auth_headers(client, fake_redis, "alice")
    kb = await create_kb(client, alice)
    bob = await auth_headers(client, fake_redis, "bob")

    before = await client.get("/api/knowledge_bases", headers=bob)
    assert all(item["id"] != kb["id"] for item in before.json())

    publish = await client.patch(
        f"/api/knowledge_bases/{kb['id']}",
        headers=alice,
        json={"is_public": True},
    )
    assert publish.status_code == 200
    assert publish.json()["is_public"] is True

    after = await client.get("/api/knowledge_bases", headers=bob)
    assert any(item["id"] == kb["id"] for item in after.json())

    unpublish = await client.patch(
        f"/api/knowledge_bases/{kb['id']}",
        headers=alice,
        json={"is_public": False},
    )
    assert unpublish.status_code == 200
    assert unpublish.json()["is_public"] is False

    hidden = await client.get("/api/knowledge_bases", headers=bob)
    assert all(item["id"] != kb["id"] for item in hidden.json())


async def test_duplicate_knowledge_base_name(
    client, fake_redis, db_maker, use_test_db, fake_store
) -> None:
    headers = await auth_headers(client, fake_redis)
    await create_kb(client, headers, "售后政策库")
    resp = await client.post(
        "/api/knowledge_bases",
        headers=headers,
        json={"name": "售后政策库"},
    )
    assert resp.status_code == 409


async def test_edit_chunk_syncs_vector(
    client, fake_redis, db_maker, use_test_db, fake_store
) -> None:
    headers = await auth_headers(client, fake_redis)
    kb = await create_kb(client, headers)
    upload = await upload_markdown(client, headers, kb["id"])
    document_id = upload.json()["id"]

    chunks = await client.get(f"/api/documents/{document_id}/chunks", headers=headers)
    chunk = chunks.json()["items"][0]
    new_content = "支持货到付款，满 199 元包邮，具体以结算页展示为准。"

    resp = await client.patch(
        f"/api/chunks/{chunk['uid']}",
        headers=headers,
        json={"content": new_content},
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["content"] == new_content
    assert resp.json()["char_count"] == len(new_content)
    assert fake_store.upserted
    assert fake_store.upserted[-1]["content"] == new_content


async def test_delete_chunk_and_document(
    client, fake_redis, db_maker, use_test_db, fake_store
) -> None:
    headers = await auth_headers(client, fake_redis)
    kb = await create_kb(client, headers)
    upload = await upload_markdown(client, headers, kb["id"])
    document_id = upload.json()["id"]

    chunks = await client.get(f"/api/documents/{document_id}/chunks", headers=headers)
    chunk = chunks.json()["items"][0]
    total_before = chunks.json()["total"]

    resp = await client.delete(f"/api/chunks/{chunk['uid']}", headers=headers)
    assert resp.status_code == 204
    assert fake_store.deleted_uids == [chunk["uid"]]

    detail = await client.get(f"/api/documents/{document_id}", headers=headers)
    assert detail.json()["chunk_count"] == total_before - 1

    resp = await client.delete(f"/api/documents/{document_id}", headers=headers)
    assert resp.status_code == 204
    assert document_id in fake_store.deleted_docs
    missing = await client.get(f"/api/documents/{document_id}", headers=headers)
    assert missing.status_code == 404


async def test_reindex_document(
    client, fake_redis, db_maker, use_test_db, fake_store
) -> None:
    headers = await auth_headers(client, fake_redis)
    kb = await create_kb(client, headers)
    upload = await upload_markdown(client, headers, kb["id"])
    document_id = upload.json()["id"]
    inserted_after_first = len(fake_store.inserted)
    old_uids = {row["chunk_uid"] for row in fake_store.inserted}

    resp = await client.post(f"/api/documents/{document_id}/reindex", headers=headers)
    assert resp.status_code == 202
    detail = await client.get(f"/api/documents/{document_id}", headers=headers)
    assert detail.json()["status"] == "completed"
    assert len(fake_store.inserted) > inserted_after_first
    # 重建索引只清理旧分块向量，不按文档整体删除（避免误删新向量）
    assert old_uids.issubset(set(fake_store.deleted_uids))


async def test_chunk_pagination_and_keyword(
    client, fake_redis, db_maker, use_test_db, fake_store
) -> None:
    headers = await auth_headers(client, fake_redis)
    kb = await create_kb(client, headers)
    upload = await upload_markdown(client, headers, kb["id"])
    document_id = upload.json()["id"]

    first = await client.get(
        f"/api/documents/{document_id}/chunks?page=1&page_size=1", headers=headers
    )
    second = await client.get(
        f"/api/documents/{document_id}/chunks?page=2&page_size=1", headers=headers
    )
    assert first.json()["total"] > 1
    assert len(first.json()["items"]) == 1
    assert first.json()["items"][0]["uid"] != second.json()["items"][0]["uid"]

    keyword = await client.get(
        f"/api/documents/{document_id}/chunks?keyword=质量问题", headers=headers
    )
    assert keyword.json()["total"] >= 1


async def test_delete_knowledge_base(
    client, fake_redis, db_maker, use_test_db, fake_store
) -> None:
    headers = await auth_headers(client, fake_redis)
    kb = await create_kb(client, headers)
    upload = await upload_markdown(client, headers, kb["id"])
    document_id = upload.json()["id"]

    resp = await client.delete(f"/api/knowledge_bases/{kb['id']}", headers=headers)
    assert resp.status_code == 204
    assert fake_store.deleted_kbs == [kb["id"]]

    assert (
        await client.get(f"/api/documents/{document_id}", headers=headers)
    ).status_code == 404
    assert (
        await client.get(f"/api/knowledge_bases/{kb['id']}", headers=headers)
    ).status_code == 404


async def test_uploaded_file_is_removed_with_kb(
    client, fake_redis, db_maker, use_test_db, fake_store, isolated_uploads
) -> None:
    headers = await auth_headers(client, fake_redis)
    kb = await create_kb(client, headers)
    await upload_markdown(client, headers, kb["id"])

    kb_dir = Path(isolated_uploads) / f"kb_{kb['id']}"
    assert kb_dir.exists()
    await client.delete(f"/api/knowledge_bases/{kb['id']}", headers=headers)
    assert not kb_dir.exists()


# --------------------------------------------------------------------------- #
# 文档更新、近重复与父子分块
# --------------------------------------------------------------------------- #
def policy_markdown(
    replacements: dict[int, str] | None = None,
    punctuation: str = "。",
) -> bytes:
    replacements = replacements or {}
    paragraphs = []
    for index in range(1, 13):
        text = replacements.get(
            index,
            f"第{index}条：自签收之日起七天内，商品保持完好可申请无理由退换货。"
            f"退换货产生的运费按照责任方承担，质量问题由商家承担。",
        )
        paragraphs.append(text.replace("。", punctuation))
    return ("# 退换货规则\n\n" + "\n\n".join(paragraphs)).encode("utf-8")


async def upload_content(
    client,
    headers,
    kb_id: int,
    content: bytes,
    filename: str = "退换货规则.md",
    *,
    force_new: bool = False,
):
    data = {"chunk_size": "300", "chunk_overlap": "60", "embedding_mode": "hybrid"}
    if force_new:
        data["force_new"] = "true"
    return await client.post(
        f"/api/knowledge_bases/{kb_id}/documents",
        headers=headers,
        files={"file": (filename, content, "text/markdown")},
        data=data,
    )


async def list_docs(client, headers, kb_id: int, *, include_history: bool = False):
    resp = await client.get(
        f"/api/knowledge_bases/{kb_id}/documents"
        f"?include_history={'true' if include_history else 'false'}",
        headers=headers,
    )
    assert resp.status_code == 200, resp.text
    return resp.json()


async def test_identical_reupload_is_skipped_without_duplicate(
    client, fake_redis, db_maker, use_test_db, fake_store
) -> None:
    headers = await auth_headers(client, fake_redis)
    kb = await create_kb(client, headers)
    first = await upload_content(client, headers, kb["id"], policy_markdown())
    first_id = first.json()["id"]

    second = await upload_content(client, headers, kb["id"], policy_markdown())
    second_detail = await client.get(
        f"/api/documents/{second.json()['id']}", headers=headers
    )
    payload = second_detail.json()
    assert payload["status"] == "skipped"
    assert payload["update_action"] == "unchanged"
    assert payload["is_current"] is False
    assert payload["similar_doc_id"] == first_id
    assert payload["similarity"] == 1.0

    docs = await list_docs(client, headers, kb["id"])
    assert [item["id"] for item in docs] == [first_id]
    kb_detail = await client.get(f"/api/knowledge_bases/{kb['id']}", headers=headers)
    assert kb_detail.json()["document_count"] == 1


async def test_punctuation_only_change_with_new_filename_is_unchanged(
    client, fake_redis, db_maker, use_test_db, fake_store
) -> None:
    headers = await auth_headers(client, fake_redis)
    kb = await create_kb(client, headers)
    first = await upload_content(client, headers, kb["id"], policy_markdown())
    first_id = first.json()["id"]

    second = await upload_content(
        client,
        headers,
        kb["id"],
        policy_markdown(punctuation="！"),
        filename="退换货规则-修订.md",
    )
    payload = (
        await client.get(f"/api/documents/{second.json()['id']}", headers=headers)
    ).json()
    assert payload["status"] == "skipped"
    assert payload["update_action"] == "unchanged"
    assert payload["similar_doc_id"] == first_id

    docs = await list_docs(client, headers, kb["id"])
    assert len(docs) == 1


async def test_same_name_change_creates_new_current_version(
    client, fake_redis, db_maker, use_test_db, fake_store
) -> None:
    headers = await auth_headers(client, fake_redis)
    kb = await create_kb(client, headers)
    first = await upload_content(client, headers, kb["id"], policy_markdown())
    first_id = first.json()["id"]

    changed = policy_markdown(
        {1: "第1条：会员积分可在结算时抵扣现金，每100积分抵扣1元。"}
    )
    second = await upload_content(client, headers, kb["id"], changed)
    second_id = second.json()["id"]
    payload = (
        await client.get(f"/api/documents/{second_id}", headers=headers)
    ).json()

    assert payload["status"] == "completed"
    assert payload["version"] == 2
    assert payload["is_current"] is True
    assert payload["update_action"] == "updated"
    assert payload["replaces_doc_id"] == first_id
    assert payload["similarity"] and payload["similarity"] >= 0.90

    old = await client.get(f"/api/documents/{first_id}", headers=headers)
    assert old.json()["is_current"] is False
    assert old.json()["update_action"] == "superseded"

    docs = await list_docs(client, headers, kb["id"])
    assert [item["id"] for item in docs] == [second_id]
    assert first_id in fake_store.deleted_docs


async def test_similar_content_with_different_filename_is_updated(
    client, fake_redis, db_maker, use_test_db, fake_store
) -> None:
    headers = await auth_headers(client, fake_redis)
    kb = await create_kb(client, headers)
    first = await upload_content(
        client, headers, kb["id"], policy_markdown(), filename="售后政策.md"
    )
    first_id = first.json()["id"]

    changed = policy_markdown(
        {1: "第1条：会员积分可在结算时抵扣现金，每100积分抵扣1元。"}
    )
    second = await upload_content(
        client, headers, kb["id"], changed, filename="售后政策-2026版.md"
    )
    second_id = second.json()["id"]
    payload = (
        await client.get(f"/api/documents/{second_id}", headers=headers)
    ).json()

    assert payload["update_action"] == "updated"
    assert payload["replaces_doc_id"] == first_id
    assert payload["version"] == 2
    docs = await list_docs(client, headers, kb["id"])
    assert [item["id"] for item in docs] == [second_id]


async def test_dissimilar_content_with_different_filename_is_new_document(
    client, fake_redis, db_maker, use_test_db, fake_store
) -> None:
    headers = await auth_headers(client, fake_redis)
    kb = await create_kb(client, headers)
    first = await upload_content(
        client, headers, kb["id"], policy_markdown(), filename="售后政策.md"
    )
    first_id = first.json()["id"]

    changed = policy_markdown(
        {
            1: "第1条：会员积分可在结算时抵扣现金，每100积分抵扣1元。",
            2: "第2条：生鲜类商品因保质期特殊，签收后24小时内可申请售后。",
            3: "第3条：定制服装一经裁剪制作，不支持无理由退换。",
        }
    )
    second = await upload_content(
        client, headers, kb["id"], changed, filename="会员与生鲜规则.md"
    )
    second_id = second.json()["id"]

    docs = await list_docs(client, headers, kb["id"])
    assert {item["id"] for item in docs} == {first_id, second_id}
    assert all(item["version"] == 1 for item in docs)


async def test_force_new_bypasses_dedup(
    client, fake_redis, db_maker, use_test_db, fake_store
) -> None:
    headers = await auth_headers(client, fake_redis)
    kb = await create_kb(client, headers)
    first = await upload_content(client, headers, kb["id"], policy_markdown())
    second = await upload_content(
        client, headers, kb["id"], policy_markdown(), force_new=True
    )
    payload = (
        await client.get(f"/api/documents/{second.json()['id']}", headers=headers)
    ).json()
    assert payload["update_action"] == "created"
    assert payload["is_current"] is True
    assert payload["chunk_count"] > 0

    docs = await list_docs(client, headers, kb["id"])
    assert {item["id"] for item in docs} == {first.json()["id"], second.json()["id"]}


async def test_failed_update_keeps_old_version_current(
    client, fake_redis, db_maker, use_test_db, fake_store, monkeypatch
) -> None:
    headers = await auth_headers(client, fake_redis)
    kb = await create_kb(client, headers)
    first = await upload_content(client, headers, kb["id"], policy_markdown())
    first_id = first.json()["id"]

    state = {"fail": True}
    original_embed = ingestion.embedding_service.embed_documents

    async def flaky_embed(texts, batch_size=None):
        if state["fail"]:
            raise RuntimeError("embedding service unavailable")
        return await original_embed(texts, batch_size=batch_size)

    monkeypatch.setattr(
        ingestion.embedding_service, "embed_documents", flaky_embed
    )

    changed = policy_markdown(
        {1: "第1条：会员积分可在结算时抵扣现金，每100积分抵扣1元。"}
    )
    failed = await upload_content(client, headers, kb["id"], changed)
    failed_id = failed.json()["id"]
    payload = (
        await client.get(f"/api/documents/{failed_id}", headers=headers)
    ).json()
    assert payload["status"] == "failed"
    assert payload["update_action"] == "update_failed"
    assert payload["is_current"] is False
    assert payload["chunk_count"] == 0

    old = (await client.get(f"/api/documents/{first_id}", headers=headers)).json()
    assert old["status"] == "completed"
    assert old["is_current"] is True
    assert old["chunk_count"] > 0

    docs = await list_docs(client, headers, kb["id"])
    assert [item["id"] for item in docs] == [first_id]
    kb_detail = await client.get(f"/api/knowledge_bases/{kb['id']}", headers=headers)
    assert kb_detail.json()["document_count"] == 1


async def test_chunks_expose_parent_context(
    client, fake_redis, db_maker, use_test_db, fake_store
) -> None:
    headers = await auth_headers(client, fake_redis)
    kb = await create_kb(client, headers)
    upload = await upload_content(client, headers, kb["id"], policy_markdown())
    document_id = upload.json()["id"]

    chunks = await client.get(f"/api/documents/{document_id}/chunks", headers=headers)
    item = chunks.json()["items"][0]
    assert item["parent_uid"]
    assert item["parent_index"] is not None
    assert item["content"] in item["parent_content"]

    parents = await client.get(
        f"/api/documents/{document_id}/parents", headers=headers
    )
    assert parents.status_code == 200
    parent_uids = {parent["uid"] for parent in parents.json()}
    assert item["parent_uid"] in parent_uids
    parent_before = next(
        row for row in parents.json() if row["uid"] == item["parent_uid"]
    )["content"]

    new_content = "支持货到付款，满199元包邮。"
    edited = await client.patch(
        f"/api/chunks/{item['uid']}", headers=headers, json={"content": new_content}
    )
    assert edited.status_code == 200

    parents_after = await client.get(
        f"/api/documents/{document_id}/parents", headers=headers
    )
    parent = next(
        row for row in parents_after.json() if row["uid"] == item["parent_uid"]
    )
    assert new_content in parent["content"]
    assert parent["content"] != parent_before


def mineru_ready(**kwargs) -> dict:
    return {
        "available": True,
        "image": "asktofit-mineru:cpu",
        "reason": None,
    }


async def test_parser_options_expose_mineru_status(client, monkeypatch) -> None:
    monkeypatch.setattr(knowledge_module, "mineru_status", mineru_ready)

    resp = await client.get("/api/parsers/options")

    assert resp.status_code == 200
    payload = resp.json()
    assert payload["auto"]["available"] is True
    assert payload["mineru"]["available"] is True
    assert payload["mineru"]["image"] == "asktofit-mineru:cpu"


async def test_parser_options_report_unavailable_reason(client, monkeypatch) -> None:
    monkeypatch.setattr(
        knowledge_module,
        "mineru_status",
        lambda **kwargs: {
            "available": False,
            "image": "asktofit-mineru:cpu",
            "reason": "Docker 未启动",
        },
    )

    resp = await client.get("/api/parsers/options")

    assert resp.status_code == 200
    assert resp.json()["mineru"]["available"] is False
    assert resp.json()["mineru"]["reason"] == "Docker 未启动"


async def test_upload_with_mineru_records_parser(
    client, fake_redis, db_maker, use_test_db, fake_store, monkeypatch
) -> None:
    headers = await auth_headers(client, fake_redis)
    kb = await create_kb(client, headers)
    monkeypatch.setattr(knowledge_module, "mineru_status", mineru_ready)

    async def fake_parse(document):
        return ParsedDocument(
            title="复杂政策",
            blocks=[
                ParsedBlock(
                    text="复杂版式政策正文。" * 20,
                    title="复杂政策",
                    section="深度解析",
                    page=1,
                )
            ],
        )

    monkeypatch.setattr(ingestion, "_parse_stored_document", fake_parse)

    resp = await client.post(
        f"/api/knowledge_bases/{kb['id']}/documents",
        headers=headers,
        files={"file": ("复杂政策.pdf", b"%PDF-1.4 fake", "application/pdf")},
        data={
            "chunk_size": "300",
            "chunk_overlap": "60",
            "embedding_mode": "hybrid",
            "parser": "mineru",
        },
    )

    assert resp.status_code == 202, resp.text
    document_id = resp.json()["id"]
    assert resp.json()["parser"] == "mineru"

    detail = await client.get(f"/api/documents/{document_id}", headers=headers)
    assert detail.status_code == 200
    assert detail.json()["status"] == "completed"
    assert detail.json()["parser"] == "mineru"
    assert fake_store.inserted


async def test_upload_rejects_unknown_parser(
    client, fake_redis, db_maker, use_test_db, fake_store
) -> None:
    headers = await auth_headers(client, fake_redis)
    kb = await create_kb(client, headers)

    resp = await client.post(
        f"/api/knowledge_bases/{kb['id']}/documents",
        headers=headers,
        files={"file": ("policy.md", b"# policy\n\ncontent", "text/markdown")},
        data={"parser": "unknown"},
    )

    assert resp.status_code == 400
    assert "auto / mineru" in resp.json()["detail"]


async def test_upload_rejects_mineru_for_markdown(
    client, fake_redis, db_maker, use_test_db, fake_store, monkeypatch
) -> None:
    headers = await auth_headers(client, fake_redis)
    kb = await create_kb(client, headers)
    monkeypatch.setattr(knowledge_module, "mineru_status", mineru_ready)

    resp = await client.post(
        f"/api/knowledge_bases/{kb['id']}/documents",
        headers=headers,
        files={"file": ("policy.md", b"# policy\n\ncontent", "text/markdown")},
        data={"parser": "mineru"},
    )

    assert resp.status_code == 400
    assert "暂不支持" in resp.json()["detail"]


async def test_upload_rejects_mineru_when_unavailable(
    client, fake_redis, db_maker, use_test_db, fake_store, monkeypatch
) -> None:
    headers = await auth_headers(client, fake_redis)
    kb = await create_kb(client, headers)
    monkeypatch.setattr(
        knowledge_module,
        "mineru_status",
        lambda **kwargs: {
            "available": False,
            "image": "asktofit-mineru:cpu",
            "reason": "MinerU 镜像不可用",
        },
    )

    resp = await client.post(
        f"/api/knowledge_bases/{kb['id']}/documents",
        headers=headers,
        files={"file": ("复杂政策.pdf", b"%PDF-1.4 fake", "application/pdf")},
        data={"parser": "mineru"},
    )

    assert resp.status_code == 400
    assert resp.json()["detail"] == "MinerU 镜像不可用"


async def test_delete_chunk_refreshes_parent(
    client, fake_redis, db_maker, use_test_db, fake_store
) -> None:
    headers = await auth_headers(client, fake_redis)
    kb = await create_kb(client, headers)
    upload = await upload_content(client, headers, kb["id"], policy_markdown())
    document_id = upload.json()["id"]

    chunks = await client.get(
        f"/api/documents/{document_id}/chunks?page_size=100", headers=headers
    )
    item = chunks.json()["items"][0]
    parent_uid = item["parent_uid"]

    before = await client.get(
        f"/api/documents/{document_id}/parents", headers=headers
    )
    parent_before = next(
        row for row in before.json() if row["uid"] == parent_uid
    )["content"]

    resp = await client.delete(f"/api/chunks/{item['uid']}", headers=headers)
    assert resp.status_code == 204

    parents = await client.get(
        f"/api/documents/{document_id}/parents", headers=headers
    )
    parent = next(row for row in parents.json() if row["uid"] == parent_uid)
    assert parent["content"] != parent_before
