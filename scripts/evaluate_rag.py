"""AskToFit RAG 评估脚本。

检索指标由本地纯函数计算；生成指标由官方 RAGAS 0.3.0 在独立 overlay 中计算：

    uv run --with-requirements scripts/eval_requirements.txt \
      python scripts/evaluate_rag.py --limit 10

不安装 overlay 时仍可运行检索评估（``--retrieval-only``），不会把生成指标记 0。
"""

from __future__ import annotations

import argparse
import asyncio
import csv
import hashlib
import inspect
import json
import math
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from sqlalchemy import select, text  # noqa: E402

from app.core.logger import get_logger  # noqa: E402
from app.db.session import (  # noqa: E402
    dispose_engine,
    ensure_database,
    ensure_schema_upgrades,
    get_session_maker,
    init_models,
)
from app.models.chunk import Chunk  # noqa: E402
from app.models.document import Document  # noqa: E402
from app.models.knowledge_base import KnowledgeBase  # noqa: E402
from app.models.user import User  # noqa: E402
from app.services import evaluation as ev  # noqa: E402
from app.services.chat import _filter_qualified  # noqa: E402
from app.services.evaluation_store import save_eval_run  # noqa: E402
from app.services.llm import LLMError, llm_service  # noqa: E402
from app.services.query_strategy import (  # noqa: E402
    StrategyPlan,
    generate_backtrack,
    generate_hyde,
    generate_subqueries,
    query_strategy_service,
)
from app.services.rag import build_llm_messages  # noqa: E402
from app.services.retrieval import retrieval_service  # noqa: E402
from config import Config, get_api_model, get_model_settings  # noqa: E402

logger = get_logger(__name__)

DEFAULT_DATASET = PROJECT_ROOT / "data" / "evaluation" / "qa_eval.jsonl"
DEFAULT_KB_NAME = "服装售前知识库（2026 春夏）"
DEFAULT_JUDGE_METRICS = (
    "faithfulness",
    "answer_relevancy",
    "context_precision",
    "context_recall",
)
_STRATEGIES = ("auto", "direct", "hyde", "subquery", "backtrack")
_REQUIRED_FIELDS = (
    "id",
    "question",
    "ground_truth",
    "expected_doc_names",
    "expected_sections",
    "expected_keywords_all",
    "expected_keywords_any",
    "answerable",
    "intent",
    "tags",
)
_CACHE_FILE = ".eval_cache.json"


def _site() -> str:
    """返回调用方的文件:行号，保证日志可定位。"""
    frame = inspect.currentframe()
    caller = frame.f_back if frame else None
    if caller is None:
        return Path(__file__).name
    return f"{Path(caller.f_code.co_filename).name}:{caller.f_lineno}"


# --------------------------------------------------------------------------- #
# 数据集加载与校验
# --------------------------------------------------------------------------- #
def load_dataset(path: Path) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    with path.open("r", encoding="utf-8") as handle:
        for line_no, raw in enumerate(handle, start=1):
            stripped = raw.strip()
            if not stripped:
                continue
            try:
                payload = json.loads(stripped)
            except json.JSONDecodeError as exc:
                raise ValueError(f"第 {line_no} 行 JSON 解析失败：{exc}") from exc
            if not isinstance(payload, dict):
                raise ValueError(f"第 {line_no} 行必须是 JSON 对象")
            payload["_line"] = line_no
            records.append(payload)
    return records


def _is_str_list(value: Any) -> bool:
    return isinstance(value, list) and all(
        isinstance(item, str) and item.strip() for item in value
    )


