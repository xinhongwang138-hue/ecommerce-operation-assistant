"""模型微调平台接口。

七个端点。导出是文本流而不是文件落地——数据集动辄几十兆，把它写进 uploads/
再让浏览器去下载，等于给磁盘留一份没人清理的副本。

出口约定：
  422  没有可用样本 / 数据集里全是待修样本 / 评测用例为空
  400  status 或 sources 取值不合法
  404  数据集或任务不存在（含跨租户）
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from sqlalchemy.orm import Session

from app.api.routes.auth import require_edit_permission, require_view_permission, workspace_scope
from app.db.session import get_db
from app.finetune import service as ft_service
from app.finetune.schemas import (
    BuildRequest,
    DatasetDetail,
    DatasetRead,
    EvalRequest,
    JobCreate,
    JobRead,
    JobUpdate,
    SummaryRead,
)
from app.models import User

router = APIRouter(prefix="/finetune", tags=["finetune"])

SessionDep = Annotated[Session, Depends(get_db)]


def _workspace(db: Session) -> int:
    workspace_id = workspace_scope(db)
    if workspace_id is None:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN, detail="Workspace context is required"
        )
    return int(workspace_id)


def _translate(exc: ft_service.FinetuneError) -> HTTPException:
    return HTTPException(status_code=exc.status_code, detail=exc.message)


@router.get("/summary", response_model=SummaryRead)
def get_summary(
    db: SessionDep, current_user: Annotated[User, Depends(require_view_permission)]
) -> SummaryRead:
    return ft_service.summary(db, _workspace(db))


@router.get("/sources", response_model=dict)
def get_sources(
    current_user: Annotated[User, Depends(require_view_permission)],
) -> dict:
    """能从哪里回流样本。写出来是为了让人知道"没样本"该去哪个模块产生数据。"""
    return {
        "titles": ft_service.source_titles(),
        "note": "本平台只做数据准备与台账，不执行训练。",
    }


@router.get("/datasets", response_model=list[DatasetRead])
def list_datasets(
    db: SessionDep,
    current_user: Annotated[User, Depends(require_view_permission)],
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
) -> list[DatasetRead]:
    return ft_service.list_datasets(db, _workspace(db), limit=limit)


@router.post("/datasets", response_model=DatasetDetail, status_code=status.HTTP_201_CREATED)
def build_dataset(
    payload: BuildRequest,
    db: SessionDep,
    current_user: Annotated[User, Depends(require_edit_permission)],
) -> DatasetDetail:
    try:
        return ft_service.build_dataset(
            db,
            _workspace(db),
            user_id=current_user.id,
            name=payload.name,
            sources=payload.sources,
            limit=payload.limit,
        )
    except ft_service.FinetuneError as exc:
        raise _translate(exc) from exc


@router.get("/datasets/{dataset_id}", response_model=DatasetDetail)
def get_dataset(
    dataset_id: int,
    db: SessionDep,
    current_user: Annotated[User, Depends(require_view_permission)],
) -> DatasetDetail:
    try:
        return ft_service.get_dataset(db, _workspace(db), dataset_id)
    except ft_service.FinetuneError as exc:
        raise _translate(exc) from exc


@router.get("/datasets/{dataset_id}/export")
def export_dataset(
    dataset_id: int,
    db: SessionDep,
    current_user: Annotated[User, Depends(require_view_permission)],
) -> Response:
    """导出 JSONL。待修样本不进训练集——把待修样本拿去训等于教模型学错的回答。"""
    try:
        filename, payload = ft_service.export_jsonl(db, _workspace(db), dataset_id)
    except ft_service.FinetuneError as exc:
        raise _translate(exc) from exc
    return Response(
        content=payload,
        media_type="application/x-ndjson; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.get("/jobs", response_model=list[JobRead])
def list_jobs(
    db: SessionDep,
    current_user: Annotated[User, Depends(require_view_permission)],
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
) -> list[JobRead]:
    return ft_service.list_jobs(db, _workspace(db), limit=limit)


@router.post("/jobs", response_model=JobRead, status_code=status.HTTP_201_CREATED)
def register_job(
    payload: JobCreate,
    db: SessionDep,
    current_user: Annotated[User, Depends(require_edit_permission)],
) -> JobRead:
    try:
        return ft_service.register_job(db, _workspace(db), payload=payload)
    except ft_service.FinetuneError as exc:
        raise _translate(exc) from exc


@router.post("/jobs/{job_id}/status", response_model=JobRead)
def update_job(
    job_id: int,
    payload: JobUpdate,
    db: SessionDep,
    current_user: Annotated[User, Depends(require_edit_permission)],
) -> JobRead:
    try:
        return ft_service.update_job(db, _workspace(db), job_id, payload=payload)
    except ft_service.FinetuneError as exc:
        raise _translate(exc) from exc


@router.post("/jobs/{job_id}/eval", response_model=JobRead)
def record_eval(
    job_id: int,
    payload: EvalRequest,
    db: SessionDep,
    current_user: Annotated[User, Depends(require_edit_permission)],
) -> JobRead:
    """记同一批题在 base 与 tuned 上的对比。"""
    try:
        return ft_service.record_eval(db, _workspace(db), job_id, payload=payload)
    except ft_service.FinetuneError as exc:
        raise _translate(exc) from exc
