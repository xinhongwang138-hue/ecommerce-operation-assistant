from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.api.routes.auth import get_current_user
from app.db.session import get_db
from app.main import app
from app.models import (
    Base,
    Platform,
    PlatformAccount,
    PlatformAccountStatus,
    Store,
    StoreStatus,
    User,
    UserRole,
    Workspace,
    WorkspaceMembership,
    WorkspaceStatus,
)


def test_platform_authorization_start_requires_edit_permission() -> None:
    """验证“平台授权启动requiresedit权限”场景，确保重构后仍满足预期业务与安全边界。"""
    # 按 Arrange、Act、Assert 组织场景，重点检查可观察状态和副作用。
    engine = _engine()
    Base.metadata.create_all(engine)
    store_id, account_id = _seed_account(engine)

    app.dependency_overrides[get_db] = _override_db(engine)
    app.dependency_overrides[get_current_user] = lambda: _user(UserRole.VIEWER)
    try:
        response = TestClient(app).post(
            f"/api/v1/stores/{store_id}/platform-accounts/{account_id}/authorization/start",
            headers={"Authorization": "Bearer test"},
            json={"auth_type": "official_api", "scopes": ["orders:read"]},
        )
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 403


def test_platform_authorization_start_records_safe_metadata() -> None:
    """验证“平台授权启动records安全metadata”场景，确保重构后仍满足预期业务与安全边界。"""
    # 按 Arrange、Act、Assert 组织场景，重点检查可观察状态和副作用。
    engine = _engine()
    Base.metadata.create_all(engine)
    store_id, account_id = _seed_account(engine)

    app.dependency_overrides[get_db] = _override_db(engine)
    app.dependency_overrides[get_current_user] = lambda: _user(UserRole.OPERATOR)
    try:
        response = TestClient(app).post(
            f"/api/v1/stores/{store_id}/platform-accounts/{account_id}/authorization/start",
            headers={"Authorization": "Bearer test"},
            json={
                "auth_type": "oauth2",
                "redirect_uri": "https://example.com/callback",
                "scopes": ["products:read", "reports:read"],
                "note": "抖音官方授权预留",
            },
        )
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "pending"
    assert payload["state"]
    assert payload["authorization_url"].startswith("https://example.com/callback")
    assert "绕过登录" in payload["safety_note"]

    with Session(engine) as db:
        account = db.get(PlatformAccount, account_id)
        assert account is not None
        authorization = account.auth_metadata["authorization"]
        assert authorization["scopes"] == ["products:read", "reports:read"]
        assert "access_token" not in authorization
        assert "cookie" not in authorization


def test_platform_authorization_callback_updates_account_status() -> None:
    """验证“平台授权callbackupdates账号状态”场景，确保重构后仍满足预期业务与安全边界。"""
    # 按 Arrange、Act、Assert 组织场景，重点检查可观察状态和副作用。
    engine = _engine()
    Base.metadata.create_all(engine)
    store_id, account_id = _seed_account(engine)
    state = _start_authorization(engine, store_id, account_id)

    app.dependency_overrides[get_db] = _override_db(engine)
    app.dependency_overrides[get_current_user] = lambda: _user(UserRole.OPERATOR)
    try:
        response = TestClient(app).post(
            f"/api/v1/stores/{store_id}/platform-accounts/{account_id}/authorization/callback-placeholder",
            headers={"Authorization": "Bearer test"},
            json={"state": state, "status": "authorized", "external_account_id": "dy-account-001"},
        )
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 200
    assert response.json()["status"] == "authorized"
    with Session(engine) as db:
        account = db.get(PlatformAccount, account_id)
        assert account is not None
        assert account.status == PlatformAccountStatus.ACTIVE
        assert account.external_account_id == "dy-account-001"


def test_platform_authorization_callback_rejects_state_mismatch() -> None:
    """验证“平台授权callbackrejects状态mismatch”场景，确保重构后仍满足预期业务与安全边界。"""
    # 按 Arrange、Act、Assert 组织场景，重点检查可观察状态和副作用。
    engine = _engine()
    Base.metadata.create_all(engine)
    store_id, account_id = _seed_account(engine)
    _start_authorization(engine, store_id, account_id)

    app.dependency_overrides[get_db] = _override_db(engine)
    app.dependency_overrides[get_current_user] = lambda: _user(UserRole.OPERATOR)
    try:
        response = TestClient(app).post(
            f"/api/v1/stores/{store_id}/platform-accounts/{account_id}/authorization/callback-placeholder",
            headers={"Authorization": "Bearer test"},
            json={"state": "wrong-state", "status": "authorized"},
        )
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 400


