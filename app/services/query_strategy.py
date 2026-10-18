"""多策略检索规划：规则优先，LLM 只负责生成扩展查询文本。

策略优先级为 ``subquery > backtrack > hyde > direct``。规则命中后只调用
一次 LLM 生成子查询/回溯问题/假设答案；LLM 超时、异常或输出无效时回退
direct，不影响正常问答。可选的 LLM 策略选择器默认关闭。
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import re
import time
from dataclasses import asdict, dataclass
from typing import Any

from app.core.logger import get_logger
from app.services.cache import PREFIX_STRATEGY, get_json, set_json
from app.services.llm import llm_service
from app.services.metrics import inc, observe
from app.services.query_prompts import (
    BACKTRACK_SYSTEM,
    BACKTRACK_USER,
    HYDE_SYSTEM,
    HYDE_USER,
    STRATEGY_SELECT_SYSTEM,
    STRATEGY_SELECT_USER,
    SUBQUERY_SYSTEM,
    SUBQUERY_USER,
)
from config import Config

logger = get_logger(__name__)

STRATEGY_DIRECT = "direct"
STRATEGY_HYDE = "hyde"
STRATEGY_SUBQUERY = "subquery"
STRATEGY_BACKTRACK = "backtrack"

_STRATEGIES = {
    STRATEGY_DIRECT,
    STRATEGY_HYDE,
    STRATEGY_SUBQUERY,
    STRATEGY_BACKTRACK,
}

_MULTI_FACTOR_CUES = (
    "并且",
    "以及",
    "分别",
    "同时",
    "还有",
    "另外",
    "对比",
    "区别",
    "哪个更",
)
_CONDITION_CUES = (
    "如果",
    "这种情况",
    "怎么办",
    "能不能",
    "是否可以",
    "遇到",
)
_ABSTRACT_CUES = (
    "为什么",
    "怎么选",
    "如何选择",
    "怎么判断",
    "适合",
    "推荐",
    "介绍",
)

_SELECTOR_ALIASES: dict[str, str] = {
    "direct": STRATEGY_DIRECT,
    "直接": STRATEGY_DIRECT,
    "直接检索": STRATEGY_DIRECT,
    "hyde": STRATEGY_HYDE,
    "假设答案": STRATEGY_HYDE,
    "假设性回答": STRATEGY_HYDE,
    "subquery": STRATEGY_SUBQUERY,
    "子查询": STRATEGY_SUBQUERY,
    "拆分子问题": STRATEGY_SUBQUERY,
    "backtrack": STRATEGY_BACKTRACK,
    "回溯": STRATEGY_BACKTRACK,
    "问题回溯": STRATEGY_BACKTRACK,
}

_LIST_PREFIX = re.compile(r"^\s*(?:\d+\s*[.、)）]|[-*•])\s*")
_QUOTES = " \t\r\n\"'“”‘’「」『』"


@dataclass(frozen=True)
class StrategyPlan:
    strategy: str
    queries: tuple[str, ...]
    rerank_query: str
    source: str


def _rule_strategy(query: str) -> str:
    """按线索优先级选择策略；短实体/金额问题保持 direct。"""
    length = len(query)
    has_multi = any(cue in query for cue in _MULTI_FACTOR_CUES)
    has_multi = has_multi or query.count("?") + query.count("？") >= 2
    if has_multi:
        return STRATEGY_SUBQUERY
    if length >= 24 and any(cue in query for cue in _CONDITION_CUES):
        return STRATEGY_BACKTRACK
    if length >= 12 and any(cue in query for cue in _ABSTRACT_CUES):
        return STRATEGY_HYDE
    return STRATEGY_DIRECT


def _clean_line(line: str) -> str:
    text = _LIST_PREFIX.sub("", line or "").strip()
    return text.strip(_QUOTES)


def _truncate(text: str, limit: int) -> str:
    return text[: max(1, int(limit))].strip()


def _config_version() -> dict[str, Any]:
    return {
        "strategy_enabled": Config.strategy_enabled,
        "selector_llm_enabled": Config.strategy_selector_llm_enabled,
        "selector_model": Config.strategy_selector_model,
        "timeout": Config.strategy_timeout_seconds,
        "max_subqueries": Config.strategy_max_subqueries,
        "max_query_chars": Config.strategy_max_query_chars,
        "hyde_max_chars": Config.strategy_hyde_max_chars,
    }


def _cache_key(query: str, intent: str) -> str:
    payload = json.dumps(
        {"query": query, "intent": intent, "config": _config_version()},
        ensure_ascii=False,
        sort_keys=True,
        default=str,
    )
    return PREFIX_STRATEGY + hashlib.sha256(payload.encode("utf-8")).hexdigest()


async def _complete(model_name: str, messages: list[dict[str, str]], max_tokens: int) -> str:
    timeout = max(0.1, float(Config.strategy_timeout_seconds))
    return await asyncio.wait_for(
        llm_service.complete(
            model_name,
            messages,
            temperature=0.3,
            top_p=0.9,
            max_tokens=max_tokens,
            timeout=timeout,
        ),
        timeout=timeout,
    )


async def select_strategy(query: str, model_name: str) -> str | None:
    """可选的 LLM 策略选择器；无法识别的输出按无效处理。"""
    try:
        raw = await _complete(
            model_name,
            [
                {"role": "system", "content": STRATEGY_SELECT_SYSTEM},
                {
                    "role": "user",
                    "content": STRATEGY_SELECT_USER.format(question=query),
                },
            ],
            16,
        )
    except asyncio.CancelledError:
        raise
    except Exception:
        inc("asktofit_strategy_generation_total", {"kind": "select", "outcome": "error"})
        logger.warning(
            "LLM 策略选择失败，回退规则结果（app/services/query_strategy.py:select_strategy）",
            exc_info=True,
        )
        return None
    label = _clean_line(raw).lower().strip("。.!！")
    selected = _SELECTOR_ALIASES.get(label)
    inc(
        "asktofit_strategy_generation_total",
        {"kind": "select", "outcome": selected or "invalid"},
    )
    return selected


async def generate_hyde(query: str, model_name: str) -> str | None:
    try:
        raw = await _complete(
            model_name,
            [
                {"role": "system", "content": HYDE_SYSTEM},
                {"role": "user", "content": HYDE_USER.format(question=query)},
            ],
            180,
        )
    except asyncio.CancelledError:
        raise
    except Exception:
        inc("asktofit_strategy_generation_total", {"kind": "hyde", "outcome": "error"})
        logger.warning(
            "HyDE 生成失败（app/services/query_strategy.py:generate_hyde）",
            exc_info=True,
        )
        return None
    text = _truncate(_clean_line(raw.replace("\n", " ")), Config.strategy_hyde_max_chars)
    if len(text) < 8:
        inc("asktofit_strategy_generation_total", {"kind": "hyde", "outcome": "empty"})
        return None
    inc("asktofit_strategy_generation_total", {"kind": "hyde", "outcome": "ok"})
    return text


async def generate_subqueries(query: str, model_name: str) -> list[str]:
    try:
        raw = await _complete(
            model_name,
            [
                {
                    "role": "system",
                    "content": SUBQUERY_SYSTEM.format(
                        max_items=max(1, int(Config.strategy_max_subqueries))
                    ),
                },
                {"role": "user", "content": SUBQUERY_USER.format(question=query)},
            ],
            220,
        )
    except asyncio.CancelledError:
        raise
    except Exception:
        inc(
            "asktofit_strategy_generation_total",
            {"kind": "subquery", "outcome": "error"},
        )
        logger.warning(
            "子查询生成失败（app/services/query_strategy.py:generate_subqueries）",
            exc_info=True,
        )
        return []

    seen = {query}
    result: list[str] = []
    for line in raw.splitlines():
        text = _truncate(_clean_line(line), Config.strategy_max_query_chars)
        if not text or text in seen:
            continue
        seen.add(text)
        result.append(text)
        if len(result) >= max(1, int(Config.strategy_max_subqueries)):
            break
    inc(
        "asktofit_strategy_generation_total",
        {"kind": "subquery", "outcome": "ok" if result else "empty"},
    )
    return result


async def generate_backtrack(query: str, model_name: str) -> str | None:
    try:
        raw = await _complete(
            model_name,
            [
                {"role": "system", "content": BACKTRACK_SYSTEM},
                {"role": "user", "content": BACKTRACK_USER.format(question=query)},
            ],
            120,
        )
    except asyncio.CancelledError:
        raise
    except Exception:
        inc(
            "asktofit_strategy_generation_total",
            {"kind": "backtrack", "outcome": "error"},
        )
        logger.warning(
            "回溯问题生成失败（app/services/query_strategy.py:generate_backtrack）",
            exc_info=True,
        )
        return None
    text = _truncate(_clean_line(raw.replace("\n", " ")), Config.strategy_max_query_chars)
    if len(text) < 4 or text == query:
        inc(
            "asktofit_strategy_generation_total",
            {"kind": "backtrack", "outcome": "empty"},
        )
        return None
    inc("asktofit_strategy_generation_total", {"kind": "backtrack", "outcome": "ok"})
    return text


def _direct_plan(query: str, source: str) -> StrategyPlan:
    return StrategyPlan(
        strategy=STRATEGY_DIRECT,
        queries=(query,),
        rerank_query=query,
        source=source,
    )


class QueryStrategyService:
    """生成检索计划；任何生成失败都不阻断问答。"""

    async def plan(
        self,
        query: str,
        *,
        intent: str,
        model_name: str,
        redis: Any | None = None,
        has_knowledge: bool = True,
    ) -> StrategyPlan:
        started = time.perf_counter()
        text = (query or "").strip()
        if (
            not text
            or intent == "greeting"
            or not Config.strategy_enabled
            or not has_knowledge
            or not model_name
        ):
            plan = _direct_plan(text, "disabled")
            self._record(plan, started, outcome="disabled")
            return plan

        cache_key = _cache_key(text, intent)
        cached = await get_json(redis, cache_key, "strategy")
        if isinstance(cached, dict):
            try:
                strategy = str(cached["strategy"])
                queries = tuple(str(item) for item in cached["queries"])
                plan = StrategyPlan(
                    strategy=strategy,
                    queries=queries or (text,),
                    rerank_query=str(cached.get("rerank_query") or text),
                    source=str(cached.get("source") or "rule"),
                )
            except (KeyError, TypeError):
                logger.warning("策略缓存格式异常，已忽略 key=%s", cache_key)
            else:
                if strategy in _STRATEGIES:
                    self._record(plan, started, outcome="cached")
                    return plan

        strategy = _rule_strategy(text)
        source = "rule"
        if (
            strategy == STRATEGY_DIRECT
            and Config.strategy_selector_llm_enabled
            and len(text) >= 32
        ):
            selector_model = Config.strategy_selector_model or model_name
            selected = await select_strategy(text, selector_model)
            if selected and selected != STRATEGY_DIRECT:
                strategy = selected
                source = "llm"

        plan = await self._build(text, strategy, source, model_name)
        await set_json(
            redis,
            cache_key,
            asdict(plan),
            max(1, int(Config.strategy_cache_ttl)),
            "strategy",
        )
        self._record(
            plan,
            started,
            outcome="fallback" if plan.source == "fallback" else "ok",
        )
        return plan

    async def _build(
        self, query: str, strategy: str, source: str, model_name: str
    ) -> StrategyPlan:
        if strategy == STRATEGY_SUBQUERY:
            subqueries = await generate_subqueries(query, model_name)
            if not subqueries:
                return _direct_plan(query, "fallback")
            return StrategyPlan(
                strategy=STRATEGY_SUBQUERY,
                queries=(query, *subqueries),
                rerank_query=query,
                source=source,
            )
        if strategy == STRATEGY_BACKTRACK:
            backtrack = await generate_backtrack(query, model_name)
            if not backtrack:
                return _direct_plan(query, "fallback")
            return StrategyPlan(
                strategy=STRATEGY_BACKTRACK,
                queries=(query, backtrack),
                rerank_query=query,
                source=source,
            )
        if strategy == STRATEGY_HYDE:
            hypothetical = await generate_hyde(query, model_name)
            if not hypothetical:
                return _direct_plan(query, "fallback")
            return StrategyPlan(
                strategy=STRATEGY_HYDE,
                queries=(query, hypothetical),
                rerank_query=query,
                source=source,
            )
        return _direct_plan(query, source)

    @staticmethod
    def _record(plan: StrategyPlan, started: float, *, outcome: str) -> None:
        inc(
            "asktofit_retrieval_strategy_total",
            {
                "strategy": plan.strategy,
                "source": plan.source,
                "outcome": outcome,
            },
        )
        observe(
            "asktofit_retrieval_strategy_duration_seconds",
            time.perf_counter() - started,
            {"strategy": plan.strategy},
        )


query_strategy_service = QueryStrategyService()
