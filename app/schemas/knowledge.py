"""知识库、文档、分块相关模型。"""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

EmbeddingMode = Literal["dense", "hybrid"]


class KnowledgeBaseCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=100)
    description: str | None = Field(None, max_length=500)
    embedding_mode: EmbeddingMode = "hybrid"
    chunk_size: int = Field(500, ge=100, le=2000)
    chunk_overlap: int = Field(80, ge=0, le=1000)
    is_public: bool = False

    @model_validator(mode="after")
    def check_overlap(self) -> "KnowledgeBaseCreate":
        if self.chunk_overlap >= self.chunk_size:
            raise ValueError("重叠大小必须小于切块大小")
        return self


class KnowledgeBaseUpdate(BaseModel):
    name: str | None = Field(None, min_length=1, max_length=100)
    description: str | None = Field(None, max_length=500)
    is_public: bool | None = None


class KnowledgeBaseOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    owner_id: int
    name: str
    description: str | None = None
    embedding_mode: str
    chunk_size: int
    chunk_overlap: int
    document_count: int
    is_public: bool = False
    created_at: datetime
    updated_at: datetime


class DocumentOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    kb_id: int
    original_name: str
    file_type: str
    file_size: int
    status: str
    error_message: str | None = None
    chunk_count: int
    chunk_size: int
    chunk_overlap: int
    embedding_mode: str
    parser: str = "auto"
    version: int = 1
    is_current: bool = True
    update_action: str = "created"
    similar_doc_id: int | None = None
    similar_doc_name: str | None = None
    similarity: float | None = None
    replaces_doc_id: int | None = None
    created_at: datetime
    updated_at: datetime


class ChunkOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    uid: str
    doc_id: int
    kb_id: int
    chunk_index: int
    parent_uid: str | None = None
    content: str
    title: str | None = None
    section: str | None = None
    page: int | None = None
    char_count: int
    parent_index: int | None = None
    parent_content: str | None = None
    created_at: datetime
    updated_at: datetime


class ParentChunkOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    uid: str
    doc_id: int
    kb_id: int
    parent_index: int
    content: str
    title: str | None = None
    section: str | None = None
    page: int | None = None
    char_count: int
    created_at: datetime
    updated_at: datetime


class ChunkListOut(BaseModel):
    total: int
    page: int
    page_size: int
    items: list[ChunkOut]


class ChunkUpdate(BaseModel):
    content: str = Field(..., min_length=1, max_length=8000)
