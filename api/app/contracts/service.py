"""合同审批的编排：收文件 → 切条款 → 跑规则 → （显式触发）语义复核 → 留痕审批。

两处刻意的"不做"：

1. **不在上传时调模型。** 上传要快、要可重试；模型调用慢且会失败。规则先出结果，
   语义复核是单独一步，失败不影响已经拿到的规则结论。
2. **不替人做通过/不通过。** 系统只出风险清单（ADR 0016）。唯一的要求是：
   在高风险合同上点"通过"，必须写一句理由——不是拦住你，是让签字有据可查。
"""

from __future__ import annotations

import hashlib
from datetime import UTC, datetime

from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session, selectinload

from app.contracts import clauses as clause_splitter
from app.contracts import extract, rules, scoring, semantic
from app.contracts.schemas import (
    ApprovalRead,
    ContractRead,
    SemanticReviewRead,
    SummaryRead,
    UploadRead,
    clause_read,
    risk_read,
)
from app.models import (
    Contract,
    ContractApproval,
    ContractClause,
    ContractRisk,
    ContractRiskLevel,
    ContractStatus,
)
from app.services.llm import LLMNotConfiguredError, LLMResponseError

DECISIONS = ("approved", "rejected", "changes_requested")

DECISION_TITLES = {
    "approved": "通过",
    "rejected": "驳回",
    "changes_requested": "退回修改",
}

# 高风险合同的"通过"必须带理由。这不是流程洁癖：一条 high 风险意味着
# 账期 90 天或赔偿不封顶这类事，签了就得有人负责。
REQUIRE_COMMENT_ON_HIGH_RISK = True


class ContractError(Exception):
    """调用方要把它翻译成 HTTP 状态码的业务异常。"""

    def __init__(self, message: str, status_code: int = 400) -> None:
        super().__init__(message)
        self.message = message
        self.status_code = status_code


def upload(
    db: Session,
    workspace_id: int,
    *,
    user_id: int | None,
    filename: str,
    content_type: str,
    data: bytes,
) -> UploadRead:
    """收下文件并跑完规则阶段。语义复核要另外调一次。"""
    try:
        text = extract.extract(filename, data)
    except extract.UnsupportedFormat as exc:
        raise ContractError(str(exc), status_code=415) from exc
    except extract.EmptyText as exc:
        raise ContractError(str(exc), status_code=422) from exc

    sha256 = hashlib.sha256(data).hexdigest()
    duplicate_of = db.scalar(
        select(Contract.id)
        .where(Contract.workspace_id == workspace_id, Contract.sha256 == sha256)
        .order_by(Contract.id.desc())
    )

    pieces = clause_splitter.split(text)
    if not pieces:
        raise ContractError("没有切出任何条款，请检查文件内容", status_code=422)

    findings = rules.evaluate(pieces)
    score = scoring.score_of([item.severity for item in findings])
    level = scoring.level_of(score)

    contract = Contract(
        workspace_id=workspace_id,
        uploaded_by_user_id=user_id,
        filename=filename[:255],
        content_type=(content_type or "application/octet-stream")[:120],
        size_bytes=len(data),
        sha256=sha256,
        text=text,
        status=ContractStatus.EXTRACTED,
        clause_count=len(pieces),
        risk_count=len(findings),
        high_risk_count=sum(1 for item in findings if item.severity == "high"),
        risk_score=score,
        risk_level=ContractRiskLevel(level),
        semantic_checked=False,
        rule_version=rules.RULE_VERSION,
    )
    db.add(contract)
    db.flush()

    db.add_all(
        [
            ContractClause(
                workspace_id=workspace_id,
                contract_id=contract.id,
                ordinal=item.ordinal,
                heading=item.heading,
                text=item.text,
                kind=item.kind,
                char_start=item.char_start,
                char_end=item.char_end,
            )
            for item in pieces
        ]
    )
    db.add_all([_risk_row(workspace_id, contract.id, item) for item in findings])
    db.commit()
    db.refresh(contract)

    return UploadRead(
        id=contract.id,
        filename=contract.filename,
        status=contract.status.value,
        clause_count=contract.clause_count,
        risk_count=contract.risk_count,
        high_risk_count=contract.high_risk_count,
        risk_score=contract.risk_score,
        risk_level=contract.risk_level.value,
        duplicate_of=duplicate_of,
    )


