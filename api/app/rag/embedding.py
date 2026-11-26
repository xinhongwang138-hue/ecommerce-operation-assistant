"""向量化：有则用，没有则明说没有。

三条自我约束：

1. **失败不抛异常**。向量是增强项，端点抽风不该让入库失败——那次入库会退化成
   纯词法检索，仍然能用。错误原因挂在返回体里，前端如实展示。
2. **不单独计费到每日模型配额**。一篇 20 页的文档一次要发几十批 embedding 请求，
   按"每次调用算一次配额"记账会让一次入库就把当日额度打满。配额是给"用户主动
   触发的对话"设的护栏，不是给批量索引设的。
3. **模型名必须显式配置**。不猜默认值：猜错的表现是"检索总是莫名其妙差一点"，
   比直接关掉向量更难查。

超时与熔断是上面第 1 条的落地细节：一次网络抖动实测能让检索卡 21 秒（连接阶段
重试），用户等不到结果就等于功能不可用。所以把超时压到"人还能等"的量级，并在
失败后的冷却窗口内直接跳过——抖动通常成串出现，逐次重试只会让每一个请求都变慢。
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field

import httpx

from app.core.config import Settings, get_settings

BATCH_SIZE = 16
# 读超时给到 8 秒：16 条短文本正常只要 1 秒左右，8 秒足够覆盖慢批次。
TIMEOUT_SECONDS = 8.0
# 连接超时必须单独收紧。抖动卡在握手阶段时，默认值会拖成几十秒的黑洞。
CONNECT_TIMEOUT_SECONDS = 3.0
# 失败后冷却多久不再尝试。抖动成串出现，冷却比逐次重试更能保护整体延迟。
BREAKER_COOLDOWN_SECONDS = 30.0

_timeout = httpx.Timeout(TIMEOUT_SECONDS, connect=CONNECT_TIMEOUT_SECONDS)

_breaker_until = 0.0
_breaker_reason: str | None = None


@dataclass
class EmbedOutcome:
    vectors: list[list[float]] | None = None
    model: str | None = None
    error: str | None = None
    batches: int = field(default=0)


def reset_breaker() -> None:
    """清空熔断状态。测试与"用户在页面上手动重试"共用这个入口。"""
    global _breaker_until, _breaker_reason
    _breaker_until = 0.0
    _breaker_reason = None


def breaker_wait() -> float:
    """距离下次允许尝试还有几秒；0 表示当前可以试。"""
    return max(0.0, _breaker_until - time.monotonic())


def _trip_breaker(reason: str) -> None:
    global _breaker_until, _breaker_reason
    _breaker_until = time.monotonic() + BREAKER_COOLDOWN_SECONDS
    _breaker_reason = reason


def breaker_reason() -> str | None:
    return _breaker_reason


def resolve(settings: Settings | None = None) -> tuple[str, str, str] | None:
    """返回 (base_url, api_key, model)；未配置时返回 None。"""
    settings = settings or get_settings()
    model = (settings.embedding_model or "").strip()
    base_url = (settings.embedding_base_url or settings.llm_base_url or "").strip()
    api_key = (settings.embedding_api_key or settings.llm_api_key or "").strip()
    if not model or not base_url or not api_key:
        return None
    return base_url, api_key, model


def is_configured(settings: Settings | None = None) -> bool:
    return resolve(settings) is not None


def model_name(settings: Settings | None = None) -> str | None:
    """只看模型名。用于展示"配了但连不上"与"根本没配"的区别。"""
    settings = settings or get_settings()
    return (settings.embedding_model or "").strip() or None


def embed_texts(texts: list[str], *, settings: Settings | None = None) -> EmbedOutcome:
    """批量取向量。空输入返回空列表而不是 None。"""
    if not texts:
        return EmbedOutcome(vectors=[], model=(settings or get_settings()).embedding_model)

    resolved = resolve(settings)
    if resolved is None:
        return EmbedOutcome(error="未配置 EMBEDDING_MODEL（或缺少 base_url / api_key）")
    base_url, api_key, model = resolved

    # 冷却窗口内直接返回，不再付一次超时的时间成本。
    wait = breaker_wait()
    if wait > 0:
        return EmbedOutcome(
            model=model,
            error=f"向量端点刚失败过（{_breaker_reason}），{wait:.0f} 秒内跳过，本次仅用词法",
        )

    url = _embeddings_url(base_url)
    vectors: list[list[float]] = []
    batches = 0
    for start in range(0, len(texts), BATCH_SIZE):
        batch = texts[start : start + BATCH_SIZE]
        batches += 1
        try:
            with httpx.Client(timeout=_timeout) as client:
                response = client.post(
                    url,
                    headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
                    json={"model": model, "input": batch},
                )
                response.raise_for_status()
                payload = response.json()
        except httpx.HTTPStatusError as exc:
            reason = f"向量端点返回 HTTP {exc.response.status_code}"
            _trip_breaker(reason)
            return EmbedOutcome(model=model, error=reason, batches=batches)
        except (httpx.HTTPError, ValueError) as exc:
            reason = f"向量端点不可用：{type(exc).__name__}"
            _trip_breaker(reason)
            return EmbedOutcome(model=model, error=reason, batches=batches)

        try:
            # 兼容 OpenAI 结构；按 index 排序，避免上游乱序导致向量与切片错位
            items = sorted(payload["data"], key=lambda item: item.get("index", 0))
            vectors.extend([float(value) for value in item["embedding"]] for item in items)
        except (KeyError, TypeError, ValueError):
            reason = "向量端点返回结构不合法"
            _trip_breaker(reason)
            return EmbedOutcome(model=model, error=reason, batches=batches)

    if len(vectors) != len(texts):
        # 宁可整批作废也不要错位：向量和切片对不上是最难查的一类 bug
        reason = f"向量数量不匹配（期望 {len(texts)}，实际 {len(vectors)}）"
        _trip_breaker(reason)
        return EmbedOutcome(model=model, error=reason, batches=batches)
    reset_breaker()
    return EmbedOutcome(vectors=vectors, model=model, batches=batches)


def _embeddings_url(base_url: str) -> str:
    normalized = base_url.strip().rstrip("/")
    if normalized.endswith("/embeddings"):
        return normalized
    return f"{normalized}/embeddings"
