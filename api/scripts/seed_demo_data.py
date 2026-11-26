"""为本地验收准备演示账号与最小工作区数据（幂等，可重复执行）。

用途：在没有 Docker 环境的本机上，为端到端验收准备 owner 账号、workspace、
以及一个商品与一个店铺，使数字人 WS 对话有可用的 product_id / store_id 上下文。

只写 development 环境的本地库，不触碰生产数据。
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

API_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(API_DIR))
os.chdir(API_DIR)

from sqlalchemy import select  # noqa: E402

from app.db.session import SessionLocal  # noqa: E402
from app.models import (  # noqa: E402
    Platform,
    Product,
    Store,
    User,
    UserRole,
    Workspace,
    WorkspaceMembership,
)
from app.services.users import hash_password  # noqa: E402

OWNER_EMAIL = "demo.owner@example.com"
# 演示账号口令在运行时由环境变量提供；不设默认明文，避免脚本内出现可用口令。
OWNER_PASSWORD = os.environ.get("DEMO_OWNER_PASSWORD", "")
OWNER_DISPLAY_NAME = "演示店主"


def ensure_owner(db) -> User:
    if not OWNER_PASSWORD:
        raise SystemExit(
            "缺少演示口令：请设置环境变量 DEMO_OWNER_PASSWORD 后重试（脚本不内置明文口令）。"
        )
    user = db.scalars(select(User).where(User.email == OWNER_EMAIL)).first()
    if user is None:
        user = User(
            email=OWNER_EMAIL,
            display_name=OWNER_DISPLAY_NAME,
            role=UserRole.OWNER,
            status="active",
            password_hash=hash_password(OWNER_PASSWORD),
        )
        db.add(user)
        db.commit()
        db.refresh(user)
        print(f"[seed] created owner {OWNER_EMAIL}")
    else:
        print(f"[seed] owner exists {OWNER_EMAIL} (id={user.id})")
    return user


def ensure_workspace(db, user: User) -> Workspace:
    workspace = db.scalars(select(Workspace).where(Workspace.slug == "demo-workspace")).first()
    if workspace is None:
        workspace = Workspace(slug="demo-workspace", name="演示工作区", status="active", settings={})
        db.add(workspace)
        db.commit()
        db.refresh(workspace)
        print(f"[seed] created workspace id={workspace.id}")
    else:
        print(f"[seed] workspace exists id={workspace.id}")

    member = db.scalars(
        select(WorkspaceMembership).where(
            WorkspaceMembership.workspace_id == workspace.id,
            WorkspaceMembership.user_id == user.id,
        )
    ).first()
    if member is None:
        db.add(
            WorkspaceMembership(
                workspace_id=workspace.id,
                user_id=user.id,
                role=UserRole.OWNER,
            )
        )
        db.commit()
        print("[seed] added workspace member")
    return workspace


def ensure_store(db, workspace: Workspace) -> Store:
    store = db.scalars(
        select(Store).where(Store.workspace_id == workspace.id, Store.name == "演示店铺")
    ).first()
    if store is None:
        store = Store(
            workspace_id=workspace.id,
            name="演示店铺",
            platform=Platform.DOUYIN,
            status="active",
        )
        db.add(store)
        db.commit()
        db.refresh(store)
        print(f"[seed] created store id={store.id}")
    else:
        print(f"[seed] store exists id={store.id}")
    return store


def ensure_product(db, workspace: Workspace, store: Store) -> Product:
    product = db.scalars(
        select(Product).where(Product.workspace_id == workspace.id, Product.name == "演示商品")
    ).first()
    if product is None:
        product = Product(
            workspace_id=workspace.id,
            store_id=store.id,
            name="演示商品",
            platform=Platform.DOUYIN,
        )
        db.add(product)
        db.commit()
        db.refresh(product)
        print(f"[seed] created product id={product.id}")
    else:
        print(f"[seed] product exists id={product.id}")
    return product


def main() -> int:
    with SessionLocal() as db:
        user = ensure_owner(db)
        workspace = ensure_workspace(db, user)
        store = ensure_store(db, workspace)
        product = ensure_product(db, workspace, store)
        print(
            "[seed] done:",
            f"email={OWNER_EMAIL}",
            f"workspace_id={workspace.id}",
            f"store_id={store.id}",
            f"product_id={product.id}",
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
