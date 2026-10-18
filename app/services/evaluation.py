"""RAG 评估的本地纯函数：检索指标、样本构造与结果聚合。

生成指标由官方 RAGAS 计算；本模块不 import ragas，保证单测与 CI 的
``--validate-dataset`` 不依赖 overlay 环境。
"""

from __future__ import annotations

import math
import unicodedata
from typing import Any, Iterable

RETRIEVAL_METRICS = ("hit_at_k", "recall_at_k", "mrr", "ndcg_at_k")
LEVELS = ("doc", "section")


def normalize_unit(text: str | None) -> str:
    """NFKC + 小写 + 去空白/标点，用于文档名与章节片段匹配。"""
    normalized = unicodedata.normalize("NFKC", text or "").lower()
    return "".join(char for char in normalized if char.isalnum())


def _contains(hit_value: str | None, expected: str | None) -> bool:
    left = normalize_unit(hit_value)
    right = normalize_unit(expected)
    if not left or not right:
        return False
    return left in right or right in left


def match_doc_name(hit: dict[str, Any], expected: str) -> bool:
    return _contains(hit.get("doc_name"), expected)


def match_section(hit: dict[str, Any], expected: str) -> bool:
    return _contains(hit.get("section"), expected)


def _doc_units(expected_doc_names: Iterable[str]) -> set[str]:
    return {normalize_unit(name) for name in expected_doc_names if normalize_unit(name)}


def _section_units(
    expected_doc_names: Iterable[str], expected_sections: Iterable[str]
) -> set[str]:
    """章节级期望单元。

    数据集分别维护文档与章节列表，无法可靠还原一一对应关系；若做笛卡尔积，
    多文档问题会虚增分母。因此章节本身作为单元，文档约束在 ``hit_units``
    中校验：命中的章节必须同时落在期望文档上。
    """
    return {
        normalize_unit(section)
        for section in expected_sections
        if normalize_unit(section)
    }


def topic_units(
    expected_doc_names: Iterable[str],
    expected_sections: Iterable[str],
    level: str,
) -> set[str]:
    if level == "doc":
        return _doc_units(expected_doc_names)
    if level == "section":
        return _section_units(expected_doc_names, expected_sections)
    raise ValueError(f"未知的相关性层级: {level}")


def hit_units(
    hit: dict[str, Any],
    expected_doc_names: Iterable[str],
    expected_sections: Iterable[str],
    level: str,
) -> set[str]:
    """返回该命中覆盖到的期望单元；同一单元多个 chunk 会在统计时去重。"""
    doc_names = [name for name in expected_doc_names if normalize_unit(name)]
    if level == "doc":
        return {
            normalize_unit(name)
            for name in doc_names
            if match_doc_name(hit, name)
        }
    sections = [section for section in expected_sections if normalize_unit(section)]
    if not sections:
        return set()
    matched_sections = {
        normalize_unit(section)
        for section in sections
        if match_section(hit, section)
    }
    if not matched_sections:
        return set()
    if doc_names and not any(match_doc_name(hit, name) for name in doc_names):
        # 有文档约束时，章节命中必须同时落在期望文档上
        return set()
    return matched_sections


def _ranked_units(
    hits: list[dict[str, Any]],
    expected_doc_names: Iterable[str],
    expected_sections: Iterable[str],
    level: str,
    k: int,
) -> list[set[str]]:
    return [
        hit_units(hit, expected_doc_names, expected_sections, level)
        for hit in hits[: max(0, k)]
    ]


def hit_at_k(ranked: list[set[str]], expected: set[str], k: int) -> float | None:
    if not expected or k <= 0:
        return None
    return 1.0 if any(unit in expected for units in ranked[:k] for unit in units) else 0.0


def recall_at_k(ranked: list[set[str]], expected: set[str], k: int) -> float | None:
    if not expected or k <= 0:
        return None
    covered: set[str] = set()
    for units in ranked[:k]:
        covered.update(units & expected)
    return len(covered) / len(expected)


def mrr(ranked: list[set[str]], expected: set[str]) -> float | None:
    if not expected:
        return None
    for rank, units in enumerate(ranked, start=1):
        if units & expected:
            return 1.0 / rank
    return 0.0


def ndcg_at_k(ranked: list[set[str]], expected: set[str], k: int) -> float | None:
    if not expected or k <= 0:
        return None
    dcg = 0.0
    covered: set[str] = set()
    for index, units in enumerate(ranked[:k]):
        new_units = (units & expected) - covered
        if not new_units:
            continue
        covered.update(new_units)
        # 二元增益：同一排名位置命中多个新单元仍按一次相关计
        dcg += 1.0 / math.log2(index + 2)
    ideal_hits = min(len(expected), k)
    idcg = sum(1.0 / math.log2(index + 2) for index in range(ideal_hits))
    return dcg / idcg if idcg > 0 else None


