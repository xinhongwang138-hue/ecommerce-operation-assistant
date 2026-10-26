"""应用级安全中间件：请求体上限、基础限流与安全响应头。

限流为进程内滑动窗口，够用于单实例/课程部署；多实例场景应换成网关或 Redis 计数。
开发与测试环境默认关闭（见 Settings.rate_limiting_active），避免影响本地联调。
"""
from __future__ import annotations

import time
from collections import deque
from threading import Lock

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


class BodySizeLimitMiddleware(BaseHTTPMiddleware):
    """按 Content-Length 拒绝超大请求体，先于 JSON 解析，避免超大 base64 撑爆内存。"""

    async def dispatch(self, request, call_next):
        settings = get_settings()
        content_length = request.headers.get("content-length")
        if content_length and content_length.isdigit() and int(content_length) > settings.max_request_bytes:
            return JSONResponse({"detail": "Request entity too large"}, status_code=413)
        return await call_next(request)


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
