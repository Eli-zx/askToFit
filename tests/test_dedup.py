"""文本归一化、指纹与近重复判定测试。"""

from app.services.dedup import (
    content_hash,
    deduplicate_items,
    fingerprint_text,
    is_same_content,
    jaccard_similarity,
    normalize_text,
    simhash_distance,
    simhash_similarity,
    text_similarity,
)


def make_policy_text(paragraphs: int = 12) -> str:
    return "\n".join(
        f"第{i}条：自签收之日起七天内，商品保持完好可申请无理由退换货。"
        f"退换货产生的运费按照责任方承担，质量问题由商家承担。"
        for i in range(1, paragraphs + 1)
    )


def test_normalize_ignores_punctuation_whitespace_and_markup() -> None:
    left = "# 退换货规则\n\n1. 支持**七天**无理由退换货。"
    right = "退换货规则 1 支持七天无理由退换货！"
    assert normalize_text(left) == normalize_text(right)
    assert is_same_content(left, right)


def test_punctuation_only_change_has_identical_hash() -> None:
    left = make_policy_text()
    right = left.replace("。", "！").replace("，", ",").replace("\n", "\n\n")
    assert content_hash(left) == content_hash(right)
    assert fingerprint_text(left).content_hash == fingerprint_text(right).content_hash


def test_markdown_link_and_url_do_not_affect_fingerprint() -> None:
    left = "查看[售后说明](https://example.com/a)，或访问 https://example.com/b 了解详情。"
    right = "查看售后说明，或访问 了解详情。"
    assert content_hash(left) == content_hash(right)


def test_one_paragraph_change_is_near_duplicate() -> None:
    base = make_policy_text()
    changed = make_policy_text().replace(
        "第1条：自签收之日起七天内，商品保持完好可申请无理由退换货。",
        "第1条：会员积分可在结算时抵扣现金，每100积分抵扣1元。",
    )
    base_fp = fingerprint_text(base)
    changed_fp = fingerprint_text(changed)

    assert simhash_distance(base_fp.simhash, changed_fp.simhash) <= 10
    assert (
        text_similarity(
            base,
            changed,
            left_simhash=base_fp.simhash,
            right_simhash=changed_fp.simhash,
        )
        >= 0.90
    )


def test_three_paragraph_changes_are_not_same_document() -> None:
    base = make_policy_text()
    changed = make_policy_text()
    for index, replacement in enumerate(
        (
            "第1条：会员积分可在结算时抵扣现金，每100积分抵扣1元，单笔订单最高抵扣订单金额的20%。",
            "第2条：生鲜类商品因保质期特殊，签收后24小时内可申请售后，逾期不再受理。",
            "第3条：定制服装一经裁剪制作，不支持无理由退换，质量问题除外。",
        ),
        start=1,
    ):
        changed = changed.replace(
            f"第{index}条：自签收之日起七天内，商品保持完好可申请无理由退换货。"
            f"退换货产生的运费按照责任方承担，质量问题由商家承担。",
            replacement,
        )
    base_fp = fingerprint_text(base)
    changed_fp = fingerprint_text(changed)
    similarity = text_similarity(
        base,
        changed,
        left_simhash=base_fp.simhash,
        right_simhash=changed_fp.simhash,
    )
    assert similarity < 0.90


def test_unrelated_documents_have_low_similarity() -> None:
    left = make_policy_text()
    right = "会员等级分为普通会员、银卡会员和金卡会员，生日当月可领取专属优惠券。"
    assert text_similarity(left, right) < 0.2
    assert simhash_similarity(fingerprint_text(left).simhash, fingerprint_text(right).simhash) < 0.75


def test_short_and_long_text_ratio_is_guarded() -> None:
    assert text_similarity("七天无理由退换", make_policy_text()) == 0.0


def test_jaccard_detects_near_duplicate() -> None:
    base = make_policy_text()
    changed = base.replace("商品保持完好", "商品保持完好无损")
    assert jaccard_similarity(base, changed) > 0.9


def test_deduplicate_items_keeps_first_and_skips_near_duplicates() -> None:
    base = make_policy_text()
    near = base.replace("第1条：", "第一条：").replace("。", "！")
    different = "会员积分可在结算时抵扣现金，每100积分抵扣1元。"

    items = [
        {"id": 1, "text": base},
        {"id": 2, "text": near},
        {"id": 3, "text": different},
    ]
    kept = deduplicate_items(items, text_getter=lambda item: item["text"], threshold=0.95)
    assert [item["id"] for item in kept] == [1, 3]


def test_normalize_keeps_digits_and_fullwidth_conversion() -> None:
    assert normalize_text("满 １９９ 元包邮") == "满199元包邮"
    assert normalize_text("订单金额 ¥199.00") == "订单金额19900"
