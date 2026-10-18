"""评估结果持久化：单事务写入、过滤分页、级联删除与看板聚合。"""

from __future__ import annotations

from datetime import datetime, timezone

from app.services import evaluation_store as store


def _run_payload(**overrides) -> dict:
    payload = {
        "dataset_name": "qa_eval.jsonl",
        "dataset_sha256": "a" * 64,
        "kb_id": 1,
        "kb_name": "服装售前知识库",
        "strategy": "auto",
        "top_k": 10,
        "judge_model": "deepseekv4-flash",
        "ragas_version": "0.3.0",
        "sample_count": 2,
        "answerable_count": 1,
        "retrieval_metrics": {"doc": {"hit_at_k": 1.0}},
        "generation_metrics": {"faithfulness": 0.8},
        "config_snapshot": {"bm25_mode": "fuse"},
        "status": "success",
        "started_at": datetime(2026, 10, 18, 9, 0, tzinfo=timezone.utc),
        "finished_at": datetime(2026, 10, 18, 9, 5, tzinfo=timezone.utc),
    }
    payload.update(overrides)
    return payload


def _case_payload(case_id: str = "eval-001", **overrides) -> dict:
    payload = {
        "case_id": case_id,
        "question": "满多少包邮？",
        "ground_truth": "满 199 元包邮。",
        "answer": "满 199 元包邮。",
        "intent": "logistics",
        "tags": ["single-hop"],
        "expected_doc_names": ["物流说明.md"],
        "expected_sections": ["四、运费标准"],
        "retrieved": [
            {"doc_name": "物流说明.md", "section": "四、运费标准", "score": 0.9}
        ],
        "doc_metrics": {"hit_at_k": 1.0, "recall_at_k": 1.0, "mrr": 1.0, "ndcg_at_k": 1.0},
        "section_metrics": {
            "hit_at_k": 1.0,
            "recall_at_k": 1.0,
            "mrr": 1.0,
            "ndcg_at_k": 1.0,
        },
        "generation_metrics": {"faithfulness": 0.9},
        "keyword_coverage": 1.0,
        "error": None,
    }
    payload.update(overrides)
    return payload


async def test_save_eval_run_persists_run_and_cases(db_maker) -> None:
    async with db_maker() as db:
        run_id = await store.save_eval_run(
            db, _run_payload(), [_case_payload()], owner_id=None
        )

        run = await store.get_eval_run(db, run_id)
        assert run is not None
        assert run.dataset_name == "qa_eval.jsonl"
        assert run.ragas_version == "0.3.0"
        assert run.retrieval_metrics == {"doc": {"hit_at_k": 1.0}}
        assert run.config_snapshot == {"bm25_mode": "fuse"}
        assert run.owner_id is None

        total, cases = await store.get_eval_cases(db, run_id)
        assert total == 1
        assert cases[0].case_id == "eval-001"
        assert cases[0].tags == ["single-hop"]
        assert cases[0].retrieved[0]["doc_name"] == "物流说明.md"
        assert cases[0].generation_metrics == {"faithfulness": 0.9}


async def test_save_eval_run_retrieval_only_allows_null_generation(db_maker) -> None:
    async with db_maker() as db:
        run_id = await store.save_eval_run(
            db,
            _run_payload(ragas_version=None, generation_metrics=None),
            [_case_payload(generation_metrics=None, answer=None)],
            owner_id=None,
        )

        run = await store.get_eval_run(db, run_id)
        _, cases = await store.get_eval_cases(db, run_id)

        assert run.generation_metrics is None
        assert run.ragas_version is None
        assert cases[0].generation_metrics is None
        assert cases[0].answer is None


async def test_save_eval_run_bulk_cases_share_transaction(db_maker) -> None:
    async with db_maker() as db:
        cases = [_case_payload(f"eval-{index:03d}") for index in range(5)]
        run_id = await store.save_eval_run(db, _run_payload(), cases, owner_id=None)
        total, rows = await store.get_eval_cases(db, run_id, limit=100)
        assert total == 5
        assert [row.case_id for row in rows] == [f"eval-{index:03d}" for index in range(5)]


