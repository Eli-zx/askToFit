"""中文友好的父子递归切块器。

切块策略：
1. 按标题 / 页码 / 章节将解析块分组，保留引用元数据；
2. 每组先聚合成 800–1200 字左右的父分块，保存完整章节上下文；
3. 父分块内部再以句子为最小单元切子分块，供向量检索；
4. 子分块保留句子级重叠，超长句子优先在标点处硬切；
5. 父子分块都做归一化内容哈希与近重复去重。
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from app.services.dedup import content_hash, simhash, text_similarity
from app.services.parsers import ParsedBlock

_SENTENCE_SPLIT = re.compile(r"(?<=[。！？!?；;])|(?<=\.)\s+|\n+")
_MEANINGFUL = re.compile(r"[一-鿿A-Za-z0-9]")
_BREAK_CHARS = "。！？；，、,.!?;: \n"


@dataclass
class ParentChunkData:
    content: str
    title: str | None
    section: str | None
    page: int | None
    index: int = 0
    content_hash: str = ""


@dataclass
class TextChunk:
    content: str
    title: str | None
    section: str | None
    page: int | None
    index: int = 0
    parent_index: int = 0
    content_hash: str = ""


def split_sentences(text: str) -> list[str]:
    return [part.strip() for part in _SENTENCE_SPLIT.split(text) if part and part.strip()]


def _meaningful_length(text: str) -> int:
    return len(_MEANINGFUL.findall(text))


class TextSplitter:
    def __init__(
        self,
        chunk_size: int = 500,
        chunk_overlap: int = 80,
        min_size: int = 80,
        parent_size: int | None = None,
        dedup_similarity: float = 0.97,
    ):
        if chunk_size < 50:
            raise ValueError("切块大小不能小于 50")
        if chunk_overlap < 0 or chunk_overlap >= chunk_size:
            raise ValueError("重叠大小必须大于等于 0 且小于切块大小")
        self.chunk_size = chunk_size
        self.chunk_overlap = chunk_overlap
        self.min_size = min(min_size, chunk_size)
        self.parent_size = max(parent_size or 1200, chunk_size * 2)
        self.dedup_similarity = max(0.0, min(1.0, dedup_similarity))

    # ------------------------------------------------------------------ #
    def split(self, blocks: list[ParsedBlock]) -> list[TextChunk]:
        """兼容旧调用：只返回子分块。"""
        _, children = self.split_with_parents(blocks)
        return children

    def split_with_parents(
        self, blocks: list[ParsedBlock]
    ) -> tuple[list[ParentChunkData], list[TextChunk]]:
        """返回父分块与子分块；子分块的 parent_index 指向父分块下标。"""
        parents: list[ParentChunkData] = []
        children: list[TextChunk] = []
        seen_parents: dict[str, int] = {}

        for group in self._group_blocks(blocks):
            units: list[str] = []
            for block in group:
                units.extend(split_sentences(block.text))
            if not units:
                continue
            head = group[0]

            for parent_text in self._pack_units(units, self.parent_size, 0):
                parent_text = parent_text.strip()
                if not parent_text:
                    continue
                parent_hash = content_hash(parent_text)
                if parent_hash in seen_parents:
                    # 同一章节内容重复出现时，只保留第一份父分块及其子分块
                    continue

                parent_index = len(parents)
                seen_parents[parent_hash] = parent_index
                parents.append(
                    ParentChunkData(
                        content=parent_text,
                        title=head.title,
                        section=head.section,
                        page=head.page,
                        index=parent_index,
                        content_hash=parent_hash,
                    )
                )

                child_units = split_sentences(parent_text) or [parent_text]
                packed = self._pack_units(
                    child_units, self.chunk_size, self.chunk_overlap
                )
                packed = self._merge_short(packed)
                for content in packed:
                    if self._is_valid(content):
                        children.append(
                            TextChunk(
                                content=content,
                                title=head.title,
                                section=head.section,
                                page=head.page,
                                parent_index=parent_index,
                                content_hash=content_hash(content),
                            )
                        )

        children = self._deduplicate(children)
        for index, chunk in enumerate(children):
            chunk.index = index
        return parents, children

    # ------------------------------------------------------------------ #
    @staticmethod
    def _group_blocks(blocks: list[ParsedBlock]) -> list[list[ParsedBlock]]:
        """连续且元数据相同的解析块分为一组，避免跨页 / 跨章节混切。"""
        groups: list[list[ParsedBlock]] = []
        for block in blocks:
            if not block.text.strip():
                continue
            if groups:
                previous = groups[-1][-1]
                same_meta = (
                    previous.title == block.title
                    and previous.section == block.section
                    and previous.page == block.page
                )
                if same_meta:
                    groups[-1].append(block)
                    continue
            groups.append([block])
        return groups

    # ------------------------------------------------------------------ #
    def _pack(self, units: list[str]) -> list[str]:
        return self._pack_units(units, self.chunk_size, self.chunk_overlap)

    def _pack_units(
        self, units: list[str], size: int, overlap: int
    ) -> list[str]:
        chunks: list[str] = []
        current = ""
        index = 0

        while index < len(units):
            unit = units[index]
            if not current:
                if len(unit) > size:
                    pieces = self._hard_split(unit, size, overlap)
                    chunks.extend(pieces[:-1])
                    current = pieces[-1]
                else:
                    current = unit
                index += 1
                continue

            candidate = f"{current}\n{unit}"
            if len(candidate) <= size:
                current = candidate
                index += 1
                continue

            chunks.append(current)
            tail = self._overlap_tail(current, overlap)
            current = f"{tail}\n{unit}".strip() if tail else unit
            if len(current) > size:
                pieces = self._hard_split(current, size, overlap)
                chunks.extend(pieces[:-1])
                current = pieces[-1]
            index += 1

        if current:
            chunks.append(current)
        return [chunk.strip() for chunk in chunks if chunk.strip()]

    def _overlap_tail(self, text: str, overlap: int | None = None) -> str:
        overlap = self.chunk_overlap if overlap is None else overlap
        if overlap <= 0:
            return ""
        sentences = split_sentences(text)
        tail: list[str] = []
        total = 0
        for sentence in reversed(sentences):
            if total + len(sentence) > overlap and tail:
                break
            if not tail and len(sentence) > overlap:
                return text[-overlap:]
            tail.insert(0, sentence)
            total += len(sentence)
            if total >= overlap:
                break
        return "\n".join(tail)

    def _hard_split(
        self,
        text: str,
        size: int | None = None,
        overlap: int | None = None,
    ) -> list[str]:
        size = self.chunk_size if size is None else size
        overlap = self.chunk_overlap if overlap is None else overlap
        pieces: list[str] = []
        start = 0
        length = len(text)
        while start < length:
            end = min(start + size, length)
            if end < length:
                window_start = max(start + int(size * 0.6), start + 1)
                window = text[window_start:end]
                best = -1
                for char in _BREAK_CHARS:
                    position = window.rfind(char)
                    if position > best:
                        best = position
                if best >= 0:
                    end = window_start + best + 1
            piece = text[start:end].strip()
            if piece:
                pieces.append(piece)
            if end >= length:
                break
            start = max(end - overlap, start + 1)
        return pieces or [text]

    def _merge_short(self, chunks: list[str]) -> list[str]:
        if len(chunks) <= 1:
            return chunks
        merged: list[str] = []
        for chunk in chunks:
            if merged and len(merged[-1]) < self.min_size:
                combined = f"{merged[-1]}\n{chunk}"
                if len(combined) <= self.chunk_size * 1.5:
                    merged[-1] = combined
                    continue
            merged.append(chunk)

        if len(merged) > 1 and len(merged[-1]) < self.min_size:
            combined = f"{merged[-2]}\n{merged[-1]}"
            if len(combined) <= self.chunk_size * 1.5:
                merged[-2] = combined
                merged.pop()

        if len(merged) > 1 and len(merged[0]) < self.min_size:
            combined = f"{merged[0]}\n{merged[1]}"
            if len(combined) <= self.chunk_size * 1.5:
                merged[1] = combined
                merged.pop(0)
        return merged

    @staticmethod
    def _is_valid(content: str) -> bool:
        return len(content.strip()) >= 5 and _meaningful_length(content) >= 3

    def _deduplicate(self, chunks: list[TextChunk]) -> list[TextChunk]:
        """先按归一化哈希去重，再按 SimHash + 文本相似度去除近重复块。"""
        kept: list[TextChunk] = []
        seen_hashes: set[str] = set()
        kept_simhashes: list[tuple[int, TextChunk]] = []
        max_distance = max(1, int(round(64 * (1 - self.dedup_similarity))))

        for chunk in chunks:
            normalized_hash = chunk.content_hash or content_hash(chunk.content)
            chunk.content_hash = normalized_hash
            if normalized_hash in seen_hashes:
                continue

            current_simhash = simhash(chunk.content)
            duplicate = False
            for candidate_simhash, candidate in kept_simhashes:
                if (
                    current_simhash ^ candidate_simhash
                ).bit_count() > max_distance:
                    continue
                if (
                    text_similarity(
                        chunk.content,
                        candidate.content,
                        left_simhash=f"{current_simhash:016x}",
                        right_simhash=f"{candidate_simhash:016x}",
                    )
                    >= self.dedup_similarity
                ):
                    duplicate = True
                    break
            if duplicate:
                continue

            kept.append(chunk)
            seen_hashes.add(normalized_hash)
            kept_simhashes.append((current_simhash, chunk))
        return kept
