"""合同审批的出入参。

读模型一律带 `*_title` 这类给人看的字段：`kind="payment"` 是给程序看的，
审批人要看到「付款」。让前端做映射表的后果是两边各写一份，迟早对不上。
"""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

from app.contracts import clauses as clause_meta
from app.contracts import rules as rule_meta


class ClauseRead(BaseModel):
    ordinal: int
    heading: str
    text: str
    kind: str
    kind_title: str
    char_start: int
    char_end: int


class RiskRead(BaseModel):
    id: int
    rule_key: str
    severity: str
    title: str
    detail: str
    quote: str
    source: str
    suggestion: str
    clause_ordinal: int | None = None
    clause_kind: str
    clause_kind_title: str


class ApprovalRead(BaseModel):
    id: int
    decision: str
    comment: str
    decided_by_user_id: int | None = None
    decided_at: datetime
    risk_snapshot: list[dict] = Field(default_factory=list)


class ContractListItem(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    filename: str
    status: str
    risk_score: int
    risk_level: str
    clause_count: int
    risk_count: int
    high_risk_count: int
    semantic_checked: bool
    reviewed_at: datetime | None = None
    created_at: datetime


class ContractRead(BaseModel):
    id: int
    filename: str
    content_type: str
    size_bytes: int
    status: str
    extract_error: str | None = None
    risk_score: int
    # 两段分数分开给：规则那一段可复现，语义那一段需要人来确认。
    # 合成一个数会让人误以为"模型读出来的和规则查到的一样硬"。
    rule_score: int = 0
    semantic_score: int = 0
    risk_level: str
    clause_count: int
    risk_count: int
    high_risk_count: int
    semantic_checked: bool
    summary: str | None = None
    rule_version: str
    reviewed_at: datetime | None = None
    created_at: datetime
    clauses: list[ClauseRead] = Field(default_factory=list)
    risks: list[RiskRead] = Field(default_factory=list)
    approvals: list[ApprovalRead] = Field(default_factory=list)


class UploadRead(BaseModel):
    """上传的即时回执。

    `duplicate_of` 不是错误：合同改一版是常态，同一个文件重复上传更常见
    （"我是不是已经传过了"）。提示一下，不拦。
    """

    id: int
    filename: str
    status: str
    clause_count: int
    risk_count: int
    high_risk_count: int
    risk_score: int
    risk_level: str
    duplicate_of: int | None = None


class SemanticReviewRead(BaseModel):
    id: int
    semantic_checked: bool
    summary: str | None = None
    added: int
    dropped: int
    dropped_reason: str = ""
    risk_count: int
    high_risk_count: int
    risk_score: int
    risk_level: str


class ApprovalRequest(BaseModel):
    decision: str = Field(description="approved / rejected / changes_requested")
    comment: str = ""


class SummaryRead(BaseModel):
    contracts: int
    reviewed: int
    pending_semantic: int
    high_risk: int
    approvals: int
    average_score: float
    by_level: dict[str, int]
    by_risk_key: list[dict]


def clause_read(clause) -> ClauseRead:
    return ClauseRead(
        ordinal=clause.ordinal,
        heading=clause.heading,
        text=clause.text,
        kind=clause.kind,
        kind_title=clause_meta.title_of(clause.kind),
        char_start=clause.char_start,
        char_end=clause.char_end,
    )


def risk_read(risk) -> RiskRead:
    return RiskRead(
        id=risk.id,
        rule_key=risk.rule_key,
        severity=risk.severity,
        title=risk.title,
        detail=risk.detail,
        quote=risk.quote,
        source=risk.source,
        suggestion=risk.suggestion,
        clause_ordinal=risk.clause_ordinal,
        clause_kind=risk.clause_kind,
        clause_kind_title=rule_meta.kind_title(risk.clause_kind),
    )
