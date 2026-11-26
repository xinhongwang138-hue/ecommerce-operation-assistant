"""P09 的入参/出参。字段名与前端 types.ts 一一对应，改名要两边一起改。"""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field

SourceKind = Literal["import", "agent"]
RoleKind = Literal["customer", "agent"]


class ImportMessage(BaseModel):
    # 上游叫法五花八门（user / bot / assistant / 客服），归一化在服务层做，
    # 这里放宽成 str，拒绝脏数据靠"归不出角色"而不是靠枚举白名单。
    role: str = Field(min_length=1, max_length=30)
    content: str = Field(min_length=1, max_length=20000)
    sent_at: datetime | None = None


class ImportSession(BaseModel):
    external_id: str = Field(min_length=1, max_length=120)
    agent_name: str = Field(min_length=1, max_length=80)
    channel: str = Field(default="manual", max_length=40)
    started_at: datetime | None = None
    messages: list[ImportMessage] = Field(min_length=1, max_length=200)


class ImportRequest(BaseModel):
    sessions: list[ImportSession] = Field(min_length=1, max_length=200)


class ImportRead(BaseModel):
    imported: int
    updated: int
    skipped: int
    scored: int
    warnings: list[str] = Field(default_factory=list)


class AgentImportRead(BaseModel):
    """从本仓会话表拉取的结果。没有数据就是 0，不编样本。"""

    imported: int
    skipped: int
    scanned: int
    reason: str = ""


class EvidenceRead(BaseModel):
    seq: int
    role: str
    excerpt: str
    note: str


class RuleScoreRead(BaseModel):
    key: str
    title: str
    dimension: str
    max_score: float
    score: float
    passed: bool
    detail: str
    advice: str
    evidence: list[EvidenceRead] = Field(default_factory=list)


class MessageRead(BaseModel):
    seq: int
    role: RoleKind
    content: str
    sent_at: datetime | None = None


class ScoreRead(BaseModel):
    total: float
    grade: str
    redline: bool
    redline_rules: list[str] = Field(default_factory=list)
    rule_version: str
    scored_at: datetime | None = None
    breakdown: list[RuleScoreRead] = Field(default_factory=list)


class SessionListItem(BaseModel):
    id: int
    external_id: str
    agent_name: str
    channel: str
    source_kind: SourceKind
    started_at: datetime
    message_count: int
    total: float | None = None
    grade: str | None = None
    redline: bool = False


class SessionListRead(BaseModel):
    items: list[SessionListItem] = Field(default_factory=list)
    total: int = 0
    average: float | None = None


class RedlineEvidenceRead(BaseModel):
    """红线证据：哪一句、踩了哪条线、命中的原词。报告必须能直接指出这句。"""

    key: str
    title: str
    seq: int
    excerpt: str
    phrase: str


class ReportRead(BaseModel):
    session: SessionListItem
    messages: list[MessageRead] = Field(default_factory=list)
    score: ScoreRead
    redline_evidence: list[RedlineEvidenceRead] = Field(default_factory=list)
    # 失分最多的一项 + 它自己的改进建议，报告的落点就是这句话
    weakest: RuleScoreRead | None = None
    suggestions: list[str] = Field(default_factory=list)


class LeaderboardRow(BaseModel):
    agent_name: str
    sessions: int
    average: float
    best: float
    worst: float
    a_count: int
    redline_count: int


class LeaderboardRead(BaseModel):
    days: int
    rows: list[LeaderboardRow] = Field(default_factory=list)
    total_sessions: int = 0


class SummaryRead(BaseModel):
    sessions: int
    scored: int
    unscored: int
    average: float | None = None
    agents: int
    redline_sessions: int
    grade_distribution: dict[str, int] = Field(default_factory=dict)
    days: int
    rule_version: str


class LowScoreItem(BaseModel):
    external_id: str
    session_id: int
    agent_name: str
    channel: str
    started_at: datetime
    total: float
    grade: str
    redline: bool
    redline_rules: list[str] = Field(default_factory=list)
    redline_evidence: list[RedlineEvidenceRead] = Field(default_factory=list)
    messages: list[MessageRead] = Field(default_factory=list)
    issues: list[RuleScoreRead] = Field(default_factory=list)


class LowScoreExport(BaseModel):
    """低分样本导出：对齐 P09→P10 的"回流成微调数据集"，所以带原文与问题项。"""

    generated_at: datetime
    threshold: float
    rule_version: str
    items: list[LowScoreItem] = Field(default_factory=list)
