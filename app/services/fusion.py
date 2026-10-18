"""多路召回融合：Reciprocal Rank Fusion（RRF）。

跨检索模式（hybrid WeightedRanker 分与 dense cosine 分）以及 BM25 与向量
之间的分数量纲不可直接比较，统一按“名次”融合，避免某一路分数偏大后
压过其他路。融合后 `score` 为 RRF 分，原始向量分保存在 `vector_score`，
BM25 分保存在 `bm25_score`，供重排序与知识缺口统计各取所需。
"""

from __future__ import annotations

from typing import Any

from config import Config


def _uid(hit: dict[str, Any], id_key: str) -> str:
    return str(hit.get(id_key) or "")


def _score(hit: dict[str, Any]) -> float:
    return float(hit.get("score", 0.0))


def _dedupe_list(hits: list[dict[str, Any]], id_key: str) -> list[dict[str, Any]]:
    """同一路结果内按分数降序、UID 升序稳定排序，重复 UID 只保留最高分。"""
    ordered = sorted(hits, key=lambda hit: (-_score(hit), _uid(hit, id_key)))
    seen: set[str] = set()
    result: list[dict[str, Any]] = []
    for hit in ordered:
        uid = _uid(hit, id_key)
        if not uid or uid in seen:
            continue
        seen.add(uid)
        result.append(hit)
    return result


def _source_of(hit: dict[str, Any]) -> str:
    source = hit.get("retrieval_source")
    if source in {"vector", "bm25", "fused"}:
        return str(source)
    return "bm25" if hit.get("bm25_score") is not None else "vector"


def rrf_fuse(
    lists: list[list[dict[str, Any]]],
    *,
    k: int | None = None,
    weights: list[float] | None = None,
    id_key: str = "chunk_uid",
) -> list[dict[str, Any]]:
    """按 RRF 融合多路命中。

    - ``lists``：每路一个命中列表，路内会先确定性排序并去重；
    - ``weights``：每路权重，缺省全为 1.0；
    - 单路输入不计算 RRF，原样返回（保持旧行为）。

    BM25-only 命中的 ``vector_score`` 明确为 0.0，避免被误当成向量分。
    """
    cleaned = [_dedupe_list(hits, id_key) for hits in lists if hits]
    cleaned = [hits for hits in cleaned if hits]
    if not cleaned:
        return []
    if len(cleaned) == 1:
        return [dict(hit) for hit in cleaned[0]]

    rrf_k = Config.rrf_k if k is None else int(k)
    if rrf_k <= 0:
        rrf_k = Config.rrf_k
    if weights is None:
        route_weights = [1.0] * len(cleaned)
    else:
        if len(weights) != len(cleaned):
            raise ValueError("RRF 权重数量必须与命中列表数量一致")
        route_weights = [float(weight) for weight in weights]

    merged: dict[str, dict[str, Any]] = {}
    for hits, weight in zip(cleaned, route_weights, strict=True):
        for rank, hit in enumerate(hits, start=1):
            uid = _uid(hit, id_key)
            source = _source_of(hit)
            entry = merged.get(uid)
            if entry is None:
                entry = {
                    "hit": dict(hit),
                    "base_source": source,
                    "fused_score": 0.0,
                    "sources": [],
                    "vector_score": 0.0,
                    "bm25_score": None,
                    "bm25_coverage": None,
                }
                merged[uid] = entry
            elif source == "vector" and entry["base_source"] == "bm25":
                # 向量命中的字段更完整，优先作为基础记录
                entry["hit"] = dict(hit)
                entry["base_source"] = source

            entry["fused_score"] += weight / (rrf_k + rank)
            if source not in entry["sources"]:
                entry["sources"].append(source)

            if source == "vector":
                vector_score = float(hit.get("vector_score", hit.get("score", 0.0)))
                entry["vector_score"] = max(entry["vector_score"], vector_score)
            if hit.get("bm25_score") is not None:
                bm25_score = float(hit["bm25_score"])
                entry["bm25_score"] = (
                    bm25_score
                    if entry["bm25_score"] is None
                    else max(float(entry["bm25_score"]), bm25_score)
                )
            if hit.get("bm25_coverage") is not None:
                coverage = float(hit["bm25_coverage"])
                entry["bm25_coverage"] = (
                    coverage
                    if entry["bm25_coverage"] is None
                    else max(float(entry["bm25_coverage"]), coverage)
                )

    fused: list[dict[str, Any]] = []
    for uid, entry in merged.items():
        item = dict(entry["hit"])
        item["chunk_uid"] = item.get("chunk_uid") or uid
        item["score"] = float(entry["fused_score"])
        item["fused_score"] = float(entry["fused_score"])
        item["vector_score"] = float(entry["vector_score"])
        if entry["bm25_score"] is not None:
            item["bm25_score"] = float(entry["bm25_score"])
        if entry["bm25_coverage"] is not None:
            item["bm25_coverage"] = float(entry["bm25_coverage"])
        item["retrieval_source"] = "fused"
        item["retrieval_sources"] = list(entry["sources"])
        fused.append(item)

    fused.sort(key=lambda item: (-float(item["score"]), str(item.get("chunk_uid") or "")))
    return fused
