"""注册 / 登录 / 验证码 / JWT 鉴权测试。"""

from sqlalchemy import select

from app.models.user import User
from app.services.captcha import CAPTCHA_KEY_PREFIX


async def fetch_captcha(client, redis) -> tuple[str, str]:
    resp = await client.get("/api/auth/captcha")
    assert resp.status_code == 200
    data = resp.json()
    code = await redis.get(CAPTCHA_KEY_PREFIX + data["captcha_id"])
    assert code
    return data["captcha_id"], code


async def register_user(client, redis, username: str = "alice", password: str = "secret123"):
    captcha_id, code = await fetch_captcha(client, redis)
    return await client.post(
        "/api/auth/register",
        json={
            "username": username,
            "password": password,
            "confirm_password": password,
            "captcha_id": captcha_id,
            "captcha_code": code,
        },
    )


async def login_user(client, redis, username: str = "alice", password: str = "secret123"):
    captcha_id, code = await fetch_captcha(client, redis)
    return await client.post(
        "/api/auth/login",
        json={
            "username": username,
            "password": password,
            "captcha_id": captcha_id,
            "captcha_code": code,
        },
    )


async def test_captcha_returns_png_data_uri(client, fake_redis) -> None:
    resp = await client.get("/api/auth/captcha")
    assert resp.status_code == 200
    data = resp.json()
    assert data["image"].startswith("data:image/png;base64,")
    assert data["expires_in"] > 0
    assert await fake_redis.ttl(CAPTCHA_KEY_PREFIX + data["captcha_id"]) > 0


async def test_register_success(client, fake_redis) -> None:
    resp = await register_user(client, fake_redis, username="Alice_01")
    assert resp.status_code == 201
    data = resp.json()
    assert data["username"] == "alice_01"
    assert "password" not in data and "password_hash" not in data
    assert data["id"] > 0


async def test_register_is_case_insensitive_for_captcha(client, fake_redis) -> None:
    captcha_id, code = await fetch_captcha(client, fake_redis)
    resp = await client.post(
        "/api/auth/register",
        json={
            "username": "bob",
            "password": "secret123",
            "confirm_password": "secret123",
            "captcha_id": captcha_id,
            "captcha_code": code.lower(),
        },
    )
    assert resp.status_code == 201


async def test_register_password_mismatch(client, fake_redis) -> None:
    captcha_id, code = await fetch_captcha(client, fake_redis)
    resp = await client.post(
        "/api/auth/register",
        json={
            "username": "bob",
            "password": "secret123",
            "confirm_password": "secret456",
            "captcha_id": captcha_id,
            "captcha_code": code,
        },
    )
    assert resp.status_code == 422
    assert "两次输入的密码不一致" in resp.text


async def test_register_wrong_captcha(client, fake_redis) -> None:
    captcha_id, _ = await fetch_captcha(client, fake_redis)
    resp = await client.post(
        "/api/auth/register",
        json={
            "username": "bob",
            "password": "secret123",
            "confirm_password": "secret123",
            "captcha_id": captcha_id,
            "captcha_code": "XXXX",
        },
    )
    assert resp.status_code == 400


async def test_captcha_is_one_time(client, fake_redis) -> None:
    captcha_id, code = await fetch_captcha(client, fake_redis)
    payload = {
        "password": "secret123",
        "confirm_password": "secret123",
        "captcha_id": captcha_id,
        "captcha_code": code,
    }
    first = await client.post("/api/auth/register", json={**payload, "username": "bob"})
    assert first.status_code == 201
    second = await client.post("/api/auth/register", json={**payload, "username": "carol"})
    assert second.status_code == 400


async def test_register_duplicate_username(client, fake_redis) -> None:
    first = await register_user(client, fake_redis, username="bob")
    assert first.status_code == 201
    second = await register_user(client, fake_redis, username="BOB")
    assert second.status_code == 409


async def test_register_rejects_invalid_username(client, fake_redis) -> None:
    captcha_id, code = await fetch_captcha(client, fake_redis)
    resp = await client.post(
        "/api/auth/register",
        json={
            "username": "ab",
            "password": "secret123",
            "confirm_password": "secret123",
            "captcha_id": captcha_id,
            "captcha_code": code,
        },
    )
    assert resp.status_code == 422


async def test_password_is_hashed(client, fake_redis, db_maker) -> None:
    resp = await register_user(client, fake_redis, username="bob")
    assert resp.status_code == 201
    async with db_maker() as session:
        user = await session.scalar(select(User).where(User.username == "bob"))
        assert user is not None
        assert user.password_hash != "secret123"
        assert user.password_hash.startswith("$2")


async def test_login_success_and_me(client, fake_redis) -> None:
    await register_user(client, fake_redis)
    resp = await login_user(client, fake_redis)
    assert resp.status_code == 200
    data = resp.json()
    assert data["token_type"] == "bearer"
    assert data["expires_in"] > 0
    assert data["user"]["username"] == "alice"

    me = await client.get(
        "/api/auth/me", headers={"Authorization": f"Bearer {data['access_token']}"}
    )
    assert me.status_code == 200
    assert me.json()["username"] == "alice"


async def test_login_wrong_password(client, fake_redis) -> None:
    await register_user(client, fake_redis)
    resp = await login_user(client, fake_redis, password="wrong-pass")
    assert resp.status_code == 401


async def test_login_unknown_user(client, fake_redis) -> None:
    resp = await login_user(client, fake_redis, username="nobody")
    assert resp.status_code == 401


async def test_login_wrong_captcha(client, fake_redis) -> None:
    await register_user(client, fake_redis)
    captcha_id, _ = await fetch_captcha(client, fake_redis)
    resp = await client.post(
        "/api/auth/login",
        json={
            "username": "alice",
            "password": "secret123",
            "captcha_id": captcha_id,
            "captcha_code": "XXXX",
        },
    )
    assert resp.status_code == 400


async def test_me_requires_token(client) -> None:
    resp = await client.get("/api/auth/me")
    assert resp.status_code == 401


async def test_me_rejects_invalid_token(client) -> None:
    resp = await client.get("/api/auth/me", headers={"Authorization": "Bearer not-a-token"})
    assert resp.status_code == 401
