"""意图识别：BERT 主分类器 + 加权关键词兜底。

- 生产路径：异步调用本地微调 BERT，返回置信度与 top1/top2 间隔；
- 兜底路径：模型缺失、推理异常或置信度不足时使用加权关键词；
- 结果可写入 Redis 缓存，避免重复推理。
"""

from __future__ import annotations

import hashlib
import json
import re
import unicodedata
from dataclasses import dataclass, field
from typing import Any, Literal

from app.core.logger import get_logger
from config import Config

logger = get_logger(__name__)

IntentSource = Literal["bert", "keyword", "default"]

# 顺序即平票优先级
_INTENT_LABELS: dict[str, str] = {
    "return_exchange": "退换货",
    "invoice": "发票",
    "logistics": "物流",
    "payment": "支付结算",
    "refund": "退款与资金安全",
    "sizing": "尺码",
    "membership": "会员与优惠",
    "product": "商品咨询",
    "greeting": "问候",
    "other": "其他",
}

INTENT_IDS: tuple[str, ...] = tuple(_INTENT_LABELS)

# 关键词按信息量加权，避免“优惠券”“退款”“地区”这类泛词跨类串扰
_INTENT_RULES: list[tuple[str, tuple[tuple[str, float], ...]]] = [
    (
        "return_exchange",
        (
            ("七天无理由", 1.8),
            ("无理由", 1.2),
            ("退换", 1.6),
            ("换货", 1.7),
            ("退货", 1.4),
            ("拒收", 1.2),
            ("质量问题", 1.2),
            ("吊牌", 1.0),
            ("寄回", 1.0),
            ("售后", 0.8),
        ),
    ),
    (
        "invoice",
        (
            ("发票", 2.0),
            ("开票", 2.0),
            ("红冲", 2.0),
            ("抬头", 1.5),
            ("税号", 1.5),
            ("专票", 1.6),
            ("普票", 1.6),
            ("增值税", 1.6),
        ),
    ),
    (
        "logistics",
        (
            ("物流", 1.6),
            ("快递", 1.3),
            ("发货", 1.6),
            ("签收", 1.1),
            ("运费", 1.3),
            ("邮费", 1.3),
            ("包邮", 1.3),
            ("配送", 1.3),
            ("时效", 1.0),
            ("承运", 1.0),
            ("顺丰", 1.3),
            ("驿站", 1.1),
            ("派送", 1.1),
            ("偏远地区", 1.4),
            ("到货", 0.9),
        ),
    ),
    (
        "refund",
        (
            ("退款到账", 2.2),
            ("原路退回", 2.2),
            ("资金安全", 1.8),
            ("退款进度", 2.2),
            ("退款时间", 1.8),
            ("退款", 1.1),
            ("退钱", 1.3),
            ("到账", 1.0),
        ),
    ),
    (
        "payment",
        (
            ("货到付款", 1.8),
            ("微信支付", 1.8),
            ("支付宝", 1.8),
            ("优惠券", 1.0),
            ("支付", 1.2),
            ("付款", 1.2),
            ("花呗", 1.5),
            ("分期", 1.5),
            ("银行卡", 1.3),
            ("满减", 1.5),
            ("定金", 1.3),
            ("尾款", 1.3),
            ("代付", 1.5),
            ("结算", 1.0),
            ("信用卡", 1.3),
        ),
    ),
    (
        "sizing",
        (
            ("尺码", 2.0),
            ("码数", 1.6),
            ("什么码", 1.8),
            ("选码", 1.8),
            ("偏大", 1.3),
            ("偏小", 1.3),
            ("身高", 1.1),
            ("体重", 1.1),
            ("版型", 1.0),
            ("腰围", 1.3),
            ("胸围", 1.3),
            ("肩宽", 1.3),
            ("臀围", 1.3),
            ("裤长", 1.3),
            ("尺寸", 1.1),
            ("试穿", 0.8),
        ),
    ),
    (
        "membership",
        (
            ("会员日", 2.0),
            ("黄金会员", 2.0),
            ("成长值", 1.8),
            ("生日礼", 1.8),
            ("储值", 1.7),
            ("会员", 1.7),
            ("积分", 1.7),
            ("等级", 1.0),
            ("黑卡", 1.7),
            ("优惠券", 0.6),
        ),
    ),
    (
        "product",
        (
            ("面料", 1.7),
            ("材质", 1.7),
            ("成分", 1.5),
            ("保养", 1.3),
            ("洗涤", 1.3),
            ("清洗", 1.3),
            ("色差", 1.1),
            ("掉色", 1.1),
            ("搭配", 1.1),
            ("穿搭", 1.1),
            ("起球", 1.1),
            ("羽绒", 1.1),
            ("真丝", 1.3),
            ("羊毛", 1.1),
            ("透气", 1.1),
            ("防水", 1.1),
            ("内衬", 1.1),
        ),
    ),
]

_GREETING = re.compile(
    r"^(你好|您好|hi|hello|嗨|哈喽|在吗|在不在|在嘛|有人吗|早上好|下午好|晚上好)"
    r"[\s！!。.，,~～呀啊哟哇亲宝老板客服们]*$",
    re.IGNORECASE,
)

