"""新增（或更新）一个可登录账号，并授权到演示工作区。

用法: python scripts/add_user.py <email> <password_file>
口令从文件读取，避免明文出现在命令行/脚本里；脚本只存 PBKDF2 摘要，不落明文。
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
from app.models import User, UserRole, Workspace, WorkspaceMembership  # noqa: E402
from app.services.users import hash_password  # noqa: E402

WORKSPACE_SLUG = "demo-workspace"


def main() -> int:
    if len(sys.argv) < 3:
        raise SystemExit("用法: python scripts/add_user.py <email> <password_file>")

    raw_email = sys.argv[1].strip()
    email = raw_email.lower()  # 登录时会把输入转小写比对，这里统一小写入库
    password = Path(sys.argv[2]).read_text(encoding="utf-8").strip()
    if not email or not password:
        raise SystemExit("email 或 password 为空")

    with SessionLocal() as db:
        user = db.scalars(select(User).where(User.email == email)).first()
        if user is None:
            user = User(
                email=email,
                display_name=raw_email,
                role=UserRole.OWNER,
                status="active",
                password_hash=hash_password(password),
            )
            db.add(user)
            db.commit()
            db.refresh(user)
            print(f"[add_user] created id={user.id} email={email} role=owner")
        else:
            user.password_hash = hash_password(password)
            user.status = "active"
            db.commit()
            print(f"[add_user] updated existing id={user.id} email={email}")

        workspace = db.scalars(select(Workspace).where(Workspace.slug == WORKSPACE_SLUG)).first()
        if workspace is None:
            print(f"[add_user] WARN: 未找到工作区 {WORKSPACE_SLUG}，跳过授权")
        else:
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
                print(f"[add_user] granted owner of workspace id={workspace.id}")
            else:
                print(f"[add_user] already member of workspace id={workspace.id}")

    # 用原始大小写模拟用户真实输入，验证登录链路是否可用
    try:
        import httpx

        resp = httpx.post(
            "http://127.0.0.1:8001/api/v1/auth/login",
            json={"email": raw_email, "password": password},
            trust_env=False,
            timeout=10.0,
        )
        print(f"[add_user] login check: HTTP {resp.status_code}")
    except Exception as exc:  # noqa: BLE001
        print(f"[add_user] login check skipped: {type(exc).__name__}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
