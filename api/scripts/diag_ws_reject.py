"""定位 WS 握手被拒的具体原因（直接调用内部校验函数）。"""
from __future__ import annotations

import os
import sys
from pathlib import Path

API_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(API_DIR))
os.chdir(API_DIR)

from app.api.routes.chat_ws import _WSAuthError, _resolve_user, _resolve_workspace  # noqa: E402
from app.db.session import SessionLocal  # noqa: E402
from app.models import User, WorkspaceMembership  # noqa: E402
from app.services.auth import create_ws_ticket  # noqa: E402


def main() -> int:
    with SessionLocal() as db:
        user = db.query(User).filter(User.email == "demo.owner@example.com").first()
        print("user:", user.id if user else None, user.status if user else None, user.role if user else None)
        if user is None:
            return 1
        member = db.query(WorkspaceMembership).filter(WorkspaceMembership.user_id == user.id).first()
        print("membership:", member.role if member else None, member.status if member else None)

    ticket = create_ws_ticket(user, ttl_seconds=60)
    print("ticket purpose ok, len:", len(ticket))
    try:
        resolved = _resolve_user(ticket)
        print("resolve_user OK ->", resolved.id)
    except _WSAuthError as exc:
        print("resolve_user FAIL ->", exc)
        return 1
    try:
        ws_id = _resolve_workspace(user=resolved, workspace_id=1)
        print("resolve_workspace OK ->", ws_id)
    except _WSAuthError as exc:
        print("resolve_workspace FAIL ->", exc)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
