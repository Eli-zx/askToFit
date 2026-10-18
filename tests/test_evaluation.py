"""评估纯函数：检索指标手算、样本构造与 RAGAS 分数合并。"""

from __future__ import annotations

import math
import sys

from app.services import evaluation as ev

_LG3 = 1.0 / math.log2(3)


def _hit(doc: str | None, section: str | None = None) -> dict:
    return {"doc_name": doc, "section": section}


# --------------------------------------------------------------------------- #
# 相关性单元与匹配
# --------------------------------------------------------------------------- #
def test_normalize_unit_handles_width_case_space_and_punctuation() -> None:
    assert ev.normalize_unit("物流仓配与时效说明.MD") == "物流仓配与时效说明md"
    assert ev.normalize_unit(" 四、运费标准 ") == "四运费标准"
    assert ev.normalize_unit("ＡＢＣ－１２３") == "abc123"
    assert ev.normalize_unit(None) == ""


def test_match_doc_and_section_use_bidirectional_containment() -> None:
    hit = _hit("物流仓配与时效说明.md", "四、运费标准")

    assert ev.match_doc_name(hit, "物流仓配与时效说明")
    assert ev.match_doc_name(hit, "物流仓配")
    assert ev.match_section(hit, "运费标准")
    assert ev.match_section(hit, "四、运费标准")
    assert not ev.match_section(hit, "退换货政策")
    assert not ev.match_doc_name({"section": "运费标准"}, "物流说明")


def test_topic_units_section_level_uses_section_units() -> None:
    doc_units = ev.topic_units(["A.md"], ["四、运费标准"], "doc")
    section_units = ev.topic_units(["A.md"], ["四、运费标准"], "section")

    assert doc_units == {"amd"}
    assert section_units == {"四运费标准"}
    # 多文档时不做笛卡尔积，避免章节分母虚增
    assert ev.topic_units(["A.md", "B.md"], ["运费标准"], "section") == {"运费标准"}

    try:
        ev.topic_units(["A.md"], [], "chunk")
    except ValueError as exc:
        assert "层级" in str(exc)
    else:  # pragma: no cover - 明确失败信息
        raise AssertionError("未知层级必须报错")


def test_hit_units_do_not_credit_section_from_wrong_document() -> None:
    hit = _hit("退换货政策.md", "四、运费标准")

    units = ev.hit_units(hit, ["物流说明.md"], ["四、运费标准"], "section")

    assert units == set()


# --------------------------------------------------------------------------- #
# 四项检索指标
# --------------------------------------------------------------------------- #
def test_hit_and_recall_and_mrr_hand_calculated() -> None:
    expected = {"a"}
    ranked = [set(), {"a"}]

    assert ev.hit_at_k(ranked, expected, 1) == 0.0
    assert ev.hit_at_k(ranked, expected, 2) == 1.0
    assert ev.recall_at_k(ranked, expected, 1) == 0.0
    assert ev.recall_at_k(ranked, expected, 2) == 1.0
    assert ev.mrr(ranked, expected) == 0.5


def test_recall_deduplicates_repeated_units_in_later_chunks() -> None:
    expected = {"a", "b"}
    ranked = [{"a"}, {"a", "b"}, {"b"}]

    assert ev.recall_at_k(ranked, expected, 1) == 0.5
    assert ev.recall_at_k(ranked, expected, 3) == 1.0
    assert ev.hit_at_k(ranked, expected, 3) == 1.0


def test_ndcg_hand_calculated_binary_gain() -> None:
    expected = {"a", "b"}
    # 第 1 位无命中，第 2 位命中一个（增益 1/log2(3)）
    ranked = [set(), {"a"}]
    assert math.isclose(
        ev.ndcg_at_k(ranked, expected, 2),
        _LG3 / (1.0 + _LG3),
        rel_tol=1e-9,
    )

    # 两个单元在同一排名位置命中仍只计一次相关
    assert math.isclose(
        ev.ndcg_at_k([{"a", "b"}], expected, 2),
        1.0 / (1.0 + _LG3),
        rel_tol=1e-9,
    )
    # 期望单元多于 k 时，IDCG 只按 k 个最理想位置构造
    assert math.isclose(
        ev.ndcg_at_k([{"a"}], expected, 1), 1.0, rel_tol=1e-9
    )


