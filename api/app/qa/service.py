"""质检的服务层：导入、评分、榜单、报告、导出。

两条纪律贯穿全文：

1. **导入是幂等的**。`(workspace_id, external_id)` 是业务主键，重复导入只更新不新增。
   排行榜的分母一旦能靠"再导一次"变大，这个榜就没人信了。
2. **评分只在两处发生**：导入之后、显式重算。读接口不顺手改数据——否则打开一次
   报告就把历史分数刷新了，考核结果会"自己变"。
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

from sqlalchemy import case, delete, func, select
from sqlalchemy.orm import Session, selectinload

from app.models import (
    AgentConversation,
    AgentConversationMessage,
    QaMessage,
    QaRole,
    QaScore,
    QaSession,
    QaSessionSource,
)
from app.qa import rules
from app.qa.schemas import (
    ImportRequest,
    ImportRead,
    LeaderboardRead,
    LeaderboardRow,
    LowScoreExport,
    LowScoreItem,
    RedlineEvidenceRead,
    MessageRead,
    ReportRead,
    RuleScoreRead,
    ScoreRead,
    SessionListItem,
    SessionListRead,
    SummaryRead,
)

# 上游角色叫法不统一，映射表在这里集中维护。归不出角色的一句话直接跳过，
# 不猜——把客户的话当成坐席的，会让"响应完整性"这一项凭空满分。
ROLE_ALIASES = {
    "customer": QaRole.CUSTOMER,
    "user": QaRole.CUSTOMER,
    "buyer": QaRole.CUSTOMER,
    "客户": QaRole.CUSTOMER,
    "买家": QaRole.CUSTOMER,
    "agent": QaRole.AGENT,
    "assistant": QaRole.AGENT,
    "bot": QaRole.AGENT,
    "service": QaRole.AGENT,
    "客服": QaRole.AGENT,
    "坐席": QaRole.AGENT,
}

MAX_EXPORT_ITEMS = 200
DEFAULT_LOW_SCORE = 60.0


def normalize_role(raw: str) -> QaRole | None:
    return ROLE_ALIASES.get((raw or "").strip().lower())


def import_sessions(db: Session, workspace_id: int, payload: ImportRequest) -> ImportRead:
    """导入一批会话：先落原文，再逐条评分。"""
    imported = updated = skipped = scored = 0
    warnings: list[str] = []

    for item in payload.sessions:
        turns = [(normalize_role(message.role), message) for message in item.messages]
        valid = [(role, message) for role, message in turns if role is not None]
        dropped = len(turns) - len(valid)
        if dropped:
            warnings.append(f"{item.external_id}：{dropped} 条消息角色无法识别，已跳过")
        if not valid:
            skipped += 1
            warnings.append(f"{item.external_id}：没有任何角色可识别的消息，整条跳过")
            continue
        if not any(role is QaRole.CUSTOMER for role, _ in valid):
            skipped += 1
            warnings.append(f"{item.external_id}：没有客户发言，无法质检，整条跳过")
            continue

        started_at = item.started_at or next(
            (message.sent_at for _role, message in valid if message.sent_at is not None),
            None,
        )
        if started_at is None:
            # 没有起点时间就无法进入任何时间窗口。不编一个"当前时间"——那会让
            # 一条三年前的会话混进本月榜单。
            skipped += 1
            warnings.append(f"{item.external_id}：缺少会话开始时间与消息时间，无法归入周期，整条跳过")
            continue

        session = db.scalar(
            select(QaSession).where(
                QaSession.workspace_id == workspace_id,
                QaSession.external_id == item.external_id,
            )
        )
        if session is None:
            session = QaSession(
                workspace_id=workspace_id,
                external_id=item.external_id,
                agent_name=item.agent_name.strip(),
                channel=item.channel.strip() or "manual",
                source_kind=QaSessionSource.IMPORT,
                started_at=_as_utc(started_at),
                message_count=0,
            )
            db.add(session)
            db.flush()
            imported += 1
        else:
            session.agent_name = item.agent_name.strip()
            session.channel = item.channel.strip() or "manual"
            session.started_at = _as_utc(started_at)
            # 重新导入即"以这次为准"：先清空旧消息，避免上一版多出来的句子残留成证据
            db.execute(delete(QaMessage).where(QaMessage.session_id == session.id))
            updated += 1

        for seq, (role, message) in enumerate(valid):
            db.add(
                QaMessage(
                    workspace_id=workspace_id,
                    session_id=session.id,
                    seq=seq,
                    role=role,
                    content=message.content,
                    sent_at=_as_utc(message.sent_at) if message.sent_at else None,
                )
            )
        session.message_count = len(valid)
        db.flush()

        _score_and_store(db, session)
        scored += 1

    db.commit()
    return ImportRead(
        imported=imported, updated=updated, skipped=skipped, scored=scored, warnings=warnings
    )


def import_from_agent_conversations(db: Session, workspace_id: int, limit: int = 200) -> tuple[int, int, int, str]:
    """从本仓的 `agent_conversations` 拉会话。

    角色映射按 P09 原定义：`user → customer`、`assistant → agent`。本仓没有人工
    坐席，因此统一挂在"AI 助手"名下——这不是虚构考核对象，而是事实：这些回复
    确实由 AI 发出。没有会话时返回 0，不造样本。
    """
    conversations = list(
        db.scalars(
            select(AgentConversation)
            .where(AgentConversation.workspace_id == workspace_id)
            .order_by(AgentConversation.id.desc())
            .limit(limit)
        )
    )

    if not conversations:
        return 0, 0, 0, "本仓没有客服会话，无法拉取（不是导入失败，是没有数据）"

    imported = skipped = 0
    for conversation in conversations:
        messages = list(
            db.scalars(
                select(AgentConversationMessage)
                .where(AgentConversationMessage.conversation_id == conversation.id)
                .order_by(AgentConversationMessage.id)
            )
        )
        if not messages:
            skipped += 1
            continue
        session = db.scalar(
            select(QaSession).where(
                QaSession.workspace_id == workspace_id,
                QaSession.external_id == f"agent-{conversation.id}",
            )
        )
        if session is None:
            session = QaSession(
                workspace_id=workspace_id,
                external_id=f"agent-{conversation.id}",
                agent_name="AI 助手",
                channel="agent",
                source_kind=QaSessionSource.AGENT,
                started_at=_as_utc(conversation.created_at),
                message_count=0,
            )
            db.add(session)
            db.flush()
        else:
            db.execute(delete(QaMessage).where(QaMessage.session_id == session.id))

        kept = 0
        for message in messages:
            role = QaRole.CUSTOMER if message.role == "user" else QaRole.AGENT
            db.add(
                QaMessage(
                    workspace_id=workspace_id,
                    session_id=session.id,
                    seq=kept,
                    role=role,
                    content=message.content,
                    sent_at=_as_utc(message.created_at),
                )
            )
            kept += 1
        session.message_count = kept
        db.flush()
        _score_and_store(db, session)
        imported += 1

    db.commit()
    reason = "" if imported else "拉到了会话但没有可用消息，全部跳过"
    return imported, skipped, len(conversations), reason


def rescore(db: Session, workspace_id: int) -> int:
    """按当前规则重算本租户全部会话。规则改了必须显式调它。"""
    sessions = list(
        db.scalars(
            select(QaSession)
            .where(QaSession.workspace_id == workspace_id)
            .options(selectinload(QaSession.messages))
        )
    )
    for session in sessions:
        _score_and_store(db, session)
    db.commit()
    return len(sessions)


def list_sessions(db: Session, workspace_id: int, *, limit: int = 20, days: int | None = None) -> SessionListRead:
    statement = (
        select(QaSession)
        .where(QaSession.workspace_id == workspace_id)
        .order_by(QaSession.started_at.desc(), QaSession.id.desc())
        .limit(limit)
        .options(selectinload(QaSession.score))
    )
    if days:
        statement = statement.where(QaSession.started_at >= _window_start(days))
    sessions = list(db.scalars(statement))

    total = db.scalar(
        select(func.count()).select_from(QaSession).where(QaSession.workspace_id == workspace_id)
    ) or 0
    average = db.scalar(
        select(func.avg(QaScore.total)).where(QaScore.workspace_id == workspace_id)
    )
    return SessionListRead(
        items=[_session_item(session) for session in sessions],
        total=int(total),
        average=round(float(average), 1) if average is not None else None,
    )


def get_report(db: Session, workspace_id: int, session_id: int) -> ReportRead | None:
    session = db.scalar(
        select(QaSession)
        .where(QaSession.workspace_id == workspace_id, QaSession.id == session_id)
        .options(selectinload(QaSession.messages), selectinload(QaSession.score))
    )
    if session is None:
        return None
    score = session.score
    breakdown = [RuleScoreRead(**item) for item in (score.breakdown if score else [])]
    weakest = _weakest(breakdown)
    redline_evidence = [
        RedlineEvidenceRead(**item) for item in rules.redline_evidence(_utterances(session))
    ]
    # 红线建议排在最前：踩线不是"下一轮优化项"，是这一轮就要停的事。
    suggestions = [rules.REDLINE_ADVICE[evidence.key] for evidence in redline_evidence]
    suggestions += [item.advice for item in breakdown if item.advice and not item.passed][:3]
    return ReportRead(
        session=_session_item(session),
        messages=[
            MessageRead(seq=item.seq, role=item.role.value, content=item.content, sent_at=item.sent_at)
            for item in session.messages
        ],
        score=_score_read(score),
        redline_evidence=redline_evidence,
        weakest=weakest,
        suggestions=suggestions,
    )


def leaderboard(db: Session, workspace_id: int, *, days: int = 30) -> LeaderboardRead:
    """按坐席聚合。平均分降序，分数相同看会话量——样本太少的平均分不该排第一。"""
    start = _window_start(days)
    rows = db.execute(
        select(
            QaSession.agent_name,
            func.count(QaScore.id),
            func.avg(QaScore.total),
            func.max(QaScore.total),
            func.min(QaScore.total),
            func.sum(case((QaScore.grade == "A", 1), else_=0)),
            func.sum(case((QaScore.redline.is_(True), 1), else_=0)),
        )
        .join(QaScore, QaScore.session_id == QaSession.id)
        .where(QaSession.workspace_id == workspace_id, QaSession.started_at >= start)
        .group_by(QaSession.agent_name)
    ).all()

    items = [
        LeaderboardRow(
            agent_name=name,
            sessions=int(sessions),
            average=round(float(average), 1),
            best=round(float(best), 1),
            worst=round(float(worst), 1),
            a_count=int(a_count or 0),
            redline_count=int(redline_count or 0),
        )
        for name, sessions, average, best, worst, a_count, redline_count in rows
    ]
    items.sort(key=lambda row: (-row.average, -row.sessions, row.agent_name))
    return LeaderboardRead(days=days, rows=items, total_sessions=sum(row.sessions for row in items))


def summary(db: Session, workspace_id: int, *, days: int = 30) -> SummaryRead:
    start = _window_start(days)
    sessions = db.scalar(
        select(func.count())
        .select_from(QaSession)
        .where(QaSession.workspace_id == workspace_id, QaSession.started_at >= start)
    ) or 0
    scored, average, redlines = db.execute(
        select(
            func.count(QaScore.id),
            func.avg(QaScore.total),
            func.sum(case((QaScore.redline.is_(True), 1), else_=0)),
        )
        .join(QaSession, QaSession.id == QaScore.session_id)
        .where(QaSession.workspace_id == workspace_id, QaSession.started_at >= start)
    ).one()

    distribution = {grade: 0 for grade in ("A", "B", "C", "D")}
    for grade, count in db.execute(
        select(QaScore.grade, func.count(QaScore.id))
        .join(QaSession, QaSession.id == QaScore.session_id)
        .where(QaSession.workspace_id == workspace_id, QaSession.started_at >= start)
        .group_by(QaScore.grade)
    ).all():
        distribution[grade] = int(count)

    agents = db.scalar(
        select(func.count(func.distinct(QaSession.agent_name))).where(
            QaSession.workspace_id == workspace_id, QaSession.started_at >= start
        )
    ) or 0

    return SummaryRead(
        sessions=int(sessions),
        scored=int(scored or 0),
        unscored=int(sessions) - int(scored or 0),
        average=round(float(average), 1) if average is not None else None,
        agents=int(agents),
        redline_sessions=int(redlines or 0),
        grade_distribution=distribution,
        days=days,
        rule_version=rules.RULE_VERSION,
    )


def export_low_scores(
    db: Session, workspace_id: int, *, threshold: float = DEFAULT_LOW_SCORE
) -> LowScoreExport:
    """导出低分样本。这是 P09 唯一的对外产物：回流成训练数据/复盘素材。"""
    rows = db.execute(
        select(QaSession, QaScore)
        .join(QaScore, QaScore.session_id == QaSession.id)
        .where(
            QaSession.workspace_id == workspace_id,
            (QaScore.total < Decimal(str(threshold))) | (QaScore.redline.is_(True)),
        )
        .order_by(QaScore.total.asc(), QaSession.started_at.desc())
        .limit(MAX_EXPORT_ITEMS)
    ).all()

    items: list[LowScoreItem] = []
    for session, score in rows:
        messages = list(
            db.scalars(
                select(QaMessage).where(QaMessage.session_id == session.id).order_by(QaMessage.seq)
            )
        )
        breakdown = [RuleScoreRead(**item) for item in (score.breakdown or [])]
        items.append(
            LowScoreItem(
                external_id=session.external_id,
                session_id=session.id,
                agent_name=session.agent_name,
                channel=session.channel,
                started_at=session.started_at,
                total=float(score.total),
                grade=score.grade,
                redline=score.redline,
                redline_rules=list(score.redline_rules or []),
                redline_evidence=[
                    RedlineEvidenceRead(**evidence)
                    for evidence in rules.redline_evidence(_utterances(session))
                ],
                messages=[
                    MessageRead(seq=item.seq, role=item.role.value, content=item.content, sent_at=item.sent_at)
                    for item in messages
                ],
                issues=[item for item in breakdown if not item.passed],
            )
        )
    return LowScoreExport(
        generated_at=datetime.now(UTC),
        threshold=threshold,
        rule_version=rules.RULE_VERSION,
        items=items,
    )


def _utterances(session: QaSession) -> list[rules.Utterance]:
    return [
        rules.Utterance(
            seq=item.seq,
            role=item.role.value,
            content=item.content,
            sent_at=item.sent_at,
        )
        for item in session.messages
    ]


def _score_and_store(db: Session, session: QaSession) -> QaScore:
    card = rules.score_session(_utterances(session))
    score = db.scalar(select(QaScore).where(QaScore.session_id == session.id))
    if score is None:
        score = QaScore(
            workspace_id=session.workspace_id,
            session_id=session.id,
            total=rules.to_decimal(card.total),
            grade=card.grade,
            breakdown=card.as_dict()["breakdown"],
            redline=card.redline,
            redline_rules=card.redline_rules,
            rule_version=card.rule_version,
            scored_at=datetime.now(UTC),
        )
        db.add(score)
    else:
        score.total = rules.to_decimal(card.total)
        score.grade = card.grade
        score.breakdown = card.as_dict()["breakdown"]
        score.redline = card.redline
        score.redline_rules = card.redline_rules
        score.rule_version = card.rule_version
        score.scored_at = datetime.now(UTC)
    db.flush()
    return score


def _session_item(session: QaSession) -> SessionListItem:
    score = session.score
    return SessionListItem(
        id=session.id,
        external_id=session.external_id,
        agent_name=session.agent_name,
        channel=session.channel,
        source_kind=session.source_kind.value,
        started_at=session.started_at,
        message_count=session.message_count,
        total=float(score.total) if score else None,
        grade=score.grade if score else None,
        redline=bool(score.redline) if score else False,
    )


def _score_read(score: QaScore | None) -> ScoreRead:
    if score is None:
        return ScoreRead(total=0.0, grade="D", redline=False, rule_version=rules.RULE_VERSION)
    return ScoreRead(
        total=float(score.total),
        grade=score.grade,
        redline=bool(score.redline),
        redline_rules=list(score.redline_rules or []),
        rule_version=score.rule_version,
        scored_at=score.scored_at,
        breakdown=[RuleScoreRead(**item) for item in (score.breakdown or [])],
    )


def _weakest(breakdown: list[RuleScoreRead]) -> RuleScoreRead | None:
    """失分最多的一项（不是得分最低的一项：满分 10 的 0 分丢的是 10 分）。"""
    if not breakdown:
        return None
    return max(breakdown, key=lambda item: item.max_score - item.score)


def _window_start(days: int) -> datetime:
    return datetime.now(UTC) - timedelta(days=max(1, days))


def _as_utc(value: datetime) -> datetime:
    """统一成 UTC。上游给本地时间时必须自己带时区，否则这里按 naive 处理。"""
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)
