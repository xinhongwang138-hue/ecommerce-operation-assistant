"""数据中台测试的共用夹具。

单独放一个模块而不是塞进 conftest：仓目录是"每条测试一个 tmp_path"的，
用 conftest 的 session 级 fixture 会让租户隔离测试之间互相看见对方的仓文件。
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from types import SimpleNamespace

from sqlalchemy.orm import Session

from app.models import (
    InventoryItem,
    PerformanceRecord,
    Product,
    ProductSku,
    Store,
    UserRole,
    Workspace,
    WorkspaceMembership,
    User,
)


@dataclass
class Tenant:
    workspace: Workspace
    store: Store
    product: Product
    sku: ProductSku
    user: User | None = None

    @property
    def workspace_id(self) -> int:
        return self.workspace.id


def seed_tenant(db: Session, slug: str = "demo", *, with_user: bool = False) -> Tenant:
    """造一个最小可跑批的租户：店铺 + 商品 + SKU + 库存。"""
    workspace = Workspace(slug=slug, name=f"{slug} 工作区")
    db.add(workspace)
    db.flush()

    store = Store(workspace_id=workspace.id, name=f"{slug} 旗舰店", platform="douyin")
    db.add(store)
    db.flush()

    product = Product(
        workspace_id=workspace.id,
        store_id=store.id,
        name=f"{slug} 洗发水",
        platform="douyin",
        price=99,
        cost=40,
    )
    db.add(product)
    db.flush()

    sku = ProductSku(product_id=product.id, sku_code=f"{slug}-SKU-1", name="500ml", price=99, cost=40)
    db.add(sku)
    db.flush()
    db.add(InventoryItem(sku_id=sku.id, quantity=20, locked_quantity=2, warning_threshold=5))

    user: User | None = None
    if with_user:
        user = User(
            email=f"{slug}@example.com",
            display_name=slug,
            role=UserRole.OWNER,
            password_hash="x",
        )
        db.add(user)
        db.flush()
        db.add(
            WorkspaceMembership(
                workspace_id=workspace.id,
                user_id=user.id,
                role=UserRole.OWNER,
            )
        )

    db.commit()
    db.refresh(workspace)
    db.refresh(store)
    db.refresh(product)
    db.refresh(sku)
    return Tenant(workspace=workspace, store=store, product=product, sku=sku, user=user)


def add_performance(
    db: Session,
    product: Product,
    *,
    day: date,
    impressions: int,
    clicks: int,
    conversions: int,
    spend: float,
    revenue: float,
) -> PerformanceRecord:
    record = PerformanceRecord(
        product_id=product.id,
        period_start=day,
        period_end=day,
        impressions=impressions,
        clicks=clicks,
        conversions=conversions,
        spend=spend,
        revenue=revenue,
    )
    db.add(record)
    db.commit()
    db.refresh(record)
    return record


def auth_payload(tenant: Tenant) -> tuple[dict[str, str], SimpleNamespace]:
    """请求头 + 覆盖 `get_current_user` 用的替身。

    替身只带 id 与 role：权限链路真正查询的是"这个 user 在目标 workspace 里有没有
    成员关系"，其余字段（密码摘要、状态）与数据中台无关，带多了反而要维护两份。
    真实 User 实例跨 Session 传递会在 commit 后过期，用替身可以避开 DetachedInstanceError。
    """
    assert tenant.user is not None
    return (
        {"Authorization": "Bearer test", "X-Workspace-ID": str(tenant.workspace_id)},
        SimpleNamespace(id=tenant.user.id, role=UserRole.OWNER),
    )
