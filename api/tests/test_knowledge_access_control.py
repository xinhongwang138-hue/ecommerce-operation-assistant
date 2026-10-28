"""跨租户直查（IDOR）防护测试。

对应硬约束："代理层必须校验当前用户所属租户，禁止任何按 ID 直查他人数据的接口"。
这条之前只有代码审查背书，没有测试钉住。

关键约定：别人的资源一律 404，不用 403 —— 403 会告诉攻击者"这个 ID 确实存在，
只是不属于你"，等于一个免费的探测 oracle。
"""
import os

os.environ.setdefault("DATABASE_URL", "sqlite:///:memory:")

from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.api.routes.auth import get_current_user
from app.db.session import get_db
from app.main import app
from app.models import (
    Base,
    KnowledgeDocument,
    KnowledgeDocumentStatus,
    MembershipStatus,
    Platform,
    Product,
    User,
    UserRole,
    Workspace,
    WorkspaceMembership,
)
from tests.support import make_engine, make_session_override


def _seed(engine) -> tuple[int, int, int, int]:
    """建两个租户：A 有成员关系、B 没有；资料与商品都放在 B 名下"""
    with Session(engine) as db:
        user = User(email="a@example.com", display_name="A", role=UserRole.OPERATOR)
        ws_a = Workspace(slug="ws-a", name="租户 A")
        ws_b = Workspace(slug="ws-b", name="租户 B")
        db.add_all([user, ws_a, ws_b])
        db.flush()
        db.add(
            WorkspaceMembership(
                workspace_id=ws_a.id,
                user_id=user.id,
                role=UserRole.OPERATOR,
                status=MembershipStatus.ACTIVE,
            )
        )
        product_b = Product(workspace_id=ws_b.id, name="B 的商品", platform=Platform.DOUYIN)
        doc_b = KnowledgeDocument(
            workspace_id=ws_b.id,
            product_id=None,
            title="B 的手册",
            original_filename="B.md",
            stored_path=f"knowledge/{ws_b.id}/b.md",
            status=KnowledgeDocumentStatus.READY,
        )
        db.add_all([product_b, doc_b])
        db.commit()
        return user.id, ws_a.id, product_b.id, doc_b.id


def _client(engine, user_id: int) -> TestClient:
    app.dependency_overrides[get_db] = make_session_override(engine)
    app.dependency_overrides[get_current_user] = lambda: User(
        id=user_id, email="a@example.com", display_name="A", role=UserRole.OPERATOR
    )
    return TestClient(app)


def _setup():
    engine = make_engine()
    Base.metadata.create_all(engine)
    user_id, ws_a, product_b, doc_b = _seed(engine)
    return engine, user_id, ws_a, product_b, doc_b


def test_cannot_read_another_tenants_document_by_id():
    engine, user_id, ws_a, _product_b, doc_b = _setup()
    try:
        client = _client(engine, user_id)
        response = client.get(
            f"/api/v1/knowledge/documents/{doc_b}",
            headers={"Authorization": "Bearer t", "X-Workspace-ID": str(ws_a)},
        )
        # 404 而非 403：不透露"这个 ID 存在"
        assert response.status_code == 404, response.text
    finally:
        app.dependency_overrides.clear()


def test_cannot_delete_another_tenants_document():
    engine, user_id, ws_a, _product_b, doc_b = _setup()
    try:
        client = _client(engine, user_id)
        response = client.delete(
            f"/api/v1/knowledge/documents/{doc_b}",
            headers={"Authorization": "Bearer t", "X-Workspace-ID": str(ws_a)},
        )
        assert response.status_code == 404, response.text
        # 越权删除必须真的没删掉
        with Session(engine) as check:
            assert check.get(KnowledgeDocument, doc_b) is not None
    finally:
        app.dependency_overrides.clear()


def test_list_only_returns_own_tenant_rows():
    engine, user_id, ws_a, _product_b, doc_b = _setup()
    try:
        client = _client(engine, user_id)
        response = client.get(
            "/api/v1/knowledge/documents",
            headers={"Authorization": "Bearer t", "X-Workspace-ID": str(ws_a)},
        )
        assert response.status_code == 200, response.text
        ids = [item["id"] for item in response.json()]
        assert doc_b not in ids
        assert ids == []
    finally:
        app.dependency_overrides.clear()


def test_non_member_workspace_is_rejected_not_silently_switched():
    """请求头填一个自己没有成员关系的租户，必须是拒绝，不能被当成有效租户放行"""
    engine, user_id, _ws_a, _product_b, doc_b = _setup()
    try:
        client = _client(engine, user_id)
        response = client.get(
            f"/api/v1/knowledge/documents/{doc_b}",
            headers={"Authorization": "Bearer t", "X-Workspace-ID": "999"},
        )
        assert response.status_code == 403, response.text
    finally:
        app.dependency_overrides.clear()


def test_missing_workspace_header_falls_back_to_own_tenant_only():
    """缺 X-Workspace-ID 时，resolve_workspace_access 回落到"该用户自己的"最早成员关系
    （查询恒带 user_id == 当前用户 且成员/租户均 ACTIVE），不会落到任意租户。
    因此别租户的资料仍然取不到 —— 这里断言的是回落安全，而不是某个状态码。
    """
    engine, user_id, _ws_a, _product_b, doc_b = _setup()
    try:
        client = _client(engine, user_id)
        response = client.get(
            f"/api/v1/knowledge/documents/{doc_b}",
            headers={"Authorization": "Bearer t"},
        )
        assert response.status_code == 404, response.text

        # 同一请求换成"自己租户里不存在"的 ID 也是 404，说明回落确实落在 ws_a 内
        own_missing = client.get(
            "/api/v1/knowledge/documents/999999",
            headers={"Authorization": "Bearer t"},
        )
        assert own_missing.status_code == 404, own_missing.text

        # 而列表接口能正常返回本租户（空）数据，证明回落后的租户上下文是有效的
        listing = client.get(
            "/api/v1/knowledge/documents",
            headers={"Authorization": "Bearer t"},
        )
        assert listing.status_code == 200, listing.text
        assert [item["id"] for item in listing.json()] == []
    finally:
        app.dependency_overrides.clear()


def test_search_rejects_product_id_from_another_tenant():
    """按商品收窄检索范围时，商品也必须属于本租户"""
    engine, user_id, ws_a, product_b, _doc_b = _setup()
    try:
        client = _client(engine, user_id)
        response = client.post(
            "/api/v1/knowledge/search",
            headers={"Authorization": "Bearer t", "X-Workspace-ID": str(ws_a)},
            json={"query": "熔带怎么更换", "product_id": product_b},
        )
        assert response.status_code == 404, response.text
    finally:
        app.dependency_overrides.clear()
