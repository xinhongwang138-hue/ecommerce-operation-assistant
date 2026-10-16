"""第15章路由闭环：临时 SQLite + 模型替身，不访问真实模型或课堂数据库。"""
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.db.session import get_db
from app.api.routes import products, stores
from app.main import app
from app.models import Base
from app.services import ad_recommendations, creative_plans, diagnosis, inventory_advice, review
from app.services.bootstrap import ensure_bootstrap_user
from app.services.llm import LLMNotConfiguredError


PRODUCT_SUFFIXES = [
    "diagnoses/generate",
    "creative-plans/main-images/generate",
    "creative-plans/video-scripts/generate",
    "review-reports/generate",
    "ad-recommendations/generate",
    "ad-experiments/generate",
]


@pytest.fixture
def client(monkeypatch):
    def unavailable(*args, **kwargs):
        raise LLMNotConfiguredError("第15章测试：使用规则回退")

    for module in [diagnosis, creative_plans, review, ad_recommendations, inventory_advice]:
        monkeypatch.setattr(module, "create_json_chat_completion", unavailable)

    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    Base.metadata.create_all(engine)
    with Session(engine) as db:
        ensure_bootstrap_user(
            db, email="stage15@example.com", display_name="第15章测试管理员",
            password="Course-demo-2026!",
        )

    def override_db():
        with Session(engine) as db:
            yield db

    app.dependency_overrides[get_db] = override_db
    try:
        with TestClient(app) as browser:
            login = browser.post("/api/v1/auth/login", json={
                "email": "stage15@example.com", "password": "Course-demo-2026!",
            })
            assert login.status_code == 200, login.text
            browser.headers["Authorization"] = "Bearer " + login.json()["access_token"]
            yield browser
    finally:
        app.dependency_overrides.clear()
        engine.dispose()


def create(client, path, payload):
    response = client.post("/api/v1" + path, json=payload)
    assert response.status_code == 201, response.text
    return response.json()


def sample_product(client):
    store = create(client, "/stores", {"name": "课堂食品店", "platform": "douyin"})
    return create(client, "/products", {
        "store_id": store["id"], "name": "低糖燕麦饼干", "platform": "douyin",
        "price": 29.9, "cost": 12.5, "selling_points": ["独立包装", "便于携带"],
    })


def test_generation_routes_save_results_and_inventory_advice_is_read_only(client):
    item = sample_product(client)
    path = f"/products/{item['id']}"
    create(client, path + "/performance-records", {
        "period_start": "2026-09-01", "period_end": "2026-09-07",
        "impressions": 1000, "clicks": 100, "conversions": 10,
        "spend": 100, "revenue": 300,
    })
    results = {}
    for suffix in PRODUCT_SUFFIXES:
        response = client.post("/api/v1" + path + "/" + suffix)
        assert response.status_code == 201, (suffix, response.text)
        results[suffix] = response.json()

    assert results["diagnoses/generate"]["source"] == "rules"
    for suffix, kind in [
        ("creative-plans/main-images/generate", "main_image"),
        ("creative-plans/video-scripts/generate", "video_script"),
    ]:
        assert results[suffix]
        assert all(row["type"] == kind for row in results[suffix])
        assert all(row["content"]["source"] == "rules" for row in results[suffix])
    assert results["ad-experiments/generate"]["status"] == "draft"

    detail = client.get("/api/v1" + path).json()
    for key in ["diagnoses", "creative_plans", "review_reports", "ad_recommendations", "ad_experiments"]:
        assert detail[key], key
    assert detail["generation_jobs"] == []

    store_path = f"/api/v1/stores/{item['store_id']}"
    before = client.get(store_path + "/inventory-summary").json()
    advice = client.post(store_path + "/inventory-advice")
    assert advice.status_code == 200, advice.text
    assert advice.json()["source"] == "rules"
    assert client.get(store_path + "/inventory-summary").json() == before


@pytest.mark.parametrize("suffix", PRODUCT_SUFFIXES)
def test_missing_product_returns_404(client, suffix):
    response = client.post(f"/api/v1/products/999999/{suffix}")
    assert response.status_code == 404, response.text


def test_review_requires_performance_records(client):
    item = sample_product(client)
    response = client.post(f"/api/v1/products/{item['id']}/review-reports/generate")
    assert response.status_code == 400, response.text


def test_all_seven_routes_require_login_and_are_registered_once(client):
    client.headers.pop("Authorization")
    paths = [f"/api/v1/products/1/{suffix}" for suffix in PRODUCT_SUFFIXES]
    paths.append("/api/v1/stores/1/inventory-advice")
    spec_paths = client.get("/openapi.json").json()["paths"]
    for path in paths:
        assert client.post(path).status_code == 401
        template = path.replace("/products/1/", "/products/{product_id}/").replace(
            "/stores/1/", "/stores/{store_id}/"
        )
        assert "post" in spec_paths[template]
        matching = [
            route for route in [*products.router.routes, *stores.router.routes]
            if getattr(route, "path", None) == template.removeprefix("/api/v1")
            and "POST" in getattr(route, "methods", set())
        ]
        assert len(matching) == 1, template
