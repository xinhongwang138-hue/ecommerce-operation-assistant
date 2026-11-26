from datetime import UTC, datetime
from threading import Lock
from time import time
from typing import Annotated

from fastapi import APIRouter, Depends, Header, HTTPException, Request, Response, status
from fastapi.security import HTTPBearer
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.db.session import get_db
from app.models import User, UserRole, Workspace
from app.schemas import AuthTokenRead, LoginRequest, UserRead
from app.services.auth import AuthTokenError, create_access_token, ensure_user_can_login, parse_access_token
from app.services.token_store import is_token_revoked, revoke_token
from app.services.users import hash_password, user_to_read, verify_password
from app.services.spend_quota import current_workspace_id
from app.services.workspaces import WorkspaceAccess, WorkspaceAccessError, resolve_workspace_access


router = APIRouter(prefix="/auth", tags=["auth"])
SessionDep = Annotated[Session, Depends(get_db)]

# 固定假摘要：账号不存在时也走一遍同成本的校验，避免用响应时间枚举账号。
_DUMMY_PASSWORD_HASH = hash_password("timing-equalizer-not-a-real-password")

_login_failures: dict[str, list[float]] = {}
_login_lock = Lock()


@router.post("/login", response_model=AuthTokenRead)
def login(payload: LoginRequest, request: Request, db: SessionDep) -> dict:
    # 邮箱统一归一化后查询，避免大小写差异导致同一运营账号出现多套登录态。
    """处理登录接口；完成身份、Workspace、资源归属和输入校验后返回结构化结果。"""
    # 先完成认证、Workspace 与资源归属校验，再进入会产生副作用的服务调用。
    settings = get_settings()
    email = payload.email.strip().lower()
    client_host = request.client.host if request.client else "unknown"
    failure_key = f"{email}|{client_host}"
    if _is_locked(failure_key, settings.login_max_attempts, settings.login_lockout_seconds):
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="Too many failed attempts, please retry later",
        )

    user = db.scalars(select(User).where(User.email == email)).first()
    candidate_hash = user.password_hash if (user and user.password_hash) else _DUMMY_PASSWORD_HASH
    password_ok = verify_password(payload.password, candidate_hash)
    if user is None or not user.password_hash or not password_ok:
        _record_failure(failure_key)
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid email or password")
    try:
        ensure_user_can_login(user)
    except AuthTokenError as exc:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(exc)) from exc
    _clear_failures(failure_key)
    return {
        "access_token": create_access_token(user),
        "token_type": "bearer",
        "user": user_to_read(user),
    }


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
def logout(authorization: Annotated[str | None, Header()] = None) -> Response:
    """登出即撤销当前令牌：把 jti 写入撤销名单，剩余有效期内一律拒绝。"""
    token = _bearer_token(authorization)
    try:
        payload = parse_access_token(token)
    except AuthTokenError as exc:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid auth token") from exc
    expires_at = int(payload.get("exp", 0))
    remaining = max(expires_at - int(datetime.now(UTC).timestamp()), 0)
    revoke_token(payload.get("jti"), remaining)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


def get_current_user(
    db: SessionDep,
    authorization: Annotated[str | None, Header()] = None,
    _swagger_bearer=Depends(HTTPBearer(auto_error=False)),
) -> User:
    # 依赖层只接受 Bearer token，并在每次请求里重新校验账号可用性，保证权限收口在后端。
    """处理获取当前用户接口；完成身份、Workspace、资源归属和输入校验后返回结构化结果。"""
    # 先完成认证、Workspace 与资源归属校验，再进入会产生副作用的服务调用。
    token = _bearer_token(authorization)
    try:
        payload = parse_access_token(token)
        # 关键的用途隔离：60 秒 WS 票据即便泄露，也不能当 API 令牌使用。
        if payload.get("purpose") != "access":
            raise AuthTokenError("Token purpose is not access")
        if is_token_revoked(payload.get("jti")):
            raise AuthTokenError("Token has been revoked")
        user_id = int(payload.get("sub", 0))
        user = ensure_user_can_login(db.get(User, user_id))
    except (AuthTokenError, ValueError) as exc:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid auth token") from exc
    return user


def require_edit_permission(
    db: SessionDep,
    current_user: Annotated[User, Depends(get_current_user)],
    x_workspace_id: Annotated[int | None, Header(alias="X-Workspace-ID")] = None,
) -> User:
    """处理校验并获取edit权限接口；完成身份、Workspace、资源归属和输入校验后返回结构化结果。"""
    access = _bind_workspace_to_session(db, current_user=current_user, workspace_id=x_workspace_id)
    # Workspace 已启用后以成员角色为准，避免全局 operator 绕过目标 Workspace 的 viewer 限制。
    effective_role = access.role if access is not None else current_user.role
    if effective_role not in {UserRole.OWNER, UserRole.OPERATOR}:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Edit permission required")
    return current_user


def require_view_permission(
    db: SessionDep,
    current_user: Annotated[User, Depends(get_current_user)],
    x_workspace_id: Annotated[int | None, Header(alias="X-Workspace-ID")] = None,
) -> User:
    """处理校验并获取查看权限接口；完成身份、Workspace、资源归属和输入校验后返回结构化结果。"""
    _bind_workspace_to_session(db, current_user=current_user, workspace_id=x_workspace_id)
    return current_user


def get_current_workspace(
    db: SessionDep,
    current_user: Annotated[User, Depends(get_current_user)],
    x_workspace_id: Annotated[int | None, Header(alias="X-Workspace-ID")] = None,
) -> WorkspaceAccess:
    # Workspace 必须从认证用户的成员关系解析，不能信任模型或普通请求体自由填写租户 ID。
    """处理获取当前Workspace接口；完成身份、Workspace、资源归属和输入校验后返回结构化结果。"""
    try:
        return resolve_workspace_access(db, user=current_user, workspace_id=x_workspace_id)
    except WorkspaceAccessError as exc:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(exc)) from exc


