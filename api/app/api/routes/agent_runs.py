import asyncio
import json
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, status
from fastapi.responses import StreamingResponse
from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from app.agents.contracts import ReviewResumePayload
from app.api.routes.auth import require_edit_permission, require_view_permission, workspace_scope
from app.db.session import get_db
from app.models import AgentRun, Product, User
from app.schemas import AgentRunRead, AgentRunReviewCreate
from app.services.agent_runs import (
    AgentRunConflictError,
    AgentRunStateError,
    create_agent_run_record,
    dispatch_agent_run,
    mark_agent_run_dispatch_failed,
    submit_agent_review,
)


router = APIRouter(tags=["agent-runs"])
SessionDep = Annotated[Session, Depends(get_db)]


@router.post(
    "/products/{product_id}/agent-runs",
    response_model=AgentRunRead,
    status_code=status.HTTP_202_ACCEPTED,
)
def create_agent_run(
    product_id: int,
    db: SessionDep,
    current_user: Annotated[User, Depends(require_edit_permission)],
) -> AgentRun:
    """处理创建Agent执行接口；完成身份、Workspace、资源归属和输入校验后返回结构化结果。"""
    # 先完成认证、Workspace 与资源归属校验，再进入会产生副作用的服务调用。
    product = db.get(Product, product_id)
    workspace_id = workspace_scope(db)
    if product is None or (workspace_id is not None and product.workspace_id != workspace_id):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Product not found")
    run_id: int | None = None
    try:
        run = create_agent_run_record(db, product=product, requested_by=current_user)
        db.commit()
        run_id = run.id
        dispatch_agent_run(run_id)
    except AgentRunConflictError as exc:
        db.rollback()
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    except Exception as exc:
        db.rollback()
        if run_id is not None:
            mark_agent_run_dispatch_failed(run_id, exc)
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=f"Agent task could not be queued: {exc}",
        ) from exc
    return _get_run_or_404(db, run_id)


@router.get("/products/{product_id}/agent-runs", response_model=list[AgentRunRead])
def list_product_agent_runs(
    product_id: int,
    db: SessionDep,
    _current_user: Annotated[User, Depends(require_view_permission)],
    limit: Annotated[int, Query(ge=1, le=50)] = 10,
) -> list[AgentRun]:
    """处理查询商品Agentruns接口；完成身份、Workspace、资源归属和输入校验后返回结构化结果。"""
    # 先完成认证、Workspace 与资源归属校验，再进入会产生副作用的服务调用。
    product = db.get(Product, product_id)
    workspace_id = workspace_scope(db)
    if product is None or (workspace_id is not None and product.workspace_id != workspace_id):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Product not found")
    statement = (
        select(AgentRun)
        .where(AgentRun.product_id == product_id)
        .options(selectinload(AgentRun.events), selectinload(AgentRun.reviews))
        .order_by(AgentRun.id.desc())
        .limit(limit)
    )
    return list(db.scalars(statement).all())


@router.get("/agent-runs/{run_id}", response_model=AgentRunRead)
def get_agent_run(
    run_id: int,
    db: SessionDep,
    _current_user: Annotated[User, Depends(require_view_permission)],
) -> AgentRun:
    """处理获取Agent执行接口；完成身份、Workspace、资源归属和输入校验后返回结构化结果。"""
    return _get_run_or_404(db, run_id)


@router.post(
    "/agent-runs/{run_id}/review",
    response_model=AgentRunRead,
    status_code=status.HTTP_202_ACCEPTED,
)
def review_agent_run(
    run_id: int,
    payload: AgentRunReviewCreate,
    db: SessionDep,
    current_user: Annotated[User, Depends(require_edit_permission)],
) -> AgentRun:
    """处理审核Agent执行接口；完成身份、Workspace、资源归属和输入校验后返回结构化结果。"""
    # 先完成认证、Workspace 与资源归属校验，再进入会产生副作用的服务调用。
    run = _get_run_or_404(db, run_id)
    resume_payload = ReviewResumePayload.model_validate(payload.model_dump(mode="json"))
    try:
        submit_agent_review(db, run=run, reviewer=current_user, payload=resume_payload)
        db.commit()
        dispatch_agent_run(run_id, resume_payload.model_dump(mode="json"))
    except AgentRunStateError as exc:
        db.rollback()
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    except Exception as exc:
        db.rollback()
        mark_agent_run_dispatch_failed(run_id, exc)
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=f"Agent resume task could not be queued: {exc}",
        ) from exc
    return _get_run_or_404(db, run_id)


