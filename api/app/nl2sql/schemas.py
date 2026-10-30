from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


class AskRequest(BaseModel):
    # 上限 400 字：再长的问题多半是"贴了一整段表格说明"，那不是问数，是需求文档。
    question: str = Field(min_length=2, max_length=400)


class AskRead(BaseModel):
    question: str
    sql: str
    explanation: str
    assumptions: list[str] = Field(default_factory=list)
    confidence: float
    columns: list[str] = Field(default_factory=list)
    rows: list[list[Any]] = Field(default_factory=list)
    row_count: int
    truncated: bool
    max_rows: int
    attempts: int
    elapsed_ms: int


class ExampleRead(BaseModel):
    question: str
    table: str
    note: str
