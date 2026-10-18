"""本地微调 BERT 意图分类器（线程内推理，异步接口）。"""

from __future__ import annotations

import asyncio
import threading
import time
from typing import Any

from app.services.intent import IntentResult, normalize_question
from config import Config

from app.core.logger import get_logger

logger = get_logger(__name__)

_LOAD_RETRY_COOLDOWN_SECONDS = 60.0


class IntentModelError(RuntimeError):
    """BERT 意图模型加载或推理失败。"""


class BertIntentClassifier:
    """懒加载 BERT；加载失败后进入冷却期，避免每个请求重复加载。"""

    def __init__(self) -> None:
        self._model: Any = None
        self._tokenizer: Any = None
        self._lock = threading.Lock()
        self._last_error: Exception | None = None
        self._last_error_at = 0.0

    def _load_sync(self) -> tuple[Any, Any]:
        if self._model is not None and self._tokenizer is not None:
            return self._model, self._tokenizer
        if self._last_error is not None:
            if time.monotonic() - self._last_error_at < _LOAD_RETRY_COOLDOWN_SECONDS:
                raise IntentModelError(str(self._last_error)) from self._last_error

        with self._lock:
            if self._model is not None and self._tokenizer is not None:
                return self._model, self._tokenizer
            try:
                from transformers import AutoModelForSequenceClassification, AutoTokenizer

                logger.info(
                    "正在加载 BERT 意图模型 path=%s", Config.intent_model_path
                )
                self._tokenizer = AutoTokenizer.from_pretrained(
                    Config.intent_model_path
                )
                model = AutoModelForSequenceClassification.from_pretrained(
                    Config.intent_model_path
                )
                model.eval()
                self._model = model
                self._last_error = None
                logger.info("BERT 意图模型加载完成")
            except Exception as exc:
                self._last_error = exc
                self._last_error_at = time.monotonic()
                logger.error(
                    "BERT 意图模型加载失败 path=%s（app/services/intent_bert.py:"
                    "_load_sync）",
                    Config.intent_model_path,
                    exc_info=True,
                )
                raise IntentModelError(str(exc)) from exc
        return self._model, self._tokenizer

    def _predict_sync(self, text: str) -> IntentResult:
        import torch

        model, tokenizer = self._load_sync()
        encoded = tokenizer(
            text,
            return_tensors="pt",
            truncation=True,
            max_length=Config.intent_max_length,
        )
        with self._lock, torch.no_grad():
            logits = model(**encoded).logits[0]
            probabilities = torch.softmax(logits, dim=-1)
            top_k = min(2, probabilities.shape[-1])
            values, indices = torch.topk(probabilities, k=top_k)

        scores: dict[str, float] = {}
        for index, probability in enumerate(probabilities.tolist()):
            label = model.config.id2label.get(index, str(index))
            scores[str(label)] = float(probability)

        top1 = int(indices[0].item())
        top1_label = str(model.config.id2label.get(top1, "other"))
        top1_score = float(values[0].item())
        top2_score = float(values[1].item()) if top_k > 1 else 0.0
        return IntentResult(
            intent=top1_label,
            confidence=top1_score,
            source="bert",
            margin=max(0.0, top1_score - top2_score),
            scores=scores,
        )

    async def predict(self, text: str) -> IntentResult:
        normalized = normalize_question(text)
        if not normalized:
            return IntentResult("other", confidence=1.0, source="default")
        return await asyncio.to_thread(self._predict_sync, normalized)

    def reset(self) -> None:
        """测试或热更新模型时清理已加载实例。"""
        with self._lock:
            self._model = None
            self._tokenizer = None
            self._last_error = None
            self._last_error_at = 0.0


intent_bert_classifier = BertIntentClassifier()
