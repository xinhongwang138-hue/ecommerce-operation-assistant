"""直播切片服务层：收转写 → 挑候选 → 落库 → 人工定稿。

切片在**上传时一次算完并落库**，不在每次读的时候重算。理由和 P09 一样：
规则改了，旧场次的候选不该跟着变——否则剪辑师昨天看中的那条今天就不见了。
要按新规则重挑，走显式的 `recut`。
"""

from __future__ import annotations

from sqlalchemy import func, select
from sqlalchemy.orm import Session, selectinload

from app.live import rules
from app.live.schemas import (
    ClipRead,
    SessionCreate,
    SessionListItem,
    SessionRead,
    SummaryRead,
)
from app.models import LiveClip, LiveClipStatus, LiveSession, LiveSessionStatus


class LiveError(Exception):
    def __init__(self, message: str, status_code: int = 400) -> None:
        super().__init__(message)
        self.message = message
        self.status_code = status_code


def create_session(
    db: Session, workspace_id: int, *, user_id: int | None, payload: SessionCreate
) -> SessionRead:
    segments = rules.parse_transcript([item.model_dump() for item in payload.transcript])
    if not segments:
        raise LiveError("转写里没有可用的句子（每句都需要 start 与 text）", status_code=422)

    duration = max(item.end for item in segments)
    session = LiveSession(
        workspace_id=workspace_id,
        created_by_user_id=user_id,
        title=payload.title[:200],
        product_name=(payload.product_name or "")[:200],
        duration_seconds=duration,
        transcript=[
            {"start": item.start, "end": item.end, "text": item.text} for item in segments
        ],
        status=LiveSessionStatus.TRANSCRIBED,
        rule_version=rules.RULE_VERSION,
    )
    db.add(session)
    db.flush()

    candidates = rules.cut(segments)
    db.add_all(
        [
            LiveClip(
                workspace_id=workspace_id,
                session_id=session.id,
                start_seconds=item.start,
                end_seconds=item.end,
                title=item.title,
                hook=item.hook,
                transcript=item.transcript,
                score=item.score,
                reasons=item.reasons,
                status=LiveClipStatus.SUGGESTED,
            )
            for item in candidates
        ]
    )
    session.clip_count = len(candidates)
    session.status = LiveSessionStatus.CUT if candidates else LiveSessionStatus.TRANSCRIBED
    db.commit()
    db.refresh(session)
    return _session_read(_load(db, workspace_id, session.id))


def recut(db: Session, workspace_id: int, session_id: int) -> SessionRead:
    """按当前规则重挑。会清掉未定稿的候选，**已通过/已驳回的保留**。

    保留已定稿的那部分，是因为人工判断的成本比机器高——不能因为改了阈值
    就把人已经看过的东西丢掉。
    """
    session = _load(db, workspace_id, session_id)
    decided = [item for item in session.clips if item.status is not LiveClipStatus.SUGGESTED]

    for item in list(session.clips):
        if item.status is LiveClipStatus.SUGGESTED:
            db.delete(item)
    db.flush()

    segments = rules.parse_transcript(session.transcript or [])
    candidates = rules.cut(segments)
    existing_span = [(item.start_seconds, item.end_seconds) for item in decided]
    for item in candidates:
        if any(_overlaps(item.start, item.end, start, end) for start, end in existing_span):
            continue
        db.add(
            LiveClip(
                workspace_id=workspace_id,
                session_id=session.id,
                start_seconds=item.start,
                end_seconds=item.end,
                title=item.title,
                hook=item.hook,
                transcript=item.transcript,
                score=item.score,
                reasons=item.reasons,
                status=LiveClipStatus.SUGGESTED,
            )
        )
    db.flush()
    session.rule_version = rules.RULE_VERSION
    session.clip_count = len(decided) + len(
        [item for item in candidates if not any(_overlaps(item.start, item.end, s, e) for s, e in existing_span)]
    )
    db.commit()
    return _session_read(_load(db, workspace_id, session_id))


