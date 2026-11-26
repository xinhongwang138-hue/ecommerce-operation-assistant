"""AI 电商客服（P01）接口。

权限：对话走 edit（会写会话与工单），查工单与 FAQ 走 view。
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session

from app.api.routes.auth import require_edit_permission, require_view_permission, workspace_scope
from app.cs import service
from app.cs.schemas import (
    ChatRead,
    ChatRequest,
    CsSummary,
    FaqItemRead,
    HandoffRequest,
    TicketRead,
)
from app.db.session import get_db
from app.models import AgentConversation, User

router = APIRouter(prefix="/cs", tags=["cs"])

SessionDep = Annotated[Session, Depends(get_db)]
ViewUserDep = Annotated[User, Depends(require_view_permission)]
EditUserDep = Annotated[User, Depends(require_edit_permission)]


def _workspace(db: Session) -> int:
    workspace_id = workspace_scope(db)
    if workspace_id is None:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="客服台必须绑定 Workspace",
        )
    return int(workspace_id)


@router.get("/summary", response_model=CsSummary)
def read_summary(db: SessionDep, _user: ViewUserDep) -> CsSummary:
    """分流分布与工单情况。转人工占比是这套系统最该盯的一个数。"""
    return service.summary(db, _workspace(db))


@router.get("/faq", response_model=list[FaqItemRead])
def read_faq(_user: ViewUserDep) -> list[FaqItemRead]:
    """内置 FAQ 清单。答得上来什么、答不上来什么，用户应该能直接看到。"""
    return service.list_faq()


@router.post("/chat", response_model=ChatRead)
def chat(payload: ChatRequest, db: SessionDep, user: EditUserDep) -> ChatRead:
    """一次问答：三轨道分流 → 取依据 → 写回复 → 落会话（该转人工时建工单）。

    返回体里的 `track` 与 `reason` 是给运维看的：为什么走了这条路，系统要说得清。
    """
    return service.chat(
        db,
        _workspace(db),
        user_id=user.id,
        message=payload.message,
        conversation_id=payload.conversation_id,
    )


@router.post("/handoff", response_model=TicketRead, status_code=status.HTTP_201_CREATED)
def handoff(payload: HandoffRequest, db: SessionDep, user: EditUserDep) -> TicketRead:
    """手动转人工。同一会话已有未关闭工单时返回那一张，不重复建单。"""
    workspace_id = _workspace(db)
    conversation = db.get(AgentConversation, payload.conversation_id)
    if conversation is None or conversation.workspace_id != workspace_id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Conversation not found")

    ticket = service.create_ticket(
        db,
        workspace_id,
        conversation_id=payload.conversation_id,
        user_id=None,
        reason=payload.reason or "客户主动请求人工服务",
        trigger="manual",
        priority="normal",
    )
    db.commit()
    created = service.ticket_by_id(db, workspace_id, ticket.id)
    if created is None:  # 理论上不可达；真出现了也不能返回别的工单
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Ticket vanished")
    return created


@router.get("/tickets", response_model=list[TicketRead])
def read_tickets(
    db: SessionDep,
    _user: ViewUserDep,
    status_filter: str | None = Query(default=None, alias="status"),
    limit: int = Query(default=20, ge=1, le=100),
) -> list[TicketRead]:
    if status_filter is not None and status_filter not in {"open", "claimed", "closed"}:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="status 只支持 open / claimed / closed",
        )
    return service.list_tickets(db, _workspace(db), status=status_filter, limit=limit)


@router.post("/tickets/{ticket_id}/close", response_model=TicketRead)
def close_ticket(ticket_id: int, db: SessionDep, _user: EditUserDep) -> TicketRead:
    """跨租户一律 404，不区分"不存在"与"不属于你"。"""
    ticket = service.close_ticket(db, _workspace(db), ticket_id)
    if ticket is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Ticket not found")
    return ticket
