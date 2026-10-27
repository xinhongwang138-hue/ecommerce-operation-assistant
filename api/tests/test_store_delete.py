"""店铺删除端点的行为测试。

重点是三条边界：有商品时必须拒绝（业务数据不可再生）、只有工作区 owner 能删、
以及跨租户不能删别人的店。
"""

from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.api.routes.auth import get_current_user
from app.db.session import get_db
from app.main import app
from app.models import (
    Base,
    Platform,
    PlatformAccount,
    Product,
    Store,
    StoreStatus,
    User,
    UserRole,
    Workspace,
    WorkspaceMembership,
)
from support import make_engine, make_session_override


def test_delete_store_is_rejected_when_it_still_has_products() -> None:
    """验证"有商品的店铺拒绝删除"场景，避免连带抹掉不可再生的运营数据。"""
    engine = make_engine()
    workspace_id, store_id, user_id = _seed(engine, role=UserRole.OWNER, with_product=True)

    payload = _delete(engine, user_id, workspace_id, store_id)
    assert payload["status"] == 409
    assert "1 个商品" in payload["body"]["detail"]

    with Session(engine) as db:
        assert db.get(Store, store_id) is not None, "被拒绝后店铺不应消失"


def test_delete_store_removes_store_and_its_platform_accounts() -> None:
    """验证"空店铺可删除"场景，平台账号随店一起清掉。"""
    engine = make_engine()
    workspace_id, store_id, user_id = _seed(engine, role=UserRole.OWNER, with_product=False)

    payload = _delete(engine, user_id, workspace_id, store_id)
    assert payload["status"] == 204

    with Session(engine) as db:
        assert db.get(Store, store_id) is None
        assert db.query(PlatformAccount).filter_by(store_id=store_id).count() == 0


def test_delete_store_requires_workspace_owner() -> None:
    """验证"非工作区 owner 不能删店"场景，删除比编辑更破坏性。"""
    engine = make_engine()
    workspace_id, store_id, user_id = _seed(engine, role=UserRole.OPERATOR, with_product=False)

    payload = _delete(engine, user_id, workspace_id, store_id)
    assert payload["status"] == 403

    with Session(engine) as db:
        assert db.get(Store, store_id) is not None


def test_delete_store_rejects_other_workspace_store() -> None:
    """验证"跨租户删店返回 404"场景，不泄露对方店铺是否存在。"""
    engine = make_engine()
    foreign_workspace, store_id, user_id = _seed(engine, role=UserRole.OWNER, with_product=False)
    # 再把同一个用户注册成另一个租户的 owner，用它去删前一个租户的店。
    with Session(engine) as db:
        intruder = db.get(User, user_id)
        other = Workspace(slug="team-b", name="团队 B")
        db.add(other)
        db.flush()
        db.add(
            WorkspaceMembership(
                workspace_id=other.id,
                user_id=intruder.id,
                role=UserRole.OWNER,
            )
        )
        db.commit()
        other_id = other.id

    payload = _delete(engine, user_id, other_id, store_id)
    assert payload["status"] == 404

    with Session(engine) as db:
        assert db.get(Store, store_id) is not None


def _delete(engine, user_id: int, workspace_id: int, store_id: int) -> dict:
    """构造或执行测试所需的删除请求，返回状态码与响应体。"""
    app.dependency_overrides[get_db] = make_session_override(engine)
    app.dependency_overrides[get_current_user] = lambda: _user(user_id)
    try:
        response = TestClient(app).delete(
            f"/api/v1/stores/{store_id}",
            headers={"Authorization": "Bearer test", "X-Workspace-ID": str(workspace_id)},
        )
    finally:
        app.dependency_overrides.clear()
    body = response.json() if response.content else None
    return {"status": response.status_code, "body": body}


def _seed(engine, *, role: UserRole, with_product: bool) -> tuple[int, int, int]:
    """构造或执行测试所需的租户、店铺与用户，返回 (workspace, store, user) 主键。"""
    Base.metadata.create_all(engine)
    with Session(engine) as db:
        workspace = Workspace(slug="team-a", name="团队 A")
        user = User(email="owner@example.com", display_name="owner", role=role)
        db.add_all([workspace, user])
        db.flush()
        db.add(WorkspaceMembership(workspace_id=workspace.id, user_id=user.id, role=role))
        store = Store(
            workspace_id=workspace.id,
            name="抖音旗舰店",
            platform=Platform.DOUYIN,
            status=StoreStatus.ACTIVE,
        )
        db.add(store)
        db.flush()
        db.add(PlatformAccount(store_id=store.id, platform=Platform.DOUYIN, account_name="官方账号"))
        if with_product:
            db.add(Product(workspace_id=workspace.id, store_id=store.id, name="低糖燕麦饼干", platform=Platform.DOUYIN))
        db.commit()
        return workspace.id, store.id, user.id


def _user(user_id: int) -> User:
    """构造或执行测试所需的用户对象，真实角色由库里的成员关系决定。"""
    return User(id=user_id, email="owner@example.com", display_name="owner", role=UserRole.OWNER)