def validate_dataset(records: Iterable[dict[str, Any]]) -> list[str]:
    """纯文件校验：不依赖数据库、ragas 或任何外部服务。"""
    errors: list[str] = []
    seen_ids: set[str] = set()
    for index, record in enumerate(records, start=1):
        label = str(record.get("id") or f"第 {index} 条")
        for field in _REQUIRED_FIELDS:
            if field not in record:
                errors.append(f"{label} 缺少字段 {field}")
        if errors and errors[-1].startswith(f"{label} 缺少字段"):
            continue

        case_id = record.get("id")
        if not isinstance(case_id, str) or not case_id.strip():
            errors.append(f"{label} 的 id 必须是非空字符串")
        elif case_id in seen_ids:
            errors.append(f"{label} 的 id 重复")
        else:
            seen_ids.add(case_id)

        for field in ("question", "ground_truth", "intent"):
            if not isinstance(record.get(field), str) or not record[field].strip():
                errors.append(f"{label} 的 {field} 必须是非空字符串")

        if not isinstance(record.get("answerable"), bool):
            errors.append(f"{label} 的 answerable 必须是布尔值")

        for field in (
            "expected_doc_names",
            "expected_sections",
            "expected_keywords_all",
            "expected_keywords_any",
            "tags",
        ):
            if not _is_str_list(record.get(field)):
                errors.append(f"{label} 的 {field} 必须是字符串数组")

        if not isinstance(record.get("expected_doc_names"), list):
            continue
        answerable = record.get("answerable") is True
        if not answerable and record["expected_doc_names"]:
            errors.append(f"{label} 不可回答时 expected_doc_names 必须为空")
        if answerable and not any(
            record.get(field)
            for field in (
                "expected_doc_names",
                "expected_sections",
                "expected_keywords_all",
                "expected_keywords_any",
            )
        ):
            errors.append(f"{label} 可回答时至少需要文档、章节或关键词之一")
    return errors


async def validate_against_kb(
    records: list[dict[str, Any]], kb: KnowledgeBase
) -> list[str]:
    """连接知识库校验文档名、章节与关键词是否真实存在。"""
    errors: list[str] = []
    async with get_session_maker()() as db:
        doc_rows = (
            await db.scalars(
                select(Document).where(
                    Document.kb_id == kb.id, Document.is_current.is_(True)
                )
            )
        ).all()
        chunk_rows = (
            await db.scalars(select(Chunk).where(Chunk.kb_id == kb.id))
        ).all()

    doc_names = [doc.original_name for doc in doc_rows]
    sections = [chunk.section for chunk in chunk_rows if chunk.section]
    corpus = ev.normalize_unit(
        "\n".join(chunk.content for chunk in chunk_rows)
    )

    for record in records:
        label = record["id"]
        for expected in record["expected_doc_names"]:
            if not any(
                ev.match_doc_name({"doc_name": name}, expected) for name in doc_names
            ):
                errors.append(f"{label} 期望文档不存在：{expected}")
        if record["expected_sections"]:
            for expected in record["expected_sections"]:
                if not any(
                    ev.match_section({"section": section}, expected)
                    for section in sections
                ):
                    errors.append(f"{label} 期望章节不存在：{expected}")
        for keyword in record["expected_keywords_all"]:
            if ev.normalize_unit(keyword) not in corpus:
                errors.append(f"{label} 关键词未在知识库出现：{keyword}")
        optional = [
            keyword
            for keyword in record["expected_keywords_any"]
            if ev.normalize_unit(keyword) in corpus
        ]
        if record["expected_keywords_any"] and not optional:
            errors.append(
                f"{label} expected_keywords_any 均未在知识库出现："
                f"{record['expected_keywords_any']}"
            )
    return errors


# --------------------------------------------------------------------------- #
# 检索与答案采集
# --------------------------------------------------------------------------- #
async def resolve_kb(kb_id: int | None, kb_name: str | None) -> KnowledgeBase:
    async with get_session_maker()() as db:
        if kb_id is not None:
            kb = await db.get(KnowledgeBase, int(kb_id))
        else:
            kb = await db.scalar(
                select(KnowledgeBase)
                .where(KnowledgeBase.name == (kb_name or DEFAULT_KB_NAME))
                .order_by(KnowledgeBase.id.asc())
            )
    if kb is None:
        target = f"id={kb_id}" if kb_id is not None else f"name={kb_name}"
        raise RuntimeError(f"找不到知识库：{target}（{_site()}）")
    return kb