def _get_run_or_404(db: Session, run_id: int) -> AgentRun:
    """处理获取执行or404接口；完成身份、Workspace、资源归属和输入校验后返回结构化结果。"""
    # 先完成认证、Workspace 与资源归属校验，再进入会产生副作用的服务调用。
    statement = (
        select(AgentRun)
        .where(AgentRun.id == run_id)
        .options(selectinload(AgentRun.events), selectinload(AgentRun.reviews))
    )
    workspace_id = workspace_scope(db)
    if workspace_id is not None:
        statement = statement.where(AgentRun.workspace_id == workspace_id)
    run = db.scalar(statement)
    if run is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Agent run not found")
    return run


_ACTIVE_STREAM_STATUSES = {"queued", "running", "resume_queued"}
_SSE_POLL_SECONDS = 0.5
_SSE_HEARTBEAT_SECONDS = 15.0
_SSE_MAX_SECONDS = 600.0


def _serialize_run(run: AgentRun) -> dict[str, object]:
    """序列化运行快照；只读取结构化 Agent 状态，不产生副作用。"""
    # 事件按 id 升序输出，保证增量推送时前端可稳定去重与追加。
    payload = AgentRunRead.model_validate(run).model_dump(mode="json")
    events = payload.get("events") or []
    payload["events"] = sorted(events, key=lambda item: item.get("id") or 0)
    return payload


def _sse_frame(event: str, payload: dict[str, object]) -> str:
    """构建SSE帧；只输出事件名与JSON数据，保持协议可解析。"""
    return f"event: {event}\ndata: {json.dumps(payload, ensure_ascii=False)}\n\n"


def _load_run_snapshot(run_id: int, workspace_id: int | None) -> tuple[dict[str, object], str, int]:
    """在独立会话中读取运行快照；返回快照、状态与最大事件 id 供增量比对。"""
    from app.db.session import SessionLocal

    with SessionLocal() as stream_db:
        if workspace_id is not None:
            stream_db.info["workspace_id"] = workspace_id
        run = _get_run_or_404(stream_db, run_id)
        return _serialize_run(run), run.status.value, max((item.id for item in run.events), default=0)


@router.get("/agent-runs/{run_id}/stream")
async def stream_agent_run(
    run_id: int,
    db: SessionDep,
    _current_user: Annotated[User, Depends(require_view_permission)],
) -> StreamingResponse:
    """处理Agent执行流式接口；完成身份、Workspace、资源归属和输入校验后按 SSE 增量推送事件。"""
    # 入口即完成认证与资源归属校验；生成器独立会话持续观测同一 run 的增量事件。
    _get_run_or_404(db, run_id)
    workspace_id = workspace_scope(db)

    async def event_generator():
        last_event_id = 0
        last_status: str | None = None
        started_at = asyncio.get_event_loop().time()
        last_heartbeat = started_at
        while True:
            now = asyncio.get_event_loop().time()
            if now - started_at > _SSE_MAX_SECONDS:
                break
            try:
                snapshot, status, max_event_id = await asyncio.to_thread(
                    _load_run_snapshot, run_id, workspace_id
                )
            except HTTPException:
                # 运行记录在流式期间不可见时结束推送，避免把内部错误写入事件流。
                break
            changed = max_event_id > last_event_id or status != last_status
            if not changed:
                if now - last_heartbeat >= _SSE_HEARTBEAT_SECONDS:
                    yield ": ping\n\n"
                    last_heartbeat = now
                await asyncio.sleep(_SSE_POLL_SECONDS)
                continue
            yield _sse_frame("snapshot" if last_status is None else "run", {"run": snapshot})
            last_event_id = max_event_id
            last_status = status
            if status not in _ACTIVE_STREAM_STATUSES:
                yield _sse_frame("done", {"run": snapshot})
                break
            await asyncio.sleep(_SSE_POLL_SECONDS)

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )
