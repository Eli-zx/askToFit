"""意图路由测试。"""

import pytest

from app.services.intent import classify_intent, intent_guidance, intent_label


@pytest.mark.parametrize(
    ("question", "expected"),
    [
        ("七天无理由退货需要吊牌吗？", "return_exchange"),
        ("开发票要多久？", "invoice"),
        ("什么时候发货，运费怎么算？", "logistics"),
        ("偏远地区包邮吗？", "logistics"),
        ("支持货到付款吗？", "payment"),
        ("退款什么时候到账？", "refund"),
        ("165cm 55kg 穿什么码？", "sizing"),
        ("会员积分怎么用？", "membership"),
        ("羊毛大衣怎么保养？", "product"),
        ("你好", "greeting"),
        ("今天天气怎么样", "other"),
    ],
)
def test_classify_intent(question: str, expected: str) -> None:
    assert classify_intent(question) == expected


def test_intent_metadata() -> None:
    assert intent_label("logistics") == "物流"
    assert "发货时效" in intent_guidance("logistics")
    assert intent_label("unknown") == "其他"
    assert intent_guidance("unknown")


def test_empty_question_is_other() -> None:
    assert classify_intent("   ") == "other"
