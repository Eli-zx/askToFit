"""知识库权限：管理能力仅管理员可用，普通用户只能读取公开知识库。

覆盖：
- 普通用户创建 / 查看详情 / 修改 / 删除知识库均为 403；
- 普通用户的知识库列表只包含公开库；
- 普通用户聊天只能选择公开库，管理员不受限制。
"""

from __future__ import annotations

from sqlalchemy import select, update

from app.main import app as fastapi_app
from app.models.knowledge_base import KnowledgeBase
from app.models.user import User
from app.services.captcha import CAPTCHA_KEY_PREFIX


async def _set_admin(db_maker, username: str, value: bool) -> None:
    async with db_maker() as session:
        await session.execute(
            update(User).where(User.username == username).values(is_admin=value)
        )
        await session.commit()


async def _auth_headers(
    client, redis, username: str, *, admin: bool
) -> dict:
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
    if admin:
        await _set_admin(fastapi_app.state.test_db_maker, username, True)
    return {"Authorization": f"Bearer {login.json()['access_token']}"}


async def _user_id(db_maker, username: str) -> int:
    async with db_maker() as session:
        user_id = await session.scalar(
            select(User.id).where(User.username == username)
        )
    assert user_id is not None
    return int(user_id)


async def _seed_kb(
    db_maker, owner_id: int, name: str, *, is_public: bool
) -> int:
    async with db_maker() as session:
        kb = KnowledgeBase(
            owner_id=owner_id,
            name=name,
            description="权限测试库",
            embedding_mode="hybrid",
            chunk_size=300,
            chunk_overlap=60,
            is_public=is_public,
        )
        session.add(kb)
        await session.commit()
        await session.refresh(kb)
        return int(kb.id)


async def test_normal_user_cannot_create_or_manage_knowledge_base(
    client, db_maker, fake_redis
) -> None:
    admin = await _auth_headers(client, fake_redis, "root", admin=True)
    normal = await _auth_headers(client, fake_redis, "alice", admin=False)
    owner_id = await _user_id(db_maker, "root")
    kb_id = await _seed_kb(db_maker, owner_id, "私有售后库", is_public=False)

    created = await client.post(
        "/api/knowledge_bases",
        headers=normal,
        json={"name": "普通用户自建库"},
    )
    assert created.status_code == 403

    assert (
        await client.get(f"/api/knowledge_bases/{kb_id}", headers=normal)
    ).status_code == 403
    assert (
        await client.patch(
            f"/api/knowledge_bases/{kb_id}",
            headers=normal,
            json={"description": "越权修改"},
        )
    ).status_code == 403
    assert (
        await client.delete(f"/api/knowledge_bases/{kb_id}", headers=normal)
    ).status_code == 403
    assert (
        await client.get(f"/api/knowledge_bases/{kb_id}/documents", headers=normal)
    ).status_code == 403
    assert (
        await client.get("/api/parsers/options", headers=normal)
    ).status_code == 403

    # 管理员仍然可以完整管理
    assert (
        await client.get(f"/api/knowledge_bases/{kb_id}", headers=admin)
    ).status_code == 200
    assert (
        await client.patch(
            f"/api/knowledge_bases/{kb_id}",
            headers=admin,
            json={"description": "管理员维护"},
        )
    ).status_code == 200


async def test_normal_user_list_only_contains_public_knowledge_bases(
    client, db_maker, fake_redis
) -> None:
    await _auth_headers(client, fake_redis, "root", admin=True)
    normal = await _auth_headers(client, fake_redis, "alice", admin=False)
    owner_id = await _user_id(db_maker, "root")
    private_id = await _seed_kb(db_maker, owner_id, "私有库", is_public=False)
    public_id = await _seed_kb(db_maker, owner_id, "公共服装库", is_public=True)

    listing = await client.get("/api/knowledge_bases", headers=normal)
    assert listing.status_code == 200
    ids = {item["id"] for item in listing.json()}
    assert public_id in ids
    assert private_id not in ids
    assert all(item["is_public"] for item in listing.json())


async def test_normal_user_chat_can_only_select_public_knowledge_base(
    client, db_maker, fake_redis
) -> None:
    admin = await _auth_headers(client, fake_redis, "root", admin=True)
    normal = await _auth_headers(client, fake_redis, "alice", admin=False)
    owner_id = await _user_id(db_maker, "root")
    private_id = await _seed_kb(db_maker, owner_id, "私有库", is_public=False)
    public_id = await _seed_kb(db_maker, owner_id, "公共服装库", is_public=True)

    payload = {
        "title": "权限测试",
        "kb_ids": [private_id],
        "model_name": "deepseekv4-flash",
    }
    denied = await client.post(
        "/api/chat/conversations", headers=normal, json=payload
    )
    assert denied.status_code == 400
    assert "无权访问" in denied.json()["detail"]

    allowed = await client.post(
        "/api/chat/conversations",
        headers=normal,
        json={**payload, "kb_ids": [public_id]},
    )
    assert allowed.status_code == 201
    assert allowed.json()["kb_ids"] == [public_id]

    # 管理员可以检索任意知识库
    admin_allowed = await client.post(
        "/api/chat/conversations", headers=admin, json=payload
    )
    assert admin_allowed.status_code == 201
    assert admin_allowed.json()["kb_ids"] == [private_id]
