from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field


class IngestRequest(BaseModel):
    title: str = Field(min_length=1, max_length=255)
    # 上限 20 万字符：再长的单篇文档应该拆文件入库，否则一次请求就把连接占住
    content: str = Field(min_length=1, max_length=200_000)
    source_kind: Literal["text", "markdown"] = "text"


class DocumentRead(BaseModel):
    id: int
    title: str
    source_kind: Literal["text", "markdown"]
    char_count: int
    chunk_count: int
    embedded_chunks: int
    embedding_model: str | None = None
    created_at: datetime


class IngestRead(BaseModel):
    document: DocumentRead
    # 向量化失败不是错误，是降级：文档已入库，只是本次没有向量
    warnings: list[str] = Field(default_factory=list)


class SearchRequest(BaseModel):
    query: str = Field(min_length=1, max_length=200)
    top_k: int = Field(default=6, ge=1, le=20)


class CitationRead(BaseModel):
    """引用：必须能指回原文的具体字符区间，否则不叫引用。"""

    document_id: int
    document_title: str
    ordinal: int
    char_start: int
    char_end: int


class HitRead(BaseModel):
    citation: CitationRead
    content: str
    snippet: str
    score: float
    lexical_rank: int | None = None
    vector_rank: int | None = None
    # 两路的原始分数：RRF 只用于排序，判断"这条够不够相关"得看原始分数。
    # 上层（如 P01 客服）拿它做拒答阈值，不要自己反过来从名次推强度。
    lexical_score: float | None = None
    vector_score: float | None = None
    matched_terms: list[str] = Field(default_factory=list)


class SearchRead(BaseModel):
    query: str
    hits: list[HitRead] = Field(default_factory=list)
    lexical_hits: int = 0
    vector_hits: int = 0
    vector_enabled: bool = False
    warnings: list[str] = Field(default_factory=list)
    elapsed_ms: int = 0


class StatsRead(BaseModel):
    documents: int
    chunks: int
    embedded_chunks: int
    embedding_coverage: float
    embedding_configured: bool
    embedding_model: str | None = None
