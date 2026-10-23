"""智能客服 WebSocket 对话接口（数字人文本通道）。

在现有受控 Agent 任务链路之上增加一条实时双向通道：客户端发文本，
服务端创建 Agent Task 并经 Outbox 派发执行，随后按轮询增量推送运行事件，
终态把 result_summary 转成 bot_text 交给前端（数字人朗读或聊天气泡展示）。

消息协议（纯 JSON 文本帧）：

客户端 -> 服务端:
    {"type": "user_text", "text": "...", "workflow_key": "(可选)",
     "conversation_id": 1, "context": {"product_id": 1, "store_id": 2}}
    {"type": "cancel"}

服务端 -> 客户端:
    {"type": "user_ack", "message_id": "..."}
    {"type": "status", "status": "thinking|done|error|cancelled|awaiting_review",
     "hint": "(可选)最新运行事件"}
    {"type": "bot_text", "message_id": "...", "index": 0, "text": "..."}
    {"type": "turn_end", "message_id": "..."}
    {"type": "error", "message": "..."}

边界约定：
- 连接鉴权走 60 秒一次性票据（POST /chat/ws-ticket 用登录态换取），
  避免长时效登录态出现在 URL 里；票据与 Workspace 成员关系逐连接校验。
  拒绝发生在 accept 之前，客户端看到的是 Starlette 标准的 HTTP 403，
  具体原因记录在服务端日志。
- 业务逻辑完全复用 create_agent_task / Outbox / Celery，不新造执行链路。
- 客户端断开不取消已创建的任务：任务是业务对象，结果仍可在任务中心查看；
  仅"新消息打断上一轮"和显式 cancel 会取消进行中的任务。
"""
from __future__ import annotations

import asyncio
import json
import logging
import uuid
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Query, WebSocket, WebSocketDisconnect
from sqlalchemy import select

from app.api.routes.auth import require_edit_permission
from app.db.session import SessionLocal
from app.models import AgentRun, AgentRunEvent, AgentTask, User, UserRole
from app.schemas import AgentTaskCreate
from app.services.agent_tasks import AgentTaskStateError, cancel_agent_task, create_agent_task
from app.services.auth import (
    AuthTokenError,
    create_ws_ticket,
    ensure_user_can_login,
    parse_access_token,
)
from app.services.outbox import kick_outbox_dispatcher
from app.services.workspaces import WorkspaceAccessError, resolve_workspace_access

logger = logging.getLogger(__name__)

router = APIRouter(tags=["chat"])

_WS_AUTH_CLOSE_CODE = 4401
_WS_FORBIDDEN_CLOSE_CODE = 4403
_TICKET_TTL_SECONDS = 60
_POLL_SECONDS = 0.5
_MAX_TURN_SECONDS = 300.0
# awaiting_approval 需要人工放行，对聊天轮次同样视为终态。
_TERMINAL_TASK_STATUSES = {"completed", "failed", "cancelled", "awaiting_approval"}


class _WSAuthError(RuntimeError):
    """WebSocket 鉴权或租户校验失败；携带可展示原因。"""


@router.post("/chat/ws-ticket")
def create_chat_ws_ticket(
    current_user: Annotated[User, Depends(require_edit_permission)],
) -> dict[str, str]:
    """用登录态换取 60 秒 WebSocket 连接票据，供前端建立 /ws/chat 长连接。"""
    return {"ticket": create_ws_ticket(current_user, ttl_seconds=_TICKET_TTL_SECONDS)}