def retrieval_metrics(
    hits: list[dict[str, Any]],
    expected_doc_names: Iterable[str],
    expected_sections: Iterable[str],
    k: int,
) -> dict[str, dict[str, float | None]]:
    """分别计算 doc 级与 section 级 Hit/Recall/MRR/NDCG。"""
    result: dict[str, dict[str, float | None]] = {}
    for level in LEVELS:
        expected = topic_units(expected_doc_names, expected_sections, level)
        ranked = _ranked_units(hits, expected_doc_names, expected_sections, level, k)
        result[level] = {
            "hit_at_k": hit_at_k(ranked, expected, k),
            "recall_at_k": recall_at_k(ranked, expected, k),
            "mrr": mrr(ranked, expected),
            "ndcg_at_k": ndcg_at_k(ranked, expected, k),
        }
    return result


def keyword_coverage(
    contexts: Iterable[str],
    keywords_all: Iterable[str],
    keywords_any: Iterable[str],
) -> float | None:
    """全部命中 ``keywords_all`` 且 ``keywords_any`` 至少一项时记 1.0。"""
    required = [keyword for keyword in keywords_all if keyword]
    optional = [keyword for keyword in keywords_any if keyword]
    if not required and not optional:
        return None
    haystack = normalize_unit("\n".join(contexts))
    if any(normalize_unit(keyword) not in haystack for keyword in required):
        return 0.0
    if optional and not any(
        normalize_unit(keyword) in haystack for keyword in optional
    ):
        return 0.0
    return 1.0


def build_eval_sample(
    question: str,
    answer: str,
    contexts: list[str],
    ground_truth: str,
) -> dict[str, Any]:
    """构造与 RAGAS ``SingleTurnSample`` 字段一一对应的纯 dict。"""
    return {
        "user_input": question,
        "response": answer,
        "retrieved_contexts": [
            context for context in contexts if context and context.strip()
        ],
        "reference": ground_truth,
    }


def _metric_value(row: Any, name: str) -> float | None:
    if hasattr(row, "get"):
        value = row.get(name)
    else:
        value = getattr(row, name, None)
    if value is None:
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return None if math.isnan(number) else number


def merge_ragas_scores(
    records: list[dict[str, Any]],
    ragas_rows: list[Any],
    metric_names: Iterable[str],
) -> None:
    """按样本顺序合并 RAGAS 逐题分数；缺行/NaN 保持 None，不记 0。"""
    names = list(metric_names)
    for index, record in enumerate(records):
        metrics = record.get("generation_metrics")
        if not isinstance(metrics, dict):
            metrics = {}
            record["generation_metrics"] = metrics
        for name in names:
            metrics.setdefault(name, None)
        if index >= len(ragas_rows):
            continue
        row = ragas_rows[index]
        for name in names:
            metrics[name] = _metric_value(row, name)


def _mean(values: list[float]) -> float | None:
    return sum(values) / len(values) if values else None


def _group_summary(records: list[dict[str, Any]]) -> dict[str, Any]:
    retrieval: dict[str, dict[str, float | None]] = {}
    valid_counts: dict[str, int] = {}
    for level in LEVELS:
        retrieval[level] = {}
        for metric in RETRIEVAL_METRICS:
            values = [
                float(value)
                for record in records
                if (
                    value := ((record.get("retrieval") or {}).get(level) or {}).get(
                        metric
                    )
                )
                is not None
            ]
            retrieval[level][metric] = _mean(values)
            valid_counts[f"retrieval.{level}.{metric}"] = len(values)

    keyword_values = [
        float(record["keyword_coverage"])
        for record in records
        if record.get("keyword_coverage") is not None
    ]
    retrieval["keyword_coverage"] = _mean(keyword_values)
    valid_counts["retrieval.keyword_coverage"] = len(keyword_values)

    generation: dict[str, float | None] = {}
    generation_names = sorted(
        {
            name
            for record in records
            for name in (record.get("generation_metrics") or {})
        }
    )
    for name in generation_names:
        values = [
            float(record["generation_metrics"][name])
            for record in records
            if (record.get("generation_metrics") or {}).get(name) is not None
        ]
        generation[name] = _mean(values)
        valid_counts[f"generation.{name}"] = len(values)

    return {
        "sample_count": len(records),
        "answerable_count": sum(
            1 for record in records if record.get("answerable", True)
        ),
        "retrieval": retrieval,
        "generation": generation,
        "valid_counts": valid_counts,
    }


def aggregate(records: list[dict[str, Any]]) -> dict[str, Any]:
    """总体 + 按 intent / tags 分组统计；空组不输出。"""
    summary = _group_summary(records)

    by_intent: dict[str, dict[str, Any]] = {}
    intents: dict[str, list[dict[str, Any]]] = {}
    for record in records:
        intent = str(record.get("intent") or "unknown")
        intents.setdefault(intent, []).append(record)
    for intent, group in sorted(intents.items()):
        by_intent[intent] = _group_summary(group)

    by_tag: dict[str, dict[str, Any]] = {}
    tags: dict[str, list[dict[str, Any]]] = {}
    for record in records:
        for tag in record.get("tags") or []:
            tags.setdefault(str(tag), []).append(record)
    for tag, group in sorted(tags.items()):
        by_tag[tag] = _group_summary(group)

    summary["by_intent"] = by_intent
    summary["by_tag"] = by_tag
    return summary