def require_workspace_edit_permission(
    access: Annotated[WorkspaceAccess, Depends(get_current_workspace)],
) -> WorkspaceAccess:
    """处理校验并获取Workspaceedit权限接口；完成身份、Workspace、资源归属和输入校验后返回结构化结果。"""
    if access.role not in {UserRole.OWNER, UserRole.OPERATOR}:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Workspace edit permission required")
    return access


def require_workspace_owner_permission(
    access: Annotated[WorkspaceAccess, Depends(get_current_workspace)],
) -> WorkspaceAccess:
    """处理校验并获取Workspaceowner权限接口；完成身份、Workspace、资源归属和输入校验后返回结构化结果。"""
    if access.role != UserRole.OWNER:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Workspace owner permission required")
    return access


def require_manage_users_permission(current_user: Annotated[User, Depends(get_current_user)]) -> User:
    # 用户管理会影响全局权限边界，当前只开放给 owner，后续如做店铺级权限再拆细。
    """处理校验并获取manageusers权限接口；完成身份、Workspace、资源归属和输入校验后返回结构化结果。"""
    if current_user.role != "owner":
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="User management permission required")
    return current_user


def require_owner_permission(current_user: Annotated[User, Depends(get_current_user)]) -> User:
    """全局 owner 专属操作（如修改全平台模型配置），不随 workspace 角色放宽。"""
    if current_user.role != UserRole.OWNER:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Owner permission required")
    return current_user


def workspace_scope(db: Session) -> int | None:
    """读取当前请求绑定的 workspace；有租户数据但未绑定时默认拒绝，避免跨租户读取。

    只有"库里还没有任何 workspace"的兼容场景才返回 None（此时也没有业务数据可漏）。
    """
    workspace_id = db.info.get("workspace_id")
    if workspace_id is not None:
        return workspace_id
    workspace_count = db.scalar(select(func.count(Workspace.id)))
    if int(workspace_count or 0) > 0:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Workspace context is required",
        )
    return None


def require_ad_confirmation_permission(
    db: SessionDep,
    current_user: Annotated[User, Depends(get_current_user)],
    x_workspace_id: Annotated[int | None, Header(alias="X-Workspace-ID")] = None,
) -> User:
    # 投放确认虽不直接扣费，但会形成运营决策记录，因此只允许 owner/operator 操作。
    """处理校验并获取投放confirmation权限接口；完成身份、Workspace、资源归属和输入校验后返回结构化结果。"""
    access = _bind_workspace_to_session(db, current_user=current_user, workspace_id=x_workspace_id)
    effective_role = access.role if access is not None else current_user.role
    if effective_role not in {UserRole.OWNER, UserRole.OPERATOR}:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Ad confirmation permission required")
    return current_user


def _bearer_token(authorization: str | None) -> str:
    """处理bearerToken接口；完成身份、Workspace、资源归属和输入校验后返回结构化结果。"""    # 先完成认证、Workspace 与资源归属校验，再进入会产生副作用的服务调用。
    if not authorization:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Missing auth token")
    scheme, _, token = authorization.partition(" ")
    if scheme.lower() != "bearer" or not token:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid auth header")
    return token


def _bind_workspace_to_session(
    db: Session,
    *,
    current_user: User,
    workspace_id: int | None,
) -> WorkspaceAccess | None:
    # 只有尚未创建 Workspace 数据的旧测试库允许兼容路径；真实环境缺少 Membership 必须拒绝。
    """处理绑定Workspaceto会话接口；完成身份、Workspace、资源归属和输入校验后返回结构化结果。"""
    # 先完成认证、Workspace 与资源归属校验，再进入会产生副作用的服务调用。
    from app.services.workspaces import list_user_workspaces

    memberships = list_user_workspaces(db, user=current_user)
    if not memberships:
        workspace_count = db.scalar(select(func.count(Workspace.id)))
        if int(workspace_count or 0) > 0:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Workspace membership not found or inactive",
            )
        db.info["workspace_id"] = None
        current_workspace_id.set(None)
        return None
    try:
        access = resolve_workspace_access(db, user=current_user, workspace_id=workspace_id)
    except WorkspaceAccessError as exc:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(exc)) from exc
    db.info["workspace_id"] = access.workspace.id
    # 同步给花费配额用：服务层拿不到 db/session，只能靠 contextvar 归属到当前租户。
    current_workspace_id.set(access.workspace.id)
    return access


@router.get("/me", response_model=UserRead)
def get_me(current_user: Annotated[User, Depends(get_current_user)]) -> dict:
    """处理获取me接口；完成身份、Workspace、资源归属和输入校验后返回结构化结果。"""
    return user_to_read(current_user)


def _is_locked(key: str, max_attempts: int, lockout_seconds: int) -> bool:
    """滑动窗口判断是否已超过失败次数阈值，避免登录被无限暴力尝试。"""
    if max_attempts <= 0:
        return False
    now = time()
    with _login_lock:
        attempts = [ts for ts in _login_failures.get(key, []) if now - ts < lockout_seconds]
        if attempts:
            _login_failures[key] = attempts
        else:
            _login_failures.pop(key, None)
        return len(attempts) >= max_attempts


def _record_failure(key: str) -> None:
    with _login_lock:
        _login_failures.setdefault(key, []).append(time())


def _clear_failures(key: str) -> None:
    with _login_lock:
        _login_failures.pop(key, None)