def test_metrics_return_none_without_expected_units() -> None:
    assert ev.hit_at_k([{"a"}], set(), 5) is None
    assert ev.recall_at_k([{"a"}], set(), 5) is None
    assert ev.mrr([{"a"}], set()) is None
    assert ev.ndcg_at_k([{"a"}], set(), 5) is None
    assert ev.hit_at_k([{"a"}], {"a"}, 0) is None
    assert ev.ndcg_at_k([{"a"}], {"a"}, 0) is None


def test_retrieval_metrics_reports_doc_and_section_levels() -> None:
    hits = [
        _hit("物流说明.md", "一、配送范围"),
        _hit("物流说明.md", "四、运费标准"),
        _hit("退换货政策.md", "换货条件"),
    ]

    metrics = ev.retrieval_metrics(
        hits,
        ["物流说明.md", "退换货政策.md"],
        ["四、运费标准", "换货条件"],
        k=3,
    )

    assert set(metrics) == {"doc", "section"}
    assert metrics["doc"]["hit_at_k"] == 1.0
    assert metrics["doc"]["recall_at_k"] == 1.0
    assert metrics["doc"]["mrr"] == 1.0
    # 第二个期望文档直到第 3 位才出现
    assert math.isclose(
        metrics["doc"]["ndcg_at_k"],
        (1.0 + 1.0 / math.log2(4)) / (1.0 + _LG3),
        rel_tol=1e-9,
    )

    assert metrics["section"]["hit_at_k"] == 1.0
    assert metrics["section"]["recall_at_k"] == 1.0
    assert metrics["section"]["mrr"] == 0.5  # 首个命中不匹配章节


def test_retrieval_metrics_negative_case_has_no_expected_units() -> None:
    metrics = ev.retrieval_metrics(
        [_hit("无关文档.md", "其他")], [], [], k=5
    )

    for level in ("doc", "section"):
        assert all(value is None for value in metrics[level].values())


# --------------------------------------------------------------------------- #
# 关键词覆盖与样本构造
# --------------------------------------------------------------------------- #
def test_keyword_coverage_requires_all_and_one_of_any() -> None:
    contexts = ["单笔订单实付满 199 元包邮，偏远地区加收运费。"]

    assert ev.keyword_coverage(contexts, ["满199"], ["包邮", "运费"]) == 1.0
    assert ev.keyword_coverage(contexts, ["满199"], ["退换", "发票"]) == 0.0
    assert ev.keyword_coverage(contexts, ["满99"], ["包邮"]) == 0.0
    assert ev.keyword_coverage(contexts, [], []) is None
    assert ev.keyword_coverage([], [], ["包邮"]) == 0.0


def test_build_eval_sample_drops_empty_contexts() -> None:
    sample = ev.build_eval_sample(
        "满多少包邮？", "满 199 元包邮。", ["  ", "满 199 元包邮。", ""], "199 元"
    )

    assert sample == {
        "user_input": "满多少包邮？",
        "response": "满 199 元包邮。",
        "retrieved_contexts": ["满 199 元包邮。"],
        "reference": "199 元",
    }
    assert ev.build_eval_sample("q", "", [], None)["retrieved_contexts"] == []


def test_evaluation_module_does_not_import_ragas() -> None:
    assert "ragas" not in sys.modules


# --------------------------------------------------------------------------- #
# RAGAS 分数合并
# --------------------------------------------------------------------------- #
def test_merge_ragas_scores_handles_none_nan_missing_and_extra_rows() -> None:
    records = [
        {"generation_metrics": {}},
        {},
        {"generation_metrics": {"faithfulness": 0.25}},
        {},
    ]
    rows = [
        {"faithfulness": 0.8, "answer_relevancy": 0.6},
        {"faithfulness": None, "answer_relevancy": float("nan")},
        {"faithfulness": 0.4},  # 缺 answer_relevancy 列
        {"faithfulness": 0.99},
        {"faithfulness": 0.11},  # 多余行应被忽略
    ]

    ev.merge_ragas_scores(
        records, rows, ["faithfulness", "answer_relevancy"]
    )

    assert records[0]["generation_metrics"] == {
        "faithfulness": 0.8,
        "answer_relevancy": 0.6,
    }
    assert records[1]["generation_metrics"] == {
        "faithfulness": None,
        "answer_relevancy": None,
    }
    assert records[2]["generation_metrics"] == {
        "faithfulness": 0.4,
        "answer_relevancy": None,
    }
    assert records[3]["generation_metrics"] == {
        "faithfulness": 0.99,
        "answer_relevancy": None,
    }


