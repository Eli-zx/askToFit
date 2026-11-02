"""按查询词挑选相关句段：重排序与出处预览共用。

分块往往包含多个话题，直接截取开头会把真正相关的事实截掉。这里按
“中文双字片段 + 英文/数字整词”给每行打分，优先保留与查询重合度高的
问句/答句，并保持原文顺序输出。
"""

from __future__ import annotations

import re
import unicodedata
from typing import Any

_CJK_RE = re.compile(r"[一-鿿]+")
_ASCII_RE = re.compile(r"[a-z0-9]+")


def query_terms(query: str) -> set[str]:
    """提取检索词：中文双字片段 + 英文/数字整词。"""
    text = unicodedata.normalize("NFKC", query or "").lower()
    terms = {word for word in _ASCII_RE.findall(text) if len(word) >= 2}
    for chunk in _CJK_RE.findall(text):
        terms.update(chunk[index : index + 2] for index in range(len(chunk) - 1))
        if len(chunk) == 1:
            terms.add(chunk)
    return terms


def term_score(line: str, terms: set[str]) -> float:
    lowered = line.lower()
    return float(sum(1 for term in terms if term in lowered))


def excerpt_text(text: str, terms: set[str], budget: int) -> str:
    """按行选择与查询最相关的句子，避免简单截断丢掉块尾的关键事实。

    FAQ 类分块常把“问/答”分两行，因此同时评估“单行”和“相邻两行”两种
    候选单元，保证选中问题时能带上紧随其后的答案。
    """
    if len(text) <= budget:
        return text
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    if not lines:
        return text[:budget]

    units: list[tuple[float, int, int, str]] = []
    for index, line in enumerate(lines):
        score = term_score(line, terms)
        if score > 0 and line.endswith(("？", "?")):
            score += 1.0
        units.append((score, index, index, line))
        if index + 1 < len(lines):
            pair = f"{line}\n{lines[index + 1]}"
            pair_score = score + 0.5 * term_score(lines[index + 1], terms)
            units.append((pair_score, index, index + 1, pair))

    ranked = sorted(units, key=lambda item: (-item[0], -(item[2] - item[1]), item[1]))
    chosen: list[tuple[int, int, str]] = []
    used = 0
    for score, start, end, unit in ranked:
        if score <= 0:
            break
        if any(
            not (end < chosen_start or start > chosen_end)
            for chosen_start, chosen_end, _ in chosen
        ):
            continue
        extra = 1 if chosen else 0
        if used + extra + len(unit) > budget:
            continue
        chosen.append((start, end, unit))
        used += extra + len(unit)

    if not chosen:
        return text[:budget]
    chosen.sort(key=lambda item: item[0])
    return "\n".join(unit for _, _, unit in chosen)[:budget]


def build_rerank_passage(query: str, hit: dict[str, Any], budget: int) -> str:
    """标题与章节始终保留，正文按查询相关度选取等长窗口。"""
    terms = query_terms(query)
    title = (hit.get("title") or "").strip()
    section = (hit.get("section") or "").strip()
    header = "\n".join(part for part in (title, section) if part)
    content = (hit.get("content") or "").strip()
    if not header:
        return excerpt_text(content, terms, budget)
    available = max(50, budget - len(header) - 1)
    return f"{header}\n{excerpt_text(content, terms, available)}"


def preview_excerpt(text: str, query: str, budget: int) -> str:
    """出处预览用摘录：保留相关句，并用省略号标出被跳过的上下文。"""
    cleaned = (text or "").strip()
    if not cleaned:
        return ""
    if len(cleaned) <= budget:
        return cleaned

    terms = query_terms(query)
    excerpt = excerpt_text(cleaned, terms, budget).strip()
    if not excerpt:
        excerpt = cleaned[:budget].rstrip()
    prefix = "" if cleaned.startswith(excerpt) else "…"
    suffix = "" if excerpt and excerpt in cleaned and cleaned.endswith(excerpt) else "…"
    if excerpt.endswith("…"):
        suffix = ""
    return f"{prefix}{excerpt}{suffix}"
