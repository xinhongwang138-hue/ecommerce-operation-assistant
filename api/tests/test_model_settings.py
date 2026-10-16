from types import SimpleNamespace

from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.api.routes.auth import get_current_user
from app.db.session import get_db
from app.main import app
from app.models import Base, ModelProviderSetting, ModelTarget
from app.models import User, UserRole
from app.services.model_settings import read_model_settings, update_model_settings


def test_read_model_settings_uses_database_before_env() -> None:
    """验证“读取模型配置usesdatabasebeforeenv”场景，确保重构后仍满足预期业务与安全边界。"""
    # 按 Arrange、Act、Assert 组织场景，重点检查可观察状态和副作用。
    with _session() as db:
        db.add(
            ModelProviderSetting(
                target=ModelTarget.IMAGE,
                provider="jimeng",
                base_url="https://visual.volcengineapi.com",
                api_key="volc-ak:volc-sk",
                model="jimeng_t2i_v31",
                size="1328*1328",
                poll_interval_seconds=3,
                poll_max_attempts=10,
            )
        )
        db.commit()

        settings = read_model_settings(db, _env_settings())

    assert settings["image"]["provider"] == "jimeng"
    assert settings["image"]["model"] == "jimeng_t2i_v31"
    assert settings["image"]["size"] == "1328*1328"
    assert settings["image"]["has_api_key"]
    assert settings["text"]["model"] == "env-text-model"


def test_update_model_settings_keeps_existing_secret_when_blank() -> None:
    """验证“更新模型配置keepsexisting密钥whenblank”场景，确保重构后仍满足预期业务与安全边界。"""
    # 按 Arrange、Act、Assert 组织场景，重点检查可观察状态和副作用。
    with _session() as db:
        db.add(
            ModelProviderSetting(
                target=ModelTarget.TEXT,
                base_url="https://old.example.com",
                api_key="old-secret",
                model="old-model",
            )
        )
        db.commit()

        settings = update_model_settings(
            {
                "text": {
                    "base_url": "https://new.example.com",
                    "api_key": "",
                    "model": "new-model",
                }
            },
            db,
        )

        row = db.query(ModelProviderSetting).filter_by(target=ModelTarget.TEXT).one()

    assert row.api_key == "old-secret"
    assert settings["text"]["base_url"] == "https://new.example.com"
    assert settings["text"]["model"] == "new-model"
    assert settings["text"]["has_api_key"]


def test_viewer_cannot_update_model_settings() -> None:
    """验证“viewercannot更新模型配置”场景，确保重构后仍满足预期业务与安全边界。"""
    # 按 Arrange、Act、Assert 组织场景，重点检查可观察状态和副作用。
    engine = _engine()
    Base.metadata.create_all(engine)

    app.dependency_overrides[get_db] = _override_db(engine)
    app.dependency_overrides[get_current_user] = lambda: _user(UserRole.VIEWER)
    try:
        response = TestClient(app).patch(
            "/api/v1/model-settings",
            json={"text": {"model": "viewer-model"}},
            headers={"Authorization": "Bearer test"},
        )
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 403


def test_viewer_cannot_test_model_settings() -> None:
    """验证“viewercannottest模型配置”场景，确保重构后仍满足预期业务与安全边界。"""
    # 按 Arrange、Act、Assert 组织场景，重点检查可观察状态和副作用。
    engine = _engine()
    Base.metadata.create_all(engine)

    app.dependency_overrides[get_db] = _override_db(engine)
    app.dependency_overrides[get_current_user] = lambda: _user(UserRole.VIEWER)
    try:
        response = TestClient(app).post(
            "/api/v1/model-settings/test",
            json={"target": "text"},
            headers={"Authorization": "Bearer test"},
        )
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 403


def test_operator_can_update_model_settings() -> None:
    """验证“operatorcan更新模型配置”场景，确保重构后仍满足预期业务与安全边界。"""
    # 按 Arrange、Act、Assert 组织场景，重点检查可观察状态和副作用。
    engine = _engine()
    Base.metadata.create_all(engine)

    app.dependency_overrides[get_db] = _override_db(engine)
    app.dependency_overrides[get_current_user] = lambda: _user(UserRole.OPERATOR)
    try:
        response = TestClient(app).patch(
            "/api/v1/model-settings",
            json={"text": {"model": "operator-model"}},
            headers={"Authorization": "Bearer test"},
        )
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 200
    assert response.json()["text"]["model"] == "operator-model"


def _session() -> Session:
    """构造或执行测试所需的会话，让测试数据、依赖替身与断言保持集中。"""
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    return Session(engine)


def _engine():
    """构造或执行测试所需的engine，让测试数据、依赖替身与断言保持集中。"""
    return create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )


def _override_db(engine):
    """构造或执行测试所需的覆盖db，让测试数据、依赖替身与断言保持集中。"""
    def override_db():
        """构造或执行测试所需的覆盖db，让测试数据、依赖替身与断言保持集中。"""
        with Session(engine) as db:
            yield db

    return override_db


def _user(role: UserRole) -> User:
    """构造或执行测试所需的用户，让测试数据、依赖替身与断言保持集中。"""
    return User(id=1, email=f"{role}@example.com", display_name=str(role), role=role)


def _env_settings() -> SimpleNamespace:
    """构造或执行测试所需的env配置，让测试数据、依赖替身与断言保持集中。"""
    # 按 Arrange、Act、Assert 组织场景，重点检查可观察状态和副作用。
    return SimpleNamespace(
        app_name="test",
        database_url="sqlite://",
        api_cors_origins="",
        llm_base_url="https://env-text.example.com",
        llm_api_key="env-text-key",
        llm_model="env-text-model",
        image_provider="dashscope",
        image_base_url="https://env-image.example.com",
        image_api_key="env-image-key",
        image_model="env-image-model",
        image_size="1024*1024",
        image_workspace_id="",
        image_poll_interval_seconds=5,
        image_poll_max_attempts=24,
        video_provider="dashscope",
        video_base_url="https://env-video.example.com",
        video_api_key="env-video-key",
        video_model="env-video-model",
        video_size="1280*720",
        video_duration=2,
        video_workspace_id="",
        video_poll_interval_seconds=10,
        video_poll_max_attempts=36,
    )
