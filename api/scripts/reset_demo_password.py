"""重置本地演示账号的口令（口令由环境变量提供，不写在命令行或脚本里）。

用法: DEMO_OWNER_PASSWORD=<新口令> python scripts/reset_demo_password.py
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
from app.models import User  # noqa: E402
from app.services.users import hash_password  # noqa: E402

EMAIL = "demo.owner@example.com"


def main() -> int:
    password = os.environ.get("DEMO_OWNER_PASSWORD", "")
    if not password:
        raise SystemExit("缺少环境变量 DEMO_OWNER_PASSWORD")
    with SessionLocal() as db:
        user = db.scalars(select(User).where(User.email == EMAIL)).first()
        if user is None:
            raise SystemExit(f"账号不存在: {EMAIL}（先跑 seed_demo_data.py）")
        user.password_hash = hash_password(password)
        db.commit()
        print(f"[reset] password updated for {EMAIL}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
