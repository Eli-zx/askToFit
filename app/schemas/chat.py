"""对话、消息与流式问答相关模型。"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from config import AVAILABLE_MODELS, Config

MessageRole = Literal["user", "assistant"]


class ConversationCreate(BaseModel):
    title: str | None = Field(None, max_length=120)
    kb_ids: list[int] = Field(..., min_length=1, max_length=10)
    model_name: str = Field(Config.model, max_length=64)
    temperature: float = Field(Config.temperature_default, ge=0.0, le=2.0)
    top_p: float = Field(Config.top_p_default, gt=0.0, le=1.0)
    max_tokens: int = Field(Config.max_tokens_default, ge=32, le=Config.max_tokens_limit)
    history_rounds: int = Field(
        Config.history_rounds_default, ge=0, le=Config.history_rounds_max
    )

    @model_validator(mode="after")
    def validate_model(self) -> "ConversationCreate":
        if self.model_name not in AVAILABLE_MODELS:
            raise ValueError(f"不支持的模型：{self.model_name}")
        self.kb_ids = list(dict.fromkeys(self.kb_ids))
        return self


class ConversationUpdate(BaseModel):
    title: str | None = Field(None, min_length=1, max_length=120)
    kb_ids: list[int] | None = Field(None, min_length=1, max_length=10)
    model_name: str | None = Field(None, max_length=64)
    temperature: float | None = Field(None, ge=0.0, le=2.0)
    top_p: float | None = Field(None, gt=0.0, le=1.0)
    max_tokens: int | None = Field(None, ge=32, le=Config.max_tokens_limit)
    history_rounds: int | None = Field(None, ge=0, le=Config.history_rounds_max)

    @model_validator(mode="after")
    def validate_model_and_kbs(self) -> "ConversationUpdate":
        if self.model_name is not None and self.model_name not in AVAILABLE_MODELS:
            raise ValueError(f"不支持的模型：{self.model_name}")
        if self.kb_ids is not None:
            self.kb_ids = list(dict.fromkeys(self.kb_ids))
        return self


class ConversationOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    title: str
    kb_ids: list[int]
    model_name: str
    temperature: float
    top_p: float
    max_tokens: int
    history_rounds: int
    message_count: int
    last_message: str | None = None
    archived_at: datetime | None = None
    created_at: datetime
    updated_at: datetime


class MessageOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    conversation_id: int
    role: MessageRole
    content: str
    sources: list[dict[str, Any]] | None = None
    intent: str | None = None
    rewritten_query: str | None = None
    latency_ms: int | None = None
    created_at: datetime


class MessageListOut(BaseModel):
    total: int
    items: list[MessageOut]


class ChatSendRequest(BaseModel):
    content: str = Field(..., min_length=1, max_length=4000)


class ModelOption(BaseModel):
    name: str
    label: str
    provider: str
    available: bool
    default: bool = False


class ChatOptionsOut(BaseModel):
    models: list[ModelOption]
    defaults: dict[str, Any]
    limits: dict[str, Any]


class KnowledgeGapOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    conversation_id: int | None = None
    question: str
    intent: str | None = None
    intent_confidence: float | None = None
    intent_source: str | None = None
    kb_ids: list[int]
    best_score: float | None = None
    vector_score: float | None = None
    rerank_score: float | None = None
    retrieval_mode: str | None = None
    status: str = "open"
    note: str | None = None
    resolved_at: datetime | None = None
    created_at: datetime


class KnowledgeGapUpdate(BaseModel):
    status: Literal["open", "resolved", "ignored"] | None = None
    note: str | None = Field(None, max_length=1000)


class KnowledgeGapStatItem(BaseModel):
    key: str
    label: str
    count: int


class KnowledgeGapStats(BaseModel):
    total: int
    open: int
    resolved: int
    ignored: int
    by_intent: list[KnowledgeGapStatItem]
    by_date: list[KnowledgeGapStatItem]
    top_questions: list[KnowledgeGapStatItem]
