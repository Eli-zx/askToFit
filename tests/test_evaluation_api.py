"""评估看板 API：管理员权限、分页过滤、趋势与删除。"""

from __future__ import annotations

import pytest
from sqlalchemy import update

from app.models.user import User
from app.services import evaluation_store
from app.services.captcha import CAPTCHA_KEY_PREFIX

_ENDPOINTS = [
    ("GET", "/api/evaluations/overview"),
    ("GET", "/api/evaluations/trends"),
    ("GET", "/api/evaluations/runs"),
    ("GET", "/api/evaluations/runs/1"),
    ("GET", "/api/evaluations/runs/1/cases"),
    ("DELETE", "/api/evaluations/runs/1"),
]


async def register_and_login(client, redis, username: str) -> dict:
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
    assert register.status_code == 201, register.text

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
    assert login.status_code == 200, login.text
    return {"Authorization": f"Bearer {login.json()['access_token']}"}


async def make_admin(db_maker, username: str) -> None:
    async with db_maker() as db:
        await db.execute(
            update(User).where(User.username == username).values(is_admin=True)
        )
        await db.commit()


async def seed_run(db_maker, **overrides) -> int:
    payload = {
        "dataset_name": "qa_eval.jsonl",
        "dataset_sha256": "b" * 64,
        "kb_id": 1,
        "kb_name": "服装售前知识库",
        "strategy": "auto",
        "top_k": 10,
        "judge_model": "deepseekv4-flash",
        "ragas_version": "0.3.0",
        "sample_count": 3,
        "answerable_count": 2,
        "retrieval_metrics": {
            "doc": {
                "hit_at_k": 0.8,
                "recall_at_k": 0.7,
                "mrr": 0.6,
                "ndcg_at_k": 0.5,
            },
            "section": {
                "hit_at_k": 0.6,
                "recall_at_k": 0.5,
                "mrr": 0.4,
                "ndcg_at_k": 0.3,
            },
        },
        "generation_metrics": {"faithfulness": 0.75},
        "config_snapshot": {"bm25_mode": "fuse"},
        "status": "success",
        "error": None,
    }
    payload.update(overrides)
    cases = [
        {
            "case_id": f"eval-{index:03d}",
            "question": f"问题 {index}",
            "ground_truth": "答案",
            "answer": "回答",
            "intent": "logistics",
            "tags": ["single-hop"],
            "expected_doc_names": ["物流说明.md"],
            "expected_sections": ["四、运费标准"],
            "retrieved": [{"doc_name": "物流说明.md", "score": 0.9}],
            "doc_metrics": {"hit_at_k": 1.0},
            "section_metrics": {"hit_at_k": 1.0},
            "generation_metrics": {"faithfulness": 0.9},
            "keyword_coverage": 1.0,
            "error": "LLM 失败" if index == 2 else None,
        }
        for index in range(3)
    ]
    async with db_maker() as db:
        return await evaluation_store.save_eval_run(db, payload, cases, owner_id=None)


async def test_non_admin_gets_403_on_every_endpoint(
    client, fake_redis
) -> None:
    headers = await register_and_login(client, fake_redis, "normal")

    for method, path in _ENDPOINTS:
        response = await client.request(method, path, headers=headers)
        assert response.status_code == 403, (method, path, response.text)
        assert response.json()["detail"] == "需要管理员权限"


async def test_unauthenticated_gets_401(client) -> None:
    response = await client.get("/api/evaluations/runs")
    assert response.status_code == 401


async def test_admin_overview_and_runs_empty(client, fake_redis, db_maker) -> None:
    headers = await register_and_login(client, fake_redis, "admin")
    await make_admin(db_maker, "admin")

    overview = await client.get("/api/evaluations/overview", headers=headers)
    assert overview.status_code == 200
    assert overview.json() == {
        "latest": None,
        "previous": None,
        "total_runs": 0,
        "delta": None,
    }

    runs = await client.get("/api/evaluations/runs", headers=headers)
    assert runs.status_code == 200
    assert runs.json() == {"total": 0, "items": []}

    trends = await client.get("/api/evaluations/trends", headers=headers)
    assert trends.status_code == 200
    assert trends.json() == {"items": []}