async def force_plan(
    question: str, intent: str, model_name: str, forced: str
) -> StrategyPlan:
    if forced == "auto":
        return await query_strategy_service.plan(
            question, intent=intent, model_name=model_name, has_knowledge=True
        )
    if forced == "direct":
        return StrategyPlan("direct", (question,), question, "forced")
    if forced == "hyde":
        text = await generate_hyde(question, model_name)
        if text:
            return StrategyPlan("hyde", (question, text), question, "forced")
    elif forced == "subquery":
        children = await generate_subqueries(question, model_name)
        if children:
            return StrategyPlan(
                "subquery", (question, *children), question, "forced"
            )
    elif forced == "backtrack":
        text = await generate_backtrack(question, model_name)
        if text:
            return StrategyPlan(
                "backtrack", (question, text), question, "forced"
            )
    return StrategyPlan("direct", (question,), question, "fallback")


async def attach_doc_names(hits: list[dict[str, Any]]) -> list[dict[str, Any]]:
    doc_ids = {
        int(hit["doc_id"]) for hit in hits if hit.get("doc_id") is not None
    }
    if not doc_ids:
        return hits
    async with get_session_maker()() as db:
        rows = await db.execute(
            select(Document.id, Document.original_name).where(
                Document.id.in_(doc_ids)
            )
        )
        names = {int(row[0]): row[1] for row in rows}
    return [
        {**hit, "doc_name": names.get(int(hit.get("doc_id") or 0))}
        for hit in hits
    ]


def _compact_hit(hit: dict[str, Any]) -> dict[str, Any]:
    return {
        "doc_name": hit.get("doc_name"),
        "section": hit.get("section"),
        "score": hit.get("score"),
        "vector_score": hit.get("vector_score"),
        "rerank_score": hit.get("rerank_score"),
        "retrieval_source": hit.get("retrieval_source"),
    }


def _context_texts(hits: Iterable[dict[str, Any]]) -> list[str]:
    return [str(hit.get("content") or "") for hit in hits if hit.get("content")]