_INTENT_GUIDANCE: dict[str, str] = {
    "return_exchange": "先明确是否支持以及时效，再说明商品状态、吊牌/包装要求、运费承担和操作入口。",
    "invoice": "准确说明发票类型、申请时限、开票时长、所需资料以及退换货后的红冲规则。",
    "logistics": "说明发货时效、不同地区到达时间、包邮门槛、偏远地区运费和异常件处理方式。",
    "payment": "说明可用支付方式、优惠叠加顺序、满减门槛与分期规则，金额示例要计算准确。",
    "refund": "说明退款触发条件、原路退回路径、到账时间和资金安全注意事项。",
    "sizing": "结合用户身高、体重、版型偏好给出明确尺码建议，并说明交界值的取舍原则。",
    "membership": "说明会员等级、积分获取与使用、优惠叠加限制和有效期。",
    "product": "从面料成分、版型、适用场景和保养方式给出专业建议，避免夸大宣传。",
    "greeting": "友好回应并主动询问用户想了解的商品或售前问题。",
    "other": "优先给出直接结论，再补充关键条件；资料不足时明确说明。",
}


@dataclass(frozen=True)
class IntentResult:
    intent: str
    confidence: float = 0.0
    source: IntentSource = "default"
    margin: float = 0.0
    scores: dict[str, float] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "intent": self.intent,
            "confidence": round(self.confidence, 4),
            "source": self.source,
            "margin": round(self.margin, 4),
            "scores": {key: round(value, 4) for key, value in self.scores.items()},
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "IntentResult":
        intent = str(data.get("intent") or "other")
        if intent not in _INTENT_LABELS:
            intent = "other"
        source = str(data.get("source") or "default")
        if source not in {"bert", "keyword", "default"}:
            source = "default"
        scores = data.get("scores")
        return cls(
            intent=intent,
            confidence=float(data.get("confidence") or 0.0),
            source=source,  # type: ignore[arg-type]
            margin=float(data.get("margin") or 0.0),
            scores={str(k): float(v) for k, v in scores.items()}
            if isinstance(scores, dict)
            else {},
        )


def normalize_question(question: str) -> str:
    text = unicodedata.normalize("NFKC", question or "").strip()
    return re.sub(r"\s+", " ", text)


def classify_intent_keyword(question: str) -> IntentResult:
    """加权关键词分类，作为 BERT 不可用时的确定性兜底。"""
    text = normalize_question(question)
    if not text:
        return IntentResult("other", confidence=1.0, source="default")
    if _GREETING.match(text):
        return IntentResult("greeting", confidence=0.95, source="keyword")

    scores: dict[str, float] = {}
    for intent, weighted_keywords in _INTENT_RULES:
        score = sum(weight for keyword, weight in weighted_keywords if keyword in text)
        if score > 0:
            scores[intent] = score
    if not scores:
        return IntentResult("other", confidence=0.4, source="keyword", scores={})

    ranked = sorted(
        scores.items(),
        key=lambda item: (
            item[1],
            -INTENT_IDS.index(item[0]) if item[0] in INTENT_IDS else 0,
        ),
        reverse=True,
    )
    best_intent, best_score = ranked[0]
    second_score = ranked[1][1] if len(ranked) > 1 else 0.0
    return IntentResult(
        intent=best_intent,
        confidence=min(1.0, best_score / 3.0),
        source="keyword",
        margin=max(0.0, best_score - second_score),
        scores=scores,
    )


def classify_intent(question: str) -> str:
    """同步便捷接口，仅使用关键词规则（保留给提示词等同步路径）。"""
    return classify_intent_keyword(question).intent


def intent_label(intent: str) -> str:
    return _INTENT_LABELS.get(intent, "其他")


def intent_guidance(intent: str) -> str:
    return _INTENT_GUIDANCE.get(intent, _INTENT_GUIDANCE["other"])


def _cache_key(question: str) -> str:
    digest = hashlib.sha256(normalize_question(question).encode("utf-8")).hexdigest()
    return f"asktofit:intent:{digest}"


async def classify_intent_detailed(
    question: str, redis: Any | None = None
) -> IntentResult:
    """异步意图识别：Redis 缓存 → BERT → 关键词兜底。"""
    text = normalize_question(question)
    if not text:
        return IntentResult("other", confidence=1.0, source="default")

    cache_key = _cache_key(text)
    if redis is not None and Config.cache_enabled:
        try:
            cached = await redis.get(cache_key)
            if cached:
                return IntentResult.from_dict(json.loads(cached))
        except Exception:
            logger.warning("读取意图缓存失败，继续实时分类 key=%s", cache_key, exc_info=True)

    result: IntentResult | None = None
    if Config.intent_enabled:
        try:
            from app.services.intent_bert import intent_bert_classifier

            bert_result = await intent_bert_classifier.predict(text)
            if (
                bert_result.confidence >= Config.intent_confidence_threshold
                and bert_result.margin >= Config.intent_margin_threshold
            ):
                result = bert_result
            else:
                logger.warning(
                    "BERT 意图置信度不足，启用关键词兜底 intent=%s confidence=%.3f "
                    "margin=%.3f question=%s",
                    bert_result.intent,
                    bert_result.confidence,
                    bert_result.margin,
                    text[:80],
                )
                keyword_result = classify_intent_keyword(text)
                result = keyword_result if keyword_result.intent != "other" else bert_result
        except Exception:
            logger.warning(
                "BERT 意图模型不可用，已降级为关键词规则（app/services/intent.py:"
                "classify_intent_detailed）question=%s",
                text[:80],
                exc_info=True,
            )

    if result is None:
        result = classify_intent_keyword(text)

    if redis is not None and Config.cache_enabled:
        try:
            await redis.setex(
                cache_key,
                max(60, Config.intent_cache_ttl),
                json.dumps(result.to_dict(), ensure_ascii=False),
            )
        except Exception:
            logger.warning("写入意图缓存失败 key=%s", cache_key, exc_info=True)
    return result