def review_semantic(db: Session, workspace_id: int, contract_id: int) -> SemanticReviewRead:
    """第二阶段：只补充规则抓不到的语义风险。

    重跑是安全的：之前由模型加的条目会被整体替换，规则条目原样保留。
    这样"重跑一次"不会让同一份合同的风险翻倍。
    """
    contract = _load(db, workspace_id, contract_id)
    if not contract.text.strip():
        raise ContractError("合同没有正文，无法复核", status_code=422)

    pieces = [
        clause_splitter.Clause(
            ordinal=item.ordinal,
            heading=item.heading,
            text=item.text,
            kind=item.kind,
            char_start=item.char_start,
            char_end=item.char_end,
        )
        for item in sorted(contract.clauses, key=lambda row: row.ordinal)
    ]
    existing = [item for item in contract.risks if item.source == "rule"]
    rule_findings = [
        rules.Finding(
            rule_key=item.rule_key,
            severity=item.severity.value,
            title=item.title,
            detail=item.detail,
            quote=item.quote,
            suggestion=item.suggestion,
            clause_ordinal=item.clause_ordinal,
            clause_kind=item.clause_kind,
        )
        for item in existing
    ]

    try:
        result = semantic.review(contract.text, pieces, rule_findings)
    except LLMNotConfiguredError as exc:
        raise ContractError("模型未配置，无法做语义复核", status_code=400) from exc
    except (LLMResponseError, ValueError) as exc:
        raise ContractError(f"语义复核失败：{exc}", status_code=502) from exc

    # 先清掉上一轮的模型条目，再写新的：重跑不能叠加
    db.execute(
        delete(ContractRisk).where(
            ContractRisk.contract_id == contract.id, ContractRisk.source == "llm"
        )
    )
    db.add_all([_risk_row(workspace_id, contract.id, item) for item in result.findings])

    contract.summary = result.summary or None
    contract.semantic_checked = True
    contract.status = ContractStatus.REVIEWED
    # 和 created_at 同源：都走数据库时钟，两者相减才是"审了多久"
    contract.reviewed_at = func.now()
    db.flush()

    _recompute(db, contract)
    db.commit()
    db.refresh(contract)

    return SemanticReviewRead(
        id=contract.id,
        semantic_checked=contract.semantic_checked,
        summary=contract.summary,
        added=len(result.findings),
        dropped=result.dropped,
        dropped_reason=result.dropped_reason,
        risk_count=contract.risk_count,
        high_risk_count=contract.high_risk_count,
        risk_score=contract.risk_score,
        risk_level=contract.risk_level.value,
    )


def approve(
    db: Session,
    workspace_id: int,
    contract_id: int,
    *,
    user_id: int | None,
    decision: str,
    comment: str,
) -> ApprovalRead:
    if decision not in DECISIONS:
        raise ContractError(f"decision 只接受 {'/'.join(DECISIONS)}", status_code=400)
    contract = _load(db, workspace_id, contract_id)

    text = (comment or "").strip()
    if REQUIRE_COMMENT_ON_HIGH_RISK and decision == "approved" and contract.high_risk_count > 0:
        if not text:
            raise ContractError(
                f"这份合同有 {contract.high_risk_count} 条高风险，通过时必须写明理由", status_code=422
            )

    snapshot = [
        {
            "rule_key": item.rule_key,
            "severity": item.severity.value,
            "title": item.title,
            "quote": item.quote,
            "source": item.source,
            "clause_ordinal": item.clause_ordinal,
        }
        for item in contract.risks
    ]
    row = ContractApproval(
        workspace_id=workspace_id,
        contract_id=contract.id,
        decision=decision,
        comment=text,
        decided_by_user_id=user_id,
        risk_snapshot=snapshot,
    )
    db.add(row)
    db.commit()
    db.refresh(row)

    return ApprovalRead(
        id=row.id,
        decision=row.decision,
        comment=row.comment,
        decided_by_user_id=row.decided_by_user_id,
        decided_at=row.decided_at,
        risk_snapshot=row.risk_snapshot or [],
    )


def get(db: Session, workspace_id: int, contract_id: int) -> ContractRead:
    contract = _load(db, workspace_id, contract_id)
    return _contract_read(contract)


def list_contracts(db: Session, workspace_id: int, *, limit: int = 20) -> list[ContractRead]:
    """列表也返回完整明细。

    不另建一个精简模型：前端点开一份合同要立刻看到条款和风险，再发一次请求
    只会多一个"加载中"的空窗。合同数量是十几个量级，不是几千个。
    """
    statement = (
        select(Contract)
        .where(Contract.workspace_id == workspace_id)
        .options(
            selectinload(Contract.clauses),
            selectinload(Contract.risks),
            selectinload(Contract.approvals),
        )
        .order_by(Contract.id.desc())
        .limit(limit)
    )
    return [_contract_read(item) for item in db.scalars(statement).unique()]


