"""短期凭证状态存储：令牌撤销名单与一次性 WS 票据。

优先使用 Redis（多进程/多实例共享）；Redis 不可用时自动回退到进程内内存，
保证本机无 Docker 的开发环境也能正常运行。两种后端都是"尽力而为"：
存储异常只会导致相应防护降级，不会把业务请求打成 500。
"""
from __future__ import annotations

import threading
import time

from app.core.config import get_settings

_lock = threading.Lock()
_memory: dict[str, float] = {}
_redis_client = None
_redis_checked = False

_REVOKED_PREFIX = "auth:revoked:"
_TICKET_PREFIX = "auth:ticket:"


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


def _mem_set(key: str, ttl_seconds: int) -> None:
    with _lock:
        now = time.time()
        # 顺带清理过期项，避免长期运行后字典无限增长。
        for existing, expires in list(_memory.items()):
            if expires <= now:
                _memory.pop(existing, None)
        _memory[key] = now + max(int(ttl_seconds), 1)


def _mem_pop_fresh(key: str) -> bool:
    with _lock:
        expires = _memory.pop(key, None)
        return expires is not None and expires > time.time()


def revoke_token(jti: str | None, ttl_seconds: int) -> None:
    """把令牌的 jti 加入撤销名单，TTL 与令牌剩余有效期一致。"""
    if not jti:
        return
    client = _client()
    if client is not None:
        try:
            client.setex(f"{_REVOKED_PREFIX}{jti}", max(int(ttl_seconds), 1), "1")
            return
        except Exception:
            pass
    _mem_set(f"{_REVOKED_PREFIX}{jti}", ttl_seconds)


def is_token_revoked(jti: str | None) -> bool:
    if not jti:
        return False
    client = _client()
    if client is not None:
        try:
            return bool(client.exists(f"{_REVOKED_PREFIX}{jti}"))
        except Exception:
            pass
    with _lock:
        expires = _memory.get(f"{_REVOKED_PREFIX}{jti}")
        return expires is not None and expires > time.time()


def register_ticket(jti: str | None, ttl_seconds: int) -> None:
    """登记一次性票据；未登记过的票据在消费时视为无效。"""
    if not jti:
        return
    client = _client()
    if client is not None:
        try:
            client.setex(f"{_TICKET_PREFIX}{jti}", max(int(ttl_seconds), 1), "1")
            return
        except Exception:
            pass
    _mem_set(f"{_TICKET_PREFIX}{jti}", ttl_seconds)


def consume_ticket(jti: str | None) -> bool:
    """原子消费票据：首次返回 True，重复使用返回 False。"""
    if not jti:
        return False
    client = _client()
    if client is not None:
        try:
            # GETDEL 保证并发下只有一个消费者能拿到值。
            if client.getdel(f"{_TICKET_PREFIX}{jti}") is not None:
                return True
        except Exception:
            pass
    return _mem_pop_fresh(f"{_TICKET_PREFIX}{jti}")
