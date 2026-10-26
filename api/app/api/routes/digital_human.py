from typing import Annotated

from fastapi import APIRouter, Depends

from app.api.routes.auth import require_edit_permission
from app.core.config import get_settings
from app.models import User

router = APIRouter(tags=["digital-human"])


@router.get("/digital-human/credentials")
def get_digital_human_credentials(
    _current_user: Annotated[User, Depends(require_edit_permission)],
) -> dict[str, str]:
    """下发数字人（魔珐星云）SDK 初始化凭证。

    密钥只存后端 .env，前端通过本接口动态获取，避免 AppSecret 进入前端源码。
    未配置时返回空串，前端据此提示"未配置"而不是报错。
    """
    settings = get_settings()
    return {
        "app_id": settings.digital_human_app_id,
        "app_secret": settings.digital_human_app_secret,
        "gateway_server": settings.digital_human_gateway_server,
    }
