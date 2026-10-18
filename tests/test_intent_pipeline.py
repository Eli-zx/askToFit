"""意图识别主链路测试：BERT 优先、关键词兜底与 Redis 缓存。"""

import json

from app.services import intent as intent_module
from app.services.intent import (
    IntentResult,
    _cache_key,
    classify_intent_detailed,
)
from app.services.intent_bert import intent_bert_classifier


def _bert_result(intent: str, confidence: float, margin: float) -> IntentResult:
    return IntentResult(
        intent=intent,
        confidence=confidence,
        margin=margin,
        source="bert",
        scores={intent: confidence},
    )


async def test_bert_high_confidence_is_used(monkeypatch) -> None:
    async def fake_predict(text):
        return _bert_result("invoice", 0.98, 0.9)

    monkeypatch.setattr(intent_bert_classifier, "predict", fake_predict)
    result = await classify_intent_detailed("这个能开票吗")

    assert result.intent == "invoice"
    assert result.source == "bert"
    assert result.confidence == 0.98


async def test_low_confidence_falls_back_to_keywords(monkeypatch) -> None:
    async def fake_predict(text):
        return _bert_result("logistics", 0.4, 0.3)

    monkeypatch.setattr(intent_bert_classifier, "predict", fake_predict)
    result = await classify_intent_detailed("退款什么时候到账")

    assert result.intent == "refund"
    assert result.source == "keyword"


async def test_small_margin_falls_back_to_keywords(monkeypatch) -> None:
    async def fake_predict(text):
        return _bert_result("logistics", 0.9, 0.01)

    monkeypatch.setattr(intent_bert_classifier, "predict", fake_predict)
    result = await classify_intent_detailed("会员积分怎么用")

    assert result.intent == "membership"
    assert result.source == "keyword"


async def test_bert_failure_degrades_to_keywords(monkeypatch) -> None:
    async def broken_predict(text):
        raise RuntimeError("model unavailable")

    monkeypatch.setattr(intent_bert_classifier, "predict", broken_predict)
    result = await classify_intent_detailed("开发票要多久")

    assert result.intent == "invoice"
    assert result.source == "keyword"


async def test_redis_cache_short_circuits_bert(monkeypatch, fake_redis) -> None:
    cached = IntentResult(
        intent="sizing", confidence=0.97, margin=0.8, source="bert"
    ).to_dict()
    await fake_redis.set(_cache_key("165cm 55kg 穿什么码"), json.dumps(cached))

    async def should_not_run(text):
        raise AssertionError("缓存命中时不应调用 BERT")

    monkeypatch.setattr(intent_bert_classifier, "predict", should_not_run)
    result = await classify_intent_detailed("165cm 55kg 穿什么码", redis=fake_redis)

    assert result.intent == "sizing"
    assert result.source == "bert"


async def test_disabled_bert_uses_keyword_only(monkeypatch) -> None:
    monkeypatch.setattr(intent_module.Config, "intent_enabled", False)
    result = await classify_intent_detailed("偏远地区包邮吗")

    assert result.intent == "logistics"
    assert result.source == "keyword"


def test_intent_result_roundtrip() -> None:
    original = IntentResult(
        intent="payment",
        confidence=0.88,
        margin=0.5,
        source="bert",
        scores={"payment": 0.88, "membership": 0.38},
    )
    restored = IntentResult.from_dict(original.to_dict())

    assert restored == original
    assert IntentResult.from_dict({"intent": "unknown", "source": "x"}).intent == "other"
