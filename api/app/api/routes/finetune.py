"""模型微调平台接口。

八个端点。导出是文本流而不是文件落地——数据集动辄几十兆，把它写进 uploads/
再让浏览器去下载，等于给磁盘留一份没人清理的副本。

出口约定：
  422  没有可用样本 / 数据集里全是待修样本 / 评测用例为空 / 回调体不合法
  400  status 或 sources 取值不合法
  404  数据集或任务不存在（含跨租户）
  409  任务已是终态，不能再改（要重跑请新登记一条）
  401  回调签名不匹配
  503  未配置 FINETUNE_CALLBACK_SECRET，回调接口未启用
"""

from __future__ import annotations

import hashlib
import hmac
import json
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response, status
from pydantic import ValidationError
from sqlalchemy.orm import Session

from app.api.routes.auth import require_edit_permission, require_view_permission, workspace_scope
from app.core.config import get_settings
from app.db.session import get_db
from app.finetune import service as ft_service
from app.finetune.schemas import (
    BuildRequest,
    CallbackRequest,
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


@router.get("/jobs/{job_id}", response_model=JobRead)
def get_job(
    job_id: int,
    db: SessionDep,
    current_user: Annotated[User, Depends(require_view_permission)],
) -> JobRead:
    try:
        return ft_service.get_job(db, _workspace(db), job_id)
    except ft_service.FinetuneError as exc:
        raise _translate(exc) from exc


def _sign(secret: str, job_id: int, raw: bytes) -> str:
    """签名覆盖 `{job_id}\n{原样请求体}`。

    把 job_id 放进签名，是为了让一条截获的回调不能改投到别的作业上——
    只签请求体的话，同一份 `{"status":"succeeded"}` 可以贴到任何 job 上。
    """
    message = f"{job_id}.".encode("utf-8") + raw
    return hmac.new(secret.encode("utf-8"), message, hashlib.sha256).hexdigest()


@router.post("/jobs/{job_id}/callback", response_model=JobRead)
async def job_callback(job_id: int, request: Request, db: SessionDep) -> JobRead:
    """外部训练服务的回写入口，用 HMAC 签名认人，不认用户令牌。

    读接口用用户身份（有租户边界），写这条用服务身份（只能改这一条作业）。
    两把钥匙各管一件事：训练机上不该长期放一个能读全租户数据的 7 天令牌。
    """
    secret = get_settings().finetune_callback_secret
    if not secret:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="未配置 FINETUNE_CALLBACK_SECRET，回调接口未启用",
        )

    raw = await request.body()
    supplied = request.headers.get("x-finetune-signature", "")
    if not hmac.compare_digest(supplied, _sign(secret, job_id, raw)):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="签名不匹配")

    try:
        payload = CallbackRequest(**json.loads(raw or b"{}"))
    except (json.JSONDecodeError, ValidationError) as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=f"请求体不合法：{exc}"
        ) from exc

    try:
        return ft_service.apply_callback(db, job_id, payload=payload)
    except ft_service.FinetuneError as exc:
        raise _translate(exc) from exc
