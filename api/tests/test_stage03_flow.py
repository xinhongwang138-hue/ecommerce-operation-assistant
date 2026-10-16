"""第 03 篇真实接口闭环；输入与课件 Swagger 示例一致。"""
from decimal import Decimal
import sys

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.db.session import get_db
from app.main import app
from app.models import Base
from app.services.bootstrap import ensure_bootstrap_user


@pytest.fixture
def client():
    engine = create_engine('sqlite://', connect_args={'check_same_thread': False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    with Session(engine) as db:
        ensure_bootstrap_user(db, email='course@example.com', display_name='课堂管理员', password='Course-demo-2026!')

    def override_db():
        with Session(engine) as db:
            yield db

    app.dependency_overrides[get_db] = override_db
    try:
        with TestClient(app) as browser:
            response = browser.post('/api/v1/auth/login', json={'email': 'course@example.com', 'password': 'Course-demo-2026!'})
            assert response.status_code == 200, response.text
            browser.headers['Authorization'] = 'Bearer ' + response.json()['access_token']
            yield browser
    finally:
        app.dependency_overrides.clear()
        engine.dispose()


def create(client, path, payload):
    response = client.post('/api/v1' + path, json=payload)
    assert response.status_code == 201, response.text
    return response.json()


def product(client):
    store = create(client, '/stores', {'name': '课堂食品店', 'platform': 'douyin'})
    return create(client, '/products', {'store_id': store['id'], 'name': '低糖燕麦饼干', 'platform': 'douyin', 'price': 29.9, 'cost': 12.5, 'selling_points': ['独立包装', '便于携带']})


def test_inventory_flow_and_failed_outbound(client):
    item = product(client)
    path = f"/products/{item['id']}"
    sku = create(client, path + '/skus', {'sku_code': 'OAT-12', 'name': '12 包装', 'initial_quantity': 100, 'warning_threshold': 20})
    movement_path = path + f"/skus/{sku['id']}/inventory-movements"
    moved = create(client, movement_path, {'movement_type': 'outbound', 'quantity_delta': -10, 'reason': '课堂出库演示'})
    assert moved['after_quantity'] == 90
    assert client.post('/api/v1' + movement_path, json={'movement_type': 'outbound', 'quantity_delta': -1000}).status_code == 400
    detail = client.get('/api/v1' + path).json()
    assert detail['skus'][0]['inventory_item']['quantity'] == 90
    assert client.get(f"/api/v1/stores/{item['store_id']}/inventory-summary").status_code == 200


def test_competitor_creative_performance_review_flow(client):
    item = product(client)
    path = f"/products/{item['id']}"
    create(client, path + '/competitors', {'name': '同类早餐饼干', 'platform': 'douyin', 'price': 25.9, 'selling_points': ['大包装']})
    create(client, path + '/creative-plans', {'type': 'main_image', 'title': '课堂人工主图方案', 'content': {'headline': '早餐随身带', 'description': '突出独立包装'}, 'status': 'draft'})
    link = create(client, path + '/promotion-links', {'label': '课堂推广入口', 'url': 'https://example.com/oat', 'scenario': '课程演示'})
    experiment = create(client, path + '/ad-experiments', {'name': '早餐人群实验', 'objective': '观察点击和转化', 'daily_budget': 50, 'promotion_link_id': link['id']})
    record = create(client, path + '/performance-records', {'ad_experiment_id': experiment['id'], 'promotion_link_id': link['id'], 'period_start': '2026-09-01', 'period_end': '2026-09-07', 'impressions': 1000, 'clicks': 100, 'conversions': 10, 'spend': 100, 'revenue': 300})
    assert Decimal(record['ctr']) == Decimal('0.1')
    assert Decimal(record['roi']) == Decimal('3')
    dashboard = client.get('/api/v1' + path + '/ad-experiments/dashboard').json()
    assert dashboard['measured_experiments'] == 1
    create(client, path + '/review-reports', {'period_start': '2026-09-01', 'period_end': '2026-09-07', 'summary': '课堂人工复盘：继续观察', 'insights': ['已有点击和转化'], 'next_actions': ['下周比较不同标题']})
    detail = client.get('/api/v1' + path).json()
    for key in ['competitors', 'creative_plans', 'promotion_links', 'ad_experiments', 'performance_records', 'review_reports']:
        assert len(detail[key]) == 1, key


def test_diagnosis_generate_rules_fallback_and_writes(client):
    # 第15章起诊断接口已实现：未配置模型时按课程约定回退 rules 并落库；
    # 原第03篇“返回501且不写库”的边界由 test_stage15_routes.py 接替验证。
    item = product(client)
    response = client.post(f"/api/v1/products/{item['id']}/diagnoses/generate")
    assert response.status_code == 201, response.text
    assert response.json()['source'] == 'rules'
    detail = client.get(f"/api/v1/products/{item['id']}").json()
    assert len(detail['diagnoses']) == 1
    assert detail['diagnoses'][0]['source'] == 'rules'
    assert detail['generation_jobs'] == []


def test_document_csv_preview_then_commit(client):
    payload = {'csv_text': 'store_name,platform,name,price\n课堂食品店,douyin,课堂导入商品,19.90', 'commit': False}
    response = client.post('/api/v1/workspace/imports/products', json=payload)
    assert response.status_code == 200, response.text
    assert response.json()['error_count'] == 0
    assert client.get('/api/v1/products').json() == []
    response = client.post('/api/v1/workspace/imports/products', json={**payload, 'commit': True})
    assert response.status_code == 200, response.text
    assert response.json()['created_count'] == 1
    assert len(client.get('/api/v1/products').json()) == 1


def test_swagger_and_stage_import_boundary(client):
    assert client.get('/docs').status_code == 200
    spec = client.get('/openapi.json').json()
    assert spec['components']['securitySchemes']['HTTPBearer']['scheme'] == 'bearer'
    assert spec['paths']['/api/v1/products']['post']['security'] == [{'HTTPBearer': []}]
    # 第18章起 Agent 基础层已成为项目正当组成，原“app.agents 未导入”的阶段边界随之取消；
    # Agent 路由与投递的挂载边界由第21章 test_agent_platform/test_agent_run_api 验证。
    client.headers.pop('Authorization')
    assert client.get('/api/v1/products').status_code == 401
