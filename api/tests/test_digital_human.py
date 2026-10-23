from types import SimpleNamespace

from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.api.routes import digital_human
from app.api.routes.auth import get_current_user
from app.db.session import get_db
from app.main import app
from app.models import Base, User, UserRole, Workspace, WorkspaceMembership


def test_credentials_requires_authentication() -> None:
    """验证“凭证requires认证”场景，确保数字人密钥接口不暴露给匿名请求。"""
    with TestClient(app) as client:
        response = client.get("/api/v1/digital-human/credentials")

    assert response.status_code == 401


def test_credentials_returns_empty_when_unconfigured(monkeypatch) -> None:
    """验证“凭证returnsemptywhen未配置”场景，确保缺密钥时前端可降级提示而不是报错。"""
    engine = _engine()
    Base.metadata.create_all(engine)
    user_id = _seed_user(engine)
    monkeypatch.setattr(
        digital_human,
        "get_settings",
        lambda: SimpleNamespace(
            digital_human_app_id="",
            digital_human_app_secret="",
            digital_human_gateway_server="https://nebula-agent.xingyun3d.com/user/v1/ttsa/session",
        ),
    )

    app.dependency_overrides[get_db] = _override_db(engine)
    app.dependency_overrides[get_current_user] = lambda: _user(user_id)
    try:
        response = TestClient(app).get("/api/v1/digital-human/credentials")
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 200
    assert response.json() == {
        "app_id": "",
        "app_secret": "",
        "gateway_server": "https://nebula-agent.xingyun3d.com/user/v1/ttsa/session",
    }


def test_credentials_returns_configured_values(monkeypatch) -> None:
    """验证“凭证returns已配置值”场景，确保后端 .env 中的凭证能原样下发前端初始化 SDK。"""
    engine = _engine()
    Base.metadata.create_all(engine)
    user_id = _seed_user(engine)
    monkeypatch.setattr(
        digital_human,
        "get_settings",
        lambda: SimpleNamespace(
            digital_human_app_id="app-id-123",
            digital_human_app_secret="app-secret-456",
            digital_human_gateway_server="https://gateway.example.com/session",
        ),
    )

    app.dependency_overrides[get_db] = _override_db(engine)
    app.dependency_overrides[get_current_user] = lambda: _user(user_id)
    try:
        response = TestClient(app).get("/api/v1/digital-human/credentials")
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 200
    assert response.json()["app_id"] == "app-id-123"
    assert response.json()["app_secret"] == "app-secret-456"
    assert response.json()["gateway_server"] == "https://gateway.example.com/session"


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
