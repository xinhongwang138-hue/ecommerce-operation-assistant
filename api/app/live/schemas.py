"""直播切片的出入参。"""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class TranscriptItem(BaseModel):
    start: int
    end: int = 0
    text: str


class SessionCreate(BaseModel):
    title: str = Field(min_length=1, max_length=200)
    product_name: str = ""
    transcript: list[TranscriptItem] = Field(default_factory=list)


class ClipRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    session_id: int
    start_seconds: int
    end_seconds: int
    title: str
    hook: str
    transcript: str
    score: int
    reasons: list[str] = Field(default_factory=list)
    status: str
    decided_at: datetime | None = None


class SessionRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    title: str
    product_name: str
    duration_seconds: int
    status: str
    clip_count: int
    rule_version: str
    created_at: datetime
    clips: list[ClipRead] = Field(default_factory=list)


class SessionListItem(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    title: str
    product_name: str
    duration_seconds: int
    status: str
    clip_count: int
    created_at: datetime


class DecideRequest(BaseModel):
    status: str = Field(description="approved / rejected")


class ClipStat(BaseModel):
    status: str
    count: int


class SummaryRead(BaseModel):
    sessions: int
    segments: int
    clips: int
    approved: int
    rejected: int
    average_score: float
    by_status: list[ClipStat]