async def test_admin_run_detail_cases_and_filters(
    client, fake_redis, db_maker
) -> None:
    headers = await register_and_login(client, fake_redis, "admin")
    await make_admin(db_maker, "admin")
    run_id = await seed_run(db_maker)

    detail = await client.get(f"/api/evaluations/runs/{run_id}", headers=headers)
    assert detail.status_code == 200
    assert detail.json()["strategy"] == "auto"
    assert detail.json()["retrieval_metrics"]["doc"]["hit_at_k"] == 0.8

    listing = await client.get(
        "/api/evaluations/runs", headers=headers, params={"strategy": "auto"}
    )
    assert listing.status_code == 200
    assert listing.json()["total"] == 1
    assert listing.json()["items"][0]["id"] == run_id

    empty = await client.get(
        "/api/evaluations/runs", headers=headers, params={"strategy": "hyde"}
    )
    assert empty.json() == {"total": 0, "items": []}

    cases = await client.get(
        f"/api/evaluations/runs/{run_id}/cases", headers=headers
    )
    assert cases.status_code == 200
    assert cases.json()["total"] == 3
    assert [item["case_id"] for item in cases.json()["items"]] == [
        "eval-000",
        "eval-001",
        "eval-002",
    ]

    failed = await client.get(
        f"/api/evaluations/runs/{run_id}/cases",
        headers=headers,
        params={"failed_only": "true"},
    )
    assert failed.json()["total"] == 1
    assert failed.json()["items"][0]["error"] == "LLM 失败"

    paged = await client.get(
        f"/api/evaluations/runs/{run_id}/cases",
        headers=headers,
        params={"limit": 1, "offset": 1},
    )
    assert paged.json()["total"] == 3
    assert [item["case_id"] for item in paged.json()["items"]] == ["eval-001"]


async def test_admin_overview_delta_and_trends(
    client, fake_redis, db_maker
) -> None:
    headers = await register_and_login(client, fake_redis, "admin")
    await make_admin(db_maker, "admin")
    first = await seed_run(db_maker, retrieval_metrics={
        "doc": {"hit_at_k": 0.5, "recall_at_k": 0.4, "mrr": 0.3, "ndcg_at_k": 0.2},
        "section": {"hit_at_k": 0.5, "recall_at_k": 0.4, "mrr": 0.3, "ndcg_at_k": 0.2},
    }, generation_metrics={"faithfulness": 0.5})
    second = await seed_run(db_maker)
    await seed_run(db_maker, status="failed", error="超时")

    overview = await client.get("/api/evaluations/overview", headers=headers)
    body = overview.json()
    assert body["total_runs"] == 3
    assert body["latest"]["id"] == second
    assert body["previous"]["id"] == first
    assert body["delta"]["retrieval"]["doc"]["hit_at_k"] == pytest.approx(0.3)
    assert body["delta"]["retrieval"]["section"]["recall_at_k"] == pytest.approx(0.1)
    assert body["delta"]["generation"]["faithfulness"] == pytest.approx(0.25)

    trends = await client.get("/api/evaluations/trends", headers=headers)
    items = trends.json()["items"]
    assert [item["id"] for item in items] == [first, second]
    assert items[0]["doc_hit_at_k"] == 0.5
    assert items[0]["section_recall_at_k"] == 0.4
    assert items[0]["generation"] == {"faithfulness": 0.5}
    assert items[1]["doc_hit_at_k"] == 0.8


async def test_missing_run_returns_404_and_literal_routes_work(
    client, fake_redis, db_maker
) -> None:
    headers = await register_and_login(client, fake_redis, "admin")
    await make_admin(db_maker, "admin")

    # /overview 与 /trends 先于 /runs/{run_id} 注册，不会被路径参数吞掉
    assert (await client.get("/api/evaluations/overview", headers=headers)).status_code == 200
    assert (await client.get("/api/evaluations/trends", headers=headers)).status_code == 200

    missing = await client.get("/api/evaluations/runs/9999", headers=headers)
    assert missing.status_code == 404
    assert missing.json()["detail"] == "评估运行不存在：9999"

    missing_cases = await client.get(
        "/api/evaluations/runs/9999/cases", headers=headers
    )
    assert missing_cases.status_code == 404

    missing_delete = await client.delete(
        "/api/evaluations/runs/9999", headers=headers
    )
    assert missing_delete.status_code == 404


async def test_delete_run_then_detail_404(client, fake_redis, db_maker) -> None:
    headers = await register_and_login(client, fake_redis, "admin")
    await make_admin(db_maker, "admin")
    run_id = await seed_run(db_maker)

    deleted = await client.delete(
        f"/api/evaluations/runs/{run_id}", headers=headers
    )
    assert deleted.status_code == 204

    detail = await client.get(f"/api/evaluations/runs/{run_id}", headers=headers)
    assert detail.status_code == 404
