"""LLM/OCR 花费配额：按 workspace 与全局两个维度做当日调用硬上限。

为什么还要再加这一层：现有 RateLimitMiddleware 是按客户端 IP 的进程内滑动窗口，
多租户共用一个反向代理时彼此串桶、进程重启即清零，而且完全管不到 Celery 里的模型调用。
本模块直接守在真正产生费用的 HTTP 出口（llm._post_chat_completion / ocr._post_ocr），
所以请求路径与后台任务都会被计入。

归属方式：HTTP 请求由 `_bind_workspace_to_session` 写入 contextvar，能归到具体 workspace；
Celery 任务没有请求上下文，只记全局总额，不会漏计也不会误挂到某个租户头上。

存储策略与 token_store 一致：Redis 优先（多进程共享），探活失败回退进程内，
两者都异常时放行——配额是护栏不是闸门，不能因为存储抖动把业务打成 500。
"""
from __future__ import annotations

import threading
import time
from contextvars import ContextVar
from datetime import UTC, datetime, timedelta

from app.core.config import get_settings

# 当前请求归属的 workspace；None 表示不在请求上下文里（例如 Celery worker）。
current_workspace_id: ContextVar[int | None] = ContextVar("current_workspace_id", default=None)

GLOBAL_BUCKET = "global"

_lock = threading.Lock()
_memory: dict[str, tuple[int, float]] = {}
_redis_client = None
_redis_checked = False


class LLMBudgetExceeded(RuntimeError):
    """已超过当日模型调用配额。HTTP 侧统一转 429，后台任务侧表现为任务失败。"""


def _client():
    """惰性探测 Redis；只探测一次，失败后本次进程内一直走内存分支。"""
    global _redis_client, _redis_checked
    if _redis_checked:
        return _redis_client
    _redis_checked = True
    try:
        import redis

        client = redis.Redis.from_url(
            get_settings().redis_url,
            socket_connect_timeout=0.5,
            socket_timeout=0.5,
        )
        client.ping()
        _redis_client = client
    except Exception:
        _redis_client = None
    return _redis_client


def _seconds_until_utc_midnight() -> int:
    """配额按 UTC 日切，取到次日零点的剩余秒数（下限 60 秒避免 TTL 为 0）。"""
    now = datetime.now(UTC)
    tomorrow = (now + timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)
    return max(60, int((tomorrow - now).total_seconds()))


def _bump_redis(key: str, ttl: int) -> int | None:
    """计数 +1 并在首次创建时设 TTL；Redis 不可用返回 None 交给内存分支。"""
    client = _client()
    if client is None:
        return None
    try:
        pipe = client.pipeline()
        pipe.incr(key)
        # nx=True 只对刚创建的键设 TTL，否则每次调用都会把窗口无限往后推。
        pipe.expire(key, ttl, nx=True)
        count, _ = pipe.execute()
        return int(count)
    except Exception:
        return None


def _bump_memory(key: str, ttl: int) -> int:
    """进程内回退计数。多 worker 各算各的，所以只是近似值。"""
    now = time.time()
    with _lock:
        for existing, (_count, expires) in list(_memory.items()):
            if expires <= now:
                _memory.pop(existing, None)
        count, expires = _memory.get(key, (0, now + ttl))
        count += 1
        _memory[key] = (count, expires)
        return count


def _charge(bucket: str, limit: int) -> None:
    """给某个桶记一次调用；limit<=0 表示该维度不限制。"""
    if limit <= 0:
        return
    key = f"quota:llm:{bucket}:{datetime.now(UTC):%Y%m%d}"
    ttl = _seconds_until_utc_midnight()
    try:
        count = _bump_redis(key, ttl)
        if count is None:
            count = _bump_memory(key, ttl)
    except Exception:
        # 配额是护栏不是闸门：两级存储都出问题就放行，不能把它变成 500。
        return
    if count > limit:
        raise LLMBudgetExceeded(f"当日模型调用配额已用尽（{bucket} 上限 {limit} 次/天）")


def charge_llm_call() -> None:
    """每次真实外呼前记账：先查本租户额度，再查全局总额度。"""
    settings = get_settings()
    workspace_id = current_workspace_id.get()
    if workspace_id is not None:
        _charge(f"workspace:{workspace_id}", settings.llm_daily_calls_per_workspace)
    _charge(GLOBAL_BUCKET, settings.llm_daily_calls_total)
