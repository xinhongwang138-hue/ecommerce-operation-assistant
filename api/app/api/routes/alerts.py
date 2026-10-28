from typing import Annotated

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.api.routes.auth import require_view_permission
from app.db.session import get_db
from app.models import User
from app.schemas import AlertSummaryRead
from app.services.alerts import read_alert_summary

router = APIRouter(prefix="/alerts", tags=["alerts"])
SessionDep = Annotated[Session, Depends(get_db)]


@router.get("/summary", response_model=AlertSummaryRead)
def get_alert_summary(
    db: SessionDep,
    _current_user: Annotated[User, Depends(require_view_permission)],
) -> dict:
    """待办中心汇总：低库存、竞品变化、失败任务、待审批（只读，viewer 及以上）。"""
    return read_alert_summary(db)