def test_merge_ragas_scores_initializes_none_generation_metrics() -> None:
    """retrieval-only 采集记录为 None 时，RAGAS 合并应自动初始化。"""
    records = [{"generation_metrics": None}]

    ev.merge_ragas_scores(records, [{"faithfulness": 0.8}], ["faithfulness"])

    assert records[0]["generation_metrics"] == {"faithfulness": 0.8}


def test_merge_ragas_scores_accepts_objects_and_numeric_strings() -> None:
    class Row:
        faithfulness = "0.75"
        answer_relevancy = "not-a-number"

    records = [{}]
    ev.merge_ragas_scores(records, [Row()], ["faithfulness", "answer_relevancy"])

    assert records[0]["generation_metrics"] == {
        "faithfulness": 0.75,
        "answer_relevancy": None,
    }


# --------------------------------------------------------------------------- #
# 聚合
# --------------------------------------------------------------------------- #
def _record(
    intent: str,
    tags: list[str],
    *,
    hit: float | None,
    keyword: float | None = None,
    faithfulness: float | None = None,
    answerable: bool = True,
) -> dict:
    return {
        "intent": intent,
        "tags": tags,
        "answerable": answerable,
        "retrieval": {
            "doc": {"hit_at_k": hit, "recall_at_k": hit, "mrr": hit, "ndcg_at_k": hit},
            "section": {
                "hit_at_k": hit,
                "recall_at_k": hit,
                "mrr": hit,
                "ndcg_at_k": hit,
            },
        },
        "keyword_coverage": keyword,
        "generation_metrics": {"faithfulness": faithfulness},
    }


def test_aggregate_means_ignore_unavailable_values() -> None:
    records = [
        _record("logistics", ["single-hop"], hit=1.0, keyword=1.0, faithfulness=0.9),
        _record("logistics", ["multi-hop"], hit=0.0, keyword=None, faithfulness=None),
        _record("size", [], hit=None, keyword=0.5, faithfulness=0.5, answerable=False),
    ]

    summary = ev.aggregate(records)

    assert summary["sample_count"] == 3
    assert summary["answerable_count"] == 2
    assert summary["retrieval"]["doc"]["hit_at_k"] == 0.5
    assert summary["retrieval"]["keyword_coverage"] == 0.75
    assert summary["generation"]["faithfulness"] == 0.7
    assert summary["valid_counts"]["retrieval.doc.hit_at_k"] == 2
    assert summary["valid_counts"]["retrieval.keyword_coverage"] == 2
    assert summary["valid_counts"]["generation.faithfulness"] == 2

    assert set(summary["by_intent"]) == {"logistics", "size"}
    assert summary["by_intent"]["logistics"]["retrieval"]["doc"]["hit_at_k"] == 0.5
    assert summary["by_tag"]["single-hop"]["sample_count"] == 1
    assert summary["by_tag"]["multi-hop"]["sample_count"] == 1


def test_aggregate_handles_empty_and_missing_retrieval_block() -> None:
    summary = ev.aggregate([])

    assert summary["sample_count"] == 0
    assert summary["retrieval"]["doc"]["hit_at_k"] is None
    assert summary["by_intent"] == {}

    partial = ev.aggregate([{"intent": "unknown", "tags": None}])
    assert partial["retrieval"]["section"]["mrr"] is None
    assert partial["generation"] == {}
    assert partial["by_intent"]["unknown"]["sample_count"] == 1


def test_aggregate_tolerates_none_generation_metrics() -> None:
    """检索-only 运行的 generation_metrics 为 None，不应中断聚合。"""
    records = [
        _record("logistics", ["single-hop"], hit=1.0, keyword=1.0, faithfulness=None),
        {
            "intent": "returns",
            "tags": [],
            "answerable": True,
            "retrieval": None,
            "keyword_coverage": None,
            "generation_metrics": None,
        },
    ]

    summary = ev.aggregate(records)

    assert summary["sample_count"] == 2
    assert summary["generation"]["faithfulness"] is None
    assert summary["valid_counts"]["generation.faithfulness"] == 0
    assert summary["by_intent"]["returns"]["generation"] == {}
