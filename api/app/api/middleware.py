"""应用级安全中间件：请求体上限、基础限流与安全响应头。

限流为进程内滑动窗口，够用于单实例/课程部署；多实例场景应换成网关或 Redis 计数。
开发与测试环境默认关闭（见 Settings.rate_limiting_active），避免影响本地联调。
"""
from __future__ import annotations

import time
from collections import deque
from threading import Lock

from starlette.datastructures import Headers
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import JSONResponse

from app.core.config import get_settings


class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    """补齐最基础的安全响应头，降低点击劫持、MIME 嗅探与 Referer 泄露风险。"""

    async def dispatch(self, request, call_next):
        response = await call_next(request)
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        response.headers.setdefault("X-Frame-Options", "DENY")
        response.headers.setdefault("Referrer-Policy", "no-referrer")
        response.headers.setdefault("Cross-Origin-Resource-Policy", "same-site")
        return response


class BodySizeLimitMiddleware:
    """按实际读取字节数封顶请求体。

    只查 Content-Length 会被 chunked 请求绕过：没有该头时判断直接落空，而 Starlette
    仍会把整个请求体读进内存才交给 Pydantic 校验。这里对 body 分片累加计数，超限即把
    消息替换为 http.disconnect 终止读取（宁可让这次请求失败，也不能让它把内存吃穿）。
    """

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        settings = get_settings()
        limit = settings.max_request_bytes
        # 知识库资料上传是唯一需要更大额度的入口：一份真实产品 PDF 轻松超过 16MB。
        # 只对该路径放宽到 KNOWLEDGE_MAX_UPLOAD_BYTES，其余接口维持全局上限，
        # 避免为了一个功能把整站的 DoS 防线一起抬高。
        if (
            scope.get("method") == "POST"
            and scope.get("path", "").endswith("/knowledge/documents")
        ):
            limit = max(limit, settings.knowledge_max_upload_bytes)
        declared = Headers(scope=scope).get("content-length")
        if declared is not None and declared.isdigit() and int(declared) > limit:
            await JSONResponse({"detail": "Request entity too large"}, status_code=413)(scope, receive, send)
            return

        consumed = 0

        async def counting_receive():
            nonlocal consumed
            message = await receive()
            if message["type"] == "http.request":
                consumed += len(message.get("body", b""))
                if consumed > limit:
                    return {"type": "http.disconnect"}
            return message

        await self.app(scope, counting_receive, send)


class RateLimitMiddleware(BaseHTTPMiddleware):
    """按客户端 IP 的滑动窗口限流；登录接口使用更严格的阈值。"""

    def __init__(self, app):
        super().__init__(app)
        self._hits: dict[str, deque[float]] = {}
        self._lock = Lock()

    async def dispatch(self, request, call_next):
        settings = get_settings()
        if (
            not settings.rate_limiting_active
            or request.method == "OPTIONS"
            or request.url.path in {"/health", "/api/v1/health"}
        ):
            return await call_next(request)

        is_login = request.url.path.endswith("/auth/login")
        limit = max(5, settings.login_max_attempts * 2) if is_login else settings.rate_limit_per_minute
        client_ip = request.client.host if request.client else "unknown"
        key = f"{client_ip}:{'login' if is_login else 'global'}"

        now = time.time()
        window_start = now - 60
        with self._lock:
            hits = self._hits.setdefault(key, deque())
            while hits and hits[0] < window_start:
                hits.popleft()
            if len(hits) >= limit:
                return JSONResponse(
                    {"detail": "Too many requests"},
                    status_code=429,
                    headers={"Retry-After": "60"},
                )
            hits.append(now)
        return await call_next(request)
