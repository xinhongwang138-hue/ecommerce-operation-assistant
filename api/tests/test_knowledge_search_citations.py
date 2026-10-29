"""检索出处的"章节"字段必须一路带到接口响应。

背景：智库 sources 里的 `title` 是切片首个标题行（"# 警告"这种），`parent_title`
才是它所属的那一节（"# HAK 180 烫金机"）。电商侧做字段映射时只要漏一个，
前端「段落」就退化成小标题，"引用了哪份资料的哪一段"这句话就落不到实处。
这条曾在 sources_utils 与 schema 之间断过一次，用测试钉住。
"""
import os

os.environ.setdefault("DATABASE_URL", "sqlite:///:memory:")

from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.api.routes import knowledge as knowledge_routes
from app.api.routes.auth import get_current_user
from app.db.session import get_db
from app.main import app
from app.models import (
    Base,
    MembershipStatus,
    User,
    UserRole,
    Workspace,
    WorkspaceMembership,
)
from tests.support import make_engine, make_session_override


def _seed(engine) -> tuple[int, int]:
    """建一个租户与它的成员，返回 (user_id, workspace_id)"""
    with Session(engine) as db:
        user = User(email="citer@example.com", display_name="C", role=UserRole.OPERATOR)
        workspace = Workspace(slug="ws-cite", name="引用测试租户")
        db.add_all([user, workspace])
        db.flush()
        db.add(
            WorkspaceMembership(
                workspace_id=workspace.id,
                user_id=user.id,
                role=UserRole.OPERATOR,
                status=MembershipStatus.ACTIVE,
            )
        )
        db.commit()
        return user.id, workspace.id


def test_search_response_keeps_parent_title(monkeypatch):
    engine = make_engine()
    Base.metadata.create_all(engine)
    user_id, workspace_id = _seed(engine)

    monkeypatch.setattr(
        knowledge_routes,
        "kb_search",
        lambda workspace_id, query: {
            "answer": "断电 30 秒后再按橙色卡扣。",
            "sources": [
                {
                    "id": 1,
                    "title": "# 警告",
                    "parent_title": "# HAK 180 烫金机",
                    "file_title": "Brother产品手册",
                    "chunk_id": "470051171099467732",
                    "score": 0.999,
                    "snippet": "更换熔带前必须断电等待 30 秒。",
                }
            ],
            "item_names": ["HAK180烫金机"],
        },
    )

    app.dependency_overrides[get_db] = make_session_override(engine)
    app.dependency_overrides[get_current_user] = lambda: User(
        id=user_id, email="citer@example.com", display_name="C", role=UserRole.OPERATOR
    )
    try:
        client = TestClient(app)
        response = client.post(
            "/api/v1/knowledge/search",
            json={"query": "熔带怎么换", "limit": 5},
            headers={"Authorization": "Bearer t", "X-Workspace-ID": str(workspace_id)},
        )
        assert response.status_code == 200, response.text
        source = response.json()["sources"][0]
        assert source["parent_title"] == "# HAK 180 烫金机"
        assert source["title"] == "# 警告"
        assert source["chunk_id"] == "470051171099467732"
    finally:
        app.dependency_overrides.clear()
        Base.metadata.drop_all(engine)


def test_missing_parent_title_is_none_not_empty_string(monkeypatch):
    """智库没给 parent_title 时（联网结果/老数据），应落成 null 而不是空串，前端才好回落"""
    engine = make_engine()
    Base.metadata.create_all(engine)
    user_id, workspace_id = _seed(engine)

    monkeypatch.setattr(
        knowledge_routes,
        "kb_search",
        lambda workspace_id, query: {
            "answer": "答",
            "sources": [
                {"id": 1, "title": "熔带更换", "file_title": "某手册", "chunk_id": "1", "score": 0.5, "snippet": "略"}
            ],
            "item_names": [],
        },
    )

    app.dependency_overrides[get_db] = make_session_override(engine)
    app.dependency_overrides[get_current_user] = lambda: User(
        id=user_id, email="citer@example.com", display_name="C", role=UserRole.OPERATOR
    )
    try:
        client = TestClient(app)
        response = client.post(
            "/api/v1/knowledge/search",
            json={"query": "熔带怎么换", "limit": 5},
            headers={"Authorization": "Bearer t", "X-Workspace-ID": str(workspace_id)},
        )
        assert response.status_code == 200, response.text
        assert response.json()["sources"][0]["parent_title"] is None
    finally:
        app.dependency_overrides.clear()
        Base.metadata.drop_all(engine)
