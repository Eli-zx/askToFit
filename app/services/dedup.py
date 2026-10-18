"""文本归一化、文档指纹与近重复判定。

判重目标：
1. 只改标点、空白、Markdown/HTML 标记时，归一化哈希保持一致，直接跳过；
2. 不同文件名但正文高度相似时，识别为同一逻辑文档的更新；
3. 分块与检索结果按归一化内容和 SimHash 去重，避免近似资料重复出现。
"""

from __future__ import annotations

import hashlib
import re
import unicodedata
from collections import Counter
from dataclasses import dataclass
from difflib import SequenceMatcher
from typing import Any, Callable, Iterable

_MARKDOWN_LINK = re.compile(r"!?\[([^\]]*)\]\([^)]*\)")
_HTML_TAG = re.compile(r"<[^>]+>")
_URL = re.compile(r"(?:https?://|www\.)\S+", re.IGNORECASE)
_ZERO_WIDTH = re.compile(r"[​-‏‪-‮﻿]")

_HASH_CHARS = 400_000
_SIMHASH_CHARS = 800_000
_SEQUENCE_CHARS = 50_000


@dataclass(frozen=True)
class Fingerprint:
    """归一化正文指纹。"""

    content_hash: str
    simhash: str
    length: int


def normalize_text(text: str | None) -> str:
    """去掉格式、空白和标点，只保留可比较的正文内容。

    数字、中文、英文和单位中的数字都会保留；全角字符先做 NFKC 归一。
    """
    if not text:
        return ""
    value = unicodedata.normalize("NFKC", text)
    value = _ZERO_WIDTH.sub("", value)
    value = _MARKDOWN_LINK.sub(r"\1", value)
    value = _HTML_TAG.sub(" ", value)
    value = _URL.sub(" ", value)
    value = value.casefold()
    return "".join(char for char in value if char.isalnum())


def content_hash(text: str | None) -> str:
    """归一化正文的 SHA-256，用于完全重复的快速判断。"""
    normalized = normalize_text(text)
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


def _hash64(token: str) -> int:
    digest = hashlib.blake2b(token.encode("utf-8"), digest_size=8).digest()
    return int.from_bytes(digest, "big")


def simhash(text: str | None, bits: int = 64) -> int:
    """基于字符 4-gram 的 SimHash，忽略标点与空白。"""
    normalized = normalize_text(text)[:_SIMHASH_CHARS]
    if not normalized:
        return 0
    size = 4 if len(normalized) >= 4 else 1
    tokens = (
        normalized[index : index + size]
        for index in range(max(1, len(normalized) - size + 1))
    )
    weights = Counter(tokens)
    vector = [0] * bits
    for token, weight in weights.items():
        hashed = _hash64(token)
        for bit in range(bits):
            if hashed & (1 << bit):
                vector[bit] += weight
            else:
                vector[bit] -= weight
    result = 0
    for bit, value in enumerate(vector):
        if value >= 0:
            result |= 1 << bit
    return result


def simhash_hex(text: str | None, bits: int = 64) -> str:
    return f"{simhash(text, bits=bits):0{bits // 4}x}"


def parse_simhash(value: str | None) -> int:
    if not value:
        return 0
    try:
        return int(value, 16)
    except (TypeError, ValueError):
        return 0


def simhash_distance(left: str | int | None, right: str | int | None) -> int:
    left_value = parse_simhash(left) if isinstance(left, str) else int(left or 0)
    right_value = parse_simhash(right) if isinstance(right, str) else int(right or 0)
    return (left_value ^ right_value).bit_count()


def simhash_similarity(left: str | int | None, right: str | int | None) -> float:
    return max(0.0, 1.0 - simhash_distance(left, right) / 64.0)


def _shingles(normalized: str, size: int = 4) -> set[str]:
    value = normalized[:_SEQUENCE_CHARS]
    if not value:
        return set()
    if len(value) <= size:
        return {value}
    return {value[index : index + size] for index in range(len(value) - size + 1)}


def jaccard_similarity(left: str, right: str, size: int = 4) -> float:
    left_norm = normalize_text(left)
    right_norm = normalize_text(right)
    if not left_norm or not right_norm:
        return 0.0
    if left_norm == right_norm:
        return 1.0
    left_set = _shingles(left_norm, size)
    right_set = _shingles(right_norm, size)
    if not left_set or not right_set:
        return 0.0
    return len(left_set & right_set) / len(left_set | right_set)


def text_similarity(
    left: str | None,
    right: str | None,
    *,
    left_simhash: str | None = None,
    right_simhash: str | None = None,
) -> float:
    """估计两段正文的相似度，优先使用序列匹配，超长文本退化为 SimHash。"""
    left_norm = normalize_text(left)
    right_norm = normalize_text(right)
    if not left_norm or not right_norm:
        return 0.0
    if left_norm == right_norm:
        return 1.0

    shorter, longer = sorted((len(left_norm), len(right_norm)))
    if shorter / max(1, longer) < 0.35:
        return 0.0

    if len(left_norm) + len(right_norm) > _SEQUENCE_CHARS * 2:
        return simhash_similarity(
            left_simhash or simhash_hex(left_norm),
            right_simhash or simhash_hex(right_norm),
        )

    left_value = left_norm[:_SEQUENCE_CHARS]
    right_value = right_norm[:_SEQUENCE_CHARS]
    # autojunk 会把中文高频字当作噪声剔除，导致长文档相似度严重偏低
    return SequenceMatcher(None, left_value, right_value, autojunk=False).ratio()


def fingerprint_text(text: str | None) -> Fingerprint:
    normalized = normalize_text(text)
    return Fingerprint(
        content_hash=hashlib.sha256(normalized.encode("utf-8")).hexdigest(),
        simhash=f"{simhash(normalized):016x}",
        length=len(normalized),
    )


def is_same_content(left: str | None, right: str | None) -> bool:
    return bool(normalize_text(left)) and content_hash(left) == content_hash(right)


def deduplicate_items(
    items: Iterable[Any],
    *,
    text_getter: Callable[[Any], str | None],
    threshold: float = 0.95,
    max_simhash_distance: int = 4,
) -> list[Any]:
    """按归一化内容去重，保留迭代顺序中靠前的条目。"""
    kept: list[Any] = []
    kept_hashes: set[str] = set()
    kept_simhashes: list[tuple[int, Any]] = []

    for item in items:
        text = text_getter(item) or ""
        normalized_hash = content_hash(text)
        if not normalize_text(text):
            kept.append(item)
            continue
        if normalized_hash in kept_hashes:
            continue

        current_simhash = simhash(text)
        duplicate = False
        for candidate_simhash, candidate in kept_simhashes:
            if (current_simhash ^ candidate_simhash).bit_count() > max_simhash_distance:
                continue
            if text_similarity(
                text,
                text_getter(candidate),
                left_simhash=f"{current_simhash:016x}",
                right_simhash=f"{candidate_simhash:016x}",
            ) >= threshold:
                duplicate = True
                break
        if duplicate:
            continue

        kept.append(item)
        kept_hashes.add(normalized_hash)
        kept_simhashes.append((current_simhash, item))
    return kept
