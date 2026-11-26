"""LLM/OCR 花费配额的行为测试。

重点是三件事：超限真的会被拦、一个租户用光不会连累另一个租户、
以及存储彻底坏掉时是放行而不是把业务打成 500。
全部强制走内存分支，避免测试往真实 Redis 累加当日计数器。
"""

from types import SimpleNamespace

import pytest

from app.services import spend_quota
from app.services.spend_quota import (
    LLMBudgetExceeded,
    charge_llm_call,
    current_workspace_id,
)


@pytest.fixture(autouse=True)
def _memory_only(monkeypatch):
    """构造或执行测试所需的隔离环境：清空计数、断开 Redis，并按需给出小额度。"""
    spend_quota._memory.clear()
    monkeypatch.setattr(spend_quota, "_client", lambda: None)
    yield
    spend_quota._memory.clear()


def _limits(monkeypatch, *, per_workspace: int, total: int) -> None:
    """构造或执行测试所需的配额上限。"""
    monkeypatch.setattr(
        spend_quota,
        "get_settings",
        lambda: SimpleNamespace(
            llm_daily_calls_per_workspace=per_workspace,
            llm_daily_calls_total=total,
            redis_url="redis://127.0.0.1:6379/0",
        ),
    )


def _charge_n_times(count: int) -> None:
    """构造或执行测试所需的连续外呼。"""
    for _ in range(count):
        charge_llm_call()


def test_workspace_budget_blocks_after_limit_is_exhausted(monkeypatch) -> None:
    """验证“租户额度用尽后被拦”场景，确保第 N+1 次外呼抛配额异常。"""
    _limits(monkeypatch, per_workspace=3, total=1000)
    token = current_workspace_id.set(11)
    try:
        _charge_n_times(3)
        with pytest.raises(LLMBudgetExceeded):
            charge_llm_call()
    finally:
        current_workspace_id.reset(token)


def test_one_workspace_exhaustion_does_not_block_another(monkeypatch) -> None:
    """验证“租户之间额度互不影响”场景，这是配额必须按 workspace 归属的理由。"""
    _limits(monkeypatch, per_workspace=2, total=1000)

    token = current_workspace_id.set(11)
    try:
        _charge_n_times(5)
    except LLMBudgetExceeded:
        pass
    finally:
        current_workspace_id.reset(token)

    # 12 号租户的额度必须是干净的。
    token = current_workspace_id.set(12)
    try:
        _charge_n_times(2)
        with pytest.raises(LLMBudgetExceeded):
            charge_llm_call()
    finally:
        current_workspace_id.reset(token)


def test_global_budget_covers_calls_without_request_context(monkeypatch) -> None:
    """验证“后台任务受全局额度约束”场景，Celery 里没有 workspace 上下文也不能漏计。"""
    _limits(monkeypatch, per_workspace=1000, total=2)

    assert current_workspace_id.get() is None
    _charge_n_times(2)
    with pytest.raises(LLMBudgetExceeded):
        charge_llm_call()


def test_quota_fails_open_when_storage_is_broken(monkeypatch) -> None:
    """验证“存储全挂时放行”场景，配额故障不能把业务打成 500。"""
    _limits(monkeypatch, per_workspace=1, total=1)

    def broken(*_args, **_kwargs):
        """构造或执行测试所需的坏存储分支。"""
        raise RuntimeError("storage down")

    monkeypatch.setattr(spend_quota, "_bump_redis", broken)
    monkeypatch.setattr(spend_quota, "_bump_memory", broken)

    # 额度只有 1 次也不该抛：计数拿不到，只能放行。
    _charge_n_times(5)


def test_zero_limit_means_unlimited(monkeypatch) -> None:
    """验证“额度为 0 表示不限制”场景，确保能按环境整体关掉配额。"""
    _limits(monkeypatch, per_workspace=0, total=0)

    _charge_n_times(50)


def test_http_route_returns_429_when_quota_exhausted(monkeypatch) -> None:
    """验证“服务层异常被转成 429”场景，证明配额对真实 HTTP 调用链有效。

    单元级断言只能证明 charge_llm_call 会抛，证明不了 main.py 的处理器接得住、
    也证明不了路由没有被别的 except 分支吞掉——所以走一次真实请求。
    """
    from fastapi.testclient import TestClient

    from app.api.routes.auth import get_current_user
    from app.main import app
    from app.models import User, UserRole
    from app.services import llm
    from app.services import model_settings as model_settings_service

    # 该端点走 require_owner_permission，不做 workspace 绑定，因此只受全局桶约束。
    _limits(monkeypatch, per_workspace=1000, total=1)
    monkeypatch.setattr(
        model_settings_service,
        "get_runtime_settings",
        lambda: SimpleNamespace(
            llm_base_url="https://llm.example.com/v1",
            llm_api_key="test-key",
            llm_model="test-model",
        ),
    )

    class _FakeResponse:
        """构造或执行测试所需的模型响应，避免真的外呼。"""

        def raise_for_status(self) -> None:
            """构造或执行测试所需的成功状态。"""

        def json(self) -> dict:
            """构造或执行测试所需的合法 JSON 补全响应。"""
            return {"choices": [{"message": {"content": '{"ok": true, "message": "ready"}'}}]}

    class _FakeClient:
        """构造或执行测试所需的 httpx 替身。"""

        def __init__(self, *_args, **_kwargs) -> None:
            """构造或执行测试所需的客户端占位。"""

        def __enter__(self):
            """构造或执行测试所需的上下文进入。"""
            return self

        def __exit__(self, *_exc) -> bool:
            """构造或执行测试所需的上下文退出。"""
            return False

        def post(self, *_args, **_kwargs):
            """构造或执行测试所需的假外呼响应。"""
            return _FakeResponse()

    monkeypatch.setattr(llm.httpx, "Client", _FakeClient)
    app.dependency_overrides[get_current_user] = lambda: User(
        id=1, email="owner@example.com", display_name="owner", role=UserRole.OWNER
    )
    try:
        with TestClient(app) as client:
            first = client.post("/api/v1/model-settings/test", json={"target": "text"})
            second = client.post("/api/v1/model-settings/test", json={"target": "text"})
    finally:
        app.dependency_overrides.clear()

    assert first.status_code == 200, first.text
    assert second.status_code == 429, second.text
    assert "配额" in second.json()["detail"]
    assert second.headers.get("retry-after") == "3600"