async def test_list_eval_runs_orders_desc_filters_and_paginates(db_maker) -> None:
    async with db_maker() as db:
        ids = []
        ids.append(
            await store.save_eval_run(db, _run_payload(strategy="auto"), [], None)
        )
        ids.append(
            await store.save_eval_run(
                db, _run_payload(strategy="direct", status="failed"), [], None
            )
        )
        ids.append(
            await store.save_eval_run(
                db, _run_payload(strategy="hyde", status="partial"), [], None
            )
        )

        total, rows = await store.list_eval_runs(db)
        assert total == 3
        assert [row.id for row in rows] == list(reversed(ids))

        total, rows = await store.list_eval_runs(db, strategy="direct")
        assert total == 1
        assert rows[0].strategy == "direct"

        total, rows = await store.list_eval_runs(db, status="success")
        assert total == 1
        assert rows[0].strategy == "auto"

        total, rows = await store.list_eval_runs(db, limit=1, offset=1)
        assert total == 3
        assert [row.id for row in rows] == [ids[1]]


async def test_eval_cases_pagination_and_failed_only(db_maker) -> None:
    async with db_maker() as db:
        cases = [
            _case_payload("eval-001"),
            _case_payload("eval-002", error="检索超时"),
            _case_payload("eval-003", answer=None, error="LLM 失败"),
        ]
        run_id = await store.save_eval_run(db, _run_payload(), cases, owner_id=None)

        total, page = await store.get_eval_cases(db, run_id, limit=2, offset=0)
        assert total == 3
        assert [row.case_id for row in page] == ["eval-001", "eval-002"]

        total, page = await store.get_eval_cases(db, run_id, limit=2, offset=2)
        assert total == 3
        assert [row.case_id for row in page] == ["eval-003"]

        total, failed = await store.get_eval_cases(db, run_id, failed_only=True)
        assert total == 2
        assert [row.case_id for row in failed] == ["eval-002", "eval-003"]

        total, missing = await store.get_eval_cases(db, 9999)
        assert total == 0
        assert missing == []


async def test_delete_eval_run_removes_cases(db_maker) -> None:
    async with db_maker() as db:
        run_id = await store.save_eval_run(
            db, _run_payload(), [_case_payload("eval-001"), _case_payload("eval-002")], None
        )

        assert await store.delete_eval_run(db, run_id) is True
        assert await store.get_eval_run(db, run_id) is None
        total, cases = await store.get_eval_cases(db, run_id)
        assert total == 0 and cases == []
        assert await store.delete_eval_run(db, run_id) is False


async def test_overview_uses_latest_and_previous_success(db_maker) -> None:
    async with db_maker() as db:
        await store.save_eval_run(
            db,
            _run_payload(
                status="success",
                retrieval_metrics={"doc": {"hit_at_k": 0.6}},
            ),
            [],
            None,
        )
        await store.save_eval_run(
            db, _run_payload(status="failed", error="boom"), [], None
        )
        await store.save_eval_run(
            db,
            _run_payload(
                status="success",
                retrieval_metrics={"doc": {"hit_at_k": 0.9}},
            ),
            [],
            None,
        )

        overview = await store.get_overview(db)

        assert overview["total_runs"] == 3
        assert overview["latest"].retrieval_metrics == {"doc": {"hit_at_k": 0.9}}
        assert overview["previous"].retrieval_metrics == {"doc": {"hit_at_k": 0.6}}


async def test_overview_empty_database(db_maker) -> None:
    async with db_maker() as db:
        overview = await store.get_overview(db)
        assert overview == {"latest": None, "previous": None, "total_runs": 0}


async def test_trends_return_success_only_in_chronological_order(db_maker) -> None:
    async with db_maker() as db:
        for index in range(4):
            await store.save_eval_run(
                db,
                _run_payload(
                    status="success" if index != 1 else "failed",
                    retrieval_metrics={"doc": {"hit_at_k": index / 10}},
                ),
                [],
                None,
            )

        trends = await store.list_eval_trends(db, limit=10)

        assert [row.status for row in trends] == ["success", "success", "success"]
        assert [
            row.retrieval_metrics["doc"]["hit_at_k"] for row in trends
        ] == [0.0, 0.2, 0.3]

        limited = await store.list_eval_trends(db, limit=2)
        assert [
            row.retrieval_metrics["doc"]["hit_at_k"] for row in limited
        ] == [0.2, 0.3]