def summary(db: Session, workspace_id: int) -> SummaryRead:
    contracts = list(
        db.scalars(
            select(Contract)
            .where(Contract.workspace_id == workspace_id)
            .options(selectinload(Contract.risks))
        ).unique()
    )
    total = len(contracts)
    scores = [item.risk_score for item in contracts]
    by_level = {"low": 0, "medium": 0, "high": 0}
    for item in contracts:
        by_level[item.risk_level.value] = by_level.get(item.risk_level.value, 0) + 1

    counters: dict[str, int] = {}
    for item in contracts:
        for risk in item.risks:
            counters[risk.rule_key] = counters.get(risk.rule_key, 0) + 1

    return SummaryRead(
        contracts=total,
        reviewed=sum(1 for item in contracts if item.status is ContractStatus.REVIEWED),
        pending_semantic=sum(
            1 for item in contracts if item.status is ContractStatus.EXTRACTED
        ),
        high_risk=sum(1 for item in contracts if item.risk_level is ContractRiskLevel.HIGH),
        approvals=db.scalar(
            select(func.count())
            .select_from(ContractApproval)
            .where(ContractApproval.workspace_id == workspace_id)
        )
        or 0,
        average_score=round(sum(scores) / total, 1) if total else 0.0,
        by_level=by_level,
        by_risk_key=[
            {"rule_key": key, "count": count}
            for key, count in sorted(counters.items(), key=lambda pair: (-pair[1], pair[0]))
        ],
    )


def decision_titles() -> dict[str, str]:
    return dict(DECISION_TITLES)


def _load(db: Session, workspace_id: int, contract_id: int) -> Contract:
    contract = db.scalar(
        select(Contract)
        .where(Contract.id == contract_id, Contract.workspace_id == workspace_id)
        .options(
            selectinload(Contract.clauses),
            selectinload(Contract.risks),
            selectinload(Contract.approvals),
        )
    )
    if contract is None:
        # 不存在和不属于你返回同一个 404：不让人用状态码探测别的租户有什么合同
        raise ContractError("合同不存在", status_code=404)
    return contract


def _risk_row(workspace_id: int, contract_id: int, finding: rules.Finding) -> ContractRisk:
    return ContractRisk(
        workspace_id=workspace_id,
        contract_id=contract_id,
        clause_ordinal=finding.clause_ordinal,
        clause_kind=finding.clause_kind,
        rule_key=finding.rule_key,
        severity=ContractRiskLevel(finding.severity),
        title=finding.title[:120],
        detail=finding.detail,
        quote=finding.quote,
        source=finding.source,
        suggestion=finding.suggestion,
    )


def _recompute(db: Session, contract: Contract) -> None:
    """风险条数、最高严重度、总分、等级，全部从库里的风险行重算。

    不增量维护：语义复核会删掉并重写模型条目，增量计数一定会漂。
    """
    rows = list(
        db.scalars(select(ContractRisk).where(ContractRisk.contract_id == contract.id))
    )
    severities = [item.severity.value for item in rows]
    score = scoring.score_of(severities)
    contract.risk_count = len(rows)
    contract.high_risk_count = sum(1 for item in severities if item == "high")
    contract.risk_score = score
    contract.risk_level = ContractRiskLevel(scoring.level_of(score))


def _contract_read(contract: Contract) -> ContractRead:
    rule_score = scoring.score_of(
        [item.severity.value for item in contract.risks if item.source == "rule"]
    )
    semantic_score = scoring.score_of(
        [item.severity.value for item in contract.risks if item.source == "llm"]
    )
    return ContractRead(
        id=contract.id,
        filename=contract.filename,
        content_type=contract.content_type,
        size_bytes=contract.size_bytes,
        status=contract.status.value,
        extract_error=contract.extract_error,
        risk_score=contract.risk_score,
        rule_score=rule_score,
        semantic_score=semantic_score,
        risk_level=contract.risk_level.value,
        clause_count=contract.clause_count,
        risk_count=contract.risk_count,
        high_risk_count=contract.high_risk_count,
        semantic_checked=contract.semantic_checked,
        summary=contract.summary,
        rule_version=contract.rule_version,
        reviewed_at=contract.reviewed_at,
        created_at=contract.created_at,
        clauses=[clause_read(item) for item in sorted(contract.clauses, key=lambda row: row.ordinal)],
        risks=sorted(
            (risk_read(item) for item in contract.risks),
            key=lambda item: ({"high": 0, "medium": 1, "low": 2}[item.severity], item.id),
        ),
        approvals=[
            ApprovalRead(
                id=item.id,
                decision=item.decision,
                comment=item.comment,
                decided_by_user_id=item.decided_by_user_id,
                decided_at=item.decided_at,
                risk_snapshot=item.risk_snapshot or [],
            )
            for item in sorted(contract.approvals, key=lambda row: row.id)
        ],
    )


def now() -> datetime:
    return datetime.now(UTC)
