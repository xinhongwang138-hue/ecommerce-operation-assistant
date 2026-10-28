"""知识库内部服务令牌签发。

浏览器永远拿不到这把钥匙，也不会直接和智库端口对话：只有本服务（API 进程与
Celery worker）用 KB_INTERNAL_SECRET 签出一个几分钟有效的租户令牌，智库验签后
**只认令牌里的 tenant**，请求体里出现的任何租户字段都不参与决策。

为什么不用 AUTH_SECRET_KEY：那会让智库具备伪造用户登录令牌的能力，而它只需要
知道"这次请求属于哪个租户"。密钥分开之后，智库侧被攻破的最坏结果是越权读自己
的知识库，而不是接管电商账号。

载荷格式与 services/auth.py 同构（base64url(payload).base64url(hmac)），
智库 app/utils/tenant_guard.py 按同样的方式验签。
"""
import base64
import hashlib
import hmac
import json
import time
from typing import Any

from app.core.config import Settings, get_settings


class KnowledgeNotConfiguredError(RuntimeError):
    """知识库未配置：调用方应返回 503，而不是退化成"不带租户"的请求"""


def tenant_key(workspace_id: int) -> str:
    """
    把电商 workspace 主键映射成智库侧的租户标识

    加 ws_ 前缀：智库的 tenant_id 是 Milvus 的 partition key，需要非空字符串，
    同时避免和智库独立本机模式下的 "local" 取值空间重叠。
    """
    return f"ws_{int(workspace_id)}"


def mint_internal_token(workspace_id: int, *, settings: Settings | None = None) -> str:
    """
    签发一次请求用的内部令牌

    :param workspace_id: 当前请求已绑定并校验过成员关系的工作区
    :param settings: 配置注入，便于测试
    :return: "<payload>.<signature>"
    :raises KnowledgeNotConfiguredError: 缺少密钥或智库地址未配置
    """
    settings = settings or get_settings()
    secret = (settings.kb_internal_secret or "").strip()
    if not secret:
        raise KnowledgeNotConfiguredError("KB_INTERNAL_SECRET 未配置，知识库功能已关闭")

    # 令牌只活几十秒：它的作用范围就是"这一次转发"，不给重放留窗口
    expires_at = int(time.time()) + max(30, int(settings.kb_internal_token_ttl_seconds))
    payload: dict[str, Any] = {
        "tenant": tenant_key(workspace_id),
        "exp": expires_at,
    }
    encoded_payload = base64.urlsafe_b64encode(
        json.dumps(payload, separators=(",", ":")).encode("utf-8")
    ).rstrip(b"=").decode("utf-8")
    signature = _sign(encoded_payload, secret)
    return f"{encoded_payload}.{signature}"


def _sign(encoded_payload: str, secret: str) -> str:
    """HMAC-SHA256 签名，输出 base64url（去掉填充，与智库侧实现一致）"""
    digest = hmac.new(secret.encode("utf-8"), encoded_payload.encode("utf-8"), hashlib.sha256).digest()
    return base64.urlsafe_b64encode(digest).rstrip(b"=").decode("utf-8")
