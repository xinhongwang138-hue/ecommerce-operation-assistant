"""销售考核（P09）接口：导入 → 评分 → 排行榜 / 个人报告 / 低分导出。

权限：浏览与报告走 view（主管看榜不该要求改数据权限），导入与重算走 edit——
重算会改写历史分数，这是"改变考核结果"的动作，必须留权限痕迹。
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session

from app.api.routes.auth import require_edit_permission, require_view_permission, workspace_scope
from app.db.session import get_db
from app.models import User
from app.qa import service
from app.qa.schemas import (
    AgentImportRead,
    ImportRead,
    ImportRequest,
    LeaderboardRead,
    LowScoreExport,
    ReportRead,
    SessionListRead,
    SummaryRead,
)

router = APIRouter(prefix="/qa", tags=["qa"])

SessionDep = Annotated[Session, Depends(get_db)]
ViewUserDep = Annotated[User, Depends(require_view_permission)]
EditUserDep = Annotated[User, Depends(require_edit_permission)]


def _workspace(db: Session) -> int:
    workspace_id = workspace_scope(db)
    if workspace_id is None:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="销售考核必须绑定 Workspace",
        )
    return int(workspace_id)


@router.get("/summary", response_model=SummaryRead)
def read_summary(
    db: SessionDep,
    _user: ViewUserDep,
    days: int = Query(default=30, ge=1, le=365),
) -> SummaryRead:
    """周期内的会话数、均分、等级分布、红线会话数。"""
    return service.summary(db, _workspace(db), days=days)


@router.get("/sessions", response_model=SessionListRead)
def read_sessions(
    db: SessionDep,
    _user: ViewUserDep,
    limit: int = Query(default=20, ge=1, le=100),
    days: int | None = Query(default=None, ge=1, le=365),
) -> SessionListRead:
    return service.list_sessions(db, _workspace(db), limit=limit, days=days)


@router.get("/sessions/{session_id}", response_model=ReportRead)
def read_report(session_id: int, db: SessionDep, _user: ViewUserDep) -> ReportRead:
    """单会话个人报告：逐项得分 + 证据原句 + 最该改的一项。

    跨租户一律 404，不区分"不存在"与"不属于你"。
    """
    report = service.get_report(db, _workspace(db), session_id)
    if report is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Session not found")
    return report


@router.post("/import", response_model=ImportRead, status_code=status.HTTP_201_CREATED)
def import_sessions(payload: ImportRequest, db: SessionDep, _user: EditUserDep) -> ImportRead:
    """导入一批会话并立即评分。

    幂等键是 `(workspace, external_id)`：重复导入只更新，不会让榜单的分母变大。
    被跳过的会话会出现在 `warnings` 里，不静默吞掉。
    """
    return service.import_sessions(db, _workspace(db), payload)


@router.post("/import/agent-conversations", response_model=AgentImportRead)
def import_from_agent(
    db: SessionDep,
    _user: EditUserDep,
    limit: int = Query(default=200, ge=1, le=500),
) -> AgentImportRead:
    """从本仓 `agent_conversations` 拉会话（`user → customer`、`assistant → agent`）。

    没有会话时 `imported=0` 并给出原因，不造样本。
    """
    imported, skipped, scanned, reason = service.import_from_agent_conversations(
        db, _workspace(db), limit=limit
    )
    return AgentImportRead(imported=imported, skipped=skipped, scanned=scanned, reason=reason)


@router.post("/rescore")
def rescore(db: SessionDep, _user: EditUserDep) -> dict:
    """按当前规则重算全部会话。规则版本变了必须显式调它，历史分数不会自己变。"""
    return {"rescored": service.rescore(db, _workspace(db))}


@router.get("/leaderboard", response_model=LeaderboardRead)
def read_leaderboard(
    db: SessionDep,
    _user: ViewUserDep,
    days: int = Query(default=30, ge=1, le=365),
) -> LeaderboardRead:
    return service.leaderboard(db, _workspace(db), days=days)


@router.get("/export/low-score", response_model=LowScoreExport)
def export_low_scores(
    db: SessionDep,
    _user: ViewUserDep,
    threshold: float = Query(default=60.0, ge=0, le=100),
) -> LowScoreExport:
    """低分样本导出：带原文与问题项，可直接作为复盘素材或微调数据集。"""
    return service.export_low_scores(db, _workspace(db), threshold=threshold)
