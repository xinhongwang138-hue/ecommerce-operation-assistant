from datetime import UTC, datetime

from fastapi.testclient import TestClient
from sqlalchemy.orm import Session, sessionmaker

from app.api.routes.auth import get_current_user
from app.db import session as db_session
from app.db.session import get_db
from app.main import app
from app.models import AgentRun, AgentRunEvent, AgentRunStatus, Base, Platform, Product, User, UserRole
from support import make_engine, make_session_override


def test_stream_completed_run_emits_snapshot_and_done(monkeypatch) -> None:
    """校验已完成执行可通过 SSE 收到首帧快照与终态事件。"""
    engine = make_engine()
    Base.metadata.create_all(engine)
    product_id, user_id = _seed_product_and_user(engine)
    run_id = _seed_run(engine, product_id, user_id, AgentRunStatus.COMPLETED)
    monkeypatch.setattr(db_session, "SessionLocal", _session_factory(engine))
    app.dependency_overrides[get_db] = make_session_override(engine)
    app.dependency_overrides[get_current_user] = lambda: _user(user_id, UserRole.OPERATOR)
    try:
        with TestClient(app).stream(
            "GET",
            f"/api/v1/agent-runs/{run_id}/stream",
            headers={"Authorization": "Bearer test"},
        ) as response:
            assert response.status_code == 200
            assert response.headers["content-type"].startswith("text/event-stream")
            body = "".join(response.iter_text())
    finally:
        app.dependency_overrides.clear()

    assert "event: snapshot" in body
    assert "event: done" in body
    assert '"status": "completed"' in body
    assert "workflow_completed" in body


def test_stream_unknown_run_returns_404(monkeypatch) -> None:
    """校验不存在的执行不会建立流式连接，仍沿用既有错误语义。"""
    engine = make_engine()
    Base.metadata.create_all(engine)
    _product_id, user_id = _seed_product_and_user(engine)
    monkeypatch.setattr(db_session, "SessionLocal", _session_factory(engine))
    app.dependency_overrides[get_db] = make_session_override(engine)
    app.dependency_overrides[get_current_user] = lambda: _user(user_id, UserRole.OPERATOR)
    try:
        response = TestClient(app).get(
            "/api/v1/agent-runs/999999/stream",
            headers={"Authorization": "Bearer test"},
        )
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 404


def _seed_product_and_user(engine) -> tuple[int, int]:
    """创建SSE测试所需的产品与运营用户。"""
    with Session(engine) as db:
        product = Product(name="补液风扇", platform=Platform.DOUYIN)
        user = User(email="operator@example.com", display_name="Operator", role=UserRole.OPERATOR)
        db.add_all([product, user])
        db.commit()
        return product.id, user.id


def _seed_run(engine, product_id: int, user_id: int, status: AgentRunStatus) -> int:
    """创建指定状态的执行记录与一条已落库事件。"""
    with Session(engine) as db:
        run = AgentRun(
            thread_id=f"00000000-0000-0000-0000-{product_id:012d}",
            product_id=product_id,
            requested_by_user_id=user_id,
            workflow_key="product_operation",
            workflow_version="1.0.0",
            status=status,
            revision_count=0,
            max_revisions=1,
            budget={},
            usage={},
            version_manifest={},
            context_snapshot={},
            draft_output={},
            quality_report={},
            final_output={},
            finished_at=datetime.now(UTC) if status == AgentRunStatus.COMPLETED else None,
        )
        db.add(run)
        db.flush()
        db.add(
            AgentRunEvent(
                run_id=run.id,
                event_type="workflow_completed",
                status=status.value,
                message="工作流执行完成。",
            )
        )
        db.commit()
        return run.id


def _session_factory(engine):
    """构造与测试库绑定的会话工厂，供流式生成器独立会话使用。"""
    return sessionmaker(bind=engine, autocommit=False, autoflush=False)


def _user(user_id: int, role: UserRole) -> User:
    """构造认证依赖所需的用户对象。"""
    return User(id=user_id, email=f"{role.value}@example.com", display_name=role.value, role=role)