def decide(db: Session, workspace_id: int, clip_id: int, *, status: str) -> ClipRead:
    if status not in (LiveClipStatus.APPROVED.value, LiveClipStatus.REJECTED.value):
        raise LiveError("status 只接受 approved / rejected", status_code=400)
    clip = db.scalar(
        select(LiveClip).where(LiveClip.id == clip_id, LiveClip.workspace_id == workspace_id)
    )
    if clip is None:
        raise LiveError("切片不存在", status_code=404)

    clip.status = LiveClipStatus(status)
    # 驳回也是一次定稿，同样要留时间。此前只给 approved 记，结果"这条什么时候被否的"
    # 答不上来——审批链路上"没通过"和"还没人看"必须是两件可区分的事。
    # 时钟取数据库的，和 created_at 同源。
    clip.decided_at = func.now()
    db.flush()

    session = db.scalar(
        select(LiveSession).where(LiveSession.id == clip.session_id)
    )
    if session is not None:
        decided = db.scalars(
            select(LiveClip).where(
                LiveClip.session_id == session.id,
                LiveClip.status != LiveClipStatus.SUGGESTED,
            )
        ).all()
        if decided:
            session.status = LiveSessionStatus.REVIEWED
    db.commit()
    db.refresh(clip)
    return ClipRead.model_validate(clip)


def get_session(db: Session, workspace_id: int, session_id: int) -> SessionRead:
    return _session_read(_load(db, workspace_id, session_id))


def list_sessions(db: Session, workspace_id: int, *, limit: int = 20) -> list[SessionListItem]:
    statement = (
        select(LiveSession)
        .where(LiveSession.workspace_id == workspace_id)
        .order_by(LiveSession.id.desc())
        .limit(limit)
    )
    return [SessionListItem.model_validate(item) for item in db.scalars(statement)]


def summary(db: Session, workspace_id: int) -> SummaryRead:
    sessions = list(
        db.scalars(
            select(LiveSession)
            .where(LiveSession.workspace_id == workspace_id)
            .options(selectinload(LiveSession.clips))
        ).unique()
    )
    clips = [clip for session in sessions for clip in session.clips]
    counts = {
        LiveClipStatus.SUGGESTED.value: 0,
        LiveClipStatus.APPROVED.value: 0,
        LiveClipStatus.REJECTED.value: 0,
    }
    for item in clips:
        counts[item.status.value] = counts.get(item.status.value, 0) + 1

    return SummaryRead(
        sessions=len(sessions),
        segments=sum(len(session.transcript or []) for session in sessions),
        clips=len(clips),
        approved=counts[LiveClipStatus.APPROVED.value],
        rejected=counts[LiveClipStatus.REJECTED.value],
        average_score=round(sum(item.score for item in clips) / len(clips), 1) if clips else 0.0,
        by_status=[{"status": key, "count": value} for key, value in counts.items()],
    )


def _load(db: Session, workspace_id: int, session_id: int) -> LiveSession:
    session = db.scalar(
        select(LiveSession)
        .where(LiveSession.id == session_id, LiveSession.workspace_id == workspace_id)
        .options(selectinload(LiveSession.clips))
    )
    if session is None:
        # 不存在和不属于你同一个响应，不让人用状态码探测别的租户
        raise LiveError("直播场次不存在", status_code=404)
    return session


def _session_read(session: LiveSession) -> SessionRead:
    return SessionRead(
        id=session.id,
        title=session.title,
        product_name=session.product_name,
        duration_seconds=session.duration_seconds,
        status=session.status.value,
        clip_count=session.clip_count,
        rule_version=session.rule_version,
        created_at=session.created_at,
        clips=sorted(
            (ClipRead.model_validate(item) for item in session.clips),
            key=lambda item: (-item.score, item.start_seconds),
        ),
    )


def _overlaps(a_start: int, a_end: int, b_start: int, b_end: int) -> bool:
    return min(a_end, b_end) - max(a_start, b_start) > 0
