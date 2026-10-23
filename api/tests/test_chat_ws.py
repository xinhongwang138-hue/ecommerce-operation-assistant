import asyncio
import json
import time

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool
from starlette.websockets import WebSocketDisconnect

from app.api.routes import chat_ws
from app.api.routes.auth import get_current_user
from app.db.session import get_db
from app.main import app
from app.models import Base, User, UserRole, Workspace, WorkspaceMembership
from app.services.agent_tasks import AgentTaskStateError
from app.services.auth import parse_access_token


def test_ws_ticket_requires_authentication() -> None:
    """验证“票据requires认证”场景，确保匿名请求不能换取 WebSocket 连接票据。"""
    with TestClient(app) as client:
        response = client.post("/api/v1/chat/ws-ticket")

    assert response.status_code == 401


def test_ws_ticket_is_short_lived_and_purpose_bound() -> None:
    """验证“票据isshort时效and用途绑定”场景，确保 URL 里的票据既短效又只能连 WebSocket。"""
    engine = _engine()
    Base.metadata.create_all(engine)
    user_id = _seed_user(engine)

    app.dependency_overrides[get_db] = _override_db(engine)
    app.dependency_overrides[get_current_user] = lambda: _user(user_id)
    try:
        response = TestClient(app).post("/api/v1/chat/ws-ticket")
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 200
    ticket = response.json()["ticket"]
    payload = parse_access_token(ticket)
    assert payload["purpose"] == "ws_ticket"
    assert payload["sub"] == str(user_id)
    # 票据有效期上限 60 秒，远小于登录态时效，降低 URL 泄露风险。
    assert 0 < payload["exp"] - int(time.time()) <= 60


def test_ws_chat_rejects_invalid_ticket() -> None:
    """验证“聊天rejects非法票据”场景，确保伪造或过期票据在握手阶段即被断开。"""
    with pytest.raises(WebSocketDisconnect), TestClient(app).websocket_connect(
        "/api/v1/ws/chat?ticket=forged.token&workspace_id=1"
    ) as ws:
        ws.receive_text()


def test_ws_chat_rejects_login_token_as_ticket() -> None:
    """验证“聊天rejects登录态as票据”场景，确保长时效登录态不能直接用于 WebSocket 连接。"""
    engine = _engine()
    Base.metadata.create_all(engine)
    user_id = _seed_user(engine)
    with Session(engine) as db:
        login_token = _login_token(db.get(User, user_id))

    with pytest.raises(WebSocketDisconnect), TestClient(app).websocket_connect(
        f"/api/v1/ws/chat?ticket={login_token}&workspace_id=1"
    ) as ws:
        ws.receive_text()


def test_ws_chat_streams_turn_frames(monkeypatch) -> None:
    """验证“聊天streams轮次frames”场景，确保一轮对话按 ack→思考→bot_text→done→turn_end 推送。"""
    monkeypatch.setattr(chat_ws, "_resolve_user", lambda ticket: _user(1))
    monkeypatch.setattr(chat_ws, "_resolve_workspace", lambda **_: 1)
    monkeypatch.setattr(chat_ws, "_create_turn", _fake_create_turn)
    monkeypatch.setattr(chat_ws, "_load_turn_state", _fake_completed_state)

    with TestClient(app).websocket_connect(
        "/api/v1/ws/chat?ticket=t&workspace_id=1"
    ) as ws:
        ws.send_json({"type": "user_text", "text": "看看这款商品的库存风险", "context": {"product_id": 7}})
        frames = [_receive(ws) for _ in range(6)]

    assert [frame["type"] for frame in frames] == [
        "user_ack",
        "status",
        "status",
        "bot_text",
        "status",
        "turn_end",
    ]
    assert frames[1]["status"] == "thinking"
    assert frames[2]["status"] == "thinking"
    assert "库存" in frames[2]["hint"]
    assert frames[3]["text"] == "当前纳入 3 个 SKU 样本。"
    assert frames[4]["status"] == "done"


def test_ws_chat_reports_task_validation_error(monkeypatch) -> None:
    """验证“聊天reports任务校验error”场景，确保缺上下文等业务拒绝以 error 帧返回而不是断开连接。"""
    monkeypatch.setattr(chat_ws, "_resolve_user", lambda ticket: _user(1))
    monkeypatch.setattr(chat_ws, "_resolve_workspace", lambda **_: 1)

    async def _raise(**_kwargs):
        raise AgentTaskStateError("Workflow requires one context field: product_id, store_id")

    monkeypatch.setattr(chat_ws, "_create_turn", _raise)

    with TestClient(app).websocket_connect(
        "/api/v1/ws/chat?ticket=t&workspace_id=1"
    ) as ws:
        ws.send_json({"type": "user_text", "text": "你好"})
        frames = [_receive(ws) for _ in range(5)]

    assert [frame["type"] for frame in frames] == ["user_ack", "status", "error", "status", "turn_end"]
    assert frames[2]["message"].startswith("Workflow requires one context field")
    assert frames[3]["status"] == "done"


