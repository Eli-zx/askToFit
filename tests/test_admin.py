"""管理员字段、权限依赖与启动期账号引导。"""

from __future__ import annotations

import pytest
from fastapi import HTTPException
from sqlalchemy import select

from app.api.deps import get_current_admin
from app.models.user import User
from app.services import admin as admin_module
from config import Config
from scripts import set_admin


def _user(username: str, is_admin: bool = False) -> User:
    return User(
        id=1,
        username=username,
        password_hash="x",
        is_active=True,
        is_admin=is_admin,
    )


async def test_is_admin_defaults_to_false_in_database(db_maker) -> None:
    async with db_maker() as db:
        db.add(User(username="alice", password_hash="x"))
        await db.commit()

    async with db_maker() as db:
        user = await db.scalar(select(User).where(User.username == "alice"))
        assert user.is_admin is False


async def test_get_current_admin_allows_admin() -> None:
    user = _user("admin", is_admin=True)
    assert await get_current_admin(user=user) is user


@pytest.mark.parametrize("is_admin", [False, None])
async def test_get_current_admin_rejects_normal_user(is_admin) -> None:
    user = _user("alice", is_admin=bool(is_admin))

    with pytest.raises(HTTPException) as exc_info:
        await get_current_admin(user=user)

    assert exc_info.value.status_code == 403
    assert exc_info.value.detail == "需要管理员权限"


async def test_sync_admin_users_promotes_configured_accounts_idempotently(
    db_maker, monkeypatch
) -> None:
    monkeypatch.setattr(admin_module, "get_session_maker", lambda: db_maker)
    monkeypatch.setattr(
        Config, "admin_usernames", (" Alice ", "bob", "BOB", "")
    )

    async with db_maker() as db:
        db.add_all(
            [
                User(username="alice", password_hash="x"),
                User(username="bob", password_hash="x"),
                User(username="carol", password_hash="x"),
            ]
        )
        await db.commit()

        assert await admin_module.sync_admin_users() == 2

    async with db_maker() as db:
        rows = {
            user.username: user.is_admin
            for user in (await db.scalars(select(User))).all()
        }
        assert rows == {"alice": True, "bob": True, "carol": False}

    # 第二次调用不再产生变更
    assert await admin_module.sync_admin_users() == 0


async def test_sync_admin_users_without_config_is_noop(
    db_maker, monkeypatch
) -> None:
    monkeypatch.setattr(admin_module, "get_session_maker", lambda: db_maker)
    monkeypatch.setattr(Config, "admin_usernames", ())
    assert await admin_module.sync_admin_users() == 0


async def test_set_admin_script_grants_and_revokes(
    db_maker, monkeypatch, capsys
) -> None:
    monkeypatch.setattr(set_admin, "get_session_maker", lambda: db_maker)
    monkeypatch.setattr(set_admin, "ensure_database", lambda: None)

    async def noop_async() -> None:
        return None

    monkeypatch.setattr(set_admin, "init_models", noop_async)
    monkeypatch.setattr(set_admin, "ensure_schema_upgrades", noop_async)

    async with db_maker() as db:
        db.add(User(username="alice", password_hash="x"))
        await db.commit()

    assert await set_admin._run("Alice", revoke=False) == 0
    assert "已授予管理员：alice" in capsys.readouterr().out

    async with db_maker() as db:
        user = await db.scalar(select(User).where(User.username == "alice"))
        assert user.is_admin is True

    assert await set_admin._run("alice", revoke=True) == 0
    assert "已撤销管理员：alice" in capsys.readouterr().out

    async with db_maker() as db:
        user = await db.scalar(select(User).where(User.username == "alice"))
        assert user.is_admin is False

    assert await set_admin._run("nobody", revoke=False) == 1
    assert "用户不存在：nobody" in capsys.readouterr().out
