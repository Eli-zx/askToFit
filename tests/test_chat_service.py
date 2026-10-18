"""查询改写兜底逻辑测试。"""

from app.services import chat as chat_module


async def test_rewrite_query_falls_back_to_previous_question(monkeypatch) -> None:
    """模型原样返回指代问题时，拼接上一轮用户问题以补全检索上下文。"""

    async def fake_complete(*args, **kwargs) -> str:
        return "那偏远地区呢？"

    monkeypatch.setattr(chat_module.llm_service, "complete", fake_complete)
    history = [
        {"role": "user", "content": "满多少包邮？"},
        {"role": "assistant", "content": "满 199 元包邮，偏远地区加收 15 元。"},
    ]

    rewritten = await chat_module.rewrite_query(
        "deepseekv4-flash", "那偏远地区呢？", history
    )

    assert rewritten == "满多少包邮？ 那偏远地区呢？"


async def test_rewrite_query_skips_previous_anaphoric_question(monkeypatch) -> None:
    """上一轮也是追问时，兜底拼接应跳过它，选取更早的完整问题。"""

    async def fake_complete(*args, **kwargs) -> str:
        return "那新疆呢？"

    monkeypatch.setattr(chat_module.llm_service, "complete", fake_complete)
    history = [
        {"role": "user", "content": "满多少包邮？"},
        {"role": "assistant", "content": "满 199 元包邮。"},
        {"role": "user", "content": "那偏远地区呢？"},
        {"role": "assistant", "content": "偏远地区加收 15 元。"},
    ]

    rewritten = await chat_module.rewrite_query(
        "deepseekv4-flash", "那新疆呢？", history
    )

    assert rewritten == "满多少包邮？ 那新疆呢？"


async def test_rewrite_query_keeps_model_rewrite(monkeypatch) -> None:
    """模型给出完整改写时直接采用，不再拼接历史。"""

    async def fake_complete(*args, **kwargs) -> str:
        return "偏远地区是否包邮，运费怎么计算？"

    monkeypatch.setattr(chat_module.llm_service, "complete", fake_complete)
    history = [{"role": "user", "content": "满多少包邮？"}]

    rewritten = await chat_module.rewrite_query(
        "deepseekv4-flash", "那偏远地区呢？", history
    )

    assert rewritten == "偏远地区是否包邮，运费怎么计算？"


def test_score_summary_prefers_rerank_but_keeps_vector_score() -> None:
    summary = chat_module._score_summary(
        [
            {
                "score": 0.03,  # RRF 融合分，量纲不可当向量分
                "vector_score": 0.72,
                "rerank_score": 0.91,
            }
        ]
    )

    assert summary == {
        "best_score": 0.91,
        "vector_score": 0.72,
        "rerank_score": 0.91,
        "retrieval_mode": "rerank",
    }


def test_score_summary_does_not_treat_fused_score_as_vector_score() -> None:
    summary = chat_module._score_summary([{"score": 0.03, "bm25_score": 12.0}])

    assert summary["vector_score"] is None
    assert summary["best_score"] is None
    assert summary["retrieval_mode"] == "vector"