def test_ws_chat_new_message_interrupts_previous_turn(monkeypatch) -> None:
    """验证“聊天new消息interrupts上一轮”场景，确保连续输入时旧任务被取消且前端收到打断通知。"""
    cancelled: list[int] = []
    monkeypatch.setattr(chat_ws, "_resolve_user", lambda ticket: _user(1))
    monkeypatch.setattr(chat_ws, "_resolve_workspace", lambda **_: 1)
    monkeypatch.setattr(chat_ws, "_create_turn", _fake_create_turn)
    monkeypatch.setattr(chat_ws, "_observe_turn", _fake_observe)

    async def _cancel(task_id: int) -> None:
        cancelled.append(task_id)

    monkeypatch.setattr(chat_ws, "_cancel_task", _cancel)

    with TestClient(app).websocket_connect(
        "/api/v1/ws/chat?ticket=t&workspace_id=1"
    ) as ws:
        ws.send_json({"type": "user_text", "text": "第一轮", "context": {"product_id": 7}})
        first_frames = [_receive(ws) for _ in range(2)]
        assert [frame["type"] for frame in first_frames] == ["user_ack", "status"]
        ws.send_json({"type": "user_text", "text": "第二轮", "context": {"product_id": 7}})
        frames = [_receive(ws) for _ in range(2)]

    assert cancelled == [11]
    assert frames[0]["type"] == "status"
    assert frames[0]["status"] == "interrupted"
    assert frames[1]["type"] == "user_ack"


def test_ws_chat_rejects_unknown_message_type(monkeypatch) -> None:
    """验证“聊天rejects未知消息type”场景，确保协议外的帧得到明确错误而不是静默丢弃。"""
    monkeypatch.setattr(chat_ws, "_resolve_user", lambda ticket: _user(1))
    monkeypatch.setattr(chat_ws, "_resolve_workspace", lambda **_: 1)

    with TestClient(app).websocket_connect(
        "/api/v1/ws/chat?ticket=t&workspace_id=1"
    ) as ws:
        ws.send_json({"type": "unknown"})
        frame = _receive(ws)

    assert frame["type"] == "error"
    assert "unknown type" in frame["message"]


async def _fake_create_turn(**_kwargs):
    """构造或执行测试所需的创建轮次，让测试数据、依赖替身与断言保持集中。"""
    return 11, 22


async def _fake_observe(websocket, **_kwargs):
    """构造或执行测试所需的观察轮次，让测试数据、依赖替身与断言保持集中。"""
    # 一直观察到被取消，模拟"轮次仍在进行时收到新消息"的打断场景。
    await asyncio.sleep(3600)


def _fake_completed_state(task_id: int, workspace_id: int):
    """构造或执行测试所需的任务快照，让测试数据、依赖替身与断言保持集中。"""
    return (
        "completed",
        [(1, "库存巡检完成，生成 3 条建议。")],
        "当前纳入 3 个 SKU 样本。",
        "",
    )


def _receive(ws) -> dict:
    """读取一帧 JSON 消息并转成字典，供断言按顺序校验协议。"""
    return json.loads(ws.receive_text())


def _login_token(user: User) -> str:
    """用测试用户签一个标准登录态，验证它不能被当作 WebSocket 票据使用。"""
    from app.services.auth import create_access_token

    return create_access_token(user)


def _engine():
    """构造或执行测试所需的engine，让测试数据、依赖替身与断言保持集中。"""
    return create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )


def _seed_user(engine) -> int:
    """构造或执行测试所需的用户，让测试数据、依赖替身与断言保持集中。"""
    with Session(engine) as db:
        workspace = Workspace(slug="team-a", name="团队 A")
        user = User(email="operator@example.com", display_name="Operator", role=UserRole.OPERATOR)
        db.add_all([workspace, user])
        db.flush()
        db.add(
            WorkspaceMembership(
                workspace_id=workspace.id,
                user_id=user.id,
                role=UserRole.OPERATOR,
            )
        )
        db.commit()
        return user.id


def _override_db(engine):
    """构造或执行测试所需的覆盖db，让测试数据、依赖替身与断言保持集中。"""

    def override_db():
        with Session(engine) as db:
            yield db

    return override_db


def _user(user_id: int) -> User:
    """构造或执行测试所需的用户，让测试数据、依赖替身与断言保持集中。"""
    return User(id=user_id, email="operator@example.com", display_name="Operator", role=UserRole.OPERATOR)
