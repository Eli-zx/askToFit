"""切块质量测试：边界、重叠、元数据、去重与短块合并。"""

import pytest

from app.services.parsers import ParsedBlock
from app.services.text_splitter import TextSplitter, split_sentences


def make_long_text(sentences: int = 40) -> str:
    return "".join(
        f"第{i}条规定：购买服装后可在七天内申请无理由退换，商品需保持完好。"
        for i in range(1, sentences + 1)
    )


def test_split_sentences_keeps_chinese_punctuation() -> None:
    sentences = split_sentences("支持货到付款吗？可以。满减怎么算！")
    assert sentences == ["支持货到付款吗？", "可以。", "满减怎么算！"]


def test_chunks_do_not_exceed_hard_limit() -> None:
    splitter = TextSplitter(chunk_size=120, chunk_overlap=24, min_size=40)
    chunks = splitter.split([ParsedBlock(text=make_long_text(), title="退换货规则")])
    assert len(chunks) > 3
    assert all(len(chunk.content) <= 120 * 1.5 for chunk in chunks)
    assert all(chunk.content.strip() for chunk in chunks)


def test_adjacent_chunks_have_sentence_overlap() -> None:
    splitter = TextSplitter(chunk_size=120, chunk_overlap=30, min_size=40)
    chunks = splitter.split([ParsedBlock(text=make_long_text(), title="退换货规则")])
    first_tail = chunks[0].content[-20:]
    assert any(first_tail[-10:] in later.content for later in chunks[1:])


def test_short_document_stays_in_single_chunk() -> None:
    splitter = TextSplitter(chunk_size=500, chunk_overlap=80)
    chunks = splitter.split([ParsedBlock(text="支持七天无理由退换货。", title="售后")])
    assert len(chunks) == 1
    assert chunks[0].content == "支持七天无理由退换货。"


def test_metadata_is_preserved_per_group() -> None:
    splitter = TextSplitter(chunk_size=100, chunk_overlap=20, min_size=20)
    blocks = [
        ParsedBlock(text="第一页内容。" * 20, title="物流说明", page=1),
        ParsedBlock(text="第二页内容。" * 20, title="物流说明", page=2),
    ]
    chunks = splitter.split(blocks)
    assert {chunk.page for chunk in chunks} == {1, 2}
    assert all(chunk.title == "物流说明" for chunk in chunks)


def test_sections_are_kept_separate() -> None:
    splitter = TextSplitter(chunk_size=200, chunk_overlap=20, min_size=20)
    blocks = [
        ParsedBlock(text="发票申请需在订单完成后三十天内提交。", section="发票申请"),
        ParsedBlock(text="电子发票将在三个工作日内开具。", section="开票时效"),
    ]
    chunks = splitter.split(blocks)
    assert {chunk.section for chunk in chunks} == {"发票申请", "开票时效"}


def test_duplicate_chunks_are_removed() -> None:
    splitter = TextSplitter(chunk_size=200, chunk_overlap=20, min_size=20)
    blocks = [
        ParsedBlock(text="支持的支付方式包括微信、支付宝和银行卡。", page=1),
        ParsedBlock(text="支持的支付方式包括微信、支付宝和银行卡。", page=2),
    ]
    chunks = splitter.split(blocks)
    assert len(chunks) == 1


def test_short_last_chunk_merges_backwards() -> None:
    splitter = TextSplitter(chunk_size=120, chunk_overlap=20, min_size=60)
    text = "甲" * 100 + "。" + "乙" * 10 + "。"
    chunks = splitter.split([ParsedBlock(text=text)])
    assert len(chunks) == 1


def test_chunk_indexes_are_sequential() -> None:
    splitter = TextSplitter(chunk_size=100, chunk_overlap=20, min_size=20)
    chunks = splitter.split([ParsedBlock(text=make_long_text(20))])
    assert [chunk.index for chunk in chunks] == list(range(len(chunks)))


def test_overlap_must_be_smaller_than_chunk_size() -> None:
    with pytest.raises(ValueError):
        TextSplitter(chunk_size=100, chunk_overlap=100)


def test_hard_split_prefers_punctuation() -> None:
    splitter = TextSplitter(chunk_size=50, chunk_overlap=10, min_size=10)
    text = "这是一个很长的句子，" * 10
    chunks = splitter.split([ParsedBlock(text=text)])
    assert all(chunk.content.endswith(("，", "。")) for chunk in chunks)


# --------------------------------------------------------------------------- #
# 父子分块
# --------------------------------------------------------------------------- #
def test_split_with_parents_maps_children_to_parents() -> None:
    splitter = TextSplitter(
        chunk_size=120, chunk_overlap=20, min_size=20, parent_size=300
    )
    blocks = [ParsedBlock(text=make_long_text(30), title="退换货规则", section="售后")]
    parents, children = splitter.split_with_parents(blocks)

    assert parents
    assert children
    assert all(child.parent_index < len(parents) for child in children)
    for parent in parents:
        related = [child for child in children if child.parent_index == parent.index]
        assert related
        for child in related:
            assert child.content in parent.content


def test_parent_chunks_respect_target_size() -> None:
    splitter = TextSplitter(
        chunk_size=100, chunk_overlap=20, min_size=20, parent_size=260
    )
    parents, children = splitter.split_with_parents(
        [ParsedBlock(text=make_long_text(40), title="物流说明")]
    )
    assert len(parents) > 1
    assert all(len(parent.content) <= 260 * 1.5 for parent in parents)
    assert {child.parent_index for child in children} == set(range(len(parents)))


def test_duplicate_parent_sections_are_kept_once() -> None:
    splitter = TextSplitter(chunk_size=200, chunk_overlap=20, min_size=20)
    text = "支持的支付方式包括微信、支付宝和银行卡，订单支付后不支持修改支付方式。"
    blocks = [
        ParsedBlock(text=text, page=1),
        ParsedBlock(text=text, page=2),
    ]
    parents, children = splitter.split_with_parents(blocks)
    assert len(parents) == 1
    assert len(children) == 1


def test_punctuation_only_parent_duplicate_is_removed() -> None:
    splitter = TextSplitter(chunk_size=200, chunk_overlap=20, min_size=20)
    blocks = [
        ParsedBlock(text="支持的支付方式包括微信、支付宝和银行卡。", page=1),
        ParsedBlock(text="支持的支付方式包括微信！支付宝？和银行卡。", page=2),
    ]
    parents, children = splitter.split_with_parents(blocks)
    assert len(parents) == 1
    assert len(children) == 1


def test_parent_children_keep_metadata() -> None:
    splitter = TextSplitter(
        chunk_size=100, chunk_overlap=20, min_size=20, parent_size=200
    )
    parents, children = splitter.split_with_parents(
        [ParsedBlock(text=make_long_text(10), title="发票规则", section="开票", page=3)]
    )
    assert all(parent.title == "发票规则" for parent in parents)
    assert all(parent.section == "开票" and parent.page == 3 for parent in parents)
    assert all(child.page == 3 for child in children)
