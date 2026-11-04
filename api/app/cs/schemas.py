"""P01 的入参/出参。字段与前端 types.ts 一一对应。"""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field

TrackKind = Literal["handoff", "faq", "retrieval", "refuse"]


class ChatRequest(BaseModel):
    message: str = Field(min_length=1, max_length=500)
    # 不给就新开一个会话；给了就续在同一个会话里（P09 按会话聚合质检）
    conversation_id: int | None = None


class CitationRead(BaseModel):
    kind: Literal["faq", "rag"]
    label: str
    snippet: str
    score: float | None = None


class TicketBrief(BaseModel):
    id: int
    trigger: str
    priority: str
    reason: str


class ChatRead(BaseModel):
    conversation_id: int
    track: TrackKind
    reply: str
    citations: list[CitationRead] = Field(default_factory=list)
    handoff: TicketBrief | None = None
    # 为什么是这个轨道：转人工命中哪个词、FAQ 命中哪条、检索分数多少
    reason: str = ""
    refuse_reason: str = ""


class HandoffRequest(BaseModel):
    conversation_id: int
    reason: str = Field(default="", max_length=200)


class TicketRead(BaseModel):
    id: int
    conversation_id: int
    reason: str
    trigger: str
    priority: str
    status: str
    summary: str | None = None
    created_at: datetime
    closed_at: datetime | None = None


class FaqItemRead(BaseModel):
    key: str
    category: str
    question: str
    answer: str


class TrackStat(BaseModel):
    track: str
    count: int


class CsSummary(BaseModel):
    """客服台首屏：分流分布 + 工单情况。转人工占比是这套系统最重要的一个数。"""

    conversations: int
    messages: int
    handoff_tickets: int
    open_tickets: int
    tracks: list[TrackStat] = Field(default_factory=list)
    faq_items: int = 0
