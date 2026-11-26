from types import SimpleNamespace

from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.api.routes import digital_human
from app.api.routes.auth import get_current_user
from app.db.session import get_db
from app.main import app
from app.models import Base, User, UserRole, Workspace, WorkspaceMembership
from support import make_engine, make_session_override


def test_credentials_requires_authentication() -> None:
    """验证“凭证requires认证”场景，确保数字人密钥接口不暴露给匿名请求。"""
    with TestClient(app) as client:
        response = client.get("/api/v1/digital-human/credentials")

    assert response.status_code == 401


def test_credentials_returns_empty_when_unconfigured(monkeypatch) -> None:
    """验证“凭证returnsemptywhen未配置”场景，确保缺密钥时前端可降级提示而不是报错。"""
    engine = make_engine()
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

    app.dependency_overrides[get_db] = make_session_override(engine)
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
    engine = make_engine()
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

    app.dependency_overrides[get_db] = make_session_override(engine)
    app.dependency_overrides[get_current_user] = lambda: _user(user_id)
    try:
        response = TestClient(app).get("/api/v1/digital-human/credentials")
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 200
    assert response.json()["app_id"] == "app-id-123"
    assert response.json()["app_secret"] == "app-secret-456"
    assert response.json()["gateway_server"] == "https://gateway.example.com/session"


def test_credentials_rejects_non_owner() -> None:
    """全局供应商密钥只对 workspace owner 开放。

    这把 appSecret 存在后端 .env 里、不区分租户，且直接对应账号的计费时长；
    原先 edit 权限即可领取，等于每个租户的 operator 都持有同一把全局密钥。
    """
    engine = make_engine()
    Base.metadata.create_all(engine)
    user_id = _seed_user(engine, UserRole.OPERATOR)

    app.dependency_overrides[get_db] = make_session_override(engine)
    app.dependency_overrides[get_current_user] = lambda: _user(user_id, UserRole.OPERATOR)
    try:
        response = TestClient(app).get("/api/v1/digital-human/credentials")
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 403


def _seed_user(engine, role: UserRole = UserRole.OWNER) -> int:
    """构造或执行测试所需的用户，让测试数据、依赖替身与断言保持集中。"""
    with Session(engine) as db:
        workspace = Workspace(slug="team-a", name="团队 A")
        user = User(email=f"{role.value}@example.com", display_name=role.value, role=role)
        db.add_all([workspace, user])
        db.flush()
        db.add(
            WorkspaceMembership(
                workspace_id=workspace.id,
                user_id=user.id,
                role=role,
            )
        )
        db.commit()
        return user.id


def _user(user_id: int, role: UserRole = UserRole.OWNER) -> User:
    """构造或执行测试所需的用户，让测试数据、依赖替身与断言保持集中。"""
    return User(id=user_id, email=f"{role.value}@example.com", display_name=role.value, role=role)
