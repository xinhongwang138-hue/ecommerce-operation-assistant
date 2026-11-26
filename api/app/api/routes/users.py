from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import delete, func, select, update
from sqlalchemy.orm import Session

from app.api.routes.auth import require_manage_users_permission
from app.db.session import get_db
from app.models import (
    AdRecommendation,
    AgentApproval,
    AgentConversation,
    AgentConversationMessage,
    AgentMemory,
    AgentRun,
    AgentRunReview,
    AgentTask,
    User,
    WorkspaceMembership,
)
from app.schemas import UserCreate, UserRead, UserUpdate
from app.services.users import hash_password, user_to_read
from app.services.workspaces import ensure_default_workspace, ensure_workspace_membership


router = APIRouter(prefix="/users", tags=["users"])
SessionDep = Annotated[Session, Depends(get_db)]


@router.get("", response_model=list[UserRead])
def list_users(
    db: SessionDep,
    _current_user: Annotated[User, Depends(require_manage_users_permission)],
) -> list[dict]:
    """处理查询users接口；完成身份、Workspace、资源归属和输入校验后返回结构化结果。"""
    users = db.scalars(select(User).order_by(User.created_at.desc())).all()
    return [user_to_read(user) for user in users]


@router.post("", response_model=UserRead, status_code=status.HTTP_201_CREATED)
def create_user(
    payload: UserCreate,
    db: SessionDep,
    _current_user: Annotated[User, Depends(require_manage_users_permission)],
) -> dict:
    # 账号邮箱统一小写，作为全局登录标识；新增用户只能由 owner 通过权限依赖进入。
    """处理创建用户接口；完成身份、Workspace、资源归属和输入校验后返回结构化结果。"""
    # 先完成认证、Workspace 与资源归属校验，再进入会产生副作用的服务调用。
    email = payload.email.strip().lower()
    existing = db.scalars(select(User.id).where(User.email == email)).first()
    if existing is not None:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="User email already exists")

    user = User(
        email=email,
        display_name=payload.display_name.strip(),
        role=payload.role,
        status=payload.status,
        password_hash=hash_password(payload.password) if payload.password else None,
    )
    db.add(user)
    db.flush()
    workspace = ensure_default_workspace(db)
    ensure_workspace_membership(db, workspace=workspace, user=user, role=payload.role)
    db.commit()
    db.refresh(user)
    return user_to_read(user)


@router.patch("/{user_id}", response_model=UserRead)
def update_user(
    user_id: int,
    payload: UserUpdate,
    db: SessionDep,
    current_user: Annotated[User, Depends(require_manage_users_permission)],
) -> dict:
    """处理更新用户接口；完成身份、Workspace、资源归属和输入校验后返回结构化结果。"""
    # 先完成认证、Workspace 与资源归属校验，再进入会产生副作用的服务调用。
    user = db.get(User, user_id)
    if user is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found")

    # 未传 password 时保持原密码不变，避免编辑角色或状态时误清空登录凭据。
    changes = payload.model_dump(exclude_unset=True)
    if "display_name" in changes and changes["display_name"] is not None:
        user.display_name = changes["display_name"].strip()
    if "role" in changes and changes["role"] is not None:
        user.role = changes["role"]
        # 当前用户管理仍是全局入口，角色变更必须同步现有 Membership，避免降权后保留旧租户权限。
        memberships = db.scalars(
            select(WorkspaceMembership).where(WorkspaceMembership.user_id == user.id)
        ).all()
        for membership in memberships:
            membership.role = changes["role"]
    if "status" in changes and changes["status"] is not None:
        next_status = changes["status"]
        # 停用自己或停用最后一个 active owner 都会把工作区锁死，必须提前拒绝。
        if next_status == "disabled":
            if user.id == current_user.id:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="You cannot disable your own account",
                )
            if user.role == "owner" and user.status == "active":
                remaining_owners = db.scalar(
                    select(func.count())
                    .select_from(User)
                    .where(User.role == "owner", User.status == "active", User.id != user.id)
                )
                if not remaining_owners:
                    raise HTTPException(
                        status_code=status.HTTP_400_BAD_REQUEST,
                        detail="Cannot disable the last active owner",
                    )
        user.status = next_status
    if "password" in changes and changes["password"]:
        user.password_hash = hash_password(changes["password"])

    db.commit()
    db.refresh(user)
    return user_to_read(user)


@router.delete("/{user_id}")
def delete_user(
    user_id: int,
    db: SessionDep,
    current_user: Annotated[User, Depends(require_manage_users_permission)],
) -> dict:
    """物理删除用户：先清理成员关系与可空引用，存在审计记录时拒绝并提示改用停用。"""
    user = db.get(User, user_id)
    if user is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found")
    if user.id == current_user.id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="You cannot delete your own account",
        )
    # 至少保留一个可用 owner，避免把工作区锁死。
    if user.role == "owner" and user.status == "active":
        remaining_owners = db.scalar(
            select(func.count())
            .select_from(User)
            .where(User.role == "owner", User.status == "active", User.id != user.id)
        )
        if not remaining_owners:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Cannot delete the last active owner",
            )

    # 审计表对 users.id 是非空外键且 NO ACTION，无法级联；有记录时拒绝物理删除，避免破坏审计链。
    audit_checks = (
        ("agent_tasks", select(func.count()).select_from(AgentTask).where(AgentTask.requested_by_user_id == user.id)),
        ("agent_runs", select(func.count()).select_from(AgentRun).where(AgentRun.requested_by_user_id == user.id)),
        ("agent_run_reviews", select(func.count()).select_from(AgentRunReview).where(AgentRunReview.reviewed_by_user_id == user.id)),
        ("agent_approvals", select(func.count()).select_from(AgentApproval).where(AgentApproval.requested_by_user_id == user.id)),
        ("agent_conversations", select(func.count()).select_from(AgentConversation).where(AgentConversation.created_by_user_id == user.id)),
        ("agent_memories", select(func.count()).select_from(AgentMemory).where(AgentMemory.created_by_user_id == user.id)),
    )
    blocked: list[str] = []
    for table_name, stmt in audit_checks:
        count = db.scalar(stmt) or 0
        if count:
            blocked.append(f"{table_name}({count})")
    if blocked:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"用户存在审计记录，无法物理删除：{', '.join(blocked)}。请改用停用。",
        )

    # 可空引用置空，保留历史记录但不指向已删除账号。
    db.execute(
        update(AdRecommendation)
        .where(AdRecommendation.confirmed_by_user_id == user.id)
        .values(confirmed_by_user_id=None)
    )
    db.execute(
        update(AgentApproval)
        .where(AgentApproval.decided_by_user_id == user.id)
        .values(decided_by_user_id=None)
    )
    db.execute(
        update(AgentConversationMessage)
        .where(AgentConversationMessage.author_user_id == user.id)
        .values(author_user_id=None)
    )
    # 成员关系随账号一起移除，否则 NOT NULL 外键会阻止物理删除。
    db.execute(delete(WorkspaceMembership).where(WorkspaceMembership.user_id == user.id))

    db.delete(user)
    db.commit()
    return {"deleted": True, "user_id": user_id}