def _sample_key(question: str, contexts: list[str]) -> str:
    payload = json.dumps(
        {"q": question, "c": contexts}, ensure_ascii=False, sort_keys=True
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _load_cache(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        logger.warning("评估缓存读取失败，将忽略（%s）", _site())
        return {}


def _save_cache(path: Path, cache: dict[str, Any]) -> None:
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(cache, ensure_ascii=False, indent=2), encoding="utf-8"
        )
    except OSError:
        logger.warning("评估缓存写入失败（%s）", _site(), exc_info=True)


async def collect(
    records: list[dict[str, Any]],
    kb: KnowledgeBase,
    *,
    top_k: int,
    strategy: str,
    model_name: str,
    generate_answers: bool,
    answer_cache: dict[str, Any],
) -> list[dict[str, Any]]:
    kb_modes = {kb.embedding_mode: [int(kb.id)]}
    results: list[dict[str, Any]] = []
    for index, record in enumerate(records, start=1):
        question = record["question"]
        intent = record["intent"]
        item: dict[str, Any] = {
            **{key: record.get(key) for key in _REQUIRED_FIELDS},
            "retrieval": {},
            "retrieval_pre_filter": {},
            "answer": "",
            "contexts": [],
            "retrieved": [],
            "generation_metrics": None,
            "keyword_coverage": None,
            "error": None,
        }
        try:
            plan = await force_plan(question, intent, model_name, strategy)
            hits = await retrieval_service.retrieve(
                question,
                kb_modes,
                final_k=top_k,
                recall_k=Config.candidate_k,
                redis=None,
                strategy=plan.strategy,
                queries=plan.queries,
                rerank_query=plan.rerank_query,
            )
            hits = await retrieval_service.enrich_hits(hits)
            hits = await attach_doc_names(hits)
            qualified = _filter_qualified(hits)
            item["strategy_used"] = plan.strategy
            item["strategy_source"] = plan.source
            item["retrieval_pre_filter"] = ev.retrieval_metrics(
                hits,
                record["expected_doc_names"],
                record["expected_sections"],
                top_k,
            )
            item["retrieval"] = ev.retrieval_metrics(
                qualified,
                record["expected_doc_names"],
                record["expected_sections"],
                top_k,
            )
            item["retrieved"] = [_compact_hit(hit) for hit in qualified]
            contexts = _context_texts(qualified)
            item["contexts"] = contexts
            item["keyword_coverage"] = ev.keyword_coverage(
                contexts,
                record["expected_keywords_all"],
                record["expected_keywords_any"],
            )
            logger.info(
                "评估采集 %d/%d %s strategy=%s hits=%d qualified=%d（%s）",
                index,
                len(records),
                record["id"],
                plan.strategy,
                len(hits),
                len(qualified),
                _site(),
            )
        except Exception as exc:
            item["error"] = f"检索失败：{exc}"
            logger.exception("评估检索失败 %s（%s）", record["id"], _site())
            results.append(item)
            continue

        if generate_answers:
            cache_key = _sample_key(question, item["contexts"])
            cached = answer_cache.get(cache_key)
            if isinstance(cached, str):
                item["answer"] = cached
            else:
                try:
                    messages = build_llm_messages(question, qualified, [], intent)
                    item["answer"] = await llm_service.complete(
                        model_name,
                        messages,
                        temperature=0.2,
                        max_tokens=Config.max_tokens_default,
                    )
                    answer_cache[cache_key] = item["answer"]
                except LLMError as exc:
                    item["error"] = f"答案生成失败：{exc}"
                    logger.warning(
                        "答案生成失败 %s：%s（%s）", record["id"], exc, _site()
                    )
                except Exception as exc:
                    item["error"] = f"答案生成异常：{exc}"
                    logger.exception("答案生成异常 %s（%s）", record["id"], _site())
        results.append(item)
    return results


# --------------------------------------------------------------------------- #
# 官方 RAGAS（惰性导入）
# --------------------------------------------------------------------------- #
def _ragas_metric_specs() -> dict[str, tuple[str, Any]]:
    from ragas.metrics import (
        AnswerRelevancy,
        Faithfulness,
        LLMContextPrecisionWithReference,
        LLMContextRecall,
    )

    return {
        "faithfulness": ("faithfulness", Faithfulness),
        "answer_relevancy": ("answer_relevancy", AnswerRelevancy),
        "context_precision": (
            "llm_context_precision_with_reference",
            LLMContextPrecisionWithReference,
        ),
        "context_recall": ("context_recall", LLMContextRecall),
    }


def _run_ragas(
    records: list[dict[str, Any]],
    *,
    judge_model: str,
    metric_names: list[str],
    cache: dict[str, Any],
) -> tuple[str | None, dict[str, Any] | None, int]:
    """返回 (ragas 版本, 逐题分数缓存, 失败数)。"""
    try:
        from importlib.metadata import version

        ragas_version = version("ragas")
    except Exception:
        return None, None, 0

    try:
        from ragas import EvaluationDataset, SingleTurnSample, evaluate
        from ragas.embeddings import LangchainEmbeddingsWrapper
        from ragas.llms import LangchainLLMWrapper
        from langchain_core.embeddings import Embeddings
        from langchain_openai import ChatOpenAI
    except ImportError:
        logger.warning(
            "未安装官方 RAGAS，跳过生成指标；请使用 "
            "uv run --with-requirements scripts/eval_requirements.txt（%s）",
            _site(),
        )
        return ragas_version, None, 0

    base_url, api_key = get_model_settings(judge_model)
    if not api_key:
        logger.warning(
            "judge 模型 %s 未配置 API Key，生成指标标记为不可用（%s）",
            judge_model,
            _site(),
        )
        return ragas_version, None, 0

    from app.services.embedding import embedding_service

    class BgeEmbeddings(Embeddings):
        def embed_documents(self, texts: list[str]) -> list[list[float]]:
            dense, _ = asyncio.run(
                embedding_service.embed_documents(list(texts))
            )
            return dense

        def embed_query(self, text: str) -> list[float]:
            return self.embed_documents([text])[0]

    specs = _ragas_metric_specs()
    metric_objects = []
    column_to_key: dict[str, str] = {}
    for name in metric_names:
        key, factory = specs[name]
        metric = factory()
        column_to_key[key] = name
        column_to_key[name] = name
        metric_objects.append(metric)
    cache_prefix = f"{judge_model}|{ragas_version}|{','.join(metric_names)}"

    pending: list[dict[str, Any]] = []
    pending_indices: list[int] = []
    for index, record in enumerate(records):
        if record.get("error") or not record.get("answer"):
            continue
        sample = ev.build_eval_sample(
            record["question"],
            record["answer"],
            record.get("contexts") or [],
            record["ground_truth"],
        )
        sample_hash = hashlib.sha256(
            json.dumps(sample, ensure_ascii=False, sort_keys=True).encode("utf-8")
        ).hexdigest()
        cache_key = f"{cache_prefix}|{sample_hash}"
        cached = cache.get(cache_key)
        if (
            isinstance(cached, dict)
            and all(name in cached for name in metric_names)
        ):
            record["generation_metrics"] = {
                name: cached.get(name) for name in metric_names
            }
            continue
        record["_sample"] = sample
        record["_cache_key"] = cache_key
        pending.append(record)
        pending_indices.append(index)

    if not pending:
        return ragas_version, cache, 0

    dataset = EvaluationDataset(
        samples=[
            SingleTurnSample(
                user_input=record["_sample"]["user_input"],
                response=record["_sample"]["response"],
                retrieved_contexts=record["_sample"]["retrieved_contexts"],
                reference=record["_sample"]["reference"],
            )
            for record in pending
        ]
    )
    llm = LangchainLLMWrapper(
        ChatOpenAI(
            model=get_api_model(judge_model),
            api_key=api_key,
            base_url=base_url,
            temperature=0,
        )
    )
    embeddings = LangchainEmbeddingsWrapper(BgeEmbeddings())
    try:
        result = evaluate(
            dataset=dataset,
            metrics=metric_objects,
            llm=llm,
            embeddings=embeddings,
            raise_exceptions=False,
            show_progress=False,
        )
        rows = result.to_pandas().to_dict("records")
    except TypeError:
        # 兼容不接受 llm/embeddings 参数的旧版 evaluate
        for metric in metric_objects:
            if hasattr(metric, "llm"):
                metric.llm = llm
            if hasattr(metric, "embeddings"):
                metric.embeddings = embeddings
        result = evaluate(
            dataset=dataset,
            metrics=metric_objects,
            raise_exceptions=False,
            show_progress=False,
        )
        rows = result.to_pandas().to_dict("records")
    except Exception:
        logger.exception("RAGAS 评估失败（%s）", _site())
        return ragas_version, cache, len(pending)

    normalized_rows: list[dict[str, Any]] = []
    for row in rows:
        normalized_rows.append(
            {
                cli_name: row.get(column)
                for column, cli_name in column_to_key.items()
            }
        )
    ev.merge_ragas_scores(pending, normalized_rows, metric_names)

    failed = 0
    for record in pending:
        scores = record.get("generation_metrics") or {}
        if any(scores.get(name) is None for name in metric_names):
            failed += 1
        cache[record["_cache_key"]] = {
            name: scores.get(name) for name in metric_names
        }
        record.pop("_sample", None)
        record.pop("_cache_key", None)
    return ragas_version, cache, failed


# --------------------------------------------------------------------------- #
# 报告与持久化
# --------------------------------------------------------------------------- #
def _jsonable(value: Any) -> Any:
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, dict):
        return {str(key): _jsonable(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_jsonable(item) for item in value]
    if isinstance(value, float):
        return None if math.isnan(value) or math.isinf(value) else value
    if hasattr(value, "item"):
        try:
            return _jsonable(value.item())
        except Exception:
            return str(value)
    return value


def _config_snapshot() -> dict[str, Any]:
    return {
        "retrieval_k": Config.retrieval_k,
        "candidate_k": Config.candidate_k,
        "rerank_enabled": Config.rerank_enabled,
        "rerank_candidate_k": Config.rerank_candidate_k,
        "rerank_score_threshold": Config.rerank_score_threshold,
        "strategy_enabled": Config.strategy_enabled,
        "bm25_enabled": Config.bm25_enabled,
        "bm25_mode": Config.bm25_mode,
        "bm25_rrf_weight": Config.bm25_rrf_weight,
        "rrf_k": Config.rrf_k,
    }


def _format_metric(value: Any) -> str:
    if value is None:
        return "—"
    try:
        return f"{float(value):.4f}"
    except (TypeError, ValueError):
        return "—"


def write_reports(
    *,
    output_dir: Path,
    records: list[dict[str, Any]],
    aggregate: dict[str, Any],
    metadata: dict[str, Any],
) -> tuple[Path, Path, Path | None]:
    output_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    payload = _jsonable(
        {
            "metadata": metadata,
            "aggregate": aggregate,
            "cases": [
                {key: value for key, value in record.items() if not key.startswith("_")}
                for record in records
            ],
        }
    )
    json_path = output_dir / f"eval_{stamp}.json"
    json_path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    lines = [
        "# AskToFit RAG 评估报告",
        "",
        f"- 生成时间：{metadata['finished_at']}",
        f"- 数据集：{metadata['dataset_name']}（SHA-256 `{metadata['dataset_sha256'][:12]}…`）",
        f"- 知识库：{metadata['kb_name']}（id={metadata['kb_id']}）",
        f"- 策略：{metadata['strategy']} / TopK={metadata['top_k']}",
        f"- 样本：{aggregate['sample_count']}（可回答 {aggregate['answerable_count']}）",
        f"- 官方 RAGAS：{metadata.get('ragas_version') or '未运行'}",
        "",
        "## 检索指标",
        "",
        "| 层级 | Hit@K | Recall@K | MRR | NDCG@K |",
        "| --- | --- | --- | --- | --- |",
    ]
    for level in ("doc", "section"):
        row = aggregate["retrieval"].get(level, {})
        lines.append(
            f"| {level} | {_format_metric(row.get('hit_at_k'))} | "
            f"{_format_metric(row.get('recall_at_k'))} | "
            f"{_format_metric(row.get('mrr'))} | "
            f"{_format_metric(row.get('ndcg_at_k'))} |"
        )
    lines.extend(
        [
            "",
            f"- 关键词覆盖：{_format_metric(aggregate['retrieval'].get('keyword_coverage'))}",
            "",
            "## 生成指标（官方 RAGAS）",
            "",
        ]
    )
    generation = aggregate.get("generation") or {}
    if generation:
        lines.extend(["| 指标 | 均值 | 有效样本 |", "| --- | --- | --- |"])
        for name, value in generation.items():
            valid = aggregate["valid_counts"].get(f"generation.{name}", 0)
            lines.append(f"| {name} | {_format_metric(value)} | {valid} |")
    else:
        lines.append("未运行官方 RAGAS，生成指标标记为不可用。")
    lines.extend(
        [
            "",
            "## 失败项",
            "",
        ]
    )
    failures = [record for record in records if record.get("error")]
    if failures:
        for record in failures:
            lines.append(f"- `{record['id']}`：{record['error']}")
    else:
        lines.append("无。")
    md_path = output_dir / f"eval_{stamp}.md"
    md_path.write_text("\n".join(lines) + "\n", encoding="utf-8")

    ragas_path: Path | None = None
    judged = [
        record
        for record in records
        if isinstance(record.get("generation_metrics"), dict)
        and record["generation_metrics"]
    ]
    if judged:
        metric_names = sorted(
            {name for record in judged for name in record["generation_metrics"]}
        )
        ragas_path = output_dir / f"ragas_{stamp}.csv"
        with ragas_path.open("w", encoding="utf-8-sig", newline="") as handle:
            writer = csv.writer(handle)
            writer.writerow(
                [
                    "case_id",
                    "question",
                    "answer",
                    "ground_truth",
                    "contexts",
                    "judge_failed",
                    *metric_names,
                ]
            )
            for record in judged:
                scores = record["generation_metrics"]
                writer.writerow(
                    [
                        record.get("id") or "",
                        record.get("question") or "",
                        record.get("answer") or "",
                        record.get("ground_truth") or "",
                        len(record.get("contexts") or []),
                        int(any(scores.get(name) is None for name in metric_names)),
                        *[
                            "" if scores.get(name) is None else scores.get(name)
                            for name in metric_names
                        ],
                    ]
                )
    return json_path, md_path, ragas_path


async def persist(
    records: list[dict[str, Any]],
    *,
    aggregate: dict[str, Any],
    metadata: dict[str, Any],
    owner_username: str | None,
) -> int | None:
    owner_id: int | None = None
    async with get_session_maker()() as db:
        if owner_username:
            user = await db.scalar(
                select(User).where(User.username == owner_username.strip().lower())
            )
            owner_id = int(user.id) if user else None
        else:
            owner_id = await db.scalar(select(User.id).order_by(User.id.asc()).limit(1))
        owner_id = int(owner_id) if owner_id is not None else None

        errors = [record for record in records if record.get("error")]
        status = (
            "failed"
            if records and len(errors) == len(records)
            else "partial"
            if errors
            else "success"
        )
        cases = [
            {
                "case_id": record["id"],
                "question": record["question"],
                "ground_truth": record["ground_truth"],
                "answer": record.get("answer") or None,
                "intent": record.get("intent"),
                "tags": record.get("tags"),
                "expected_doc_names": record.get("expected_doc_names"),
                "expected_sections": record.get("expected_sections"),
                "retrieved": record.get("retrieved"),
                "doc_metrics": (record.get("retrieval") or {}).get("doc"),
                "section_metrics": (record.get("retrieval") or {}).get("section"),
                "generation_metrics": record.get("generation_metrics"),
                "keyword_coverage": record.get("keyword_coverage"),
                "error": record.get("error"),
            }
            for record in records
        ]
        run = {
            "dataset_name": metadata["dataset_name"],
            "dataset_sha256": metadata["dataset_sha256"],
            "kb_id": metadata["kb_id"],
            "kb_name": metadata["kb_name"],
            "strategy": metadata["strategy"],
            "top_k": metadata["top_k"],
            "judge_model": metadata.get("judge_model"),
            "ragas_version": metadata.get("ragas_version"),
            "sample_count": aggregate["sample_count"],
            "answerable_count": aggregate["answerable_count"],
            "retrieval_metrics": aggregate["retrieval"],
            "generation_metrics": aggregate.get("generation") or None,
            "config_snapshot": metadata.get("config_snapshot"),
            "status": status,
            "error": None,
            "started_at": metadata["started_at"],
            "finished_at": metadata["finished_at"],
        }
        return await save_eval_run(db, run, cases, owner_id)


# --------------------------------------------------------------------------- #
# 入口
# --------------------------------------------------------------------------- #
async def run(args: argparse.Namespace) -> int:
    dataset_path = Path(args.dataset)
    if not dataset_path.is_absolute():
        dataset_path = PROJECT_ROOT / dataset_path
    try:
        records = load_dataset(dataset_path)
    except (OSError, ValueError) as exc:
        print(f"数据集读取失败：{exc}")
        return 1
    errors = validate_dataset(records)
    if errors:
        print("数据集校验失败：")
        for error in errors:
            print(f"  - {error}")
        return 1
    if args.limit:
        records = records[: max(1, int(args.limit))]
    if args.validate_dataset:
        answerable = sum(1 for record in records if record["answerable"])
        print(
            f"数据集校验通过：{len(records)} 条"
            f"（可回答 {answerable}，不可回答 {len(records) - answerable}）"
        )
        return 0

    await asyncio.to_thread(ensure_database)
    await init_models()
    await ensure_schema_upgrades()

    async with get_session_maker()() as db:
        await db.execute(text("SELECT 1"))

    kb = await resolve_kb(args.kb_id, args.kb_name)
    kb_errors = await validate_against_kb(records, kb)
    if kb_errors:
        print("知识库一致性校验失败：")
        for error in kb_errors:
            print(f"  - {error}")
        return 1
    if args.validate_only:
        print(f"知识库校验通过：{len(records)} 条，文档与章节均存在")
        return 0

    started_at = datetime.now(timezone.utc).replace(tzinfo=None)
    output_dir = Path(args.output_dir)
    if not output_dir.is_absolute():
        output_dir = PROJECT_ROOT / output_dir
    cache_path = output_dir / _CACHE_FILE
    cache = _load_cache(cache_path)
    answer_cache: dict[str, Any] = cache.setdefault("answers", {})
    ragas_cache: dict[str, Any] = cache.setdefault("ragas", {})

    generate_answers = not (args.retrieval_only or args.no_llm)
    model_name = args.judge_model or "deepseekv4-flash"
    records = await collect(
        records,
        kb,
        top_k=args.top_k,
        strategy=args.strategy,
        model_name=model_name,
        generate_answers=generate_answers,
        answer_cache=answer_cache,
    )
    _save_cache(cache_path, cache)

    metric_names = [
        name.strip()
        for name in (args.judge_metrics or "").split(",")
        if name.strip()
    ]
    metric_names = list(dict.fromkeys(metric_names)) or list(DEFAULT_JUDGE_METRICS)
    unknown = [name for name in metric_names if name not in DEFAULT_JUDGE_METRICS]
    if unknown:
        print(f"未知评估指标：{', '.join(unknown)}")
        return 2

    ragas_version: str | None = None
    judge_failed = 0
    if generate_answers:
        ragas_version, updated, judge_failed = _run_ragas(
            records,
            judge_model=model_name,
            metric_names=metric_names,
            cache=ragas_cache,
        )
        if updated is not None:
            cache["ragas"] = updated
            _save_cache(cache_path, cache)
    else:
        for record in records:
            record["generation_metrics"] = None

    aggregate = ev.aggregate(records)
    finished_at = datetime.now(timezone.utc).replace(tzinfo=None)
    dataset_sha = hashlib.sha256(dataset_path.read_bytes()).hexdigest()
    metadata = {
        "dataset_name": dataset_path.name,
        "dataset_sha256": dataset_sha,
        "kb_id": int(kb.id),
        "kb_name": kb.name,
        "strategy": args.strategy,
        "top_k": args.top_k,
        "judge_model": model_name if generate_answers else None,
        "ragas_version": ragas_version if generate_answers else None,
        "started_at": started_at,
        "finished_at": finished_at,
        "config_snapshot": _config_snapshot(),
        "judge_failed": judge_failed,
    }
    json_path, md_path, ragas_path = write_reports(
        output_dir=output_dir,
        records=records,
        aggregate=aggregate,
        metadata=metadata,
    )

    run_id: int | None = None
    if args.persist:
        try:
            run_id = await persist(
                records,
                aggregate=aggregate,
                metadata=metadata,
                owner_username=args.owner_username,
            )
            logger.info("评估结果已写入看板 run_id=%s（%s）", run_id, _site())
        except Exception:
            logger.exception("评估结果写入数据库失败，报告已落盘（%s）", _site())

    print(f"评估完成：{len(records)} 条")
    print(f"  doc Hit@K={_format_metric(aggregate['retrieval']['doc']['hit_at_k'])} "
          f"section Recall@K="
          f"{_format_metric(aggregate['retrieval']['section']['recall_at_k'])}")
    if generate_answers and ragas_version:
        for name, value in (aggregate.get("generation") or {}).items():
            print(f"  RAGAS {name}={_format_metric(value)}（失败 {judge_failed} 条）")
    elif not generate_answers:
        print("  未生成答案与 RAGAS 指标（--retrieval-only）")
    print(f"  报告：{json_path}")
    print(f"        {md_path}")
    if ragas_path is not None:
        print(f"        {ragas_path}")
    if run_id is not None:
        print(f"  看板运行 ID：{run_id}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="AskToFit RAG 评估")
    parser.add_argument("--dataset", default=str(DEFAULT_DATASET))
    parser.add_argument("--kb-name", default=DEFAULT_KB_NAME)
    parser.add_argument("--kb-id", type=int, default=None)
    parser.add_argument("--top-k", type=int, default=10)
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument("--strategy", choices=_STRATEGIES, default="auto")
    parser.add_argument("--retrieval-only", action="store_true")
    parser.add_argument("--no-llm", action="store_true")
    parser.add_argument("--judge-model", default="deepseekv4-flash")
    parser.add_argument(
        "--judge-metrics", default=",".join(DEFAULT_JUDGE_METRICS)
    )
    parser.add_argument("--validate-dataset", action="store_true")
    parser.add_argument("--validate-only", action="store_true")
    parser.add_argument("--persist", dest="persist", action="store_true", default=True)
    parser.add_argument("--no-persist", dest="persist", action="store_false")
    parser.add_argument("--owner-username", default=None)
    parser.add_argument("--output-dir", default="reports")
    return parser


def main() -> int:
    args = build_parser().parse_args()

    async def runner() -> int:
        try:
            return await run(args)
        finally:
            await dispose_engine()

    return asyncio.run(runner())


if __name__ == "__main__":
    raise SystemExit(main())