def test_platform_authorization_rejects_other_workspace_account() -> None:
    """跨租户护栏：A 租户的 operator 不能对 B 租户的平台账号发起授权或回填回调。

    回归审计复核时新发现的 IDOR。`get_platform_account_or_404` 原先只校验
    `account.store_id` 的父子归属，而父子关系天然成立，等于任何已登录租户都能
    凭 (store_id, account_id) 整数对改写别租户账号的授权状态。
    """
    engine = _engine()
    Base.metadata.create_all(engine)
    workspace_a, workspace_b, store_b, account_b = _seed_split_workspace_accounts(engine)
    with Session(engine) as db:
        initial_status = db.get(PlatformAccount, account_b).status

    attempts = [
        ("authorization/start", {"auth_type": "official_api", "scopes": ["orders:read"]}),
        ("authorization/callback-placeholder", {"state": "guessed", "status": "authorized"}),
    ]
    for path, payload in attempts:
        app.dependency_overrides[get_db] = _override_db(engine)
        app.dependency_overrides[get_current_user] = lambda: _user(UserRole.OPERATOR)
        try:
            response = TestClient(app).post(
                f"/api/v1/stores/{store_b}/platform-accounts/{account_b}/{path}",
                headers={"Authorization": "Bearer test", "X-Workspace-ID": str(workspace_a)},
                json=payload,
            )
        finally:
            app.dependency_overrides.clear()
        assert response.status_code == 404, path

    with Session(engine) as db:
        account = db.get(PlatformAccount, account_b)
        assert account is not None
        assert not (account.auth_metadata or {}).get("authorization")
        assert account.external_account_id is None
        assert account.status == initial_status

    # 正向对照：同一组主键、同一用户，只把租户切到资源真正归属的 B，就必须放行。
    # 缺了这一段，上面的 404 可能只是被成员关系或外键顺带挡掉的，测不到租户边界。
    app.dependency_overrides[get_db] = _override_db(engine)
    app.dependency_overrides[get_current_user] = lambda: _user(UserRole.OPERATOR)
    try:
        allowed = TestClient(app).post(
            f"/api/v1/stores/{store_b}/platform-accounts/{account_b}/authorization/start",
            headers={"Authorization": "Bearer test", "X-Workspace-ID": str(workspace_b)},
            json={"auth_type": "official_api", "scopes": ["orders:read"]},
        )
    finally:
        app.dependency_overrides.clear()
    assert allowed.status_code == 200


def _start_authorization(engine, store_id: int, account_id: int) -> str:
    """构造或执行测试所需的启动授权，让测试数据、依赖替身与断言保持集中。"""
    # 按 Arrange、Act、Assert 组织场景，重点检查可观察状态和副作用。
    app.dependency_overrides[get_db] = _override_db(engine)
    app.dependency_overrides[get_current_user] = lambda: _user(UserRole.OPERATOR)
    try:
        response = TestClient(app).post(
            f"/api/v1/stores/{store_id}/platform-accounts/{account_id}/authorization/start",
            headers={"Authorization": "Bearer test"},
            json={"auth_type": "official_api", "scopes": ["reports:read"]},
        )
    finally:
        app.dependency_overrides.clear()
    assert response.status_code == 200
    return response.json()["state"]


def _seed_account(engine) -> tuple[int, int]:
    """创建平台授权测试所需的店铺和平台账号，并返回二者主键。"""
    # 先 flush 店铺取得真实主键，再创建平台账号，确保授权接口的归属校验有效。
    with Session(engine) as db:
        store = Store(name="抖音旗舰店", platform=Platform.DOUYIN)
        db.add(store)
        db.flush()
        account = PlatformAccount(store_id=store.id, platform=Platform.DOUYIN, account_name="官方账号")
        db.add(account)
        db.commit()
        return store.id, account.id


def _engine():
    """构造或执行测试所需的engine，让测试数据、依赖替身与断言保持集中。"""
    return create_engine(
        "sqlite+pysqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )


def _override_db(engine):
    """构造或执行测试所需的覆盖db，让测试数据、依赖替身与断言保持集中。"""
    def _dependency():
        """构造或执行测试所需的依赖覆盖，让测试数据、依赖替身与断言保持集中。"""
        with Session(engine) as db:
            yield db

    return _dependency


def _user(role: UserRole) -> User:
    """构造或执行测试所需的用户，让测试数据、依赖替身与断言保持集中。"""
    return User(id=1, email=f"{role.value}@example.com", display_name=f"{role.value}-user", role=role)


def _seed_split_workspace_accounts(engine) -> tuple[int, int, int]:
    """建两个互不相干的 workspace，把店铺与平台账号只放在 B，返回 (A 主键, B 店铺, B 账号)。"""
    # 必须真的存在第二个 workspace，否则 workspace_scope 在无租户数据时会退化成不过滤。
    # 同时给测试用户挂上 A 的成员关系，让请求能正常通过权限层，只被资源归属拦下。
    with Session(engine) as db:
        user = db.get(User, 1) or User(id=1, email="op@example.com", display_name="op", role=UserRole.OPERATOR)
        workspace_a = Workspace(slug="tenant-a", name="租户 A", status=WorkspaceStatus.ACTIVE)
        workspace_b = Workspace(slug="tenant-b", name="租户 B", status=WorkspaceStatus.ACTIVE)
        db.add_all([user, workspace_a, workspace_b])
        db.flush()
        # 同一个用户在 A、B 都是成员：这样 404 只可能由"资源不属于当前租户"产生，
        # 而不是成员关系缺失之类的旁路原因，测试才真正咬住租户边界。
        db.add_all(
            [
                WorkspaceMembership(workspace_id=workspace_a.id, user_id=user.id, role=UserRole.OPERATOR),
                WorkspaceMembership(workspace_id=workspace_b.id, user_id=user.id, role=UserRole.OPERATOR),
            ]
        )
        store = Store(
            workspace_id=workspace_b.id,
            name="B 的旗舰店",
            platform=Platform.DOUYIN,
            status=StoreStatus.ACTIVE,
        )
        db.add(store)
        db.flush()
        account = PlatformAccount(store_id=store.id, platform=Platform.DOUYIN, account_name="B 的账号")
        db.add(account)
        db.commit()
        return workspace_a.id, workspace_b.id, store.id, account.id
