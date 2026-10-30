"""数据中台接口契约测试。

覆盖三件事：未跑批时给"未就绪"而不是 500；自由查询的 400/409/200 三种出口；
viewer 不能触发跑批也不能扫全仓。
"""

from __future__ import annotations

import os
from datetime import date

os.environ.setdefault("DATABASE_URL", "sqlite:///:memory:")

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.api.routes.auth import get_current_user
from app.db.session import get_db
from app.main import app
from app.models import Base, User, UserRole, WorkspaceMembership
from tests.dataplatform_support import add_performance, auth_payload, seed_tenant
from tests.support import make_engine, make_session_override

BASE = "/api/v1/data-platform"


@pytest.fixture()
def engine():
    return make_engine()


@pytest.fixture()
def db(engine, tmp_path, monkeypatch):
    monkeypatch.setenv("WAREHOUSE_DIR", str(tmp_path / "warehouse"))
    Base.metadata.create_all(bind=engine)
    with Session(engine) as session:
        yield session


@pytest.fixture()
def tenant(db):
    seeded = seed_tenant(db, with_user=True)
    add_performance(
        db,
        seeded.product,
        day=date(2026, 9, 1),
        impressions=100,
        clicks=10,
        conversions=1,
        spend=10,
        revenue=50,
    )
    return seeded


def client(engine, user) -> TestClient:
    app.dependency_overrides[get_db] = make_session_override(engine)
    app.dependency_overrides[get_current_user] = lambda: user
    return TestClient(app)


def test_overview_reports_not_ready_before_first_run(engine, tenant):
    headers, user = auth_payload(tenant)
    with client(engine, user) as http:
        response = http.get(f"{BASE}/overview", headers=headers)
    app.dependency_overrides.clear()

    assert response.status_code == 200
    payload = response.json()
    assert payload["ready"] is False
    assert payload["run"] is None
    assert [layer["layer"] for layer in payload["layers"]] == ["ODS", "DWD", "DWS", "ADS"]


def test_trigger_run_then_read_everything(engine, tenant):
    headers, user = auth_payload(tenant)
    with client(engine, user) as http:
        started = http.post(f"{BASE}/runs", headers=headers)
        overview = http.get(f"{BASE}/overview", headers=headers)
        metrics = http.get(f"{BASE}/metrics", headers=headers)
        series = http.get(f"{BASE}/metrics/gmv", headers=headers)
        quality = http.get(f"{BASE}/quality", headers=headers)
        lineage = http.get(f"{BASE}/lineage", headers=headers)
        runs = http.get(f"{BASE}/runs", headers=headers)
    app.dependency_overrides.clear()

    assert started.status_code == 201
    assert started.json()["run"]["status"] == "succeeded"
    assert overview.json()["ready"] is True
    assert overview.json()["summary"]["gmv_7d"] == pytest.approx(50.0)
    assert len(metrics.json()) == 8
    assert all(item["formula"] for item in metrics.json())
    assert series.json()["points"][0]["value"] == pytest.approx(50.0)
    assert quality.json()["failed"] == 0
    assert len(lineage.json()["nodes"]) > 10
    assert len(runs.json()) == 1


def test_unknown_metric_returns_404(engine, tenant):
    headers, user = auth_payload(tenant)
    with client(engine, user) as http:
        response = http.get(f"{BASE}/metrics/not-a-metric", headers=headers)
    app.dependency_overrides.clear()
    assert response.status_code == 404


def test_query_before_run_returns_409(engine, tenant):
    headers, user = auth_payload(tenant)
    with client(engine, user) as http:
        response = http.post(f"{BASE}/query", headers=headers, json={"sql": "SELECT 1"})
    app.dependency_overrides.clear()
    assert response.status_code == 409


def test_query_rejects_unsafe_sql_and_accepts_safe_sql(engine, tenant):
    headers, user = auth_payload(tenant)
    with client(engine, user) as http:
        http.post(f"{BASE}/runs", headers=headers)
        bad = http.post(f"{BASE}/query", headers=headers, json={"sql": "DROP TABLE ads_ops_daily"})
        not_whitelisted = http.post(
            f"{BASE}/query", headers=headers, json={"sql": "SELECT * FROM products"}
        )
        good = http.post(
            f"{BASE}/query",
            headers=headers,
            json={"sql": "SELECT date, gmv FROM ads_ops_daily ORDER BY date"},
        )
        broken = http.post(
            f"{BASE}/query", headers=headers, json={"sql": "SELECT nope FROM ads_ops_daily"}
        )
    app.dependency_overrides.clear()

    assert bad.status_code == 400
    assert not_whitelisted.status_code == 400
    assert good.status_code == 200
    assert good.json()["row_count"] == 1
    assert good.json()["columns"] == ["date", "gmv"]
    assert broken.status_code == 400
    assert "SQL 执行失败" in broken.json()["detail"]


def test_viewer_cannot_run_pipeline(engine, tenant, db):
    viewer = User(email="viewer@example.com", display_name="viewer", role=UserRole.VIEWER, password_hash="x")
    db.add(viewer)
    db.flush()
    db.add(
        WorkspaceMembership(
            workspace_id=tenant.workspace_id,
            user_id=viewer.id,
            role=UserRole.VIEWER,
        )
    )
    db.commit()

    headers, _ = auth_payload(tenant)
    with client(engine, type("Stub", (), {"id": viewer.id, "role": UserRole.VIEWER})()) as http:
        forbidden = http.post(f"{BASE}/runs", headers=headers)
        readable = http.get(f"{BASE}/overview", headers=headers)
    app.dependency_overrides.clear()

    assert forbidden.status_code == 403
    assert readable.status_code == 200
