"""直播切片接口。

五个端点。上传一场转写会同步算完候选——直播转写是几千字的文本，规则跑一遍
不到 50 毫秒，没有理由做成异步任务再让前端轮询。
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session

from app.api.routes.auth import require_edit_permission, require_view_permission, workspace_scope
from app.db.session import get_db
from app.live import service as live_service
from app.live.schemas import (
    ClipRead,
    DecideRequest,
    SessionCreate,
    SessionListItem,
    SessionRead,
    SummaryRead,
)
from app.models import User

router = APIRouter(prefix="/live", tags=["live"])

SessionDep = Annotated[Session, Depends(get_db)]


def _workspace(db: Session) -> int:
    workspace_id = workspace_scope(db)
    if workspace_id is None:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN, detail="Workspace context is required"
        )
    return int(workspace_id)


def _translate(exc: live_service.LiveError) -> HTTPException:
    return HTTPException(status_code=exc.status_code, detail=exc.message)


@router.get("/summary", response_model=SummaryRead)
def get_summary(
    db: SessionDep, current_user: Annotated[User, Depends(require_view_permission)]
) -> SummaryRead:
    return live_service.summary(db, _workspace(db))


@router.get("/sessions", response_model=list[SessionListItem])
def list_sessions(
    db: SessionDep,
    current_user: Annotated[User, Depends(require_view_permission)],
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
) -> list[SessionListItem]:
    return live_service.list_sessions(db, _workspace(db), limit=limit)


@router.post("/sessions", response_model=SessionRead, status_code=status.HTTP_201_CREATED)
def create_session(
    payload: SessionCreate,
    db: SessionDep,
    current_user: Annotated[User, Depends(require_edit_permission)],
) -> SessionRead:
    try:
        return live_service.create_session(
            db, _workspace(db), user_id=current_user.id, payload=payload
        )
    except live_service.LiveError as exc:
        raise _translate(exc) from exc


@router.get("/sessions/{session_id}", response_model=SessionRead)
def get_session(
    session_id: int,
    db: SessionDep,
    current_user: Annotated[User, Depends(require_view_permission)],
) -> SessionRead:
    try:
        return live_service.get_session(db, _workspace(db), session_id)
    except live_service.LiveError as exc:
        raise _translate(exc) from exc


@router.post("/sessions/{session_id}/recut", response_model=SessionRead)
def recut(
    session_id: int,
    db: SessionDep,
    current_user: Annotated[User, Depends(require_edit_permission)],
) -> SessionRead:
    """按当前规则重挑。已定稿的候选保留——人的判断比机器贵。"""
    try:
        return live_service.recut(db, _workspace(db), session_id)
    except live_service.LiveError as exc:
        raise _translate(exc) from exc


@router.post("/clips/{clip_id}/decide", response_model=ClipRead)
def decide(
    clip_id: int,
    payload: DecideRequest,
    db: SessionDep,
    current_user: Annotated[User, Depends(require_edit_permission)],
) -> ClipRead:
    try:
        return live_service.decide(db, _workspace(db), clip_id, status=payload.status)
    except live_service.LiveError as exc:
        raise _translate(exc) from exc
