"""合同审批接口。

六个端点，把"上传"和"复核"分开是刻意的：上传要快、要能重试，模型调用慢且会失败。
如果合成一个接口，模型超时会让用户以为文件没传上去，然后重复上传。

出口约定：
  415  格式不支持（.doc/.xls/扫描件）
  422  能打开但抽不出文字、决策值不合法、高风险通过却没写理由
  400  模型没配（语义复核）
  502  模型返回不可用（语义复核）
  404  合同不存在，或不属于当前租户（同一个响应，不区分）
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, File, HTTPException, Query, UploadFile, status
from sqlalchemy.orm import Session

from app.api.routes.auth import require_edit_permission, require_view_permission, workspace_scope
from app.contracts import scoring
from app.contracts import service as contract_service
from app.contracts.schemas import (
    ApprovalRead,
    ApprovalRequest,
    ContractRead,
    SemanticReviewRead,
    SummaryRead,
    UploadRead,
)
from app.db.session import get_db
from app.models import User

router = APIRouter(prefix="/contracts", tags=["contracts"])

SessionDep = Annotated[Session, Depends(get_db)]


def _workspace(db: Session) -> int:
    workspace_id = workspace_scope(db)
    if workspace_id is None:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN, detail="Workspace context is required"
        )
    return int(workspace_id)


def _translate(exc: contract_service.ContractError) -> HTTPException:
    return HTTPException(status_code=exc.status_code, detail=exc.message)


@router.get("/summary", response_model=SummaryRead)
def get_summary(
    db: SessionDep, current_user: Annotated[User, Depends(require_view_permission)]
) -> SummaryRead:
    return contract_service.summary(db, _workspace(db))


@router.get("/scoring", response_model=list[dict])
def get_scoring(
    current_user: Annotated[User, Depends(require_view_permission)],
) -> list[dict]:
    """分值口径。审批人要看得到"34 分是怎么来的"，否则分数就只是个数字。"""
    return scoring.explain()


@router.get("", response_model=list[ContractRead])
def list_contracts(
    db: SessionDep,
    current_user: Annotated[User, Depends(require_view_permission)],
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
) -> list[ContractRead]:
    return contract_service.list_contracts(db, _workspace(db), limit=limit)


@router.post("/upload", response_model=UploadRead, status_code=status.HTTP_201_CREATED)
def upload_contract(
    db: SessionDep,
    current_user: Annotated[User, Depends(require_edit_permission)],
    file: Annotated[UploadFile, File()],
) -> UploadRead:
    """上传并跑完规则阶段。语义复核要再调 `/contracts/{id}/review`。"""
    workspace_id = _workspace(db)
    if not file.filename:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="缺少文件名")
    data = file.file.read()
    try:
        return contract_service.upload(
            db,
            workspace_id,
            user_id=current_user.id,
            filename=file.filename,
            content_type=file.content_type or "",
            data=data,
        )
    except contract_service.ContractError as exc:
        raise _translate(exc) from exc


@router.get("/{contract_id}", response_model=ContractRead)
def get_contract(
    contract_id: int,
    db: SessionDep,
    current_user: Annotated[User, Depends(require_view_permission)],
) -> ContractRead:
    try:
        return contract_service.get(db, _workspace(db), contract_id)
    except contract_service.ContractError as exc:
        raise _translate(exc) from exc


@router.post("/{contract_id}/review", response_model=SemanticReviewRead)
def review_contract(
    contract_id: int,
    db: SessionDep,
    current_user: Annotated[User, Depends(require_edit_permission)],
) -> SemanticReviewRead:
    """第二阶段语义复核。可重复调用：模型条目整体替换，规则条目不动。"""
    try:
        return contract_service.review_semantic(db, _workspace(db), contract_id)
    except contract_service.ContractError as exc:
        raise _translate(exc) from exc


@router.post("/{contract_id}/approvals", response_model=ApprovalRead, status_code=status.HTTP_201_CREATED)
def decide(
    contract_id: int,
    payload: ApprovalRequest,
    db: SessionDep,
    current_user: Annotated[User, Depends(require_edit_permission)],
) -> ApprovalRead:
    """留痕审批。只追加：改结论要再写一条，历史都在。"""
    try:
        return contract_service.approve(
            db,
            _workspace(db),
            contract_id,
            user_id=current_user.id,
            decision=payload.decision,
            comment=payload.comment,
        )
    except contract_service.ContractError as exc:
        raise _translate(exc) from exc