@router.websocket("/ws/chat")
async def websocket_chat(
    websocket: WebSocket,
    ticket: Annotated[str, Query(description="POST /chat/ws-ticket 换取的一次性票据")],
    workspace_id: Annotated[int, Query(description="目标 Workspace，必须是当前用户的成员租户")],
) -> None:
    """处理 WebSocket 聊天连接；先鉴权与会话校验，再进入消息循环。

    结构：主循环只负责收帧；每轮对话跑在独立 asyncio 任务里，
    因此"新消息打断上一轮"和显式 cancel 能在观察轮次期间实时生效。
    """
    try:
        user = _resolve_user(ticket)
        workspace_id = _resolve_workspace(user=user, workspace_id=workspace_id)
    except _WSAuthError as exc:
        logger.info("WS chat rejected: %s", exc)
        await websocket.close(code=_WS_AUTH_CLOSE_CODE, reason=str(exc)[:120])
        return

    await websocket.accept()
    current_turn: asyncio.Task | None = None
    turn_state: dict[str, int | None] = {"task_id": None}

    async def stop_current_turn(reason: str, *, cancel_business: bool = True) -> bool:
        """停止当前轮次任务；返回是否真的打断了一轮。业务任务默认同步取消。"""
        nonlocal current_turn
        if current_turn is None or current_turn.done():
            current_turn = None
            return False
        task_id = turn_state.get("task_id")
        current_turn.cancel()
        try:
            await current_turn
        except (asyncio.CancelledError, Exception):
            pass
        finally:
            current_turn = None
        if cancel_business and task_id is not None:
            await _cancel_task(task_id)
        logger.info("WS turn stopped: %s", reason)
        return True

    try:
        while True:
            raw = await websocket.receive_text()
            try:
                payload = json.loads(raw)
            except json.JSONDecodeError:
                await _send(websocket, {"type": "error", "message": "invalid json"})
                continue

            msg_type = payload.get("type")
            if msg_type == "user_text":
                text = (payload.get("text") or "").strip()
                if not text:
                    await _send(websocket, {"type": "error", "message": "empty text"})
                    continue
                if await stop_current_turn("new user_text arrived"):
                    # 旧轮次已停止，通知前端立即停播旧回复，再开始新一轮。
                    await _send(websocket, {"type": "status", "status": "interrupted"})
                current_turn = asyncio.create_task(
                    _run_turn(
                        websocket,
                        payload=payload,
                        workspace_id=workspace_id,
                        user=user,
                        turn_state=turn_state,
                    )
                )
            elif msg_type == "cancel":
                if await stop_current_turn("cancel requested"):
                    await _send(websocket, {"type": "status", "status": "cancelled"})
            else:
                await _send(websocket, {"type": "error", "message": f"unknown type: {msg_type}"})
    except WebSocketDisconnect:
        logger.info("WS chat client disconnected")
        # 断开只停止本地观察；已创建的任务是业务对象，继续执行并在任务中心可见。
        await stop_current_turn("ws closing", cancel_business=False)
    except Exception:
        logger.exception("WS chat handler crashed")
        try:
            await websocket.close()
        except Exception:
            pass


async def _run_turn(
    websocket: WebSocket,
    *,
    payload: dict[str, Any],
    workspace_id: int,
    user: User,
    turn_state: dict[str, int | None],
) -> None:
    """执行一轮对话：ack → 创建受控任务 → 观察并转发增量事件与终态摘要。"""
    message_id = uuid.uuid4().hex
    try:
        await _send(websocket, {"type": "user_ack", "message_id": message_id})
        await _send(websocket, {"type": "status", "status": "thinking"})
        task_id, run_id = await _create_turn(
            user=user, workspace_id=workspace_id, payload=payload
        )
    except (AgentTaskStateError, ValueError) as exc:
        await _send(websocket, {"type": "error", "message": str(exc)})
        await _send(websocket, {"type": "status", "status": "done"})
        await _send(websocket, {"type": "turn_end", "message_id": message_id})
        return
    turn_state["task_id"] = task_id
    await _observe_turn(
        websocket,
        task_id=task_id,
        run_id=run_id,
        message_id=message_id,
        workspace_id=workspace_id,
    )


def _resolve_user(ticket: str) -> User:
    """校验一次性票据并回表确认账号可用；失败抛 _WSAuthError。"""
    try:
        payload = parse_access_token(ticket)
        if payload.get("purpose") != "ws_ticket":
            raise AuthTokenError("ticket purpose mismatch")
        user_id = int(payload.get("sub", 0))
    except (AuthTokenError, ValueError) as exc:
        raise _WSAuthError(f"invalid ticket: {exc}") from exc
    with SessionLocal() as db:
        try:
            return ensure_user_can_login(db.get(User, user_id))
        except AuthTokenError as exc:
            raise _WSAuthError(str(exc)) from exc


def _resolve_workspace(*, user: User, workspace_id: int) -> int:
    """校验 Workspace 成员关系与编辑权限；失败抛 _WSAuthError。"""
    with SessionLocal() as db:
        try:
            access = resolve_workspace_access(db, user=user, workspace_id=workspace_id)
        except WorkspaceAccessError as exc:
            raise _WSAuthError(str(exc)) from exc
        if access.role not in {UserRole.OWNER, UserRole.OPERATOR}:
            raise _WSAuthError("workspace edit permission required")
        return access.workspace.id


async def _create_turn(
    *, user: User, workspace_id: int, payload: dict[str, Any]
) -> tuple[int, int]:
    """把一轮用户输入落成受控 Agent Task 并经 Outbox 派发，返回 (task_id, run_id)。"""
    context = payload.get("context") or {}
    task_payload = AgentTaskCreate(
        workflow_key=payload.get("workflow_key"),
        intent=(payload.get("text") or "").strip(),
        title=(payload.get("text") or "").strip()[:255] or None,
        conversation_id=payload.get("conversation_id"),
        context_scope={
            key: context[key]
            for key in ("product_id", "store_id")
            if context.get(key) is not None
        },
    )

    def _work() -> tuple[int, int]:
        with SessionLocal() as db:
            # 在独立会话里回表取用户，避免把已分离的 ORM 实例带进写路径。
            fresh_user = db.get(User, user.id)
            task, run, _event = create_agent_task(
                db,
                workspace_id=workspace_id,
                requested_by=fresh_user,
                payload=task_payload,
            )
            db.commit()
            return task.id, run.id

    task_id, run_id = await asyncio.to_thread(_work)
    try:
        kick_outbox_dispatcher()
    except Exception:
        # Outbox 已与 Task 同事务提交，瞬时队列故障由 maintenance worker 后续重投。
        logger.exception("kick outbox dispatcher failed for task %s", task_id)
    return task_id, run_id


