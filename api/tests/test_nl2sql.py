"""NL2SQL 问数测试。

重点不在"模型能不能写对 SQL"（那是模型的事），而在四件工程上的事：
1. 目录只包含业务表，`meta_*` 不进去；
2. 模型给的 SQL 必须先过护栏，且护栏拒绝时会把原因回灌给模型重来一次；
3. 两次都失败时抛 `NL2SQLRejected` 并带上每次的原因，不吞掉信息；
4. 未跑批 / 非合法 SQL / 空 SQL 各有正确的出口。
"""

from __future__ import annotations

import os
from datetime import date

os.environ.setdefault("DATABASE_URL", "sqlite:///:memory:")

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.api.routes.auth import get_current_user
from app.dataplatform.pipeline import run_pipeline
from app.db.session import get_db
from app.main import app
from app.models import Base, User, UserRole, WorkspaceMembership
from app.nl2sql import catalog, generator
from app.nl2sql.errors import NL2SQLRejected, WarehouseNotReady
from tests.dataplatform_support import add_performance, auth_payload, seed_tenant
from tests.support import make_engine, make_session_override

BASE = "/api/v1/nl2sql"


class FakeLLM:
    """按顺序吐出预设响应的替身，同时记录每一轮收到的 messages。"""

    def __init__(self, *responses: dict) -> None:
        self.responses = list(responses)
        self.calls: list[list[dict[str, str]]] = []

    def __call__(self, messages, **_kwargs):
        self.calls.append(messages)
        return self.responses.pop(0), {}


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


@pytest.fixture()
def ready(tenant, db):
    """跑一次批，让仓里有表可查。"""
    run_pipeline(db, tenant.workspace_id)
    return tenant


def client(engine, user) -> TestClient:
    app.dependency_overrides[get_db] = make_session_override(engine)
    app.dependency_overrides[get_current_user] = lambda: user
    return TestClient(app)


# --- 目录 -------------------------------------------------------------------


def test_catalog_lists_business_tables_and_skips_meta(tenant, db):
    run_pipeline(db, tenant.workspace_id)

    text = catalog.render_catalog(tenant.workspace_id)

    assert "ads_ops_daily" in text
    assert "dwd_sku_dim" in text
    assert "dws_agent_daily" in text
    # meta_* 是跑批元数据，进了目录模型就会去回答"数据质量如何"
    assert "meta_runs" not in text
    assert "meta_quality" not in text
    assert len(catalog.table_names(tenant.workspace_id)) == 21


def test_catalog_is_empty_before_first_run(tenant):
    assert catalog.render_catalog(tenant.workspace_id) == ""


def test_catalog_annotates_columns_whose_name_lies(tenant, db):
    """字段释义是刚需不是装饰：模型看到 success_rate 就会当成失败率用。

    实测：没有释义时模型输出 `success_rate AS 失败率`（口径错，且不会报错）；
    加释义后输出 `success_rate AS 成功率, 1 - success_rate AS 失败率`。
    """
    run_pipeline(db, tenant.workspace_id)

    text = catalog.render_catalog(tenant.workspace_id)

    assert "success_rate（成功率" in text
    assert "失败率 = 1 - success_rate" in text
    assert "spend=0 不代表真的没花钱" in text


# --- 生成与重试 --------------------------------------------------------------


def test_answer_executes_model_sql(ready, monkeypatch):
    fake = FakeLLM(
        {
            "sql": "SELECT date, gmv FROM ads_ops_daily ORDER BY date",
            "explanation": "取日指标",
            "assumptions": [],
            "confidence": 0.9,
        }
    )
    monkeypatch.setattr(generator, "create_json_chat_completion", fake)

    result = generator.answer(ready.workspace_id, "近 7 天成交额")

    assert result.attempts == 1
    assert result.result["columns"] == ["date", "gmv"]
    assert result.result["row_count"] == 1
    assert result.confidence == pytest.approx(0.9)
    assert len(fake.calls) == 1


def test_answer_repairs_after_guard_rejection(ready, monkeypatch):
    fake = FakeLLM(
        {"sql": "DROP TABLE ads_ops_daily", "explanation": "", "confidence": 0.4},
        {
            "sql": "SELECT count(*) AS 商品数 FROM dwd_product_dim",
            "explanation": "改查商品维行数",
            "confidence": 0.8,
        },
    )
    monkeypatch.setattr(generator, "create_json_chat_completion", fake)

    result = generator.answer(ready.workspace_id, "帮我删掉日指标表")

    assert result.attempts == 2
    assert result.result["row_count"] == 1
    # 第二次调用必须带上拒绝原因，否则模型只会重新猜一遍
    repair_message = fake.calls[1][-1]["content"]
    assert "没能通过校验" in repair_message
    assert "SELECT" in repair_message


def test_answer_repairs_after_execution_error(ready, monkeypatch):
    fake = FakeLLM(
        {"sql": "SELECT nope FROM ads_ops_daily", "explanation": "", "confidence": 0.5},
        {"sql": "SELECT count(*) AS 行数 FROM ads_ops_daily", "explanation": "换个写法", "confidence": 0.8},
    )
    monkeypatch.setattr(generator, "create_json_chat_completion", fake)

    result = generator.answer(ready.workspace_id, "有多少天数据")

    assert result.attempts == 2
    assert "执行报错" in fake.calls[1][-1]["content"]


