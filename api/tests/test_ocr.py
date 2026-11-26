"""图片取字：临时 SQLite + httpx MockTransport，不访问真实供应商、不消耗额度。"""

import base64
from types import SimpleNamespace

import httpx
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.api.routes import ocr as ocr_routes
from app.db.session import get_db
from app.main import app
from app.models import Base
from app.services import ocr
from app.services.bootstrap import ensure_bootstrap_user

# 1x1 透明 PNG，够通过解码与体积校验，不依赖任何外部图片文件。
PNG_BYTES = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg=="
)
PNG_B64 = base64.b64encode(PNG_BYTES).decode("ascii")


def fake_settings(**overrides):
    base = {
        "ocr_model": "qwen-vl-ocr-test",
        "ocr_base_url": None,
        "ocr_api_key": None,
        "ocr_max_image_bytes": 8 * 1024 * 1024,
        "llm_base_url": "https://provider.example/v1",
        "llm_api_key": "test-secret-do-not-echo",
    }
    base.update(overrides)
    return SimpleNamespace(**base)


def install_transport(monkeypatch, handler, settings=None):
    real_client = httpx.Client
    monkeypatch.setattr(ocr.httpx, "Client", lambda **kwargs: real_client(transport=httpx.MockTransport(handler), **kwargs))
    monkeypatch.setattr(ocr, "get_settings", lambda: settings or fake_settings())


@pytest.fixture
def browser(monkeypatch):
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    with Session(engine) as db:
        ensure_bootstrap_user(
            db,
            email="ocr@example.com",
            display_name="图片取字测试用户",
            password="Course-demo-2026!",
        )

    def override_db():
        with Session(engine) as db:
            yield db

    app.dependency_overrides[get_db] = override_db
    try:
        with TestClient(app) as client:
            login = client.post("/api/v1/auth/login", json={"email": "ocr@example.com", "password": "Course-demo-2026!"})
            assert login.status_code == 200, login.text
            client.headers["Authorization"] = "Bearer " + login.json()["access_token"]
            yield client
    finally:
        app.dependency_overrides.clear()


def extract(browser, **payload):
    body = {"image_base64": PNG_B64, "mime_type": "image/png"}
    body.update(payload)
    return browser.post("/api/v1/ocr/extract", json=body)


def test_requires_login():
    with TestClient(app) as anonymous:
        assert anonymous.post("/api/v1/ocr/extract", json={"image_base64": PNG_B64, "mime_type": "image/png"}).status_code == 401


def test_extract_returns_plain_text(browser, monkeypatch):
    def handler(request):
        # 保证把模型名和 base64 图片真的送出去了，而不是空请求。
        assert str(request.url) == "https://provider.example/v1/chat/completions"
        assert request.headers["Authorization"] == "Bearer test-secret-do-not-echo"
        assert b"data:image/png;base64," in request.content
        assert b"qwen-vl-ocr-test" in request.content
        return httpx.Response(200, json={"choices": [{"message": {"content": "第一行\n第二行"}}], "usage": {"total_tokens": 12}})

    install_transport(monkeypatch, handler)
    response = extract(browser)
    assert response.status_code == 200, response.text
    data = response.json()
    assert data["text"] == "第一行\n第二行"
    assert data["lines"] == ["第一行", "第二行"]
    assert data["model"] == "qwen-vl-ocr-test"
    assert data["usage"] == {"total_tokens": 12}


def test_coordinate_lines_are_dropped(browser, monkeypatch):
    # qwen-vl-ocr 原生格式会混入坐标行，接口必须把它们清掉，否则前端复制到的是数字串。
    content = "48,22,21,63,90\n商品标题\n120,300,15,80,90"
    install_transport(monkeypatch, lambda request: httpx.Response(200, json={"choices": [{"message": {"content": content}}]}))
    data = extract(browser).json()
    assert data["text"] == "商品标题"
    assert data["lines"] == ["商品标题"]


def test_code_fence_is_stripped(browser, monkeypatch):
    content = "```text\n保温杯 500ml\n```"
    install_transport(monkeypatch, lambda request: httpx.Response(200, json={"choices": [{"message": {"content": content}}]}))
    assert extract(browser).json()["text"] == "保温杯 500ml"


def test_rejects_unsupported_mime_type(browser, monkeypatch):
    # 先装好可用配置，确保 400 来自格式校验，而不是"未配置"这条更早的分支。
    install_transport(monkeypatch, lambda request: httpx.Response(200, json={"choices": [{"message": {"content": "x"}}]}))
    response = extract(browser, mime_type="application/pdf")
    assert response.status_code == 400
    assert "Unsupported image type" in response.json()["detail"]


def test_rejects_invalid_base64(browser, monkeypatch):
    install_transport(monkeypatch, lambda request: httpx.Response(200, json={"choices": [{"message": {"content": "x"}}]}))
    response = extract(browser, image_base64="这不是base64哦这不是base64哦")
    assert response.status_code == 400
    assert "base64" in response.json()["detail"]


def test_rejects_oversized_image(browser, monkeypatch):
    install_transport(monkeypatch, lambda request: httpx.Response(200, json={}), settings=fake_settings(ocr_max_image_bytes=16))
    assert extract(browser).status_code == 400


def test_upstream_error_maps_to_502_and_hides_body(browser, monkeypatch):
    install_transport(monkeypatch, lambda request: httpx.Response(401, json={"error": "test-secret-do-not-echo"}))
    response = extract(browser)
    assert response.status_code == 502
    assert "test-secret-do-not-echo" not in response.text


def test_unconfigured_provider_returns_400(browser, monkeypatch):
    install_transport(
        monkeypatch,
        lambda request: httpx.Response(200, json={"choices": [{"message": {"content": "x"}}]}),
        settings=fake_settings(llm_base_url="", llm_api_key=""),
    )
    assert extract(browser).status_code == 400


def test_status_endpoint_reports_masked_configuration(browser, monkeypatch):
    monkeypatch.setattr(ocr_routes, "get_settings", lambda: fake_settings())
    data = browser.get("/api/v1/ocr/status").json()
    assert data["configured"] is True
    assert data["model"] == "qwen-vl-ocr-test"
    assert "test-secret-do-not-echo" not in str(data)
