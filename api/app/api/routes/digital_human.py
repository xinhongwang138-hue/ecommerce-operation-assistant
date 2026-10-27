from typing import Annotated

from fastapi import APIRouter, Depends

from app.api.routes.auth import require_owner_permission
from app.core.config import get_settings
from app.models import User

router = APIRouter(tags=["digital-human"])


@router.get("/digital-human/credentials")
def get_digital_human_credentials(
    _current_user: Annotated[User, Depends(require_owner_permission)],
) -> dict[str, str]:
    """下发数字人（魔珐星云）SDK 初始化凭证。

    密钥只存后端 .env，前端通过本接口动态获取，避免 AppSecret 进入前端源码。
    未配置时返回空串，前端据此提示"未配置"而不是报错。

    权限取全局 owner（`require_owner_permission` 判的是 user.role，不是 workspace 成员角色），
    而非 edit：这是 .env 里的**全局**供应商密钥，不区分租户，且直接对应账号的计费时长。
    任何租户的 operator 都能领到它，等于共享账号被全员持有。
    LiteSDK 要求在前端初始化时同时给出 appId 与 appSecret，所以只要还走这条路，
    密钥就必然落到浏览器——真正的收口方案是后端代签短时 session（见 SECURITY_AUDIT.md 残留项）。
    """
    settings = get_settings()
    return {
        "app_id": settings.digital_human_app_id,
        "app_secret": settings.digital_human_app_secret,
        "gateway_server": settings.digital_human_gateway_server,
    }