def test_answer_raises_after_two_rejections(ready, monkeypatch):
    fake = FakeLLM(
        {"sql": "SELECT * FROM products", "explanation": "", "confidence": 0.3},
        {"sql": "DELETE FROM ads_ops_daily", "explanation": "", "confidence": 0.2},
    )
    monkeypatch.setattr(generator, "create_json_chat_completion", fake)

    with pytest.raises(NL2SQLRejected) as caught:
        generator.answer(ready.workspace_id, "删库")

    assert len(caught.value.attempts) == 2
    assert all(reason for reason in caught.value.attempts)


def test_answer_rejects_empty_sql(ready, monkeypatch):
    fake = FakeLLM({"sql": "", "explanation": "", "confidence": 0.1}, {"sql": " ", "confidence": 0.1})
    monkeypatch.setattr(generator, "create_json_chat_completion", fake)

    with pytest.raises(NL2SQLRejected):
        generator.answer(ready.workspace_id, "今天天气怎么样")

    assert len(fake.calls) == 2


def test_answer_before_first_run_raises_not_ready(tenant, monkeypatch):
    monkeypatch.setattr(generator, "create_json_chat_completion", FakeLLM())

    with pytest.raises(WarehouseNotReady):
        generator.answer(tenant.workspace_id, "成交额")


def test_question_length_is_capped(ready, monkeypatch):
    monkeypatch.setattr(generator, "create_json_chat_completion", FakeLLM())

    with pytest.raises(NL2SQLRejected) as caught:
        generator.answer(ready.workspace_id, "额" * 401)

    assert "400" in str(caught.value)


# --- 归一化 -----------------------------------------------------------------


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("SELECT 1", "SELECT 1"),
        ("SELECT 1;", "SELECT 1"),
        ("```sql\nSELECT 1;\n```", "SELECT 1"),
        ("  \nSELECT 1\n  ", "SELECT 1"),
    ],
)
def test_normalize_sql(raw, expected):
    assert generator.normalize_sql(raw) == expected


# --- 接口 -------------------------------------------------------------------


def test_ask_endpoint_returns_rows(engine, ready, monkeypatch):
    monkeypatch.setattr(
        generator,
        "create_json_chat_completion",
        FakeLLM(
            {
                "sql": "SELECT store_name AS 店铺, gmv_7d AS 成交额 FROM ads_store_kpi",
                "explanation": "直接读店铺 KPI",
                "confidence": 0.88,
            }
        ),
    )
    headers, user = auth_payload(ready)

    with client(engine, user) as http:
        response = http.post(f"{BASE}/ask", headers=headers, json={"question": "各店成交额"})

    app.dependency_overrides.clear()

    assert response.status_code == 200
    payload = response.json()
    assert payload["columns"] == ["店铺", "成交额"]
    assert payload["attempts"] == 1
    assert payload["sql"].startswith("SELECT")


def test_ask_endpoint_before_run_returns_409(engine, tenant, monkeypatch):
    monkeypatch.setattr(generator, "create_json_chat_completion", FakeLLM())
    headers, user = auth_payload(tenant)

    with client(engine, user) as http:
        response = http.post(f"{BASE}/ask", headers=headers, json={"question": "成交额"})

    app.dependency_overrides.clear()
    assert response.status_code == 409


def test_ask_endpoint_reports_attempts_on_422(engine, ready, monkeypatch):
    monkeypatch.setattr(
        generator,
        "create_json_chat_completion",
        FakeLLM(
            {"sql": "SELECT * FROM secrets", "confidence": 0.1},
            {"sql": "SELECT * FROM users", "confidence": 0.1},
        ),
    )
    headers, user = auth_payload(ready)

    with client(engine, user) as http:
        response = http.post(f"{BASE}/ask", headers=headers, json={"question": "所有用户"})

    app.dependency_overrides.clear()

    assert response.status_code == 422
    detail = response.json()["detail"]
    assert len(detail["attempts"]) == 2
    assert detail["message"]


def test_examples_endpoint_covers_three_layers(engine, ready):
    headers, user = auth_payload(ready)

    with client(engine, user) as http:
        response = http.get(f"{BASE}/examples", headers=headers)

    app.dependency_overrides.clear()

    assert response.status_code == 200
    tables = {item["table"] for item in response.json()}
    assert any(table.startswith("ads_") for table in tables)
    assert any(table.startswith("dws_") for table in tables)
    assert any(table.startswith("dwd_") for table in tables)


def test_viewer_cannot_ask(engine, ready, db):
    viewer = User(email="v2@example.com", display_name="v2", role=UserRole.VIEWER, password_hash="x")
    db.add(viewer)
    db.flush()
    db.add(
        WorkspaceMembership(
            workspace_id=ready.workspace_id,
            user_id=viewer.id,
            role=UserRole.VIEWER,
        )
    )
    db.commit()

    headers, _ = auth_payload(ready)
    with client(engine, type("Stub", (), {"id": viewer.id, "role": UserRole.VIEWER})()) as http:
        response = http.post(f"{BASE}/ask", headers=headers, json={"question": "成交额"})

    app.dependency_overrides.clear()
    assert response.status_code == 403
