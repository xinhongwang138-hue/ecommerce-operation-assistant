"""请求体体积上限的行为测试。

覆盖两条路径：带 Content-Length 的常规请求要拿到干净的 413；
分块传输（没有 Content-Length）必须被中途掐断，不能把整个请求体读进内存。
后者是 V9 修复当时的盲区——只查 Content-Length 时，chunked 请求会直接跳过检查。
"""

import asyncio
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.api import middleware
from app.api.routes.auth import get_current_user
from app.db.session import get_db
from app.main import app
from app.models import Base, User, UserRole

_LIMIT = 4096


@pytest.fixture(autouse=True)
def _isolated_app(monkeypatch):
    """构造或执行测试所需的隔离环境：调小体积上限，并把会话指向内存库。"""
    # middleware 模块里的 get_settings 被体积与限流两个中间件共用，
    # 桩必须同时满足两者的字段，否则测体积会把限流中间件一起打挂。
    monkeypatch.setattr(
        middleware,
        "get_settings",
        lambda: SimpleNamespace(max_request_bytes=_LIMIT, rate_limiting_active=False),
    )
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    with Session(engine) as db:
        db.add(User(email="nobody@example.com", display_name="nobody", role=UserRole.VIEWER))
        db.commit()

    def override_db():
        """构造或执行测试所需的依赖覆盖，让请求只用内存库而不是真实数据库。"""
        with Session(engine) as session:
            yield session

    app.dependency_overrides[get_db] = override_db
    app.dependency_overrides[get_current_user] = lambda: User(
        id=1, email="nobody@example.com", display_name="nobody", role=UserRole.VIEWER
    )
    try:
        yield
    finally:
        app.dependency_overrides.clear()


def test_oversized_content_length_is_rejected_with_413() -> None:
    """验证“超大Content-Length返回413”场景，确保常规客户端拿到明确状态码。"""
    with TestClient(app) as client:
        response = client.post(
            "/api/v1/auth/login",
            content=b"x" * (_LIMIT * 3),
            headers={"Content-Type": "application/json"},
        )

    assert response.status_code == 413
    assert response.json()["detail"] == "Request entity too large"


def test_middleware_stops_consuming_chunked_body_over_limit() -> None:
    """验证“中间件掐断超限分块流”场景，确保缺 Content-Length 时不再绕过上限。

    必须直接驱动 ASGI 接口：TestClient 会先把整个请求体缓冲掉，用它数"客户端递了几块"
    永远是全量，测不出服务端有没有提前停止读取。
    """
    delivered = 0
    total_chunks = 30

    async def client_receive():
        """构造或执行测试所需的分块消息流，模拟 chunked 请求逐块送达。"""
        nonlocal delivered
        delivered += 1
        if delivered < total_chunks:
            return {"type": "http.request", "body": b"x" * _LIMIT, "more_body": True}
        return {"type": "http.request", "body": b"", "more_body": False}

    async def downstream(scope, receive, _send):
        """构造或执行测试所需的下游应用，读到 disconnect 就退出。"""
        while True:
            message = await receive()
            if message["type"] != "http.request" or not message.get("more_body", False):
                return

    async def never_send(_message):
        """构造或执行测试所需的响应通道占位，本用例只关心读取是否被掐断。"""

    asyncio.run(
        middleware.BodySizeLimitMiddleware(downstream)(
            {"type": "http", "headers": []}, client_receive, never_send
        )
    )

    assert delivered < total_chunks, f"分块流被全部读完（{delivered} 块），体积上限对 chunked 无效"


def test_body_within_limit_still_reaches_the_endpoint() -> None:
    """验证“未超限请求正常放行”场景，确保中间件改写后不误伤正常流量。"""
    with TestClient(app) as client:
        response = client.post(
            "/api/v1/auth/login",
            content=b'{"email":"nobody@example.com","password":"***"}',
            headers={"Content-Type": "application/json"},
        )

    # 到得了端点就说明没被体积拦截：口令不对只会得到 401，而不是 413。
    assert response.status_code == 401