async def _observe_turn(
    websocket: WebSocket,
    *,
    task_id: int,
    run_id: int,
    message_id: str,
    workspace_id: int,
) -> None:
    """轮询一轮任务的增量事件并转发；终态输出 bot_text 与 turn_end。"""
    last_event_id = 0
    started_at = asyncio.get_event_loop().time()
    while True:
        try:
            status_value, events, summary_text, error_text = await asyncio.to_thread(
                _load_turn_state, task_id, workspace_id
            )
        except LookupError:
            await _send(websocket, {"type": "error", "message": "task not found"})
            await _send(websocket, {"type": "turn_end", "message_id": message_id})
            return

        for event_id, event_text in events:
            if event_id <= last_event_id:
                continue
            last_event_id = event_id
            if event_text:
                await _send(
                    websocket,
                    {"type": "status", "status": "thinking", "hint": event_text},
                )

        if status_value in _TERMINAL_TASK_STATUSES:
            if status_value == "completed":
                text = summary_text or "任务已完成，请在任务中心查看结构化结果。"
                await _send(
                    websocket,
                    {"type": "bot_text", "message_id": message_id, "index": 0, "text": text},
                )
                await _send(websocket, {"type": "status", "status": "done"})
            elif status_value == "awaiting_approval":
                await _send(
                    websocket,
                    {"type": "status", "status": "awaiting_review", "hint": "草稿已生成，等待人工审核"},
                )
            elif status_value == "cancelled":
                await _send(websocket, {"type": "status", "status": "cancelled"})
            else:
                await _send(
                    websocket,
                    {"type": "error", "message": f"任务失败：{error_text or status_value}"},
                )
                await _send(websocket, {"type": "status", "status": "error"})
            await _send(websocket, {"type": "turn_end", "message_id": message_id})
            return

        if asyncio.get_event_loop().time() - started_at > _MAX_TURN_SECONDS:
            await _send(websocket, {"type": "error", "message": "turn timeout"})
            await _send(websocket, {"type": "turn_end", "message_id": message_id})
            return
        await asyncio.sleep(_POLL_SECONDS)


def _load_turn_state(
    task_id: int, workspace_id: int
) -> tuple[str, list[tuple[int, str]], str, str]:
    """在独立会话里读取任务快照；返回状态、增量事件、口头摘要与错误信息。"""
    with SessionLocal() as db:
        task = db.get(AgentTask, task_id)
        if task is None or task.workspace_id != workspace_id:
            raise LookupError("task not found in workspace")
        run = db.scalar(
            select(AgentRun).where(AgentRun.task_id == task_id).order_by(AgentRun.id.desc())
        )
        events: list[tuple[int, str]] = []
        if run is not None:
            rows = db.scalars(
                select(AgentRunEvent)
                .where(AgentRunEvent.run_id == run.id)
                .order_by(AgentRunEvent.id.asc())
            ).all()
            events = [(row.id, row.message or "") for row in rows]
        return (
            task.status.value,
            events,
            _summary_text(task.result_summary),
            task.error or "",
        )


def _summary_text(summary: dict[str, Any] | None) -> str:
    """把结构化 result_summary 转成适合朗读的单段文本；无摘要时返回空串。"""
    if not isinstance(summary, dict):
        return ""
    text = summary.get("summary")
    if isinstance(text, str) and text.strip():
        return text.strip()
    outputs = summary.get("outputs")
    if isinstance(outputs, dict):
        for value in outputs.values():
            if isinstance(value, str) and value.strip():
                return value.strip()
    return ""


async def _cancel_task(task_id: int) -> None:
    """取消进行中的任务；已终态或写入失败时只记录，不打断聊天主循环。"""

    def _work() -> None:
        with SessionLocal() as db:
            task = db.get(AgentTask, task_id)
            if task is None:
                return
            try:
                cancel_agent_task(db, task=task)
                db.commit()
            except AgentTaskStateError:
                db.rollback()

    try:
        await asyncio.to_thread(_work)
    except Exception:
        logger.exception("cancel task %s failed", task_id)


async def _send(websocket: WebSocket, data: dict[str, Any]) -> None:
    """安全发送 JSON 文本帧；客户端已断开时静默失败。"""
    try:
        await websocket.send_text(json.dumps(data, ensure_ascii=False))
    except Exception:
        logger.debug("WS send failed (client likely closed)", exc_info=True)
